#!/usr/bin/env python3
"""Benchmark bioleads' citation expansion against systematic reviews.

Why
---
`expand_strategy="relevance"` rests on an asserted asymmetry: papers that *cite*
a seed are topically precise, papers a seed *cites* are comprehensive but noisy.
That claim, and the `rocchio_gamma` that acts on it, were never measured. This
harness measures both.

Design (after Sjogarde & Ahlgren 2024, JASIST; arXiv:2403.09295)
----------------------------------------------------------------
A systematic review's reference list is a curated, human-assembled answer to
"which papers are relevant to this topic?". So:

1. Take a systematic review with a decent reference list.
2. Sample `--seeds` of its references at random. These are the seeds.
3. The *remaining* references are the target: papers a perfect expansion would
   recover from those seeds.
4. Run each arm from the seeds and score what it retrieves against the target.

Fairness correction
-------------------
A paper published after the review cannot possibly be in its reference list, so
counting such papers as false positives would penalise forward expansion for
doing exactly what it is supposed to do. Candidates published after the review's
year are therefore dropped before scoring (disable with --no-year-cutoff to see
how much this matters).

Cost
----
The cheap arms (forward / backward / both) need only iCite records and answer
the asymmetry question on their own. The `relevance` arms additionally need text
to score against; by default they use the titles iCite already returned, so a
full run costs *no extra requests*. Pass --abstracts to fetch title+abstract
from PubMed instead — better fidelity, far more requests. Every response is
cached on disk, so re-runs and added arms are free.

Usage
-----
    python3 tools/benchmark_expansion.py --reviews 40 --seeds 5 \
        --arms forward,backward,both,relevance --gammas 0,0.25,0.5 \
        --out bench.csv
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import statistics
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

DEFAULT_QUERY = (
    'systematic review[pt] AND "journal article"[pt] '
    'AND 2018:2022[dp] AND hasabstract'
)


# --------------------------------------------------------------- cache --
class Cache:
    """Dumb, durable JSON cache: one file per request key.

    The benchmark is re-run constantly while arms are added and gammas swept;
    without this, every run would re-hit NCBI and iCite for identical data.
    """

    def __init__(self, path: str):
        self.path = path
        os.makedirs(path, exist_ok=True)
        self.hits = self.misses = 0

    def _file(self, key: str) -> str:
        return os.path.join(self.path, hashlib.sha1(key.encode()).hexdigest() + ".json")

    def get_or(self, key: str, produce):
        f = self._file(key)
        if os.path.exists(f):
            self.hits += 1
            with open(f) as fh:
                return json.load(fh)
        self.misses += 1
        value = produce()
        tmp = f + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(value, fh)
        os.replace(tmp, f)          # never leave a half-written cache entry
        return value


# ---------------------------------------------------------------- data --
@dataclass
class Review:
    pmid: str
    year: int
    references: list[str]


@dataclass
class Trial:
    """One review split into seeds and the target they should recover."""
    review: Review
    seeds: list[str]
    target: set[str]
    records: dict = field(default_factory=dict)   # iCite records for the seeds


def _icite(pmids, cache: Cache) -> dict:
    """Cached iCite lookup, batched by the library's own fetcher."""
    from bioleads.citations import fetch_icite

    ids = sorted({str(p) for p in pmids if str(p).strip()})
    if not ids:
        return {}
    out: dict = {}
    missing: list[str] = []
    for pmid in ids:                      # per-PMID caching so batches compose
        f = cache._file(f"icite:{pmid}")
        if os.path.exists(f):
            cache.hits += 1
            with open(f) as fh:
                rec = json.load(fh)
            if rec:
                out[pmid] = rec
        else:
            missing.append(pmid)
    if missing:
        cache.misses += len(missing)
        fetched = fetch_icite(missing)
        for pmid in missing:
            rec = fetched.get(pmid, {})
            tmp = cache._file(f"icite:{pmid}") + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(rec, fh)
            os.replace(tmp, cache._file(f"icite:{pmid}"))
            if rec:
                out[pmid] = rec
    return out


def _as_ids(value) -> list[str]:
    """iCite returns reference/cited_by lists as ints, strings, or None."""
    if not value:
        return []
    if isinstance(value, str):
        value = value.split()
    return [str(v).strip() for v in value if str(v).strip()]


