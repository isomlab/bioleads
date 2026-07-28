"""Rank terms by how distinctive they are to the corpus.

Raw frequency just surfaces generic words ("cell", "patient"). We instead
score each term against a *background* distribution so that what rises to the
top is what's over-represented in the topic corpus.

Methods
-------
log_odds       Monroe, Colaresi & Quinn (2008) informative-Dirichlet
               log-odds-ratio with a z-score. Robust to frequency; the field
               standard for "distinctive terms" comparisons.
hypergeometric Classic over-representation test (Fisher/hypergeometric tail).
tfidf          Corpus-internal TF-IDF; needs no background corpus.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass

from .config import Config


@dataclass
class TermScore:
    term: str
    score: float          # method-specific (z, -log10 p, or tfidf weight)
    corpus_count: int
    doc_freq: int
    bg_count: int = 0

    def as_row(self) -> dict:
        return {
            "term": self.term,
            "score": round(self.score, 4),
            "corpus_count": self.corpus_count,
            "doc_freq": self.doc_freq,
            "bg_count": self.bg_count,
        }


def _corpus_counts(entities: dict[str, list[str]]) -> tuple[Counter, Counter]:
    """Return (total term counts, document frequency) across the corpus."""
    total = Counter()
    docfreq = Counter()
    for ents in entities.values():
        total.update(ents)
        docfreq.update(set(ents))
    return total, docfreq


def load_background(path: str) -> Counter:
    """Load a background term->count map (JSON). E.g. counts over all PubMed."""
    with open(path) as f:
        return Counter(json.load(f))


def rank_terms(
    entities: dict[str, list[str]],
    cfg: Config | None = None,
    background: Counter | None = None,
) -> list[TermScore]:
    """Score and rank terms. Returns a list sorted by descending score."""
    cfg = cfg or Config()
    total, docfreq = _corpus_counts(entities)

    # frequency floor
    terms = [t for t, df in docfreq.items() if df >= cfg.min_doc_freq]
    if not terms:
        return []

    if background is None and cfg.background_path:
        background = load_background(cfg.background_path)

    method = cfg.enrichment_method
    if method == "tfidf" or background is None:
        scores = _tfidf(entities, terms)
        if background is None and method != "tfidf":
            print("[bioleads] no background corpus -> falling back to TF-IDF.")
    elif method == "log_odds":
        scores = _log_odds(total, background, terms, cfg.log_odds_prior)
    elif method == "hypergeometric":
        scores = _hypergeometric(total, background, terms)
    else:
        raise ValueError(f"unknown enrichment_method: {method}")

    ranked = [
        TermScore(term=t, score=scores[t], corpus_count=total[t],
                  doc_freq=docfreq[t], bg_count=(background or {}).get(t, 0))
        for t in terms
    ]
    ranked.sort(key=lambda r: r.score, reverse=True)
    return ranked[: cfg.top_terms]


# --------------------------------------------------------------------------- #
# Scoring methods
# --------------------------------------------------------------------------- #
def _log_odds(corpus: Counter, bg: Counter, terms, prior: float) -> dict[str, float]:
    """Monroe et al. weighted log-odds with informative Dirichlet prior.

    z_w = delta_w / sqrt(var(delta_w)), where delta is the difference in
    log-odds of word w between corpus and background, smoothed by `prior`.
    """
    n_corpus = sum(corpus.values())
    n_bg = sum(bg.values())
    a0 = prior * max(len(set(corpus) | set(bg)), 1)

    z = {}
    for w in terms:
        y_i = corpus.get(w, 0)
        y_j = bg.get(w, 0)
        a_i = prior
        l_i = math.log((y_i + a_i) / (n_corpus + a0 - y_i - a_i))
        l_j = math.log((y_j + a_i) / (n_bg + a0 - y_j - a_i))
        delta = l_i - l_j
        var = 1.0 / (y_i + a_i) + 1.0 / (y_j + a_i)
        z[w] = delta / math.sqrt(var)
    return z


def _hypergeometric(corpus: Counter, bg: Counter, terms) -> dict[str, float]:
    """Over-representation as -log10(survival prob) under hypergeometric."""
    try:
        from scipy.stats import hypergeom
    except ImportError as e:  # pragma: no cover
        raise ImportError("hypergeometric scoring needs scipy") from e

    n_corpus = sum(corpus.values())
    n_bg = sum(bg.values())
    M = n_corpus + n_bg            # population
    N = n_corpus                   # draws (corpus size)
    out = {}
    for w in terms:
        k = corpus.get(w, 0)       # successes observed in corpus
        n = corpus.get(w, 0) + bg.get(w, 0)  # total successes in population
        # P(X >= k)
        p = hypergeom.sf(k - 1, M, n, N)
        out[w] = -math.log10(max(p, 1e-300))
    return out


def _tfidf(entities: dict[str, list[str]], terms) -> dict[str, float]:
    """Corpus-level TF-IDF: mean tf-idf weight of each term across documents."""
    n_docs = len(entities)
    docfreq = Counter()
    for ents in entities.values():
        docfreq.update(set(ents))
    out = {}
    tf_total = Counter()
    for ents in entities.values():
        tf_total.update(ents)
    for w in terms:
        tf = tf_total[w]
        idf = math.log((1 + n_docs) / (1 + docfreq[w])) + 1.0
        out[w] = tf * idf
    return out


def to_dataframe(ranked: list[TermScore]):
    """Convert ranked terms to a pandas DataFrame (for CSV export)."""
    import pandas as pd
    return pd.DataFrame([r.as_row() for r in ranked])
