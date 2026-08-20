"""Tkinter desktop GUI for bioleads.

Launch with `bioleads-gui` (or `python -m bioleads.gui`). The pipeline runs in a
background thread so the window stays responsive; results stream back to the UI
through a queue polled on the Tk event loop.

Only the standard library is needed for the GUI itself — Tkinter ships with
CPython. The pipeline's own optional extras (pubmed, ner, ...) still apply.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import traceback
import webbrowser
from dataclasses import replace

import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk

from .config import Config
from .cooccurrence import write_graph_html, write_graph_html_3d
from .embeddings import (
    TermCluster,
    cluster_terms,
    term_to_cluster,
    write_cluster_scatter,
    to_dataframe as clusters_df,
)
from .pipeline import PipelineResult, run_pipeline
from .sources import PipelineCancelled

# Visual language shared with the other lab tools (probelog, plasmidlog): a navy
# header, a soft grey page, white cards holding the fields, and one green accent
# for the primary action. Kept as module constants so a future restyle is one
# edit here rather than a sweep through the widget code.
BG = "#eef2f5"
CARD = "#ffffff"
HEADER_BG = "#1f3a5f"
HEADER_FG = "#ffffff"
SUBTITLE_FG = "#c3d0de"
TEXT = "#1f2a36"
MUTED = "#6b7a8d"
BORDER = "#cfd8e0"
ACCENT = "#2e7d32"
ACCENT_ACTIVE = "#256628"

# The form column is deliberately narrow. Every control in it is a short entry,
# a spinbox or a dropdown, so stretching it across a 1120px window left the
# fields metres from their labels and the eye with nowhere to land.
FORM_WIDTH = 430

# What the Outputs tab lists, in order: (group heading, [(row label, key)]),
# where each key is a PipelineResult.outputs key. A group whose run produced
# none of its files is hidden entirely; within a group that produced something,
# missing siblings stay listed but greyed, so it's visible what wasn't made.
# Any outputs key not named here still appears, under "Other outputs".
OUTPUT_GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    ("Term co-occurrence network", [
        ("2D", "graph"),
        ("3D", "graph_3d"),
    ]),
    ("Term clusters", [
        ("Scatter", "cluster_scatter"),
        ("Table (CSV)", "clusters"),
    ]),
    ("Paper citation network", [
        ("2D", "citation_network"),
        ("3D", "citation_network_3d"),
        ("Ranking (CSV)", "citation_ranking"),
    ]),
    ("Senior-author citation network", [
        ("2D", "author_network"),
        ("3D", "author_network_3d"),
        ("Ranking (CSV)", "author_ranking"),
    ]),
    ("Tables", [
        ("Ranked terms (CSV)", "ranked_terms"),
        ("Hypotheses (CSV)", "candidates"),
    ]),
]


class BioleadsGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("bioleads")
        self.root.geometry("1240x800")
        self.root.minsize(1000, 640)
        self.root.configure(background=BG)
        self._queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._cancel = threading.Event()
        # Everything below describes the *current* run and is reset by
        # _clear_results() when a new one starts, so a failed or stopped run
        # can't leave the previous run's results wired to the buttons.
        self._result: PipelineResult | None = None
        self._out_dir: str | None = None
        self._cfg: Config | None = None

        self._build_fonts()
        self._build_style()
        self._build_header()
        self._build_statusbar()   # pinned to the bottom edge before the body
        self._build_body()
        self.root.after(120, self._poll_queue)

    # ----------------------------------------------------------------- UI --
    def _build_fonts(self) -> None:
        fam = tkfont.nametofont("TkDefaultFont").actual("family")
        self.f_title = tkfont.Font(family=fam, size=19, weight="bold")
        self.f_sub = tkfont.Font(family=fam, size=11)
        self.f_section = tkfont.Font(family=fam, size=13, weight="bold")
        self.f_label = tkfont.Font(family=fam, size=11)
        self.f_help = tkfont.Font(family=fam, size=9)

    def _build_style(self) -> None:
        st = ttk.Style()
        try:
            st.theme_use("clam")          # the only stock theme that honours
        except tk.TclError:               # background colour on every widget
            pass
        st.configure("TFrame", background=BG)
        st.configure("Card.TFrame", background=CARD)
        st.configure("Header.TFrame", background=HEADER_BG)
        st.configure("Title.TLabel", background=HEADER_BG, foreground=HEADER_FG,
                     font=self.f_title)
        st.configure("Sub.TLabel", background=HEADER_BG, foreground=SUBTITLE_FG,
                     font=self.f_sub)
        st.configure("Section.TLabel", background=BG, foreground=HEADER_BG,
                     font=self.f_section)
        st.configure("Page.TLabel", background=BG, foreground=TEXT, font=self.f_label)
        st.configure("PageHelp.TLabel", background=BG, foreground=MUTED,
                     font=self.f_help)
        st.configure("Field.TLabel", background=CARD, foreground=TEXT,
                     font=self.f_label)
        st.configure("Help.TLabel", background=CARD, foreground=MUTED,
                     font=self.f_help)
        st.configure("Card.TCheckbutton", background=CARD, font=self.f_label)
        st.configure("Accent.TButton", font=self.f_label, foreground="#fff",
                     background=ACCENT, padding=(16, 8), borderwidth=0)
        st.map("Accent.TButton", background=[("active", ACCENT_ACTIVE),
                                             ("disabled", "#a8c3aa")])
        st.configure("TButton", font=self.f_label, padding=(10, 5))
        st.configure("Treeview", background=CARD, fieldbackground=CARD,
                     font=self.f_label, rowheight=22)
        st.configure("Treeview.Heading", font=self.f_label)

    def _build_header(self) -> None:
        bar = ttk.Frame(self.root, style="Header.TFrame")
        bar.pack(fill="x")
        inner = ttk.Frame(bar, style="Header.TFrame")
        inner.pack(fill="x", padx=20, pady=12)
        ttk.Label(inner, text="bioleads", style="Title.TLabel").pack(side="left")
        ttk.Label(inner, text="  literature mining for biological leads",
                  style="Sub.TLabel").pack(side="left", padx=(8, 0))

    # --- the two-column body: settings on the left, everything a run produces
    # --- on the right, so a run's progress is visible without leaving the form.
    def _build_body(self) -> None:
        outer = ttk.Frame(self.root, style="TFrame")
        outer.pack(fill="both", expand=True)

        left = ttk.Frame(outer, style="TFrame", width=FORM_WIDTH)
        left.pack(side="left", fill="y", padx=(18, 8), pady=14)
        left.pack_propagate(False)        # honour FORM_WIDTH, don't fit content
        self._build_form_column(left)

        right = ttk.Frame(outer, style="TFrame")
        right.pack(side="left", fill="both", expand=True, padx=(8, 18), pady=14)
        ttk.Label(right, text="Results", style="Section.TLabel").pack(anchor="w")
        self._build_results(right)

    def _build_form_column(self, left) -> None:
        ttk.Label(left, text="Set what you want to search, then run.",
                  style="PageHelp.TLabel").pack(anchor="w", pady=(0, 6))
        # Buttons pinned to the bottom; the form scrolls between them and the
        # blurb, so a small window never hides Run behind a scroll.
        self._build_actions(left)
        wrap = ttk.Frame(left, style="TFrame")
        wrap.pack(side="top", fill="both", expand=True)
        self._build_inputs(self._scroll_body(wrap))

    def _scroll_body(self, parent):
        """A vertically scrolling page inside `parent`; returns the inner frame."""
        canvas = tk.Canvas(parent, background=BG, highlightthickness=0)
        sb = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        body = ttk.Frame(canvas, style="TFrame")
        body.bind("<Configure>",
                  lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        window = canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._bind_wheel(canvas)
        return body

    @staticmethod
    def _bind_wheel(canvas) -> None:
        """Scroll `canvas` while the pointer is over it, and only then.

        Bound on Enter and dropped on Leave: a permanent bind_all would have
        each scrolling region fighting the others for the wheel.
        """
        def wheel(event):
            # macOS sends ±1 per notch; Windows sends multiples of 120.
            step = -event.delta
            if abs(event.delta) >= 120:
                step = int(step / 120)
            canvas.yview_scroll(step, "units")

        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))

    def _section(self, parent, title):
        """A section heading over a white card; returns the card to fill."""
        ttk.Label(parent, text=title, style="Section.TLabel").pack(
            anchor="w", pady=(12, 6))
        card = ttk.Frame(parent, style="Card.TFrame")
        card.pack(fill="x")
        card.columnconfigure(1, weight=1)
        return card

    def _field(self, card, row, label, widget, hint=None, button=None):
        """One label + control row inside a card, with optional hover help."""
        ttk.Label(card, text=label, style="Field.TLabel").grid(
            row=row, column=0, sticky="w", padx=(14, 8), pady=(9, 9))
        widget.grid(row=row, column=1, sticky="we", padx=(0, 14 if not button else 6),
                    pady=(9, 9))
        if hint:
            self._add_tooltip(widget, hint)
        if button:
            text, cmd = button
            ttk.Button(card, text=text, command=cmd, width=8).grid(
                row=row, column=2, sticky="e", padx=(0, 12), pady=(9, 9))
        return widget

    def _entry(self, card):
        return ttk.Entry(card, font=self.f_label)

    def _spin(self, card, var, lo, hi, width=7):
        return ttk.Spinbox(card, from_=lo, to=hi, width=width, textvariable=var,
                           font=self.f_label)

    def _combo(self, card, var, values, width=12):
        return ttk.Combobox(card, textvariable=var, values=values, width=width,
                            state="readonly", font=self.f_label)

    def _build_inputs(self, page) -> None:
        self.pubmed_var = tk.StringVar()
        self.pmids_var = tk.StringVar()
        self.refs_var = tk.StringVar()
        self.out_var = tk.StringVar(value=os.path.abspath("./bioleads_out"))
        self.anchors_var = tk.StringVar()
        self.citations_var = tk.BooleanVar(value=Config.do_citation_network)
        self.mindeg_paper_var = tk.IntVar(value=Config.min_paper_degree)
        self.mindeg_author_var = tk.IntVar(value=Config.min_author_degree)
        self.nclusters_var = tk.IntVar(value=Config.n_clusters)
        self.expand_var = tk.IntVar(value=Config.expand_rounds)
        self.expand_link_var = tk.StringVar(value=Config.expand_link)
        self.expand_source_var = tk.StringVar(value=Config.expand_source)
        self.expand_strategy_var = tk.StringVar(value=Config.expand_strategy)
        self.expand_topk_var = tk.IntVar(value=Config.expand_top_k)
        self.expand_max_var = tk.IntVar(value=Config.expand_max)
        self.retmax_var = tk.IntVar(value=Config.pubmed_retmax)

        # --- what to search -------------------------------------------------
        card = self._section(page, "Corpus")
        e = self._entry(card)
        e.configure(textvariable=self.pubmed_var)
        self._field(card, 0, "PubMed query", e,
                    'An Entrez search (e.g. "CFTR AND chloride channel"). '
                    "Fetches title + abstract for each hit, up to Max query "
                    "hits.")
        e = self._entry(card)
        e.configure(textvariable=self.pmids_var)
        self._field(card, 1, "PubMed IDs", e,
                    "Specific records by PMID: comma/space-separated, or load "
                    "a file of IDs. These are the seeds citation expansion "
                    "follows.",
                    button=("Load…", self._pick_pmid_file))
        e = self._entry(card)
        e.configure(textvariable=self.refs_var)
        self._field(card, 2, "References file", e,
                    "An EndNote/Zotero export: RIS (.ris) or EndNote XML "
                    "(.xml), auto-detected. Title + abstract are used as "
                    "written; PMIDs found in the file are what seed expansion "
                    "and the citation networks.",
                    button=("Browse…", self._pick_refs))
        self._field(card, 3, "Max query hits",
                    self._spin(card, self.retmax_var, 1, 100000),
                    "Cap on how many hits a PubMed search may fetch.")

        # --- grow it --------------------------------------------------------
        card = self._section(page, "Grow the corpus")
        self._field(card, 0, "Strategy",
                    self._combo(card, self.expand_strategy_var, ["bfs", "relevance"]),
                    "How candidates are chosen: bfs snowballs along Follow, "
                    "adding every linked paper — exhaustive, and it drifts off "
                    "topic because a reference list spans every field the seed "
                    "touched. relevance trusts neither direction: it builds a "
                    "topic profile from your seeds alone, then keeps only the "
                    "top-K papers most similar to it in each direction. "
                    "Benchmarked far cleaner than bfs at equal reach — see "
                    "docs/benchmark.md.")
        self._field(card, 1, "Rounds",
                    self._spin(card, self.expand_var, 0, 10),
                    "Follow citation links out from the PMID seeds this many "
                    "rounds (0 = off, and off is the default); each round "
                    "chases what the previous round found. bfs runs this "
                    "many rounds; relevance runs one gated round in each "
                    "direction, so for it any value above 0 means the same "
                    "thing.")
        self._field(card, 2, "Follow",
                    self._combo(card, self.expand_link_var,
                                ["references", "cited_by", "both"]),
                    "Which direction to follow: references = papers your seeds "
                    "cite (backward, into the foundations); cited_by = papers "
                    "that cite your seeds (forward, into the follow-up work); "
                    "both = the union. Ignored by the relevance strategy, "
                    "which always does both, gating each.")
        self._field(card, 3, "Source",
                    self._combo(card, self.expand_source_var,
                                ["all", "ncbi", "icite"]),
                    "Which backend supplies citation links: ncbi (Entrez "
                    "ELink, PMC-derived), icite (NIH iCite / Open Citation "
                    "Collection), or all (the union — broadest coverage, and "
                    "it still works if one backend is down).")
        self._field(card, 4, "Relevance top-K",
                    self._spin(card, self.expand_topk_var, 1, 100000),
                    "Relevance strategy only (ignored by bfs). Candidates in "
                    "each direction are scored against the seed profile and "
                    "only the top-K are kept — so this is the main control "
                    "over corpus size and cleanliness. Benchmarked: K=25 is "
                    "sharpest, K=50 the best all-round (the default), and "
                    "K~100–200 keeps 76–92% of bfs's reach at 2–3x its "
                    "precision — prefer that range when you want ABC "
                    "hypotheses, which need the linking concepts present at "
                    "all. Max corpus size still caps the total.")
        self._field(card, 5, "Max corpus size",
                    self._spin(card, self.expand_max_var, 1, 100000),
                    "Hard cap on the total PMIDs (seeds + discovered) after "
                    "expansion.")

        # --- what to build from it ------------------------------------------
        card = self._section(page, "Analysis")
        e = self._entry(card)
        e.configure(textvariable=self.anchors_var)
        self._field(card, 0, "ABC anchors", e,
                    "Comma-separated concepts to use as A in the Swanson ABC "
                    "search — open discovery from a starting point you care "
                    "about. Leave empty to try every term in the network "
                    "(exhaustive).")
        self._field(card, 1, "Clusters",
                    self._spin(card, self.nclusters_var, 2, 200),
                    "Target number of KMeans groups in PubMedBERT space. "
                    "Applied by the Cluster terms button, not by Run pipeline.")

        # --- citation networks ----------------------------------------------
        card = self._section(page, "Citation networks")
        chk = ttk.Checkbutton(card, text="Build them (NIH iCite)",
                              variable=self.citations_var,
                              style="Card.TCheckbutton")
        chk.grid(row=0, column=0, columnspan=3, sticky="w", padx=(12, 14),
                 pady=(10, 2))
        self._add_tooltip(
            chk,
            "Builds the paper→paper AND senior-author→senior-author citation "
            "networks from NIH iCite, ranking each by in-corpus citations (how "
            "foundational within your set) and global citations (across all of "
            "PubMed). Each paper is represented by its LAST author — the "
            "senior author, i.e. the lab the work came from; first and middle "
            "authors do not appear. Only PMID-bearing documents can appear; a "
            "reference record with no accession is skipped.")
        self._field(card, 1, "Min degree — papers",
                    self._spin(card, self.mindeg_paper_var, 0, 10000),
                    "Keep only papers with at least this many connections in "
                    "the network — citations they receive from the corpus plus "
                    "citations they make to it. 0 keeps everything; 1 drops "
                    "the papers with no intra-corpus link at all, which in a "
                    "sparse corpus is most of them. Filters the ranking and "
                    "the picture alike.")
        self._field(card, 2, "Min degree — senior authors",
                    self._spin(card, self.mindeg_author_var, 0, 10000),
                    "The same threshold for the senior-author network, counted "
                    "in distinct senior authors cited or citing. It is a "
                    "separate number because that graph is not the paper "
                    "graph: each node is one lab, and a productive lab absorbs "
                    "several of the corpus's papers along with all of their "
                    "links, so the two degree distributions differ.")
        note = ttk.Label(card, style="Help.TLabel", wraplength=FORM_WIDTH - 60,
                         text="One node per lab, not per byline — each paper is "
                              "represented by its last author.")
        note.grid(row=3, column=0, columnspan=3, sticky="w", padx=(12, 14),
                  pady=(0, 10))

        # --- where it lands --------------------------------------------------
        card = self._section(page, "Output")
        e = self._entry(card)
        e.configure(textvariable=self.out_var)
        self._field(card, 0, "Folder", e,
                    "Where the CSVs and interactive HTML land; they are also "
                    "listed, with Open buttons, in the Outputs tab.",
                    button=("Browse…", self._pick_out))

    def _add_tooltip(self, widget, text) -> None:
        # Lightweight hover tooltip — avoids cluttering the grid with help text.
        tip = {"win": None}

        def show(_):
            if tip["win"] or not text:
                return
            x = widget.winfo_rootx() + 10
            y = widget.winfo_rooty() + widget.winfo_height() + 2
            win = tk.Toplevel(widget)
            win.wm_overrideredirect(True)
            win.wm_geometry(f"+{x}+{y}")
            ttk.Label(win, text=text, relief="solid", borderwidth=1,
                      background=CARD, foreground=TEXT, font=self.f_help,
                      padding=(8, 6), wraplength=360, justify="left").pack()
            tip["win"] = win

        def hide(_):
            if tip["win"]:
                tip["win"].destroy()
                tip["win"] = None

        widget.bind("<Enter>", show)
        widget.bind("<Leave>", hide)

    def _build_actions(self, parent) -> None:
        """The run controls, pinned to the bottom of the form column.

        Everything a run *produces* is listed in the Outputs tab instead, so
        this row never competes with the settings above it for width.
        """
        bar = ttk.Frame(parent, style="TFrame")
        bar.pack(side="bottom", fill="x", pady=(10, 0))

        self.run_btn = ttk.Button(bar, text="Run pipeline", style="Accent.TButton",
                                  command=self._on_run)
        self.run_btn.pack(side="left")
        self._add_tooltip(
            self.run_btn,
            "Collects the documents, grows the corpus, extracts entities, "
            "ranks the terms, builds the term network and proposes hypotheses "
            "— plus the citation networks if Citation network is ticked. Runs "
            "in a background thread; progress streams to the Log tab.")
        self.stop_btn = ttk.Button(bar, text="Stop", command=self._on_stop,
                                   state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        self._add_tooltip(
            self.stop_btn,
            "Halts after the current step finishes — an in-flight network "
            "fetch cannot be interrupted mid-call. No results are written.")
        self.cluster_btn = ttk.Button(bar, text="Cluster terms",
                                      command=self._on_cluster, state="disabled")
        self.cluster_btn.pack(side="left")
        self._add_tooltip(
            self.cluster_btn,
            "Groups the ranked terms in PubMedBERT space, fills the Clusters "
            "tab, recolors the co-occurrence graph by cluster, and writes the "
            "embedding scatter. Available after a run; the first use "
            "downloads the model.")

    def _build_statusbar(self) -> None:
        """A dedicated bottom strip for status text + the progress indicator.

        Keeping these off the action row means the button count can grow without
        crowding the progress bar against the window edge.
        """
        bar = ttk.Frame(self.root, style="TFrame", padding=(18, 6))
        bar.pack(side="bottom", fill="x")
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=180)
        self.progress.pack(side="right")
        self.status_var = tk.StringVar(value="Ready.")
        ttk.Label(bar, textvariable=self.status_var, anchor="w",
                  style="PageHelp.TLabel").pack(side="left", fill="x", expand=True)

    def _build_results(self, parent) -> None:
        nb = ttk.Notebook(parent)
        nb.pack(fill="both", expand=True, pady=(6, 0))

        log_tab = ttk.Frame(nb)
        nb.add(log_tab, text="  Log  ")
        self.log = tk.Text(log_tab, wrap="word", height=10, state="disabled",
                           font=self.f_label, background=CARD, foreground=TEXT,
                           relief="flat", padx=10, pady=8)
        self.log.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(log_tab, command=self.log.yview)
        sb.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=sb.set)

        self.terms_tree = self._make_tree(
            nb, "Ranked terms",
            [("term", 240), ("score", 90), ("corpus_count", 110),
             ("doc_freq", 90)])
        self.cand_tree = self._make_tree(
            nb, "Hypotheses",
            [("a", 160), ("c", 160), ("score", 80),
             ("direct", 60), ("shared_b", 240)])

        clu_tab = ttk.Frame(nb)
        nb.add(clu_tab, text="  Clusters  ")
        self.clusters_tree = ttk.Treeview(
            clu_tab, columns=("size",), show="tree headings")
        self.clusters_tree.heading("#0", text="Cluster / member terms")
        self.clusters_tree.column("#0", width=400, anchor="w")
        self.clusters_tree.heading("size", text="size")
        self.clusters_tree.column("size", width=80, anchor="e")
        self.clusters_tree.pack(side="left", fill="both", expand=True)
        clu_sb = ttk.Scrollbar(clu_tab, command=self.clusters_tree.yview)
        clu_sb.pack(side="right", fill="y")
        self.clusters_tree.configure(yscrollcommand=clu_sb.set)

        self._build_outputs_tab(nb)

    def _build_outputs_tab(self, nb) -> None:
        """A scrollable list of the files a run produced, each with an Open button.

        Built from result.outputs rather than from a fixed set of buttons, so a
        new pipeline output shows up here without any UI bookkeeping.
        """
        tab = ttk.Frame(nb)
        nb.add(tab, text="  Outputs  ")

        self._heading_font = tkfont.nametofont("TkDefaultFont").copy()
        self._heading_font.configure(weight="bold")

        canvas = tk.Canvas(tab, highlightthickness=0)
        sb = ttk.Scrollbar(tab, orient="vertical", command=canvas.yview)
        self.outputs_frame = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=self.outputs_frame, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.outputs_frame.bind(
            "<Configure>",
            lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        # Keep the inner frame exactly as wide as the canvas so each row's Open
        # button right-aligns against the scrollbar.
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(window, width=e.width))

        def _wheel(event):
            # macOS sends ±1 per notch; Windows sends multiples of 120.
            step = -event.delta
            if abs(event.delta) >= 120:
                step = int(step / 120)
            canvas.yview_scroll(step, "units")

        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", _wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))

        self._refresh_outputs()

    def _refresh_outputs(self) -> None:
        """Rebuild the Outputs tab from the current run's outputs."""
        for child in self.outputs_frame.winfo_children():
            child.destroy()

        outs = dict(self._result.outputs) if self._result else {}
        if not outs:
            ttk.Label(self.outputs_frame, padding=(12, 12), justify="left",
                      text="No outputs yet.\n\nRun the pipeline and the files it "
                           "writes will be listed here.").pack(anchor="w")
            return

        named = {key for _, rows in OUTPUT_GROUPS for _, key in rows}
        groups = list(OUTPUT_GROUPS)
        extra = [(key.replace("_", " ").capitalize(), key)
                 for key in outs if key not in named]
        if extra:
            groups.append(("Other outputs", extra))

        for title, rows in groups:
            if not any(outs.get(key) for _, key in rows):
                continue
            ttk.Label(self.outputs_frame, text=title, font=self._heading_font,
                      padding=(12, 10, 12, 2)).pack(anchor="w")
            for label, key in rows:
                self._output_row(label, outs.get(key))

        ttk.Separator(self.outputs_frame, orient="horizontal").pack(
            fill="x", padx=12, pady=(12, 8))
        foot = ttk.Frame(self.outputs_frame)
        foot.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(foot, text="Open output folder", command=self._open_out
                   ).pack(side="left")
        ttk.Label(foot, text=self._display_path(self._out_dir or "")).pack(
            side="left", padx=8)

    def _output_row(self, label: str, path: str | None) -> None:
        row = ttk.Frame(self.outputs_frame)
        row.pack(fill="x", padx=(28, 12), pady=1)
        exists = bool(path) and os.path.exists(path)

        ttk.Label(row, text=label, width=18, anchor="w").pack(side="left")
        btn = ttk.Button(row, text="Open", width=7,
                         command=lambda p=path: self._open_path(p))
        btn.pack(side="right")
        if not exists:
            btn.configure(state="disabled")

        detail = ttk.Label(
            row, anchor="w",
            text=self._display_path(path) if exists else "— not produced this run")
        detail.pack(side="left", fill="x", expand=True, padx=(6, 6))
        if exists:
            self._add_tooltip(detail, os.path.abspath(path))
        else:
            detail.configure(foreground="#888888")

    @staticmethod
    def _display_path(path: str, limit: int = 72) -> str:
        """Home-relative, middle-elided path; the tooltip carries the full one."""
        if not path:
            return ""
        home = os.path.expanduser("~")
        shown = "~" + path[len(home):] if path.startswith(home) else path
        if len(shown) > limit:
            keep = (limit - 1) // 2
            shown = f"{shown[:keep]}…{shown[-keep:]}"
        return shown

    def _make_tree(self, nb, title, columns) -> ttk.Treeview:
        tab = ttk.Frame(nb)
        nb.add(tab, text=f"  {title}  ")
        tree = ttk.Treeview(tab, columns=[c for c, _ in columns], show="headings")
        for col, width in columns:
            tree.heading(col, text=col)
            tree.column(col, width=width, anchor="w")
        tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(tab, command=tree.yview)
        sb.pack(side="right", fill="y")
        tree.configure(yscrollcommand=sb.set)
        return tree

    # -------------------------------------------------------------- file pickers --
    def _pick_pmid_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Select a file of PubMed IDs",
            filetypes=[("Text", "*.txt *.csv *.tsv"), ("All files", "*.*")])
        if path:
            self.pmids_var.set(f"@{path}")

    def _pick_refs(self) -> None:
        path = filedialog.askopenfilename(
            title="Select a reference-manager export (RIS or EndNote XML)",
            filetypes=[("RIS / EndNote XML", "*.ris *.xml *.txt"),
                       ("All files", "*.*")])
        if path:
            self.refs_var.set(path)

    def _pick_out(self) -> None:
        path = filedialog.askdirectory(title="Select output folder")
        if path:
            self.out_var.set(path)

    # ----------------------------------------------------------------- run --
    def _on_run(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        pubmed = self.pubmed_var.get().strip() or None
        pmids = self.pmids_var.get().strip() or None
        refs = self.refs_var.get().strip() or None
        if not (pubmed or pmids or refs):
            messagebox.showwarning(
                "No input",
                "Provide at least one of: PDF path, PubMed query, PubMed IDs, "
                "or a references file.")
            return

        out_dir = self.out_var.get().strip() or "./bioleads_out"
        anchors_raw = self.anchors_var.get().strip()
        anchors = [a.strip() for a in anchors_raw.split(",") if a.strip()] or None

        cfg = Config(
            pubmed_retmax=int(self.retmax_var.get()),
            do_citation_network=self.citations_var.get(),
            min_paper_degree=int(self.mindeg_paper_var.get()),
            min_author_degree=int(self.mindeg_author_var.get()),
            expand_rounds=int(self.expand_var.get()),
            expand_link=self.expand_link_var.get(),
            expand_source=self.expand_source_var.get(),
            expand_strategy=self.expand_strategy_var.get(),
            expand_top_k=int(self.expand_topk_var.get()),
            expand_max=int(self.expand_max_var.get()),
        )

        self._cancel.clear()
        self._set_running(True)
        self._clear_results()
        # Claim the run's output folder and settings up front: clustering can be
        # asked for as soon as terms exist, and it needs both.
        self._out_dir = out_dir
        self._cfg = cfg
        self._log(f"Starting pipeline → {out_dir}")
        self.status_var.set("Starting…")
        self._worker = threading.Thread(
            target=self._run_worker,
            args=(pubmed, pmids, refs, cfg, anchors, out_dir),
            daemon=True,
        )
        self._worker.start()

    def _run_worker(self, pubmed, pmids, refs, cfg, anchors, out_dir) -> None:
        try:
            result = run_pipeline(
                pubmed_query=pubmed, pmids=pmids, refs=refs, cfg=cfg,
                anchors=anchors, out_dir=out_dir,
                cancel=self._cancel, progress=self._emit_progress,
            )
            self._queue.put(("done", result, out_dir))
        except PipelineCancelled:
            self._queue.put(("cancelled",))
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            self._queue.put(("error", exc, traceback.format_exc()))

    # ------------------------------------------------------------- cluster --
    def _on_cluster(self) -> None:
        if (self._worker and self._worker.is_alive()) or not self._result:
            return
        terms = [t.term for t in self._result.ranked_terms]
        if not terms:
            messagebox.showinfo("No terms", "Run the pipeline first to get ranked terms.")
            return
        # Carry the run's settings (embed model, batch size, seed, ...) over and
        # only override the cluster count from the spinbox.
        cfg = replace(self._cfg or Config(), n_clusters=int(self.nclusters_var.get()))
        self._set_running(True)
        self.status_var.set("Embedding & clustering… (first run downloads the model)")
        self.clusters_tree.delete(*self.clusters_tree.get_children())
        self._log(f"Clustering {len(terms)} terms into up to "
                  f"{cfg.n_clusters} groups with PubMedBERT…")
        self._worker = threading.Thread(
            target=self._cluster_worker, args=(terms, cfg), daemon=True)
        self._worker.start()

    def _cluster_worker(self, terms, cfg) -> None:
        try:
            clusters = cluster_terms(terms, cfg, progress=self._emit_progress)
            # Render the 2D scatter here (off the UI thread) so a slow reduction
            # doesn't freeze the window.
            scatter = None
            out_dir = self._out_dir
            if clusters and out_dir:
                try:
                    self._emit_progress("Rendering term-cluster scatter…")
                    scatter = write_cluster_scatter(
                        clusters, os.path.join(out_dir, "term_clusters.html"), cfg)
                except Exception as exc:  # noqa: BLE001 - scatter is best-effort
                    self._emit_progress(f"  could not render cluster scatter: {exc}")
            self._queue.put(("clusters", clusters, scatter))
        except Exception as exc:  # noqa: BLE001 - surface to the UI (e.g. missing extra)
            self._queue.put(("error", exc, traceback.format_exc()))

    def _emit_progress(self, msg: str) -> None:
        """Thread-safe progress sink: hand the message to the Tk event loop."""
        self._queue.put(("log", msg))

    def _on_progress(self, msg: str) -> None:
        """Render a streamed progress message (runs on the Tk thread)."""
        self._log(msg)
        # Mirror the latest line in the status bar, unless a Stop is pending.
        if self.status_var.get() != "Stopping…":
            self.status_var.set(msg.strip()[:140] or "Working…")

    def _on_clusters_done(self, clusters: list[TermCluster], scatter=None) -> None:
        self._set_running(False)
        self.status_var.set("Done.")
        self._log(f"Built {len(clusters)} clusters.")
        for clu in sorted(clusters, key=lambda c: len(c.terms), reverse=True):
            members = sorted(clu.terms)
            parent = self.clusters_tree.insert(
                "", "end",
                text=f"#{clu.cluster_id} · {clu.centroid_term}",
                values=(len(members),), open=False)
            for term in members:
                self.clusters_tree.insert(parent, "end", text=term, values=("",))
        self._persist_clusters(clusters)
        if scatter and os.path.exists(scatter):
            if self._result is not None:
                self._result.outputs["cluster_scatter"] = scatter
            self._log(f"  cluster plot   -> {scatter}")
        self._refresh_outputs()

    def _persist_clusters(self, clusters: list[TermCluster]) -> None:
        """Write term_clusters.csv and recolor the co-occurrence graph by cluster."""
        out_dir = self._out_dir
        if not (clusters and out_dir and self._result):
            return
        try:
            csv_path = os.path.join(out_dir, "term_clusters.csv")
            clusters_df(clusters).to_csv(csv_path, index=False)
            self._result.outputs["clusters"] = csv_path
            self._log(f"  clusters       -> {csv_path}")

            graph_html = os.path.join(out_dir, "cooccurrence.html")
            groups = term_to_cluster(clusters)
            path = write_graph_html(self._result.graph, graph_html, groups=groups)
            self._result.outputs["graph"] = path
            self._log(f"  graph (colored)-> {path}")
            path_3d = write_graph_html_3d(
                self._result.graph, os.path.join(out_dir, "cooccurrence_3d.html"),
                groups=groups)
            if path_3d:
                self._result.outputs["graph_3d"] = path_3d
                self._log(f"  graph 3d       -> {path_3d}")
        except Exception as exc:  # noqa: BLE001 - persistence is best-effort
            self._log(f"  could not persist clusters: {exc}")

    # ----------------------------------------------------------- queue poll --
    def _poll_queue(self) -> None:
        """Drain the worker queue onto the Tk thread, then reschedule.

        Rescheduling is in a `finally` on purpose. An exception escaping here
        would stop the poll being queued again, and the whole UI is driven by
        this loop: the log would stop updating and the buttons would stay stuck
        in their running state, with no error shown anywhere. A failure in one
        handler has to surface as an error and leave the window alive.
        """
        try:
            while True:
                try:
                    kind, *payload = self._queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    if kind == "done":
                        self._on_done(*payload)
                    elif kind == "clusters":
                        self._on_clusters_done(*payload)
                    elif kind == "log":
                        self._on_progress(*payload)
                    elif kind == "cancelled":
                        self._on_cancelled()
                    elif kind == "error":
                        self._on_error(*payload)
                except Exception as exc:  # noqa: BLE001 - never kill the poll
                    try:
                        self._on_error(exc, traceback.format_exc())
                    except Exception:  # noqa: BLE001 - reporting must not either
                        traceback.print_exc()
        finally:
            self.root.after(120, self._poll_queue)

    def _on_done(self, result: PipelineResult, out_dir: str) -> None:
        self._result = result
        self._out_dir = out_dir
        self._set_running(False)
        self.status_var.set("Done.")
        self._log(result.summary())
        for label, path in result.outputs.items():
            self._log(f"  {label:14s} -> {path}")
        n_exp = sum(1 for d in result.documents if d.meta.get("expanded"))
        expansion_requested = (int(self.expand_var.get()) > 0
                               or self.expand_strategy_var.get() == "relevance")
        if n_exp:
            n_fwd = sum(1 for d in result.documents
                        if d.meta.get("expand_phase") == "forward")
            n_bwd = sum(1 for d in result.documents
                        if d.meta.get("expand_phase") == "backward")
            detail = f" ({n_fwd} forward, {n_bwd} backward)" if (n_fwd or n_bwd) else ""
            self._log(f"  {n_exp} documents added via citation expansion{detail}")
        elif expansion_requested:
            self._log("  citation expansion added 0 documents — seeds may lack "
                      "PMIDs or have no linked records for this source/direction "
                      "(try Source: icite, or Follow: both)")

        for t in sorted(result.ranked_terms, key=lambda r: r.score, reverse=True):
            self.terms_tree.insert(
                "", "end",
                values=(t.term, f"{t.score:.4g}", t.corpus_count, t.doc_freq))
        for c in result.candidates:
            self.cand_tree.insert(
                "", "end",
                values=(c.a, c.c, f"{c.score:.4g}", c.direct_cooccurrence,
                        ", ".join(c.shared_b)))

        self._refresh_outputs()

    def _on_stop(self) -> None:
        if not (self._worker and self._worker.is_alive()):
            return
        self._cancel.set()
        self.stop_btn.configure(state="disabled")
        self.status_var.set("Stopping…")
        self._log("Stop requested — finishing the current step, then halting "
                  "(an in-flight fetch can't be interrupted mid-call)…")

    def _on_cancelled(self) -> None:
        self._set_running(False)
        self.status_var.set("Stopped.")
        self._log("Pipeline stopped before completion. No results written.")

    def _on_error(self, exc: Exception, tb: str) -> None:
        self._set_running(False)
        self.status_var.set("Error.")
        self._log(f"ERROR: {exc}\n{tb}")
        messagebox.showerror("Pipeline failed", str(exc))

    # ------------------------------------------------------------- helpers --
    def _open_path(self, path: str | None) -> None:
        """Hand a file or folder to the OS's default handler.

        webbrowser.open is right for the HTML graphs but turns a CSV into a
        browser download and a folder into a directory listing, so go through
        the platform opener first and keep the browser as the fallback.
        """
        if not path or not os.path.exists(path):
            return
        target = os.path.abspath(path)
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", target])
            elif os.name == "nt":
                os.startfile(target)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", target])
        except Exception:  # noqa: BLE001 - any opener failure falls back
            webbrowser.open(f"file://{target}")

    def _open_out(self) -> None:
        self._open_path(self._out_dir)

    def _set_running(self, running: bool) -> None:
        self.run_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")
        if running:
            self.cluster_btn.configure(state="disabled")
            self.progress.start(12)
        else:
            self.progress.stop()
            has_terms = bool(self._result and self._result.ranked_terms)
            self.cluster_btn.configure(state="normal" if has_terms else "disabled")

    def _clear_results(self) -> None:
        self._result = None
        self._out_dir = None
        for tree in (self.terms_tree, self.cand_tree, self.clusters_tree):
            tree.delete(*tree.get_children())
        self._refresh_outputs()

    def _log(self, msg: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")


def main() -> int:
    root = tk.Tk()
    BioleadsGUI(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