def load_reviews(query: str, n: int, min_refs: int, cache: Cache,
                 *, email: str, api_key=None) -> list[Review]:
    """Find systematic reviews and read their reference lists from iCite."""
    from bioleads.sources import _entrez

    def _search():
        Entrez, _Medline = _entrez(email, api_key)   # returns a (Entrez, Medline) pair
        handle = Entrez.esearch(db="pubmed", term=query, retmax=n * 6, sort="relevance")
        return Entrez.read(handle).get("IdList", [])

    pmids = cache.get_or(f"esearch:{query}:{n * 6}", _search)
    records = _icite(pmids, cache)

    reviews: list[Review] = []
    for pmid in pmids:
        rec = records.get(str(pmid)) or {}
        refs = _as_ids(rec.get("references"))
        year = rec.get("year")
        if len(refs) >= min_refs and year:
            reviews.append(Review(pmid=str(pmid), year=int(year), references=refs))
        if len(reviews) >= n:
            break
    return reviews


def build_trials(reviews, n_seeds: int, rng: random.Random, cache: Cache) -> list[Trial]:
    trials = []
    for rv in reviews:
        refs = sorted(set(rv.references))
        if len(refs) <= n_seeds + 1:
            continue
        seeds = rng.sample(refs, n_seeds)
        target = set(refs) - set(seeds)
        trials.append(Trial(review=rv, seeds=seeds, target=target,
                            records=_icite(seeds, cache)))
    return trials


# ------------------------------------------------------------- metrics --
def evaluate(retrieved: set[str], target: set[str], seeds) -> dict:
    """Precision / recall / F1 of `retrieved` against `target`.

    Seeds are excluded from both sides: they were handed to the arm, so finding
    them again is neither a hit nor a false positive.
    """
    seeds = set(seeds)
    retrieved = set(retrieved) - seeds
    target = set(target) - seeds
    hits = retrieved & target
    precision = len(hits) / len(retrieved) if retrieved else 0.0
    recall = len(hits) / len(target) if target else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {"precision": precision, "recall": recall, "f1": f1,
            "n_retrieved": len(retrieved), "n_target": len(target), "n_hits": len(hits)}


def summarize(rows: list[dict]) -> dict:
    """Median and mean per metric. Medians are the headline (skewed distributions)."""
    out = {}
    for metric in ("precision", "recall", "f1", "n_retrieved"):
        vals = [r[metric] for r in rows]
        out[f"median_{metric}"] = statistics.median(vals) if vals else 0.0
        out[f"mean_{metric}"] = statistics.fmean(vals) if vals else 0.0
    out["n_reviews"] = len(rows)
    return out


# ---------------------------------------------------------------- arms --
def _seed_links(trial: Trial, field_name: str) -> set[str]:
    """Union of one iCite link field across a trial's seeds (one round)."""
    out: set[str] = set()
    for pmid in trial.seeds:
        out |= set(_as_ids((trial.records.get(pmid) or {}).get(field_name)))
    return out - set(trial.seeds)


def _docs_from_records(pmids, records: dict, texts: dict | None = None):
    """Documents for the relevance scorer: iCite titles, or fetched abstracts."""
    from bioleads.sources import Document

    docs = []
    for pmid in pmids:
        rec = records.get(pmid) or {}
        title = (rec.get("title") or "").strip()
        body = (texts or {}).get(pmid, "")
        if not (title or body):
            continue
        docs.append(Document(doc_id=f"PMID:{pmid}", text=body, title=title,
                             source="pubmed", meta={"pmid": pmid}))
    return docs


def _fetch_abstracts(pmids, cache: Cache, *, email: str, api_key=None) -> dict:
    """Cached title+abstract text per PMID (only used with --abstracts)."""
    from bioleads.sources import fetch_pubmed_by_ids

    out: dict[str, str] = {}
    missing = []
    for pmid in pmids:
        f = cache._file(f"abstract:{pmid}")
        if os.path.exists(f):
            cache.hits += 1
            with open(f) as fh:
                out[pmid] = json.load(fh)
        else:
            missing.append(pmid)
    if missing:
        cache.misses += len(missing)
        docs = fetch_pubmed_by_ids(missing, email=email, api_key=api_key)
        got = {d.meta.get("pmid", d.doc_id.replace("PMID:", "")): d.text for d in docs}
        for pmid in missing:
            text = got.get(pmid, "")
            tmp = cache._file(f"abstract:{pmid}") + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(text, fh)
            os.replace(tmp, cache._file(f"abstract:{pmid}"))
            out[pmid] = text
    return out


