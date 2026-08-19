#!/usr/bin/env python3
"""Generate the SVG figures for docs/how_it_works.md.

Hand-built SVG rather than a plotting library: these are explanatory diagrams,
not plots of data, and keeping them dependency-free means `python3
tools/make_figures.py` regenerates them from a clean checkout.

Every figure paints its own light background so it stays legible whatever theme
the page is rendered in — GitHub serves SVG through an <img>, which does not
inherit the surrounding colours.

    python3 tools/make_figures.py           # writes docs/figures/*.svg
"""
from __future__ import annotations

import math
import os

OUT = os.path.join(os.path.dirname(__file__), "..", "docs", "figures")

INK = "#1f2933"
MUTED = "#6b7280"
GRID = "#d8dee9"
BG = "#fbfbfd"
CARD = "#eef2f7"
BLUE = "#1f6feb"      # the seeds / the topic direction
GREEN = "#1a7f37"     # on-topic, kept
RED = "#cf222e"       # off-topic, dropped
AMBER = "#bf8700"

FONT = "font-family='ui-sans-serif,-apple-system,Segoe UI,Helvetica,Arial,sans-serif'"
MONO = "font-family='ui-monospace,SFMono-Regular,Menlo,Consolas,monospace'"


def svg(w, h, body, title):
    return (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {w} {h}' "
            f"width='{w}' height='{h}' role='img' aria-label='{title}'>"
            f"<title>{title}</title>"
            f"<rect width='{w}' height='{h}' fill='{BG}'/>"
            f"{body}</svg>\n")


def text(x, y, s, size=13, fill=INK, anchor="start", mono=False, weight="normal",
         style=""):
    f = MONO if mono else FONT
    return (f"<text x='{x}' y='{y}' {f} font-size='{size}' fill='{fill}' "
            f"text-anchor='{anchor}' font-weight='{weight}' "
            f"font-style='{style or 'normal'}'>{s}</text>")


def box(x, y, w, h, fill=CARD, stroke=GRID, r=5, sw=1):
    return (f"<rect x='{x}' y='{y}' width='{w}' height='{h}' rx='{r}' "
            f"fill='{fill}' stroke='{stroke}' stroke-width='{sw}'/>")


def line(x1, y1, x2, y2, stroke=MUTED, sw=1.5, dash=None):
    d = f" stroke-dasharray='{dash}'" if dash else ""
    return (f"<line x1='{x1}' y1='{y1}' x2='{x2}' y2='{y2}' stroke='{stroke}' "
            f"stroke-width='{sw}'{d}/>")


def arrow(x1, y1, x2, y2, stroke=INK, sw=2, head=7):
    ang = math.atan2(y2 - y1, x2 - x1)
    p = []
    for s in (2.6, -2.6):
        p.append((x2 - head * math.cos(ang - s / 6), y2 - head * math.sin(ang - s / 6)))
    ax, ay = x2 - head * math.cos(ang), y2 - head * math.sin(ang)
    return (line(x1, y1, ax, ay, stroke, sw)
            + f"<polygon points='{x2},{y2} "
              f"{x2-head*math.cos(ang-0.42)},{y2-head*math.sin(ang-0.42)} "
              f"{x2-head*math.cos(ang+0.42)},{y2-head*math.sin(ang+0.42)}' "
              f"fill='{stroke}'/>")


def write(name, content):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.abspath(os.path.join(OUT, name))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    print(f"wrote {os.path.relpath(path, os.path.join(os.path.dirname(__file__), '..'))}"
          f"  ({len(content)} bytes)")
    return path


