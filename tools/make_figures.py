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
# 0. The map: how the pieces nest
# --------------------------------------------------------------------------- #
def fig_map(n=1):
    """Orientation figure — every term in this section, and how they compose."""
    W, H = 880, 1040
    b = [text(24, 30, f"{n} · The pieces, and how they fit together", 15, INK,
              weight="600")]
    b.append(text(24, 56, "Everything in this stage is built from one idea — a list "
                  "of numbers — repeated at four sizes.", 12, MUTED))

    # ---- band 1: the atom ---------------------------------------------------
    b.append(text(24, 92, "THE ATOM", 10.5, MUTED, weight="600"))
    b.append(box(24, 102, 520, 76, fill="#ffffff"))
    b.append(text(40, 124, "a vector", 13, BLUE, weight="600"))
    b.append(text(110, 124, "— a fixed-length list of numbers", 12, INK))
    for i in range(24):
        v = 0.25 + 0.75 * abs(math.sin(i * 1.7))
        b.append(f"<rect x='{40 + i * 13}' y='{136}' width='11' height='16' rx='2' "
                 f"fill='{BLUE}' opacity='{v:.2f}'/>")
    b.append(text(360, 149, "… 768 of them", 11, MUTED, mono=True))
    b.append(text(40, 170, "Its *direction* carries the meaning; its length is thrown "
                  "away.", 11, MUTED))
    b.append(text(560, 124, "Two vectors are compared by the", 11.5, MUTED))
    b.append(text(560, 141, "angle between them — that single", 11.5, MUTED))
    b.append(text(560, 158, "number is the whole relevance gate.", 11.5, MUTED))

    # ---- band 2: inside the model -------------------------------------------
    b.append(text(24, 214, "WHERE VECTORS COME FROM — inside PubMedBERT", 10.5, MUTED,
                  weight="600"))
    b.append(box(24, 224, W - 48, 502, fill="#ffffff"))

    b.append(text(44, 250, "word piece", 11.5, INK, weight="600"))
    b.append(box(44, 258, 96, 24, fill=CARD))
    b.append(text(92, 274, "muscle", 11, INK, "middle", mono=True))
    b.append(text(44, 300, "row 3760 of a", 10.5, MUTED))
    b.append(text(44, 314, "30,522-word list", 10.5, MUTED))
    b.append(arrow(148, 270, 186, 270, MUTED, 1.8))

    b.append(text(196, 250, "its stored vector", 11.5, INK, weight="600"))
    for i in range(12):
        b.append(f"<rect x='{196 + i * 9}' y='258' width='7' height='24' rx='1.5' "
                 f"fill='{MUTED}' opacity='0.45'/>")
    b.append(text(196, 300, "the same numbers every", 10.5, MUTED))
    b.append(text(196, 314, "time this word appears,", 10.5, MUTED))
    b.append(text(196, 328, "in any sentence", 10.5, MUTED))
    b.append(arrow(316, 270, 354, 270, MUTED, 1.8))

    # the nesting: head -> layer -> model
    b.append(box(364, 240, 300, 132, fill="#f4f7fd", stroke="#c3d3f2"))
    b.append(text(374, 258, "the model — 12 layers, stacked", 11.5, BLUE,
                  weight="600"))
    b.append(box(374, 266, 280, 96, fill="#e8eefb", stroke="#c3d3f2"))
    b.append(text(384, 284, "one layer — 12 heads, side by side", 11, BLUE))
    for i in range(12):
        w = 20
        b.append(f"<rect x='{384 + i * 22}' y='294' width='{w}' height='40' rx='3' "
                 f"fill='{BLUE}' opacity='{0.5 if i != 3 else 0.95}'/>")
    b.append(text(384, 350, "one head — a 64-wide slice; 12 × 64 = 768", 10.5, MUTED))
    b.append(arrow(672, 300, 706, 300, MUTED, 1.8))

    b.append(text(716, 250, "the token, in", 11.5, INK, weight="600"))
    b.append(text(716, 266, "this sentence", 11.5, INK, weight="600"))
    for i in range(12):
        v = 0.3 + 0.7 * abs(math.sin(i * 2.3))
        b.append(f"<rect x='{716 + i * 9}' y='276' width='7' height='24' rx='1.5' "
                 f"fill='{GREEN}' opacity='{v:.2f}'/>")
    b.append(text(716, 318, "rewritten twelve", 10.5, MUTED))
    b.append(text(716, 332, "times over", 10.5, MUTED))

    # ---- inset A: what "a 64-wide slice" actually means ---------------------
    b.append(line(44, 392, W - 68, 392, GRID, 1))
    b.append(text(44, 414, "what “a 64-wide slice” means", 12, INK, weight="600"))
    b.append(text(44, 432, "Every head sees all 768 numbers of the token. Heads "
                  "differ in the weights they use, not in which", 11, MUTED))
    b.append(text(44, 446, "part of the token they are handed.", 11, MUTED))
    for i in range(36):
        b.append(f"<rect x='{44 + i * 7}' y='462' width='5.5' height='20' rx='1' "
                 f"fill='{GREEN}' opacity='0.55'/>")
    b.append(text(44, 498, "the token — all 768 of its numbers", 10.5, MUTED))
    b.append(arrow(310, 472, 348, 472, MUTED, 1.8))
    b.append(box(358, 440, 214, 56, fill="#e8eefb", stroke="#c3d3f2"))
    b.append(text(465, 460, "the layer’s weights — 768 columns", 10.5, BLUE,
                  "middle"))
    for i in range(12):
        op = 0.95 if i == 3 else 0.25
        b.append(f"<rect x='{368 + i * 17}' y='468' width='14' height='20' rx='2' "
                 f"fill='{BLUE}' opacity='{op}'/>")
    b.append(text(465, 500, "each block = 64 columns. head 4 uses", 10.5, BLUE,
                  "middle"))
    b.append(text(465, 514, "this one: columns 192–255", 10.5, BLUE, "middle"))
    b.append(arrow(582, 472, 620, 472, MUTED, 1.8))
    for i in range(9):
        b.append(f"<rect x='{630 + i * 7}' y='462' width='5.5' height='20' rx='1' "
                 f"fill={chr(39)}{BLUE}{chr(39)} opacity='0.8'/>")
    b.append(text(630, 498, "this head’s answer — 64 numbers", 10.5, BLUE))
    b.append(text(630, 530, "the other eleven heads use the", 10.5, MUTED))
    b.append(text(630, 544, "other eleven blocks. Their answers", 10.5, MUTED))
    b.append(text(630, 558, "join back up to 768.", 10.5, MUTED))

    # ---- inset B: what "rewritten twelve times" means -----------------------
    b.append(line(44, 578, W - 68, 578, GRID, 1))
    b.append(text(44, 600, "what “rewritten twelve times” means", 12, INK,
                  weight="600"))
    b.append(text(44, 618, "The same token, after each layer. Every step is a small "
                  "edit; together they move it a long way — it stops being", 11, MUTED))
    b.append(text(44, 632, "“muscle” in general and becomes “muscle” in this "
                  "sentence.", 11, MUTED))
    TRACE = [1.000, .795, .696, .594, .576, .562, .523, .494, .492, .478, .454,
             .440, .403]
    x0, y0, wdt, hgt = 60, 650, 700, 52
    for gv, lab in ((1.0, "1.0"), (0.5, "0.5"), (0.0, "0")):
        yy = y0 + hgt - hgt * gv
        b.append(line(x0, yy, x0 + wdt, yy, GRID, 1))
        b.append(text(x0 - 8, yy + 4, lab, 9.5, MUTED, "end", mono=True))
    pts = " ".join(f"{x0 + wdt * k / 12:.1f},{y0 + hgt - hgt * v:.1f}"
                   for k, v in enumerate(TRACE))
    b.append(f"<polyline points='{pts}' fill='none' stroke='{GREEN}' "
             f"stroke-width='2.4'/>")
    for k, v in enumerate(TRACE):
        b.append(f"<circle cx='{x0 + wdt * k / 12:.1f}' "
                 f"cy='{y0 + hgt - hgt * v:.1f}' r='3.2' fill='{GREEN}'/>")
    for k in (0, 6, 12):
        b.append(text(x0 + wdt * k / 12, y0 + hgt + 16,
                      "as it enters" if k == 0 else f"after layer {k}", 9.5, MUTED,
                      "middle"))
    b.append(text(x0 + wdt + 12, y0 + 8, "how much of the", 10, MUTED))
    b.append(text(x0 + wdt + 12, y0 + 22, "original is left", 10, MUTED))


    # ---- band 3: what bioleads does -----------------------------------------
    b.append(text(24, 762, "WHAT bioleads DOES WITH THEM", 10.5, MUTED, weight="600"))
    b.append(box(24, 772, W - 48, 174, fill="#ffffff"))

    def stack(x, y, k, col, lab, sub):
        out = []
        for r in range(k):
            for i in range(10):
                v = 0.25 + 0.7 * abs(math.sin((i + r * 3) * 1.9))
                out.append(f"<rect x='{x + i * 8}' y='{y + r * 11}' width='6.5' "
                           f"height='8' rx='1' fill='{col}' opacity='{v:.2f}'/>")
        out.append(text(x, y + k * 11 + 16, lab, 11.5, INK, weight="600"))
        out.append(text(x, y + k * 11 + 32, sub, 10.5, MUTED))
        return "".join(out)

    b.append(text(48, 792, "↑ the vectors the model just produced", 10, GREEN))
    b.append(stack(48, 798, 4, GREEN, "every token", "in one paper"))
    b.append(arrow(148, 822, 186, 822, MUTED, 1.8))
    b.append(text(196, 806, "average", 11, INK, weight="600"))
    b.append(stack(196, 818, 1, GREEN, "one paper", "= one vector, length 1"))
    b.append(arrow(300, 822, 338, 822, MUTED, 1.8))
    b.append(text(319, 802, "repeat for", 9.5, MUTED, "middle"))
    b.append(text(319, 813, "every paper", 9.5, MUTED, "middle"))

    b.append(stack(348, 798, 4, BLUE, "your seed papers", "the ones you chose"))
    b.append(arrow(448, 822, 486, 822, MUTED, 1.8))
    b.append(text(496, 806, "average", 11, INK, weight="600"))
    b.append(stack(496, 818, 1, BLUE, "the topic", "one direction to aim at"))
    b.append(arrow(600, 822, 638, 822, MUTED, 1.8))

    b.append(box(648, 792, 208, 92, fill="#eaf1fb", stroke="#c3d3f2"))
    b.append(text(662, 814, "the angle between", 11.5, INK, weight="600"))
    b.append(text(662, 832, "a candidate and the topic", 11.5, INK))
    b.append(text(662, 854, "→ rank all candidates", 11, GREEN))
    b.append(text(662, 872, "→ keep the top K", 11, GREEN, weight="600"))

    b.append(text(48, 906, "Steps 1–5 below walk this left to right. Everything "
                  "after — the seed profile, the negative correction, the cut — "
                  "happens in the third band,", 12, MUTED))
    b.append(text(48, 924, "and never touches the machinery in the second.", 12, MUTED))

    b.append(line(24, 966, W - 24, 966, GRID, 1))
    b.append(text(24, 988, "One sentence for the whole stage: turn every paper into "
                  "one arrow, point an arrow at your topic, and keep the papers "
                  "whose arrows", 12, INK))
    b.append(text(24, 1008, "point most nearly the same way.", 12, INK))
    return svg(W, H, "".join(b), "The pieces and how they fit together")


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
    [0.27,0.46,0.41,0.24,0.37,0.46,0.30,0.28,0.21,0.50,0.24,0.46],
    [0.61,0.75,0.76,0.42,0.36,0.85,0.71,0.70,0.15,0.45,0.91,0.52],
    [0.61,0.65,0.57,0.82,0.67,0.62,0.38,0.69,0.77,0.61,0.52,0.52],
    [0.57,0.73,0.44,0.26,0.59,0.58,0.73,0.49,0.86,0.42,0.74,0.36],
    [0.77,0.58,0.55,0.70,0.47,0.49,0.80,0.74,0.46,0.64,0.63,0.72],
    [0.27,0.59,0.64,0.81,0.85,0.23,0.58,0.45,0.24,0.14,0.56,0.54],
    [0.15,0.14,0.53,0.14,0.48,0.32,0.52,0.43,0.63,0.31,0.53,0.27],
    [0.80,0.73,0.85,0.45,0.61,0.20,0.77,0.94,0.59,0.81,0.45,0.70],
    [0.68,0.58,0.94,0.53,0.67,0.67,0.80,0.51,0.61,0.91,0.91,0.43],
    [0.94,0.66,0.75,0.85,0.67,0.71,0.65,0.88,0.57,0.87,0.76,0.74],
    [0.93,0.95,0.77,0.58,0.89,0.88,0.85,0.74,0.74,0.76,0.82,0.99],
    [0.85,0.83,0.80,0.75,0.70,0.80,0.70,0.51,0.80,0.81,0.72,0.56],
]


