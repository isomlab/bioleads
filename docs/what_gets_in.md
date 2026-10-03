# What gets in, and what gets cut

A paper has to clear several gates before it appears as a node, and an author has to clear
a different set. Each gate is useful, and each one has removed something somebody wanted.
This is the whole chain in order, with the defaults, so a missing paper can be traced to the
step that dropped it rather than guessed at.

Companion to [how_it_works.md](how_it_works.md), which explains *why* each stage exists.

---

## Part 1 · Becoming a seed

A **seed** is a document the search returned. Everything else arrived by citation expansion
and is marked `expanded` in its metadata. The distinction runs through every later step,
because **seeds are exempt from almost every filter**: a seed is in the picture because it
is what was asked for, not because of how it scored.

### 1. Retrieval

`esearch` is called with **`sort=relevance`** — PubMed's "Best Match". This has to be asked
for: **E-utilities' own default is most-recently-indexed**, which is not a ranking of
anything useful. The sort decides which records survive `pubmed_retmax` (default **1000**),
not just their order.

### 2. The first exclusion, and it is silent

**Records with no abstract are dropped on arrival.** A title-only record cannot be matched,
profiled, or coloured, so it never becomes a document at all. If a PubMed hit count exceeds
the corpus size and nothing else explains the gap, this is usually why.

### 3. What counts as a seed

| Source | Seed? |
|---|---|
| `--pubmed` query results | yes |
| `--pmids` | yes |
| `--refs` reference-manager export | yes |
| `--texts` / documents passed directly | yes |
| anything citation expansion discovered | **no** |

