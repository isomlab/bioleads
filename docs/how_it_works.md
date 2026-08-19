# How bioleads works

A guided tour of the pipeline: what each stage does, why it's there, what it
produces, and which control changes it. If you just want to get a run going, see
[getting_started.md](getting_started.md); this page is for understanding what
came out and deciding what to change.

---

## The idea

A literature search gives you papers. bioleads tries to give you *leads*: the
concepts that make your corpus distinctive, how those concepts connect, and
which pairs of concepts look related through the literature without any paper
having actually connected them.

The last part is the point. It's the **Swanson ABC model** of literature-based
discovery: if concept **A** keeps appearing alongside intermediates **B**, and
those same **B** appear alongside concept **C**, but **A** and **C** never appear
together, then A–C is a connection the literature implies but hasn't stated.
Swanson's original case linked dietary fish oil to Raynaud's syndrome through
blood-viscosity intermediates — two literatures that never cited each other.

Everything upstream of that exists to make the ABC step trustworthy: get the
right papers, pull out real biomedical entities, keep only the terms that
actually distinguish this corpus, and connect them only where the connection
beats chance.

---

## The pipeline at a glance

```
   ┌─ 1. Collect documents ──────── PDFs · PubMed query · PMIDs · RIS/EndNote
   │
   ├─ 2. Grow the corpus ────────── follow citations (optional)
   │
   ├─ 3. Extract entities ───────── scispaCy biomedical NER
   │
   ├─ 4. Rank distinctive terms ─── score against a background corpus
   │
   ├─ 5. Build the term network ─── co-occurrence, PMI-filtered
   │
   ├─ 6. Propose hypotheses ─────── Swanson ABC over that network
   │
   ├─ 7. Cluster terms ─────────── PubMedBERT + KMeans (optional)
   │
   ├─ 8. Map the citations ──────── paper→paper and author→author (optional)
   │
   └─ 9. Write outputs ─────────── CSVs + interactive HTML
```

Stages 1–6 always run. Stages 7 and 8 are optional and independent of each
other. Each stage consumes only what the stage above it produced, so a weak
result usually traces to one specific step — the sections below say which.

---

## 1. Collect documents

**What it does.** Loads every input you gave into one flat list of documents,
each with a title, text body, and whatever metadata the source carried (PMID,
year, journal, authors). Sources combine freely — a folder of PDFs *and* a
PubMed query *and* an EndNote export all land in the same corpus.

| Input | What's read |
|---|---|
| **PDF file/folder** | full extracted text, de-hyphenated across line breaks |
| **PubMed query** | an Entrez search; title + abstract of each hit |
| **PubMed IDs** | specific records, inline or from a file |
| **References file** | RIS or EndNote XML export; title + abstract as written |

PubMed records give you the **abstract** by default. Turning on full text
upgrades any article that's open-access in PubMed Central to its **full body**
(introduction, methods, results); articles that aren't open-access quietly fall
back to the abstract. Full text is far richer and materially slower — one extra
fetch per article.

**Why it matters downstream.** This is the only stage that decides what the
corpus *is*. Every later stage describes this document set and nothing else, so
a biased or too-small corpus produces confident, well-scored nonsense.

**Controls.** PDF file/folder · PubMed query · PubMed IDs · References file ·
PMC full text · Max records (caps how many hits a query fetches, default 500).

---

## 2. Grow the corpus by following citations

**What it does.** Optional. Starts from the documents that carry a PMID, walks
the citation graph outward, and appends what it finds. Local PDFs without a PMID
can't seed this — there is nothing to follow.

This is the one stage where bioleads does something more than plumbing, so it is
documented mechanism-first: what the two directions are, what the strategy does
with them, the exact arithmetic, then what measurement had to say about all of
it.

### 2.1 The two directions

Every seed sits in the middle of two very different sets of papers.

