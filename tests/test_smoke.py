"""Smoke tests: exercise the full pipeline on a tiny synthetic corpus.

These run without any model downloads or network access — the NER step falls
back to the regex extractor and enrichment falls back to TF-IDF when no
background is supplied.
"""
import os
import warnings
from collections import Counter

import networkx as nx
import pytest

from bioleads.config import Config
from bioleads.cooccurrence import write_graph_html
from bioleads.enrichment import rank_terms
from bioleads.embeddings import (
    TermCluster,
    term_to_cluster,
    write_cluster_scatter,
    to_dataframe as clusters_df,
)
from bioleads.sources import (
    Document,
    _expand_bfs,
    _seed_pmids,
    documents_from_texts,
    expand_pmids,
    load_refs,
    parse_pmid_input,
)
from bioleads.pipeline import run_pipeline
from bioleads.expansion import relevance_guided_expand, _top_k_relevant, _term_overlap_scores
import bioleads.citations as citations
from bioleads.citations import (
    build_citation_graph,
    build_author_citation_graph,
    most_cited,
    to_dataframe as citations_df,
    authors_to_dataframe as authors_df,
)


# A toy corpus engineered so that "trpv1" and "raynaud" never co-occur
# directly, but both connect through "vasodilation" / "bloodflow" — a planted
# ABC link the discovery step should surface.
CORPUS = [
    "trpv1 activation drives vasodilation and bloodflow in arterial tissue.",
    "trpv1 channels modulate vasodilation through calcium signaling pathways.",
    "vasodilation improves bloodflow and relieves raynaud symptoms in patients.",
    "reduced bloodflow and impaired vasodilation characterize raynaud phenomenon.",
    "capsaicin targets trpv1 to promote vasodilation in peripheral vessels.",
    "raynaud episodes follow vasoconstriction and loss of bloodflow.",
]


@pytest.fixture(autouse=True)
def _force_regex_ner(monkeypatch):
    """Keep entity extraction deterministic across environments.

    These tests assert on specific extracted terms (the planted trpv1->raynaud
    link, topic-overlap scores, etc.), which were designed around the regex
    fallback NER. When scispaCy is installed it produces different / multi-word
    entities, so we pin every test to the fallback path by making the model
    loader return None. Real runs still use scispaCy when available.
    """
    import bioleads.ner as _ner
    monkeypatch.setattr(_ner, "_load_scispacy", lambda model: None)


def _cfg():
    return Config(min_doc_freq=1, min_cooccurrence=1, min_pmi=None,
                  min_b_links=1, max_direct_cooccurrence=0, top_terms=50)


def test_pipeline_runs_and_ranks():
    res = run_pipeline(documents=documents_from_texts(CORPUS), cfg=_cfg())
    assert res.documents and res.entities
    terms = {t.term for t in res.ranked_terms}
    assert "vasodilation" in terms
    assert res.graph.number_of_nodes() > 0


def test_abc_finds_planted_link():
    # Seed open discovery from trpv1. With the regex-fallback NER, global
    # ranking is noisy (verbs leak in), so anchored discovery is the realistic
    # and deterministic way to surface the planted trpv1->(B)->raynaud link.
    res = run_pipeline(documents=documents_from_texts(CORPUS), cfg=_cfg(),
                       anchors=["trpv1"])
    pairs = {frozenset((c.a, c.c)) for c in res.candidates}
    assert frozenset(("trpv1", "raynaud")) in pairs


def test_outputs_written(tmp_path):
    res = run_pipeline(documents=documents_from_texts(CORPUS), cfg=_cfg(),
                       out_dir=str(tmp_path))
    assert (tmp_path / "ranked_terms.csv").exists()
    assert (tmp_path / "hypothesis_candidates.csv").exists()


def test_parse_pmid_input_string():
    # comma/space/semicolon separated, with PMID: prefixes and duplicates
    ids = parse_pmid_input("PMID:12345, 67890 67890; pmid:111")
    assert ids == ["12345", "67890", "111"]


def test_parse_pmid_input_list_and_empty():
    assert parse_pmid_input([12345, "67890"]) == ["12345", "67890"]
    assert parse_pmid_input(None) == []
    assert parse_pmid_input("") == []


def test_parse_pmid_input_file(tmp_path):
    f = tmp_path / "ids.txt"
    f.write_text("12345\n67890\n12345\n")  # trailing dup should be dropped
    assert parse_pmid_input(f"@{f}") == ["12345", "67890"]
    assert parse_pmid_input(str(f)) == ["12345", "67890"]


def test_clusters_to_dataframe_and_map():
    clusters = [
        TermCluster(0, ["vasodilation", "vasorelaxation"], "vasodilation"),
        TermCluster(1, ["trpv1"], "trpv1"),
    ]
    df = clusters_df(clusters)
    assert list(df.columns) == ["cluster_id", "centroid_term", "term", "is_centroid"]
    assert len(df) == 3
    assert df[df.term == "vasodilation"].is_centroid.iloc[0]
    assert not df[df.term == "vasorelaxation"].is_centroid.iloc[0]
    assert term_to_cluster(clusters) == {
        "vasodilation": 0, "vasorelaxation": 0, "trpv1": 1}


def test_graph_colored_by_cluster_group(tmp_path):
    g = nx.Graph()
    g.add_edge("a", "b", weight=2, pmi=0.5)
    g.nodes["a"]["count"] = 3
    g.nodes["b"]["count"] = 2
    out = write_graph_html(g, str(tmp_path / "g.html"), groups={"a": 0, "b": 1})
    assert os.path.exists(out)
    # Without the viz extra this falls back to GraphML, where we persist the
    # cluster id as a node attribute; assert it round-trips.
    if out.endswith(".graphml"):
        h = nx.read_graphml(out)
        assert int(h.nodes["a"]["cluster"]) == 0


RIS_SAMPLE = """\
TY  - JOUR
TI  - TRPV1 activation drives vasodilation
AB  - This study shows trpv1 promotes vasodilation and
bloodflow in arterial tissue.
AN  - 12345678
DO  - 10.1000/xyz
UR  - https://example.org/a
ER  -
TY  - JOUR
T1  - Raynaud phenomenon and reduced bloodflow
N2  - Reduced bloodflow characterizes raynaud.
AN  - WOS:000123
ER  -
"""

ENDNOTE_XML_SAMPLE = """\
<?xml version="1.0" encoding="UTF-8"?>
<xml><records>
<record>
<titles><title><style face="normal">Capsaicin targets TRPV1</style></title></titles>
<abstract><style>capsaicin targets trpv1 to promote vasodilation.</style></abstract>
<accession-num>87654321</accession-num>
<electronic-resource-num>10.1000/abc</electronic-resource-num>
</record>
</records></xml>
"""


def test_load_refs_ris(tmp_path):
    f = tmp_path / "lib.ris"
    f.write_text(RIS_SAMPLE)
    docs = load_refs(str(f))
    assert len(docs) == 2
    d0 = docs[0]
    assert d0.title == "TRPV1 activation drives vasodilation"
    assert "bloodflow in arterial tissue" in d0.text  # wrapped line joined
    assert d0.meta["pmid"] == "12345678"
    assert d0.meta["doi"] == "10.1000/xyz"
    assert d0.doc_id == "PMID:12345678"
    # non-numeric accession (Web of Science) is not treated as a PMID
    assert "pmid" not in docs[1].meta
    assert docs[1].doc_id.startswith("ref:")


