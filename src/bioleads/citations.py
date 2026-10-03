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

import json
import warnings

import networkx as nx

from .config import Config
from .querymatch import MATCH_COLORS
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


def abbreviate_authors(names: list[str], keep: int = 3) -> str:
    """A short byline that always keeps the senior author.

    `Roy S, Huang H \u2026 Kornberg TB (5 authors)`. The last name in a
    biomedical byline is the lab the work came from, which is the one a reader
    scanning a network actually wants, and it is the name a plain truncation
    throws away first.
    """
    names = [n for n in (names or []) if n]
    if not names:
        return ""
    if len(names) <= keep:
        return ", ".join(names)
    head = ", ".join(names[:keep - 1])
    return f"{head} \u2026 {names[-1]} ({len(names)} authors)"


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


def citation_cache(cfg: Config | None):
    """The on-disk cache `cfg` asks for, or None to always fetch.

    Shared by the citation networks and by citation expansion: one store, one
    setting, so "cached citation data" means the same thing everywhere.
    """
    from .cache import JsonCache

    days = (cfg or Config()).citation_cache_days
    return JsonCache(ttl_days=days) if days else None


def _corpus_records(docs, cfg: Config | None = None, *, cancel=None, progress=None):
    """Map corpus PMIDs to documents and fetch their iCite records once.

    Returns ``(pmid_to_doc, corpus, records)``. Shared by the paper- and
    author-level citation graphs so a run hits iCite a single time — and, via
    ``cfg.citation_cache_days``, so a repeat run doesn't hit it at all.
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
    records = fetch_icite(corpus, cancel=cancel, progress=progress,
                          cache=citation_cache(cfg))
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
    pmids, *, timeout: int = 30, cancel=None, progress=None, cache=None
) -> dict[str, dict]:
    """Fetch iCite records for `pmids`, keyed by PMID string.

    Batched to be gentle on the API. Returns {} (with a warning) if `requests`
    isn't installed or every batch fails — callers degrade to whatever metadata
    the documents already carry.

    With a :class:`~bioleads.cache.JsonCache`, only the PMIDs it does not
    already hold are requested, so a repeat run costs nothing and works with no
    network at all. Records come back in one dict either way; the caller cannot
    tell which of them were fetched.
    """
    say = _sayer(progress)
    ids = [str(p).strip() for p in pmids if str(p).strip()]
    if not ids:
        return {}

    out: dict[str, dict] = {}
    if cache is not None:
        wanted, ids = ids, []
        for pmid in wanted:
            record = cache.get(f"icite:{pmid}")
            if record is None:
                ids.append(pmid)        # unknown or expired: fetch it
            elif record:
                out[pmid] = record      # {} is a cached "iCite has nothing"
        note = cache.summary("record")
        if note:
            say(note)
        if not ids:
            return out
    try:
        import requests
    except ImportError:  # pragma: no cover - requests ships with the pubmed extra
        warnings.warn(
            'citation network needs requests. Install with: pip install "bioleads[pubmed]"')
        return out

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
            if cache is not None:
                # Written per batch, so a run interrupted halfway keeps what it
                # already paid for. PMIDs the batch returned nothing for are
                # stored empty, or they would be re-requested every run.
                for pmid in batch:
                    cache.put(f"icite:{pmid}", out.get(pmid, {}))
        except Exception as exc:  # noqa: BLE001 - one bad batch shouldn't sink the rest
            warnings.warn(f"iCite request failed for a batch ({exc}); continuing")
    return out


def _trim_to_top(g: nx.DiGraph, cap: int, noun: str, say, *, key) -> nx.DiGraph:
    """Trim to `cap` nodes for display, **keeping every seed**.

    Ranking by citations alone drops exactly the papers a search was about. A
    seed with few in-corpus links is usually a **lightly cited** paper and only
    sometimes a **new** one, the uncited case being much the commoner: most
    papers are cited rarely, while few are new at any given moment. Either way
    the seed loses to twenty-year-old reviews and vanishes without a word. That
    happened: a search for TM184C grew to 910 documents and the seed was not
    among the 150 nodes drawn.

    **Neither case is a reason to hide it.** Low in-corpus degree is a fact
    about this corpus, not a verdict on the paper. ``year`` and
    ``global_citations`` sit on every node and are what tell the two apart, so
    the hover can say which it is rather than the trim deciding for you.

    So seeds are kept first and the remaining slots go to the highest-ranking
    non-seeds. **If the seeds alone exceed the cap** the cap wins, because a
    picture of 900 nodes is not a picture, and the message says how many seeds
    were dropped so it is never silent.

    Nodes with no ``seed`` attribute count as non-seeds, which keeps graphs
    built some other way behaving exactly as before.
    """
    if cap <= 0 or g.number_of_nodes() <= cap:
        return g
    seeds = [n for n, d in g.nodes(data=True) if d.get("seed")]
    others = [n for n, d in g.nodes(data=True) if not d.get("seed")]
    rank = lambda n: key(g.nodes[n])

    if len(seeds) >= cap:
        keep = sorted(seeds, key=rank, reverse=True)[:cap]
        say(f"  trimmed to {cap} {noun}(s) for display: the seeds alone exceed "
            f"the cap, so {len(seeds) - cap} seed(s) were dropped too.")
    else:
        keep = seeds + sorted(others, key=rank, reverse=True)[:cap - len(seeds)]
        say(f"  trimmed to {len(keep)} {noun}(s) for display: all {len(seeds)} "
            f"seed(s) kept, plus the top {len(keep) - len(seeds)} of "
            f"{len(others)} by citations.")
    return g.subgraph(keep).copy()


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

    **Seeds are exempt.** A seed is a paper the search returned or an author of
    one, and it is in the picture because it is what was asked for, not because
    of how connected it turned out to be. **A seed with few neighbours is most
    often simply an uncited paper, and sometimes a new one** — both are ordinary
    — so any threshold above zero removes the run's own subject along with the
    noise. Seeds still count toward their neighbours' degree, so exempting them
    does not inflate anyone else's.
    """
    if min_degree <= 0 or not g.number_of_nodes():
        return g
    before = g.number_of_nodes()
    rounds = 0
    while g.number_of_nodes():
        keep = [n for n, d in g.degree()
                if d >= min_degree or g.nodes[n].get("seed")]
        if len(keep) == g.number_of_nodes():
            break
        g = g.subgraph(keep).copy()
        rounds += 1
    dropped = before - g.number_of_nodes()
    if dropped:
        under = [n for n, d in g.degree()
                 if d < min_degree and g.nodes[n].get("seed")]
        extra = ""
        if under:
            # Say which kind of thinly linked they are, because the reading
            # differs: a paper cited elsewhere but not here is peripheral to
            # this corpus, one cited nowhere is lightly cited full stop.
            cited = sum(1 for n in under
                        if (g.nodes[n].get("global_citations") or 0) > 0)
            extra = (f" {len(under)} seed(s) kept below the threshold, "
                     f"{cited} of them cited outside this corpus.")
        say(f"  dropped {dropped} {noun}(s) below degree {min_degree} "
            f"in {rounds} round(s); {g.number_of_nodes()} left.{extra}")
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
        else _corpus_records(docs, cfg, cancel=cancel, progress=progress))
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
            authors=abbreviate_authors(_parse_authors(rec.get("authors"))),
            url=doc.meta.get("url") or f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            source=doc.source,
            # A seed came from the query or the ID list; anything else was added
            # by citation expansion. The display trim below protects seeds.
            seed=not bool(doc.meta.get("expanded")),
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

    g = _trim_to_top(g, cfg.max_graph_nodes, "paper", say,
                     key=lambda d: (d.get("in_corpus_citations", 0),
                                    d.get("global_citations") or 0))
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
        else _corpus_records(docs, cfg, cancel=cancel, progress=progress))
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
                       in_corpus_citations=0, seed=False)
        g.nodes[senior]["papers"] += 1
        # An author is a seed author if ANY of their corpus papers is a seed, so
        # the same trim that protects a lightly cited paper protects its lab.
        if not (pmid_to_doc.get(pmid) and
                pmid_to_doc[pmid].meta.get("expanded")):
            g.nodes[senior]["seed"] = True
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

    # Kept on the graph so the query-match annotation can reach back to the
    # papers behind each author. Pruning below returns new graph objects, which
    # carry .graph through, so this is set once here.
    g.graph["paper_senior"] = dict(paper_senior)

    say(f"  senior-author network: {g.number_of_nodes()} author(s) / "
        f"{g.number_of_edges()} author-citation edge(s).")

    g = _prune_by_degree(g, cfg.min_author_degree, "author", say)

    if rank_by == "papers" and cfg.min_author_papers > 0:
        # Seeds are exempt here for the same reason as everywhere else: an
        # author is in this picture because the search found their paper, and
        # one paper is all a search needs to have found.
        keep = [n for n, d in g.nodes(data=True)
                if (d.get("papers") or 0) >= cfg.min_author_papers
                or d.get("seed")]
        if len(keep) < g.number_of_nodes():
            spared = sum(1 for n in keep
                         if (g.nodes[n].get("papers") or 0) < cfg.min_author_papers)
            extra = f" {spared} seed author(s) kept below it." if spared else ""
            say(f"  dropped {g.number_of_nodes() - len(keep)} author(s) below "
                f"{cfg.min_author_papers} corpus paper(s); {len(keep)} left."
                f"{extra}")
            g = g.subgraph(keep).copy()

    # Trim by whatever the view is about. Ranking by citations while displaying
    # paper counts would drop the prolific-but-uncited authors the paper view
    # exists to show — a lab publishing steadily without being cited *within
    # this corpus* is exactly the case of interest. Seed authors are kept either
    # way, for the same reason seed papers are.
    second = ("global_citations" if rank_by == "in_corpus_citations"
              else "in_corpus_citations")
    noun = "most-published" if rank_by == "papers" else "most-cited"
    g = _trim_to_top(g, cfg.max_graph_nodes, noun + " author", say,
                     key=lambda d: (d.get(rank_by) or 0, d.get(second) or 0))
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


