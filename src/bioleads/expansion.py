"""Relevance-guided two-phase citation expansion (pseudo-relevance feedback).

Both citation directions are noisy, so neither is trusted and both are filtered:

* The **profile** is built from the seed documents alone — the only papers we
  know are on topic, because the user chose them.
* **Forward** links (`cited_by`) and **backward** links (`references`) are each
  collected, scored against that profile, and cut to the top-K.

Relevance is NER term-overlap cosine, auto-upgraded to PubMedBERT cosine when
the `embed` extra is installed.

This replaces an earlier design that built the profile from the seeds *plus*
their forward citers and passed every citer through ungated, on the theory that
citing papers converge on a seed's topic. Benchmarking against systematic
reviews (see docs/benchmark.md) measured that theory false: forward citers were
the *less* precise direction, they made up ~95% of the returned volume, and
including them in the profile actively hurt. Profiling on seeds alone and gating
both directions took 47,974 retrieved documents at 0.36% precision to 975 at
10.56%, and at a high enough top-K it matches plain BFS recall while retrieving
87% less.

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
    """Return new Documents to append: the top-K relevant of each direction.

    `seed_docs` are the already-loaded documents; their PMIDs seed both
    directions and their text — and only their text — forms the topic profile.
    Network failures in either direction are non-fatal: whichever succeeds still
    contributes.
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

    # The profile is the seeds themselves: the only documents known to be on
    # topic. Citers were once folded in here; benchmarking showed that hurt.
    profile_docs = list(seed_docs)
    seen = set(have)
    added: list[Document] = []

    def _collect(phase: str, link: str, rounds: int):
        """Fetch one direction's candidates and keep the top-K by relevance."""
        _check_cancel(cancel)
        say(f"  {phase} ({link}): collecting candidates from {len(seeds)} seed(s)…")
        ids = expand_pmids(
            seeds, rounds=rounds, link=link,
            source=cfg.expand_source, max_records=cfg.expand_max,
            email=email, api_key=api_key, cancel=cancel, progress=log,
        )
        new = [i for i in ids if i not in seeds and f"PMID:{i}" not in seen]
        docs = (
            fetch_pubmed_by_ids(new, email=email, api_key=api_key,
                                fulltext=fulltext, cancel=cancel, progress=log)
            if new else []
        )
        if not docs:
            say(f"  {phase} ({link}): no new candidates.")
            return
        say(f"  scoring {len(docs)} {link} candidate(s) against the seed profile…")
        kept = _top_k_relevant(profile_docs, docs, cfg, say=say)
        for d, score in kept:
            d.meta["expanded"] = True
            d.meta["expand_phase"] = phase
            d.meta["relevance"] = round(float(score), 4)
            seen.add(d.doc_id)
        added.extend(d for d, _ in kept)
        say(f"  {phase} ({link}): kept {len(kept)} of {len(docs)} candidate(s) "
            f"by relevance (top_k={cfg.expand_top_k})")

    # Forward first only so its keeps are de-duplicated out of the backward
    # pool; neither direction is privileged any more.
    _collect("forward", "cited_by", cfg.expand_fwd_rounds)
    _collect("backward", "references", cfg.expand_back_rounds)
    return added


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
