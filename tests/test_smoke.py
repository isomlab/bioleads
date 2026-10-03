"""Smoke tests: exercise the full pipeline on a tiny synthetic corpus.

These run without any model downloads or network access — the NER step falls
back to the regex extractor and enrichment falls back to TF-IDF when no
background is supplied.
"""
import os
import warnings
from collections import Counter
from pathlib import Path

import networkx as nx
import pytest

from bioleads.config import Config
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
    document_pmids,
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
    """Config for the offline tests.

    **`expand_rounds=0` is pinned here deliberately, not inherited.** When the
    default became 3, every pipeline test passing `_cfg()` started walking
    citations over the live NCBI API: `test_run_pipeline_with_refs` went from 2
    documents to 108. A test helper that leans on a default is coupled to it,
    and the coupling shows up only as a slow, networked, flaky suite.
    """
    return Config(min_doc_freq=1, min_cooccurrence=1, min_pmi=None,
                  min_b_links=1, max_direct_cooccurrence=0, top_terms=50,
                  expand_rounds=0)


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
    # out_dir is the results ROOT; the files land in the run folder under it.
    res = run_pipeline(documents=documents_from_texts(CORPUS), cfg=_cfg(),
                       out_dir=str(tmp_path))
    run = Path(res.run_dir)
    assert run.parent == tmp_path
    assert (run / "ranked_terms.csv").exists()
    assert (run / "hypothesis_candidates.csv").exists()


def test_pmid_list_is_one_bare_pmid_per_line(tmp_path):
    """pmids.txt has to paste straight into PubMed, so nothing but the IDs.

    No header, no `PMID:` prefix and no blank lines -- one of those would make
    the file need editing before it could be used, which is the whole point of
    writing it separately from the CSVs.
    """
    res = run_pipeline(documents=_citation_docs(), cfg=_cfg(),
                       out_dir=str(tmp_path))

    written = Path(res.run_dir) / "pmids.txt"
    assert written.exists(), "a PMID-bearing corpus produced no pmids.txt"
    assert res.outputs["pmids"] == str(written)
    assert written.read_text() == "1\n2\n3\n"


def test_no_pmid_list_when_nothing_in_the_corpus_has_one(tmp_path):
    """A PDF-only run writes no file rather than an empty one.

    An empty pmids.txt reads as a run that failed to find anything; the absent
    key greys the row in the Outputs tab instead, which says "not applicable".
    """
    res = run_pipeline(documents=documents_from_texts(CORPUS), cfg=_cfg(),
                       out_dir=str(tmp_path))

    assert not (tmp_path / "pmids.txt").exists()
    assert "pmids" not in res.outputs


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


def _two_blobs():
    """Embeddings with an obvious answer: two tight, well-separated groups.

    Deterministic, and no model needed — cluster_terms takes embeddings.
    """
    import numpy as np

    rng = np.random.default_rng(0)
    a = np.array([1.0, 0.0, 0.0]) + rng.normal(scale=0.01, size=(6, 3))
    b = np.array([0.0, 1.0, 0.0]) + rng.normal(scale=0.01, size=(6, 3))
    terms = [f"a{i}" for i in range(6)] + [f"b{i}" for i in range(6)]
    return terms, np.vstack([a, b])


def test_hdbscan_picks_the_cluster_count_itself():
    # The whole point of the default: no k is supplied anywhere and the two
    # groups still come back as two.
    from bioleads.embeddings import cluster_terms

    terms, emb = _two_blobs()
    clusters = cluster_terms(terms, Config(), embeddings=emb)

    real = [c for c in clusters if not c.is_noise]
    assert len(real) == 2, [c.terms for c in clusters]
    # Each blob stayed whole and didn't mix with the other.
    for c in real:
        assert len({t[0] for t in c.terms}) == 1
    # Nothing was dropped on the way through.
    assert sorted(t for c in clusters for t in c.terms) == sorted(terms)
    # The centroid is a real member, not an invented label.
    assert all(c.centroid_term in c.terms for c in real)


def test_hdbscan_reduces_before_it_measures_density():
    # Term embeddings are anisotropic enough (mean pairwise cosine ~0.93) that
    # clustering them raw returns one blob; the centre+PCA step is what makes
    # the groups separable, so guard that it is actually applied.
    import numpy as np
    from bioleads.embeddings import _reduce_for_density

    rng = np.random.default_rng(0)
    shared = np.ones(64) * 5.0            # the direction every vector shares
    a = shared + np.array([3.0] + [0.0] * 63) + rng.normal(scale=0.05, size=(8, 64))
    b = shared + np.array([0.0, 3.0] + [0.0] * 62) + rng.normal(scale=0.05, size=(8, 64))
    X = np.vstack([a, b])

    Z = _reduce_for_density(X, Config())
    assert Z.shape == (16, 10)            # projected, not the raw 64 dims

    def spread(M):                        # between-group gap over within-group
        ga, gb = M[:8], M[8:]
        within = (np.linalg.norm(ga - ga.mean(0), axis=1).mean()
                  + np.linalg.norm(gb - gb.mean(0), axis=1).mean()) / 2
        return np.linalg.norm(ga.mean(0) - gb.mean(0)) / within

    from sklearn.preprocessing import normalize
    assert spread(Z) > spread(normalize(X))


def test_kmeans_is_still_available_with_an_explicit_k():
    from bioleads.embeddings import cluster_terms

    terms, emb = _two_blobs()
    clusters = cluster_terms(terms, Config(cluster_method="kmeans", n_clusters=3),
                             embeddings=emb)

    assert len(clusters) == 3
    # KMeans assigns everything: no unclustered bucket, no term left out.
    assert not any(c.is_noise for c in clusters)
    assert sorted(t for c in clusters for t in c.terms) == sorted(terms)


def test_unassigned_terms_become_the_noise_bucket():
    # HDBSCAN's -1 label must survive as a bucket rather than vanish: the CSV,
    # the scatter and the graph coloring all expect every term back.
    import numpy as np
    from bioleads.embeddings import _group

    terms = ["a", "b", "c", "lonely", "stray"]
    X = np.eye(5)
    clusters = _group(terms, X, [1, 1, 1, -1, -1])

    assert [c.cluster_id for c in clusters] == [0, -1]      # bucket last
    real, noise = clusters
    assert real.terms == ["a", "b", "c"] and not real.is_noise
    assert noise.is_noise and noise.terms == ["lonely", "stray"]
    assert noise.centroid_term == ""       # leftovers have no representative
    assert term_to_cluster(clusters)["stray"] == -1


def test_clusters_are_numbered_largest_first():
    from bioleads.embeddings import cluster_terms
    import numpy as np

    rng = np.random.default_rng(1)
    big = np.array([1.0, 0.0]) + rng.normal(scale=0.01, size=(8, 2))
    small = np.array([0.0, 1.0]) + rng.normal(scale=0.01, size=(3, 2))
    terms = [f"big{i}" for i in range(8)] + [f"small{i}" for i in range(3)]
    clusters = [c for c in cluster_terms(terms, Config(),
                                         embeddings=np.vstack([big, small]))
                if not c.is_noise]

    assert [c.cluster_id for c in clusters] == list(range(len(clusters)))
    assert clusters[0].terms[0].startswith("big")


def test_unknown_cluster_method_is_rejected():
    from bioleads.embeddings import cluster_terms
    import numpy as np

    with pytest.raises(ValueError, match="cluster_method"):
        cluster_terms(["a", "b"], Config(cluster_method="dbscan"),
                      embeddings=np.eye(2))


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
        # HDBSCAN's leftovers: no centroid, and drawn as "unclustered".
        TermCluster(cluster_id=-1, terms=["odd"], centroid_term="",
                    is_noise=True),
    ]
    # six rows, one per flattened term, in cluster order
    rng = np.random.default_rng(0)
    emb = rng.normal(size=(6, 16))
    out = tmp_path / "term_clusters.html"
    path = write_cluster_scatter(clusters, str(out), embeddings=emb)

    assert path == str(out)
    assert out.exists()
    html = out.read_text()
    assert "trpv1" in html and "mitochondria" in html   # labels/hover baked in
    assert "unclustered" in html                         # leftovers named, not "-1"
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


def test_expansion_is_off_at_zero_rounds_for_either_strategy(monkeypatch):
    """`relevance` used to expand even at 0 rounds; both strategies now agree.

    That asymmetry is why the strategy default could not be flipped: making
    `relevance` the default would have put a round of network calls on every
    run, including someone's quick look at their own seed set.
    """
    from bioleads import expansion, pipeline as pipe

    called: list[str] = []
    monkeypatch.setattr(expansion, "relevance_guided_expand",
                        lambda *a, **k: called.append("relevance") or [])
    monkeypatch.setattr(pipe, "load_documents",
                        lambda **kw: (called.append(f"load:{kw['expand_rounds']}"),
                                      documents_from_texts(CORPUS))[1])

    cfg = _cfg()
    assert cfg.expand_strategy == "bfs"            # the default strategy
    assert cfg.expand_rounds == 0                  # pinned by _cfg, not the default
    run_pipeline(pmids="1", cfg=cfg, out_dir=None)
    assert called == ["load:0"], f"a plain run must not expand: {called}"


def test_relevance_expands_once_asked(monkeypatch):
    from bioleads import expansion, pipeline as pipe

    called: list[str] = []
    monkeypatch.setattr(expansion, "relevance_guided_expand",
                        lambda *a, **k: called.append("relevance") or [])
    monkeypatch.setattr(pipe, "load_documents",
                        lambda **kw: (called.append(f"load:{kw['expand_rounds']}"),
                                      documents_from_texts(CORPUS))[1])

    cfg = _cfg()
    cfg.expand_rounds = 1

    # bfs is the default, and expands inside load_documents
    run_pipeline(pmids="1", cfg=cfg, out_dir=None)
    assert called == ["load:1"], called

    # relevance, in contrast, is a gated pass over a load that did not expand
    called.clear()
    cfg.expand_strategy = "relevance"
    run_pipeline(pmids="1", cfg=cfg, out_dir=None)
    assert called == ["load:0", "relevance"], called


def test_relevance_guided_expand_gates_both_directions(monkeypatch):
    # The profile is the seeds alone, and BOTH directions are cut to top-K.
    # Stub the network; with no `embed` extra the scorer falls back to NER
    # term overlap.
    import bioleads.expansion as E

    seed_docs = [Document(doc_id="PMID:1",
                          text="trpv1 vasodilation calcium signaling pathways",
                          source="pubmed")]

    def fake_expand(seeds, *, rounds, link, source, max_records, email, api_key,
                    cache=None, cancel=None, progress=None):
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
                    cache=None, cancel=None, progress=None):
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
    assert document_pmids(docs) == ["111", "222"]


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


def _as_expanded(docs):
    """Mark a corpus as expansion-discovered.

    Seeds are exempt from the degree and paper-count filters, deliberately: a
    seed is in the picture because the search returned it. So a test about what
    a threshold *does* has to be run on non-seed nodes, or it is testing the
    exemption instead. The promise those controls make is now "every non-seed
    node you see clears the number", and that is what these check.
    """
    for d in docs:
        d.meta["expanded"] = True
    return docs


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
        assert os.path.exists(res.outputs["citation_network_3d"])


