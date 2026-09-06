"""Run the ground-truth benchmarks end to end.

    python -m benchmarks.run              # every dataset
    python -m benchmarks.run gnss         # one
    python -m benchmarks.run --json out.json

For each dataset: download the log and its reference DBC, replay the log into
can-logger-esp32 CSV, generate a DBC from that CSV alone, then score the result
against the reference.  The generator never sees the reference.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import cantools  # noqa: E402

from dbcgen.emit import dbc as writer  # noqa: E402
from dbcgen.generate import generate  # noqa: E402
from dbcgen.readers.canlogger_csv import read_canlogger_csv  # noqa: E402
from dbcgen.standard import fetch as standard_fetch  # noqa: E402
from dbcgen.standard import isobus  # noqa: E402
from dbcgen.standard import tables as identity_tables  # noqa: E402

from . import synthesize  # noqa: E402
from .datasets import DATASETS, LICENCE, Dataset, fetch  # noqa: E402
from .score import Score, score  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "out"


def run_one(dataset: Dataset, standard, tables, verbose: bool = False) -> Optional[Score]:
    paths = fetch(dataset)
    reference = cantools.database.load_file(str(paths["dbc"]))

    # Replay every log of this dataset into one recording, so the generator
    # sees as much of the bus as the publisher recorded.
    csv_path = dataset.dir / f"{dataset.key}.csv"
    frames: List[tuple] = []
    for log in paths["logs"]:
        frames.extend(synthesize.read_any(log))
    frames.sort(key=lambda f: f[0])
    synthesize.write(
        frames, csv_path, source=", ".join(p.name for p in paths["logs"]), bitrate=dataset.bitrate
    )

    frameset, meta = read_canlogger_csv(csv_path)
    if frameset.total == 0:
        print(f"{dataset.key}: no frames decoded from the source logs", file=sys.stderr)
        return None

    db, report, _ = generate(
        frameset, standard, tables, meta=meta, version=f"benchmark {dataset.key}"
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    generated_path = OUT_DIR / f"{dataset.key}.dbc"
    writer.dump(db, generated_path)
    generated = cantools.database.load_file(str(generated_path))

    result = score(reference, generated, frameset, dataset=dataset.key)

    print("=" * 72)
    print(f"{dataset.key}: {dataset.description}")
    print(f"  reference : {paths['dbc'].name}  [{LICENCE}]")
    print(f"  generated : {generated_path}")
    print(f"  ground truth: {dataset.ground_truth}")
    print("-" * 72)
    print(result.report())
    print("-" * 72)
    print(
        f"generator: {report.from_standard_layout} message(s) from the published layout, "
        f"{report.from_network_layer} network layer, {report.named_only} named only, "
        f"{report.unknown} unknown"
    )
    if verbose:
        print()
        print(f"{'ID':>9} {'reference':<26} {'generated':<26} {'sig':>4} {'exact':>5} {'val':>4}")
        for m in result.per_message:
            print(
                f"{m.hex:>9} {m.name_reference[:26]:<26} {m.name_generated[:26]:<26} "
                f"{m.reference_signals:4d} {m.exact_matches:5d} {m.value_recoverable:4d}"
            )
    print()
    return result


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "datasets",
        nargs="*",
        default=[],
        help=f"datasets to run (default: all). One or more of: {', '.join(DATASETS)}",
    )
    parser.add_argument("--json", help="write the scores to this path")
    parser.add_argument("-v", "--verbose", action="store_true", help="per-message breakdown")
    args = parser.parse_args(argv)

    # Validated here rather than with argparse `choices`, which in 3.10 also
    # checks the empty default and so rejects "run everything".
    unknown = [k for k in args.datasets if k not in DATASETS]
    if unknown:
        parser.error(f"unknown dataset(s): {', '.join(unknown)}. Choose from: {', '.join(DATASETS)}")

    keys = args.datasets or list(DATASETS)
    info = standard_fetch.ensure()
    standard = isobus.load(info)
    tables = identity_tables.load(info)

    results = []
    for key in keys:
        result = run_one(DATASETS[key], standard, tables, verbose=args.verbose)
        if result is not None:
            results.append(result)

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {"standard": info.short(), "results": [r.to_dict() for r in results]}, indent=2
            )
        )
        print(f"wrote {args.json}")
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
