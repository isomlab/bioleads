"""Named-entity extraction.

Primary path: scispaCy biomedical NER (install with `bioleads[ner]`).
Fallback: a lightweight regex/stopword term extractor so the pipeline runs
end-to-end without model downloads (useful for tests and quick looks).
"""
from __future__ import annotations

import re
from functools import lru_cache

from .config import Config
from .sources import Document, _check_cancel, _sayer

# Minimal stopword set for the fallback extractor. The scispaCy path doesn't
# need this since it returns curated entity spans.
_FALLBACK_STOP = {
    "the", "and", "for", "with", "that", "this", "these", "those", "from",
    "were", "was", "are", "have", "has", "had", "not", "but", "all", "can",
    "may", "our", "their", "its", "which", "when", "than", "also", "been",
    "using", "used", "use", "study", "studies", "results", "result", "showed",
    "show", "data", "analysis", "patients", "patient", "cells", "cell",
    "expression", "levels", "level", "associated", "between", "within",
    "however", "thus", "therefore", "both", "more", "most", "such", "into",
    "during", "via", "two", "one", "three", "we", "in", "of", "to", "a", "an",
    "is", "by", "on", "as", "at", "or", "be", "it",
}


@lru_cache(maxsize=4)
def _load_scispacy(model: str):
    """Load and cache a scispaCy model; return None if unavailable."""
    try:
        import spacy
        return spacy.load(model)
    except Exception:  # noqa: BLE001 - model missing / spacy not installed
        return None


def _normalize(term: str) -> str:
    term = term.lower().strip()
    term = re.sub(r"\s+", " ", term)
    term = term.strip(" .,;:()[]{}\"'")
    return term


def extract_entities(
    docs: list[Document], cfg: Config | None = None, *, progress=None, cancel=None
) -> dict[str, list[str]]:
    """Return {doc_id: [entity, ...]} for every document.

    Entities are normalized (lowercased, whitespace-collapsed) when
    cfg.normalize_entities is True so that downstream counting merges variants.

    Pass `progress` (callable taking one str) to receive periodic "processed
    X/N" updates — extraction over a large corpus (especially with scispaCy) is
    otherwise silent for a long time. Pass `cancel` (a threading.Event) to bail
    out cooperatively at those same checkpoints.
    """
    cfg = cfg or Config()
    say = _sayer(progress)
    nlp = _load_scispacy(cfg.scispacy_model)
    if nlp is not None:
        say(f"  NER engine: scispaCy ({cfg.scispacy_model})")
        return _extract_scispacy(docs, nlp, cfg, say=say, cancel=cancel)
    say("  NER engine: regex fallback (install the 'ner' extra for scispaCy)")
    return _extract_fallback(docs, cfg, say=say, cancel=cancel)


def _keep(term: str, cfg: Config) -> bool:
    return len(term) >= cfg.min_entity_len and term not in cfg.stopwords


def _report_step(n: int) -> int:
    """Docs between progress updates: ~20 updates, but no finer than every 25."""
    return max(25, n // 20) if n else 1


def _extract_scispacy(docs, nlp, cfg, *, say, cancel=None) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    texts = [d.content for d in docs]
    n = len(docs)
    step = _report_step(n)
    for i, (d, doc) in enumerate(zip(docs, nlp.pipe(texts, batch_size=32)), start=1):
        ents = []
        for ent in doc.ents:
            term = _normalize(ent.text) if cfg.normalize_entities else ent.text.strip()
            if _keep(term, cfg):
                ents.append(term)
        out[d.doc_id] = ents
        if i % step == 0 or i == n:
            _check_cancel(cancel)
            say(f"  NER: {i}/{n} document(s) processed…")
    return out


# Candidate terms in the fallback: capitalized tokens, gene-like symbols
# (e.g. TP53, IL6R), and 2-3 word phrases. Crude but useful without models.
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}")
_SYMBOL_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,}[0-9][A-Za-z0-9]*\b")


def _extract_fallback(docs, cfg, *, say=None, cancel=None) -> dict[str, list[str]]:
    say = say or (lambda _m: None)
    n = len(docs)
    step = _report_step(n)
    out: dict[str, list[str]] = {}
    for i, d in enumerate(docs, start=1):
        text = d.content
        terms: list[str] = []

        # gene/protein-like symbols, kept in original case for recognizability
        for m in _SYMBOL_RE.findall(text):
            t = m.lower() if cfg.normalize_entities else m
            if _keep(t, cfg):
                terms.append(t)

        # general content words
        for tok in _TOKEN_RE.findall(text):
            t = _normalize(tok)
            if t in _FALLBACK_STOP:
                continue
            if _keep(t, cfg):
                terms.append(t)

        out[d.doc_id] = terms
        if i % step == 0 or i == n:
            _check_cancel(cancel)
            say(f"  NER: {i}/{n} document(s) processed…")
    return out