# --------------------------------------------------------------------------- #
# iCite cache: a repeat run should cost no network
# --------------------------------------------------------------------------- #
def _fake_icite(monkeypatch, calls, *, known=()):
    """Stand in for `requests`, recording each batch it is asked for."""
    import sys
    import types

    class Resp:
        def __init__(self, ids): self._ids = ids
        def raise_for_status(self): pass
        def json(self):
            return {"data": [{"pmid": int(i), "title": f"P{i}", "citation_count": 7}
                             for i in self._ids if not known or i in known]}

    def get(url, params=None, timeout=None):
        ids = params["pmids"].split(",")
        calls.append(ids)
        return Resp(ids)

    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=get))


def test_a_second_run_asks_icite_for_nothing(tmp_path, monkeypatch):
    """The whole point: the first run pays, the rest are free and work offline."""
    from bioleads.cache import JsonCache
    from bioleads.citations import fetch_icite

    calls = []
    _fake_icite(monkeypatch, calls)
    pmids = ["11", "22", "33"]

    first = fetch_icite(pmids, cache=JsonCache(path=str(tmp_path)))
    second = fetch_icite(pmids, cache=JsonCache(path=str(tmp_path)))

    assert len(calls) == 1, f"the second run hit the network: {calls}"
    assert second == first and sorted(second) == pmids


def test_only_the_papers_not_already_held_are_fetched(tmp_path, monkeypatch):
    """Cached per PMID, not per request, so a grown corpus reuses the old one."""
    from bioleads.cache import JsonCache
    from bioleads.citations import fetch_icite

    calls = []
    _fake_icite(monkeypatch, calls)
    fetch_icite(["11", "22"], cache=JsonCache(path=str(tmp_path)))

    out = fetch_icite(["11", "22", "33"], cache=JsonCache(path=str(tmp_path)))

    assert calls[-1] == ["33"], f"refetched papers it already had: {calls[-1]}"
    assert sorted(out) == ["11", "22", "33"]


def test_a_paper_icite_knows_nothing_about_is_not_asked_for_twice(tmp_path, monkeypatch):
    """A negative is a result too, and re-asking every run would waste the trip."""
    from bioleads.cache import JsonCache
    from bioleads.citations import fetch_icite

    calls = []
    _fake_icite(monkeypatch, calls, known={"11"})       # iCite has nothing for 22

    fetch_icite(["11", "22"], cache=JsonCache(path=str(tmp_path)))
    out = fetch_icite(["11", "22"], cache=JsonCache(path=str(tmp_path)))

    assert len(calls) == 1, f"re-asked for the missing paper: {calls}"
    assert sorted(out) == ["11"], "a blank record must not enter the corpus"


def test_records_expire_so_citation_counts_do_not_freeze(tmp_path, monkeypatch):
    """iCite's global count keeps growing; a permanent entry would pin it."""
    import time

    from bioleads.cache import JsonCache
    from bioleads.citations import fetch_icite

    calls = []
    _fake_icite(monkeypatch, calls)
    fetch_icite(["11"], cache=JsonCache(path=str(tmp_path), ttl_days=30))

    aged = JsonCache(path=str(tmp_path), ttl_days=30)
    later = time.time() + 31 * 86400
    monkeypatch.setattr("bioleads.cache.time.time", lambda: later)
    fetch_icite(["11"], cache=aged)

    assert len(calls) == 2, "an expired record was served anyway"
    assert aged.stale == 1


def test_an_unusable_cache_slows_a_run_down_but_never_fails_it(tmp_path, monkeypatch):
    """Best-effort by design: a read-only cache directory is not an error."""
    from bioleads.cache import JsonCache
    from bioleads.citations import fetch_icite

    calls = []
    _fake_icite(monkeypatch, calls)
    blocked = tmp_path / "nope"
    blocked.write_text("")              # a file where the directory should be

    out = fetch_icite(["11"], cache=JsonCache(path=str(blocked)))

    assert sorted(out) == ["11"], "the run should still produce its records"


def test_zero_days_turns_the_cache_off(tmp_path, monkeypatch):
    """The escape hatch, for when you want today's numbers whatever the cost."""
    from bioleads.citations import citation_cache

    assert citation_cache(Config(citation_cache_days=0)) is None
    assert citation_cache(Config(citation_cache_days=30)) is not None


def _fake_icite_links(monkeypatch, net, fail_first=False):
    """Stand in for `requests` on the expansion endpoint."""
    import sys
    import types

    state = {"failed": not fail_first}

    class Resp:
        def __init__(s, ids): s.ids = ids
        def raise_for_status(s): pass
        def json(s):
            return {"data": [{"pmid": int(i), "references": [int(i) * 10]}
                             for i in s.ids]}

    def get(url, params=None, timeout=None):
        ids = params["pmids"].split(",")
        net.append(ids)
        if not state["failed"]:
            state["failed"] = True
            raise RuntimeError("iCite is down")
        return Resp(ids)

    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=get))


def test_repeating_an_expansion_asks_for_nothing(tmp_path, monkeypatch):
    """The walk is deterministic, so a repeat replays entirely from disk."""
    from bioleads.cache import JsonCache
    from bioleads.sources import expand_pmids

    net = []
    _fake_icite_links(monkeypatch, net)
    kw = dict(rounds=2, link="references", source="icite", max_records=50)

    first = expand_pmids(["1", "2"], cache=JsonCache(path=str(tmp_path)), **kw)
    asked = len(net)
    second = expand_pmids(["1", "2"], cache=JsonCache(path=str(tmp_path)), **kw)

    assert len(net) == asked, f"the repeat hit the network: {net[asked:]}"
    assert second == first


def test_the_cache_cannot_change_what_expansion_returns(tmp_path, monkeypatch):
    """Caching a request must be invisible: same walk, same ids, same order."""
    from bioleads.cache import JsonCache
    from bioleads.sources import expand_pmids

    net = []
    _fake_icite_links(monkeypatch, net)
    kw = dict(rounds=2, link="references", source="icite", max_records=50)

    uncached = expand_pmids(["1", "2"], cache=None, **kw)
    cached = expand_pmids(["1", "2"], cache=JsonCache(path=str(tmp_path)), **kw)
    replayed = expand_pmids(["1", "2"], cache=JsonCache(path=str(tmp_path)), **kw)

    assert uncached == cached == replayed


def test_a_backend_failure_is_not_cached(tmp_path, monkeypatch):
    """A cached outage would turn one bad afternoon into a permanently empty walk."""
    import bioleads.sources as S
    from bioleads.cache import JsonCache
    from bioleads.sources import expand_pmids

    net = []
    _fake_icite_links(monkeypatch, net, fail_first=True)
    # 'all' is the tolerant source, but its other backend is a live NCBI call;
    # stub it so this test exercises the failure path and not the network.
    monkeypatch.setattr(S, "_elink_neighbors", lambda *a, **k: [])
    kw = dict(rounds=1, link="references", source="all", max_records=50)

    with pytest.warns(UserWarning):          # 'all' absorbs one backend failing
        broke = expand_pmids(["1"], cache=JsonCache(path=str(tmp_path)), **kw)
    recovered = expand_pmids(["1"], cache=JsonCache(path=str(tmp_path)), **kw)

    assert broke == ["1"], "the failed round should have added nothing"
    assert "10" in recovered, "the failure was cached and the walk stayed empty"


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
    docs = _as_expanded([Document(doc_id=f"PMID:{p}", text="x", source="pubmed",
                                  meta={"pmid": p}) for p in ("10", "11", "12")])
    monkeypatch.setattr(citations, "fetch_icite", lambda pmids, **kw: fake)
    g = build_author_citation_graph(docs, Config(min_author_degree=0))

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
    docs = _as_expanded(_citation_docs() + [
        Document(doc_id="PMID:4", text="unrelated", title="Unconnected",
                 source="pubmed", meta={"pmid": "4"})])
    monkeypatch.setattr(citations, "fetch_icite", lambda pmids, **kw: fake)

    kept_all = build_citation_graph(docs, Config(min_paper_degree=0))
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


def test_min_degree_settles_instead_of_pruning_once(monkeypatch):
    """5 → 6 → 7: 6 clears degree 2 only while 5 and 7 are still there.

    A single pass keeps 6 and shows it with one arrow, contradicting the
    threshold that drew it. Settling drops it too: in this graph there is no
    set of papers that all have two connections to each other.
    """
    fake = {
        "5": {"pmid": 5, "title": "A", "authors": "Ann A",
              "references": ["6"], "cited_by": []},
        "6": {"pmid": 6, "title": "B", "authors": "Ben B",
              "references": ["7"], "cited_by": ["5"]},
        "7": {"pmid": 7, "title": "C", "authors": "Cal C",
              "references": [], "cited_by": ["6"]},
    }
    docs = _as_expanded([Document(doc_id=f"PMID:{p}", text="t", title=p,
                                  source="pubmed", meta={"pmid": p})
                         for p in ("5", "6", "7")])
    monkeypatch.setattr(citations, "fetch_icite", lambda pmids, **kw: fake)

    g = build_citation_graph(docs, Config(min_paper_degree=2))
    assert set(g.nodes) == set()


def test_nothing_below_the_threshold_survives_the_filter(monkeypatch):
    """The promise the control makes, as it now stands: every node you see
    clears the number **unless it is a seed**.

    Seeds are shown whatever their degree, because they are what the search
    returned. Everything else has to earn its place. Checked on a graph dense
    enough to leave a core behind, so this is about survivors clearing the bar
    rather than about an empty result.
    """
    monkeypatch.setattr(citations, "fetch_icite", lambda pmids, **kw: _ICITE_FAKE)
    docs = _as_expanded(_citation_docs())

    for k in (1, 2, 3):
        g = build_citation_graph(docs, Config(min_paper_degree=k))
        under = {n: d for n, d in g.degree()
                 if d < k and not g.nodes[n].get("seed")}
        assert not under, f"min_paper_degree={k} left {under} in the graph"


def test_a_seed_is_the_only_thing_shown_below_the_threshold(monkeypatch):
    """The other half of the same promise: the exemption is for seeds only."""
    monkeypatch.setattr(citations, "fetch_icite", lambda pmids, **kw: _ICITE_FAKE)
    docs = _citation_docs()
    docs[0].meta["expanded"] = False        # a seed
    for d in docs[1:]:
        d.meta["expanded"] = True

    g = build_citation_graph(docs, Config(min_paper_degree=3))
    below = {n for n, d in g.degree() if d < 3}
    assert all(g.nodes[n].get("seed") for n in below)


