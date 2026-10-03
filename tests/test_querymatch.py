"""Regression tests for literal query-term matching on the citation network.

Every case in the field-tag section is a bug that actually shipped and was
caught by eye rather than by a test, which is the reason this file exists. The
parser reads field tags *after* splitting on booleans and parentheses, because
a tag binds to the whole term before it and splitting on whitespace first lets
``Isom DG[au]`` leak the bare word ``Isom`` into the term list.
"""
import io as _io

import networkx as nx
import pytest

from bioleads.querymatch import (
    MATCH_COLORS,
    annotate_author_graph,
    MATCHING_STATES,
    NON_TEXT_FIELDS,
    annotate_citation_graph,
    classify,
    matched_terms,
    matching_subgraph,
    parse_query_terms,
)


# --------------------------------------------------------------------------
# parse_query_terms: what counts as a searchable term
# --------------------------------------------------------------------------

@pytest.mark.parametrize("query", [None, "", "   ", "\n\t "])
def test_empty_query_has_no_terms(query):
    assert parse_query_terms(query) == []


def test_plain_words_are_separate_terms():
    # An untagged multi-word chunk is PubMed's automatic term mapping, not a
    # literal phrase, so its words are searched independently.
    assert parse_query_terms("tunneling nanotube") == ["tunneling", "nanotube"]


def test_quoted_phrase_survives_as_one_term():
    assert parse_query_terms('"tunneling nanotube"') == ["tunneling nanotube"]
    assert parse_query_terms("'tunneling nanotube'") == ["tunneling nanotube"]


def test_booleans_and_parentheses_are_dropped():
    terms = parse_query_terms("(autophagy OR mitophagy) AND NOT lysosome")
    assert terms == ["autophagy", "mitophagy", "lysosome"]


def test_duplicates_are_removed_case_insensitively_keeping_order():
    assert parse_query_terms("TM184C OR tm184c OR autophagy") == ["TM184C", "autophagy"]


def test_single_characters_are_dropped():
    # Length < 2 is noise, not a term.
    assert parse_query_terms("a OR autophagy") == ["autophagy"]


# --------------------------------------------------------------------------
# Field tags. Each of these is a shipped bug.
# --------------------------------------------------------------------------

def test_author_tag_drops_the_whole_chunk_not_just_the_tagged_token():
    # SHIPPED BUG: this returned ["Isom"], because only "DG[au]" carried the
    # tag once the query had been split on whitespace.
    assert parse_query_terms("Isom DG[au]") == []


def test_author_tag_drops_only_its_own_chunk():
    terms = parse_query_terms("Isom DG[au] AND autophagy")
    assert terms == ["autophagy"]
    assert "Isom" not in terms


def test_journal_tag_after_a_closing_quote_is_honoured():
    # SHIPPED BUG: the quoted phrase was taken before the tag was read, so
    # "Nature"[ta] leaked the journal name in as a searchable term.
    assert parse_query_terms('"Nature"[ta]') == []


def test_journal_tag_drops_only_its_own_chunk():
    terms = parse_query_terms('"Nature"[ta] AND "tunneling nanotube"')
    assert terms == ["tunneling nanotube"]


def test_text_bearing_tags_are_kept():
    # tiab is title/abstract: exactly the text being searched, so keep it.
    assert parse_query_terms('"nanotube"[tiab]') == ["nanotube"]


@pytest.mark.parametrize("field", sorted(NON_TEXT_FIELDS))
def test_every_non_text_field_is_dropped(field):
    assert parse_query_terms(f"something[{field}]") == []


def test_tags_are_case_insensitive():
    assert parse_query_terms("Isom DG[AU]") == []
    assert parse_query_terms("Isom DG[Au]") == []


def test_a_realistic_mixed_query():
    terms = parse_query_terms(
        '("tunneling nanotube"[tiab] OR cytoneme) AND Isom DG[au] '
        'AND "Nature"[ta] AND 2026[dp]'
    )
    assert terms == ["tunneling nanotube", "cytoneme"]


# --------------------------------------------------------------------------
# matched_terms: literal, case-insensitive, word-boundary
# --------------------------------------------------------------------------

def test_matching_is_case_insensitive():
    assert matched_terms("Autophagy is induced", ["autophagy"]) == ["autophagy"]


def test_matching_is_on_word_boundaries_and_does_not_stem():
    # The docstring promises exactly this: autophagy does not match autophagic.
    assert matched_terms("autophagic flux", ["autophagy"]) == []
    assert matched_terms("macroautophagy", ["autophagy"]) == []


