"""Input sources: local PDFs and PubMed records (by query or explicit IDs).

PubMed records carry title + abstract by default; with `fulltext=True`,
open-access PubMed Central articles are upgraded to full body text and the rest
fall back to the abstract. All sources are normalized to a list of `Document`
objects so the rest of the pipeline is source-agnostic.
"""
from __future__ import annotations

import glob
import os
import re
import warnings
from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class Document:
    """A single literature record."""
    doc_id: str
    text: str
    title: str = ""
    source: str = ""               # "pdf" | "pubmed" | ...
    meta: dict = field(default_factory=dict)

    @property
    def content(self) -> str:
        """Title + body, used for entity extraction."""
        return f"{self.title}. {self.text}" if self.title else self.text


# --------------------------------------------------------------------------- #
# Cooperative cancellation
# --------------------------------------------------------------------------- #
# Python threads can't be force-killed, so a Stop button is cooperative: the
# caller passes a `threading.Event` (or anything with `.is_set()`) and the long
# loops below check it at safe boundaries (between network batches / rounds) and
# bail out by raising PipelineCancelled. Granularity is per-checkpoint — a single
# in-flight efetch or model download can't be interrupted mid-call.
class PipelineCancelled(Exception):
    """Raised at a checkpoint when the caller's cancel flag is set."""


def _check_cancel(cancel) -> None:
    """Raise PipelineCancelled if the cancel flag (if any) has been set."""
    if cancel is not None and cancel.is_set():
        raise PipelineCancelled("cancelled by user")


def _sayer(progress):
    """Normalize an optional progress callback into a callable taking one str."""
    return progress if callable(progress) else (lambda _msg: None)


# --------------------------------------------------------------------------- #
# Local PDFs
# --------------------------------------------------------------------------- #
def load_pdfs(path: str, recursive: bool = True) -> list[Document]:
    """Load every PDF under `path` (a file or directory) into Documents.

    Requires the `pdf` extra: pip install "bioleads[pdf]"
    """
    try:
        import fitz  # PyMuPDF
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            'PDF support needs PyMuPDF. Install with: pip install "bioleads[pdf]"'
        ) from e

    paths: list[str]
    if os.path.isdir(path):
        pattern = "**/*.pdf" if recursive else "*.pdf"
        paths = sorted(glob.glob(os.path.join(path, pattern), recursive=recursive))
    else:
        paths = [path]

    docs: list[Document] = []
    for p in paths:
        try:
            with fitz.open(p) as pdf:
                pages = [pg.get_text("text") for pg in pdf]
                title = (pdf.metadata or {}).get("title") or _stem(p)
            text = _clean_pdf_text("\n".join(pages))
        except Exception as exc:  # noqa: BLE001 - skip unreadable PDFs, keep going
            print(f"[bioleads] skipping {p}: {exc}")
            continue
        docs.append(
            Document(doc_id=_stem(p), text=text, title=title, source="pdf",
                     meta={"path": os.path.abspath(p)})
        )
    return docs


def _stem(p: str) -> str:
    return os.path.splitext(os.path.basename(p))[0]


def _clean_pdf_text(text: str) -> str:
    """De-hyphenate line breaks and collapse whitespace from PDF extraction."""
    text = re.sub(r"-\n(\w)", r"\1", text)      # join hyphenated line wraps
    text = re.sub(r"\s*\n\s*", " ", text)        # newlines -> spaces
    text = re.sub(r"\s{2,}", " ", text)          # collapse runs of spaces
    return text.strip()


# --------------------------------------------------------------------------- #
# PubMed / Europe PMC
# --------------------------------------------------------------------------- #
DEFAULT_ENTREZ_EMAIL = "disom.biophysics@gmail.com"


def fetch_pubmed(
    query: str,
    retmax: int = 500,
    email: str = DEFAULT_ENTREZ_EMAIL,
    api_key: str | None = None,
    fulltext: bool = False,
    cancel=None,
    progress=None,
) -> list[Document]:
    """Fetch records for a PubMed query via Entrez (Biopython).

    By default each Document holds title + abstract. With `fulltext=True`,
    open-access articles in PubMed Central are upgraded to their full body
    text (intro/methods/results); the rest fall back to the abstract.

    Requires the `pubmed` extra: pip install "bioleads[pubmed]"
    """
    Entrez, _ = _entrez(email, api_key)
    say = _sayer(progress)

    # esearch -> PMIDs
    say(f"PubMed search: {query!r}…")
    with Entrez.esearch(db="pubmed", term=query, retmax=retmax) as h:
        pmids = Entrez.read(h)["IdList"]
    say(f"  search matched {len(pmids)} record(s)")
    if not pmids:
        return []
    return fetch_pubmed_by_ids(pmids, email=email, api_key=api_key,
                               fulltext=fulltext, cancel=cancel, progress=progress)