def fig_heads(n=1):
    """Twelve heads per layer, twelve layers: what they actually attend to."""
    W, H = 880, 556
    b = [text(24, 30, f"{n} · Every layer has twelve heads, each reading the "
              f"sentence differently", 15, INK, weight="600")]
    b.append(text(24, 56, "Left: two single heads, and how one word divides its "
                  "attention across the sentence — those bars sum to 1.", 12, MUTED))

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
    gx, gy, c = 706, 138, 12.5
    b.append(text(gx - 34, 92, "12 layers × 12 heads each = 144", 12.5, INK,
                  weight="600"))
    b.append(text(gx - 34, 108, "one square per head. darker = it ignores", 10.5, MUTED))
    b.append(text(gx - 34, 121, "the words and parks on punctuation.", 10.5, MUTED))
    for L, row in enumerate(DELIM_FRAC):
        for Hd, v in enumerate(row):
            op = 0.10 + 0.85 * v
            b.append(f"<rect x='{gx+Hd*c:.1f}' y='{gy+L*c:.1f}' width='{c-1.6:.1f}' "
                     f"height='{c-1.6:.1f}' fill='{RED}' opacity='{op:.2f}'/>")
    for k in (0, 5, 11):
        b.append(text(gx - 6, gy + k * c + 9, str(k + 1), 9, MUTED, "end", mono=True))
        b.append(text(gx + k * c + 5.5, gy + 12 * c + 11, str(k + 1), 9, MUTED,
                      "middle", mono=True))
    b.append(text(gx - 22, gy + 6 * c, "layer", 10.5, MUTED, "end"))
    b.append(text(gx + 6 * c, gy + 12 * c + 26, "head within that layer", 10.5, MUTED,
                  "middle"))
    b.append(text(gx - 34, gy + 12 * c + 46, "These squares are not attention", 10, MUTED))
    b.append(text(gx - 34, gy + 12 * c + 59, "weights and do not sum to 1.", 10, MUTED))

    b.append(box(24, 372, W - 48, 82, fill="#fff6e5", stroke="#f0d9a8"))
    b.append(text(40, 394, "Heads specialise, but most of them are not doing anything "
                  "tidy.", 12.5, INK, weight="600"))
    b.append(text(40, 414, "Counting only what the real words point at: 57 of the 144 "
                  "send over 70% of their attention to punctuation and the", 12, MUTED))
    b.append(text(40, 432, "sentence markers — a known idling behaviour — and 23 "
                  "mostly read the words.", 12, MUTED))

    b.append(line(24, 472, W - 24, 472, GRID, 1))
    b.append(text(24, 494, "Within one layer a head is a 64-number slice of the 768 "
                  "— twelve slices attending independently, then joined back "
                  "together: 12 × 64 = 768.", 12, INK))
    b.append(text(24, 516, "A layer is one round of that, and the twelve layers do "
                  "not share heads — each has its own twelve, hence 144 in total. "
                  "Stacking them lets a", 12, MUTED))
    b.append(text(24, 534, "token be influenced by a word it never looked at "
                  "directly, reached through whatever the layer below folded in.",
                  12, MUTED))
    return svg(W, H, "".join(b), "Layers and attention heads")


