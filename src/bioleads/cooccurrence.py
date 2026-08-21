"""Entity co-occurrence network.

An edge connects two entities that appear together more than chance predicts.
We score edges by pointwise mutual information (PMI) so that strong, specific
associations outrank pairs that are merely both frequent.
"""
from __future__ import annotations

import math
from collections import Counter
from itertools import combinations

import networkx as nx

from .config import Config


def _doc_term_sets(
    entities: dict[str, list[str]], keep: set[str] | None
) -> list[set[str]]:
    sets = []
    for ents in entities.values():
        s = set(ents)
        if keep is not None:
            s &= keep
        if len(s) >= 2:
            sets.append(s)
    return sets


def build_cooccurrence(
    entities: dict[str, list[str]],
    cfg: Config | None = None,
    keep_terms: set[str] | None = None,
) -> nx.Graph:
    """Build a co-occurrence graph.

    Parameters
    ----------
    entities    {doc_id: [entity, ...]}
    keep_terms  optional whitelist (e.g. the top enriched terms) to keep the
                graph readable. If None, all entities are used.

    Node attrs: count (document frequency).
    Edge attrs: weight (co-occurrence count), pmi.
    """
    cfg = cfg or Config()
    doc_sets = _doc_term_sets(entities, keep_terms)
    n_docs = max(len(doc_sets), 1)

    node_df = Counter()                       # document frequency per term
    pair_df: Counter = Counter()              # co-document frequency per pair
    for s in doc_sets:
        node_df.update(s)
        for a, b in combinations(sorted(s), 2):
            pair_df[(a, b)] += 1

    g = nx.Graph()
    for (a, b), w in pair_df.items():
        if w < cfg.min_cooccurrence:
            continue
        # PMI = log[ P(a,b) / (P(a)P(b)) ]
        p_ab = w / n_docs
        p_a = node_df[a] / n_docs
        p_b = node_df[b] / n_docs
        pmi = math.log(p_ab / (p_a * p_b)) if p_a and p_b else 0.0
        if cfg.min_pmi is not None and pmi < cfg.min_pmi:
            continue
        g.add_edge(a, b, weight=w, pmi=round(pmi, 4))

    for n in g.nodes:
        g.nodes[n]["count"] = node_df[n]

    # Trim to the most connected nodes for visualization sanity.
    if g.number_of_nodes() > cfg.max_graph_nodes:
        top = sorted(g.degree, key=lambda kv: kv[1], reverse=True)
        keep = {n for n, _ in top[: cfg.max_graph_nodes]}
        g = g.subgraph(keep).copy()
    return g
