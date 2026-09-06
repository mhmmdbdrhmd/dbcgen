"""The inference engine.

Payloads are built by encoding known signals, so each test states a ground
truth and checks the segmenter recovers it -- the same contract the benchmark
applies to real recordings, at a scale small enough to read.
"""

import random

from dbcgen.frames import Frame, IdGroup
from dbcgen.infer import counters, endian, segment
from dbcgen.infer.stats import analyse


def build(payloads, can_id=0x100):
    group = IdGroup(
        can_id, False, [Frame(i * 100_000, can_id, p, False) for i, p in enumerate(payloads)]
    )
    return analyse(group), payloads


def pack(dlc, *fields):
    """Build a payload from (start_bit, length, value) little-endian fields."""
    bits = 0
    for start, length, value in fields:
        bits |= (value & ((1 << length) - 1)) << start
    return bits.to_bytes(dlc, "little")


def shapes(fields):
    return {(f.start_bit, f.length) for f in fields}


def walk(n, start, step, lo=0, hi=1 << 30, seed=0):
    """A random walk.

    Real signals wander.  A fixed-step ramp does not, and worse, its low bits
    stay constant -- an artefact of the fixture rather than anything a bus
    does, which would make the segmenter look wrong for being right.
    """
    rng = random.Random(seed)
    value = start
    out = []
    for _ in range(n):
        value = min(hi, max(lo, value + rng.randint(-step, step)))
        out.append(value)
    return out


# --- bit ordering ----------------------------------------------------------
def test_little_endian_bit_indices_are_contiguous():
    assert endian.bit_indices(0, 4, endian.LITTLE) == [3, 2, 1, 0]


def test_big_endian_walks_down_a_byte_then_to_the_top_of_the_next():
    """A Motorola signal's start bit is its MSB, so its bits are not a range."""
    assert endian.bit_indices(7, 16, endian.BIG) == [
        7, 6, 5, 4, 3, 2, 1, 0, 15, 14, 13, 12, 11, 10, 9, 8
    ]


def test_extract_reads_the_expected_value():
    ints = endian.to_ints([pack(2, (0, 16, 0x1234))], 2)
    assert endian.extract(ints, endian.bit_indices(0, 16, endian.LITTLE)) == [0x1234]


# --- smoothness ------------------------------------------------------------
def test_a_wandering_signal_is_smooth_and_noise_is_not():
    assert endian.smoothness(walk(300, 30000, 40, hi=65535)) > 0.8
    rng = random.Random(7)
    assert endian.smoothness([rng.randrange(65536) for _ in range(300)]) < 0.2


def test_a_constant_series_is_not_called_smooth():
    """Constancy is a separate finding; scoring it as smooth would let any
    unchanging region pose as a well-chosen field."""
    assert endian.smoothness([42] * 50) == 0.0


def test_byte_order_is_chosen_by_which_reading_moves_sensibly():
    """A big-endian signal must not be mistaken for a little-endian one."""
    payloads = [v.to_bytes(2, "big") for v in walk(400, 30000, 30, hi=65535)]
    winner, why = endian.best_byte_order(endian.to_ints(payloads, 2), 0, 16)
    assert winner.byte_order == endian.BIG
    assert "big-endian" in why


def test_byte_order_does_not_apply_inside_one_byte():
    ints = endian.to_ints([bytes([v]) for v in range(200)], 1)
    _, why = endian.best_byte_order(ints, 0, 8)
    assert "one byte" in why


# --- counters and checksums ------------------------------------------------
def test_plain_byte_counter():
    ints = endian.to_ints([pack(2, (0, 8, i % 256), (8, 8, 7)) for i in range(300)], 2)
    found = counters.find_counter(ints, 2)
    assert (found.start_bit, found.length, found.modulus) == (0, 8, 256)
    assert found.ratio == 1.0