# --------------------------------------------------------------------------- #
# 3c. What one head actually computes
# --------------------------------------------------------------------------- #
def fig_head_mechanics(n=1):
    """Query, key, value — the arithmetic inside a single head."""
    W, H = 880, 520
    b = [text(24, 30, f"{n} · What one head actually computes", 15, INK, weight="600")]
    b.append(text(24, 56, "Layer 1, head 8, asking on behalf of the word “muscle”. "
                  "Every number here is from the model.", 12, MUTED))

    # the three projections
    b.append(text(24, 92, "The layer holds three 768×768 matrices. This head owns "
                  "columns 512–575 of each — its 64-wide slice.", 11.5, MUTED))
    for i, (nm, sub, col, x0) in enumerate([
            ("query", "what “muscle” is looking for", BLUE, 24),
            ("key", "what each word offers", GREEN, 306),
            ("value", "what gets passed on", AMBER, 588)]):
        b.append(box(x0, 110, 262, 66, fill="#ffffff"))
        b.append(text(x0 + 14, 132, nm, 12.5, col, weight="600"))
        b.append(text(x0 + 14, 150, sub, 11, MUTED))
        b.append(text(x0 + 14, 168, "768 numbers in → 64 out", 10.5, INK, mono=True))

    # the comparison
    b.append(text(24, 208, "Every word’s key is compared with that one query — a dot "
                  "product, divided by √64 to keep the numbers in range:", 11.5, MUTED))
    rows = [("smooth", 23.95, 0.999, GREEN), ("muscle", 16.31, 0.0005, MUTED),
            ("arterial", None, None, MUTED)]
    y = 228
    b.append(text(150, y, "key of…", 11, MUTED, "end"))
    b.append(text(300, y, "q · k / √64", 11, MUTED, "end", mono=True))
    b.append(text(470, y, "after softmax", 11, MUTED, "end", mono=True))
    for nm, sc, wt, col in rows:
        y += 22
        b.append(text(150, y, nm, 11.5, col, "end", mono=True))
        if sc is None:
            b.append(text(300, y, "…", 11.5, MUTED, "end", mono=True))
            b.append(text(470, y, "…", 11.5, MUTED, "end", mono=True))
            continue
        b.append(text(300, y, f"{sc:.2f}", 11.5, INK, "end", mono=True))
        b.append(text(470, y, f"{wt:.4f}", 11.5, col, "end", mono=True))
        b.append(f"<rect x='{482}' y='{y-9}' width='{max(1.5, 200*wt):.1f}' "
                 f"height='11' rx='2' fill='{col}' opacity='0.7'/>")
    b.append(text(24, 316, "A gap of 7.6 in the raw scores becomes 0.999 against "
                  "0.0005 — softmax turns a preference into a near-decision.",
                  11.5, MUTED))

    # the output
    b.append(box(24, 340, W - 48, 74, fill=CARD))
    b.append(text(40, 362, "The head’s answer for “muscle”", 12.5, INK, weight="600"))
    b.append(text(40, 382, "= 0.999 × value(smooth)  +  0.0005 × value(muscle)  +  … "
                  "→ 64 numbers", 11.5, INK, mono=True))
    b.append(text(40, 402, "Almost literally: “for this round, become the word before "
                  "me.”", 11.5, MUTED))

    b.append(line(24, 434, W - 24, 434, GRID, 1))
    b.append(text(24, 456, "All twelve heads do this at once on their own 64-wide "
                  "slices. Their outputs are laid end to end — 12 × 64 = 768 — and "
                  "passed through a", 12, INK))
    b.append(text(24, 474, "fourth matrix that mixes them back together. That "
                  "combined result is what the token becomes, and the next layer "
                  "starts again from it.", 12, MUTED))
    b.append(text(24, 502, "So a head is not a component you could point at in "
                  "isolation — it is a 64-column slice of three shared matrices, plus "
                  "the comparison that slice performs.", 12, MUTED))
    return svg(W, H, "".join(b), "What one head computes")


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
    ("map", fig_map),
    ("two-directions", fig_directions),
    ("token-in-context", fig_context),
    ("head-mechanics", fig_head_mechanics),   # what a head is…
    ("layers-and-heads", fig_heads),          # …then what all 144 of them do
    ("tokens-to-vector", fig_tokens),   # the recap, after all five steps are told
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
