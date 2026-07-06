"""Fairness-aware imposter selection.

A fresh uniform draw each round produces frequent repeat-imposter streaks
(with 3 players, 1-in-3 every round). Instead:

1. the previous round's imposter is excluded (never twice in a row), and
2. among the rest, only players who've been imposter the *fewest* times this
   match are eligible -- picked at random as the tie-break.

Result: unpredictable to players, but guaranteed to rotate evenly.
"""
from __future__ import annotations

import secrets
from typing import Sequence


def choose_imposter(
    participants: Sequence[int],
    imposter_counts: dict[int, int] | None = None,
    last_imposter_id: int | None = None,
) -> int:
    if not participants:
        raise ValueError("choose_imposter needs at least one participant")
    counts = imposter_counts or {}

    candidates = list(participants)
    if last_imposter_id is not None and len(candidates) > 1:
        without_last = [p for p in candidates if p != last_imposter_id]
        if without_last:
            candidates = without_last

    min_count = min(counts.get(p, 0) for p in candidates)
    least_used = [p for p in candidates if counts.get(p, 0) == min_count]
    return secrets.choice(least_used)
