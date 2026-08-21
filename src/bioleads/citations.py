"""Citation network among the corpus papers — who cites whom, and who's cited most.

Unlike the co-occurrence graph (which links *entities*), this links *papers*:
a directed edge A → B means "paper A cites paper B", restricted to the PubMed
records actually in your corpus. From that graph fall out two complementary
notions of "most cited":

* **in-corpus citations** — a node's in-degree: how many *other papers in your
  set* cite it. Surfaces the work your particular corpus is built on.
* **global citations** — iCite's ``citation_count`` across all of PubMed: the
  paper's overall impact, independent of your corpus.

Citation links and metadata come from NIH iCite (the Open Citation Collection:
PMC + Crossref + MEDLINE) — PMID-native, no API key required. Only PMID-bearing
documents can participate; PDFs / refs without a PMID are skipped.
"""
from __future__ import annotations

import warnings

import networkx as nx

from .config import Config
from .sources import (
    Document,
    _ICITE_URL,
    _check_cancel,
    _chunks,
    _sayer,
)

# iCite fields we pull per record. citation_count is the global impact metric;
# references / cited_by give the directed links we intersect with the corpus.
_ICITE_FL = "pmid,year,title,authors,journal,citation_count,references,cited_by"


def _doc_pmid(doc: Document) -> str:
    """Return a document's bare PMID (no 'PMID:' prefix), or '' if it has none."""
    pmid = str(doc.meta.get("pmid", "")).strip()
    if not pmid and doc.doc_id.startswith("PMID:"):
        pmid = doc.doc_id.split("PMID:", 1)[1].strip()
    return pmid


def _as_id_list(val) -> list[str]:
    """Normalize an iCite references/cited_by value into a list of PMID strings."""
    if not val:
        return []
    if isinstance(val, str):  # iCite has returned space-joined strings historically
        val = val.split()
    return [str(x).strip() for x in val if str(x).strip()]


def _parse_authors(val) -> list[str]:
    """Normalize an iCite ``authors`` value into a de-duplicated list of names.

    iCite returns authors as a single comma-separated string ("Jane A Doe,
    John B Smith"); we also tolerate a list (of strings or {"name": ...} dicts).
    Names are whitespace-collapsed and de-duplicated case-insensitively while
    preserving order.
    """
    if not val:
        return []
    if isinstance(val, str):
        parts = val.split(",")
    elif isinstance(val, (list, tuple)):
        # Live iCite returns [{"fullName": "Ding, Li"}, ...]; tolerate the other
        # common key spellings and bare strings too.
        parts = [
            (a.get("fullName") or a.get("full_name") or a.get("name") or "")
            if isinstance(a, dict) else str(a)
            for a in val
        ]
    else:
        parts = [str(val)]
    out, seen = [], set()
    for p in parts:
        name = " ".join(str(p).split()).strip()
        key = name.lower()
        if name and key not in seen:
            out.append(name)
            seen.add(key)
    return out


def _corpus_records(docs, *, cancel=None, progress=None):
    """Map corpus PMIDs to documents and fetch their iCite records once.

    Returns ``(pmid_to_doc, corpus, records)``. Shared by the paper- and
    author-level citation graphs so a run hits iCite a single time.
    """
    say = _sayer(progress)
    pmid_to_doc: dict[str, Document] = {}
    for d in docs:
        pmid = _doc_pmid(d)
        if pmid and pmid not in pmid_to_doc:
            pmid_to_doc[pmid] = d
    corpus = set(pmid_to_doc)
    say(f"  {len(corpus)} of {len(docs)} document(s) carry a PMID for the "
        f"citation network.")
    if not corpus:
        return pmid_to_doc, corpus, {}
    _check_cancel(cancel)
    records = fetch_icite(corpus, cancel=cancel, progress=progress)
    return pmid_to_doc, corpus, records


