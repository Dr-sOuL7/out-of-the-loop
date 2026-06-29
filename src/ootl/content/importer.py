"""Import the JSON content files into the SQLite content tables.

Run directly:

    python -m ootl.content.importer            # upsert (keeps usage stats)
    python -m ootl.content.importer --reset     # deactivate-then-reimport

The JSON files under ``content/data`` are the editable source of truth; this
script is the only thing that writes them into the database. Banned terms are
applied here: any word/question whose text contains a banned term is stored
with ``active = 0`` so the ContentManager will never select it.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

from ootl.config import load_settings
from ootl.db.database import Database

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).with_name("data")


def _load_json(name: str):
    path = DATA_DIR / name
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _is_banned(text: str, banned_terms: list[str]) -> bool:
    lowered = text.lower()
    return any(term and term.lower() in lowered for term in banned_terms)


def validate_content() -> list[str]:
    """Validate the JSON files; return a list of human-readable problems."""
    problems: list[str] = []

    categories = _load_json("categories.json")
    category_keys = {c["key"] for c in categories}
    if not category_keys:
        problems.append("categories.json defines no categories")

    words = _load_json("words.json")
    seen_words: set[str] = set()
    for cat, items in words.items():
        if cat not in category_keys:
            problems.append(f"words.json uses unknown category '{cat}'")
        for item in items:
            text = item.get("text", "").strip()
            if not text:
                problems.append(f"empty word text in category '{cat}'")
                continue
            key = text.lower()
            if key in seen_words:
                problems.append(f"duplicate word '{text}'")
            seen_words.add(key)
            diff = item.get("difficulty", 2)
            if diff not in (1, 2, 3):
                problems.append(f"word '{text}' has invalid difficulty {diff}")

    questions = _load_json("questions.json")
    for cat in questions:
        if cat != "generic" and cat not in category_keys:
            problems.append(f"questions.json uses unknown category '{cat}'")

    return problems


async def import_content(db: Database, reset: bool = False) -> dict[str, int]:
    """Import all content into ``db``. Returns counts of imported rows."""
    categories = _load_json("categories.json")
    words = _load_json("words.json")
    questions = _load_json("questions.json")
    banned_doc = _load_json("banned.json")
    banned_terms = [t for t in banned_doc.get("terms", []) if isinstance(t, str)]

    if reset:
        # Keep usage stats but deactivate everything; re-import reactivates
        # whatever is still present in the JSON.
        await db.execute("UPDATE content_words SET active = 0")
        await db.execute("UPDATE content_questions SET active = 0")
        await db.execute("DELETE FROM content_categories")
        await db.execute("DELETE FROM content_banned")

    # -- banned terms --------------------------------------------------------
    for term in banned_terms:
        await db.execute(
            "INSERT OR IGNORE INTO content_banned (term) VALUES (?)", (term.lower(),)
        )

    # -- categories ----------------------------------------------------------
    n_cat = 0
    for cat in categories:
        await db.execute(
            """
            INSERT INTO content_categories (key, name, description)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                name = excluded.name,
                description = excluded.description
            """,
            (cat["key"], cat["name"], cat.get("description")),
        )
        n_cat += 1

    # -- words ---------------------------------------------------------------
    n_words = 0
    n_banned_words = 0
    for cat, items in words.items():
        for item in items:
            text = item["text"].strip()
            difficulty = int(item.get("difficulty", 2))
            active = 0 if _is_banned(text, banned_terms) else 1
            if active == 0:
                n_banned_words += 1
            await db.execute(
                """
                INSERT INTO content_words (text, category, difficulty, active)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(text) DO UPDATE SET
                    category = excluded.category,
                    difficulty = excluded.difficulty,
                    active = excluded.active
                """,
                (text, cat, difficulty, active),
            )
            n_words += 1

    # -- questions -----------------------------------------------------------
    n_questions = 0
    for cat, items in questions.items():
        category = None if cat == "generic" else cat
        for text in items:
            text = text.strip()
            active = 0 if _is_banned(text, banned_terms) else 1
            await db.execute(
                """
                INSERT INTO content_questions (text, category, difficulty, active)
                VALUES (?, ?, 2, ?)
                ON CONFLICT(text) DO UPDATE SET
                    category = excluded.category,
                    active = excluded.active
                """,
                (text, category, active),
            )
            n_questions += 1

    result = {
        "categories": n_cat,
        "words": n_words,
        "questions": n_questions,
        "banned_words_deactivated": n_banned_words,
    }
    logger.info("Content import complete: %s", result)
    return result


async def _async_main(reset: bool) -> None:
    problems = validate_content()
    if problems:
        for p in problems:
            logger.error("Content problem: %s", p)
        raise SystemExit("Content validation failed; aborting import.")

    settings = load_settings()
    db = Database(settings.database_path)
    await db.connect()
    try:
        result = await import_content(db, reset=reset)
    finally:
        await db.close()

    print("Imported content:")
    for key, value in result.items():
        print(f"  {key:>28}: {value}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Import OOTL content into SQLite.")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Deactivate existing content first, then re-import (keeps usage stats).",
    )
    args = parser.parse_args()
    asyncio.run(_async_main(reset=args.reset))


if __name__ == "__main__":
    main()