```
         PAST                      SEEDS                       FUTURE
    ─────────────────────────────────────────────────────────────────────▶ time

    ○ ○ ○ ○ ○ ○ ○ ○ ○           ★ ★ ★ ★ ★           ○ ○ ○ ○ ○ ○ ○ ○ ○ ○ ○ ○ ○
    └───────┬───────┘           └────┬────┘           └──────────┬──────────┘
        references                you chose               cited_by
        (backward)                  these                 (forward)
    "what the seeds cite"                          "what cites the seeds"
      ~2,300 papers                                    ~47,000 papers
```

Those two counts are measured, not illustrative: across the 12 systematic-review
seed sets used in [the benchmark](benchmark.md), one round backward yielded
2,342 papers and one round forward yielded roughly 47,000. **Forward is ~20×
larger.** That size difference drives most of what follows.

The directions also differ in kind:

- **Backward — what a seed cites.** A reference list is everything the authors
  needed: the method, the reagent, the statistical tool, the mouse line,
  background from an adjacent field, a courtesy citation. A union of topics.
- **Forward — what cites a seed.** To cite a paper you generally engage with what
  it showed — but a widely-used method or reagent paper is cited by every field
  that uses it.

Plain snowballing (`bfs`) takes both wholesale, which is why it drifts: one round
backward drags in every field the seed touched, and the next round expands from
*those*. But backward is also where the foundational and older literature lives —
exactly what ABC discovery (stage 6) needs to find a link nobody has stated. The
goal is therefore not to skip a direction but to **filter** both.

### 2.2 What the strategy does

`relevance` trusts neither direction. It trusts only the seeds — the one set of
papers known to be on topic, because you chose them — and filters everything
else against them.

```
                        ┌──────────────────┐
                        │   seed docs  S   │  PMID-bearing, chosen by you
                        └───┬──────────┬───┘
              ┌─────────────┘          └─────────────┐
              ▼                                      ▼
   ┌─────────────────────┐              ┌──────────────────────────┐
   │ PROFILE             │              │ CANDIDATES               │
   │  embed S            │              │  F = cited_by(S)         │
   │  average            │              │  B = references(S)       │
   │  normalise          │              │  fetch text, embed       │
   │            →  q₀    │              │            →  ê_c        │
   └──────────┬──────────┘              └────────────┬─────────────┘
              │                                      │
              └──────────────┬───────────────────────┘
                             ▼
                 ┌───────────────────────┐
                 │  score   s_c = ê_c·q₀ │   cosine similarity
                 └───────────┬───────────┘
                             ▼
                 ┌───────────────────────────────────┐
                 │  negative term        (if γ > 0)  │
                 │   worst-scoring fraction  →  n̂    │
                 │   q = normalise(q₀ − γ·n̂)         │
                 │   re-score against q              │
                 └───────────┬───────────────────────┘
                             ▼
                 ┌───────────────────────┐
                 │  keep top K           │   applied to F and to B
                 └───────────┬───────────┘   separately
                             ▼
                      added to the corpus
```

Both directions run through the same gate, independently, each keeping its own
top K. Nothing passes through unfiltered.

### 2.3 The math

Five steps. Everything below is what the code actually computes; the symbols are
used consistently.

| symbol | meaning |
|---|---|
| $S$ | the seed documents |
| $F$, $B$ | forward (`cited_by`) and backward (`references`) candidate sets |
| $\mathbf{e}_d$ | the embedding of document $d$ |
| $\hat{\mathbf{e}}_d$ | that embedding, scaled to unit length |
| $\mathbf{q}_0$, $\mathbf{q}$ | the profile vector, before and after the negative term |
| $\gamma$ | `rocchio_gamma`, the weight of the negative term |
| $K$ | `expand_top_k`, how many papers survive per direction |

**Step 1 — embed a document.** PubMedBERT reads the title and abstract and emits
one vector per token. Those are averaged over the real (non-padding) tokens —
"mean pooling" — to get one vector for the document:

$$\mathbf{e}_d \;=\; \frac{\sum_{t} m_t \, \mathbf{h}_t}{\sum_{t} m_t}$$

where $\mathbf{h}_t$ is the model's output for token $t$ and $m_t$ is 1 for a
real token and 0 for padding. Text is truncated at 256 tokens. Every vector is
then scaled to unit length,