def test_min_author_degree_drops_isolated_authors(monkeypatch):
    """The author graph takes the same threshold, on distinct partners."""
    fake = dict(_ICITE_FAKE)
    fake["4"] = {"pmid": 4, "title": "Unconnected", "year": 2021,
                 "journal": "PLoS One", "citation_count": 3, "authors": "Eve E",
                 "references": [], "cited_by": []}
    docs = _as_expanded(_citation_docs() + [
        Document(doc_id="PMID:4", text="unrelated", title="Unconnected",
                 source="pubmed", meta={"pmid": "4"})])
    monkeypatch.setattr(citations, "fetch_icite", lambda pmids, **kw: fake)

    assert "Eve E" in build_author_citation_graph(
        docs, Config(min_author_degree=0)).nodes
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
    docs = _as_expanded(_citation_docs() + [
        Document(doc_id="PMID:4", text="unrelated", title="Unconnected",
                 source="pubmed", meta={"pmid": "4"})])
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
    docs = _as_expanded(_citation_docs() + [
        Document(doc_id="PMID:4", text="unrelated", title="Unconnected",
                 source="pubmed", meta={"pmid": "4"})])
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
    # Track Config rather than a literal: these defaults have moved once and a
    # test that hard-codes them just has to be edited again.
    assert (args.min_paper_degree, args.min_author_degree) == (
        Config.min_paper_degree, Config.min_author_degree)
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


def test_pipeline_writes_author_paper_outputs(tmp_path, monkeypatch):
    """The paper-count view ships alongside the citation view."""
    monkeypatch.setattr(citations, "fetch_icite", lambda pmids, **kw: _ICITE_FAKE)
    cfg = _cfg()
    cfg.do_citation_network = True
    res = run_pipeline(documents=_citation_docs(), cfg=cfg, out_dir=str(tmp_path))

    assert os.path.exists(res.outputs["author_paper_ranking"])
    assert os.path.exists(res.outputs["author_paper_network"])
    # Ranked by corpus output, not by standing: papers descends.
    import csv
    with open(res.outputs["author_paper_ranking"]) as fh:
        papers = [int(row["papers"]) for row in csv.DictReader(fh)]
    assert papers == sorted(papers, reverse=True), \
        "the paper ranking must be ordered by papers, not citations"


def test_author_paper_view_keeps_the_prolific_not_the_cited(monkeypatch):
    """The display trim must follow the measure the view is about.

    Ranking by citations while sizing by papers drops exactly the authors this
    view exists to surface: a lab publishing steadily that nothing in the
    corpus happens to cite.
    """
    monkeypatch.setattr(citations, "fetch_icite", lambda pmids, **kw: _ICITE_FAKE)
    cfg = _cfg()
    cfg.max_graph_nodes = 1          # force the trim to choose

    by_papers = build_author_citation_graph(_citation_docs(), cfg, rank_by="papers")
    by_cites = build_author_citation_graph(_citation_docs(), cfg)

    assert by_papers.number_of_nodes() == 1
    kept = next(iter(by_papers.nodes(data=True)))[1]
    everyone = build_author_citation_graph(_citation_docs(), Config())
    most_papers = max(d.get("papers", 0) for _, d in everyone.nodes(data=True))
    assert kept["papers"] == most_papers, \
        "the papers view kept an author who is not the most published"
    # And the two views are free to disagree about who survives.
    assert by_cites.number_of_nodes() == 1


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
def test_pyvis_heading_not_duplicated(tmp_path, monkeypatch):
    pytest.importorskip("pyvis")
    from bioleads.citations import write_citation_html
    monkeypatch.setattr(citations, "fetch_icite", lambda pmids, **kw: _ICITE_FAKE)
    g = build_citation_graph(_citation_docs(), Config())
    path = write_citation_html(g, str(tmp_path / "citation_network.html"),
                               title="bioleads citations")

    # pyvis 0.3.2 doubles the <h1>. The duplicate is collapsed first and the
    # survivor is then replaced by the compact bar, so the page ends with no
    # <h1> at all and the title exactly once. **Both steps still matter**: skip
    # the collapse and the bar replaces only the first heading, leaving the
    # second one sitting there full size.
    with open(path, encoding="utf-8") as fh:
        html = fh.read()
    assert "<h1>bioleads citations</h1>" not in html
    assert html.count("bioleads citations") == 1
    assert 'id="bl-bar"' in html


# --------------------------------------------------------------------------- #
# 3D layout: isotropic shells growing out from the highest-degree node
# --------------------------------------------------------------------------- #
def _hub_and_spokes():
    """A hub, its 8 neighbours, and a tail three hops out."""
    g = nx.Graph()
    for i in range(8):
        g.add_edge("hub", f"n{i}")
    g.add_edge("n0", "far")          # hop 2
    g.add_edge("far", "further")     # hop 3
    return g


def test_layout_puts_the_highest_degree_node_at_the_centre():
    from bioleads.graph3d import _isotropic_layout

    pos = _isotropic_layout(_hub_and_spokes())

    assert pos["hub"] == (0.0, 0.0, 0.0)


def test_radius_ranks_nodes_by_degree():
    """Radius has to mean something, which is the point of not using springs.

    Here it means connectedness: the root at the centre, then each degree
    present in turn, out to the least connected nodes on the rim.
    """
    from bioleads.graph3d import _isotropic_layout

    g = _hub_and_spokes()                       # hub 8, n0 2, far 2, rest 1
    pos = _isotropic_layout(g)
    radius = {n: sum(c * c for c in p) ** 0.5 for n, p in pos.items()}

    assert radius["hub"] == 0.0
    assert radius["n0"] == pytest.approx(radius["far"]), "degree 2 is one shell"
    assert radius["n1"] == pytest.approx(radius["further"]), "degree 1 is one shell"
    assert radius["hub"] < radius["n0"] < radius["n1"]
    assert radius["n1"] == pytest.approx(1.0), "least connected reach the rim"


def test_each_shell_is_spread_over_the_whole_sphere():
    """Isotropic means no axis is favoured -- otherwise it is a disc, not a ball.

    Eight points pulled to one side would leave a mean vector near 1; spread
    over a sphere they cancel out.
    """
    from bioleads.graph3d import _isotropic_layout

    g = nx.Graph()
    for i in range(12):                          # one root, twelve degree-1 nodes
        g.add_edge("root", f"n{i}")
    pos = _isotropic_layout(g)
    shell = [pos[f"n{i}"] for i in range(12)]
    mean = [sum(p[axis] for p in shell) / len(shell) for axis in range(3)]

    assert sum(c * c for c in mean) ** 0.5 < 0.05, f"shell is lopsided: {mean}"


def _anisotropy(pos):
    """Ratio of the largest to smallest spread across any axis of the figure.

    1.0 is a ball; a large number means the layout has collapsed toward a plane
    or a line, which is exactly what "not isotropic" looks like on screen.
    """
    import numpy as np

    ev = np.linalg.eigvalsh(np.cov(np.array(list(pos.values())).T))
    return float(ev.max() / max(ev.min(), 1e-12))


def test_detached_pieces_do_not_flatten_the_figure():
    """A graph that is mostly small islands still has to render as a ball.

    Each island contributes shells of one node, and a single point on a shell
    lands on the equator unless the shell is tilted -- so without the tilt every
    island lands on one plane and the whole view is flat.
    """
    from bioleads.graph3d import _isotropic_layout

    g = nx.Graph()
    for i in range(10):
        g.add_edge(f"p{i}a", f"p{i}b")

    assert _anisotropy(_isotropic_layout(g)) < 2.0


def test_islands_do_not_shrink_the_graph_that_matters():
    """Detached pieces cannot push the connected core toward the centre.

    An earlier version parked each island further out than the last, so twenty
    of them squeezed the core into 6% of the figure. Ranking by degree bounds
    the radius by construction: an island's nodes are simply poorly connected,
    which puts them on the rim rather than beyond it.
    """
    from bioleads.graph3d import _isotropic_layout

    g = nx.Graph(nx.barabasi_albert_graph(60, 3, seed=2).edges())
    core = set(g.nodes)
    for i in range(20):
        g.add_edge(f"x{i}a", f"x{i}b")          # degree 1: outermost shell

    pos = _isotropic_layout(g)
    radius = {n: sum(c * c for c in p) ** 0.5 for n, p in pos.items()}

    assert max(radius[n] for n in core) < 1.0, "core pushed out to the rim"
    assert radius["x0a"] == pytest.approx(1.0), "islands are the least connected"
    assert _anisotropy(pos) < 2.0


def test_degree_counts_both_directions():
    """Counting only outgoing arrows would rank a much-cited paper as isolated.

    `cited` points *into* the hub and has no outgoing edge at all, so an
    out-degree ranking would give it 0 and bury it past everything. It has one
    connection, the same as `a`, and belongs on the same shell.
    """
    from bioleads.graph3d import _isotropic_layout

    g = nx.DiGraph()
    g.add_edge("hub", "a")
    g.add_edge("hub", "b")
    g.add_edge("cited", "hub")

    pos = _isotropic_layout(g)
    radius = {n: sum(c * c for c in p) ** 0.5 for n, p in pos.items()}

    assert pos["hub"] == (0.0, 0.0, 0.0), "3 connections: the root"
    assert radius["cited"] == pytest.approx(radius["a"]), "one connection each"


def test_the_layout_is_deterministic():
    """No simulation and no random start, so two runs place nodes identically."""
    from bioleads.graph3d import _isotropic_layout

    g = _hub_and_spokes()

    assert _isotropic_layout(g) == _isotropic_layout(g)


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


# ── every run gets its own folder ───────────────────────────────────────────────
# Before 0.3, out_dir was written to with exist_ok=True, so a second run silently
# overwrote the first and nothing on disk said which settings produced it.

def test_two_runs_into_one_root_do_not_overwrite_each_other(tmp_path):
    """REGRESSION: this is the whole reason the run folder exists."""
    docs = documents_from_texts(CORPUS)
    a = run_pipeline(documents=docs, cfg=_cfg(), out_dir=str(tmp_path))
    b = run_pipeline(documents=docs, cfg=_cfg(), out_dir=str(tmp_path))
    assert a.run_dir != b.run_dir
    assert Path(a.run_dir).exists() and Path(b.run_dir).exists()
    assert (Path(a.run_dir) / "ranked_terms.csv").exists()
    assert (Path(b.run_dir) / "ranked_terms.csv").exists()


def test_runs_in_the_same_second_still_get_separate_folders(tmp_path):
    """The folder name is second-resolution, so collisions are reachable."""
    from datetime import datetime
    from bioleads import runs
    when = datetime(2026, 9, 29, 14, 30, 5)
    made = {runs.new_run_dir(str(tmp_path), slug="x", when=when) for _ in range(3)}
    assert len(made) == 3


def test_the_run_folder_is_named_from_the_query(tmp_path):
    res = run_pipeline(documents=documents_from_texts(CORPUS), cfg=_cfg(),
                       out_dir=str(tmp_path), pubmed_query="TMEM184C AND autophagy")
    assert "tmem184c-and-autophagy" in os.path.basename(res.run_dir)


def test_run_name_overrides_the_generated_folder_name(tmp_path):
    res = run_pipeline(documents=documents_from_texts(CORPUS), cfg=_cfg(),
                       out_dir=str(tmp_path), run_name="Sweep 3")
    assert os.path.basename(res.run_dir).endswith("_sweep-3")


