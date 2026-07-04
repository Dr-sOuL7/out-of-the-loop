"""In-memory runtime state for an active game.

The *live* state of a game lives here (the design doc keeps the database for
persistence/stats and runs the active match from memory). One ``GameState``
exists per group chat while a lobby or match is active.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from ootl.game.enums import MatchState, Role


@dataclass
class Player:
    user_id: int
    display_name: str
    username: str | None = None

    @property
    def label(self) -> str:
        """Plain-text label for lists (no markup)."""
        if self.username:
            return f"{self.display_name} (@{self.username})"
        return self.display_name


@dataclass
class Round:
    """State of a single round."""

    round_number: int
    category: str
    secret_word: str
    question: str
    imposter_id: int

    # The 4 subjective answer options for this round's question (players pick
    # one). Empty list = free-text answers (v1 worker mode).
    options: list[str] = field(default_factory=list)

    # Content / DB identifiers (for usage tracking & persistence).
    word_id: int | None = None
    question_id: int | None = None
    round_id: int | None = None

    # Player order for this round (stable display order).
    participant_ids: list[int] = field(default_factory=list)

    # Collected during play.
    answers: dict[int, str] = field(default_factory=dict)   # user_id -> text
    answer_order: list[int] = field(default_factory=list)   # submission order
    votes: dict[int, int] = field(default_factory=dict)     # voter_id -> target

    # Outcome.
    eliminated_id: int | None = None
    imposter_caught: bool | None = None
    imposter_guess: str | None = None
    imposter_guessed_correct: bool | None = None

    # Telegram bookkeeping.
    voting_message_id: int | None = None
    answer_prompt_message_id: int | None = None

    def role_of(self, user_id: int) -> Role:
        return Role.IMPOSTER if user_id == self.imposter_id else Role.CLUE_HOLDER

    def clue_holder_ids(self) -> list[int]:
        return [pid for pid in self.participant_ids if pid != self.imposter_id]

    def all_answers_in(self) -> bool:
        return len(self.answers) >= len(self.participant_ids)

    def all_votes_in(self) -> bool:
        return len(self.votes) >= len(self.participant_ids)


@dataclass
class GameState:
    """Lobby + match-level state for one group chat."""

    chat_id: int
    host_id: int
    state: MatchState = MatchState.LOBBY
    players: dict[int, Player] = field(default_factory=dict)  # insertion-ordered
    total_rounds: int = 5
    current_round_number: int = 0
    match_id: int | None = None
    scores: dict[int, int] = field(default_factory=dict)      # user_id -> points
    current_round: Round | None = None
    created_at: float = field(default_factory=time.monotonic)

    # Name of the currently-scheduled phase-deadline job (so it can be cancelled
    # when a phase ends early). One phase timer is active at a time.
    active_job_name: str | None = None

    # Message id of the lobby message (so join/leave can refresh the roster).
    lobby_message_id: int | None = None

    # Serverless (webhook) mode only: which phase deadline is pending
    # ('answer' | 'vote' | 'guess' | 'nextround'). The deadline timestamp
    # itself is stored as a DB column so a cron tick can find expired games.
    deadline_kind: str | None = None

    # -- player helpers ------------------------------------------------------
    @property
    def player_ids(self) -> list[int]:
        return list(self.players.keys())

    @property
    def player_count(self) -> int:
        return len(self.players)

    def add_player(self, player: Player) -> bool:
        """Add a player; return False if already present."""
        if player.user_id in self.players:
            return False
        self.players[player.user_id] = player
        self.scores.setdefault(player.user_id, 0)
        return True

    def remove_player(self, user_id: int) -> bool:
        return self.players.pop(user_id, None) is not None

    def is_host(self, user_id: int) -> bool:
        return user_id == self.host_id

    def display_name(self, user_id: int) -> str:
        player = self.players.get(user_id)
        return player.display_name if player else "Someone"

    def add_score(self, user_id: int, points: int) -> None:
        self.scores[user_id] = self.scores.get(user_id, 0) + points

    def leaderboard(self) -> list[tuple[int, int]]:
        """(user_id, points) sorted high -> low."""
        return sorted(self.scores.items(), key=lambda kv: kv[1], reverse=True)
