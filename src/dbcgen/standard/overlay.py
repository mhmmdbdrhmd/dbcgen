"""A database the user already owns, used as the authoritative layer.

Most of SAE J1939 is sold, not published: the free registry names about 2,764
parameter groups and gives the bit layout of none of them.  Anyone who has
bought the Digital Annex, or a commercial DBC built from it, already holds the
answer this tool otherwise has to guess at.

This reads such a file from a path the user gives and lets it outrank
inference.  Three rules keep it safe:

* The file is read where it lies.  It is never copied into the cache, never
  written into the output directory, and never committed -- licensed material
  stays where its licence-holder put it.
* A layout that comes from it is marked `standard`, because it is the
  publisher's own definition, and the generated file records the file's name
  so a reader knows an external source was involved.
* Matching is by full identifier first and by parameter group second, so a
  database written for source address 0x00 still resolves traffic from an ECU
  that claimed 0x17.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..evidence import from_standard
from ..model import BIG, LITTLE, Signal
from .fetch import StandardDataError

#: Extensions understood.  A Digital Annex arrives as a spreadsheet, which this
#: does not read: its column layout is licensed material that varies by
#: revision, and guessing at it would produce silent misplacement rather than
#: an error.  Convert it to a DBC with the tool of your choice first.
SUPPORTED = (".dbc",)


@dataclass
class Overlay:
    """Layouts from a user-supplied database, indexed for lookup."""

    source: str = ""
    by_frame_id: Dict[Tuple[int, bool], "OverlayEntry"] = field(default_factory=dict)
    by_pgn: Dict[int, "OverlayEntry"] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.by_frame_id)

    def lookup(
        self, can_id: int, extended: bool, pgn: Optional[int]
    ) -> Optional[Tuple["OverlayEntry", str]]:
        """The best entry for this frame, and how it was matched."""
        exact = self.by_frame_id.get((can_id, extended))
        if exact is not None:
            return exact, "identifier"
        if pgn is not None:
            group = self.by_pgn.get(pgn)
            if group is not None:
                return group, "parameter group"
        return None


@dataclass
class OverlayEntry:
    name: str
    length: int
    comment: str = ""
    cycle_time: Optional[int] = None
    signals: List[Signal] = field(default_factory=list)


def _byte_order(signal) -> str:
    return BIG if getattr(signal, "byte_order", "") == "big_endian" else LITTLE


def load(path: str | Path) -> Overlay:
    """Read a user-supplied DBC into an overlay."""
    path = Path(path).expanduser()
    if not path.exists():
        raise StandardDataError(f"no such file: {path}")
    if path.suffix.lower() not in SUPPORTED:
        raise StandardDataError(
            f"{path.name}: only {', '.join(SUPPORTED)} is understood here. A "
            f"Digital Annex spreadsheet must be converted to a DBC first -- its "
            f"column layout is licensed and varies by revision, and reading it "
            f"by guesswork would misplace signals silently."
        )
    try:
        import cantools
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise StandardDataError("cantools is required to read a DBC overlay") from exc

    loaded = cantools.database.load_file(str(path))
    overlay = Overlay(source=path.name)

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
                    evidence=from_standard(
                        f"overlay:{path.name}",
                        f"{s.name} placed from '{path.name}', a database supplied "
                        f"by the operator rather than fetched",
                    ),
                )
            )
        entry = OverlayEntry(
            name=m.name,
            length=m.length,
            comment=(m.comment or "")[:220],
            cycle_time=m.cycle_time,
            signals=signals,
        )
        overlay.by_frame_id[(m.frame_id, bool(m.is_extended_frame))] = entry
        if m.is_extended_frame:
            from ..j1939.ids import decompose

            overlay.by_pgn.setdefault(decompose(m.frame_id).pgn, entry)

    if not overlay.by_frame_id:
        raise StandardDataError(f"{path.name} defines no messages")
    return overlay
