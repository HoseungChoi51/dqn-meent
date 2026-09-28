"""Captured primary-source text and stable, citable passages.

Metadata search remains in evidence.py. Acquisition does not establish a
scientific claim: receipts state exactly which extracted passages were supplied.
"""
from __future__ import annotations

from hashlib import sha256
from html.parser import HTMLParser
import json
import os
import re
import subprocess
import sys
import tempfile
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

from optimization_framework.contracts.base import content_hash
from optimization_framework.storage.sqlite import now
from .evidence import EvidenceError, _safe_url


EXTRACTOR = "primary-text-v1"
MAX_BYTES = 24 * 1024**2
MAX_TEXT = 600_000


def fetch_document(url):
    """Every redirect is independently checked against the primary-source list."""
    with httpx.Client(timeout=httpx.Timeout(25, connect=5), follow_redirects=False) as client:
        try:
            for _ in range(4):
                _safe_url(url)
                with client.stream("GET", url) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers.get("location", ""))
                        continue
                    response.raise_for_status()
                    data, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise EvidenceError("Full text exceeds the 24 MiB retrieval limit")
                        data.append(chunk)
                    return b"".join(data), url, response.headers.get("content-type", "").split(";", 1)[0]
            raise EvidenceError("Full-text redirect limit reached")
        except httpx.HTTPError as exc:
            raise EvidenceError(f"Full-text retrieval failed ({type(exc).__name__}); this source was not read") from exc


