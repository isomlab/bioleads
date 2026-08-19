"""End-to-end orchestration: documents -> terms -> graph -> hypotheses."""
from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field

import networkx as nx

from .config import Config
from .sources import Document, load_documents, _check_cancel
from .ner import extract_entities
from .enrichment import rank_terms, TermScore, to_dataframe as terms_df
from .cooccurrence import build_cooccurrence, write_graph_html, write_graph_html_3d
from .citations import (
    build_citation_graph,
    build_author_citation_graph,
    write_citation_html,
    write_citation_html_3d,
    write_author_html,
    write_author_html_3d,
    to_dataframe as citations_df,
    authors_to_dataframe as authors_df,
    _corpus_records,
)
from .discovery import abc_candidates, Candidate, to_dataframe as cand_df
from .embeddings import (
    TermCluster,
    cluster_terms,
    term_to_cluster,
    write_cluster_scatter,
    to_dataframe as clusters_df,
)


@dataclass
class PipelineResult:
    documents: list[Document]
    entities: dict[str, list[str]]
    ranked_terms: list[TermScore]
    graph: nx.Graph
    candidates: list[Candidate]
    clusters: list[TermCluster] = field(default_factory=list)
    citation_graph: nx.DiGraph | None = None
    author_graph: nx.DiGraph | None = None
    outputs: dict[str, str] = field(default_factory=dict)

    def summary(self) -> str:
        parts = [
            f"{len(self.documents)} docs",
            f"{sum(len(v) for v in self.entities.values())} entity mentions",
            f"{len(self.ranked_terms)} ranked terms",
            f"graph: {self.graph.number_of_nodes()} nodes / "
            f"{self.graph.number_of_edges()} edges",
            f"{len(self.candidates)} hypothesis candidates",
        ]
        if self.clusters:
            parts.append(f"{len(self.clusters)} term clusters")
        if self.citation_graph is not None:
            parts.append(
                f"citation net: {self.citation_graph.number_of_nodes()} papers / "
                f"{self.citation_graph.number_of_edges()} citations")
        if self.author_graph is not None:
            parts.append(
                f"author net: {self.author_graph.number_of_nodes()} authors / "
                f"{self.author_graph.number_of_edges()} author citations")
        return " | ".join(parts)


