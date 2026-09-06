"""Vehicle databases from opendbc.

opendbc is comma.ai's MIT-licensed collection of DBC files for production
cars.  It is the odd one out among the sources here, and the difference
matters more than the convenience does:

The others describe a *protocol*.  Any machine speaking ISOBUS sends PGN 44032
with the layout ISO 11783 publishes, so a database built from the standard is
true of every such machine.  opendbc describes *particular cars* -- one file
per platform, reverse-engineered by people driving them and watching the bus.
A file is evidence about the vehicles it was built from, and a manufacturer is
free to change a layout between model years without telling anyone.

So these are emitted as `community`, never `standard`, and the header of every
generated file says which platform file it came from.  Treat a match as a
strong lead, and check a signal against the car in front of you before you
trust it with anything that matters.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Tuple

from ..evidence import from_community
from ..model import BIG, LITTLE, Database, Message, Node, Signal
from .download import Asset, ensure
from .fetch import StandardDataError, cache_dir

REPO = "commaai/opendbc"
BRANCH = "master"
DBC_DIR = "opendbc/dbc"
INDEX_URL = f"https://api.github.com/repos/{REPO}/contents/{DBC_DIR}?ref={BRANCH}"
RAW_URL = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{DBC_DIR}/{{name}}.dbc"

LICENCE = "MIT"
ATTRIBUTION = f"opendbc, comma.ai -- https://github.com/{REPO}"


def _validate_index(payload: bytes) -> None:
    try:
        doc = json.loads(payload)
    except ValueError as exc:
        raise StandardDataError("opendbc index is not JSON") from exc
    if not isinstance(doc, list):
        message = doc.get("message", "unexpected shape") if isinstance(doc, dict) else ""
        raise StandardDataError(f"opendbc index could not be read: {message}")


INDEX_ASSET = Asset(
    name="opendbc-index",
    url=INDEX_URL,
    filename="opendbc-index.json",
    licence=LICENCE,
    attribution=ATTRIBUTION,
    validate=_validate_index,
)


def _validate_dbc(payload: bytes) -> None:
    text = payload[:4096].decode("utf-8", "replace")
    if "BO_ " not in text and "VERSION" not in text:
        raise StandardDataError(
            f"downloaded {len(payload)} bytes, which do not look like a DBC file"
        )


def platform_asset(name: str) -> Asset:
    return Asset(
        name=f"opendbc:{name}",
        url=RAW_URL.format(name=name),
        filename=f"opendbc-{name}.dbc",
        licence=LICENCE,
        attribution=ATTRIBUTION,
        validate=_validate_dbc,
    )


def platforms(refresh: bool = False) -> List[Tuple[str, int]]:
    """Every platform file opendbc publishes, as (name, size in bytes)."""
    info = ensure(INDEX_ASSET, refresh=refresh)
    entries = json.loads(Path(info.path).read_text(encoding="utf-8"))
    out = [
        (e["name"][: -len(".dbc")], int(e.get("size") or 0))
        for e in entries
        if isinstance(e, dict) and e.get("name", "").endswith(".dbc")
    ]
    return sorted(out)


def _byte_order(signal) -> str:
    return BIG if getattr(signal, "byte_order", "") == "big_endian" else LITTLE


def build(path: Path, platform: str) -> Database:
    """Re-emit an opendbc file through this tool's writer, with provenance."""
    try:
        import cantools
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise StandardDataError("cantools is required to read opendbc files") from exc

    loaded = cantools.database.load_file(str(path))
    messages: List[Message] = []
    nodes = {n.name for n in getattr(loaded, "nodes", []) or []}

    for m in loaded.messages:
        signals: List[Signal] = []
        for s in m.signals:
            multiplexer = None
            if getattr(s, "is_multiplexer", False):
                multiplexer = "M"
            elif getattr(s, "multiplexer_ids", None):
                multiplexer = f"m{s.multiplexer_ids[0]}"
            signals.append(
                Signal(
                    name=s.name,
                    start_bit=s.start,
                    length=s.length,
                    byte_order=_byte_order(s),
                    signed=bool(s.is_signed),
                    factor=float(s.scale),
                    offset=float(s.offset),
                    minimum=s.minimum,
                    maximum=s.maximum,
                    unit=(s.unit or ""),
                    values={int(k): str(v) for k, v in (s.choices or {}).items()},
                    comment=(s.comment or "")[:220],
                    multiplexer=multiplexer,
                    evidence=from_community(
                        "opendbc:signal",
                        f"{s.name} of {m.name}, from the opendbc platform file "
                        f"'{platform}'; reverse-engineered from vehicles, not "
                        f"published by the manufacturer",
                        platform=platform,
                    ),
                )
            )
        messages.append(
            Message(
                frame_id=m.frame_id,
                name=m.name,
                length=m.length,
                extended=bool(m.is_extended_frame),
                senders=[s for s in (m.senders or []) if s],
                comment=(m.comment or "")[:220],
                cycle_time=m.cycle_time,
                evidence=from_community(
                    "opendbc:message",
                    f"{m.name} carries {len(signals)} signal(s) per the opendbc "
                    f"platform file '{platform}'",
                    platform=platform,
                ),
                signals=signals,
            )
        )

    db = Database(
        version=f"generated by dbcgen from opendbc/{platform}",
        nodes=[Node(name=n, evidence=from_community(
            "opendbc:node", f"transmitter named by the opendbc file '{platform}'"))
            for n in sorted(nodes) if n and n != "Vector__XXX"],
        messages=messages,
    )
    db.header_notes = [
        f"Generated by dbcgen from the opendbc platform file '{platform}'. Every "
        "signal is marked 'community', NOT 'standard'.",
        "opendbc describes PARTICULAR VEHICLES, not a protocol. This file was "
        "reverse-engineered by people driving those cars and watching the bus; a "
        "manufacturer may change a layout between model years without telling "
        "anyone. Verify against the vehicle in front of you before relying on it.",
        f"source: {RAW_URL.format(name=platform)} ({LICENCE}, {ATTRIBUTION})",
    ]
    return db


def ensure_and_build(platform: str, refresh: bool = False) -> Database:
    info = ensure(platform_asset(platform), refresh=refresh)
    return build(Path(info.path), platform)