def test_load_refs_endnote_xml(tmp_path):
    f = tmp_path / "lib.xml"
    f.write_text(ENDNOTE_XML_SAMPLE)
    docs = load_refs(str(f))
    assert len(docs) == 1
    assert docs[0].title == "Capsaicin targets TRPV1"
    assert "capsaicin targets trpv1" in docs[0].text
    assert docs[0].meta["pmid"] == "87654321"


def test_load_refs_bad_format(tmp_path):
    f = tmp_path / "notrefs.md"
    f.write_text("# just some markdown, not a reference export\n")
    with pytest.raises(ValueError):
        load_refs(str(f))


def test_expand_bfs_rounds_and_dedup():
    # citation graph: 1 -> {2,3}; 2 -> {4}; 3 -> {4,5}; 4 -> {1 (cycle)}
    graph = {"1": ["2", "3"], "2": ["4"], "3": ["4", "5"], "4": ["1"], "5": []}

    def neighbors(frontier):
        out = []
        for n in frontier:
            out += graph.get(n, [])
        return out

    # one round from seed 1 -> add its direct references only
    assert _expand_bfs(["1"], neighbors, rounds=1, max_records=100) == ["1", "2", "3"]
    # two rounds -> chase the new frontier, dedup the shared "4" and the cycle
    assert _expand_bfs(["1"], neighbors, rounds=2, max_records=100) == \
        ["1", "2", "3", "4", "5"]
    # max_records caps total (seeds counted)
    assert _expand_bfs(["1"], neighbors, rounds=3, max_records=3) == ["1", "2", "3"]


def test_expand_pmids_guards():
    # rounds<=0 or no seeds -> just the unique seeds, no network call
    assert expand_pmids([], rounds=2) == []
    assert expand_pmids(["1", "1", "2"], rounds=0) == ["1", "2"]
    with pytest.raises(ValueError):
        expand_pmids(["1"], rounds=1, link="bogus")


def test_expand_both_unions_linknames(monkeypatch):
    # "both" follows backward refs AND forward citations, deduped, seeds first.
    # Stub the network so we exercise the union logic, not NCBI.
    import bioleads.sources as S
    monkeypatch.setattr(S, "_entrez", lambda email, api_key: (object(), None))

    def fake_elink(Entrez, ids, linkname):
        if linkname == "pubmed_pubmed_refs":
            return ["10"]      # backward
        if linkname == "pubmed_pubmed_citedin":
            return ["20"]      # forward
        return []

    monkeypatch.setattr(S, "_elink_neighbors", fake_elink)
    # pin source="ncbi" so the default union doesn't also reach for iCite
    assert S.expand_pmids(["1"], rounds=1, link="references", source="ncbi") == ["1", "10"]
    assert S.expand_pmids(["1"], rounds=1, link="cited_by", source="ncbi") == ["1", "20"]
    assert S.expand_pmids(["1"], rounds=1, link="both", source="ncbi") == ["1", "10", "20"]


def test_expand_source_guard_and_icite(monkeypatch):
    import bioleads.sources as S
    # unknown source rejected before any network call
    with pytest.raises(ValueError):
        expand_pmids(["1"], rounds=1, source="bogus")

    # source="icite" routes through _icite_neighbors (not Entrez) and honors
    # the same direction map. Stub the iCite fetch to keep it offline.
    calls = {}

    def fake_icite(ids, fields, timeout=30):
        calls["fields"] = list(fields)
        out = []
        if "references" in fields:
            out.append("100")
        if "cited_by" in fields:
            out.append("200")
        return out

    monkeypatch.setattr(S, "_icite_neighbors", fake_icite)
    # if it touched Entrez we'd hit the network; assert it doesn't by stubbing it to blow up
    monkeypatch.setattr(S, "_entrez", lambda *a, **k: (_ for _ in ()).throw(AssertionError("used ncbi")))
    assert S.expand_pmids(["1"], rounds=1, link="references", source="icite") == ["1", "100"]
    assert S.expand_pmids(["1"], rounds=1, link="both", source="icite") == ["1", "100", "200"]
    assert calls["fields"] == ["references", "cited_by"]


def test_expand_all_unions_and_tolerates(monkeypatch):
    # source="all" (the default) unions NCBI + iCite, deduped...
    import bioleads.sources as S
    monkeypatch.setattr(S, "_entrez", lambda *a, **k: (object(), None))
    monkeypatch.setattr(S, "_elink_neighbors", lambda Entrez, ids, linkname: ["10"])
    monkeypatch.setattr(S, "_icite_neighbors", lambda ids, fields, timeout=30: ["20"])
    assert S.expand_pmids(["1"], rounds=1, link="references", source="all") == ["1", "10", "20"]

    # ...and if one backend fails, "all" still returns the other's results
    def boom(*a, **k):
        raise RuntimeError("service down")

    monkeypatch.setattr(S, "_icite_neighbors", boom)
    with pytest.warns(UserWarning):
        assert S.expand_pmids(["1"], rounds=1, link="references", source="all") == ["1", "10"]

    # a forced single backend that fails should NOT be swallowed
    with pytest.raises(RuntimeError):
        S.expand_pmids(["1"], rounds=1, link="references", source="icite")


def test_cancel_stops_expansion_and_fetch(monkeypatch):
    # A set cancel flag raises PipelineCancelled at the next checkpoint, before
    # any further network work — the Stop button's contract.
    import threading
    import bioleads.sources as S
    from bioleads.sources import PipelineCancelled

    cancel = threading.Event()
    cancel.set()

    # expand_pmids checks the flag inside its per-batch neighbors loop, so the
    # backend stubs must never be reached.
    monkeypatch.setattr(S, "_entrez", lambda *a, **k: (object(), None))
    monkeypatch.setattr(S, "_elink_neighbors",
                        lambda *a, **k: pytest.fail("network hit after cancel"))
    monkeypatch.setattr(S, "_icite_neighbors",
                        lambda *a, **k: pytest.fail("network hit after cancel"))
    with pytest.raises(PipelineCancelled):
        S.expand_pmids(["1"], rounds=1, link="references", source="ncbi", cancel=cancel)

    # fetch_pubmed_by_ids bails at its batch boundary too (efetch never called).
    def boom_efetch(*a, **k):
        pytest.fail("efetch hit after cancel")

    monkeypatch.setattr(
        S, "_entrez",
        lambda *a, **k: (type("E", (), {"efetch": staticmethod(boom_efetch)}), object()))
    with pytest.raises(PipelineCancelled):
        S.fetch_pubmed_by_ids(["1", "2"], cancel=cancel)


def test_write_cluster_scatter_html(tmp_path):
    # 2D term-cluster scatter -> standalone interactive HTML. Pass embeddings
    # directly (row-aligned to the flattened cluster terms) so no model is needed.
    pytest.importorskip("plotly")
    import numpy as np

    clusters = [
        TermCluster(cluster_id=0, terms=["trpv1", "vasodilation", "calcium"],
                    centroid_term="trpv1"),
        TermCluster(cluster_id=1, terms=["mitochondria", "glycolysis"],
                    centroid_term="mitochondria"),
    ]
    # five rows, one per flattened term, in cluster order
    rng = np.random.default_rng(0)
    emb = rng.normal(size=(5, 16))
    out = tmp_path / "term_clusters.html"
    path = write_cluster_scatter(clusters, str(out), embeddings=emb)

    assert path == str(out)
    assert out.exists()
    html = out.read_text()
    assert "trpv1" in html and "mitochondria" in html   # labels/hover baked in
    assert "Plotly" in html or "plotly" in html          # self-contained plot


