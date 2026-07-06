"""Webhook-driven game engine for the serverless (Vercel + Supabase) deployment.

Same state machine and rules as ``ootl.game.engine``, restructured for a world
where nothing runs between requests:

* Live state is a JSONB row per chat, loaded fresh each invocation and locked
  with ``SELECT ... FOR UPDATE`` so concurrent webhooks for one game serialise.
* Phase timers become a ``deadline_at`` column; ``process_due()`` (called by the
  Supabase pg_cron tick and opportunistically after each webhook) advances any
  game whose deadline expired. Phases still advance *instantly* when everyone
  has acted -- the deadline is only the fallback for silent players.

One WebEngine is constructed per invocation with a fresh connection.
"""
from __future__ import annotations

import logging
import secrets

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError

from ootl.config import Settings
from ootl.content.manager import ContentError
from ootl.game import texts
from ootl.game.enums import MatchState, can_transition
from ootl.game.models import GameState, Player, Round, choose_imposter
from ootl.game.scoring import score_round
from ootl.game.tally import tally_votes
from ootl.utils.text import guess_matches, truncate
from ootl.web import content, pg
from ootl.web.state import game_from_json, game_to_json

logger = logging.getLogger(__name__)

ANSWER_CHAR_LIMIT = 200
NEXT_ROUND_DELAY = 6
_KEEP = object()  # sentinel: leave deadline_at untouched on save

GROUP_TYPES = ("group", "supergroup")