# How long the cursor must rest on a node before its tooltip appears, in
# milliseconds. vis.js defaults to 300, which fires while the cursor is merely
# passing over: on a dense graph the tooltips flicker up one after another and
# reading any single node is hard. 700 waits for the cursor to actually stop.
TOOLTIP_DELAY_MS = 700

# How many nodes a guided tour visits, most connected first.
TOUR_STOPS = 10

# Tour pacing. The first version flew in 1.4 s and moved on after 4.2 s, which
# is long enough to see that something happened and not long enough to read it.
TOUR_FLIGHT_MS = 4200      # camera travel in 3D, where the camera only moves

# 2D needs longer than 3D for the same distance to feel the same. vis.js `focus`
# changes position *and* zoom level together, and the scale change is what the
# eye reads as speed, so a duration that is gentle in a Plotly scene is abrupt
# here.
TOUR_FLIGHT_2D_MS = 6500
TOUR_HOLD_MS = 3200        # time to read the record, AFTER the camera arrives
TOUR_ZOOM = 1.6            # gentler than the 1.9 it started at

# Total time on a stop. Keeping the hold separate from the flight is what lets
# the zoom be slowed and the advance be quickened at the same time: when the
# two were one number, a slower camera meant less reading time.
TOUR_DWELL_MS = TOUR_FLIGHT_MS + TOUR_HOLD_MS
TOUR_DWELL_2D_MS = TOUR_FLIGHT_2D_MS + TOUR_HOLD_MS

