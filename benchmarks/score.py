"""Score a generated DBC against the reference DBC its publisher shipped.

The reference bounds what can be measured.  A signal the reference never names
cannot be counted right or wrong, so every metric here is reported over
*reference* signals in messages that actually appear in the recording -- never
over everything the decoder happened to emit.  A tool that emits a thousand
speculative signals should not score better than one that emits eight correct
ones, so precision is reported alongside recall.

Metrics, weakest claim to strongest:

`message_recall`
    Reference messages present in the generated file.  Nearly free for a
    J1939 bus, where the id itself names the message.

`bit_coverage`
    Fraction of reference signal bits that some generated signal covers.  Raw
    `ByteN` fallbacks score full marks here, which is the point: it measures
    whether the bytes were kept, not whether they were understood.

`mean_jaccard`
    Per reference signal, the best bit-set overlap with any generated signal in
    the same message.  Partial credit for boundaries that are close.

`boundary_match`
    Exact start bit, length and byte order.  The real test of segmentation.

`exact_match`
    Boundary plus signedness.

`value_recoverable`
    Whether the generated DBC yields a signal whose raw values, across every
    frame, are identical to the reference signal's.  The metric that decides
    whether a user can actually get the number off the bus.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

BIG = "big_endian"


# --------------------------------------------------------------------------
# Reference and candidate signals are reduced to a common shape so the two
# DBCs can be compared without either library's object model leaking in.
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class SignalShape:
    name: str
    start: int
    length: int
    byte_order: str
    signed: bool

    @property
    def key(self) -> Tuple[int, int, str]:
        return (self.start, self.length, self.byte_order)

    def bit_set(self) -> frozenset:
        """Payload bits covered, resolved for either byte order.

        A big-endian signal's DBC start bit is its MSB and it walks downward
        inside a byte before continuing at the top of the next, so its bits are
        not a contiguous range in little-endian numbering.
        """
        if self.byte_order != BIG:
            return frozenset(range(self.start, self.start + self.length))
        covered = []
        byte, bit = divmod(self.start, 8)
        for _ in range(self.length):
            covered.append(byte * 8 + bit)
            if bit == 0:
                byte, bit = byte + 1, 7
            else:
                bit -= 1
        return frozenset(covered)


def shapes_of(message) -> List[SignalShape]:
    return [
        SignalShape(
            name=s.name,
            start=s.start,
            length=s.length,
            byte_order=s.byte_order,
            signed=bool(s.is_signed),
        )
        for s in message.signals
    ]


def jaccard(a: frozenset, b: frozenset) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


# --------------------------------------------------------------------------
@dataclass
class MessageScore:
    frame_id: int
    extended: bool
    name_reference: str
    name_generated: str = ""
    found: bool = False
    frames: int = 0
    reference_signals: int = 0
    generated_signals: int = 0
    bit_coverage: float = 0.0
    mean_jaccard: float = 0.0
    boundary_matches: int = 0
    exact_matches: int = 0
    value_recoverable: int = 0
    details: List[str] = field(default_factory=list)

    @property
    def hex(self) -> str:
        return f"{self.frame_id:08X}" if self.extended else f"{self.frame_id:03X}"


@dataclass
class Score:
    dataset: str = ""
    frames: int = 0
    messages_reference: int = 0
    messages_observed: int = 0
    messages_found: int = 0
    messages_generated: int = 0
    signals_reference: int = 0
    signals_generated: int = 0
    signals_observable: int = 0
    """Reference signals whose bits actually changed during the recording.

    A signal that held one value throughout leaves no trace of where its
    boundaries lie, so no method could recover it; counting it as a miss
    measures the recording rather than the decoder.  Both denominators are
    reported, and the difference between them is itself a useful number -- it
    says how much of the reference the log had no chance of confirming.
    """
    exact_observable: int = 0
    value_observable: int = 0
    bit_coverage: float = 0.0
    mean_jaccard: float = 0.0
    boundary_matches: int = 0
    exact_matches: int = 0
    value_recoverable: int = 0
    per_message: List[MessageScore] = field(default_factory=list)

    def _rate(self, n: int) -> float:
        return n / self.signals_reference if self.signals_reference else 0.0

    @property
    def message_recall(self) -> float:
        return self.messages_found / self.messages_observed if self.messages_observed else 0.0

    @property
    def boundary_rate(self) -> float:
        return self._rate(self.boundary_matches)

    @property
    def exact_rate(self) -> float:
        return self._rate(self.exact_matches)

    @property
    def value_rate(self) -> float:
        return self._rate(self.value_recoverable)

    @property
    def exact_rate_observable(self) -> float:
        return self.exact_observable / self.signals_observable if self.signals_observable else 0.0

    @property
    def value_rate_observable(self) -> float:
        return self.value_observable / self.signals_observable if self.signals_observable else 0.0

    @property
    def signal_precision(self) -> float:
        """Exactly-matched signals as a share of everything the decoder emitted.

        Guards against scoring well by emitting every possible field.
        """
        return self.exact_matches / self.signals_generated if self.signals_generated else 0.0

    def report(self) -> str:
        lines = [
            f"dataset            : {self.dataset}",
            f"frames             : {self.frames}",
            "",
            f"messages (reference): {self.messages_reference}"
            f", observed in log: {self.messages_observed}",
            f"messages (generated): {self.messages_generated}",
            f"message recall      : {self.message_recall:6.1%}"
            f"  ({self.messages_found}/{self.messages_observed})",
            "",
            f"reference signals   : {self.signals_reference}"
            f"   generated signals: {self.signals_generated}",
            f"bit coverage        : {self.bit_coverage:6.1%}"
            "   (bytes kept, not necessarily understood)",
            f"mean bit Jaccard    : {self.mean_jaccard:6.1%}"
            "   (best overlap per reference signal)",
            f"boundary match      : {self.boundary_rate:6.1%}"
            f"  ({self.boundary_matches}/{self.signals_reference})"
            "  start+length+order",
            f"exact match         : {self.exact_rate:6.1%}"
            f"  ({self.exact_matches}/{self.signals_reference})"
            "  + signedness",
            f"value recoverable   : {self.value_rate:6.1%}"
            f"  ({self.value_recoverable}/{self.signals_reference})"
            "  identical raw values",
            f"signal precision    : {self.signal_precision:6.1%}"
            "   exact matches / signals emitted",
            "",
            f"observable signals  : {self.signals_observable}/{self.signals_reference}"
            f"   ({self.signals_reference - self.signals_observable} held one value "
            f"for the whole recording, so no method could place them)",
            f"  exact match       : {self.exact_rate_observable:6.1%}"
            f"  ({self.exact_observable}/{self.signals_observable})",
            f"  value recoverable : {self.value_rate_observable:6.1%}"
            f"  ({self.value_observable}/{self.signals_observable})",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "dataset": self.dataset,
            "frames": self.frames,
            "messages_reference": self.messages_reference,
            "messages_observed": self.messages_observed,
            "messages_found": self.messages_found,
            "messages_generated": self.messages_generated,
            "message_recall": round(self.message_recall, 4),
            "signals_reference": self.signals_reference,
            "signals_generated": self.signals_generated,
            "bit_coverage": round(self.bit_coverage, 4),
            "mean_jaccard": round(self.mean_jaccard, 4),
            "boundary_match": round(self.boundary_rate, 4),
            "exact_match": round(self.exact_rate, 4),
            "value_recoverable": round(self.value_rate, 4),
            "signal_precision": round(self.signal_precision, 4),
            "signals_observable": self.signals_observable,
            "exact_match_observable": round(self.exact_rate_observable, 4),
            "value_recoverable_observable": round(self.value_rate_observable, 4),
        }


def _index(database) -> Dict[Tuple[int, bool], object]:
    return {(m.frame_id, bool(m.is_extended_frame)): m for m in database.messages}


#: J1939 source addresses that mean "any node" when they appear in a DBC.
#: 0xFE is the NULL address and 0xFF the global one; publishers routinely use
#: one of them as a placeholder so a single entry covers a PGN from whichever
#: ECU happens to send it.
WILDCARD_SOURCE_ADDRESSES = (0xFE, 0xFF)


def _pgn_of(can_id: int) -> int:
    """PGN of a 29-bit id, per J1939-21: PS is part of it only for PDU2."""
    pf = (can_id >> 16) & 0xFF
    base = ((can_id >> 24) & 0x03) << 16 | (pf << 8)
    return base | ((can_id >> 8) & 0xFF) if pf >= 240 else base


def observed_for(ref_key: Tuple[int, bool], observed: Dict[Tuple[int, bool], object]) -> list:
    """Which recorded ids a reference message applies to.

    Usually just the identical id.  But a J1939 reference written against a
    wildcard source address describes a parameter group rather than one frame,
    so it applies to every recorded id carrying that PGN -- and this decoder
    keys messages on the whole 29-bit id, source address included, which is
    what a DBC consumer matches on.  Comparing the two conventions without
    this step scores a correct result as a total miss.
    """
    can_id, extended = ref_key
    if ref_key in observed:
        return [ref_key]
    if not extended or (can_id & 0xFF) not in WILDCARD_SOURCE_ADDRESSES:
        return []
    pgn = _pgn_of(can_id)
    return [k for k in observed if k[1] and _pgn_of(k[0]) == pgn]


def _raw_values(shape: SignalShape, payloads: Iterable[bytes], length: int) -> Optional[tuple]:
    """The signal's raw integer value in every frame, or None if it never fits."""
    out = []
    for data in payloads:
        padded = data.ljust(length, b"\x00")[:length]
        bits = int.from_bytes(padded, "little")
        value = 0
        for i, bit_index in enumerate(sorted(shape.bit_set())):
            value |= ((bits >> bit_index) & 1) << i
        out.append(value)
    return tuple(out) if out else None


