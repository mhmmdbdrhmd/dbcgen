"""Reading a licensed J1939 Digital Annex.

The annex is the file that turns `Guess_24_16` into `EngineSpeed` on a truck
bus, and it is a spreadsheet, not a DBC. It is also licensed, so there is no
specimen in this repository and none of these tests uses one.

What they use instead is the free ISOBUS export, which is the same kind of
table with the same kind of columns and known-correct answers. If the
header-sniffing parser reproduces the dedicated parser exactly on 1,269 real
laid-out parameters, the sniffing works; what remains untested against a real
annex is only whether its column *spellings* are in the synonym list, and a
spelling that is missing produces a refusal naming the headers it did find,
not a misplaced signal.
"""

import csv

import pytest

from dbcgen.standard import annex, fetch, isobus

DA_HEADERS = [
    "PGN", "Parameter Group Label", "Acronym", "PGN Data Length",
    "Default Priority", "Transmission Rate", "SPN", "SPN Name",
    "SPN Position in PGN", "SPN Length", "Resolution", "Offset", "Units",
    "Data Range", "SLOT Name",
]
EEC1_ROWS = [
    [61444, "Electronic Engine Controller 1", "EEC1", 8, 3, "10 ms",
     899, "Engine Torque Mode", "1.1", "4 bits", 1, 0, "", "0 to 15", "SAEbs04"],
    [61444, "Electronic Engine Controller 1", "EEC1", 8, 3, "10 ms",
     190, "Engine Speed", "4", "16 bits", 0.125, 0, "rpm",
     "0 to 8031.875 rpm", "SAEvr03"],
]


def _write(path, rows, title=True):
    head = [["SAE J1939 Digital Annex 202412"], ["Licensed to: an owner"], []]
    with path.open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows((head if title else []) + [DA_HEADERS] + rows)
    return path


@pytest.fixture
def eec1(tmp_path):
    return _write(tmp_path / "da.csv", EEC1_ROWS)


@pytest.fixture(scope="module")
def free_export(tmp_path_factory):
    """The free ISOBUS table, as a file, to sniff exactly like an annex."""
    try:
        info = fetch.ensure()
    except Exception as exc:
        pytest.skip(f"standard export unavailable: {exc}")
    text = isobus.open_member(info, isobus.MEMBER)
    p = tmp_path_factory.mktemp("std") / "spns.csv"
    p.write_text(text, encoding="utf-8")
    return p, isobus.parse(text)


# ------------------------------------------------- sniffing real J1939 data


def test_sniffed_columns_reproduce_the_dedicated_parser_exactly(free_export):
    """The strongest evidence available without a licensed specimen."""
    path, official = free_export
    sniffed = annex.load(path)

    official_layouts = {
        (pgn, s.spn): (s.start_bit, s.length_bits)
        for pgn, e in official.pgns.items()
        for s in e.laid_out_spns
    }
    sniffed_layouts = {
        (pgn, s.spn): (s.start_bit, s.length_bits)
        for pgn, e in sniffed.pgns.items()
        for s in e.laid_out_spns
    }
    assert official_layouts == sniffed_layouts
    assert len(official_layouts) > 1200


def test_quoting_is_not_inferred_only_the_delimiter(free_export):
    """csv.Sniffer decides doublequote is off on this file and shreds 741 rows."""
    path, _ = free_export
    rows = annex._read_text(path)
    widths = {len(r) for r in rows}
    assert len(widths) == 1, f"rows came out ragged: {sorted(widths)[:5]}"


# ------------------------------------------------------- a DA-shaped table


def test_digital_annex_headers_are_recognised(eec1):
    db = annex.load(eec1)
    assert 61444 in db.pgns
    speed = next(s for s in db.pgns[61444].spns if s.spn == 190)
    assert (speed.start_bit, speed.length_bits) == (24, 16)
    assert speed.factor == 0.125
    assert speed.unit == "rpm"


def test_a_title_block_above_the_table_is_skipped(tmp_path):
    """An annex opens with its own title, not with the header row."""
    with_title = annex.load(_write(tmp_path / "a.csv", EEC1_ROWS, title=True))
    without = annex.load(_write(tmp_path / "b.csv", EEC1_ROWS, title=False))
    assert set(with_title.pgns) == set(without.pgns)