# Hard limit on how long the layout may simulate before physics is switched off
# regardless. vis.js does not always emit `stabilizationIterationsDone` on a
# large graph, and until physics stops the main thread is busy enough that the
# buttons feel broken. Better a layout that stopped early than a page that
# cannot be clicked.
SETTLE_LIMIT_MS = 12000

# The node the tour is looking at. Selection alone is too quiet to find on a
# crowded graph, so the current node is recolored and enlarged and put back
# when the tour moves on.
#
# Okabe-Ito reddish purple, chosen to sit apart from both match colors under
# red-green color blindness. **The size and border changes carry the signal on
# their own**, which is the part that works whatever a reader can see.
TOUR_HIGHLIGHT = "#CC79A7"


def _tour_runtime_seconds(stops: int = TOUR_STOPS) -> int:
    """Roughly how long a full tour takes, for telling someone what to record."""
    return int(round(stops * TOUR_DWELL_MS / 1000))


# Shown on the little information mark beside the tour buttons, as a plain
# `title` tooltip — no library, no click, and it is there at the moment someone
# wants it rather than in a document they would have to go and find.
RECORD_HELP = (
    "To make a movie of the tour, screen-record it.\n\n"
    "macOS: Shift-Command-5, choose Record Selected Portion, then Record. "
    "The file lands on the Desktop.\n"
    "Windows 11: Windows-Alt-R for Game Bar, or the Snipping Tool's record "
    "button.\n"
    "Linux, GNOME: Ctrl-Alt-Shift-R. KDE: Spectacle. Otherwise OBS Studio, "
    "which works on all three.\n\n"
    "Start recording first, then press Play tour. A full tour of "
    f"{TOUR_STOPS} nodes runs about {_tour_runtime_seconds() // 60} min "
    f"{_tour_runtime_seconds() % 60} s.\n\n"
    "Pause layout first if the graph is still drifting."
)

# The node record, in the order it reads best. A label of None means the raw
# attribute name is unsuitable for display and the entry is skipped.
# Deliberately short. The tour panel is read at a glance while the camera is
# moving, so it carries what identifies a paper and nothing else; the hover
# still has the counts and the match detail for anyone who wants them.
_RECORD_FIELDS = [
    ("title", "Title"),
    ("author", "Author"),          # author networks have this instead of a title
    ("authors", "Authors"),
    ("pmid", "PMID"),
    ("year", "Year"),
    ("journal", "Journal"),
    ("papers", "Papers in corpus"),
]


def node_record(node, d: dict) -> list[list[str]]:
    """Every field a node carries, as label/value pairs for display.

    **The hover is a summary; this is the record.** The tour shows all of it,
    because the point of stopping on a node is to read it rather than to be told
    its title again. Empty and absent fields are skipped — a blank row says
    nothing — and booleans are rendered as yes/no rather than Python's `True`.
    """
    out: list[list[str]] = []
    for key, label in _RECORD_FIELDS:
        if key not in d:
            continue
        v = d[key]
        if v is None or v == "":
            continue
        if isinstance(v, bool):
            v = "yes" if v else "no"
        out.append([label, str(v)])
    if not out:
        out = [["Node", str(node)]]
    return out


