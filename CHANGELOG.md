# Changelog

Notable changes to bioleads. Versions follow [semantic versioning](https://semver.org);
while the major version is 0, a minor bump may change defaults.

## Unreleased

### The `latest` symlink is gone; the timestamp is the pointer

- **No more `<out>/latest`.** Runs are identified by the timestamp in their
  folder name and nothing else. The names sort chronologically as plain text,
  so the newest run is the last one listed.
- **A stale `latest` left by 0.3.x is removed on the next run**, with a line
  saying so, because nothing writes it any more and it would otherwise sit
  there pointing at whatever ran last before the upgrade.
- **A real file or directory named `latest` is left alone.** Only a symlink is
  removed.
- Why: a symlink is a second name for a run, and a second name is a way to be
  wrong about which run you are looking at. A file opened through it reports a
  path that is not where it lives, and it goes stale silently.
- `runs.update_latest` is replaced by `runs.prune_latest_link`.

### Every network is coloured by query-term containment, not just the 2D one

- **All five network views now colour by the query when a `--pubmed` search was
  run**: the paper citation network in 2D and 3D, the matches-only subnetwork,
  and both senior-author networks in 2D and 3D. Previously only the 2D paper
  network was coloured, so the same corpus told different stories depending on
  which file was opened.
- **An author is coloured by their single best-matching paper**, not by pooling
  terms across their papers. An author with one paper naming every term is
  green; an author with two papers naming one term each is amber, because
  pooling would claim a paper that does not exist. The hover gives `k of n`
  papers naming any term, and the legend says so rather than leaving it to be
  assumed.
- **A run with no text query is unchanged.** The 3D views keep their
  citation-count colorscale instead of being painted the "nothing matched"
  colour, which is what returning no colours rather than a dict of greys buys.
- `querymatch.annotate_author_graph` is the API. It needs
  `graph["paper_senior"]`, which `build_author_citation_graph` now carries.
- **Fixed in passing: GraphML writes of the author graph.** That `paper_senior`
  map is a dict on `graph`, which GraphML cannot store, so the pyvis fallback
  raised on a core-only install — the one the conda recipe builds. Both
  fallbacks now write a copy with graph-level attributes dropped.

### Fixed: nothing was ever coloured on a real PubMed search

- **Every node came out grey on a `--pubmed` run, whatever the query.** A paper
  with the search term in both its title and its abstract was classified as
  containing none of it.
- **Cause: PubMed records did not carry their PMID where the matcher looked.**
  `_record_to_document` put the PMID in `doc_id` but not in `meta`, and
  `annotate_citation_graph` read only `meta`, so its lookup table was empty and
  every node fell through to "no document, therefore no match".
- Fixed in both places. PubMed records now carry `meta["pmid"]`, and the matcher
  falls back to a `PMID:` doc_id the way `citations._doc_pmid` and
  `sources.document_pmids` already did.
- **The tests passed throughout**, because every fixture built its documents by
  hand with a `meta` pmid, which is what the code read rather than what the
  PubMed path produced. The regression tests now build documents through
  `_record_to_document` itself.

### A second citation network holding only the papers that match

- **`citation_network_matches.html`**, written beside the full network after a
  `--pubmed` text search: the **union of the green and amber nodes**, meaning
  every paper whose own title or abstract contains **all** of the query's terms
  or **some** of them, with the citations between them.
- **Edges are induced**, so a citation is kept only when both ends matched.
  Citation paths between matching papers often run through papers that matched
  nothing, so this network is routinely sparser and more fragmented than the
  same nodes look inside the full graph. **That is a property of the selection,
  not a finding about the literature**, and it is the one thing to understand
  before reading anything into it.
- **`in_corpus_citations` is carried over, not recomputed**, so node sizes stay
  comparable with the full network rather than being rescaled against the
  subset.
- Written only when a text query produced terms and at least one paper matched.
  A `--pmids` or `--refs` run has nothing to subset, and a query no paper
  carries writes no file rather than an empty network. The Outputs tab lists
  the row greyed in both cases.
- `querymatch.matching_subgraph(g, states=("all", "partial"))` is the API;
  pass `("all",)` for the stricter set.

### `querymatch` has tests

- **66 tests for the query-term matcher**, which had none. Every case in the
  field-tag section is a bug that actually shipped and was caught by eye:
  `Isom DG[au]` leaking the bare word `Isom`, and `"Nature"[ta]` leaking the
  journal name, both because a tag binds to the whole term before it and the
  query has to be cut at booleans and parentheses before tags are read.
- Also covered: truncation (`autophag*` matches `autophagic`, `autophagy` does
  not), phrases matching across a line break in an abstract, regex
  metacharacters in a term staying literal, and **the GraphML contract** that
  the node attributes are a string and two ints, never a list and never `None`.
- **Checked by mutation, not just by passing.** Reverting the parser to the
  shipped bug fails six of them. A test that has never been seen to fail is not
  evidence.

### Citation-network nodes are coloured by whether the paper contains the query

- **After a `--pubmed` text search, each paper in `citation_network.html` is
  coloured by how many of the query's terms its title and abstract actually
  contain**: green for all, amber for some, grey for none. The hover says which
  terms were found, and flags a paper added by citation expansion.
- **This is not the same as "was it a hit".** PubMed can match on a MeSH term,
  on automatic term mapping, or on full text never fetched here, so a grey node
  can be a perfectly good hit. `--expand` also adds papers that never went
  through the query. The legend in the file says so rather than leaving the
  colours to be misread, and that crossing is the interesting part: a grey seed
  matched on something else, and a coloured expansion-added paper is one the
  search arguably should have returned.
- Matching is **literal**, case-insensitive, on word boundaries, so `autophagy`
  does not match `autophagic`. PubMed's truncation operator works: `autophag*`
  matches both. Quoted phrases stay phrases.
- Field-tagged terms that cannot appear in an abstract, such as `Isom DG[au]` or
  `"Nature"[ta]`, are dropped rather than searched for and reported as misses.
- `--pmids` and `--refs` runs have no query, so their graphs stay uncoloured
  rather than showing every paper as "contains none".
- Nodes carry `query_match`, `query_match_count` and `query_terms_matched`, all
  GraphML-safe, so the pyvis-less fallback keeps the information too.

## 0.3.0 — 2026-09-29

### Every run gets its own folder

- **`--out` is now the results root, and each run writes to a timestamped
  subfolder of it.** It was written to directly with `exist_ok=True`, so a
  second run silently overwrote the first, and nothing on disk said which
  settings produced the files that survived.
- The folder is named from the query, `2026-09-29_143205_gpcr-allosteric`.
  Two runs starting in the same second get `-2`, `-3` rather than colliding.
  `--run-name` labels one yourself.
- **`run.json` in each folder** records the bioleads version, the inputs, the
  fully resolved `Config` and the result counts, with output names relative so
  the folder survives being moved or sent on. **Config fields that look like
  credentials are redacted**, so an Entrez API key cannot reach disk.
- **`<root>/latest`** symlinks to the newest run. Best effort: a platform that
  refuses symlinks loses the convenience, not the run.
- `PipelineResult.run_dir` is the folder actually written. The GUI follows it,
  so clustering started after a run lands in that run's folder rather than the
  root.
- **`--no-run-dir`** (or `unique_run_dir=False`) restores the old flat layout.

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
