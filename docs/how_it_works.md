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

**What it does.** Optional. Starts from the documents that carry a PMID and
walks the citation graph outward, fetching what it finds and appending it to the
corpus. Local PDFs without a PMID can't seed this — there's nothing to follow.

This stage is where bioleads does something that isn't just plumbing, so it's
worth understanding the reasoning rather than only the controls.

### The asymmetry the design rests on

Citation links point two ways, and **the two directions are not equally
reliable**:

- **Forward — papers that *cite* your seed.** To cite a paper you generally have
  to engage with what it showed. The citing set therefore clusters *around the
  seed's topic*. High precision, limited recall.
- **Backward — papers your seed *cites*.** A reference list is everything the
  authors needed: the method, the reagent, the statistical tool, the mouse line,
  background from an adjacent field, a courtesy citation. It is a *union of
  topics*, only some of which are the topic you care about. High recall, poor
  precision.

Plain snowballing treats both directions the same, which is why it drifts: one
round backward through a reference list drags in every field the seed happened
to touch, and the next round expands from *those*, compounding the drift.

But the backward direction is also where the interesting material lives — the
foundational work, the older literature, the papers that ABC discovery (stage 6)
needs in order to find a connection nobody has stated. You don't want to skip
it. You want to *filter* it.

### The move: use the reliable direction to filter the unreliable one

That's what `relevance` does. It spends the precision of the forward direction
on the recall of the backward one:

1. **Phase 1 — build a topic profile from forward citations.** Collect the
   papers citing your seeds. Because that set is topically concentrated, seeds +
   citers together are a decent empirical description of "what this topic looks
   like". They're condensed into a single **profile vector**: the mean of the
   unit-normalized PubMedBERT embeddings of those documents.
2. **Phase 2 — score the backward references against it.** Collect the seeds'
   references — the diffuse, high-recall direction — embed each one, and rank it
   by **cosine similarity to the profile**. Keep only the top **K**. The rest
   are discarded before they ever reach the corpus.

The profile is a **Rocchio query vector**, which means it has a negative half as
well:

> q = normalize( centroid(profile) − γ · centroid(worst-scoring candidates) )

Pointing the gate only *toward* the topic is not the same as pointing it *away
from* what the topic isn't. A methods paper that shares the profile's technique
vocabulary can sit as close to a positive centroid as a genuinely on-topic paper
does — the centroid has no way to tell them apart, because the profile documents
use that technique vocabulary too. Subtracting a negative centroid is what
creates the separation.

**The negatives come free, from the candidate pool itself.** Phase 2 exists
precisely because a reference list is mostly off-topic, which means its
low-scoring tail is already a supply of *hard* negatives — citation-adjacent,
plausible, and wrong — which is exactly the region the gate has to discriminate.
So candidates are scored twice: once against the positive centroid to find the
tail, then again against the full Rocchio vector. No extra fetches, and no extra
model calls (the candidate embeddings are computed once and reused).

