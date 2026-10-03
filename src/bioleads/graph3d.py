"""Rotatable 3D network rendering with Plotly.

pyvis (vis.js) is 2D only, so for a drag-to-rotate view we lay the graph out in
3D ourselves and draw it as Plotly Scatter3d traces: one trace for the edges
(lines) and one for the nodes (markers). The result is a self-contained HTML
file you can rotate, zoom, and hover.

The layout is isotropic and hub-centred rather than force-directed: the
highest-degree node sits at the origin and the rest of the graph grows outward
in shells, one per hop away from it. See `_isotropic_layout`.

Generic on purpose — both the entity co-occurrence graph and the paper citation
graph render through `write_graph_3d` with different size / color / hover hooks.
"""
from __future__ import annotations

import math
import warnings

import networkx as nx

# A qualitative palette for cluster coloring (cycled if there are more clusters).
_PALETTE = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b",
    "#e377c2", "#7f7f7f", "#bcbd22", "#17becf", "#aec7e8", "#ffbb78",
    "#98df8a", "#ff9896", "#c5b0d5", "#c49c94", "#f7b6d2", "#dbdb8d",
]



# The angle between successive points of a Fibonacci sphere. Stepping by it
# spreads any number of points over a sphere with near-uniform density and no
# clustering at the poles, which is what makes each shell isotropic.
_GOLDEN_ANGLE = math.pi * (3.0 - math.sqrt(5.0))


def _hub(g: nx.Graph, nodes: list) -> object:
    """The highest-degree node; ties broken on the node itself, not on order."""
    return min(nodes, key=lambda n: (-g.degree(n), str(n)))


def _shell_directions(m: int, phase: float, tilt: float) -> list[tuple]:
    """`m` unit vectors spread over the sphere, stepped by the golden angle.

    The tilt rotates the whole set about the x-axis. For a large shell that
    changes nothing -- a uniform set stays uniform under rotation -- but a shell
    holding a single node sits exactly on the equator without it, and a graph
    with many one-node shells would put every one of them on the same plane and
    render flat.
    """
    out = []
    for i in range(m):
        y = 1.0 - 2.0 * (i + 0.5) / m
        rho = math.sqrt(max(0.0, 1.0 - y * y))
        theta = _GOLDEN_ANGLE * i + phase
        x, z = rho * math.cos(theta), rho * math.sin(theta)
        y, z = (y * math.cos(tilt) - z * math.sin(tilt),
                y * math.sin(tilt) + z * math.cos(tilt))
        out.append((x, y, z))
    return out


def _isotropic_layout(g: nx.Graph, seed: int = 0) -> dict:
    """Lay `g` out in 3D, ranked outward from its highest-degree node.

    The highest-degree node is the root and sits at the origin. Every other node
    is placed on a shell chosen by its degree: the next-highest degree present
    forms the first shell, the one after that the second, and so on out to the
    least connected nodes on the rim. Radius is therefore a rank, and reads
    directly as "how well connected" -- the centre of the picture is the centre
    of the network, and you can see the ranking fall away from it.

    Degree is total and undirected: a citation network is a DiGraph, and
    counting only outgoing arrows would rank a much-cited paper as though it
    had no connections at all. It is the same measure the Min degree filter
    uses, so the two controls agree about what a connection is.

    Ties share a shell, which is what makes this a ranking rather than a
    spiral -- nodes of equal degree are equally central and are drawn that way.
    Within a shell the directions are spread by the golden angle, so each is
    covered evenly in every direction rather than bunching at the poles, and
    members are ordered by their best-connected neighbour so the nodes hanging
    off one hub stay together on the shell.

    Nothing is special-cased for detached pieces: an isolated paper has degree
    zero, which puts it on the outermost shell, where it belongs.

    Fully deterministic: no simulation, no random start. `seed` only turns the
    whole figure, which is occasionally useful for a screenshot.
    """
    nodes = list(g.nodes)
    und = g.to_undirected(as_view=True) if g.is_directed() else g
    root = _hub(g, nodes)

    by_degree: dict[int, list] = {}
    for n in nodes:
        if n != root:
            by_degree.setdefault(g.degree(n), []).append(n)

    def anchor(n):
        """The node's best-connected neighbour: its handle on the shell inside."""
        nb = [(-g.degree(m), str(m)) for m in und.neighbors(n) if m != n]
        return min(nb)[1] if nb else ""

    pos = {root: (0.0, 0.0, 0.0)}
    ranks = sorted(by_degree, reverse=True)
    for i, deg in enumerate(ranks, start=1):
        members = sorted(by_degree[deg], key=lambda n: (anchor(n), str(n)))
        r = i / len(ranks)
        for n, d in zip(members, _shell_directions(
                len(members), 0.7 * i + 0.1 * seed, 1.1 * i + 0.37 * seed)):
            pos[n] = (r * d[0], r * d[1], r * d[2])
    return pos