def test_progress_callback_reports_each_stage():
    # The pipeline should stream a message for every major stage so the GUI log
    # can show live progress. Feed documents directly to keep it offline.
    docs = [
        Document(doc_id="d0", text="trpv1 vasodilation calcium signaling",
                 source="text"),
        Document(doc_id="d1", text="trpv1 channel arterial tissue calcium",
                 source="text"),
    ]
    msgs: list[str] = []
    run_pipeline(documents=docs, cfg=Config(), progress=msgs.append)

    blob = "\n".join(msgs).lower()
    for stage in ("entit", "ranking", "co-occurrence", "abc"):
        assert stage in blob, f"missing progress for stage {stage!r}: {msgs}"
    cfg = Config()
    profile = [Document(doc_id="PMID:1",
                        text="trpv1 vasodilation calcium signaling", source="pubmed")]
    cands = [
        Document(doc_id="PMID:200", text="trpv1 vasodilation channel", source="pubmed"),
        Document(doc_id="PMID:201", text="mitochondria glycolysis oxidative", source="pubmed"),
    ]
    s = _term_overlap_scores(profile, cands, cfg)
    assert s[0] > s[1]  # the candidate sharing topic terms scores higher
    # top-K gate keeps only the on-topic one
    cfg.expand_top_k = 1
    kept = _top_k_relevant(profile, cands, cfg)
    assert [d.doc_id for d, _ in kept] == ["PMID:200"]


def test_relevance_guided_expand_gates_both_directions(monkeypatch):
    # The profile is the seeds alone, and BOTH directions are cut to top-K.
    # Stub the network; with no `embed` extra the scorer falls back to NER
    # term overlap.
    import bioleads.expansion as E

    seed_docs = [Document(doc_id="PMID:1",
                          text="trpv1 vasodilation calcium signaling pathways",
                          source="pubmed")]

    def fake_expand(seeds, *, rounds, link, source, max_records, email, api_key,
                    cancel=None, progress=None):
        if link == "cited_by":
            return list(seeds) + ["100", "101"]   # one on-topic citer, one not
        if link == "references":
            return list(seeds) + ["200", "201"]   # one on-topic ref, one not
        return list(seeds)

    texts = {
        "100": "trpv1 vasodilation arterial tissue",                 # fwd, on-topic
        "101": "crystallography detector calibration software",      # fwd, off-topic
        "200": "trpv1 vasodilation channel calcium",                 # bwd, on-topic
        "201": "mitochondria glycolysis oxidative phosphorylation",  # bwd, off-topic
    }

    def fake_fetch(ids, *, email=None, api_key=None, cancel=None,
                   progress=None):
        return [Document(doc_id=f"PMID:{i}", text=texts[i], source="pubmed") for i in ids]

    monkeypatch.setattr(E, "expand_pmids", fake_expand)
    monkeypatch.setattr(E, "fetch_pubmed_by_ids", fake_fetch)

    cfg = Config(expand_strategy="relevance", expand_top_k=1)
    added = relevance_guided_expand(seed_docs, cfg)
    ids = {d.doc_id for d in added}

    # Forward is gated now, not passed through: the off-topic citer is dropped.
    assert "PMID:100" in ids
    assert "PMID:101" not in ids, "forward citers must be gated, not added wholesale"
    # Backward gated the same way.
    assert "PMID:200" in ids
    assert "PMID:201" not in ids

    # Both directions are tagged and scored.
    fwd = [d for d in added if d.meta.get("expand_phase") == "forward"]
    bwd = [d for d in added if d.meta.get("expand_phase") == "backward"]
    assert [d.doc_id for d in fwd] == ["PMID:100"]
    assert [d.doc_id for d in bwd] == ["PMID:200"]
    assert all(d.meta.get("expanded") for d in added)
    assert all("relevance" in d.meta for d in added), \
        "every kept document now carries its relevance score, forward included"


def test_relevance_profile_is_the_seeds_alone(monkeypatch):
    """A flood of off-topic citers must not drag the profile off the seed topic.

    Under the old design the citers *were* the profile, so enough of them could
    redefine the topic and let their own kind through.
    """
    import bioleads.expansion as E

    seed_docs = [Document(doc_id="PMID:1", text="trpv1 vasodilation calcium artery",
                          source="pubmed")]
    citers = [str(300 + i) for i in range(12)]

    def fake_expand(seeds, *, rounds, link, source, max_records, email, api_key,
                    cancel=None, progress=None):
        if link == "cited_by":
            return list(seeds) + citers
        if link == "references":
            return list(seeds) + ["200", "201"]
        return list(seeds)

    texts = {c: "crystallography detector calibration synchrotron optics" for c in citers}
    texts["200"] = "trpv1 vasodilation channel calcium artery"
    texts["201"] = "crystallography detector calibration synchrotron optics"

    def fake_fetch(ids, *, email=None, api_key=None, cancel=None,
                   progress=None):
        return [Document(doc_id=f"PMID:{i}", text=texts[i], source="pubmed") for i in ids]

    monkeypatch.setattr(E, "expand_pmids", fake_expand)
    monkeypatch.setattr(E, "fetch_pubmed_by_ids", fake_fetch)

    added = relevance_guided_expand(
        seed_docs, Config(expand_strategy="relevance", expand_top_k=1))
    bwd = [d for d in added if d.meta.get("expand_phase") == "backward"]
    # Twelve off-topic citers did not stop the on-topic reference winning.
    assert [d.doc_id for d in bwd] == ["PMID:200"]


def test_seed_pmids_from_documents():
    docs = [
        Document(doc_id="PMID:111", text="a", source="pubmed"),
        Document(doc_id="ref:0", text="b", source="ris", meta={"pmid": "222"}),
        Document(doc_id="ref:1", text="c", source="ris"),  # no PMID -> skipped
        Document(doc_id="PMID:111", text="dup", source="pubmed"),  # dedup
    ]
    assert _seed_pmids(docs) == ["111", "222"]


def test_run_pipeline_with_refs(tmp_path):
    f = tmp_path / "lib.ris"
    f.write_text(RIS_SAMPLE)
    res = run_pipeline(refs=str(f), cfg=_cfg())
    assert len(res.documents) == 2
    assert res.entities


def test_cli_requires_a_pmid_bearing_source(capsys):
    """PDF input is gone, so every source the CLI accepts carries accessions."""
    from bioleads.cli import build_parser, main

    assert main([]) == 2
    assert "--pubmed, --pmids, and/or --refs" in capsys.readouterr().err
    assert not any(a.dest == "pdf" for a in build_parser()._actions)
    assert not any(a.dest == "background" for a in build_parser()._actions)


