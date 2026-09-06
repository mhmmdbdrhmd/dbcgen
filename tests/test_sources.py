"""The catalogue of standards a user can pick without finding a manual.

The point of these tests is less that each source parses and more that each
one keeps saying how much it should be trusted.  A database reconstructed by
volunteers and a database published by the body that defines the protocol are
different kinds of claim, and the file has to keep them apart.
"""

import cantools
import pytest

from dbcgen.emit import dbc as writer
from dbcgen.standard import obd2, registry
from dbcgen.standard.download import read_cached


def _cached_or_skip(asset):
    info = read_cached(asset)
    if info is None:
        pytest.skip(f"{asset.name} not cached; run 'dbcgen sources' online first")
    return info


# --------------------------------------------------------------- registry


def test_every_source_declares_a_licence_and_an_origin():
    for source in registry.SOURCES:
        assert source.licence, f"{source.name} has no licence"
        assert source.attribution, f"{source.name} has no attribution"
        assert source.origin in ("standard", "community")


def test_an_unknown_source_names_the_ones_that_exist():
    with pytest.raises(KeyError) as excinfo:
        registry.get("no-such-protocol")
    assert "isobus" in str(excinfo.value) and "obd2" in str(excinfo.value)


def test_opendbc_refuses_to_guess_a_vehicle():
    """A family of per-car files must not silently pick one."""
    with pytest.raises(KeyError) as excinfo:
        registry.build("opendbc")
    assert "platform" in str(excinfo.value)


# ------------------------------------------------------------------ obd2


def test_obd2_needs_no_network_and_no_recording():
    db = registry.build("obd2")
    assert len(db.messages) == 1
    assert db.messages[0].frame_id == obd2.RESPONSE_ID_FIRST
    assert not db.messages[0].extended


@pytest.mark.parametrize(
    "frame,signal,expected",
    [
        # Real service 01 replies: length, 0x41, PID, then the data bytes.
        ("03410D3200000000", "VehicleSpeed", 50),           # 0x32 km/h
        ("04410C1AF8000000", "EngineSpeed", 0x1AF8 / 4),    # (A*256+B)/4
        ("0341055A00000000", "EngineCoolantTemperature", 0x5A - 40),
        ("034111FF00000000", "ThrottlePosition", 100.0),    # A*100/255
    ],
)
def test_obd2_formulas_match_the_published_definitions(frame, signal, expected, tmp_path):
    out = tmp_path / "obd2.dbc"
    writer.dump(registry.build("obd2"), out)
    db = cantools.database.load_file(out)
    decoded = db.decode_message(obd2.RESPONSE_ID_FIRST, bytes.fromhex(frame))
    assert float(decoded[signal]) == pytest.approx(expected)


def test_obd2_is_multiplexed_on_the_parameter_id(tmp_path):
    out = tmp_path / "obd2.dbc"
    writer.dump(registry.build("obd2"), out)
    db = cantools.database.load_file(out)
    assert db.get_message_by_frame_id(obd2.RESPONSE_ID_FIRST).is_multiplexed()


# -------------------------------------------------------------- nmea2000


def test_nmea2000_is_community_never_standard():
    """NMEA sells its specification, so canboat cannot be quoted as standard."""
    from dbcgen.standard import nmea2000

    _cached_or_skip(nmea2000.ASSET)
    db = registry.build("nmea2000")
    origins = {s.evidence.origin for m in db.messages for s in m.signals}
    assert origins == {"community"}
    assert any("community" in note and "NOT 'standard'" in note
               for note in db.header_notes)


def test_nmea2000_emits_only_single_frame_groups():
    from dbcgen.standard import nmea2000

    _cached_or_skip(nmea2000.ASSET)
    db = registry.build("nmea2000")
    assert db.messages
    assert all(m.length <= 8 for m in db.messages)


def test_nmea2000_omits_proprietary_groups():
    """One identifier cannot hold six manufacturers' meanings."""
    from dbcgen.standard import nmea2000

    _cached_or_skip(nmea2000.ASSET)
    db = registry.build("nmea2000")
    assert not any(nmea2000._is_proprietary(m.pgn) for m in db.messages if m.pgn)


def test_nmea2000_skips_canboat_range_placeholders():
    from dbcgen.standard import nmea2000

    assert nmea2000._usable({"Type": "Single", "Length": 8, "Fallback": True,
                             "Fields": [{"BitOffset": 0, "BitLength": 8}]}) is False


def test_nmea2000_reloads_and_every_message_is_unique(tmp_path):
    from dbcgen.standard import nmea2000

    _cached_or_skip(nmea2000.ASSET)
    db = registry.build("nmea2000")
    out = tmp_path / "nmea.dbc"
    writer.dump(db, out)
    loaded = cantools.database.load_file(out)
    ids = [m.frame_id for m in loaded.messages]
    assert len(ids) == len(set(ids))


# --------------------------------------------------------------- opendbc


def test_opendbc_is_community_and_says_it_describes_cars_not_a_protocol():
    from dbcgen.standard import opendbc

    _cached_or_skip(opendbc.platform_asset("acura_ilx_2016_nidec"))
    db = registry.build("opendbc:acura_ilx_2016_nidec")
    origins = {s.evidence.origin for m in db.messages for s in m.signals}
    assert origins == {"community"}
    assert any("PARTICULAR VEHICLES" in note for note in db.header_notes)