def tour_stops(g, n: int = TOUR_STOPS) -> list[dict]:
    """The most connected nodes, in order, with what to say about each.

    **Degree, not size.** A node is large here because of how often it was
    cited; it is *connected* because of how much of the corpus it touches, and
    the second is what a tour of a network should follow. Ties break on the
    size attribute so the bigger of two equally connected papers goes first.

    Each stop carries the same text the hover shows, so the tour and the
    tooltip can never tell different stories.
    """
    if not g.number_of_nodes():
        return []
    deg = dict(g.degree())
    author = "author" in next(iter(g.nodes(data=True)))[1]
    size_key = "papers" if author and "papers" in next(iter(g.nodes(data=True)))[1] \
        else "in_corpus_citations"
    order = sorted(g.nodes, key=lambda x: (deg.get(x, 0),
                                           g.nodes[x].get(size_key) or 0),
                   reverse=True)
    stops = []
    for node in order[:max(0, n)]:
        d = g.nodes[node]
        lines = (_author_tip_lines(node, d) if author
                 else _citation_tip_lines(node, d))
        stops.append({
            "id": node,
            "label": str(d.get("author") or d.get("pmid") or node),
            "degree": deg.get(node, 0),
            "info": lines,
            "record": node_record(node, d),
        })
    return stops


# **One card, both views.** The 2D and 3D tours each used to build their own
# markup, so a change to one quietly left the other looking like the previous
# version. The CSS and the renderer live here and are injected into both.
CARD_CSS = """
  /* Every class is bl- prefixed. pyvis ships Bootstrap, whose own `.row` and
     `.hd` captured the card and stacked each label above its value. */
  .bl-card {font:13px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",
      system-ui,sans-serif; color:#16202b}
  .bl-card .bl-hd {font-size:15.5px; line-height:1.34; font-weight:600;
    letter-spacing:-.011em; margin:0 0 11px; color:#0f1821}
  .bl-card .bl-rule {height:1px; margin:0 0 11px;
    background:linear-gradient(90deg, __HILITE__ 0%, rgba(0,0,0,.07) 36%,
      rgba(0,0,0,0) 100%)}
  .bl-card .bl-row {display:flex; gap:12px; padding:2.5px 0; margin:0}
  .bl-card .bl-lbl {flex:0 0 76px; max-width:76px; font-size:10.5px;
    text-transform:uppercase; letter-spacing:.07em; color:#93a1b0;
    padding-top:2px}
  .bl-card .bl-val {flex:1 1 auto; min-width:0; word-break:break-word;
    color:#16202b}
  .bl-card a {color:#0072B2; text-decoration:none}
  .bl-card a:hover {text-decoration:underline}
"""

CARD_JS = """
window.BL_CARD = function (record) {
  // The title is the headline, everything else is quiet metadata beneath it.
  // A flat label/value table gave the journal the same weight as the paper.
  var head = "", rows = "";
  for (var r = 0; r < record.length; r++) {
    var lab = record[r][0], v = String(record[r][1]);
    if (/^https?:[/][/]/.test(v)) {
      v = '<a href="' + v + '" target="_blank" rel="noopener">' + v + "</a>";
    }
    if (!head && lab === "Title") { head = v; continue; }
    rows += '<div class="bl-row"><div class="bl-lbl">' + lab +
            '</div><div class="bl-val">' + v + "</div></div>";
  }
  return (head ? '<div class="bl-hd">' + head + "</div>" : "") +
         '<div class="bl-rule"></div>' + rows;
};
"""


