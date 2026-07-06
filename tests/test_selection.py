"""Tests for fairness-aware imposter selection."""
from ootl.game.selection import choose_imposter


def test_first_round_picks_a_participant():
    assert choose_imposter([1, 2, 3]) in (1, 2, 3)


def test_never_same_imposter_twice_in_a_row():
    players = [1, 2, 3]
    counts: dict[int, int] = {}
    last = None
    for _ in range(50):
        pick = choose_imposter(players, counts, last)
        assert pick != last
        counts[pick] = counts.get(pick, 0) + 1
        last = pick


def test_rotation_is_even_across_a_match():
    players = [1, 2, 3, 4, 5]
    counts: dict[int, int] = {}
    last = None
    for _ in range(50):
        pick = choose_imposter(players, counts, last)
        counts[pick] = counts.get(pick, 0) + 1
        last = pick
    # 50 rounds / 5 players -> everyone should be imposter 10 +/- 1 times.
    assert all(9 <= counts.get(p, 0) <= 11 for p in players), counts


def test_prefers_players_who_have_been_imposter_least():
    # 4 has never been imposter; everyone else has. 4 must be picked.
    assert choose_imposter([1, 2, 3, 4], {1: 2, 2: 1, 3: 1}, last_imposter_id=3) == 4


def test_single_candidate_falls_back_gracefully():
    # Only one participant left: they get picked even if they were last.
    assert choose_imposter([7], {7: 3}, last_imposter_id=7) == 7
