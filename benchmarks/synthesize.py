"""Replay a recording into can-logger-esp32 CSV.

The benchmark has to exercise the same reader the real recordings use, so any
source log is first rewritten as the ESP32 recorder would have written it: the
'#' header block, the column row, then one raw row per frame.

No frame map is applied, which is the point -- every frame keeps its payload,
which is the condition bit-level inference needs and the condition a real
recording should be made under.

Timestamps are rebased to zero and converted to microseconds, matching the
recorder's `t_us` column ("microseconds since the START of THIS file").
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Iterator, Optional, Tuple

HEADER_TEMPLATE = """\
# =====================================================================
# CAN Logger ESP32 v1.0.0  -  file: {name}
# Bus    : {bitrate}
# Layout : one row per decoded signal, event based, ';' separated
#
# COLUMNS
#   t_us    Recorder clock, microseconds since the START of THIS file.
#   id      CAN identifier, 0x notation. 29-bit ids print 8 digits.
#   name    Message name from the frame map. Empty if unmapped.
#   signal  Signal name from the frame map. Empty on a raw row.
#   value   Physical value, or the symbolic name of that raw value.
#   unit    Unit from the frame map. Often empty.
#   raw     Payload bytes, hex.
#
# NOTE: synthesized by dbcgen benchmarks from {source}
#       No frame map was applied, so every frame keeps its payload.
# ====================================================================="""

COLUMNS = "t_us;id;name;signal;value;unit;raw"

#: (timestamp_seconds, can_id, extended, payload)
FrameTuple = Tuple[float, int, bool, bytes]


def format_id(can_id: int, extended: bool) -> str:
    """29-bit ids print eight digits; that is how the reader tells them apart."""
    return f"0x{can_id:08X}" if extended else f"0x{can_id:03X}"


def rows(frames: Iterable[FrameTuple]) -> Iterator[str]:
    """Raw CSV rows, with timestamps rebased so the first frame is t=0."""
    base: Optional[float] = None
    for timestamp, can_id, extended, data in frames:
        if base is None:
            base = timestamp
        t_us = int(round((timestamp - base) * 1_000_000))
        yield f"{t_us};{format_id(can_id, extended)};;;;;{data.hex().upper()}"


def write(
    frames: Iterable[FrameTuple],
    path: str | Path,
    source: str = "",
    bitrate: str = "classical CAN, 250 kbit/s, normal mode",
) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as fh:
        fh.write(HEADER_TEMPLATE.format(name=path.name, bitrate=bitrate, source=source or "?"))
        fh.write("\n")
        fh.write(COLUMNS + "\n")
        for line in rows(frames):
            fh.write(line + "\n")
            count += 1
    return count


def read_any(path: str | Path) -> Iterator[FrameTuple]:
    """Read any format python-can understands (MF4, BLF, ASC, TRC, candump).

    Used only by the benchmark: the tool's own input path is the recorder CSV.
    """
    import can

    with can.LogReader(str(path)) as reader:
        for msg in reader:
            if getattr(msg, "is_error_frame", False) or getattr(msg, "is_remote_frame", False):
                continue
            yield (
                float(msg.timestamp),
                int(msg.arbitration_id),
                bool(msg.is_extended_id),
                bytes(msg.data),
            )


def from_log(source: str | Path, dest: str | Path, bitrate: str = "") -> int:
    """Convert one log file into a can-logger-esp32 CSV recording."""
    return write(
        read_any(source),
        dest,
        source=str(source),
        bitrate=bitrate or "classical CAN, 250 kbit/s, normal mode",
    )
