"""Repository helpers for persisting game state and reading stats.

These wrap :class:`ootl.db.database.Database` with intention-revealing methods so
the rest of the app never writes raw SQL. Persistence is used for player
profiles, all-time stats and a full audit trail of matches/rounds/votes/scores;
the *live* match state lives in memory in the game engine.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ootl.db.database import Database


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
class UserRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert(
        self, user_id: int, username: str | None, display_name: str
    ) -> None:
        await self._db.execute(
            """
            INSERT INTO users (user_id, username, display_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                display_name = excluded.display_name
            """,
            (user_id, username, display_name),
        )

    async def mark_dm_ok(self, user_id: int) -> None:
        await self._db.execute(
            "UPDATE users SET dm_ok = 1 WHERE user_id = ?", (user_id,)
        )

    async def is_dm_ok(self, user_id: int) -> bool:
        row = await self._db.fetchone(
            "SELECT dm_ok FROM users WHERE user_id = ?", (user_id,)
        )
        return bool(row and row["dm_ok"])

    async def add_points(self, user_id: int, points: int) -> None:
        await self._db.execute(
            "UPDATE users SET total_points = total_points + ? WHERE user_id = ?",
            (points, user_id),
        )

    async def increment_games_played(self, user_ids: Sequence[int]) -> None:
        await self._db.executemany(
            "UPDATE users SET games_played = games_played + 1 WHERE user_id = ?",
            [(uid,) for uid in user_ids],
        )

    async def increment_wins(self, user_ids: Sequence[int]) -> None:
        await self._db.executemany(
            "UPDATE users SET wins = wins + 1 WHERE user_id = ?",
            [(uid,) for uid in user_ids],
        )

    async def top_all_time(self, limit: int = 10) -> list[dict]:
        rows = await self._db.fetchall(
            """
            SELECT user_id, display_name, username, total_points, games_played, wins
            FROM users
            WHERE games_played > 0
            ORDER BY total_points DESC, wins DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Matches / rounds / answers / votes / scores
# ---------------------------------------------------------------------------
class MatchRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(
        self, chat_id: int, total_rounds: int, room_id: int | None = None
    ) -> int:
        return await self._db.execute(
            """
            INSERT INTO matches (room_id, chat_id, status, total_rounds, current_round)
            VALUES (?, ?, 'IN_PROGRESS', ?, 0)
            """,
            (room_id, chat_id, total_rounds),
        )

    async def set_current_round(self, match_id: int, round_number: int) -> None:
        await self._db.execute(
            "UPDATE matches SET current_round = ? WHERE match_id = ?",
            (round_number, match_id),
        )

    async def finish(self, match_id: int, status: str = "COMPLETED") -> None:
        await self._db.execute(
            "UPDATE matches SET status = ?, ended_at = datetime('now') WHERE match_id = ?",
            (status, match_id),
        )


class RoundRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(
        self,
        match_id: int,
        round_number: int,
        category: str,
        secret_word: str,
        question: str,
        imposter_id: int,
    ) -> int:
        return await self._db.execute(
            """
            INSERT INTO rounds
                (match_id, round_number, category, secret_word, question,
                 imposter_id, state)
            VALUES (?, ?, ?, ?, ?, ?, 'ASSIGNING_ROLES')
            """,
            (match_id, round_number, category, secret_word, question, imposter_id),
        )

    async def set_state(self, round_id: int, state: str) -> None:
        await self._db.execute(
            "UPDATE rounds SET state = ? WHERE round_id = ?", (state, round_id)
        )

    async def finish(
        self,
        round_id: int,
        imposter_caught: bool,
        imposter_guess: str | None,
        imposter_guessed_correct: bool | None,
    ) -> None:
        await self._db.execute(
            """
            UPDATE rounds
            SET state = 'ROUND_END',
                imposter_caught = ?,
                imposter_guess = ?,
                imposter_guessed_correct = ?,
                ended_at = datetime('now')
            WHERE round_id = ?
            """,
            (
                1 if imposter_caught else 0,
                imposter_guess,
                None if imposter_guessed_correct is None else int(imposter_guessed_correct),
                round_id,
            ),
        )


class AnswerRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def save(self, round_id: int, user_id: int, answer_text: str) -> None:
        await self._db.execute(
            """
            INSERT INTO answers (round_id, user_id, answer_text)
            VALUES (?, ?, ?)
            ON CONFLICT(round_id, user_id) DO UPDATE SET
                answer_text = excluded.answer_text,
                submitted_at = datetime('now')
            """,
            (round_id, user_id, answer_text),
        )


class VoteRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def save(self, round_id: int, voter_id: int, target_id: int) -> None:
        await self._db.execute(
            """
            INSERT INTO votes (round_id, voter_id, target_id)
            VALUES (?, ?, ?)
            ON CONFLICT(round_id, voter_id) DO UPDATE SET
                target_id = excluded.target_id,
                submitted_at = datetime('now')
            """,
            (round_id, voter_id, target_id),
        )


class ScoreRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def award(
        self,
        match_id: int,
        round_id: int | None,
        user_id: int,
        points: int,
        reason: str,
    ) -> None:
        await self._db.execute(
            """
            INSERT INTO scores (match_id, round_id, user_id, points_awarded, reason)
            VALUES (?, ?, ?, ?, ?)
            """,
            (match_id, round_id, user_id, points, reason),
        )

    async def match_totals(self, match_id: int) -> list[dict]:
        rows = await self._db.fetchall(
            """
            SELECT s.user_id, u.display_name, u.username,
                   SUM(s.points_awarded) AS points
            FROM scores s
            LEFT JOIN users u ON u.user_id = s.user_id
            WHERE s.match_id = ?
            GROUP BY s.user_id
            ORDER BY points DESC
            """,
            (match_id,),
        )
        return [dict(r) for r in rows]


@dataclass
class Repositories:
    """Bundle of all repositories, sharing one Database connection."""

    db: Database
    users: UserRepository
    matches: MatchRepository
    rounds: RoundRepository
    answers: AnswerRepository
    votes: VoteRepository
    scores: ScoreRepository

    @classmethod
    def build(cls, db: Database) -> "Repositories":
        return cls(
            db=db,
            users=UserRepository(db),
            matches=MatchRepository(db),
            rounds=RoundRepository(db),
            answers=AnswerRepository(db),
            votes=VoteRepository(db),
            scores=ScoreRepository(db),
        )
