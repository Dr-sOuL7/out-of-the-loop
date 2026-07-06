"""End-to-end tests for the serverless webhook engine against real Postgres.

Uses ``pgserver`` (embedded PostgreSQL, no root needed) and a fake Telegram
Bot, driving full matches through the exact code paths the Vercel deployment
uses: command handlers, private answers, vote callbacks, and the cron tick.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from psycopg.rows import dict_row

from ootl.config import Settings
from ootl.game.enums import MatchState
from ootl.web import pg
from ootl.web.engine import WebEngine
from ootl.web.state import game_from_json, game_to_json

pgserver = pytest.importorskip("pgserver")

_RAW_SCHEMA = (
    Path(__file__).resolve().parents[1] / "supabase" / "schema.sql"
).read_text()
# Drop `--` comment lines so naive splitting on ';' is safe.
SCHEMA = "\n".join(
    line for line in _RAW_SCHEMA.splitlines() if not line.lstrip().startswith("--")
)

GROUP = -100500
SETTINGS = Settings(
    bot_token="test",
    database_path=Path("/tmp/unused.db"),
    answer_time_seconds=90,
    vote_time_seconds=60,
    guess_time_seconds=45,
    min_players=3,
    max_players=9,
    default_rounds=5,
    word_history_window=10,
    question_history_window=10,
    log_level="WARNING",
)


class FakeBot:
    """Mimics the telegram.Bot surface the engine touches."""

    def __init__(self):
        self._mid = 0
        self.group_msgs: dict[int, list[str]] = {}
        self.dms: dict[int, list[str]] = {}
        self.blocked: set[int] = set()

    async def send_message(self, chat_id, text, **kwargs):
        if chat_id > 0 and chat_id in self.blocked:
            from telegram.error import Forbidden

            raise Forbidden("bot was blocked by the user")
        self._mid += 1
        target = self.dms if chat_id > 0 else self.group_msgs
        target.setdefault(chat_id, []).append(text)
        return SimpleNamespace(message_id=self._mid)

    async def edit_message_text(self, **kwargs):
        return SimpleNamespace(message_id=kwargs.get("message_id", 0))

    async def edit_message_reply_markup(self, **kwargs):
        return SimpleNamespace(message_id=kwargs.get("message_id", 0))

    async def answer_callback_query(self, callback_id, text=None, **kwargs):
        return True

    def group_text(self, chat_id=GROUP) -> str:
        return "\n---\n".join(self.group_msgs.get(chat_id, []))


def user_ns(uid: int, name: str | None = None):
    return SimpleNamespace(
        id=uid, username=None, full_name=name or f"P{uid}", is_bot=False
    )


@pytest.fixture(scope="session")
def pg_uri(tmp_path_factory):
    server = pgserver.get_server(tmp_path_factory.mktemp("pgdata"))
    yield server.get_uri()


@pytest.fixture
async def conn(pg_uri):
    connection = await psycopg.AsyncConnection.connect(
        pg_uri, row_factory=dict_row, autocommit=True
    )
    for stmt in SCHEMA.split(";"):
        if stmt.strip():
            await connection.execute(stmt)
    for table in (
        "live_games", "player_chat", "scores", "votes", "answers",
        "rounds", "matches", "users", "content_usage", "quiz_questions",
    ):
        await connection.execute(f"TRUNCATE {table} CASCADE")
    yield connection
    await connection.close()


@pytest.fixture
def bot():
    return FakeBot()


@pytest.fixture
def engine(conn, bot):
    return WebEngine(SETTINGS, bot, conn)


async def setup_lobby(engine, conn, uids=(1, 2, 3)):
    for uid in uids:
        await pg.upsert_user(conn, uid, None, f"P{uid}")
        await pg.mark_dm_ok(conn, uid)
    await engine._cmd_create(GROUP, user_ns(uids[0]), [])
    for uid in uids[1:]:
        await engine._cmd_join(GROUP, user_ns(uid), [])


async def load_game(conn):
    row = await pg.load_game_row(conn, GROUP)
    return game_from_json(row["state"]) if row else None


async def submit_answer(engine, uid: int, text: str):
    """Send a private text (used for the imposter's word guess)."""
    update = SimpleNamespace(effective_user=user_ns(uid))
    await engine._handle_private_text(update, text)


async def pick_option(engine, conn, uid: int, idx: int = 0) -> str:
    """Tap an answer option button (the answer-phase input)."""
    game = await load_game(conn)
    return await engine._handle_answer_tap(
        uid, game.current_round.round_number, idx, None
    )


# ---------------------------------------------------------------------------
# JSON round-trip
# ---------------------------------------------------------------------------
async def test_state_roundtrip(engine, conn):
    await setup_lobby(engine, conn)
    await engine._cmd_startgame(GROUP, user_ns(1), ["2"])
    game = await load_game(conn)
    again = game_from_json(game_to_json(game))
    assert again.players.keys() == game.players.keys()
    assert again.state == game.state
    assert again.current_round.secret_word == game.current_round.secret_word
    assert again.current_round.participant_ids == game.current_round.participant_ids
    assert again.current_round.options == game.current_round.options
    assert len(again.current_round.options) == 4
    assert again.scores == game.scores
    assert again.imposter_history == game.imposter_history
    assert game.imposter_history == [game.current_round.imposter_id]
    assert again.current_round.guess_options == game.current_round.guess_options


# ---------------------------------------------------------------------------
# Lobby
# ---------------------------------------------------------------------------
async def test_create_join_and_duplicate_join(engine, conn, bot):
    await setup_lobby(engine, conn)
    game = await load_game(conn)
    assert game.state == MatchState.LOBBY
    assert game.player_count == 3
    toast = await engine._cmd_join(GROUP, user_ns(2), [])
    assert "already" in toast.lower()


async def test_startgame_requires_dm_ok(engine, conn, bot):
    for uid in (1, 2, 3):
        await pg.upsert_user(conn, uid, None, f"P{uid}")
    await pg.mark_dm_ok(conn, 1)  # 2 and 3 never DM'd the bot
    await engine._cmd_create(GROUP, user_ns(1), [])
    await engine._cmd_join(GROUP, user_ns(2), [])
    await engine._cmd_join(GROUP, user_ns(3), [])
    await engine._cmd_startgame(GROUP, user_ns(1), [])
    game = await load_game(conn)
    assert game.state == MatchState.LOBBY  # did not start
    assert "haven't opened a private chat" in bot.group_text()


async def test_only_host_can_start(engine, conn):
    await setup_lobby(engine, conn)
    toast = await engine._cmd_startgame(GROUP, user_ns(2), [])
    assert toast == "Host only."
    game = await load_game(conn)
    assert game.state == MatchState.LOBBY


# ---------------------------------------------------------------------------
# Full round: imposter caught
# ---------------------------------------------------------------------------
async def test_full_round_imposter_caught(engine, conn, bot):
    await setup_lobby(engine, conn)
    await engine._cmd_startgame(GROUP, user_ns(1), ["1"])

    game = await load_game(conn)
    assert game.state == MatchState.ANSWER_COLLECTION
    assert game.deadline_kind == "answer"
    rnd = game.current_round
    imposter = rnd.imposter_id
    # Roles were DM'd: clue-holders got the word, imposter did not.
    for uid in (1, 2, 3):
        dm = "\n".join(bot.dms[uid])
        if uid == imposter:
            assert "IMPOSTER" in dm
            assert rnd.secret_word not in dm
        else:
            assert rnd.secret_word in dm

    for i, uid in enumerate((1, 2, 3)):
        await pick_option(engine, conn, uid, idx=i)
    game = await load_game(conn)
    assert game.state == MatchState.VOTING_PHASE  # early-advanced

    # Everyone votes for the imposter (imposter votes someone else).
    for uid in (1, 2, 3):
        target = imposter if uid != imposter else next(
            u for u in (1, 2, 3) if u != uid
        )
        await engine._cast_vote(GROUP, uid, target)

    # rounds=1 -> match should be over and cleaned up.
    assert await load_game(conn) is None
    text = bot.group_text()
    assert "Caught!" in text
    assert "Match over" in text

    # Two clue-holders earned +1 each; recorded in Postgres.
    cur = await conn.execute("SELECT COUNT(*) AS n, SUM(points_awarded) AS p FROM scores")
    row = await cur.fetchone()
    assert row["n"] == 2 and row["p"] == 2
    cur = await conn.execute(
        "SELECT games_played FROM users WHERE user_id = %s", (imposter,)
    )
    assert (await cur.fetchone())["games_played"] == 1
    cur = await conn.execute("SELECT COUNT(*) AS n FROM player_chat")
    assert (await cur.fetchone())["n"] == 0


# ---------------------------------------------------------------------------
# Full round: imposter survives and guesses
# ---------------------------------------------------------------------------
async def test_imposter_survives_and_guesses(engine, conn, bot):
    await setup_lobby(engine, conn, uids=(1, 2, 3, 4))
    await engine._cmd_startgame(GROUP, user_ns(1), ["1"])
    game = await load_game(conn)
    imposter = game.current_round.imposter_id
    word = game.current_round.secret_word
    scapegoat = next(u for u in (1, 2, 3, 4) if u != imposter)

    for uid in (1, 2, 3, 4):
        await pick_option(engine, conn, uid)
    for uid in (1, 2, 3, 4):
        target = scapegoat if uid != scapegoat else imposter
        await engine._cast_vote(GROUP, uid, target)

    game = await load_game(conn)
    assert game.state == MatchState.IMPOSTER_GUESS_PHASE
    assert game.deadline_kind == "guess"
    # The word must NOT be revealed while the imposter still has to guess it.
    assert word not in bot.group_text()
    assert "stays hidden" in bot.group_text()

    # The guess is now a 4-option pick: the secret word + 3 decoys.
    opts = game.current_round.guess_options
    assert len(opts) == 4
    assert word in opts
    assert len(set(opts)) == 4

    # Typing during the guess phase nudges to the buttons, consumes nothing.
    await submit_answer(engine, imposter, word)
    assert any("tap one of the word buttons" in m for m in bot.dms[imposter])
    game = await load_game(conn)
    assert game.state == MatchState.IMPOSTER_GUESS_PHASE

    toast = await engine._handle_guess_tap(
        imposter, game.current_round.round_number, opts.index(word), None
    )
    assert "Correct" in toast
    assert await load_game(conn) is None  # match over (1 round)
    text = bot.group_text()
    assert "survived" in text
    assert "correct!" in text.lower()
    assert word in text  # now the word is out

    cur = await conn.execute(
        "SELECT SUM(points_awarded) AS p FROM scores WHERE user_id = %s", (imposter,)
    )
    assert (await cur.fetchone())["p"] == 3  # +2 survive, +1 guess
    cur = await conn.execute("SELECT wins FROM users WHERE user_id = %s", (imposter,))
    assert (await cur.fetchone())["wins"] == 1


async def test_imposter_wrong_guess_no_bonus(engine, conn, bot):
    await setup_lobby(engine, conn, uids=(1, 2, 3, 4))
    await engine._cmd_startgame(GROUP, user_ns(1), ["1"])
    game = await load_game(conn)
    imposter = game.current_round.imposter_id
    word = game.current_round.secret_word
    scapegoat = next(u for u in (1, 2, 3, 4) if u != imposter)

    for uid in (1, 2, 3, 4):
        await pick_option(engine, conn, uid)
    for uid in (1, 2, 3, 4):
        target = scapegoat if uid != scapegoat else imposter
        await engine._cast_vote(GROUP, uid, target)

    game = await load_game(conn)
    opts = game.current_round.guess_options
    wrong_idx = next(i for i, o in enumerate(opts) if o != word)
    # A clue-holder can't tap the imposter's buttons.
    other = next(u for u in (1, 2, 3, 4) if u != imposter)
    toast = await engine._handle_guess_tap(
        other, game.current_round.round_number, wrong_idx, None
    )
    assert "Only the imposter" in toast

    toast = await engine._handle_guess_tap(
        imposter, game.current_round.round_number, wrong_idx, None
    )
    assert "Wrong" in toast
    assert await load_game(conn) is None  # match over (1 round)
    assert word in bot.group_text()  # word revealed after the failed guess
    cur = await conn.execute(
        "SELECT SUM(points_awarded) AS p FROM scores WHERE user_id = %s", (imposter,)
    )
    assert (await cur.fetchone())["p"] == 2  # +2 survive, no guess bonus


# ---------------------------------------------------------------------------
# Imposter rotation: never the same player twice in a row
# ---------------------------------------------------------------------------
async def test_imposter_rotates_between_rounds(engine, conn, bot):
    await setup_lobby(engine, conn)
    await engine._cmd_startgame(GROUP, user_ns(1), ["3"])

    imposters = []
    for _ in range(2):
        game = await load_game(conn)
        imposters.append(game.current_round.imposter_id)
        # Finish the round: everyone answers, everyone votes out the imposter.
        for uid in (1, 2, 3):
            await pick_option(engine, conn, uid)
        imposter = imposters[-1]
        for uid in (1, 2, 3):
            target = imposter if uid != imposter else next(
                u for u in (1, 2, 3) if u != uid
            )
            await engine._cast_vote(GROUP, uid, target)
        await conn.execute(
            "UPDATE live_games SET deadline_at = now() - interval '1 second'"
        )
        await engine.process_due()  # fire the next-round deadline

    game = await load_game(conn)
    imposters.append(game.current_round.imposter_id)
    assert game.imposter_history == imposters
    # Back-to-back repeats are impossible with 3 players.
    assert imposters[0] != imposters[1]
    assert imposters[1] != imposters[2]
    # After 3 rounds with 3 players, everyone has been the imposter once.
    assert set(imposters) == {1, 2, 3}


# ---------------------------------------------------------------------------
# Tie -> no elimination -> imposter survives
# ---------------------------------------------------------------------------
async def test_tie_means_imposter_survives(engine, conn, bot):
    await setup_lobby(engine, conn, uids=(1, 2, 3, 4))
    await engine._cmd_startgame(GROUP, user_ns(1), ["1"])
    game = await load_game(conn)
    imposter = game.current_round.imposter_id
    others = [u for u in (1, 2, 3, 4) if u != imposter]

    for uid in (1, 2, 3, 4):
        await pick_option(engine, conn, uid)
    # Force a 2-2 tie between the imposter and one clue-holder.
    await engine._cast_vote(GROUP, others[0], imposter)
    await engine._cast_vote(GROUP, others[1], imposter)
    await engine._cast_vote(GROUP, others[2], others[0])
    await engine._cast_vote(GROUP, imposter, others[0])

    game = await load_game(conn)
    assert game.state == MatchState.IMPOSTER_GUESS_PHASE
    assert "tie" in bot.group_text().lower()


# ---------------------------------------------------------------------------
# Deadline tick: silent players don't stall the game
# ---------------------------------------------------------------------------
async def test_tick_advances_expired_answer_phase(engine, conn, bot):
    await setup_lobby(engine, conn)
    await engine._cmd_startgame(GROUP, user_ns(1), ["1"])
    await pick_option(engine, conn, 1)  # only one player answers

    processed = await engine.process_due()
    assert processed == 0  # deadline not reached yet

    await conn.execute(
        "UPDATE live_games SET deadline_at = now() - interval '1 second'"
    )
    processed = await engine.process_due()
    assert processed == 1
    game = await load_game(conn)
    assert game.state == MatchState.VOTING_PHASE
    assert game.deadline_kind == "vote"
    assert "(no answer)" in bot.group_text()
    assert "Time's up" in bot.group_text()  # timeout is called out explicitly


async def test_option_picks_forwarded_to_imposter(engine, conn, bot):
    await setup_lobby(engine, conn)
    await engine._cmd_startgame(GROUP, user_ns(1), ["1"])
    game = await load_game(conn)
    rnd = game.current_round
    imposter = rnd.imposter_id
    clue_holders = [u for u in (1, 2, 3) if u != imposter]

    # Imposter picks first: nothing is forwarded to anyone.
    toast = await pick_option(engine, conn, imposter, idx=3)
    assert "Locked in" in toast
    for uid in (1, 2, 3):
        assert not any("Intercepted" in m for m in bot.dms.get(uid, []))

    # A clue-holder's pick is forwarded (anonymously) to the imposter.
    await pick_option(engine, conn, clue_holders[0], idx=1)
    imposter_dm = "\n".join(bot.dms[imposter])
    assert "Intercepted answer" in imposter_dm
    assert rnd.options[1] in imposter_dm
    assert f"P{clue_holders[0]}" not in imposter_dm  # no names leaked

    # Double-pick is rejected; picks lock on first tap.
    toast = await pick_option(engine, conn, clue_holders[0], idx=2)
    assert "already picked" in toast

    await pick_option(engine, conn, clue_holders[1], idx=0)
    # Clue-holders never receive forwards.
    for uid in clue_holders:
        assert not any("Intercepted" in m for m in bot.dms.get(uid, []))
    # All three answered -> voting opened, reveal contains the option text.
    game = await load_game(conn)
    assert game.state == MatchState.VOTING_PHASE
    assert rnd.options[1] in bot.group_text()


async def test_quiz_questions_table_takes_priority(engine, conn, bot):
    await conn.execute(
        """
        INSERT INTO quiz_questions (category, question, option_a, option_b, option_c, option_d)
        VALUES ('generic', 'CUSTOM QUESTION FROM TABLE?', 'One', 'Two', 'Three', 'Four')
        """
    )
    await setup_lobby(engine, conn)
    await engine._cmd_startgame(GROUP, user_ns(1), ["1"])
    game = await load_game(conn)
    assert game.current_round.question == "CUSTOM QUESTION FROM TABLE?"
    assert game.current_round.options == ["One", "Two", "Three", "Four"]


async def test_tick_guess_timeout_scores_without_bonus(engine, conn, bot):
    await setup_lobby(engine, conn, uids=(1, 2, 3, 4))
    await engine._cmd_startgame(GROUP, user_ns(1), ["1"])
    game = await load_game(conn)
    imposter = game.current_round.imposter_id
    scapegoat = next(u for u in (1, 2, 3, 4) if u != imposter)
    for uid in (1, 2, 3, 4):
        await pick_option(engine, conn, uid)
    for uid in (1, 2, 3, 4):
        target = scapegoat if uid != scapegoat else imposter
        await engine._cast_vote(GROUP, uid, target)

    game = await load_game(conn)
    assert game.state == MatchState.IMPOSTER_GUESS_PHASE
    await conn.execute(
        "UPDATE live_games SET deadline_at = now() - interval '1 second'"
    )
    assert await engine.process_due() == 1
    assert await load_game(conn) is None  # match ended
    cur = await conn.execute(
        "SELECT SUM(points_awarded) AS p FROM scores WHERE user_id = %s", (imposter,)
    )
    assert (await cur.fetchone())["p"] == 2  # survive only, no guess bonus


# ---------------------------------------------------------------------------
# Multi-round: nextround deadline chains into round 2
# ---------------------------------------------------------------------------
async def test_next_round_via_tick(engine, conn, bot):
    await setup_lobby(engine, conn)
    await engine._cmd_startgame(GROUP, user_ns(1), ["2"])
    game = await load_game(conn)
    imposter = game.current_round.imposter_id
    for uid in (1, 2, 3):
        await pick_option(engine, conn, uid)
    for uid in (1, 2, 3):
        target = imposter if uid != imposter else next(u for u in (1, 2, 3) if u != uid)
        await engine._cast_vote(GROUP, uid, target)

    game = await load_game(conn)
    assert game.state == MatchState.ROUND_END
    assert game.deadline_kind == "nextround"

    await conn.execute(
        "UPDATE live_games SET deadline_at = now() - interval '1 second'"
    )
    assert await engine.process_due() == 1
    game = await load_game(conn)
    assert game.state == MatchState.ANSWER_COLLECTION
    assert game.current_round_number == 2


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------
async def test_imposter_leaving_voids_round(engine, conn, bot):
    await setup_lobby(engine, conn, uids=(1, 2, 3, 4))
    await engine._cmd_startgame(GROUP, user_ns(1), ["2"])
    game = await load_game(conn)
    imposter = game.current_round.imposter_id
    await engine._cmd_leave(GROUP, user_ns(imposter), [])
    game = await load_game(conn)
    assert game is not None
    assert game.state == MatchState.ROUND_END  # round voided, next scheduled
    assert "void" in bot.group_text()


async def test_leave_below_minimum_aborts(engine, conn, bot):
    await setup_lobby(engine, conn)
    await engine._cmd_startgame(GROUP, user_ns(1), ["2"])
    game = await load_game(conn)
    # A non-imposter clue-holder leaves -> only 2 remain -> abort.
    leaver = next(u for u in (1, 2, 3) if u != game.current_round.imposter_id)
    await engine._cmd_leave(GROUP, user_ns(leaver), [])
    assert await load_game(conn) is None
    cur = await conn.execute("SELECT status FROM matches")
    assert (await cur.fetchone())["status"] == "ABORTED"


async def test_dm_failure_on_start_removes_player(engine, conn, bot):
    await setup_lobby(engine, conn, uids=(1, 2, 3, 4))
    bot.blocked.add(4)  # user 4 blocked the bot after registering
    await engine._cmd_startgame(GROUP, user_ns(1), ["1"])
    game = await load_game(conn)
    assert game is not None
    assert 4 not in game.players
    assert game.state == MatchState.ANSWER_COLLECTION
    assert len(game.current_round.participant_ids) == 3


async def test_abort_by_host(engine, conn, bot):
    await setup_lobby(engine, conn)
    await engine._cmd_startgame(GROUP, user_ns(1), ["3"])
    toast = await engine._cmd_abort(GROUP, user_ns(2), [])
    assert toast == "Host only."
    toast = await engine._cmd_abort(GROUP, user_ns(1), [])
    assert toast == "Aborted."
    assert await load_game(conn) is None


async def test_vote_validation(engine, conn):
    await setup_lobby(engine, conn)
    await engine._cmd_startgame(GROUP, user_ns(1), ["1"])
    for uid in (1, 2, 3):
        await pick_option(engine, conn, uid)
    assert "yourself" in await engine._cast_vote(GROUP, 1, 1)
    assert "isn't in this round" in await engine._cast_vote(GROUP, 1, 999)
    assert "not playing" in await engine._cast_vote(GROUP, 999, 1)


async def test_content_usage_tracked(engine, conn):
    await setup_lobby(engine, conn)
    await engine._cmd_startgame(GROUP, user_ns(1), ["1"])
    cur = await conn.execute("SELECT kind, COUNT(*) AS n FROM content_usage GROUP BY kind")
    kinds = {r["kind"]: r["n"] for r in await cur.fetchall()}
    assert kinds.get("word") == 1
    assert kinds.get("question") == 1