**On a run with no expansion, every document is a seed.** That has consequences further
down: see [the warning in Part 3](#when-the-thresholds-do-nothing).

### With `--pmids` or `--refs` and no query

Worth its own paragraph, because three things behave differently and only one of them is
obvious.

- **Order is yours.** `parse_pmid_input` strips any `PMID:` prefix, splits on whitespace,
  commas or semicolons, and de-duplicates **keeping first-seen order**. `@file.txt` is read
  the same way. No `esearch` runs, so there is no Best Match — you named the papers.
- **Seed ranking does nothing.** It ranks on query terms and there are none, so the list is
  returned untouched and no `seed_rank` is set. **`expand_seed_profile_n` therefore means
  "the first n PMIDs as you listed them."** That is real control if your list is ordered by
  importance, and a trap if you pasted an unordered export. **Put the papers you mean
  first.**
- **The `seeds` gate still works, and this is the point:** its terms are compiled from the
  seed papers' own text, not from a query, so it needs no query at all. The `terms` gate, by
  contrast, is guarded on there being a query and **silently filters nothing**.
- **No colouring and no matches network**, since annotation is guarded on the query too. With
  nothing asked, every node would be "unknown", which would read as a finding rather than as
  "nothing was asked".
- The same abstract rule applies: `fetch_pubmed_by_ids` reports **"retrieved N document(s)
  with usable text"**, and N can be smaller than the number of IDs pasted.

### 4. Seed ranking

Seeds are ranked by `sources.seed_rank_key`, used to choose which build the relevance
profile. Three signals, in order:

1. **A query term in the title.** A paper titled for the thing is about the thing.
2. **How often the terms occur** in title plus abstract.
3. **How many distinct terms** appear.

PubMed's order only breaks ties. **Counting presence instead would not work**: on
`TMEM184C OR TM184C` all three seeds contain a term, so presence ties them and the decision
falls back to arrival order. The discriminator is 6 occurrences and a title hit against 1
occurrence buried in an abstract. A paper about a gene names it repeatedly; a paper that
merely lists it names it once.

Each seed carries `seed_rank`, `seed_query_hits` and `seed_title_hit`, so the ranking can be
inspected rather than trusted. With no query, or a query of nothing but author and journal
tags, PubMed's order is kept unchanged.

---

## Part 2 · Joining the corpus

### 5. The walk is never gated

Expansion is breadth-first over PMIDs: `expand_rounds` deep, following `expand_link`, from
`expand_source`, capped at `expand_max` (default **1000**, counting seeds). **Nothing below
gates the walk** — the next round's frontier is chosen from PMIDs before any record is
fetched, so **a paper that is later rejected has already contributed its links.**

### 6. The collection gate

`expand_gate` decides what a discovered paper must do to stay. **It never applies to seeds.**

| `expand_gate` | A discovered paper is kept if… |
|---|---|
| **`seeds`** *(default)* | it contains at least `expand_seed_min_share` (default **0.10**) of a profile built from the top `expand_seed_profile_n` ranked seeds. **Needs no query — the profile comes from the seeds' own text** |
| `terms` | its title or abstract literally contains at least one query term. **Needs a query; with `--pmids` alone it filters nothing** |
| `off` | always |

**The profile** is the terms the chosen seeds share, weighted by **how many seeds mention a
term, not how often**: a word used forty times in one paper describes that paper, a word
used once in each of eight describes the topic. The score is the **share of the profile a
candidate contains**, which reads plainly — 0.10 means the paper names a tenth of what the
seeds are about — and does not reward length, because the denominator is the profile.

**`expand_seed_profile_n` is the control that matters most.** Measured on
`TMEM184C OR TM184C`, whose three seeds are one mechanism paper and two genomics case
reports that merely name the gene inside a copy-number region:

| Seeds in profile | Kept of 582 | What came back |
|---|---|---|
| all 3 | 107 | hypertrichosis, SOX3 insertions, Boer goats, sheep resequencing |
| **1** | **19** | GPCR activation, autophagosome–lysosome fusion, mini-G probes, GRK5/6 and β-arrestin bias, Atg8 |

**Know what `terms` does on a rare symbol.** On that same query it kept **0 of 582**,
because PubMed had already found every record containing those strings and they were all
seeds. Expansion finds neighbours, and a paper's neighbours are mostly about other things.
The terms gate earns its place on a broad query where retrieval is incomplete.

**Degenerate inputs are no-ops, never wipeouts.** A query with no parsable term, or a corpus
with no profile, filters nothing.

---

## Part 3 · Entering the paper citation network

### 7. Having a PMID

Nodes are corpus documents **with a PMID**. A PDF or a reference-manager entry without one
is absent from every citation network, however relevant it is.

### 8. Being cited *inside the corpus*

**Edges are intra-corpus citations only.** A paper cited a thousand times by work outside
the corpus has an in-corpus degree of zero. `global_citations` is carried on the node and is
a different number entirely.

### 9. `min_paper_degree` — **seeds exempt**

Iterated to a fixed point rather than applied once, so the picture cannot contradict the
control that drew it. **The promise is "every non-seed node you see clears the number".**

Seeds still count toward their neighbours' degree, so exempting a seed neither inflates a
neighbour nor rescues one that only reaches the threshold through it.

### 10. `max_graph_nodes` (default 150) — **seeds first**

All seeds are kept, then the remaining slots go to the highest in-corpus citations, tie-broken
by global citations. **If the seeds alone exceed the cap the cap wins**, and the log says how
many seeds were dropped.

**Why this matters:** ranking by citations drops exactly the paper a search was for. A real
`TM184C` run grew to 910 documents and the seed was not among the 150 nodes drawn, was absent
from `citation_ranking.csv`, and nothing said so.

### Reading a thinly linked seed

A seed with few neighbours is, in rough order of likelihood:

1. **a lightly cited paper** — much the commonest, since most papers are cited rarely;
2. **a paper the corpus is not about** — cited elsewhere, just not by anything this search
   pulled in;
3. **a new paper**, not cited yet — the rarest, and the striking one.

**The tool does not choose for you.** `year` and `global_citations` are on every node and
separate the cases, and the degree log reports how many spared seeds are cited outside the
corpus.

### When the thresholds do nothing

**On a run with no expansion every document is a seed, so `min_paper_degree`,
`min_author_degree` and `min_author_papers` have no effect at all.** They act on what
expansion brought in. This is deliberate, and it is the price of never hiding what was asked
for.

---

## Part 4 · Entering the author networks

### 11. Having a byline

**One node per senior (last) author.** A paper whose record carries no author list cannot be
placed and is skipped — it contributes neither a node nor an edge.

### 12. Seed authors

An author is a seed author if **any** of their corpus papers is a seed. One paper is all a
search needs to have found.

### 13. `min_author_degree`, `min_author_papers`, `max_graph_nodes` — **seeds exempt at all three**

`min_author_papers` applies only to the paper-count view. The trim ranks by whatever the view
is about — citations for the citation view, papers for the paper-count view — because ranking
by citations while displaying paper counts would drop the prolific-but-uncited labs that view
exists to show.

---

## Part 5 · The matches-only network

`citation_network_matches.html` holds the nodes classified `all` or `partial` — every paper
whose own title or abstract contains some or all of the query's terms.

**Seeds are not exempt here**, and should not be: that network's definition *is* containing
the terms. A grey seed belongs out of it.

**Edges are induced**, so a citation survives only when both ends matched. Paths between
matching papers often run through papers that matched nothing, so this network is routinely
sparser and more fragmented than the same nodes look inside the full graph. **That is a
property of the selection, not a finding about the literature.**

---

## Tracing a missing paper

| It is missing from… | Check, in order |
|---|---|
| the corpus | did it have an abstract (step 2); did the gate drop it (step 6); was `expand_max` reached |
| the citation network | does it have a PMID (7); `min_paper_degree` (9); `max_graph_nodes` (10) |
| the author networks | does its record carry authors (11); is it the *last* author you are looking for (11) |
| the matches network | does its text actually contain a query term — a PubMed hit need not |

The run log names every drop with a count. `run.json` records the full resolved config, so a
folder found months later still says which thresholds produced it.
