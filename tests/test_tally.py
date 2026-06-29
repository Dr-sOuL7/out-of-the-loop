"""Tests for vote tallying."""
from ootl.game.tally import tally_votes


def test_no_votes_is_tie_with_no_elimination():
    result = tally_votes({})
    assert result.is_tie
    assert not result.has_votes
    assert result.eliminated is None


def test_clear_winner():
    # voters 1,2,3 -> target 9 ; voter 4 -> target 5
    result = tally_votes({1: 9, 2: 9, 3: 9, 4: 5})
    assert result.top_count == 3
    assert result.leaders == [9]
    assert not result.is_tie
    assert result.eliminated == 9


def test_tie_means_no_elimination():
    result = tally_votes({1: 9, 2: 5})
    assert result.is_tie
    assert result.eliminated is None
    assert set(result.leaders) == {5, 9}


def test_counts_are_correct():
    result = tally_votes({1: 9, 2: 9, 3: 5})
    assert result.counts == {9: 2, 5: 1}
