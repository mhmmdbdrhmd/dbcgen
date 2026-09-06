"""Rolling counters and checksums.

These are found before anything else, because they are the two fields most
likely to mislead segmentation: a counter's low bit toggles every frame and a
checksum's bits look uniformly random, so left in place both look exactly like
the low bits of a fast-moving signal.

Both are recognised by behaviour rather than by position, which is what lets
a masked `0xF0 | counter` form be found without knowing in advance that a bus
uses it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

#: Counters may skip when frames are lost, so a clean majority is enough.
COUNTER_MIN_RATIO = 0.90
#: A counter has to be seen wrapping, or it is just a slow ramp.
COUNTER_MIN_FRAMES = 16
#: A checksum should take nearly a different value every frame.
CHECKSUM_MIN_DISTINCT_RATIO = 0.85
#: ... and its bits should each be near-balanced, unlike a physical quantity.
CHECKSUM_MAX_BIT_BIAS = 0.25


@dataclass
class CounterFinding:
    start_bit: int
    length: int
    modulus: int
    ratio: float
    """Fraction of frame-to-frame steps that advanced by exactly one."""
    constant_high_bits: Optional[int] = None
    """Value of the fixed bits above the counter, when it is a masked field."""

    def detail(self, frames: int) -> str:
        text = (
            f"increments by 1 modulo {self.modulus} on {self.ratio:.1%} of "
            f"{frames - 1} frame-to-frame steps"
        )
        if self.constant_high_bits is not None:
            text += (
                f"; the bits above it held 0x{self.constant_high_bits:X} throughout, "
                f"so the byte is a masked counter"
            )
        return text


@dataclass
class ChecksumFinding:
    start_bit: int
    length: int
    distinct_ratio: float
    max_bit_bias: float

    def detail(self, frames: int) -> str:
        return (
            f"takes {self.distinct_ratio:.1%} distinct values across {frames} frames "
            f"and every bit is near-balanced (worst bias {self.max_bit_bias:.2f}), "
            f"which is how a checksum behaves and a physical quantity does not"
        )


def _field_values(payload_ints: Sequence[int], start_bit: int, length: int) -> List[int]:
    mask = (1 << length) - 1
    return [(bits >> start_bit) & mask for bits in payload_ints]


def _increment_ratio(values: Sequence[int], modulus: int) -> float:
    """Fraction of steps that advance by exactly one, modulo `modulus`."""
    if len(values) < 2:
        return 0.0
    steps = len(values) - 1
    good = sum(
        1
        for i in range(steps)
        if (values[i] + 1) % modulus == values[i + 1] % modulus
    )
    return good / steps


def find_counter(
    payload_ints: Sequence[int], dlc: int, exclude: Optional[set] = None
) -> Optional[CounterFinding]:
    """The best rolling-counter field, if the payload has one.

    Whole bytes and both nibbles are tried, which covers the plain 8-bit
    counter, the 4-bit counter, and the masked form where a constant marker
    occupies the high nibble.
    """
    if len(payload_ints) < COUNTER_MIN_FRAMES:
        return None
    exclude = exclude or set()
    best: Optional[CounterFinding] = None

    for byte in range(dlc):
        for start_offset, length in ((0, 8), (0, 4), (4, 4)):
            start = byte * 8 + start_offset
            if set(range(start, start + length)) & exclude:
                continue
            values = _field_values(payload_ints, start, length)
            if len(set(values)) < 2:
                continue
            modulus = 1 << length
            ratio = _increment_ratio(values, modulus)
            if ratio < COUNTER_MIN_RATIO:
                continue
            # Require a wrap, so a slowly rising measurement is not mistaken
            # for a counter.
            if max(values) - min(values) < modulus - 1:
                continue

            high = None
            if length == 4 and start_offset == 0:
                top = _field_values(payload_ints, byte * 8 + 4, 4)
                if len(set(top)) == 1:
                    high = top[0]

            finding = CounterFinding(start, length, modulus, ratio, high)
            if best is None or (ratio, length) > (best.ratio, best.length):
                best = finding
    return best


def find_checksum(
    payload_ints: Sequence[int], dlc: int, exclude: Optional[set] = None
) -> Optional[ChecksumFinding]:
    """A trailing field that behaves like a checksum.

    Only the last bytes are considered: a field earlier in the payload with
    these statistics is far more likely to be an identifier or a fast signal,
    and calling it a checksum would be a guess the data does not support.
    """
    frames = len(payload_ints)
    if frames < COUNTER_MIN_FRAMES or dlc < 2:
        return None
    exclude = exclude or set()

    for length in (16, 8):
        byte = dlc - length // 8
        if byte < 1:
            continue
        start = byte * 8
        if set(range(start, start + length)) & exclude:
            continue
        values = _field_values(payload_ints, start, length)
        distinct_ratio = len(set(values)) / frames
        if distinct_ratio < CHECKSUM_MIN_DISTINCT_RATIO:
            continue
        biases = [
            abs(sum((v >> i) & 1 for v in values) / frames - 0.5) for i in range(length)
        ]
        worst = max(biases)
        if worst > CHECKSUM_MAX_BIT_BIAS:
            continue
        return ChecksumFinding(start, length, distinct_ratio, worst)
    return None