def run_pipeline(
    *,
    pdf_path: str | None = None,
    pubmed_query: str | None = None,
    pmids: str | list[str] | None = None,
    refs: str | None = None,
    texts=None,
    cfg: Config | None = None,
    background: Counter | None = None,
    anchors: list[str] | None = None,
    out_dir: str | None = None,
    documents: list[Document] | None = None,
    cancel=None,
    progress=None,
) -> PipelineResult:
    """Run the full bioleads pipeline and optionally write outputs to `out_dir`.

    Provide documents directly, or any combination of pdf_path / pubmed_query /
    pmids / refs / texts to load them.

    Pass `cancel` (a threading.Event) to support cooperative cancellation: the
    pipeline checks it between stages and inside the network loops and raises
    sources.PipelineCancelled when it is set.

    Pass `progress` (a callable taking one str) to receive step-by-step status
    messages as each stage runs — handy for a live log/progress display.
    """
    cfg = cfg or Config()
    say = progress if callable(progress) else (lambda _msg: None)

    # In "relevance" mode the two-phase expansion runs here (it needs NER /
    # embeddings), so suppress load_documents' plain BFS snowball to avoid
    # double-expanding.
    relevance_mode = documents is None and cfg.expand_strategy == "relevance"
    if documents is not None:
        docs = documents
        say(f"Using {len(docs)} document(s) supplied directly.")
    else:
        say("Loading documents…")
        docs = load_documents(
            pdf_path=pdf_path, pubmed_query=pubmed_query, pmids=pmids, refs=refs,
            texts=texts,
            fulltext=cfg.pubmed_fulltext,
            expand_rounds=0 if relevance_mode else cfg.expand_rounds,
            expand_link=cfg.expand_link,
            expand_source=cfg.expand_source, expand_max=cfg.expand_max,
            retmax=cfg.pubmed_retmax, email=cfg.entrez_email,
            api_key=cfg.entrez_api_key,
            cancel=cancel, progress=progress,
        )
    if not docs:
        raise ValueError("No documents loaded. Check your inputs.")
    say(f"Corpus: {len(docs)} document(s).")

    if relevance_mode:
        say("Relevance-guided expansion (two-phase)…")
        from .expansion import relevance_guided_expand
        docs += relevance_guided_expand(
            docs, cfg, email=cfg.entrez_email, api_key=cfg.entrez_api_key,
            fulltext=cfg.pubmed_fulltext, cancel=cancel, log=progress,
        )
        say(f"Corpus after expansion: {len(docs)} document(s).")

    _check_cancel(cancel)
    say(f"Extracting entities (NER) from {len(docs)} document(s)…")
    entities = extract_entities(docs, cfg, progress=progress, cancel=cancel)
    say(f"  {sum(len(v) for v in entities.values())} entity mention(s) extracted.")
    _check_cancel(cancel)
    say("Ranking distinctive terms vs. background…")
    ranked = rank_terms(entities, cfg, background=background, progress=progress)
    say(f"  {len(ranked)} ranked term(s).")
    keep = {r.term for r in ranked}
    say("Building co-occurrence network…")
    graph = build_cooccurrence(entities, cfg, keep_terms=keep or None)
    say(f"  graph: {graph.number_of_nodes()} node(s) / "
        f"{graph.number_of_edges()} edge(s).")
    say("Generating Swanson ABC hypothesis candidates…")
    candidates = abc_candidates(graph, cfg, anchors=anchors)
    say(f"  {len(candidates)} candidate(s).")
    _check_cancel(cancel)

    clusters: list[TermCluster] = []
    if cfg.do_clustering and ranked:
        say("Clustering ranked terms…")
        clusters = cluster_terms([r.term for r in ranked], cfg, progress=progress)
        say(f"  {len(clusters)} cluster(s).")

    citation_graph: nx.DiGraph | None = None
    author_graph: nx.DiGraph | None = None
    if cfg.do_citation_network:
        _check_cancel(cancel)
        say("Building citation network (iCite)…")
        # One iCite fetch feeds both the paper- and author-level graphs.
        prefetched = _corpus_records(docs, cancel=cancel, progress=progress)
        citation_graph = build_citation_graph(
            docs, cfg, prefetched=prefetched, cancel=cancel, progress=progress)
        _check_cancel(cancel)
        say("Building author citation network…")
        author_graph = build_author_citation_graph(
            docs, cfg, prefetched=prefetched, cancel=cancel, progress=progress)

    result = PipelineResult(docs, entities, ranked, graph, candidates, clusters,
                            citation_graph, author_graph)
    groups = term_to_cluster(clusters) if clusters else None

    if out_dir:
        say(f"Writing outputs to {out_dir}…")
        os.makedirs(out_dir, exist_ok=True)
        terms_csv = os.path.join(out_dir, "ranked_terms.csv")
        terms_df(ranked).to_csv(terms_csv, index=False)
        result.outputs["ranked_terms"] = terms_csv

        cand_csv = os.path.join(out_dir, "hypothesis_candidates.csv")
        cand_df(candidates).to_csv(cand_csv, index=False)
        result.outputs["candidates"] = cand_csv

        if clusters:
            clusters_csv = os.path.join(out_dir, "term_clusters.csv")
            clusters_df(clusters).to_csv(clusters_csv, index=False)
            result.outputs["clusters"] = clusters_csv

            say("Rendering term-cluster scatter…")
            scatter_html = os.path.join(out_dir, "term_clusters.html")
            scatter = write_cluster_scatter(clusters, scatter_html, cfg)
            if scatter:
                result.outputs["cluster_scatter"] = scatter

        graph_html = os.path.join(out_dir, "cooccurrence.html")
        result.outputs["graph"] = write_graph_html(graph, graph_html, groups=groups)
        graph_3d = write_graph_html_3d(
            graph, os.path.join(out_dir, "cooccurrence_3d.html"),
            groups=groups, seed=cfg.seed)
        if graph_3d:
            result.outputs["graph_3d"] = graph_3d

        if citation_graph is not None and citation_graph.number_of_nodes():
            rank_csv = os.path.join(out_dir, "citation_ranking.csv")
            citations_df(citation_graph).to_csv(rank_csv, index=False)
            result.outputs["citation_ranking"] = rank_csv

            say("Rendering citation network…")
            cit_html = os.path.join(out_dir, "citation_network.html")
            result.outputs["citation_network"] = write_citation_html(
                citation_graph, cit_html)
            cit_3d = write_citation_html_3d(
                citation_graph, os.path.join(out_dir, "citation_network_3d.html"),
                seed=cfg.seed)
            if cit_3d:
                result.outputs["citation_network_3d"] = cit_3d

        if author_graph is not None and author_graph.number_of_nodes():
            arank_csv = os.path.join(out_dir, "author_ranking.csv")
            authors_df(author_graph).to_csv(arank_csv, index=False)
            result.outputs["author_ranking"] = arank_csv

            say("Rendering author citation network…")
            auth_html = os.path.join(out_dir, "author_network.html")
            result.outputs["author_network"] = write_author_html(
                author_graph, auth_html)
            auth_3d = write_author_html_3d(
                author_graph, os.path.join(out_dir, "author_network_3d.html"),
                seed=cfg.seed)
            if auth_3d:
                result.outputs["author_network_3d"] = auth_3d

    return result
