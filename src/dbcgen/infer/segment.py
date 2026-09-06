"""Partition a payload into fields.

The core observation is about *magnitude*.  Inside one little-endian integer
field, the least significant bit changes most often and each higher bit changes
about half as often as the one below it, so the flip-rate profile decreases
monotonically across the field.  Where a new field begins, its own LSB changes
often again -- the profile jumps back up.  So the boundaries between fields are
exactly the bit positions where the flip rate *rises*.

That reads a real bus well.  On the `gnss_odo` message of the benchmark, whose
publisher packs a 1-bit flag and three fields of 22, 19 and 22 bits with no byte
alignment at all, the rises land on bits 1 and 23 -- two of the three published
boundaries, recovered from nothing but how often each bit changed.  The third,
at bit 42, is not found: that bit changed exactly once in 346 frames, which is
below the noise floor and indistinguishable from a stuck bit.  See
`docs/make_figures.py`, which draws this.

Two kinds of field break the assumption and so are found first, by behaviour:

* a **rolling counter**, whose low bits flip on a fixed schedule;
* a **checksum**, whose bits are all near-random and so have no profile at all.

Both are recognised in `counters.py`.  Left in place, either looks exactly like
the low bits of a fast-moving signal.

Nothing here invents scaling.  A field's factor stays 1 and its offset 0: a
recording shows what the bits do, not what engineering unit they are in.  Where
a quantity is direction-dependent or piecewise, no single factor/offset pair is
correct, and fitting one by least squares buys a tidy-looking DBC at the cost of
a signal that reads wrongly exactly where it matters most.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field as dataclass_field
from typing import List, Optional, Sequence, Set, Tuple

from . import counters, endian
from .stats import MessageStats

#: A rise in flip rate counts as a boundary only if it clears the sampling
#: noise of the estimate.  The rate is a proportion over `count - 1` steps, so
#: its standard error is sqrt(p(1-p)/n); four of those is a ~99.99% bound and
#: keeps neighbouring bits of one field from being split apart.
NOISE_SIGMAS = 4.0

#: Floor for the above, so that a rise out of a perfectly constant region --
#: where the standard error is zero -- still needs real evidence behind it.
MIN_RISE = 0.004

#: Once a bit changes about half the time it is saturated: the signal moves by
#: more than that bit's weight between frames, so every bit below it is
#: effectively random and their order carries no magnitude information.  Rises
#: inside a saturated region are noise dressed as structure, and acting on them
#: splits one fast signal into several.
SATURATION_RATE = 0.40

#: A trailing run of constant bits at least this long, inside a field that
#: starts on a byte boundary, is read as padding rather than as the field's
#: high bits.  Restricted to byte-aligned fields on purpose: a field that
#: begins mid-byte belongs to a tightly packed layout, where high bits that
#: happen not to move during one recording are perfectly ordinary.
CONSTANT_TAIL_BITS = 8

#: A field with no more than this many distinct values is reported as discrete.
DISCRETE_MAX_DISTINCT = 12

#: Byte order is only overridden for a byte-aligned field this wide or wider.
MIN_BYTE_ORDER_WIDTH = 16

CONSTANT = "constant"
COUNTER = "counter"
CHECKSUM = "checksum"
VALUE = "value"
DISCRETE = "discrete"


@dataclass
class Field:
    """One inferred field, with the statistic that justified it."""

    start_bit: int
    length: int
    byte_order: str = endian.LITTLE
    kind: str = VALUE
    rule: str = "bit-segmentation"
    detail: str = ""
    confidence: str = "medium"
    values: List[int] = dataclass_field(default_factory=list)
    distinct: int = 0
    score: float = 0.0

    @property
    def name_hint(self) -> str:
        """The signal name.

        Every name here begins `Guess_` or `Const_`, because every one of them
        was derived from how the bytes behaved rather than from a published
        document.  The boundaries are usually right and sometimes are not, and
        the person reading a trace six months from now has no other way to tell
        these apart from the names the standard supplied.  Making the
        distinction visible in the name itself means it survives being exported,
        pasted into a report, or opened in a tool that hides comments.
        """
        if self.kind == COUNTER:
            return "Guess_RollingCounter"
        if self.kind == CHECKSUM:
            return "Guess_Checksum"
        if self.kind == CONSTANT:
            return f"Const_{self.start_bit}_{self.length}"
        return f"Guess_{self.start_bit}_{self.length}"

    def bit_set(self) -> Set[int]:
        return set(endian.bit_indices(self.start_bit, self.length, self.byte_order))


def boundaries(rates: Sequence[float], span: Sequence[int], samples: int) -> List[int]:
    """Positions within `span` where the flip rate rises out of the noise.

    `span` is a run of bit indices in ascending significance.  Returns indices
    *into* `span` at which a new field starts; position 0 always does.
    """
    out = [0]
    for i in range(1, len(span)):
        previous = rates[span[i - 1]]
        current = rates[span[i]]
        if current <= previous or previous >= SATURATION_RATE:
            continue
        error = math.sqrt(max(previous * (1.0 - previous), 0.0) / max(samples, 1))
        if current - previous > max(MIN_RISE, NOISE_SIGMAS * error):
            out.append(i)
    return out


def looks_byte_aligned(span: Sequence[int], cuts: Sequence[int]) -> bool:
    """Whether this message uses byte-oriented fields or packed bit fields.

    The two conventions need opposite treatment, and the boundaries already
    found say which one is in play: if most of them sit on byte edges the
    layout is byte-oriented and a stray mid-byte boundary is an artefact worth
    correcting; if most sit mid-byte the layout is packed, fields genuinely
    begin at odd bits, and tidying them would destroy correct answers.
    """
    interior = [c for c in cuts if c > 0]
    if not interior:
        return False
    return sum(1 for c in interior if span[c] % 8 == 0) * 2 >= len(interior)


def snap_boundaries(
    rates: Sequence[float], span: Sequence[int], cuts: Sequence[int]
) -> List[int]:
    """Pull boundaries onto byte edges in a byte-oriented layout.

    A field's least significant bit can sit still for a whole recording -- an
    engine speed reported to 0.125 rpm barely exercises its bottom bit -- and a
    bit that never changes produces no rise, so the boundary lands one or two
    bits late and the field appears to start inside a byte.
    """
    if not looks_byte_aligned(span, cuts):
        return list(cuts)

    position = {bit: i for i, bit in enumerate(span)}
    out = []
    for c in cuts:
        bit = span[c]
        edge = (bit // 8) * 8
        if (
            c > 0
            and bit % 8
            and edge in position
            and position[edge] > cuts[cuts.index(c) - 1]
            and all(rates[b] == 0.0 for b in range(edge, bit))
        ):
            out.append(position[edge])
        else:
            out.append(c)
    return sorted(set(out))


def trim_constant_tail(
    rates: Sequence[float],
    start_bit: int,
    length: int,
    n_bits: int = 0,
    byte_aligned: bool = False,
) -> Tuple[int, int]:
    """Shorten a byte-aligned field whose high bits never moved.

    Nothing in the flip profile separates a signal's unused high bits from
    padding that follows it, so a field bounded only by the next signal's LSB
    runs on to wherever that is -- on the benchmark's engine message a 16-bit
    engine speed ran to 32 bits for exactly this reason.  For a field starting
    on a byte boundary the ambiguity is resolved towards the dominant
    convention: end at the first byte boundary past the last bit that changed.

    Returns the kept `(start_bit, length)`; the caller emits the remainder as
    constant fields, so no bit is lost.

    Applied to a field starting on a byte boundary, and -- in a byte-oriented
    layout only -- to a field whose tail runs to the end of the payload.
    Nothing bounds that last field from above, so without the rule it claims
    every trailing byte of the frame; but in a packed layout a final field with
    quiet high bits is perfectly ordinary and must be left alone.
    """
    reaches_end = byte_aligned and n_bits and start_bit + length >= n_bits
    if start_bit % 8 and not reaches_end:
        return start_bit, length
    varying = [i for i in range(length) if rates[start_bit + i] > 0.0]
    if not varying:
        return start_bit, length
    if length - (varying[-1] + 1) < CONSTANT_TAIL_BITS:
        return start_bit, length
    kept = ((start_bit + varying[-1]) // 8 + 1) * 8 - start_bit
    return start_bit, max(kept, 1)


def _runs(bits: Sequence[int]) -> List[List[int]]:
    """Maximal contiguous runs in a sorted sequence of bit indices."""
    out: List[List[int]] = []
    current: List[int] = []
    for b in bits:
        if current and b != current[-1] + 1:
            out.append(current)
            current = []
        current.append(b)
    if current:
        out.append(current)
    return out


def _classify(
    candidate: endian.Candidate, frames: int
) -> Tuple[str, str, str, str]:
    """(kind, rule, detail, confidence) for a plain value field."""
    if candidate.distinct == 1:
        return (
            CONSTANT,
            "constant-bits",
            f"held {candidate.values[0]} in all {frames} frames, so the recording "
            f"shows no signal here",
            "high",
        )
    if candidate.distinct <= DISCRETE_MAX_DISTINCT:
        seen = sorted(set(candidate.values))[:DISCRETE_MAX_DISTINCT]
        return (
            DISCRETE,
            "low-cardinality",
            f"took only {candidate.distinct} distinct values {seen} across "
            f"{frames} frames, so it is discrete rather than a measurement",
            "medium",
        )
    return (
        VALUE,
        "magnitude-boundary",
        f"{candidate.distinct} distinct values over {frames} frames; bounded by "
        f"rises in bit flip rate, and reads with smoothness {candidate.score:.2f}",
        "high" if candidate.is_smooth else "medium",
    )


def _choose_byte_order(
    payload_ints: Sequence[int], start_bit: int, length: int
) -> Tuple[endian.Candidate, Optional[str]]:
    """Keep little-endian unless a byte-aligned field reads clearly better as big.

    Only byte-aligned fields are reconsidered.  Motorola ordering is a
    byte-level convention, and re-reading an arbitrary bit-packed field
    backwards would be a hypothesis nothing on the bus supports.
    """
    little = endian.evaluate(payload_ints, start_bit, length, endian.LITTLE)
    if length < MIN_BYTE_ORDER_WIDTH or start_bit % 8 or length % 8:
        return little, None

    be_start = (start_bit // 8) * 8 + 7
    big = endian.evaluate(payload_ints, be_start, length, endian.BIG)
    if big.score >= little.score + endian.DECISION_MARGIN:
        return big, (
            f"read big-endian: reads smoothly that way (score {big.score:.2f}) "
            f"and not little-endian ({little.score:.2f})"
        )
    return little, None


def segment(stats: MessageStats, payloads: Sequence[bytes]) -> List[Field]:
    """Partition one message's payload into fields."""
    dlc = stats.dlc
    frames = len(payloads)
    if dlc == 0 or frames == 0:
        return []

    payload_ints = endian.to_ints(payloads, dlc)
    fields: List[Field] = []
    taken: Set[int] = set()

    # A payload that never changed has no boundaries to find; presenting it as
    # one wide field would imply a signal that was never observed to move.
    if stats.is_constant:
        for byte in range(dlc):
            start = byte * 8
            value = (payload_ints[0] >> start) & 0xFF
            fields.append(
                Field(
                    start_bit=start,
                    length=8,
                    kind=CONSTANT,
                    rule="constant-message",
                    detail=(
                        f"the whole payload was identical in all {frames} frames; "
                        f"byte {byte} held 0x{value:02X}"
                    ),
                    confidence="high",
                    distinct=1,
                    values=[value],
                )
            )
        return fields

    # --- fields that break the magnitude assumption, found by behaviour ----
    counter = counters.find_counter(payload_ints, dlc)
    if counter is not None:
        fields.append(
            Field(
                start_bit=counter.start_bit,
                length=counter.length,
                kind=COUNTER,
                rule="rolling-counter",
                detail=counter.detail(frames),
                confidence="high",
            )
        )
        taken |= set(range(counter.start_bit, counter.start_bit + counter.length))
        if counter.constant_high_bits is not None:
            high = counter.start_bit + 4
            fields.append(
                Field(
                    start_bit=high,
                    length=4,
                    kind=CONSTANT,
                    rule="constant-nibble",
                    detail=(
                        f"held 0x{counter.constant_high_bits:X} in all {frames} frames, "
                        f"masking the rolling counter below it"
                    ),
                    confidence="high",
                    distinct=1,
                    values=[counter.constant_high_bits],
                )
            )
            taken |= set(range(high, high + 4))

    checksum = counters.find_checksum(payload_ints, dlc, exclude=taken)
    if checksum is not None:
        fields.append(
            Field(
                start_bit=checksum.start_bit,
                length=checksum.length,
                kind=CHECKSUM,
                rule="checksum",
                detail=checksum.detail(frames),
                confidence="medium",
            )
        )
        taken |= set(range(checksum.start_bit, checksum.start_bit + checksum.length))

    # --- magnitude segmentation over everything else ----------------------
    rates = [stats.flip_rate(i) for i in range(stats.n_bits)]
    samples = max(stats.count - 1, 1)

    for span in _runs([b for b in range(dlc * 8) if b not in taken]):
        cuts = snap_boundaries(rates, span, boundaries(rates, span, samples))
        byte_aligned = looks_byte_aligned(span, cuts)
        for index, cut in enumerate(cuts):
            end = cuts[index + 1] if index + 1 < len(cuts) else len(span)
            start_bit = span[cut]
            full_length = end - cut

            start_bit, length = trim_constant_tail(
                rates, start_bit, full_length, stats.n_bits, byte_aligned
            )
            candidate, order_note = _choose_byte_order(payload_ints, start_bit, length)
            kind, rule, detail, confidence = _classify(candidate, frames)
            if order_note:
                detail = f"{detail}; {order_note}"
            if length < full_length:
                detail += (
                    f"; the {full_length - length} bits above it never changed in "
                    f"{frames} frames and are reported separately rather than "
                    f"claimed as part of this field"
                )
            fields.append(
                Field(
                    start_bit=candidate.start_bit,
                    length=length,
                    byte_order=candidate.byte_order,
                    kind=kind,
                    rule=rule,
                    detail=detail,
                    confidence=confidence,
                    values=candidate.values,
                    distinct=candidate.distinct,
                    score=candidate.score,
                )
            )
            # The trimmed remainder still has to be named, or the DBC would
            # silently drop payload bytes it was handed.
            remainder = start_bit + full_length - (start_bit + length)
            offset = start_bit + length
            while remainder > 0:
                piece = min(8 - (offset % 8), remainder)
                fields.append(
                    Field(
                        start_bit=offset,
                        length=piece,
                        kind=CONSTANT,
                        rule="constant-bits",
                        detail=f"never changed across {frames} frames",
                        confidence="high",
                        distinct=1,
                    )
                )
                offset += piece
                remainder -= piece

    fields.sort(key=lambda f: f.start_bit)
    return fields