def test_truncation_operator_matches_the_stem():
    assert matched_terms("autophagic flux", ["autophag*"]) == ["autophag*"]
    assert matched_terms("autophagy is induced", ["autophag*"]) == ["autophag*"]


def test_phrase_matches_across_a_line_break():
    # Abstracts wrap, so internal whitespace has to match any run of it.
    text = "we observed a tunneling\nnanotube between cells"
    assert matched_terms(text, ["tunneling nanotube"]) == ["tunneling nanotube"]


def test_regex_metacharacters_in_a_term_are_literal():
    assert matched_terms("the C.elegans gene", ["C.elegans"]) == ["C.elegans"]
    assert matched_terms("the Cxelegans gene", ["C.elegans"]) == []


@pytest.mark.parametrize("text", [None, ""])
def test_no_text_matches_nothing(text):
    assert matched_terms(text, ["autophagy"]) == []


def test_no_terms_match_nothing():
    assert matched_terms("autophagy", []) == []


# --------------------------------------------------------------------------
# classify
# --------------------------------------------------------------------------

@pytest.mark.parametrize("n_matched,n_terms,expected", [
    (0, 0, "unknown"),
    (1, 0, "unknown"),
    (0, 3, "none"),
    (1, 3, "partial"),
    (3, 3, "all"),
])
def test_classify(n_matched, n_terms, expected):
    assert classify(n_matched, n_terms) == expected


def test_every_class_has_a_color():
    for name in ("all", "partial", "none", "unknown"):
        assert MATCH_COLORS[name].startswith("#")


# --------------------------------------------------------------------------
# annotate_citation_graph: the GraphML contract
# --------------------------------------------------------------------------

class _Doc:
    """Minimal stand-in for a corpus Document."""

    def __init__(self, pmid, content, expanded=False):
        self.content = content
        self.meta = {"pmid": pmid, "expanded": expanded}


def _graph(*pmids):
    g = nx.DiGraph()
    for p in pmids:
        g.add_node(p, pmid=p)
    return g


def test_annotation_sets_the_three_attributes_on_every_node():
    g = _graph("1", "2", "3")
    docs = [
        _Doc("1", "autophagy and the nanotube"),
        _Doc("2", "autophagy alone"),
        _Doc("3", "neither word here"),
    ]
    terms = annotate_citation_graph(g, docs, "autophagy nanotube")

    assert terms == ["autophagy", "nanotube"]
    assert g.nodes["1"]["query_match"] == "all"
    assert g.nodes["2"]["query_match"] == "partial"
    assert g.nodes["3"]["query_match"] == "none"
    assert g.nodes["2"]["query_match_count"] == 1
    assert g.nodes["2"]["query_terms_matched"] == "autophagy"


def test_attributes_are_graphml_safe():
    # GraphML cannot store a list or a None, which is why these are a string
    # and two ints. A node with no matching document must still be writable.
    g = _graph("1", "99")
    docs = [_Doc("1", "autophagy")]
    annotate_citation_graph(g, docs, "autophagy")

    for _, data in g.nodes(data=True):
        assert isinstance(data["query_terms_matched"], str)
        assert isinstance(data["query_match_count"], int)
        assert isinstance(data["query_match"], str)
        assert isinstance(data["expanded"], bool)
        assert None not in data.values()


def test_node_with_no_document_is_a_miss_not_a_crash():
    g = _graph("99")
    annotate_citation_graph(g, [_Doc("1", "autophagy")], "autophagy")
    assert g.nodes["99"]["query_match"] == "none"
    assert g.nodes["99"]["query_terms_matched"] == ""


def test_no_query_makes_every_node_unknown():
    g = _graph("1")
    terms = annotate_citation_graph(g, [_Doc("1", "autophagy")], None)
    assert terms == []
    assert g.nodes["1"]["query_match"] == "unknown"


def test_expansion_flag_is_carried_through():
    g = _graph("1", "2")
    docs = [_Doc("1", "autophagy", expanded=False),
            _Doc("2", "autophagy", expanded=True)]
    annotate_citation_graph(g, docs, "autophagy")
    assert g.nodes["1"]["expanded"] is False
    assert g.nodes["2"]["expanded"] is True


def test_an_expansion_added_paper_can_match_every_term():
    # The crossing that makes this feature worth having: a paper the search
    # never returned, which nonetheless contains everything that was typed.
    g = _graph("2")
    annotate_citation_graph(g, [_Doc("2", "autophagy nanotube", expanded=True)],
                            "autophagy nanotube")
    assert g.nodes["2"]["query_match"] == "all"
    assert g.nodes["2"]["expanded"] is True