def test_pmc_full_text_is_gone_end_to_end():
    """Full text was removed because ~28% open-access coverage skewed everything.

    Those documents ran ~30x longer than abstracts, so they supplied 87% of term
    mentions and 99% of co-occurrence pairs: stages 4-6 described the
    open-access subset, not the corpus. Pinned here so no path quietly grows a
    `fulltext=` argument back.
    """
    import inspect

    from bioleads import expansion, sources

    assert not hasattr(Config(), "pubmed_fulltext")
    for gone in ("_fetch_pmc_body", "_upgrade_refs_fulltext", "_pmid_to_pmcid"):
        assert not hasattr(sources, gone), gone
    for fn in (sources.fetch_pubmed, sources.fetch_pubmed_by_ids,
               sources.load_refs, sources.load_documents,
               expansion.relevance_guided_expand, run_pipeline):
        assert "fulltext" not in inspect.signature(fn).parameters, fn.__name__

    from bioleads.cli import build_parser
    assert not any(a.dest == "fulltext" for a in build_parser()._actions)


def test_ranking_is_tfidf_only_and_needs_nothing_external():
    """Background scoring is gone: no file to load, no method to pick, no warning.

    What is left has to run clean on a bare Config, since that is now the only
    way it is ever called.
    """
    import inspect

    from bioleads import enrichment

    for gone in ("background_path", "enrichment_method", "log_odds_prior"):
        assert not hasattr(Config(), gone), gone
    for gone in ("load_background", "_log_odds", "_hypergeometric"):
        assert not hasattr(enrichment, gone), gone
    assert "background" not in inspect.signature(rank_terms).parameters
    assert "background" not in inspect.signature(run_pipeline).parameters

    entities = {"d1": ["trpv1", "artery"], "d2": ["trpv1", "vasodilation"]}
    with warnings.catch_warnings():
        warnings.simplefilter("error")          # any fallback notice fails here
        ranked = rank_terms(entities, Config())
    assert [r.term for r in ranked]
    assert set(ranked[0].as_row()) == {"term", "score", "corpus_count", "doc_freq"}


# --------------------------------------------------------------------------- #
# Citation network
# --------------------------------------------------------------------------- #
# A 3-paper corpus: paper 1 is foundational (cited by 2 and 3), paper 2 is cited
# by 3, paper 3 cites nobody in the set. Global citation_count is independent.
_ICITE_FAKE = {
    "1": {"pmid": 1, "title": "Foundational paper", "year": 2010,
          "journal": "Cell", "citation_count": 500, "authors": "Alice A, Bob B",
          "references": [], "cited_by": ["2", "3"]},
    "2": {"pmid": 2, "title": "Follow-up", "year": 2015,
          "journal": "Nature", "citation_count": 50, "authors": "Carol C, Alice A",
          "references": ["1"], "cited_by": ["3"]},
    "3": {"pmid": 3, "title": "Recent review", "year": 2020,
          "journal": "Science", "citation_count": 5, "authors": "Dan D",
          "references": ["1", "2"], "cited_by": []},
}


def _citation_docs():
    return [
        Document(doc_id="PMID:1", text="foundational work", title="Foundational paper",
                 source="pubmed", meta={"pmid": "1"}),
        Document(doc_id="PMID:2", text="follow up", title="Follow-up",
                 source="pubmed", meta={"pmid": "2"}),
        Document(doc_id="PMID:3", text="review", title="Recent review",
                 source="pubmed", meta={"pmid": "3"}),
    ]


def test_citation_graph_in_corpus_and_global(monkeypatch):
    monkeypatch.setattr(citations, "fetch_icite", lambda pmids, **kw: _ICITE_FAKE)
    g = build_citation_graph(_citation_docs(), Config())

    # Edge A->B means "A cites B"; paper 1 is cited by 2 and 3 within the corpus.
    assert g.number_of_nodes() == 3
    assert g.nodes["PMID:1"]["in_corpus_citations"] == 2
    assert g.nodes["PMID:2"]["in_corpus_citations"] == 1
    assert g.nodes["PMID:3"]["in_corpus_citations"] == 0
    # Global citation_count is carried straight from iCite.
    assert g.nodes["PMID:1"]["global_citations"] == 500
    assert g.has_edge("PMID:2", "PMID:1") and g.has_edge("PMID:3", "PMID:1")

    ranked = most_cited(g)
    assert [n for n, _ in ranked] == ["PMID:1", "PMID:2", "PMID:3"]


def test_citation_graph_skips_non_pmid_docs(monkeypatch):
    monkeypatch.setattr(citations, "fetch_icite", lambda pmids, **kw: _ICITE_FAKE)
    docs = _citation_docs() + [Document(doc_id="pdf0", text="local pdf", source="pdf")]
    g = build_citation_graph(docs, Config())
    assert "pdf0" not in g.nodes  # no PMID -> can't be placed in the network
    assert g.number_of_nodes() == 3


def test_citation_ranking_dataframe(monkeypatch):
    monkeypatch.setattr(citations, "fetch_icite", lambda pmids, **kw: _ICITE_FAKE)
    g = build_citation_graph(_citation_docs(), Config())
    df = citations_df(g)
    assert list(df.columns) == ["pmid", "title", "year", "journal",
                                "in_corpus_citations", "global_citations", "url"]
    # Sorted most-cited first.
    assert df.iloc[0]["pmid"] == "1"
    assert df.iloc[0]["in_corpus_citations"] == 2
    assert df.iloc[0]["global_citations"] == 500


def test_pipeline_writes_citation_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(citations, "fetch_icite", lambda pmids, **kw: _ICITE_FAKE)
    cfg = _cfg()
    cfg.do_citation_network = True
    res = run_pipeline(documents=_citation_docs(), cfg=cfg, out_dir=str(tmp_path))
    assert res.citation_graph is not None
    assert res.citation_graph.number_of_nodes() == 3
    assert os.path.exists(res.outputs["citation_ranking"])
    assert os.path.exists(res.outputs["citation_network"])
    assert "citation net" in res.summary()
    import importlib.util
    if importlib.util.find_spec("plotly"):  # 3D views written alongside the 2D
        assert os.path.exists(res.outputs["graph_3d"])
        assert os.path.exists(res.outputs["citation_network_3d"])


def test_write_citation_html(tmp_path, monkeypatch):
    pytest.importorskip("pyvis")
    from bioleads.citations import write_citation_html
    monkeypatch.setattr(citations, "fetch_icite", lambda pmids, **kw: _ICITE_FAKE)
    g = build_citation_graph(_citation_docs(), Config())
    out = tmp_path / "citation_network.html"
    path = write_citation_html(g, str(out))
    assert os.path.exists(path)
    assert out.read_text().strip()


# --------------------------------------------------------------------------- #
# Author citation network (projected from the paper citation links)
# --------------------------------------------------------------------------- #
def test_author_graph_uses_only_the_senior_author(monkeypatch):
    """One node per lab, not per byline: the last author stands for the paper."""
    monkeypatch.setattr(citations, "fetch_icite", lambda pmids, **kw: _ICITE_FAKE)
    g = build_author_citation_graph(_citation_docs(), Config())

    # Bob (last on paper 1), Alice (last on paper 2), Dan (sole author of 3).
    # Carol is first author on paper 2 and nothing else — she does not appear.
    assert set(g.nodes) == {"Bob B", "Alice A", "Dan D"}
    assert "Carol C" not in g.nodes

    # Alice is first author on paper 1 too; that byline does not earn her a node,
    # so her one paper is the one she was senior on, with its citation count.
    assert g.nodes["Alice A"]["papers"] == 1
    assert g.nodes["Alice A"]["global_citations"] == 50
    assert g.nodes["Bob B"]["global_citations"] == 500

    # One paper→paper link is exactly one author→author edge.
    assert set(g.edges) == {("Alice A", "Bob B"), ("Dan D", "Bob B"),
                            ("Dan D", "Alice A")}
    assert not any(u == v for u, v in g.edges)      # self-citations dropped

    # in_corpus_citations = weighted in-degree (how often the lab is cited).
    assert g.nodes["Bob B"]["in_corpus_citations"] == 2
    assert g.nodes["Alice A"]["in_corpus_citations"] == 1
    assert g.nodes["Dan D"]["in_corpus_citations"] == 0

    assert [n for n, _ in most_cited(g)] == ["Bob B", "Alice A", "Dan D"]