def write_graph_3d(
    g: nx.Graph,
    path: str,
    *,
    title: str = "bioleads network (3D)",
    size_attr: str,
    seed: int = 0,
    groups: dict | None = None,
    color_attr: str | None = None,
    colors: dict | None = None,
    stops: list | None = None,
    hover=None,
    directed: bool = False,
) -> str | None:
    """Write a 3D Plotly rendering of `g` to `path`; return the path (or None).

    Parameters
    ----------
    size_attr   node attribute used for marker size (e.g. "count").
    groups      {node: cluster_id} → discrete per-cluster colors (takes priority).
    stops       tour stops from `citations.tour_stops`, for the guided tour.
    colors      {node: css color} → explicit per-node colors. Outranks both of
                the above, because it carries a meaning the caller has already
                decided (query-term match) rather than one derived here.
    color_attr  node attribute for a continuous colorscale when no `groups`.
    hover       callable(node, data)->str for the marker tooltip (HTML allowed).
    directed    annotate the title that edges are directed (A→B = A cites B).

    Returns None (with a warning) if Plotly isn't installed — the caller keeps
    its 2D output and the 3D view is simply skipped.
    """
    try:
        import plotly.graph_objects as go
    except ImportError:
        warnings.warn(
            'the 3D graph needs Plotly. Install with: pip install "bioleads[viz]"')
        return None

    nodes = list(g.nodes)
    if not nodes:  # nothing to draw, but still emit a valid (empty) page
        go.Figure().write_html(path, include_plotlyjs=True, full_html=True)
        return path

    pos = _isotropic_layout(g, seed=seed)

    # --- edges: one line trace, segments separated by None ----------------- #
    ex, ey, ez = [], [], []
    for a, b in g.edges():
        (xa, ya, za), (xb, yb, zb) = pos[a], pos[b]
        ex += [xa, xb, None]
        ey += [ya, yb, None]
        ez += [za, zb, None]
    edge_trace = go.Scatter3d(
        x=ex, y=ey, z=ez, mode="lines",
        line=dict(color="rgba(90,110,150,0.55)", width=1.5),
        hoverinfo="none", showlegend=False,
    )

    # --- nodes: marker trace, sized by size_attr, colored by group/attr ---- #
    xs = [pos[n][0] for n in nodes]
    ys = [pos[n][1] for n in nodes]
    zs = [pos[n][2] for n in nodes]
    sizes = [float(g.nodes[n].get(size_attr) or 0) for n in nodes]
    smax = max(sizes) or 1.0
    marker_sizes = [7 + 19 * (s / smax) for s in sizes]
    hover = hover or (lambda n, d: str(n))
    texts = [hover(n, g.nodes[n]) for n in nodes]

    # A dark outline keeps every node visible (even faint, low-value ones)
    # against the white background.
    marker = dict(size=marker_sizes, opacity=0.95,
                  line=dict(width=0.8, color="rgba(40,40,40,0.65)"))
    if colors:
        marker["color"] = [colors.get(n, "#2b6cb0") for n in nodes]
    elif groups:
        marker["color"] = [_PALETTE[(groups.get(n, 0)) % len(_PALETTE)] for n in nodes]
    elif color_attr:
        marker["color"] = [float(g.nodes[n].get(color_attr) or 0) for n in nodes]
        # Floor the low end at a clearly visible mid-blue (not near-white) so the
        # "bottom" of the distribution doesn't vanish; high values go dark navy.
        marker["colorscale"] = [[0.0, "#6baed6"], [1.0, "#08306b"]]
        marker["showscale"] = True
        marker["colorbar"] = dict(title=color_attr.replace("_", " "))
    else:
        marker["color"] = "#2b6cb0"

    node_trace = go.Scatter3d(
        x=xs, y=ys, z=zs, mode="markers",
        marker=marker, hoverinfo="text", hovertext=texts, showlegend=False,
    )

    heading = title + ("  ·  edges are directed (A→B = A cites B)" if directed else "")
    fig = go.Figure(data=[edge_trace, node_trace])
    # visible=False hides ticks, gridlines, AND the bounding-box wireframe that
    # Plotly otherwise flashes up while you drag to rotate.
    axis = dict(visible=False)
    # Pinned ranges, so a normalised position is an exact camera centre. See
    # `scene_ranges`.
    _r = scene_ranges(pos)
    axes = ([dict(axis, range=list(r)) for r in _r] if _r else [axis] * 3)
    fig.update_layout(
        title=heading,
        # aspectmode="cube" fixes the scene to a unit cube, which is what makes
        # a node's normalised position a camera centre that actually centres it.
        scene=dict(xaxis=axes[0], yaxis=axes[1], zaxis=axes[2],
                   dragmode="orbit", aspectmode="cube"),
        margin=dict(l=0, r=0, t=40, b=0),
        showlegend=False,
        paper_bgcolor="white",
    )
    fig.write_html(path, include_plotlyjs=True, full_html=True)
    _inject_tour_3d(path, stops, pos)
    return path


