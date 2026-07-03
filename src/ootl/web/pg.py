"""Postgres persistence helpers (users, match history, live-state rows).

Counterpart of ``ootl.db.repositories`` for the serverless deployment. All
functions take an open ``psycopg.AsyncConnection``; callers wrap game-mutating
sequences in ``conn.transaction()`` so the ``FOR UPDATE`` row lock on
``live_games`` serialises concurrent webhooks for the same chat.
"""
from __future__ import annotations

import json
from typing import Any, Sequence

import psycopg


# -- users --------------------------------------------------------------------
async def upsert_user(
    conn: psycopg.AsyncConnection, user_id: int, username: str | None, display_name: str
) -> None:
    await conn.execute(
        """
        INSERT INTO users (user_id, username, display_name)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET
            username = EXCLUDED.username,
            display_name = EXCLUDED.display_name
        """,
        (user_id, username, display_name),
    )


async def mark_dm_ok(conn: psycopg.AsyncConnection, user_id: int) -> None:
    await conn.execute("UPDATE users SET dm_ok = TRUE WHERE user_id = %s", (user_id,))


async def set_dm_broken(conn: psycopg.AsyncConnection, user_id: int) -> None:
    await conn.execute("UPDATE users SET dm_ok = FALSE WHERE user_id = %s", (user_id,))


async def is_dm_ok(conn: psycopg.AsyncConnection, user_id: int) -> bool:
    cur = await conn.execute("SELECT dm_ok FROM users WHERE user_id = %s", (user_id,))
    row = await cur.fetchone()
    return bool(row and row["dm_ok"])


async def add_points(conn: psycopg.AsyncConnection, user_id: int, points: int) -> None:
    await conn.execute(
        "UPDATE users SET total_points = total_points + %s WHERE user_id = %s",
        (points, user_id),
    )


async def increment_games_played(
    conn: psycopg.AsyncConnection, user_ids: Sequence[int]
) -> None:
    await conn.execute(
        "UPDATE users SET games_played = games_played + 1 WHERE user_id = ANY(%s)",
        (list(user_ids),),
    )


async def increment_wins(
    conn: psycopg.AsyncConnection, user_ids: Sequence[int]
) -> None:
    await conn.execute(
        "UPDATE users SET wins = wins + 1 WHERE user_id = ANY(%s)",
        (list(user_ids),),
    )


async def top_all_time(conn: psycopg.AsyncConnection, limit: int = 10) -> list[dict]:
    cur = await conn.execute(
        """
        SELECT user_id, display_name, username, total_points, games_played, wins
        FROM users WHERE games_played > 0
        ORDER BY total_points DESC, wins DESC LIMIT %s
        """,
        (limit,),
    )
    return list(await cur.fetchall())


# -- live game rows -------------------------------------------------------------
async def load_game_row(
    conn: psycopg.AsyncConnection, chat_id: int, for_update: bool = False
) -> dict | None:
    lock = " FOR UPDATE" if for_update else ""
    cur = await conn.execute(
        f"SELECT chat_id, state FROM live_games WHERE chat_id = %s{lock}",
        (chat_id,),
    )
    return await cur.fetchone()


async def insert_game_row(
    conn: psycopg.AsyncConnection, chat_id: int, state: dict
) -> bool:
    """Insert a new live-game row; returns False if one already exists."""
    cur = await conn.execute(
        """
        INSERT INTO live_games (chat_id, state) VALUES (%s, %s)
        ON CONFLICT (chat_id) DO NOTHING
        """,
        (chat_id, json.dumps(state)),
    )
    return cur.rowcount == 1


async def save_state_only(
    conn: psycopg.AsyncConnection, chat_id: int, state: dict
) -> None:
    """Persist state without touching the deadline (mid-phase updates)."""
    await conn.execute(
        "UPDATE live_games SET state = %s, updated_at = now() WHERE chat_id = %s",
        (json.dumps(state), chat_id),
    )


async def save_game_row(
    conn: psycopg.AsyncConnection,
    chat_id: int,
    state: dict,
    deadline_seconds: float | None,
) -> None:
    """Persist state; deadline_seconds sets/clears the tick deadline column."""
    if deadline_seconds is None:
        await conn.execute(
            """
            UPDATE live_games
            SET state = %s, deadline_at = NULL, updated_at = now()
            WHERE chat_id = %s
            """,
            (json.dumps(state), chat_id),
        )
    else:
        await conn.execute(
            """
            UPDATE live_games
            SET state = %s,
                deadline_at = now() + make_interval(secs => %s),
                updated_at = now()
            WHERE chat_id = %s
            """,
            (json.dumps(state), deadline_seconds, chat_id),
        )


async def delete_game_row(conn: psycopg.AsyncConnection, chat_id: int) -> None:
    await conn.execute("DELETE FROM live_games WHERE chat_id = %s", (chat_id,))


async def due_chat_ids(conn: psycopg.AsyncConnection, limit: int = 20) -> list[int]:
    """Chats whose phase deadline has expired (candidates for the tick)."""
    cur = await conn.execute(
        """
        SELECT chat_id FROM live_games
        WHERE deadline_at IS NOT NULL AND deadline_at <= now()
        ORDER BY deadline_at LIMIT %s
        """,
        (limit,),
    )
    return [r["chat_id"] for r in await cur.fetchall()]


async def load_due_game_row_locked(
    conn: psycopg.AsyncConnection, chat_id: int
) -> dict | None:
    """Lock a due game for timeout processing; skip if another worker has it.

    Re-checks deadline_at under the lock so a webhook that already advanced the
    phase (and cleared/moved the deadline) isn't double-processed.
    """
    cur = await conn.execute(
        """
        SELECT chat_id, state FROM live_games
        WHERE chat_id = %s AND deadline_at IS NOT NULL AND deadline_at <= now()
        FOR UPDATE SKIP LOCKED
        """,
        (chat_id,),
    )
    return await cur.fetchone()