# --------------------------------------------------------------------------- #
# 1. A sentence becomes one vector
# --------------------------------------------------------------------------- #
def fig_tokens(n=1):
    """The beginner on-ramp: real tokens, real numbers, real mean pooling."""
    W, H = 880, 430
    toks = ["[CLS]", "trpv1", "mediates", "vasodilation", "in", "arterial",
            "smooth", "muscle", ".", "[SEP]"]
    rows = [("[CLS]", "-0.05  -0.48  +0.03  -0.34  -0.24"),
            ("trpv1", "-0.02  -0.33  -0.18  -0.20  -0.23"),
            ("mediates", "-0.01  +0.00  +0.10  -0.22  +0.10"),
            ("vasodilation", "-0.09  -0.10  -0.09  -0.12  -0.17"),
            ("…", "…"),
            ("[SEP]", "-0.07  -0.21  -0.05  -0.19  -0.11")]
    b = [text(24, 30, f"{n} · A sentence becomes one vector", 15, INK, weight="600")]

    b.append(text(24, 60, "“TRPV1 mediates vasodilation in arterial smooth muscle.”",
                  13, MUTED, style="italic"))

    # token chips, each with the vocabulary id underneath
    ids = {"[CLS]": 2, "trpv1": 17501, "mediates": 10412, "vasodilation": 21742,
           "in": 1922, "arterial": 6624, "smooth": 6689, "muscle": 3760,
           ".": 18, "[SEP]": 3}
    x = 24
    b.append(text(24, 90, "① split into word pieces from a fixed 30,522-word "
                  "vocabulary", 11, MUTED))
    for t in toks:
        w = 9 + 7.3 * len(t)
        fill = "#e4ecf7" if t.startswith("[") else CARD
        b.append(box(x, 100, w, 24, fill=fill))
        b.append(text(x + w / 2, 116, t, 11, INK, "middle", mono=True))
        b.append(text(x + w / 2, 138, str(ids[t]), 10, BLUE, "middle", mono=True))
        x += w + 6
    b.append(text(24, 156, "② each word piece is just its row number in that "
                  "vocabulary", 11, MUTED))

    # per-token vectors
    b.append(text(24, 182, "③ that row number looks up 768 numbers, which the model "
                  "then rewrites using the surrounding words", 11, MUTED))
    y = 194
    for name, vals in rows:
        b.append(text(150, y + 14, name, 11, INK, "end", mono=True))
        b.append(box(162, y, 300, 20, fill="#ffffff"))
        b.append(text(172, y + 14, vals, 10.5, MUTED, mono=True))
        b.append(text(470, y + 14, "…" if vals != "…" else "", 10.5, MUTED, mono=True))
        y += 24

    # the averaging bracket
    b.append(f"<path d='M486 196 q10 0 10 12 v36 q0 12 10 12 q-10 0 -10 12 v36 "
             f"q0 12 -10 12' fill='none' stroke='{GRID}' stroke-width='1.5'/>")
    b.append(text(506, 272, "④ average", 12, INK, weight="600"))
    b.append(text(506, 288, "over the real tokens", 10.5, MUTED))
    b.append(arrow(592, 267, 626, 267, MUTED, 1.8))

    b.append(text(636, 214, "one vector for the whole sentence", 11, MUTED))
    b.append(box(636, 224, 216, 22, fill="#ffffff"))
    b.append(text(646, 239, "-0.02  -0.10  -0.01  -0.16  …", 10.5, INK, mono=True))
    b.append(text(636, 268, "length 14.08", 11, MUTED, mono=True))
    b.append(arrow(700, 276, 700, 300, MUTED, 1.8))
    b.append(text(712, 294, "⑤ scale to length 1", 11, INK))
    b.append(box(636, 308, 216, 22, fill="#e7f3ec", stroke="#b7dfc6"))
    b.append(text(646, 323, "-0.0011  -0.0069  -0.0010  …", 10.5, GREEN, mono=True))
    b.append(text(636, 350, "length 1.00 — only the direction is kept", 11, GREEN))

    b.append(line(24, 372, W - 24, 372, GRID, 1))
    b.append(text(24, 396, "768 is simply how wide this model is built — every token "
                  "vector, at every layer, is 768 numbers.", 12, INK))
    b.append(text(24, 416, "Papers about similar things end up pointing in similar "
                  "directions, and that is the whole basis of the relevance gate.",
                  12, MUTED))
    return svg(W, H, "".join(b), "A sentence becomes one vector")