def test_senior_author_accumulates_papers_and_edge_weight(monkeypatch):
    """A lab's papers sum into one node, and repeat citations into one edge."""
    fake = {
        "10": {"pmid": 10, "citation_count": 7, "authors": "Ann A, Lee L",
               "references": ["12"], "cited_by": []},
        "11": {"pmid": 11, "citation_count": 4, "authors": "Bea B, Lee L",
               "references": ["12"], "cited_by": []},
        "12": {"pmid": 12, "citation_count": 90, "authors": "Cy C, Mor M",
               "references": [], "cited_by": ["10", "11"]},
    }
    docs = [Document(doc_id=f"PMID:{p}", text="x", source="pubmed",
                     meta={"pmid": p}) for p in ("10", "11", "12")]
    monkeypatch.setattr(citations, "fetch_icite", lambda pmids, **kw: fake)
    g = build_author_citation_graph(docs, Config())

    assert set(g.nodes) == {"Lee L", "Mor M"}
    assert g.nodes["Lee L"]["papers"] == 2
    assert g.nodes["Lee L"]["global_citations"] == 11      # 7 + 4
    assert g.edges["Lee L", "Mor M"]["weight"] == 2        # two papers, one edge
    assert g.nodes["Mor M"]["in_corpus_citations"] == 2

    # Degree is unweighted, so those two citations are one connection: a
    # threshold of 2 empties the graph even though the edge weighs 2.
    assert build_author_citation_graph(
        docs, Config(min_author_degree=2)).number_of_nodes() == 0


def test_min_paper_degree_drops_isolated_papers(monkeypatch):
    """A paper with no intra-corpus link either way is what degree 1 removes."""
    fake = dict(_ICITE_FAKE)
    fake["4"] = {"pmid": 4, "title": "Unconnected", "year": 2021,
                 "journal": "PLoS One", "citation_count": 3, "authors": "Eve E",
                 "references": [], "cited_by": []}
    docs = _citation_docs() + [
        Document(doc_id="PMID:4", text="unrelated", title="Unconnected",
                 source="pubmed", meta={"pmid": "4"})]
    monkeypatch.setattr(citations, "fetch_icite", lambda pmids, **kw: fake)

    kept_all = build_citation_graph(docs, Config())
    assert kept_all.number_of_nodes() == 4              # 0 = keep everything

    g = build_citation_graph(docs, Config(min_paper_degree=1))
    assert "PMID:4" not in g.nodes
    assert g.number_of_nodes() == 3
    # Surviving nodes keep the counts they were built with — they describe the
    # node's place in the corpus, not in the pruned picture.
    assert g.nodes["PMID:1"]["in_corpus_citations"] == 2


def test_min_paper_degree_counts_citations_given_and_received(monkeypatch):
    """Paper 3 cites two papers and is cited by none: degree 2, not 0."""
    monkeypatch.setattr(citations, "fetch_icite", lambda pmids, **kw: _ICITE_FAKE)
    g = build_citation_graph(_citation_docs(), Config(min_paper_degree=2))
    assert g.nodes["PMID:3"]["in_corpus_citations"] == 0   # never cited...
    assert "PMID:3" in g.nodes                             # ...but not isolated


def test_min_paper_degree_prunes_once_not_as_a_k_core(monkeypatch):
    """5 → 6 → 7: only 6 clears degree 2, and it survives its neighbours' loss."""
    fake = {
        "5": {"pmid": 5, "title": "A", "authors": "Ann A",
              "references": ["6"], "cited_by": []},
        "6": {"pmid": 6, "title": "B", "authors": "Ben B",
              "references": ["7"], "cited_by": ["5"]},
        "7": {"pmid": 7, "title": "C", "authors": "Cal C",
              "references": [], "cited_by": ["6"]},
    }
    docs = [Document(doc_id=f"PMID:{p}", text="t", title=p, source="pubmed",
                     meta={"pmid": p}) for p in ("5", "6", "7")]
    monkeypatch.setattr(citations, "fetch_icite", lambda pmids, **kw: fake)

    g = build_citation_graph(docs, Config(min_paper_degree=2))
    assert set(g.nodes) == {"PMID:6"}       # iterating would have emptied it


def test_min_author_degree_drops_isolated_authors(monkeypatch):
    """The author graph takes the same threshold, on distinct partners."""
    fake = dict(_ICITE_FAKE)
    fake["4"] = {"pmid": 4, "title": "Unconnected", "year": 2021,
                 "journal": "PLoS One", "citation_count": 3, "authors": "Eve E",
                 "references": [], "cited_by": []}
    docs = _citation_docs() + [
        Document(doc_id="PMID:4", text="unrelated", title="Unconnected",
                 source="pubmed", meta={"pmid": "4"})]
    monkeypatch.setattr(citations, "fetch_icite", lambda pmids, **kw: fake)

    assert "Eve E" in build_author_citation_graph(docs, Config()).nodes
    g = build_author_citation_graph(docs, Config(min_author_degree=1))
    assert "Eve E" not in g.nodes
    assert set(g.nodes) == {"Alice A", "Bob B", "Dan D"}

    # Nobody in this corpus has three connections, so a threshold of 3 empties it.
    g3 = build_author_citation_graph(docs, Config(min_author_degree=3))
    assert g3.number_of_nodes() == 0


def test_degree_thresholds_reach_the_written_outputs(tmp_path, monkeypatch):
    """The rankings are filtered too, not just the HTML views."""
    import pandas as pd

    fake = dict(_ICITE_FAKE)
    fake["4"] = {"pmid": 4, "title": "Unconnected", "year": 2021,
                 "journal": "PLoS One", "citation_count": 3, "authors": "Eve E",
                 "references": [], "cited_by": []}
    docs = _citation_docs() + [
        Document(doc_id="PMID:4", text="unrelated", title="Unconnected",
                 source="pubmed", meta={"pmid": "4"})]
    monkeypatch.setattr(citations, "fetch_icite", lambda pmids, **kw: fake)

    res = run_pipeline(documents=docs,
                       cfg=Config(do_citation_network=True,
                                  min_paper_degree=1, min_author_degree=1),
                       out_dir=str(tmp_path))
    papers = pd.read_csv(res.outputs["citation_ranking"])
    authors = pd.read_csv(res.outputs["author_ranking"])
    assert 4 not in set(papers["pmid"])
    assert "Eve E" not in set(authors["author"])


