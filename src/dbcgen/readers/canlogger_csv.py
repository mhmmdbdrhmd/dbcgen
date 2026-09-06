"""Reader for can-logger-esp32 CSV recordings.

Layout, as documented in the recorder's own header block:

    t_us;id;name;signal;value;unit;raw

One row per *decoded signal*, so a mapped frame produces several rows that share
`t_us` and `id`.  The `raw` column holds the full payload and is written
whenever a frame could not be decoded -- which, for a recording made without a
frame map, is every frame:

    28870;0x1A0;;;;;01020304

Only rows carrying `raw` can be used here: a decoded row has already thrown the
payload away, and bit-level inference needs the bytes.  That loss is counted and
reported rather than passed over, because a recording made *with* a frame map
will silently hand back far fewer frames than it contains.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

from ..frames import Frame, FrameSet

_HEX_ID = re.compile(r"^0[xX]([0-9A-Fa-f]+)$")


@dataclass
class RecordingMeta:
    """What the reader could establish about the recording itself."""

    source: str = ""
    logger_version: str = ""
    bitrate: str = ""
    frame_map: str = ""
    header_lines: list = field(default_factory=list)

    rows_total: int = 0
    rows_raw: int = 0
    """Rows carrying a payload -- these became frames."""
    rows_decoded_only: int = 0
    """Rows that held only a decoded signal value; their payload is unrecoverable."""
    rows_malformed: int = 0

    ids_payload_only: set = field(default_factory=set)
    ids_decoded_only: set = field(default_factory=set)

    @property
    def lossy(self) -> bool:
        """True when the recording was made with a frame map, so payloads are missing."""
        return bool(self.ids_decoded_only - self.ids_payload_only)

    def summary(self) -> str:
        parts = [f"{self.rows_raw} frames from {self.rows_total} rows"]
        if self.rows_decoded_only:
            parts.append(f"{self.rows_decoded_only} decoded-only rows skipped")
        if self.rows_malformed:
            parts.append(f"{self.rows_malformed} malformed")
        return ", ".join(parts)


def _parse_header(line: str, meta: RecordingMeta) -> None:
    body = line.lstrip("#").strip()
    if not body:
        return
    meta.header_lines.append(body)
    if body.startswith("CAN Logger ESP32"):
        meta.logger_version = body.split("-")[0].strip()
    elif body.startswith("Bus"):
        meta.bitrate = body.split(":", 1)[1].strip()
    elif body.startswith("FRAME MAP:"):
        meta.frame_map = body.split(":", 1)[1].strip()


def _parse_id(token: str) -> Optional[Tuple[int, bool]]:
    """Return (can_id, extended).

    The recorder zero-pads 29-bit ids to eight hex digits, so digit count is the
    reliable signal -- a small 29-bit id would otherwise be read as 11-bit.
    """
    m = _HEX_ID.match(token.strip())
    if not m:
        return None
    digits = m.group(1)
    try:
        value = int(digits, 16)
    except ValueError:
        return None
    extended = len(digits) == 8 or value > 0x7FF
    return value, extended


def read_canlogger_lines(lines: Iterable[str], source: str = "") -> Tuple[FrameSet, RecordingMeta]:
    """Parse an iterable of text lines.  Accepts anything line-iterable, so a
    member of a zip archive can be read without unpacking it to disk."""
    meta = RecordingMeta(source=source)
    fs = FrameSet()
    seen: Dict[Tuple[int, int], None] = {}

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            _parse_header(line, meta)
            continue

        if line.startswith("t_us;"):
            # The column-header row, written once after the comment block.
            continue

        meta.rows_total += 1
        cols = line.split(";")
        if len(cols) < 2:
            meta.rows_malformed += 1
            continue

        try:
            t_us = int(cols[0])
        except ValueError:
            meta.rows_malformed += 1
            continue

        parsed = _parse_id(cols[1])
        if parsed is None:
            meta.rows_malformed += 1
            continue
        can_id, extended = parsed

        raw = cols[6].strip() if len(cols) > 6 else ""
        if not raw:
            meta.rows_decoded_only += 1
            meta.ids_decoded_only.add(can_id)
            continue

        try:
            data = bytes.fromhex(raw)
        except ValueError:
            meta.rows_malformed += 1
            continue

        # A frame map that names the message but decodes no signal can emit the
        # same payload once per row; collapse on (t_us, id) so it counts once.
        key = (t_us, can_id)
        if key in seen:
            continue
        seen[key] = None

        meta.rows_raw += 1
        meta.ids_payload_only.add(can_id)
        fs.add(Frame(t_us=t_us, can_id=can_id, data=data, extended=extended))

    return fs, meta


def read_canlogger_csv(path: str | Path) -> Tuple[FrameSet, RecordingMeta]:
    path = Path(path)
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        return read_canlogger_lines(fh, source=str(path))
