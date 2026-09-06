"""Reader for HUD ECU Hacker HTML trace logs.

The tool writes one `<div>` per bus event, timestamped to the millisecond:

    <div><span class='TimeBk'>10:15:42.100</span>
         <span class='RxData'>18FF322A: 01 23 45 67 89 AB CD EF</span></div>

Interleaved with those are adapter messages in the same shape but a different
CSS class, so frames are recognised by the `ID: BB BB ...` payload pattern
rather than by class name -- new classes appear between tool versions, but the
frame text has not changed.

Timestamps are wall-clock time of day, so they are rebased to the first frame
and converted to microseconds.  Millisecond resolution is coarser than a CAN
bus: at 250 kbit/s several frames share a millisecond, which is fine for cycle
times but means arrival order within a millisecond is the log's, not the wire's.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional, Tuple

from ..frames import Frame, FrameSet
from .canlogger_csv import RecordingMeta

#: `<span class='X'>HHH: BB BB ...</span>` -- the id, then space-separated bytes.
_FRAME = re.compile(
    r"<span class='(?P<cls>[A-Za-z]+)'>"
    r"(?P<id>[0-9A-Fa-f]{3,8})\s*:\s*(?P<data>(?:[0-9A-Fa-f]{2}\s*)+)"
    r"</span>"
)
_TIME = re.compile(r"<span class='Time(?:Bk|Wt)'>(\d+):(\d+):(\d+)\.(\d+)</span>")

#: Classes the tool uses for frames that were actually on the wire.  Anything
#: else matching the frame pattern (adapter chatter, error text) is ignored.
_FRAME_CLASSES = {"RxData", "TxData", "RxSecnd", "TxSecnd", "RxDataInvalid"}

#: A frame the adapter flagged as malformed; counted, never decoded.
_INVALID_CLASS = "RxDataInvalid"


def _timestamp_us(line: str) -> Optional[int]:
    m = _TIME.search(line)
    if not m:
        return None
    hours, minutes, seconds, fraction = m.groups()
    micros = int(fraction.ljust(6, "0")[:6])
    return ((int(hours) * 60 + int(minutes)) * 60 + int(seconds)) * 1_000_000 + micros


def read_hudecu_lines(lines: Iterable[str], source: str = "") -> Tuple[FrameSet, RecordingMeta]:
    meta = RecordingMeta(source=source)
    fs = FrameSet()
    base: Optional[int] = None
    previous_us = 0

    for line in lines:
        if "<span" not in line:
            continue
        match = _FRAME.search(line)
        if match is None:
            continue
        if match.group("cls") not in _FRAME_CLASSES:
            continue

        meta.rows_total += 1
        if match.group("cls") == _INVALID_CLASS:
            meta.rows_malformed += 1
            continue

        digits = match.group("id")
        try:
            can_id = int(digits, 16)
            data = bytes.fromhex(match.group("data").replace(" ", ""))
        except ValueError:
            meta.rows_malformed += 1
            continue

        stamp = _timestamp_us(line)
        if stamp is None:
            # Keep the frame; a missing timestamp costs cycle-time accuracy
            # but the payload is still evidence.
            t_us = previous_us
        else:
            if base is None:
                base = stamp
            t_us = stamp - base
            if t_us < 0:  # the log crossed midnight
                t_us += 24 * 3600 * 1_000_000
            previous_us = t_us

        # HUD ECU Hacker prints 29-bit ids as eight digits and 11-bit ids as
        # three, the same convention the recorder CSV uses.
        extended = len(digits) > 4 or can_id > 0x7FF
        meta.rows_raw += 1
        meta.ids_payload_only.add(can_id)
        fs.add(Frame(t_us=t_us, can_id=can_id, data=data, extended=extended))

    meta.header_lines.append("HUD ECU Hacker HTML trace")
    return fs, meta


def read_hudecu_html(path: str | Path) -> Tuple[FrameSet, RecordingMeta]:
    path = Path(path)
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        return read_hudecu_lines(fh, source=str(path))


def looks_like_hudecu(head: str) -> bool:
    return "HUD ECU Hacker" in head or ("TimeBk" in head and "RxData" in head)
