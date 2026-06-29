"""Game enums and the match state machine.

The strict state machine is what keeps a Telegram bot from descending into
chaos: it makes double starts, late votes, duplicate answers and broken round
transitions structurally impossible.
"""
from __future__ import annotations

from enum import Enum


class MatchState(str, Enum):
    LOBBY = "LOBBY"                          # players gathering, not started
    STARTING = "STARTING"                    # host pressed start, setting up
    ASSIGNING_ROLES = "ASSIGNING_ROLES"      # DMing word / imposter notice
    QUESTION_PHASE = "QUESTION_PHASE"        # prompt posted
    ANSWER_COLLECTION = "ANSWER_COLLECTION"  # collecting private answers
    VOTING_PHASE = "VOTING_PHASE"            # voting open
    REVEAL_PHASE = "REVEAL_PHASE"            # showing who the imposter was
    IMPOSTER_GUESS_PHASE = "IMPOSTER_GUESS_PHASE"  # imposter's final guess
    SCORING_PHASE = "SCORING_PHASE"          # awarding points
    ROUND_END = "ROUND_END"                  # round wrapped up
    MATCH_END = "MATCH_END"                  # whole match finished
    ABORTED = "ABORTED"                      # cancelled by host / error


# Allowed transitions. ABORTED is reachable from any active state (handled
# separately) so it is not listed here.
_TRANSITIONS: dict[MatchState, set[MatchState]] = {
    MatchState.LOBBY: {MatchState.STARTING},
    MatchState.STARTING: {MatchState.ASSIGNING_ROLES},
    MatchState.ASSIGNING_ROLES: {MatchState.QUESTION_PHASE},
    MatchState.QUESTION_PHASE: {MatchState.ANSWER_COLLECTION},
    MatchState.ANSWER_COLLECTION: {MatchState.VOTING_PHASE},
    MatchState.VOTING_PHASE: {MatchState.REVEAL_PHASE},
    # After reveal we either go to the imposter's guess (survived) or straight
    # to scoring (caught).
    MatchState.REVEAL_PHASE: {MatchState.IMPOSTER_GUESS_PHASE, MatchState.SCORING_PHASE},
    MatchState.IMPOSTER_GUESS_PHASE: {MatchState.SCORING_PHASE},
    MatchState.SCORING_PHASE: {MatchState.ROUND_END},
    # A round either rolls into the next (back to assigning roles) or ends the
    # match.
    MatchState.ROUND_END: {MatchState.ASSIGNING_ROLES, MatchState.MATCH_END},
    MatchState.MATCH_END: set(),
    MatchState.ABORTED: set(),
}

# States in which the game is actively running (and can be aborted).
ACTIVE_STATES = frozenset(
    s for s in MatchState if s not in (MatchState.MATCH_END, MatchState.ABORTED)
)


def can_transition(current: MatchState, target: MatchState) -> bool:
    """Return True if moving ``current`` -> ``target`` is allowed."""
    if target == MatchState.ABORTED:
        return current in ACTIVE_STATES
    return target in _TRANSITIONS.get(current, set())


class Role(str, Enum):
    CLUE_HOLDER = "CLUE_HOLDER"
    IMPOSTER = "IMPOSTER"
