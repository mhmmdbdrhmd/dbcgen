"""Command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from .emit import dbc as dbc_writer
from .generate import generate, generate_from_standard
from .infer.stats import MessageStats, analyse_all
from .j1939.ids import decompose, pgn_name_hint
from .sources import open_recording
from .standard import fetch as standard_fetch
from .standard import isobus, tables as identity_tables
from .standard import overlay as overlay_mod
from .standard import registry


def _classify(st: MessageStats) -> str:
    if not st.extended:
        return "11-bit"
    j = decompose(st.can_id)
    if j.is_transport:
        return "transport"
    if j.is_proprietary:
        return "proprietary"
    return "PDU1" if j.is_pdu1 else "PDU2"


def _row(st: MessageStats) -> dict:
    row = {
        "can_id": st.can_id,
        "hex": f"{st.can_id:08X}" if st.extended else f"{st.can_id:03X}",
        "extended": st.extended,
        "kind": _classify(st),
        "count": st.count,
        "dlc": st.dlc,
        "dlcs": sorted(st.dlcs),
        "cycle_ms": round(st.cycle_ms, 2) if st.cycle_ms is not None else None,
        "cyclic": st.is_cyclic,
        "cyclic_fraction": round(st.cyclic_fraction, 3),
        "distinct_payloads": st.distinct_payloads,
        "constant": st.is_constant,
        "constant_bytes": st.constant_bytes(),
        "active_bits": len(st.active_bits()),
    }
    if st.extended:
        j = decompose(st.can_id)
        row.update(
            pgn=j.pgn,
            pf=j.pf,
            ps=j.ps,
            sa=j.sa,
            priority=j.priority,
            destination=j.destination,
            name_hint=pgn_name_hint(j),
        )
    return row


def cmd_inspect(args: argparse.Namespace) -> int:
    fs, meta = open_recording(args.recording)
    stats = analyse_all(fs)
    rows = [_row(stats[g.key]) for g in fs]

    if args.json:
        json.dump(
            {
                "source": meta.source,
                "bitrate": meta.bitrate,
                "frame_map": meta.frame_map,
                "rows_total": meta.rows_total,
                "frames": fs.total,
                "ids": len(fs),
                "duration_us": fs.duration_us,
                "lossy": meta.lossy,
                "messages": rows,
            },
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
        return 0

    dur_s = fs.duration_us / 1e6
    print(f"recording : {meta.source}")
    if meta.bitrate:
        print(f"bus       : {meta.bitrate}")
    print(f"frames    : {fs.total} across {len(fs)} ids, {dur_s:.1f} s")
    print(f"parsed    : {meta.summary()}")
    if meta.lossy:
        n = len(meta.ids_decoded_only - meta.ids_payload_only)
        print(
            f"WARNING   : recorded with frame map {meta.frame_map or '(unnamed)'} -- "
            f"{meta.rows_decoded_only} rows carry a decoded value but no payload, "
            f"and {n} id(s) have no recoverable bytes at all. "
            f"Re-record without a frame map for full inference."
        )
    print()

    hdr = (
        f"{'ID':>8} {'kind':<11} {'n':>7} {'rate/s':>7} {'dlc':>3} "
        f"{'cycle_ms':>8} {'cyc':>3} {'PGN':>6} {'SA':>3} {'dst':>3} "
        f"{'distinct':>8} {'actbits':>7}  name_hint"
    )
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        rate = r["count"] / dur_s if dur_s > 0 else 0.0
        cycle = f"{r['cycle_ms']:.1f}" if r["cycle_ms"] is not None else "-"
        cyc = "y" if r["cyclic"] else "-"
        pgn = str(r["pgn"]) if "pgn" in r else "-"
        sa = f"{r['sa']:02X}" if "sa" in r else "-"
        dst = f"{r['destination']:02X}" if r.get("destination") is not None else "-"
        print(
            f"{r['hex']:>8} {r['kind']:<11} {r['count']:7d} {rate:7.1f} {r['dlc']:3d} "
            f"{cycle:>8} {cyc:>3} {pgn:>6} {sa:>3} {dst:>3} "
            f"{r['distinct_payloads']:8d} {r['active_bits']:7d}  {r.get('name_hint', '')}"
        )

    kinds: dict = {}
    for r in rows:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    print()
    print("by kind   : " + ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())))
    const = sum(1 for r in rows if r["constant"])
    print(f"constant  : {const} id(s) never changed payload")
    return 0


def cmd_fetch_standard(args: argparse.Namespace) -> int:
    info = standard_fetch.ensure(refresh=args.refresh)
    db = isobus.load(info)
    print(f"cached  : {info.path}")
    print(f"source  : {info.url}")
    print(f"sha256  : {info.sha256}")
    print(f"size    : {info.size} bytes")
    print(f"fetched : {info.fetched_at}")
    if info.last_modified:
        print(f"upstream: {info.last_modified}")
    st = db.stats
    print()
    print(f"PGNs             : {st['pgns']}")
    print(f"  with layout    : {st['pgns_with_layout']}  (bit positions published)")
    print(f"  name only      : {st['pgns_name_only']}  (layout in the paid SAE Digital Annex)")
    print(f"SPNs             : {st['spns']}")
    print(f"  with layout    : {st['spns_with_layout']}")
    return 0


def _parse_pgns(values) -> list:
    """Accept PGNs as decimal or 0x-prefixed hex, comma- or space-separated."""
    out = []
    for chunk in values or []:
        for part in chunk.replace(",", " ").split():
            out.append(int(part, 0))
    return out


def cmd_sources(args: argparse.Namespace) -> int:
    """List the standards that can be built without the user finding anything."""
    if args.list:
        source = registry.get(args.list)
        if source.name != "opendbc":
            print(f"{source.name} has no variants to list", file=sys.stderr)
            return 2
        from .standard import opendbc

        rows = opendbc.platforms(refresh=args.refresh)
        print(f"{len(rows)} platform(s) in opendbc:\n")
        for name, size in rows:
            print(f"  opendbc:{name:<44} {size / 1024:7.0f} KB")
        print("\nEach describes particular vehicles, not a protocol.")
        return 0

    print("Standards dbcgen can build from. Nothing here is stored in this")
    print("repository -- each is fetched once and cached, or encoded from public")
    print("definitions, so licensed material is never redistributed.\n")
    for source in registry.SOURCES:
        cached = "" if source.needs_network else "  (no download needed)"
        print(f"  {source.label}")
        print(f"      {source.title}  --  signals marked '{source.origin}'{cached}")
        for line in _wrap(source.summary, 68):
            print(f"      {line}")
        print(f"      licence: {source.licence}")
        print()
    print("Use one with:   dbcgen generate --source <name> -o out.dbc")
    print("Your own file:  dbcgen generate --standard-file mine.dbc rec.csv -o out.dbc")
    return 0


def _wrap(text: str, width: int):
    words, line, out = text.split(), "", []
    for w in words:
        if len(line) + len(w) + 1 > width:
            out.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        out.append(line)
    return out


def cmd_generate(args: argparse.Namespace) -> int:
    if args.source and registry.get(args.source).name != registry.DEFAULT:
        if args.recording is not None:
            print(
                f"--source {args.source} builds from that database alone; it does "
                f"not read a recording. Drop the recording, or use --source isobus "
                f"(the default), which does.",
                file=sys.stderr,
            )
            return 2
        return _generate_from_source(args)

    if args.recording is None:
        return _generate_standard_only(args)

    fs, meta = open_recording(args.recording)
    if fs.total == 0:
        print(f"no frames with recoverable payloads in {args.recording}", file=sys.stderr)
        return 2

    info = standard_fetch.ensure(refresh=args.refresh_standard)
    std = isobus.load(info)
    tbl = identity_tables.load(info)

    overlay = overlay_mod.load(args.standard_file) if args.standard_file else None
    if overlay is not None:
        print(f"overlay: {len(overlay)} message(s) from {args.standard_file}")

    db, report, nodes = generate(
        fs, std, tbl, meta=meta, version=args.version, overlay=overlay
    )
    dbc_writer.dump(db, args.output)

    print(f"wrote {args.output}")
    print(f"  messages          : {report.messages}")
    if report.from_overlay:
        print(f"    your database   : {report.from_overlay}")
        if report.overlay_signals_dropped:
            print(
                f"      WARNING       : {report.overlay_signals_dropped} signal(s) "
                f"from your database did not fit the frames actually recorded"
            )
    print(f"    network layer   : {report.from_network_layer}")
    print(f"    standard layout : {report.from_standard_layout}")
    print(f"    named only      : {report.named_only}")
    print(f"    unknown         : {report.unknown}")
    print(f"  signals           : {report.signals_standard} from the standard, "
          f"{report.signals_inferred} inferred from the recording")
    print(f"  nodes             : {len(db.nodes)}")
    if report.dropped_spns:
        print(f"  dropped SPNs      : {len(report.dropped_spns)}")
        for line in report.dropped_spns[:5]:
            print(f"      {line}")

    if args.verify:
        return _verify(args.output, fs)
    return 0


def _reload_count(path: str):
    """How many messages the written file parses back to, or None if cantools
    is not installed."""
    try:
        import cantools
    except ImportError:
        return None
    return len(cantools.database.load_file(path).messages)


def _generate_from_source(args: argparse.Namespace) -> int:
    """Build from one of the catalogued databases that is not the ISOBUS export."""
    source = registry.get(args.source)
    try:
        db = registry.build(args.source, refresh=args.refresh_standard)
    except KeyError as exc:
        print(str(exc).strip("'\""), file=sys.stderr)
        return 2

    if not db.messages:
        print(f"{args.source} produced no messages", file=sys.stderr)
        return 2

    dbc_writer.dump(db, args.output)
    signals = sum(len(m.signals) for m in db.messages)

    # A database that cannot be read back is not a database.  The recording
    # path has always reloaded its own output; a catalogued source is exactly
    # as capable of emitting something malformed, and more likely to, because
    # its input is somebody else's file in somebody else's format.
    reloaded = _reload_count(args.output)
    if reloaded is not None and reloaded != len(db.messages):
        print(
            f"wrote {args.output}, but it reloads as {reloaded} of "
            f"{len(db.messages)} messages",
            file=sys.stderr,
        )
        return 1

    print(f"wrote {args.output}  (from {source.title}, no recording read)")
    print(f"  messages          : {len(db.messages)}")
    print(f"  signals           : {signals}, all marked '{source.origin}'")
    print(f"  licence           : {source.licence}")
    print(f"  attribution       : {source.attribution}")
    if source.origin == "community":
        print()
        print("These definitions were reconstructed by other people, not published")
        print("by whoever owns the protocol. Check anything that matters against")
        print("the machine in front of you.")
    return 0


def _generate_standard_only(args: argparse.Namespace) -> int:
    """Build a DBC with no recording at all, from the standard by itself."""
    info = standard_fetch.ensure(refresh=args.refresh_standard)
    std = isobus.load(info)

    db, report = generate_from_standard(
        std,
        pgns=_parse_pgns(args.pgn) or None,
        source_address=int(args.source_address, 0),
        version=args.version,
    )
    if not db.messages:
        print(
            "no parameter group matched -- the standard publishes a bit layout for "
            f"{std.stats['pgns_with_layout']} of its {std.stats['pgns']} PGNs, and "
            "none of the requested ones is among them",
            file=sys.stderr,
        )
        return 2

    dbc_writer.dump(db, args.output)
    print(f"wrote {args.output}  (no recording read)")
    print(f"  messages          : {report.messages}")
    if report.from_overlay:
        print(f"    your database   : {report.from_overlay}")
        if report.overlay_signals_dropped:
            print(
                f"      WARNING       : {report.overlay_signals_dropped} signal(s) "
                f"from your database did not fit the frames actually recorded"
            )
    print(f"    network layer   : {report.from_network_layer}")
    print(f"    standard layout : {report.from_standard_layout}")
    print(f"  signals           : {report.signals_standard}, all from the standard")
    print(f"  skipped           : {report.skipped_name_only} PGN(s) the standard names "
          f"but does not lay out, {report.skipped_multipacket} multi-frame")
    print()
    print("Source address is a placeholder. Match on the PGN rather than the whole")
    print("29-bit id, or pass a recording to resolve the real addresses and to")
    print("recover structure for the groups above that were skipped.")
    return 0


def _verify(dbc_path: str, fs) -> int:
    """Reload the generated file and decode every recorded frame with it.

    A DBC that parses but cannot decode its own source recording is not done,
    so anything short of a clean pass is a failure exit code.
    """
    try:
        import cantools
    except ImportError:
        print("cantools not installed; skipping verification", file=sys.stderr)
        return 0

    loaded = cantools.database.load_file(dbc_path)
    by_id = {}
    for m in loaded.messages:
        by_id[(m.frame_id, bool(m.is_extended_frame))] = m

    decoded = failed = missing = 0
    errors = {}
    for group in fs:
        m = by_id.get((group.can_id, group.extended))
        if m is None:
            missing += len(group)
            continue
        for frame in group.frames:
            try:
                m.decode(frame.data.ljust(m.length, b"\x00")[: m.length])
                decoded += 1
            except Exception as exc:
                failed += 1
                errors.setdefault(type(exc).__name__ + ": " + str(exc)[:90], 0)
                errors[type(exc).__name__ + ": " + str(exc)[:90]] += 1

    total = fs.total
    print()
    print(f"verify: {decoded}/{total} frames decoded"
          f"{f', {missing} with no message' if missing else ''}"
          f"{f', {failed} failed' if failed else ''}")
    for text, n in sorted(errors.items(), key=lambda kv: -kv[1])[:5]:
        print(f"    {n:6d}x {text}")
    ok = decoded == total and failed == 0 and missing == 0
    print("verify: PASS" if ok else "verify: FAIL")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dbcgen",
        description="Generate a Vector DBC from the published J1939/ISOBUS standard, "
        "refined by a CAN recording when one is available.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    ins = sub.add_parser(
        "inspect", help="report what is on the bus, without generating a DBC"
    )
    ins.add_argument(
        "recording",
        help="can-logger-esp32 CSV, or archive.zip::member.csv",
    )
    ins.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    ins.set_defaults(func=cmd_inspect)

    fetch = sub.add_parser(
        "fetch-standard", help="download and cache the public ISOBUS/J1939 export"
    )
    fetch.add_argument(
        "--refresh", action="store_true", help="re-download even if already cached"
    )
    fetch.set_defaults(func=cmd_fetch_standard)

    src = sub.add_parser(
        "sources",
        help="list the standards dbcgen can build from",
        description="Every standard available without you having to find one. "
        "None is stored in this repository: each is fetched once and cached, or "
        "encoded from public definitions.",
    )
    src.add_argument(
        "--list",
        metavar="SOURCE",
        help="list the variants of a source that has them (currently: opendbc)",
    )
    src.add_argument(
        "--refresh", action="store_true", help="re-download the variant index"
    )
    src.set_defaults(func=cmd_sources)

    gen = sub.add_parser(
        "generate",
        help="generate a DBC from the standard, and from a recording if given",
        description="With no recording, every parameter group the standard lays out "
        "is emitted from the standard alone. With a recording, the output is "
        "narrowed to what that bus actually carries, source addresses and cycle "
        "times become real, and field structure is inferred for the groups the "
        "standard does not lay out.",
    )
    gen.add_argument(
        "recording",
        nargs="?",
        default=None,
        help="optional: can-logger-esp32 CSV, HUD ECU Hacker HTML, or "
        "archive.zip::member. Omit to build from the standard alone.",
    )
    gen.add_argument("-o", "--output", required=True, help="path to write the .dbc to")
    gen.add_argument(
        "--source",
        default=registry.DEFAULT,
        metavar="NAME",
        help="which standard to build from: "
        + ", ".join(x.label for x in registry.SOURCES)
        + f" (default {registry.DEFAULT}). Run 'dbcgen sources' for what each is.",
    )
    gen.add_argument(
        "--standard-file",
        metavar="PATH",
        help="a DBC you already own -- a licensed Digital Annex converted to DBC, "
        "or any database you trust -- used as the authoritative layer ahead of "
        "inference. Read where it lies; never copied, cached or committed.",
    )
    gen.add_argument(
        "--pgn",
        action="append",
        metavar="N",
        help="with no recording, restrict output to these PGNs "
        "(decimal or 0x hex, repeatable, comma-separated)",
    )
    gen.add_argument(
        "--source-address",
        default="0xFE",
        metavar="SA",
        help="with no recording, the placeholder source address to build ids "
        "from (default 0xFE, the J1939 null address)",
    )
    gen.add_argument("--version", default="", help="VERSION string for the DBC")
    gen.add_argument(
        "--refresh-standard", action="store_true", help="re-download the standard export"
    )
    gen.add_argument(
        "--no-verify",
        dest="verify",
        action="store_false",
        default=True,
        help="skip reloading the result and decoding the recording with it",
    )
    gen.set_defaults(func=cmd_generate)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyError as exc:
        # Raised by the source registry for a name it does not know, and by
        # opendbc when a platform was not chosen.  Both are user errors with
        # something useful to say, so they are worth a line rather than a stack.
        print(str(exc).strip("'\""), file=sys.stderr)
        return 2
    except standard_fetch.StandardDataError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (FileNotFoundError, IsADirectoryError) as exc:
        print(f"cannot read {exc.filename}: {exc.strerror}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
