"""29-bit J1939 arbitration id decomposition.

Layout (SAE J1939-21):

    bits 28..26  priority
    bit  25      EDP  extended data page
    bit  24      DP   data page
    bits 23..16  PF   PDU format
    bits 15..8   PS   PDU specific
    bits  7..0   SA   source address

PF < 240 is PDU1: the message is addressed, PS carries the destination address
and is *not* part of the PGN.  PF >= 240 is PDU2: the message is broadcast and
PS is the group extension, which *is* part of the PGN.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

ADDR_GLOBAL = 0xFF
ADDR_NULL = 0xFE

#: PGNs the pipeline needs to recognise by number rather than by name.
PGN_REQUEST = 59904        # 0xEA00  RQST
PGN_TP_DT = 60160          # 0xEB00  TP.DT
PGN_TP_CM = 60416          # 0xEC00  TP.CM
PGN_ADDRESS_CLAIMED = 60928  # 0xEE00  AC
PGN_PROPRIETARY_A = 61184  # 0xEF00
PGN_ETP_DT = 51712         # 0xC900
PGN_ETP_CM = 51968         # 0xCB00

TRANSPORT_PGNS = frozenset({PGN_TP_DT, PGN_TP_CM, PGN_ETP_DT, PGN_ETP_CM})


@dataclass(frozen=True, slots=True)
class J1939Id:
    """A decomposed 29-bit identifier."""

    can_id: int
    priority: int
    edp: int
    dp: int
    pf: int
    ps: int
    sa: int

    @property
    def is_pdu1(self) -> bool:
        return self.pf < 240

    @property
    def is_pdu2(self) -> bool:
        return self.pf >= 240

    @property
    def pgn(self) -> int:
        """Parameter Group Number.  PS is included only for PDU2."""
        base = (self.edp << 17) | (self.dp << 16) | (self.pf << 8)
        return base | self.ps if self.is_pdu2 else base

    @property
    def destination(self) -> Optional[int]:
        """Destination address, or None for a broadcast (PDU2) message."""
        return self.ps if self.is_pdu1 else None

    @property
    def is_proprietary(self) -> bool:
        """True for the PGN ranges J1939 reserves for manufacturers.

        Proprietary A (0xEF00), Proprietary A2 (0x1EF00) and the whole
        Proprietary B block (0xFF00..0xFFFF).
        """
        pgn = self.pgn
        return pgn in (PGN_PROPRIETARY_A, 0x1EF00) or 0xFF00 <= pgn <= 0xFFFF

    @property
    def is_transport(self) -> bool:
        return self.pgn in TRANSPORT_PGNS

    def __str__(self) -> str:
        return f"{self.can_id:08X}"


def decompose(can_id: int) -> J1939Id:
    """Split a 29-bit id into its J1939 fields."""
    return J1939Id(
        can_id=can_id & 0x1FFFFFFF,
        priority=(can_id >> 26) & 0x07,
        edp=(can_id >> 25) & 0x01,
        dp=(can_id >> 24) & 0x01,
        pf=(can_id >> 16) & 0xFF,
        ps=(can_id >> 8) & 0xFF,
        sa=can_id & 0xFF,
    )


def compose(pgn: int, sa: int, priority: int = 6, destination: int = ADDR_GLOBAL) -> int:
    """Build a 29-bit id from a PGN and a source address.

    For a PDU1 PGN the caller's `destination` fills PS; for PDU2 the PGN already
    carries its group extension and `destination` is ignored.
    """
    edp = (pgn >> 17) & 0x01
    dp = (pgn >> 16) & 0x01
    pf = (pgn >> 8) & 0xFF
    ps = destination & 0xFF if pf < 240 else pgn & 0xFF
    return (
        ((priority & 0x07) << 26)
        | (edp << 25)
        | (dp << 24)
        | (pf << 16)
        | (ps << 8)
        | (sa & 0xFF)
    )


def pgn_name_hint(jid: J1939Id) -> str:
    """Fallback message name when the standard database has nothing.

    Mirrors the naming used in the hand-built reference DBC: the PF/PS pair in
    hex, then the source address, so the name states exactly where the frame
    came from.
    """
    if jid.is_pdu2:
        return f"Proprietary_{jid.pf:02X}{jid.ps:02X}_{jid.sa:02X}"
    return f"PGN{jid.pgn}_{jid.sa:02X}_to_{jid.ps:02X}"