def test_unique_run_dir_off_restores_the_old_flat_layout(tmp_path):
    res = run_pipeline(documents=documents_from_texts(CORPUS), cfg=_cfg(),
                       out_dir=str(tmp_path), unique_run_dir=False)
    assert res.run_dir == str(tmp_path)
    assert (tmp_path / "ranked_terms.csv").exists()


def test_the_manifest_records_what_produced_the_run(tmp_path):
    import json
    res = run_pipeline(documents=documents_from_texts(CORPUS), cfg=_cfg(),
                       out_dir=str(tmp_path), pubmed_query="autophagy")
    man = json.loads((Path(res.run_dir) / "run.json").read_text())
    assert man["inputs"]["pubmed_query"] == "autophagy"
    assert man["results"]["documents"] == len(res.documents)
    assert man["config"]["min_doc_freq"] == _cfg().min_doc_freq
    # Output names are relative, so the folder survives being moved or sent on.
    assert man["outputs"]["ranked_terms"] == "ranked_terms.csv"


def test_the_manifest_never_writes_a_credential(tmp_path):
    """An api key in Config must not reach disk. Checked on the raw text."""
    import json
    cfg = _cfg()
    cfg.entrez_api_key = "SECRET_KEY_THAT_MUST_NOT_LEAK"
    res = run_pipeline(documents=documents_from_texts(CORPUS), cfg=cfg,
                       out_dir=str(tmp_path))
    raw = (Path(res.run_dir) / "run.json").read_text()
    assert "SECRET_KEY_THAT_MUST_NOT_LEAK" not in raw
    assert json.loads(raw)["config"]["entrez_api_key"] == "<redacted>"


def test_the_manifest_is_valid_json_despite_config_holding_a_set(tmp_path):
    """Config.stopwords is a set, which json cannot serialise directly."""
    import json
    cfg = _cfg()
    cfg.stopwords = {"zzz", "aaa"}
    res = run_pipeline(documents=documents_from_texts(CORPUS), cfg=cfg,
                       out_dir=str(tmp_path))
    man = json.loads((Path(res.run_dir) / "run.json").read_text())
    assert man["config"]["stopwords"] == ["aaa", "zzz"]


def test_no_latest_symlink_is_written(tmp_path):
    """Runs are identified by the timestamp in the folder name and nothing else."""
    docs = documents_from_texts(CORPUS)
    run_pipeline(documents=docs, cfg=_cfg(), out_dir=str(tmp_path))
    run_pipeline(documents=docs, cfg=_cfg(), out_dir=str(tmp_path))
    assert not (tmp_path / "latest").exists()
    assert not os.path.islink(str(tmp_path / "latest"))


def test_run_folders_sort_chronologically(tmp_path):
    """Plain-text sorting has to put the newest run last, since that is the
    only way to find it now."""
    docs = documents_from_texts(CORPUS)
    first = run_pipeline(documents=docs, cfg=_cfg(), out_dir=str(tmp_path))
    second = run_pipeline(documents=docs, cfg=_cfg(), out_dir=str(tmp_path))
    names = sorted(p.name for p in tmp_path.iterdir() if p.is_dir())
    assert names[-1] == os.path.basename(second.run_dir)
    assert os.path.basename(first.run_dir) in names


def test_a_stale_latest_symlink_from_an_older_version_is_removed(tmp_path):
    docs = documents_from_texts(CORPUS)
    first = run_pipeline(documents=docs, cfg=_cfg(), out_dir=str(tmp_path))
    link = tmp_path / "latest"
    try:
        os.symlink(os.path.basename(first.run_dir), link)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")
    run_pipeline(documents=docs, cfg=_cfg(), out_dir=str(tmp_path))
    assert not os.path.islink(str(link))


def test_a_real_directory_named_latest_is_left_alone(tmp_path):
    """Tidying up must never delete something a person put there."""
    (tmp_path / "latest").mkdir()
    (tmp_path / "latest" / "keep.txt").write_text("mine")
    run_pipeline(documents=documents_from_texts(CORPUS), cfg=_cfg(),
                 out_dir=str(tmp_path))
    assert (tmp_path / "latest" / "keep.txt").read_text() == "mine"


# ── query-term coloring ────────────────────────────────────────────────────────
# A PubMed hit need not contain the query's words: it can match on a MeSH term or
# on full text never fetched. And --expand adds papers that never went through
# the query at all. Coloring the papers that literally contain the terms cuts
# across both, which is the point.

@pytest.mark.parametrize("query, expected", [
    ("TMEM184C AND (autophagy OR lysosome)", ["TMEM184C", "autophagy", "lysosome"]),
    ('"tunneling nanotube" AND cancer', ["tunneling nanotube", "cancer"]),
    ('"tunneling nanotube"[tiab] AND cancer[mesh]', ["tunneling nanotube", "cancer"]),
    ("GPCR NOT olfactory", ["GPCR", "olfactory"]),
    ("cancer AND cancer", ["cancer"]),
    ("", []),
    (None, []),
])
def test_query_terms_are_pulled_out_of_a_pubmed_query(query, expected):
    from bioleads.querymatch import parse_query_terms
    assert parse_query_terms(query) == expected


@pytest.mark.parametrize("query, expected", [
    ("Isom DG[au] AND autophagy", ["autophagy"]),
    ('"Nature"[ta] AND spermine', ["spermine"]),
    ('"Isom D"[au]', []),
])
def test_a_field_tag_drops_the_whole_term_before_it(query, expected):
    """REGRESSION: splitting on whitespace first let `Isom DG[au]` leak `Isom`.

    The tag binds to the whole term, so the query has to be cut at the boolean
    operators before tags are read.
    """
    from bioleads.querymatch import parse_query_terms
    assert parse_query_terms(query) == expected


def test_an_untagged_multi_word_chunk_becomes_separate_words():
    """PubMed maps `firefighter cancer risk` term by term, it is not a phrase."""
    from bioleads.querymatch import parse_query_terms
    assert parse_query_terms("firefighter cancer risk") == [
        "firefighter", "cancer", "risk"]


def test_matching_is_literal_and_respects_word_boundaries():
    from bioleads.querymatch import matched_terms
    text = "Spermine transport and autophagic flux in TMEM184C-positive vesicles."
    assert matched_terms(text, ["TMEM184C"]) == ["TMEM184C"]
    assert matched_terms(text, ["spermine"]) == ["spermine"]      # case-insensitive
    assert matched_terms(text, ["autophagy"]) == []               # no stemming
    assert matched_terms(text, ["ras"]) == []                     # not inside a word
    assert matched_terms(text, ["spermine transport"]) == ["spermine transport"]


def test_a_trailing_star_is_pubmed_truncation():
    from bioleads.querymatch import matched_terms
    assert matched_terms("autophagic flux", ["autophag*"]) == ["autophag*"]
    assert matched_terms("autophagic flux", ["lysosom*"]) == []


def test_nodes_are_classified_all_partial_and_none(tmp_path):
    from bioleads.querymatch import annotate_citation_graph
    import networkx as nx
    docs = [
        Document(doc_id="1", title="TMEM184C drives autophagy",
                 text="lysosome biology", source="pubmed", meta={"pmid": "1"}),
        Document(doc_id="2", title="TMEM184C in vesicles", text="trafficking",
                 source="pubmed", meta={"pmid": "2"}),
        Document(doc_id="3", title="Unrelated kinase", text="signalling",
                 source="pubmed", meta={"pmid": "3"}),
        Document(doc_id="4", title="Pulled in", text="autophagy here",
                 source="pubmed", meta={"pmid": "4", "expanded": True}),
    ]
    g = nx.DiGraph()
    for d in docs:
        g.add_node(f"PMID:{d.meta['pmid']}", pmid=d.meta["pmid"])
    terms = annotate_citation_graph(g, docs, "TMEM184C AND (autophagy OR lysosome)")
    assert terms == ["TMEM184C", "autophagy", "lysosome"]
    assert g.nodes["PMID:1"]["query_match"] == "all"
    assert g.nodes["PMID:2"]["query_match"] == "partial"
    assert g.nodes["PMID:3"]["query_match"] == "none"
    # An expansion-added paper can still contain a term, which is worth seeing.
    assert g.nodes["PMID:4"]["query_match"] == "partial"
    assert g.nodes["PMID:4"]["expanded"] is True
    assert g.nodes["PMID:3"]["expanded"] is False


def test_annotations_are_graphml_safe():
    """GraphML cannot store a list or a None, so the attrs must be str/int/bool."""
    from bioleads.querymatch import annotate_citation_graph
    import networkx as nx, io as _io
    docs = [Document(doc_id="1", title="TMEM184C", text="autophagy",
                     source="pubmed", meta={"pmid": "1"})]
    g = nx.DiGraph()
    g.add_node("PMID:1", pmid="1")
    annotate_citation_graph(g, docs, "TMEM184C AND autophagy")
    buf = _io.BytesIO()
    nx.write_graphml(g, buf)          # raises if an attr type is unsupported
    assert b"query_match" in buf.getvalue()


def test_a_text_run_writes_the_matching_subnetwork(tmp_path):
    """The matches-only network is written beside the full one, not instead of it.

    The query is `review`, which is a word the fixture corpus actually contains.
    An earlier version of this test used a term no document carried, so it
    passed while the file was never written, which is no test at all.
    """
    res = run_pipeline(documents=_citation_docs(), cfg=_cfg(),
                       out_dir=str(tmp_path), pubmed_query="review")
    if res.citation_graph is None or not res.citation_graph.number_of_nodes():
        pytest.skip("no citation graph in this environment")

    from bioleads.querymatch import matching_subgraph
    sub = matching_subgraph(res.citation_graph)
    assert sub.number_of_nodes() > 0, "fixture no longer matches the query"

    path = res.outputs["citation_network_matches"]
    assert os.path.exists(path)
    assert os.path.basename(path).startswith("citation_network_matches")
    # It must not replace the full network.
    assert os.path.exists(res.outputs["citation_network"])
    assert path != res.outputs["citation_network"]
    # And it must be a strict subset, not a copy of everything.
    assert sub.number_of_nodes() <= res.citation_graph.number_of_nodes()


def test_no_matching_subnetwork_when_nothing_contains_the_terms(tmp_path):
    """A query no paper carries writes no file rather than an empty network."""
    res = run_pipeline(documents=_citation_docs(), cfg=_cfg(),
                       out_dir=str(tmp_path), pubmed_query="zzzznotaword")
    assert "citation_network_matches" not in res.outputs
    assert not (tmp_path / "citation_network_matches.html").exists()


def test_a_non_text_run_writes_no_matching_subnetwork(tmp_path):
    """No query means every node is `unknown`, so there is nothing to subset."""
    res = run_pipeline(documents=_citation_docs(), cfg=_cfg(), out_dir=str(tmp_path))
    assert "citation_network_matches" not in res.outputs


def test_a_non_text_run_leaves_the_graph_uncolored(tmp_path):
    """--pmids has no query, so nothing should be marked `none` as if it failed."""
    res = run_pipeline(documents=_citation_docs(), cfg=_cfg(), out_dir=str(tmp_path))
    if res.citation_graph is None or not res.citation_graph.number_of_nodes():
        pytest.skip("no citation graph in this environment")
    assert not any("query_match" in d
                   for _, d in res.citation_graph.nodes(data=True))