# --------------------------------------------------------------------------- #
# 2b. What the layers do to one token
# --------------------------------------------------------------------------- #
def fig_context(n=1):
    """Why a token's stored numbers are not the numbers the model ends up using."""
    W, H = 880, 400
    b = [text(24, 30, f"{n} · The stored numbers are only a starting point", 15, INK,
              weight="600")]
    b.append(text(24, 56, "Following one token — “muscle”, row 3760 — through the "
                  "twelve layers, in two different sentences.", 12, MUTED))

    # the stored row
    b.append(box(24, 92, 210, 78, fill="#ffffff"))
    b.append(text(36, 114, "row 3760 of the table", 11, MUTED))
    b.append(text(36, 136, "-0.09  -0.05  -0.09  …", 10.5, INK, mono=True))
    b.append(text(36, 158, "the same every time", 10.5, MUTED, style="italic"))
    b.append(text(24, 186, "context-free: identical in", 11, MUTED))
    b.append(text(24, 202, "every sentence ever written", 11, MUTED))

    b.append(arrow(240, 131, 292, 131, MUTED, 2))

    # the layers
    b.append(box(296, 86, 150, 92, fill="#e8eefb", stroke="#c3d3f2"))
    b.append(text(371, 112, "12 layers", 12.5, BLUE, "middle", weight="600"))
    b.append(text(371, 132, "each token looks at", 10.5, MUTED, "middle"))
    b.append(text(371, 148, "every other token", 10.5, MUTED, "middle"))
    b.append(text(371, 168, "and is rewritten", 10.5, MUTED, "middle"))
    b.append(text(371, 200, "the sentence enters here", 11, MUTED, "middle"))

    b.append(arrow(452, 131, 504, 131, MUTED, 2))

    # two outcomes
    for i, (sent, val, col) in enumerate([
            ("“arterial smooth muscle …”", "+0.08  -0.03  +0.21  …", GREEN),
            ("“he lost muscle mass …”", "-0.17  -0.08  +0.09  …", AMBER)]):
        y = 92 + i * 76
        b.append(box(510, y, 346, 60, fill="#ffffff"))
        b.append(text(524, y + 22, sent, 11.5, col))
        b.append(text(524, y + 44, val, 10.5, INK, mono=True))

    b.append(box(24, 236, W - 48, 96, fill="#fff6e5", stroke="#f0d9a8"))
    b.append(text(40, 260, "Measured on this exact token", 12.5, INK, weight="600"))
    rows = [("stored row  vs  the artery sentence", "0.135"),
            ("stored row  vs  the illness sentence", "0.140"),
            ("the two sentences, against each other", "0.939")]
    for i, (lab, v) in enumerate(rows):
        b.append(text(40, 282 + i * 18, lab, 11.5, MUTED))
        b.append(text(330, 282 + i * 18, v, 11.5, INK, "end", mono=True))
    b.append(text(360, 288, "The stored row barely resembles what comes out —", 11.5, INK))
    b.append(text(360, 306, "the layers do not adjust it, they replace it.", 11.5, INK))

    b.append(text(24, 362, "So “muscle” in an artery paper and “muscle” in a wasting "
                  "study are different numbers, which is what lets one vector stand "
                  "for a meaning", 12, MUTED))
    b.append(text(24, 380, "rather than a spelling.", 12, MUTED))
    return svg(W, H, "".join(b), "The layers rewrite each token")


# --------------------------------------------------------------------------- #
# 3b. Layers and heads
# --------------------------------------------------------------------------- #
TOKENS = ["[CLS]", "trpv1", "mediates", "vasodilation", "in", "arterial",
          "smooth", "muscle", ".", "[SEP]"]
MUSCLE_L1H8 = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.999, 0.0, 0.0, 0.0]
ARTERIAL_L3H3 = [0.011, 0.002, 0.002, 0.615, 0.01, 0.2, 0.126, 0.013, 0.014, 0.008]
DELIM_FRAC = [
    [.26,.43,.43,.41,.35,.46,.46,.34,.30,.59,.35,.50],
    [.67,.75,.74,.52,.49,.84,.73,.68,.29,.52,.92,.57],
    [.68,.67,.63,.80,.69,.64,.50,.67,.80,.66,.61,.61],
    [.61,.67,.46,.40,.57,.63,.79,.57,.82,.45,.73,.46],
    [.78,.61,.50,.67,.53,.59,.79,.73,.49,.66,.69,.72],
    [.32,.56,.66,.74,.82,.34,.59,.46,.33,.28,.57,.57],
    [.36,.39,.58,.19,.57,.38,.55,.50,.55,.36,.50,.26],
    [.85,.77,.85,.53,.66,.41,.77,.94,.64,.83,.59,.70],
    [.73,.63,.94,.63,.75,.70,.82,.63,.64,.92,.91,.58],
    [.95,.70,.80,.88,.68,.78,.70,.91,.68,.89,.80,.69],
    [.93,.96,.81,.66,.92,.88,.84,.74,.77,.78,.80,.98],
    [.73,.79,.73,.69,.60,.72,.70,.55,.73,.77,.72,.52],
]


