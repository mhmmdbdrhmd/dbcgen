"""Reading a J1939 Digital Annex you have licensed.

This is the file that closes the biggest hole in the tool.  The free registry
names about 2,764 parameter groups and publishes the bit layout of none of
them, so on a truck bus `dbcgen` returns real message names wrapped around
`Guess_24_16`.  Anyone who has bought the Digital Annex already owns those
layouts -- as a spreadsheet, which is why "convert it to a DBC first" was never
a real answer.  Converting it *is* the work.

Two things make reading it defensible rather than reckless:

**Columns are found by name, not by position.**  The annex is revised yearly
and its column order is not a stable interface.  Every column this needs is
located by matching its header against a list of spellings, after normalising
case and punctuation away, so a moved or renamed column is survivable and an
absent one is reported rather than silently read as something else.

**It fails loudly.**  If the header row cannot be located, or a column this
needs is missing, the error names the headers that *were* found.  A parser that
guesses at a licensed spreadsheet does not produce an error, it produces signals
in the wrong place, and those are far more expensive than a refusal.

Nothing read here is cached, copied or written anywhere.  The file stays where
its licence-holder put it.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .fetch import StandardDataError
from .isobus import (
    PgnDef,
    SpnDef,
    StandardDatabase,
    parse_cycle_ms,
    parse_length_bits,
    parse_start_bit,
)

SPREADSHEET_SUFFIXES = (".xlsx", ".xlsm")
TEXT_SUFFIXES = (".csv", ".tsv", ".txt")

#: The first numeric token in a cell.  The annex writes resolutions as prose --
#: "0.125 rpm/bit, 0 offset" -- so a plain float() is not enough.
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")

#: How many rows to look through for the header before giving up.  The annex
#: puts a title block above its table.
HEADER_SEARCH_ROWS = 40


def _norm(text) -> str:
    """Fold a header to letters and digits, so spacing and punctuation stop mattering."""
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


#: field -> the header spellings that have been seen for it, most specific
#: first.  Matching is on the normalised form, so "SPN Position in PGN",
#: "spn_position_in_pgn" and "SPN position in PGN" are one entry.
COLUMNS: Dict[str, Tuple[str, ...]] = {
    "pgn": ("pgn", "parametergroupnumber", "pgnumber"),
    "label": ("parametergrouplabel", "pglabel", "parametergroupname", "pgname"),
    "acronym": ("acronym", "pgacronym", "parametergroupacronym"),
    "pgn_description": ("pgndescription", "pgdescription", "parametergroupdescription"),
    "dlc": ("pgndatalength", "pgdatalength", "datalength", "pgnlength"),
    "priority": ("defaultpriority", "priority"),
    "rate": ("transmissionrate", "transmissionrepetitionrate", "repetitionrate"),
    "multipacket": ("multipacket", "multiframe", "transportprotocol"),
    "spn": ("spn", "suspectparameternumber", "spnumber"),
    "spn_name": ("spnname", "parametername", "suspectparametername", "spname"),
    "spn_description": ("spndescription", "parameterdescription", "spdescription"),
    "start": (
        "spstartbit",
        "spnstartbit",
        "startbit",
        "spnpositioninpgn",
        "positioninpgn",
        "spnposition",
        "startposition",
        "bitposition",
    ),
    "length": ("spnlength", "parameterlength", "splength", "lengthbits", "length"),
    "factor": ("scalefactor", "resolution", "scaling", "factor"),
    "offset": ("offsetvalue", "offset"),
    "units": ("units", "unit", "unitofmeasure", "unitmeasure"),
    "range": ("datarange", "range", "operationalrange"),
    "slot_id": ("slotidentifier", "slotid"),
    "slot_name": ("slotname",),
}

#: Without these nothing can be placed, so their absence is an error rather
#: than a degraded result.
REQUIRED = ("pgn", "spn", "start", "length")


def _first_number(text) -> Optional[float]:
    if text is None:
        return None
    m = _NUMBER.search(str(text))
    return float(m.group(0)) if m else None


def _map_columns(header: Sequence) -> Dict[str, int]:
    """Which column index holds each field."""
    normalised = [_norm(h) for h in header]
    found: Dict[str, int] = {}
    for field, spellings in COLUMNS.items():
        for spelling in spellings:
            if spelling in normalised:
                found[field] = normalised.index(spelling)
                break
    return found


def _looks_like_header(row: Sequence) -> bool:
    mapped = _map_columns(row)
    return all(k in mapped for k in ("pgn", "spn"))


def _find_header(rows: List[Sequence]) -> Tuple[int, Dict[str, int]]:
    for index, row in enumerate(rows[:HEADER_SEARCH_ROWS]):
        if _looks_like_header(row):
            return index, _map_columns(row)
    seen = []
    for row in rows[:6]:
        seen.append(", ".join(str(c) for c in row[:10] if c not in (None, "")))
    raise StandardDataError(
        "could not find the header row: no row in the first "
        f"{HEADER_SEARCH_ROWS} names both a PGN and an SPN column. The first "
        "rows contained:\n  " + "\n  ".join(s for s in seen if s)
    )


def _read_spreadsheet(path: Path, sheet: Optional[str]) -> List[Sequence]:
    try:
        import openpyxl
    except ImportError as exc:
        raise StandardDataError(
            f"{path.name} is a spreadsheet, which needs openpyxl:\n"
            f"    pip install 'dbcgen[annex]'\n"
            f"Alternatively export the sheet to CSV and pass that instead."
        ) from exc

    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    if sheet is not None:
        if sheet not in book.sheetnames:
            raise StandardDataError(
                f"{path.name} has no sheet {sheet!r}. It has: "
                + ", ".join(book.sheetnames)
            )
        names = [sheet]
    else:
        # Prefer a sheet whose name mentions both, then try them all: the annex
        # keeps its table on a sheet named for what is in it.
        names = sorted(
            book.sheetnames,
            key=lambda n: ("spn" not in _norm(n), "pgn" not in _norm(n), n),
        )

    problems = []
    for name in names:
        rows = [tuple(r) for r in book[name].iter_rows(values_only=True)]
        if not rows:
            continue
        try:
            _find_header(rows)
        except StandardDataError as exc:
            problems.append(f"  sheet {name!r}: {str(exc).splitlines()[0]}")
            continue
        return rows

    raise StandardDataError(
        f"no sheet in {path.name} contains a PGN/SPN table:\n" + "\n".join(problems)
    )


def _read_text(path: Path) -> List[Sequence]:
    """Read an exported table, sniffing the delimiter and nothing else.

    `csv.Sniffer` infers quoting rules as well as the delimiter, and it gets
    them wrong on real exports: on the ISOBUS table it decides doublequote is
    off, which silently splits 741 of 17,951 rows into ragged fragments and
    drops fields off the end of them.  The delimiter is the only part worth
    inferring; everything else is left at the standard dialect, which reads the
    same file with no ragged rows at all.
    """
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    first = text.split("\n", 1)[0]
    delimiter = max(",;\t", key=first.count) if first else ","
    if first.count(delimiter) == 0:
        delimiter = ","
    rows = [tuple(r) for r in csv.reader(io.StringIO(text), delimiter=delimiter)]

    # A table whose rows do not agree with each other has been mis-split, and
    # reading it anyway would place signals from whatever landed in the column.
    # The comparison is against the most common width rather than the first
    # row's: an annex puts a title block above its table, so row 0 is usually a
    # single cell and would condemn the entire file.
    if rows:
        widths: dict = {}
        for row in rows:
            widths[len(row)] = widths.get(len(row), 0) + 1
        width = max(widths, key=lambda w: (widths[w], w))
        ragged = sum(count for w, count in widths.items() if w != width and w > 1)
        if ragged > len(rows) // 20:
            raise StandardDataError(
                f"{path.name}: {ragged} of {len(rows)} rows do not have the "
                f"{width} fields most rows do, so the file cannot be split "
                f"reliably. Re-export it as comma-separated UTF-8."
            )
    return rows


def parse_rows(rows: List[Sequence], source: str = "") -> StandardDatabase:
    """Build a StandardDatabase from an annex table."""
    header_index, columns = _find_header(rows)
    missing = [k for k in REQUIRED if k not in columns]
    if missing:
        raise StandardDataError(
            f"the table has no column for: {', '.join(missing)}.\n"
            f"Headers found: "
            + ", ".join(str(c) for c in rows[header_index] if c not in (None, ""))
        )

    def cell(row: Sequence, field: str) -> str:
        index = columns.get(field)
        if index is None or index >= len(row):
            return ""
        value = row[index]
        return "" if value is None else str(value).strip()

    pgns: Dict[int, PgnDef] = {}
    seen: Dict[int, set] = {}

    for row in rows[header_index + 1:]:
        if not any(c not in (None, "") for c in row):
            continue
        raw_pgn = cell(row, "pgn").replace(",", "")
        number = _first_number(raw_pgn)
        if number is None or number != int(number):
            continue
        pgn = int(number)

        entry = pgns.get(pgn)
        if entry is None:
            dlc = _first_number(cell(row, "dlc"))
            priority = _first_number(cell(row, "priority"))
            entry = pgns[pgn] = PgnDef(
                pgn=pgn,
                label=cell(row, "label"),
                acronym=cell(row, "acronym"),
                description=cell(row, "pgn_description"),
                document="licensed J1939 Digital Annex",
                dlc=int(dlc) if dlc and 0 < dlc <= 1785 else None,
                priority=int(priority) if priority is not None and 0 <= priority <= 7 else None,
                cycle_ms=parse_cycle_ms(cell(row, "rate")),
                multipacket=cell(row, "multipacket").strip().lower()
                in ("yes", "true", "1"),
            )
            seen[pgn] = set()

        spn_number = _first_number(cell(row, "spn"))
        if spn_number is None or spn_number != int(spn_number):
            continue
        spn = int(spn_number)
        if spn in seen[pgn]:
            continue

        minimum = maximum = None
        rng = cell(row, "range")
        if " to " in rng:
            lo, _, hi = rng.partition(" to ")
            minimum, maximum = _first_number(lo), _first_number(hi)

        seen[pgn].add(spn)
        entry.spns.append(
            SpnDef(
                spn=spn,
                name=cell(row, "spn_name"),
                description=cell(row, "spn_description"),
                document="licensed J1939 Digital Annex",
                start_bit=parse_start_bit(cell(row, "start")),
                length_bits=parse_length_bits(cell(row, "length")),
                factor=_first_number(cell(row, "factor")),
                offset=_first_number(cell(row, "offset")),
                unit=cell(row, "units"),
                minimum=minimum,
                maximum=maximum,
                slot_id=cell(row, "slot_id"),
                slot_name=cell(row, "slot_name"),
                position=cell(row, "start"),
            )
        )

    for entry in pgns.values():
        entry.spns.sort(
            key=lambda s: (s.start_bit if s.start_bit is not None else 1 << 30, s.spn)
        )
    if not pgns:
        raise StandardDataError("the table parsed, but contained no usable PGN rows")
    return StandardDatabase(pgns, source=source)


def load(path: str | Path, sheet: Optional[str] = None) -> StandardDatabase:
    """Read a licensed annex from a spreadsheet or an exported table."""
    path = Path(path).expanduser()
    if not path.exists():
        raise StandardDataError(f"no such file: {path}")
    suffix = path.suffix.lower()
    if suffix in SPREADSHEET_SUFFIXES:
        rows = _read_spreadsheet(path, sheet)
    elif suffix in TEXT_SUFFIXES:
        rows = _read_text(path)
    else:
        raise StandardDataError(
            f"{path.name}: expected one of "
            f"{', '.join(SPREADSHEET_SUFFIXES + TEXT_SUFFIXES)}"
        )
    return parse_rows(rows, source=f"{path.name} (licensed, read in place)")


def merge(base: StandardDatabase, extra: StandardDatabase) -> Tuple[StandardDatabase, dict]:
    """Fill in layouts the free export lacks, without overwriting what it has.

    The free registry is authoritative where it publishes a layout, and the two
    agree where they overlap, so the annex is used only to *add*: a parameter
    group the export names but does not lay out gains its layout, and one it
    never mentions is added outright.
    """
    merged: Dict[int, PgnDef] = dict(base.pgns)
    stats = {"laid_out": 0, "added": 0, "already_known": 0}

    for pgn, entry in extra.pgns.items():
        if not entry.has_layout:
            continue
        current = merged.get(pgn)
        if current is None:
            merged[pgn] = entry
            stats["added"] += 1
        elif current.has_layout:
            stats["already_known"] += 1
        else:
            replacement = PgnDef(
                pgn=pgn,
                label=current.label or entry.label,
                acronym=current.acronym or entry.acronym,
                description=current.description or entry.description,
                document=entry.document,
                dlc=current.dlc if current.dlc is not None else entry.dlc,
                priority=current.priority if current.priority is not None else entry.priority,
                cycle_ms=current.cycle_ms if current.cycle_ms is not None else entry.cycle_ms,
                multipacket=current.multipacket or entry.multipacket,
                spns=entry.spns,
            )
            merged[pgn] = replacement
            stats["laid_out"] += 1

    return StandardDatabase(merged, source=f"{base.source} + {extra.source}"), stats
