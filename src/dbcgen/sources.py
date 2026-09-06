"""Opening a recording, including one held inside a zip archive.

Recordings are routinely shipped zipped and are often far too large to unpack,
so `archive.zip::member.csv` is accepted anywhere a path is.

Which reader applies is decided by looking at the file, not at its extension:
trace tools are inconsistent about naming, and the HUD ECU Hacker export
arrives as `.htm.html`.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Iterable, List, Tuple

from .frames import FrameSet
from .readers.canlogger_csv import RecordingMeta, read_canlogger_lines
from .readers.hudecu_html import looks_like_hudecu, read_hudecu_lines

ARCHIVE_SEP = "::"

#: Lines read before deciding which reader applies.  Enough to clear the HTML
#: <head> of a trace export, and cheap for a multi-gigabyte CSV.
SNIFF_LINES = 80

MEMBER_SUFFIXES = (".csv", ".htm", ".html", ".log", ".txt")


def list_members(archive: str | Path) -> List[str]:
    with zipfile.ZipFile(archive) as z:
        return [n for n in z.namelist() if n.lower().endswith(MEMBER_SUFFIXES)]


def read_lines(lines: Iterable[str], source: str = "") -> Tuple[FrameSet, RecordingMeta]:
    """Pick a reader by content and run it."""
    iterator = iter(lines)
    head: List[str] = []
    for line in iterator:
        head.append(line)
        if len(head) >= SNIFF_LINES:
            break

    def rejoined():
        yield from head
        yield from iterator

    if looks_like_hudecu("".join(head)):
        return read_hudecu_lines(rejoined(), source=source)
    return read_canlogger_lines(rejoined(), source=source)


def open_recording(spec: str) -> Tuple[FrameSet, RecordingMeta]:
    """Read a recording from a path, or from `archive.zip::member`."""
    if ARCHIVE_SEP in spec:
        archive, member = spec.split(ARCHIVE_SEP, 1)
        with zipfile.ZipFile(archive) as z:
            with z.open(member) as fh:
                return read_lines(
                    io.TextIOWrapper(fh, encoding="utf-8", errors="replace"), source=spec
                )

    path = Path(spec)
    if path.suffix.lower() == ".zip":
        members = list_members(path)
        raise ValueError(
            f"{spec} is an archive; choose a member with "
            f"'{spec}{ARCHIVE_SEP}<name>'. Available: {', '.join(members[:12])}"
            + (" ..." if len(members) > 12 else "")
        )
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        return read_lines(fh, source=str(path))