def test_degree_thresholds_are_independent(monkeypatch):
    """The reason they are two controls: one number does not fit both graphs.

    The author graph is a projection, so a threshold that thins the papers is
    barely felt by the authors — here degree 2 removes a third of the papers and
    no authors at all.
    """
    fake = dict(_ICITE_FAKE)
    fake["4"] = {"pmid": 4, "title": "Unconnected", "year": 2021,
                 "journal": "PLoS One", "citation_count": 3, "authors": "Eve E",
                 "references": [], "cited_by": []}
    docs = _citation_docs() + [
        Document(doc_id="PMID:4", text="unrelated", title="Unconnected",
                 source="pubmed", meta={"pmid": "4"})]
    monkeypatch.setattr(citations, "fetch_icite", lambda pmids, **kw: fake)

    cfg = Config(min_paper_degree=2, min_author_degree=0)
    assert build_citation_graph(docs, cfg).number_of_nodes() == 3   # 4 dropped
    assert build_author_citation_graph(docs, cfg).number_of_nodes() == 4  # intact

    # ...and the author threshold leaves the papers alone.
    cfg = Config(min_paper_degree=0, min_author_degree=1)
    assert build_citation_graph(docs, cfg).number_of_nodes() == 4
    assert "Eve E" not in build_author_citation_graph(docs, cfg).nodes


def test_degree_threshold_flags(monkeypatch):
    from bioleads.cli import build_parser

    base = ["--pmids", "1"]
    args = build_parser().parse_args(base)
    assert (args.min_paper_degree, args.min_author_degree) == (0, 0)
    args = build_parser().parse_args(
        base + ["--min-paper-degree", "3", "--min-author-degree", "12"])
    assert (args.min_paper_degree, args.min_author_degree) == (3, 12)


def test_parse_authors_formats():
    from bioleads.citations import _parse_authors
    # Live iCite returns a list of {"fullName": ...} dicts.
    assert _parse_authors([{"fullName": "Ding, Li"}, {"fullName": "Getz, Gad"}]) == \
        ["Ding, Li", "Getz, Gad"]
    # Legacy comma-separated string form still works.
    assert _parse_authors("Alice A, Bob B") == ["Alice A", "Bob B"]
    # Other key spellings + case-insensitive de-dup.
    assert _parse_authors([{"name": "Alice A"}, {"full_name": "alice a"}, "Bob B"]) == \
        ["Alice A", "Bob B"]
    assert _parse_authors(None) == [] and _parse_authors("") == []


def test_author_graph_from_icite_dict_authors(monkeypatch):
    # Mirror the live iCite payload shape (list-of-dicts authors) to guard the
    # author network against silently emptying out.
    fake = {
        "1": {"pmid": 1, "citation_count": 9,
              "authors": [{"fullName": "Ding, Li"}, {"fullName": "Getz, Gad"}],
              "references": [], "cited_by": ["2"]},
        "2": {"pmid": 2, "citation_count": 3,
              "authors": [{"fullName": "Smith, Jane"}],
              "references": ["1"], "cited_by": []},
    }
    monkeypatch.setattr(citations, "fetch_icite", lambda pmids, **kw: fake)
    docs = [Document(doc_id=f"PMID:{i}", text="x", source="pubmed", meta={"pmid": str(i)})
            for i in (1, 2)]
    g = build_author_citation_graph(docs, Config())
    assert g.number_of_nodes() == 2  # not zero!
    # Getz is last on paper 1, so the lab node is his; Ding, first author, is
    # not in the graph at all.
    assert "Ding, Li" not in g.nodes
    assert g.nodes["Getz, Gad"]["in_corpus_citations"] == 1  # cited by Smith via 2→1
    assert g.has_edge("Smith, Jane", "Getz, Gad")


def test_author_ranking_dataframe(monkeypatch):
    monkeypatch.setattr(citations, "fetch_icite", lambda pmids, **kw: _ICITE_FAKE)
    g = build_author_citation_graph(_citation_docs(), Config())
    df = authors_df(g)
    assert list(df.columns) == ["author", "papers", "in_corpus_citations",
                                "global_citations"]
    assert df.iloc[0]["author"] == "Bob B"          # senior author of paper 1
    assert df.iloc[0]["in_corpus_citations"] == 2


def test_pipeline_writes_author_outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(citations, "fetch_icite", lambda pmids, **kw: _ICITE_FAKE)
    cfg = _cfg()
    cfg.do_citation_network = True
    res = run_pipeline(documents=_citation_docs(), cfg=cfg, out_dir=str(tmp_path))
    assert res.author_graph is not None
    assert res.author_graph.number_of_nodes() == 3   # one per senior author
    assert os.path.exists(res.outputs["author_ranking"])
    assert os.path.exists(res.outputs["author_network"])
    assert "senior-author net" in res.summary()
    import importlib.util
    if importlib.util.find_spec("plotly"):
        assert os.path.exists(res.outputs["author_network_3d"])


def test_write_author_html(tmp_path, monkeypatch):
    pytest.importorskip("pyvis")
    from bioleads.citations import write_author_html
    monkeypatch.setattr(citations, "fetch_icite", lambda pmids, **kw: _ICITE_FAKE)
    g = build_author_citation_graph(_citation_docs(), Config())
    out = tmp_path / "author_network.html"
    path = write_author_html(g, str(out))
    assert os.path.exists(path)
    assert out.read_text().strip()


# --------------------------------------------------------------------------- #
# Graph rendering: 2D heading fix + 3D Plotly
# --------------------------------------------------------------------------- #
def test_pyvis_heading_not_duplicated(tmp_path):
    pytest.importorskip("pyvis")
    res = run_pipeline(documents=documents_from_texts(CORPUS), cfg=_cfg(),
                       out_dir=str(tmp_path))
    html = (tmp_path / "cooccurrence.html").read_text()
    # pyvis 0.3.2 doubles the <h1>; our post-process collapses it to one.
    assert html.count("<h1>bioleads co-occurrence</h1>") == 1
    assert "graph" in res.outputs


def test_write_graph_3d_cooccurrence(tmp_path):
    pytest.importorskip("plotly")
    from bioleads.cooccurrence import build_cooccurrence, write_graph_html_3d
    from bioleads.ner import extract_entities
    ents = extract_entities(documents_from_texts(CORPUS), _cfg())
    g = build_cooccurrence(ents, _cfg())
    out = tmp_path / "cooccurrence_3d.html"
    path = write_graph_html_3d(g, str(out), seed=0)
    assert path and os.path.exists(path)
    assert "plotly" in out.read_text().lower()


def test_write_citation_graph_3d(tmp_path, monkeypatch):
    pytest.importorskip("plotly")
    from bioleads.citations import write_citation_html_3d
    monkeypatch.setattr(citations, "fetch_icite", lambda pmids, **kw: _ICITE_FAKE)
    g = build_citation_graph(_citation_docs(), Config())
    out = tmp_path / "citation_network_3d.html"
    path = write_citation_html_3d(g, str(out))
    assert path and os.path.exists(path)
    assert "plotly" in out.read_text().lower()


def test_write_author_graph_3d(tmp_path, monkeypatch):
    pytest.importorskip("plotly")
    from bioleads.citations import write_author_html_3d
    monkeypatch.setattr(citations, "fetch_icite", lambda pmids, **kw: _ICITE_FAKE)
    g = build_author_citation_graph(_citation_docs(), Config())
    out = tmp_path / "author_network_3d.html"
    path = write_author_html_3d(g, str(out))
    assert path and os.path.exists(path)
    assert "plotly" in out.read_text().lower()


# ------------------------------------------------ Rocchio negative term --

