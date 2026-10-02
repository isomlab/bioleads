"""Regression tests for literal query-term matching on the citation network.

Every case in the field-tag section is a bug that actually shipped and was
caught by eye rather than by a test, which is the reason this file exists. The
parser reads field tags *after* splitting on booleans and parentheses, because
a tag binds to the whole term before it and splitting on whitespace first lets
``Isom DG[au]`` leak the bare word ``Isom`` into the term list.
"""
import networkx as nx
import pytest

from bioleads.querymatch import (
    MATCH_COLORS,
    NON_TEXT_FIELDS,
    annotate_citation_graph,
    classify,
    matched_terms,
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


def test_every_class_has_a_colour():
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
