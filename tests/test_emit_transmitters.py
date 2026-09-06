"""A BO_ line names one transmitter, and only one.

Space-joining several produced a file every DBC reader rejects. It went
unnoticed because the J1939 path never produces more than one sender; the
opendbc source does, on the first file it was pointed at.
"""

import cantools

from dbcgen.emit import dbc as writer
from dbcgen.model import Database, Message, Node, Signal


def _db(senders):
    msg = Message(frame_id=0x123, name="Msg", length=1, extended=False, senders=senders)
    msg.signals = [Signal(name="Sig", start_bit=0, length=8)]
    return Database(
        version="t",
        nodes=[Node(name=n) for n in senders],
        messages=[msg],
    )


def test_two_transmitters_still_produce_a_loadable_file(tmp_path):
    out = tmp_path / "two.dbc"
    writer.dump(_db(["ADAS", "NEO"]), out)
    loaded = cantools.database.load_file(out)
    assert loaded.get_message_by_frame_id(0x123).name == "Msg"


def test_the_extra_transmitter_is_kept_in_bo_tx_bu(tmp_path):
    text = writer.dumps(_db(["ADAS", "NEO"]))
    assert "BO_ 291 Msg: 1 ADAS" in text
    assert "BO_TX_BU_ 291 : ADAS,NEO;" in text


def test_a_single_transmitter_adds_no_bo_tx_bu(tmp_path):
    """`BO_TX_BU_` also appears in the NS_ keyword block of every DBC, so the
    check has to be for the statement rather than for the word."""
    text = writer.dumps(_db(["ADAS"]))
    assert "BO_ 291 Msg: 1 ADAS" in text
    assert not [l for l in text.splitlines() if l.startswith("BO_TX_BU_ ")]