class ArticleText(HTMLParser):
    """Preserve heading locations and paragraphs without browser/script execution."""
    BLOCKS = {"p", "div", "section", "article", "main", "li", "br", "tr", "h1", "h2", "h3", "h4", "pre"}
    SKIP = {"script", "style", "nav", "footer", "header", "noscript", "svg", "form"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.parts, self.blocks, self.links = [], [], [], []
        self.heading, self.heading_tag, self.size = "", None, 0
        self.article = False

    def flush(self):
        text = " ".join(" ".join(self.parts).split())
        self.parts = []
        if text:
            self.blocks.append({"section": self.heading, "text": text})

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if self.stack or tag in self.SKIP:
            if tag not in {"meta", "link", "img", "br", "input", "hr", "source", "wbr"}:
                self.stack.append(tag)
            return
        if tag in self.BLOCKS:
            self.flush()
        if tag in {"article", "main"}:
            self.article = True
        if tag in {"h1", "h2", "h3", "h4"}:
            self.heading_tag = tag
        if tag == "a" and values.get("href") and len(self.links) < 200:
            self.links.append(values["href"])

    def handle_endtag(self, tag):
        if self.stack:
            if tag in self.stack:
                self.stack = self.stack[:len(self.stack) - 1 - self.stack[::-1].index(tag)]
            return
        if tag == self.heading_tag:
            self.heading = " ".join(self.parts)[:500]
            self.heading_tag = None
        if tag in self.BLOCKS:
            self.flush()

    def handle_data(self, data):
        if not self.stack and self.size < MAX_TEXT:
            self.parts.append(data[:MAX_TEXT - self.size])
            self.size += len(data)


def extract(raw, mime, url):
    if raw.startswith(b"%PDF-"):
        try:
            result = subprocess.run([sys.executable, "-m", "optimization_framework.research.pdf_text"],
                input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=True)
            parsed = json.loads(result.stdout)
        except (subprocess.SubprocessError, ValueError) as exc:
            raise EvidenceError("PDF text extraction failed or exceeded its resource limit; no readable text is claimed") from exc
        blocks = parsed["pages"]
        limitations = ["PDF text extraction can lose equations, table structure and reading order; images are not OCRed."]
        if not any(row["text"].strip() for row in blocks):
            raise EvidenceError("PDF has no extractable text; scanned/image pages require a separate OCR source")
        if parsed["truncated"]:
            limitations.append("Extraction stopped at 100 pages or 600,000 characters.")
        return blocks, "partial_text" if parsed["truncated"] else "full_text_captured", limitations, []
    if mime not in {"text/html", "application/xhtml+xml", "", "text/plain"}:
        raise EvidenceError("Source did not return readable HTML, plain text or PDF")
    parser = ArticleText()
    parser.feed(raw.decode("utf-8", errors="replace"))
    parser.flush()
    if any(block["text"].strip().lower() == "client challenge" for block in parser.blocks[:3]):
        raise EvidenceError("Publisher returned an access challenge rather than article text; use an accessible primary copy")
    if not parser.blocks:
        raise EvidenceError("Source contained no readable text")
    links = list(dict.fromkeys(urljoin(url, link) for link in parser.links if urljoin(url, link).startswith("https://")))
    # An HTML article marker alone does not prove the publisher supplied the
    # complete paper. Keep the coverage conservative and let agents cite passages.
    limitations = ["HTML extraction captures supplied page text; completeness and paywall omissions are not independently verified."]
    if parser.size >= MAX_TEXT:
        limitations.append("Extracted text reached the 600,000-character limit.")
    return parser.blocks, "partial_text", limitations, links


def source_urls(source):
    url = source["url"]
    parsed = urlparse(url)
    if parsed.hostname == "arxiv.org" and parsed.path.startswith(("/abs/", "/pdf/", "/html/")):
        paper = parsed.path.split("/", 2)[2].removesuffix(".pdf")
        return ["https://arxiv.org/html/" + paper, "https://arxiv.org/pdf/" + paper]
    if parsed.hostname == "openreview.net" and parse_qs(parsed.query).get("id"):
        return ["https://openreview.net/pdf?id=" + parse_qs(parsed.query)["id"][0]]
    urls = list(source.get("full_text_links", []))
    if str(source.get("doi", "")).startswith("10.1038/"):
        urls.append("https://www.nature.com/articles/" + source["doi"].split("/", 1)[1])
    urls.append(url)
    return list(dict.fromkeys(urls))[:4]


class LiteratureReader:
    def __init__(self, workspace):
        self.workspace, self.store = workspace, workspace.store

    def capture(self, campaign_id, source_id):
        source = self.store.get(source_id, "source")
        if source["campaign_id"] != campaign_id:
            raise ValueError("Source belongs to another campaign")
        previous = [row for row in self.store.list("source_capture", campaign_id)
                    if row["source_id"] == source_id and row["extractor"] == EXTRACTOR]
        if previous:
            return previous[-1]
        errors = []
        for url in source_urls(source):
            try:
                raw, resolved, mime = fetch_document(url)
                blocks, coverage, limitations, links = extract(raw, mime, resolved)
                break
            except EvidenceError as exc:
                errors.append(str(exc))
        else:
            raise EvidenceError("Full text unavailable: " + "; ".join(dict.fromkeys(errors)))
        raw_hash = sha256(raw).hexdigest()
        identity = "capture_" + content_hash([campaign_id, source_id, raw_hash, EXTRACTOR])[:32]
        root = self.workspace.directory / "campaigns" / campaign_id / "sources"
        root.mkdir(parents=True, exist_ok=True)
        target = root / raw_hash
        if not target.exists():
            fd, temporary = tempfile.mkstemp(dir=root, prefix=".capture-")
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        passages = []
        for block in blocks:
            text = block["text"].strip()
            for start in range(0, len(text), 2400):
                passages.append({"id": f"{identity}_passage_{len(passages)}", "campaign_id": campaign_id,
                    "source_id": source_id, "capture_id": identity, "ordinal": len(passages),
                    "page": block.get("page"), "section": block.get("section"), "block_offset": start,
                    "text": text[start:start + 2400]})
        capture = {"id": identity, "campaign_id": campaign_id, "source_id": source_id, "url": resolved,
            "extractor": EXTRACTOR, "created_at": now(), "raw_sha256": raw_hash, "raw_bytes": len(raw),
            "text_sha256": content_hash(blocks), "mime_type": mime, "coverage": coverage,
            "limitations": limitations + (["Earlier acquisition attempts failed: " + "; ".join(errors)] if errors else []),
            "passage_ids": [row["id"] for row in passages], "reference_links": links}
        with self.workspace.lock, self.store.transaction():
            try:
                return self.store.get(identity, "source_capture")
            except KeyError:
                for passage in passages:
                    self.store.put_immutable("source_passage", passage)
                return self.store.put_immutable("source_capture", capture, "discovery.source_captured")

    def read(self, campaign_id, *, source_id=None, capture_id=None, query="", offset=0, limit=8):
        if bool(source_id) == bool(capture_id):
            raise ValueError("Select one source_id or capture_id")
        capture = self.capture(campaign_id, source_id) if source_id else self.store.get(capture_id, "source_capture")
        if capture["campaign_id"] != campaign_id:
            raise ValueError("Source capture belongs to another campaign")
        passages = [self.store.get(key, "source_passage") for key in capture["passage_ids"]]
        if query:
            terms = set(re.findall(r"\w+", query.lower()))
            passages.sort(key=lambda row: -sum(row["text"].lower().count(term) for term in terms))
        selected = passages[offset:offset + limit]
        return {"capture": capture, "passages": selected, "total_passages": len(passages),
                "next_offset": offset + len(selected) if offset + len(selected) < len(passages) else None,
                "read_coverage": "Only the returned passages were supplied by this tool; acquisition is not a claim that an agent read the entire source."}