def test_annotation_survives_an_empty_corpus():
    g = _graph("1")
    annotate_citation_graph(g, [], "autophagy")
    assert g.nodes["1"]["query_match"] == "none"


# --------------------------------------------------------------------------
# matching_subgraph: the union of "all" and "partial"
# --------------------------------------------------------------------------

def _annotated(edges, texts, query="autophagy nanotube"):
    g = nx.DiGraph()
    for p in texts:
        g.add_node(p, pmid=p, in_corpus_citations=1)
    g.add_edges_from(edges)
    annotate_citation_graph(g, [_Doc(p, t) for p, t in texts.items()], query)
    return g


def test_matching_states_are_all_and_partial():
    assert MATCHING_STATES == ("all", "partial")


def test_subgraph_keeps_all_and_partial_and_drops_none():
    g = _annotated(
        [("1", "2"), ("2", "3")],
        {"1": "autophagy nanotube", "2": "nothing here", "3": "autophagy"},
    )
    sub = matching_subgraph(g)
    assert sorted(sub.nodes()) == ["1", "3"]


def test_edges_are_induced_so_a_path_through_a_miss_is_broken():
    # 1 cites 2 cites 3. Only 2 fails to match, and the subnetwork is then two
    # isolated nodes rather than a chain. This is the behaviour to understand
    # before reading anything into how fragmented the result looks.
    g = _annotated(
        [("1", "2"), ("2", "3")],
        {"1": "autophagy", "2": "nothing here", "3": "nanotube"},
    )
    sub = matching_subgraph(g)
    assert sub.number_of_edges() == 0


def test_edges_between_two_matching_nodes_survive():
    g = _annotated(
        [("1", "3")],
        {"1": "autophagy", "3": "nanotube"},
    )
    assert sorted(matching_subgraph(g).edges()) == [("1", "3")]


def test_strict_mode_keeps_only_all():
    g = _annotated(
        [],
        {"1": "autophagy nanotube", "2": "autophagy", "3": "nothing"},
    )
    assert sorted(matching_subgraph(g, ("all",)).nodes()) == ["1"]


def test_subgraph_is_a_copy_and_the_full_graph_is_untouched():
    g = _annotated([("1", "2")], {"1": "autophagy", "2": "nothing"})
    sub = matching_subgraph(g)
    sub.add_node("new")
    assert "new" not in g
    assert g.number_of_nodes() == 2


def test_node_attributes_carry_over_including_corpus_wide_citations():
    # in_corpus_citations was computed against the WHOLE corpus and must not be
    # recomputed here, or node sizes stop being comparable with the full network.
    g = _annotated([], {"1": "autophagy"})
    g.nodes["1"]["in_corpus_citations"] = 7
    sub = matching_subgraph(g)
    assert sub.nodes["1"]["in_corpus_citations"] == 7
    assert sub.nodes["1"]["query_match"] == "partial"


def test_nothing_matching_gives_an_empty_graph_not_an_error():
    g = _annotated([("1", "2")], {"1": "nothing", "2": "nothing either"})
    sub = matching_subgraph(g)
    assert sub.number_of_nodes() == 0
    assert isinstance(sub, nx.DiGraph)


def test_an_unannotated_graph_yields_nothing():
    # No text query means every node is "unknown", which is not a match.
    g = nx.DiGraph()
    g.add_node("1", pmid="1")
    annotate_citation_graph(g, [_Doc("1", "autophagy")], None)
    assert matching_subgraph(g).number_of_nodes() == 0


# --------------------------------------------------------------------------
# The real PubMed document shape.
#
# Everything above this point uses a hand-made stand-in whose meta carries a
# pmid, because that is what the code reads. A real `--pubmed` record did not,
# so the matcher found nothing on live runs while every test passed. These
# build the document through the actual PubMed path so the fixture cannot
# drift from it again.
# --------------------------------------------------------------------------

def _pubmed_doc(pmid, title, abstract):
    from bioleads.sources import _record_to_document
    return _record_to_document({"PMID": pmid, "TI": title, "AB": abstract,
                                "JT": "J Test", "DP": "2026"})


def test_a_pubmed_record_carries_its_pmid_in_meta():
    doc = _pubmed_doc("12345", "TMEM184A", "about TMEM184A")
    assert doc.meta["pmid"] == "12345"
    assert doc.doc_id == "PMID:12345"