# ── an empty PubMed search explains itself ──────────────────────────────────────
# "No documents loaded. Check your inputs." is equally true of a typo, a dead
# network and a query that simply matches nothing. PubMed hands back its own
# translation of the query, which is the thing that tells those apart.

class _FakeHandle:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_entrez(payload):
    """An Entrez stand-in whose esearch returns `payload`."""
    class _E:
        @staticmethod
        def esearch(**kw):
            return _FakeHandle(payload)

        @staticmethod
        def read(handle):
            return handle.payload
    return lambda email, api_key: (_E, None)


_EMPTY_SEARCH = {
    "Count": "0",
    "IdList": [],
    "QueryTranslation": '"TMEM184C"[All Fields] AND "TM184C"[All Fields]',
    "WarningList": {"OutputMessage": ["No items found."]},
}


def test_fetch_pubmed_reports_what_the_search_did(monkeypatch):
    import bioleads.sources as S
    monkeypatch.setattr(S, "_entrez", _fake_entrez(_EMPTY_SEARCH))
    report: dict = {}
    docs = S.fetch_pubmed("TMEM184C AND TM184C", report=report)

    assert docs == []
    assert report["count"] == 0
    assert report["translation"].startswith('"TMEM184C"[All Fields] AND')
    assert report["warnings"] == ["OutputMessage: No items found."]


def test_the_no_documents_error_names_the_translation(monkeypatch):
    """The regression this exists for: an AND of two terms no paper shares."""
    import bioleads.sources as S
    monkeypatch.setattr(S, "_entrez", _fake_entrez(_EMPTY_SEARCH))

    with pytest.raises(ValueError) as exc:
        run_pipeline(pubmed_query="TMEM184C AND TM184C", cfg=_cfg())

    msg = str(exc.value)
    assert "PubMed returned 0 record(s)" in msg
    assert '"TMEM184C"[All Fields] AND "TM184C"[All Fields]' in msg
    assert "No items found." in msg


def test_a_non_pubmed_run_keeps_the_plain_message():
    """Nothing to say about PubMed when PubMed was never asked."""
    with pytest.raises(ValueError) as exc:
        run_pipeline(texts=[], cfg=_cfg())
    assert str(exc.value) == "No documents loaded. Check your inputs."


def test_describe_pubmed_search_handles_a_missing_report():
    from bioleads.sources import describe_pubmed_search
    assert describe_pubmed_search(None) == ""
    assert describe_pubmed_search({}) == ""


# ── the display trim keeps seeds ───────────────────────────────────────────────
# Ranking by citations drops exactly the paper a search was about, whether it is
# thinly cited or simply recent. This happened to a real TM184C run: 910
# documents, and the seed was not among the 150 nodes drawn.

def _trim_graph(n_seeds, n_others):
    import networkx as nx
    g = nx.DiGraph()
    for i in range(n_seeds):
        g.add_node(f"seed{i}", in_corpus_citations=0, global_citations=1, seed=True)
    for i in range(n_others):
        g.add_node(f"old{i}", in_corpus_citations=50 + i, global_citations=900,
                   seed=False)
    return g


def _trim(g, cap):
    from bioleads.citations import _trim_to_top
    return _trim_to_top(g, cap, "paper", lambda m: None,
                        key=lambda d: (d.get("in_corpus_citations", 0),
                                       d.get("global_citations") or 0))


def test_an_uncited_seed_survives_the_trim():
    kept = _trim(_trim_graph(1, 10), 5)
    assert "seed0" in kept
    assert kept.number_of_nodes() == 5


def test_remaining_slots_go_to_the_highest_ranked_non_seeds():
    kept = _trim(_trim_graph(1, 10), 5)
    assert sorted(n for n in kept if n != "seed0") == ["old6", "old7", "old8", "old9"]


def test_the_cap_still_wins_when_seeds_alone_exceed_it():
    """A picture of 900 nodes is not a picture. The cap holds and the message says so."""
    kept = _trim(_trim_graph(8, 0), 3)
    assert kept.number_of_nodes() == 3


def test_a_graph_with_no_seed_attribute_trims_exactly_as_before():
    import networkx as nx
    g = nx.DiGraph()
    for i in range(6):
        g.add_node(f"n{i}", in_corpus_citations=i)
    kept = _trim(g, 2)
    assert sorted(kept.nodes()) == ["n4", "n5"]


def test_nothing_is_trimmed_below_the_cap():
    g = _trim_graph(1, 2)
    assert _trim(g, 10).number_of_nodes() == 3


def test_seeds_are_marked_on_the_citation_graph(monkeypatch):
    """The trim can only protect seeds if the graph says which nodes are seeds."""
    monkeypatch.setattr(citations, "fetch_icite", lambda pmids, **kw: _ICITE_FAKE)
    docs = _citation_docs()
    docs[2].meta["expanded"] = True
    g = build_citation_graph(docs, Config(min_paper_degree=0))
    if not g.number_of_nodes():
        pytest.skip("no citation graph in this environment")
    assert g.nodes["PMID:1"]["seed"] is True
    assert g.nodes["PMID:3"]["seed"] is False


# ── seeds survive every filter, not just the display trim ──────────────────────
# A seed is in the picture because it is what was asked for. A seed with few
# neighbours is usually a lightly cited paper and sometimes a new one, and
# neither is a reason to hide it, so any threshold above zero would remove
# exactly the thing the run was about.

def _degree_graph():
    import networkx as nx
    g = nx.DiGraph()
    g.add_node("seed", seed=True, papers=1)            # degree 0
    g.add_node("lonely", seed=False, papers=1)         # degree 0, not a seed
    for i in range(4):
        g.add_node(f"p{i}", seed=False, papers=9)
    g.add_edges_from([("p0", "p1"), ("p1", "p2"), ("p2", "p0"),
                      ("p0", "p2"), ("p1", "p3")])
    return g


def test_a_seed_survives_min_degree():
    from bioleads.citations import _prune_by_degree
    kept = _prune_by_degree(_degree_graph(), 2, "paper", lambda m: None)
    assert "seed" in kept


def test_a_non_seed_of_the_same_degree_is_still_dropped():
    """Exempting seeds must not quietly disable the control."""
    from bioleads.citations import _prune_by_degree
    kept = _prune_by_degree(_degree_graph(), 2, "paper", lambda m: None)
    assert "lonely" not in kept


def test_the_log_says_a_seed_was_spared():
    from bioleads.citations import _prune_by_degree
    lines = []
    _prune_by_degree(_degree_graph(), 2, "paper", lines.append)
    assert any("seed(s) kept below the threshold" in l for l in lines)


def test_seeds_still_count_toward_their_neighbours_degree():
    """Exempting a seed must not inflate anyone else, and must not rescue a
    neighbour that only reaches the threshold through it."""
    import networkx as nx
    from bioleads.citations import _prune_by_degree
    g = nx.DiGraph()
    g.add_node("seed", seed=True)
    g.add_node("friend", seed=False)
    g.add_edge("seed", "friend")
    kept = _prune_by_degree(g, 2, "paper", lambda m: None)
    assert "seed" in kept
    assert "friend" not in kept


def test_min_author_papers_spares_seed_authors(monkeypatch):
    monkeypatch.setattr(citations, "fetch_icite", lambda pmids, **kw: _ICITE_FAKE)
    cfg = Config()
    cfg.min_author_papers = 5          # no author in a tiny corpus clears this
    g = build_author_citation_graph(_citation_docs(), cfg, rank_by="papers")
    if g is None:
        pytest.skip("no author graph in this environment")
    # Every author here is a seed author, so the filter must keep them all.
    assert all(d.get("seed") for _, d in g.nodes(data=True))


# ── the pyvis fallback has to actually fall back ───────────────────────────────
# On a core-only install — the one the conda recipe builds — pyvis is absent and
# the writers drop to GraphML. That path used to raise twice: nx.write_graphml is
# bound to the lxml implementation and imports lxml when called, and the node
# attributes carry None for an unknown citation count.

def _graph_with_nulls():
    import networkx as nx
    g = nx.DiGraph()
    g.add_node("PMID:1", pmid="1", in_corpus_citations=1,
               global_citations=None, title="A", seed=True)
    g.add_node("PMID:2", pmid="2", in_corpus_citations=0,
               global_citations=7, title="B", seed=False)
    g.add_edge("PMID:1", "PMID:2")
    g.graph["paper_senior"] = {"1": "Ann A"}      # a dict GraphML cannot store
    return g


def test_graphml_write_needs_no_lxml(tmp_path, monkeypatch):
    import networkx as nx
    from bioleads.citations import write_graphml
    # Make the lxml-backed writer unusable, the way a core-only install does.
    monkeypatch.setattr(nx, "write_graphml",
                        lambda *a, **k: (_ for _ in ()).throw(
                            ModuleNotFoundError("No module named 'lxml'")))
    out = write_graphml(_graph_with_nulls(), str(tmp_path / "g.graphml"))
    assert os.path.exists(out)
    assert "graphml" in open(out, encoding="utf-8").read()


def test_graphml_drops_none_attributes_rather_than_zeroing_them():
    """An absent attribute means "not known"; 0 would assert a measurement."""
    from bioleads.citations import _graphml_safe
    h = _graphml_safe(_graph_with_nulls())
    assert "global_citations" not in h.nodes["PMID:1"]
    assert h.nodes["PMID:2"]["global_citations"] == 7
    assert h.graph == {}


def test_the_citation_writer_degrades_to_graphml_without_pyvis(tmp_path, monkeypatch):
    """The whole point of the fallback: no pyvis must not mean no output."""
    import builtins
    from bioleads.citations import write_citation_html
    real_import = builtins.__import__

    def no_pyvis(name, *a, **k):
        if name.startswith("pyvis"):
            raise ImportError("No module named 'pyvis'")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_pyvis)
    out = write_citation_html(_graph_with_nulls(), str(tmp_path / "net.html"))
    assert out.endswith(".graphml")
    assert os.path.exists(out)


# ── the physics switch ─────────────────────────────────────────────────────────
# Physics starts on load, because the browser layout is what makes these graphs
# readable: a server-side spring layout collapsed a 150-node network onto a
# diagonal line of overlapping nodes. What it needed was control, not removal.

def _written_network(tmp_path, n=8):
    import networkx as nx
    from bioleads.citations import write_citation_html
    g = nx.DiGraph()
    for i in range(n):
        g.add_node(f"PMID:{i}", pmid=str(i), in_corpus_citations=i,
                   title=f"P{i}", global_citations=i)
    g.add_edges_from([(f"PMID:{i}", f"PMID:{(i + 1) % n}") for i in range(n)])
    path = write_citation_html(g, str(tmp_path / "net.html"))
    if path.endswith(".graphml"):
        pytest.skip("pyvis not installed; no HTML to inspect")
    return open(path, encoding="utf-8").read()


def test_physics_starts_on_load(tmp_path):
    html = _written_network(tmp_path)
    assert '"enabled": true' in html.split('"physics"')[1][:120]
    assert '"solver": "forceAtlas2Based"' in html