def _freeze_physics_after_stabilization(path: str, stops=None) -> None:
    """Give the page a physics switch, settle the layout, and calm the tooltips.

    **The layout is computed in the browser, which is what makes these graphs
    readable**: a server-side spring layout was tried and collapsed a 150-node
    network onto a diagonal line of overlapping nodes. So physics starts on
    load, as it always did.

    What was missing is control. vis.js keeps simulating, and a graph that is
    still drifting cannot be clicked on. Two things fix that:

    - **It stops by itself** when stabilization finishes.
    - **A button stops and restarts it**, so a long settle can be cut short and
      a tangled layout can be shaken out again.

    It also raises the tooltip delay to :data:`TOOLTIP_DELAY_MS`, because the
    vis.js default of 300 ms fires while the cursor is still moving.

    The script waits for pyvis's ``network`` object rather than assuming it
    exists: it is assigned inside ``drawGraph()``, and an injected script that
    runs first would silently attach nothing, which is how the automatic freeze
    came to look like it was working when it was not.
    """
    snippet = """
<style>
  #bl-physics {position:fixed; bottom:16px; left:16px; z-index:9999;
    font:12px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,
      sans-serif;
    background:rgba(255,255,255,.88); -webkit-backdrop-filter:blur(8px);
    backdrop-filter:blur(8px); border:0; border-radius:10px; padding:8px 10px;
    box-shadow:0 4px 16px rgba(20,32,48,.10); color:#1f2a36}
  #bl-physics button {font:inherit; cursor:pointer; border:0;
    background:rgba(31,42,54,.06); color:#1f2a36; border-radius:7px;
    padding:5px 11px; transition:background .15s}
  #bl-physics button:hover {background:rgba(31,42,54,.12)}
  #bl-physics span {color:#8795a4; margin-left:7px}
  #bl-tour-controls button {margin-left:5px}
  #bl-tour-help {cursor:help; margin-left:6px; color:#8795a4; font-size:13px}
  #bl-tour-info {display:none; margin-top:8px; color:#5b6b7c;
    max-width:320px; letter-spacing:.01em}
__CARD_CSS__
  /* The record itself rides next to the node, not in the corner. */
  #bl-node-card {display:none; position:fixed; z-index:10000; width:344px;
    max-height:72vh; overflow-y:auto;
    background:rgba(255,255,255,.82); -webkit-backdrop-filter:blur(18px)
      saturate(140%); backdrop-filter:blur(18px) saturate(140%);
    border:1px solid rgba(255,255,255,.65); border-radius:16px;
    padding:18px 20px 16px; pointer-events:auto;
    box-shadow:0 18px 50px rgba(16,26,40,.18), 0 2px 6px rgba(16,26,40,.06);
    opacity:0; transition:opacity .45s ease-out}
  #bl-node-card.on {opacity:1}
</style>
<div id="bl-physics">
  <button id="bl-physics-toggle">Pause layout</button>
  <span id="bl-physics-state">settling\u2026</span>
  <span id="bl-tour-controls">
    <button id="bl-tour-play">Play tour</button>
    <button id="bl-tour-next">Next</button>
    <button id="bl-tour-reset">Reset view</button>
    <span id="bl-tour-help" title="__RECORD_HELP__">&#9432;</span>
  </span>
  <div id="bl-tour-info"></div>
</div>
<div id="bl-node-card" class="bl-card"></div>
<script type="text/javascript">
__CARD_JS__
(function () {
  var on = true;
  var STOPS = __TOUR_STOPS__;
  function tour(net) {
    // The tour drives the camera, so physics is switched off first: otherwise
    // the nodes keep moving out from under it mid-flight.
    var i = -1, playing = false, timer = null, lit = null;
    var panel = document.getElementById("bl-tour-info");
    var card = document.getElementById("bl-node-card");
    var play = document.getElementById("bl-tour-play");
    var at = null, landed = false;
    var nodes = net.body.data.nodes;
    // **`value` is what sizes a vis.js node, not `size`.** When a node carries
    // a value, vis recomputes `size` from it on every redraw, so enlarging the
    // focus node by writing `size` did nothing at all -- it stayed smaller
    // than its better-cited neighbours while claiming to be the subject.
    var MAXV = 1;
    nodes.forEach(function (n) { MAXV = Math.max(MAXV, n.value || 1); });
    function unlight() {
      if (lit) { nodes.update({id: lit.id, color: lit.color, size: lit.size,
                               value: lit.value, borderWidth: lit.borderWidth});
                 lit = null; }
    }
    function light(id) {
      unlight();
      var n = nodes.get(id);
      lit = {id: id, color: n.color, size: n.size, value: n.value,
             borderWidth: n.borderWidth};
      // A white rim, not a dark one: the focus node should read as lifted off
      // the graph rather than outlined on it.
      nodes.update({id: id, color: {background: "__HILITE__",
                                    border: "#ffffff", highlight:
                                    {background: "__HILITE__", border: "#ffffff"}},
                    value: MAXV * 1.9, size: (n.size || 10) * 1.9,
                    borderWidth: 4});
    }
    // Canvas coordinates survive panning and zooming; DOM coordinates do not.
    // So the card is positioned from the node's canvas position on every
    // redraw rather than once, which is what keeps it beside its node when
    // the view moves afterwards.
    function place() {
      if (!landed || !at) {
        card.classList.remove("on"); card.style.display = "none"; return;
      }
      var pos = net.getPositions([at.id])[at.id];
      if (!pos) { card.style.display = "none"; return; }
      var dom = net.canvasToDOM(pos);
      var box = net.body.container.getBoundingClientRect();
      card.style.display = "block";
      void card.offsetWidth;
      card.classList.add("on");
      var w = card.offsetWidth, h = card.offsetHeight;
      // Clear the marker itself. `size` is in canvas units, so it has to be
      // scaled: at tour zoom a big node is tens of pixels across, and a fixed
      // offset put the card on top of the node it was describing.
      var nd = nodes.get(at.id) || {};
      var r = (nd.size || 10) * net.getScale();
      var x = box.left + dom.x + r + 16, y = box.top + dom.y - h / 2;
      // Always to the right of the node. Clamped, not flipped: flipping put
      // the card on the side the reader was not looking at.
      x = Math.min(x, window.innerWidth - w - 8);
      card.style.left = Math.max(8, x) + "px";
      card.style.top = Math.min(Math.max(8, y), window.innerHeight - h - 8) + "px";
    }
    function show(k) {
      var s = STOPS[k];
      if (!s) { return; }
      net.setOptions({physics: {enabled: false}});
      light(s.id);
      net.selectNodes([s.id]);
      // The card goes away for the duration of the flight. Showing it first
      // means reading a card that is still travelling.
      at = s; landed = false;
      card.classList.remove("on"); card.style.display = "none";
      net.focus(s.id, {scale: __ZOOM__, animation:
        {duration: __FLIGHT__, easingFunction: "easeInOutCubic"}});
      card.innerHTML = BL_CARD(s.record);
      panel.innerHTML = '<div class="bl-tour-head"><b>' + (k + 1) + " of " +
        STOPS.length + "</b> &middot; " + s.degree + " connection(s) &middot; " +
        (s.label || "") + "</div>";
      panel.style.display = "block";
      // `animationFinished` is the real signal; the timer is the fallback,
      // because a focus that is interrupted never fires the event at all.
      var mine = s;
      var land = function () { if (at === mine) { landed = true; place(); } };
      net.once("animationFinished", land);
      setTimeout(land, __FLIGHT__ + 120);
    }
    net.on("afterDrawing", place);
    function step() {
      i = (i + 1) % STOPS.length;
      show(i);
      if (playing) { timer = setTimeout(step, __DWELL__); }
    }
    document.getElementById("bl-tour-next").addEventListener("click", function () {
      playing = false; clearTimeout(timer); play.textContent = "Play tour"; step();
    });
    play.addEventListener("click", function () {
      playing = !playing;
      play.textContent = playing ? "Stop tour" : "Play tour";
      clearTimeout(timer);
      if (playing) { step(); }
    });
    document.getElementById("bl-tour-reset").addEventListener("click", function () {
      playing = false; clearTimeout(timer); play.textContent = "Play tour";
      panel.style.display = "none";
      at = null; landed = false;
      card.classList.remove("on"); card.style.display = "none";
      unlight();
      net.unselectAll();
      net.fit({animation: {duration: 1400, easingFunction: "easeInOutCubic"}});
    });
  }
  function wire(net) {
    // Tooltips should wait for the cursor to stop, not fire on the way past.
    // The edge colors are set here too: selecting a node makes vis.js repaint
    // its edges in the highlight color, and the default is a heavy maroon that
    // slashes across the whole picture.
    net.setOptions({
      interaction: {hover: true, tooltipDelay: __TOOLTIP_DELAY__},
      edges: {width: 1, selectionWidth: 1, smooth: {roundness: 0.2},
              color: {color: "rgba(126,148,184,0.50)", highlight: "__HILITE__",
                      hover: "__HILITE__", inherit: false, opacity: 1}},
      nodes: {borderWidth: 1.5, color: {border: "rgba(255,255,255,0.95)"}}});
    if (STOPS.length) { tour(net); }
    var btn = document.getElementById("bl-physics-toggle");
    var lbl = document.getElementById("bl-physics-state");
    function set(state, note) {
      on = state;
      net.setOptions({physics: {enabled: on}});
      btn.textContent = on ? "Pause layout" : "Resume layout";
      lbl.textContent = note;
    }
    btn.addEventListener("click", function () {
      set(!on, on ? "paused" : "settling\u2026");
    });
    // Stop on its own once the layout has settled. Three ways, because one was
    // not enough: on a large graph `stabilizationIterationsDone` can fail to
    // arrive, the simulation then runs forever, and a busy main thread makes
    // every button feel broken. The watchdog is what guarantees the page
    // becomes responsive.
    function settle(note) { if (on) { set(false, note); } }
    net.on("stabilizationIterationsDone", function () { settle("settled"); });
    net.on("stabilized", function () { settle("settled"); });
    var watchdog = setTimeout(function () {
      settle("settled (time limit)");
    }, __SETTLE_LIMIT__);
    // A manual pause or resume takes the watchdog out of the way.
    btn.addEventListener("click", function () { clearTimeout(watchdog); });
  }
  // `network` is assigned inside drawGraph(); poll briefly rather than assume.
  var tries = 0;
  (function wait() {
    if (typeof network !== "undefined" && network) { wire(network); return; }
    if (tries++ < 200) { setTimeout(wait, 50); }
  })();
})();
</script>
"""
    try:
        with open(path, encoding="utf-8") as f:
            html = f.read()
    except OSError:
        return
    snippet = snippet.replace("__CARD_CSS__", CARD_CSS)
    snippet = snippet.replace("__CARD_JS__", CARD_JS)
    snippet = snippet.replace("__TOOLTIP_DELAY__", str(TOOLTIP_DELAY_MS))
    snippet = snippet.replace("__TOUR_STOPS__", json.dumps(stops or []))
    snippet = snippet.replace("__FLIGHT__", str(TOUR_FLIGHT_2D_MS))
    snippet = snippet.replace("__DWELL__", str(TOUR_DWELL_2D_MS))
    snippet = snippet.replace("__ZOOM__", str(TOUR_ZOOM))
    snippet = snippet.replace("__RECORD_HELP__", RECORD_HELP)
    snippet = snippet.replace("__SETTLE_LIMIT__", str(SETTLE_LIMIT_MS))
    snippet = snippet.replace("__HILITE__", TOUR_HIGHLIGHT)
    if "bl-physics" in html:          # already injected
        return
    if "</body>" in html:
        html = html.replace("</body>", snippet + "</body>", 1)
    else:
        html += snippet
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def _inject_match_legend(path: str, title: str, terms: list[str],
                         unit: str = "paper") -> None:
    """Explain the node colors, under the heading.

    A colored graph with no key is a worse graph than an uncolored one, and
    the "none" case needs the caveat spelled out: PubMed can match a paper on a
    MeSH term or on full text we never fetched, so grey does not mean the hit
    was wrong.

    ``unit`` is "paper" or "author". An author node is colored by their single
    best-matching paper, which is a different claim from a paper node and has
    to be said in the key rather than left to be assumed.
    """
    if not terms:
        return
    try:
        with open(path, encoding="utf-8") as f:
            html = f.read()
    except OSError:
        return
    shown = ", ".join(f"<code>{t}</code>" for t in terms[:8])
    if len(terms) > 8:
        shown += f" and {len(terms) - 8} more"
    swatch = (
        '<span style="display:inline-block;width:11px;height:11px;'
        'border-radius:50%%;background:%s;margin-right:5px;'
        'vertical-align:middle"></span>')
    legend = (
        '<div style="font:13px/1.5 system-ui,sans-serif;color:#1f2a36;'
        'max-width:860px;margin:4px auto 10px;padding:8px 12px;'
        'border:1px solid #d7dee6;border-radius:6px;background:#f8fafc">'
        f'<b>Query terms:</b> {shown}<br>'
        f'{swatch % MATCH_COLORS["all"]}contains every term &nbsp; '
        f'{swatch % MATCH_COLORS["partial"]}contains some &nbsp; '
        f'{swatch % MATCH_COLORS["none"]}contains none'
        + (('<br><span style="color:#5b6b7c"><b>An author is colored by their '
            'single best-matching paper</b>, not by pooling terms across their '
            'papers. Green means one paper of theirs names every term. The '
            'hover gives how many of their papers name any.</span>')
           if unit == "author" else '')
        + '<br><span style="color:#5b6b7c">Matching is literal, on title and '
        'abstract only. PubMed can also match on MeSH terms or on full text '
        'not fetched here, so grey does not mean the hit was wrong. Papers '
        'added by citation expansion never went through the query at '
        'all.</span></div>'
    )
    needle = f"<h1>{title}</h1>"
    at = html.find(needle)
    if at == -1 or legend in html:
        return
    cut = at + len(needle)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html[:cut] + legend + html[cut:])


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
    g: nx.DiGraph, path: str, title: str = "bioleads citation network",
    query_terms: list[str] | None = None,
) -> str:
    """Render the directed citation network to a standalone HTML file (pyvis).

    Nodes are sized by in-corpus citations (most-cited papers are largest);
    arrows point from a paper to the papers it cites. Falls back to GraphML if
    pyvis isn't installed.

    When the graph carries ``query_match`` attributes (see
    :func:`bioleads.querymatch.annotate_citation_graph`), nodes are colored by
    whether the paper's title and abstract contain the PubMed query's terms, and
    `query_terms` is used to caption the legend.
    """
    try:
        from pyvis.network import Network
    except ImportError:
        alt = write_graphml(g, path.rsplit(".", 1)[0] + ".graphml")
        print(f'[bioleads] pyvis not installed; wrote {alt}. '
              f'Install with: pip install "bioleads[viz]"')
        return alt

    net = Network(height="800px", width="100%", notebook=False, directed=True,
                  heading=title, bgcolor="#ffffff")
    # Color by query-term containment only when the graph was annotated with a
    # text query; otherwise leave pyvis's own color alone, as before.
    colored = any("query_match" in d for _, d in g.nodes(data=True))
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
            kw = {}
            if colored:
                state = d.get("query_match", "unknown")
                kw["color"] = MATCH_COLORS.get(state, MATCH_COLORS["unknown"])
                hits = d.get("query_terms_matched") or ""
                tip_lines.append(
                    f"query terms found: {hits}" if hits
                    else "query terms found: none in title/abstract")
                if d.get("expanded"):
                    tip_lines.append("added by citation expansion, not a search hit")
            # No text under the marker: printed under every node the PMID
            # only collided with its neighbours and hid the colors the graph
            # encodes. It stays in the hover and in the tour card.
            #
            # The label is kept and the FONT is zeroed, because pyvis
            # substitutes the node id for a falsy label -- passing label=""
            # put "PMID:12345" under every node instead of nothing.
            net.add_node(n, label=label, font={"size": 0},
                         value=cit + 1, size=size,
                         title="\n".join(tip_lines), **kw)
        for a, b in g.edges():
            # No arrowheads. On a network this dense they stack into a
            # texture that reads as noise rather than as direction, and the
            # direction is in the hover and the heading. The graph is still
            # directed; only the ornament is gone.
            net.add_edge(a, b, title="cites", arrows="")
    net.force_atlas_2based(spring_length=120)
    net.write_html(path, notebook=False, open_browser=False)
    _collapse_duplicate_heading(path, title)  # pyvis 0.3.2 doubles the <h1>
    if colored:
        _inject_match_legend(path, title, query_terms or [])
    _freeze_physics_after_stabilization(path, tour_stops(g))
    return path


