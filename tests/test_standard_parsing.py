"""Parsing the ISOBUS export.

The parser is exercised against an inline fixture so these tests need no
network.  Field values are copied verbatim from the real export.
"""

import pytest

from dbcgen.standard import isobus, slots

HEADER = (
    "pgn,parameter_group_label,acronym,pgn_description,pgn_notes,edp,dp,pf,ps,"
    "multipacket,transmission_rate,pgn_data_length,default_priority,pgn_reference,"
    "spn_position_in_pgn,sp_start_bit,spn,spn_name,spn_description,spn_notes,"
    "has_attachment,sp_historical_remarks,sp_historical_attachment,from_2015_2018,"
    "spn_length,resolution,offset,data_range,operational_range,units,slot_identifier,"
    "slot_name,spn_type,spn_reference,scale_factor,offset_value,range_maximum,"
    "length_min,length_max,spn_document,pgn_document,spn_created_or_modified_date,"
    "pgn_created_or_modified_date,pgn_spn_mapping_created_or_modified_date"
)


def row(**kw):
    fields = {name: "" for name in HEADER.split(",")}
    fields.update(kw)
    return ",".join(f'"{fields[name]}"' for name in HEADER.split(","))


def csv_text(*rows):
    return "\n".join([HEADER, *rows])


# --- start bit -------------------------------------------------------------
@pytest.mark.parametrize(
    "text,expected",
    [
        ("1.1", 0),    # byte 1 bit 1 -> DBC bit 0
        ("3.1", 16),
        ("3.3", 18),
        ("3.7", 22),
        ("4.6", 29),
        ("5.1", 32),
        ("5.7", 38),
        ("2,1", 8),    # the export uses a comma in some rows
        ("4", 24),     # bare byte number means its first bit
        ("", None),
        ("n/a", None),
        ("1.9", None),  # bit index is 1..8
        ("0.1", None),  # byte index is 1-based
    ],
)
def test_parse_start_bit(text, expected):
    assert isobus.parse_start_bit(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [("16 bits", 16), ("2 bits", 2), ("1 bit", 1), ("16", 16), ("1 byte", 8),
     ("4 bytes", 32), ("", None), ("variable", None)],
)
def test_parse_length_bits(text, expected):
    assert isobus.parse_length_bits(text) == expected


# --- cycle time ------------------------------------------------------------
@pytest.mark.parametrize(
    "text,expected",
    [
        ("100 ms", 100),
        ("500 ms", 500),
        ("1 s", 1000),
        ("100 ms when active", 100),  # narrow no-break space in the export
        # Event-driven traffic must not get a cycle time: a monitoring tool
        # would report the normal quiet between bursts as loss.
        ("As Required", None),
        ("On request", None),
        ("On power-up and then on request", None),
        ("", None),
    ],
)
def test_parse_cycle_ms(text, expected):
    assert isobus.parse_cycle_ms(text) == expected


# --- identifiers -----------------------------------------------------------
@pytest.mark.parametrize(
    "text,expected",
    [
        ("Estimated curvature", "EstimatedCurvature"),
        ("Guidance system command exit/reason code", "GuidanceSystemCommandExitReasonCode"),
        ("Mechanical system lockout", "MechanicalSystemLockout"),
        ("", "Unnamed"),
    ],
)
def test_to_identifier(text, expected):
    assert isobus.to_identifier(text) == expected


def test_identifier_never_starts_with_a_digit():
    assert isobus.to_identifier("4WD status")[0] != "4"


# --- the database ----------------------------------------------------------
def test_layout_and_name_only_are_distinguished():
    """The export gives ISO parameters a layout and J1939DA ones only a name."""
    text = csv_text(
        row(pgn="44032", parameter_group_label="Agricultural Guidance Machine Info",
            acronym="GMS", pgn_data_length="8", default_priority="3",
            transmission_rate="100 ms", spn="5238", spn_name="Estimated curvature",
            sp_start_bit="1.1", spn_length="16 bits", scale_factor="0.25",
            offset_value="-8032", units="km-1", data_range="-8032 to 8031.75 km-1",
            slot_name="SAEcu03", spn_document="ISO 11783-7", pgn_document="ISO 11783-7"),
        row(pgn="61444", parameter_group_label="Electronic Engine Controller 1",
            acronym="EEC1", spn="190", spn_name="Engine Speed",
            spn_document="J1939DA", pgn_document="J1939DA"),
    )
    db = isobus.parse(text)

    gms = db.get(44032)
    assert gms.has_layout and not gms.name_only
    assert gms.identifier == "GMS"
    assert gms.cycle_ms == 100 and gms.dlc == 8 and gms.priority == 3

    eec1 = db.get(61444)
    assert eec1.name_only and not eec1.has_layout
    assert eec1.identifier == "EEC1"
    # A name-only PGN still contributes its SPN names.
    assert [s.name for s in eec1.spns] == ["Engine Speed"]


def test_spn_scaling_matches_iso_11783_7():
    """SPN 5238 must reconstruct the published curvature encoding."""
    text = csv_text(
        row(pgn="44032", acronym="GMS", spn="5238", spn_name="Estimated curvature",
            sp_start_bit="1.1", spn_length="16 bits", scale_factor="0.25",
            offset_value="-8032", units="km-1", data_range="-8032 to 8031.75 km-1",
            slot_name="SAEcu03", spn_document="ISO 11783-7"),
    )
    s = isobus.parse(text).get(44032).spns[0]
    assert (s.start_bit, s.length_bits) == (0, 16)
    assert (s.factor, s.offset) == (0.25, -8032.0)
    assert (s.minimum, s.maximum) == (-8032.0, 8031.75)
    assert s.unit == "km-1" and s.identifier == "EstimatedCurvature"


def test_spns_are_ordered_by_position():
    text = csv_text(
        row(pgn="1", spn="20", sp_start_bit="3.1", spn_length="2 bits"),
        row(pgn="1", spn="10", sp_start_bit="1.1", spn_length="16 bits"),
    )
    assert [s.spn for s in isobus.parse(text).get(1).spns] == [10, 20]


def test_duplicate_spn_rows_collapse():
    text = csv_text(
        row(pgn="1", spn="10", sp_start_bit="1.1", spn_length="8 bits"),
        row(pgn="1", spn="10", sp_start_bit="1.1", spn_length="8 bits"),
    )
    assert len(isobus.parse(text).get(1).spns) == 1


# --- SLOT value tables -----------------------------------------------------
def test_bit_state_tables_use_the_j1939_top_of_range_codes():
    """These are the two codes J1939 fixes for every discrete parameter."""
    assert slots.value_table("SAEbs02") == {3: "NotAvailable", 2: "Error"}
    assert slots.value_table("SAEbs03") == {7: "NotAvailable", 6: "Error"}
    assert slots.value_table("SAEbs06") == {63: "NotAvailable", 62: "Error"}


def test_one_bit_states_and_measured_values_get_no_table():
    """A 1-bit field has no room for error codes; a measured value has no states."""
    assert slots.value_table("SAEbs01") == {}
    assert slots.value_table("SAEcu03") == {}
    assert slots.value_table("") == {}