$$\hat{\mathbf{e}}_d \;=\; \frac{\mathbf{e}_d}{\lVert \mathbf{e}_d \rVert}$$

which is what makes the dot products further down equal cosines.

**Step 2 — build the profile.** Average the seed vectors and re-normalise:

$$\mathbf{q}_0 \;=\; \frac{\bar{\mathbf{e}}_S}{\lVert \bar{\mathbf{e}}_S \rVert}, \qquad \bar{\mathbf{e}}_S = \frac{1}{|S|}\sum_{d \in S} \hat{\mathbf{e}}_d$$

The average of several unit vectors points "between" them, so $\mathbf{q}_0$ is a
single direction standing for the topic the seeds share. Re-normalising matters
because averaging shortens the vector — seeds that disagree shorten it more —
and only the *direction* should carry meaning.

**Step 3 — score a candidate.** Because both vectors are unit length, the dot
product *is* the cosine of the angle between them:

$$s_c \;=\; \hat{\mathbf{e}}_c \cdot \mathbf{q}_0 \;=\; \cos\theta_c \;\in\; [-1, 1]$$

1 means "points the same way as the topic", 0 means unrelated. In practice
biomedical abstracts all point broadly similarly, so the useful signal is in the
*ranking*, not the absolute value.

**Step 4 — the negative term.** Pointing the gate *toward* the topic is not the
same as pointing it *away from* what the topic isn't. A methods paper that shares
the seeds' technique vocabulary can sit exactly as close to $\mathbf{q}_0$ as a
genuinely on-topic paper, because the seed papers use that vocabulary too. The
fix is Rocchio's negative term: take the worst-scoring candidates as a stand-in
for "off topic", average them, and subtract.

$$\hat{\mathbf{n}} \;=\; \frac{\bar{\mathbf{e}}_N}{\lVert\bar{\mathbf{e}}_N\rVert}, \qquad \mathbf{q} \;=\; \frac{\mathbf{q}_0 - \gamma\,\hat{\mathbf{n}}}{\lVert \mathbf{q}_0 - \gamma\,\hat{\mathbf{n}} \rVert}$$

where $N$ is the lowest-scoring fraction of the candidate pool. Candidates are
then re-scored against $\mathbf{q}$ instead of $\mathbf{q}_0$. Geometrically the
query vector rotates away from the off-topic cloud, which spreads apart
candidates the positive centroid alone had tied:

```
   similarity →  0.0        0.2        0.4        0.6        0.8
                 ├──────────┼──────────┼──────────┼──────────┤
   γ = 0                          tail▲            M▲X▲          M and X tie
                 ├──────────┼──────────┼──────────┼──────────┤
   γ = 0.25            tail▲                     M▲     X▲       X clears M
                 ├──────────┼──────────┼──────────┼──────────┤

        X = on-topic paper    M = methods paper    tail = off-topic candidates
```

Those are real numbers from the regression test: the positive centroid scores X
and M identically at 0.7071, and the negative term moves them to 0.7566 and
0.6136 while pushing the tail from 0.3693 to ~0.14.

**Why the negatives are free.** The gate exists because a candidate pool is
mostly off topic — so the pool's own low-scoring tail *is* a supply of negatives,
and better ones than random papers would be: they are citation-adjacent and
plausible, which is the distinction the gate actually has to make. Candidates are
scored twice but embedded once, so this costs a dot product, not a second model
pass.

The selection has three guards, all in `_pseudo_negative_idx`:

- $\gamma \le 0$ disables it entirely, recovering the plain positive centroid.
- A pool smaller than `rocchio_min_candidates` (8) has no meaningful tail.
- The tail is clamped so it can never reach into the top $K$ about to be kept —
  a document should not be evidence of what the topic *isn't* while also being
  kept as on-topic. (Re-scoring can reorder, so this is enforced on the
  first-pass ranking.)

**Step 5 — the cut.** Sort by score, keep the $K$ highest, discard the rest
before they ever reach the corpus. Applied to $F$ and $B$ separately, so the
strategy returns at most $2K$ new documents per round.

