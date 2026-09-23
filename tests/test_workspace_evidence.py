import json

import httpx
import pytest

from dqn_meent.workspace import evidence


FEED = b'''<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry>
<id>http://arxiv.org/abs/2406.12904v1</id><title>Differentiable electromagnetic simulation</title>
<summary>We evaluate a differentiable solver.</summary><author><name>A. Researcher</name></author>
<published>2024-06-01T00:00:00Z</published></entry></feed>'''


def test_real_metadata_shape_is_parsed_with_limited_verification(monkeypatch):
    requests = []

    def get(url, params=None):
        requests.append((url, params))
        return FEED

    monkeypatch.setattr(evidence, "_get", get)
    result = evidence.search_literature("binary grating", limit=3)
    source = result["sources"][0]
    assert source["url"] == "https://arxiv.org/abs/2406.12904v1"
    assert source["authors"] == ["A. Researcher"]
    assert source["verification"] == "metadata_retrieved"
    assert "scientific claims require review" in source["supports"]
    assert requests[0][1]["search_query"] == 'all:"binary" AND all:"grating"'
    assert result["status"] == "completed"


def test_crossref_search_and_nature_doi_ingestion_use_fixed_origin(monkeypatch):
    calls = []
    item = {"DOI": "10.1038/s41586-026-10644-y", "title": ["Co-Scientist"], "publisher": "Nature",
            "abstract": "<jats:p>Hypothesis generation and review.</jats:p>", "author": [{"given": "J.", "family": "Researcher"}]}

    def get(url, params=None):
        calls.append((url, params))
        return json.dumps({"message": {"items": [item]} if params else item}).encode()

    monkeypatch.setattr(evidence, "_get", get)
    search = evidence.search_literature("Co-Scientist", provider="crossref")
    source = evidence.ingest_source("https://www.nature.com/articles/s41586-026-10644-y")
    assert source["doi"] == item["DOI"]
    assert source["excerpt"] == "Hypothesis generation and review."
    assert source["id"] == search["sources"][0]["id"]
    assert calls[1][0].startswith("https://api.crossref.org/works/")
    assert "%2F" in calls[1][0]


@pytest.mark.parametrize("url", ["http://127.0.0.1/admin", "https://localhost/private", "file:///etc/passwd",
                                  "https://proceedings.mlr.press.attacker.example/paper", "https://user:secret@arxiv.org/abs/2406.12904",
                                  "https://arxiv.org:8443/abs/2406.12904"])
def test_source_ingestion_rejects_untrusted_origins_before_request(monkeypatch, url):
    monkeypatch.setattr(evidence, "_get", lambda *_: pytest.fail("No request permitted"))
    with pytest.raises(evidence.EvidenceError):
        evidence.ingest_source(url)


def test_publisher_html_returns_citation_metadata_without_claiming_full_text(monkeypatch):
    html = b'<html><meta name="citation_title" content="Combinatorial optimization"><meta name="citation_author" content="A. Author"><meta name="description" content="A useful abstract"><body>Unretrieved claims</body></html>'
    monkeypatch.setattr(evidence, "_get", lambda *_: html)
    source = evidence.ingest_source("https://proceedings.mlr.press/v80/baptista18a.html")
    assert source["title"] == "Combinatorial optimization"
    assert source["verification"] == "publisher_metadata_retrieved"
    assert "Unretrieved claims" not in json.dumps(source)


def test_no_synthetic_fallback_on_failure_and_no_xml_entities(monkeypatch):
    monkeypatch.setattr(evidence, "_get", lambda *_: b"<!DOCTYPE feed [<!ENTITY x 'x'>]><feed/>")
    with pytest.raises(evidence.EvidenceError, match="declarations"):
        evidence.search_literature("grating")


def test_requests_do_not_follow_redirects_or_unbounded_responses(monkeypatch):
    requests = []

    def handle(request):
        requests.append(request.url)
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/private"})

    client = httpx.Client
    monkeypatch.setattr(evidence.httpx, "Client", lambda **kw: client(transport=httpx.MockTransport(handle), **kw))
    with pytest.raises(evidence.EvidenceError):
        evidence._get("https://proceedings.mlr.press/v80/baptista18a.html")
    assert len(requests) == 1
    monkeypatch.setattr(evidence.httpx, "Client", lambda **kw: client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * 11)), **kw))
    monkeypatch.setattr(evidence, "MAX_BYTES", 10)
    with pytest.raises(evidence.EvidenceError, match="size"):
        evidence._get("https://proceedings.mlr.press/v80/baptista18a.html")
