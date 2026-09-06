"""Frame model.

A recording reduces to a flat list of `Frame`s, which the rest of the pipeline
groups by CAN id.  Nothing above this module knows what file format the frames
came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, Iterator, List, Tuple


@dataclass(frozen=True, slots=True)
class Frame:
    """One CAN frame as it appeared on the wire."""

    t_us: int
    """Arrival time in microseconds, relative to the start of the recording."""

    can_id: int
    """Arbitration id, without flags.  11-bit or 29-bit depending on `extended`."""

    data: bytes

    extended: bool = False

    @property
    def dlc(self) -> int:
        return len(self.data)


@dataclass
class IdGroup:
    """Every frame observed for one CAN id, in arrival order.

    The whole 29-bit id is the key, source address included.  That is the
    convention a DBC consumer expects: SavvyCAN, python-can and Vector all match
    on the raw id, so the same PGN sent from two source addresses has to become
    two messages.
    """

    can_id: int
    extended: bool
    frames: List[Frame] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.frames)

    @property
    def payloads(self) -> List[bytes]:
        return [f.data for f in self.frames]

    @property
    def dlcs(self) -> set:
        return {f.dlc for f in self.frames}

    @property
    def dlc(self) -> int:
        """The DLC to emit.  Widest observed, so no payload byte is unreachable."""
        return max((f.dlc for f in self.frames), default=0)

    @property
    def timestamps(self) -> List[int]:
        return [f.t_us for f in self.frames]

    @property
    def key(self) -> Tuple[int, bool]:
        """Identity of this group.  A standard 0x123 and an extended 0x00000123
        are different frames on the wire, so the format flag is part of the key."""
        return (self.can_id, self.extended)

    @property
    def hex(self) -> str:
        return f"{self.can_id:08X}" if self.extended else f"{self.can_id:03X}"


class FrameSet:
    """A whole recording, grouped by can id."""

    def __init__(self, frames: Iterable[Frame] = ()):
        self.groups: Dict[Tuple[int, bool], IdGroup] = {}
        self.total = 0
        for f in frames:
            self.add(f)

    def add(self, frame: Frame) -> None:
        key = (frame.can_id, frame.extended)
        g = self.groups.get(key)
        if g is None:
            g = self.groups[key] = IdGroup(frame.can_id, frame.extended)
        g.frames.append(frame)
        self.total += 1

    def get(self, can_id: int, extended: bool) -> IdGroup:
        return self.groups[(can_id, extended)]

    def __len__(self) -> int:
        return len(self.groups)

    def __iter__(self) -> Iterator[IdGroup]:
        """11-bit ids first, then 29-bit, each ascending -- stable across runs."""
        for key in sorted(self.groups, key=lambda k: (k[1], k[0])):
            yield self.groups[key]

    @property
    def duration_us(self) -> int:
        lo, hi = None, None
        for g in self.groups.values():
            for f in g.frames:
                if lo is None or f.t_us < lo:
                    lo = f.t_us
                if hi is None or f.t_us > hi:
                    hi = f.t_us
        return 0 if lo is None else hi - lo
