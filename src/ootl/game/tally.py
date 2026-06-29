"""Pure vote-tallying logic (no Telegram, no I/O -- easy to unit-test)."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TallyResult:
    counts: dict[int, int]          # target_id -> number of votes
    top_count: int                  # highest vote count (0 if no votes)
    leaders: list[int] = field(default_factory=list)  # target_ids tied at top
    is_tie: bool = False            # True if >1 leader (or no votes)

    @property
    def has_votes(self) -> bool:
        return self.top_count > 0

    @property
    def eliminated(self) -> int | None:
        """The single eliminated player, or None on a tie / no votes.

        With the v1 tie policy ("no elimination on tie") a tie means nobody is
        eliminated, so the caller treats ``None`` as "imposter survives".
        """
        if self.is_tie or not self.has_votes:
            return None
        return self.leaders[0]


def tally_votes(votes: dict[int, int]) -> TallyResult:
    """Tally ``votes`` (voter_id -> target_id) into a :class:`TallyResult`."""
    if not votes:
        return TallyResult(counts={}, top_count=0, leaders=[], is_tie=True)

    counts: dict[int, int] = dict(Counter(votes.values()))
    top_count = max(counts.values())
    leaders = sorted(t for t, c in counts.items() if c == top_count)
    is_tie = len(leaders) != 1
    return TallyResult(
        counts=counts,
        top_count=top_count,
        leaders=leaders,
        is_tie=is_tie,
    )