def fig_heads(n=1):
    """One layer, twelve heads: what they actually attend to."""
    W, H = 880, 470
    b = [text(24, 30, f"{n} · Inside a layer: twelve heads, each reading the "
              f"sentence differently", 15, INK, weight="600")]
    b.append(text(24, 56, "Real attention weights for the sentence above. Each row "
                  "sums to 1 — a head spreads one unit of attention.", 12, MUTED))

    def panel(x0, y0, weights, focus, title, sub, col):
        out = [text(x0, y0, title, 12.5, INK, weight="600"),
               text(x0, y0 + 18, sub, 11.5, col)]
        out.append(text(x0, y0 + 42, f"how “{focus}” splits its attention:", 11, MUTED))
        yy = y0 + 54
        for t, w in zip(TOKENS, weights):
            out.append(text(x0 + 86, yy + 10, t, 10.5, INK, "end", mono=True))
            out.append(f"<rect x='{x0+94}' y='{yy+1}' width='{max(1, 210*w):.1f}' "
                       f"height='11' rx='2' fill='{col}' opacity='0.75'/>")
            if w >= 0.05:
                out.append(text(x0 + 94 + 210 * w + 6, yy + 11, f"{w:.2f}", 10, MUTED,
                                mono=True))
            yy += 15
        return "".join(out)

    b.append(panel(24, 92, MUSCLE_L1H8, "muscle", "layer 1, head 8",
                   "reads the word immediately before — every time", BLUE))
    b.append(panel(360, 92, ARTERIAL_L3H3, "arterial", "layer 3, head 3",
                   "reaches across the sentence to a related word", GREEN))

    # the 12x12 grid
    gx, gy, c = 692, 132, 12.5
    b.append(text(gx, 92, "all 144 heads", 12.5, INK, weight="600"))
    b.append(text(gx, 108, "darker = ignores the words and", 10.5, MUTED))
    b.append(text(gx, 122, "parks on punctuation instead", 10.5, MUTED))
    for L, row in enumerate(DELIM_FRAC):
        for Hd, v in enumerate(row):
            op = 0.10 + 0.85 * v
            b.append(f"<rect x='{gx+Hd*c:.1f}' y='{gy+L*c:.1f}' width='{c-1.6:.1f}' "
                     f"height='{c-1.6:.1f}' fill='{RED}' opacity='{op:.2f}'/>")
    b.append(text(gx - 8, gy + 6 * c + 4, "layers", 10.5, MUTED, "end"))
    b.append(text(gx + 6 * c, gy + 12 * c + 16, "heads", 10.5, MUTED, "middle"))

    b.append(box(24, 300, W - 48, 62, fill="#fff6e5", stroke="#f0d9a8"))
    b.append(text(40, 322, "Heads specialise, but most of them are not doing anything "
                  "tidy.", 12.5, INK, weight="600"))
    b.append(text(40, 342, "56 of the 144 send over 70% of their attention to "
                  "punctuation and sentence markers — a known idling behaviour. Only "
                  "16 mostly read the words.", 12, MUTED))

    b.append(line(24, 380, W - 24, 380, GRID, 1))
    b.append(text(24, 402, "A head is a 64-number slice of the 768: twelve of them, "
                  "each attending independently, then joined back together. "
                  "12 × 64 = 768.", 12, INK))
    b.append(text(24, 424, "A layer is one round of that. Stacking twelve means a "
                  "token can be influenced by a word it never looked at directly — "
                  "reached through", 12, MUTED))
    b.append(text(24, 442, "whatever the layer below already folded in.", 12, MUTED))
    return svg(W, H, "".join(b), "Layers and attention heads")


