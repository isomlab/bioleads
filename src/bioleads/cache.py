"""On-disk cache for the citation data bioleads fetches over the network.

Without it every run re-fetches the whole corpus, which is the reason the
citation networks are behind a checkbox at all: they cost a live round-trip.
With it the first run on a corpus pays, and every run after it is free —
including runs with no network at all.

Two kinds of thing live here, keyed differently because they arrive
differently. iCite *records* are keyed per PMID, so batches compose: a corpus
overlapping a previous one pays only for the papers it adds. Expansion *link
lookups* are keyed by the whole request, because both backends answer a batch
with one flat list and do not say which paper each link came from — so the
request is the smallest thing that can be replayed faithfully.

Everything here is best-effort. A cache that cannot be read or written is a
slow run, never a failed one, so every filesystem error degrades to a miss.
"""
from __future__ import annotations

import hashlib
import json
import os
import time

# Under ~/.cache rather than beside the code: it survives a re-clone, it is
# shared across every checkout, and it is somewhere a user already knows to
# clear. The benchmark keeps its own store under bioleads-benchmark, which is
# deliberately separate -- it is pinned for reproducibility and never expires.
DEFAULT_DIR = os.path.join(
    os.path.expanduser("~"), ".cache", "bioleads", "citations")

# iCite's global citation_count grows continuously, so an entry that never
# expired would quietly freeze the "cited across PubMed" half of the ranking at
# whatever it was the day you first looked. The same applies to forward
# expansion: a paper's reference list is fixed once published, but the list of
# papers citing it is not. A month is short enough that both stay honest and
# long enough that a working session never re-fetches.
DEFAULT_TTL_DAYS = 30


class JsonCache:
    """Keyed JSON store with an age limit.

    `get` returns the stored value — which may legitimately be empty, as `{}`
    for a PMID iCite has no data for or `[]` for a batch with no links, both
    cached so they are not re-requested every run — or None when there is
    nothing usable and the caller should fetch.
    """

    def __init__(self, path: str | None = None, ttl_days: int = DEFAULT_TTL_DAYS):
        self.path = path or DEFAULT_DIR
        self.ttl = ttl_days * 86400 if ttl_days and ttl_days > 0 else None
        self.hits = 0
        self.misses = 0
        self.stale = 0
        self.writes = 0

    def _file(self, key: str) -> str:
        # Hashed rather than named by the key so the filename is a fixed length
        # and a stray value can never escape the directory.
        return os.path.join(
            self.path, hashlib.sha1(key.encode()).hexdigest() + ".json")

    def get(self, key: str):
        try:
            with open(self._file(key), encoding="utf-8") as fh:
                entry = json.load(fh)
        except (OSError, ValueError):
            self.misses += 1
            return None
        if self.ttl is not None and time.time() - entry.get("fetched", 0) > self.ttl:
            self.stale += 1
            self.misses += 1
            return None
        self.hits += 1
        return entry.get("value")

    def put(self, key: str, value) -> None:
        """Store `value`. An empty result is stored as such, so it stays empty."""
        try:
            os.makedirs(self.path, exist_ok=True)
            final = self._file(key)
            tmp = f"{final}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"fetched": time.time(), "value": value}, fh)
            os.replace(tmp, final)      # never leave a half-written entry
            self.writes += 1
        except OSError:
            pass                        # a cache miss next time is the worst case

    def summary(self, noun: str = "record") -> str:
        """One line for the run log, or "" when there was nothing to say."""
        if not (self.hits or self.misses):
            return ""
        note = (f"  citation cache: {self.hits} {noun}(s) reused, "
                f"{self.misses} to fetch")
        if self.stale:
            note += f" ({self.stale} expired)"
        return note + "."
