"""End-to-end generation.

The `live` tests use the real cached ISOBUS export and are skipped when it is
absent, so a clean checkout still runs the rest of the suite offline.
Run `dbcgen fetch-standard` to populate the cache.
"""

import cantools
import pytest

from dbcgen.emit import dbc as writer
from dbcgen.frames import Frame, FrameSet
from dbcgen.generate import generate
from dbcgen.standard import fetch as standard_fetch
from dbcgen.standard import isobus
from dbcgen.standard import tables as identity_tables

live = pytest.mark.skipif(
    standard_fetch.read_cache_info() is None,
    reason="standard export not cached; run 'dbcgen fetch-standard'",
)


@pytest.fixture(scope="module")
def standard():
    info = standard_fetch.ensure()
    return isobus.load(info), identity_tables.load(info)


def frames(*specs):
    fs = FrameSet()
    for t, can_id, payload, ext in specs:
        fs.add(Frame(t, can_id, bytes.fromhex(payload), ext))
    return fs


@live
def test_agricultural_guidance_reproduces_the_published_layout(standard, tmp_path):
    """The Phase 1 acceptance criterion.

    PGN 44032 is fully specified in the free ISO 11783-7 tables, so the
    generator must place all eight signals exactly as the standard defines
    them -- these lines are identical to a DBC written by hand from the
    standard document.
    """
    std, tables = standard
    fs = frames(*[(i * 100_000, 0x0CAC1B2A, "807D000000000000", True) for i in range(20)])
    db, report, _ = generate(fs, std, tables)

    lines = [l for l in writer.dumps(db).splitlines() if l.startswith(" SG_")]
    assert lines == [
        ' SG_ EstimatedCurvature : 0|16@1+ (0.25,-8032) [-8032|8031.75] "km-1"  Vector__XXX',
        ' SG_ MechanicalSystemLockout : 16|2@1+ (1,0) [0|3] ""  Vector__XXX',
        ' SG_ GuidanceSteeringSystemReadiness : 18|2@1+ (1,0) [0|3] ""  Vector__XXX',
        ' SG_ SteeringInputPositionStatus : 20|2@1+ (1,0) [0|3] ""  Vector__XXX',
        ' SG_ RequestResetCommandStatus : 22|2@1+ (1,0) [0|3] ""  Vector__XXX',
        ' SG_ GuidanceLimitStatus : 29|3@1+ (1,0) [0|7] ""  Vector__XXX',
        ' SG_ GuidanceSystemCommandExitReasonCode : 32|6@1+ (1,0) [0|63] ""  Vector__XXX',
        ' SG_ GuidanceSystemRemoteEngageSwitchStatus : 38|2@1+ (1,0) [0|3] ""  Vector__XXX',
    ]
    assert report.from_standard_layout == 1


@live
def test_generated_file_decodes_every_recorded_frame(standard, tmp_path):
    """The bar the generated DBC has to clear: no frame left undecoded."""
    std, tables = standard
    fs = frames(
        (0, 0x0CAC1B2A, "807D000000000000", True),
        (100_000, 0x0CAC1B2A, "003E000000000000", True),
        (200_000, 0x18FF0C55, "0102030405060708", True),   # proprietary
        (300_000, 0x18EEFF2A, "8A03C000202B0120", True),   # address claimed
        (400_000, 0x1A0, "01020304", False),               # 11-bit
    )
    db, _, _ = generate(fs, std, tables)
    path = tmp_path / "out.dbc"
    writer.dump(db, path)

    loaded = cantools.database.load_file(str(path))
    by_id = {(m.frame_id, bool(m.is_extended_frame)): m for m in loaded.messages}
    for group in fs:
        m = by_id[(group.can_id, group.extended)]
        for f in group.frames:
            m.decode(f.data)


@live
def test_node_identity_comes_from_address_claimed(standard):
    """A claimed address must be named from the ECU's own NAME, not guessed."""
    std, tables = standard
    # NAME: manufacturer 123 (Basler Electric), function 16 (Steering Controller).
    name = (6 | (123 << 21) | (1 << 32) | (16 << 40)).to_bytes(8, "little")
    fs = FrameSet([Frame(0, 0x18EEFF2A, name, True)])
    _, _, nodes = generate(fs, std, tables)
    node = nodes[0x2A]
    assert node.name == "SteeringController_2A"
    assert node.evidence.origin == "standard"
    assert "Basler" in node.comment