def fetch_pubmed_by_ids(
    pmids: Iterable[str],
    email: str = DEFAULT_ENTREZ_EMAIL,
    api_key: str | None = None,
    fulltext: bool = False,
    cancel=None,
    progress=None,
) -> list[Document]:
    """Fetch records for an explicit list of PubMed IDs via Entrez efetch.

    Order is preserved as given and empty/duplicate IDs should be removed by
    the caller (see `parse_pmid_input`). With `fulltext=True`, open-access PMC
    articles are upgraded to full body text, others fall back to the abstract.

    Requires the `pubmed` extra: pip install "bioleads[pubmed]"
    """
    Entrez, Medline = _entrez(email, api_key)

    ids = [str(p).strip() for p in pmids if str(p).strip()]
    if not ids:
        return []
    say = _sayer(progress)

    # efetch -> MEDLINE records (batched to be gentle on NCBI)
    total = len(ids)
    docs: list[Document] = []
    for start, batch in zip(range(0, total, 200), _chunks(ids, 200)):
        _check_cancel(cancel)
        say(f"  fetching PubMed records {start + 1}–{start + len(batch)} of {total}"
            + (" (with PMC full text)" if fulltext else "") + "…")
        with Entrez.efetch(
            db="pubmed", id=",".join(batch), rettype="medline", retmode="text"
        ) as h:
            records = list(Medline.parse(h))
        docs += _build_pubmed_docs(records, fulltext=fulltext,
                                   email=email, api_key=api_key)
    say(f"  retrieved {len(docs)} document(s) with usable text")
    return docs


