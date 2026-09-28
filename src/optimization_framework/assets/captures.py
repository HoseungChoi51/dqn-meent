"""Resolve admitted source captures from immutable blobs, independent of staging paths."""
import json
from pathlib import Path
import shutil
import tempfile

from optimization_framework.contracts.base import content_hash
from optimization_framework.contracts.bundles import CapturedFiles
from optimization_framework.contracts.experiments import ArtifactReference
from optimization_framework.execution.provenance import verify
from optimization_framework.storage import history


def experiment(workspace, reference):
    record = history.exact(workspace.store, reference)
    if record.reference.owner != "workspace" or record.reference.kind != "trial":
        raise ValueError("Select an archived experiment snapshot")
    manifest = record.data.get("execution_manifest")
    if not manifest or manifest.get("purpose") != "worker":
        raise ValueError("Historical experiment has no captured framework execution manifest")
    captures = [CapturedFiles(**row["capture"]) for row in workspace.store.list("archived_capture")
        if row["capture"]["record_key"] == record.reference.key and row["capture"]["purpose"] == "experiment"]
    if not captures:
        raise ValueError("The selected historical experiment's source capture is unavailable")
    # A repeated export can add logs to the same scientific snapshot. Resolve
    # only the source and lock files that the immutable execution manifest pins.
    names = {"execution-manifest.json", *["code/" + name for name in manifest["files"]],
             *["runtime-locks/" + name for name in manifest["runtime"]["locks"]]}
    failures = []
    for capture in sorted(captures, key=lambda value: content_hash(value.model_dump(mode="json"))):
        if not names <= set(capture.files):
            failures.append("Captured source or dependency locks are incomplete")
            continue
        files = {name: capture.files[name] for name in sorted(names)}
        identity = content_hash(files)
        base = workspace.directory / "bundles" / "resolved-sources"
        directory = base / identity
        if directory.exists():
            try:
                verify(directory, manifest, runtime=False)
                return directory, manifest, record.data
            except ValueError:
                # Do not repair a published tree in place while another reader
                # may hold it. Its damage remains inspectable.
                failures.append("Materialized captured source changed; restore its recorded bytes")
                continue
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="resolving-", dir=base) as temporary:
            stage = Path(temporary)
            try:
                for name, checksum in files.items():
                    blob = workspace.store.get("bundle_blob_" + checksum, "bundle_blob")["blob"]
                    artifact = ArtifactReference(id="sha256:" + checksum,
                        **{key: value for key, value in blob.items() if key != "schema_version"})
                    workspace.assets.artifacts.verify(artifact)
                    target = stage / name
                    if not target.resolve().is_relative_to(stage.resolve()):
                        raise ValueError("Captured source contains an invalid relative file name")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with workspace.assets.artifacts.open(artifact) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output, 8 * 1024**2)
                if json.loads((stage / "execution-manifest.json").read_text()) != manifest:
                    raise ValueError("Captured files refer to another experiment's source manifest")
                verify(stage, manifest, runtime=False)
                try:
                    stage.rename(directory)
                except FileExistsError:
                    verify(directory, manifest, runtime=False)
                return directory, manifest, record.data
            except (OSError, KeyError, ValueError) as exc:
                failures.append(str(exc))
    raise ValueError("Historical source is unavailable: " + "; ".join(sorted(set(failures))))