def test_a_real_pubmed_document_is_matched():
    """The regression: a paper naming the term in title AND abstract read as `none`."""
    doc = _pubmed_doc("12345", "TMEM184A is a heparin receptor",
                      "We show that TMEM184A binds heparin.")
    g = nx.DiGraph()
    g.add_node("PMID:12345", pmid="12345")
    annotate_citation_graph(g, [doc], "TMEM184A")
    assert g.nodes["PMID:12345"]["query_match"] == "all"


def test_a_document_with_no_meta_pmid_is_still_matched_via_doc_id():
    """Defence in depth: doc_id alone has to be enough."""
    doc = _pubmed_doc("777", "TMEM184A", "abstract naming TMEM184A")
    doc.meta.pop("pmid")
    g = nx.DiGraph()
    g.add_node("PMID:777", pmid="777")
    annotate_citation_graph(g, [doc], "TMEM184A")
    assert g.nodes["PMID:777"]["query_match"] == "all"


def test_a_mixed_corpus_from_real_records():
    docs = [
        _pubmed_doc("1", "TMEM184A is a heparin receptor", "TMEM184A binds heparin."),
        _pubmed_doc("2", "Vascular biology review", "Nothing relevant here."),
        _pubmed_doc("3", "Tmem184a in endothelium", "We study it."),
    ]
    g = nx.DiGraph()
    for d in docs:
        g.add_node(d.doc_id, pmid=d.meta["pmid"])
    g.add_edges_from([("PMID:1", "PMID:3"), ("PMID:1", "PMID:2")])
    annotate_citation_graph(g, docs, "TMEM184A")

    assert g.nodes["PMID:1"]["query_match"] == "all"
    assert g.nodes["PMID:2"]["query_match"] == "none"
    assert g.nodes["PMID:3"]["query_match"] == "all"   # case-insensitive

    sub = matching_subgraph(g)
    assert sorted(sub.nodes()) == ["PMID:1", "PMID:3"]
    assert sorted(sub.edges()) == [("PMID:1", "PMID:3")]


# --------------------------------------------------------------------------
# annotate_author_graph: an author takes their best paper's state
# --------------------------------------------------------------------------

def _author_graph(rows):
    """rows = [(pmid, title, abstract, senior_author)]."""
    from bioleads.sources import _record_to_document
    docs = [_record_to_document({"PMID": p, "TI": t, "AB": a})
            for p, t, a, _ in rows]
    g = nx.DiGraph()
    for _, _, _, au in rows:
        if not g.has_node(au):
            g.add_node(au, author=au, papers=0, in_corpus_citations=0)
        g.nodes[au]["papers"] += 1
    g.graph["paper_senior"] = {p: au for p, _, _, au in rows}
    return g, docs


def test_author_takes_the_state_of_their_best_paper():
    g, docs = _author_graph([
        ("1", "TMEM184A heparin", "TMEM184A binds heparin", "Smith A"),
        ("2", "Vascular review", "nothing", "Smith A"),
        ("3", "Tmem184a only", "we study it", "Jones B"),
        ("4", "Unrelated", "nothing at all", "Lee C"),
    ])
    annotate_author_graph(g, docs, "TMEM184A heparin")
    assert g.nodes["Smith A"]["query_match"] == "all"
    assert g.nodes["Jones B"]["query_match"] == "partial"
    assert g.nodes["Lee C"]["query_match"] == "none"


def test_terms_are_not_pooled_across_an_authors_papers():
    """Two papers naming one term each is amber, not green.

    Pooling would claim a paper naming both terms, which does not exist. This
    is the whole design decision of this function.
    """
    g, docs = _author_graph([
        ("1", "TMEM184A", "about the protein", "Split A"),
        ("2", "heparin", "about the sugar", "Split A"),
    ])
    annotate_author_graph(g, docs, "TMEM184A heparin")
    assert g.nodes["Split A"]["query_match"] == "partial"
    assert g.nodes["Split A"]["query_match_count"] == 1


def test_author_carries_how_many_of_their_papers_matched():
    g, docs = _author_graph([
        ("1", "TMEM184A", "yes", "Smith A"),
        ("2", "Vascular review", "no", "Smith A"),
        ("3", "TMEM184A again", "yes", "Smith A"),
    ])
    annotate_author_graph(g, docs, "TMEM184A")
    assert g.nodes["Smith A"]["query_papers_matched"] == 2
    assert g.nodes["Smith A"]["query_papers_total"] == 3


