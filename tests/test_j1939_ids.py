"""J1939 identifier decomposition.

Expected values are taken from SAE J1939-21 and cross-checked against the
hand-built reference DBC for an agricultural steering bus, where each of
these identifiers appears.
"""

import pytest

from dbcgen.j1939.ids import ADDR_GLOBAL, compose, decompose, pgn_name_hint


@pytest.mark.parametrize(
    "can_id,pgn,sa,pf,ps,prio,dest",
    [
        # PDU1: PS is the destination and is excluded from the PGN.
        (0x0CAC1B2A, 44032, 0x2A, 172, 0x1B, 3, 0x1B),
        (0x18EEFF2A, 60928, 0x2A, 238, 0xFF, 6, 0xFF),
        (0x1CECFF2A, 60416, 0x2A, 236, 0xFF, 7, 0xFF),
        (0x0CEF2A55, 61184, 0x55, 239, 0x2A, 3, 0x2A),
        # PDU2: PS is the group extension and is part of the PGN.
        (0x18FF0C55, 65292, 0x55, 255, 0x0C, 6, None),
        (0x18FF322A, 65330, 0x2A, 255, 0x32, 6, None),
    ],
)
def test_decompose(can_id, pgn, sa, pf, ps, prio, dest):
    j = decompose(can_id)
    assert (j.pgn, j.sa, j.pf, j.ps, j.priority, j.destination) == (
        pgn,
        sa,
        pf,
        ps,
        prio,
        dest,
    )


def test_pdu_boundary_is_pf_240():
    assert decompose(0x18EFFF01).is_pdu1  # PF 239
    assert decompose(0x18F0FF01).is_pdu2  # PF 240


def test_pdu1_pgn_excludes_destination():
    """The same PGN sent to two destinations must yield one PGN."""
    assert decompose(0x0CAC1B2A).pgn == decompose(0x0CACAB2A).pgn == 44032


def test_proprietary_ranges():
    assert decompose(0x0CEF2A55).is_proprietary  # Proprietary A
    assert decompose(0x18FF0C55).is_proprietary  # Proprietary B
    assert not decompose(0x0CAC1B2A).is_proprietary  # ISO-defined
    assert not decompose(0x18EEFF2A).is_proprietary  # Address Claimed


def test_transport_pgns():
    assert decompose(0x1CECFF2A).is_transport  # TP.CM
    assert decompose(0x1CEBFF2A).is_transport  # TP.DT
    assert not decompose(0x18EEFF2A).is_transport


@pytest.mark.parametrize(
    "can_id", [0x0CAC1B2A, 0x18FF0C55, 0x18FF322A, 0x18EEFF2A, 0x1CECFF2A, 0x0CEF2A55]
)
def test_compose_round_trip(can_id):
    j = decompose(can_id)
    dest = j.ps if j.is_pdu1 else ADDR_GLOBAL
    assert compose(j.pgn, j.sa, j.priority, dest) == can_id


def test_name_hint_matches_reference_convention():
    """The fallback name states PF/PS and the source address, as the reference DBC does."""
    assert pgn_name_hint(decompose(0x18FF0C55)) == "Proprietary_FF0C_55"
    assert pgn_name_hint(decompose(0x0CAC1B2A)) == "PGN44032_2A_to_1B"
