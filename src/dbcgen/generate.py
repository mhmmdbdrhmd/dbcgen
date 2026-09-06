"""Assemble a Database from a recording and the standard.

Layering, highest authority first:

1. the network-layer groups (address claim, request, transport), whose layouts
   J1939-21/-81 publish;
2. the standard's own bit layout, where the export provides one;
3. inference over the recorded bytes (added in the inference phase);
4. raw `ByteN` signals, so that every payload byte on the bus is named and the
   generated DBC decodes 100% of the recording rather than most of it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from .evidence import from_standard, inferred
from .frames import FrameSet, IdGroup
from .infer.segment import segment
from .infer.stats import MessageStats, analyse
from .j1939.ids import compose, decompose, pgn_name_hint
from .j1939.nodes import NodeIdentity, identify
from .model import Database, Message, Node, Signal
from .readers.canlogger_csv import RecordingMeta
from .standard import network_layer, slots
from .standard.isobus import PgnDef, SpnDef, StandardDatabase

#: Units the export uses for discrete parameters; meaningless in a DBC.
_NON_UNITS = {"bit", "Bit Field", "bits", "-"}


@dataclass
class GenerationReport:
    """What the generator did, and on what basis."""

    messages: int = 0
    from_network_layer: int = 0
    from_standard_layout: int = 0
    named_only: int = 0
    """PGN named by the standard, layout paywalled -- raw bytes emitted."""
    unknown: int = 0
    raw_byte_messages: int = 0
    signals_standard: int = 0
    signals_inferred: int = 0
    """Signals derived from the recording, whether segmented or passed through."""
    dropped_spns: List[str] = None

    def __post_init__(self) -> None:
        if self.dropped_spns is None:
            self.dropped_spns = []


def _unit(text: str) -> str:
    return "" if (text or "").strip() in _NON_UNITS else (text or "").strip()


def _raw_byte_signals(dlc: int, stats: MessageStats, reason: str) -> List[Signal]:
    """One 8-bit signal per payload byte.

    Not a decode -- a guarantee that the frame appears named in a trace viewer
    and that no byte on the bus is silently discarded.  Used when inference is
    switched off, and as the fallback when a message has too few frames for any
    statistic to mean anything.
    """
    sigs = []
    for i in range(dlc):
        constant = all(stats.bit_flips[i * 8 + j] == 0 for j in range(8))
        detail = f"{reason}; byte {i} "
        detail += (
            f"held one value across all {stats.count} frames"
            if constant
            else f"varied across {stats.count} frames"
        )
        sigs.append(
            Signal(
                name=f"Raw_Byte{i}",
                start_bit=i * 8,
                length=8,
                evidence=inferred("raw-byte", detail, n=stats.count, byte=i),
            )
        )
    return sigs


#: Below this many frames no statistic here is worth acting on, so the payload
#: is presented as plain bytes rather than as a confident-looking guess.
MIN_FRAMES_FOR_INFERENCE = 12


def _inferred_signals(
    group: IdGroup, stats: MessageStats, reason: str, dlc: int
) -> List[Signal]:
    """Signals recovered from the bytes themselves.

    Falls back to raw bytes when the recording is too short to support any
    conclusion: a segmentation drawn from four frames is decoration, not
    evidence.
    """
    if stats.count < MIN_FRAMES_FOR_INFERENCE:
        return _raw_byte_signals(
            dlc,
            stats,
            f"{reason}; only {stats.count} frame(s) recorded, too few to infer a layout",
        )

    fields = segment(stats, [f.data for f in group.frames])
    if not fields:
        return _raw_byte_signals(dlc, stats, reason)

    signals: List[Signal] = []
    used: set = set()
    for fld in fields:
        name = fld.name_hint
        while name in used:
            name = f"{name}_b{fld.start_bit}"
        used.add(name)
        signals.append(
            Signal(
                name=name,
                start_bit=fld.start_bit,
                length=fld.length,
                byte_order=fld.byte_order,
                evidence=inferred(fld.rule, f"{reason}; {fld.detail}", fld.confidence,
                                  n=stats.count),
            )
        )
    return signals


def _standard_signals(
    pgn: PgnDef, dlc: int, report: GenerationReport
) -> Tuple[List[Signal], List[str]]:
    """Signals from the export's own layout, clipped to what fits the frame."""
    sigs: List[Signal] = []
    dropped: List[str] = []
    used: set = set()

    for spn in pgn.laid_out_spns:
        end = spn.start_bit + spn.length_bits
        if end > dlc * 8:
            dropped.append(
                f"SPN {spn.spn} ({spn.name}) ends at bit {end}, past the {dlc}-byte frame"
            )
            continue
        overlap = used & set(range(spn.start_bit, end))
        if overlap:
            dropped.append(
                f"SPN {spn.spn} ({spn.name}) overlaps bits {min(overlap)}..{max(overlap)}"
            )
            continue
        used |= set(range(spn.start_bit, end))

        values = slots.value_table(spn.slot_name, spn.length_bits)
        evidence = from_standard(
            f"{spn.document or pgn.document}:SPN{spn.spn}",
            f"PGN {pgn.pgn} position {spn.position or '?'}, "
            f"{spn.length_bits} bits at byte.bit {spn.position or '?'}",
            spn=spn.spn,
            document=spn.document,
        )
        sigs.append(
            Signal(
                name=spn.identifier,
                start_bit=spn.start_bit,
                length=spn.length_bits,
                factor=spn.factor if spn.factor is not None else 1.0,
                offset=spn.offset if spn.offset is not None else 0.0,
                minimum=spn.minimum,
                maximum=spn.maximum,
                unit=_unit(spn.unit),
                spn=spn.spn,
                values=values,
                comment=spn.description[:220] if spn.description else "",
                evidence=evidence,
            )
        )
    return sigs, dropped