def _corpus_paper_edges(records: dict, corpus: set) -> set:
    """Directed paper-citation edges ``(citer_pmid, cited_pmid)`` within corpus.

    Unions references (A→cited) and cited_by (citer→A) so asymmetric iCite data
    still yields the edge, and de-duplicates so each citation is counted once.
    """
    edges: set[tuple[str, str]] = set()
    for pmid, rec in records.items():
        if pmid not in corpus:
            continue
        for cited in _as_id_list(rec.get("references")):
            if cited in corpus and cited != pmid:
                edges.add((pmid, cited))
        for citer in _as_id_list(rec.get("cited_by")):
            if citer in corpus and citer != pmid:
                edges.add((citer, pmid))
    return edges


def fetch_icite(
    pmids, *, timeout: int = 30, cancel=None, progress=None
) -> dict[str, dict]:
    """Fetch iCite records for `pmids`, keyed by PMID string.

    Batched to be gentle on the API. Returns {} (with a warning) if `requests`
    isn't installed or every batch fails — callers degrade to whatever metadata
    the documents already carry.
    """
    say = _sayer(progress)
    ids = [str(p).strip() for p in pmids if str(p).strip()]
    if not ids:
        return {}
    try:
        import requests
    except ImportError:  # pragma: no cover - requests ships with the pubmed extra
        warnings.warn(
            'citation network needs requests. Install with: pip install "bioleads[pubmed]"')
        return {}

    out: dict[str, dict] = {}
    total = len(ids)
    for start, batch in zip(range(0, total, 200), _chunks(ids, 200)):
        _check_cancel(cancel)
        say(f"  iCite: fetching citation data for {start + 1}–{start + len(batch)} "
            f"of {total} paper(s)…")
        try:
            resp = requests.get(
                _ICITE_URL,
                params={"pmids": ",".join(batch), "fl": _ICITE_FL},
                timeout=timeout,
            )
            resp.raise_for_status()
            for rec in resp.json().get("data", []):
                pmid = str(rec.get("pmid", "")).strip()
                if pmid:
                    out[pmid] = rec
        except Exception as exc:  # noqa: BLE001 - one bad batch shouldn't sink the rest
            warnings.warn(f"iCite request failed for a batch ({exc}); continuing")
    return out


def _prune_by_degree(g: nx.DiGraph, min_degree: int, noun: str, say) -> nx.DiGraph:
    """Drop nodes whose total degree is below ``min_degree``.

    The paper and author graphs pass their own threshold here, because they are
    not the same object: one node per paper against one node per lab, where a
    productive lab absorbs many of the corpus's papers and inherits all of their
    links. The two degree distributions differ, so one number cannot serve both.

    Degree is counted on the *whole* graph — citations received from corpus
    papers plus citations made to them — and unweighted, so an author who cites
    one colleague forty times counts as one connection, not forty. Survivors
    keep the attributes they were built with, in_corpus_citations included: those
    describe the node's place in the corpus, not in the pruned picture.

    Repeated to a fixed point -- the k-core -- rather than run once. Removing a
    node lowers its neighbours' degree, so a single pass leaves behind nodes
    that are now under the threshold, and the picture then contradicts the
    control that drew it: you ask for degree 5 and can still count 2 arrows on
    a node. Settling is what makes "every node here has at least N connections"
    true of what you are looking at.

    The cost is that a high threshold can cascade, since each round can expose
    more nodes to the next. That is the honest consequence of the setting, so
    the log reports the rounds and the total rather than hiding it in one line.
    """
    if min_degree <= 0 or not g.number_of_nodes():
        return g
    before = g.number_of_nodes()
    rounds = 0
    while g.number_of_nodes():
        keep = [n for n, d in g.degree() if d >= min_degree]
        if len(keep) == g.number_of_nodes():
            break
        g = g.subgraph(keep).copy()
        rounds += 1
    dropped = before - g.number_of_nodes()
    if dropped:
        say(f"  dropped {dropped} {noun}(s) below degree {min_degree} "
            f"in {rounds} round(s); {g.number_of_nodes()} left.")
    if dropped and not g.number_of_nodes():
        # Not a failure and not an empty corpus: there is simply no group of
        # {noun}s this size all connected to each other. Said plainly, because
        # otherwise it surfaces as a missing network with no explanation.
        say(f"  no {noun} survives degree {min_degree} — every one of them "
            f"loses connections as the others go. Try a lower threshold.")
    return g


