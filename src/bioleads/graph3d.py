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
    hover=None,
    directed: bool = False,
) -> str | None:
    """Write a 3D Plotly rendering of `g` to `path`; return the path (or None).

    Parameters
    ----------
    size_attr   node attribute used for marker size (e.g. "count").
    groups      {node: cluster_id} → discrete per-cluster colors (takes priority).
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
    if groups:
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
    fig.update_layout(
        title=heading,
        scene=dict(xaxis=axis, yaxis=axis, zaxis=axis, dragmode="orbit"),
        margin=dict(l=0, r=0, t=40, b=0),
        showlegend=False,
        paper_bgcolor="white",
    )
    fig.write_html(path, include_plotlyjs=True, full_html=True)
    return path
