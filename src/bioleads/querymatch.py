"""Which corpus papers actually contain the PubMed query's terms.

A PubMed search returns papers that PubMed decided are hits, which is not the
same as papers whose text contains what you typed. Two gaps open up:

- **PubMed matched something we do not hold.** A hit can come from a MeSH term,
  an automatic term-mapping expansion, or full text we never fetched, so a
  genuine hit can contain none of the query words in its title or abstract.
- **Expansion added papers that were never hits at all.** With ``--expand`` the
  corpus grows along citation links, and those papers never went through the
  query.

So marking the papers that literally contain the terms cuts across how a paper
got into the corpus, and that is what makes it worth looking at: a seed with no
literal match was matched on something else, and an expansion-added paper that
does contain the terms is one the search arguably should have returned.

**Matching here is literal**, case-insensitive, on word boundaries, over title
plus abstract. It is not PubMed's automatic term mapping and does not stem, so
``autophagy`` does not match ``autophagic``. PubMed's truncation operator is
honoured: ``autophag*`` matches both.
"""
from __future__ import annotations

import re

# PubMed field tags whose terms are not expected in title or abstract text, so
# searching for them would only ever produce misleading misses.
NON_TEXT_FIELDS = {
    "au", "auth", "author", "1au", "lastau", "ad", "affil", "affiliation",
    "dp", "date", "edat", "crdt", "pdat", "ta", "jour", "journal", "si",
    "pt", "ptyp", "la", "lang", "language", "pmid", "uid", "issn", "vi", "ip",
    "cn", "ir", "ip",
}

_QUOTED = re.compile(r'"([^"]+)"|\'([^\']+)\'')
_FIELD_TAG = re.compile(r"\[([^\]]*)\]\s*$")     # tag closing a term
_LEADING_TAG = re.compile(r"^\[([^\]]*)\]")      # tag straight after a quote
_BOOLEANS = {"AND", "OR", "NOT"}
# A field tag binds to the whole term before it, so the query has to be cut into
# chunks at the boolean operators and parentheses BEFORE tags are read. Splitting
# on whitespace first lets `Isom DG[au]` leak the word `Isom`, because only the
# `DG[au]` token carries the tag.
_CHUNK_SPLIT = re.compile(r"[()]|(?<=\s)(?:AND|OR|NOT)(?=\s)")


def _strip_field_tag(token: str) -> tuple[str, str | None]:
    """`"nanotube"[tiab]` -> ("nanotube", "tiab"). Returns (term, field-or-None)."""
    m = _FIELD_TAG.search(token)
    if not m:
        return token, None
    return token[: m.start()].strip(), m.group(1).strip().lower()


def parse_query_terms(query: str | None) -> list[str]:
    """Pull the searchable terms out of a PubMed query string.

    Quoted phrases survive as phrases. Boolean operators, parentheses and
    field-tagged terms that cannot appear in an abstract (author, journal, date
    and so on) are dropped. Order is preserved and duplicates removed.
    """
    if not query or not str(query).strip():
        return []
    text = str(query)
    terms: list[str] = []

    # Quoted phrases first, so their internal spaces survive as one term and a
    # tag straight after the closing quote is read from what follows.
    def _take_quoted(m):
        phrase = (m.group(1) or m.group(2) or "").strip()
        tag = _LEADING_TAG.match(text[m.end():].lstrip())
        field = tag.group(1).strip().lower() if tag else None
        if phrase and (field is None or field not in NON_TEXT_FIELDS):
            terms.append(phrase)
        return " "

    rest = _QUOTED.sub(_take_quoted, text)

    for chunk in _CHUNK_SPLIT.split(rest):
        chunk = (chunk or "").strip()
        if not chunk or chunk.upper() in _BOOLEANS:
            continue
        body, field = _strip_field_tag(chunk)
        # The tag governs the whole chunk: `Isom DG[au]` drops both words.
        if field is not None and field in NON_TEXT_FIELDS:
            continue
        # An untagged multi-word chunk is PubMed's automatic term mapping, which
        # is not a literal phrase, so its words are searched separately.
        for word in body.split():
            term = word.strip(" ,;:")
            if len(term) < 2 or term.upper() in _BOOLEANS:
                continue
            terms.append(term)

    seen, out = set(), []
    for t in terms:
        k = t.lower()
        if k not in seen:
            seen.add(k)
            out.append(t)
    return out


