"""Tests for the content importer + ContentManager against a temp SQLite DB."""
import pytest

from ootl.content.importer import import_content, validate_content
from ootl.content.manager import ContentError, ContentManager
from ootl.db.database import Database


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "test.db")
    await database.connect()
    await import_content(database)
    yield database
    await database.close()


def test_json_content_validates():
    problems = validate_content()
    assert problems == [], f"content validation problems: {problems}"


async def test_counts_are_populated(db):
    manager = ContentManager(db)
    counts = await manager.counts()
    assert counts["categories"] == 10
    assert counts["words"] >= 400
    assert counts["questions"] >= 100


async def test_pick_word_in_category(db):
    manager = ContentManager(db)
    word = await manager.pick_word(category="food")
    assert word.category == "food"
    assert word.text


async def test_pick_word_unknown_category_raises(db):
    manager = ContentManager(db)
    with pytest.raises(ContentError):
        await manager.pick_word(category="does_not_exist")


async def test_pick_question_returns_category_or_generic(db):
    manager = ContentManager(db)
    q = await manager.pick_question(category="animals")
    assert q.category in (None, "animals")


async def test_usage_tracking_increments(db):
    manager = ContentManager(db)
    word = await manager.pick_word(category="food")
    row = await db.fetchone(
        "SELECT times_used, last_used_at FROM content_words WHERE word_id = ?",
        (word.word_id,),
    )
    assert row["times_used"] == 1
    assert row["last_used_at"] is not None


async def test_anti_repetition_avoids_recent(db):
    # With a small window we should not see an immediate repeat within the window.
    manager = ContentManager(db, word_history_window=10)
    seen = []
    for _ in range(10):
        word = await manager.pick_word(category="objects")
        seen.append(word.text)
    # 10 distinct picks from a >40-word category should be unique.
    assert len(set(seen)) == len(seen)


async def test_banned_words_are_inactive(db):
    # No active word should contain a banned term.
    banned = await db.fetchall("SELECT term FROM content_banned")
    terms = [r["term"] for r in banned]
    rows = await db.fetchall("SELECT text FROM content_words WHERE active = 1")
    for row in rows:
        lowered = row["text"].lower()
        for term in terms:
            assert term not in lowered


async def test_reset_preserves_usage_stats(db):
    manager = ContentManager(db)
    word = await manager.pick_word(category="food")
    # Re-import with reset; usage stats keyed by text should survive.
    await import_content(db, reset=True)
    row = await db.fetchone(
        "SELECT times_used FROM content_words WHERE text = ?", (word.text,)
    )
    assert row["times_used"] == 1
