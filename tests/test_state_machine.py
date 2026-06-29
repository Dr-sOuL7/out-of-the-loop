"""Tests for the match state machine transitions."""
from ootl.game.enums import MatchState, can_transition


def test_happy_path_round_flow():
    flow = [
        MatchState.LOBBY,
        MatchState.STARTING,
        MatchState.ASSIGNING_ROLES,
        MatchState.QUESTION_PHASE,
        MatchState.ANSWER_COLLECTION,
        MatchState.VOTING_PHASE,
        MatchState.REVEAL_PHASE,
        MatchState.IMPOSTER_GUESS_PHASE,
        MatchState.SCORING_PHASE,
        MatchState.ROUND_END,
    ]
    for a, b in zip(flow, flow[1:]):
        assert can_transition(a, b), f"{a} -> {b} should be allowed"


def test_reveal_can_skip_guess_when_caught():
    assert can_transition(MatchState.REVEAL_PHASE, MatchState.SCORING_PHASE)


def test_round_end_loops_or_finishes():
    assert can_transition(MatchState.ROUND_END, MatchState.ASSIGNING_ROLES)
    assert can_transition(MatchState.ROUND_END, MatchState.MATCH_END)


def test_illegal_transitions_blocked():
    assert not can_transition(MatchState.LOBBY, MatchState.VOTING_PHASE)
    assert not can_transition(MatchState.VOTING_PHASE, MatchState.LOBBY)
    assert not can_transition(MatchState.ANSWER_COLLECTION, MatchState.ANSWER_COLLECTION)


def test_abort_from_any_active_state():
    assert can_transition(MatchState.VOTING_PHASE, MatchState.ABORTED)
    assert can_transition(MatchState.LOBBY, MatchState.ABORTED)
    # Cannot abort a finished match.
    assert not can_transition(MatchState.MATCH_END, MatchState.ABORTED)
