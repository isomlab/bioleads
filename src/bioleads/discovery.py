"""Swanson-style literature-based discovery (the ABC model).

If concept A co-occurs with intermediates B, and those same B co-occur with
concept C, but A and C (almost) never appear together directly, then A--C is a
candidate hidden association worth investigating. This is how Swanson linked
dietary fish oil to Raynaud's syndrome via blood-viscosity intermediates.

We operate on the co-occurrence graph: open triangles (A-B, B-C present, A-C
weak/absent) with enough shared intermediates become ranked candidates.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx

from .config import Config


@dataclass
class Candidate:
    a: str
    c: str
    shared_b: list[str] = field(default_factory=list)
    score: float = 0.0           # strength of indirect linkage
    direct_cooccurrence: int = 0

    def as_row(self) -> dict:
        return {
            "concept_a": self.a,
            "concept_c": self.c,
            "n_intermediates": len(self.shared_b),
            "intermediates": "; ".join(self.shared_b[:10]),
            "score": round(self.score, 4),
            "direct_cooccurrence": self.direct_cooccurrence,
        }


def abc_candidates(
    g: nx.Graph,
    cfg: Config | None = None,
    anchors: list[str] | None = None,
) -> list[Candidate]:
    """Find ABC candidate links in co-occurrence graph `g`.

    Parameters
    ----------
    anchors  optional list of concepts to use as A (open discovery from a few
             seeds). If None, all nodes are considered (closed/exhaustive).
    """
    cfg = cfg or Config()
    nodes = anchors if anchors else list(g.nodes)

    candidates: list[Candidate] = []
    seen: set[frozenset] = set()

    for a in nodes:
        if a not in g:
            continue
        b_neighbors = set(g.neighbors(a))
        # all nodes two hops out (potential C)
        two_hop: set[str] = set()
        for b in b_neighbors:
            two_hop |= set(g.neighbors(b))
        two_hop -= b_neighbors
        two_hop.discard(a)

        for c in two_hop:
            key = frozenset((a, c))
            if key in seen:
                continue
            direct = g[a][c]["weight"] if g.has_edge(a, c) else 0
            if direct > cfg.max_direct_cooccurrence:
                continue
            shared = sorted(b_neighbors & set(g.neighbors(c)))
            if len(shared) < cfg.min_b_links:
                continue
            # Score: sum over shared B of (PMI(A,B) * PMI(B,C)), rewarding
            # specific intermediate links over generic hubs.
            score = 0.0
            for b in shared:
                score += g[a][b]["pmi"] * g[b][c]["pmi"]
            candidates.append(
                Candidate(a=a, c=c, shared_b=shared, score=score,
                          direct_cooccurrence=direct)
            )
            seen.add(key)

    candidates.sort(key=lambda x: x.score, reverse=True)
    return candidates[: cfg.top_candidates]


def to_dataframe(cands: list[Candidate]):
    import pandas as pd
    return pd.DataFrame([c.as_row() for c in cands])