SCENE_PAD = 0.06          # fraction of each axis span, so markers aren't clipped
# How much of the graph a tour stop shows, as a fraction of the full span.
TOUR_WINDOW = 0.25
# **Narrowing the window is only half of a zoom.** In 2D, vis.js scales the
# whole canvas, so the nodes and edges grow as the view closes in, and that
# growth is most of what reads as "zoomed in". Plotly's markers are sized in
# SCREEN pixels, so a narrower range moves nodes apart without making any of
# them bigger -- the view crops but never magnifies, which is why three rounds
# of shrinking the window still did not look like the 2D tour.
#
# So the markers and edges are scaled by hand over the flight, to match.
TOUR_MAGNIFY = 3.5        # node markers at the end of a flight
TOUR_EDGE_WIDTH = 3.2     # edge width at the end of a flight
TOUR_RING = 30            # the highlight ring, unzoomed
TOUR_RING_ZOOMED = 110


def scene_ranges(pos):
    """Explicit ``[lo, hi]`` per axis, or None when there are no positions.

    **This has to be pinned, not left to autorange.** Plotly maps each axis's
    RANGE onto the scene cube, and ``scene.camera.center`` is given in that
    cube's -1..1 space. With autorange the range is whatever Plotly decides,
    so a position normalised against the data's own min and max is only
    approximately the right camera centre. At a close standoff "approximately"
    puts the focus node out of frame.

    The same ranges are used to draw the figure and to convert positions for
    the camera, which is the only way the two can agree.
    """
    raw = [list(map(float, p)) for p in (pos or {}).values()]
    if not raw:
        return None
    out = []
    for i in range(3):
        lo = min(p[i] for p in raw)
        hi = max(p[i] for p in raw)
        pad = ((hi - lo) or 1.0) * SCENE_PAD
        out.append([lo - pad, hi + pad])
    return out


