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
        # Quiet, but it has to survive the zoomed-out view: at 0.40 alpha the
        # edges disappeared entirely once Reset view pulled back.
        line=dict(color="rgba(103,126,166,0.55)", width=1.4),
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
                  line=dict(width=1.2, color="rgba(255,255,255,0.9)"))
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
        marker["colorbar"] = dict(
            title=dict(text=color_attr.replace("_", " "),
                       font=dict(size=11, color="#5b6b7c")),
            thickness=10, len=0.45, x=0.99, xpad=0, outlinewidth=0,
            tickfont=dict(size=10, color="#5b6b7c"))
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
        title=dict(text=heading, font=dict(size=15, color="#1f2a36"), x=0.012,
                   xanchor="left"),
        # The scene starts as a unit cube. A tour stop switches aspectmode to
        # "manual" and grows the ratio, which is the zoom; "Reset view" puts it
        # back to 1. Note that `scene.camera.center` is then in units of HALF
        # the aspect ratio, which `centre()` in the injected script applies.
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
# **The zoom is `scene.aspectratio`, and nothing is ever hidden.**
#
# Narrowing the axis ranges was tried first and was wrong: Plotly drops
# whatever falls outside a range, so zooming in deleted most of the network.
# 2D never does that -- vis.js moves a camera over a graph that stays whole --
# and a tour that culls the thing it is touring is worse than no tour.
#
# Growing the aspect ratio scales the scene box instead. The camera holds its
# distance, so the graph gets bigger and the parts that no longer fit simply
# fall outside the view, exactly as they do when 2D zooms. Every node is still
# there to pan back to.
TOUR_ZOOM_3D = 7.0
# **Scaling the scene is still only half of a zoom.** vis.js scales the whole
# canvas, so in 2D the nodes and edges grow as the view closes in, and that
# growth is most of what reads as "zoomed in". Plotly's markers are sized in
# SCREEN pixels, so spreading the scene apart leaves every marker the size it
# was. They are scaled by hand over the flight to match.
# Measured against the 2D tour on a 1096px canvas, where the nodes run 32px
# to 90px across and the focused one is 82px. 3.5x puts the 3D markers at
# 24-91px, which is the same graph at the same size.
TOUR_MAGNIFY = 3.5        # node markers at the end of a flight
TOUR_EDGE_WIDTH = 2.6     # edge width at the end of a flight
# **The focus node is a real sphere in the scene, not a marker and not an
# overlay.** Markers are sized in screen pixels, so they never grow as the
# camera comes in; an HTML disc does grow, but it is a flat sticker pasted
# over the picture that does not rotate, does not shade and hides whatever is
# behind it. A Mesh3d sphere lives in the data: the zoom makes it bigger the
# way moving towards something makes it bigger, and the orbit turns it.
#
# Its radius is a fraction of the graph's span, PER AXIS, so it is round on
# screen. The axes have different data ranges but are drawn into the same
# cube, so a sphere of equal radius in data units renders as an ellipsoid.
TOUR_SPHERE_FRACTION = 0.072
TOUR_SPHERE_SEGMENTS = (48, 24)
# How far the camera swings around the node while it flies in. A tour that
# only dollies looks like a slideshow; a little rotation reads as moving
# through the network.
TOUR_ORBIT_DEG = 55.0


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


def unit_sphere(segments=TOUR_SPHERE_SEGMENTS):
    """A unit sphere as three ``(lat+1) x (lon+1)`` grids for a ``Surface``.

    **Not a ``Mesh3d``.** A mesh renders its own triangle edges here, so the
    focus node came out as a wireframe globe, and no combination of
    ``contour.show``, ``flatshading`` or the normals epsilons removed them. A
    ``Surface`` over the same parametric grid shades smoothly with no lines.

    Built in Python so the page carries the geometry rather than the code to
    make it, and so the grid can be checked by a test instead of by looking at
    a render.
    """
    import math

    lon, lat = segments
    xs, ys, zs = [], [], []
    for a in range(lat + 1):                      # pole to pole
        phi = math.pi * a / lat
        sp, cp = math.sin(phi), math.cos(phi)
        rx, ry, rz = [], [], []
        for b in range(lon + 1):                  # the seam column repeats
            th = 2 * math.pi * b / lon
            rx.append(round(sp * math.cos(th), 4))
            ry.append(round(sp * math.sin(th), 4))
            rz.append(round(cp, 4))
        xs.append(rx)
        ys.append(ry)
        zs.append(rz)
    return xs, ys, zs


