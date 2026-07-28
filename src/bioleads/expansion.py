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
    kept = _top_k_relevant(profile_docs, bwd_docs, cfg)
    for d, score in kept:
        d.meta["expanded"] = True
        d.meta["expand_phase"] = "backward"
        d.meta["relevance"] = round(float(score), 4)
    say(f"  phase 2 (references): kept {len(kept)} of {len(bwd_docs)} candidates "
        f"by relevance (top_k={cfg.expand_top_k})")

    return fwd_docs + [d for d, _ in kept]


def _top_k_relevant(profile_docs, cand_docs, cfg):
    """Score candidates against the profile, return [(doc, score)] for the top K."""
    if not cand_docs:
        return []
    scores = _relevance_scores(profile_docs, cand_docs, cfg)
    ranked = sorted(zip(cand_docs, scores), key=lambda ds: ds[1], reverse=True)
    k = cfg.expand_top_k
    return ranked[:k] if k and k > 0 else ranked


def _relevance_scores(profile_docs, cand_docs, cfg) -> list[float]:
    """Cosine of each candidate to the profile. Embeddings if available, else terms."""
    try:
        return _embedding_scores(profile_docs, cand_docs, cfg)
    except ImportError:
        return _term_overlap_scores(profile_docs, cand_docs, cfg)
    except Exception as exc:  # noqa: BLE001 - never let scoring sink the run
        warnings.warn(f"embedding relevance failed ({exc}); falling back to term overlap")
        return _term_overlap_scores(profile_docs, cand_docs, cfg)


def _embedding_scores(profile_docs, cand_docs, cfg) -> list[float]:
    """PubMedBERT cosine to the (unit) mean profile vector. Raises ImportError if no embed extra."""
    import numpy as np

    from .embeddings import embed_texts  # ImportError here triggers the term fallback

    def _unit_rows(mat):
        return mat / np.clip(np.linalg.norm(mat, axis=1, keepdims=True), 1e-9, None)

    prof_texts = [d.content for d in profile_docs] or [""]
    P = _unit_rows(embed_texts(prof_texts, cfg))
    centroid = P.mean(axis=0)
    centroid = centroid / max(float(np.linalg.norm(centroid)), 1e-9)

    C = _unit_rows(embed_texts([d.content for d in cand_docs], cfg))
    return [float(x) for x in (C @ centroid)]


def _term_overlap_scores(profile_docs, cand_docs, cfg) -> list[float]:
    """Cosine between the profile term vector (doc-frequency weighted) and each candidate."""
    from .ner import extract_entities

    prof_ent = extract_entities(profile_docs, cfg)
    cand_ent = extract_entities(cand_docs, cfg)

    # Weight profile terms by how many profile docs mention them (a term shared
    # across the topic's papers is more characteristic than a one-off).
    prof_vec: Counter = Counter()
    for terms in prof_ent.values():
        prof_vec.update(set(terms))
    prof_norm = math.sqrt(sum(v * v for v in prof_vec.values())) or 1.0

    scores = []
    for d in cand_docs:
        cand_terms = set(cand_ent.get(d.doc_id, []))
        dot = sum(prof_vec.get(t, 0) for t in cand_terms)
        cand_norm = math.sqrt(len(cand_terms)) or 1.0
        scores.append(dot / (prof_norm * cand_norm))
    return scores