def run_arm(name: str, trial: Trial, ctx: dict) -> set[str]:
    """Retrieve a candidate set for one arm. `name` is 'forward'/'backward'/
    'both'/'relevance:<gamma>'."""
    forward = _seed_links(trial, "cited_by")
    backward = _seed_links(trial, "references")

    if name == "forward":
        return forward
    if name == "backward":
        return backward
    if name == "both":
        return forward | backward
    if name.startswith("relevance"):
        from dataclasses import replace as _replace

        from bioleads.config import Config
        from bioleads.expansion import _top_k_relevant

        gamma = float(name.split(":", 1)[1]) if ":" in name else Config.rocchio_gamma
        cfg = _replace(ctx["cfg"], rocchio_gamma=gamma)
        records, texts = ctx["records"], ctx.get("texts")
        # A single heavily-cited seed can pull tens of thousands of citers, which
        # would dominate the sweep's runtime. Cap the profile deterministically
        # (benchmark-only; the pipeline itself has no such cap).
        cap = ctx.get("max_profile") or 0
        if cap and len(forward) > cap:
            forward = set(random.Random(0).sample(sorted(forward), cap))
        # Faithful to relevance_guided_expand: seeds + forward citers are the
        # profile, backward references are the gated candidates, and the return
        # is every forward citer plus the kept top-K.
        profile = _docs_from_records(list(trial.seeds) + sorted(forward), records, texts)
        cands = _docs_from_records(sorted(backward), records, texts)
        if not cands:
            return set(forward)
        kept = _top_k_relevant(profile, cands, cfg)
        return set(forward) | {d.meta["pmid"] for d, _ in kept}
    raise ValueError(f"unknown arm: {name}")


def apply_year_cutoff(pmids, records: dict, year: int) -> set[str]:
    """Drop candidates published after the review — they could not be cited by it.

    Without this, forward expansion is charged with false positives for finding
    recent work, which is the one thing it is guaranteed to do.
    """
    out = set()
    for pmid in pmids:
        y = (records.get(pmid) or {}).get("year")
        if y is None or int(y) <= year:
            out.add(pmid)
    return out


# -------------------------------------------------------------- driver --
def run_benchmark(trials, arms, ctx, *, year_cutoff: bool = True, log=print):
    """Score every arm on every trial. Returns (per-trial rows, per-arm summary)."""
    rows: list[dict] = []
    for i, trial in enumerate(trials, start=1):
        log(f"  review {i}/{len(trials)}  PMID:{trial.review.pmid} "
            f"({trial.review.year}, {len(trial.target)} target refs)")
        for arm in arms:
            retrieved = run_arm(arm, trial, ctx)
            if year_cutoff:
                retrieved = apply_year_cutoff(retrieved, ctx["records"],
                                              trial.review.year)
            row = evaluate(retrieved, trial.target, trial.seeds)
            row.update(arm=arm, review=trial.review.pmid, year=trial.review.year)
            rows.append(row)
    summary = {arm: summarize([r for r in rows if r["arm"] == arm]) for arm in arms}
    return rows, summary


def print_summary(summary: dict, log=print) -> None:
    log("")
    log(f"{'arm':<18}{'median P':>10}{'median R':>10}{'median F1':>11}"
        f"{'median n':>10}{'reviews':>9}")
    log("-" * 68)
    for arm, s in summary.items():
        log(f"{arm:<18}{s['median_precision']:>10.4f}{s['median_recall']:>10.4f}"
            f"{s['median_f1']:>11.4f}{s['median_n_retrieved']:>10.0f}"
            f"{s['n_reviews']:>9d}")


