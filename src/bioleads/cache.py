"""On-disk cache for the records bioleads fetches from NIH iCite.

Without it every run re-fetches the whole corpus, which is the reason the
citation networks are behind a checkbox at all: they cost a live round-trip.
With it the first run on a corpus pays, and every run after it is free —
including runs with no network at all.

Entries are one JSON file per PMID rather than one per request, so batches
compose: a corpus that overlaps a previous one pays only for the papers it
adds, and expanding a corpus re-reads everything it already had.

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
    os.path.expanduser("~"), ".cache", "bioleads", "icite")

# iCite's global citation_count grows continuously, so an entry that never
# expired would quietly freeze the "cited across PubMed" half of the ranking at
# whatever it was the day you first looked. References and cited_by move far
# more slowly; a month is short enough that the counts stay honest and long
# enough that a working session never re-fetches.
DEFAULT_TTL_DAYS = 30


class RecordCache:
    """Per-PMID JSON store with an age limit.

    `get` returns the record, `{}` for a PMID iCite has no data for (cached so
    it is not re-requested every run), or None when there is nothing usable and
    the caller should fetch.
    """

    def __init__(self, path: str | None = None, ttl_days: int = DEFAULT_TTL_DAYS):
        self.path = path or DEFAULT_DIR
        self.ttl = ttl_days * 86400 if ttl_days and ttl_days > 0 else None
        self.hits = 0
        self.misses = 0
        self.stale = 0
        self.writes = 0

    def _file(self, pmid: str) -> str:
        # Hashed rather than named by PMID so the filename is a fixed length and
        # a stray value can never escape the directory.
        key = hashlib.sha1(f"icite:{pmid}".encode()).hexdigest()
        return os.path.join(self.path, key + ".json")

    def get(self, pmid: str):
        try:
            with open(self._file(pmid), encoding="utf-8") as fh:
                entry = json.load(fh)
        except (OSError, ValueError):
            self.misses += 1
            return None
        if self.ttl is not None and time.time() - entry.get("fetched", 0) > self.ttl:
            self.stale += 1
            self.misses += 1
            return None
        self.hits += 1
        return entry.get("record") or {}

    def put(self, pmid: str, record: dict) -> None:
        """Store `record` for `pmid`. A miss is stored as {} so it stays a miss."""
        try:
            os.makedirs(self.path, exist_ok=True)
            final = self._file(pmid)
            tmp = f"{final}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"fetched": time.time(), "record": record or {}}, fh)
            os.replace(tmp, final)      # never leave a half-written entry
            self.writes += 1
        except OSError:
            pass                        # a cache miss next time is the worst case

    def summary(self) -> str:
        """One line for the run log, or "" when there was nothing to say."""
        if not (self.hits or self.misses):
            return ""
        note = f"  iCite cache: {self.hits} hit(s), {self.misses} to fetch"
        if self.stale:
            note += f" ({self.stale} expired)"
        return note + "."