def _citation_tip_lines(n, d) -> list[str]:
    """What a paper node says about itself, one line each.

    Shared by the hover, the 3D hover and the guided tour, so the three cannot
    drift into telling different stories about the same node.
    """
    lines = [d.get("title") or d.get("pmid", n), f"PMID: {d.get('pmid', '')}"]
    if d.get("year"):
        lines.append(f"year: {d['year']}")
    if d.get("journal"):
        lines.append(d["journal"])
    lines.append(f"cited by {d.get('in_corpus_citations', 0)} paper(s) in corpus")
    if d.get("global_citations") is not None:
        lines.append(f"global citations: {d['global_citations']}")
    if "query_match" in d:
        hits = d.get("query_terms_matched") or ""
        lines.append(f"query terms found: {hits}" if hits
                     else "query terms found: none in title/abstract")
    return lines


def _citation_hover(n, d) -> str:
    return "<br>".join(_citation_tip_lines(n, d))


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
        color_attr="in_corpus_citations", colors=_match_colors(g),
        hover=_citation_hover, directed=True, stops=tour_stops(g),
    )


def write_graphml(g, path: str) -> str:
    """Write GraphML without needing lxml.

    ``nx.write_graphml`` is bound to the lxml implementation, which imports lxml
    **when called**, not when networkx is imported. So on an install without it
    the failure arrives at the moment of writing, inside the very fallback that
    exists because pyvis is missing — asking for ``--citations`` on a core-only
    install crashed instead of degrading, which is exactly what the fallback was
    there to prevent. That is the install the conda recipe builds.

    networkx ships a pure-stdlib writer too. These graphs are capped at
    ``max_graph_nodes``, so lxml's speed buys nothing here and its absence costs
    everything.
    """
    nx.write_graphml_xml(_graphml_safe(g), path)
    return path


