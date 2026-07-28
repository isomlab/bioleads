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
    cancel=None,
    progress=None,
) -> nx.DiGraph:
    """Build a directed *author* citation graph from the corpus papers.

    Authors are projected from the paper-citation links: when corpus paper P
    cites corpus paper Q, every author of P gets a directed edge to every author
    of Q (self-citations — shared authors — are dropped). Each citation is
    counted once; an edge's ``weight`` is how many times that author cited that
    other author across the corpus. Node attrs: ``author``, ``papers`` (corpus
    papers they (co)authored), ``global_citations`` (summed iCite citation_count
    of those papers), and ``in_corpus_citations`` (weighted in-degree — how often
    the author is cited within the corpus), the metric the most-cited ranking and
    node size use.

    ``prefetched`` shares one iCite fetch with :func:`build_citation_graph`.
    """
    cfg = cfg or Config()
    say = _sayer(progress)

    pmid_to_doc, corpus, records = (
        prefetched if prefetched is not None
        else _corpus_records(docs, cancel=cancel, progress=progress))
    if not corpus:
        return nx.DiGraph()

    # Authors and global-citation count per corpus paper.
    paper_authors: dict[str, list[str]] = {}
    paper_global: dict[str, int] = {}
    for pmid in corpus:
        rec = records.get(pmid, {})
        paper_authors[pmid] = _parse_authors(rec.get("authors"))
        cc = rec.get("citation_count")
        paper_global[pmid] = int(cc) if cc is not None else 0

    g = nx.DiGraph()
    for pmid, authors in paper_authors.items():
        gc = paper_global.get(pmid, 0)
        for a in authors:
            if not g.has_node(a):
                g.add_node(a, author=a, papers=0, global_citations=0,
                           in_corpus_citations=0)
            g.nodes[a]["papers"] += 1
            g.nodes[a]["global_citations"] += gc

    # author → author edges from the (de-duplicated) paper citation links.
    edge_w: dict[tuple[str, str], int] = {}
    for citer_pmid, cited_pmid in _corpus_paper_edges(records, corpus):
        for a in paper_authors.get(citer_pmid, []):
            for b in paper_authors.get(cited_pmid, []):
                if a != b:
                    edge_w[(a, b)] = edge_w.get((a, b), 0) + 1
    for (a, b), w in edge_w.items():
        g.add_edge(a, b, weight=w)

    for n in g.nodes:
        g.nodes[n]["in_corpus_citations"] = int(g.in_degree(n, weight="weight"))

    say(f"  author network: {g.number_of_nodes()} author(s) / "
        f"{g.number_of_edges()} author-citation edge(s).")

    if g.number_of_nodes() > cfg.max_graph_nodes:
        ranked = sorted(
            g.nodes,
            key=lambda n: (g.nodes[n]["in_corpus_citations"],
                           g.nodes[n].get("global_citations") or 0),
            reverse=True,
        )
        g = g.subgraph(ranked[: cfg.max_graph_nodes]).copy()
        say(f"  trimmed to the top {cfg.max_graph_nodes} most-cited author(s) "
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


def authors_to_dataframe(graph: nx.DiGraph):
    """Authors ranked by in-corpus citations, as a pandas DataFrame (for CSV)."""
    import pandas as pd

    rows = [
        {
            "author": d.get("author", ""),
            "papers": d.get("papers", 0),
            "in_corpus_citations": d.get("in_corpus_citations", 0),
            "global_citations": d.get("global_citations", 0),
        }
        for _, d in most_cited(graph)
    ]
    return pd.DataFrame(
        rows,
        columns=["author", "papers", "in_corpus_citations", "global_citations"],
    )


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
    from .cooccurrence import (_collapse_duplicate_heading,
                               _freeze_physics_after_stabilization)
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
    g: nx.DiGraph, path: str, title: str = "bioleads author citation network"
) -> str:
    """Render the directed author citation network to standalone HTML (pyvis).

    Nodes are authors, sized by in-corpus citations (most-cited authors are
    largest); an arrow A → B means an author A (co)authored a paper that cites a
    paper (co)authored by B. Falls back to GraphML if pyvis isn't installed.
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
            net.add_node(n, label=d.get("author") or n, value=cit + 1, size=size,
                         title="\n".join(_author_tip_lines(n, d)))
        for a, b, ed in g.edges(data=True):
            net.add_edge(a, b, title=f"cites ×{ed.get('weight', 1)}", arrows="to")
    net.force_atlas_2based(spring_length=120)
    net.write_html(path, notebook=False, open_browser=False)
    from .cooccurrence import (_collapse_duplicate_heading,
                               _freeze_physics_after_stabilization)
    _collapse_duplicate_heading(path, title)  # pyvis 0.3.2 doubles the <h1>
    _freeze_physics_after_stabilization(path)
    return path


def write_author_html_3d(
    g: nx.DiGraph, path: str,
    title: str = "bioleads author citation network (3D)", seed: int = 0,
) -> str | None:
    """Render the author citation network as a rotatable 3D Plotly graph.

    Nodes are sized and colored by in-corpus citations (most-cited authors are
    largest / hottest). Returns None if Plotly isn't installed.
    """
    from .graph3d import write_graph_3d

    return write_graph_3d(
        g, path, title=title, size_attr="in_corpus_citations", seed=seed,
        color_attr="in_corpus_citations", hover=_author_hover, directed=True,
    )
