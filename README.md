# bioleads

Mine biomedical literature for candidate biological leads. Given a set of papers
(local PDFs and/or a PubMed query), `bioleads`:

1. **Extracts entities** — genes, diseases, chemicals, phenotypes — with
   scispaCy biomedical NER (regex fallback if models aren't installed).
2. **Ranks distinctive terms** — scores each term against a *background* corpus
   so what surfaces is over-represented in your topic, not just frequent. Uses
   weighted log-odds (Monroe et al.), a hypergeometric over-representation test,
   or TF-IDF.
3. **Builds a co-occurrence network** — entities linked when they co-mention
   more than chance predicts (PMI-weighted), exported as an interactive HTML graph.
4. **Generates hypothesis candidates** — Swanson-style literature-based discovery
   (the ABC model): A–C pairs that share intermediates B but never co-occur
   directly are flagged as hidden associations worth investigating.

Optional PubMedBERT embeddings cluster terms semantically so synonyms group together.

It can also **map the citation network** of your corpus (`--citations`): a directed
paper→paper graph (via NIH iCite) that ranks the most-cited papers both *within
your set* (in-degree — the work your corpus is built on) and *globally* (iCite's
citation count across all of PubMed).

## Install

```bash
pip install -e .            # core (runs with regex NER + TF-IDF)
pip install -e ".[all]"     # PDF, PubMed, scispaCy NER, embeddings, viz
```

Extras: `pdf`, `pubmed`, `ner`, `embed`, `viz`, `dev`. For real NER also fetch a model:

```bash
pip install https://s3-us-west-2.amazonaws.com/ai2-s2-scispacy/releases/v0.5.4/en_core_sci_sm-0.5.4.tar.gz
```

## Environment

The quickest way to get a working setup (including the GUI) is the bundled
conda environment:

```bash
cd <repo root>                      # the pip block installs "-e ."
conda env create -f environment.yml
conda activate bioleads
```

This gives you Python 3.11 plus PDF input, PubMed/PMC fetching, citation
expansion, the interactive graph viz, the Tkinter GUI, and the test suite
(`pdf,pubmed,viz,dev`). Tkinter ships with the conda-forge Python build, so
`bioleads-gui` works out of the box — no extra dependency beyond the pipeline's.

Each extra unlocks a feature; install only what you need:

| Extra    | Enables                                                            |
|----------|-------------------------------------------------------------------|
| *(core)* | regex NER + TF-IDF ranking, co-occurrence, ABC discovery          |
| `pdf`    | `--pdf` (PyMuPDF text extraction)                                 |
| `pubmed` | `--pubmed`, `--pmids`, `--refs`, `--fulltext`, `--expand`         |
| `viz`    | interactive `cooccurrence.html` (pyvis) + `term_clusters.html` scatter (plotly; UMAP for layout, t-SNE/PCA fallback) |
| `ner`    | scispaCy biomedical NER (real entities, not regex)                |
| `embed`  | PubMedBERT term clustering / `--cluster` (torch + transformers)   |
| `dev`    | the pytest test suite                                             |

The two heavy extras are opt-in (they pull in large models / frameworks):

```bash
pip install -e ".[ner]"      # scispaCy NER — also fetch the model (URL above)
pip install -e ".[embed]"    # PubMedBERT clustering — downloads on first use
```

## Use

```bash
# From a folder of PDFs, with a background corpus, write all outputs:
bioleads --pdf ./papers --background bg_counts.json --out ./results

# From a PubMed query:
bioleads --pubmed "GPCR allosteric modulation cardiac" --out ./results

# From an explicit list of PubMed IDs (inline, or a file of IDs via @path):
bioleads --pmids "29622564, 31515768, 30971826" --out ./results
bioleads --pmids @my_pmids.txt --out ./results

# Pull full text for open-access PMC articles (others fall back to abstract):
bioleads --pmids @my_pmids.txt --fulltext --out ./results

# Seed from a reference-manager export (RIS or EndNote XML, auto-detected):
bioleads --refs my_library.ris --out ./results
bioleads --refs my_library.xml --fulltext --out ./results

# Snowball: grow the corpus by following citations from the seeds (2 rounds):
bioleads --pmids @seeds.txt --expand 2 --out ./results
bioleads --refs my_library.ris --expand 1 --expand-link cited_by --out ./results

# Maximum coverage: follow both directions; sources default to NCBI ∪ iCite:
bioleads --pmids @seeds.txt --expand 1 --expand-link both --out ./results

# Relevance-guided: profile the topic from forward citers, then keep only the
# most on-topic backward references (top-K):
bioleads --pmids @seeds.txt --expand-strategy relevance --expand-top-k 50 --out ./results

# Open discovery seeded from specific concepts:
bioleads --pdf ./papers --anchors "trpv1,inflammation" --out ./results

# Cluster ranked terms (writes term_clusters.csv + colors the graph by cluster):
bioleads --pubmed "trpv1 vasodilation" --cluster --n-clusters 10 --out ./results

# Build the citation network and rank the most-cited papers (in-corpus + global):
bioleads --pmids @seeds.txt --citations --out ./results
```

Every network is written **twice**: an interactive 2D pyvis graph (drag nodes,
physics layout) and a rotatable 3D Plotly graph (`*_3d.html` — drag to orbit,
scroll to zoom). So co-occurrence ships as `cooccurrence.html` +
`cooccurrence_3d.html`, the citation network as `citation_network.html` +
`citation_network_3d.html`, and the author network as `author_network.html` +
`author_network_3d.html`.

Outputs in `--out`: `ranked_terms.csv`, `hypothesis_candidates.csv`,
`cooccurrence.html` (+ `cooccurrence_3d.html`), — with `--citations` —
`citation_ranking.csv` + `citation_network.html` and `author_ranking.csv` +
`author_network.html` (each with a `*_3d.html` alongside),
and — with `--cluster` — `term_clusters.csv` (one row per
term: cluster id, centroid, member) plus `term_clusters.html`, an interactive
2D scatter of the term embeddings colored by cluster (every clustered term is a
point; centroids are starred and labeled). The 2D layout uses UMAP when
`umap-learn` is installed (it preserves cluster structure best); otherwise it
falls back to t-SNE, then PCA for very small term sets. With clustering on, co-occurrence
graph nodes are *also* colored by cluster — the two views are complementary:
the graph shows clusters among co-occurring terms, the scatter shows *all*
clustered terms in the embedding space the clustering actually used.

### Citation network

`--citations` builds a directed **paper→paper** graph over the PMID-bearing
records in your corpus (an edge A→B means "A cites B"), using NIH
[iCite](https://icite.od.nih.gov/) for both the links and per-paper metadata
(PMID-native, no API key). It writes two outputs:

- **`citation_ranking.csv`** — every paper ranked by citations, with both
  `in_corpus_citations` (in-degree: how many *other papers in your set* cite it
  — the work your corpus is built on) and `global_citations` (iCite's count
  across all of PubMed), plus title / year / journal / URL.
- **`citation_network.html`** — the interactive directed graph (pyvis), nodes
  sized by in-corpus citations, arrows pointing from each paper to the papers it
  cites. A rotatable 3D version is written alongside as
  `citation_network_3d.html` (Plotly; nodes colored/sized by in-corpus citations).

`--citations` also builds a companion **author→author** network, projected from
the same paper-citation links: when corpus paper P cites corpus paper Q, every
author of P gets a directed edge to every author of Q (shared-author
self-citations dropped; repeated citations accumulate as edge weight). It writes:

- **`author_ranking.csv`** — every author ranked by `in_corpus_citations`
  (weighted in-degree — how often the author is cited within your corpus), with
  `papers` (corpus papers they (co)authored) and `global_citations` (summed iCite
  count across those papers).
- **`author_network.html`** (+ **`author_network_3d.html`**) — the interactive
  2D / rotatable 3D graph, nodes labeled by author and sized by in-corpus
  citations, so the most-cited authors are the largest, hottest nodes.

Only PMID-bearing documents can appear in either network; local PDFs or reference
records without a PMID are skipped. Pair it with `--expand` to first grow the
corpus, then see which papers — and authors — are most foundational.

### What gets processed

PubMed inputs (`--pubmed` / `--pmids`) fetch the **title + abstract** of each
record by default. Pass `--fulltext` to upgrade articles that are open-access in
PubMed Central to their **full body text** (intro/methods/results); records
without PMC full text fall back to the abstract. Local PDFs are always processed
as full extracted text.

Reference-manager exports (`--refs`) accept **RIS** (`.ris`) or **EndNote XML**
(`.xml`), auto-detected from content. Each record's title + abstract is used as
written, and PMIDs are harvested from the file (RIS `AN`, EndNote
`<accession-num>`) so `--fulltext` can upgrade those records to PMC full text
when available. BibTeX and styled/RTF bibliographies are intentionally not
supported (they often lack abstracts or aren't reliably structured).

### Citation expansion (snowballing)

`--expand N` grows the corpus iteratively from the **PMID-bearing seeds** (from
`--pmids`, `--pubmed`, or `--refs`): each round adds the papers linked from the
current frontier and then chases those in the next round, up to `--expand-max`
total records. `--expand-link references` (default) follows the papers each seed
*cites* (backward); `cited_by` follows papers that *cite* the seeds (forward);
`both` unions the two directions. Newly discovered records are fetched from
PubMed (honoring `--fulltext`) and tagged `expanded` in their metadata.

`--expand-source` picks where the citation links come from. By default you
don't have to choose — it **unions both backends** for the broadest recall:

- **`all`** (default) — union of NCBI ELink **and** NIH iCite, deduped. If one
  service is down or rate-limits you, the other still runs (the failure is
  warned, not fatal).
- **`ncbi`** — Entrez ELink only. Backward `references` links exist only for
  seeds whose reference list NCBI parsed out of **PubMed Central**, so a non-PMC
  seed yields *nothing* backward (forward `cited_by` is well covered).
- **`icite`** — NIH [iCite](https://icite.od.nih.gov/) / Open Citation
  Collection only (PMC + Crossref + MEDLINE). Broad backward coverage regardless
  of PMC; PMID-native, no API key.

(Concretely, for PMID `34813650` — not in PMC — `ncbi` finds 0 backward
references while `icite` finds 184; `all` gives the union.)

Seeds without a PMID (raw PDFs, refs lacking an accession) can't be expanded by
either source. Cycles are avoided: a record already seen is never re-queued.

**Relevance-guided expansion** (`--expand-strategy relevance`) is a smarter,
two-phase alternative to the plain BFS snowball. It exploits an asymmetry
between the two link directions:

1. **Phase 1 — forward (`cited_by`).** Papers that cite your seeds tend to
   *converge* on the seed's topic, so the seeds plus their citers are used to
   build a **topic profile** (a term/embedding fingerprint of the subject). All
   forward citers are added to the corpus.
2. **Phase 2 — backward (`references`), gated.** A paper's reference list is
   topically *diffuse* — it cites methods, tangential background, and adjacent
   fields alongside the on-topic work. So rather than swallow every reference,
   each candidate is scored against the Phase-1 profile and only the
   `--expand-top-k` most relevant are kept.

Relevance is measured by **NER term-overlap cosine**, automatically upgraded to
**PubMedBERT cosine** when the `embed` extra is installed. Added records are
tagged with their phase (`forward`/`backward`) and, for kept references, a
`relevance` score. Caveat: the "forward converges" assumption is strongest for
topical research seeds; a popular *method/tool/review* seed gets cited across
many fields, so its profile is broader and the Phase-2 gate discriminates less.

### GUI

A Tkinter desktop front-end wraps the same pipeline — point-and-click inputs,
a live log, tables of ranked terms and hypothesis candidates, and one-click
opening of the interactive co-occurrence graph:

```bash
bioleads-gui          # installed entry point
python -m bioleads.gui   # or run the module directly
```

Tick **Citation network (iCite)** before running to also build the paper→paper
citation graph *and* the author→author citation graph; `citation_ranking.csv`
and `author_ranking.csv` land in the output folder alongside the graph files.
Each network gets its own **2D** and **3D** open buttons — the **Graph** pair for
co-occurrence, the **Citations** pair for the paper network, and the **Authors**
pair for the author network — so both the 2D pyvis layout and the rotatable 3D
Plotly view are one click away. The **3D** button enables only when Plotly
produced the `*_3d.html` file.

After a run, the **Cluster terms** button groups the ranked terms semantically
with PubMedBERT and shows them in a collapsible **Clusters** tab (centroid term
+ members). It also writes `term_clusters.csv` to the output folder, recolors
the co-occurrence graph by cluster, and writes `term_clusters.html` — the
**Open cluster plot** button opens this interactive 2D embedding scatter (every
term a point, colored by cluster). Clustering runs on demand in a background
thread — the first use downloads the embedding model and needs the `embed`
extra (and `viz` for the scatter). Tkinter ships with CPython, so the GUI itself
needs no extra dependency beyond the pipeline's own extras (e.g. `pubmed` for
PubMed/PMC fetching, `embed` for clustering, `viz` for the plots).

### Python API

```python
from bioleads import run_pipeline
from bioleads.config import Config

res = run_pipeline(
    pubmed_query="trpv1 vasodilation",
    cfg=Config(enrichment_method="log_odds", top_terms=150),
    out_dir="./results",
)
print(res.summary())
```

## Background corpus

Enrichment is only as good as the background. Supply a JSON `{term: count}` map of
term frequencies over a neutral reference (e.g. a random PubMed sample, or
all-of-PubMed entity counts from PubTator3). Without one, `bioleads` falls back to
TF-IDF and warns.

## Layout

```
src/bioleads/
  sources.py       PDF + PubMed/PMC + RIS/EndNote loaders, citation links -> Document
  ner.py           scispaCy NER (regex fallback)
  enrichment.py    log-odds / hypergeometric / tf-idf ranking
  cooccurrence.py  PMI-weighted network + pyvis HTML
  graph3d.py       rotatable 3D Plotly rendering of any network
  citations.py     paper->paper citation network (iCite) + most-cited ranking
  discovery.py     Swanson ABC hypothesis candidates
  embeddings.py    PubMedBERT term/text embeddings + clustering
  expansion.py     relevance-guided two-phase citation expansion
  pipeline.py      orchestration
  cli.py           command-line entry point
  gui.py           Tkinter desktop front-end
tests/test_smoke.py
```

## Caveats

- A literature LLM/NER pipeline predicts *text*, not biology. Candidate leads are
  hypotheses to triage, not findings — verify against primary sources.
- Co-occurrence ≠ causation; ABC candidates are starting points for design.