# --------------------------------------------------------------------------- #
# 2. Scoring by angle, with real cosines
# --------------------------------------------------------------------------- #
def fig_angle(n=1):
    W, H = 880, 400
    cx, cy, R = 236, 236, 152
    b = [text(24, 30, f"{n} · Candidates are scored by the angle they make with the topic",
              15, INK, weight="600")]
    b.append(text(24, 56, "Real PubMedBERT cosines — and note how little of the circle "
                  "any of this text actually uses.", 12, MUTED))

    b.append(f"<path d='M {cx} {cy} L {cx+R} {cy} A {R} {R} 0 0 0 "
             f"{cx+R*math.cos(math.radians(16.8))} {cy-R*math.sin(math.radians(16.8))} Z' "
             f"fill='#e3ecfa' stroke='none'/>")
    b.append(f"<circle cx='{cx}' cy='{cy}' r='{R}' fill='none' stroke='{GRID}' "
             f"stroke-width='1.5'/>")
    b.append(line(cx - R - 20, cy, cx + R + 24, cy, GRID, 1, "3 3"))

    for deg, col, lw in ((0, BLUE, 3.4), (7.8, GREEN, 2.4), (16.8, RED, 2.4)):
        a = math.radians(deg)
        b.append(arrow(cx, cy, cx + R * math.cos(a), cy - R * math.sin(a), col, lw))
    b.append(f"<path d='M {cx+52} {cy} A 52 52 0 0 0 "
             f"{cx+52*math.cos(math.radians(16.8))} "
             f"{cy-52*math.sin(math.radians(16.8))}' fill='none' stroke='{MUTED}' "
             f"stroke-width='1.2'/>")
    b.append(text(cx + 64, cy - 6, "θ", 12, MUTED, style="italic"))
    b.append(text(cx, cy + R + 40, "every direction these texts use", 11.5, MUTED,
                  "middle"))

    # legend table — no radial labels, so nothing can collide
    x0, y0 = 462, 96
    b.append(box(x0, y0, W - x0 - 24, 150, fill="#ffffff"))
    b.append(text(x0 + 18, y0 + 26, "angle from the topic", 11.5, MUTED))
    b.append(text(W - 42, y0 + 26, "cosine", 11.5, MUTED, "end"))
    b.append(line(x0 + 18, y0 + 36, W - 42, y0 + 36, GRID, 1))
    rows = [(BLUE, "q₀   the seed topic", "0°", "1.0000"),
            (GREEN, "a related TRPV1 paper", "7.8°", "0.9907"),
            (RED, "wheat fertiliser in soils", "16.8°", "0.9574")]
    for i, (col, lab, ang, cos) in enumerate(rows):
        y = y0 + 62 + i * 30
        b.append(f"<rect x='{x0+18}' y='{y-9}' width='11' height='11' rx='2' "
                 f"fill='{col}'/>")
        b.append(text(x0 + 38, y, lab, 12, INK))
        b.append(text(x0 + 250, y, ang, 12, MUTED, "end", mono=True))
        b.append(text(W - 42, y, cos, 12, col, "end", mono=True))

    b.append(box(462, 262, W - 486, 76, fill="#fff6e5", stroke="#f0d9a8"))
    b.append(text(480, 286, "A paper about wheat still scores 0.957.", 12.5, INK,
                  weight="600"))
    b.append(text(480, 306, "Everything crowds into a narrow wedge, so the", 12, MUTED))
    b.append(text(480, 324, "ranking is meaningful — the number is not.", 12, MUTED))
    b.append(text(24, 378, "That is why step 5 keeps the top K rather than everything "
                  "above a fixed score.", 12, MUTED))
    return svg(W, H, "".join(b), "Candidates are scored by angle")


# --------------------------------------------------------------------------- #
# 3. The direction every paper shares
# --------------------------------------------------------------------------- #
def fig_shared(n=1):
    W, H = 880, 350
    rows = [("TRPV1 mediates vasodilation in arterial smooth muscle.", -0.967),
            ("Wheat yield responses to nitrogen fertiliser in soils.", -0.965),
            ("Microglia mediate forgetting via synaptic elimination.", -0.973),
            ("The cat sat on the mat.", -0.954)]
    b = [text(24, 30, f"{n} · Why every score comes out near 0.99", 15, INK, weight="600")]
    b.append(text(24, 56, "Of the 768 numbers, one is almost the same for every "
                  "text — whatever the text is about.", 12, MUTED))

    x0, bw = 470, 330
    b.append(text(x0, 84, "value at dimension 424", 11, MUTED))
    b.append(line(x0, 92, x0 + bw, 92, GRID, 1))
    for i, (t, v) in enumerate(rows):
        y = 104 + i * 34
        b.append(text(x0 - 14, y + 14, t, 11.5, INK, "end"))
        w = bw * abs(v)
        b.append(box(x0, y, w, 20, fill="#f6dde0", stroke="#e8b4ba", r=3))
        b.append(text(x0 + w - 8, y + 14, f"{v:.3f}", 11, RED, "end", mono=True))
    b.append(line(x0, 246, x0 + bw, 246, GRID, 1))
    b.append(text(x0, 264, "−1.0", 10.5, MUTED, "start", mono=True))

    b.append(box(24, 282, W - 48, 48, fill="#fff6e5", stroke="#f0d9a8"))
    b.append(text(40, 303, "That single shared dimension is 93% of each vector.",
                  12.5, INK, weight="600"))
    b.append(text(40, 321, "Two papers are ~93% identical before either says anything "
                  "— hence wheat scoring 0.957 against TRPV1.", 12, MUTED))
    return svg(W, H, "".join(b), "The direction every paper shares")