def test_a_resolution_written_as_prose_is_read(tmp_path):
    """The annex writes resolutions like '0.125 rpm/bit', not as a bare float."""
    rows = [list(EEC1_ROWS[1])]
    rows[0][10] = "0.125 rpm/bit"
    db = annex.load(_write(tmp_path / "p.csv", rows))
    assert next(iter(db.pgns[61444].spns)).factor == 0.125


# --------------------------------------------------------------- refusals


def test_a_table_without_a_pgn_column_is_refused_and_names_what_it_found(tmp_path):
    p = tmp_path / "wrong.csv"
    with p.open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows([["Colour", "Size"], ["red", 3]])
    with pytest.raises(fetch.StandardDataError) as excinfo:
        annex.load(p)
    assert "Colour" in str(excinfo.value)


def test_an_unreadable_extension_is_refused(tmp_path):
    p = tmp_path / "annex.pdf"
    p.write_bytes(b"%PDF-1.4")
    with pytest.raises(fetch.StandardDataError):
        annex.load(p)


# ----------------------------------------------------------------- merging


def test_the_annex_lays_out_a_group_the_free_registry_only_names(free_export, eec1):
    """EEC1 is the case the whole feature exists for."""
    _, base = free_export
    assert base.pgns[61444].name_only, "EEC1 should be paywalled in the free export"

    merged, stats = annex.merge(base, annex.load(eec1))
    assert stats["laid_out"] == 1
    assert merged.pgns[61444].has_layout
    speed = next(s for s in merged.pgns[61444].laid_out_spns if s.spn == 190)
    assert (speed.start_bit, speed.length_bits, speed.factor) == (24, 16, 0.125)


def test_merging_never_overwrites_what_the_free_registry_publishes(free_export):
    """Where both have an answer the free one is authoritative and stays."""
    path, base = free_export
    merged, stats = annex.merge(base, annex.load(path))
    assert stats["laid_out"] == 0
    assert stats["already_known"] > 90
    before = {s.spn: (s.start_bit, s.length_bits)
              for s in base.pgns[44032].laid_out_spns}
    after = {s.spn: (s.start_bit, s.length_bits)
             for s in merged.pgns[44032].laid_out_spns}
    assert before == after


def test_the_merged_layout_is_marked_standard(free_export, eec1):
    _, base = free_export
    merged, _ = annex.merge(base, annex.load(eec1))
    assert "Digital Annex" in merged.pgns[61444].laid_out_spns[0].document


# ------------------------------------------------------------- spreadsheets


def test_a_real_xlsx_is_read_the_same_as_its_csv(tmp_path, eec1):
    """The licensed article arrives as a workbook, so that path must work."""
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "SPNs & PGNs"
    sheet.append(["SAE J1939 Digital Annex 202412"])
    sheet.append([])
    sheet.append(DA_HEADERS)
    for row in EEC1_ROWS:
        sheet.append(row)
    path = tmp_path / "annex.xlsx"
    book.save(path)

    from_xlsx = annex.load(path)
    from_csv = annex.load(eec1)
    assert {p: [(s.spn, s.start_bit, s.length_bits) for s in e.spns]
            for p, e in from_xlsx.pgns.items()} == \
           {p: [(s.spn, s.start_bit, s.length_bits) for s in e.spns]
            for p, e in from_csv.pgns.items()}


def test_the_right_sheet_is_found_among_several(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    book.active.title = "Cover"
    book["Cover"].append(["This workbook is licensed to someone"])
    notes = book.create_sheet("Revision Notes")
    notes.append(["Changed in this revision"])
    table = book.create_sheet("SPNs and PGNs")
    table.append(DA_HEADERS)
    for row in EEC1_ROWS:
        table.append(row)
    path = tmp_path / "multi.xlsx"
    book.save(path)

    db = annex.load(path)
    assert 61444 in db.pgns


def test_a_named_sheet_that_does_not_exist_lists_the_ones_that_do(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    book = openpyxl.Workbook()
    book.active.title = "SPNs and PGNs"
    book.active.append(DA_HEADERS)
    book.active.append(EEC1_ROWS[0])
    path = tmp_path / "one.xlsx"
    book.save(path)

    with pytest.raises(fetch.StandardDataError) as excinfo:
        annex.load(path, sheet="No Such Sheet")
    assert "SPNs and PGNs" in str(excinfo.value)
