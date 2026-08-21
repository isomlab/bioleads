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


def test_paper_count_group_sits_under_the_citation_group():
    """The two senior-author views are siblings, and read as a pair.

    They share a shape — 2D, 3D, Ranking — so a reader can tell at a glance
    that the second measures the same authors differently, rather than being
    a stray set of files under "Other outputs".
    """
    from bioleads.gui import OUTPUT_GROUPS

    names = [g for g, _ in OUTPUT_GROUPS]
    cit = names.index("Senior-author citation network")
    papers = names.index("Senior-author paper-count network")
    assert papers == cit + 1, "the paper-count view must sit directly under it"

    rows = dict(OUTPUT_GROUPS)
    labels = [lab for lab, _ in rows["Senior-author paper-count network"]]
    assert labels == [lab for lab, _ in rows["Senior-author citation network"]]
    assert [k for _, k in rows["Senior-author paper-count network"]] == [
        "author_paper_network", "author_paper_network_3d", "author_paper_ranking"]


def test_paper_count_outputs_are_not_stray_keys(app):
    """Every author_paper_* key is claimed by a group, so none falls to "Other"."""
    from bioleads.gui import OUTPUT_GROUPS

    claimed = {k for _, rows in OUTPUT_GROUPS for _, k in rows}
    for key in ("author_paper_network", "author_paper_network_3d",
                "author_paper_ranking"):
        assert key in claimed


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


def _form_content_height(app):
    """Height the form wants, and the height the viewport actually gives it."""
    canvas = app._form_canvas
    body = canvas.nametowidget(canvas.itemcget(canvas.find_all()[0], "window"))
    return body.winfo_reqheight(), canvas.winfo_height()


# Tk reports whatever display the window is currently on, so these tests pin a
# height instead of reading it. A laptop screen and an external monitor fall on
# opposite sides of the clamp in _fit_to_form, and the guarantee differs across
# it: the form fits outright when there is room, and stays reachable by
# scrolling when there is not. Reading the ambient screen would silently test
# only whichever of the two the machine happened to be on.
TALL_SCREEN = 1600
SHORT_SCREEN = 900


def _fit_on_screen(app, screen_height):
    """Re-fit the window as if the display were `screen_height` tall."""
    app.root.winfo_screenheight = lambda: screen_height
    app._fit_to_form()
    app.root.update_idletasks()


def test_the_whole_form_is_visible_when_the_screen_has_room(app):
    """Given the room, opening the window shows every option at once.

    The window sizes itself to the measured form rather than to a number
    written here, so adding a field keeps this passing instead of silently
    pushing the last section back under the fold.
    """
    _fit_on_screen(app, TALL_SCREEN)

    wanted, viewport = _form_content_height(app)
    assert viewport >= wanted, (
        f"{wanted - viewport}px of the form is hidden below the fold")


def test_the_window_never_grows_past_the_screen(app):
    """On a display too short for the form, the window stops at the screen.

    Growing past it would push the Run button off the bottom, which is worse
    than the scroll it was trying to avoid.
    """
    from bioleads.gui import SCREEN_MARGIN

    _fit_on_screen(app, SHORT_SCREEN)

    height = app.root.winfo_height()
    assert height <= SHORT_SCREEN - SCREEN_MARGIN, (
        f"window is {height}px on a {SHORT_SCREEN}px screen")


def test_the_form_stays_reachable_when_it_cannot_all_fit(app):
    """What the fold costs on a short screen is a scroll, not a dead end.

    This is the guarantee that makes the clamp above acceptable, so it is
    asserted rather than assumed: the viewport really is too short here, and
    the scrollregion really does still span the whole form.
    """
    _fit_on_screen(app, SHORT_SCREEN)

    canvas = app._form_canvas
    wanted, viewport = _form_content_height(app)
    assert viewport < wanted, (
        "form fits on a short screen -- this test no longer covers the clamp")
    assert float(canvas.cget("scrollregion").split()[3]) >= wanted, (
        "the hidden part of the form cannot be scrolled to")


# --------------------------------------------------- strategy-linked fields --

def _states(app):
    """(Follow, top-K) widget states, as strings."""
    return (str(app._follow_field.cget("state")),
            str(app._topk_field.cget("state")))


def test_bfs_greys_out_the_control_it_ignores(app):
    """top-K is a relevance-only knob, so bfs must not offer it."""
    app.expand_strategy_var.set("bfs")

    follow, topk = _states(app)
    assert topk == "disabled", "bfs leaves the top-K it ignores editable"
    assert follow == "readonly", "bfs walks Follow, so Follow must stay live"


def test_relevance_greys_out_the_control_it_ignores(app):
    """relevance always gates both directions, so Follow can't change anything."""
    app.expand_strategy_var.set("relevance")

    follow, topk = _states(app)
    assert follow == "disabled", "relevance leaves the Follow it ignores editable"
    assert topk == "normal", "relevance is sized by top-K, which must stay live"


def test_switching_back_restores_the_dropdown_as_readonly(app):
    """Re-enabling must not turn a fixed list of choices into a free text box.

    A ttk.Combobox handed "normal" accepts typed input, which would let a
    direction the pipeline has never heard of reach the config.
    """
    app.expand_strategy_var.set("relevance")   # disables Follow
    app.expand_strategy_var.set("bfs")         # and back

    assert str(app._follow_field.cget("state")) == "readonly"


def test_the_greyed_field_takes_its_label_with_it(app):
    """A full-contrast label over a dead control reads as merely broken."""
    app.expand_strategy_var.set("bfs")

    assert str(app._topk_field._field_label.cget("style")) == "FieldOff.TLabel"
    assert str(app._follow_field._field_label.cget("style")) == "Field.TLabel"
