"""Generate the workflow diagrams in the README.

Two figures, each written twice so GitHub can pick one per colour scheme:

    docs/figures/workflow-light.svg   docs/figures/workflow-dark.svg
    docs/figures/inputs-light.svg     docs/figures/inputs-dark.svg

Hand-built SVG rather than a plotting library: these are box-and-arrow
diagrams, not plots, and the text has to stay crisp at any zoom.

    python docs/make_workflow.py
"""

from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).resolve().parent / "figures"

FONT = (
    "ui-sans-serif,-apple-system,BlinkMacSystemFont,'Segoe UI',"
    "Helvetica,Arial,sans-serif"
)
MONO = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,monospace"

#: Accents are shared by both themes; only the neutrals change, which is what
#: keeps a single legend true of both files.
ACCENT = {
    "standard": "#0D9488",   # published, authoritative
    "inferred": "#C2410C",   # measured from traffic
    "hardware": "#1F6FEB",   # the recorder
    "output": "#6E40C9",     # the database itself
}
ACCENT_DARK = {
    "standard": "#2DD4BF",
    "inferred": "#F0883E",
    "hardware": "#58A6FF",
    "output": "#A371F7",
}

THEMES = {
    "light": dict(
        ink="#1F2328", muted="#59636E", faint="#818B98",
        line="#D1D9E0", panel="#F6F8FA", panel2="#EEF1F4",
        bg="#FFFFFF", accent=ACCENT,
    ),
    "dark": dict(
        ink="#E6EDF3", muted="#9198A1", faint="#6E7681",
        line="#3D444D", panel="#151B23", panel2="#1C2129",
        bg="#0D1117", accent=ACCENT_DARK,
    ),
}


# --------------------------------------------------------------------------
# primitives


def esc(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


def box(x, y, w, h, t, *, fill=None, stroke=None, dash=False, r=10, width=1.5):
    fill = fill or t["panel"]
    stroke = stroke or t["line"]
    d = ' stroke-dasharray="6 5"' if dash else ""
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" ry="{r}" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="{width}"{d}/>'
    )


def text(x, y, s, *, fill, size=13, weight=400, anchor="start", mono=False,
         spacing=None, opacity=1.0):
    family = MONO if mono else FONT
    ls = f' letter-spacing="{spacing}"' if spacing else ""
    op = f' opacity="{opacity}"' if opacity != 1.0 else ""
    return (
        f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{ls}{op}>'
        f"{esc(s)}</text>"
    )


def arrow(x1, y1, x2, y2, t, *, colour=None, width=2, dash=False, marker="head"):
    colour = colour or t["muted"]
    d = ' stroke-dasharray="6 5"' if dash else ""
    return (
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{colour}" '
        f'stroke-width="{width}" stroke-linecap="round"{d} '
        f'marker-end="url(#{marker})"/>'
    )


def path(d, t, *, colour=None, width=2, dash=False, marker="head"):
    colour = colour or t["muted"]
    da = ' stroke-dasharray="6 5"' if dash else ""
    mk = f' marker-end="url(#{marker})"' if marker else ""
    return (
        f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="{width}" '
        f'stroke-linecap="round" stroke-linejoin="round"{da}{mk}/>'
    )


def header(w, h, t):
    defs = "".join(
        f'<marker id="{name}" viewBox="0 0 10 10" refX="9" refY="5" '
        f'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{colour}"/></marker>'
        for name, colour in (
            ("head", t["muted"]),
            ("head-std", t["accent"]["standard"]),
            ("head-inf", t["accent"]["inferred"]),
            ("head-out", t["accent"]["output"]),
            ("head-hw", t["accent"]["hardware"]),
        )
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" role="img">'
        f"<defs>{defs}</defs>"
        f'<rect width="{w}" height="{h}" fill="{t["bg"]}"/>'
    )


def label(x, y, s, t, colour_key):
    """A small uppercase stage label in an accent colour."""
    return text(x, y, s, fill=t["accent"][colour_key], size=10.5, weight=700,
                spacing="1.4")


# --------------------------------------------------------------------------
# figure 1 -- the loop


W1, H1 = 1000, 570