def _graphml_safe(g):
    """A copy GraphML can actually store.

    GraphML takes scalars only, and these graphs carry two things it refuses:

    - **``graph["paper_senior"]``**, a ``{pmid: author}`` dict the author graph
      keeps so the query-match annotation can reach back to the papers behind
      each author. Graph-level attributes are not read by anything consuming
      these files, so the whole dict is dropped.
    - **``None`` values**, chiefly ``global_citations`` when iCite reports no
      count for a paper. An absent attribute carries the same meaning as a null
      one — not known — so those keys are removed rather than coerced to 0,
      which would assert a count of zero that nobody measured.
    """
    h = g.copy()
    h.graph.clear()
    for _, data in h.nodes(data=True):
        for k in [k for k, v in data.items() if v is None]:
            del data[k]
    for _, _, data in h.edges(data=True):
        for k in [k for k, v in data.items() if v is None]:
            del data[k]
    return h


def _match_colors(g) -> dict | None:
    """Per-node query-match colors, or None when the graph was never annotated.

    Returning None rather than a dict of greys matters: it lets the 3D writer
    keep its citation-count colorscale on a run with no text query, instead of
    painting every node the "nothing matched" color.
    """
    if not any("query_match" in d for _, d in g.nodes(data=True)):
        return None
    return {n: MATCH_COLORS.get(d.get("query_match", "unknown"),
                                MATCH_COLORS["unknown"])
            for n, d in g.nodes(data=True)}