class _Namer:
    """Keeps message names unique and deterministic."""

    def __init__(self) -> None:
        self.taken: Dict[str, int] = {}

    def unique(self, base: str) -> str:
        if base not in self.taken:
            self.taken[base] = 1
            return base
        n = self.taken[base] = self.taken[base] + 1
        return f"{base}_{n}"


def _message_base_name(jid, pgn_def: Optional[PgnDef], override: str = "") -> str:
    """`<group>_<source address>`, plus the destination when there is a specific one.

    Messages are keyed on the whole 29-bit id, so one parameter group sent from
    one ECU to two different destinations is two messages.  Naming the
    destination up front makes each name say which frame it is, instead of
    depending on which the reader happened to meet first.
    """
    stem = override or (pgn_def.identifier if pgn_def is not None else "")
    base = f"{stem}_{jid.sa:02X}" if stem else pgn_name_hint(jid)
    if jid.is_pdu1 and jid.destination not in (None, 0xFF):
        base = f"{base}_to_{jid.destination:02X}"
    return base


def generate(
    frameset: FrameSet,
    standard: StandardDatabase,
    tables,
    meta: Optional[RecordingMeta] = None,
    version: str = "",
) -> Tuple[Database, GenerationReport, Dict[int, NodeIdentity]]:
    report = GenerationReport()
    nodes = identify(frameset, tables)
    namer = _Namer()
    messages: List[Message] = []

    for group in frameset:
        stats = analyse(group)
        dlc = max(group.dlc, 1)

        if not group.extended:
            name = namer.unique(f"CAN_{group.can_id:03X}")
            msg = Message(
                frame_id=group.can_id,
                name=name,
                length=dlc,
                extended=False,
                comment="11-bit identifier, outside the J1939 addressing scheme",
                evidence=inferred(
                    "standard-frame",
                    f"11-bit id 0x{group.can_id:03X}; J1939 defines no meaning for it, "
                    f"so no PGN lookup applies",
                    n=stats.count,
                ),
            )
            msg.signals = _inferred_signals(
                group, stats, "11-bit id, not a J1939 parameter group", dlc
            )
            report.unknown += 1
            report.raw_byte_messages += 1
            report.signals_inferred += len(msg.signals)
            messages.append(msg)
            continue

        jid = decompose(group.can_id)
        node = nodes.get(jid.sa)
        sender = [node.name] if node else []
        pgn_def = standard.get(jid.pgn)

        signals: List[Signal] = []
        comment = ""
        evidence = None

        net = network_layer.info_for(jid.pgn)
        if net is not None:
            _, fallback, description = net
            signals = [
                s for s in network_layer.signals_for(jid.pgn) if s.start_bit + s.length <= dlc * 8
            ]
            # The export's acronym for these is punctuation-heavy ("TP.CM.xx");
            # the network-layer module's own name is the readable one.
            base = _message_base_name(jid, pgn_def, override=fallback)
            comment = description
            evidence = from_standard(
                f"j1939-network-layer:PGN{jid.pgn}",
                f"{description}; layout defined by the network-layer part of the standard",
                pgn=jid.pgn,
            )
            report.from_network_layer += 1
            report.signals_standard += len(signals)

        elif pgn_def is not None and pgn_def.has_layout and not pgn_def.multipacket:
            signals, dropped = _standard_signals(pgn_def, dlc, report)
            report.dropped_spns.extend(f"PGN {jid.pgn}: {d}" for d in dropped)
            base = _message_base_name(jid, pgn_def)
            comment = pgn_def.label
            evidence = from_standard(
                f"{pgn_def.document}:PGN{jid.pgn}",
                f"{pgn_def.label}; {len(signals)} signal(s) placed from the published layout",
                pgn=jid.pgn,
            )
            report.from_standard_layout += 1
            report.signals_standard += len(signals)

        elif pgn_def is not None:
            reason = (
                "parameter group is transported multi-packet, so a single frame "
                "carries only a fragment"
                if pgn_def.multipacket
                else "the standard names this parameter group but publishes its bit "
                "layout only in the paid SAE J1939 Digital Annex"
            )
            base = _message_base_name(jid, pgn_def)
            comment = pgn_def.label
            evidence = inferred(
                "standard-name-only",
                f"PGN {jid.pgn} is '{pgn_def.label}' ({pgn_def.acronym}) per "
                f"{pgn_def.document}, listing {len(pgn_def.spns)} SPN(s); {reason}",
                pgn=jid.pgn,
                spns=[s.spn for s in pgn_def.spns][:32],
            )
            signals = _inferred_signals(group, stats, reason, dlc)
            report.named_only += 1
            report.raw_byte_messages += 1
            report.signals_inferred += len(signals)

        else:
            base = _message_base_name(jid, None)
            reason = (
                "proprietary parameter group; by definition the standard does not "
                "define its contents"
                if jid.is_proprietary
                else "no entry for this PGN in the standard export"
            )
            evidence = inferred("unknown-pgn", f"PGN {jid.pgn}; {reason}", pgn=jid.pgn)
            signals = _inferred_signals(group, stats, reason, dlc)
            report.unknown += 1
            report.raw_byte_messages += 1
            report.signals_inferred += len(signals)

        cycle = None
        if stats.is_cyclic and stats.cycle_ms:
            cycle = int(round(stats.cycle_ms))

        msg = Message(
            frame_id=group.can_id,
            name=namer.unique(base),
            length=dlc,
            extended=True,
            senders=sender,
            comment=comment,
            cycle_time=cycle,
            evidence=evidence,
            pgn=jid.pgn,
            source_address=jid.sa,
        )
        msg.signals = signals
        messages.append(msg)

    report.messages = len(messages)

    db = Database(
        version=version or "generated by dbcgen",
        nodes=[
            Node(
                name=n.name,
                comment=n.comment,
                evidence=n.evidence,
                source_address=n.source_address,
            )
            for n in sorted(nodes.values(), key=lambda x: x.source_address)
        ],
        messages=messages,
    )
    db.header_notes = [
        "Generated by dbcgen. Every message and signal carries a [origin:rule] comment.",
        f"standard: {standard.source}",
    ]
    if meta is not None:
        db.header_notes.append(f"recording: {meta.source} ({meta.summary()})")
        if meta.lossy:
            db.header_notes.append(
                "WARNING: recording was made with a frame map, so decoded rows carry no "
                "payload; inference saw only the frames that still had raw bytes."
            )
    return db, report, nodes