def score(reference, generated, frameset, dataset: str = "") -> Score:
    """Compare two loaded cantools databases over the frames actually recorded."""
    ref_index = _index(reference)
    gen_index = _index(generated)
    observed = {(g.can_id, g.extended): g for g in frameset}

    result = Score(
        dataset=dataset,
        frames=frameset.total,
        messages_reference=len(reference.messages),
        messages_generated=len(generated.messages),
    )
    jaccards: List[float] = []
    bits = [0, 0]  # covered, total

    for ref_key, ref_msg in sorted(ref_index.items()):
        # One reference entry can describe several recorded ids -- see
        # `observed_for` on the J1939 wildcard source address.
        for key in sorted(observed_for(ref_key, observed)):
            _score_one(key, ref_msg, observed[key], gen_index, result, jaccards, bits)

    result.bit_coverage = bits[0] / bits[1] if bits[1] else 0.0
    result.mean_jaccard = sum(jaccards) / len(jaccards) if jaccards else 0.0
    return result


def _score_one(key, ref_msg, group, gen_index, result, jaccards, bits) -> None:
    """Score one reference message against the frames recorded under one id."""
    result.messages_observed += 1
    ms = MessageScore(
        frame_id=key[0], extended=key[1], name_reference=ref_msg.name, frames=len(group)
    )
    ref_shapes = shapes_of(ref_msg)
    ms.reference_signals = len(ref_shapes)
    result.signals_reference += len(ref_shapes)

    gen_msg = gen_index.get(key)
    if gen_msg is None:
        bits[1] += sum(len(s.bit_set()) for s in ref_shapes)
        result.per_message.append(ms)
        return

    ms.found = True
    result.messages_found += 1
    ms.name_generated = gen_msg.name
    gen_shapes = shapes_of(gen_msg)
    ms.generated_signals = len(gen_shapes)
    result.signals_generated += len(gen_shapes)

    gen_by_key = {s.key: s for s in gen_shapes}
    gen_covered = frozenset().union(*(s.bit_set() for s in gen_shapes)) if gen_shapes else frozenset()

    length = max(gen_msg.length, ref_msg.length)
    payloads = [f.data for f in group.frames]
    gen_values = {s.key: _raw_values(s, payloads, length) for s in gen_shapes}

    for ref in ref_shapes:
        ref_bits = ref.bit_set()
        bits[1] += len(ref_bits)
        bits[0] += len(ref_bits & gen_covered)
        jaccards.append(max((jaccard(ref_bits, g.bit_set()) for g in gen_shapes), default=0.0))

        wanted = _raw_values(ref, payloads, length)
        observable = wanted is not None and len(set(wanted)) > 1
        if observable:
            result.signals_observable += 1

        hit = gen_by_key.get(ref.key)
        if hit is not None:
            ms.boundary_matches += 1
            result.boundary_matches += 1
            if hit.signed == ref.signed:
                ms.exact_matches += 1
                result.exact_matches += 1
                if observable:
                    result.exact_observable += 1

        if wanted is not None and any(v == wanted for v in gen_values.values() if v):
            ms.value_recoverable += 1
            result.value_recoverable += 1
            if observable:
                result.value_observable += 1

    result.per_message.append(ms)
