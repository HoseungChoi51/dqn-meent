"""Publish worker files without granting their paths authority in the host.

Imported workers write a private tree. This reader uses descriptor-relative
opens, rejects links and special files, and copies validated bytes into the
host-owned experiment projection. No private pathname is handed to a consumer.
"""
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile

from optimization_framework.contracts.base import canonical_json, content_hash
from optimization_framework.contracts.experiments import ArtifactReference
from optimization_framework.storage.artifacts import atomic_json, sync_directory


MAX_RECORD_BYTES = 32 * 1024**2
JOURNALS = ("requests.jsonl", "observations.jsonl", "costs.jsonl", "attempts.jsonl", "metrics.jsonl")
HEX = r"[a-f0-9]{64}"
DEFAULT_PUBLISHED_BYTES = 64 * 1024**3


class InvalidWorkerOutput(ValueError):
    pass


class OutputStorageLimit(InvalidWorkerOutput):
    pass


def decode(data):
    def nonfinite(value):
        raise InvalidWorkerOutput("Worker records must contain finite JSON")
    def number(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            nonfinite(value)
        return parsed
    try:
        value = json.loads(data, parse_constant=nonfinite, parse_float=number)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise InvalidWorkerOutput("Worker returned an invalid JSON record") from exc
    if not isinstance(value, dict):
        raise InvalidWorkerOutput("Worker records must be JSON objects")
    return value


class PrivateTree:
    def __init__(self, directory):
        self.fd = os.dup(directory) if type(directory) is int else os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        if not stat.S_ISDIR(os.fstat(self.fd).st_mode):
            os.close(self.fd)
            raise InvalidWorkerOutput("Worker output root must be a directory")

    def close(self):
        os.close(self.fd)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @contextmanager
    def parent(self, relative):
        path = PurePosixPath(relative)
        if path.is_absolute() or not path.parts or ".." in path.parts or str(path) != relative:
            raise InvalidWorkerOutput("Worker output requires a safe relative path")
        descriptor = os.dup(self.fd)
        try:
            for part in path.parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            yield descriptor, path.name
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise InvalidWorkerOutput("Worker output contains a link or invalid directory") from exc
        finally:
            os.close(descriptor)

    @contextmanager
    def open(self, relative, *, limit):
        with self.parent(relative) as (parent, name):
            try:
                descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            except FileNotFoundError:
                raise
            except OSError as exc:
                raise InvalidWorkerOutput("Worker output contains a link or invalid file") from exc
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
                    raise InvalidWorkerOutput("Worker output must be a bounded, unlinked regular file")
                yield stream

    def json(self, relative):
        with self.open(relative, limit=MAX_RECORD_BYTES) as stream:
            data = stream.read(MAX_RECORD_BYTES + 1)
        if len(data) > MAX_RECORD_BYTES:
            raise InvalidWorkerOutput("Worker JSON record exceeds its byte allowance")
        return decode(data)

    def names(self, relative, *, limit=100000):
        try:
            with self.parent(relative + "/_") as (directory, _):
                result = []
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if len(result) >= limit:
                            raise InvalidWorkerOutput("Worker output directory exceeds its entry allowance")
                        result.append(entry.name)
                return result
        except FileNotFoundError:
            return []


class Publisher:
    def __init__(self, private, destination, specification, *, max_bytes=DEFAULT_PUBLISHED_BYTES, max_entries=100000):
        self.private = private if type(private) is int else Path(private)
        self.destination, self.spec = Path(destination), specification
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self.published = {}
        self.committed_bytes = self.staged_bytes = 0
        # The destination is host-owned. Include prior attempts in the allowance;
        # restarting the publisher cannot reset its persistent storage budget.
        if type(max_bytes) is not int or max_bytes <= 0 or type(max_entries) is not int or max_entries <= 0:
            raise ValueError('Published output requires positive byte and entry allowances')
        for name in (*JOURNALS, 'progress.json', 'result.json', 'outputs.json', 'checkpoints', 'artifacts', 'outputs', 'diagnostics',
                     *(path.name for path in self.destination.glob('result.attempt-*.json'))):
            path = self.destination / name
            paths = path.rglob('*') if path.is_dir() else (path,)
            for member in paths:
                if member.is_file():
                    if member.is_symlink():
                        raise InvalidWorkerOutput('Published output contains a symbolic link')
                    self._remember(str(member.relative_to(self.destination)), member.stat().st_size)
        self.copied = {}
        self.bytes = 0
        from optimization_framework.execution.worker import fingerprint
        self.fingerprint = fingerprint(specification)
        self.counts = {}

    def _remember(self, relative, size):
        self.committed_bytes += size - self.published.get(relative, 0)
        self.published[relative] = size
        if self.committed_bytes > self.max_bytes or len(self.published) > self.max_entries:
            raise OutputStorageLimit('Published output exceeds its aggregate allowance')

    @contextmanager
    def _staging(self, relative, size=0):
        reserved = 0
        def grow(total):
            nonlocal reserved
            extra = total - reserved
            if self.committed_bytes + self.staged_bytes + extra > self.max_bytes:
                raise OutputStorageLimit('Published output and staging exceed their aggregate byte allowance')
            self.staged_bytes += extra
            reserved = total
        if relative not in self.published and len(self.published) >= self.max_entries:
            raise OutputStorageLimit('Published output exceeds its entry allowance')
        try:
            grow(size)
            yield grow
        finally:
            self.staged_bytes -= reserved

    def _json(self, relative, value):
        size = len(canonical_json(value)) + 1
        with self._staging(relative, size):
            atomic_json(self.destination / relative, value)
            self._remember(relative, size)

    def _identity(self, value):
        if value.get("experiment_id", self.spec["id"]) != self.spec["id"]:
            raise InvalidWorkerOutput("Worker record refers to another experiment")
        if value.get("spec_hash", self.fingerprint) != self.fingerprint:
            raise InvalidWorkerOutput("Worker record refers to another frozen procedure")

    def _reference(self, tree, raw):
        reference = ArtifactReference(**raw)
        if (not re.fullmatch(HEX, reference.sha256) or reference.id != "sha256:" + reference.sha256
                or reference.availability != "local"):
            raise InvalidWorkerOutput("Worker artifacts must use local content-addressed blobs")
        if reference.sha256 in self.copied:
            if self.copied[reference.sha256] != reference.bytes:
                raise InvalidWorkerOutput("Worker artifact has conflicting byte counts")
            return
        if reference.bytes > self.max_bytes - self.bytes:
            raise InvalidWorkerOutput("Worker artifacts exceed the attempt's output allowance")
        relative = "artifacts/blobs/" + reference.sha256[:2] + "/" + reference.sha256[2:]
        target = self.destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="publish-", dir=target.parent)
        size, checksum = 0, hashlib.sha256()
        try:
            with os.fdopen(fd, "wb") as output, self._staging(relative, reference.bytes), tree.open(relative, limit=reference.bytes) as source:
                while data := source.read(min(8 * 1024**2, reference.bytes - size + 1)):
                    size += len(data)
                    if size > reference.bytes:
                        raise InvalidWorkerOutput("Worker artifact grew beyond its declared size")
                    checksum.update(data)
                    output.write(data)
                output.flush()
                os.fsync(output.fileno())
                if size != reference.bytes or checksum.hexdigest() != reference.sha256:
                    raise InvalidWorkerOutput("Worker artifact differs from its declared content")
                os.replace(temporary, target)
                sync_directory(target.parent)
                self._remember(relative, size)
                self.copied[reference.sha256] = size
                self.bytes += size
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _references(self, tree, value):
        if isinstance(value, dict):
            if {"id", "sha256", "bytes"} <= set(value):
                self._reference(tree, value)
            else:
                for item in value.values():
                    self._references(tree, item)
        elif isinstance(value, list):
            for item in value:
                self._references(tree, item)

    def _journal(self, tree, name):
        target = self.destination / name
        opened = False
        try:
            source = tree.open(name, limit=self.max_bytes)
            with source as stream:
                opened = True
                fd, temporary = tempfile.mkstemp(prefix="publish-", dir=self.destination)
                count, size = 0, 0
                try:
                    with os.fdopen(fd, "wb") as output, self._staging(name) as reserve:
                        previous = target.open("rb") if target.exists() else None
                        try:
                            while line := stream.readline(MAX_RECORD_BYTES + 1):
                                if len(line) > MAX_RECORD_BYTES:
                                    raise InvalidWorkerOutput("Worker journal record exceeds its byte allowance")
                                if not line.endswith(b"\n"):
                                    break  # Only committed records become host-visible.
                                value = decode(line)
                                self._identity(value)
                                self._references(tree, value)
                                if previous:
                                    original = previous.readline(MAX_RECORD_BYTES + 1)
                                    if original and original != line:
                                        raise InvalidWorkerOutput("Worker changed an already published journal prefix")
                                size += len(line)
                                reserve(size)
                                output.write(line)
                                count += 1
                            if previous and previous.read(1):
                                raise InvalidWorkerOutput("Worker removed an already published journal prefix")
                        finally:
                            if previous:
                                previous.close()
                        output.flush()
                        os.fsync(output.fileno())
                        os.replace(temporary, target)
                        sync_directory(self.destination)
                        self._remember(name, size)
                        self.counts[name] = count
                finally:
                    Path(temporary).unlink(missing_ok=True)
        except FileNotFoundError as exc:
            if target.exists() or opened:
                raise InvalidWorkerOutput("Worker removed a committed journal or its referenced artifact") from exc
            return

    def _checkpoint(self, tree, relative):
        manifest = tree.json(relative)
        identity = "checkpoint_" + content_hash({key: value for key, value in manifest.items() if key != "id"})
        if manifest.get("id") != identity or manifest.get("metadata", {}).get("spec_hash") != self.fingerprint:
            raise InvalidWorkerOutput("Worker checkpoint differs from its recorded identity or procedure")
        if manifest["bytes"] != sum(item["bytes"] for item in manifest["chunks"]):
            raise InvalidWorkerOutput("Worker checkpoint has inconsistent chunk sizes")
        if manifest["bytes"] > self.spec.get("recovery", {}).get("max_checkpoint_bytes", 4 * 1024**3):
            raise InvalidWorkerOutput("Worker checkpoint exceeds its frozen allowance")
        if manifest["metadata"]["observation_cursor"] > self.counts.get("observations.jsonl", 0):
            return None
        self._references(tree, manifest)
        self._json('checkpoints/' + identity + '.json', manifest)
        return identity

    def _output(self, tree, relative):
        manifest = tree.json(relative)
        self._identity(manifest)
        if manifest.get("id") != "outputs_" + content_hash({key: value for key, value in manifest.items() if key != "id"}):
            raise InvalidWorkerOutput("Worker output manifest differs from its content identity")
        checkpoint = manifest.get("checkpoint_id")
        if not isinstance(checkpoint, str) or not re.fullmatch("checkpoint_" + HEX, checkpoint):
            raise InvalidWorkerOutput("Worker output has no valid checkpoint identity")
        if not (self.destination / "checkpoints" / (checkpoint + ".json")).is_file():
            return
        self._references(tree, manifest)
        self._json(relative, manifest)

    def publish(self, *, terminal=False):
        with PrivateTree(self.private) as tree:
            if tree.names("artifacts/external"):
                raise InvalidWorkerOutput("Imported workers cannot publish external artifact locations")
            for name in JOURNALS:
                self._journal(tree, name)
            for name in tree.names("checkpoints"):
                if re.fullmatch("checkpoint_" + HEX + r"\.json", name):
                    self._checkpoint(tree, "checkpoints/" + name)
            try:
                pointer = tree.json("checkpoints/latest.json")
                identity = pointer.get("id")
                if not isinstance(identity, str) or not re.fullmatch("checkpoint_" + HEX, identity):
                    raise InvalidWorkerOutput("Worker checkpoint pointer is invalid")
                if (self.destination / "checkpoints" / (identity + ".json")).exists():
                    self._json('checkpoints/latest.json', pointer)
            except FileNotFoundError:
                pass
            for name in tree.names("outputs"):
                if re.fullmatch("outputs_" + HEX + r"\.json", name):
                    self._output(tree, "outputs/" + name)
            for schedule in tree.names("diagnostics"):
                if re.fullmatch(HEX, schedule):
                    for name in tree.names("diagnostics/" + schedule):
                        if re.fullmatch(r"[0-9]+\.json", name):
                            self._output(tree, "diagnostics/" + schedule + "/" + name)
            try:
                self._output(tree, "outputs.json")
            except FileNotFoundError:
                pass
            for name in ("progress.json", "result.json") if terminal else ("progress.json",):
                try:
                    value = tree.json(name)
                except FileNotFoundError:
                    continue
                self._identity(value)
                if value.get("id", self.spec["id"]) != self.spec["id"]:
                    raise InvalidWorkerOutput("Worker result refers to another experiment")
                self._references(tree, value)
                self._json(name, value)
        return dict(self.counts)
