"""Content-addressed blobs and atomic checkpoint manifests.

Blob writes complete and are fsynced before publishing their manifest. Readers
verify hashes. Locations of external historical blobs are local resolver data,
never scientific identity; a portable export materializes them.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
from typing import BinaryIO

from optimization_framework.contracts.base import canonical_json, content_hash
from optimization_framework.contracts.experiments import ArtifactReference


def sync_directory(path: Path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical_json(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        sync_directory(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


def file_digest(path: Path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(data)
    return h.hexdigest()


class LocalArtifactStore:
    def __init__(self, directory: Path | str):
        self.directory = Path(directory).resolve()
        self.blobs = self.directory / "blobs"
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.external = self.directory / "external"
        self.external.mkdir(exist_ok=True)

    def _path(self, digest):
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid artifact digest")
        return self.blobs / digest[:2] / digest[2:]

    def put_stream(self, source: BinaryIO, *, media_type="application/octet-stream"):
        fd, name = tempfile.mkstemp(dir=self.blobs, prefix="pending-")
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(fd, "wb") as destination:
                for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
                    digest.update(chunk)
                    size += len(chunk)
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
            checksum = digest.hexdigest()
            target = self._path(checksum)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(name, target)
            sync_directory(target.parent)
            return ArtifactReference(id="sha256:" + checksum, sha256=checksum, bytes=size, media_type=media_type)
        finally:
            Path(name).unlink(missing_ok=True)

    def put_bytes(self, data: bytes, *, media_type="application/octet-stream"):
        return self.put_stream(io.BytesIO(data), media_type=media_type)

    def register_external(self, path: Path | str, expected_hash: str | None = None, *, media_type="application/octet-stream"):
        path = Path(path).resolve()
        digest = file_digest(path)
        if expected_hash is not None and digest != expected_hash:
            raise ValueError("External artifact differs from its import manifest")
        reference = ArtifactReference(id="sha256:" + digest, sha256=digest, bytes=path.stat().st_size,
                                      media_type=media_type, availability="external")
        atomic_json(self.external / (digest + ".json"), {"path": str(path), "reference": reference.model_dump()})
        return reference

    def resolve(self, reference: ArtifactReference):
        if reference.id != "sha256:" + reference.sha256:
            raise ValueError("Artifact identifier disagrees with its content hash")
        path = self._path(reference.sha256)
        if not path.is_file():
            location = self.external / (reference.sha256 + ".json")
            if not location.is_file():
                raise FileNotFoundError(f"Artifact {reference.id} is unavailable")
            path = Path(json.loads(location.read_text())["path"])
        return path

    def verify(self, reference: ArtifactReference):
        path = self.resolve(reference)
        if path.stat().st_size != reference.bytes or file_digest(path) != reference.sha256:
            raise ValueError(f"Artifact failed integrity verification: {reference.id}")

    def open(self, reference: ArtifactReference):
        self.verify(reference)
        return self.resolve(reference).open("rb")

    def publish_checkpoint(self, directory: Path, stream: BinaryIO, metadata: dict, *, chunk_bytes=8 * 1024**2, max_bytes=4 * 1024**3):
        chunks = []
        total = 0
        if not 1024 <= chunk_bytes <= 64 * 1024**2 or max_bytes < 1024:
            raise ValueError("Invalid checkpoint chunk or total size limit")
        while data := stream.read(chunk_bytes):
            total += len(data)
            if total > max_bytes:
                raise ValueError("Checkpoint exceeds the experiment's declared artifact allowance")
            reference = self.put_bytes(data)
            self.verify(reference)
            chunks.append(reference.model_dump())
        manifest = {"schema_version": 1, "metadata": metadata, "chunks": chunks, "bytes": total}
        manifest["id"] = "checkpoint_" + content_hash(manifest)
        directory.mkdir(parents=True, exist_ok=True)
        immutable = directory / (manifest["id"] + ".json")
        atomic_json(immutable, manifest)
        # The previous manifest and blobs remain durable if publishing the pointer fails.
        atomic_json(directory / "latest.json", {"id": manifest["id"]})
        return manifest

    def checkpoint_stream(self, manifest: dict):
        original = {k: v for k, v in manifest.items() if k != "id"}
        if manifest["id"] != "checkpoint_" + content_hash(original):
            raise ValueError("Checkpoint manifest hash mismatch")
        total = 0
        # Spools large state to disk, avoiding a universal small JSON/base64 limit.
        result = tempfile.SpooledTemporaryFile(max_size=8 * 1024**2)
        try:
            for raw in manifest["chunks"]:
                with self.open(ArtifactReference(**raw)) as source:
                    for chunk in iter(lambda: source.read(8 * 1024**2), b""):
                        total += len(chunk)
                        result.write(chunk)
            if total != manifest["bytes"]:
                raise ValueError("Checkpoint size mismatch")
            result.seek(0)
            return result
        except BaseException:
            result.close()
            raise
