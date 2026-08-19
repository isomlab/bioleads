"""Command-line interface for bioleads.

Examples
--------
# From local PDFs, log-odds vs a background, write all outputs:
bioleads --pdf ./papers --background bg_counts.json --out ./results

# From a PubMed query:
bioleads --pubmed "GPCR allosteric modulation cardiac" --out ./results

# Open ABC discovery seeded from specific concepts:
bioleads --pdf ./papers --anchors "trpv1,inflammation" --out ./results
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter

from .config import Config
from .enrichment import load_background
from .pipeline import run_pipeline


class ParagraphHelpFormatter(argparse.HelpFormatter):
    """Help formatter that renders each option's explanation as a wrapped
    paragraph on its own lines below the flag, using (most of) the terminal
    width, instead of cramming it into a narrow right-hand column that runs
    off the edge of the screen.
    """

    def __init__(self, prog):
        width = min(shutil.get_terminal_size((80, 24)).columns - 2, 96)
        # max_help_position=4 forces the help onto its own indented lines
        # below the flag, so the paragraph gets the full width to wrap into.
        super().__init__(prog, max_help_position=4, width=width)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bioleads",
        formatter_class=ParagraphHelpFormatter,
        description="Mine biomedical literature for enriched terms, "
                    "co-occurrence networks, and Swanson ABC hypothesis leads.",
    )
    src = p.add_argument_group("inputs")
    src.add_argument("--pdf", metavar="PATH", help="PDF file or directory of PDFs")
    src.add_argument("--pubmed", metavar="QUERY", help="PubMed search query")
    src.add_argument("--pmids", metavar="IDS",
                     help="explicit PubMed IDs: comma/space-separated list, "
                          "or a path to a file of IDs (optionally '@file')")
    src.add_argument("--refs", metavar="FILE",
                     help="reference-manager export to seed from: RIS (.ris) "
                          "or EndNote XML (.xml), auto-detected")

    p.add_argument("--fulltext", action="store_true",
                   help="upgrade open-access PMC articles to full body text "
                        "(falls back to abstract otherwise)")
    p.add_argument("--out", default="./bioleads_out", help="output directory")
    p.add_argument("--background", metavar="JSON",
                   help="background term->count JSON for enrichment")
    p.add_argument("--method", choices=["log_odds", "hypergeometric", "tfidf"],
                   default="log_odds", help="enrichment scoring method")
    p.add_argument("--anchors", help="comma-separated seed concepts for ABC discovery")
    p.add_argument("--expand", type=int, default=0, metavar="ROUNDS",
                   help="grow the corpus by following citations from the seed "
                        "PMIDs for N rounds (0 = off)")
    p.add_argument("--expand-link", choices=["references", "cited_by", "both"],
                   default=Config.expand_link,
                   help="citation direction: 'references' = papers seeds cite, "
                        "'cited_by' = papers that cite the seeds, "
                        "'both' = follow both directions (default; backward refs "
                        "only resolve for PMC-indexed seeds)")
    p.add_argument("--expand-source", choices=["all", "ncbi", "icite"],
                   default="all",
                   help="citation backend: 'all' = union of NCBI ELink + NIH "
                        "iCite (default, broadest coverage), 'ncbi' = Entrez "
                        "ELink only (PMC-derived), 'icite' = NIH iCite / Open "
                        "Citation Collection only")
    p.add_argument("--expand-strategy", choices=["bfs", "relevance"],
                   default="bfs",
                   help="'bfs' = plain snowball along --expand-link (default); "
                        "'relevance' = profile the topic from the seeds alone, "
                        "then keep only the --expand-top-k most on-topic papers "
                        "in each direction")
    p.add_argument("--expand-top-k", type=int, default=Config.expand_top_k,
                   metavar="K", help="relevance strategy: keep the K most "
                                     "on-topic papers per direction")
    p.add_argument("--expand-max", type=int, default=Config.expand_max,
                   metavar="N", help="cap on total PMIDs (seeds + discovered)")
    p.add_argument("--citations", action=argparse.BooleanOptionalAction,
                   default=Config.do_citation_network,
                   help="build paper-to-paper AND author-to-author citation "
                        "networks (via NIH iCite), ranking papers and authors by "
                        "in-corpus + global citation count: writes "
                        "citation_ranking.csv / citation_network.html and "
                        "author_ranking.csv / author_network.html (+ _3d.html)")
    p.add_argument("--min-paper-degree", type=int,
                   default=Config.min_paper_degree, metavar="N",
                   help="keep only papers with at least N connections in the "
                        "citation network (citations given + received; 0 = keep "
                        "everything, 1 = drop the isolated papers). Applies to "
                        "the rankings as well as the HTML views")
    p.add_argument("--min-author-degree", type=int,
                   default=Config.min_author_degree, metavar="N",
                   help="the same threshold for the author network, counted in "
                        "distinct authors cited or citing. Separate because the "
                        "author graph is a projection, so its degrees run an "
                        "order of magnitude higher than the paper graph's")
    p.add_argument("--cluster", action="store_true",
                   help="cluster ranked terms with PubMedBERT: writes "
                        "term_clusters.csv and colors the graph by cluster "
                        '(needs the "embed" extra)')
    p.add_argument("--n-clusters", type=int, default=Config.n_clusters,
                   help="number of term clusters when --cluster is set")
    p.add_argument("--top-terms", type=int, default=200)
    p.add_argument("--min-doc-freq", type=int, default=2)
    p.add_argument("--scispacy-model", default="en_core_sci_sm")
    p.add_argument("--retmax", type=int, default=500, help="max PubMed records")
    p.add_argument("--email", default="disom.biophysics@gmail.com",
                   help="Entrez contact email")
    p.add_argument("--api-key", default=None, help="NCBI API key (optional)")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if not args.pdf and not args.pubmed and not args.pmids and not args.refs:
        print("error: provide --pdf, --pubmed, --pmids, and/or --refs",
              file=sys.stderr)
        return 2

    cfg = Config(
        scispacy_model=args.scispacy_model,
        enrichment_method=args.method,
        top_terms=args.top_terms,
        min_doc_freq=args.min_doc_freq,
        background_path=args.background,
        pubmed_retmax=args.retmax,
        entrez_email=args.email,
        entrez_api_key=args.api_key,
        pubmed_fulltext=args.fulltext,
        do_clustering=args.cluster,
        do_citation_network=args.citations,
        min_paper_degree=args.min_paper_degree,
        min_author_degree=args.min_author_degree,
        n_clusters=args.n_clusters,
        expand_rounds=args.expand,
        expand_link=args.expand_link,
        expand_source=args.expand_source,
        expand_strategy=args.expand_strategy,
        expand_top_k=args.expand_top_k,
        expand_max=args.expand_max,
    )

    background: Counter | None = load_background(args.background) if args.background else None
    anchors = [a.strip() for a in args.anchors.split(",")] if args.anchors else None

    result = run_pipeline(
        pdf_path=args.pdf, pubmed_query=args.pubmed, pmids=args.pmids,
        refs=args.refs, cfg=cfg,
        background=background, anchors=anchors, out_dir=args.out,
    )

    print(result.summary())
    for label, path in result.outputs.items():
        print(f"  {label:14s} -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