#: Source address used when nothing says which ECU sends a parameter group.
#: J1939-81 calls 0xFE the null address -- no ECU may claim it -- which is why
#: bus-template DBCs published without a recording use it to mean "whichever
#: node sends this".  A reader that matches on the whole 29-bit id will not
#: match real traffic; one that matches on the PGN will.
TEMPLATE_SOURCE_ADDRESS = 0xFE

#: A parameter group longer than this cannot arrive in one classical CAN frame,
#: so its published layout describes a reassembled message, not a frame.
MAX_SINGLE_FRAME_DLC = 8


@dataclass
class StandardOnlyReport:
    """What a recording-free build could and could not place."""

    messages: int = 0
    from_network_layer: int = 0
    from_standard_layout: int = 0
    signals_standard: int = 0
    skipped_name_only: int = 0
    """PGNs the standard names but does not lay out -- nothing to emit."""
    skipped_multipacket: int = 0
    """PGNs whose layout spans more than one frame."""
    dropped_spns: List[str] = None

    def __post_init__(self) -> None:
        if self.dropped_spns is None:
            self.dropped_spns = []


def generate_from_standard(
    standard: StandardDatabase,
    pgns: Optional[Iterable[int]] = None,
    source_address: int = TEMPLATE_SOURCE_ADDRESS,
    version: str = "",
) -> Tuple[Database, StandardOnlyReport]:
    """Build a DBC from the published standard alone, with no recording.

    This is the base layer of the tool and it stands on its own: every parameter
    group whose bit layout the standard publishes becomes a message with real
    signal names, scales, offsets and units, and every one of them is marked
    `standard`.  Nothing here is a guess, and nothing here depends on having
    seen the bus.

    What a recording adds is the other half: which of these groups a particular
    machine actually sends, which ECU sends each one, how often, and -- for the
    groups the standard names but does not lay out, and for proprietary
    identifiers it never mentions at all -- a measured field structure where the
    standard offers none.  Those are additions, not corrections.
    """
    report = StandardOnlyReport()
    namer = _Namer()
    messages: List[Message] = []

    wanted = set(pgns) if pgns is not None else None
    candidates = sorted(standard.pgns)
    if wanted is not None:
        candidates = [p for p in candidates if p in wanted]
    else:
        # Every bus carries the network-layer groups whether or not the export
        # lists them, so they are always offered.
        candidates = sorted(set(candidates) | set(network_layer.NETWORK_LAYER))

    for pgn in candidates:
        pgn_def = standard.get(pgn)
        net = network_layer.info_for(pgn)

        if net is not None:
            dlc, fallback, description = net
            signals = list(network_layer.signals_for(pgn) or [])
            base = _message_base_name(
                decompose(compose(pgn, source_address)), pgn_def, override=fallback
            )
            comment = description
            evidence = from_standard(
                f"j1939-network-layer:PGN{pgn}",
                f"{description}; layout defined by the network-layer part of the standard",
                pgn=pgn,
            )
            report.from_network_layer += 1

        elif pgn_def is None or not pgn_def.has_layout:
            if pgn_def is not None and pgn_def.name_only:
                report.skipped_name_only += 1
            continue

        elif pgn_def.multipacket or (pgn_def.dlc or 8) > MAX_SINGLE_FRAME_DLC:
            report.skipped_multipacket += 1
            continue

        else:
            dlc = min(pgn_def.dlc or MAX_SINGLE_FRAME_DLC, MAX_SINGLE_FRAME_DLC)
            signals, dropped = _standard_signals(pgn_def, dlc, report)
            if not signals:
                continue
            report.dropped_spns.extend(f"PGN {pgn}: {d}" for d in dropped)
            base = _message_base_name(decompose(compose(pgn, source_address)), pgn_def)
            comment = pgn_def.label
            evidence = from_standard(
                f"{pgn_def.document}:PGN{pgn}",
                f"{pgn_def.label}; {len(signals)} signal(s) placed from the published layout",
                pgn=pgn,
            )
            report.from_standard_layout += 1

        report.signals_standard += len(signals)
        priority = (pgn_def.priority if pgn_def is not None else None) or 6
        msg = Message(
            frame_id=compose(pgn, source_address, priority=priority),
            name=namer.unique(base),
            length=max(dlc, 1),
            extended=True,
            comment=comment,
            cycle_time=pgn_def.cycle_ms if pgn_def is not None else None,
            evidence=evidence,
            pgn=pgn,
            source_address=source_address,
        )
        msg.signals = signals
        messages.append(msg)

    report.messages = len(messages)
    db = Database(
        version=version or "generated by dbcgen from the published standard",
        nodes=[],
        messages=messages,
    )
    db.header_notes = [
        "Generated by dbcgen from the published standard alone -- no recording was "
        "read, so every signal here is marked 'standard' and none is inferred.",
        f"standard: {standard.source}",
        f"Source address 0x{source_address:02X} is a placeholder: the standard defines "
        "parameter groups, not which ECU sends them. Match on the PGN, not the whole "
        "29-bit identifier, or regenerate from a recording to get the real addresses.",
    ]
    return db, report