def test_nodes_carry_no_precomputed_coordinates(tmp_path):
    """The server-side layout is gone. vis.js places the nodes."""
    import json
    import re
    html = _written_network(tmp_path)
    nodes = json.loads(re.search(r"nodes = new vis.DataSet\((\[.*?\])\);",
                                 html, re.S).group(1))
    assert nodes and not any("x" in n or "y" in n for n in nodes)


def test_the_page_has_a_physics_switch(tmp_path):
    html = _written_network(tmp_path)
    assert 'id="bl-physics-toggle"' in html
    assert "Pause layout" in html


def test_the_switch_waits_for_the_network_object(tmp_path):
    """`network` is assigned inside drawGraph(); assuming it exists is how the
    old automatic freeze came to look like it worked when it did not."""
    html = _written_network(tmp_path)
    assert 'typeof network !== "undefined"' in html
    assert "setTimeout(wait, 50)" in html


def test_physics_also_stops_on_its_own(tmp_path):
    html = _written_network(tmp_path)
    assert "stabilizationIterationsDone" in html


def test_the_control_is_injected_once(tmp_path):
    html = _written_network(tmp_path)
    assert html.count('id="bl-physics"') == 1


def test_the_tooltip_waits_for_the_cursor_to_stop(tmp_path):
    """vis.js defaults to 300 ms, which fires while the cursor is still moving;
    on a dense graph the tooltips flicker up one after another."""
    from bioleads.citations import TOOLTIP_DELAY_MS
    html = _written_network(tmp_path)
    assert TOOLTIP_DELAY_MS >= 500
    assert f"tooltipDelay: {TOOLTIP_DELAY_MS}" in html
    assert "__TOOLTIP_DELAY__" not in html      # the placeholder was substituted


# ── the guided tour ────────────────────────────────────────────────────────────
# Zoom to the most connected node and say what it is, then step through the
# next. Degree, not size: a node is large here because it was cited often, but
# it is connected because it touches much of the corpus, and a tour of a network
# should follow the second.

def test_the_tour_visits_the_most_connected_node_first():
    import networkx as nx
    from bioleads.citations import tour_stops
    g = nx.DiGraph()
    for i in range(6):
        g.add_node(f"PMID:{i}", pmid=str(i), in_corpus_citations=0, title=f"P{i}")
    # PMID:5 is the hub; PMID:0 is the most *cited* but barely connected.
    g.nodes["PMID:0"]["in_corpus_citations"] = 99
    g.add_edges_from([("PMID:5", f"PMID:{j}") for j in (1, 2, 3, 4)])
    stops = tour_stops(g, 2)
    assert stops[0]["id"] == "PMID:5"
    assert stops[0]["degree"] == 4


def test_tour_stops_carry_the_same_text_as_the_hover():
    """The tour and the tooltip must not tell different stories about a node."""
    import networkx as nx
    from bioleads.citations import tour_stops, _citation_tip_lines
    g = nx.DiGraph()
    g.add_node("PMID:1", pmid="1", title="A paper", year="2020", journal="J",
               in_corpus_citations=2, global_citations=9)
    g.add_node("PMID:2", pmid="2", title="B", in_corpus_citations=0)
    g.add_edge("PMID:1", "PMID:2")
    stop = tour_stops(g, 1)[0]
    assert stop["info"] == _citation_tip_lines("PMID:1", g.nodes["PMID:1"])


def test_an_empty_graph_has_no_tour():
    import networkx as nx
    from bioleads.citations import tour_stops
    assert tour_stops(nx.DiGraph()) == []


def test_the_page_carries_the_tour_controls_and_its_stops(tmp_path):
    import json
    import re
    html = _written_network(tmp_path)
    for control in ("bl-tour-play", "bl-tour-next", "bl-tour-reset"):
        assert control in html
    stops = json.loads(re.search(r"var STOPS = (\[.*?\]);", html, re.S).group(1))
    assert stops and stops[0]["degree"] >= stops[-1]["degree"]
    assert "__TOUR_STOPS__" not in html          # placeholder substituted


def test_the_tour_turns_physics_off_before_flying(tmp_path):
    """The camera cannot chase a node that is still being simulated."""
    html = _written_network(tmp_path)
    assert "physics: {enabled: false}" in html
    assert "net.focus(s.id" in html


def test_a_tour_stop_identifies_the_paper_and_stops_there():
    """The panel is read at a glance while the camera is moving, so it carries
    what identifies a paper and nothing else. The counts and the match detail
    stay on the hover."""
    import networkx as nx
    from bioleads.citations import tour_stops
    g = nx.DiGraph()
    g.add_node("PMID:1", pmid="1", title="A paper", year="2026", journal="Nature",
               authors="Roy S \u2026 Kornberg TB (9 authors)",
               in_corpus_citations=4, global_citations=1, source="pubmed",
               url="https://pubmed.ncbi.nlm.nih.gov/1/", seed=True,
               query_match="all", query_terms_matched="TM184C")
    g.add_node("PMID:2", pmid="2", title="B", in_corpus_citations=0)
    g.add_edge("PMID:1", "PMID:2")
    labels = [r[0] for r in tour_stops(g, 1)[0]["record"]]
    assert labels == ["Title", "Authors", "PMID", "Year", "Journal"]


def test_the_record_skips_empty_fields():
    from bioleads.citations import node_record
    rows = dict(node_record("n", {"pmid": "1", "journal": "", "year": None,
                                  "title": "T"}))
    assert "Journal" not in rows and "Year" not in rows   # a blank row says nothing
    assert rows["Title"] == "T"


def test_the_senior_author_survives_abbreviation():
    """The last name in a biomedical byline is the lab, and a plain truncation
    throws it away first."""
    from bioleads.citations import abbreviate_authors
    out = abbreviate_authors(["Roy S", "Huang H", "Liu S", "Kornberg TB"])
    assert out.startswith("Roy S")
    assert "Kornberg TB" in out
    assert "(4 authors)" in out
    assert abbreviate_authors(["Roy S", "Kornberg TB"]) == "Roy S, Kornberg TB"
    assert abbreviate_authors([]) == ""


def test_a_node_with_nothing_to_show_still_gets_a_row():
    from bioleads.citations import node_record
    assert node_record("lonely", {}) == [["Node", "lonely"]]


def test_the_tour_is_paced_to_be_read(tmp_path):
    """It flew in 1.4 s and moved on after 4.2 s, which is long enough to see
    that something happened and not long enough to read it."""
    from bioleads.citations import TOUR_FLIGHT_MS, TOUR_HOLD_MS, TOUR_DWELL_MS
    # The camera is slow enough to follow, the wait after it lands is short.
    # Keeping them separate is what lets both be true at once: when the two were
    # one number, slowing the zoom ate the reading time.
    assert TOUR_FLIGHT_MS >= 3500
    assert TOUR_HOLD_MS <= 4000
    assert TOUR_DWELL_MS == TOUR_FLIGHT_MS + TOUR_HOLD_MS
    from bioleads.citations import TOUR_FLIGHT_2D_MS, TOUR_DWELL_2D_MS
    # 2D is slower than 3D for the same distance: vis.js `focus` changes zoom
    # as well as position, and the scale change is what reads as speed.
    assert TOUR_FLIGHT_2D_MS > TOUR_FLIGHT_MS
    html = _written_network(tmp_path)
    assert f"duration: {TOUR_FLIGHT_2D_MS}" in html
    assert f"setTimeout(step, {TOUR_DWELL_2D_MS})" in html
    assert not any(x in html for x in ("__FLIGHT__", "__DWELL__", "__ZOOM__"))


def test_the_page_says_how_to_record_it(tmp_path):
    """The help belongs where someone is when they want it, not in a document
    they would have to go and find."""
    html = _written_network(tmp_path)
    assert 'id="bl-tour-help"' in html
    assert "__RECORD_HELP__" not in html
    for system in ("Shift-Command-5", "Windows-Alt-R", "Ctrl-Alt-Shift-R", "OBS"):
        assert system in html


def test_the_quoted_tour_runtime_follows_the_pacing():
    """A number in help text that does not track the constant is a lie waiting
    to happen."""
    from bioleads.citations import RECORD_HELP, TOUR_DWELL_MS, TOUR_STOPS
    total = TOUR_STOPS * TOUR_DWELL_MS // 1000
    assert f"{total // 60} min {total % 60} s" in RECORD_HELP


# ── the settle watchdog, and the 3D tour ───────────────────────────────────────

def test_the_layout_stops_even_if_the_event_never_arrives(tmp_path):
    """vis.js does not always emit stabilizationIterationsDone on a large
    graph, and until physics stops the main thread is busy enough that every
    button feels broken. The watchdog is what guarantees a clickable page."""
    from bioleads.citations import SETTLE_LIMIT_MS
    html = _written_network(tmp_path)
    assert f"}}, {SETTLE_LIMIT_MS});" in html
    assert 'net.on("stabilized"' in html        # a second event, not just one
    assert "__SETTLE_LIMIT__" not in html


def _written_3d(tmp_path, n=7):
    import networkx as nx
    from bioleads.citations import write_citation_html_3d
    g = nx.DiGraph()
    for i in range(n):
        g.add_node(f"PMID:{i}", pmid=str(i), in_corpus_citations=i,
                   title=f"P{i}", global_citations=i)
    g.add_edges_from([(f"PMID:{n-1}", f"PMID:{j}") for j in range(3)])
    path = write_citation_html_3d(g, str(tmp_path / "n3.html"))
    if not path:
        pytest.skip("plotly not installed")
    return open(path, encoding="utf-8").read()


def test_the_3d_view_has_the_tour_too(tmp_path):
    """It had none of the controls: no way to reach the most connected node and
    nowhere to read it."""
    html = _written_3d(tmp_path)
    for control in ("bl3-play", "bl3-next", "bl3-reset", "bl3-help"):
        assert control in html
    assert not any(x in html for x in ("__STOPS__", "__FLIGHT__", "__HELP__"))


def test_the_3d_stops_carry_coordinates_and_the_same_record(tmp_path):
    import json
    import re
    html = _written_3d(tmp_path)
    stops = json.loads(re.search(r"var STOPS = (\[.*?\]), FLIGHT", html, re.S).group(1))
    assert stops and len(stops[0]["xyz"]) == 3
    assert stops[0]["record"] and stops[0]["degree"] >= stops[-1]["degree"]


def test_the_3d_camera_is_tweened_not_snapped(tmp_path):
    """Plotly has no camera tween of its own, and jumping is a slideshow."""
    html = _written_3d(tmp_path)
    assert "requestAnimationFrame(frame)" in html
    assert "Plotly.relayout" in html


# ── the injected scripts must actually parse ───────────────────────────────────
# A substring test cannot see a syntax error. The 2D controls shipped broken for
# two rounds because `"<div class=\"x\">"` collapsed to `"<div class="x">"` in a
# plain triple-quoted Python string, the whole IIFE failed to parse, and no
# handler was ever attached — so the buttons did nothing and physics never
# stopped. Every assertion about the page still passed.

