"""Text normalisation and guess matching.

Used to (a) tidy player answers and (b) decide whether the imposter's final
guess matches the secret word. Matching is deliberately forgiving: casing,
surrounding punctuation, leading articles ("a"/"an"/"the") and a trailing
plural "s" are ignored, so "an Umbrella!" matches "umbrella".
"""
from __future__ import annotations

import re
import unicodedata

_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS_RE = re.compile(r"\s+")
_ARTICLES = {"a", "an", "the"}


def normalize(text: str) -> str:
    """Lowercase, strip accents/punctuation and collapse whitespace."""
    if not text:
        return ""
    # Decompose accents (café -> cafe).
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = _PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def _canonical(text: str) -> str:
    """Normalised form with leading articles and a trailing plural removed."""
    words = normalize(text).split()
    while words and words[0] in _ARTICLES:
        words = words[1:]
    canon = " ".join(words)
    # Drop a single trailing plural "s" on the final token (umbrellas -> umbrella),
    # but leave very short tokens alone (e.g. "us").
    if len(canon) > 3 and canon.endswith("s") and not canon.endswith("ss"):
        canon = canon[:-1]
    return canon


def guess_matches(guess: str, secret: str) -> bool:
    """Return True if ``guess`` should be accepted as the ``secret`` word."""
    if not guess or not secret:
        return False
    if normalize(guess) == normalize(secret):
        return True
    return _canonical(guess) == _canonical(secret)


def truncate(text: str, limit: int) -> str:
    """Trim ``text`` to ``limit`` characters, adding an ellipsis if cut."""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"