@live
def test_unclaimed_address_is_named_but_not_claimed_to_be_known(standard):
    std, tables = standard
    fs = FrameSet([Frame(0, 0x18FF0C55, b"\x01" * 8, True)])
    _, _, nodes = generate(fs, std, tables)
    assert nodes[0x55].name == "ECU_55"
    assert nodes[0x55].evidence.origin == "inferred"


@live
def test_proprietary_message_gets_raw_bytes_not_invented_signals(standard):
    """Too few frames to infer anything, so the bytes are passed through --
    and the names say so."""
    std, tables = standard
    fs = frames(*[(i * 1000, 0x18FF0C55, "0102030405060708", True) for i in range(5)])
    db, report, _ = generate(fs, std, tables)
    msg = db.messages[0]
    assert [s.name for s in msg.sorted_signals()] == [f"Raw_Byte{i}" for i in range(8)]
    assert all(s.evidence.origin == "inferred" for s in msg.signals)


@live
def test_inferred_signal_names_warn_that_they_are_guesses(standard):
    """A DBC outlives the conversation that produced it.  Anyone opening this
    file must be able to tell a documented signal from an inferred one without
    reading comments or asking anybody."""
    import random

    std, tables = standard
    rng = random.Random(0)
    value = 20000
    specs = []
    for i in range(300):
        value += rng.randint(-40, 40)
        specs.append((i * 100_000, 0x18FF0C55, value.to_bytes(2, "little").hex() + "00" * 6, True))
    db, _, _ = generate(frames(*specs), std, tables)
    names = [s.name for s in db.messages[0].sorted_signals()]
    assert all(n.startswith(("Guess_", "Const_", "Raw_")) for n in names), names


@live
def test_standard_signals_are_not_prefixed(standard):
    """Names from the published standard carry no warning, because there is
    nothing to warn about."""
    std, tables = standard
    fs = frames(*[(i * 100_000, 0x0CAC1B2A, "807D000000000000", True) for i in range(20)])
    db, _, _ = generate(fs, std, tables)
    names = [s.name for s in db.messages[0].sorted_signals()]
    assert "EstimatedCurvature" in names
    assert not any(n.startswith(("Guess_", "Const_", "Raw_")) for n in names)


@live
def test_name_only_pgn_keeps_its_standard_name(standard):
    """EEC1's layout is paywalled, but the standard still names the group."""
    std, tables = standard
    fs = frames(*[(i * 1000, 0x0CF00400, "FFFF00007D000000", True) for i in range(5)])
    db, report, _ = generate(fs, std, tables)
    assert db.messages[0].name.startswith("EEC1_")
    assert report.named_only == 1
    # Named, but honest about not knowing the layout.
    assert db.messages[0].evidence.rule == "standard-name-only"
    assert "Digital Annex" in db.messages[0].evidence.detail


@live
def test_measured_cycle_time_is_used(standard):
    std, tables = standard
    fs = frames(*[(i * 210_000, 0x18FF0C55, "00", True) for i in range(30)])
    db, _, _ = generate(fs, std, tables)
    assert db.messages[0].cycle_time == 210


@live
def test_event_driven_traffic_gets_no_cycle_time(standard):
    """Address claims come in bursts; a cycle time would misreport the quiet."""
    std, tables = standard
    times = [0, 10_000, 20_000, 9_000_000, 9_010_000, 9_020_000]
    fs = frames(*[(t, 0x18EEFF2A, "00" * 8, True) for t in times])
    db, _, _ = generate(fs, std, tables)
    assert db.messages[0].cycle_time is None


@live
def test_same_pgn_from_two_destinations_are_two_named_messages(standard):
    std, tables = standard
    fs = frames(
        (0, 0x0CAC1B2A, "807D000000000000", True),
        (1000, 0x0CACAB2A, "807D000000000000", True),
    )
    db, _, _ = generate(fs, std, tables)
    names = sorted(m.name for m in db.messages)
    assert names == ["GMS_2A_to_1B", "GMS_2A_to_AB"]