**Without the `embed` extra.** The same five steps run in term space instead of
embedding space. The profile is a vector over NER terms weighted by how many
profile documents contain each,

$$p_t \;=\; \bigl|\{\, d \in S : t \in \text{terms}(d) \,\}\bigr|$$

a candidate is the 0/1 indicator vector of its own term set $C$, and the score is
the same cosine:

$$s_C \;=\; \frac{\sum_{t \in C} q_t}{\lVert \mathbf{q} \rVert \cdot \sqrt{|C|}}$$

with $\sqrt{|C|}$ being the length of a binary vector with $|C|$ ones. The
negative term works identically, subtracting a term vector built from the tail.
The upgrade to PubMedBERT is automatic when the extra is installed, and a failure
in the embedding path falls back here rather than sinking the run.

### 2.4 The parameters

| control | symbol | default | what it changes |
|---|---|---|---|
| `expand_top_k` | $K$ | 50 | papers kept **per direction**. The main control over corpus size and cleanliness. |
| `rocchio_gamma` | $\gamma$ | 0.25 | weight of the negative term. Measured to change almost nothing — see 2.5. |
| `rocchio_neg_frac` | | 0.25 | fraction of the pool taken as the negative tail. |
| `rocchio_min_candidates` | | 8 | pools smaller than this skip the negative term. |
| `expand_fwd_rounds` / `expand_back_rounds` | | 1 / 1 | depth in each direction. Not exposed in the GUI. |
| `expand_max` | | 1000 | hard cap on total PMIDs. |
| `expand_source` | | `all` | NCBI ELink, NIH iCite, or the union. |

Two things about the GUI controls are easy to get wrong: choosing `relevance`
**runs expansion even with "Citation expansion rounds" set to 0** (that spinbox
drives `bfs` only), and **`Follow` is ignored** by `relevance`, which always does
both directions.

### 2.5 What the measurements said

This design replaced an earlier one **on evidence**, and the evidence is worth
keeping visible because it contradicts the intuition the original was built on.
Method and full tables: [docs/benchmark.md](benchmark.md). Ground truth is a
systematic review's reference list; seeds are sampled from it; the arms try to
recover the rest.

**The original design.** Profile built from the seeds *plus* their forward
citers, with every citer added ungated and only backward references filtered —
on the theory that citing papers converge on a seed's topic while reference lists
sprawl.

**Finding 1 — the asymmetry runs the other way.** Over 40 reviews, backward was
*more* precise than forward in **33 of 40**: forward P 0.0152 / R 0.1154 against
backward P 0.0530 / R 0.1667. Comparable true hits (403 vs 397) but forward drags
2.88× the volume. The direction the design treated as reliable is the noisy one.

**Finding 2 — the gate governed 5% of the output.** Backward references were
2,342 of the ~49,700 documents the strategy returned; the other ~95% were
ungated forward citers. The profile, the negative term and $K$ were all tuning
one twentieth of the result — and the cleaner twentieth. This is also why
$\gamma$ looked inert: $\gamma = 0 \to 0.25$ moved the total from 172 hits to
**173**, one document across ~48,000 retrieved. Re-running with full abstracts
instead of titles changed nothing (172 / 173 / 172), refuting the obvious
"scoring fidelity" explanation.

**Finding 3 — gating the other way is much better.** Two arms, 12 reviews:

| arm | median P | median R | median F1 | retrieved | pooled P |
|---|---|---|---|---|---|
| `relevance` (original) | 0.0247 | 0.2659 | 0.0434 | 47,974 | 0.36% |
| `both` (= bfs) | 0.0244 | 0.3252 | 0.0442 | 49,683 | 0.41% |
| `relevance_fwd` | 0.0520 | 0.1760 | 0.0718 | 2,712 | 4.46% |
| **`relevance_seeds`** | **0.0854** | 0.1406 | **0.0927** | **975** | **10.56%** |