def _inject_tour_3d(path: str, stops, pos) -> None:
    """Give a 3D view the same tour and record panel as the 2D one.

    There is no physics here — Plotly draws a fixed scene — so there is nothing
    to pause. What was missing is everything else: a way to go to the most
    connected node, and the node's record where you can read it.

    The camera is moved by ``Plotly.relayout`` on ``scene.camera``, looking at
    the node from a fixed offset. Plotly has no camera tween, so the move is
    stepped here over :data:`TOUR_FLIGHT_MS` rather than jumping, which is the
    difference between a tour and a slideshow.
    """
    if not stops:
        return
    try:
        from .citations import (RECORD_HELP, TOUR_DWELL_MS, TOUR_FLIGHT_MS,
                                TOUR_HIGHLIGHT)
    except Exception:          # pragma: no cover - citations is always present
        return
    # **The tour moves the axis ranges, not the camera.** Three rounds were
    # spent shortening `scene.camera.eye` to zoom in, and none of them did
    # anything: gl3d clamps the camera's distance, so every standoff below
    # about 0.5 renders identically. `scene.camera.center` is no better -- it
    # is in a normalised space of its own, which is a second coordinate system
    # to get wrong, and it was getting it wrong.
    #
    # Narrowing each axis range around the node does both jobs exactly and in
    # ONE space, the data's own: the node is the centre of the cube, which is
    # what the default camera looks at, and the window width is the zoom. Every
    # other piece of the tour (the highlight trace, the annotation) is already
    # in data coordinates, so there is now only one.
    raw = {n: list(map(float, p)) for n, p in (pos or {}).items()}
    bounds = scene_ranges(pos)
    stops = [dict(st, xyz=raw.get(st["id"])) for st in stops
             if raw.get(st["id"])]
    if not stops:
        return

    if not bounds:
        return

    block = """
<style>
  #bl3-panel {position:fixed; top:12px; right:14px; z-index:9999; width:390px;
    font:12px/1.45 system-ui,sans-serif; background:#f8fafc;
    border:1px solid #d7dee6; border-radius:6px; padding:7px 9px;
    box-shadow:0 1px 3px rgba(0,0,0,.12); color:#1f2a36}
  #bl3-panel button {font:12px/1.3 system-ui,sans-serif; cursor:pointer;
    border:1px solid #b9c6bd; background:#fff; border-radius:4px;
    padding:3px 9px; margin-right:5px}
  #bl3-help {cursor:help; color:#5b6b7c; border-bottom:1px dotted #9aa8b6}
  #bl3-info {display:none; margin-top:7px; max-height:60vh; overflow-y:auto;
    border-top:1px solid #d7dee6; padding-top:6px}
  #bl3-info .h {margin-bottom:5px; color:#5b6b7c}
  #bl3-info table {border-collapse:collapse; width:100%}
  #bl3-info th {text-align:left; vertical-align:top; font-weight:600;
    color:#5b6b7c; padding:2px 8px 2px 0; white-space:nowrap}
  #bl3-info td {vertical-align:top; padding:2px 0; word-break:break-word}
</style>
<div id="bl3-panel">
  <button id="bl3-play">Play tour</button>
  <button id="bl3-next">Next</button>
  <button id="bl3-reset">Reset view</button>
  <span id="bl3-help" title="__HELP__">&#9432;</span>
  <div id="bl3-info"></div>
</div>
<script>
(function () {
  var STOPS = __STOPS__, FLIGHT = __FLIGHT__, DWELL = __DWELL__;
  var BOUNDS = __BOUNDS__, WINDOW = __WINDOW__, AX = ["x", "y", "z"];
  var MAG = __MAGNIFY__, EDGE_W = __EDGE_W__;
  var RING = __RING__, RING_Z = __RING_Z__;
  var BASE = null, shown = 1;
  var HOME = {eye: {x: 1.25, y: 1.25, z: 1.25}, center: {x: 0, y: 0, z: 0}};
  var i = -1, playing = false, timer = null, anim = null;
  function gd() { return document.querySelector(".plotly-graph-div"); }
  function ease(t) { return t < 0.5 ? 4*t*t*t : 1 - Math.pow(-2*t + 2, 3)/2; }
  function ranges() {
    // Whatever the scene is showing now, so a flight starts where the last one
    // left off instead of jumping back to the full view first.
    var sc = gd()._fullLayout.scene;
    return AX.map(function (a, i) {
      var r = sc[a + "axis"] && sc[a + "axis"].range;
      return r ? [r[0], r[1]] : [BOUNDS[i][0], BOUNDS[i][1]];
    });
  }
  function windowAt(p) {
    // A box of the same size on every stop, centred on the node. Equal widths
    // are what make the stops comparable: the node is always the middle of the
    // view and always at the same magnification.
    return AX.map(function (a, i) {
      var h = (BOUNDS[i][1] - BOUNDS[i][0]) * WINDOW / 2;
      return [p[i] - h, p[i] + h];
    });
  }
  function base() {
    // The sizes the figure was drawn with, captured once. Reading them back
    // from the traces after a magnification would compound it.
    var el = gd();
    if (!BASE) {
      var m = el.data[1].marker, ln = el.data[0].line;
      BASE = {size: [].concat(m.size), width: ln.width, color: ln.color};
    }
    return BASE;
  }
  function magnify(f) {
    // Markers are screen-sized, so they have to be grown by hand to match the
    // narrowing window. Applied in steps rather than every frame: on a large
    // graph a per-frame restyle rewrites the whole size buffer and the flight
    // stutters, and at this speed the steps are not visible.
    var el = gd(), b = base();
    if (Math.abs(f - shown) < 0.08 && f !== 1 && f !== MAG) { return; }
    shown = f;
    var t = MAG > 1 ? (f - 1) / (MAG - 1) : 0;
    Plotly.restyle(el, {"marker.size": [b.size.map(function (v) {
      return v * f; })]}, [1]);
    Plotly.restyle(el, {"line.width": b.width + (EDGE_W - b.width) * t}, [0]);
    if (el.data.length > 2) {
      Plotly.restyle(el, {"marker.size": [[RING + (RING_Z - RING) * t]]},
                     [el.data.length - 1]);
    }
  }
  function glide(to, done, mag) {
    var el = gd(), from = ranges(), t0 = performance.now();
    cancelAnimationFrame(anim);
    (function frame(now) {
      var t = Math.min(1, (now - t0) / FLIGHT), e = ease(t), u = {};
      for (var i = 0; i < 3; i++) {
        u["scene." + AX[i] + "axis.range"] = [
          from[i][0] + (to[i][0] - from[i][0]) * e,
          from[i][1] + (to[i][1] - from[i][1]) * e];
      }
      Plotly.relayout(el, u);
      if (mag) { magnify(mag[0] + (mag[1] - mag[0]) * e); }
      if (t < 1) { anim = requestAnimationFrame(frame); } else if (done) { done(); }
    })(t0);
  }
  function flyTo(p, done) { glide(windowAt(p), done, [shown, MAG]); }
  function annotate(s) {
    // A scene annotation is anchored in the data, so it travels with the node
    // as the camera moves instead of sitting in a corner of the window.
    var lines = [];
    for (var r = 0; r < s.record.length; r++) {
      lines.push("<b>" + s.record[r][0] + "</b>  " + s.record[r][1]);
    }
    Plotly.relayout(gd(), {"scene.annotations": [{
      x: s.xyz[0], y: s.xyz[1], z: s.xyz[2],
      text: lines.join("<br>"), showarrow: true, arrowhead: 2, arrowsize: 1,
      arrowwidth: 1.2, arrowcolor: "#5b6b7c", ax: 90, ay: 0,
      align: "left", xanchor: "left", bgcolor: "rgba(255,255,255,0.94)",
      bordercolor: "#d7dee6", borderwidth: 1, borderpad: 6,
      font: {size: 11, color: "#1f2a36"}
    }]});
  }
  function unannotate() { Plotly.relayout(gd(), {"scene.annotations": []}); }
  function light(p) {
    // A ring drawn on top of the node. Recoloring the node itself would mean
    // rewriting the whole marker array on every stop.
    var el = gd();
    var trace = {x: [p[0]], y: [p[1]], z: [p[2]], mode: "markers",
                 type: "scatter3d", hoverinfo: "skip", showlegend: false,
                 marker: {size: RING, color: "__HILITE__", opacity: 0.55,
                          line: {width: 2, color: "#7a0f37"}}};
    if (el.data.length > 2) { Plotly.deleteTraces(el, el.data.length - 1); }
    Plotly.addTraces(el, trace);
  }
  function unlight() {
    var el = gd();
    if (el.data.length > 2) { Plotly.deleteTraces(el, el.data.length - 1); }
  }
  function show(k) {
    var s = STOPS[k]; if (!s) { return; }
    light(s.xyz);
    // The ring is a brand-new trace, drawn at its unzoomed size. If the view
    // is already magnified from the previous stop it has to be brought up to
    // match, or the marker it is meant to circle sits outside it.
    magnify(shown);
    unannotate();          // the old card must not ride along during the flight
    var rows = "";
    for (var r = 0; r < s.record.length; r++) {
      var v = s.record[r][1];
      if (/^https?:[/][/]/.test(v)) {
        v = '<a href="' + v + '" target="_blank" rel="noopener">' + v + "</a>";
      }
      rows += "<tr><th>" + s.record[r][0] + "</th><td>" + v + "</td></tr>";
    }
    var info = document.getElementById("bl3-info");
    info.innerHTML = '<div class="h"><b>' + (k + 1) + " of " + STOPS.length +
      "</b> &middot; " + s.degree + " connection(s) &middot; " +
      (s.label || "") + "</div>";
    info.style.display = "block";
    // The label waits for the camera. Annotating first means reading a card
    // that is sliding across the screen.
    flyTo(s.xyz, function () { annotate(s); });
  }
  function step() {
    i = (i + 1) % STOPS.length;
    show(i);
    if (playing) { timer = setTimeout(step, DWELL); }
  }
  var play = document.getElementById("bl3-play");
  document.getElementById("bl3-next").addEventListener("click", function () {
    playing = false; clearTimeout(timer); play.textContent = "Play tour"; step();
  });
  play.addEventListener("click", function () {
    playing = !playing;
    play.textContent = playing ? "Stop tour" : "Play tour";
    clearTimeout(timer);
    if (playing) { step(); }
  });
  document.getElementById("bl3-reset").addEventListener("click", function () {
    playing = false; clearTimeout(timer); cancelAnimationFrame(anim);
    play.textContent = "Play tour";
    unlight();
    unannotate();
    document.getElementById("bl3-info").style.display = "none";
    glide(BOUNDS, null, [shown, 1]);
    Plotly.relayout(gd(), {"scene.camera": HOME});
  });
})();
</script>
"""
    import json as _json
    block = (block.replace("__STOPS__", _json.dumps(stops))
                  .replace("__BOUNDS__", _json.dumps(bounds))
                  .replace("__WINDOW__", str(TOUR_WINDOW))
                  .replace("__MAGNIFY__", str(TOUR_MAGNIFY))
                  .replace("__EDGE_W__", str(TOUR_EDGE_WIDTH))
                  .replace("__RING_Z__", str(TOUR_RING_ZOOMED))
                  .replace("__RING__", str(TOUR_RING))
                  .replace("__FLIGHT__", str(TOUR_FLIGHT_MS))
                  .replace("__DWELL__", str(TOUR_DWELL_MS))
                  .replace("__HELP__", RECORD_HELP.replace('"', "&quot;"))
                  .replace("__HILITE__", TOUR_HIGHLIGHT))
    try:
        with open(path, encoding="utf-8") as fh:
            html = fh.read()
    except OSError:
        return
    if "bl3-panel" in html:
        return
    html = (html.replace("</body>", block + "</body>", 1) if "</body>" in html
            else html + block)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)