def test_masked_counter_with_a_constant_marker_above_it():
    """Some buses write a counter as 0xF0 | n; the marker must not
    stop it being recognised."""
    ints = endian.to_ints([bytes([0xF0 | (i % 16)]) for i in range(200)], 1)
    found = counters.find_counter(ints, 1)
    assert (found.start_bit, found.length) == (0, 4)
    assert found.constant_high_bits == 0xF


def test_a_slow_ramp_is_not_a_counter():
    """A counter has to be seen wrapping, or every rising measurement is one."""
    ints = endian.to_ints([bytes([i // 4]) for i in range(60)], 1)
    assert counters.find_counter(ints, 1) is None


def test_checksum_is_recognised_by_its_statistics():
    rng = random.Random(0)
    live = walk(400, 20000, 40, hi=65535, seed=11)
    payloads = [pack(4, (0, 16, live[i]), (16, 16, rng.getrandbits(16))) for i in range(400)]
    found = counters.find_checksum(endian.to_ints(payloads, 4), 4)
    assert (found.start_bit, found.length) == (16, 16)


def test_a_smooth_value_is_not_called_a_checksum():
    live = walk(400, 30000, 40, hi=65535, seed=12)
    payloads = [pack(4, (0, 16, 0), (16, 16, live[i])) for i in range(400)]
    assert counters.find_checksum(endian.to_ints(payloads, 4), 4) is None


# --- boundary detection ----------------------------------------------------
def test_boundaries_land_where_the_flip_rate_rises():
    rates = [0.5, 0.25, 0.12, 0.06, 0.5, 0.25, 0.12, 0.06]
    assert segment.boundaries(rates, list(range(8)), samples=1000) == [0, 4]


def test_sampling_noise_does_not_create_a_boundary():
    """Two bits of one field, whose rates differ only by estimation noise."""
    rates = [0.20, 0.21, 0.10, 0.05]
    assert segment.boundaries(rates, list(range(4)), samples=300) == [0]


def test_no_boundary_is_taken_inside_a_saturated_region():
    """Below the signal's step size every bit sits near 0.5 at random, so a
    rise there is noise, not a new field."""
    rates = [0.45, 0.50, 0.48, 0.51, 0.25, 0.12]
    assert segment.boundaries(rates, list(range(6)), samples=20000) == [0]


def test_trim_leaves_a_field_that_begins_mid_byte_alone():
    """Packed layouts routinely have high bits that do not move; trimming them
    would break the very layouts the flip profile recovers best."""
    rates = [0.0] + [0.2] * 8 + [0.0] * 20
    assert segment.trim_constant_tail(rates, 1, 28) == (1, 28)


def test_trim_shortens_a_byte_aligned_field_with_a_long_dead_tail():
    rates = [0.0] * 24 + [0.4, 0.3, 0.2, 0.1, 0.05, 0.02, 0.01, 0.01] + [0.0] * 32
    assert segment.trim_constant_tail(rates, 24, 40) == (24, 8)


# --- end to end ------------------------------------------------------------
def test_two_adjacent_16_bit_signals_are_separated():
    a = walk(400, 5000, 25, hi=65535, seed=1)
    b = walk(400, 40000, 25, hi=65535, seed=2)
    payloads = [pack(4, (0, 16, a[i]), (16, 16, b[i])) for i in range(400)]
    stats, _ = build(payloads)
    assert shapes(segment.segment(stats, payloads)) == {(0, 16), (16, 16)}


def test_a_packed_layout_is_recovered_without_byte_alignment():
    """The benchmark's GNSS odometer message, exactly as its publisher packs it:
    a validity flag then fields of 22, 19 and 22 bits, none byte-aligned."""
    trip = walk(500, 100000, 40, hi=(1 << 22) - 1, seed=3)
    accuracy = walk(500, 4000, 20, hi=(1 << 19) - 1, seed=4)
    total = walk(500, 900000, 40, hi=(1 << 22) - 1, seed=13)
    payloads = [
        pack(8, (0, 1, 1), (1, 22, trip[i]), (23, 19, accuracy[i]), (42, 22, total[i]))
        for i in range(500)
    ]
    stats, _ = build(payloads)
    found = shapes(segment.segment(stats, payloads))
    assert (1, 22) in found and (23, 19) in found and (42, 22) in found


def test_a_field_below_a_faster_one_cannot_be_separated_from_it():
    """A known limit, recorded rather than hidden.

    Boundaries are found where the flip rate *rises*.  A field whose own bits
    change more often than the LSB of the field above it produces a fall
    instead, and the two merge.  In practice the low field is a flag or a
    counter -- both of which are caught earlier by behaviour -- but where it is
    neither, the boundary is not visible in the profile at all.
    """
    fast = walk(400, 30000, 4000, hi=65535, seed=14)   # jumps most frames
    slow = walk(400, 300, 2, hi=65535, seed=15)        # barely moves
    payloads = [pack(4, (0, 16, fast[i]), (16, 16, slow[i])) for i in range(400)]
    stats, _ = build(payloads)
    found = shapes(segment.segment(stats, payloads))
    assert (0, 16) in found  # the fast field is bounded correctly from below
    assert (16, 16) not in found  # but the slow one above it is not separated


def test_constant_bytes_are_reported_as_constant_not_as_signals():
    live = walk(300, 20000, 30, hi=65535, seed=5)
    payloads = [pack(4, (0, 16, live[i]), (16, 16, 0xBEEF)) for i in range(300)]
    stats, _ = build(payloads)
    fields = {(f.start_bit, f.length): f.kind for f in segment.segment(stats, payloads)}
    assert fields[(0, 16)] == segment.VALUE
    assert all(kind == segment.CONSTANT for (s, _), kind in fields.items() if s >= 16)


def test_an_unchanging_payload_yields_constants_not_an_invented_field():
    payloads = [bytes.fromhex("0102030405060708")] * 100
    stats, _ = build(payloads)
    fields = segment.segment(stats, payloads)
    assert len(fields) == 8
    assert all(f.kind == segment.CONSTANT for f in fields)


def test_constant_high_bits_do_not_swallow_the_rest_of_the_payload():
    """A 16-bit signal at byte 3 with an empty tail must stay 16 bits.

    Nothing bounds such a field from above except the next signal's LSB, so
    without the tail rule it runs to the end of the frame -- which is how a
    16-bit engine speed became a 32-bit one on the benchmark.
    """
    speed = walk(500, 20000, 30, hi=65535, seed=6)
    payloads = [pack(8, (24, 16, speed[i])) for i in range(500)]
    stats, _ = build(payloads)
    assert (24, 16) in shapes(segment.segment(stats, payloads))


def test_a_low_cardinality_field_is_called_discrete_not_measured():
    live = walk(300, 128, 9, hi=255, seed=8)
    payloads = [pack(2, (0, 8, i % 3), (8, 8, live[i])) for i in range(300)]
    stats, _ = build(payloads)
    fields = {(f.start_bit, f.length): f.kind for f in segment.segment(stats, payloads)}
    assert fields[(0, 8)] == segment.DISCRETE


def test_every_payload_bit_is_accounted_for_exactly_once():
    """No byte on the bus may be silently dropped or double-claimed."""
    rng = random.Random(1)
    small = walk(400, 2000, 20, hi=4095, seed=9)
    big = walk(400, 5_000_000, 500, hi=(1 << 32) - 1, seed=10)
    payloads = [
        pack(8, (0, 12, small[i]), (12, 4, i % 16), (16, 32, big[i]),
             (48, 16, rng.getrandbits(16)))
        for i in range(400)
    ]
    stats, _ = build(payloads)
    covered = set()
    for f in segment.segment(stats, payloads):
        assert not (covered & f.bit_set()), f"fields overlap at bit {f.start_bit}"
        covered |= f.bit_set()
    assert covered == set(range(64))
