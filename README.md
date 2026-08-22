# bioleads

Mine biomedical literature for candidate biological leads. Given a set of papers
(a PubMed query, a list of PMIDs, or a reference-manager export), `bioleads`:

1. **Extracts entities** — genes, diseases, chemicals, phenotypes — with
   scispaCy biomedical NER (regex fallback if models aren't installed).
2. **Ranks distinctive terms** — corpus-internal TF-IDF, so what surfaces is
   what carries weight in your topic rather than what is merely frequent.
   (Python callers that can supply a background term-count distribution can
   switch to weighted log-odds (Monroe et al.) or a hypergeometric
   over-representation test instead.)
3. **Builds a co-occurrence network** — entities linked when they co-mention
   more than chance predicts (PMI-weighted), exported as an interactive HTML graph.
4. **Generates hypothesis candidates** — Swanson-style literature-based discovery
   (the ABC model): A–C pairs that share intermediates B but never co-occur
   directly are flagged as hidden associations worth investigating.

Optional PubMedBERT embeddings cluster terms semantically so synonyms group
together — density-based (HDBSCAN), so the number of groups comes from the
terms rather than from you.

**For a stage-by-stage walkthrough of the whole pipeline — what each step does,
why it's there, and which control changes it — see [How bioleads
works](docs/how_it_works.md).** The citation-expansion strategy is benchmarked
against systematic reviews with `tools/benchmark_expansion.py`; see
[Benchmarking citation expansion](docs/benchmark.md).

It can also **map the citation network** of your corpus (`--citations`): a directed
paper→paper graph (via NIH iCite) that ranks the most-cited papers both *within
your set* (in-degree — the work your corpus is built on) and *globally* (iCite's
citation count across all of PubMed).

## Install & run

**Most people: just use the launcher — no typing.**

- **macOS:** in the `launchers` folder, double-click **`Launch bioleads.command`**
- **Windows:** double-click **`launchers\Launch bioleads.bat`**

