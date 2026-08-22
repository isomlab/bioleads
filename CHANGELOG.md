# Changelog

Notable changes to bioleads. Versions follow [semantic versioning](https://semver.org);
while the major version is 0, a minor bump may change defaults.

## 0.2.0 — 2026-08-22

### Clustering

- **Term clustering is now density-based (HDBSCAN) and picks the number of
  clusters itself.** The old KMeans path asked for a count before anyone had
  seen the terms; how many concepts a corpus splits into is a property of the
  corpus. HDBSCAN's own parameter — the smallest admissible group — is derived
  from the term count.
- Terms in no dense region are reported as **unclustered** (`cluster_id -1`)
  instead of being pushed into the nearest group: last in the GUI's Clusters
  tab, id `-1` in `term_clusters.csv`, grey in `term_clusters.html`.
- Clustering runs on a centred, PCA-reduced view of the embeddings. Term
  vectors have mean pairwise cosine ≈ 0.93, so on the raw cloud HDBSCAN
  returned one cluster of 67 terms out of 74; centring and projecting to 10
  dimensions turns that into groups that read correctly. Cluster names
  (medoids) are still chosen in the full cosine space.
- KMeans remains available: `--cluster-method kmeans` with `--n-clusters`, or
  the **Clustering** combo in the GUI, where **Clusters (k)** greys out under
  `hdbscan` because it is ignored.
- New: `--cluster-method`, `--min-cluster-size`; `Config.cluster_method`,
  `Config.min_cluster_size`. `Config.n_clusters` is now KMeans-only.

### Corpus expansion

- `bfs` is the default expansion strategy; `relevance` is one flag away
  (`--expand-strategy relevance`) and measured cleaner at equal reach.
- The relevance profile is built from the **seed documents alone**, and both
  directions (`cited_by`, `references`) are gated. Profiling on forward citers
  measured worse on every count — see `docs/benchmark.md`.
- The relevance gate gained an optional **negative (Rocchio) term**
  (`rocchio_gamma`, `rocchio_neg_frac`, `rocchio_min_candidates`) and an
  optional centring of the scoring space (`relevance_center`, off: it did not
  help selection over 40 systematic reviews).
- Kept documents are returned sorted by the relevance score that was reported.
- Everything fetched to build or expand a citation graph — iCite records and
  the ELink/iCite link lookups — is cached under `~/.cache/bioleads` for 30
  days (`citation_cache_days`), so a repeat run costs no network.

### Citation networks

- Author networks are built from **senior authors** (last byline) only.
- Authors can be ranked and drawn by **output** as well as by citations
  received, with its own floor (`min_author_papers`).
- Weakly connected nodes can be dropped before anything is written, from the
  rankings as well as the pictures (`min_paper_degree`, `min_author_degree`).
- The 3D layouts were reworked: isotropic shells outward from the busiest
  node, ranked by degree, and no longer collapsing to a plane.

### Outputs

- New `pmids.txt`: every PMID in the corpus, one per line, ready to paste into
  PubMed or feed back in as `--pmids @file`.
- **Removed** the term co-occurrence network file. The graph is still built and
  ABC discovery still runs over it; it is simply not written out.

### GUI

- A run's outputs moved out of the action bar into their own **Outputs** tab.
- Fields a run would ignore are greyed out rather than left live, so tuning a
  setting that will be discarded is visible before the run.
- Removed three inputs that could not do what they claimed.
- The window opens at the size the form needs; a failing handler no longer
  kills the event pump, and run state no longer outlives its run.

### Performance

- PubMedBERT is loaded once and held, instead of re-reading ~400 MB of weights
  on every embed call.

### Documentation

- `docs/how_it_works.md`: a stage-by-stage account of the pipeline with
  figures, including the geometry of the relevance gate.
- `docs/benchmark.md`: the systematic-review benchmark, including the results
  that falsified the original expansion design.

## 0.1.0 — 2026-07-28

Initial release: PubMed/PMC and reference-file ingestion, optional scispaCy
NER, corpus-internal TF-IDF term ranking, PMI-filtered co-occurrence, Swanson
ABC hypothesis candidates, optional PubMedBERT clustering and citation
networks, a Tkinter GUI, and a Bioconda recipe.