def interpret(summary: dict, log=print) -> None:
    """Say what the numbers mean for the two claims the harness exists to test."""
    log("")
    fwd, bwd = summary.get("forward"), summary.get("backward")
    if fwd and bwd and fwd["n_reviews"]:
        fp, bp = fwd["median_precision"], bwd["median_precision"]
        verdict = ("SUPPORTED" if fp > bp else
                   "NOT SUPPORTED" if fp < bp else "INCONCLUSIVE (tied)")
        log(f"Asymmetry claim (forward more precise than backward): {verdict}")
        log(f"  median precision  forward {fp:.4f}  vs  backward {bp:.4f}")
        log(f"  median recall     forward {fwd['median_recall']:.4f}  vs  "
            f"backward {bwd['median_recall']:.4f}")

    gammas = sorted((a for a in summary if a.startswith("relevance:")),
                    key=lambda a: float(a.split(":", 1)[1]))
    if len(gammas) > 1:
        best = max(gammas, key=lambda a: summary[a]["median_f1"])
        log("")
        log(f"Best gamma by median F1: {best.split(':', 1)[1]} "
            f"(F1 {summary[best]['median_f1']:.4f})")
        base = summary.get("relevance:0.0") or summary.get("relevance:0")
        if base and best not in ("relevance:0.0", "relevance:0"):
            delta = summary[best]["median_f1"] - base["median_f1"]
            log(f"  negative term changes median F1 by {delta:+.4f} vs gamma=0")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Benchmark bioleads citation expansion against systematic reviews.")
    p.add_argument("--reviews", type=int, default=25, help="systematic reviews to use")
    p.add_argument("--seeds", type=int, default=5, help="seed papers sampled per review")
    p.add_argument("--min-refs", type=int, default=30,
                   help="skip reviews with fewer references than this")
    p.add_argument("--arms", default="forward,backward,both,relevance",
                   help="comma-separated; 'relevance' expands over --gammas")
    p.add_argument("--gammas", default="0,0.25,0.5",
                   help="rocchio_gamma values for the relevance arm")
    p.add_argument("--top-k", type=int, default=50, help="expand_top_k for relevance")
    p.add_argument("--max-profile", type=int, default=0,
                   help="cap the forward citers used as the relevance profile "
                        "(0 = uncapped); keeps one mega-cited seed from "
                        "dominating a sweep's runtime")
    p.add_argument("--query", default=DEFAULT_QUERY, help="PubMed query for reviews")
    p.add_argument("--abstracts", action="store_true",
                   help="fetch title+abstract for scoring instead of using iCite "
                        "titles (better fidelity, many more requests)")
    p.add_argument("--no-year-cutoff", action="store_true",
                   help="do not drop candidates published after the review")
    p.add_argument("--cache", default=".benchmark_cache", help="cache directory")
    p.add_argument("--out", default="", help="write per-trial rows to this CSV")
    p.add_argument("--seed", type=int, default=0, help="RNG seed for sampling")
    p.add_argument("--email", default="", help="Entrez contact email")
    p.add_argument("--api-key", default=None, help="NCBI API key")
    args = p.parse_args(argv)

    from bioleads.config import Config

    email = args.email or Config.entrez_email
    cache = Cache(args.cache)
    rng = random.Random(args.seed)

    arms: list[str] = []
    for arm in [a.strip() for a in args.arms.split(",") if a.strip()]:
        if arm == "relevance":
            arms += [f"relevance:{g.strip()}" for g in args.gammas.split(",") if g.strip()]
        else:
            arms.append(arm)

    print(f"Loading up to {args.reviews} systematic reviews (>= {args.min_refs} refs)…")
    reviews = load_reviews(args.query, args.reviews, args.min_refs, cache,
                           email=email, api_key=args.api_key)
    if not reviews:
        print("No reviews matched. Try a broader --query or a lower --min-refs.",
              file=sys.stderr)
        return 1
    print(f"  {len(reviews)} review(s).")

    trials = build_trials(reviews, args.seeds, rng, cache)
    print(f"  {len(trials)} trial(s) after sampling {args.seeds} seed(s) each.")

    # Candidate metadata: years for the cutoff, titles for the relevance arms.
    candidates: set[str] = set()
    for t in trials:
        candidates |= _seed_links(t, "cited_by") | _seed_links(t, "references")
    print(f"Fetching iCite metadata for {len(candidates)} candidate(s)…")
    records = _icite(candidates, cache)
    for t in trials:
        records.update(t.records)

    texts = None
    if args.abstracts:
        print(f"Fetching abstracts for {len(candidates)} candidate(s)…")
        texts = _fetch_abstracts(sorted(candidates), cache,
                                 email=email, api_key=args.api_key)

    ctx = {"records": records, "texts": texts,
           "max_profile": args.max_profile,
           "cfg": Config(expand_top_k=args.top_k)}

    print(f"\nScoring {len(arms)} arm(s): {', '.join(arms)}")
    rows, summary = run_benchmark(trials, arms, ctx,
                                  year_cutoff=not args.no_year_cutoff)
    print_summary(summary)
    interpret(summary)

    scoring = "iCite titles" if texts is None else "title + abstract"
    print(f"\nseeds/review={args.seeds}  top_k={args.top_k}  scoring={scoring}  "
          f"year_cutoff={not args.no_year_cutoff}")
    print(f"cache: {cache.hits} hit(s), {cache.misses} miss(es) -> {args.cache}")

    if args.out:
        with open(args.out, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"per-trial rows -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
