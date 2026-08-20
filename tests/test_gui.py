"""Regression tests for the Tkinter GUI's run-scoped state.

These need a real Tk display, so they skip anywhere one can't be created
(headless CI, a machine without Tk). The window is withdrawn immediately so
running the suite never steals focus.
"""
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

tk = pytest.importorskip("tkinter")

from bioleads.config import Config


@pytest.fixture()
def app():
    from bioleads import gui as gui_mod

    try:
        root = tk.Tk()
    except tk.TclError as exc:  # no display available
        pytest.skip(f"no Tk display: {exc}")
    root.withdraw()
    try:
        yield gui_mod.BioleadsGUI(root)
    finally:
        root.destroy()


def _finished_run():
    """Stand-in for a PipelineResult that produced one ranked term."""
    term = SimpleNamespace(term="cftr", score=1.0, corpus_count=3, doc_freq=2)
    return SimpleNamespace(ranked_terms=[term], candidates=[], outputs={},
                           graph=None, documents=[])


def test_run_state_starts_empty(app):
    assert app._result is None
    assert app._out_dir is None
    assert app._cfg is None


def test_clear_results_drops_the_previous_run(app):
    app._result = _finished_run()
    app._out_dir = "/tmp/old_run"
    app._set_running(False)
    assert str(app.cluster_btn["state"]) == "normal"

    app._clear_results()

    assert app._result is None
    assert app._out_dir is None
    # A run that then fails or is stopped must not leave "Cluster terms" live
    # off the previous run's terms (which would also write into its out_dir).
    app._set_running(False)
    assert str(app.cluster_btn["state"]) == "disabled"


def test_cluster_inherits_the_runs_config(app, monkeypatch):
    from bioleads import gui as gui_mod

    seen = {}

    def spy(terms, cfg=None, embeddings=None, progress=None):
        seen["cfg"] = cfg
        return []

    monkeypatch.setattr(gui_mod, "cluster_terms", spy)

    run_cfg = Config(n_clusters=4, embed_batch_size=8, seed=7,
                     embed_model="my/custom-model")
    app._result = _finished_run()
    app._cfg = run_cfg
    app._out_dir = None  # keep the best-effort scatter write out of this test
    app.nclusters_var.set(25)

    app._on_cluster()
    app._worker.join(timeout=60)

    cfg = seen["cfg"]
    assert cfg is not None, "cluster_terms was never called"
    # The spinbox overrides only the cluster count...
    assert cfg.n_clusters == 25
    # ...everything else comes from the run that produced the terms.
    assert cfg.embed_model == "my/custom-model"
    assert cfg.embed_batch_size == 8
    assert cfg.seed == 7
    # and the run's own Config is left untouched.
    assert run_cfg.n_clusters == 4


# --------------------------------------------------------------- Outputs tab --

def _labels(app):
    from tkinter import ttk
    return [c.cget("text") for c in app.outputs_frame.winfo_children()
            if isinstance(c, ttk.Label)]


def _rows(app):
    from tkinter import ttk
    return [c for c in app.outputs_frame.winfo_children()
            if isinstance(c, ttk.Frame)]


@pytest.fixture()
def run_with_outputs(app, tmp_path):
    """A finished run whose outputs are real (empty) files on disk."""
    def _make(**names):
        outs = {}
        for key, filename in names.items():
            f = tmp_path / filename
            f.write_text("")
            outs[key] = str(f)
        app._result = _finished_run()
        app._result.outputs = outs
        app._out_dir = str(tmp_path)
        app._refresh_outputs()
        return outs
    return _make


def test_outputs_tab_is_empty_before_a_run(app):
    assert "No outputs yet" in _labels(app)[0]


def test_outputs_tab_hides_groups_the_run_didnt_produce(run_with_outputs, app):
    run_with_outputs(graph="cooccurrence.html", ranked_terms="ranked_terms.csv")
    labels = _labels(app)
    assert "Term co-occurrence network" in labels
    assert "Tables" in labels
    # Nothing clustered and no citation network this run.
    assert "Term clusters" not in labels
    assert "Paper citation network" not in labels


def test_outputs_tab_lists_keys_it_doesnt_know_about(run_with_outputs, app):
    run_with_outputs(graph="cooccurrence.html", some_new_output="new.html")
    assert "Other outputs" in _labels(app)