def _author_tip_lines(n, d) -> list[str]:
    lines = [d.get("author") or n,
             f"papers in corpus: {d.get('papers', 0)}",
             f"cited {d.get('in_corpus_citations', 0)} time(s) within corpus"]
    if d.get("global_citations"):
        lines.append(f"global citations (sum): {d['global_citations']}")
    if "query_match" in d:
        hits = d.get("query_terms_matched") or ""
        lines.append(f"best paper's query terms: {hits}" if hits
                     else "no paper of theirs names a query term")
        total = d.get("query_papers_total", 0)
        if total:
            lines.append(f"{d.get('query_papers_matched', 0)} of {total} "
                         f"paper(s) name at least one term")
    return lines


def _author_hover(n, d) -> str:
    return "<br>".join(_author_tip_lines(n, d))


def write_author_html(
    g: nx.DiGraph, path: str, title: str = "bioleads senior-author citation network",
    size_attr: str = "in_corpus_citations",
    query_terms: list[str] | None = None,
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
        alt = write_graphml(g, path.rsplit(".", 1)[0] + ".graphml")
        print(f'[bioleads] pyvis not installed; wrote {alt}. '
              f'Install with: pip install "bioleads[viz]"')
        return alt

    net = Network(height="800px", width="100%", notebook=False, directed=True,
                  heading=title, bgcolor="#ffffff")
    colored = any("query_match" in d for _, d in g.nodes(data=True))
    if g.number_of_nodes():
        top = max((d.get(size_attr) or 0 for _, d in g.nodes(data=True)), default=0)
        for n, d in g.nodes(data=True):
            v = d.get(size_attr) or 0
            size = 10 + 30 * (v / top if top else 0)
            kw = {}
            if colored:
                kw["color"] = MATCH_COLORS.get(d.get("query_match", "unknown"),
                                               MATCH_COLORS["unknown"])
            net.add_node(n, label=d.get("author") or n, value=v + 1, size=size,
                         title="\n".join(_author_tip_lines(n, d)), **kw)
        for a, b, ed in g.edges(data=True):
            net.add_edge(a, b, title=f"cites ×{ed.get('weight', 1)}", arrows="")
    net.force_atlas_2based(spring_length=120)
    net.write_html(path, notebook=False, open_browser=False)
    _collapse_duplicate_heading(path, title)  # pyvis 0.3.2 doubles the <h1>
    if colored:
        _inject_match_legend(path, title, query_terms or [], unit="author")
    _freeze_physics_after_stabilization(path, tour_stops(g))
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
        color_attr=size_attr, colors=_match_colors(g),
        hover=_author_hover, directed=True, stops=tour_stops(g),
    )