def _node_check(js: str, tmp_path, name):
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available to parse the injected script")
    f = tmp_path / name
    f.write_text(js, encoding="utf-8")
    r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, f"injected script does not parse:\n{r.stderr}"


def _injected(html, marker):
    """The body of the <script> block containing `marker`.

    Cutting at the first ">" after the marker lands inside the code — the 3D
    script contains `el.data.length > 2` — so the whole script tag has to be
    found first.
    """
    import re
    for m in re.finditer(r"<script[^>]*>(.*?)</script>", html, re.S):
        if marker in m.group(1):
            return m.group(1)
    raise AssertionError(f"no injected script containing {marker!r}")


def test_the_2d_injected_script_parses(tmp_path):
    _node_check(_injected(_written_network(tmp_path), "bl-tour-play"),
                tmp_path, "a.js")


def test_the_3d_injected_script_parses(tmp_path):
    _node_check(_injected(_written_3d(tmp_path), "bl3-play"), tmp_path, "b.js")


def test_the_visited_node_is_marked_in_2d(tmp_path):
    """Selection alone is too quiet to find on a crowded graph."""
    from bioleads.citations import TOUR_HIGHLIGHT
    html = _written_network(tmp_path)
    assert TOUR_HIGHLIGHT in html
    assert "function light(" in html and "function unlight(" in html
    assert "__HILITE__" not in html


def test_the_visited_node_is_marked_in_3d(tmp_path):
    from bioleads.citations import TOUR_HIGHLIGHT
    html = _written_3d(tmp_path)
    assert TOUR_HIGHLIGHT in html
    assert "Plotly.addTraces" in html and "Plotly.deleteTraces" in html
    assert "__HILITE__" not in html


def test_the_match_colors_are_not_red_green():
    """Green against amber is the one pair a red-green color blind reader
    cannot separate, and roughly one man in twelve is."""
    from bioleads.querymatch import MATCH_COLORS
    from bioleads.citations import TOUR_HIGHLIGHT
    assert MATCH_COLORS["all"] == "#0072B2"       # Okabe-Ito blue
    assert MATCH_COLORS["partial"] == "#E69F00"   # Okabe-Ito orange
    # Blue and orange differ in lightness too, so they survive greyscale.
    def lum(h):
        r, g, b = (int(h[i:i + 2], 16) for i in (1, 3, 5))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    assert abs(lum(MATCH_COLORS["all"]) - lum(MATCH_COLORS["partial"])) > 40
    assert TOUR_HIGHLIGHT not in MATCH_COLORS.values()


def test_the_3d_scene_opens_larger_than_the_default(tmp_path):
    """The page must not open at aspectratio 1.

    gl3d clamps the camera's distance and Plotly's default eye of 1.25 per axis
    is already past that clamp, so the scene cannot be opened closer by moving
    the camera: the graph just sits small in the middle of a lot of white.
    Growing the aspect ratio is the only lever, and it is the same one the tour
    uses, so the ratio is equal on all three axes and the scene stays a cube in
    shape.
    """
    import re
    from bioleads.graph3d import TOUR_HOME_ZOOM, TOUR_ZOOM_3D

    html = _written_3d(tmp_path)
    # Plotly serialises without spaces, and the page embeds a template scene
    # too, so match the key/value pair rather than a formatted string.
    assert re.search(r'"aspectmode":\s*"manual"', html)
    m = re.search(r'"aspectratio":\s*\{"x":([0-9.]+),"y":([0-9.]+),"z":([0-9.]+)\}',
                  html)
    assert m, "no explicit aspect ratio"
    x, y, z = (float(v) for v in m.groups())
    assert x == y == z, "an uneven ratio would distort the layout"
    assert x == TOUR_HOME_ZOOM > 1, "the scene opens at the default size"
    assert TOUR_ZOOM_3D > TOUR_HOME_ZOOM, "a stop does not zoom in"


def test_3d_tour_zooms_without_hiding_any_of_the_network():
    """Zooming must not delete nodes, and the two coordinate spaces must agree.

    Three mechanisms were tried here. Shortening `scene.camera.eye` does
    nothing at all, because gl3d clamps the camera's distance. Narrowing the
    axis ranges does zoom, but Plotly drops whatever falls outside a range, so
    it deleted most of the network on the way in -- and 2D, which this is meant
    to behave like, never hides anything. Scaling `scene.aspectratio` is the
    one that zooms while leaving every node in the figure.

    `cam` (normalised, for the camera) and `xyz` (data, for the ring and the
    annotation) are separate because Plotly demands it; feeding a trace the
    camera form once put a loose marker in the scene attached to no node.
    """
    pytest.importorskip("plotly")
    import json
    import re
    import tempfile

    import networkx as nx

    from bioleads import citations, graph3d

    g = nx.DiGraph()
    for i in range(6):
        g.add_node(f"PMID:{i}", pmid=str(i), title=f"P{i}", in_corpus_citations=i)
    g.add_edges_from([("PMID:5", f"PMID:{j}") for j in range(3)])

    with tempfile.TemporaryDirectory() as d:
        out = graph3d.write_graph_3d(g, os.path.join(d, "t.html"),
                                     size_attr="in_corpus_citations",
                                     stops=citations.tour_stops(g, 3))
        html = open(out, encoding="utf-8").read()

    # The injected tour alone. Plotly's own bundle is in this file too, and it
    # mentions everything, so asserting against the whole page proves nothing.
    tour = [m.group(1) for m in
            re.finditer(r"<script[^>]*>(.*?)</script>", html, re.S)
            if "bl3-play" in m.group(1)][0]

    # Nothing may cull the network: no axis range is ever rewritten, and the
    # dead camera-standoff approach must not come back.
    assert "axis.range" not in tour, "a flight that moves a range hides nodes"
    assert "var d = 0." not in tour, "the camera standoff is back"
    assert '"scene.aspectratio"' in tour, "there is no zoom at all"

    zoom = float(re.search(r"ZOOM = ([0-9.]+)", tour).group(1))
    mag = float(re.search(r"MAG = ([0-9.]+)", tour).group(1))
    orbit = float(re.search(r"ORBIT = ([0-9.]+)", tour).group(1))
    across = float(re.search(r"ACROSS = ([0-9.]+)", tour).group(1))
    assert zoom > 1, "the scene never grows, so nothing looks closer"
    # Plotly markers are sized in screen pixels, so spreading the scene apart
    # leaves every node the size it was. They have to be scaled to match.
    assert mag > 1 and "function magnify(" in tour
    # **The focus node is a real sphere in the scene.** Markers never grow as
    # the camera comes in, and an HTML overlay is a flat sticker that does not
    # rotate or shade. Only something in the data gets bigger because the view
    # got closer, which is what was asked for.
    # The sphere is sized from the node's OWN marker, so that it reads as that
    # node and the only reason it grows is that the camera came closer. A fixed
    # fraction of the graph made every focus node the same enormous ball and hid
    # what a node's size means here.
    assert "sphereRadius(px)" in tour and "light(s.xyz, s.px, s.seg)" in tour
    # The focus colour runs on the FLIGHT's progress, not on the zoom. From one
    # stop to the next the zoom does not change at all, so a ramp tied to it
    # would jump straight to full and the colour would snap.
    assert "var SWAP" in tour and "var d = 1 - t / sw;" in tour
    # The card rides the same ramp as the colour, so they read as one arrival
    # rather than a highlight coming up and a card appearing after it.
    assert "cardFade(u);" in tour and "cardFade(d);" in tour
    # With nothing lit there is nothing to fade out, so the handover is skipped
    # and the opening zoom fades up over the whole flight instead of the last
    # 62% of it.
    assert "(gd().data.length > 2) ? SWAP : 0" in tour
    assert "var SR" not in tour, "the fixed sphere fraction is back"
    assert across > 2, (
        "the cube does not fill the viewport: the default camera sits back, so "
        "assuming 2 units across makes the sphere smaller than its own marker")
    assert '"surface"' in tour, "the focus node is not in the scene"
    # Mesh3d renders its own triangle edges here, giving a wireframe globe that
    # no contour/flatshading/normals-epsilon setting removed.
    assert "mesh3d" not in tour
    assert orbit > 0, "the flight only dollies, it never turns"

    # The camera centre is in units of half the aspect ratio, so it has to be
    # rescaled as the zoom tweens or the focus node drifts off to one side.
    assert "n.x * k / 2" in tour

    # Both coordinate forms are present, and the camera form inverts through
    # the figure's OWN pinned ranges back to the data form.
    stops = json.loads(re.search(r"var STOPS = (\[.*\]), FLIGHT = ", tour,
                                 re.S).group(1))
    assert stops and all(s.get("xyz") and s.get("cam") for s in stops)
    assert "x: s.cam[0], y: s.cam[1], z: s.cam[2]" in tour, \
        "the camera is not given the camera form"

    ranges = [[float(v) for v in m]
              for m in re.findall(r'"range":\s*\[([-\d.e]+),\s*([-\d.e]+)\]',
                                  html)[:3]]
    assert len(ranges) == 3, "axis ranges must be pinned, not autoranged"
    for s in stops:
        for i in range(3):
            lo, hi = ranges[i]
            back = lo + (s["cam"][i] + 1) / 2 * (hi - lo)
            assert abs(back - s["xyz"][i]) < 1e-6, (
                f"axis {i}: camera {s['cam'][i]} inverts to {back}, "
                f"not {s['xyz'][i]}")


def test_2d_tour_has_no_arrowheads_and_sizes_the_focus_by_value():
    """Two silent failures in the 2D view, both invisible to the old tests.

    Arrowheads on a dense citation network stack into a texture that reads as
    noise, not as direction. The graph is still directed and the hover still
    says "cites"; only the ornament is gone.

    And **`value` is what sizes a vis.js node, not `size`.** When a node
    carries a value, vis recomputes `size` from it on every redraw, so the
    tour's `size` multiplier did nothing and the focused node stayed smaller
    than its better-cited neighbours while claiming to be the subject.
    """
    pytest.importorskip("pyvis")
    import json
    import re
    import tempfile

    import networkx as nx

    from bioleads import citations

    g = nx.DiGraph()
    for i in range(6):
        g.add_node(f"PMID:{i}", pmid=str(i), title=f"P{i}", in_corpus_citations=i)
    g.add_edges_from([("PMID:5", f"PMID:{j}") for j in range(3)])

    with tempfile.TemporaryDirectory() as d:
        out = citations.write_citation_html(g, os.path.join(d, "t.html"),
                                            title="t")
        html = open(out, encoding="utf-8").read()

    edges = json.loads(re.search(r"edges = new vis\.DataSet\((\[.*?\])\);",
                                 html, re.S).group(1))
    assert edges, "no edges to check"
    assert all(e.get("arrows") == "" for e in edges), "arrowheads are back"
    # Direction is still recorded, just not drawn.
    assert all("cites" in (e.get("title") or "") for e in edges)

    tour = [m.group(1) for m in
            re.finditer(r"<script[^>]*>(.*?)</script>", html, re.S)
            if "bl-tour-play" in m.group(1)][0]
    assert "v1: MAXV * 1.9" in tour, "the focus node is sized by `size`, which"\
                                     " vis overwrites from `value` on redraw"
    # Grown over the flight rather than written in one go: the jump was most of
    # what still read as snappy however smoothly the colour faded.
    assert "value: lit.v0 + (lit.v1 - lit.v0) * u" in tour
    assert "value: lit.value" in tour, "unlight leaves the node enlarged"


