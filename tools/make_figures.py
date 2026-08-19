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
def fig_tokens():
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
    b = [text(24, 30, "1 · A sentence becomes one vector", 15, INK, weight="600")]

    b.append(text(24, 60, "“TRPV1 mediates vasodilation in arterial smooth muscle.”",
                  13, MUTED, style="italic"))

    # token chips
    x = 24
    b.append(text(24, 92, "split into word pieces the model knows", 11, MUTED))
    for t in toks:
        w = 9 + 7.3 * len(t)
        fill = "#e4ecf7" if t.startswith("[") else CARD
        b.append(box(x, 102, w, 26, fill=fill))
        b.append(text(x + w / 2, 119, t, 11, INK, "middle", mono=True))
        x += w + 6
    b.append(text(x + 4, 119, "10 tokens", 11, MUTED))

    # per-token vectors
    b.append(text(24, 162, "each token becomes 768 numbers", 11, MUTED))
    y = 174
    for name, vals in rows:
        b.append(text(150, y + 14, name, 11, INK, "end", mono=True))
        b.append(box(162, y, 300, 20, fill="#ffffff"))
        b.append(text(172, y + 14, vals, 10.5, MUTED, mono=True))
        b.append(text(470, y + 14, "…" if vals != "…" else "", 10.5, MUTED, mono=True))
        y += 24

    # the averaging bracket
    b.append(f"<path d='M486 176 q10 0 10 12 v40 q0 12 10 12 q-10 0 -10 12 v40 "
             f"q0 12 -10 12' fill='none' stroke='{GRID}' stroke-width='1.5'/>")
    b.append(text(506, 258, "average", 12, INK, weight="600"))
    b.append(text(506, 274, "over the real tokens", 10.5, MUTED))
    b.append(arrow(586, 253, 626, 253, MUTED, 1.8))

    b.append(text(636, 200, "one vector for the whole sentence", 11, MUTED))
    b.append(box(636, 210, 216, 22, fill="#ffffff"))
    b.append(text(646, 225, "-0.02  -0.10  -0.01  -0.16  …", 10.5, INK, mono=True))
    b.append(text(636, 254, "length 14.08", 11, MUTED, mono=True))
    b.append(arrow(700, 262, 700, 288, MUTED, 1.8))
    b.append(text(712, 280, "scale to length 1", 11, INK))
    b.append(box(636, 296, 216, 22, fill="#e7f3ec", stroke="#b7dfc6"))
    b.append(text(646, 311, "-0.0011  -0.0069  -0.0010  …", 10.5, GREEN, mono=True))
    b.append(text(636, 338, "length 1.00 — only the direction is kept", 11, GREEN))

    b.append(line(24, 366, W - 24, 366, GRID, 1))
    b.append(text(24, 390, "Every paper in the corpus goes through exactly this, so "
                  "each one ends up as a single arrow of length 1.", 12, INK))
    b.append(text(24, 410, "Papers about similar things end up pointing in similar "
                  "directions — that is the whole basis of the relevance gate.", 12, MUTED))
    return svg(W, H, "".join(b), "A sentence becomes one vector")


# --------------------------------------------------------------------------- #
# 2. Scoring by angle, with real cosines
# --------------------------------------------------------------------------- #
def fig_angle():
    W, H = 880, 400
    cx, cy, R = 236, 236, 152
    b = [text(24, 30, "2 · Candidates are scored by the angle they make with the topic",
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
def fig_shared():
    W, H = 880, 350
    rows = [("TRPV1 mediates vasodilation in arterial smooth muscle.", -0.967),
            ("Wheat yield responses to nitrogen fertiliser in soils.", -0.965),
            ("Microglia mediate forgetting via synaptic elimination.", -0.973),
            ("The cat sat on the mat.", -0.954)]
    b = [text(24, 30, "3 · Why every score comes out near 0.99", 15, INK, weight="600")]
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
def fig_centroid():
    W, H = 880, 404
    b = [text(24, 30, "4 · The seeds define the direction to score against", 15, INK,
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
def fig_rocchio():
    W, H = 880, 380
    b = [text(24, 30, "5 · Learning what the topic is not", 15, INK, weight="600")]
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


def fig_topk():
    W, H = 880, 420
    L, Rr, T, B = 78, 560, 84, 320
    ymax = 0.34
    b = [text(24, 30, "6 · What K trades", 15, INK, weight="600")]
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
def fig_directions():
    """Dot counts are proportional: one dot = 150 papers, so the 20x really shows."""
    W, H = 880, 380
    PER_DOT = 150
    back, fwd = 2342, 47000
    b = [text(24, 30, "7 · The two directions are not the same size", 15, INK,
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


if __name__ == "__main__":
    write("01-tokens-to-vector.svg", fig_tokens())
    write("02-scoring-by-angle.svg", fig_angle())
    write("03-shared-direction.svg", fig_shared())
    write("04-seed-direction.svg", fig_centroid())
    write("05-negative-term.svg", fig_rocchio())
    write("06-top-k-tradeoff.svg", fig_topk())
    write("07-two-directions.svg", fig_directions())