def workflow(t) -> str:
    a = t["accent"]
    o = [header(W1, H1, t)]
    o.append(text(W1 / 2, 30, "How a database gets made, and what it is for",
                  fill=t["ink"], size=16, weight=600, anchor="middle"))

    # --- stage 1: the two inputs -----------------------------------------
    o.append(box(30, 62, 292, 118, t, stroke=a["standard"]))
    o.append(label(50, 86, "ALWAYS AVAILABLE", t, "standard"))
    o.append(text(50, 110, "1  The published standard", fill=t["ink"], size=14.5,
                  weight=600))
    o.append(text(50, 132, "isobus.net export, ~320 KB, no", fill=t["muted"], size=12))
    o.append(text(50, 148, "account and no licence needed.", fill=t["muted"], size=12))
    o.append(text(50, 169, "dbcgen fetch-standard", fill=a["standard"], size=11.5,
                  mono=True))

    o.append(box(30, 200, 292, 152, t, stroke=a["hardware"], dash=True))
    o.append(label(50, 224, "OPTIONAL — AND WHERE", t, "hardware"))
    o.append(label(50, 238, "THE REAL GAINS ARE", t, "hardware"))
    o.append(text(50, 262, "2  A recording of your bus", fill=t["ink"], size=14.5,
                  weight=600))
    o.append(text(50, 284, "can-logger-esp32 with NO frame", fill=t["muted"], size=12))
    o.append(text(50, 300, "map on the card, so every frame", fill=t["muted"], size=12))
    o.append(text(50, 316, "keeps its raw payload bytes.", fill=t["muted"], size=12))
    o.append(text(50, 338, "t_us;0xID;;;;;HEX", fill=a["hardware"], size=11.5,
                  mono=True))

    # --- stage 2: the generator ------------------------------------------
    gx, gy, gw, gh = 372, 62, 282, 290
    o.append(box(gx, gy, gw, gh, t, fill=t["panel2"], stroke=t["line"]))
    o.append(text(gx + gw / 2, 88, "3  dbcgen generate", fill=t["ink"], size=14.5,
                  weight=600, anchor="middle"))
    o.append(text(gx + gw / 2, 107, "four layers, highest authority first",
                  fill=t["faint"], size=11, anchor="middle"))

    layers = [
        ("Network layer", "J1939-21 / -73 / -81", "standard", "standard"),
        ("Published layout", "ISO 11783 bit positions", "standard", "standard"),
        ("Flip-rate inference", "field edges out of the bytes", "inferred", "recorded"),
        ("Raw byte passthrough", "no byte on the bus is lost", "inferred", "recorded"),
    ]
    ly = 124
    for name, sub, key, tag in layers:
        always = tag == "standard"
        o.append(box(gx + 16, ly, gw - 32, 48, t, fill=t["panel"],
                     stroke=a[key], dash=not always, r=7, width=1.2))
        o.append(text(gx + 28, ly + 20, name, fill=t["ink"], size=12.5, weight=600))
        o.append(text(gx + 28, ly + 37, sub, fill=t["muted"], size=10.5))
        o.append(text(gx + gw - 28, ly + 20, tag, fill=a[key], size=9.5,
                      weight=700, anchor="end"))
        ly += 56

    o.append(arrow(322, 121, gx - 8, 150, t, colour=a["standard"], marker="head-std"))
    o.append(arrow(322, 285, gx - 8, 264, t, colour=a["hardware"], dash=True,
                   marker="head-hw"))

    # --- stage 3: the database -------------------------------------------
    dx, dy, dw, dh = 690, 118, 280, 152
    o.append(box(dx, dy, dw, dh, t, stroke=a["output"], width=2))
    o.append(label(dx + 20, dy + 24, "THE DELIVERABLE", t, "output"))
    o.append(text(dx + 20, dy + 50, "4  bus.dbc", fill=t["ink"], size=15, weight=600))
    o.append(text(dx + 20, dy + 74, "Every signal names its own origin,", fill=t["muted"],
                  size=12))
    o.append(text(dx + 20, dy + 90, "so a documented definition is never", fill=t["muted"],
                  size=12))
    o.append(text(dx + 20, dy + 106, "mistaken for a measured guess.", fill=t["muted"],
                  size=12))
    o.append(text(dx + 20, dy + 130, "reloaded, then re-decoded", fill=a["output"],
                  size=10.5, mono=True))
    o.append(arrow(gx + gw + 8, 194, dx - 10, 194, t, colour=a["output"],
                   marker="head-out"))

    # --- stage 4: consumers ----------------------------------------------
    o.append(text(dx, 312, "5  Point a tool at it", fill=t["ink"], size=13.5,
                  weight=600))
    for i, (name, note) in enumerate((
        ("can-logger-esp32", "copy to the SD card as /frames.dbc"),
        ("SavvyCAN / CANalyzer", "open a trace with real signal names"),
        ("cantools / pandas", "decode a log into a dataframe"),
    )):
        yy = 330 + i * 50
        o.append(box(dx, yy, dw, 42, t, fill=t["panel"], r=7, width=1.2))
        o.append(text(dx + 14, yy + 18, name, fill=t["ink"], size=11.5, mono=True))
        o.append(text(dx + 14, yy + 33, note, fill=t["faint"], size=9.5))
    o.append(path(f"M {dx + dw / 2} {dy + dh} L {dx + dw / 2} 322", t,
                  colour=a["output"], marker="head-out"))

    # --- the loop back ----------------------------------------------------
    o.append(path("M 690 351 C 600 351, 600 512, 470 512 L 176 512 L 176 360", t,
                  colour=a["hardware"], dash=True, marker="head-hw"))
    o.append(text(206, 470, "The logger now writes named signals in engineering units,",
                  fill=t["faint"], size=11))
    o.append(text(206, 486, "and the next recording is better evidence than the last.",
                  fill=t["faint"], size=11))

    o.append("</svg>")
    return "".join(o)


