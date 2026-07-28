"""bioleads — mine biomedical literature for candidate biological leads.

Pipeline: documents -> entities (NER) -> enrichment vs. background ->
co-occurrence network -> Swanson ABC hypothesis candidates, with optional
PubMedBERT embeddings for semantic clustering of terms.
"""
from __future__ import annotations

from .sources import (
    Document,
    load_pdfs,
    fetch_pubmed,
    fetch_pubmed_by_ids,
    parse_pmid_input,
    load_refs,
    expand_pmids,
    load_documents,
)
from .ner import extract_entities
from .enrichment import rank_terms
from .cooccurrence import build_cooccurrence, write_graph_html, write_graph_html_3d
from .citations import (
    build_citation_graph,
    build_author_citation_graph,
    write_citation_html,
    write_citation_html_3d,
    write_author_html,
    write_author_html_3d,
    most_cited,
)
from .discovery import abc_candidates
from .embeddings import cluster_terms, TermCluster, write_cluster_scatter
from .expansion import relevance_guided_expand
from .pipeline import run_pipeline, PipelineResult

__version__ = "0.1.0"

__all__ = [
    "Document",
    "load_pdfs",
    "fetch_pubmed",
    "fetch_pubmed_by_ids",
    "parse_pmid_input",
    "load_refs",
    "expand_pmids",
    "load_documents",
    "extract_entities",
    "rank_terms",
    "build_cooccurrence",
    "write_graph_html",
    "write_graph_html_3d",
    "build_citation_graph",
    "build_author_citation_graph",
    "write_citation_html",
    "write_citation_html_3d",
    "write_author_html",
    "write_author_html_3d",
    "most_cited",
    "abc_candidates",
    "cluster_terms",
    "TermCluster",
    "write_cluster_scatter",
    "relevance_guided_expand",
    "run_pipeline",
    "PipelineResult",
    "__version__",
]
