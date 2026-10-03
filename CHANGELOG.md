# Changelog

Notable changes to bioleads. Versions follow [semantic versioning](https://semver.org);
while the major version is 0, a minor bump may change defaults.

## Unreleased

### The 3D tour flies into the node, and the node is a real sphere

- **The focus node is a `Surface` sphere in the data**, so the camera coming
  closer is what makes it bigger, and the orbit turns it. Three substitutes were
  tried first and all of them were wrong: a marker is sized in **screen pixels**
  and never grows however far in the flight goes; an HTML disc over the canvas
  does grow, but it is a flat sticker that cannot rotate, shade, or let an edge
  pass in front of it; and `Mesh3d` **renders its own triangle edges**, giving a
  wireframe globe that `contour.show`, `flatshading` and both normals epsilons
  all failed to remove.
- The sphere is scaled **per axis by that axis's span**. The three axes carry
  different data ranges but are drawn into one cube, so equal radii in data
  units would be an ellipsoid.
- **The flight swings 55° around the node** while it closes in, holding whatever
  viewing direction the reader has dragged the scene to. A tour that only dollies
  reads as a slideshow.

### The header is one line, and the network has the window

- **The big `<h1>` and the boxed legend are gone**, replaced by a single 12px
  bar: title, the query terms, three swatches reading *every / some / none*, and
  an ⓘ. The caveats that used to take three lines on every page, about literal
  matching and about expansion papers never going through the query, are in that
  tooltip. They are read once, not on every glance.
- **The canvas fills what is left** rather than a fixed 800px, with the card
  padding and borders removed. The graph is the page now.
- **On every page, not only colored ones.** The function that does this began
  life as the color key and was called only for graphs that had one, so an
  uncolored network kept the big heading and a fixed 800px canvas. It is
  `_inject_header_bar` now, and the key is the part that is conditional.

### The scene opens larger, and the focus comes up on a dimmer

- **The 3D page opens at aspectratio 2.4, not 1.** gl3d clamps the camera's
  distance and Plotly's default eye of 1.25 per axis is already past that
  clamp, so the scene cannot be opened closer by moving the camera: the graph
  just sits small in the middle of a lot of white. The aspect ratio is the only
  lever, and it is the one the tour already uses. *Reset view* returns here.
- **The focus color ramps up and down with the flight** instead of switching
  on, in **both** views, on the opening zoom and on every move from one node to
  the next. The focus node and its edges start at the highlight mixed most of
  the way to white and are blended to full strength over the flight. Taking the
  quiet end from the same hue keeps it a change in intensity rather than two
  different marks.
- **The ramp runs on the flight's progress, not on the zoom.** From one stop to
  the next the zoom does not change at all, so a ramp tied to it jumped straight
  to full and the color snapped, which is the thing the ramp was added to avoid.
- **The handover happens at the palest point.** The previous stop fades down
  over the first 38% of a flight, the new one is swapped in there and fades up
  over the rest, so the eye sees one highlight give way to another rather than a
  node changing color.
- **With nothing lit there is nothing to hand over**, so the opening zoom skips
  the swap and fades up over the whole flight. Leaving it in cost that first
  ramp more than a third of its length and made the highlight appear abruptly
  part-way through, which is what still read as snappy.
- **The ramp is eased, like the camera**, and in 2D the focus node's size and
  border grow over the flight rather than being written in one go. That jump
  was most of what remained of the snap however smoothly the color faded.
- 2D colors the node and its edges by hand for this. vis.js will recolor a
  selected node and its connections for free, but only instantly.

### Fixed: visited nodes stayed pink

- **Restoring a saved `undefined` does not undo a tint.** vis.js has already
  parsed the color that was set, and writing the key back as undefined leaves
  that parsed value in place. Every node the tour visited therefore kept its
  edges pink, and a long tour slowly turned the whole graph.
- An edge or node that never carried its own color is now put back to an
  explicit resting style. That style is defined once and used both by the
  options and by the restore, so the two cannot drift apart.
- After four stops exactly one node and its eight edges are tinted, which is
  the stop currently being shown, and after *Reset view* nothing is.
- **The record card is on that same ramp**, in both views, so it fades in and
  out exactly with the focus node and its edges. In 2D it keeps following the
  node it describes while it fades out, and only moves to the new one at the
  handover, where it is invisible. Driven separately the two
  drifted: the highlight came up over the flight and the card appeared at the
  end of it, which read as two events rather than one arrival.
- *Reset view* dims the highlight down over the flight home and removes the
  overlay traces when it lands, rather than switching them off at the start.
- **Hold 3.2 s → 2.0 s** in both views, so the step from node to node is
  quicker without the camera moving any faster.

### Fixed: every tour left the markers slightly larger than they started

- **A float comparison dropped the final restore.** `magnify` skipped steps
  smaller than 0.08 and made an exception for the exact end values 1 and MAG,
  which floating point defeats: the last value comes out as
  `1.0000000000000002`, the equality fails, the restore is dropped as too small
  a step, and the markers keep a little of each tour's growth. The last frame of
  a flight now forces the update instead of testing for equality.
- The baseline sizes are also captured before the first flight rather than
  lazily inside `magnify`, where they were already a frame late.

### A stop lights the node's own edges, in both views

- **The 3D tour now shows which papers the focus node connects to.** 2D has had
  this from vis.js, which repaints a selected node's edges; Plotly has no
  equivalent, because the whole graph is **one line trace** and a trace cannot
  be partly recolored. The node's own edges are drawn again on top as a second
  trace in the highlight color.
- The segments are computed in Python, once per stop, rather than walking the
  adjacency in the browser every time the camera lands.
- **Direction is dropped.** At a stop the question is which papers this one is
  connected to, not which way each citation points, so a reciprocal pair is one
  line rather than two drawn over each other.

### The 3D focus node is the size of the node, not of the screen

- **Its radius comes from that node's own marker**, so the only reason it gets
  bigger is that the camera came closer. A fixed fraction of the graph's span
  made every focus node the same enormous ball whatever it stood for, which hid
  the one thing a node's size means here: how often the paper was cited.
- Sized to end exactly as wide as the node's magnified marker, so it sits among
  its neighbours instead of swallowing them.
- **The flight goes to aspectratio 26, and the markers grow 2x rather than
  3.5x.** Both changes are about the same thing: at a stop you should be able to
  see that the most connected nodes are separate nodes. Zooming spreads them
  apart and magnifying pushes them back together, so the previous pair worked
  against itself in the densest part of the graph, which is exactly where the
  tour spends its time.
- **The cube does not fill the viewport.** The obvious constant for converting a
  scene unit to pixels is 2, the width of the cube, and it is wrong by about
  2.5x because Plotly's default eye sits well back. Measured instead, as
  `TOUR_SCENE_UNITS_ACROSS`; the first attempt made the sphere smaller than the
  marker it was covering.

### Fixed: three ways of zooming a Plotly 3D scene that do not work

Worth recording, because each one looks right in the code and silently is not.

- **Shortening `scene.camera.eye` does nothing.** gl3d clamps the camera's
  distance: below about 0.5 per axis the reported `camera.distance` stops
  changing and the frame is pixel-identical. Three successive reductions changed
  the view not at all.
- **Narrowing the axis ranges zooms, but it culls.** Plotly drops whatever falls
  outside a range, so closing in deleted most of the network. The 2D view never
  hides anything, and a tour that culls the thing it is touring is worse than no
  tour.
- **`scene.camera.center` is in units of half the aspect ratio.** A node at
  normalised position *n* sits at *n · k / 2*. Passing *n* unscaled centres the
  node only near *k* = 2 and drifts further off the further in the view goes, so
  every attempt to zoom harder also pushed the node toward the edge.

**The zoom that works is `scene.aspectratio`**, 1 to 7, which scales the scene
while the camera holds its distance. Nothing is hidden: the parts that no longer
fit fall outside the view exactly as they do when the 2D view zooms.

### One card, beside the node, shown when the camera lands

- **Both views share one design**, defined once and injected into both. They had
  each built their own markup, so a change to one quietly left the other on the
  previous version. Title as the headline, an accent rule, then quiet uppercase
  labels against dark values.
- **Every class is `bl-` prefixed.** pyvis ships Bootstrap, whose own `.row`
  captured the card and stacked each label above its value — **in the 2D page
  only**, which is how the two views drifted apart while each looked plausible
  alone.
- **The card waits for the camera.** In 2D the signal is `animationFinished`
  with a timer fallback, because an interrupted focus never fires that event;
  in 3D it hangs off the flight's completion.
- **2D places it from canvas coordinates on every redraw**, so it stays beside
  its node through later panning and zooming, offset by the node's scaled radius
  so it never covers the node it describes.

### The graphs are quieter: no arrowheads, no PMIDs under the nodes

- **Arrowheads are gone from the 2D citation graphs.** On a network this dense
  they stacked into a texture that read as noise rather than as direction. The
  graph is still directed, and the direction is in the hover and the heading.
- **No text prints under any marker.** PMIDs on the citation networks and
  **senior-author names on both author networks**, the citation one and the
  paper-count one, collided with their neighbours and hid the colors the graph
  encodes. A senior author's name is long enough to bury the node it belongs
  to. All of it stays in the hover and in the tour card. *pyvis substitutes the
  node id for a falsy label, so the font is zeroed rather than the label
  blanked.*
- Translucent blurred control panels with no borders, white node rims, thinner
  edges, a slim colorbar. **The controls moved to the bottom-left in both views**,
  out of the picture.

### Fixed: the 2D focus node was sized by the wrong attribute

- **`value` is what sizes a vis.js node, not `size`.** A node carrying a value
  has its `size` recomputed from it on every redraw, so the tour's size
  multiplier did nothing: the focused node measured **82 px against a 96 px
  neighbour** while claiming to be the subject of the stop. It is now the
  largest node on screen.

### Colors that work for red-green color blindness

- **Green and amber are gone.** That is the one pair a red-green color blind
  reader cannot separate, and roughly **one man in twelve** is. Matches are now
  **Okabe-Ito blue `#0072B2`** and **orange `#E69F00`**, which stay apart under
  deuteranopia, protanopia and tritanopia.
- They differ in **lightness** as well as hue, so they survive a greyscale print.
  A test asserts the gap.
- The tour highlight is Okabe-Ito **reddish purple `#CC79A7`**, and the size and
  border changes carry the signal on their own — the part that works whatever a
  reader can see.

### The tour panel says what identifies a paper, and stops there

- **Title, authors, PMID, year, journal.** Nothing else. The panel is read at a
  glance while the camera is moving; the counts, match detail and link stay on
  the hover.
- **An abbreviated byline that always keeps the senior author**:
  `Roy S, Huang H … Kornberg TB (4 authors)`. The last name in a biomedical
  byline is the lab the work came from, and it is what a plain truncation throws
  away first.

### Tour pacing is per view, and 2D is the slower one

- **The camera is slower (3D 4.2 s) and the wait after it lands is shorter
  (2.5 s).** Both at once, which the old single `TOUR_DWELL_MS` could not do:
  slowing the zoom used to eat the reading time.
- **2D flies for 6.5 s.** The same duration does not feel the same in both:
  vis.js `focus` changes zoom level as well as position, and **the scale change
  is what the eye reads as speed**, so what is gentle in a Plotly scene is abrupt
  here.

### Fixed: the 2D controls never ran at all

- **A syntax error in the injected script.** `"<div class=\"x\">"` written in a
  plain triple-quoted Python string collapsed to `"<div class="x">"`, the whole
  IIFE failed to parse, **and no handler was ever attached** — so the buttons did
  nothing and physics never stopped. Single quotes now, which need no escaping.
- **Every assertion about that page still passed**, because a substring test
  cannot see a syntax error. The suite now runs `node --check` over both
  injected scripts, skipping where node is absent.

### The node being visited is marked

- **2D:** recolored, enlarged 1.9x by `value` and given a white rim, restored
  when the tour moves on. Selection alone was too quiet to find on a crowded
  graph.
- **3D:** a sphere in the scene, described above. It began as a translucent
  ring over the node and that was not enough: the node kept its original size
  underneath, so zooming never showed it.

### Fixed: the network buttons could feel broken, and 3D had none of them

- **The layout now always stops.** It relied on a single vis.js event,
  `stabilizationIterationsDone`, which **does not reliably arrive on a large
  graph**. Until physics stops the main thread is busy enough that every button
  feels laggy or dead, and the status stayed on *settling…* forever. A second
  event and a **12-second watchdog** now guarantee a clickable page; the status
  says `settled (time limit)` when the watchdog is what stopped it.
- **The 3D views have the tour too** — *Play tour*, *Next*, *Reset view*, and the
  same ⓘ. They had none of it: no way to reach the most connected node and
  nowhere to read it.
- The 3D panel shows **the same record** as the 2D one, built by the same
  function.
- **The 3D camera is tweened, not snapped.** Plotly has no camera animation of
  its own, so the move is stepped over `TOUR_FLIGHT_MS` — the difference between
  a tour and a slideshow. There is no physics in a Plotly scene, so there is no
  Pause button there.

### A guided tour of the most connected nodes

- **Play tour** flies the camera to the most connected node, zooms in and shows
  **its whole record**, then steps to the next, ten stops by default. **Next** advances by
  hand and **Reset view** returns to the whole graph.
- **Degree, not size.** A node is large here because it was cited often; it is
  *connected* because it touches much of the corpus, and a tour of a network
  should follow the second. Ties break on the size attribute.
- The stops carry **the same text the hover shows**, built from one function, so
  the tour and the tooltip cannot drift into telling different stories about a
  node.
- **Paced to be read**, rather than long enough only to see that something had
  happened. The timings moved several times during this cycle; see *Tour pacing*
  above for where they ended up.
- **A record, not the hover summary.** Trimmed later in this cycle to what
  identifies a paper; see *The tour panel says what identifies a paper* above.
- Physics is switched off before each flight, since the camera cannot chase a
  node that is still being simulated.
- **This is a tour, not a video file.** Screen-record it to get a movie. Writing
  an `.mp4` directly would mean driving a headless browser — a few hundred
  megabytes of dependency for a package meant to be conda-installable — and the
  tour is what such a recorder would drive anyway.

### Matching uses PubMed's own expansion of the query

- **Searching `cytoneme` now colors a paper that says `cytonemes`.** The terms
  come from `esearch`'s `QueryTranslation` — the search PubMed actually ran —
  rather than from the words typed into the box, so plurals and PubMed's synonym
  mapping arrive without anyone adding a `*`.
- Measured on a live `cytoneme` search: **12 of 12 fetched papers matched with
  the expansion, 11 of 12 without.** The missing one says only "cytonemes".
- **A term is now a group of surface forms**, and a paper matches the concept if
  it contains any of them. Flattening would have been wrong: `classify` would
  then demand every form, so a paper saying only the plural could never be
  green.
- `AND` and `NOT` separate concepts; `OR` joins forms of one. The first form
  labels the group, chosen for readability — PubMed emits artifacts like
  `"nanotube s"` and a hover should not report that.
- **Two kinds of form are dropped.** `[MeSH Terms]` and other indexed fields,
  because a heading assigned by an indexer often appears nowhere in the abstract
  and matching on it would mark a paper for something its text does not say. And
  expansions past six forms: PubMed turns `tunneling` into nine words including
  `tunnelization`, so the group collapses to `tunnel*`, which covers the same
  ground in one.
- Feeds the coloring, the `terms` gate and the seed ranking alike, so **all
  three now agree with retrieval** instead of second-guessing it.
- Falls back to reading the raw query whenever there is no translation: a
  `--pmids` run, or a search that failed before `esearch` answered.

### Node tooltips wait for the cursor to stop

- **The hover was too sensitive.** vis.js defaults to a 300 ms tooltip delay,
  which fires while the cursor is still travelling, so on a dense graph the
  tooltips flicker up one after another and reading any single node is a
  struggle. Raised to **700 ms** (`citations.TOOLTIP_DELAY_MS`).

### The networks have a physics switch

- **Physics starts on load, as it always did, and a button pauses and resumes
  it.** It also stops by itself once stabilization finishes, so the graph is
  still by the time you try to click a node.
- **Why the button and not a fixed layout:** a server-side spring layout was
  tried first and was far worse — it collapsed a 150-node network onto a
  diagonal line of overlapping nodes with the labels piled on top of each other.
  The browser's force layout is what makes these graphs readable. What it needed
  was control, not removal. **Reverted.**
- The automatic stop existed before and **never ran**. It assumed pyvis's
  `network` object was already defined, when it is assigned inside
  `drawGraph()`; the injected script attached nothing and failed silently. It
  now waits for the object.

### The Citation networks defaults are now 3

- **`min_paper_degree`, `min_author_degree` and `min_author_papers` default to
  3**, matching `expand_rounds`.
- **Why a non-zero floor is now safe:** seeds are exempt from all three, so the
  papers a search actually returned can never be hidden by them. Before that
  exemption existed, any floor above zero risked deleting the run's own subject.
- An expanded corpus is mostly papers reached once that connect to nothing else.
  Drawing them makes a hairball. Three connections is roughly where a node
  starts saying something about the corpus rather than about the walk.
- **A run that used to draw everything will now draw less.** Set any of them to
  0 for the old behaviour.

### `expand_rounds` now defaults to 3

- Expansion is what turns a handful of search hits into a corpus worth drawing a
  network from, and **off by default meant the common case — a specific query
  returning a few papers — produced an empty picture with no explanation.**
- The gates added in this release are what make a default this high safe:
  discovered papers now have to earn their place.
- `--expand` previously hard-coded its own default of 0 instead of reading
  `Config`, so the CLI and the GUI could disagree. It reads `Config` now, as the
  other flags already did.
- **Watch for this if you have a config or script that relied on the old
  default.** A run that used to fetch nothing will now walk three rounds.

### Asking for more seeds than exist is harmless

- `expand_seed_profile_n = 10` on a query that returned 3 papers profiles all 3,
  exactly as `0` would. Nothing is clamped and nothing warns; the log reports
  the number actually used.

### Fixed: `--citations` crashed on a core-only install

- **The pyvis fallback did not fall back.** Without pyvis the writers drop to
  GraphML, and that path raised twice over: `nx.write_graphml` is bound to the
  lxml implementation and **imports lxml when called**, not when networkx is
  imported, and the node attributes carry `None` for an unknown citation count,
  which GraphML refuses.
- **That is the install the conda recipe builds**, so asking for `--citations`
  after `conda install bioleads` crashed instead of degrading — inside the very
  fallback that exists to prevent it.
- Now uses networkx's **pure-stdlib** GraphML writer. These graphs are capped at
  `max_graph_nodes`, so lxml's speed buys nothing and its absence cost
  everything. **No new dependency was added.**
- `None`-valued attributes are **dropped rather than zeroed**: an absent
  attribute means "not known", while 0 would assert a count nobody measured.
- Three tests pin it, including one that makes the lxml writer unusable and one
  that removes pyvis from the import system, so the fallback is exercised rather
  than assumed. **The suite now passes on a core-only install** as well as a full
  one.

### Fixed: a paper supplied twice counted twice

- **Documents are de-duplicated by `doc_id` across sources at load time**, first
  occurrence wins. Sources were de-duplicated only within themselves, so a PMID
  named in `--pmids` that the query had already returned arrived twice.
- It mattered because **the seed profile ranks a term by how many seeds mention
  it**: a duplicated paper double-weighted its own vocabulary, and could take
  two of the `expand_seed_profile_n` slots with one paper.
- This bites exactly where sources are combined, which is when someone
  re-supplies a paper the query already found.
- The log says how many duplicates were removed.

### Better help for the two expansion controls

- "Keep a found paper if" and "Seeds for relevance" now say what to set and
  when, with the measured numbers, rather than describing the mechanism: which
  gate needs a query, what happens on a rare symbol, and what the control means
  with PubMed IDs and no query at all.

### Fixed: a single-seed profile was alphabetical

- **`expand_seed_profile_n = 1` built its profile from the first sixty content
  words from *a* to *c*.** With one seed every term has a document frequency of
  one, so ranking on that alone had nothing left to sort by. The real profile
  began *accumulate, accumulation-a, activities, along, alphafold2, ancient,
  annotation…*
- Total frequency now breaks the tie, so the same seed yields *tm184c,
  gpcr-like, intercellular, protein, autophagic, autophagy, beta-arrestin,
  connectivity, conserved, exchange, gpcr, grk, homology…*
- **One seed is exactly the setting worth using when a query returns a mixed
  bag**, so this was broken where it was most needed. It still produced
  on-topic results, because an abstract's vocabulary is topical whichever sixty
  words you take, which is why nothing looked wrong.
- Document frequency still leads across several seeds: a term shared by all of
  them outranks one repeated in a single paper.

### `docs/what_gets_in.md`

- **Every gate a paper or an author has to clear, in order, with the defaults.**
  Written because the filters now have exceptions — seeds are exempt from most
  of them — and a rule with exceptions is not something to reconstruct from
  source each time.
- Includes the two silent exclusions that catch people out: **records with no
  abstract are dropped on arrival**, and **papers with no author list never
  reach the author networks**.
- Ends with a table for tracing a paper that is missing from a particular
  output, which is the question this document exists to answer.

### Seeds survive every filter, not only the display trim

- **`min_paper_degree`, `min_author_degree` and `min_author_papers` no longer
  remove seeds.** A seed is in the picture because the search returned it, not
  because of how connected it turned out to be. **A seed with few neighbours is
  usually a lightly cited paper and only sometimes a new one**, the uncited case
  being much the commoner: most papers are cited rarely, few are new at any
  moment. Neither is a reason to hide the thing that was asked for.
- The log now says how many spared seeds are cited **outside** this corpus,
  since that is what separates "not cited yet" from "not much cited".
- **The exemption is for seeds only.** A non-seed of the same degree is still
  dropped, so the control is not quietly disabled, and the log says how many
  seeds were kept below the threshold rather than leaving it to be noticed.
- **Seeds still count toward their neighbours' degree**, so exempting one does
  not inflate anyone else and does not rescue a neighbour that only reaches the
  threshold through it.
- **The promise these controls make has changed, and it is worth being clear
  about.** It was "every node you see clears the number". It is now "every
  **non-seed** node you see clears the number". Seven tests encoded the old
  promise and were rewritten rather than deleted.
- **The consequence to know: on a run with no expansion every document is a
  seed, so these three controls do nothing.** They act on what expansion
  brought in, which is what they were useful for anyway.

### Seeds are ranked, so "the top n" means something

- **E-utilities does not sort by relevance.** Its default returns the most
  recently indexed records. `fetch_pubmed` now asks for `sort=relevance` ("Best
  Match"), which changes which records survive `--max-hits` as well as their
  order. Everything before this took "the first n seeds" to mean the n most
  recently indexed.
- **Best Match alone is still not enough.** On `TMEM184C OR TM184C` it returns a
  goat copy-number paper first and the paper the gene is named for third.
- **So seeds are ranked on what they say**, by three signals in order:
  **a query term in the title**, then **how often the terms occur**, then **how
  many distinct terms** appear. PubMed's order only breaks ties.
- **Counting presence would not have worked.** All three of those seeds contain
  a term exactly once by that measure, which ties them and hands the decision
  back to arrival order. The discriminator is 6 occurrences and a title hit
  against 1 occurrence buried in an abstract.
- The run log prints the chosen profile seeds with their counts, and each seed
  carries `seed_rank`, `seed_query_hits` and `seed_title_hit` in its metadata,
  so the ranking can be checked rather than trusted.

### The display trim no longer drops the papers the search was about

- **Seeds survive the `max_graph_nodes` trim.** Ranking by in-corpus citations
  drops exactly the paper a search was for: a thinly cited paper, or a recent
  one, loses to twenty-year-old reviews.
- **This was real, not hypothetical.** A `TM184C` run grew to 910 documents and
  the seed — three weeks old, one global citation — was not among the 150 nodes
  drawn, was absent from `citation_ranking.csv`, and nothing said so.
- Seeds are kept first, remaining slots go to the highest-ranked non-seeds, and
  the message now reports both counts. **If the seeds alone exceed the cap the
  cap still wins**, and the message says how many seeds were dropped.
- The same protection covers the **senior-author networks**: an author is a seed
  author if any of their corpus papers is a seed.
- Graphs with no `seed` attribute trim exactly as before.

### Expansion keeps a paper only if it looks like the seeds

- **`expand_gate`** replaces the boolean added earlier the same day:
  `"seeds"` (default), `"terms"`, or `"off"`. Seeds are never filtered by any
  of them.
- **`"seeds"` builds a profile from the seed papers' own text** — the terms most
  of them share, weighted by how many seeds mention a term rather than how often,
  so a word repeated forty times in one paper does not define the topic — and
  keeps a discovered paper if it contains at least `expand_seed_min_share` of
  that profile.
- **`expand_seed_profile_n` caps how many seeds build the profile**, taken in the
  ranked order above, and it matters far more than it sounds. Measured on
  `TMEM184C OR TM184C`, whose three seeds are one mechanism paper and **two
  genomics case reports that merely name the gene inside a copy-number region**:

  | Seeds in profile | Kept of 582 | What came back |
  |---|---|---|
  | all 3 | 107 | hypertrichosis, SOX3 insertions, **Boer goats, sheep resequencing** |
  | **1** | **19** | **GPCR activation, autophagosome-lysosome fusion, mini-G probes, GRK5/6 and β-arrestin bias, Atg8** |

  A profile over all three described chromosomes. A profile over the first
  described the biology. **Both are settable in the GUI** under "Keep a found
  paper if" and "Seeds for relevance".

### Expansion can instead be required to name a query term

- **`expand_gate = "terms"`.** A paper discovered by citation expansion is kept
  only if its own title or abstract contains at least one of the query's terms.
  **Seeds are never filtered**: PubMed may have matched them on a MeSH term or
  on full text not fetched here.
- The test is the same literal one that colors the networks, so the gate and
  the colors can never disagree.
- A query with no searchable term — all author or journal tags — filters
  nothing, rather than silently discarding the whole expansion.
- **Know what this does to a gene-symbol query.** Measured on a real run:
  `TMEM184C OR TM184C` expanded to 582 discovered papers and **the gate kept
  none of them**, because PubMed had already found every record containing those
  strings and they were all seeds. **Expansion finds neighbours, and a paper's
  neighbours are mostly about other things.** The gate earns its place on a
  broad query where retrieval is incomplete, not on a rare symbol where it is
  already exhaustive. The log line says exactly how many were dropped and why.
- This gates **collection, not the walk**: the next round's frontier is chosen
  from PMIDs before anything is fetched, so a rejected paper has already
  contributed its links.

### An empty PubMed search now explains itself

- **"No documents loaded. Check your inputs."** was equally true of a typo, a
  dead network, a bad API key and a query that simply matches nothing. It now
  carries what PubMed actually did:

  > No documents loaded. PubMed returned 0 record(s) for:
  > `"TMEM184C"[All Fields] AND "TM184C"[All Fields]`. OutputMessage: No items
  > found.

- **PubMed's own translation of the query is the useful part.** Seeing an `AND`
  of two terms is what tells you no single record holds both, which is a fact
  about the literature rather than a fault to fix. Any `WarningList` or
  `ErrorList` PubMed returns is carried through too.
- The Log tab gets the same lines on an empty search even when other sources
  supplied documents, so a query that quietly matched nothing is visible rather
  than silent.
- `fetch_pubmed(..., report=dict)` fills in `count`, `translation` and
  `warnings`; `describe_pubmed_search(report)` turns it into the sentence.

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

### Every network is colored by query-term containment, not just the 2D one

- **All five network views now color by the query when a `--pubmed` search was
  run**: the paper citation network in 2D and 3D, the matches-only subnetwork,
  and both senior-author networks in 2D and 3D. Previously only the 2D paper
  network was colored, so the same corpus told different stories depending on
  which file was opened.
- **An author is colored by their single best-matching paper**, not by pooling
  terms across their papers. An author with one paper naming every term is
  green; an author with two papers naming one term each is amber, because
  pooling would claim a paper that does not exist. The hover gives `k of n`
  papers naming any term, and the legend says so rather than leaving it to be
  assumed.
- **A run with no text query is unchanged.** The 3D views keep their
  citation-count colorscale instead of being painted the "nothing matched"
  color, which is what returning no colors rather than a dict of greys buys.
- `querymatch.annotate_author_graph` is the API. It needs
  `graph["paper_senior"]`, which `build_author_citation_graph` now carries.
- **Fixed in passing: GraphML writes of the author graph.** That `paper_senior`
  map is a dict on `graph`, which GraphML cannot store, so the pyvis fallback
  raised on a core-only install — the one the conda recipe builds. Both
  fallbacks now write a copy with graph-level attributes dropped.

### Fixed: nothing was ever colored on a real PubMed search

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

### Citation-network nodes are colored by whether the paper contains the query

- **After a `--pubmed` text search, each paper in `citation_network.html` is
  colored by how many of the query's terms its title and abstract actually
  contain**: green for all, amber for some, grey for none. The hover says which
  terms were found, and flags a paper added by citation expansion.
- **This is not the same as "was it a hit".** PubMed can match on a MeSH term,
  on automatic term mapping, or on full text never fetched here, so a grey node
  can be a perfectly good hit. `--expand` also adds papers that never went
  through the query. The legend in the file says so rather than leaving the
  colors to be misread, and that crossing is the interesting part: a grey seed
  matched on something else, and a colored expansion-added paper is one the
  search arguably should have returned.
- Matching is **literal**, case-insensitive, on word boundaries, so `autophagy`
  does not match `autophagic`. PubMed's truncation operator works: `autophag*`
  matches both. Quoted phrases stay phrases.
- Field-tagged terms that cannot appear in an abstract, such as `Isom DG[au]` or
  `"Nature"[ta]`, are dropped rather than searched for and reported as misses.
- `--pmids` and `--refs` runs have no query, so their graphs stay uncolored
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