def _term_pattern(term: str) -> re.Pattern:
    """Word-boundary, case-insensitive. A trailing `*` becomes a prefix match."""
    if term.endswith("*"):
        stem = re.escape(term[:-1].strip())
        return re.compile(rf"\b{stem}\w*", re.I)
    # Escape, then let internal whitespace match any run of whitespace so a
    # phrase still matches across a line break in an abstract.
    body = r"\s+".join(re.escape(w) for w in term.split())
    return re.compile(rf"\b{body}\b" if body else r"(?!)", re.I)


# How many surface forms one concept may expand into before the expansion is
# doing more harm than good. PubMed turns `tunneling` into nine words, some of
# them ("tunnelization", "tunnelized") nothing to do with the question, which is
# why the unquoted query returns 1,706 hits against 215 for the phrase. Past
# this many, the raw term is kept instead.
MAX_FORMS_PER_TERM = 6

# Field tags whose terms are concepts assigned by an indexer rather than words
# the author wrote. A MeSH heading frequently appears nowhere in the abstract,
# so matching on it literally would mark a paper for something its text does not
# say — the exact confusion the coloring exists to expose.
_INDEXED_FIELDS = {"mesh terms", "mh", "majr", "mesh major topic",
                   "mesh subheading", "sh", "pharmacological action", "pa"}

_TRANSLATION_TERM = re.compile(r'"([^"]+)"\s*\[([^\]]+)\]')


def terms_from_translation(translation: str | None) -> list[list[str]]:
    """PubMed's own expansion of the query, as one group of forms per concept.

    `esearch` returns a ``QueryTranslation`` saying what it actually searched:

        cytoneme  ->  "cytoneme"[All Fields] OR "cytonemes"[All Fields]

    **That is the search that ran**, so gating and coloring on it cannot
    disagree with retrieval, and plurals and PubMed's synonym mapping come for
    free rather than needing a `*` typed by hand.

    Returns **groups**: a paper matches the concept if it contains *any* form in
    the group. Flattening them would be wrong — ``classify`` would then demand
    every surface form, so a paper saying "cytonemes" could never be green.

    Groups are split on ``AND``/``NOT`` at the top level, since those join
    different concepts, while ``OR`` joins forms of one. Two kinds of form are
    dropped: indexed fields like MeSH, which name concepts an author never
    wrote, and groups that ballooned past :data:`MAX_FORMS_PER_TERM`.

    Returns ``[]`` when there is nothing usable, and the caller falls back to
    :func:`parse_query_terms` on the raw query.
    """
    if not translation or not translation.strip():
        return []
    groups: list[list[str]] = []
    # Parentheses bound a concept's own OR-group; AND/NOT separate concepts.
    for chunk in re.split(r"\s+(?:AND|NOT)\s+", translation):
        forms, dropped_indexed = [], False
        for text, field in _TRANSLATION_TERM.findall(chunk):
            if field.strip().lower() in _INDEXED_FIELDS:
                dropped_indexed = True
                continue
            if field.strip().lower() in NON_TEXT_FIELDS:
                continue
            form = text.strip()
            if len(form) >= 2 and form not in forms:
                forms.append(form)
        if not forms:
            continue
        # The first form labels the group wherever a match is reported, so put
        # the readable one there. PubMed emits artifacts like "nanotube s", and
        # a hover saying `query terms found: nanotube s` is worse than useless.
        forms.sort(key=lambda f: (" " in f, len(f), f.lower()))
        if len(forms) > MAX_FORMS_PER_TERM:
            # Collapse to the shortest form as a truncation rather than keeping
            # it bare. PubMed expanded `tunneling` into nine words; `tunnel*`
            # covers the same ground in one, and keeping `tunnel` alone would
            # match none of them, since the test is on word boundaries.
            stem = min(forms, key=len)
            forms = [stem + "*"] if len(stem) >= 4 and not stem.endswith("*") \
                else [stem]
        groups.append(forms)
    return groups