# --------------------------------------------------------------------------- #
# 4. Seeds define a direction
# --------------------------------------------------------------------------- #
def fig_centroid(n=1):
    W, H = 880, 404
    b = [text(24, 30, f"{n} · The seeds define the direction to score against", 15, INK,
              weight="600")]
    b.append(text(24, 58, "Average the seed arrows. How long that average comes out "
                  "says whether the seeds agree.", 12, MUTED))

    def panel(cx, cy, angles, label, sub, ok):
        R, col = 108, BLUE if ok else AMBER
        out = [f"<circle cx='{cx}' cy='{cy}' r='{R}' fill='none' stroke='{GRID}' "
               f"stroke-width='1.5' stroke-dasharray='4 4'/>"]
        vx = vy = 0.0
        for a in angles:
            r = math.radians(a)
            out.append(arrow(cx, cy, cx + R * math.cos(r), cy - R * math.sin(r),
                             MUTED, 1.6))
            vx += math.cos(r); vy += math.sin(r)
        vx, vy = vx / len(angles), vy / len(angles)
        mag = math.hypot(vx, vy)
        out.append(arrow(cx, cy, cx + R * vx, cy - R * vy, col, 3.4))
        out.append(text(cx, cy + R + 34, label, 13, INK, "middle", weight="600"))
        out.append(text(cx, cy + R + 52, sub, 11.5, MUTED, "middle"))
        out.append(text(cx, cy + R + 72, f"length of average = {mag:.2f}", 12, col,
                        "middle"))
        return "".join(out)

    b.append(panel(210, 168, [72, 84, 96, 108], "seeds that agree",
                   "one subject, arrows nearly parallel", True))
    b.append(panel(650, 168, [20, 70, 110, 160], "seeds that don't",
                   "two subjects, arrows pulling apart", False))
    b.append(line(440, 84, 440, 330, GRID, 1))
    b.append(text(24, 384, "A short average points into the gap between subjects — a "
                  "direction no seed occupies, so the gate scores against something "
                  "none of your papers are about.", 12, MUTED))
    return svg(W, H, "".join(b), "Seeds define a direction")


# --------------------------------------------------------------------------- #
# 5. The negative term rotates the gate
# --------------------------------------------------------------------------- #
def fig_rocchio(n=1):
    W, H = 880, 380
    b = [text(24, 30, f"{n} · Learning what the topic is not", 15, INK, weight="600")]
    b.append(text(24, 56, "A methods paper can sit at the same angle as a real match. "
                  "Tilting away from the worst candidates separates them.", 12, MUTED))

    def panel(cx, cy, qdeg, caption, sc_x, sc_m):
        R = 112
        out = [f"<circle cx='{cx}' cy='{cy}' r='{R}' fill='none' stroke='{GRID}' "
               f"stroke-width='1.5' stroke-dasharray='4 4'/>"]
        for deg, col, lab, lw in ((40, GREEN, "X  on topic", 2.4),
                                  (-40, AMBER, "M  methods", 2.4),
                                  (-88, RED, "n̂  worst candidates", 2)):
            r = math.radians(deg)
            x2, y2 = cx + R * math.cos(r), cy - R * math.sin(r)
            out.append(arrow(cx, cy, x2, y2, col, lw))
            ax = "start" if math.cos(r) >= -0.2 else "end"
            out.append(text(cx + (R + 12) * math.cos(r), cy - (R + 12) * math.sin(r) + 4,
                            lab, 11, col, ax))
        r = math.radians(qdeg)
        out.append(arrow(cx, cy, cx + R * math.cos(r), cy - R * math.sin(r), BLUE, 3.6))
        out.append(text(cx + (R + 14) * math.cos(r), cy - (R + 14) * math.sin(r) - 4,
                        "q", 13, BLUE, "start", weight="600"))
        out.append(text(cx, cy + R + 40, caption, 12.5, INK, "middle", weight="600"))
        out.append(text(cx, cy + R + 60, f"X {sc_x}   ·   M {sc_m}", 12, MUTED,
                        "middle", mono=True))
        return "".join(out)

    b.append(panel(215, 168, 0, "before — q points at the seeds", "0.7071", "0.7071"))
    b.append(panel(655, 168, 22, "after — q tilts away from n̂", "0.7566", "0.6136"))
    b.append(line(440, 86, 440, 300, GRID, 1))
    b.append(arrow(392, 168, 488, 168, MUTED, 2))
    b.append(text(440, 158, "subtract γ·n̂", 11.5, INK, "middle"))
    b.append(text(24, 360, "M sits on the same side as the worst candidates, so tilting "
                  "away from them costs M more than X. Real numbers from the test suite.",
                  12, MUTED))
    return svg(W, H, "".join(b), "The negative term rotates the gate")


