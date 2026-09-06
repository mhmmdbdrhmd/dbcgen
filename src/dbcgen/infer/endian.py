"""Reading candidate fields out of payloads, and telling the byte order.

The test for byte order is the one a human uses on a trace: extract the field
both ways and see which one moves like a physical quantity.  A signal sampled
at tens of hertz changes by a small fraction of its range between frames; the
same bits read in the wrong order jump around the whole range at random.

That is measurable rather than a matter of judgement, and it is what caught a
big-endian proprietary field that a little-endian-only sweep had missed.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

LITTLE = "little"
BIG = "big"

#: Mean step of a uniformly random sequence over a range R is about R/3.  A
#: field scoring near 0 moves like noise; near 1, like a physical quantity.
_RANDOM_WALK_FRACTION = 3.0

#: Below this, the field is not behaving like a sampled physical quantity.
SMOOTH_THRESHOLD = 0.55

#: A byte order only wins if it is clearly better, not merely better.
DECISION_MARGIN = 0.10


def bit_indices(start_bit: int, length: int, byte_order: str) -> List[int]:
    """Payload bit indices of a field, most significant first.

    Bit indices are DBC little-endian numbering throughout
    (`byte * 8 + bit_in_byte`, bit 0 = LSB of the byte).

    For a little-endian signal the DBC start bit is the LSB and the field runs
    upward.  For a big-endian signal the start bit is the MSB, and the field
    walks *down* within a byte before continuing at the top of the next -- so
    its bits are not a contiguous range in this numbering.
    """
    if byte_order == LITTLE:
        return list(range(start_bit + length - 1, start_bit - 1, -1))
    out = []
    byte, bit = divmod(start_bit, 8)
    for _ in range(length):
        out.append(byte * 8 + bit)
        if bit == 0:
            byte, bit = byte + 1, 7
        else:
            bit -= 1
    return out


def extract(payload_ints: Sequence[int], indices: Sequence[int]) -> List[int]:
    """Pull one field's raw value out of every frame.

    `payload_ints` are payloads already converted to little-endian integers, so
    bit i of the integer is DBC bit index i.
    """
    out = []
    for bits in payload_ints:
        value = 0
        for index in indices:  # most significant first
            value = (value << 1) | ((bits >> index) & 1)
        out.append(value)
    return out


def to_ints(payloads: Sequence[bytes], dlc: int) -> List[int]:
    """Payloads as little-endian integers, padded so bit positions never shift."""
    return [int.from_bytes(p.ljust(dlc, b"\x00")[:dlc], "little") for p in payloads]


def smoothness(values: Sequence[int]) -> float:
    """How much this series behaves like a sampled physical quantity, 0..1.

    Returns 0.0 for a constant series -- constancy is a separate finding, and
    scoring it as perfectly smooth would let any constant region masquerade as
    a well-chosen signal.
    """
    if len(values) < 3:
        return 0.0
    span = max(values) - min(values)
    if span == 0:
        return 0.0
    steps = [abs(values[i + 1] - values[i]) for i in range(len(values) - 1)]
    # The median step, not the mean: a handful of wraps or resets should not
    # decide the byte order for a signal that is otherwise smooth.
    typical = statistics.median(steps)
    return max(0.0, 1.0 - typical / (span / _RANDOM_WALK_FRACTION))


def wrap_ratio(values: Sequence[int], length: int) -> float:
    """Fraction of steps that look like a full-scale wrap rather than motion."""
    if len(values) < 2:
        return 0.0
    full = 1 << length
    jumps = sum(
        1 for i in range(len(values) - 1) if abs(values[i + 1] - values[i]) > full * 0.5
    )
    return jumps / (len(values) - 1)


@dataclass
class Candidate:
    """One possible field placement, with the numbers that judge it."""

    start_bit: int
    length: int
    byte_order: str
    score: float
    values: List[int]
    span: int
    distinct: int

    @property
    def is_smooth(self) -> bool:
        return self.score >= SMOOTH_THRESHOLD

    def bit_set(self) -> set:
        return set(bit_indices(self.start_bit, self.length, self.byte_order))


def evaluate(
    payload_ints: Sequence[int], start_bit: int, length: int, byte_order: str
) -> Candidate:
    indices = bit_indices(start_bit, length, byte_order)
    values = extract(payload_ints, indices)
    return Candidate(
        start_bit=start_bit,
        length=length,
        byte_order=byte_order,
        score=smoothness(values),
        values=values,
        span=max(values) - min(values) if values else 0,
        distinct=len(set(values)),
    )


def best_byte_order(
    payload_ints: Sequence[int], start_bit: int, length: int
) -> Tuple[Optional[Candidate], str]:
    """Pick the byte order for a field, and say why.

    A big-endian field's DBC start bit is its most significant bit, which for a
    byte-aligned field sits at the top of the first byte -- so the two
    hypotheses are compared at the start bits each convention would use.
    """
    little = evaluate(payload_ints, start_bit, length, LITTLE)
    be_start = (start_bit // 8) * 8 + 7
    big = evaluate(payload_ints, be_start, length, BIG)

    if length <= 8:
        # Inside a single byte the two orders describe the same bits.
        return little, "field fits in one byte, so byte order does not apply"

    if little.score >= big.score + DECISION_MARGIN:
        return little, (
            f"little-endian reads smoothly (score {little.score:.2f} vs "
            f"{big.score:.2f} big-endian) over {len(little.values)} frames"
        )
    if big.score >= little.score + DECISION_MARGIN:
        return big, (
            f"big-endian reads smoothly (score {big.score:.2f} vs "
            f"{little.score:.2f} little-endian) over {len(big.values)} frames"
        )
    winner = little if little.score >= big.score else big
    return winner, (
        f"byte order is not separable from this recording "
        f"(little {little.score:.2f}, big {big.score:.2f}); "
        f"defaulted to {winner.byte_order}-endian"
    )
