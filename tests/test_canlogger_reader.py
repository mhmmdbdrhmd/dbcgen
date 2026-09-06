"""can-logger-esp32 CSV reader.

The fixtures reproduce the recorder's real output: a '#' comment header, a
column-header row, then ';' separated rows in which only `raw` carries a payload.
"""

from dbcgen.readers import read_canlogger_lines

HEADER = [
    "# =====================================================================",
    "# CAN Logger ESP32 v1.0.0  -  file: 4.csv",
    "# Bus    : classical CAN, 250 kbit/s, normal mode",
    "# FRAME MAP: /machine.dbc",
    "t_us;id;name;signal;value;unit;raw",
]


def test_reads_raw_rows():
    lines = HEADER + [
        "1000;0x0CAC1B2A;;;;;0102030405060708",
        "2000;0x1A0;;;;;01020304",
    ]
    fs, meta = read_canlogger_lines(lines)
    assert fs.total == 2
    assert len(fs) == 2
    assert meta.rows_malformed == 0
    g = fs.get(0x0CAC1B2A, extended=True)
    assert g.frames[0].data == bytes.fromhex("0102030405060708")
    assert g.extended is True


def test_header_metadata_is_recovered():
    fs, meta = read_canlogger_lines(HEADER + ["1;0x123;;;;;00"])
    assert meta.bitrate == "classical CAN, 250 kbit/s, normal mode"
    assert meta.frame_map == "/machine.dbc"
    assert meta.logger_version.startswith("CAN Logger ESP32")


def test_extended_flag_from_digit_count():
    """An 8-digit id is 29-bit even when its value would fit in 11 bits.

    The recorder zero-pads 29-bit ids to eight digits, so digit count is the
    only way to tell 0x00000123 (extended) from 0x123 (standard).  They are
    different frames on the wire and must not merge into one message.
    """
    fs, _ = read_canlogger_lines(HEADER + ["1;0x00000123;;;;;00", "2;0x123;;;;;00"])
    assert len(fs) == 2
    assert fs.get(0x123, extended=True).frames[0].t_us == 1
    assert fs.get(0x123, extended=False).frames[0].t_us == 2


def test_decoded_only_rows_are_counted_not_silently_dropped():
    """A mapped recording loses payloads; the reader has to say so."""
    lines = HEADER + [
        "9309;0x18AC1B2A;Msg1;SigA;0;;",
        "9309;0x18AC1B2A;Msg1;SigB;NotAvailable;;",
        "9893;0x18FF322A;Msg2;;;;0011223344556677",
    ]
    fs, meta = read_canlogger_lines(lines)
    assert fs.total == 1
    assert meta.rows_decoded_only == 2
    assert meta.lossy is True
    assert 0x18AC1B2A in (meta.ids_decoded_only - meta.ids_payload_only)


def test_repeated_rows_for_one_frame_collapse():
    """Rows sharing (t_us, id) are one frame, however many signals they carry."""
    lines = HEADER + [
        "500;0x18FF0C55;Msg3;Byte0;0;;0102030405060708",
        "500;0x18FF0C55;Msg3;Byte1;15;;0102030405060708",
    ]
    fs, meta = read_canlogger_lines(lines)
    assert fs.total == 1
    assert meta.rows_raw == 1


def test_malformed_rows_are_counted():
    lines = HEADER + [
        "notanumber;0x123;;;;;00",
        "1;notanid;;;;;00",
        "2;0x123;;;;;zzz",
        "3;0x123;;;;;AA",
    ]
    fs, meta = read_canlogger_lines(lines)
    assert fs.total == 1
    assert meta.rows_malformed == 3


def test_empty_recording():
    fs, meta = read_canlogger_lines(HEADER)
    assert fs.total == 0 and len(fs) == 0 and meta.rows_total == 0
