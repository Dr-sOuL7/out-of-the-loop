"""The game engine: drives every match through the state machine.

One :class:`GameEngine` instance is shared across the bot (stored in
``application.bot_data``). It owns the live :class:`GameState` for each group
chat, schedules phase deadlines on the PTB ``JobQueue``, and performs all the
Telegram I/O for the game flow. Pure logic (scoring, tallying, matching) lives
in sibling modules and is unit-tested independently.

Concurrency: Telegram updates interleave at ``await`` points, so each chat's
state changes are guarded by a per-chat :class:`asyncio.Lock`. Phase-advancing
methods are also idempotent (they check the current state), so an early advance
and a timeout firing for the same phase can't double-process.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
from collections import defaultdict
from typing import Awaitable, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, User
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from ootl.config import Settings
from ootl.content.manager import ContentError, ContentManager
from ootl.db.repositories import Repositories
from ootl.game import texts
from ootl.game.enums import MatchState, can_transition
from ootl.game.models import GameState, Player, Round
from ootl.game.scoring import score_round
from ootl.game.selection import choose_imposter
from ootl.game.tally import tally_votes
from ootl.utils.text import guess_matches, truncate

logger = logging.getLogger(__name__)

ANSWER_CHAR_LIMIT = 200
NEXT_ROUND_DELAY = 6  # seconds between rounds

JobCallback = Callable[[ContextTypes.DEFAULT_TYPE], Awaitable[None]]


class GameEngine:
    def __init__(
        self,
        settings: Settings,
        content: ContentManager,
        repos: Repositories,
    ) -> None:
        self.settings = settings
        self.content = content
        self.repos = repos
        self.games: dict[int, GameState] = {}
        # user_id -> chat_id of the match they're actively playing (for DM routing)
        self._player_index: dict[int, int] = {}
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    # ======================================================================
    # Small Telegram helpers
    # ======================================================================
    async def _send(
        self, context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, **kwargs
    ):
        try:
            return await context.bot.send_message(
                chat_id=chat_id, text=text, parse_mode=ParseMode.HTML, **kwargs
            )
        except TelegramError as exc:
            logger.warning("Failed to send to chat %s: %s", chat_id, exc)
            return None

    async def _dm(
        self, context: ContextTypes.DEFAULT_TYPE, user_id: int, text: str, **kwargs
    ) -> bool:
        """DM a user; return True on success, False if blocked/unreachable."""
        try:
            await context.bot.send_message(
                chat_id=user_id, text=text, parse_mode=ParseMode.HTML, **kwargs
            )
            return True
        except TelegramError as exc:
            logger.info("DM to user %s failed: %s", user_id, exc)
            return False

    # ======================================================================
    # State machine + job scheduling
    # ======================================================================
    def _transition(self, game: GameState, target: MatchState) -> bool:
        if not can_transition(game.state, target):
            logger.warning(
                "chat %s: illegal transition %s -> %s",
                game.chat_id,
                game.state,
                target,
            )
            return False
        logger.info("chat %s: %s -> %s", game.chat_id, game.state.value, target.value)
        game.state = target
        return True

    def _schedule(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        game: GameState,
        seconds: int,
        callback: JobCallback,
        tag: str,
    ) -> None:
        self._cancel_job(context, game)
        name = f"{game.chat_id}:{tag}:{game.current_round_number}"
        context.job_queue.run_once(
            callback, when=seconds, chat_id=game.chat_id, name=name
        )
        game.active_job_name = name

    def _cancel_job(
        self, context: ContextTypes.DEFAULT_TYPE, game: GameState
    ) -> None:
        if game.active_job_name and context.job_queue:
            for job in context.job_queue.get_jobs_by_name(game.active_job_name):
                job.schedule_removal()
        game.active_job_name = None

    def _lock(self, chat_id: int) -> asyncio.Lock:
        return self._locks[chat_id]

    # ======================================================================
    # Lobby
    # ======================================================================
    def get_game(self, chat_id: int) -> GameState | None:
        return self.games.get(chat_id)

    async def create_room(
        self, chat_id: int, host: Player
    ) -> tuple[GameState | None, str]:
        existing = self.games.get(chat_id)
        if existing is not None:
            return None, texts.already_have_room()
        game = GameState(
            chat_id=chat_id,
            host_id=host.user_id,
            total_rounds=self.settings.default_rounds,
        )
        game.add_player(host)
        self.games[chat_id] = game
        return game, texts.room_created(host, self.settings.min_players)

    async def join(self, chat_id: int, player: Player) -> tuple[bool, str]:
        game = self.games.get(chat_id)
        if game is None:
            return False, texts.not_in_room()
        if game.state != MatchState.LOBBY:
            return False, texts.game_already_running()
        if player.user_id in game.players:
            return False, texts.already_joined()
        if game.player_count >= self.settings.max_players:
            return False, texts.room_full(self.settings.max_players)
        game.add_player(player)
        return True, texts.joined(player, game.player_count)

    async def leave(
        self, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int
    ) -> tuple[bool, str]:
        game = self.games.get(chat_id)
        if game is None or user_id not in game.players:
            return False, "You're not in a room here."
        name = game.display_name(user_id)

        if game.state == MatchState.LOBBY:
            game.remove_player(user_id)
            if game.player_count == 0:
                self.games.pop(chat_id, None)
                return True, texts.left(name, 0) + "\nRoom is now empty and closed."
            if game.host_id == user_id:
                game.host_id = game.player_ids[0]
                new_host = game.players[game.host_id]
                return True, texts.left(name, game.player_count) + (
                    f"\n👑 {texts.mention(new_host)} is the new host."
                )
            return True, texts.left(name, game.player_count)

        # Mid-match leave.
        await self._handle_active_leave(context, game, user_id)
        return True, texts.left(name, game.player_count)

    async def _handle_active_leave(
        self, context: ContextTypes.DEFAULT_TYPE, game: GameState, user_id: int
    ) -> None:
        async with self._lock(game.chat_id):
            was_imposter = (
                game.current_round is not None
                and game.current_round.imposter_id == user_id
            )
            game.remove_player(user_id)
            self._player_index.pop(user_id, None)
            rnd = game.current_round
            if rnd is not None:
                if user_id in rnd.participant_ids:
                    rnd.participant_ids.remove(user_id)
                rnd.answers.pop(user_id, None)
                if user_id in rnd.answer_order:
                    rnd.answer_order.remove(user_id)
                rnd.votes.pop(user_id, None)
                # Remove votes cast *for* the leaver too.
                for voter, target in list(rnd.votes.items()):
                    if target == user_id:
                        rnd.votes.pop(voter, None)

            if game.player_count < self.settings.min_players:
                self._cancel_job(context, game)
                await self._send(context, game.chat_id, texts.round_aborted_not_enough())
                await self._finalize_aborted(context, game)
                return

            if was_imposter:
                self._cancel_job(context, game)
                await self._send(
                    context,
                    game.chat_id,
                    "🚪 The imposter left mid-round — this round is void. Moving on…",
                )
                # Exceptional path: force ROUND_END so the next-round timer (or
                # match end) can proceed from a legal state.
                game.state = MatchState.ROUND_END
                if rnd is not None and rnd.round_id is not None:
                    await self.repos.rounds.set_state(rnd.round_id, "VOIDED")
                await self._after_round(context, game)
                return

            # A clue-holder left: their absence may complete the current phase.
            await self._maybe_advance_after_change(context, game)

    # ======================================================================
    # Start match
    # ======================================================================
    async def start_match(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
        host_id: int,
        rounds: int | None,
    ) -> tuple[bool, str]:
        game = self.games.get(chat_id)
        if game is None:
            return False, texts.not_in_room()
        if game.state != MatchState.LOBBY:
            return False, texts.game_already_running()
        if not game.is_host(host_id):
            return False, texts.only_host("start the game")
        if game.player_count < self.settings.min_players:
            return False, texts.need_more_players(
                game.player_count, self.settings.min_players
            )

        # Verify everyone can be DM'd (they've pressed /start in private).
        missing = []
        for uid, player in game.players.items():
            if not await self.repos.users.is_dm_ok(uid):
                missing.append(player.display_name)
        if missing:
            return False, texts.dm_required(missing)

        if rounds is not None and rounds > 0:
            game.total_rounds = min(rounds, 20)

        # Make sure no player is already in another active match.
        for uid in game.player_ids:
            other = self._player_index.get(uid)
            if other is not None and other != chat_id:
                return False, (
                    f"{texts.esc(game.display_name(uid))} is already in a game "
                    "elsewhere. Finish that one first."
                )

        if not self._transition(game, MatchState.STARTING):
            return False, texts.game_already_running()

        game.match_id = await self.repos.matches.create(chat_id, game.total_rounds)
        for uid in game.player_ids:
            self._player_index[uid] = chat_id

        await self._send(context, chat_id, texts.match_starting(game.total_rounds))
        await self._begin_round(context, game)
        return True, ""

    # ======================================================================
    # Round flow
    # ======================================================================
    async def _begin_round(
        self, context: ContextTypes.DEFAULT_TYPE, game: GameState, attempt: int = 0
    ) -> None:
        if attempt > 3:
            await self._send(
                context, game.chat_id, "Couldn't deliver roles. Aborting match."
            )
            await self._finalize_aborted(context, game)
            return
        if not self._transition(game, MatchState.ASSIGNING_ROLES):
            return

        game.current_round_number += 1
        await self.repos.matches.set_current_round(
            game.match_id, game.current_round_number
        )

        # --- pick content ---------------------------------------------------
        try:
            category_keys = await self.content.category_keys()
            category = secrets.choice(category_keys)
            word = await self.content.pick_word(category=category)
            question = await self.content.pick_question(category=category)
        except (ContentError, IndexError) as exc:
            logger.error("Content selection failed: %s", exc)
            await self._send(
                context,
                game.chat_id,
                "⚠️ Content error — couldn't pick a word. Aborting match.",
            )
            await self._finalize_aborted(context, game)
            return

        participants = game.player_ids
        imposter_id = choose_imposter(
            participants, game.imposter_counts, game.last_imposter_id
        )
        game.imposter_counts[imposter_id] = game.imposter_counts.get(imposter_id, 0) + 1
        game.last_imposter_id = imposter_id

        rnd = Round(
            round_number=game.current_round_number,
            category=category,
            secret_word=word.text,
            question=question.text,
            imposter_id=imposter_id,
            word_id=word.word_id,
            question_id=question.question_id,
            participant_ids=list(participants),
        )
        rnd.round_id = await self.repos.rounds.create(
            game.match_id,
            game.current_round_number,
            category,
            word.text,
            question.text,
            imposter_id,
        )
        game.current_round = rnd

        # --- deliver private roles -----------------------------------------
        failures = await self._deliver_roles(context, game, rnd)
        if failures:
            await self._handle_role_failures(context, game, failures, attempt)
            return

        # --- post the question ---------------------------------------------
        self._transition(game, MatchState.QUESTION_PHASE)
        await self._send(
            context,
            game.chat_id,
            texts.question_post(
                rnd.question,
                _category_name(rnd.category, await self.content.categories()),
                rnd.round_number,
                game.total_rounds,
                self.settings.answer_time_seconds,
            ),
        )
        # DM each participant a convenient answer prompt.
        for uid in rnd.participant_ids:
            await self._dm(context, uid, texts.answer_dm_prompt(rnd.question))

        self._transition(game, MatchState.ANSWER_COLLECTION)
        await self.repos.rounds.set_state(rnd.round_id, MatchState.ANSWER_COLLECTION.value)
        self._schedule(
            context,
            game,
            self.settings.answer_time_seconds,
            self._job_answer_timeout,
            "answer",
        )

    async def _deliver_roles(
        self, context: ContextTypes.DEFAULT_TYPE, game: GameState, rnd: Round
    ) -> list[int]:
        cat_name = _category_name(rnd.category, await self.content.categories())
        failures: list[int] = []
        for uid in rnd.participant_ids:
            if uid == rnd.imposter_id:
                text = texts.role_imposter(cat_name, rnd.round_number, game.total_rounds)
            else:
                text = texts.role_clue_holder(
                    rnd.secret_word, cat_name, rnd.round_number, game.total_rounds
                )
            ok = await self._dm(context, uid, text)
            if not ok:
                failures.append(uid)
        return failures

    async def _handle_role_failures(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        game: GameState,
        failures: list[int],
        attempt: int,
    ) -> None:
        names = [game.display_name(uid) for uid in failures]
        # Mark them not-DM-able and drop them from the game.
        for uid in failures:
            await self.repos.db.execute(
                "UPDATE users SET dm_ok = 0 WHERE user_id = ?", (uid,)
            )
            game.remove_player(uid)
            self._player_index.pop(uid, None)
        await self._send(
            context,
            game.chat_id,
            "⚠️ Couldn't DM these players (they must /start me in private): "
            f"<b>{', '.join(texts.esc(n) for n in names)}</b>. They've been "
            "removed from the match.",
        )
        if game.player_count < self.settings.min_players:
            await self._send(context, game.chat_id, texts.round_aborted_not_enough())
            await self._finalize_aborted(context, game)
            return
        # Roll back this round and retry with the remaining players.
        game.current_round = None
        game.current_round_number -= 1
        # We're in ASSIGNING_ROLES; allow another begin by moving to ROUND_END-like
        # restart: simplest is to call _begin_round which expects a valid source.
        # Re-enter ASSIGNING_ROLES is not allowed from itself, so hop via a manual
        # state reset.
        game.state = MatchState.ROUND_END if game.current_round_number > 0 else MatchState.STARTING
        await self._begin_round(context, game, attempt=attempt + 1)

    # ---------------------------------------------------------------- answers
    async def record_private_answer(
        self, context: ContextTypes.DEFAULT_TYPE, user: User, text: str
    ) -> bool:
        """Handle a private text that might be an answer or guess. Returns True
        if it was consumed by the game."""
        chat_id = self._player_index.get(user.id)
        if chat_id is None:
            return False
        game = self.games.get(chat_id)
        if game is None or game.current_round is None:
            return False

        if (
            game.state == MatchState.ANSWER_COLLECTION
            and user.id in game.current_round.participant_ids
        ):
            await self._record_answer(context, game, user, text)
            return True
        if (
            game.state == MatchState.IMPOSTER_GUESS_PHASE
            and user.id == game.current_round.imposter_id
        ):
            await self._record_guess(context, game, user, text)
            return True
        return False

    async def _record_answer(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        game: GameState,
        user: User,
        text: str,
    ) -> None:
        async with self._lock(game.chat_id):
            rnd = game.current_round
            if rnd is None or game.state != MatchState.ANSWER_COLLECTION:
                await self._dm(context, user.id, texts.answer_too_late())
                return
            if user.id in rnd.answers:
                await self._dm(
                    context, user.id, "You've already answered this round. ✅"
                )
                return
            answer = truncate(text, ANSWER_CHAR_LIMIT)
            rnd.answers[user.id] = answer
            rnd.answer_order.append(user.id)
            await self.repos.answers.save(rnd.round_id, user.id, answer)
            await self._dm(context, user.id, texts.answer_received(answer))
            # Live intel: clue-holder answers are forwarded (anonymously) to
            # the imposter's DM so they can infer the word and blend in.
            if user.id != rnd.imposter_id and rnd.imposter_id in rnd.participant_ids:
                await self._dm(context, rnd.imposter_id, texts.answer_forward(answer))
            await self._send(
                context,
                game.chat_id,
                texts.answer_progress(len(rnd.answers), len(rnd.participant_ids)),
            )
            await self._maybe_advance_after_change(context, game)

    async def _job_answer_timeout(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        game = self.games.get(context.job.chat_id)
        if game is None or game.state != MatchState.ANSWER_COLLECTION:
            return
        async with self._lock(game.chat_id):
            if game.state != MatchState.ANSWER_COLLECTION:
                return
            await self._open_voting(context, game, timed_out=True)

    async def _open_voting(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        game: GameState,
        timed_out: bool = False,
    ) -> None:
        rnd = game.current_round
        if rnd is None:
            return
        self._cancel_job(context, game)
        # Reveal answers.
        await self._send(
            context,
            game.chat_id,
            texts.answers_revealed(game, rnd, timed_out=timed_out),
        )

        if not self._transition(game, MatchState.VOTING_PHASE):
            return
        await self.repos.rounds.set_state(rnd.round_id, MatchState.VOTING_PHASE.value)

        keyboard = self._vote_keyboard(game, rnd)
        msg = await self._send(
            context,
            game.chat_id,
            texts.voting_open(self.settings.vote_time_seconds),
            reply_markup=keyboard,
        )
        if msg is not None:
            rnd.voting_message_id = msg.message_id
        self._schedule(
            context,
            game,
            self.settings.vote_time_seconds,
            self._job_vote_timeout,
            "vote",
        )

    def _vote_keyboard(self, game: GameState, rnd: Round) -> InlineKeyboardMarkup:
        buttons = []
        row: list[InlineKeyboardButton] = []
        for uid in rnd.participant_ids:
            row.append(
                InlineKeyboardButton(
                    game.display_name(uid), callback_data=f"vote:{uid}"
                )
            )
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        return InlineKeyboardMarkup(buttons)

    # ----------------------------------------------------------------- voting
    async def cast_vote(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        chat_id: int,
        voter_id: int,
        target_id: int,
    ) -> str:
        game = self.games.get(chat_id)
        if game is None or game.state != MatchState.VOTING_PHASE or game.current_round is None:
            return "Voting isn't open right now."
        rnd = game.current_round
        if voter_id not in rnd.participant_ids:
            return "You're not playing in this round."
        if voter_id == target_id:
            return "You can't vote for yourself! 🙃"

        async with self._lock(chat_id):
            if game.state != MatchState.VOTING_PHASE:
                return "Voting just closed."
            changed = voter_id in rnd.votes
            rnd.votes[voter_id] = target_id
            await self.repos.votes.save(rnd.round_id, voter_id, target_id)
            target_name = game.display_name(target_id)
            # Update the live progress count on the voting message.
            await self._update_vote_progress(context, game, rnd)
            await self._maybe_advance_after_change(context, game)
        verb = "Changed vote to" if changed else "Voted for"
        return f"{verb} {target_name}"

    async def _update_vote_progress(
        self, context: ContextTypes.DEFAULT_TYPE, game: GameState, rnd: Round
    ) -> None:
        if rnd.voting_message_id is None:
            return
        text = (
            texts.voting_open(self.settings.vote_time_seconds)
            + "\n\n"
            + texts.vote_progress(len(rnd.votes), len(rnd.participant_ids))
        )
        try:
            await context.bot.edit_message_text(
                chat_id=game.chat_id,
                message_id=rnd.voting_message_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=self._vote_keyboard(game, rnd),
            )
        except TelegramError:
            pass

    async def _job_vote_timeout(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        game = self.games.get(context.job.chat_id)
        if game is None or game.state != MatchState.VOTING_PHASE:
            return
        async with self._lock(game.chat_id):
            if game.state != MatchState.VOTING_PHASE:
                return
            await self._close_voting(context, game)

    async def _close_voting(
        self, context: ContextTypes.DEFAULT_TYPE, game: GameState
    ) -> None:
        rnd = game.current_round
        if rnd is None:
            return
        self._cancel_job(context, game)

        # Freeze the voting message (remove buttons).
        if rnd.voting_message_id is not None:
            try:
                await context.bot.edit_message_reply_markup(
                    chat_id=game.chat_id, message_id=rnd.voting_message_id
                )
            except TelegramError:
                pass

        result = tally_votes(rnd.votes)
        # v1 tie policy: no elimination on tie -> imposter survives.
        eliminated = result.eliminated
        rnd.eliminated_id = eliminated
        imposter_caught = eliminated == rnd.imposter_id
        rnd.imposter_caught = imposter_caught

        if not self._transition(game, MatchState.REVEAL_PHASE):
            return
        await self._send(
            context,
            game.chat_id,
            texts.reveal(
                game,
                rnd,
                result.counts,
                imposter_caught,
                result.is_tie and result.has_votes,
                no_votes=not result.has_votes,
            ),
        )

        if imposter_caught:
            await self._do_scoring(context, game)
        else:
            await self._start_imposter_guess(context, game)

    # ------------------------------------------------------------- imposter guess
    async def _start_imposter_guess(
        self, context: ContextTypes.DEFAULT_TYPE, game: GameState
    ) -> None:
        rnd = game.current_round
        if rnd is None:
            return
        if not self._transition(game, MatchState.IMPOSTER_GUESS_PHASE):
            return
        await self.repos.rounds.set_state(
            rnd.round_id, MatchState.IMPOSTER_GUESS_PHASE.value
        )
        cat_name = _category_name(rnd.category, await self.content.categories())
        ok = await self._dm(
            context,
            rnd.imposter_id,
            texts.imposter_guess_prompt(cat_name, self.settings.guess_time_seconds),
        )
        await self._send(
            context,
            game.chat_id,
            texts.imposter_guess_announce(
                game.display_name(rnd.imposter_id), self.settings.guess_time_seconds
            ),
        )
        if not ok:
            # Can't reach the imposter; treat as no guess.
            await self._do_scoring(context, game)
            return
        self._schedule(
            context,
            game,
            self.settings.guess_time_seconds,
            self._job_guess_timeout,
            "guess",
        )

    async def _record_guess(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        game: GameState,
        user: User,
        text: str,
    ) -> None:
        async with self._lock(game.chat_id):
            rnd = game.current_round
            if rnd is None or game.state != MatchState.IMPOSTER_GUESS_PHASE:
                return
            if rnd.imposter_guess is not None:
                await self._dm(context, user.id, "You've already guessed.")
                return
            guess = truncate(text, ANSWER_CHAR_LIMIT)
            rnd.imposter_guess = guess
            rnd.imposter_guessed_correct = guess_matches(guess, rnd.secret_word)
            self._cancel_job(context, game)
            if rnd.imposter_guessed_correct:
                await self._dm(context, user.id, texts.guess_dm_correct(rnd.secret_word))
            else:
                await self._dm(context, user.id, texts.guess_dm_wrong(rnd.secret_word))
            await self._do_scoring(context, game)

    async def _job_guess_timeout(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        game = self.games.get(context.job.chat_id)
        if game is None or game.state != MatchState.IMPOSTER_GUESS_PHASE:
            return
        async with self._lock(game.chat_id):
            if game.state != MatchState.IMPOSTER_GUESS_PHASE:
                return
            rnd = game.current_round
            if rnd is not None and rnd.imposter_guess is None:
                rnd.imposter_guessed_correct = False
                await self._dm(
                    context, rnd.imposter_id, "⏰ Time's up — no guess made."
                )
            await self._do_scoring(context, game)

    # ----------------------------------------------------------------- scoring
    async def _do_scoring(
        self, context: ContextTypes.DEFAULT_TYPE, game: GameState
    ) -> None:
        rnd = game.current_round
        if rnd is None:
            return
        if not self._transition(game, MatchState.SCORING_PHASE):
            return

        awards = score_round(
            imposter_id=rnd.imposter_id,
            clue_holder_ids=rnd.clue_holder_ids(),
            votes=rnd.votes,
            imposter_caught=bool(rnd.imposter_caught),
            imposter_guessed_correct=rnd.imposter_guessed_correct,
        )

        # Announce the guess outcome (group) if there was a guess phase.
        if rnd.imposter_caught is False:
            if rnd.imposter_guess is not None and rnd.imposter_guessed_correct:
                await self._send(context, game.chat_id, texts.guess_correct(game, rnd))
            else:
                await self._send(context, game.chat_id, texts.guess_wrong(game, rnd))

        awards_text: list[str] = []
        for award in awards:
            game.add_score(award.user_id, award.points)
            await self.repos.scores.award(
                game.match_id, rnd.round_id, award.user_id, award.points, award.reason
            )
            await self.repos.users.add_points(award.user_id, award.points)
            awards_text.append(
                f"• {texts.esc(game.display_name(award.user_id))}: "
                f"<b>+{award.points}</b> ({texts.esc(award.reason)})"
            )

        await self.repos.rounds.finish(
            rnd.round_id,
            bool(rnd.imposter_caught),
            rnd.imposter_guess,
            rnd.imposter_guessed_correct,
        )
        await self._send(context, game.chat_id, texts.round_scores(game, awards_text))

        if not self._transition(game, MatchState.ROUND_END):
            return
        await self._after_round(context, game)

    async def _after_round(
        self, context: ContextTypes.DEFAULT_TYPE, game: GameState
    ) -> None:
        game.current_round = None
        if game.current_round_number >= game.total_rounds:
            await self._end_match(context, game)
            return
        await self._send(context, game.chat_id, texts.next_round_in(NEXT_ROUND_DELAY))
        self._schedule(context, game, NEXT_ROUND_DELAY, self._job_next_round, "nextround")

    async def _job_next_round(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        game = self.games.get(context.job.chat_id)
        if game is None or game.state != MatchState.ROUND_END:
            return
        async with self._lock(game.chat_id):
            if game.state != MatchState.ROUND_END:
                return
            await self._begin_round(context, game)

    # ------------------------------------------------------------------- end
    async def _end_match(
        self, context: ContextTypes.DEFAULT_TYPE, game: GameState
    ) -> None:
        if not self._transition(game, MatchState.MATCH_END):
            return
        await self.repos.matches.finish(game.match_id, "COMPLETED")
        await self.repos.users.increment_games_played(game.player_ids)

        board = game.leaderboard()
        if board:
            top = board[0][1]
            winners = [uid for uid, p in board if p == top and top > 0]
            if winners:
                await self.repos.users.increment_wins(winners)

        await self._send(context, game.chat_id, texts.match_over(game))
        self._cleanup(game)

    async def abort(
        self, context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int
    ) -> tuple[bool, str]:
        game = self.games.get(chat_id)
        if game is None:
            return False, "No game to abort here."
        if not game.is_host(user_id):
            return False, texts.only_host("abort the game")
        name = game.display_name(user_id)
        self._cancel_job(context, game)
        await self._finalize_aborted(context, game)
        return True, texts.aborted(name)

    async def _finalize_aborted(
        self, context: ContextTypes.DEFAULT_TYPE, game: GameState
    ) -> None:
        self._cancel_job(context, game)
        game.state = MatchState.ABORTED
        if game.match_id is not None:
            await self.repos.matches.finish(game.match_id, "ABORTED")
        self._cleanup(game)

    def _cleanup(self, game: GameState) -> None:
        for uid in game.player_ids:
            if self._player_index.get(uid) == game.chat_id:
                self._player_index.pop(uid, None)
        self.games.pop(game.chat_id, None)
        self._locks.pop(game.chat_id, None)

    # ======================================================================
    # Shared completion check
    # ======================================================================
    async def _maybe_advance_after_change(
        self, context: ContextTypes.DEFAULT_TYPE, game: GameState
    ) -> None:
        """If everyone has acted in the current phase, advance immediately."""
        rnd = game.current_round
        if rnd is None:
            return
        if game.state == MatchState.ANSWER_COLLECTION and rnd.all_answers_in():
            await self._open_voting(context, game)
        elif game.state == MatchState.VOTING_PHASE and rnd.all_votes_in():
            await self._close_voting(context, game)


def _category_name(key: str, categories: list[dict]) -> str:
    for cat in categories:
        if cat["key"] == key:
            return cat["name"]
    return key