def test_author_attributes_are_graphml_safe():
    """Node attributes must be scalars, and the paper_senior map must not block
    a GraphML write.

    `paper_senior` is a dict on `graph`, which GraphML cannot store, so writing
    the annotated author graph raised until `_graphml_safe` was added. That is
    not hypothetical: the pyvis fallback writes GraphML whenever pyvis is
    missing, which is the core-only install the conda recipe builds.
    """
    from bioleads.citations import _graphml_safe
    g, docs = _author_graph([("1", "TMEM184A", "yes", "Smith A")])
    annotate_author_graph(g, docs, "TMEM184A")

    with pytest.raises(Exception):
        nx.write_graphml(g, _io.BytesIO())        # the dict on .graph

    buf = _io.BytesIO()
    nx.write_graphml(_graphml_safe(g), buf)       # what the writers actually do
    assert b"query_match" in buf.getvalue()


def test_no_paper_senior_map_means_no_annotation():
    """An author graph built some other way is left alone rather than greyed."""
    g = nx.DiGraph()
    g.add_node("Smith A", author="Smith A")
    annotate_author_graph(g, [], "TMEM184A")
    assert "query_match" not in g.nodes["Smith A"]


def test_no_query_leaves_the_author_graph_alone():
    g, docs = _author_graph([("1", "TMEM184A", "yes", "Smith A")])
    assert annotate_author_graph(g, docs, None) == []
    assert "query_match" not in g.nodes["Smith A"]


# --------------------------------------------------------------------------
# The expansion gate: keep a discovered paper only if it names a query term
# --------------------------------------------------------------------------

def test_expansion_gate_keeps_only_papers_naming_a_term():
    from bioleads.sources import _keep_if_query_terms
    docs = [_pubmed_doc("1", "TMEM184C and autophagy", "about the protein"),
            _pubmed_doc("2", "Something else entirely", "unrelated"),
            _pubmed_doc("3", "A review", "mentions TMEM184C once")]
    kept = _keep_if_query_terms(docs, "TMEM184C", lambda m: None)
    assert [d.meta["pmid"] for d in kept] == ["1", "3"]


def test_expansion_gate_is_a_no_op_when_the_query_has_no_searchable_term():
    """A query of nothing but author and journal tags must not wipe the expansion."""
    from bioleads.sources import _keep_if_query_terms
    docs = [_pubmed_doc("1", "Anything", "at all")]
    assert _keep_if_query_terms(docs, 'Isom DG[au] AND "Nature"[ta]', lambda m: None) == docs


def test_expansion_gate_matches_the_coloring():
    """The gate and the node colors must use the same test, or a kept paper
    could render grey and a dropped one would have been green."""
    from bioleads.sources import _keep_if_query_terms
    docs = [_pubmed_doc("1", "Tmem184c in endothelium", "we study it")]
    kept = _keep_if_query_terms(docs, "TMEM184C", lambda m: None)
    assert len(kept) == 1                       # case-insensitive, as the colors are
    assert matched_terms(kept[0].content, parse_query_terms("TMEM184C"))


# --------------------------------------------------------------------------
# The seed-profile gate
# --------------------------------------------------------------------------

def test_seed_profile_ranks_terms_by_how_many_seeds_share_them():
    from bioleads.sources import seed_profile
    docs = [_pubmed_doc("1", "autophagy and lysosome", "autophagy autophagy autophagy"),
            _pubmed_doc("2", "lysosome biology", "the lysosome again"),
            _pubmed_doc("3", "lysosome transport", "lysosome vesicles")]
    # autophagy is said many times but in one paper; lysosome is in all three.
    profile = seed_profile(docs, n_terms=5)
    assert profile[0] == "lysosome"


def test_top_n_limits_which_seeds_build_the_profile():
    """The control Dan asked for: a heterogeneous seed set is fixed by using fewer.

    This mirrors the real case. A gene-symbol query returned one mechanism paper
    and two genomics case reports that merely name the gene inside a copy-number
    region, and a profile over all three described chromosomes.
    """
    from bioleads.sources import seed_profile
    docs = [_pubmed_doc("1", "GPCR-like regulator of autophagy", "arrestin autophagosome"),
            _pubmed_doc("2", "A duplication CNV on chromosome 17", "chromosome sequencing"),
            _pubmed_doc("3", "Interchromosomal insertion", "chromosome sequencing")]
    all_three = seed_profile(docs, n_terms=10)
    just_one = seed_profile(docs, top_n=1, n_terms=10)
    assert "chromosome" in all_three
    assert "chromosome" not in just_one
    assert "autophagosome" in just_one


