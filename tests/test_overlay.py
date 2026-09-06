"""Using a database the operator already owns.

Most of J1939 is sold rather than published. Someone who bought the Digital
Annex holds the answers this tool otherwise guesses at, and these tests pin the
three things that makes true: it outranks inference, it never leaves the path
it was given, and it never drops one of their signals in silence.
"""

import cantools
import pytest

from dbcgen.emit import dbc as writer
from dbcgen.frames import Frame, FrameSet
from dbcgen.generate import generate
from dbcgen.standard import fetch as standard_fetch
from dbcgen.standard import isobus
from dbcgen.standard import overlay as overlay_mod
from dbcgen.standard import tables as identity_tables

MINE = '''VERSION "a database I own"
NS_ :
BS_:
BU_: Tester
BO_ 2364540158 MyEngineGroup: 8 Tester
 SG_ MyEngineSpeed : 24|16@1+ (0.125,0) [0|8031] "rpm"  Vector__XXX
BO_ 291 MyBodyFrame: 8 Tester
 SG_ MyFlag : 0|1@1+ (1,0) [0|1] ""  Vector__XXX
 SG_ MyWideField : 8|56@1+ (1,0) [0|1] ""  Vector__XXX
'''


@pytest.fixture
def mine(tmp_path):
    p = tmp_path / "mine.dbc"
    p.write_text(MINE)
    return p


@pytest.fixture(scope="module")
def standard():
    try:
        info = standard_fetch.ensure()
    except Exception as exc:
        pytest.skip(f"standard export unavailable: {exc}")
    return isobus.load(info), identity_tables.load(info)


def frames(*specs):
    fs = FrameSet()
    for t, can_id, payload, ext in specs:
        fs.add(Frame(t, can_id, bytes.fromhex(payload), ext))
    return fs


def test_a_non_dbc_file_is_refused_with_a_reason(tmp_path):
    """A Digital Annex spreadsheet must fail loudly, not be half-read."""
    xl = tmp_path / "j1939da.xlsx"
    xl.write_bytes(b"PK\x03\x04not really")
    with pytest.raises(standard_fetch.StandardDataError) as excinfo:
        overlay_mod.load(xl)
    assert "converted to a DBC" in str(excinfo.value)


def test_a_missing_file_is_refused(tmp_path):
    with pytest.raises(standard_fetch.StandardDataError):
        overlay_mod.load(tmp_path / "absent.dbc")


def test_the_overlay_outranks_inference(standard, mine):
    """0x8CF004FE is a proprietary id inference would otherwise guess at."""
    std, tables = standard
    fs = frames(*[(i * 100_000, 0x0CF004FE, "0000803E00000000", True) for i in range(40)])
    overlay = overlay_mod.load(mine)

    _, without, _ = generate(fs, std, tables)
    db, with_, _ = generate(fs, std, tables, overlay=overlay)

    assert with_.from_overlay == 1
    assert without.from_overlay == 0
    names = [s.name for m in db.messages for s in m.signals]
    assert names == ["MyEngineSpeed"]
    assert db.messages[0].signals[0].evidence.origin == "standard"


def test_the_overlay_applies_to_11_bit_frames(standard, mine):
    """A passenger-car database is 11-bit, and must not be ignored."""
    std, tables = standard
    fs = frames(*[(i * 1000, 0x123, "0102030405060708", False) for i in range(40)])
    db, report, _ = generate(fs, std, tables, overlay=overlay_mod.load(mine))
    assert report.from_overlay == 1
    assert db.messages[0].name == "MyBodyFrame"


def test_a_signal_that_does_not_fit_is_reported_not_dropped_quietly(standard, mine):
    std, tables = standard
    # Four-byte frames cannot hold MyWideField, which ends at bit 64.
    fs = frames(*[(i * 1000, 0x123, "01020304", False) for i in range(40)])
    db, report, _ = generate(fs, std, tables, overlay=overlay_mod.load(mine))
    assert report.overlay_signals_dropped == 1
    assert any("MyWideField" in line for line in report.dropped_spns)
    assert [s.name for s in db.messages[0].signals] == ["MyFlag"]


def test_the_overlay_file_is_never_copied(standard, mine, tmp_path):
    """Licensed material stays where its licence-holder put it."""
    std, tables = standard
    before = set(p.name for p in tmp_path.iterdir())
    fs = frames(*[(i * 1000, 0x123, "0102030405060708", False) for i in range(40)])
    db, _, _ = generate(fs, std, tables, overlay=overlay_mod.load(mine))
    writer.dump(db, tmp_path / "out.dbc")
    after = set(p.name for p in tmp_path.iterdir())
    assert after - before == {"out.dbc"}


def test_the_generated_file_still_decodes_every_frame(standard, mine, tmp_path):
    std, tables = standard
    fs = frames(*[(i * 1000, 0x123, "0102030405060708", False) for i in range(40)])
    db, _, _ = generate(fs, std, tables, overlay=overlay_mod.load(mine))
    out = tmp_path / "out.dbc"
    writer.dump(db, out)
    loaded = cantools.database.load_file(out)
    m = loaded.get_message_by_frame_id(0x123)
    assert m.decode(bytes.fromhex("0102030405060708"))