`relevance_fwd` inverts the design (profile on backward, gate forward);
`relevance_seeds` is the control — profile on the **seeds alone**, gate both —
and it wins: better F1 in 10 of 12 reviews, better precision in 11 of 12, and
better than `relevance_fwd` in 11 of 12. Because the arm *least* exposed to the
circularity worry (the ground truth is itself a reference list, which could
flatter an arm that profiles on references) is the strongest, the result is not
an artefact. **`relevance_seeds` is what stage 2 now implements.**

**Finding 4 — $K$ is the knob, not $\gamma$.** Swept on `relevance_seeds`:

```
     K   median precision             median recall                   corpus
   ───   ───────────────────────────   ────────────────────────────   ──────
    10   ████████████ 0.1255          ███ 0.0333                         199
    25   █████████ 0.0941             ███████ 0.0903                     486
    50   ████████ 0.0854              ███████████ 0.1406                 975
   100   ██████ 0.0624                ███████████████████ 0.2473       1,948
   200   █████ 0.0511                 ███████████████████████ 0.2992   3,350
   400   ████ 0.0385                  ████████████████████████ 0.3186  4,780
   800   ███ 0.0328                   █████████████████████████ 0.3252  6,595
   ───   ───────────────────────────   ────────────────────────────   ──────
   bfs   ██ 0.0244                    █████████████████████████ 0.3252 49,683
```

The bottom two rows are the point: **at $K = 800$ the gate reaches exactly the
same recall as `bfs`, 0.3252, on 87% less material.** The recall gap at smaller
$K$ is the cutoff choosing, not the gate losing — so there is no breadth argument
for unfiltered snowballing.

Choose $K$ by what stage 6 needs rather than by F1:

- **$K \approx 10\text{–}25$** — sharpest (F1 peaks at 25), for reading a tight
  corpus yourself.
- **$K = 50$** (default) — best paired record of any $K$: better F1 than `bfs` in
  11 of 12 reviews, than raw `backward` in 10 of 12, and best of any $K$ in 7 of 12.
- **$K \approx 100\text{–}200$** — for ABC discovery, which can only find an A–C
  pair if some B is in the corpus: 76–92% of `bfs`'s recall at 2.1–2.6× its
  median precision (13–18× by pooled precision), on 93–96% less material.

**Caveats.** 12 reviews for the gate comparisons, 40 for the asymmetry. Scoring
used iCite titles, though the abstract re-run agreed. Ground truth is a reference
list, which may favour backward-ish arms generally — a topic-labelled benchmark
would be the independent check.

### 2.6 When it breaks

- **Seeds that don't share a topic.** The profile is one centroid, so a two-topic
  seed set averages to the space *between* them and can rank papers from neither
  highly. Run them as separate corpora.
- **Very few seeds.** One seed makes $\mathbf{q}_0$ that paper's own vector, and
  the gate becomes "papers similar to this one" rather than "papers about this
  topic".
- **$K$ is a count, not a threshold.** Exactly $K$ papers are kept per direction
  even when none of them are close, and equally $K$ is a ceiling when hundreds
  are.
- **No PMIDs, no expansion.** PDF-only corpora cannot seed this at all.

### 2.7 The other strategy: `bfs`

Plain snowball, no gating. Each round takes the current frontier, adds everything
linked to it, and chases those next, up to the record cap. Direction is yours:
`references` (backward), `cited_by` (forward), or `both`. Use it when you want
exhaustive coverage of a small, tight seed set and intend to do the filtering
yourself — noting that on the benchmark, `relevance` reaches the same recall more
cleanly at large $K$.

### Controls

Citation expansion rounds (0 = off) · Follow · Source · Strategy · Relevance
top-K · Max records.

## 3. Extract entities

**What it does.** Runs biomedical named-entity recognition over every document
and keeps the spans it finds — genes, proteins, diseases, chemicals, phenotypes,
cell types. Entities are lowercased and whitespace-collapsed so that surface
variants of the same thing count as the same term, and anything shorter than
three characters is dropped.

