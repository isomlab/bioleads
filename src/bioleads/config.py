"""Central configuration for the bioleads pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Config:
    # --- NER ---
    # scispaCy model used when available. en_core_sci_sm is the small
    # biomedical model; en_core_sci_lg / en_ner_bionlp13cg_md give better recall.
    scispacy_model: str = "en_core_sci_sm"
    # Minimum entity length (chars) to keep; filters single-letter noise.
    min_entity_len: int = 3
    # Lowercase + lemmatize-style normalization for entity matching.
    normalize_entities: bool = True

    # --- Enrichment ---
    # Scoring method for term ranking against the background corpus.
    #   "log_odds"      -> Monroe et al. informative Dirichlet log-odds (robust)
    #   "hypergeometric"-> classic over-representation p-value
    #   "tfidf"         -> simple corpus-level TF-IDF (no background needed)
    enrichment_method: str = "log_odds"
    log_odds_prior: float = 0.01
    min_doc_freq: int = 2          # term must appear in >= this many docs
    top_terms: int = 200           # how many ranked terms to keep

    # --- Co-occurrence ---
    cooccurrence_window: str = "document"  # "document" or "sentence"
    min_cooccurrence: int = 2              # min co-mentions to draw an edge
    # Significance test for an edge: keep pairs whose co-occurrence exceeds
    # chance by this PMI threshold (set None to keep raw counts only).
    min_pmi: float | None = 0.0
    max_graph_nodes: int = 150

    # --- Embeddings (PubMedBERT) ---
    embed_model: str = "microsoft/BiomedNLP-PubMedBERT-base-uncased-abstract-fulltext"
    embed_batch_size: int = 16
    n_clusters: int = 12
    # Cluster ranked terms in PubMedBERT space, write term_clusters.csv, and
    # color the co-occurrence graph by cluster. Off by default — needs the
    # `embed` extra and downloads a model on first use.
    do_clustering: bool = False

    # --- Swanson ABC discovery ---
    # A and C are candidate endpoints (e.g. drug/phenotype); B are linking
    # intermediates. A-C pair is a candidate iff they share >= min_b_links
    # intermediates yet (near) never co-occur directly.
    min_b_links: int = 2
    max_direct_cooccurrence: int = 0
    top_candidates: int = 100

    # --- PubMed fetching ---
    entrez_email: str = "disom.biophysics@gmail.com"
    entrez_api_key: str | None = None
    pubmed_retmax: int = 500
    # Upgrade open-access PubMed Central articles to full body text (intro/
    # methods/results) when available; others fall back to title + abstract.
    pubmed_fulltext: bool = False

    # --- Citation expansion (snowballing) ---
    # Grow the corpus from the PMID-bearing seeds by following citation links
    # for this many rounds (0 = off). "references" = papers each seed cites
    # (backward); "cited_by" = papers that cite each seed (forward); "both" =
    # union of the two (backward refs only resolve for PMC-indexed seeds).
    # expand_source picks the citation backend(s): "all" (default) unions NCBI
    # ELink + NIH iCite for the broadest recall (and degrades gracefully if one
    # is down); "ncbi" or "icite" force a single backend.
    expand_rounds: int = 0
    expand_link: str = "both"
    expand_source: str = "all"
    expand_max: int = 1000  # cap on total PMIDs (seeds + discovered)

    # Expansion strategy:
    #   "bfs"       -> plain breadth-first snowball along expand_link (above).
    #   "relevance" -> two-phase, pseudo-relevance-feedback expansion:
    #       Phase 1 follows cited_by (forward; converges on the seed topic) and
    #       builds a topic profile from the seeds + their citers. Phase 2 follows
    #       references (backward; topically diffuse) but keeps only the
    #       expand_top_k candidates most similar to that profile. Relevance uses
    #       NER term-overlap, auto-upgrading to PubMedBERT cosine when the
    #       `embed` extra is installed.
    expand_strategy: str = "bfs"
    expand_fwd_rounds: int = 1   # phase-1 (cited_by) depth
    expand_back_rounds: int = 1  # phase-2 (references) depth
    expand_top_k: int = 50       # keep this many most-relevant backward papers

    # --- Citation network ---
    # Build a directed paper-to-paper citation graph over the corpus's
    # PMID-bearing records (via NIH iCite) and rank papers by how often they're
    # cited — both within the corpus (in-degree) and globally (iCite
    # citation_count). On by default; needs a network round-trip to iCite.
    do_citation_network: bool = True

    # --- Misc ---
    background_path: str | None = None  # path to background term-freq json
    seed: int = 0
    stopwords: set[str] = field(default_factory=set)