The first launch sets everything up on its own — Python, the app, and its full pipeline
(it needs [Miniforge](https://conda-forge.org/download/) — a free, one-time install;
brand new to this? see the [install-from-scratch guide](docs/INSTALL.md)); after that it
opens straight away. Step-by-step, including how to download the code:
**[getting started](docs/getting_started.md)**.

bioleads is a **public** repository, so nothing here needs a GitHub account.

<details>
<summary><b>Prefer the command line?</b> — conda or pip</summary>

```bash
# conda (its Python includes the GUI toolkit):
conda env create -f environment.yml
conda activate bioleads
bioleads --help          # or: bioleads-gui

# or plain pip, from a clone:
pip install -e .            # core (regex NER + TF-IDF)
pip install -e ".[all]"     # PubMed, scispaCy NER, embeddings, viz
```

Extras: `pubmed`, `ner`, `embed`, `viz`, `dev`. For real NER also fetch a model:

```bash
pip install https://s3-us-west-2.amazonaws.com/ai2-s2-scispacy/releases/v0.5.4/en_core_sci_sm-0.5.4.tar.gz
```

</details>

## Environment

The quickest way to get a working setup (including the GUI) is the bundled
conda environment:

```bash
cd <repo root>                      # the pip block installs "-e ."
conda env create -f environment.yml
conda activate bioleads
```

This gives you Python 3.11 plus PubMed/PMC fetching, citation
expansion, the interactive graph viz, the Tkinter GUI, and the test suite
(`pubmed,viz,dev`). Tkinter ships with the conda-forge Python build, so
`bioleads-gui` works out of the box — no extra dependency beyond the pipeline's.

Each extra unlocks a feature; install only what you need:

| Extra    | Enables                                                            |
|----------|-------------------------------------------------------------------|
| *(core)* | regex NER + TF-IDF ranking, co-occurrence, ABC discovery          |
| `pubmed` | `--pubmed`, `--pmids`, `--refs`, `--expand`                       |
| `viz`    | interactive citation/author networks (pyvis) + `term_clusters.html` scatter (plotly; UMAP for layout, t-SNE/PCA fallback) |
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
# From a PubMed query, writing all outputs:
bioleads --pubmed "GPCR allosteric modulation cardiac" --out ./results

# From an explicit list of PubMed IDs (inline, or a file of IDs via @path):
bioleads --pmids "29622564, 31515768, 30971826" --out ./results
bioleads --pmids @my_pmids.txt --out ./results

# Seed from a reference-manager export (RIS or EndNote XML, auto-detected):
bioleads --refs my_library.ris --out ./results
bioleads --refs my_library.xml --out ./results

# Grow the corpus by following citations from the seeds. The default strategy
# is `bfs`: a plain snowball, everything linked, ungated, for N rounds.
bioleads --pmids @seeds.txt --expand 2 --out ./results
bioleads --refs my_library.ris --expand 1 --expand-link cited_by --out ./results

# Gated instead — one round in each direction against a profile built from the
# seeds, keeping the top-K of each. Measured cleaner; --expand 1 is enough:
bioleads --pmids @seeds.txt --expand 1 --expand-strategy relevance --out ./results
bioleads --pmids @seeds.txt --expand 1 --expand-strategy relevance \
         --expand-top-k 100 --out ./results

# Open discovery seeded from specific concepts:
bioleads --pmids @seeds.txt --anchors "trpv1,inflammation" --out ./results

# Cluster ranked terms (writes term_clusters.csv + term_clusters.html).
# How many clusters is HDBSCAN's problem, not yours; add
# --cluster-method kmeans --n-clusters 10 if you want a fixed number:
bioleads --pubmed "trpv1 vasodilation" --cluster --out ./results

# Build the citation network and rank the most-cited papers (in-corpus + global):
bioleads --pmids @seeds.txt --citations --out ./results
```

Every network is written **twice**: an interactive 2D pyvis graph (drag nodes,
physics layout) and a rotatable 3D Plotly graph (`*_3d.html` — drag to orbit,
scroll to zoom; laid out in shells ranked by degree outward from the busiest node). So the citation network ships as `citation_network.html` +
`citation_network_3d.html`, and the author network as `author_network.html` +
`author_network_3d.html`.

The term co-occurrence graph is **not** rendered. It is still built, and ABC
discovery still runs over it — it is simply not written out as a network file.

Outputs in `--out`: `ranked_terms.csv`, `hypothesis_candidates.csv`,
`pmids.txt` (every PMID in the corpus — seeds and anything expansion added —
one per line, ready to paste into PubMed or feed back in as `--pmids @file`;
written only if the corpus has PMIDs at all), — with `--citations` —
`citation_ranking.csv` + `citation_network.html`, `author_ranking.csv` +
`author_network.html`, and `author_paper_ranking.csv` +
`author_paper_network.html` (the same senior authors sized by papers published
into the corpus rather than by citations received; each network with a
`*_3d.html` alongside), and — with `--cluster` — `term_clusters.csv` (one row per
term: cluster id, centroid, member; unclustered terms carry id `-1`)
plus `term_clusters.html`, an interactive
2D scatter of the term embeddings colored by cluster (every clustered term is a
point; centroids are starred and labeled, and the unclustered leftovers are
drawn in grey). The 2D layout uses UMAP when
`umap-learn` is installed (it preserves cluster structure best); otherwise it
falls back to t-SNE, then PCA for very small term sets. The scatter is the only
view of the clusters: since the co-occurrence network is no longer written out,
there is no graph left to colour by cluster.

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

`--citations` also builds a companion **senior-author→senior-author** network
from the same paper-citation links, with **one** author standing for each paper:
the **last** name in its byline, which by biomedical convention is the senior
author — the lab the work came from. First and middle authors do not appear. When
corpus paper P cites corpus paper Q, P's senior author gets a directed edge to
Q's (a shared senior author is a self-citation and is dropped; repeated citations
accumulate as edge weight, so two papers from one lab citing the same lab make
one edge of weight 2). Read it as labs citing labs. It writes:

- **`author_ranking.csv`** — every senior author ranked by `in_corpus_citations`
  (weighted in-degree — how often the lab is cited within your corpus), with
  `papers` (corpus papers they were senior author on) and `global_citations`
  (summed iCite count across those papers).
- **`author_network.html`** (+ **`author_network_3d.html`**) — the interactive
  2D / rotatable 3D graph, nodes labeled by senior author and sized by in-corpus
  citations, so the most-cited labs are the largest, hottest nodes.

Only PMID-bearing documents can appear in either network; reference records
without a PMID are skipped, as are records iCite has no author list for.
Names are matched as strings, so "Smith J" and "Smith JA" are two people. Pair it
with `--expand` to first grow the corpus, then see which papers — and which labs
— are most foundational.

**`--min-paper-degree N`** and **`--min-author-degree N`** thin the two networks
to their connected part. A node is kept when its total connections — citations
received from corpus papers plus citations made to them (for a senior author,
distinct labs cited or citing) — reach `N`. The default `0` keeps everything; `1`
drops the nodes with no intra-corpus link at all, which in a sparse corpus is
most of them. The rankings are filtered alongside the pictures, so the CSV and
the graph always agree.

**Citation data is cached.** Everything fetched over the network to build or
expand a citation graph is kept under `~/.cache/bioleads/citations` for 30 days:
iCite records for the networks, and the link lookups `--expand` makes against
iCite and NCBI ELink. The first run pays; repeats cost nothing and work with no
network at all.

Records are keyed per PMID, so growing a corpus re-reads everything it already
had and fetches only what you added. Expansion lookups are keyed by the whole
request instead — both backends answer a batch with one flat list and never say
which paper each link came from, so the request is the largest thing that can be
replayed with the result guaranteed identical. The walk is deterministic, so
repeating one replays entirely from disk.

Entries expire because two of the numbers are alive: iCite's *global* citation
count grows continuously, and so does the set of papers citing any given one. An
entry that never expired would freeze both at whatever they were the day you
first looked. `--citation-cache-days 0` disables the cache and always fetches; a
large value pins it.

The filter settles rather than running once, so every node left really does
clear the number — dropping a node lowers its neighbours' degree, and one pass
would leave some of them under the bar. A high threshold can therefore cascade,
and can empty the network when nothing that size hangs together; the run log
says so.

They are two numbers because the two graphs are different objects — a node is a
paper in one and a lab in the other, and a lab inherits the links of every corpus
paper it led — though since each paper contributes one senior author, the two
degree distributions are similar enough that the same value is a fair starting
point for both:

```bash
bioleads --pmids @seeds.txt --citations \
         --min-paper-degree 2 --min-author-degree 2 --out ./results
```

### What gets processed

Every document is a **title + abstract**. bioleads used to offer `--fulltext`,
which upgraded open-access PubMed Central articles to their full body; it was
removed because it quietly reweighted everything downstream. Only ~28% of a
typical corpus is open-access, and those documents run ~30× longer, so they
contributed 87% of all term mentions and — since co-occurrence pairs grow with
the square of document length — **99% of the network's edges**. Stages 4–6 ended
up describing the open-access subset rather than the corpus, and open access is
not a random sample of the literature.

Reference-manager exports (`--refs`) accept **RIS** (`.ris`) or **EndNote XML**
(`.xml`), auto-detected from content. Each record's title + abstract is used as
written, and PMIDs are harvested from the file (RIS `AN`, EndNote
`<accession-num>`) so those records can seed expansion and appear in the
citation networks. BibTeX and styled/RTF bibliographies are intentionally not
supported (they often lack abstracts or aren't reliably structured).

### Citation expansion (snowballing)

`--expand N` grows the corpus from the **PMID-bearing seeds** (from `--pmids`,
`--pubmed`, or `--refs`). Nothing is expanded without it, whichever strategy is
selected. What `N` means depends on the strategy: under the default `bfs` it is
a depth, each round adding the papers linked from the current frontier and then
chasing those in the next, up to `--expand-max` total records; under `relevance`
any `N > 0` means one gated round in each direction (see below).

`--expand-link both` (the default) unions the two directions; `references`
alone follows the papers each seed *cites* (backward), and `cited_by` alone
follows papers that *cite* the seeds (forward). It applies to `bfs` only —
`relevance` always does both and gates each. Newly discovered records are
fetched from PubMed and tagged `expanded` in their metadata.

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

Seeds without a PMID (refs lacking an accession) can't be expanded by either
source. Cycles are avoided: a record already seen is never re-queued.

**Relevance-guided expansion** (`--expand-strategy relevance`) is the filtered
alternative to the plain BFS snowball that `--expand` does by default. It is not
the default, but it is what the benchmark favours *on precision*: at equal reach
it beat `bfs` on F1 in 35 of 40 reviews at K=50 and 38 of 40 at K=100, with about
ten times the pooled precision.

`bfs` keeps two advantages worth stating. It has the **highest recall of any arm
measured** (0.3252 over 12 reviews, 0.2735 over 40) — the gate's own best result
is matching that number at K=800, not exceeding it — and it needs **no model**:
no embeddings, no `embed` extra, no download, and its link lookups are cached.
Without that extra, `relevance` silently drops to a term-overlap fallback the
benchmark never measured, so on a core install the comparison above does not
apply. Both citation directions are noisy, so the gate trusts neither:

1. **Profile from the seeds alone.** Your seed documents are the only papers
   known to be on topic, so they — and nothing else — form the **topic profile**
   (a term/embedding fingerprint of the subject).
2. **Both directions gated against it.** Forward citers (`cited_by`) and
   backward references (`references`) are each collected, scored against that
   profile, and cut to the `--expand-top-k` most relevant *per direction*.
   Nothing passes through unfiltered.

Relevance is **NER term-overlap cosine**, automatically upgraded to **PubMedBERT
cosine** when the `embed` extra is installed. The profile is a **Rocchio query
vector**: the centroid of the seed documents minus `rocchio_gamma` times the
centroid of the worst-scoring candidates (set `rocchio_gamma=0` for a
positive-only centroid). Every kept record is tagged with its direction
(`forward`/`backward`) and its `relevance` score. That score is *centred* — the
candidate pool's mean is removed before it is written — because raw cosines
between biomedical abstracts all land near 0.99 and cannot be read. Selection
still uses the raw cosine; centring was measured not to change which papers are
kept, so it applies to the reported number only. Kept records come back sorted by
that score, most relevant first.

`--expand-top-k` is the control that matters: it sets corpus size and
cleanliness together. Swept against systematic reviews (12 reviews, full table
in [docs/benchmark.md](docs/benchmark.md)):

| K | median P | median R | median F1 | retrieved | vs BFS |
|---|---|---|---|---|---|
| 10 | 0.1255 | 0.0333 | 0.0504 | 199 | 10% of its recall, 100% less material |
| 25 | 0.0941 | 0.0903 | **0.0948** | 486 | 28% |
| **50** | 0.0854 | 0.1406 | 0.0927 | 975 | 43% — best in 7 of 12 reviews |
| 100 | 0.0624 | 0.2473 | 0.0865 | 1,948 | 76% of its recall, 96% less material |
| 200 | 0.0511 | 0.2992 | 0.0745 | 3,350 | 92%, 93% less |
| 800 | 0.0328 | 0.3252 | 0.0542 | 6,595 | **100%**, 87% less |

K=25 is sharpest and **K=50** (the default) has the best paired record. But note
the last row: at K=800 the strategy matches BFS's recall *exactly* while
retrieving 87% less. K≈100–200 is the region to prefer when the corpus feeds ABC
discovery, which needs the intermediate concepts present at all — 76–92% of BFS's
reach at 2–3× its median precision.

> **This design replaced an earlier one, on evidence.** Until 2026-08 the profile
> was built from the seeds *plus* their forward citers, and every citer was added
> ungated, on the theory that citing papers converge on a seed's topic while
> reference lists sprawl. Benchmarking measured the opposite: forward citers were
> the *less* precise direction, made up ~95% of the returned volume, and putting
> them in the profile actively hurt. Profiling on seeds alone and gating both
> directions took 47,974 retrieved documents at 0.36% precision to 975 at 10.56%.
> Full numbers and method in [docs/benchmark.md](docs/benchmark.md).

### GUI

A Tkinter desktop front-end wraps the same pipeline, laid out like the other
lab tools (probelog, plasmidlog): a narrow settings column on the left, and
everything the run produces on the right — a live log, tables of ranked terms
and hypothesis candidates, and an **Outputs** tab that opens every file written:

```bash
bioleads-gui          # installed entry point
python -m bioleads.gui   # or run the module directly
```

The settings column groups every control into five cards — **Corpus**, **Grow
the corpus**, **Analysis**, **Citation networks**, **Output** — and scrolls,
with **Run pipeline**, **Stop** and **Cluster terms** pinned below it so they
are always reachable. Each control explains itself on hover.

Everything a run *produces* is listed in the **Outputs** tab of the results
notebook, grouped by what it describes (term clusters, paper citations,
senior-author citations, senior-author output, tables), each with its path and an
**Open** button that hands the file to your default application. Files the run
didn't produce stay listed but greyed, so it's visible what's missing and why —
a `*_3d.html` row is empty when Plotly isn't installed, for example.

Tick **Build them (NIH iCite)** in the Citation networks card to also build the
paper→paper citation graph *and* the senior-author→senior-author graph, the
latter drawn twice — once sized by citations received in the corpus, once by
papers published into it. `citation_ranking.csv`, `author_ranking.csv` and
`author_paper_ranking.csv` land in the output folder alongside the graph files.
Every network is written as both a 2D pyvis layout and a rotatable 3D Plotly
view, listed side by side in the Outputs tab.

The 3D view is **ranked outward from the busiest node**: the highest-degree
node is the root at the centre, and every other node sits on a shell chosen by
its own degree — next-highest degree on the first shell, out to the least
connected on the rim — with each shell spread evenly over the sphere. So radius
means connectedness rather than wherever a force simulation happened to settle,
and the ranking is visible as you look outward. Nodes of equal degree share a
shell, since they are equally central.

Degree is counted in both directions, the same measure `--min-paper-degree`
uses, so a much-cited paper is not treated as unconnected. Isolated papers have
degree zero and land on the rim, which needs no special case. It is
deterministic: the same graph lays out the same way every time.

Note that the citation view *sizes* nodes by in-corpus citations, which is not
the same measure as degree — the largest node need not be the central one.

After a run, the **Cluster terms** button groups the ranked terms semantically
with PubMedBERT and shows them in a collapsible **Clusters** tab (centroid term
+ members, with anything left unclustered in a bucket at the bottom). The
**Clustering** field on the Inputs page picks the method: `hdbscan` (the
default) finds the number of groups itself, `kmeans` takes the number from
**Clusters (k)**, which is greyed out under `hdbscan` because it is ignored.

It also writes `term_clusters.csv` to the output folder and
`term_clusters.html` — an interactive 2D embedding scatter (every term a point,
colored by cluster) that appears under **Term clusters** in the Outputs tab. Clustering runs on demand
in a background thread — the first use downloads the embedding model and needs the `embed`
extra (and `viz` for the scatter). Tkinter ships with CPython, so the GUI itself
needs no extra dependency beyond the pipeline's own extras (e.g. `pubmed` for
PubMed/PMC fetching, `embed` for clustering, `viz` for the plots).

### Python API

```python
from bioleads import run_pipeline
from bioleads.config import Config

res = run_pipeline(
    pubmed_query="trpv1 vasodilation",
    cfg=Config(top_terms=150),
    out_dir="./results",
)
print(res.summary())
```

## Ranking

Terms are scored by corpus-level TF-IDF: total count damped by how many
documents the term appears in, so anything that shows up everywhere sinks.
There is nothing to configure and nothing to supply.

Two other methods used to exist — Monroe et al. weighted log-odds and a
hypergeometric over-representation test — which asked the stronger question of
whether a term is over-represented *relative to biomedicine at large*. Answering
that needs a **background**: a `{term: count}` map over some neutral reference
collection. bioleads never shipped one, so in practice every run fell back to
TF-IDF while labelling its output as z-scores or p-values. Both methods and the
background plumbing are gone; bringing them back means bringing back a
background worth scoring against, not just the arithmetic.

## Layout

```
src/bioleads/
  config.py        every knob, with the reasoning next to it
  sources.py       PubMed/PMC + RIS/EndNote loaders, citation links -> Document
  ner.py           scispaCy NER (regex fallback)
  enrichment.py    corpus-internal TF-IDF term ranking
  cooccurrence.py  PMI-filtered term network (built, not rendered)
  graph3d.py       rotatable 3D Plotly rendering of any network
  citations.py     paper->paper and senior-author networks (iCite) + rankings
  cache.py         on-disk cache for everything fetched from iCite / ELink
  discovery.py     Swanson ABC hypothesis candidates
  embeddings.py    PubMedBERT term/text embeddings + HDBSCAN clustering
  expansion.py     citation expansion: bfs, or the relevance-gated two-phase walk
  pipeline.py      orchestration
  cli.py           command-line entry point
  gui.py           Tkinter desktop front-end
tests/
  test_smoke.py    the pipeline, end to end and unit
  test_gui.py      the GUI's run-scoped state (skipped without a display)
  test_benchmark.py  the systematic-review benchmark harness
```

## Caveats

- A literature LLM/NER pipeline predicts *text*, not biology. Candidate leads are
  hypotheses to triage, not findings — verify against primary sources.
- Co-occurrence ≠ causation; ABC candidates are starting points for design.