**Two engines, and it matters which one you got.** With the `ner` extra
installed, this is **scispaCy**, which returns curated biomedical entity spans.
Without it, bioleads falls back to a **regex extractor** — gene-like symbols
(`TP53`, `IL6R`) plus general content words minus a stopword list. The fallback
keeps the pipeline runnable without a model download, and it is genuinely
cruder: it will hand you ordinary English words as "entities". The log line
`NER engine: …` says which one ran. If your ranked terms look like vocabulary
rather than biology, that line is the first thing to check.

**Controls.** None in the GUI — the engine is whichever is installed.

---

## 4. Rank distinctive terms

**What it does.** Counts every term across the corpus, then scores each one for
how **over-represented** it is relative to a **background** distribution of term
counts. Raw frequency is useless here — it just surfaces `cell`, `patient`,
`expression`. Enrichment against a background is what separates "common in
biomedicine" from "distinctive about *this* topic".

Terms appearing in fewer than 2 documents are dropped first, and the top 200
scoring terms are kept.

**Three methods:**

- **`log_odds`** — weighted log-odds with an informative Dirichlet prior
  (Monroe, Colaresi & Quinn 2008), reported as a z-score. Robust across the
  frequency range and the field standard for distinctive-term comparisons.
- **`hypergeometric`** — classic over-representation test, reported as
  −log₁₀(p). Familiar from enrichment analysis.
- **`tfidf`** — corpus-internal TF-IDF. Needs **no background** at all.