def matched_terms(text: str | None, terms) -> list[str]:
    """Which of `terms` literally appear in `text`.

    `terms` is a list of strings, or a list of **groups** of surface forms from
    :func:`terms_from_translation`. A group counts as matched when any of its
    forms appears, and is reported by its first form, which is what was typed.
    """
    if not text or not terms:
        return []
    out = []
    for t in terms:
        if isinstance(t, (list, tuple)):
            hit = next((f for f in t if _term_pattern(f).search(text)), None)
            if hit is not None:
                out.append(t[0])
        elif _term_pattern(t).search(text):
            out.append(t)
    return out


def classify(n_matched: int, n_terms: int) -> str:
    """`all`, `partial` or `none`. With no terms to match, `unknown`."""
    if not n_terms:
        return "unknown"
    if n_matched == 0:
        return "none"
    return "all" if n_matched == n_terms else "partial"


# Blue and orange, not green and amber. **Green against amber is the one pair a
# red-green color blind reader cannot separate**, and roughly one man in twelve
# is. These are Okabe-Ito, which stays distinguishable under deuteranopia,
# protanopia and tritanopia, and the two differ in lightness as well as hue so
# they survive a greyscale print too.
#
# Grey for "none" matches the unclustered grey in the term scatter, so the two
# views read the same way: grey is always "not in the thing being shown".
MATCH_COLORS = {
    "all": "#0072B2",       # blue: contains every query term
    "partial": "#E69F00",   # orange: some
    "none": "#BBBBBB",      # grey: none
    "unknown": "#5B6B7C",   # slate: nothing to match against (no text query)
}


# The two states that mean "this paper's text actually contains what was typed".
# `none` is not a bad hit (PubMed matches MeSH and full text), and `unknown`
# means there was no text query to match against, so neither belongs in a
# subnetwork of matching papers.
def _doc_pmid(doc) -> str:
    """A document's bare PMID, from meta or from a ``PMID:`` doc_id.

    **The doc_id fallback is not belt and braces.** PubMed records built by
    :func:`bioleads.sources._record_to_document` carried their PMID only in
    ``doc_id`` for a while, so reading ``meta`` alone matched nothing on a real
    ``--pubmed`` run and every node came out grey. Both now carry it, and this
    reads either.

    The same rule lives in ``citations._doc_pmid`` and ``sources.document_pmids``.
    Keep the three in step.
    """
    pmid = str((getattr(doc, "meta", None) or {}).get("pmid", "")).strip()
    if not pmid:
        doc_id = str(getattr(doc, "doc_id", "") or "")
        if doc_id.startswith("PMID:"):
            pmid = doc_id.split("PMID:", 1)[1].strip()
    return pmid


MATCHING_STATES = ("all", "partial")


def matching_subgraph(g, states=MATCHING_STATES):
    """The induced subgraph of nodes whose text contains at least one query term.

    `states` defaults to ``("all", "partial")``, which is the union asked for:
    papers containing every term, plus papers containing some. Pass
    ``("all",)`` for the stricter set.

    **Edges are induced**, so a citation is kept only when both ends matched.
    Citation paths between matching papers often run through papers that matched
    nothing, so this subnetwork is routinely sparser and more fragmented than
    the same nodes look inside the full graph. That is a property of the
    selection, not a finding about the literature.

    Node attributes are carried over untouched, including
    ``in_corpus_citations``, which was computed against the **whole** corpus.
    Sizes therefore stay comparable with the full network rather than being
    recomputed against this subset.

    Returns a copy, so annotating or writing it cannot disturb the full graph.
    Returns an empty graph of the same type when nothing matched.
    """
    wanted = set(states)
    keep = [n for n, d in g.nodes(data=True) if d.get("query_match") in wanted]
    return g.subgraph(keep).copy()


