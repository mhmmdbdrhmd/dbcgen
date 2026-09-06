"""Shared figure style. Copy into docs/ and import from every figure script.

One palette across every repository is the cheapest way to make separate projects
read as a single body of work. It costs nothing and a reader notices immediately.

The palette is GitHub's own dark UI, so figures sit naturally in a README viewed
in dark mode and remain legible in light mode.

Usage
-----
    from figure_style import *          # applies rcParams on import
    fig, ax = plt.subplots()
    ax.plot(x, y, color=TEAL)
"""

import matplotlib
matplotlib.use("Agg")            # never require a display; CI has none
import matplotlib.pyplot as plt

# GitHub dark surfaces
BG, PANEL, EDGE = "#0D1117", "#161B22", "#30363D"
# Text, in three weights of emphasis
INK, MUTED, FAINT = "#E6EDF3", "#8B949E", "#6E7681"
# Accents. Keep the meaning stable across a project: ROSE = the expensive error,
# TEAL = the thing being measured, AMBER = the alternative, GREEN = ground truth.
TEAL, AMBER, ROSE = "#2DD4BF", "#F59E0B", "#FB7185"
VIOLET, BLUE, GREEN = "#A78BFA", "#58A6FF", "#3FB950"

plt.rcParams.update({
    "figure.facecolor": BG, "axes.facecolor": PANEL, "savefig.facecolor": BG,
    "axes.edgecolor": EDGE, "axes.labelcolor": INK, "text.color": INK,
    "xtick.color": MUTED, "ytick.color": MUTED,
    "grid.color": EDGE, "axes.grid": True, "grid.alpha": 0.45,
    "font.family": "monospace", "font.size": 9,
    "axes.titlesize": 10, "axes.titleweight": "bold",
    "legend.facecolor": PANEL, "legend.edgecolor": EDGE, "legend.framealpha": 0.95,
    "figure.dpi": 130,
})


def shade(ax, t, mask, colour, alpha, label=None):
    """Shade the spans where ``mask`` is true, as ONE legend entry.

    Spans rather than lines because what a reader is asking is "how much of this
    stretch", and a block answers that at a glance where boundaries do not.
    """
    import numpy as np
    if not np.any(mask):
        return
    edges = np.diff(mask.astype(np.int8))
    starts = list(np.flatnonzero(edges == 1) + 1)
    stops = list(np.flatnonzero(edges == -1) + 1)
    if mask[0]:
        starts = [0] + starts
    if mask[-1]:
        stops = stops + [len(mask) - 1]
    for i, (a, b) in enumerate(zip(starts, stops)):
        ax.axvspan(t[a], t[b], color=colour, alpha=alpha, lw=0,
                   label=label if i == 0 else None)


# --------------------------------------------------------------------------- #
# Rules that only get learned by shipping a wrong figure
# --------------------------------------------------------------------------- #
# 1. Position annotation text in AXES FRACTIONS, never data coordinates. Data
#    coordinates fly off-canvas when the data changes and matplotlib silently
#    widens the figure to fit.
#       ax.annotate(txt, xy=(x, y), xycoords="data",
#                   xytext=(0.6, 0.2), textcoords="axes fraction")
#
# 2. On a sequential colormap (Blues, viridis), LOW values are PALE. Pale cells
#    need DARK text. Getting it backwards makes the small cells -- the errors,
#    the ones that matter -- invisible:
#       pale = value < vmax * 0.55
#       color = "#0D1117" if pale else INK
#
# 3. Leave headroom before placing a legend, or it lands on the trace:
#       ax.set_ylim(lo, data.max() * 1.4)
#
# 4. A suptitle and an axes title collide once bbox_inches="tight" pulls the
#    layout in. Use one two-line suptitle instead of both.
#
# 5. Route connectors around boxes, not through them. An elbow beats a diagonal:
#       path: M x0 y0  H bypass_x  V y1  H x1
#
# 6. GENERATE, RENDER, then LOOK. Every rule above came from opening a PNG and
#    seeing something wrong that no amount of reading the code would have shown.