def build_citation_graph(
    docs: list[Document],
    cfg: Config | None = None,
    *,
    email: str | None = None,  # accepted for signature symmetry; iCite needs none
    api_key: str | None = None,
    prefetched: tuple | None = None,
    cancel=None,
    progress=None,
) -> nx.DiGraph:
    """Build a directed citation graph over the corpus's PMID-bearing papers.

    Edge A → B means "A cites B" (both A and B are in the corpus). Node attrs:
    ``pmid``, ``title``, ``year``, ``journal``, ``global_citations`` (iCite
    citation_count across all of PubMed), ``url``, ``source``, and
    ``in_corpus_citations`` (the in-degree — how many corpus papers cite it).

    Papers without a PMID can't be placed in the network and are skipped.
    ``prefetched`` is the ``(pmid_to_doc, corpus, records)`` tuple from
    :func:`_corpus_records`; pass it to share one iCite fetch with the author
    graph (otherwise it's fetched here).
    """
    cfg = cfg or Config()
    say = _sayer(progress)

    pmid_to_doc, corpus, records = (
        prefetched if prefetched is not None
        else _corpus_records(docs, cancel=cancel, progress=progress))
    if not corpus:
        return nx.DiGraph()

    g = nx.DiGraph()
    # One node per corpus paper, with iCite metadata (falling back to the doc).
    for pmid, doc in pmid_to_doc.items():
        rec = records.get(pmid, {})
        cc = rec.get("citation_count")
        g.add_node(
            f"PMID:{pmid}",
            pmid=pmid,
            title=(rec.get("title") or doc.title or "").strip(),
            year=str(rec.get("year") or doc.meta.get("year") or "").strip(),
            journal=(rec.get("journal") or doc.meta.get("journal") or "").strip(),
            global_citations=int(cc) if cc is not None else None,
            url=doc.meta.get("url") or f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            source=doc.source,
        )

    # Directed edges, intersected with the corpus. references give A→(cited),
    # cited_by give (citer)→A; we union both so asymmetric iCite data still
    # yields the edge.
    for pmid, rec in records.items():
        if pmid not in corpus:
            continue
        src = f"PMID:{pmid}"
        for cited in _as_id_list(rec.get("references")):
            if cited in corpus and cited != pmid:
                g.add_edge(src, f"PMID:{cited}")
        for citer in _as_id_list(rec.get("cited_by")):
            if citer in corpus and citer != pmid:
                g.add_edge(f"PMID:{citer}", src)

    for n in g.nodes:
        g.nodes[n]["in_corpus_citations"] = g.in_degree(n)

    say(f"  citation network: {g.number_of_nodes()} paper(s) / "
        f"{g.number_of_edges()} intra-corpus citation edge(s).")

    g = _prune_by_degree(g, cfg.min_paper_degree, "paper", say)

    # Trim to the most-cited papers for visualization sanity (keep the induced
    # subgraph so edges among the survivors are preserved).
    if g.number_of_nodes() > cfg.max_graph_nodes:
        ranked = sorted(
            g.nodes,
            key=lambda n: (g.nodes[n]["in_corpus_citations"],
                           g.nodes[n].get("global_citations") or 0),
            reverse=True,
        )
        g = g.subgraph(ranked[: cfg.max_graph_nodes]).copy()
        say(f"  trimmed to the top {cfg.max_graph_nodes} most-cited paper(s) "
            f"for display.")
    return g


