"""Per-id statistics.

Bit indices follow the DBC little-endian convention:
bit_index = byte_index * 8 + bit_within_byte, bit 0 = LSB of the byte.
"""

from dbcgen.frames import Frame, IdGroup
from dbcgen.infer.stats import analyse


def group(payloads, period_us=100_000, can_id=0x100, extended=False):
    return IdGroup(
        can_id,
        extended,
        [Frame(i * period_us, can_id, p, extended) for i, p in enumerate(payloads)],
    )


def test_constant_payload():
    st = analyse(group([b"\x01\x02"] * 10))
    assert st.is_constant
    assert st.distinct_payloads == 1
    assert st.constant_bytes() == [0, 1]
    assert st.active_bits() == []


def test_bit_indexing_is_dbc_little_endian():
    """Byte 1 bit 0 must be bit index 8."""
    st = analyse(group([b"\x00\x00", b"\x00\x01"]))
    assert st.bit_flips[8] == 1
    assert sum(st.bit_flips) == 1


def test_bit_ones_counts_set_bits():
    st = analyse(group([b"\x01", b"\x01", b"\x00"]))
    assert st.bit_ones[0] == 2
    assert st.constant_value(1) == 0


def test_flip_rate_of_toggling_bit_is_one():
    st = analyse(group([b"\x00", b"\x01"] * 10))
    assert st.flip_rate(0) == 1.0
    assert st.flip_rate(1) == 0.0


def test_cycle_time_is_median_and_survives_a_gap():
    """A recording pause must not drag the cycle time; the median ignores it."""
    ts = [0, 100_000, 200_000, 5_000_000, 5_100_000, 5_200_000]
    frames = [Frame(t, 0x100, b"\x00", False) for t in ts]
    st = analyse(IdGroup(0x100, False, frames))
    assert st.cycle_us == 100_000
    assert st.cycle_ms == 100.0


def test_event_driven_traffic_is_not_marked_cyclic():
    ts = [0, 10_000, 900_000, 950_000, 5_000_000, 5_010_000]
    frames = [Frame(t, 0x100, b"\x00", False) for t in ts]
    assert analyse(IdGroup(0x100, False, frames)).is_cyclic is False


def test_cyclic_traffic_is_marked_cyclic():
    st = analyse(group([b"\x00"] * 50, period_us=210_000))
    assert st.is_cyclic
    assert st.cycle_ms == 210.0


def test_short_frames_are_padded_so_bit_positions_do_not_shift():
    """A varying DLC must not move bit indices; short payloads pad on the right."""
    frames = [
        Frame(0, 0x100, b"\xff", False),
        Frame(1000, 0x100, b"\xff\x01", False),
    ]
    st = analyse(IdGroup(0x100, False, frames))
    assert st.dlc == 2
    assert st.bit_flips[0] == 0        # byte 0 unchanged
    assert st.bit_flips[8] == 1        # byte 1 bit 0 appeared
    assert st.dlcs == {1, 2}


def test_empty_group():
    st = analyse(IdGroup(0x100, False, []))
    assert st.count == 0 and st.cycle_us is None and st.n_bits == 0


def test_too_few_frames_for_a_cycle_time():
    st = analyse(group([b"\x00", b"\x01"]))
    assert st.cycle_us is None and st.is_cyclic is False
