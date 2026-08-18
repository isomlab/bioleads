"""Relevance-guided two-phase citation expansion (pseudo-relevance feedback).

The intuition (see README "Citation expansion"):

* **Forward** links (`cited_by`) *converge* on a topic — a paper that cites your
  seed is usually working in the same area — so the seeds plus their citers make
  a clean **topic profile**.
* **Backward** links (`references`) *diverge* — a paper cites methods, tangential
  background, and adjacent fields alongside the on-topic work — so swallowing a
  reference list is noisy.

So we use Phase 1 (forward) to learn what the topic looks like, then in Phase 2
score each backward reference against that profile and keep only the most
relevant (top-K). Relevance is NER term-overlap cosine, auto-upgraded to
PubMedBERT cosine when the `embed` extra is installed.

The profile is a Rocchio query vector: the positive centroid of the profile
documents, minus `cfg.rocchio_gamma` times the centroid of the worst-scoring
candidates (see Config). Subtracting that negative term is what makes the gate
discriminate *on-topic* from *citation-adjacent but off-topic*, rather than
merely from *unrelated*. With gamma = 0 it reduces to the positive-only
centroid.
"""
from __future__ import annotations

import math
import warnings
from collections import Counter

from .config import Config
from .sources import (
    Document,
    _check_cancel,
    _seed_pmids,
    expand_pmids,
    fetch_pubmed_by_ids,
)


def relevance_guided_expand(
    seed_docs: list[Document],
    cfg: Config | None = None,
    *,
    email: str | None = None,
    api_key: str | None = None,
    fulltext: bool = False,
    log=None,
    cancel=None,
) -> list[Document]:
    """Return new Documents to append: all forward citers + top-K relevant refs.

    `seed_docs` are the already-loaded documents; their PMIDs seed both phases
    and their text feeds the topic profile. Network failures in either phase are
    non-fatal — the phase that succeeds still contributes.
    """
    cfg = cfg or Config()
    email = email or cfg.entrez_email
    api_key = api_key if api_key is not None else cfg.entrez_api_key
    say = log or (lambda _msg: None)

    seeds = _seed_pmids(seed_docs)
    if not seeds:
        say("relevance expansion: no PMID-bearing seeds — nothing to expand.")
        return []
    have = {d.doc_id for d in seed_docs}

    # ---- Phase 1: forward (cited_by) → topic profile --------------------------
    _check_cancel(cancel)
    say(f"  phase 1 (cited_by): collecting papers that cite {len(seeds)} seed(s)…")
    fwd_ids = expand_pmids(
        seeds, rounds=cfg.expand_fwd_rounds, link="cited_by",
        source=cfg.expand_source, max_records=cfg.expand_max,
        email=email, api_key=api_key, cancel=cancel, progress=log,
    )
    fwd_new = [i for i in fwd_ids if i not in seeds and f"PMID:{i}" not in have]
    fwd_docs = (
        fetch_pubmed_by_ids(fwd_new, email=email, api_key=api_key,
                            fulltext=fulltext, cancel=cancel, progress=log)
        if fwd_new else []
    )
    for d in fwd_docs:
        d.meta["expanded"] = True
        d.meta["expand_phase"] = "forward"
    say(f"  phase 1 (cited_by): {len(fwd_docs)} citing papers → topic profile")

    profile_docs = list(seed_docs) + fwd_docs

    # ---- Phase 2: backward (references), gated by relevance -------------------
    _check_cancel(cancel)
    say("  phase 2 (references): collecting backward references to score…")
    bwd_ids = expand_pmids(
        seeds, rounds=cfg.expand_back_rounds, link="references",
        source=cfg.expand_source, max_records=cfg.expand_max,
        email=email, api_key=api_key, cancel=cancel, progress=log,
    )
    seen = have | {f"PMID:{i}" for i in fwd_new}
    bwd_new = [i for i in bwd_ids if i not in seeds and f"PMID:{i}" not in seen]
    bwd_docs = (
        fetch_pubmed_by_ids(bwd_new, email=email, api_key=api_key,
                            fulltext=fulltext, cancel=cancel, progress=log)
        if bwd_new else []
    )

    if bwd_docs:
        say(f"  scoring {len(bwd_docs)} backward reference(s) against the topic "
            f"profile…")
    kept = _top_k_relevant(profile_docs, bwd_docs, cfg, say=say)
    for d, score in kept:
        d.meta["expanded"] = True
        d.meta["expand_phase"] = "backward"
        d.meta["relevance"] = round(float(score), 4)
    say(f"  phase 2 (references): kept {len(kept)} of {len(bwd_docs)} candidates "
        f"by relevance (top_k={cfg.expand_top_k})")

    return fwd_docs + [d for d, _ in kept]


def _top_k_relevant(profile_docs, cand_docs, cfg, *, say=None):
    """Score candidates against the profile, return [(doc, score)] for the top K."""
    if not cand_docs:
        return []
    scores = _relevance_scores(profile_docs, cand_docs, cfg, say=say)
    ranked = sorted(zip(cand_docs, scores), key=lambda ds: ds[1], reverse=True)
    k = cfg.expand_top_k
    return ranked[:k] if k and k > 0 else ranked