def test_seed_gate_keeps_papers_sharing_enough_of_the_profile():
    from bioleads.sources import _keep_if_like_seeds
    seeds = [_pubmed_doc("1", "autophagy lysosome arrestin",
                         "autophagosome vesicle trafficking")]
    cands = [_pubmed_doc("9", "autophagosome lysosome fusion",
                         "vesicle trafficking and arrestin"),
             _pubmed_doc("8", "Sheep genome resequencing",
                         "domestic breeds and agronomic traits")]
    kept = _keep_if_like_seeds(cands, seeds, top_n=1, n_terms=10,
                               min_share=0.3, say=lambda m: None)
    assert [d.meta["pmid"] for d in kept] == ["9"]


def test_seed_gate_does_nothing_without_a_profile():
    """No seeds means no profile, and discarding the expansion over that would
    be far worse than keeping it."""
    from bioleads.sources import _keep_if_like_seeds
    cands = [_pubmed_doc("9", "anything", "at all")]
    kept = _keep_if_like_seeds(cands, [], top_n=1, n_terms=10, min_share=0.3,
                               say=lambda m: None)
    assert kept == cands


def test_seed_gate_records_the_hit_count_on_what_it_keeps():
    from bioleads.sources import _keep_if_like_seeds
    seeds = [_pubmed_doc("1", "autophagy lysosome arrestin", "autophagosome vesicle")]
    cands = [_pubmed_doc("9", "autophagy lysosome", "arrestin autophagosome vesicle")]
    kept = _keep_if_like_seeds(cands, seeds, top_n=1, n_terms=10, min_share=0.1,
                               say=lambda m: None)
    assert kept[0].meta["seed_profile_hits"] >= 1


# --------------------------------------------------------------------------
# Seed ranking. "Top n" is meaningless unless the order means something.
# --------------------------------------------------------------------------

def _ranked(query, rows):
    from bioleads.sources import rank_seeds
    return rank_seeds([_pubmed_doc(p, t, a) for p, t, a in rows], query)


def test_a_title_hit_outranks_everything():
    r = _ranked("TMEM184C", [
        ("1", "A copy-number study", "we list TMEM184C TMEM184C TMEM184C here"),
        ("2", "TMEM184C regulates autophagy", "one mention"),
    ])
    assert [d.meta["pmid"] for d in r] == ["2", "1"]
    assert r[0].meta["seed_title_hit"] is True


def test_occurrences_break_the_tie_when_neither_is_in_the_title():
    """The real case: three seeds each containing a term, ranked 6x against 1x."""
    r = _ranked("TMEM184C", [
        ("1", "A copy-number study", "we list TMEM184C among others"),
        ("2", "A mechanism study", "TMEM184C does this, TMEM184C does that"),
    ])
    assert [d.meta["pmid"] for d in r] == ["2", "1"]
    assert r[0].meta["seed_query_hits"] == 2


def test_presence_alone_would_not_have_ranked_these():
    """Each seed contains exactly one distinct term, so counting presence ties
    them and the order falls back to whatever arrived first. That is the bug."""
    r = _ranked("TMEM184C OR TM184C", [
        ("1", "A copy-number study", "TMEM184C appears once"),
        ("2", "TM184C is a regulator", "TM184C TM184C TM184C"),
    ])
    assert r[0].meta["pmid"] == "2"


def test_distinct_terms_separate_full_from_partial_coverage():
    r = _ranked("autophagy AND lysosome", [
        ("1", "A study", "autophagy autophagy autophagy"),
        ("2", "A study", "autophagy and the lysosome"),
    ])
    # Equal title hits; 3 occurrences beats 2, so #1 leads on count alone.
    assert r[0].meta["pmid"] == "1"
    # But coverage is recorded, and #2 is the one covering the whole query.
    from bioleads.sources import seed_rank_key
    from bioleads.querymatch import parse_query_terms
    terms = parse_query_terms("autophagy AND lysosome")
    assert seed_rank_key(r[1], terms)[2] == 2
    assert seed_rank_key(r[0], terms)[2] == 1


def test_no_query_leaves_pubmed_order_alone():
    from bioleads.sources import rank_seeds
    docs = [_pubmed_doc("1", "a", "b"), _pubmed_doc("2", "c", "d")]
    assert [d.meta["pmid"] for d in rank_seeds(docs, None)] == ["1", "2"]


def test_a_tagless_query_leaves_pubmed_order_alone():
    from bioleads.sources import rank_seeds
    docs = [_pubmed_doc("1", "a", "b"), _pubmed_doc("2", "c", "d")]
    out = rank_seeds(docs, 'Isom DG[au] AND "Nature"[ta]')
    assert [d.meta["pmid"] for d in out] == ["1", "2"]


