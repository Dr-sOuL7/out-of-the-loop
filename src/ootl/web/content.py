"""Content selection for the serverless deployment.

The JSON files in ``ootl/content/data`` ship inside the Vercel bundle and are
the source of truth (exactly as the design doc prescribes); only *usage* state
(anti-repetition, times_used) lives in Postgres. Banned terms are filtered at
load time so they can never be selected.
"""
from __future__ import annotations

import json
import secrets
from functools import lru_cache
from pathlib import Path

import psycopg

from ootl.content.manager import ContentError, SelectedQuestion, SelectedWord
from ootl.web import pg

DATA_DIR = Path(__file__).resolve().parents[1] / "content" / "data"


def _load(name: str):
    with (DATA_DIR / name).open(encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def load_bundle() -> dict:
    """Load and pre-filter all content once per process (warm invocations reuse)."""
    categories = _load("categories.json")
    words = _load("words.json")
    questions = _load("questions.json")
    banned = [t.lower() for t in _load("banned.json").get("terms", []) if isinstance(t, str)]

    def ok(text: str) -> bool:
        lowered = text.lower()
        return not any(term in lowered for term in banned)

    words_by_cat = {
        cat: [w for w in items if ok(w["text"])] for cat, items in words.items()
    }
    questions_by_cat = {
        cat: [q for q in items if ok(q["q"])] for cat, items in questions.items()
    }
    return {
        "categories": categories,
        "category_keys": [c["key"] for c in categories],
        "category_names": {c["key"]: c["name"] for c in categories},
        "words": words_by_cat,
        "questions": questions_by_cat,
    }


def category_name(key: str) -> str:
    return load_bundle()["category_names"].get(key, key)


def random_category() -> str:
    return secrets.choice(load_bundle()["category_keys"])


def decoy_words(category: str, exclude: str, count: int = 3) -> list[str]:
    """Random distinct same-category words for the imposter's guess options."""
    pool = [
        w["text"]
        for w in load_bundle()["words"].get(category, [])
        if w["text"].lower() != exclude.lower()
    ]
    if len(pool) <= count:
        return pool
    return secrets.SystemRandom().sample(pool, count)


async def pick_word(
    conn: psycopg.AsyncConnection, category: str, window: int
) -> SelectedWord:
    pool = load_bundle()["words"].get(category, [])
    if not pool:
        raise ContentError(f"No words for category {category!r}.")
    recent = await pg.recently_used(conn, "word", window)
    candidates = [w for w in pool if w["text"] not in recent] or pool
    chosen = secrets.choice(candidates)
    await pg.mark_content_used(conn, "word", chosen["text"])
    return SelectedWord(
        word_id=0,
        text=chosen["text"],
        category=category,
        difficulty=int(chosen.get("difficulty", 2)),
    )


async def pick_question(
    conn: psycopg.AsyncConnection, category: str, window: int
) -> tuple[SelectedQuestion, list[str]]:
    """Pick a question + its 4 options.

    The Supabase ``quiz_questions`` table (user-editable, CSV-seeded) is the
    primary source; the bundled JSON is the fallback when it's empty. Returns
    ``(question, options)``.
    """
    pool: list[tuple[str, list[str], str]] = []  # (text, options, category)
    try:
        # Savepoint: if the table doesn't exist yet, roll back cleanly without
        # poisoning the caller's enclosing transaction.
        async with conn.transaction():
            cur = await conn.execute(
                """
                SELECT question, option_a, option_b, option_c, option_d, category
                FROM quiz_questions
                WHERE active AND (category = %s OR category = 'generic')
                """,
                (category,),
            )
            rows = await cur.fetchall()
        pool = [
            (
                r["question"],
                [r["option_a"], r["option_b"], r["option_c"], r["option_d"]],
                r["category"],
            )
            for r in rows
        ]
    except psycopg.Error:
        pool = []

    if not pool:
        bundle = load_bundle()["questions"]
        for cat in (category, "generic"):
            for item in bundle.get(cat, []):
                pool.append((item["q"], list(item["options"]), cat))
    if not pool:
        raise ContentError(f"No questions for category {category!r}.")

    recent = await pg.recently_used(conn, "question", window)
    candidates = [p for p in pool if p[0] not in recent] or pool
    text, options, qcat = secrets.choice(candidates)
    await pg.mark_content_used(conn, "question", text)
    question = SelectedQuestion(
        question_id=0,
        text=text,
        category=None if qcat == "generic" else qcat,
        difficulty=2,
    )
    return question, options