> **Without a background file, `log_odds` and `hypergeometric` cannot run.**
> There is no bundled background corpus. If you leave the background empty,
> ranking silently falls back to **TF-IDF** regardless of the method you picked.
> The numbers you get are then TF-IDF weights, not z-scores or p-values — see
> [Background corpus](../README.md#background-corpus) for how to build one.

**Controls.** Background JSON · Method.

---

## 5. Build the term co-occurrence network

**What it does.** Connects two terms when they appear in the same document more
often than chance predicts. The graph is built **only from the ranked terms of
stage 4**, not from every entity — that's what keeps it legible.

Each candidate edge is scored by **pointwise mutual information**:

> PMI(a,b) = log[ P(a,b) / (P(a)·P(b)) ]

PMI asks whether a pair co-occurs *more than their individual frequencies would
predict*. Two common terms co-occurring often is unremarkable and scores near
zero; a specific pair that reliably travels together scores high. Edges need at
least 2 co-mentions and positive PMI to survive.

Finally the graph is trimmed to its **150 most-connected nodes** so the rendered
network stays readable.

Co-occurrence is always counted at the **whole-document** level: two terms in
the same paper are co-occurring, however far apart. (`Config.cooccurrence_window`
advertises a `"sentence"` option that is not implemented — the value is never
read.)

**What it produces.** `cooccurrence.html` — an interactive 2D network (drag
nodes, hover for document frequency and PMI) — plus a rotatable 3D version.
Nodes are sized by document frequency.

**Controls.** No GUI controls; adjust `Config.min_cooccurrence`, `min_pmi`, and
`max_graph_nodes` via the Python API.

---

## 6. Propose hypothesis candidates

**What it does.** The ABC step, run over the co-occurrence graph from stage 5.
For each term **A**, it walks two hops out to reach candidate terms **C**, then
keeps the A–C pair only if:

- they share at least **2 intermediate** terms B, **and**
- they **never co-occur directly** (0 shared documents).

That second condition is the whole idea — a pair that already co-occurs isn't a
discovery, it's a known fact. Candidates are scored by summing
`PMI(A,B) × PMI(B,C)` over every shared intermediate, which rewards pairs linked
through *specific* intermediates and penalizes ones linked only through generic
hubs. The top 100 are kept.

**Open vs. exhaustive.** Leave **ABC anchors** empty and every node is tried as
A (exhaustive). Name a few concepts and only those seed the search — "open
discovery" from a starting point you care about, which is usually what you want.

**A limit worth knowing.** Because stage 5 trims to 150 nodes, ABC only ever
sees those 150 terms. Candidates are drawn from the densest part of your term
network, not the whole corpus.

**What it produces.** `hypothesis_candidates.csv` and the **Hypotheses** tab:
concept A, concept C, the shared intermediates, the score, and the direct
co-occurrence count (0 by construction).

**Controls.** ABC anchors.

---

## 7. Cluster terms semantically *(optional)*

**What it does.** Embeds each ranked term with **PubMedBERT** (mean-pooled over
the token embeddings), L2-normalizes, and runs **KMeans**. Each cluster is
labeled by the member term closest to the centroid.

**Why.** The ranked list fragments across surface variants — `nmda receptor`,
`nmdar`, and `glutamate receptor` compete as separate rows when they're one
concept. Clustering groups them so you read concepts instead of strings, and
recolors the co-occurrence graph by cluster so related regions become visible.

In the GUI this is the **Cluster terms** button, run on demand after a pipeline
run rather than as part of it — the first use downloads the model.

**What it produces.** The **Clusters** tab, `term_clusters.csv`, a recolored
co-occurrence graph, and `term_clusters.html` — a 2D scatter of the embedding
space, every term a point colored by cluster, laid out with UMAP (falling back
to t-SNE, then PCA).

**Requires.** The `embed` extra (transformers + torch), and `viz` for the
scatter.

**Controls.** Clusters (target count, default 12) · the Cluster terms button.

---

## 8. Map the citation networks *(optional)*

**What it does.** Asks **NIH iCite** for the citation record of every
PMID-bearing paper in the corpus, then builds two directed graphs from a single
fetch:

**Paper → paper.** An edge means "this corpus paper cites that corpus paper" —
links to papers outside your set are ignored, so what you see is your corpus's
internal structure. Papers are ranked two ways: **in-corpus citations**
(in-degree — how foundational a paper is *to your topic*) and **global
citations** (iCite's count across all of PubMed — how big it is generally). A
paper high on the first and modest on the second is a specialty cornerstone.

**Author → author.** Projected from those same links: when corpus paper P cites
corpus paper Q, every author of P gets an edge to every author of Q, with shared
authors dropped so self-citation doesn't inflate anyone. Edge weight is how many
times that author cited that other author. Authors rank by weighted in-degree.

**Limits.** Only PMID-bearing documents can appear — PDFs and reference records
without a PMID are skipped entirely, so a PDF-only corpus produces no citation
network. Author matching is by name string, so name variants split and common
names collide. Pair this with stage 2: expand first, then see what the enlarged
set is built on.

**What it produces.** `citation_ranking.csv`, `author_ranking.csv`, and 2D + 3D
networks for both, nodes sized by in-corpus citations.

**Controls.** Citation network (iCite).

---

## 9. Write the outputs

Everything lands in the output folder and is listed in the GUI's **Outputs** tab
with an Open button.

| File | What it holds |
|---|---|
| `ranked_terms.csv` | stage 4: term, score, corpus count, doc frequency, background count |
| `hypothesis_candidates.csv` | stage 6: A, C, intermediates, score, direct co-occurrence |
| `cooccurrence.html` / `_3d.html` | stage 5: the term network |
| `term_clusters.csv` / `.html` | stage 7: cluster membership and the embedding scatter |
| `citation_ranking.csv` | stage 8: papers by in-corpus and global citations |
| `citation_network.html` / `_3d.html` | stage 8: the paper network |
| `author_ranking.csv` | stage 8: authors by in-corpus citations |
| `author_network.html` / `_3d.html` | stage 8: the author network |

---

## Reading the results honestly

- **Co-occurrence is not causation**, and an ABC candidate is not a finding. It
  says two concepts sit in a suggestive configuration in the text — the next
  step is reading the intermediates and deciding whether the link is mechanistic
  or an artifact of how the fields write.
- **A missing background silently changes the units** of your scores (stage 4).
- **The NER engine changes what "entity" means** (stage 3). Regex-fallback runs
  produce term lists that look plausible and aren't biomedical.
- **Every result describes your corpus**, including its biases. If the corpus
  came from one query, the leads describe that query.
- **The trimmed graph bounds the search.** Stages 5–6 work on 150 terms.
