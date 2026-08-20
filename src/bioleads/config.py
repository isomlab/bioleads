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
    # Terms are scored by corpus-level TF-IDF; there is no other method and no
    # background corpus (see enrichment.py for what was removed and why).
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
    #   "relevance" -> pseudo-relevance-feedback expansion. The topic profile is
    #       built from the seed documents alone; both directions (cited_by, then
    #       references) are collected, scored against it, and cut to the
    #       expand_top_k most similar. Relevance uses NER term-overlap,
    #       auto-upgrading to PubMedBERT cosine when the `embed` extra is
    #       installed.
    #       Until 2026-08 the profile also contained the forward citers, which
    #       were then added ungated. Benchmarking against systematic reviews
    #       (docs/benchmark.md) measured that worse on every count, so both
    #       directions are now gated and only the seeds define the topic.
    expand_strategy: str = "bfs"
    expand_fwd_rounds: int = 1   # cited_by depth
    expand_back_rounds: int = 1  # references depth
    # Keep this many most-relevant papers *per direction*. Swept on the
    # systematic-review benchmark (12 reviews; docs/benchmark.md): K=25 has the
    # best median F1 (0.0948), K=50 the best paired record (best in 7 of 12) at
    # F1 0.0927, and K~100-200 keeps 76-92% of BFS's recall at 2-3x its median
    # precision while retrieving 93-96% less material — the region to prefer
    # when the corpus feeds ABC discovery, which needs the intermediate concepts
    # present at all. Recall matches BFS exactly at K=800, still on 87% less.
    expand_top_k: int = 50

    # --- Relevance gating (Rocchio) ---
    # Phase 2 scores each backward reference against the phase-1 topic profile.
    # With rocchio_gamma > 0 the profile carries a *negative* term as well:
    # candidates are ranked once against the positive centroid, the worst
    # rocchio_neg_frac of them are taken as pseudo-non-relevant, and their
    # centroid is subtracted from the query vector —
    #     q = normalize(centroid(profile) - gamma * centroid(worst candidates))
    # — so the gate is pushed *away* from the off-topic bulk of a reference list
    # instead of only toward the topic. This is the negative half of Rocchio
    # (1971); the beta term is implicit, since q is renormalized and only the
    # gamma/beta ratio matters.
    #
    # The negatives come from the candidate pool itself: a reference list is
    # mostly off-topic by assumption (that is why phase 2 exists), so its
    # low-scoring tail is a supply of *hard* negatives — citation-adjacent but
    # off-topic — at no extra fetch or embedding cost. Set gamma to 0 for the
    # plain positive-only centroid.
    rocchio_gamma: float = 0.25
    rocchio_neg_frac: float = 0.25   # fraction of the tail used as negatives
    rocchio_min_candidates: int = 8  # below this, the tail is too small to trust

    # Mean-pooled PubMedBERT vectors are strongly anisotropic: ~99.5% of every
    # unit document vector is one direction shared by all biomedical text, so
    # candidates are ranked on the ~10% that remains. With relevance_center on,
    # the candidate pool's mean is subtracted from the profile and the
    # candidates before scoring, which removes that shared component and lets
    # the gate work on the part that distinguishes papers. Embedding path only;
    # the NER term-overlap fallback ignores it.
    relevance_center: bool = False

    # --- Citation network ---
    # Build a directed paper-to-paper citation graph over the corpus's
    # PMID-bearing records (via NIH iCite) and rank papers by how often they're
    # cited — both within the corpus (in-degree) and globally (iCite
    # citation_count). On by default; needs a network round-trip to iCite.
    do_citation_network: bool = True
    # Drop weakly-connected nodes from the stage-8 graphs before anything is
    # written. A node is kept when its total degree — citations received from
    # corpus papers plus citations it makes to them (for authors, the number of
    # distinct authors it cites or is cited by) — is at least this. 0 keeps
    # everything; 1 drops the isolated nodes, which are usually the bulk of a
    # sparse corpus. Applied before max_graph_nodes, and to the rankings as
    # well as the pictures, so every stage-8 output shows the same set.
    #
    # The two graphs get their own threshold because they are not on the same
    # scale: the author graph is a projection, in which one paper→paper link
    # becomes an edge between every pair of their authors, so author degrees run
    # an order of magnitude higher. A number that meaningfully thins papers
    # barely touches authors.
    min_paper_degree: int = 0
    min_author_degree: int = 0

    # --- Misc ---
    seed: int = 0
    stopwords: set[str] = field(default_factory=set)
