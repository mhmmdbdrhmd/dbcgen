"""Parse `SPNs and PGNs.csv` from the ISOBUS export into usable definitions.

Coverage is deliberately split, and the split is preserved in the data model:

* ~1.3k SPNs across ~100 PGNs carry a full bit layout (start bit, length,
  resolution, offset, unit).  These come from the freely published ISO 11783
  parts and are authoritative -- `SpnDef.has_layout` is True.
* ~15k further SPNs are named but carry no layout, because SAE publishes those
  only in the paid J1939 Digital Annex.  Their `pgn_description` says as much.
  These give a real message and signal *name*, and nothing else; the layout has
  to be inferred from the recording or supplied by a licensed DA overlay.

Bit positions in the export use the J1939 `byte.bit` convention, 1-based in both
halves, bit 1 being the LSB of the byte.  DBC start bits for little-endian
signals are `(byte - 1) * 8 + (bit - 1)`, which is what `start_bit` holds.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .fetch import CacheInfo, open_member

MEMBER = "SPNs and PGNs.csv"

_BYTE_BIT = re.compile(r"^\s*(\d+)\s*[.,]\s*(\d+)\s*$")
_LENGTH = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(bit|bits|byte|bytes)?\s*$", re.I)
_RATE_MS = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|s\b|seconds?)", re.I)
_NAME_CLEAN = re.compile(r"[^0-9A-Za-z]+")


def parse_start_bit(text: str) -> Optional[int]:
    """`byte.bit` (1-based, bit 1 = LSB) -> DBC little-endian start bit.

    The export uses both '.' and ',' as the separator.  A bare byte number means
    the first bit of that byte.
    """
    text = (text or "").strip()
    if not text:
        return None
    m = _BYTE_BIT.match(text)
    if m:
        byte, bit = int(m.group(1)), int(m.group(2))
        if byte < 1 or not 1 <= bit <= 8:
            return None
        return (byte - 1) * 8 + (bit - 1)
    if text.isdigit():
        return (int(text) - 1) * 8
    return None


def parse_length_bits(text: str) -> Optional[int]:
    """'16 bits', '2 bits', '1 byte', bare '16' -> a bit count."""
    text = (text or "").strip()
    if not text:
        return None
    m = _LENGTH.match(text)
    if not m:
        return None
    value = float(m.group(1))
    unit = (m.group(2) or "bits").lower()
    bits = value * 8 if unit.startswith("byte") else value
    return int(bits) if bits > 0 and bits == int(bits) else None


def parse_cycle_ms(text: str) -> Optional[int]:
    """Extract a cycle time from the transmission-rate prose.

    Only a plain periodic rate is taken.  'As Required', 'On request' and the
    conditional phrasings describe event-driven traffic, and writing a
    GenMsgCycleTime for those would make a monitoring tool report loss during
    perfectly normal quiet.
    """
    text = (text or "").replace(" ", " ").replace("\xa0", " ").strip()
    if not text:
        return None
    low = text.lower()
    if low.startswith(("as ", "on ", "when ", "each ", "every time")):
        return None
    m = _RATE_MS.search(text)
    if not m:
        return None
    value = float(m.group(1))
    if m.group(2).lower().startswith("s"):
        value *= 1000
    return int(value) if 0 < value <= 60_000 else None


def parse_number(text: str) -> Optional[float]:
    text = (text or "").strip()
    if not text or text in {"-", "N/A", "NA"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def to_identifier(text: str, fallback: str = "Unnamed") -> str:
    """A DBC-safe CamelCase identifier built from a standard name."""
    parts = [p for p in _NAME_CLEAN.split(text or "") if p]
    if not parts:
        return fallback
    out = []
    for p in parts:
        out.append(p if p.isupper() and len(p) <= 4 else p[:1].upper() + p[1:])
    name = "".join(out)
    if name[0].isdigit():
        name = "_" + name
    return name


@dataclass
class SpnDef:
    """One Suspect Parameter Number as the standard describes it."""

    spn: int
    name: str
    description: str = ""
    document: str = ""

    start_bit: Optional[int] = None
    length_bits: Optional[int] = None
    factor: Optional[float] = None
    offset: Optional[float] = None
    unit: str = ""
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    slot_id: str = ""
    slot_name: str = ""
    position: str = ""

    @property
    def has_layout(self) -> bool:
        """True when the export gives enough to place the signal exactly."""
        return self.start_bit is not None and self.length_bits is not None

    @property
    def has_scaling(self) -> bool:
        return self.factor is not None

    @property
    def identifier(self) -> str:
        return to_identifier(self.name, f"SPN{self.spn}")

    @property
    def is_bit_state(self) -> bool:
        """SAEbsNN -- a discrete state parameter with J1939's standard top-of-range codes."""
        return self.slot_name.upper().startswith("SAEBS")

    @property
    def end_bit(self) -> Optional[int]:
        if not self.has_layout:
            return None
        return self.start_bit + self.length_bits - 1


