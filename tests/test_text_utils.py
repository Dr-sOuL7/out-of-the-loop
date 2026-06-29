"""Tests for text normalisation and guess matching."""
from ootl.utils.text import guess_matches, normalize, truncate


def test_normalize_lowercases_and_strips_punctuation():
    assert normalize("  An Umbrella! ") == "an umbrella"
    assert normalize("Café") == "cafe"
    assert normalize("ICE   cream") == "ice cream"


def test_guess_matches_exact_and_case_insensitive():
    assert guess_matches("Umbrella", "umbrella")
    assert guess_matches("  UMBRELLA  ", "umbrella")


def test_guess_matches_ignores_leading_articles():
    assert guess_matches("an umbrella", "umbrella")
    assert guess_matches("the bicycle", "bicycle")


def test_guess_matches_ignores_trailing_plural():
    assert guess_matches("umbrellas", "umbrella")
    assert guess_matches("bicycle", "bicycles")


def test_guess_matches_rejects_wrong_word():
    assert not guess_matches("raincoat", "umbrella")
    assert not guess_matches("", "umbrella")
    assert not guess_matches("umbrella", "")


def test_guess_matches_does_not_overstrip_short_words():
    # "us" should not be reduced to "u"
    assert guess_matches("us", "us")
    assert not guess_matches("us", "u")


def test_truncate():
    assert truncate("hello", 10) == "hello"
    out = truncate("hello world this is long", 10)
    assert len(out) <= 10
    assert out.endswith("…")