def _pinned_entities(monkeypatch, mapping):
    """Pin NER output so these tests don't depend on which engine is installed."""
    import bioleads.ner as ner_mod
    monkeypatch.setattr(
        ner_mod, "extract_entities",
        lambda docs, cfg=None, **kw: {d.doc_id: mapping[d.doc_id] for d in docs})


def _rocchio_fixture(monkeypatch):
    """A profile that cannot separate an on-topic paper from a methods paper.

    The profile carries the topic *and* the methods vocabulary its own papers
    use, so X (on topic) and Y (methods) overlap it equally — a positive-only
    centroid ties them. The tail shares Y's vocabulary, diluted with its own
    jargon, so it ranks last and supplies hard negatives for free.
    """
    from bioleads.sources import Document

    def doc(i):
        return Document(doc_id=f"PMID:{i}", text="x", source="pubmed")

    ents = {
        "PMID:1": ["trpv1", "vasodilation", "artery", "calcium", "imaging", "microscopy"],
        "PMID:100": ["trpv1", "vasodilation", "artery"],
        "PMID:101": ["calcium", "imaging", "microscopy"],
    }
    junk = ["buffer", "pipette", "coverslip", "objective", "laser", "filter", "dish"]
    for i in range(8):
        ents[f"PMID:{200 + i}"] = ["calcium", "imaging", "microscopy"] + junk + [f"s{i:02d}"]
    _pinned_entities(monkeypatch, ents)
    return [doc(1)], [doc(100), doc(101)] + [doc(200 + i) for i in range(8)]


def test_rocchio_negative_term_separates_a_hard_negative(monkeypatch):
    from bioleads.expansion import _term_overlap_scores

    profile, cands = _rocchio_fixture(monkeypatch)

    positive_only = _term_overlap_scores(profile, cands, Config(rocchio_gamma=0.0))
    assert positive_only[0] == pytest.approx(positive_only[1]), (
        "fixture should tie the on-topic and methods papers under a bare centroid")

    with_negative = _term_overlap_scores(
        profile, cands, Config(rocchio_gamma=0.25, expand_top_k=1))
    # X (on topic) is promoted over Y (methods) once the tail is subtracted.
    assert with_negative[0] > with_negative[1]
    # ...and the off-topic tail is pushed down, not merely reordered.
    assert max(with_negative[2:]) < min(with_negative[:2])


def test_rocchio_gamma_zero_is_the_old_behavior(monkeypatch):
    from bioleads.expansion import _term_overlap_scores

    profile, cands = _rocchio_fixture(monkeypatch)
    cfg = Config(rocchio_gamma=0.0)
    once = _term_overlap_scores(profile, cands, cfg)
    assert once == _term_overlap_scores(profile, cands, cfg)
    assert all(x >= 0 for x in once), "positive-only scores are cosines of counts"


def test_rocchio_applies_to_the_embedding_path_too(monkeypatch):
    """Same construction, but through the PubMedBERT scorer with pinned vectors."""
    np = pytest.importorskip("numpy")
    import bioleads.embeddings as emb_mod
    from bioleads.expansion import _embedding_scores
    from bioleads.sources import Document

    topic, methods = np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])
    vecs = {
        "PMID:1": topic + methods,                 # profile: topic AND methods
        "PMID:100": topic,                         # on topic
        "PMID:101": methods,                       # methods, equally close
    }
    for i in range(8):                             # tail: methods + own jargon
        vecs[f"PMID:{200 + i}"] = methods + np.array([0.0, 0.0, 2.0])

    monkeypatch.setattr(emb_mod, "embed_texts",
                        lambda texts, cfg=None: np.vstack([vecs[t] for t in texts]))

    def doc(i):
        return Document(doc_id=f"PMID:{i}", text=f"PMID:{i}", source="pubmed")

    profile = [doc(1)]
    cands = [doc(100), doc(101)] + [doc(200 + i) for i in range(8)]

    positive_only = _embedding_scores(profile, cands, Config(rocchio_gamma=0.0))
    assert positive_only[0] == pytest.approx(positive_only[1])

    with_negative = _embedding_scores(
        profile, cands, Config(rocchio_gamma=0.25, expand_top_k=1))
    assert with_negative[0] > with_negative[1]


def test_pseudo_negatives_never_eat_the_kept_top_k():
    from bioleads.expansion import _pseudo_negative_idx

    # Off by default when the pool is too small for a meaningful tail.
    assert _pseudo_negative_idx([1.0, 0.5], Config(rocchio_gamma=0.5), top_k=1) == []
    # gamma = 0 disables it regardless of pool size.
    assert _pseudo_negative_idx(list(range(50)), Config(rocchio_gamma=0.0), top_k=5) == []
    # A greedy tail is clamped so it cannot overlap the top-K.
    greedy = Config(rocchio_gamma=0.5, rocchio_neg_frac=0.9)
    assert len(_pseudo_negative_idx(list(range(10)), greedy, top_k=8)) == 2
    # The tail is the *worst*-scoring end.
    scores = [0.9, 0.1, 0.8, 0.2, 0.7, 0.3, 0.6, 0.4, 0.5, 0.05]
    idx = _pseudo_negative_idx(scores, Config(rocchio_gamma=0.5), top_k=1)
    assert set(idx) == {9, 1}, f"expected the two lowest scores, got {idx}"


def test_embedder_is_loaded_once_and_reused(monkeypatch):
    """PubMedBERT must not be re-read from disk on every _embed call.

    Regression guard: _embed used to call from_pretrained itself, so a single
    clustering run or relevance sweep reloaded ~400MB of weights dozens of times.
    """
    import sys
    import types

    from bioleads import embeddings as emb

    loads = {"tok": 0, "model": 0}

    class _FakeAuto:
        def __init__(self, key):
            self.key = key

        def from_pretrained(self, name):
            loads[self.key] += 1
            return object()

    fake = types.ModuleType("transformers")
    fake.AutoTokenizer = _FakeAuto("tok")
    fake.AutoModel = _FakeAuto("model")
    # the real model object needs .eval(); hand back something that has it
    fake.AutoModel.from_pretrained = lambda name: types.SimpleNamespace(
        eval=lambda: loads.__setitem__("model", loads["model"] + 1))

    monkeypatch.setitem(sys.modules, "transformers", fake)
    emb._load_embedder.cache_clear()
    try:
        a = emb._load_embedder("some/model")
        b = emb._load_embedder("some/model")
        assert a is b, "second call must come from the cache"
        assert loads["tok"] == 1, f"tokenizer loaded {loads['tok']} times"
        # a different model name is a separate entry
        emb._load_embedder("other/model")
        assert loads["tok"] == 2
    finally:
        emb._load_embedder.cache_clear()


