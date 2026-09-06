"""The DBC writer.

Every test that produces a file also reloads it with cantools, because the
deliverable is a file other tools must read, not a string that looks right.
"""

import cantools
import pytest

from dbcgen.emit import dbc as writer
from dbcgen.evidence import from_standard, inferred
from dbcgen.model import BIG, Database, Message, Node, Signal


def load(text, tmp_path):
    path = tmp_path / "out.dbc"
    path.write_text(text)
    return cantools.database.load_file(str(path))


def simple_db(**msg_kw):
    msg = Message(frame_id=0x0CAC1B2A, name="GMS_2A", length=8, extended=True,
                  senders=["Steer_2A"], **msg_kw)
    msg.signals = [
        Signal(name="EstimatedCurvature", start_bit=0, length=16, factor=0.25,
               offset=-8032, minimum=-8032, maximum=8031.75, unit="km-1", spn=5238,
               evidence=from_standard("ISO 11783-7:SPN5238", "curvature")),
    ]
    return Database(version="t", nodes=[Node("Steer_2A")], messages=[msg])


def test_extended_id_sets_the_dbc_flag():
    """DBC marks 29-bit ids by setting bit 31: 0x0CAC1B2A -> 2360089386."""
    assert simple_db().messages[0].dbc_id == 2360089386


def test_round_trip_through_cantools(tmp_path):
    db = load(writer.dumps(simple_db(cycle_time=100)), tmp_path)
    m = db.get_message_by_frame_id(0x0CAC1B2A)
    assert m.name == "GMS_2A" and m.length == 8 and m.cycle_time == 100
    assert m.is_extended_frame
    s = m.signals[0]
    assert (s.start, s.length, s.scale, s.offset) == (0, 16, 0.25, -8032)
    assert s.unit == "km-1"


def test_signal_line_matches_the_published_iso_encoding():
    """SPN 5238 must render exactly as ISO 11783-7 defines it."""
    line = [l for l in writer.dumps(simple_db()).splitlines() if l.startswith(" SG_")][0]
    assert line == (
        ' SG_ EstimatedCurvature : 0|16@1+ (0.25,-8032) [-8032|8031.75] "km-1"'
        "  Vector__XXX"
    )


def test_decode_is_correct_after_round_trip(tmp_path):
    db = load(writer.dumps(simple_db()), tmp_path)
    # Straight ahead is raw 8032/0.25 = 32128 = 0x7D80, little-endian "807D".
    assert db.decode_message(0x0CAC1B2A, bytes.fromhex("807D000000000000"))[
        "EstimatedCurvature"
    ] == 0.0
    # +500 km-1 is raw (500 + 8032) / 0.25 = 34128.
    assert db.decode_message(0x0CAC1B2A, (34128).to_bytes(2, "little") + b"\x00" * 6)[
        "EstimatedCurvature"
    ] == 500.0


def test_value_tables_are_written(tmp_path):
    msg = Message(frame_id=0x100, name="M", length=1, extended=False)
    msg.signals = [Signal(name="State", start_bit=0, length=2,
                          values={3: "NotAvailable", 2: "Error"})]
    db = load(writer.dumps(Database(messages=[msg])), tmp_path)
    assert db.decode_message(0x100, b"\x03")["State"] == "NotAvailable"


def test_multiplexed_signals_round_trip(tmp_path):
    msg = Message(frame_id=0x1CECFF2A, name="TPCM_54", length=8)
    msg.signals = [
        Signal(name="ControlByte", start_bit=0, length=8, multiplexer="M"),
        Signal(name="TotalMessageSize_RTS", start_bit=8, length=16, multiplexer="m16"),
        Signal(name="AbortReason", start_bit=8, length=8, multiplexer="m255"),
    ]
    db = load(writer.dumps(Database(messages=[msg])), tmp_path)
    decoded = db.decode_message(0x1CECFF2A, bytes.fromhex("106A00000000FFFF"))
    assert decoded["ControlByte"] == 16
    assert decoded["TotalMessageSize_RTS"] == 0x006A
    assert "AbortReason" not in decoded


def test_big_endian_signal_round_trips(tmp_path):
    msg = Message(frame_id=0x200, name="BE", length=2, extended=False)
    msg.signals = [Signal(name="Value", start_bit=7, length=16, byte_order=BIG)]
    db = load(writer.dumps(Database(messages=[msg])), tmp_path)
    assert db.decode_message(0x200, b"\x12\x34")["Value"] == 0x1234


def test_standard_and_11bit_messages_coexist(tmp_path):
    """A standard 0x123 and an extended 0x123 must stay separate messages."""
    a = Message(frame_id=0x123, name="Std", length=1, extended=False)
    b = Message(frame_id=0x123, name="Ext", length=1, extended=True)
    for m in (a, b):
        m.signals = [Signal(name="B0", start_bit=0, length=8)]
    db = load(writer.dumps(Database(messages=[a, b])), tmp_path)
    assert {m.name for m in db.messages} == {"Std", "Ext"}


def test_evidence_reaches_the_file(tmp_path):
    msg = Message(frame_id=0x100, name="M", length=1, extended=False,
                  evidence=inferred("constant-bytes", "identical across 41203 frames"))
    msg.signals = [Signal(name="B0", start_bit=0, length=8)]
    text = writer.dumps(Database(messages=[msg]))
    assert '[inferred:constant-bytes] identical across 41203 frames' in text
    load(text, tmp_path)  # and it still parses


def test_quotes_in_a_comment_do_not_break_the_file(tmp_path):
    msg = Message(frame_id=0x100, name="M", length=1, extended=False,
                  comment='has "quotes" and a \\ backslash')
    msg.signals = [Signal(name="B0", start_bit=0, length=8)]
    db = load(writer.dumps(Database(messages=[msg])), tmp_path)
    assert db.messages[0].comment.startswith("has")
