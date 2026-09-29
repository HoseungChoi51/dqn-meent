"""Bounded primary-literature retrieval for the research evidence library.

arXiv and Crossref metadata are retrieved through fixed HTTPS origins. Source
ingestion accepts DOI/arXiv identifiers, and extracts publisher citation metadata
on a short primary-source allowlist. Retrieval verifies what was actually read,
not the truth of a paper or a proposed algorithm's performance.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from html import unescape
from html.parser import HTMLParser
import json
import re
import threading
import time
from urllib.parse import quote, unquote, urlparse
import xml.etree.ElementTree as ET

import httpx


MAX_BYTES = 2_000_000
ALLOWED_HOSTS = frozenset({"export.arxiv.org", "api.crossref.org", "arxiv.org", "proceedings.mlr.press",
                           "www.nature.com", "nature.com", "openreview.net", "pmc.ncbi.nlm.nih.gov",
                           "github.com", "raw.githubusercontent.com"})
_REQUEST_LOCK = threading.Lock()
_LAST_ARXIV_REQUEST = 0.0


class EvidenceError(ValueError):
    pass


def _clean(value: str, limit: int = 12_000) -> str:
    return " ".join(unescape(re.sub(r"<[^>]*>", " ", value or "")).split())[:limit]


def _safe_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError as exc:
        raise EvidenceError("Malformed source URL") from exc
    if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or parsed.username or
            parsed.password or port not in {None, 443}):
        raise EvidenceError("Source retrieval accepts only HTTPS links on the primary-source allowlist or DOI/arXiv identifiers.")
    return url


def _get(url: str, params: dict | None = None) -> bytes:
    global _LAST_ARXIV_REQUEST
    _safe_url(url)
    # Follow arXiv's documented courtesy interval. The research thread may wait;
    # cancellation and numerical worker control use the independent service.
    if urlparse(url).hostname == "export.arxiv.org":
        with _REQUEST_LOCK:
            delay = max(0, 3.05 - (time.monotonic() - _LAST_ARXIV_REQUEST))
            if delay:
                time.sleep(delay)
            _LAST_ARXIV_REQUEST = time.monotonic()
    try:
        with httpx.Client(timeout=httpx.Timeout(20, connect=5), follow_redirects=False) as client:
            with client.stream("GET", url, params=params) as response:
                response.raise_for_status()
                if 300 <= response.status_code < 400:
                    raise EvidenceError("Publisher redirected the request; redirect retrieval is disabled.")
                parts, count = [], 0
                for chunk in response.iter_bytes():
                    count += len(chunk)
                    if count > MAX_BYTES:
                        raise EvidenceError("Source exceeds the bounded retrieval size")
                    parts.append(chunk)
                return b"".join(parts)
    except httpx.HTTPError as exc:
        raise EvidenceError(f"Metadata retrieval failed ({type(exc).__name__}); no paper verification is claimed.") from exc


def _record(title: str, url: str, excerpt: str = "", **extra) -> dict:
    return {"id": "source_" + sha256(url.encode()).hexdigest()[:16], "title": _clean(title, 500),
            "url": url, "excerpt": _clean(excerpt), "verification": "metadata_retrieved",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "supports": "Bibliographic metadata and supplied abstract only; algorithm applicability and scientific claims require review.",
            **extra}


def _arxiv(payload: bytes) -> list[dict]:
    if b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
        raise EvidenceError("Unexpected XML document declarations")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise EvidenceError("arXiv returned invalid metadata") from exc
    ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    sources = []
    for entry in root.findall("atom:entry", ns):
        identifier = entry.findtext("atom:id", default="", namespaces=ns)
        parsed = urlparse(identifier)
        if parsed.hostname != "arxiv.org" or not parsed.path.startswith("/abs/"):
            continue
        sources.append(_record(entry.findtext("atom:title", default="", namespaces=ns),
                               "https://arxiv.org" + parsed.path,
                               entry.findtext("atom:summary", default="", namespaces=ns),
                               authors=[_clean(node.text or "") for node in entry.findall("atom:author/atom:name", ns)],
                               published=entry.findtext("atom:published", default=None, namespaces=ns),
                               doi=entry.findtext("arxiv:doi", default=None, namespaces=ns),
                               provider="arxiv", evidence_type="preprint metadata and abstract"))
    return sources


def _crossref(item: dict) -> dict | None:
    doi = item.get("DOI", "")
    if not re.fullmatch(r"10\.\d{4,9}/\S+", doi):
        return None
    title = item.get("title", [])
    return _record(title[0] if isinstance(title, list) and title else str(title),
                   "https://doi.org/" + quote(doi, safe="/"), item.get("abstract", ""), doi=doi,
                   authors=[_clean(" ".join([a.get("given", ""), a.get("family", "")])) for a in item.get("author", [])],
                   publisher=item.get("publisher"), published=item.get("published", {}).get("date-parts"),
                   provider="crossref", evidence_type="publisher-deposited bibliographic metadata",
                   full_text_links=[link["URL"] for link in item.get("link", []) if isinstance(link.get("URL"), str)][:4],
                   abstract_available=bool(item.get("abstract")))


def search_literature(query: str, limit: int = 5, provider: str = "arxiv") -> dict:
    """Search real metadata; failures remain explicit and never return seed cards."""
    query = str(query).strip()
    if not query or len(query) > 1000:
        raise EvidenceError("A literature query must contain 1–1000 characters")
    if not 1 <= limit <= 10:
        raise EvidenceError("Request between 1 and 10 sources")
    if provider not in {"arxiv", "crossref"}:
        raise EvidenceError("Supported metadata providers are arxiv and crossref")
    if provider == "arxiv":
        # Quote ordinary user terms to keep the query's meaning predictable.
        terms = re.findall(r"[\w.-]+", query)
        expression = " AND ".join(f'all:"{term}"' for term in terms[:30])
        raw = _get("https://export.arxiv.org/api/query", {"search_query": expression, "start": 0, "max_results": limit})
        sources = _arxiv(raw)[:limit]
    else:
        raw = _get("https://api.crossref.org/works", {"query.bibliographic": query, "rows": limit})
        try:
            sources = [source for item in json.loads(raw)["message"]["items"] if (source := _crossref(item))]
        except (KeyError, TypeError, ValueError) as exc:
            raise EvidenceError("Crossref returned invalid metadata") from exc
    return {"query": query, "provider": provider, "sources": sources, "status": "completed",
            "claim_level": "retrieved_metadata; relevance and scientific claims are not verified"}


class _Metadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.metadata = {}
        self.in_title = False
        self.title = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "meta":
            name = values.get("name", values.get("property", "")).lower()
            if name and values.get("content"):
                self.metadata.setdefault(name, []).append(values["content"])
        if tag == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)


def ingest_source(identifier: str) -> dict:
    """Resolve DOI/arXiv or primary publisher HTML into a provenance record."""
    identifier = str(identifier).strip()
    if not identifier or len(identifier) > 2000:
        raise EvidenceError("Provide a DOI, arXiv identifier, or a bounded primary-source URL")
    parsed = urlparse(identifier)
    doi = None
    arxiv_id = None
    if identifier.startswith("10."):
        doi = identifier
    elif identifier.lower().startswith("arxiv:"):
        arxiv_id = identifier.split(":", 1)[1]
    elif parsed.hostname in {"doi.org", "dx.doi.org"} and parsed.scheme == "https" and not parsed.username and not parsed.password:
        doi = unquote(parsed.path.lstrip("/"))
    elif parsed.hostname == "arxiv.org" and parsed.path.startswith(("/abs/", "/pdf/")):
        _safe_url(identifier)
        arxiv_id = parsed.path.split("/", 2)[2].removesuffix(".pdf")
    elif parsed.hostname in {"www.nature.com", "nature.com"} and parsed.path.startswith("/articles/"):
        _safe_url(identifier)
        doi = "10.1038/" + parsed.path.split("/", 2)[2]
    if arxiv_id is not None:
        if not re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-zA-Z.-]+/\d{7})(?:v\d+)?", arxiv_id):
            raise EvidenceError("Invalid arXiv identifier")
        try:
            sources = _arxiv(_get("https://export.arxiv.org/api/query", {"id_list": arxiv_id, "max_results": 1}))
        except EvidenceError as exc:
            # The public abstract page remains useful when the metadata API is
            # unavailable. This is still actual metadata retrieval, not a seed.
            url = "https://arxiv.org/abs/" + arxiv_id
            parser = _Metadata()
            parser.feed(_get(url).decode("utf-8", errors="replace"))
            title = (parser.metadata.get("citation_title") or [None])[0]
            if not title:
                raise EvidenceError("arXiv API and public metadata page were unavailable") from exc
            return _record(title, url, authors=parser.metadata.get("citation_author", []),
                provider="arxiv_html", evidence_type="arXiv public-page citation metadata",
                retrieval_limitations=[str(exc)], full_text_links=parser.metadata.get("citation_pdf_url", []))
        if not sources:
            raise EvidenceError("No arXiv metadata found for that identifier")
        return sources[0]
    if doi is not None:
        if not re.fullmatch(r"10\.\d{4,9}/\S+", doi):
            raise EvidenceError("Invalid DOI")
        try:
            data = json.loads(_get("https://api.crossref.org/works/" + quote(doi, safe="")))
            source = _crossref(data["message"])
        except (TypeError, KeyError, json.JSONDecodeError) as exc:
            raise EvidenceError("Crossref returned invalid DOI metadata") from exc
        if source is None:
            raise EvidenceError("No bibliographic metadata found for that DOI")
        return source
    _safe_url(identifier)
    if parsed.path.lower().endswith(".pdf"):
        return _record(unquote(parsed.path.rsplit("/", 1)[-1]), identifier, provider="primary_pdf_identifier",
            verification="identifier_only", evidence_type="document URL; bibliography not retrieved",
            supports="This is a supplied primary-document URL, not verified bibliographic metadata. Read the captured document before citing its method.")
    parser = _Metadata()
    parser.feed(_get(identifier).decode("utf-8", errors="replace"))
    meta = parser.metadata
    title = (meta.get("citation_title") or meta.get("dc.title") or meta.get("og:title") or [" ".join(parser.title)])[0]
    if not title:
        raise EvidenceError("The primary source did not expose recognizable bibliographic metadata")
    abstract = (meta.get("citation_abstract") or meta.get("description") or meta.get("og:description") or [""])[0]
    return _record(title, identifier, abstract, authors=meta.get("citation_author", []),
                   full_text_links=meta.get("citation_pdf_url", [])[:4],
                   doi=(meta.get("citation_doi") or [None])[0], provider="publisher_html",
                   evidence_type="publisher HTML citation metadata", verification="publisher_metadata_retrieved")
