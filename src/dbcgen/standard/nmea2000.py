"""NMEA 2000 parameter groups, from the canboat database.

NMEA 2000 runs on the same 29-bit J1939 addressing this tool already
decomposes, so its parameter groups drop straight into the same model as the
ISOBUS ones.  What differs is where the definitions come from and how much
they can be trusted:

NMEA sells its standard and does not publish a machine-readable form.
`canboat` is an Apache-2.0 database reconstructed from observed traffic and
public sources, and it says so.  That is far better evidence than counting bit
flips on one recording, and it is *not* the protocol's owner stating a fact, so
everything here is emitted as `community` rather than `standard`.  Two claims
of different strength must not arrive wearing the same label.

Two further honesties are enforced here:

* Only single-frame groups are emitted.  Most of NMEA 2000 is carried in
  "Fast" packets, which span several frames and cannot be described by one
  message in a DBC any more than a J1939 multi-packet group can.
* canboat marks a definition `Complete: false` when it knows the
  reconstruction is partial.  Those are still emitted -- a partial map beats
  none -- but the message comment says which they are, so nobody mistakes a
  work in progress for a finished one.
"""

from __future__ import annotations

import json
import pathlib
from typing import Dict, List, Optional

from ..evidence import from_community
from ..model import Database, Message, Signal
from .download import Asset, ensure
from .fetch import StandardDataError

CANBOAT_URL = (
    "https://raw.githubusercontent.com/canboat/canboat/master/docs/canboat.json"
)

#: Field types that describe padding rather than a value.  Emitting them would
#: fill a database with signals nobody wants to see in a trace viewer.
_SKIP_FIELD_TYPES = {"RESERVED", "SPARE"}

#: Groups NMEA 2000 shares with J1939, whose layouts the network-layer part of
#: J1939 publishes.  Those are authoritative and already emitted as `standard`.
_SHARED_WITH_J1939 = {59392, 59904, 60160, 60416, 60928, 65240}

MAX_SINGLE_FRAME_DLC = 8


def _is_proprietary(pgn: int) -> bool:
    """Proprietary A and the Proprietary B block.

    canboat describes several manufacturers' meanings for each of these,
    distinguished by a matched manufacturer code inside the payload.  A DBC
    keyed on the identifier cannot hold more than one of them, and picking one
    would assert that every proprietary frame on the bus belongs to whichever
    vendor sorted first.  They are left to inference over a recording instead,
    which is the same call the J1939 path makes for the same reason.
    """
    return pgn in (0xEF00, 0x1EF00) or 0xFF00 <= pgn <= 0xFFFF


def _validate(payload: bytes) -> None:
    try:
        doc = json.loads(payload)
    except ValueError as exc:
        raise StandardDataError(
            f"canboat database is not JSON ({len(payload)} bytes)"
        ) from exc
    if "PGNs" not in doc:
        raise StandardDataError("canboat database has no 'PGNs' key")


ASSET = Asset(
    name="canboat",
    url=CANBOAT_URL,
    filename="canboat.json",
    licence="Apache-2.0",
    attribution="CANboat, (C) Kees Verruijt -- https://github.com/canboat/canboat",
    validate=_validate,
)


def _identifier(text: str, fallback: str) -> str:
    out = "".join(c if c.isalnum() or c == "_" else "_" for c in (text or ""))
    out = "_".join(p for p in out.split("_") if p)
    if not out:
        return fallback
    if out[0].isdigit():
        out = f"S_{out}"
    return out[:64]


def _value_table(doc: dict, field: dict) -> Dict[int, str]:
    """Turn a canboat lookup enumeration into DBC VAL_ entries."""
    name = field.get("LookupEnumeration")
    if not name:
        return {}
    for enum in doc.get("LookupEnumerations", []) or []:
        if enum.get("Name") == name:
            return {
                int(v["Value"]): _identifier(v.get("Name", ""), f"V{v['Value']}")
                for v in enum.get("EnumValues", []) or []
                if v.get("Value") is not None
            }
    return {}


def _usable(pgn: dict) -> bool:
    if pgn.get("Type") != "Single":
        return False
    # canboat's `Fallback` entries describe a whole identifier *range* -- "any
    # standardized single-frame addressed group" -- rather than one group.
    # Emitting one as a message would put a made-up name on every frame in the
    # range it stands for.
    if pgn.get("Fallback"):
        return False
    length = pgn.get("Length") or 0
    if not 1 <= length <= MAX_SINGLE_FRAME_DLC:
        return False
    fields = [
        f
        for f in (pgn.get("Fields") or [])
        if f.get("BitOffset") is not None and f.get("BitLength")
    ]
    if not fields:
        return False
    return max(f["BitOffset"] + f["BitLength"] for f in fields) <= length * 8


