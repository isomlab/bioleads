"""Tests for tools/benchmark_expansion.py — all offline.

Every network boundary is stubbed; what's under test is the scoring, the arms,
the fairness correction and the cache, not NCBI.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

_TOOL = pathlib.Path(__file__).resolve().parents[1] / "tools" / "benchmark_expansion.py"


@pytest.fixture(scope="module")
def bench():
    spec = importlib.util.spec_from_file_location("benchmark_expansion", _TOOL)
    mod = importlib.util.module_from_spec(spec)
    # @dataclass resolves its module through sys.modules, so register before exec.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------- metrics --

def test_evaluate_counts_hits_against_the_target(bench):
    r = bench.evaluate(retrieved={"a", "b", "c"}, target={"b", "c", "d"}, seeds=[])
    assert r["n_hits"] == 2
    assert r["precision"] == pytest.approx(2 / 3)
    assert r["recall"] == pytest.approx(2 / 3)
    assert r["f1"] == pytest.approx(2 / 3)


def test_evaluate_ignores_the_seeds_it_was_given(bench):
    # 's' was handed to the arm: finding it is neither a hit nor a false positive.
    r = bench.evaluate(retrieved={"s", "b"}, target={"s", "b", "d"}, seeds=["s"])
    assert r["n_retrieved"] == 1 and r["n_target"] == 2
    assert r["precision"] == pytest.approx(1.0)
    assert r["recall"] == pytest.approx(0.5)


def test_evaluate_handles_empty_sides(bench):
    assert bench.evaluate(set(), {"a"}, []) == {
        "precision": 0.0, "recall": 0.0, "f1": 0.0,
        "n_retrieved": 0, "n_target": 1, "n_hits": 0}
    assert bench.evaluate({"a"}, set(), [])["recall"] == 0.0


def test_summarize_reports_median_and_mean(bench):
    rows = [{"precision": p, "recall": 0.0, "f1": 0.0, "n_retrieved": 1}
            for p in (0.1, 0.2, 0.9)]
    s = bench.summarize(rows)
    assert s["median_precision"] == pytest.approx(0.2)
    assert s["mean_precision"] == pytest.approx(0.4)
    assert s["n_reviews"] == 3


def test_as_ids_normalizes_icites_shapes(bench):
    assert bench._as_ids([1, "2", " 3 "]) == ["1", "2", "3"]
    assert bench._as_ids("10 11") == ["10", "11"]
    assert bench._as_ids(None) == [] and bench._as_ids([]) == []


# ---------------------------------------------------------------- arms --

def _trial(bench, seeds, records, target, year=2020):
    return bench.Trial(review=bench.Review(pmid="R", year=year, references=[]),
                       seeds=seeds, target=set(target), records=records)


def test_seed_links_unions_across_seeds_and_drops_seeds(bench):
    records = {"1": {"cited_by": [10, 11], "references": [20, "2"]},
               "2": {"cited_by": [11, 12], "references": [21]}}
    t = _trial(bench, ["1", "2"], records, target=[])
    assert bench._seed_links(t, "cited_by") == {"10", "11", "12"}
    # seed "2" appears in seed "1"'s references and must not count as retrieved
    assert bench._seed_links(t, "references") == {"20", "21"}


def test_forward_backward_and_both_arms(bench):
    records = {"1": {"cited_by": [10], "references": [20]}}
    t = _trial(bench, ["1"], records, target=[])
    ctx = {"records": records}
    assert bench.run_arm("forward", t, ctx) == {"10"}
    assert bench.run_arm("backward", t, ctx) == {"20"}
    assert bench.run_arm("both", t, ctx) == {"10", "20"}
    with pytest.raises(ValueError):
        bench.run_arm("sideways", t, ctx)


def test_year_cutoff_drops_papers_the_review_could_not_cite(bench):
    records = {"old": {"year": 2015}, "same": {"year": 2020},
               "new": {"year": 2023}, "unknown": {}}
    kept = bench.apply_year_cutoff({"old", "same", "new", "unknown"}, records, 2020)
    assert kept == {"old", "same", "unknown"}, "post-review papers must be dropped"


def test_relevance_arm_keeps_all_forward_and_gates_backward(bench, monkeypatch):
    """The arm must mirror relevance_guided_expand: forward ungated, backward top-K."""
    import bioleads.expansion as exp_mod

    seen = {}

    def fake_top_k(profile_docs, cand_docs, cfg, **kw):
        seen["gamma"] = cfg.rocchio_gamma
        seen["top_k"] = cfg.expand_top_k
        seen["profile"] = {d.meta["pmid"] for d in profile_docs}
        return [(cand_docs[0], 1.0)]          # keep exactly one candidate

    monkeypatch.setattr(exp_mod, "_top_k_relevant", fake_top_k)

    from bioleads.config import Config

    records = {
        "1": {"cited_by": [10, 11], "references": [20, 21], "title": "seed"},
        "10": {"title": "citer a"}, "11": {"title": "citer b"},
        "20": {"title": "ref a"}, "21": {"title": "ref b"},
    }
    t = _trial(bench, ["1"], records, target=[])
    ctx = {"records": records, "texts": None, "cfg": Config(expand_top_k=7)}

    got = bench.run_arm("relevance:0.4", t, ctx)
    assert {"10", "11"} <= got, "forward citers are added ungated"
    assert len(got & {"20", "21"}) == 1, "backward must be gated to the kept top-K"
    assert seen["gamma"] == pytest.approx(0.4), "gamma from the arm name must reach cfg"
    assert seen["top_k"] == 7, "--top-k must reach cfg"
    assert seen["profile"] == {"1", "10", "11"}, "profile = seeds + forward citers"


def test_relevance_arm_without_candidates_returns_forward(bench):
    from bioleads.config import Config

    records = {"1": {"cited_by": [10], "references": [], "title": "seed"},
               "10": {"title": "citer"}}
    t = _trial(bench, ["1"], records, target=[])
    ctx = {"records": records, "texts": None, "cfg": Config()}
    assert bench.run_arm("relevance:0.25", t, ctx) == {"10"}


def test_docs_skip_records_with_no_text(bench):
    docs = bench._docs_from_records(["1", "2"], {"1": {"title": "has one"}, "2": {}})
    assert [d.meta["pmid"] for d in docs] == ["1"]


# --------------------------------------------------------------- cache --

def test_cache_produces_once_then_reads_from_disk(bench, tmp_path):
    cache = bench.Cache(str(tmp_path / "c"))
    calls = []

    def produce():
        calls.append(1)
        return {"v": 42}

    assert cache.get_or("k", produce) == {"v": 42}
    assert cache.get_or("k", produce) == {"v": 42}
    assert len(calls) == 1, "second call must come from disk"
    assert (cache.hits, cache.misses) == (1, 1)


# -------------------------------------------------------------- driver --

def test_build_trials_is_deterministic_and_holds_out_the_target(bench, tmp_path):
    import random

    reviews = [bench.Review(pmid="R1", year=2020, references=[str(i) for i in range(20)])]
    cache = bench.Cache(str(tmp_path / "c"))
    # No network: pre-seed the per-PMID iCite cache with empty records.
    for i in range(20):
        cache.get_or(f"unused{i}", dict)
    monkey = {p: {} for p in (str(i) for i in range(20))}
    bench_icite = bench._icite
    bench._icite = lambda pmids, _cache: {p: monkey.get(p, {}) for p in pmids}
    try:
        a = bench.build_trials(reviews, 5, random.Random(0), cache)
        b = bench.build_trials(reviews, 5, random.Random(0), cache)
    finally:
        bench._icite = bench_icite
    assert a[0].seeds == b[0].seeds, "same RNG seed must give the same split"
    assert len(a[0].seeds) == 5
    assert not (set(a[0].seeds) & a[0].target), "seeds must be held out of the target"
    assert len(a[0].target) == 15


def test_run_benchmark_scores_every_arm_on_every_trial(bench):
    from bioleads.config import Config

    records = {
        "s1": {"cited_by": ["f1"], "references": ["b1"], "year": 2015},
        "f1": {"year": 2019}, "b1": {"year": 2012},
        "s2": {"cited_by": ["f2"], "references": ["b2"], "year": 2015},
        "f2": {"year": 2030}, "b2": {"year": 2011},          # f2 is post-review
    }
    trials = [
        _trial(bench, ["s1"], records, target={"f1", "b1"}, year=2020),
        _trial(bench, ["s2"], records, target={"f2", "b2"}, year=2020),
    ]
    ctx = {"records": records, "texts": None, "cfg": Config()}
    rows, summary = bench.run_benchmark(trials, ["forward", "backward"], ctx, log=lambda _m: None)

    assert len(rows) == 4 and {r["arm"] for r in rows} == {"forward", "backward"}
    # Each target holds one forward and one backward paper, so a single-direction
    # arm caps at 0.5 recall. In trial 2 the forward hit is published after the
    # review, so the cutoff removes it and forward recall falls to 0.
    fwd = [r for r in rows if r["arm"] == "forward"]
    assert [r["recall"] for r in fwd] == [0.5, 0.0]
    assert [r["precision"] for r in fwd] == [1.0, 0.0]
    assert summary["backward"]["median_recall"] == pytest.approx(0.5)
    assert summary["forward"]["n_reviews"] == 2


def test_interpret_reads_the_asymmetry_off_the_summary(bench):
    lines: list[str] = []
    summary = {
        "forward": {"median_precision": 0.30, "median_recall": 0.10,
                    "median_f1": 0.15, "n_reviews": 5},
        "backward": {"median_precision": 0.10, "median_recall": 0.40,
                     "median_f1": 0.16, "n_reviews": 5},
    }
    bench.interpret(summary, log=lines.append)
    assert any("SUPPORTED" in l and "NOT SUPPORTED" not in l for l in lines)

    lines.clear()
    summary["forward"]["median_precision"] = 0.05
    bench.interpret(summary, log=lines.append)
    assert any("NOT SUPPORTED" in l for l in lines)


def test_interpret_picks_the_best_gamma(bench):
    lines: list[str] = []
    summary = {
        "relevance:0.0": {"median_f1": 0.20, "median_precision": 0, "median_recall": 0,
                          "n_reviews": 3},
        "relevance:0.25": {"median_f1": 0.28, "median_precision": 0, "median_recall": 0,
                           "n_reviews": 3},
    }
    bench.interpret(summary, log=lines.append)
    assert any("Best gamma by median F1: 0.25" in l for l in lines)
    assert any("+0.0800" in l for l in lines), lines


def test_max_profile_caps_the_profile_but_not_what_is_retrieved(bench, monkeypatch):
    """The runtime guard must never change an arm's retrieved set.

    Capping the profile is a benchmark-only concession to runtime; if it also
    shrank the returned candidates it would silently alter precision and recall.
    """
    import bioleads.expansion as exp_mod
    from bioleads.config import Config

    seen = {}

    def fake_top_k(profile_docs, cand_docs, cfg, **kw):
        seen["profile_size"] = len(profile_docs)
        return []

    monkeypatch.setattr(exp_mod, "_top_k_relevant", fake_top_k)

    citers = [str(100 + i) for i in range(50)]
    records = {"1": {"cited_by": citers, "references": ["20"], "title": "seed"},
               "20": {"title": "ref"}}
    for c in citers:
        records[c] = {"title": f"citer {c}"}
    t = _trial(bench, ["1"], records, target=[])

    uncapped = bench.run_arm("relevance:0.25", t,
                             {"records": records, "texts": None, "cfg": Config()})
    assert seen["profile_size"] == 51                       # 1 seed + 50 citers

    capped = bench.run_arm("relevance:0.25", t,
                           {"records": records, "texts": None, "cfg": Config(),
                            "max_profile": 10})
    assert seen["profile_size"] == 11                       # 1 seed + 10 citers
    assert capped == uncapped, "the cap must not change what the arm retrieves"
    assert len(capped) == 50