def build_author_citation_graph(
    docs: list[Document],
    cfg: Config | None = None,
    *,
    prefetched: tuple | None = None,
    rank_by: str = "in_corpus_citations",
    cancel=None,
    progress=None,
) -> nx.DiGraph:
    """Build a directed *senior-author* citation graph from the corpus papers.

    Each paper is represented by **one** author: the last name in its byline,
    which by biomedical convention is the senior author — the lab the work came
    out of. Middle and first authors do not appear. That makes the graph a map
    of labs citing labs rather than of everyone who ever appeared on a byline,
    and it keeps one paper→paper citation worth exactly one author→author edge
    instead of the product of the two author lists.

    When corpus paper P cites corpus paper Q, P's senior author gets a directed
    edge to Q's (a shared senior author is a self-citation and is dropped). An
    edge's ``weight`` is how many times that lab cited the other across the
    corpus. Node attrs: ``author``, ``papers`` (corpus papers they were senior
    author on), ``global_citations`` (summed iCite citation_count of those
    papers), and ``in_corpus_citations`` (weighted in-degree — how often the
    author is cited within the corpus), the metric the most-cited ranking and
    node size use.

    `rank_by` decides which measure survives the `max_graph_nodes` trim —
    ``"in_corpus_citations"`` for the standing view, ``"papers"`` for the
    productivity one. It changes only *which* authors are displayed when the
    graph is too large to draw, never the graph that is built.

    A record with no author list cannot be placed and is skipped. Matching is by
    name string, so "Smith J" and "Smith JA" are two people.

    ``prefetched`` shares one iCite fetch with :func:`build_citation_graph`.
    """
    cfg = cfg or Config()
    say = _sayer(progress)

    pmid_to_doc, corpus, records = (
        prefetched if prefetched is not None
        else _corpus_records(docs, cancel=cancel, progress=progress))
    if not corpus:
        return nx.DiGraph()

    # One author stands for each paper: the last in the byline, which in
    # biomedical convention is the senior author whose lab the work came from.
    # Papers with no author list at all cannot be placed and are skipped.
    paper_senior: dict[str, str] = {}
    paper_global: dict[str, int] = {}
    for pmid in corpus:
        rec = records.get(pmid, {})
        authors = _parse_authors(rec.get("authors"))
        if authors:
            paper_senior[pmid] = authors[-1]
        cc = rec.get("citation_count")
        paper_global[pmid] = int(cc) if cc is not None else 0

    g = nx.DiGraph()
    for pmid, senior in paper_senior.items():
        if not g.has_node(senior):
            g.add_node(senior, author=senior, papers=0, global_citations=0,
                       in_corpus_citations=0)
        g.nodes[senior]["papers"] += 1
        g.nodes[senior]["global_citations"] += paper_global.get(pmid, 0)

    # senior author → senior author, one edge per (de-duplicated) paper link.
    edge_w: dict[tuple[str, str], int] = {}
    for citer_pmid, cited_pmid in _corpus_paper_edges(records, corpus):
        a, b = paper_senior.get(citer_pmid), paper_senior.get(cited_pmid)
        if a and b and a != b:                      # a == b is a self-citation
            edge_w[(a, b)] = edge_w.get((a, b), 0) + 1
    for (a, b), w in edge_w.items():
        g.add_edge(a, b, weight=w)

    for n in g.nodes:
        g.nodes[n]["in_corpus_citations"] = int(g.in_degree(n, weight="weight"))

    say(f"  senior-author network: {g.number_of_nodes()} author(s) / "
        f"{g.number_of_edges()} author-citation edge(s).")

    g = _prune_by_degree(g, cfg.min_author_degree, "author", say)

    if rank_by == "papers" and cfg.min_author_papers > 0:
        keep = [n for n, d in g.nodes(data=True)
                if (d.get("papers") or 0) >= cfg.min_author_papers]
        if len(keep) < g.number_of_nodes():
            say(f"  dropped {g.number_of_nodes() - len(keep)} author(s) below "
                f"{cfg.min_author_papers} corpus paper(s); {len(keep)} left.")
            g = g.subgraph(keep).copy()

    if g.number_of_nodes() > cfg.max_graph_nodes:
        # Trim by whatever the view is about. Ranking by citations while
        # displaying paper counts would drop the prolific-but-uncited authors
        # the paper view exists to show — a lab publishing steadily without
        # being cited *within this corpus* is exactly the case of interest.
        second = ("global_citations" if rank_by == "in_corpus_citations"
                  else "in_corpus_citations")
        ranked = sorted(
            g.nodes,
            key=lambda n: (g.nodes[n].get(rank_by) or 0,
                           g.nodes[n].get(second) or 0),
            reverse=True,
        )
        g = g.subgraph(ranked[: cfg.max_graph_nodes]).copy()
        noun = "most-published" if rank_by == "papers" else "most-cited"
        say(f"  trimmed to the top {cfg.max_graph_nodes} {noun} author(s) "
            f"for display.")
    return g