# --------------------------------------------------------------------------- #
# 6. What top-K trades  (real benchmark numbers, 12 reviews)
# --------------------------------------------------------------------------- #
DATA = [(10, 0.1255, 0.0333), (25, 0.0941, 0.0903), (50, 0.0854, 0.1406),
        (100, 0.0624, 0.2473), (200, 0.0511, 0.2992), (400, 0.0385, 0.3186),
        (800, 0.0328, 0.3252)]
BFS = (0.0244, 0.3252)


def fig_topk(n=1):
    W, H = 880, 420
    L, Rr, T, B = 78, 560, 84, 320
    ymax = 0.34
    b = [text(24, 30, f"{n} · What K trades", 15, INK, weight="600")]
    b.append(text(24, 56, "Benchmarked on 12 systematic reviews. Precision falls and "
                  "recall rises as K grows.", 12, MUTED))

    def X(i):
        return L + (Rr - L) * i / (len(DATA) - 1)

    def Y(v):
        return B - (B - T) * v / ymax

    for g in (0.0, 0.1, 0.2, 0.3):
        b.append(line(L, Y(g), Rr, Y(g), GRID, 1))
        b.append(text(L - 10, Y(g) + 4, f"{g:.1f}", 10.5, MUTED, "end", mono=True))
    for i, (k, _, _) in enumerate(DATA):
        b.append(text(X(i), B + 20, str(k), 10.5, MUTED, "middle", mono=True))
    b.append(text((L + Rr) / 2, B + 42, "K  (papers kept per direction)", 11.5, INK,
                  "middle"))

    b.append(line(L, Y(BFS[1]), Rr, Y(BFS[1]), MUTED, 1.4, "5 4"))
    b.append(text(Rr + 8, Y(BFS[1]) + 4, "bfs recall", 11, MUTED))
    b.append(line(L, Y(BFS[0]), Rr, Y(BFS[0]), MUTED, 1.4, "5 4"))
    b.append(text(Rr + 8, Y(BFS[0]) + 4, "bfs precision", 11, MUTED))

    for idx, col, lab in ((2, GREEN, "recall"), (1, BLUE, "precision")):
        pts = " ".join(f"{X(i)},{Y(row[idx])}" for i, row in enumerate(DATA))
        b.append(f"<polyline points='{pts}' fill='none' stroke='{col}' "
                 f"stroke-width='2.6'/>")
        for i, row in enumerate(DATA):
            b.append(f"<circle cx='{X(i)}' cy='{Y(row[idx])}' r='3.6' fill='{col}'/>")
        b.append(text(X(len(DATA) - 1) + 8, Y(DATA[-1][idx]) - 10, lab, 12, col,
                      weight="600"))

    # the default and the ABC band
    b.append(line(X(2), T - 12, X(2), B, BLUE, 1.2, "3 3"))
    b.append(text(X(2), T - 18, "default K = 50", 11, BLUE, "middle"))
    b.append(f"<rect x='{X(3)}' y='{T}' width='{X(4)-X(3)}' height='{B-T}' "
             f"fill='{GREEN}' opacity='0.07'/>")
    b.append(text((X(3) + X(4)) / 2, T - 18, "K ≈ 100–200", 11, GREEN, "middle"))

    x0 = 616
    b.append(box(x0, T, W - x0 - 24, 196, fill="#ffffff"))
    for i, (s, col, w) in enumerate([
            ("Reading it", INK, "600"),
            ("K = 25 is sharpest.", MUTED, "normal"),
            ("K = 50 is the default and has", MUTED, "normal"),
            ("the best record head-to-head.", MUTED, "normal"),
            ("K ≈ 100–200 keeps enough", GREEN, "normal"),
            ("intermediates for ABC discovery.", GREEN, "normal"),
            ("At K = 800 recall equals bfs", INK, "normal"),
            ("exactly — on 87% less material.", INK, "normal")]):
        b.append(text(x0 + 16, T + 26 + i * 22, s, 12, col, weight=w))

    b.append(text(24, 400, "The recall gap at small K is the cutoff choosing, not the "
                  "gate failing — so there is no breadth argument for taking everything.",
                  12, MUTED))
    return svg(W, H, "".join(b), "What top-K trades")


