"""Rank terms by how much weight they carry in the corpus.

Raw frequency just surfaces generic words ("cell", "patient"), so terms are
scored by corpus-level TF-IDF: total count damped by how many documents the
term appears in, which pushes down anything that shows up everywhere.

This used to offer two other methods — Monroe et al. weighted log-odds and a
hypergeometric over-representation test — that scored the corpus against a
*background* term-count distribution over some neutral reference collection.
Both are gone, along with the background itself. Nothing shipped a background,
nothing could load one, so in every real run they fell back to TF-IDF while
labelling the output as z-scores or p-values. Restoring them means restoring a
background worth scoring against, not just the arithmetic.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from .config import Config


@dataclass
class TermScore:
    term: str
    score: float          # mean TF-IDF weight
    corpus_count: int
    doc_freq: int

    def as_row(self) -> dict:
        return {
            "term": self.term,
            "score": round(self.score, 4),
            "corpus_count": self.corpus_count,
            "doc_freq": self.doc_freq,
        }


def _corpus_counts(entities: dict[str, list[str]]) -> tuple[Counter, Counter]:
    """Return (total term counts, document frequency) across the corpus."""
    total = Counter()
    docfreq = Counter()
    for ents in entities.values():
        total.update(ents)
        docfreq.update(set(ents))
    return total, docfreq


def rank_terms(
    entities: dict[str, list[str]],
    cfg: Config | None = None,
    *,
    progress=None,
) -> list[TermScore]:
    """Score and rank terms by TF-IDF. Returns a list sorted by descending score."""
    cfg = cfg or Config()
    total, docfreq = _corpus_counts(entities)

    # frequency floor
    terms = [t for t, df in docfreq.items() if df >= cfg.min_doc_freq]
    if not terms:
        return []

    scores = _tfidf(entities, terms)
    ranked = [
        TermScore(term=t, score=scores[t], corpus_count=total[t],
                  doc_freq=docfreq[t])
        for t in terms
    ]
    ranked.sort(key=lambda r: r.score, reverse=True)
    return ranked[: cfg.top_terms]


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
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