def test_centring_recovers_signal_the_shared_direction_hides(monkeypatch):
    """Reproduces the measured anisotropy: a huge common component plus a small
    distinguishing one. Uncentred, every candidate scores nearly the same;
    centred, the on-topic candidate separates."""
    np = pytest.importorskip("numpy")
    import bioleads.embeddings as emb_mod
    from bioleads.expansion import _embedding_scores

    shared = np.array([10.0, 0.0, 0.0])          # the ~99.5% every paper carries
    topic = np.array([0.0, 1.0, 0.0])            # what the seeds are about
    other = np.array([0.0, 0.0, 1.0])            # a different subject
    vecs = {
        "seed": shared + topic,
        "on":   shared + topic,                  # same subject as the seeds
        "off1": shared + other,
        "off2": shared + other,
        "off3": shared + other,
    }

    def fake_embed(texts, cfg=None):
        return np.vstack([vecs[t] for t in texts])

    monkeypatch.setattr(emb_mod, "embed_texts", fake_embed)

    def doc(key):
        return Document(doc_id=key, text=key, source="pubmed", meta={"pmid": key})

    profile = [doc("seed")]
    cands = [doc("on"), doc("off1"), doc("off2"), doc("off3")]

    plain = _embedding_scores(profile, cands, Config(rocchio_gamma=0.0))
    centred = _embedding_scores(profile, cands,
                                Config(rocchio_gamma=0.0, relevance_center=True))

    # Uncentred, the shared direction dominates and the gap is tiny.
    gap_plain = plain[0] - max(plain[1:])
    gap_centred = centred[0] - max(centred[1:])
    assert gap_plain < 0.02, f"setup should nearly tie uncentred: {plain}"
    assert gap_centred > 0.5, f"centring should separate them: {centred}"
    assert gap_centred > gap_plain * 10
    # ordering is correct either way; centring widens the margin
    assert plain[0] == max(plain) and centred[0] == max(centred)


def test_centring_is_off_by_default_and_a_no_op_without_it(monkeypatch):
    np = pytest.importorskip("numpy")
    import bioleads.embeddings as emb_mod
    from bioleads.expansion import _embedding_scores

    assert Config().relevance_center is False, "must stay opt-in until measured"

    rng = np.random.default_rng(0)
    mat = rng.normal(size=(4, 6)) + 5.0
    monkeypatch.setattr(emb_mod, "embed_texts", lambda texts, cfg=None: mat[: len(texts)])

    def doc(i):
        return Document(doc_id=str(i), text=str(i), source="pubmed", meta={"pmid": str(i)})

    profile, cands = [doc(0)], [doc(1), doc(2), doc(3)]
    a = _embedding_scores(profile, cands, Config(rocchio_gamma=0.0))
    b = _embedding_scores(profile, cands, Config(rocchio_gamma=0.0))
    assert a == b


def test_reported_relevance_is_centred_but_selection_is_not(monkeypatch):
    """Centring was measured not to improve retrieval, so it must not touch which
    documents are kept — only the score written onto them, which is otherwise
    ~0.99 for everything and unreadable."""
    np = pytest.importorskip("numpy")
    import bioleads.embeddings as emb_mod
    from bioleads.expansion import _top_k_relevant, _embedding_scores

    shared = np.array([10.0, 0.0, 0.0])
    vecs = {
        "seed": shared + np.array([0.0, 1.0, 0.0]),
        "c0":   shared + np.array([0.0, 1.0, 0.0]),      # best match
        "c1":   shared + np.array([0.0, 0.6, 0.4]),
        "c2":   shared + np.array([0.0, 0.2, 0.8]),
        "c3":   shared + np.array([0.0, 0.0, 1.0]),      # worst
    }
    monkeypatch.setattr(emb_mod, "embed_texts",
                        lambda texts, cfg=None: np.vstack([vecs[t] for t in texts]))

    def doc(k):
        return Document(doc_id=k, text=k, source="pubmed", meta={"pmid": k})

    profile = [doc("seed")]
    cands = [doc(f"c{i}") for i in range(4)]
    cfg = Config(rocchio_gamma=0.0, expand_top_k=2)

    kept = _top_k_relevant(profile, cands, cfg)
    raw = _embedding_scores(profile, cands, cfg)

    # WHICH documents survive is decided by the raw scores...
    picked = [d.doc_id for d, _ in kept]
    assert set(picked) == {d.doc_id for d, _ in
                           sorted(zip(cands, raw), key=lambda t: t[1],
                                  reverse=True)[:2]}
    assert set(picked) == {"c0", "c1"}
    # ...but the ORDER they come back in is by the reported score.
    reported_seq = [score for _, score in kept]
    assert reported_seq == sorted(reported_seq, reverse=True), \
        f"output should be ordered by the reported score: {reported_seq}"

    # Raw cosines are crushed together near 1 — the reason for reporting
    # something else at all.
    assert min(raw) > 0.99, f"setup should produce saturated raw scores: {raw}"

    # The reported score for each kept doc is exactly what centred scoring gives
    # it, not its raw cosine.
    centred = _embedding_scores(profile, cands,
                                Config(rocchio_gamma=0.0, expand_top_k=2,
                                       relevance_center=True))
    by_id = {d.doc_id: c for d, c in zip(cands, centred)}
    for doc_obj, score in kept:
        assert score == pytest.approx(by_id[doc_obj.doc_id]), \
            f"{doc_obj.doc_id}: reported {score} is not the centred score"
    raw_by_id = {d.doc_id: r for d, r in zip(cands, raw)}
    assert any(score != pytest.approx(raw_by_id[d.doc_id]) for d, score in kept), \
        "reported scores are indistinguishable from the raw ones"


def test_term_space_reports_its_own_scores(monkeypatch):
    """The term fallback is not anisotropic, so there is nothing to centre."""
    import bioleads.ner as ner_mod
    from bioleads.expansion import _term_overlap_scores

    ents = {"s": ["trpv1", "artery"], "a": ["trpv1", "artery"], "b": ["mitochondria"]}
    monkeypatch.setattr(ner_mod, "extract_entities",
                        lambda docs, cfg=None, **kw: {d.doc_id: ents[d.doc_id] for d in docs})

    def doc(k):
        return Document(doc_id=k, text=k, source="pubmed", meta={"pmid": k})

    sel, rep = _term_overlap_scores([doc("s")], [doc("a"), doc("b")],
                                    Config(rocchio_gamma=0.0), with_reported=True)
    assert sel == rep


def test_output_order_follows_the_reported_score_not_the_raw_one(monkeypatch):
    """The two orderings genuinely differ, so this pins which one is returned."""
    np = pytest.importorskip("numpy")
    import bioleads.embeddings as emb_mod
    from bioleads.expansion import _top_k_relevant, _embedding_scores

    shared = np.array([10.0, 0.0, 0.0])
    # Chosen so centring reorders the survivors relative to the raw cosine.
    vecs = {
        "seed": shared + np.array([0.0, 1.0, 0.20]),
        "c0":   shared + np.array([0.0, 1.0, 0.00]),
        "c1":   shared + np.array([0.0, 0.9, 0.45]),
        "c2":   shared + np.array([0.0, 0.5, 0.10]),
        "c3":   shared + np.array([0.0, 0.0, 1.00]),
    }
    monkeypatch.setattr(emb_mod, "embed_texts",
                        lambda texts, cfg=None: np.vstack([vecs[t] for t in texts]))

    def doc(k):
        return Document(doc_id=k, text=k, source="pubmed", meta={"pmid": k})

    profile, cands = [doc("seed")], [doc(f"c{i}") for i in range(4)]
    cfg = Config(rocchio_gamma=0.0, expand_top_k=3)

    kept = _top_k_relevant(profile, cands, cfg)
    raw = _embedding_scores(profile, cands, cfg)
    raw_order = [d.doc_id for d, _ in
                 sorted(zip(cands, raw), key=lambda t: t[1], reverse=True)[:3]]

    scores = [sc for _, sc in kept]
    assert scores == sorted(scores, reverse=True), "not sorted by reported score"
    assert set(d.doc_id for d, _ in kept) == set(raw_order), "selection changed"