# --------------------------------------------------------------------------- #
# 7. The two citation directions
# --------------------------------------------------------------------------- #
def fig_directions(n=1):
    """Dot counts are proportional: one dot = 150 papers, so the 20x really shows."""
    W, H = 880, 380
    PER_DOT = 150
    back, fwd = 2342, 47000
    b = [text(24, 30, f"{n} · The two directions are not the same size", 15, INK,
              weight="600")]
    b.append(text(24, 56, f"Measured across the benchmark's seed sets, one round each "
                  f"way. One dot = {PER_DOT} papers.", 12, MUTED))

    mid, y = 440, 190
    b.append(line(48, y, W - 48, y, GRID, 1.5))
    b.append(text(W - 48, y - 12, "time →", 11, MUTED, "end"))

    import random as _r
    rnd = _r.Random(11)
    for side, total, col, span in ((-1, back, AMBER, 300), (1, fwd, BLUE, 330)):
        n = max(1, round(total / PER_DOT))
        for _ in range(n):
            dx = (28 + abs(rnd.gauss(0, 0.42)) % 1.0 * span) * side
            dy = rnd.gauss(0, 26)
            if abs(dy) > 62:
                dy = rnd.uniform(-62, 62)
            b.append(f"<circle cx='{mid + dx:.1f}' cy='{y + dy:.1f}' r='2.4' "
                     f"fill='{col}' opacity='0.6'/>")
    for i in range(5):
        b.append(f"<circle cx='{mid - 18 + i * 9}' cy='{y}' r='5.2' fill='{GREEN}' "
                 f"stroke='{BG}' stroke-width='1.4'/>")

    b.append(text(mid, y - 92, "your seeds", 12.5, GREEN, "middle", weight="600"))
    b.append(text(mid, y - 76, "5 papers", 11, MUTED, "middle"))

    def caption(cx, title, sub, count, col):
        return "".join([
            text(cx, y + 104, title, 12.5, col, "middle", weight="600"),
            text(cx, y + 122, sub, 11.5, MUTED, "middle"),
            text(cx, y + 144, count, 12.5, col, "middle", mono=True)])

    b.append(caption(mid - 190, "references (backward)", "what the seeds cite",
                     "≈ 2,300 papers", AMBER))
    b.append(caption(mid + 190, "cited_by (forward)", "what cites the seeds",
                     "≈ 47,000 papers", BLUE))
    b.append(line(24, 356, W - 24, 356, GRID, 1))
    b.append(text(24, 374, "Forward is ~20× larger. Adding it unfiltered is what made "
                  "95% of the corpus material the gate never looked at.", 12, MUTED))
    return svg(W, H, "".join(b), "The two citation directions")


# The order figures appear in docs/how_it_works.md. The number printed on each
# figure comes from this list, so the two can never disagree — reorder here and
# the captions follow.
ORDER = [
    ("two-directions", fig_directions),
    ("tokens-to-vector", fig_tokens),
    ("token-in-context", fig_context),
    ("layers-and-heads", fig_heads),
    ("seed-direction", fig_centroid),
    ("scoring-by-angle", fig_angle),
    ("shared-direction", fig_shared),
    ("negative-term", fig_rocchio),
    ("top-k-tradeoff", fig_topk),
]


def main():
    for i, (stem, build) in enumerate(ORDER, start=1):
        write(f"{i:02d}-{stem}.svg", build(i))


if __name__ == "__main__":
    main()
