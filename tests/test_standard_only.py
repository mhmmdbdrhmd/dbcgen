"""Generating without a recording.

The standard is the primary input; a recording is optional.  These tests keep
that true, because it is the claim the README leads with.
"""

import cantools
import pytest

from dbcgen.emit import dbc as writer
from dbcgen.generate import TEMPLATE_SOURCE_ADDRESS, generate_from_standard
from dbcgen.j1939.ids import decompose
from dbcgen.standard import fetch as standard_fetch
from dbcgen.standard import isobus

PGN_GUIDANCE_MACHINE_INFO = 44032
PGN_ADDRESS_CLAIMED = 60928


@pytest.fixture(scope="module")
def standard():
    try:
        info = standard_fetch.ensure()
    except Exception as exc:  # network or cache unavailable
        pytest.skip(f"standard export unavailable: {exc}")
    return isobus.load(info)


def test_a_usable_database_comes_out_of_the_standard_alone(standard, tmp_path):
    db, report = generate_from_standard(standard)
    assert report.messages > 50
    assert report.signals_standard > 300
    assert report.from_network_layer == 5

    out = tmp_path / "standard.dbc"
    writer.dump(db, out)
    loaded = cantools.database.load_file(out)
    assert len(loaded.messages) == report.messages


def test_nothing_is_inferred_when_no_recording_was_read(standard):
    db, _ = generate_from_standard(standard)
    origins = {s.evidence.origin for m in db.messages for s in m.signals}
    assert origins == {"standard"}


def test_restricting_to_one_pgn_yields_that_pgn(standard):
    db, report = generate_from_standard(standard, pgns=[PGN_GUIDANCE_MACHINE_INFO])
    assert report.messages == 1
    assert decompose(db.messages[0].frame_id).pgn == PGN_GUIDANCE_MACHINE_INFO


def test_the_placeholder_source_address_is_stated_not_hidden(standard):
    db, _ = generate_from_standard(standard, pgns=[PGN_ADDRESS_CLAIMED])
    assert decompose(db.messages[0].frame_id).sa == TEMPLATE_SOURCE_ADDRESS
    assert any("placeholder" in note for note in db.header_notes)
    assert any("no recording" in note.lower() for note in db.header_notes)


def test_curvature_keeps_its_published_scale_without_a_recording(standard):
    """The acceptance case from the plan, with the recording taken away."""
    db, _ = generate_from_standard(standard, pgns=[PGN_GUIDANCE_MACHINE_INFO])
    curvature = next(
        s for s in db.messages[0].signals if "Curvature" in s.name
    )
    assert (curvature.start_bit, curvature.length) == (0, 16)
    assert curvature.factor == pytest.approx(0.25)
    assert curvature.offset == pytest.approx(-8032)
    assert curvature.unit == "km-1"
