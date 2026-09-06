"""Per-id statistics.

Everything the inference stages need is derived here, once, in a single pass per
id.  These numbers are also what gets quoted as evidence in the report, so they
are counts and medians -- reproducible on the same input, never sampled.

Bit numbering follows the DBC convention for little-endian signals:

    bit_index = byte_index * 8 + bit_within_byte,  bit 0 = LSB of the byte

so a start bit computed here can be written into a `SG_` line unchanged.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..frames import IdGroup

#: An id whose inter-arrival times cluster this tightly around their median is
#: treated as cyclic and gets a GenMsgCycleTime attribute.
CYCLIC_TOLERANCE = 0.25
CYCLIC_FRACTION = 0.90


@dataclass
class MessageStats:
    """Counted facts about one CAN id over the whole recording."""

    can_id: int
    extended: bool
    count: int
    dlc: int
    dlcs: set

    # --- timing -----------------------------------------------------------
    cycle_us: Optional[float] = None
    """Median inter-arrival time, or None when fewer than 3 frames were seen."""
    cycle_jitter_us: Optional[float] = None
    cyclic_fraction: float = 0.0
    """Fraction of gaps within CYCLIC_TOLERANCE of the median."""
    is_cyclic: bool = False
    first_us: int = 0
    last_us: int = 0

    # --- payload ----------------------------------------------------------
    bit_flips: List[int] = field(default_factory=list)
    """Per bit: how many times it differed from the previous frame."""
    bit_ones: List[int] = field(default_factory=list)
    """Per bit: how many frames had it set."""
    distinct_payloads: int = 0
    is_constant: bool = False
    """Every frame carried an identical payload."""

    @property
    def n_bits(self) -> int:
        return self.dlc * 8

    @property
    def cycle_ms(self) -> Optional[float]:
        return None if self.cycle_us is None else self.cycle_us / 1000.0

    def bit_is_constant(self, i: int) -> bool:
        return self.bit_flips[i] == 0

    def constant_bits(self) -> List[int]:
        return [i for i in range(self.n_bits) if self.bit_flips[i] == 0]

    def active_bits(self) -> List[int]:
        return [i for i in range(self.n_bits) if self.bit_flips[i] > 0]

    def flip_rate(self, i: int) -> float:
        """Flips per transition.  1.0 means the bit toggled every single frame."""
        return self.bit_flips[i] / (self.count - 1) if self.count > 1 else 0.0

    def constant_value(self, i: int) -> int:
        """The fixed value of a constant bit."""
        return 1 if self.bit_ones[i] == self.count else 0

    def constant_bytes(self) -> List[int]:
        """Byte indices whose eight bits never changed."""
        return [
            b
            for b in range(self.dlc)
            if all(self.bit_flips[b * 8 + j] == 0 for j in range(8))
        ]


def analyse(group: IdGroup) -> MessageStats:
    """Single pass over one id's frames."""
    frames = group.frames
    n = len(frames)
    dlc = group.dlc
    st = MessageStats(
        can_id=group.can_id,
        extended=group.extended,
        count=n,
        dlc=dlc,
        dlcs=group.dlcs,
        bit_flips=[0] * (dlc * 8),
        bit_ones=[0] * (dlc * 8),
    )
    if n == 0:
        return st

    st.first_us = frames[0].t_us
    st.last_us = frames[-1].t_us

    # --- timing -----------------------------------------------------------
    if n >= 3:
        gaps = [frames[i + 1].t_us - frames[i].t_us for i in range(n - 1)]
        gaps = [g for g in gaps if g > 0]
        if gaps:
            med = statistics.median(gaps)
            st.cycle_us = med
            st.cycle_jitter_us = statistics.median(abs(g - med) for g in gaps)
            if med > 0:
                within = sum(1 for g in gaps if abs(g - med) <= CYCLIC_TOLERANCE * med)
                st.cyclic_fraction = within / len(gaps)
                st.is_cyclic = st.cyclic_fraction >= CYCLIC_FRACTION

    # --- payload ----------------------------------------------------------
    distinct = set()
    prev_bits: Optional[int] = None
    for f in frames:
        # Pad short frames so a varying DLC does not shift bit positions.
        data = f.data.ljust(dlc, b"\x00")[:dlc]
        distinct.add(bytes(data))
        # One integer per payload, little-endian, so bit i of the integer is
        # exactly DBC bit index i.
        cur = int.from_bytes(data, "little")
        ones = cur
        while ones:
            st.bit_ones[(ones & -ones).bit_length() - 1] += 1
            ones &= ones - 1
        if prev_bits is not None:
            diff = cur ^ prev_bits
            while diff:
                st.bit_flips[(diff & -diff).bit_length() - 1] += 1
                diff &= diff - 1
        prev_bits = cur

    st.distinct_payloads = len(distinct)
    st.is_constant = len(distinct) == 1
    return st


def analyse_all(frameset) -> Dict[Tuple[int, bool], MessageStats]:
    """Statistics for every id, keyed as the FrameSet keys its groups."""
    return {g.key: analyse(g) for g in frameset}
