"""DBC writer.

Written as text rather than through a library, because the output has to carry
provenance comments and value tables in a specific shape, and because the file
is the deliverable -- it should be diffable and readable, not whatever a
serialiser happens to produce.  Correctness is enforced by reloading the result
with cantools, which is a stricter reader than most tools that will consume it.
"""

from __future__ import annotations

from typing import Iterable, List, Optional

from ..model import BIG, Database, Message, Node, Signal

NS_ENTRIES = (
    "NS_DESC_", "CM_", "BA_DEF_", "BA_", "VAL_", "CAT_DEF_", "CAT_", "FILTER",
    "BA_DEF_DEF_", "EV_DATA_", "ENVVAR_DATA_", "SGTYPE_", "SGTYPE_VAL_",
    "BA_DEF_SGTYPE_", "BA_SGTYPE_", "SIG_TYPE_REF_", "VAL_TABLE_", "SIG_GROUP_",
    "SIG_VALTYPE_", "SIGTYPE_VALTYPE_", "BO_TX_BU_", "BA_DEF_REL_", "BA_REL_",
    "BA_DEF_DEF_REL_", "BU_SG_REL_", "BU_EV_REL_", "BU_BO_REL_", "SG_MUL_VAL_",
)

NO_RECEIVER = "Vector__XXX"

#: Order must match the "SignalSource" ENUM written into the file.
_SOURCE_INDEX = {"standard": 0, "convention": 1, "inferred": 2, "proposed": 3}

#: Written at the top of every generated file.  A DBC gets passed around
#: without whoever opens it having met the tool that made it.
HEADER_WARNING = (
    "READ THIS FIRST. Signal names state where each signal came from:",
    "  <name>          a real name from the published standard: bit position,",
    "                  scale and unit are as documented. Trust these.",
    "  Guess_<bit>_<n> derived from how the bytes behaved in ONE recording.",
    "                  The bit boundaries are a best estimate, measured at about",
    "                  69% exactly correct on buses where a reference exists.",
    "                  Scale is always 1 and offset 0 -- the raw value, not a",
    "                  physical unit. The meaning is unknown.",
    "  Const_<bit>_<n> these bits never changed during the recording. That is a",
    "                  fact about the recording, not proof they are constant.",
    "  Raw_Byte<n>     no structure was inferred; the byte is passed through.",
    "Every signal also carries a CM_ comment with the statistic behind it, and a",
    "SignalSource attribute (standard / convention / inferred / proposed).",
    "A signal whose bits never moved during the recording cannot be found at all.",
)

#: VFrameFormat enum indices, as Vector defines them.
FRAME_FORMAT_STANDARD = 0
FRAME_FORMAT_J1939 = 3


def _num(value: float) -> str:
    """Render a number the way DBC readers expect: no trailing '.0', no exponent
    for ordinary magnitudes."""
    if value is None:
        return "0"
    if float(value) == int(value) and abs(value) < 1e15:
        return str(int(value))
    text = repr(float(value))
    return text


