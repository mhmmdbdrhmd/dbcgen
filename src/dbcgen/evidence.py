"""Provenance.

Every message, signal and value table this tool emits carries an `Evidence`
record saying where it came from.  There are only two legitimate origins -- a
row in the published standard, or a counted statistic over the recording -- and
the distinction is preserved all the way into the DBC comments and the report,
so a reader can tell an ISO-defined fact from a guess made about their bus.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

#: Straight from the standard database; exact, not open to revision by data.
STANDARD = "standard"
#: Derived from counted statistics over this recording.
INFERRED = "inferred"
#: A standard name mapped onto an inferred layout.  Plausible, unconfirmed.
PROPOSED = "proposed"
#: A J1939 encoding convention applied because the standard names the SLOT.
CONVENTION = "convention"

AUTHORITATIVE = "authoritative"
HIGH = "high"
MEDIUM = "medium"
LOW = "low"

_CONFIDENCE_ORDER = {AUTHORITATIVE: 3, HIGH: 2, MEDIUM: 1, LOW: 0}


@dataclass(frozen=True)
class Evidence:
    origin: str
    rule: str
    """Short machine-readable id of the rule that fired, e.g. 'bit-segmentation'."""
    detail: str = ""
    """Human-readable justification, and it must contain the numbers."""
    confidence: str = MEDIUM
    stats: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.origin not in (STANDARD, INFERRED, PROPOSED, CONVENTION):
            raise ValueError(f"unknown origin {self.origin!r}")
        if self.confidence not in _CONFIDENCE_ORDER:
            raise ValueError(f"unknown confidence {self.confidence!r}")

    @property
    def rank(self) -> int:
        return _CONFIDENCE_ORDER[self.confidence]

    def to_comment(self) -> str:
        """One line, suitable for a DBC `CM_` string."""
        head = f"[{self.origin}:{self.rule}]"
        return f"{head} {self.detail}".strip()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "origin": self.origin,
            "rule": self.rule,
            "detail": self.detail,
            "confidence": self.confidence,
            "stats": dict(self.stats),
        }


def from_standard(rule: str, detail: str, **stats: Any) -> Evidence:
    return Evidence(STANDARD, rule, detail, AUTHORITATIVE, stats)


def inferred(rule: str, detail: str, confidence: str = MEDIUM, **stats: Any) -> Evidence:
    return Evidence(INFERRED, rule, detail, confidence, stats)


def proposed(rule: str, detail: str, **stats: Any) -> Evidence:
    return Evidence(PROPOSED, rule, detail, LOW, stats)


def by_convention(rule: str, detail: str, **stats: Any) -> Evidence:
    return Evidence(CONVENTION, rule, detail, MEDIUM, stats)


def best(items: List[Evidence]) -> Evidence:
    """The highest-confidence evidence in a list."""
    return max(items, key=lambda e: e.rank)
