"""Tests for the scoring rules."""
from ootl.game.scoring import Award, score_round, totals_from_awards


def test_imposter_caught_rewards_correct_voters():
    # imposter = 1, clue-holders = 2,3,4
    # votes: 2->1 (correct), 3->1 (correct), 4->2 (wrong)
    awards = score_round(
        imposter_id=1,
        clue_holder_ids=[2, 3, 4],
        votes={2: 1, 3: 1, 4: 2},
        imposter_caught=True,
        imposter_guessed_correct=None,
    )
    totals = totals_from_awards(awards)
    assert totals == {2: 1, 3: 1}
    assert 1 not in totals  # imposter gets nothing
    assert 4 not in totals  # wrong vote gets nothing


def test_imposter_survives_gets_two():
    awards = score_round(
        imposter_id=1,
        clue_holder_ids=[2, 3, 4],
        votes={2: 3, 3: 2, 4: 2},
        imposter_caught=False,
        imposter_guessed_correct=False,
    )
    totals = totals_from_awards(awards)
    assert totals == {1: 2}


def test_imposter_survives_and_guesses_gets_bonus():
    awards = score_round(
        imposter_id=1,
        clue_holder_ids=[2, 3],
        votes={2: 3, 3: 2},
        imposter_caught=False,
        imposter_guessed_correct=True,
    )
    totals = totals_from_awards(awards)
    assert totals == {1: 3}


def test_no_negative_points_anywhere():
    awards = score_round(
        imposter_id=1,
        clue_holder_ids=[2, 3, 4],
        votes={2: 2, 3: 4, 4: 3},
        imposter_caught=False,
        imposter_guessed_correct=False,
    )
    assert all(a.points > 0 for a in awards)


def test_caught_with_no_correct_voters_awards_nothing():
    awards = score_round(
        imposter_id=1,
        clue_holder_ids=[2, 3],
        votes={2: 3, 3: 2},  # nobody voted for the imposter, but caught anyway
        imposter_caught=True,
        imposter_guessed_correct=None,
    )
    assert awards == []