def _escape(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()


def _signal_line(sig: Signal) -> str:
    mux = ""
    if sig.multiplexer:
        mux = f" {sig.multiplexer}"
    order = "0" if sig.byte_order == BIG else "1"
    sign = "-" if sig.signed else "+"
    receivers = ",".join(sig.receivers) if sig.receivers else NO_RECEIVER
    return (
        f" SG_ {sig.name}{mux} : {sig.start_bit}|{sig.length}@{order}{sign}"
        f" ({_num(sig.factor)},{_num(sig.offset)})"
        f" [{_num(sig.minimum)}|{_num(sig.maximum)}]"
        f' "{_escape(sig.unit)}"  {receivers}'
    )


def _comment_for(sig: Signal) -> str:
    parts = []
    if sig.comment:
        parts.append(sig.comment)
    if sig.evidence is not None:
        parts.append(sig.evidence.to_comment())
    return " -- ".join(parts)


def _message_comment(msg: Message) -> str:
    parts = []
    if msg.comment:
        parts.append(msg.comment)
    if msg.evidence is not None:
        parts.append(msg.evidence.to_comment())
    return " -- ".join(parts)


def dumps(db: Database) -> str:
    out: List[str] = []
    messages = db.sorted_messages()

    for line in HEADER_WARNING:
        out.append(f"// {line}")
    out.append("//")
    for note in db.header_notes:
        out.append(f"// {note}")
    out.append("")

    out.append(f'VERSION "{_escape(db.version)}"')
    out.append("")
    out.append("")
    out.append("NS_ :")
    out.extend(f"\t{e}" for e in NS_ENTRIES)
    out.append("")
    out.append("BS_:")
    out.append("")

    names = db.node_names()
    out.append("BU_: " + " ".join(names) if names else "BU_:")
    out.append("")

    # --- messages ---------------------------------------------------------
    for msg in messages:
        senders = " ".join(msg.senders) if msg.senders else NO_RECEIVER
        out.append(f"BO_ {msg.dbc_id} {msg.name}: {msg.length} {senders}")
        for sig in msg.sorted_signals():
            out.append(_signal_line(sig))
        out.append("")

    # --- comments ---------------------------------------------------------
    for node in db.nodes:
        text = node.comment
        if node.evidence is not None:
            text = f"{text} -- {node.evidence.to_comment()}" if text else node.evidence.to_comment()
        if text:
            out.append(f'CM_ BU_ {node.name} "{_escape(text)}";')

    for msg in messages:
        text = _message_comment(msg)
        if text:
            out.append(f'CM_ BO_ {msg.dbc_id} "{_escape(text)}";')
        for sig in msg.sorted_signals():
            text = _comment_for(sig)
            if text:
                out.append(f'CM_ SG_ {msg.dbc_id} {sig.name} "{_escape(text)}";')

    # --- attribute definitions -------------------------------------------
    out.append('BA_DEF_ BO_  "VFrameFormat" ENUM  "StandardCAN","ExtendedCAN","reserved","J1939PG";')
    out.append('BA_DEF_ BO_  "GenMsgCycleTime" INT 0 65535;')
    out.append('BA_DEF_ SG_  "SPN" INT 0 524287;')
    out.append(
        'BA_DEF_ SG_  "SignalSource" ENUM  '
        '"standard","convention","inferred","proposed";'
    )
    out.append('BA_DEF_  "BusType" STRING ;')
    out.append('BA_DEF_  "ProtocolType" STRING ;')
    out.append('BA_DEF_  "DatabaseVersion" STRING ;')
    out.append('BA_DEF_DEF_  "VFrameFormat" "J1939PG";')
    out.append('BA_DEF_DEF_  "GenMsgCycleTime" 0;')
    out.append('BA_DEF_DEF_  "SPN" 0;')
    out.append('BA_DEF_DEF_  "SignalSource" "inferred";')
    out.append('BA_DEF_DEF_  "BusType" "";')
    out.append('BA_DEF_DEF_  "ProtocolType" "";')
    out.append('BA_DEF_DEF_  "DatabaseVersion" "";')

    out.append('BA_ "BusType" "CAN";')
    out.append('BA_ "ProtocolType" "J1939";')
    out.append(f'BA_ "DatabaseVersion" "{_escape(db.version)}";')

    for msg in messages:
        fmt = FRAME_FORMAT_J1939 if msg.extended else FRAME_FORMAT_STANDARD
        out.append(f'BA_ "VFrameFormat" BO_ {msg.dbc_id} {fmt};')
    for msg in messages:
        if msg.cycle_time:
            out.append(f'BA_ "GenMsgCycleTime" BO_ {msg.dbc_id} {int(msg.cycle_time)};')
    for msg in messages:
        for sig in msg.sorted_signals():
            if sig.spn:
                out.append(f'BA_ "SPN" SG_ {msg.dbc_id} {sig.name} {sig.spn};')
    # Provenance as an attribute as well as in the name, so a tool can filter
    # on it without parsing comments.
    for msg in messages:
        for sig in msg.sorted_signals():
            if sig.evidence is None:
                continue
            index = _SOURCE_INDEX.get(sig.evidence.origin)
            if index is not None:
                out.append(
                    f'BA_ "SignalSource" SG_ {msg.dbc_id} {sig.name} {index};'
                )

    # --- value tables -----------------------------------------------------
    for msg in messages:
        for sig in msg.sorted_signals():
            if not sig.values:
                continue
            pairs = " ".join(
                f'{value} "{_escape(label)}"' for value, label in sorted(sig.values.items())
            )
            out.append(f"VAL_ {msg.dbc_id} {sig.name} {pairs} ;")

    out.append("")
    return "\n".join(out)


def dump(db: Database, path) -> None:
    from pathlib import Path

    Path(path).write_text(dumps(db), encoding="utf-8")