def parse_pmid_input(value: str | Iterable[str] | None) -> list[str]:
    """Normalize a PMID specification into a de-duplicated ordered list.

    Accepts a list/tuple of IDs, a comma/whitespace-separated string, or a path
    to a file of IDs (optionally prefixed with '@'). 'PMID:' prefixes are
    stripped. Returns [] for empty input.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        raw = " ".join(str(v) for v in value)
    else:
        raw = str(value)
        candidate = raw[1:] if raw.startswith("@") else raw
        if os.path.isfile(candidate):
            with open(candidate, encoding="utf-8") as f:
                raw = f.read()

    out: list[str] = []
    seen: set[str] = set()
    for tok in re.split(r"[\s,;]+", raw.strip()):
        tok = re.sub(r"(?i)^pmid:?", "", tok).strip()
        if tok and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def _entrez(email: str, api_key: str | None):
    try:
        from Bio import Entrez, Medline
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            'PubMed support needs Biopython. Install with: pip install "bioleads[pubmed]"'
        ) from e
    Entrez.email = email
    if api_key:
        Entrez.api_key = api_key
    return Entrez, Medline


def _record_to_document(rec: dict) -> Document:
    pmid = rec.get("PMID", "")
    return Document(
        doc_id=f"PMID:{pmid}",
        text=rec.get("AB", ""),
        title=rec.get("TI", ""),
        source="pubmed",
        meta={
            "journal": rec.get("JT", ""),
            "year": rec.get("DP", "")[:4],
            "mesh": rec.get("MH", []),
            "pmc": rec.get("PMC", ""),
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        },
    )


def _build_pubmed_docs(
    records: list[dict], *, fulltext: bool, email: str, api_key: str | None
) -> list[Document]:
    docs: list[Document] = []
    for rec in records:
        doc = _record_to_document(rec)
        if fulltext and doc.meta.get("pmc"):
            body = _fetch_pmc_body(doc.meta["pmc"], email=email, api_key=api_key)
            if body:
                doc.text = body
                doc.meta["fulltext"] = True
        if not doc.text:  # no abstract and no retrievable full text
            continue
        docs.append(doc)
    return docs


def _fetch_pmc_body(
    pmc_id: str, *, email: str, api_key: str | None = None
) -> str | None:
    """Fetch and flatten the JATS <body> of an open-access PMC article.

    Returns None when the article isn't in PMC open-access, has no body, or the
    request fails — callers fall back to the abstract.
    """
    from xml.etree import ElementTree as ET

    numeric = re.sub(r"[^0-9]", "", str(pmc_id))
    if not numeric:
        return None
    Entrez, _ = _entrez(email, api_key)
    try:
        with Entrez.efetch(db="pmc", id=numeric, retmode="xml") as h:
            tree = ET.parse(h)
    except Exception as exc:  # noqa: BLE001 - non-OA / network errors -> fall back
        print(f"[bioleads] PMC full text unavailable for PMC{numeric}: {exc}")
        return None

    body = tree.getroot().find(".//body")
    if body is None:
        return None
    text = _clean_pdf_text(" ".join(body.itertext()))
    return text or None


def _chunks(seq: list, n: int) -> Iterable[list]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


# --------------------------------------------------------------------------- #
# Citation expansion (snowballing) via NCBI ELink
# --------------------------------------------------------------------------- #
# Backward = the references a paper cites; forward = papers that cite it. Both
# come from NCBI's link database (populated largely via PMC), so coverage is
# partial — many references simply aren't linked.
# Each choice maps to the NCBI ELink linknames it follows. "both" unions the
# two directions (deduped downstream); backward references only resolve when the
# seed is in PMC, so "both" adds them opportunistically and is otherwise == cited_by.
_LINKNAMES = {
    "references": ["pubmed_pubmed_refs"],    # papers each seed cites (backward)
    "cited_by": ["pubmed_pubmed_citedin"],   # papers that cite each seed (forward)
    "both": ["pubmed_pubmed_refs", "pubmed_pubmed_citedin"],
}

# The NIH iCite backend ("icite") returns the same directions from the NIH Open
# Citation Collection (PMC + Crossref + MEDLINE), so its backward-reference
# coverage is far broader than NCBI's PMC-only ELink. It speaks PMIDs natively,
# needs no API key, and slots into the same BFS.
_ICITE_FIELDS = {
    "references": ["references"],
    "cited_by": ["cited_by"],
    "both": ["references", "cited_by"],
}
# "all" (default) unions both backends for the broadest recall and degrades
# gracefully if one is unavailable; "ncbi" / "icite" force a single backend.
_EXPAND_SOURCES = ("all", "ncbi", "icite")
_ICITE_URL = "https://icite.od.nih.gov/api/pubs"


def expand_pmids(
    seeds: Iterable[str],
    *,
    rounds: int = 1,
    link: str = "references",
    source: str = "all",
    max_records: int = 1000,
    email: str = DEFAULT_ENTREZ_EMAIL,
    api_key: str | None = None,
    cancel=None,
    progress=None,
) -> list[str]:
    """Iteratively grow a PMID set by following citation links.

    Starting from `seeds`, each round adds the linked PMIDs (cited references by
    default) of the current frontier, then chases those in the next round, until
    `rounds` is reached or `max_records` total is hit. Returns the seeds plus all
    newly discovered PMIDs (seeds first, discovery order preserved).

    `source` selects the citation backend(s):
      - "all"   — union of NCBI ELink and NIH iCite (default; broadest recall).
                  Best-effort: if one backend is unavailable the other still runs.
      - "ncbi"  — Entrez ELink only (PMC-derived).
      - "icite" — NIH iCite / Open Citation Collection only (broad backward
                  coverage, works regardless of PMC).
    """
    if link not in _LINKNAMES:
        raise ValueError(
            f"unknown expansion link {link!r}; choose from {sorted(_LINKNAMES)}")
    if source not in _EXPAND_SOURCES:
        raise ValueError(
            f"unknown expansion source {source!r}; choose from {sorted(_EXPAND_SOURCES)}")
    seed_ids = [str(s).strip() for s in seeds if str(s).strip()]
    if not seed_ids or rounds <= 0:
        return list(dict.fromkeys(seed_ids))[:max_records]

    use_ncbi = source in ("all", "ncbi")
    use_icite = source in ("all", "icite")
    tolerant = source == "all"  # don't let one backend's failure sink the other
    warned: set[str] = set()

    def _attempt(backend: str, fn):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            if not tolerant:
                raise
            if backend not in warned:
                warnings.warn(
                    f"citation source {backend!r} unavailable ({exc}); "
                    "continuing with the other source")
                warned.add(backend)
            return []

    Entrez = None
    if use_ncbi:
        try:
            Entrez, _ = _entrez(email, api_key)
        except Exception as exc:  # biopython missing, etc.
            if not tolerant:
                raise
            warnings.warn(f"citation source 'ncbi' unavailable ({exc}); using iCite only")
            use_ncbi = False
    linknames = _LINKNAMES[link]
    fields = _ICITE_FIELDS[link]

    def neighbors(frontier: list[str]) -> list[str]:
        out: list[str] = []
        for batch in _chunks(list(frontier), 200):
            _check_cancel(cancel)
            if use_ncbi:
                out += _attempt(
                    "ncbi",
                    lambda b=batch: [n for ln in linknames
                                     for n in _elink_neighbors(Entrez, b, ln)])
            if use_icite:
                out += _attempt("icite", lambda b=batch: _icite_neighbors(b, fields))
        return out

    return _expand_bfs(seed_ids, neighbors, rounds=rounds,
                       max_records=max_records, progress=progress)


def _icite_neighbors(ids: list[str], fields: list[str], *, timeout: int = 30) -> list[str]:
    """Fetch citation links for `ids` from NIH iCite. `fields` ⊆ {references, cited_by}."""
    try:
        import requests
    except ImportError as e:  # pragma: no cover
        raise ImportError(
            'iCite expansion needs requests. Install with: pip install "bioleads[pubmed]"'
        ) from e
    resp = requests.get(
        _ICITE_URL,
        params={"pmids": ",".join(ids), "fl": "pmid,references,cited_by"},
        timeout=timeout,
    )
    resp.raise_for_status()
    out: list[str] = []
    for rec in resp.json().get("data", []):
        for fld in fields:
            val = rec.get(fld) or []
            if isinstance(val, str):  # iCite has returned space-joined strings historically
                val = val.split()
            out += [str(x).strip() for x in val if str(x).strip()]
    return out


def _expand_bfs(
    seeds: list[str], neighbors_fn, *, rounds: int, max_records: int, progress=None
) -> list[str]:
    """Breadth-first PMID expansion. Pure logic — `neighbors_fn` does any I/O."""
    say = _sayer(progress)
    all_ids = list(dict.fromkeys(seeds))
    seen = set(all_ids)
    frontier = list(all_ids)
    for rnd in range(max(0, rounds)):
        if not frontier or len(all_ids) >= max_records:
            break
        say(f"  citation round {rnd + 1}/{rounds}: following links from "
            f"{len(frontier)} PMID(s)…")
        nxt: list[str] = []
        for nid in neighbors_fn(frontier):
            nid = str(nid).strip()
            if nid and nid not in seen:
                seen.add(nid)
                all_ids.append(nid)
                nxt.append(nid)
                if len(all_ids) >= max_records:
                    break
        say(f"    round {rnd + 1}: +{len(nxt)} new PMID(s) ({len(all_ids)} total)")
        frontier = nxt
    return all_ids[:max_records]


def _elink_neighbors(Entrez, ids: list[str], linkname: str) -> list[str]:
    with Entrez.elink(dbfrom="pubmed", db="pubmed",
                      id=",".join(ids), linkname=linkname) as h:
        result = Entrez.read(h)
    out: list[str] = []
    for linkset in result:
        for ldb in linkset.get("LinkSetDb", []):
            if ldb.get("LinkName") == linkname:
                out += [lnk["Id"] for lnk in ldb.get("Link", [])]
    return out


def _seed_pmids(docs: list[Document]) -> list[str]:
    """Collect PMIDs already present on loaded documents (for expansion seeds)."""
    out: list[str] = []
    for d in docs:
        pmid = d.meta.get("pmid", "")
        if not pmid and d.doc_id.startswith("PMID:"):
            pmid = d.doc_id.split("PMID:", 1)[1]
        if pmid:
            out.append(pmid)
    return list(dict.fromkeys(out))


# --------------------------------------------------------------------------- #
# Reference-manager exports: RIS and EndNote XML
# --------------------------------------------------------------------------- #
def load_refs(
    path: str,
    *,
    fulltext: bool = False,
    email: str = DEFAULT_ENTREZ_EMAIL,
    api_key: str | None = None,
) -> list[Document]:
    """Load a reference-manager export (RIS or EndNote XML) into Documents.

    Format is auto-detected from content (falling back to file extension). Each
    record's title + abstract become the Document text. PMIDs are harvested into
    `meta["pmid"]`; with `fulltext=True`, records whose PMID is open-access in
    PMC are upgraded to full body text (others keep their file abstract).
    """
    with open(path, "rb") as f:
        data = f.read()
    text = data.decode("utf-8", errors="replace")

    fmt = _detect_ref_format(path, text)
    if fmt == "endnote-xml":
        records = _parse_endnote_xml(data)
    elif fmt == "ris":
        records = _parse_ris(text)
    else:
        raise ValueError(
            f"Could not recognize {path} as RIS or EndNote XML. "
            "Export from your reference manager as RIS (.ris) or EndNote XML (.xml)."
        )

    docs: list[Document] = []
    for i, r in enumerate(records):
        title = r.get("title", "").strip()
        abstract = r.get("abstract", "").strip()
        if not (title or abstract):
            continue
        pmid = r.get("pmid", "")
        meta = {k: v for k, v in (
            ("pmid", pmid), ("doi", r.get("doi", "")), ("url", r.get("url", ""))
        ) if v}
        docs.append(
            Document(
                doc_id=f"PMID:{pmid}" if pmid else f"ref:{i}",
                text=abstract, title=title, source=fmt, meta=meta,
            )
        )

    if fulltext:
        _upgrade_refs_fulltext(docs, email=email, api_key=api_key)
    return docs


def _detect_ref_format(path: str, text: str) -> str:
    head = text.lstrip()[:512].lower()
    if head.startswith("<?xml") or "<records>" in head or "<xml>" in head:
        return "endnote-xml"
    if re.search(r"(?m)^TY {1,2}-\s", text):  # RIS type tag opens every record
        return "ris"
    ext = os.path.splitext(path)[1].lower()
    if ext == ".xml":
        return "endnote-xml"
    if ext in (".ris", ".txt"):
        return "ris"
    return "unknown"


def _parse_ris(text: str) -> list[dict]:
    """Parse RIS into raw {tag: [values]} records, then normalize fields."""
    records: list[dict] = []
    cur: dict | None = None
    last: str | None = None
    for raw in text.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9]) {1,2}-\s?(.*)$", raw)
        if m:
            tag, val = m.group(1), m.group(2).rstrip()
            if tag == "TY" or cur is None:
                cur = {}
                records.append(cur)
            if tag == "ER":
                cur, last = None, None
                continue
            cur.setdefault(tag, []).append(val)
            last = tag
        elif cur is not None and last and raw.strip():
            # continuation of the previous tag (e.g. a wrapped abstract)
            cur[last][-1] = (cur[last][-1] + " " + raw.strip()).rstrip()
    return [_normalize_ris(r) for r in records if r]


def _normalize_ris(r: dict) -> dict:
    def first(*tags: str) -> str:
        for t in tags:
            if r.get(t):
                return r[t][0].strip()
        return ""

    def joined(*tags: str) -> str:
        for t in tags:
            if r.get(t):
                return " ".join(x.strip() for x in r[t]).strip()
        return ""

    an = first("AN")
    return {
        "title": first("TI", "T1"),
        "abstract": joined("AB", "N2"),
        "pmid": an if an.isdigit() else "",
        "doi": first("DO"),
        "url": first("UR"),
    }


def _parse_endnote_xml(data: bytes) -> list[dict]:
    from xml.etree import ElementTree as ET

    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError(f"Malformed EndNote XML: {exc}") from exc

    out: list[dict] = []
    for rec in root.iter("record"):
        acc = _xml_text(rec.find("./accession-num"))
        out.append({
            "title": _xml_text(rec.find("./titles/title")),
            "abstract": _xml_text(rec.find("./abstract")),
            "pmid": acc if acc.isdigit() else "",
            "doi": _xml_text(rec.find("./electronic-resource-num")),
            "url": _xml_text(rec.find("./urls/related-urls/url")),
        })
    return out


def _xml_text(el) -> str:
    if el is None:
        return ""
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def _upgrade_refs_fulltext(
    docs: list[Document], *, email: str, api_key: str | None
) -> None:
    """In place: replace abstracts with PMC full text for PMIDs that have it."""
    pmids = [d.meta["pmid"] for d in docs if d.meta.get("pmid")]
    if not pmids:
        return
    pmc_map = _pmid_to_pmcid(pmids, email=email, api_key=api_key)
    for d in docs:
        pmcid = pmc_map.get(d.meta.get("pmid", ""))
        if not pmcid:
            continue
        body = _fetch_pmc_body(pmcid, email=email, api_key=api_key)
        if body:
            d.text = body
            d.meta["fulltext"] = True
            d.meta["pmcid"] = pmcid


def _pmid_to_pmcid(
    pmids: Iterable[str], *, email: str, api_key: str | None
) -> dict[str, str]:
    Entrez, Medline = _entrez(email, api_key)
    out: dict[str, str] = {}
    for batch in _chunks(list(pmids), 200):
        with Entrez.efetch(
            db="pubmed", id=",".join(batch), rettype="medline", retmode="text"
        ) as h:
            for rec in Medline.parse(h):
                pmid, pmc = rec.get("PMID", ""), rec.get("PMC", "")
                if pmid and pmc:
                    out[pmid] = pmc
    return out


# --------------------------------------------------------------------------- #
# Convenience: build documents from text directly (and dispatcher)
# --------------------------------------------------------------------------- #
def documents_from_texts(texts: Iterable[str], prefix: str = "doc") -> list[Document]:
    """Wrap raw strings as Documents — handy for testing and notebooks."""
    return [Document(doc_id=f"{prefix}{i}", text=t, source="text")
            for i, t in enumerate(texts)]


def load_documents(
    *,
    pdf_path: str | None = None,
    pubmed_query: str | None = None,
    pmids: str | Iterable[str] | None = None,
    refs: str | None = None,
    texts: Iterable[str] | None = None,
    fulltext: bool = False,
    expand_rounds: int = 0,
    expand_link: str = "references",
    expand_source: str = "ncbi",
    expand_max: int = 1000,
    cancel=None,
    progress=None,
    **pubmed_kwargs,
) -> list[Document]:
    """Unified loader. Combine any subset of sources into one document list.

    With `expand_rounds > 0`, the PMID-bearing seed documents are grown by
    following citation links (cited references by default) and the newly
    discovered PubMed records are fetched and appended.
    """
    say = _sayer(progress)
    docs: list[Document] = []
    if pdf_path:
        say(f"Reading PDFs from {pdf_path}…")
        loaded = load_pdfs(pdf_path)
        say(f"  loaded {len(loaded)} PDF document(s)")
        docs += loaded
    if pubmed_query:
        _check_cancel(cancel)
        docs += fetch_pubmed(pubmed_query, fulltext=fulltext, cancel=cancel,
                             progress=progress, **pubmed_kwargs)
    if pmids:
        ids = parse_pmid_input(pmids)
        if ids:
            _check_cancel(cancel)
            say(f"Fetching {len(ids)} PubMed record(s) by ID…")
            docs += fetch_pubmed_by_ids(
                ids,
                email=pubmed_kwargs.get("email", DEFAULT_ENTREZ_EMAIL),
                api_key=pubmed_kwargs.get("api_key"),
                fulltext=fulltext,
                cancel=cancel,
                progress=progress,
            )
    if refs:
        say(f"Parsing reference-manager export {refs}…")
        loaded = load_refs(
            refs,
            fulltext=fulltext,
            email=pubmed_kwargs.get("email", DEFAULT_ENTREZ_EMAIL),
            api_key=pubmed_kwargs.get("api_key"),
        )
        say(f"  parsed {len(loaded)} reference(s)")
        docs += loaded
    if texts:
        docs += documents_from_texts(texts)

    if expand_rounds and expand_rounds > 0:
        _check_cancel(cancel)
        email = pubmed_kwargs.get("email", DEFAULT_ENTREZ_EMAIL)
        api_key = pubmed_kwargs.get("api_key")
        seeds = _seed_pmids(docs)
        if seeds:
            say(f"Citation expansion: {expand_rounds} round(s) following "
                f"{expand_link} via {expand_source} from {len(seeds)} seed PMID(s)…")
            expanded = expand_pmids(
                seeds, rounds=expand_rounds, link=expand_link,
                source=expand_source,
                max_records=expand_max, email=email, api_key=api_key,
                cancel=cancel, progress=progress,
            )
            have = {d.doc_id for d in docs}
            new_ids = [i for i in expanded if f"PMID:{i}" not in have]
            if new_ids:
                say(f"  fetching {len(new_ids)} newly discovered record(s)…")
                added = fetch_pubmed_by_ids(
                    new_ids, email=email, api_key=api_key, fulltext=fulltext,
                    cancel=cancel, progress=progress)
                for d in added:
                    d.meta["expanded"] = True
                docs += added
            else:
                say("  no new records found to add")
        else:
            say("Citation expansion requested, but no PMID-bearing seeds to expand.")
    return docs
