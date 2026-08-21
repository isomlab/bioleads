"""End-to-end orchestration: documents -> terms -> graph -> hypotheses."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import networkx as nx

from .config import Config
from .sources import Document, load_documents, document_pmids, _check_cancel
from .ner import extract_entities
from .enrichment import rank_terms, TermScore, to_dataframe as terms_df
from .cooccurrence import build_cooccurrence
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
                f"senior-author net: {self.author_graph.number_of_nodes()} authors / "
                f"{self.author_graph.number_of_edges()} author citations")
        return " | ".join(parts)


def run_pipeline(
    *,
    pubmed_query: str | None = None,
    pmids: str | list[str] | None = None,
    refs: str | None = None,
    texts=None,
    cfg: Config | None = None,
    anchors: list[str] | None = None,
    out_dir: str | None = None,
    documents: list[Document] | None = None,
    cancel=None,
    progress=None,
) -> PipelineResult:
    """Run the full bioleads pipeline and optionally write outputs to `out_dir`.

    Provide documents directly, or any combination of pubmed_query /
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
    # Both strategies are off at expand_rounds = 0. relevance used to ignore the
    # setting and expand anyway, which made "rounds" mean nothing for half the
    # users of it and made the strategy default un-flippable: choosing it would
    # silently have put a network round on every run.
    relevance_mode = (documents is None and cfg.expand_strategy == "relevance"
                      and cfg.expand_rounds > 0)
    if documents is not None:
        docs = documents
        say(f"Using {len(docs)} document(s) supplied directly.")
    else:
        say("Loading documents…")
        docs = load_documents(
            pubmed_query=pubmed_query, pmids=pmids, refs=refs,
            texts=texts,
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
            cancel=cancel, log=progress,
        )
        say(f"Corpus after expansion: {len(docs)} document(s).")

    _check_cancel(cancel)
    say(f"Extracting entities (NER) from {len(docs)} document(s)…")
    entities = extract_entities(docs, cfg, progress=progress, cancel=cancel)
    say(f"  {sum(len(v) for v in entities.values())} entity mention(s) extracted.")
    _check_cancel(cancel)
    say("Ranking distinctive terms…")
    ranked = rank_terms(entities, cfg)
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
        prefetched = _corpus_records(docs, cfg, cancel=cancel, progress=progress)
        citation_graph = build_citation_graph(
            docs, cfg, prefetched=prefetched, cancel=cancel, progress=progress)
        _check_cancel(cancel)
        say("Building senior-author citation network…")
        author_graph = build_author_citation_graph(
            docs, cfg, prefetched=prefetched, cancel=cancel, progress=progress)

    result = PipelineResult(docs, entities, ranked, graph, candidates, clusters,
                            citation_graph, author_graph)

    if out_dir:
        say(f"Writing outputs to {out_dir}…")
        os.makedirs(out_dir, exist_ok=True)
        terms_csv = os.path.join(out_dir, "ranked_terms.csv")
        terms_df(ranked).to_csv(terms_csv, index=False)
        result.outputs["ranked_terms"] = terms_csv

        cand_csv = os.path.join(out_dir, "hypothesis_candidates.csv")
        cand_df(candidates).to_csv(cand_csv, index=False)
        result.outputs["candidates"] = cand_csv

        # The corpus as a plain PMID list: seeds and anything expansion added,
        # one per line and nothing else, so it can be pasted straight into
        # PubMed or fed to another tool with --pmids @file. Written only when
        # the corpus actually has PMIDs -- a PDF-only run would otherwise get
        # an empty file, which reads as a failure rather than as "not
        # applicable". The Outputs tab greys the row instead.
        pmids_out = document_pmids(docs)
        if pmids_out:
            pmids_txt = os.path.join(out_dir, "pmids.txt")
            with open(pmids_txt, "w", encoding="utf-8") as fh:
                fh.write("\n".join(pmids_out) + "\n")
            result.outputs["pmids"] = pmids_txt

        if clusters:
            clusters_csv = os.path.join(out_dir, "term_clusters.csv")
            clusters_df(clusters).to_csv(clusters_csv, index=False)
            result.outputs["clusters"] = clusters_csv

            say("Rendering term-cluster scatter…")
            scatter_html = os.path.join(out_dir, "term_clusters.html")
            scatter = write_cluster_scatter(clusters, scatter_html, cfg)
            if scatter:
                result.outputs["cluster_scatter"] = scatter

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

            say("Rendering senior-author citation network…")
            auth_html = os.path.join(out_dir, "author_network.html")
            result.outputs["author_network"] = write_author_html(
                author_graph, auth_html)
            auth_3d = write_author_html_3d(
                author_graph, os.path.join(out_dir, "author_network_3d.html"),
                seed=cfg.seed)
            if auth_3d:
                result.outputs["author_network_3d"] = auth_3d

            # The same senior authors and the same citation edges, measured by
            # output rather than standing: node size is corpus papers. Built
            # separately because the max_graph_nodes trim has to keep the most
            # published authors here, not the most cited ones — otherwise a lab
            # publishing steadily without being cited in-corpus, which is
            # precisely what this view is for, never reaches the picture.
            say("Rendering senior-author paper-count network…")
            paper_graph = build_author_citation_graph(
                docs, cfg, prefetched=prefetched, rank_by="papers",
                cancel=cancel, progress=progress)
            if paper_graph is not None and paper_graph.number_of_nodes():
                aprank_csv = os.path.join(out_dir, "author_paper_ranking.csv")
                authors_df(paper_graph, by="papers").to_csv(aprank_csv, index=False)
                result.outputs["author_paper_ranking"] = aprank_csv

                ap_html = os.path.join(out_dir, "author_paper_network.html")
                result.outputs["author_paper_network"] = write_author_html(
                    paper_graph, ap_html,
                    title="bioleads senior-author paper-count network",
                    size_attr="papers")
                ap_3d = write_author_html_3d(
                    paper_graph,
                    os.path.join(out_dir, "author_paper_network_3d.html"),
                    title="bioleads senior-author paper-count network (3D)",
                    seed=cfg.seed, size_attr="papers")
                if ap_3d:
                    result.outputs["author_paper_network_3d"] = ap_3d

    return result
