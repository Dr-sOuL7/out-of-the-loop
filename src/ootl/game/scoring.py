"""Pure scoring logic, implementing the v1 rules from the design doc.

Rules
-----
* If the imposter is voted out:
    - each clue-holder who voted correctly (for the imposter): **+1**
    - imposter: 0
* If the imposter survives the vote:
    - imposter: **+2**
    - if the imposter also guesses the word: **+1** bonus

No negative points (casual-friendly).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Award:
    user_id: int
    points: int
    reason: str


def score_round(
    *,
    imposter_id: int,
    clue_holder_ids: list[int],
    votes: dict[int, int],
    imposter_caught: bool,
    imposter_guessed_correct: bool | None,
) -> list[Award]:
    """Compute the list of point awards for one round."""
    awards: list[Award] = []
    clue_set = set(clue_holder_ids)

    if imposter_caught:
        # Reward every clue-holder who correctly fingered the imposter.
        for voter_id, target_id in votes.items():
            if voter_id in clue_set and target_id == imposter_id:
                awards.append(
                    Award(voter_id, 1, "voted correctly for the imposter")
                )
    else:
        awards.append(Award(imposter_id, 2, "survived the vote"))
        if imposter_guessed_correct:
            awards.append(Award(imposter_id, 1, "guessed the secret word"))

    return awards


def totals_from_awards(awards: list[Award]) -> dict[int, int]:
    """Collapse a list of awards into a ``user_id -> total points`` map."""
    totals: dict[int, int] = {}
    for award in awards:
        totals[award.user_id] = totals.get(award.user_id, 0) + award.points
    return totals
