"""Regenerate the README figure from the public benchmark recording.

    python docs/make_figures.py

Downloads the CANmod.gps log and its reference DBC from
CSS-Electronics/api-examples (MIT) if they are not cached, measures the bit flip
rates, runs the segmenter, and plots both against the published boundaries.

The figure is generated rather than drawn so it cannot drift from the code, and
so a reader can check it.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cantools  # noqa: E402
from figure_style import (  # noqa: E402
    AMBER, EDGE, FAINT, GREEN, INK, MUTED, PANEL, ROSE, TEAL, plt,
)

from benchmarks import synthesize  # noqa: E402
from benchmarks.datasets import DATASETS, fetch  # noqa: E402
from dbcgen.infer import segment  # noqa: E402
from dbcgen.infer.stats import analyse  # noqa: E402
from dbcgen.readers.canlogger_csv import read_canlogger_csv  # noqa: E402

FIGURES = Path(__file__).resolve().parent / "figures"

#: The message drawn.  Its publisher packs a 1-bit flag and three fields of 22,
#: 19 and 22 bits with no byte alignment anywhere, which is the hardest case for
#: a byte-oriented method and the clearest demonstration of a bit-level one.
MESSAGE_ID = 0x016


def load():
    dataset = DATASETS["gnss"]
    paths = fetch(dataset)
    csv_path = dataset.dir / f"{dataset.key}.csv"
    if not csv_path.exists():
        frames = []
        for log in paths["logs"]:
            frames.extend(synthesize.read_any(log))
        frames.sort(key=lambda f: f[0])
        synthesize.write(frames, csv_path, bitrate=dataset.bitrate)
    frameset, _ = read_canlogger_csv(csv_path)
    reference = cantools.database.load_file(str(paths["dbc"]))
    return frameset, reference


def main() -> int:
    frameset, reference = load()
    group = frameset.get(MESSAGE_ID, False)
    stats = analyse(group)
    payloads = [f.data for f in group.frames]

    rates = [stats.flip_rate(i) for i in range(stats.n_bits)]
    fields = segment.segment(stats, payloads)
    message = reference.get_message_by_frame_id(MESSAGE_ID)
    truth = sorted(s.start for s in message.signals)
    found = sorted(f.start_bit for f in fields)

    # Bit 0 is a boundary by definition, so scoring it would flatter the
    # result. Only the interior boundaries are a finding.
    interior_truth = [b for b in truth if b > 0]
    interior_hit = sorted(set(interior_truth) & set(found))

    fig, ax = plt.subplots(figsize=(11.5, 4.2))
    ax.bar(range(stats.n_bits), rates, width=0.78, color=TEAL, edgecolor="none",
           zorder=3, label="how often each bit changed")

    # Headroom before anything is placed, or the legend lands on the bars.
    top = (max(rates) or 1.0) * 2.35
    ax.set_ylim(0, top)

    for i, bit in enumerate(interior_truth):
        ax.axvline(bit - 0.5, color=GREEN, lw=1.7, ls="--", zorder=4,
                   label="published boundary" if i == 0 else None)
    for i, bit in enumerate(b for b in found if b > 0):
        ax.axvline(bit - 0.28, color=AMBER, lw=1.5, ls=":", zorder=5,
                   label="boundary this tool found" if i == 0 else None)

    # Field names sit below the legend band, centred on their own span.
    for start_bit, end_bit, name in (
        (1, 23, "DistanceTrip 1|22"),
        (23, 42, "DistanceAccuracy 23|19"),
        (42, 64, "DistanceTotal 42|22"),
    ):
        ax.annotate(name, xy=((start_bit + end_bit) / 2 - 0.5, top * 0.66),
                    xycoords="data", ha="center", va="center", fontsize=8,
                    color=MUTED, zorder=6)

    # Name the miss on the figure. A figure showing only successes is not
    # evidence, and the reason for this one is the interesting part.
    for bit in sorted(set(interior_truth) - set(found)):
        flips = round(rates[bit] * (stats.count - 1))
        ax.annotate(
            f"missed: bit {bit} changed {flips}x in {stats.count} frames\n"
            f"(rate {rates[bit]:.4f}) -- below the noise floor",
            xy=(bit - 0.5, top * 0.10), xycoords="data",
            xytext=(0.40, 0.44), textcoords="axes fraction",
            ha="left", va="top", fontsize=8, color=ROSE, zorder=7,
            arrowprops=dict(arrowstyle="->", color=ROSE, lw=1.1, shrinkA=0, shrinkB=3),
        )

    ax.annotate(
        "inside one field the low bit changes most and each higher bit less;\n"
        "a rise means the next field has started",
        xy=(0.02, 0.97), xycoords="axes fraction", ha="left", va="top",
        fontsize=8, color=FAINT, zorder=6,
    )

    ax.set_xlabel("payload bit  (DBC little-endian numbering)")
    ax.set_ylabel("flip rate")
    ax.set_title(
        f"gnss_odo — {len(interior_hit)} of {len(interior_truth)} field boundaries "
        f"recovered from bit statistics alone "
        f"({stats.count} frames, no standard data used)"
    )
    ax.set_xlim(-1, stats.n_bits)
    ax.set_xticks(range(0, stats.n_bits + 1, 8))
    ax.legend(loc="upper right", fontsize=8, framealpha=0.97)

    FIGURES.mkdir(parents=True, exist_ok=True)
    out = FIGURES / "segmentation.png"
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)

    print(f"wrote {out}")
    print(f"  published boundaries : {truth}")
    print(f"  boundaries found     : {found}")
    print(f"  matched (interior)   : {interior_hit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