# --------------------------------------------------------------------------
# figure 2 -- what the recording buys you


W2, H2 = 1000, 470


def inputs(t) -> str:
    a = t["accent"]
    o = [header(W2, H2, t)]
    o.append(text(W2 / 2, 30, "What you get with the standard alone, "
                  "and what a recording adds",
                  fill=t["ink"], size=16, weight=600, anchor="middle"))

    cols = [
        (40, 450, "Standard only", "dbcgen generate -o bus.dbc", "standard",
         "No hardware, no bus, no machine. Run it on a train."),
        (510, 450, "Standard + recording", "dbcgen generate rec.csv -o bus.dbc",
         "inferred", "Everything on the left, plus everything below."),
    ]
    for x, w, title, cmd, key, blurb in cols:
        o.append(box(x, 56, w, 92, t, stroke=a[key], width=2))
        o.append(text(x + 22, 84, title, fill=t["ink"], size=15, weight=600))
        o.append(text(x + 22, 108, cmd, fill=a[key], size=11.5, mono=True))
        o.append(text(x + 22, 132, blurb, fill=t["muted"], size=11.5))

    left = [
        ("100 messages, 612 signals",
         "every group whose layout the standard publishes"),
        ("Real names, scales, units",
         "EstimatedCurvature, 0.25 km-1/bit, offset -8032"),
        ("Address claim, request, transport, DM1",
         "the network-layer groups every J1939 bus carries"),
        ("Nothing is guessed",
         "every signal is marked standard; none is inferred"),
    ]
    right = [
        ("Only what your machine sends",
         "the ids that machine sends, not 3213 in the registry"),
        ("Real source addresses and senders",
         "read out of the 64-bit NAME in each address claim"),
        ("Measured cycle times",
         "median inter-arrival, suppressed for event traffic"),
        ("Structure where the standard has none",
         "name-only groups and proprietary ids, from flip rates"),
        ("Which signals carry data at all",
         "a field stuck at the not-available code is visible"),
    ]

    for x, items, key in ((40, left, "standard"), (510, right, "inferred")):
        y = 170
        for name, note in items:
            o.append(f'<circle cx="{x + 30}" cy="{y + 12}" r="4.5" '
                     f'fill="{a[key]}"/>')
            o.append(text(x + 48, y + 10, name, fill=t["ink"], size=12.5, weight=600))
            o.append(text(x + 48, y + 27, note, fill=t["muted"], size=11))
            y += 46

    # the honest footer
    o.append(box(40, 396, 920, 54, t, fill=t["panel"], r=8, width=1.2))
    o.append(text(62, 420,
                  "A signal that never moved during the recording cannot be located by "
                  "any method, by this tool or any other.",
                  fill=t["muted"], size=11.5))
    o.append(text(62, 438,
                  "That is why every accuracy figure here is quoted twice: against all "
                  "reference signals, and against the ones that actually moved.",
                  fill=t["muted"], size=11.5))

    o.append("</svg>")
    return "".join(o)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in (("workflow", workflow), ("inputs", inputs)):
        for theme, palette in THEMES.items():
            p = OUT / f"{name}-{theme}.svg"
            p.write_text(fn(palette), encoding="utf-8")
            print(f"wrote {p.relative_to(OUT.parent.parent)}")


if __name__ == "__main__":
    main()