@dataclass
class PgnDef:
    """One Parameter Group, with whatever SPNs the export lists for it."""

    pgn: int
    label: str = ""
    acronym: str = ""
    description: str = ""
    document: str = ""
    dlc: Optional[int] = None
    priority: Optional[int] = None
    cycle_ms: Optional[int] = None
    multipacket: bool = False
    spns: List[SpnDef] = field(default_factory=list)

    @property
    def identifier(self) -> str:
        """Prefer the acronym: short, standard, and what engineers actually say."""
        if self.acronym and self.acronym.strip("-"):
            return to_identifier(self.acronym, f"PGN{self.pgn}")
        return to_identifier(self.label, f"PGN{self.pgn}")

    @property
    def laid_out_spns(self) -> List[SpnDef]:
        return [s for s in self.spns if s.has_layout]

    @property
    def has_layout(self) -> bool:
        """True when the standard can place at least one signal in this PGN."""
        return any(s.has_layout for s in self.spns)

    @property
    def name_only(self) -> bool:
        """Named by the standard, but its layout is paywalled."""
        return bool(self.spns) and not self.has_layout


class StandardDatabase:
    """PGN and SPN definitions, indexed by PGN."""

    def __init__(self, pgns: Dict[int, PgnDef], source: str = ""):
        self.pgns = pgns
        self.source = source

    def get(self, pgn: int) -> Optional[PgnDef]:
        return self.pgns.get(pgn)

    def __len__(self) -> int:
        return len(self.pgns)

    @property
    def stats(self) -> Dict[str, int]:
        laid_out = [p for p in self.pgns.values() if p.has_layout]
        return {
            "pgns": len(self.pgns),
            "pgns_with_layout": len(laid_out),
            "pgns_name_only": sum(1 for p in self.pgns.values() if p.name_only),
            "spns": sum(len(p.spns) for p in self.pgns.values()),
            "spns_with_layout": sum(len(p.laid_out_spns) for p in self.pgns.values()),
        }


def _spn_from_row(row: Dict[str, str]) -> Optional[SpnDef]:
    try:
        spn = int((row.get("spn") or "").strip())
    except ValueError:
        return None

    minimum = maximum = None
    rng = (row.get("data_range") or "").strip()
    if " to " in rng:
        lo, _, hi = rng.partition(" to ")
        minimum = parse_number(lo)
        # The range carries its unit: '-8032 to 8031.75 km-1'.
        maximum = parse_number(hi.split(" ")[0] if " " in hi.strip() else hi)

    return SpnDef(
        spn=spn,
        name=(row.get("spn_name") or "").strip(),
        description=(row.get("spn_description") or "").strip(),
        document=(row.get("spn_document") or "").strip(),
        start_bit=parse_start_bit(row.get("sp_start_bit", "")),
        length_bits=parse_length_bits(row.get("spn_length", "")),
        factor=parse_number(row.get("scale_factor", "")),
        offset=parse_number(row.get("offset_value", "")),
        unit=(row.get("units") or "").strip(),
        minimum=minimum,
        maximum=maximum,
        slot_id=(row.get("slot_identifier") or "").strip(),
        slot_name=(row.get("slot_name") or "").strip(),
        position=(row.get("spn_position_in_pgn") or "").strip(),
    )


def parse(text: str, source: str = "") -> StandardDatabase:
    """Parse the CSV text into a StandardDatabase."""
    pgns: Dict[int, PgnDef] = {}
    seen: Dict[int, set] = {}

    for row in csv.DictReader(io.StringIO(text)):
        raw_pgn = (row.get("pgn") or "").strip()
        if not raw_pgn.isdigit():
            continue
        pgn = int(raw_pgn)

        entry = pgns.get(pgn)
        if entry is None:
            entry = pgns[pgn] = PgnDef(
                pgn=pgn,
                label=(row.get("parameter_group_label") or "").strip(),
                acronym=(row.get("acronym") or "").strip(),
                description=(row.get("pgn_description") or "").strip(),
                document=(row.get("pgn_document") or "").strip(),
                dlc=int(row["pgn_data_length"])
                if (row.get("pgn_data_length") or "").strip().isdigit()
                else None,
                priority=int(row["default_priority"])
                if (row.get("default_priority") or "").strip().isdigit()
                else None,
                cycle_ms=parse_cycle_ms(row.get("transmission_rate", "")),
                multipacket=(row.get("multipacket") or "").strip().lower() == "yes",
            )
            seen[pgn] = set()

        spn = _spn_from_row(row)
        if spn is not None and spn.spn not in seen[pgn]:
            seen[pgn].add(spn.spn)
            entry.spns.append(spn)

    for entry in pgns.values():
        entry.spns.sort(key=lambda s: (s.start_bit if s.start_bit is not None else 1 << 30, s.spn))

    return StandardDatabase(pgns, source=source)


def load(info: CacheInfo) -> StandardDatabase:
    return parse(open_member(info, MEMBER), source=info.short())