def _sphere_payload():
    xs, ys, zs = unit_sphere()
    return {"x": xs, "y": ys, "z": zs}


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
        from .citations import (CARD_CSS, CARD_JS, RECORD_HELP,
                                TOUR_DWELL_MS, TOUR_FLIGHT_MS, TOUR_HIGHLIGHT)
    except Exception:          # pragma: no cover - citations is always present
        return
    # **Two coordinate systems, and mixing them up is the bug to watch for.**
    #
    # `xyz` is the node's DATA position. The highlight ring is a Scatter3d
    # trace and the record is a scene annotation, and both of those live in the
    # data -- which is what makes the annotation travel with its node.
    #
    # `cam` is the same point in the scene's own normalised -1..1 space, which
    # is the only thing `scene.camera.center` accepts. Handing a trace the
    # camera form once put a loose marker in the scene attached to nothing, so
    # the two are named apart and converted here rather than in JavaScript.
    #
    # The conversion is exact only because `scene_ranges` pins the axis ranges
    # the figure is drawn with; against an autoranged axis it would be a guess.
    raw = {n: list(map(float, p)) for n, p in (pos or {}).items()}
    bounds = scene_ranges(pos)
    cam = {}
    if bounds:
        cam = {n: [2 * (q[i] - bounds[i][0]) /
                   ((bounds[i][1] - bounds[i][0]) or 1.0) - 1 for i in range(3)]
               for n, q in raw.items()}
    stops = [dict(st, xyz=raw.get(st["id"]), cam=cam.get(st["id"]))
             for st in stops if raw.get(st["id"]) and cam.get(st["id"])]
    if not stops:
        return

    if not bounds:
        return

    block = """
<style>
  #bl3-panel {position:fixed; bottom:16px; left:16px; z-index:9999;
    font:12px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,
      sans-serif;
    background:rgba(255,255,255,.88); -webkit-backdrop-filter:blur(8px);
    backdrop-filter:blur(8px); border:0; border-radius:10px; padding:8px 10px;
    box-shadow:0 4px 16px rgba(20,32,48,.10); color:#1f2a36}
  #bl3-panel button {font:inherit; cursor:pointer; border:0;
    background:rgba(31,42,54,.06); color:#1f2a36; border-radius:7px;
    padding:5px 11px; margin-right:5px; transition:background .15s}
  #bl3-panel button:hover {background:rgba(31,42,54,.12)}
  #bl3-help {cursor:help; color:#8795a4}
  #bl3-info {display:none; margin-top:8px; color:#5b6b7c;
    letter-spacing:.01em}
__CARD_CSS__
  /* The record sits at the right edge, clear of the sphere however large it
     grows, and fades in when the camera lands. */
  #bl3-card {display:none; position:fixed; right:28px; top:50%; z-index:10000;
    width:344px; max-height:72vh; overflow-y:auto;
    background:rgba(255,255,255,.82); -webkit-backdrop-filter:blur(18px)
      saturate(140%); backdrop-filter:blur(18px) saturate(140%);
    border:1px solid rgba(255,255,255,.65); border-radius:16px;
    padding:18px 20px 16px; color:#16202b;
    box-shadow:0 18px 50px rgba(16,26,40,.18), 0 2px 6px rgba(16,26,40,.06);
    opacity:0; transform:translateY(-50%) translateX(14px);
    transition:opacity .5s ease-out, transform .6s cubic-bezier(.2,.7,.3,1)}
  #bl3-card.on {opacity:1; transform:translateY(-50%) translateX(0)}
</style>
<div id="bl3-panel">
  <button id="bl3-play">Play tour</button>
  <button id="bl3-next">Next</button>
  <button id="bl3-reset">Reset view</button>
  <span id="bl3-help" title="__HELP__">&#9432;</span>
  <div id="bl3-info"></div>
</div>
<div id="bl3-card" class="bl-card"></div>
<script>
__CARD_JS__
(function () {
  var STOPS = __STOPS__, FLIGHT = __FLIGHT__, DWELL = __DWELL__;
  var ZOOM = __ZOOM3D__, MAG = __MAGNIFY__, EDGE_W = __EDGE_W__;
  var SPH = __SPHERE__, SPAN = __SPAN__, SR = __SPHERE_R__;
  var ORBIT = __ORBIT__ * Math.PI / 180;
  var BASE = null, shown = 1;
  var atK = 1, atC = {x: 0, y: 0, z: 0};
  var HOME = {eye: {x: 1.25, y: 1.25, z: 1.25}, center: {x: 0, y: 0, z: 0}};
  var i = -1, playing = false, timer = null, anim = null;
  function gd() { return document.querySelector(".plotly-graph-div"); }
  function ease(t) { return t < 0.5 ? 4*t*t*t : 1 - Math.pow(-2*t + 2, 3)/2; }
  function heading() {
    // The direction the viewer is currently looking from, at a fixed length.
    // Held constant through a flight so a stop pans and zooms without also
    // spinning, and so a scene the viewer has dragged around keeps the angle
    // they chose instead of snapping back to the default corner.
    var c = gd()._fullLayout.scene.camera, e = c.eye, m = c.center ||
      {x: 0, y: 0, z: 0};
    var v = [e.x - m.x, e.y - m.y, e.z - m.z];
    var n = Math.sqrt(v[0]*v[0] + v[1]*v[1] + v[2]*v[2]) || 1;
    var L = 1.25 * Math.sqrt(3);          // the default view's distance
    return [v[0]/n*L, v[1]/n*L, v[2]/n*L];
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
    // The ordinary nodes are markers, sized in screen pixels, so they have to
    // be grown by hand as the view closes in. Applied in steps rather than
    // every frame: on a large
    // graph a per-frame restyle rewrites the whole size buffer and the flight
    // stutters, and at this speed the steps are not visible.
    var el = gd(), b = base();
    if (Math.abs(f - shown) < 0.08 && f !== 1 && f !== MAG) { return; }
    shown = f;
    var t = MAG > 1 ? (f - 1) / (MAG - 1) : 0;
    Plotly.restyle(el, {"marker.size": [b.size.map(function (v) {
      return v * f; })]}, [1]);
    Plotly.restyle(el, {"line.width": b.width + (EDGE_W - b.width) * t}, [0]);
    // The focus sphere needs nothing here: it is in the data, so the zoom
    // grows it on its own.
  }
  // **`scene.camera.center` is in units of half the aspect ratio.** A node at
  // normalised position n sits at n * k / 2, so the centre has to be rescaled
  // as the zoom tweens or the focus node drifts off to one side -- which is
  // what it did, further the further in the view went.
  function centre(n, k) {
    return {x: n.x * k / 2, y: n.y * k / 2, z: n.z * k / 2};
  }
  function glide(k1, c1, done) {
    // Zoom is the aspect ratio, pan is the camera centre, and the axis ranges
    // are never touched -- so the whole network stays in the figure however
    // far in a stop goes.
    var el = gd(), k0 = atK, c0 = atC, d0 = heading(), t0 = performance.now();
    cancelAnimationFrame(anim);
    (function frame(now) {
      var t = Math.min(1, (now - t0) / FLIGHT), e = ease(t);
      var k = k0 + (k1 - k0) * e;
      var n = {x: c0.x + (c1.x - c0.x) * e, y: c0.y + (c1.y - c0.y) * e,
               z: c0.z + (c1.z - c0.z) * e};
      var c = centre(n, k);
      // Swing around the node while coming in. A tour that only dollies reads
      // as a slideshow; the turn is what makes it feel like moving through
      // the network rather than cutting between stills.
      var a = ORBIT * e * (k1 > k0 ? 1 : -1);
      var ca = Math.cos(a), sa = Math.sin(a);
      var dir = [d0[0] * ca - d0[1] * sa, d0[0] * sa + d0[1] * ca, d0[2]];
      // Updated every frame, so an interrupted flight resumes from where it
      // actually got to rather than from where it was aiming.
      atK = k; atC = n;
      Plotly.relayout(el, {
        "scene.aspectmode": "manual",
        "scene.aspectratio": {x: k, y: k, z: k},
        "scene.camera": {center: c, eye: {x: c.x + dir[0], y: c.y + dir[1],
                                          z: c.z + dir[2]}}});
      magnify(ZOOM > 1 ? 1 + (MAG - 1) * (k - 1) / (ZOOM - 1) : 1);
      if (t < 1) { anim = requestAnimationFrame(frame); } else if (done) { done(); }
    })(t0);
  }
  function flyTo(p, done) { glide(ZOOM, {x: p[0], y: p[1], z: p[2]}, done); }
  function annotate(s) {
    var card = document.getElementById("bl3-card");
    card.innerHTML = BL_CARD(s.record);
    card.style.display = "block";
    void card.offsetWidth;                 // commit before the transition
    card.classList.add("on");
  }
  function unannotate() {
    var card = document.getElementById("bl3-card");
    card.classList.remove("on");
    card.style.display = "none";
  }
  function light(p) {
    // A real sphere, in the data. It grows because the camera comes closer,
    // which is the whole point: a marker would stay the same size however far
    // in the flight went, and an overlay would be a sticker on the glass.
    //
    // Scaled per axis by the span, because the three axes have different data
    // ranges but are drawn into one cube -- equal radii in data units would
    // render as an ellipsoid.
    var el = gd();
    var rx = SPAN[0] * SR, ry = SPAN[1] * SR, rz = SPAN[2] * SR;
    var X = [], Y = [], Z = [];
    for (var a = 0; a < SPH.x.length; a++) {
      var ax = [], ay = [], az = [];
      for (var b = 0; b < SPH.x[a].length; b++) {
        ax.push(p[0] + SPH.x[a][b] * rx);
        ay.push(p[1] + SPH.y[a][b] * ry);
        az.push(p[2] + SPH.z[a][b] * rz);
      }
      X.push(ax); Y.push(ay); Z.push(az);
    }
    var trace = {type: "surface", x: X, y: Y, z: Z,
                 colorscale: [[0, "__HILITE__"], [1, "__HILITE__"]],
                 showscale: false, hoverinfo: "skip", showlegend: false,
                 contours: {x: {show: false}, y: {show: false},
                            z: {show: false}},
                 lighting: {ambient: 0.60, diffuse: 0.85, specular: 0.14,
                            roughness: 0.80, fresnel: 0.05},
                 lightposition: {x: -1e4, y: 1e4, z: 1e4}};
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
    // that is sliding across the screen. `cam`, not `xyz`: this one is the
    // camera centre.
    flyTo(s.cam, function () { annotate(s); });
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
    glide(1, {x: 0, y: 0, z: 0});
    Plotly.relayout(gd(), {"scene.camera": HOME});
  });
})();
</script>
"""
    import json as _json
    block = (block.replace("__CARD_CSS__", CARD_CSS)
                  .replace("__CARD_JS__", CARD_JS)
                  .replace("__STOPS__", _json.dumps(stops))
                  .replace("__ZOOM3D__", str(TOUR_ZOOM_3D))
                  .replace("__MAGNIFY__", str(TOUR_MAGNIFY))
                  .replace("__EDGE_W__", str(TOUR_EDGE_WIDTH))
                  .replace("__SPHERE__", _json.dumps(_sphere_payload()))
                  .replace("__SPHERE_R__", str(TOUR_SPHERE_FRACTION))
                  .replace("__ORBIT__", str(TOUR_ORBIT_DEG))
                  .replace("__SPAN__", _json.dumps(
                      [b[1] - b[0] for b in bounds]))
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
