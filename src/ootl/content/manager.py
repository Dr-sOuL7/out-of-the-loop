"""ContentManager -- the single point of control over game content.

Per the design doc, the bot never touches the content tables directly. The
ContentManager:

* validates content,
* selects random words / prompts,
* avoids repetition (a configurable "recently used" window),
* filters by category and difficulty,
* enforces safety rules (banned terms are deactivated at import time), and
* tracks usage (``times_used`` / ``last_used_at``).
"""
from __future__ import annotations

from dataclasses import dataclass

from ootl.db.database import Database


@dataclass(frozen=True)
class SelectedWord:
    word_id: int
    text: str
    category: str
    difficulty: int


@dataclass(frozen=True)
class SelectedQuestion:
    question_id: int
    text: str
    category: str | None
    difficulty: int


class ContentError(RuntimeError):
    """Raised when no suitable content can be selected."""


class ContentManager:
    def __init__(
        self,
        db: Database,
        word_history_window: int = 40,
        question_history_window: int = 20,
    ) -> None:
        self._db = db
        self._word_window = max(0, word_history_window)
        self._question_window = max(0, question_history_window)

    # -- introspection -------------------------------------------------------
    async def categories(self) -> list[dict]:
        rows = await self._db.fetchall(
            "SELECT key, name, description FROM content_categories ORDER BY name"
        )
        return [dict(r) for r in rows]

    async def category_keys(self) -> list[str]:
        rows = await self._db.fetchall("SELECT key FROM content_categories")
        return [r["key"] for r in rows]

    async def counts(self) -> dict[str, int]:
        words = await self._db.fetchone(
            "SELECT COUNT(*) AS n FROM content_words WHERE active = 1"
        )
        questions = await self._db.fetchone(
            "SELECT COUNT(*) AS n FROM content_questions WHERE active = 1"
        )
        cats = await self._db.fetchone("SELECT COUNT(*) AS n FROM content_categories")
        return {
            "words": int(words["n"]) if words else 0,
            "questions": int(questions["n"]) if questions else 0,
            "categories": int(cats["n"]) if cats else 0,
        }

    # -- selection -----------------------------------------------------------
    async def pick_word(
        self,
        category: str | None = None,
        max_difficulty: int | None = None,
    ) -> SelectedWord:
        """Pick a random active word, avoiding recently-used ones.

        If ``category`` is None a random category is used implicitly (any word).
        """
        filters = ["active = 1"]
        params: list[object] = []
        if category:
            filters.append("category = ?")
            params.append(category)
        if max_difficulty is not None:
            filters.append("difficulty <= ?")
            params.append(max_difficulty)
        where = " AND ".join(filters)

        row = await self._pick_least_recent(
            table="content_words",
            id_col="word_id",
            where=where,
            params=params,
            window=self._word_window,
        )
        if row is None:
            raise ContentError(
                f"No words available (category={category!r}, "
                f"max_difficulty={max_difficulty!r})."
            )
        await self._mark_used("content_words", "word_id", row["word_id"])
        return SelectedWord(
            word_id=row["word_id"],
            text=row["text"],
            category=row["category"],
            difficulty=row["difficulty"],
        )

    async def pick_question(self, category: str | None = None) -> SelectedQuestion:
        """Pick a prompt: category-specific OR generic (category IS NULL)."""
        if category:
            where = "active = 1 AND (category = ? OR category IS NULL)"
            params: list[object] = [category]
        else:
            where = "active = 1"
            params = []

        row = await self._pick_least_recent(
            table="content_questions",
            id_col="question_id",
            where=where,
            params=params,
            window=self._question_window,
        )
        if row is None:
            raise ContentError(f"No questions available (category={category!r}).")
        await self._mark_used("content_questions", "question_id", row["question_id"])
        return SelectedQuestion(
            question_id=row["question_id"],
            text=row["text"],
            category=row["category"],
            difficulty=row["difficulty"],
        )

    # -- internals -----------------------------------------------------------
    async def _pick_least_recent(
        self,
        table: str,
        id_col: str,
        where: str,
        params: list[object],
        window: int,
    ):
        """Pick a random row, excluding the ``window`` most-recently-used rows.

        Falls back to ignoring the exclusion window if the candidate pool is
        smaller than the window (so small banks still work).
        """
        if window > 0:
            exclude_sql = (
                f"{id_col} NOT IN ("
                f"  SELECT {id_col} FROM {table} "
                f"  WHERE last_used_at IS NOT NULL "
                f"  ORDER BY last_used_at DESC LIMIT {int(window)}"
                f")"
            )
            sql = (
                f"SELECT * FROM {table} WHERE {where} AND {exclude_sql} "
                f"ORDER BY RANDOM() LIMIT 1"
            )
            row = await self._db.fetchone(sql, params)
            if row is not None:
                return row

        # Fallback: prefer never-used, then least-recently-used, with a tie-break.
        sql = (
            f"SELECT * FROM {table} WHERE {where} "
            f"ORDER BY (last_used_at IS NOT NULL), last_used_at ASC, RANDOM() "
            f"LIMIT 1"
        )
        return await self._db.fetchone(sql, params)

    async def _mark_used(self, table: str, id_col: str, row_id: int) -> None:
        await self._db.execute(
            f"UPDATE {table} SET times_used = times_used + 1, "
            f"last_used_at = datetime('now') WHERE {id_col} = ?",
            (row_id,),
        )
