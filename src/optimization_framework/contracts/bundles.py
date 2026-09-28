"""Portable evidence envelopes; importing a record does not activate its producer."""
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import Field, model_validator

from .base import Contract, content_hash


class RecordKey(Contract):
    owner: Literal["workspace", "library"]
    source_id: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=120)
    id: str = Field(min_length=1, max_length=300)
    content_digest: str = Field(pattern=r"^[a-f0-9]{64}$")

    @property
    def key(self):
        return content_hash(self.model_dump(mode="json"))


class BundleRecord(Contract):
    reference: RecordKey
    data: dict[str, Any]

    @model_validator(mode="after")
    def identity(self):
        if self.reference.id != self.data.get("id") or self.reference.content_digest != content_hash(self.data):
            raise ValueError("Bundle record identity or content digest mismatch")
        if "content_hash" in self.data and self.data["content_hash"] != content_hash({
                key: value for key, value in self.data.items() if key != "content_hash"}):
            raise ValueError("Bundle contains a modified immutable record")
        return self


class BundleBlob(Contract):
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    bytes: int = Field(ge=0, le=64 * 1024**3)
    media_type: str = Field(default="application/octet-stream", max_length=200)


class CapturedFiles(Contract):
    record_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    purpose: Literal["experiment", "compiler", "executable"]
    files: dict[str, str]  # relative file name -> blob sha256

    @model_validator(mode="after")
    def paths(self):
        for name, checksum in self.files.items():
            path = PurePosixPath(name)
            if (not name or name != str(path) or path.is_absolute() or ".." in path.parts
                    or "\\" in name or "\x00" in name or len(name) > 1000
                    or len(checksum) != 64 or any(char not in "0123456789abcdef" for char in checksum)):
                raise ValueError("Captured files require safe relative names and content digests")
        return self


class BundleEdge(Contract):
    source: str = Field(pattern=r"^[a-f0-9]{64}$")
    dependency: str = Field(pattern=r"^[a-f0-9]{64}$")
    role: str = Field(min_length=1, max_length=120)


class MissingEvidence(Contract):
    owner: Literal["workspace", "library"]
    kind: str
    id: str
    reason: str = Field(min_length=1, max_length=2000)
    record_key: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class EvidenceBundle(Contract):
    format: Literal["optimization_evidence_v1"] = "optimization_evidence_v1"
    roots: list[str] = Field(min_length=1, max_length=256)
    records: list[RecordKey] = Field(max_length=100000)
    blobs: list[BundleBlob] = Field(default_factory=list, max_length=100000)
    captures: list[CapturedFiles] = Field(default_factory=list, max_length=10000)
    edges: list[BundleEdge] = Field(default_factory=list, max_length=500000)
    missing: list[MissingEvidence] = Field(default_factory=list, max_length=100000)

    @model_validator(mode="after")
    def graph(self):
        keys = {record.key for record in self.records}
        blobs = {blob.sha256 for blob in self.blobs}
        if len(keys) != len(self.records) or len(blobs) != len(self.blobs):
            raise ValueError("Bundle contains duplicate record or blob identities")
        if not set(self.roots) <= keys or len(set(self.roots)) != len(self.roots):
            raise ValueError("Bundle roots do not identify distinct included records")
        if any(edge.source not in keys or edge.dependency not in keys for edge in self.edges):
            raise ValueError("Bundle dependency graph refers to missing records")
        if any(item.record_key is not None and item.record_key not in keys for item in self.missing):
            raise ValueError("Missing evidence refers to an undeclared owner record")
        for capture in self.captures:
            if capture.record_key not in keys or not set(capture.files.values()) <= blobs:
                raise ValueError("Captured files refer to missing records or blobs")
        if len({(capture.record_key, capture.purpose) for capture in self.captures}) != len(self.captures):
            raise ValueError("Bundle contains conflicting file captures")
        return self

    @property
    def digest(self):
        return content_hash(self.model_dump(mode="json"))


class BundleExportInput(Contract):
    asset_ids: list[str] = Field(min_length=1, max_length=256)


class BundleInspectInput(Contract):
    upload_id: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")


class BundlePublishInput(Contract):
    inspection_id: str
