"""Entity co-occurrence network.

An edge connects two entities that appear together more than chance predicts.
We score edges by pointwise mutual information (PMI) so that strong, specific
associations outrank pairs that are merely both frequent.
"""
from __future__ import annotations

import math
from collections import Counter
from itertools import combinations

import networkx as nx

from .config import Config


def _doc_term_sets(
    entities: dict[str, list[str]], keep: set[str] | None
) -> list[set[str]]:
    sets = []
    for ents in entities.values():
        s = set(ents)
        if keep is not None:
            s &= keep
        if len(s) >= 2:
            sets.append(s)
    return sets


def build_cooccurrence(
    entities: dict[str, list[str]],
    cfg: Config | None = None,
    keep_terms: set[str] | None = None,
) -> nx.Graph:
    """Build a co-occurrence graph.

    Parameters
    ----------
    entities    {doc_id: [entity, ...]}
    keep_terms  optional whitelist (e.g. the top enriched terms) to keep the
                graph readable. If None, all entities are used.

    Node attrs: count (document frequency).
    Edge attrs: weight (co-occurrence count), pmi.
    """
    cfg = cfg or Config()
    doc_sets = _doc_term_sets(entities, keep_terms)
    n_docs = max(len(doc_sets), 1)

    node_df = Counter()                       # document frequency per term
    pair_df: Counter = Counter()              # co-document frequency per pair
    for s in doc_sets:
        node_df.update(s)
        for a, b in combinations(sorted(s), 2):
            pair_df[(a, b)] += 1

    g = nx.Graph()
    for (a, b), w in pair_df.items():
        if w < cfg.min_cooccurrence:
            continue
        # PMI = log[ P(a,b) / (P(a)P(b)) ]
        p_ab = w / n_docs
        p_a = node_df[a] / n_docs
        p_b = node_df[b] / n_docs
        pmi = math.log(p_ab / (p_a * p_b)) if p_a and p_b else 0.0
        if cfg.min_pmi is not None and pmi < cfg.min_pmi:
            continue
        g.add_edge(a, b, weight=w, pmi=round(pmi, 4))

    for n in g.nodes:
        g.nodes[n]["count"] = node_df[n]

    # Trim to the most connected nodes for visualization sanity.
    if g.number_of_nodes() > cfg.max_graph_nodes:
        top = sorted(g.degree, key=lambda kv: kv[1], reverse=True)
        keep = {n for n, _ in top[: cfg.max_graph_nodes]}
        g = g.subgraph(keep).copy()
    return g


def write_graph_html(
    g: nx.Graph,
    path: str,
    title: str = "bioleads co-occurrence",
    groups: dict[str, int] | None = None,
) -> str:
    """Render an interactive network to a standalone HTML file (pyvis).

    If `groups` ({term: cluster_id}) is given, nodes are colored by cluster
    (pyvis assigns a palette color per group). Falls back to writing GraphML if
    pyvis isn't installed.
    """
    try:
        from pyvis.network import Network
    except ImportError:
        alt = path.rsplit(".", 1)[0] + ".graphml"
        if groups:
            for n in g.nodes:
                if n in groups:
                    g.nodes[n]["cluster"] = groups[n]
        nx.write_graphml(g, alt)
        print(f'[bioleads] pyvis not installed; wrote {alt}. '
              f'Install with: pip install "bioleads[viz]"')
        return alt

    net = Network(height="800px", width="100%", notebook=False,
                  heading=title, bgcolor="#ffffff")
    if g.number_of_nodes():
        max_count = max(d["count"] for _, d in g.nodes(data=True))
        max_w = max((d["weight"] for _, _, d in g.edges(data=True)), default=1)
        for n, d in g.nodes(data=True):
            size = 10 + 30 * (d["count"] / max_count)
            cid = groups.get(n) if groups else None
            tip = f"{n}\ndoc freq: {d['count']}"
            if cid is not None:
                tip += f"\ncluster: {cid}"
            kwargs = {"group": cid} if cid is not None else {}
            net.add_node(n, label=n, value=d["count"], size=size,
                         title=tip, **kwargs)
        for a, b, d in g.edges(data=True):
            net.add_edge(a, b, value=d["weight"],
                         width=1 + 6 * (d["weight"] / max_w),
                         title=f"co-occur: {d['weight']} | PMI: {d['pmi']}")
    net.force_atlas_2based(spring_length=120)
    net.write_html(path, notebook=False, open_browser=False)
    _collapse_duplicate_heading(path, title)
    _freeze_physics_after_stabilization(path)
    return path


def _freeze_physics_after_stabilization(path: str) -> None:
    """Stop the vis.js physics engine once the initial layout has stabilized.

    pyvis leaves force-atlas physics running continuously, so a large 2D graph
    keeps recomputing forces forever and pegs the CPU — the view feels like it's
    'choking' and never settles. We append a small script that freezes physics
    the moment the one-time stabilization finishes: the layout is laid out, then
    it stops churning (the node positions are kept; you can still pan/zoom/drag).
    """
    snippet = (
        '\n<script type="text/javascript">\n'
        '  if (typeof network !== "undefined" && network) {\n'
        '    network.on("stabilizationIterationsDone", function () {\n'
        '      network.setOptions({ physics: false });\n'
        '    });\n'
        '  }\n'
        '</script>\n'
    )
    try:
        with open(path, encoding="utf-8") as f:
            html = f.read()
    except OSError:
        return
    if "stabilizationIterationsDone" in html:  # already injected
        return
    if "</body>" in html:
        html = html.replace("</body>", snippet + "</body>", 1)
    else:
        html += snippet
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def _collapse_duplicate_heading(path: str, title: str) -> None:
    """Keep only the first ``<h1>{title}</h1>``.

    pyvis 0.3.2's bundled template.html renders ``<h1>{{heading}}</h1>`` twice,
    so every graph shows its title doubled. We strip the extra copies from the
    written file (a no-op on pyvis releases that fix the template).
    """
    try:
        with open(path, encoding="utf-8") as f:
            html = f.read()
    except OSError:
        return
    needle = f"<h1>{title}</h1>"
    first = html.find(needle)
    if first == -1:
        return
    cut = first + len(needle)
    deduped = html[:cut] + html[cut:].replace(needle, "")
    if deduped != html:
        with open(path, "w", encoding="utf-8") as f:
            f.write(deduped)


def write_graph_html_3d(
    g: nx.Graph,
    path: str,
    title: str = "bioleads co-occurrence (3D)",
    groups: dict[str, int] | None = None,
    seed: int = 0,
) -> str | None:
    """Render the co-occurrence network as a rotatable 3D Plotly graph.

    Nodes are sized by document frequency and colored by cluster (if `groups`
    is given) or by frequency otherwise. Returns None if Plotly isn't installed.
    """
    from .graph3d import write_graph_3d

    def hover(n, d):
        tip = f"{n}<br>doc freq: {d.get('count', '?')}"
        if groups and n in groups:
            tip += f"<br>cluster: {groups[n]}"
        return tip

    return write_graph_3d(
        g, path, title=title, size_attr="count", seed=seed,
        groups=groups, color_attr=None if groups else "count",
        hover=hover, directed=False,
    )