def query_terms(query: str | None, translation: str | None = None):
    """The terms to match on: PubMed's expansion when there is one.

    **Prefer the translation.** It is what PubMed actually searched, so the
    coloring and the gate agree with retrieval instead of second-guessing it,
    and plurals come for free: `cytoneme` is expanded to
    ``{cytoneme, cytonemes}`` without anyone typing a `*`.

    Falls back to reading the raw query when there is no translation — a
    ``--pmids`` run, a cached result, or a search that failed before esearch
    answered.
    """
    groups = terms_from_translation(translation)
    return groups if groups else parse_query_terms(query)


def _labels(terms) -> list[str]:
    """How the terms read in a legend: one label per concept."""
    return [t[0] if isinstance(t, (list, tuple)) else t for t in terms]


def annotate_citation_graph(g, docs, query: str | None,
                            translation: str | None = None) -> list[str]:
    """Tag each paper node with which query terms its text contains.

    Sets ``query_terms_matched`` (a comma-joined string, for GraphML's sake),
    ``query_match_count`` and ``query_match`` on every node, and returns the
    parsed terms so a caller can report or legend them.

    GraphML cannot store a list or a None, which is why the attributes written
    here are a string and two ints.
    """
    terms = query_terms(query, translation)
    by_pmid = {}
    for d in docs or []:
        pmid = _doc_pmid(d)
        if pmid:
            by_pmid[pmid] = d

    for n, data in g.nodes(data=True):
        doc = by_pmid.get(str(data.get("pmid") or "").strip())
        hits = matched_terms(getattr(doc, "content", None), terms) if doc else []
        data["query_terms_matched"] = ", ".join(hits)
        data["query_match_count"] = len(hits)
        data["query_match"] = classify(len(hits), len(terms))
        data["expanded"] = bool(doc.meta.get("expanded")) if doc else False
    return _labels(terms)


def annotate_author_graph(g, docs, query: str | None,
                          translation: str | None = None) -> list[str]:
    """Tag each author node with how well their corpus papers match the query.

    Needs ``g.graph["paper_senior"]`` (``{pmid: author}``), which
    :func:`bioleads.citations.build_author_citation_graph` stores for exactly
    this purpose. Without it nothing is annotated and the graph is left alone,
    which is the right outcome for an author graph built some other way.

    **An author takes the state of their single best-matching paper**, not the
    union of terms across their papers. An author with one paper naming every
    term is green; an author with five papers that each name a different single
    term is amber. The alternative — pooling terms across an author's papers —
    would call that second author green, which claims a paper that does not
    exist. The hover carries ``k of n papers`` so the spread is visible either
    way.

    Sets ``query_match``, ``query_match_count``, ``query_terms_matched`` (the
    best paper's terms, comma-joined for GraphML's sake),
    ``query_papers_matched`` and ``query_papers_total``. Returns the parsed
    terms.
    """
    terms = query_terms(query, translation)
    paper_senior = (getattr(g, "graph", None) or {}).get("paper_senior") or {}
    if not terms or not paper_senior:
        return _labels(terms)

    by_pmid = {}
    for d in docs or []:
        pmid = _doc_pmid(d)
        if pmid:
            by_pmid[pmid] = d

    # best[author] = (n_matched, hits); counts[author] = (matched, total)
    best: dict = {}
    counts: dict = {}
    for pmid, author in paper_senior.items():
        doc = by_pmid.get(pmid)
        hits = matched_terms(getattr(doc, "content", None), terms) if doc else []
        prev_n, prev_hits = best.get(author, (-1, []))
        if len(hits) > prev_n:
            best[author] = (len(hits), hits)
        m, t = counts.get(author, (0, 0))
        counts[author] = (m + (1 if hits else 0), t + 1)

    for n, data in g.nodes(data=True):
        n_hits, hits = best.get(n, (0, []))
        matched, total = counts.get(n, (0, 0))
        data["query_terms_matched"] = ", ".join(hits)
        data["query_match_count"] = max(n_hits, 0)
        data["query_match"] = classify(max(n_hits, 0), len(terms))
        data["query_papers_matched"] = matched
        data["query_papers_total"] = total
    return _labels(terms)
