"""Give every run its own folder, and a manifest saying what produced it.

Before this, `--out` was written to directly with ``exist_ok=True``, so a second
run silently overwrote the first. Two runs with different settings left one set
of files on disk and no way to tell which settings made them.

A run now lands in ``<root>/<timestamp>_<slug>/`` with a ``run.json`` recording
the version, the inputs, the resolved config and what came out. ``<root>/latest``
points at the newest one.
"""
from __future__ import annotations

import dataclasses
import json
import os
import platform
import re
import sys
from datetime import datetime, timezone

MANIFEST_NAME = "run.json"
LATEST_NAME = "latest"

# Config fields whose values must never reach a manifest on disk. Matched on the
# field NAME, so a key added to Config later is redacted without touching this
# module, as long as it is named like a credential.
_SECRET_RE = re.compile(r"(key|token|secret|password|passwd|credential)", re.I)
REDACTED = "<redacted>"


def slugify(text: str | None, max_len: int = 40) -> str:
    """A short, filename-safe tag for a run. Empty string when there is nothing useful."""
    if not text:
        return ""
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    if len(s) > max_len:
        # Trim at a word boundary so the tag stays readable.
        s = s[:max_len].rsplit("-", 1)[0] or s[:max_len]
    return s.strip("-")


def describe_inputs(pubmed_query=None, pmids=None, refs=None, texts=None) -> str:
    """Pick the most descriptive slug the inputs offer."""
    if pubmed_query:
        return slugify(pubmed_query)
    if pmids:
        n = len(pmids) if isinstance(pmids, (list, tuple, set)) else 1
        return f"pmids-{n}" if isinstance(pmids, (list, tuple, set)) else "pmids"
    if refs:
        return slugify(os.path.splitext(os.path.basename(str(refs)))[0])
    if texts:
        return "texts"
    return ""


def run_dir_name(when: datetime, slug: str = "") -> str:
    """`2026-09-29_143205_tmem184c` — sorts chronologically as plain text."""
    stamp = when.strftime("%Y-%m-%d_%H%M%S")
    return f"{stamp}_{slug}" if slug else stamp


def new_run_dir(root: str, *, slug: str = "", when: datetime | None = None) -> str:
    """Create and return a fresh directory under `root`. Never reuses one.

    Two runs started in the same second get `-2`, `-3` and so on, rather than
    one overwriting the other, which is the whole point of this module.
    """
    when = when or datetime.now()
    base = run_dir_name(when, slug)
    os.makedirs(root, exist_ok=True)
    candidate, n = base, 1
    while True:
        path = os.path.join(root, candidate)
        try:
            os.mkdir(path)
            return path
        except FileExistsError:
            n += 1
            candidate = f"{base}-{n}"


def update_latest(root: str, run_dir: str) -> str | None:
    """Point `<root>/latest` at this run. Best effort, never fatal.

    Symlinks need a developer mode or elevation on Windows, and can fail on some
    network shares, so a failure here must not lose a run that already succeeded.
    """
    link = os.path.join(root, LATEST_NAME)
    try:
        if os.path.islink(link) or os.path.exists(link):
            if os.path.islink(link):
                os.unlink(link)
            else:
                return None          # a real file or directory: leave it alone
        os.symlink(os.path.basename(run_dir), link)
        return link
    except OSError:
        return None


def _jsonable(v):
    """Coerce the few Config values JSON cannot take. `stopwords` is a set."""
    if isinstance(v, (set, frozenset)):
        return sorted(v)
    if isinstance(v, tuple):
        return list(v)
    return v


def config_to_dict(cfg) -> dict:
    """Config as plain data, with anything credential-shaped redacted."""
    if cfg is None:
        return {}
    raw = dataclasses.asdict(cfg) if dataclasses.is_dataclass(cfg) else dict(vars(cfg))
    out = {}
    for k, v in raw.items():
        if _SECRET_RE.search(k) and v not in (None, "", [], {}, set()):
            out[k] = REDACTED
        else:
            out[k] = _jsonable(v)
    return out


def build_manifest(*, version, started, finished, run_dir, cfg,
                   pubmed_query=None, pmids=None, refs=None, texts=None,
                   anchors=None, result=None, outputs=None) -> dict:
    """Everything needed to say what this folder is and what made it."""
    def _count(x):
        if x is None:
            return None
        if isinstance(x, (list, tuple, set)):
            return len(x)
        return 1

    man = {
        "bioleads_version": version,
        "started_utc": started.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "finished_utc": finished.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "started_local": started.isoformat(timespec="seconds"),
        "run_dir": os.path.basename(run_dir),
        "inputs": {
            "pubmed_query": pubmed_query,
            "pmids": _count(pmids),
            "refs": str(refs) if refs else None,
            "texts": _count(texts),
            "anchors": list(anchors) if anchors else [],
        },
        "config": config_to_dict(cfg),
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
    }
    if result is not None:
        graph = getattr(result, "graph", None)
        man["results"] = {
            "documents": len(getattr(result, "documents", []) or []),
            "ranked_terms": len(getattr(result, "ranked_terms", []) or []),
            "candidates": len(getattr(result, "candidates", []) or []),
            "clusters": len(getattr(result, "clusters", []) or []),
            "graph_nodes": graph.number_of_nodes() if graph is not None else None,
            "graph_edges": graph.number_of_edges() if graph is not None else None,
        }
    # Relative names, so the folder can be moved or sent to someone without the
    # manifest pointing at a path that only existed on this machine.
    if outputs:
        man["outputs"] = {k: os.path.basename(v) for k, v in outputs.items()}
    return man


def write_manifest(run_dir: str, manifest: dict) -> str:
    path = os.path.join(run_dir, MANIFEST_NAME)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=False, default=str)
        fh.write("\n")
    return path