# -- player -> active chat routing ---------------------------------------------
async def set_player_chats(
    conn: psycopg.AsyncConnection, user_ids: Sequence[int], chat_id: int
) -> None:
    for uid in user_ids:
        await conn.execute(
            """
            INSERT INTO player_chat (user_id, chat_id) VALUES (%s, %s)
            ON CONFLICT (user_id) DO UPDATE SET chat_id = EXCLUDED.chat_id
            """,
            (uid, chat_id),
        )


async def clear_player_chats(
    conn: psycopg.AsyncConnection, chat_id: int, user_ids: Sequence[int] | None = None
) -> None:
    if user_ids is None:
        await conn.execute("DELETE FROM player_chat WHERE chat_id = %s", (chat_id,))
    else:
        await conn.execute(
            "DELETE FROM player_chat WHERE chat_id = %s AND user_id = ANY(%s)",
            (chat_id, list(user_ids)),
        )


async def chat_for_player(conn: psycopg.AsyncConnection, user_id: int) -> int | None:
    cur = await conn.execute(
        "SELECT chat_id FROM player_chat WHERE user_id = %s", (user_id,)
    )
    row = await cur.fetchone()
    return row["chat_id"] if row else None


# -- match / round history -------------------------------------------------------
async def create_match(
    conn: psycopg.AsyncConnection, chat_id: int, total_rounds: int
) -> int:
    cur = await conn.execute(
        """
        INSERT INTO matches (chat_id, total_rounds) VALUES (%s, %s)
        RETURNING match_id
        """,
        (chat_id, total_rounds),
    )
    return (await cur.fetchone())["match_id"]


async def set_current_round(
    conn: psycopg.AsyncConnection, match_id: int, round_number: int
) -> None:
    await conn.execute(
        "UPDATE matches SET current_round = %s WHERE match_id = %s",
        (round_number, match_id),
    )


async def finish_match(
    conn: psycopg.AsyncConnection, match_id: int, status: str = "COMPLETED"
) -> None:
    await conn.execute(
        "UPDATE matches SET status = %s, ended_at = now() WHERE match_id = %s",
        (status, match_id),
    )


async def create_round(
    conn: psycopg.AsyncConnection,
    match_id: int,
    round_number: int,
    category: str,
    secret_word: str,
    question: str,
    imposter_id: int,
) -> int:
    cur = await conn.execute(
        """
        INSERT INTO rounds
            (match_id, round_number, category, secret_word, question, imposter_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING round_id
        """,
        (match_id, round_number, category, secret_word, question, imposter_id),
    )
    return (await cur.fetchone())["round_id"]


async def set_round_state(
    conn: psycopg.AsyncConnection, round_id: int, state: str
) -> None:
    await conn.execute(
        "UPDATE rounds SET state = %s WHERE round_id = %s", (state, round_id)
    )


async def finish_round(
    conn: psycopg.AsyncConnection,
    round_id: int,
    imposter_caught: bool,
    imposter_guess: str | None,
    imposter_guessed_correct: bool | None,
) -> None:
    await conn.execute(
        """
        UPDATE rounds
        SET state = 'ROUND_END', imposter_caught = %s, imposter_guess = %s,
            imposter_guessed_correct = %s, ended_at = now()
        WHERE round_id = %s
        """,
        (imposter_caught, imposter_guess, imposter_guessed_correct, round_id),
    )


async def save_answer(
    conn: psycopg.AsyncConnection, round_id: int, user_id: int, answer_text: str
) -> None:
    await conn.execute(
        """
        INSERT INTO answers (round_id, user_id, answer_text) VALUES (%s, %s, %s)
        ON CONFLICT (round_id, user_id) DO UPDATE SET
            answer_text = EXCLUDED.answer_text, submitted_at = now()
        """,
        (round_id, user_id, answer_text),
    )


async def save_vote(
    conn: psycopg.AsyncConnection, round_id: int, voter_id: int, target_id: int
) -> None:
    await conn.execute(
        """
        INSERT INTO votes (round_id, voter_id, target_id) VALUES (%s, %s, %s)
        ON CONFLICT (round_id, voter_id) DO UPDATE SET
            target_id = EXCLUDED.target_id, submitted_at = now()
        """,
        (round_id, voter_id, target_id),
    )


async def award_score(
    conn: psycopg.AsyncConnection,
    match_id: int,
    round_id: int | None,
    user_id: int,
    points: int,
    reason: str,
) -> None:
    await conn.execute(
        """
        INSERT INTO scores (match_id, round_id, user_id, points_awarded, reason)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (match_id, round_id, user_id, points, reason),
    )


# -- content usage (anti-repetition) ---------------------------------------------
async def recently_used(
    conn: psycopg.AsyncConnection, kind: str, window: int
) -> set[str]:
    if window <= 0:
        return set()
    cur = await conn.execute(
        """
        SELECT item FROM content_usage
        WHERE kind = %s AND last_used_at IS NOT NULL
        ORDER BY last_used_at DESC LIMIT %s
        """,
        (kind, window),
    )
    return {r["item"] for r in await cur.fetchall()}


async def mark_content_used(
    conn: psycopg.AsyncConnection, kind: str, item: str
) -> None:
    await conn.execute(
        """
        INSERT INTO content_usage (kind, item, times_used, last_used_at)
        VALUES (%s, %s, 1, now())
        ON CONFLICT (kind, item) DO UPDATE SET
            times_used = content_usage.times_used + 1, last_used_at = now()
        """,
        (kind, item),
    )
