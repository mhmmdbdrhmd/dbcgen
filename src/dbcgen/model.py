"""The intermediate model the emitters render.

Deliberately close to the DBC object model, but every object also carries the
`Evidence` that put it there, which a DBC has nowhere to store.  The writer
folds that into `CM_` comments; the JSON report keeps it in full.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .evidence import Evidence

LITTLE = "little"
BIG = "big"

#: DBC marks a 29-bit identifier by setting the top bit of the message id.
EXTENDED_FLAG = 0x80000000


@dataclass
class Signal:
    name: str
    start_bit: int
    length: int
    byte_order: str = LITTLE
    signed: bool = False
    factor: float = 1.0
    offset: float = 0.0
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    unit: str = ""
    receivers: List[str] = field(default_factory=list)
    comment: str = ""
    evidence: Optional[Evidence] = None
    values: Dict[int, str] = field(default_factory=dict)
    spn: Optional[int] = None
    multiplexer: Optional[str] = None
    """None, 'M' for the multiplexer switch, or 'mN' for a multiplexed signal."""

    def __post_init__(self) -> None:
        if self.minimum is None:
            self.minimum = self.offset
        if self.maximum is None:
            top = (1 << (self.length - 1)) - 1 if self.signed else (1 << self.length) - 1
            self.maximum = top * self.factor + self.offset

    @property
    def bits(self) -> range:
        """Bit indices this signal occupies, in DBC little-endian numbering.

        Only meaningful for little-endian signals; big-endian signals are
        compared through `bit_set()`.
        """
        return range(self.start_bit, self.start_bit + self.length)

    def bit_set(self) -> set:
        """The payload bits covered, resolved for either byte order.

        For a big-endian (Motorola) signal the DBC start bit is the MSB, and the
        signal walks downward within a byte then continues at the top of the
        next byte -- so the covered bits are not a contiguous range in
        little-endian numbering.
        """
        if self.byte_order == LITTLE:
            return set(self.bits)
        covered = set()
        byte, bit = divmod(self.start_bit, 8)
        for _ in range(self.length):
            covered.add(byte * 8 + bit)
            if bit == 0:
                byte += 1
                bit = 7
            else:
                bit -= 1
        return covered


@dataclass
class Message:
    frame_id: int
    """The raw arbitration id, without the DBC extended flag."""
    name: str
    length: int
    extended: bool = True
    senders: List[str] = field(default_factory=list)
    comment: str = ""
    cycle_time: Optional[int] = None
    evidence: Optional[Evidence] = None
    signals: List[Signal] = field(default_factory=list)
    pgn: Optional[int] = None
    source_address: Optional[int] = None

    @property
    def dbc_id(self) -> int:
        return self.frame_id | EXTENDED_FLAG if self.extended else self.frame_id

    @property
    def hex(self) -> str:
        return f"{self.frame_id:08X}" if self.extended else f"{self.frame_id:03X}"

    def sorted_signals(self) -> List[Signal]:
        return sorted(self.signals, key=lambda s: (s.start_bit, s.name))


@dataclass
class Node:
    name: str
    comment: str = ""
    evidence: Optional[Evidence] = None
    source_address: Optional[int] = None


@dataclass
class Database:
    version: str = ""
    nodes: List[Node] = field(default_factory=list)
    messages: List[Message] = field(default_factory=list)
    header_notes: List[str] = field(default_factory=list)
    """Provenance lines written above the DBC, e.g. which export was used."""

    def node_names(self) -> List[str]:
        return sorted({n.name for n in self.nodes})

    def sorted_messages(self) -> List[Message]:
        return sorted(self.messages, key=lambda m: (m.extended, m.frame_id))