def test_the_profile_follows_the_ranking():
    from bioleads.sources import rank_seeds, seed_profile
    r = rank_seeds([
        _pubmed_doc("1", "A chromosome study", "TMEM184C chromosome sequencing"),
        _pubmed_doc("2", "TMEM184C regulates autophagy", "TMEM184C autophagosome arrestin"),
    ], "TMEM184C")
    top1 = seed_profile(r, top_n=1, n_terms=20)
    assert "autophagosome" in top1
    assert "chromosome" not in top1


def test_fetch_pubmed_asks_for_relevance_not_the_default(monkeypatch):
    """E-utilities sorts by recency unless asked, which is not a ranking of
    anything we care about."""
    import bioleads.sources as S
    seen = {}

    class _E:
        @staticmethod
        def esearch(**kw):
            seen.update(kw)
            return _FakeHandleQM({"Count": "0", "IdList": [], "QueryTranslation": "x"})

        @staticmethod
        def read(handle):
            return handle.payload

    monkeypatch.setattr(S, "_entrez", lambda email, api_key: (_E, None))
    S.fetch_pubmed("autophagy")
    assert seen.get("sort") == "relevance"


class _FakeHandleQM:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_a_single_seed_profile_is_not_alphabetical():
    """With one seed every term has document frequency 1, so ranking on that
    alone collapses to alphabetical order — a profile of words from a to c.

    One seed is exactly the setting worth using when a query returns a mixed
    bag, so this case has to work. Total frequency breaks the tie.
    """
    from bioleads.sources import seed_profile
    doc = _pubmed_doc("1", "Zebra regulator of autophagy",
                      "autophagy autophagy autophagy and an aardvark")
    # Alphabetically "aardvark" would lead; by frequency "autophagy" does.
    assert seed_profile([doc], top_n=1, n_terms=1) == ["autophagy"]
    assert seed_profile([doc], top_n=1, n_terms=3)[0] == "autophagy"


def test_document_frequency_still_outranks_raw_frequency():
    """Across several seeds the shared term wins even if another is repeated."""
    from bioleads.sources import seed_profile
    docs = [_pubmed_doc("1", "a", "lysosome autophagy autophagy autophagy autophagy"),
            _pubmed_doc("2", "b", "lysosome elsewhere"),
            _pubmed_doc("3", "c", "lysosome again")]
    assert seed_profile(docs, n_terms=1) == ["lysosome"]


def test_the_same_paper_from_two_sources_is_loaded_once():
    """A PMID named in --pmids that the query already returned used to arrive
    twice, double-weighting its vocabulary in the seed profile and occupying
    two of the expand_seed_profile_n slots."""
    from bioleads.sources import dedupe_documents
    a = _pubmed_doc("1", "TM184C paper", "autophagy")
    b = _pubmed_doc("1", "TM184C paper", "autophagy")
    c = _pubmed_doc("2", "Another", "lysosome")
    out = dedupe_documents([a, b, c])
    assert [d.meta["pmid"] for d in out] == ["1", "2"]
    assert out[0] is a                      # first seen wins


def test_dedupe_keeps_documents_without_a_doc_id():
    from bioleads.sources import dedupe_documents
    from bioleads.sources import Document
    docs = [Document(doc_id="", text="one"), Document(doc_id="", text="two")]
    assert len(dedupe_documents(docs)) == 2


def test_dedupe_reports_what_it_dropped():
    from bioleads.sources import dedupe_documents
    lines = []
    dedupe_documents([_pubmed_doc("1", "a", "b"), _pubmed_doc("1", "a", "b")],
                     lines.append)
    assert any("duplicate document" in l for l in lines)


def test_a_duplicated_seed_no_longer_double_weights_the_profile():
    from bioleads.sources import dedupe_documents, seed_profile
    dup = [_pubmed_doc("1", "lysosome study", "lysosome"),
           _pubmed_doc("1", "lysosome study", "lysosome"),
           _pubmed_doc("2", "autophagy study", "autophagy")]
    # Before dedupe "lysosome" is in 2 of 3 documents and leads on document
    # frequency; after, the two terms tie and sort alphabetically.
    assert seed_profile(dup, n_terms=1) == ["lysosome"]
    assert seed_profile(dedupe_documents(dup), n_terms=1) == ["autophagy"]