def test_missing_sibling_is_listed_but_disabled(run_with_outputs, app):
    from tkinter import ttk
    # 2D produced, 3D not (e.g. Plotly missing) — the row stays, greyed.
    run_with_outputs(graph="cooccurrence.html")
    greyed = [r for r in _rows(app)
              if any(isinstance(k, ttk.Label) and "not produced" in k.cget("text")
                     for k in r.winfo_children())]
    assert len(greyed) == 1
    btn = next(k for k in greyed[0].winfo_children() if isinstance(k, ttk.Button))
    assert str(btn.cget("state")) == "disabled"


def test_clearing_a_run_empties_the_outputs_tab(run_with_outputs, app):
    run_with_outputs(graph="cooccurrence.html")
    assert "No outputs yet" not in _labels(app)[0]
    app._clear_results()
    assert "No outputs yet" in _labels(app)[0]


def test_display_path_is_home_relative_and_elided(app):
    import os
    home_file = os.path.join(os.path.expanduser("~"), "bioleads_out", "x.html")
    assert app._display_path(home_file) == "~/bioleads_out/x.html"
    long_name = os.path.join(os.path.expanduser("~"), "a" * 200 + ".html")
    shown = app._display_path(long_name)
    assert shown.startswith("~/") and shown.endswith(".html") and len(shown) <= 72


# ------------------------------------------------- hover help vs. the docs --

def test_tooltips_are_help_text_not_stage_labels(app, monkeypatch):
    """Hover help explains the control; it no longer cites pipeline stages.

    Every tooltip used to open with "Stage N · <section>.", mirroring
    how_it_works.md. That made the help read as documentation cross-references
    rather than as an answer to "what does this box do", so the labels were
    removed — and the numbering is no longer a thing the GUI can get wrong.
    """
    import re

    from bioleads import gui as gui_mod

    captured: list[str] = []
    original = gui_mod.BioleadsGUI._add_tooltip

    def spy(self, widget, text):
        captured.append(text)
        return original(self, widget, text)

    monkeypatch.setattr(gui_mod.BioleadsGUI, "_add_tooltip", spy)
    gui_mod.BioleadsGUI(app.root)

    assert captured, "no tooltips were attached"
    assert all(t and t.strip() for t in captured), "an empty tooltip was attached"
    stagey = [t for t in captured if re.search(r"[Ss]tages? \d", t)]
    assert not stagey, f"tooltips still cite stage numbers: {stagey}"
    assert all(t[0].isupper() for t in captured), (
        "a tooltip lost its opening capital when its stage label was stripped")


def test_a_failing_handler_does_not_kill_the_event_pump(app, monkeypatch):
    """The whole UI is driven by _poll_queue. If a handler raises and the poll is
    never rescheduled, the log freezes and the buttons stay stuck with nothing
    shown — which is indistinguishable from the app hanging."""
    from tkinter import messagebox

    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: None)

    boom = RuntimeError("handler blew up")

    def explode(*_a, **_k):
        raise boom

    monkeypatch.setattr(app, "_on_clusters_done", explode)

    scheduled: list = []
    real_after = app.root.after
    monkeypatch.setattr(app.root, "after",
                        lambda ms, fn=None, *a: (scheduled.append(fn), 0)[1]
                        if fn is not None else real_after(ms))

    app._queue.put(("clusters", [], None))
    app._poll_queue()

    assert scheduled and scheduled[-1] == app._poll_queue, \
        "the poll must reschedule itself even when a handler raises"
    log = app.log.get("1.0", "end")
    assert "handler blew up" in log, f"the failure must be surfaced: {log[-300:]}"
    assert str(app.run_btn["state"]) == "normal", "the UI must not stay stuck running"


def test_the_pump_keeps_draining_after_one_bad_message(app, monkeypatch):
    from tkinter import messagebox

    monkeypatch.setattr(messagebox, "showerror", lambda *a, **k: None)
    monkeypatch.setattr(app, "_on_clusters_done",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("bad")))
    monkeypatch.setattr(app.root, "after", lambda *a, **k: 0)

    app._queue.put(("clusters", [], None))     # this one fails
    app._queue.put(("log", "still alive"))     # this one must still be handled
    app._poll_queue()

    assert "still alive" in app.log.get("1.0", "end")
