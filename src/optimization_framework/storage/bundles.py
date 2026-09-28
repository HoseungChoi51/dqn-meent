"""Streaming, content-verified bundle files. Never extract or execute archive members."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import stat
import zipfile

from optimization_framework.contracts.base import canonical_json
from optimization_framework.contracts.bundles import BundleRecord, EvidenceBundle


MAX_METADATA_BYTES = 64 * 1024**2
MAX_RECORD_BYTES = 256 * 1024**2
MAX_BUNDLE_BYTES = 64 * 1024**3


def _entry(name):
    entry = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
    entry.create_system = 3
    entry.external_attr = (stat.S_IFREG | 0o600) << 16
    entry.compress_type = zipfile.ZIP_STORED
    return entry


def write(path, manifest, records, open_blob):
    """Write deterministic bytes; callers atomically publish the completed file."""
    manifest = manifest if isinstance(manifest, EvidenceBundle) else EvidenceBundle(**manifest)
    expected = {reference.key: reference for reference in manifest.records}
    supplied = {record.reference.key: record for record in records}
    if set(expected) != set(supplied):
        raise ValueError("Export record set differs from the manifest")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        archive.writestr(_entry("manifest.json"), canonical_json(manifest.model_dump(mode="json")))
        for key in sorted(expected):
            record = supplied[key]
            BundleRecord.model_validate(record.model_dump(mode="json"))
            archive.writestr(_entry("records/" + key + ".json"), canonical_json(record.model_dump(mode="json")))
        for blob in sorted(manifest.blobs, key=lambda item: item.sha256):
            hasher, size = hashlib.sha256(), 0
            with open_blob(blob) as source, archive.open(_entry("blobs/" + blob.sha256), "w", force_zip64=True) as destination:
                for chunk in iter(lambda: source.read(8 * 1024**2), b""):
                    size += len(chunk)
                    if size > blob.bytes:
                        raise ValueError("Export blob exceeds its declared size")
                    hasher.update(chunk)
                    destination.write(chunk)
            if size != blob.bytes or hasher.hexdigest() != blob.sha256:
                raise ValueError("Export blob differs from its content identity")


class Reader:
    def __init__(self, path):
        self.path = Path(path)
        if self.path.stat().st_size > MAX_BUNDLE_BYTES:
            raise ValueError("Bundle exceeds the supported byte allowance")
        self.archive = zipfile.ZipFile(self.path)
        try:
            members = self.archive.infolist()
            if len(members) > 200001 or len({member.filename for member in members}) != len(members):
                raise ValueError("Bundle contains duplicate or too many members")
            if any(member.compress_type != zipfile.ZIP_STORED or member.flag_bits & 1
                   or stat.S_IFMT(member.external_attr >> 16) not in {0, stat.S_IFREG} for member in members):
                raise ValueError("Evidence bundles require unencrypted regular stored files")
            self.manifest = EvidenceBundle(**self._json("manifest.json"))
            expected = {"manifest.json", *("records/" + record.key + ".json" for record in self.manifest.records),
                        *("blobs/" + blob.sha256 for blob in self.manifest.blobs)}
            if {member.filename for member in members} != expected:
                raise ValueError("Bundle members differ from its declared record and blob set")
            if sum(member.file_size for member in members if member.filename.startswith("records/")) > MAX_RECORD_BYTES:
                raise ValueError("Bundle record metadata exceeds the supported byte allowance")
            for blob in self.manifest.blobs:
                if self.archive.getinfo("blobs/" + blob.sha256).file_size != blob.bytes:
                    raise ValueError("Bundle blob size differs from its manifest")
        except BaseException:
            self.archive.close()
            raise

    def _json(self, name):
        if self.archive.getinfo(name).file_size > MAX_METADATA_BYTES:
            raise ValueError("Bundle metadata exceeds the supported byte allowance")
        value = json.loads(self.archive.read(name))
        if not isinstance(value, dict):
            raise ValueError("Bundle metadata must be a JSON object")
        return value

    def records(self):
        for reference in self.manifest.records:
            record = BundleRecord(**self._json("records/" + reference.key + ".json"))
            if record.reference != reference:
                raise ValueError("Bundle record differs from its manifest identity")
            yield record

    @contextmanager
    def open_blob(self, blob):
        with self.archive.open("blobs/" + blob.sha256) as stream:
            yield stream

    def verify(self, artifacts=None):
        records = list(self.records())
        for blob in self.manifest.blobs:
            with self.open_blob(blob) as source:
                if artifacts is not None:
                    captured = artifacts.put_stream(source, media_type=blob.media_type)
                    checksum, size = captured.sha256, captured.bytes
                else:
                    hasher, size = hashlib.sha256(), 0
                    for chunk in iter(lambda: source.read(8 * 1024**2), b""):
                        hasher.update(chunk)
                        size += len(chunk)
                    checksum = hasher.hexdigest()
            if checksum != blob.sha256 or size != blob.bytes:
                raise ValueError("Bundle blob content does not match its manifest")
        return records

    def close(self):
        self.archive.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