def test_asking_for_more_seeds_than_exist_uses_all_of_them():
    """A query that returns 3 papers with the control at 10 is not an error."""
    from bioleads.sources import seed_profile
    docs = [_pubmed_doc(str(i), f"Paper {i} about autophagy", "lysosome") for i in (1, 2, 3)]
    assert seed_profile(docs, top_n=10, n_terms=3) == seed_profile(docs, top_n=0, n_terms=3)
    assert seed_profile(docs, top_n=999, n_terms=3) == seed_profile(docs, n_terms=3)


# --------------------------------------------------------------------------
# PubMed's own query translation as the source of terms
# --------------------------------------------------------------------------

CYTONEME_TR = '"cytoneme"[All Fields] OR "cytonemes"[All Fields]'
TNT_TR = ('("tunnel"[All Fields] OR "tunneled"[All Fields] OR '
          '"tunneling"[All Fields] OR "tunnelings"[All Fields] OR '
          '"tunnelization"[All Fields] OR "tunnelized"[All Fields] OR '
          '"tunnelled"[All Fields] OR "tunnelling"[All Fields] OR '
          '"tunnels"[All Fields]) AND ("nanotube s"[All Fields] OR '
          '"nanotubes"[MeSH Terms] OR "nanotubes"[All Fields] OR '
          '"nanotube"[All Fields])')


def test_a_plural_and_a_singular_become_one_concept():
    """The question this exists for: searching cytoneme must color a paper
    that says cytonemes."""
    from bioleads.querymatch import terms_from_translation
    assert terms_from_translation(CYTONEME_TR) == [["cytoneme", "cytonemes"]]


def test_a_group_matches_on_any_of_its_forms_and_reports_one_label():
    from bioleads.querymatch import terms_from_translation
    groups = terms_from_translation(CYTONEME_TR)
    assert matched_terms("Cytonemes carry Shh.", groups) == ["cytoneme"]
    assert matched_terms("A single cytoneme.", groups) == ["cytoneme"]
    assert matched_terms("Filopodia only.", groups) == []


def test_a_paper_with_the_plural_is_green_not_partial():
    """Flattening the groups would break this: classify would demand every
    surface form, so a paper saying only the plural could never be green."""
    from bioleads.querymatch import terms_from_translation
    groups = terms_from_translation(CYTONEME_TR)
    hits = matched_terms("Cytonemes carry Shh.", groups)
    assert classify(len(hits), len(groups)) == "all"


def test_and_separates_concepts_while_or_joins_forms():
    from bioleads.querymatch import terms_from_translation
    groups = terms_from_translation(TNT_TR)
    assert len(groups) == 2
    assert matched_terms("Tunneling nanotubes connect cells.", groups) == \
        [groups[0][0], groups[1][0]]


def test_an_oversized_expansion_collapses_to_a_truncation():
    """PubMed turned `tunneling` into nine words. Keeping the shortest bare
    would match none of them, since the test is on word boundaries."""
    from bioleads.querymatch import terms_from_translation
    groups = terms_from_translation(TNT_TR)
    assert groups[0] == ["tunnel*"]
    assert matched_terms("tunnelling nanotubes", groups[:1]) == ["tunnel*"]


def test_mesh_forms_are_dropped():
    """A MeSH heading is assigned by an indexer and often appears nowhere in
    the abstract, so matching on it would mark a paper for something its text
    does not say."""
    from bioleads.querymatch import terms_from_translation
    groups = terms_from_translation('"autophagy"[MeSH Terms]')
    assert groups == []


def test_author_and_journal_tags_are_still_dropped():
    from bioleads.querymatch import terms_from_translation
    assert terms_from_translation('"Isom DG"[Author] AND "Nature"[Journal]') == []


def test_the_readable_form_labels_the_group():
    """PubMed emits artifacts like "nanotube s"; a hover must not report that."""
    from bioleads.querymatch import terms_from_translation
    groups = terms_from_translation(TNT_TR)
    assert groups[1][0] == "nanotube"


def test_it_falls_back_to_the_raw_query_without_a_translation():
    from bioleads.querymatch import query_terms
    assert query_terms("cytoneme", None) == ["cytoneme"]
    assert query_terms("cytoneme", "") == ["cytoneme"]
    assert query_terms("cytoneme", CYTONEME_TR) == [["cytoneme", "cytonemes"]]


def test_annotation_returns_one_label_per_concept():
    doc = _pubmed_doc("1", "Cytonemes in development", "they carry Shh")
    g = nx.DiGraph()
    g.add_node("PMID:1", pmid="1")
    labels = annotate_citation_graph(g, [doc], "cytoneme", CYTONEME_TR)
    assert labels == ["cytoneme"]
    assert g.nodes["PMID:1"]["query_match"] == "all"