def most_cited(graph: nx.DiGraph, top_n: int | None = None,
               by: str = "in_corpus_citations") -> list[tuple[str, dict]]:
    """Return (node_id, attrs) sorted by `by` (default in-corpus citations).

    Ties broken by the other citation metric so the ranking is deterministic.
    """
    other = "global_citations" if by == "in_corpus_citations" else "in_corpus_citations"

    def key(item):
        _, d = item
        return (d.get(by) or 0, d.get(other) or 0)

    ranked = sorted(graph.nodes(data=True), key=key, reverse=True)
    return ranked[:top_n] if top_n else ranked


def to_dataframe(graph: nx.DiGraph):
    """Papers ranked by citation count, as a pandas DataFrame (for CSV export)."""
    import pandas as pd

    rows = [
        {
            "pmid": d.get("pmid", ""),
            "title": d.get("title", ""),
            "year": d.get("year", ""),
            "journal": d.get("journal", ""),
            "in_corpus_citations": d.get("in_corpus_citations", 0),
            "global_citations": d.get("global_citations"),
            "url": d.get("url", ""),
        }
        for _, d in most_cited(graph)
    ]
    return pd.DataFrame(
        rows,
        columns=["pmid", "title", "year", "journal",
                 "in_corpus_citations", "global_citations", "url"],
    )


def authors_to_dataframe(graph: nx.DiGraph, by: str = "in_corpus_citations"):
    """Senior authors ranked by `by`, as a DataFrame (for CSV).

    `by="papers"` orders by how many corpus papers each was senior author on —
    productivity within the corpus rather than standing within it.
    """
    import pandas as pd

    rows = [
        {
            "author": d.get("author", ""),
            "papers": d.get("papers", 0),
            "in_corpus_citations": d.get("in_corpus_citations", 0),
            "global_citations": d.get("global_citations", 0),
        }
        for _, d in most_cited(graph, by=by)
    ]
    return pd.DataFrame(
        rows,
        columns=["author", "papers", "in_corpus_citations", "global_citations"],
    )


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


def write_citation_html(
    g: nx.DiGraph, path: str, title: str = "bioleads citation network"
) -> str:
    """Render the directed citation network to a standalone HTML file (pyvis).

    Nodes are sized by in-corpus citations (most-cited papers are largest);
    arrows point from a paper to the papers it cites. Falls back to GraphML if
    pyvis isn't installed.
    """
    try:
        from pyvis.network import Network
    except ImportError:
        alt = path.rsplit(".", 1)[0] + ".graphml"
        nx.write_graphml(g, alt)
        print(f'[bioleads] pyvis not installed; wrote {alt}. '
              f'Install with: pip install "bioleads[viz]"')
        return alt

    net = Network(height="800px", width="100%", notebook=False, directed=True,
                  heading=title, bgcolor="#ffffff")
    if g.number_of_nodes():
        max_cit = max((d["in_corpus_citations"] for _, d in g.nodes(data=True)),
                      default=0)
        for n, d in g.nodes(data=True):
            cit = d["in_corpus_citations"]
            size = 10 + 30 * (cit / max_cit if max_cit else 0)
            label = d.get("pmid", n)
            tip_lines = [
                d.get("title") or label,
                f"PMID: {d.get('pmid', '')}",
            ]
            if d.get("year"):
                tip_lines.append(f"year: {d['year']}")
            if d.get("journal"):
                tip_lines.append(d["journal"])
            tip_lines.append(f"cited by {cit} paper(s) in corpus")
            if d.get("global_citations") is not None:
                tip_lines.append(f"global citations: {d['global_citations']}")
            net.add_node(n, label=label, value=cit + 1, size=size,
                         title="\n".join(tip_lines))
        for a, b in g.edges():
            net.add_edge(a, b, title="cites", arrows="to")
    net.force_atlas_2based(spring_length=120)
    net.write_html(path, notebook=False, open_browser=False)
    _collapse_duplicate_heading(path, title)  # pyvis 0.3.2 doubles the <h1>
    _freeze_physics_after_stabilization(path)
    return path