Guards: the negative term switches off when the pool is smaller than
`rocchio_min_candidates` (its tail wouldn't mean anything), and the tail is
clamped so it can never reach into the top-K the gate is about to keep. Setting
`rocchio_gamma` to 0 reduces the whole thing to the positive-only centroid.

The result is meant to be backward-direction *recall* at forward-direction
*precision*: you reach the foundational literature without inheriting the
reference list's off-topic bulk.

**No model is trained here.** Nothing is fit, and there are no learned
parameters — the profile is a weighted difference of two averages, and the gate
is a rank cutoff. In information-retrieval terms this is **pseudo-relevance
feedback** in the classical Rocchio (1971) form: assume a set you retrieved is
relevant, build a query representation out of it, then re-rank with that. The
parts are all standard; what's specific to bioleads is *which* sets it trusts —
forward citers as the positive evidence, the backward tail as the negative — and
why those roles fall out of the citation direction.

**How relevance is measured.** With the `embed` extra installed, PubMedBERT
cosine as described above. Without it, the same shape in a cheaper space: the
profile becomes a term vector over the NER entities, weighted by how many
profile documents mention each term (a term shared across the topic's papers is
more characteristic than a one-off), and candidates are scored by cosine against
it. The fallback is automatic, and a failure in the embedding path degrades to
it rather than sinking the run.

### Measured: the precision half of this claim does not hold

The asymmetry above is the design's *rationale*. It is also an empirical claim,
and [the benchmark](benchmark.md) now tests it against systematic reviews
instead of argument. On 40 reviews, 5 seeds each, one round, with the
publication-year correction applied:

| direction | median precision | median recall | median candidates |
|---|---|---|---|
| forward (`cited_by`) | 0.0152 | 0.1154 | 487 |
| backward (`references`) | **0.0530** | **0.1667** | 164 |

**Backward wins on both, in 33 of the 40 reviews** (median precision ratio 0.43).
The two directions surface a comparable number of true hits — 403 forward against
397 backward — but forward has to drag in 2.88× as many candidates to do it, and
one seed set pulled 30,848.

So the direction this pipeline treats as *reliable* is, on this measure, the
noisy one. Two consequences worth being blunt about:

- **The stated justification for `relevance` is unsupported.** It may still build
  a better corpus than `bfs` — that is a separate question, measured separately —
  but not for the reason given.
- **The ungated half looks like the wrong half.** Phase 1 adds *every* forward
  citer without filtering and only gates the backward references. The measurement
  says the filtering effort is being spent on the cleaner direction.

One caveat, stated once and not used to explain the result away: the ground truth
*is* a reference list, so "can a seed's references predict other references" may
sit closer to backward's home turf than a topic-labelled benchmark would. That
is a reason to want a second, non-citation ground truth — not a reason to keep
asserting an asymmetry that measured the other way.

### Measured: the gate governs 5% of what the strategy returns

A second run (12 reviews, 5 seeds, gamma swept, titles-only scoring) measured the
gate itself:

| arm | median P | median R | median F1 | total retrieved | total hits |
|---|---|---|---|---|---|
| `backward` (ungated) | 0.0399 | 0.1426 | **0.0634** | 2,342 | 95 |
| `both` (= bfs) | 0.0244 | 0.3252 | 0.0442 | 49,683 | 205 |
| `relevance` γ=0 | 0.0247 | 0.2659 | 0.0434 | 47,975 | 172 |
| `relevance` γ=0.25 | 0.0247 | 0.2659 | 0.0434 | 47,974 | 173 |
| `relevance` γ=0.5 | 0.0263 | 0.2726 | 0.0461 | 47,972 | 177 |

Two things fall out.

**The negative term is inert at its default.** Going from γ=0 to γ=0.25 changes
the outcome in 4 of 12 reviews and moves the total from 172 hits to 173 — one
document across nearly 48,000 retrieved. γ=0.5 moves it to 177.

The obvious suspect was the input: scoring on iCite *titles* would leave every
candidate pointing much the same way in embedding space, so the tail centroid
would nearly parallel the positive one and subtracting it would rescale more than
it reorients. **That explanation was tested and is wrong.** Re-run with
`--abstracts`, scoring on title + abstract, the gate finds *exactly* the same
number of hits — 172, 173, 172 for γ = 0, 0.25, 0.5 — against 172, 173, 177 on
titles. Richer text bought nothing; at γ=0.5 it cost 5 hits.

So the negative term is inert regardless of how good the text is, which points
back at the structural problem below rather than at the scoring.

**The gate is attached to the wrong 5%.** Compared with `both`, relevance
retrieves 3.4% fewer candidates and loses 15.6% of the hits. It looks
ineffective because it *is* barely acting: backward references are only 2,342 of
the ~49,700 documents this strategy returns. The other ~95% are forward citers,
added with no filtering at all. So the entire apparatus — profile, negative term,
top-K — tunes a knob controlling one twentieth of the output, and (per the
measurement above) the cleaner twentieth at that.

Plain `backward` retrieves 2,342 documents for 95 hits — 4.1% precision, against
0.4% for `both`. On this benchmark, doing less is worth more.

This also explains the inert negative term without appealing to text quality: no
setting of γ can move an aggregate when the thing γ controls is one twentieth of
the output.

The obvious implication is to gate the forward direction rather than the backward
one, or to stop adding it wholesale. That is a design change, not a
documentation change, and it has not been made.

### The assumption, and when it breaks

"Forward converges" is a heuristic, not a law. It holds for a **topical research
seed**, where citations track the finding. It weakens badly for a **method,
tool, or review** paper: those get cited across every field that uses them, so
the citing set is broad, the profile averages over unrelated topics, and the
phase-2 gate stops discriminating. If your seeds are methods papers, expect the
filter to behave more like plain BFS.

Two smaller caveats: the profile is a **single centroid**, so a genuinely
two-topic seed set averages into the space between them and may rank papers from
*neither* topic highly; and top-**K** is a fixed count, not a similarity
threshold, so K papers are always kept even when none of them are close.

### The other strategy

`bfs` — plain snowball, no gating. Each round takes the current frontier, adds
everything linked to it, and chases those in the next round, until it hits the
record cap. Direction is yours: `references` (backward), `cited_by` (forward),
or `both`. Use it when you want exhaustive coverage of a small, tight seed set
and intend to do the filtering yourself.

### Controls

Citation expansion rounds (0 = off) · Follow · Source (NCBI ELink, NIH iCite, or
the union) · Strategy · Relevance top-K · Max records.

Two things about the controls are easy to get wrong:

- **Selecting `relevance` runs expansion even with rounds set to 0.** The rounds
  spinbox drives `bfs` only. Relevance depth is a separate pair of settings
  (`Config.expand_fwd_rounds` and `expand_back_rounds`, both 1 by default) that
  the GUI doesn't expose — one round forward, one round backward.
- **`Follow` is ignored by `relevance`**, which by construction always goes
  forward first and backward second.

The Rocchio settings (`rocchio_gamma`, `rocchio_neg_frac`,
`rocchio_min_candidates`) are Python-API only — the GUI and CLI use the
defaults.

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
