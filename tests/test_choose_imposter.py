"""Unit tests for the fair imposter-rotation picker."""
from __future__ import annotations

import pytest

from ootl.game.models import choose_imposter

PLAYERS = [10, 20, 30]


def test_first_round_any_player_eligible():
    seen = {choose_imposter(PLAYERS, []) for _ in range(200)}
    assert seen == set(PLAYERS)


def test_never_same_player_twice_in_a_row():
    history = [choose_imposter(PLAYERS, [])]
    for _ in range(50):
        nxt = choose_imposter(PLAYERS, history)
        assert nxt != history[-1]
        history.append(nxt)


def test_prefers_players_who_have_been_imposter_least():
    # 10 and 20 have each had a turn -> 30 is the only fair pick.
    assert choose_imposter(PLAYERS, [10, 20]) == 30
    # Everyone level again -> anyone but the previous imposter (30).
    seen = {choose_imposter(PLAYERS, [10, 20, 30]) for _ in range(100)}
    assert seen == {10, 20}


def test_even_distribution_across_a_full_match():
    history: list[int] = []
    for _ in range(9):  # 9 rounds, 3 players -> exactly 3 turns each
        history.append(choose_imposter(PLAYERS, history))
    assert all(history.count(uid) == 3 for uid in PLAYERS)


def test_history_of_departed_players_is_ignored():
    # 99 left the game; remaining players rotate among themselves.
    assert choose_imposter([10, 20], [99, 10]) == 20


def test_single_player_repeats_when_unavoidable():
    assert choose_imposter([10], [10, 10]) == 10


def test_no_participants_raises():
    with pytest.raises(ValueError):
        choose_imposter([], [])