def _citation_hover(n, d) -> str:
    lines = [d.get("title") or d.get("pmid", n), f"PMID: {d.get('pmid', '')}"]
    if d.get("year"):
        lines.append(f"year: {d['year']}")
    if d.get("journal"):
        lines.append(d["journal"])
    lines.append(f"cited by {d.get('in_corpus_citations', 0)} paper(s) in corpus")
    if d.get("global_citations") is not None:
        lines.append(f"global citations: {d['global_citations']}")
    return "<br>".join(lines)


def write_citation_html_3d(
    g: nx.DiGraph, path: str, title: str = "bioleads citation network (3D)",
    seed: int = 0,
) -> str | None:
    """Render the citation network as a rotatable 3D Plotly graph.

    Nodes are sized and colored by in-corpus citations (most-cited papers are
    largest / hottest). Returns None if Plotly isn't installed.
    """
    from .graph3d import write_graph_3d

    return write_graph_3d(
        g, path, title=title, size_attr="in_corpus_citations", seed=seed,
        color_attr="in_corpus_citations", hover=_citation_hover, directed=True,
    )


def _author_tip_lines(n, d) -> list[str]:
    lines = [d.get("author") or n,
             f"papers in corpus: {d.get('papers', 0)}",
             f"cited {d.get('in_corpus_citations', 0)} time(s) within corpus"]
    if d.get("global_citations"):
        lines.append(f"global citations (sum): {d['global_citations']}")
    return lines


def _author_hover(n, d) -> str:
    return "<br>".join(_author_tip_lines(n, d))


def write_author_html(
    g: nx.DiGraph, path: str, title: str = "bioleads senior-author citation network",
    size_attr: str = "in_corpus_citations",
) -> str:
    """Render the senior-author network to standalone HTML (pyvis).

    Nodes are authors, sized by `size_attr`; an arrow A → B means an author A
    (co)authored a paper that cites a paper (co)authored by B. Falls back to
    GraphML if pyvis isn't installed.

    The edges are citations whichever measure sizes the nodes. With
    ``size_attr="papers"`` the picture answers "who publishes most here, and
    who cites whom" at once — a large node with no arrows into it is a lab
    publishing steadily in this field that nothing in the corpus cites.
    """
    try:
        from pyvis.network import Network
    except ImportError:
        alt = path.rsplit(".", 1)[0] + ".graphml"
        nx.write_graphml(g, alt)
        print(f'[bioleads] pyvis not installed; wrote {alt}. '
              f'Install with: pip install "bioleads[viz]"')
        return alt

    net = Network(height="800px", width="100%", notebook=False, directed=True,
                  heading=title, bgcolor="#ffffff")
    if g.number_of_nodes():
        top = max((d.get(size_attr) or 0 for _, d in g.nodes(data=True)), default=0)
        for n, d in g.nodes(data=True):
            v = d.get(size_attr) or 0
            size = 10 + 30 * (v / top if top else 0)
            net.add_node(n, label=d.get("author") or n, value=v + 1, size=size,
                         title="\n".join(_author_tip_lines(n, d)))
        for a, b, ed in g.edges(data=True):
            net.add_edge(a, b, title=f"cites ×{ed.get('weight', 1)}", arrows="to")
    net.force_atlas_2based(spring_length=120)
    net.write_html(path, notebook=False, open_browser=False)
    _collapse_duplicate_heading(path, title)  # pyvis 0.3.2 doubles the <h1>
    _freeze_physics_after_stabilization(path)
    return path


def write_author_html_3d(
    g: nx.DiGraph, path: str,
    title: str = "bioleads senior-author citation network (3D)", seed: int = 0,
    size_attr: str = "in_corpus_citations",
) -> str | None:
    """Render the senior-author citation network as a rotatable 3D Plotly graph.

    Nodes are sized and colored by in-corpus citations (most-cited authors are
    largest / hottest). Returns None if Plotly isn't installed.
    """
    from .graph3d import write_graph_3d

    return write_graph_3d(
        g, path, title=title, size_attr=size_attr, seed=seed,
        color_attr=size_attr, hover=_author_hover, directed=True,
    )