def _relevance_scores(profile_docs, cand_docs, cfg, *, say=None) -> list[float]:
    """Cosine of each candidate to the profile. Embeddings if available, else terms."""
    try:
        return _embedding_scores(profile_docs, cand_docs, cfg, say=say)
    except ImportError:
        return _term_overlap_scores(profile_docs, cand_docs, cfg, say=say)
    except Exception as exc:  # noqa: BLE001 - never let scoring sink the run
        warnings.warn(f"embedding relevance failed ({exc}); falling back to term overlap")
        return _term_overlap_scores(profile_docs, cand_docs, cfg, say=say)


def _pseudo_negative_idx(scores, cfg, *, top_k: int) -> list[int]:
    """Indices of the worst-scoring candidates, to serve as pseudo-non-relevant.

    Returns [] when the negative term is switched off, when the pool is too
    small for its tail to mean anything, or when the tail would reach into the
    top-K of the first-pass ranking — the documents phase 2 is most likely to
    keep should not also be serving as evidence of what the topic is *not*.
    (Re-scoring can reorder the pool, so this is a strong tendency rather than
    a hard invariant; a negative is heavily penalized by its own subtraction
    and in practice does not climb back into the kept set.)
    """
    n = len(scores)
    if cfg.rocchio_gamma <= 0 or n < max(cfg.rocchio_min_candidates, 2):
        return []
    n_neg = max(1, int(round(n * cfg.rocchio_neg_frac)))
    keep = top_k if top_k and top_k > 0 else 0
    if keep and n - n_neg < keep:
        n_neg = n - keep
    if n_neg < 1:
        return []
    return sorted(range(n), key=lambda i: scores[i])[:n_neg]


def _embedding_scores(profile_docs, cand_docs, cfg, *, say=None) -> list[float]:
    """PubMedBERT cosine to the Rocchio query vector. ImportError if no embed extra."""
    import numpy as np

    from .embeddings import embed_texts  # ImportError here triggers the term fallback

    def _unit_rows(mat):
        return mat / np.clip(np.linalg.norm(mat, axis=1, keepdims=True), 1e-9, None)

    def _unit(vec):
        return vec / max(float(np.linalg.norm(vec)), 1e-9)

    prof_texts = [d.content for d in profile_docs] or [""]
    P = _unit_rows(embed_texts(prof_texts, cfg))
    q = _unit(P.mean(axis=0))

    C = _unit_rows(embed_texts([d.content for d in cand_docs], cfg))
    scores = C @ q

    # Rocchio negative term. The candidates are embedded once and reused, so the
    # second pass costs a matrix multiply, not another model call.
    neg = _pseudo_negative_idx(scores.tolist(), cfg, top_k=cfg.expand_top_k)
    if neg:
        q = _unit(q - cfg.rocchio_gamma * _unit(C[neg].mean(axis=0)))
        scores = C @ q
        if say:
            say(f"  relevance: subtracted a negative centroid from "
                f"{len(neg)} low-scoring candidate(s) (gamma={cfg.rocchio_gamma})")
    return [float(x) for x in scores]


def _term_overlap_scores(profile_docs, cand_docs, cfg, *, say=None) -> list[float]:
    """Cosine between the Rocchio term vector and each candidate's term set."""
    from .ner import extract_entities

    prof_ent = extract_entities(profile_docs, cfg)
    cand_ent = extract_entities(cand_docs, cfg)
    cand_terms = [set(cand_ent.get(d.doc_id, [])) for d in cand_docs]

    # Weight profile terms by how many profile docs mention them (a term shared
    # across the topic's papers is more characteristic than a one-off).
    prof_vec: Counter = Counter()
    for terms in prof_ent.values():
        prof_vec.update(set(terms))

    def _unit(vec: Counter) -> dict[str, float]:
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {t: v / norm for t, v in vec.items()}

    def _score(q: dict[str, float]) -> list[float]:
        qnorm = math.sqrt(sum(v * v for v in q.values())) or 1.0
        return [sum(q.get(t, 0.0) for t in terms) / (qnorm * (math.sqrt(len(terms)) or 1.0))
                for terms in cand_terms]

    q = _unit(prof_vec)
    scores = _score(q)

    # Rocchio negative term, same construction as the embedding path.
    neg = _pseudo_negative_idx(scores, cfg, top_k=cfg.expand_top_k)
    if neg:
        neg_vec: Counter = Counter()
        for i in neg:
            neg_vec.update(cand_terms[i])
        neg_unit = _unit(neg_vec)
        q = {t: q.get(t, 0.0) - cfg.rocchio_gamma * neg_unit.get(t, 0.0)
             for t in set(q) | set(neg_unit)}
        scores = _score(q)
        if say:
            say(f"  relevance: subtracted a negative centroid from "
                f"{len(neg)} low-scoring candidate(s) (gamma={cfg.rocchio_gamma})")
    return scores
