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
    term = SimpleNamespace(term="cftr", score=1.0, corpus_count=3,
                           doc_freq=2, bg_count=1)
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