class WebEngine:
    def __init__(self, settings: Settings, bot: Bot, conn) -> None:
        self.settings = settings
        self.bot = bot
        self.conn = conn
        self._deadline: object = _KEEP  # seconds (float) | None (clear) | _KEEP

    # ======================================================================
    # Telegram helpers
    # ======================================================================
    async def _send(self, chat_id: int, text: str, **kwargs):
        try:
            return await self.bot.send_message(
                chat_id=chat_id, text=text, parse_mode=ParseMode.HTML, **kwargs
            )
        except TelegramError as exc:
            logger.warning("send to %s failed: %s", chat_id, exc)
            return None

    async def _dm(self, user_id: int, text: str, **kwargs) -> bool:
        try:
            await self.bot.send_message(
                chat_id=user_id, text=text, parse_mode=ParseMode.HTML, **kwargs
            )
            return True
        except TelegramError as exc:
            logger.info("DM to %s failed: %s", user_id, exc)
            return False

    # ======================================================================
    # State machine + persistence plumbing
    # ======================================================================
    def _transition(self, game: GameState, target: MatchState) -> bool:
        if not can_transition(game.state, target):
            logger.warning(
                "chat %s: illegal transition %s -> %s", game.chat_id, game.state, target
            )
            return False
        game.state = target
        return True

    def _schedule(self, game: GameState, kind: str, seconds: float) -> None:
        game.deadline_kind = kind
        self._deadline = float(seconds)

    def _clear_deadline(self, game: GameState) -> None:
        game.deadline_kind = None
        self._deadline = None

    async def _save(self, game: GameState) -> None:
        data = game_to_json(game)
        if self._deadline is _KEEP:
            await pg.save_state_only(self.conn, game.chat_id, data)
        else:
            await pg.save_game_row(self.conn, game.chat_id, data, self._deadline)
        self._deadline = _KEEP

    async def _cleanup(self, game: GameState) -> None:
        await pg.clear_player_chats(self.conn, game.chat_id)
        await pg.delete_game_row(self.conn, game.chat_id)
        self._deadline = _KEEP

    async def _load_locked(self, chat_id: int) -> GameState | None:
        row = await pg.load_game_row(self.conn, chat_id, for_update=True)
        return game_from_json(row["state"]) if row else None

    async def _load(self, chat_id: int) -> GameState | None:
        row = await pg.load_game_row(self.conn, chat_id)
        return game_from_json(row["state"]) if row else None

    # ======================================================================
    # Update routing
    # ======================================================================
    async def handle_update(self, update: Update) -> None:
        if update.callback_query is not None:
            await self._handle_callback(update)
            return
        msg = update.effective_message
        if msg is None or update.effective_user is None or update.effective_user.is_bot:
            return
        text = (msg.text or "").strip()
        chat = update.effective_chat
        if text.startswith("/"):
            await self._handle_command(update, text)
        elif chat.type == "private" and text:
            await self._handle_private_text(update, text)

    async def _handle_command(self, update: Update, text: str) -> None:
        cmd = text.split()[0].lower().split("@")[0].lstrip("/")
        args = text.split()[1:]
        chat = update.effective_chat
        user = update.effective_user
        is_group = chat.type in GROUP_TYPES

        if cmd == "start":
            await self._cmd_start(update)
        elif cmd in ("help",):
            await self._send(chat.id, texts.help_text())
        elif cmd == "rules":
            await self._send(chat.id, texts.rules_text())
        elif cmd == "leaderboard":
            rows = await pg.top_all_time(self.conn)
            await self._send(chat.id, texts.all_time_leaderboard(rows))
        elif cmd in ("create", "join", "leave", "players", "startgame", "score", "abort"):
            if not is_group:
                await self._send(chat.id, texts.private_only_hint())
                return
            handler = {
                "create": self._cmd_create,
                "join": self._cmd_join,
                "leave": self._cmd_leave,
                "players": self._cmd_players,
                "startgame": self._cmd_startgame,
                "score": self._cmd_score,
                "abort": self._cmd_abort,
            }[cmd]
            await handler(chat.id, user, args)

    # ======================================================================
    # Commands
    # ======================================================================
    async def _cmd_start(self, update: Update) -> None:
        user = update.effective_user
        chat = update.effective_chat
        await pg.upsert_user(self.conn, user.id, user.username, user.full_name or "")
        if chat.type == "private":
            await pg.mark_dm_ok(self.conn, user.id)
            await self._send(chat.id, texts.dm_welcome(user.full_name or "there"))
        else:
            await self._send(
                chat.id,
                "👋 Open a private chat with me and press <b>Start</b> so I can DM "
                "you your secret role. Then play here in the group!",
            )

    async def _cmd_create(self, chat_id: int, user, args) -> None:
        await pg.upsert_user(self.conn, user.id, user.username, user.full_name or "")
        host = Player(user.id, user.full_name or f"Player{user.id}", user.username)
        game = GameState(
            chat_id=chat_id, host_id=host.user_id,
            total_rounds=self.settings.default_rounds,
        )
        game.add_player(host)
        async with self.conn.transaction():
            created = await pg.insert_game_row(self.conn, chat_id, game_to_json(game))
        if not created:
            await self._send(chat_id, texts.already_have_room())
            return
        sent = await self._send(
            chat_id,
            texts.room_created(host, self.settings.min_players),
            reply_markup=_lobby_keyboard(),
        )
        if sent is not None:
            async with self.conn.transaction():
                fresh = await self._load_locked(chat_id)
                if fresh is not None:
                    fresh.lobby_message_id = sent.message_id
                    await self._save(fresh)

    async def _cmd_join(self, chat_id: int, user, args) -> str | None:
        """Shared by /join and the Join button; returns a toast for callbacks."""
        await pg.upsert_user(self.conn, user.id, user.username, user.full_name or "")
        player = Player(user.id, user.full_name or f"Player{user.id}", user.username)
        async with self.conn.transaction():
            game = await self._load_locked(chat_id)
            if game is None:
                await self._send(chat_id, texts.not_in_room())
                return "No open room here."
            if game.state != MatchState.LOBBY:
                return "A match is already running."
            if player.user_id in game.players:
                return "You're already in the lobby. 😊"
            if game.player_count >= self.settings.max_players:
                return f"Room is full ({self.settings.max_players})."
            game.add_player(player)
            await self._save(game)
            await self._send(chat_id, texts.joined(player, game.player_count))
            await self._refresh_lobby(game)
        return "Joined! ✅"

    async def _cmd_leave(self, chat_id: int, user, args) -> str | None:
        async with self.conn.transaction():
            game = await self._load_locked(chat_id)
            if game is None or user.id not in game.players:
                return "You're not in a room here."
            name = game.display_name(user.id)
            if game.state == MatchState.LOBBY:
                game.remove_player(user.id)
                if game.player_count == 0:
                    await self._cleanup(game)
                    await self._send(
                        chat_id, texts.left(name, 0) + "\nRoom is now empty and closed."
                    )
                    return "Left."
                extra = ""
                if game.host_id == user.id:
                    game.host_id = game.player_ids[0]
                    extra = f"\n👑 {texts.mention(game.players[game.host_id])} is the new host."
                await self._save(game)
                await self._send(chat_id, texts.left(name, game.player_count) + extra)
                await self._refresh_lobby(game)
                return "Left."
            await self._handle_active_leave(game, user.id, name)
        return "Left."

    async def _cmd_players(self, chat_id: int, user, args) -> None:
        game = await self._load(chat_id)
        if game is None:
            await self._send(chat_id, texts.not_in_room())
            return
        await self._send(
            chat_id,
            texts.player_list(game, self.settings.min_players, self.settings.max_players),
        )

    async def _cmd_score(self, chat_id: int, user, args) -> None:
        game = await self._load(chat_id)
        if game is None or not game.scores:
            await self._send(chat_id, "No active match here. Send /create to start one!")
            return
        await self._send(chat_id, texts.scoreboard(game))

    async def _cmd_abort(self, chat_id: int, user, args) -> str | None:
        async with self.conn.transaction():
            game = await self._load_locked(chat_id)
            if game is None:
                await self._send(chat_id, "No game to abort here.")
                return "Nothing to abort."
            if not game.is_host(user.id):
                await self._send(chat_id, texts.only_host("abort the game"))
                return "Host only."
            name = game.display_name(user.id)
            if game.match_id is not None:
                await pg.finish_match(self.conn, game.match_id, "ABORTED")
            await self._cleanup(game)
            await self._send(chat_id, texts.aborted(name))
        return "Aborted."

    async def _cmd_startgame(self, chat_id: int, user, args) -> str | None:
        rounds = None
        if args:
            try:
                rounds = int(args[0])
            except ValueError:
                rounds = None
        async with self.conn.transaction():
            game = await self._load_locked(chat_id)
            if game is None:
                await self._send(chat_id, texts.not_in_room())
                return "No room."
            if game.state != MatchState.LOBBY:
                await self._send(chat_id, texts.game_already_running())
                return "Already running."
            if not game.is_host(user.id):
                await self._send(chat_id, texts.only_host("start the game"))
                return "Host only."
            if game.player_count < self.settings.min_players:
                await self._send(
                    chat_id,
                    texts.need_more_players(game.player_count, self.settings.min_players),
                )
                return "Not enough players."

            missing = []
            for uid, player in game.players.items():
                if not await pg.is_dm_ok(self.conn, uid):
                    missing.append(player.display_name)
            if missing:
                await self._send(chat_id, texts.dm_required(missing))
                return "Players must /start me in DM first."

            if rounds is not None and rounds > 0:
                game.total_rounds = min(rounds, 20)

            if not self._transition(game, MatchState.STARTING):
                return None
            game.match_id = await pg.create_match(self.conn, chat_id, game.total_rounds)
            await pg.set_player_chats(self.conn, game.player_ids, chat_id)

            if game.lobby_message_id:
                try:
                    await self.bot.edit_message_reply_markup(
                        chat_id=chat_id, message_id=game.lobby_message_id
                    )
                except TelegramError:
                    pass
            await self._send(chat_id, texts.match_starting(game.total_rounds))
            await self._begin_round(game)
        return "Game on!"

    # ======================================================================
    # Callbacks (inline buttons)
    # ======================================================================
    async def _handle_callback(self, update: Update) -> None:
        query = update.callback_query
        data = query.data or ""
        user = query.from_user
        message = query.message
        toast: str | None = None
        if message is None:
            await self._answer_callback(query.id, "This button has expired.")
            return
        chat_id = message.chat.id

        if data == "join":
            toast = await self._cmd_join(chat_id, user, [])
        elif data == "leave":
            toast = await self._cmd_leave(chat_id, user, [])
        elif data == "start":
            toast = await self._cmd_startgame(chat_id, user, [])
        elif data.startswith("ans:"):
            # Option buttons live in the player's DM, so the game chat comes
            # from the player index, not the callback's chat.
            try:
                _, round_no, idx = data.split(":")
                toast = await self._handle_answer_tap(
                    user.id, int(round_no), int(idx), message
                )
            except (ValueError, IndexError):
                toast = "Invalid choice."
        elif data.startswith("guess:"):
            # Same DM-button routing as answers, for the imposter's word pick.
            try:
                _, round_no, idx = data.split(":")
                toast = await self._handle_guess_tap(
                    user.id, int(round_no), int(idx), message
                )
            except (ValueError, IndexError):
                toast = "Invalid choice."
        elif data.startswith("vote:"):
            try:
                target_id = int(data.split(":", 1)[1])
            except ValueError:
                target_id = None
            toast = (
                await self._cast_vote(chat_id, user.id, target_id)
                if target_id is not None
                else "Invalid vote."
            )
        await self._answer_callback(query.id, toast or "OK")

    async def _answer_callback(self, callback_id: str, text: str) -> None:
        try:
            await self.bot.answer_callback_query(callback_id, text=text[:190])
        except TelegramError:
            pass

    # ======================================================================
    # Round flow (ported from ootl.game.engine)
    # ======================================================================
    async def _begin_round(self, game: GameState, attempt: int = 0) -> None:
        if attempt > 3:
            await self._send(game.chat_id, "Couldn't deliver roles. Aborting match.")
            await self._finalize_abort(game)
            return
        if not self._transition(game, MatchState.ASSIGNING_ROLES):
            await self._save(game)
            return

        game.current_round_number += 1
        await pg.set_current_round(self.conn, game.match_id, game.current_round_number)

        try:
            category = content.random_category()
            word = await content.pick_word(
                self.conn, category, self.settings.word_history_window
            )
            question, options = await content.pick_question(
                self.conn, category, self.settings.question_history_window
            )
        except ContentError as exc:
            logger.error("content selection failed: %s", exc)
            await self._send(game.chat_id, "⚠️ Content error — aborting match.")
            await self._finalize_abort(game)
            return

        participants = game.player_ids
        imposter_id = choose_imposter(participants, game.imposter_history)
        rnd = Round(
            round_number=game.current_round_number,
            category=category,
            secret_word=word.text,
            question=question.text,
            options=options,
            imposter_id=imposter_id,
            participant_ids=list(participants),
        )
        rnd.round_id = await pg.create_round(
            self.conn, game.match_id, game.current_round_number,
            category, word.text, question.text, imposter_id,
        )
        game.current_round = rnd

        failures = await self._deliver_roles(game, rnd)
        if failures:
            await self._handle_role_failures(game, failures, attempt)
            return
        # Only successfully-dealt rounds count toward rotation fairness.
        game.imposter_history.append(imposter_id)

        self._transition(game, MatchState.QUESTION_PHASE)
        await self._send(
            game.chat_id,
            texts.question_post(
                rnd.question,
                content.category_name(rnd.category),
                rnd.round_number,
                game.total_rounds,
                self.settings.answer_time_seconds,
                options=rnd.options,
            ),
        )
        keyboard = _options_keyboard(rnd)
        for uid in rnd.participant_ids:
            await self._dm(
                uid, texts.answer_dm_options(rnd.question), reply_markup=keyboard
            )

        self._transition(game, MatchState.ANSWER_COLLECTION)
        await pg.set_round_state(self.conn, rnd.round_id, MatchState.ANSWER_COLLECTION.value)
        self._schedule(game, "answer", self.settings.answer_time_seconds)
        await self._save(game)

    async def _deliver_roles(self, game: GameState, rnd: Round) -> list[int]:
        cat_name = content.category_name(rnd.category)
        failures: list[int] = []
        for uid in rnd.participant_ids:
            if uid == rnd.imposter_id:
                text = texts.role_imposter(cat_name, rnd.round_number, game.total_rounds)
            else:
                text = texts.role_clue_holder(
                    rnd.secret_word, cat_name, rnd.round_number, game.total_rounds
                )
            if not await self._dm(uid, text):
                failures.append(uid)
        return failures

    async def _handle_role_failures(
        self, game: GameState, failures: list[int], attempt: int
    ) -> None:
        names = [game.display_name(uid) for uid in failures]
        for uid in failures:
            await pg.set_dm_broken(self.conn, uid)
            game.remove_player(uid)
        await pg.clear_player_chats(self.conn, game.chat_id, failures)
        await self._send(
            game.chat_id,
            "⚠️ Couldn't DM these players (they must /start me in private): "
            f"<b>{', '.join(texts.esc(n) for n in names)}</b>. They've been "
            "removed from the match.",
        )
        if game.player_count < self.settings.min_players:
            await self._send(game.chat_id, texts.round_aborted_not_enough())
            await self._finalize_abort(game)
            return
        game.current_round = None
        game.current_round_number -= 1
        game.state = (
            MatchState.ROUND_END if game.current_round_number > 0 else MatchState.STARTING
        )
        await self._begin_round(game, attempt=attempt + 1)

    # -------------------------------------------------------------- answers
    async def _handle_private_text(self, update: Update, text: str) -> None:
        user = update.effective_user
        chat_id = await pg.chat_for_player(self.conn, user.id)
        if chat_id is None:
            await self._send(
                user.id,
                "I'm not expecting anything from you right now. Send /help for how "
                "to play, or jump into a game in your group!",
            )
            return
        async with self.conn.transaction():
            game = await self._load_locked(chat_id)
            rnd = game.current_round if game else None
            if game is None or rnd is None:
                return
            if (
                game.state == MatchState.ANSWER_COLLECTION
                and user.id in rnd.participant_ids
            ):
                await self._dm(
                    user.id,
                    "🔘 This round uses <b>options</b> — tap one of the buttons "
                    "I sent above instead of typing!",
                )
            elif (
                game.state == MatchState.IMPOSTER_GUESS_PHASE
                and user.id == rnd.imposter_id
            ):
                if rnd.guess_options:
                    await self._dm(user.id, texts.guess_use_buttons())
                else:
                    await self._record_guess(game, rnd, user.id, text)
            else:
                await self._dm(user.id, texts.not_your_turn_to_answer())

    async def _handle_answer_tap(
        self, user_id: int, round_no: int, idx: int, dm_message
    ) -> str:
        chat_id = await pg.chat_for_player(self.conn, user_id)
        if chat_id is None:
            return "You're not in an active game."
        async with self.conn.transaction():
            game = await self._load_locked(chat_id)
            rnd = game.current_round if game else None
            if (
                game is None
                or rnd is None
                or game.state != MatchState.ANSWER_COLLECTION
                or rnd.round_number != round_no
            ):
                return "⏰ Too late — that answer phase is closed."
            if user_id not in rnd.participant_ids:
                return "You're not playing in this round."
            if user_id in rnd.answers:
                return "You've already picked — no take-backs!"
            if not (0 <= idx < len(rnd.options)):
                return "Invalid choice."
            answer = rnd.options[idx]
            rnd.answers[user_id] = answer
            rnd.answer_order.append(user_id)
            await pg.save_answer(self.conn, rnd.round_id, user_id, answer)
            # Lock the DM message: replace the buttons with the confirmation.
            if dm_message is not None:
                try:
                    await self.bot.edit_message_text(
                        chat_id=dm_message.chat.id,
                        message_id=dm_message.message_id,
                        text=texts.answer_picked(rnd.question, answer),
                        parse_mode=ParseMode.HTML,
                    )
                except TelegramError:
                    pass
            # Live intel: clue-holder picks are forwarded (anonymously) to the
            # imposter's DM so they can infer the word and blend in.
            if user_id != rnd.imposter_id and rnd.imposter_id in rnd.participant_ids:
                await self._dm(rnd.imposter_id, texts.answer_forward(answer))
            await self._send(
                game.chat_id,
                texts.answer_progress(len(rnd.answers), len(rnd.participant_ids)),
            )
            if rnd.all_answers_in():
                await self._open_voting(game)
            else:
                await self._save(game)
        return f"Locked in: {answer}"

    async def _open_voting(self, game: GameState, timed_out: bool = False) -> None:
        rnd = game.current_round
        if rnd is None:
            return
        await self._send(
            game.chat_id, texts.answers_revealed(game, rnd, timed_out=timed_out)
        )
        if not self._transition(game, MatchState.VOTING_PHASE):
            await self._save(game)
            return
        await pg.set_round_state(self.conn, rnd.round_id, MatchState.VOTING_PHASE.value)
        msg = await self._send(
            game.chat_id,
            texts.voting_open(self.settings.vote_time_seconds),
            reply_markup=_vote_keyboard(game, rnd),
        )
        if msg is not None:
            rnd.voting_message_id = msg.message_id
        self._schedule(game, "vote", self.settings.vote_time_seconds)
        await self._save(game)

    # -------------------------------------------------------------- voting
    async def _cast_vote(
        self, chat_id: int, voter_id: int, target_id: int
    ) -> str:
        async with self.conn.transaction():
            game = await self._load_locked(chat_id)
            rnd = game.current_round if game else None
            if game is None or rnd is None or game.state != MatchState.VOTING_PHASE:
                return "Voting isn't open right now."
            if voter_id not in rnd.participant_ids:
                return "You're not playing in this round."
            if voter_id == target_id:
                return "You can't vote for yourself! 🙃"
            if target_id not in rnd.participant_ids:
                return "That player isn't in this round."
            changed = voter_id in rnd.votes
            rnd.votes[voter_id] = target_id
            await pg.save_vote(self.conn, rnd.round_id, voter_id, target_id)
            target_name = game.display_name(target_id)
            await self._update_vote_progress(game, rnd)
            if rnd.all_votes_in():
                await self._close_voting(game)
            else:
                await self._save(game)
        verb = "Changed vote to" if changed else "Voted for"
        return f"{verb} {target_name}"

    async def _update_vote_progress(self, game: GameState, rnd: Round) -> None:
        if rnd.voting_message_id is None:
            return
        text = (
            texts.voting_open(self.settings.vote_time_seconds)
            + "\n\n"
            + texts.vote_progress(len(rnd.votes), len(rnd.participant_ids))
        )
        try:
            await self.bot.edit_message_text(
                chat_id=game.chat_id,
                message_id=rnd.voting_message_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=_vote_keyboard(game, rnd),
            )
        except TelegramError:
            pass

    async def _close_voting(self, game: GameState) -> None:
        rnd = game.current_round
        if rnd is None:
            return
        if rnd.voting_message_id is not None:
            try:
                await self.bot.edit_message_reply_markup(
                    chat_id=game.chat_id, message_id=rnd.voting_message_id
                )
            except TelegramError:
                pass

        result = tally_votes(rnd.votes)
        eliminated = result.eliminated  # None on tie/no votes -> imposter survives
        rnd.eliminated_id = eliminated
        imposter_caught = eliminated == rnd.imposter_id
        rnd.imposter_caught = imposter_caught

        if not self._transition(game, MatchState.REVEAL_PHASE):
            await self._save(game)
            return
        await self._send(
            game.chat_id,
            texts.reveal(
                game, rnd, result.counts, imposter_caught,
                result.is_tie and result.has_votes,
                no_votes=not result.has_votes,
            ),
        )
        if imposter_caught:
            await self._do_scoring(game)
        else:
            await self._start_imposter_guess(game)

    # ------------------------------------------------------- imposter guess
    async def _start_imposter_guess(self, game: GameState) -> None:
        rnd = game.current_round
        if rnd is None or not self._transition(game, MatchState.IMPOSTER_GUESS_PHASE):
            await self._save(game)
            return
        await pg.set_round_state(
            self.conn, rnd.round_id, MatchState.IMPOSTER_GUESS_PHASE.value
        )
        cat_name = content.category_name(rnd.category)
        seconds = self.settings.guess_time_seconds
        decoys = content.decoy_words(rnd.category, rnd.secret_word)
        if decoys:
            guess_options = [rnd.secret_word, *decoys]
            secrets.SystemRandom().shuffle(guess_options)
            rnd.guess_options = guess_options
            ok = await self._dm(
                rnd.imposter_id,
                texts.imposter_guess_prompt_options(cat_name, seconds),
                reply_markup=_guess_keyboard(rnd),
            )
        else:
            # No decoys available for this category -> free-text fallback.
            ok = await self._dm(
                rnd.imposter_id, texts.imposter_guess_prompt(cat_name, seconds)
            )
        await self._send(
            game.chat_id,
            texts.imposter_guess_announce(
                game.display_name(rnd.imposter_id),
                seconds,
                option_count=len(rnd.guess_options) or None,
            ),
        )
        if not ok:
            await self._do_scoring(game)
            return
        self._schedule(game, "guess", seconds)
        await self._save(game)

    async def _record_guess(
        self, game: GameState, rnd: Round, user_id: int, text: str
    ) -> None:
        if rnd.imposter_guess is not None:
            await self._dm(user_id, "You've already guessed.")
            return
        guess = truncate(text, ANSWER_CHAR_LIMIT)
        rnd.imposter_guess = guess
        rnd.imposter_guessed_correct = guess_matches(guess, rnd.secret_word)
        if rnd.imposter_guessed_correct:
            await self._dm(user_id, texts.guess_dm_correct(rnd.secret_word))
        else:
            await self._dm(user_id, texts.guess_dm_wrong(rnd.secret_word))
        await self._do_scoring(game)

    async def _handle_guess_tap(
        self, user_id: int, round_no: int, idx: int, dm_message
    ) -> str:
        chat_id = await pg.chat_for_player(self.conn, user_id)
        if chat_id is None:
            return "You're not in an active game."
        async with self.conn.transaction():
            game = await self._load_locked(chat_id)
            rnd = game.current_round if game else None
            if (
                game is None
                or rnd is None
                or game.state != MatchState.IMPOSTER_GUESS_PHASE
                or rnd.round_number != round_no
            ):
                return "⏰ Too late — the guess phase is closed."
            if user_id != rnd.imposter_id:
                return "Only the imposter can guess."
            if rnd.imposter_guess is not None:
                return "You've already guessed."
            if not (0 <= idx < len(rnd.guess_options)):
                return "Invalid choice."
            guess = rnd.guess_options[idx]
            rnd.imposter_guess = guess
            rnd.imposter_guessed_correct = guess_matches(guess, rnd.secret_word)
            outcome = (
                texts.guess_dm_correct(rnd.secret_word)
                if rnd.imposter_guessed_correct
                else texts.guess_dm_wrong(rnd.secret_word)
            )
            # Lock the DM message: replace the buttons with the outcome.
            edited = False
            if dm_message is not None:
                try:
                    await self.bot.edit_message_text(
                        chat_id=dm_message.chat.id,
                        message_id=dm_message.message_id,
                        text=outcome,
                        parse_mode=ParseMode.HTML,
                    )
                    edited = True
                except TelegramError:
                    pass
            if not edited:
                await self._dm(user_id, outcome)
            await self._do_scoring(game)
        return "🎯 Correct!" if rnd.imposter_guessed_correct else "❌ Wrong…"

    # ------------------------------------------------------------- scoring
    async def _do_scoring(self, game: GameState) -> None:
        rnd = game.current_round
        if rnd is None or not self._transition(game, MatchState.SCORING_PHASE):
            await self._save(game)
            return
        self._clear_deadline(game)

        awards = score_round(
            imposter_id=rnd.imposter_id,
            clue_holder_ids=rnd.clue_holder_ids(),
            votes=rnd.votes,
            imposter_caught=bool(rnd.imposter_caught),
            imposter_guessed_correct=rnd.imposter_guessed_correct,
        )
        if rnd.imposter_caught is False:
            if rnd.imposter_guess is not None and rnd.imposter_guessed_correct:
                await self._send(game.chat_id, texts.guess_correct(game, rnd))
            else:
                await self._send(game.chat_id, texts.guess_wrong(game, rnd))

        awards_text: list[str] = []
        for award in awards:
            game.add_score(award.user_id, award.points)
            await pg.award_score(
                self.conn, game.match_id, rnd.round_id,
                award.user_id, award.points, award.reason,
            )
            await pg.add_points(self.conn, award.user_id, award.points)
            awards_text.append(
                f"• {texts.esc(game.display_name(award.user_id))}: "
                f"<b>+{award.points}</b> ({texts.esc(award.reason)})"
            )
        await pg.finish_round(
            self.conn, rnd.round_id, bool(rnd.imposter_caught),
            rnd.imposter_guess, rnd.imposter_guessed_correct,
        )
        await self._send(game.chat_id, texts.round_scores(game, awards_text))

        if not self._transition(game, MatchState.ROUND_END):
            await self._save(game)
            return
        await self._after_round(game)

    async def _after_round(self, game: GameState) -> None:
        game.current_round = None
        if game.current_round_number >= game.total_rounds:
            await self._end_match(game)
            return
        await self._send(game.chat_id, texts.next_round_in(NEXT_ROUND_DELAY))
        self._schedule(game, "nextround", NEXT_ROUND_DELAY)
        await self._save(game)

    async def _end_match(self, game: GameState) -> None:
        if not self._transition(game, MatchState.MATCH_END):
            await self._save(game)
            return
        await pg.finish_match(self.conn, game.match_id, "COMPLETED")
        await pg.increment_games_played(self.conn, game.player_ids)
        board = game.leaderboard()
        if board:
            top = board[0][1]
            winners = [uid for uid, p in board if p == top and top > 0]
            if winners:
                await pg.increment_wins(self.conn, winners)
        await self._send(game.chat_id, texts.match_over(game))
        await self._cleanup(game)

    async def _finalize_abort(self, game: GameState) -> None:
        if game.match_id is not None:
            await pg.finish_match(self.conn, game.match_id, "ABORTED")
        await self._cleanup(game)

    # ------------------------------------------------------------ mid-match leave
    async def _handle_active_leave(
        self, game: GameState, user_id: int, name: str
    ) -> None:
        was_imposter = (
            game.current_round is not None
            and game.current_round.imposter_id == user_id
        )
        game.remove_player(user_id)
        await pg.clear_player_chats(self.conn, game.chat_id, [user_id])
        rnd = game.current_round
        if rnd is not None:
            if user_id in rnd.participant_ids:
                rnd.participant_ids.remove(user_id)
            rnd.answers.pop(user_id, None)
            if user_id in rnd.answer_order:
                rnd.answer_order.remove(user_id)
            rnd.votes.pop(user_id, None)
            for voter, target in list(rnd.votes.items()):
                if target == user_id:
                    rnd.votes.pop(voter, None)

        await self._send(game.chat_id, texts.left(name, game.player_count))

        if game.player_count < self.settings.min_players:
            await self._send(game.chat_id, texts.round_aborted_not_enough())
            await self._finalize_abort(game)
            return
        if was_imposter:
            await self._send(
                game.chat_id,
                "🚪 The imposter left mid-round — this round is void. Moving on…",
            )
            # Exceptional path: force ROUND_END so the next round (or match
            # end) can proceed from a legal state.
            game.state = MatchState.ROUND_END
            if game.current_round is not None and game.current_round.round_id:
                await pg.set_round_state(
                    self.conn, game.current_round.round_id, "VOIDED"
                )
            await self._after_round(game)
            return
        # A clue-holder left; their absence may complete the current phase.
        if game.state == MatchState.ANSWER_COLLECTION and rnd and rnd.all_answers_in():
            await self._open_voting(game)
        elif game.state == MatchState.VOTING_PHASE and rnd and rnd.all_votes_in():
            await self._close_voting(game)
        else:
            await self._save(game)

    # ======================================================================
    # Deadline (tick) processing
    # ======================================================================
    async def process_due(self, limit: int = 20) -> int:
        """Advance every game whose phase deadline expired. Returns count."""
        processed = 0
        for chat_id in await pg.due_chat_ids(self.conn, limit):
            async with self.conn.transaction():
                row = await pg.load_due_game_row_locked(self.conn, chat_id)
                if row is None:
                    continue  # another worker got it, or it advanced meanwhile
                game = game_from_json(row["state"])
                await self._fire_deadline(game)
                processed += 1
        return processed

    async def _fire_deadline(self, game: GameState) -> None:
        kind = game.deadline_kind
        rnd = game.current_round
        if kind == "answer" and game.state == MatchState.ANSWER_COLLECTION:
            await self._open_voting(game, timed_out=True)
        elif kind == "vote" and game.state == MatchState.VOTING_PHASE:
            await self._close_voting(game)
        elif kind == "guess" and game.state == MatchState.IMPOSTER_GUESS_PHASE:
            if rnd is not None and rnd.imposter_guess is None:
                rnd.imposter_guessed_correct = False
                await self._dm(rnd.imposter_id, "⏰ Time's up — no guess made.")
            await self._do_scoring(game)
        elif kind == "nextround" and game.state == MatchState.ROUND_END:
            await self._begin_round(game)
        else:
            # Stale/mismatched deadline: clear it so the tick stops retrying.
            logger.warning(
                "chat %s: stale deadline %r in state %s", game.chat_id, kind, game.state
            )
            self._clear_deadline(game)
            await self._save(game)

    # ======================================================================
    # Lobby helpers
    # ======================================================================
    async def _refresh_lobby(self, game: GameState) -> None:
        if game.lobby_message_id is None:
            return
        try:
            await self.bot.edit_message_text(
                chat_id=game.chat_id,
                message_id=game.lobby_message_id,
                text=texts.player_list(
                    game, self.settings.min_players, self.settings.max_players
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=_lobby_keyboard(),
            )
        except TelegramError:
            pass


def _lobby_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Join ✅", callback_data="join"),
                InlineKeyboardButton("Leave 🚪", callback_data="leave"),
            ],
            [InlineKeyboardButton("Start ▶️", callback_data="start")],
        ]
    )


def _options_keyboard(rnd: Round) -> InlineKeyboardMarkup:
    """One button per answer option, tapped in the player's DM."""
    rows = []
    for i, opt in enumerate(rnd.options):
        label = f"{texts.OPTION_LETTERS[i]}) {truncate(opt, 28)}"
        rows.append(
            [InlineKeyboardButton(label, callback_data=f"ans:{rnd.round_number}:{i}")]
        )
    return InlineKeyboardMarkup(rows)


def _guess_keyboard(rnd: Round) -> InlineKeyboardMarkup:
    """One button per candidate word for the imposter's final guess."""
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for i, opt in enumerate(rnd.guess_options):
        row.append(
            InlineKeyboardButton(
                truncate(opt, 28), callback_data=f"guess:{rnd.round_number}:{i}"
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def _vote_keyboard(game: GameState, rnd: Round) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for uid in rnd.participant_ids:
        row.append(
            InlineKeyboardButton(game.display_name(uid), callback_data=f"vote:{uid}")
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)