def test_unit_sphere_is_a_sphere():
    """Round, closed, and a grid -- checked here rather than in a render."""
    import math

    from bioleads.graph3d import TOUR_SPHERE_SEGMENTS, unit_sphere

    xs, ys, zs = unit_sphere()
    lon, lat = TOUR_SPHERE_SEGMENTS
    assert len(xs) == lat + 1 and len(xs[0]) == lon + 1, "not a closed grid"
    assert len(ys) == len(xs) and len(zs) == len(xs)
    for row_x, row_y, row_z in zip(xs, ys, zs):
        for x, y, z in zip(row_x, row_y, row_z):
            assert abs(math.sqrt(x * x + y * y + z * z) - 1) < 2e-4
    # The seam column repeats the first, or the surface has a slit in it.
    assert xs[5][0] == xs[5][-1] and ys[5][0] == ys[5][-1]


def test_both_tours_share_one_card_and_keep_clear_of_bootstrap():
    """One card design for both views, with class names that cannot collide.

    pyvis ships Bootstrap, whose own `.row` captured the card and stacked
    every label above its value -- in the 2D page only, so the two views drifted
    apart while each looked fine on its own.
    """
    from bioleads.citations import CARD_CSS, CARD_JS

    assert ".bl-row" in CARD_CSS and ".bl-lbl" in CARD_CSS
    for generic in (" .row ", " .hd ", " .lbl ", " .val ", " .rule "):
        assert generic not in CARD_CSS, f"{generic.strip()} can collide"
    assert "bl-row" in CARD_JS and "bl-hd" in CARD_JS

    pytest.importorskip("pyvis")
    pytest.importorskip("plotly")
    import tempfile

    import networkx as nx

    from bioleads import citations

    g = nx.DiGraph()
    for i in range(4):
        g.add_node(f"PMID:{i}", pmid=str(i), title=f"P{i}", in_corpus_citations=i)
    g.add_edges_from([("PMID:3", f"PMID:{j}") for j in range(2)])

    with tempfile.TemporaryDirectory() as d:
        two = open(citations.write_citation_html(
            g, os.path.join(d, "a.html"), title="t"), encoding="utf-8").read()
        three = open(citations.write_citation_html_3d(
            g, os.path.join(d, "b.html"), title="t"), encoding="utf-8").read()
    for page in (two, three):
        assert "window.BL_CARD" in page and ".bl-card .bl-row" in page
        # Controls belong in a bottom corner, out of the picture.
        assert "bottom:16px; left:16px" in page


def test_3d_tour_lights_the_focus_node_s_own_edges():
    """A stop should show which papers this one connects to, in both views.

    2D gets this from vis.js, which repaints a selected node's edges. Plotly
    has no equivalent: the whole graph is **one line trace**, and a trace
    cannot be partly recolored, so the node's own edges are drawn again on top
    as a second trace. The segments are computed in Python, once, rather than
    walking the adjacency in the browser on every stop.
    """
    pytest.importorskip("plotly")
    import json
    import re
    import tempfile

    import networkx as nx

    from bioleads import citations, graph3d

    g = nx.DiGraph()
    for i in range(7):
        g.add_node(f"PMID:{i}", pmid=str(i), title=f"P{i}", in_corpus_citations=i)
    # PMID:6 cites three papers and is cited by one, and one of those is
    # reciprocal, so its degree exceeds its neighbour count.
    g.add_edges_from([("PMID:6", "PMID:0"), ("PMID:6", "PMID:1"),
                      ("PMID:6", "PMID:2"), ("PMID:0", "PMID:6")])

    with tempfile.TemporaryDirectory() as d:
        out = graph3d.write_graph_3d(g, os.path.join(d, "t.html"),
                                     size_attr="in_corpus_citations",
                                     stops=citations.tour_stops(g, 3))
        html = open(out, encoding="utf-8").read()

    tour = [m.group(1) for m in
            re.finditer(r"<script[^>]*>(.*?)</script>", html, re.S)
            if "bl3-play" in m.group(1)][0]
    stops = json.loads(re.search(r"var STOPS = (\[.*\]), FLIGHT = ", tour,
                                 re.S).group(1))
    top = stops[0]
    assert top["id"] == "PMID:6"

    # Three distinct neighbours, each one segment of two points plus a None.
    seg = top["seg"]
    assert len(seg["x"]) == 9, seg["x"]
    assert seg["x"][2] is None and seg["x"][5] is None
    assert len(seg["y"]) == len(seg["x"]) == len(seg["z"])

    # **The count the panel shows is the count the picture supports.** The
    # reciprocal pair is one line, not two drawn over each other, and the stop
    # claims 3 connections rather than the 4 that `g.degree()` reports for a
    # directed graph. A number a reader cannot verify by counting is worse
    # than no number, and ranking on it reordered the tour against the picture.
    assert top["degree"] == 3, "in+out degree is back"
    assert top["degree"] == len(seg["x"]) // 3

    # Both overlay traces come off together, or deleting by index unlights the
    # wrong one.
    assert "function clearLit()" in tour
    assert "while (el.data.length > 2)" in tour
    assert "Plotly.addTraces(el, [lit, trace])" in tour


def test_2d_tour_restores_edges_to_an_explicit_resting_style():
    """A visited node's edges must go back to grey, not stay pink.

    **Restoring a saved `undefined` does not undo a tint.** vis.js has already
    parsed the colour that was set, and writing the key back as undefined
    leaves that parsed value in place, so every node the tour visited kept its
    edges pink for the rest of the session and the graph slowly turned.
    """
    pytest.importorskip("pyvis")
    import re
    import tempfile

    import networkx as nx

    from bioleads import citations

    g = nx.DiGraph()
    for i in range(5):
        g.add_node(f"PMID:{i}", pmid=str(i), title=f"P{i}", in_corpus_citations=i)
    g.add_edges_from([("PMID:4", f"PMID:{j}") for j in range(3)])

    with tempfile.TemporaryDirectory() as d:
        html = open(citations.write_citation_html(
            g, os.path.join(d, "t.html"), title="t"), encoding="utf-8").read()

    tour = [m.group(1) for m in
            re.finditer(r"<script[^>]*>(.*?)</script>", html, re.S)
            if "bl-tour-play" in m.group(1)][0]

    # An explicit resting style exists and is what unlight falls back to.
    assert "var EDGE_REST = {" in tour and "var NODE_REST = {" in tour
    assert "e.color === undefined ? EDGE_REST : e.color" in tour
    assert "lit.color === undefined ? NODE_REST : lit.color" in tour
    assert "e.width === undefined ? EDGE_REST_W : e.width" in tour

    # The resting edge colour is defined once and used by both the options and
    # the restore, so the two cannot drift apart.
    assert tour.count("var EDGE_BASE =") == 1
    assert tour.count("EDGE_BASE") >= 3


def test_senior_authors_merge_by_family_name():
    """One lab, one node, however the records spell it.

    The same senior author is written "Kornberg TB" by one record and
    "Kornberg T" by another. Left alone that splits a lab into two nodes, each
    with half its papers and half its edges, which is a worse error than the
    one merging risks.
    """
    from bioleads.citations import merge_by_surname, surname

    assert surname("Kornberg TB") == "kornberg"          # PubMed order
    assert surname("Kornberg, Thomas B") == "kornberg"   # iCite fullName
    assert surname("Thomas B Kornberg") == "kornberg"    # written out
    # Particles belong to the surname, both ways round.
    assert surname("van der Berg AJ") == "van der berg"
    assert surname("Anna van der Berg") == "van der berg"
    # A short surname is not initials: all-caps is what distinguishes them.
    assert surname("Ng W") == "ng"
    assert surname("") == ""

    senior = {"1": "Kornberg TB", "2": "Kornberg T", "3": "Thomas B Kornberg",
              "4": "Wu M", "5": "Zurzolo C"}
    remapped, merged, ambiguous = merge_by_surname(senior)
    assert not ambiguous

    # All three spellings land on one node, under the fullest label.
    assert len({remapped["1"], remapped["2"], remapped["3"]}) == 1
    assert remapped["1"] == "Thomas B Kornberg"
    # Untouched names are left exactly as they were.
    assert remapped["4"] == "Wu M" and remapped["5"] == "Zurzolo C"
    # Only surnames that actually had variants are reported.
    assert set(merged) == {"Thomas B Kornberg"}
    assert len(merged["Thomas B Kornberg"]) == 3


def test_merging_needs_compatible_initials_too():
    """A shared surname is not enough: two Wangs must stay two nodes.

    Initials are compatible when one is a prefix of the other, which is what a
    fuller spelling of one person looks like. Where a vague spelling fits more
    than one person of that surname there is no way to choose, so it is left on
    its own node and reported rather than assigned by a coin toss.
    """
    from bioleads.citations import merge_by_surname

    def nodes(names):
        senior = {str(i): n for i, n in enumerate(names)}
        remapped, merged, ambiguous = merge_by_surname(senior)
        return sorted(set(remapped.values())), merged, ambiguous

    # One person, three spellings, one node.
    got, _, amb = nodes(["Kornberg TB", "Kornberg T", "Thomas B Kornberg"])
    assert got == ["Thomas B Kornberg"] and not amb

    # Two people, one surname, two nodes.
    got, merged, amb = nodes(["Wang Y", "Wang X"])
    assert got == ["Wang X", "Wang Y"]
    assert not merged and not amb

    # H joins HJ, K stands alone.
    got, _, amb = nodes(["Lee H", "Lee HJ", "Lee K"])
    assert got == ["Lee HJ", "Lee K"] and not amb

    # No initials at all is compatible with one person...
    got, _, amb = nodes(["Smith", "Smith JA"])
    assert got == ["Smith JA"] and not amb

    # ...but not when it could be either of two.
    got, _, amb = nodes(["Smith", "Smith JA", "Smith RB"])
    assert got == ["Smith", "Smith JA", "Smith RB"]
    assert amb == {"smith": ["Smith"]}
def test_initials_are_read_the_same_way_whatever_the_name_form():
    """Reading initials off the tokens does not survive the three spellings.

    "Kornberg TB" offers the token "TB" while "Thomas B Kornberg" offers only
    "B", so comparing them straight made one person look like two and every
    merged lab got flagged as suspicious. They are taken as "the name minus
    the surname" instead.
    """
    from bioleads.citations import _given_initials

    assert _given_initials("Kornberg TB") == "TB"
    assert _given_initials("Kornberg, Thomas B") == "TB"
    assert _given_initials("Thomas B Kornberg") == "TB"
    assert _given_initials("Kornberg T") == "T"
    assert _given_initials("Kornberg") == ""
    assert _given_initials("van der Berg AJ") == "AJ"
    assert _given_initials("Anna van der Berg") == "A"