def _pick_variant(candidates: List[dict]) -> dict:
    """One message per PGN.

    canboat lists several entries for a group whose payload is multiplexed on a
    matched field.  Its variants do not always share a common selector, so the
    richest complete one is taken and the comment records that others exist.
    """
    return max(
        candidates,
        key=lambda p: (bool(p.get("Complete")), len(p.get("Fields") or [])),
    )


def _signals(doc: dict, pgn: dict) -> List[Signal]:
    out: List[Signal] = []
    used = set()
    for field in pgn.get("Fields") or []:
        if field.get("FieldType") in _SKIP_FIELD_TYPES:
            continue
        start, length = field.get("BitOffset"), field.get("BitLength")
        if start is None or not length:
            continue
        name = _identifier(field.get("Id") or field.get("Name", ""), f"Field{start}")
        while name in used:
            name = f"{name}_{start}"
        used.add(name)

        resolution = field.get("Resolution")
        factor = float(resolution) if resolution not in (None, 0) else 1.0
        out.append(
            Signal(
                name=name,
                start_bit=int(start),
                length=int(length),
                signed=bool(field.get("Signed")),
                factor=factor,
                offset=float(field.get("Offset") or 0.0),
                minimum=field.get("RangeMin"),
                maximum=field.get("RangeMax"),
                unit=(field.get("Unit") or "").strip(),
                values=_value_table(doc, field),
                comment=(field.get("Description") or "")[:220],
                evidence=from_community(
                    "canboat:field",
                    f"{field.get('Name', name)} of PGN {pgn['PGN']}, "
                    f"{length} bits at bit {start}, per the canboat database",
                    pgn=pgn["PGN"],
                ),
            )
        )
    return out


def load(path) -> dict:
    """Read the cached canboat JSON."""
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def build(
    doc: dict,
    source_address: int = 0xFE,
    pgns: Optional[List[int]] = None,
) -> Database:
    """A database of every single-frame NMEA 2000 group canboat describes."""
    from ..j1939.ids import compose

    wanted = set(pgns) if pgns else None
    by_pgn: Dict[int, List[dict]] = {}
    for entry in doc.get("PGNs", []) or []:
        number = entry.get("PGN")
        if number is None or number in _SHARED_WITH_J1939:
            continue
        if _is_proprietary(int(number)):
            continue
        if wanted is not None and number not in wanted:
            continue
        if not _usable(entry):
            continue
        by_pgn.setdefault(int(number), []).append(entry)

    messages: List[Message] = []
    for number in sorted(by_pgn):
        variants = by_pgn[number]
        entry = _pick_variant(variants)
        signals = _signals(doc, entry)
        if not signals:
            continue

        note = entry.get("Description") or entry.get("Id") or f"PGN {number}"
        caveats = []
        if not entry.get("Complete"):
            caveats.append(
                "canboat marks this definition incomplete, so fields may be "
                "missing or wrong"
            )
        if len(variants) > 1:
            caveats.append(
                f"canboat lists {len(variants)} variants; the most complete is used"
            )
        comment = note + ("; " + "; ".join(caveats) if caveats else "")

        messages.append(
            Message(
                frame_id=compose(
                    number, source_address, priority=int(entry.get("Priority") or 6)
                ),
                name=_identifier(entry.get("Id") or "", f"PGN{number}")
                + f"_{source_address:02X}",
                length=int(entry.get("Length") or MAX_SINGLE_FRAME_DLC),
                extended=True,
                comment=comment[:220],
                pgn=number,
                source_address=source_address,
                evidence=from_community(
                    "canboat:pgn",
                    f"{note}; {len(signals)} field(s) from the canboat database"
                    + (
                        "; marked incomplete upstream"
                        if not entry.get("Complete")
                        else ""
                    ),
                    confidence="high" if entry.get("Complete") else "medium",
                    pgn=number,
                ),
                signals=signals,
            )
        )

    db = Database(
        version=f"generated by dbcgen from canboat {doc.get('Version', '?')}",
        nodes=[],
        messages=messages,
    )
    db.header_notes = [
        "Generated by dbcgen from the canboat NMEA 2000 database. Every signal here "
        "is marked 'community', NOT 'standard': NMEA sells its specification and "
        "does not publish a machine-readable form, so these definitions were "
        "reconstructed from observed traffic and public sources.",
        f"source: {CANBOAT_URL} (canboat {doc.get('Version', '?')}, Apache-2.0)",
        "Only single-frame groups appear. Most of NMEA 2000 travels in multi-frame "
        "'Fast' packets, which no single DBC message can describe.",
        "Proprietary groups are omitted: canboat knows several manufacturers' "
        "meanings for each, only one can occupy an identifier in a DBC, and "
        "choosing one would mislabel every other vendor's frames.",
        f"Source address 0x{source_address:02X} is a placeholder; match on the PGN.",
    ]
    return db


def ensure_and_build(refresh: bool = False, **kw) -> Database:
    info = ensure(ASSET, refresh=refresh)
    return build(load(info.path), **kw)
