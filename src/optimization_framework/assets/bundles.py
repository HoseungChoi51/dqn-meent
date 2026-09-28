"""Durable inspect/verify/stage/publish workflow for portable research evidence."""
import json
from pathlib import Path
import shutil
import tempfile
import zipfile

from optimization_framework.contracts.assets import Asset, CostEvent, CostReconciliation, CostSnapshot
from optimization_framework.contracts.base import content_hash
from optimization_framework.contracts.bundles import BundleRecord, CapturedFiles, EvidenceBundle
from optimization_framework.contracts.experiments import ArtifactReference
from optimization_framework.storage import history
from optimization_framework.storage.artifacts import atomic_json
from optimization_framework.storage.bundles import MAX_BUNDLE_BYTES, Reader
from optimization_framework.storage.sqlite import now
from optimization_framework.implementations.models import LibraryUnavailable


NATIVE_EVIDENCE = {"asset": Asset, "cost_event": CostEvent, "cost_reconciliation": CostReconciliation, "cost_snapshot": CostSnapshot}


class BundleService:
    def __init__(self, workspace):
        self.workspace, self.store = workspace, workspace.store
        self.artifacts = workspace.assets.artifacts
        self.directory = workspace.directory / "bundles"

    def upload(self, stream):
        reference = self.artifacts.put_stream(stream, media_type="application/vnd.optimization.evidence+zip")
        if reference.bytes > MAX_BUNDLE_BYTES:
            raise ValueError("Bundle exceeds the supported byte allowance")
        self.store.put_immutable("bundle_upload", {"id": "bundle_upload_" + reference.sha256,
            "schema_version": 1, "reference": reference.model_dump(mode="json")})
        return {"upload_id": reference.id, "bytes": reference.bytes}

    def _upload(self, upload_id):
        raw = self.store.get("bundle_upload_" + upload_id.removeprefix("sha256:"), "bundle_upload")
        reference = ArtifactReference(**raw["reference"])
        self.artifacts.verify(reference)
        return reference

    def update(self, operation, **changes):
        operation.update(**changes, updated_at=now())
        return self.store.put("bundle_operation", operation, "bundle.operation_updated")

    def export(self, operation):
        from optimization_framework.assets.bundle_export import Exporter
        if operation["status"] == "completed":
            return operation
        self.update(operation, status="capturing")
        self.directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="export-", dir=self.directory) as temporary:
            path = Path(temporary) / "evidence.zip"
            exporter = Exporter(self.workspace)
            manifest = exporter.export(operation["payload"]["asset_ids"], path)
            with path.open("rb") as stream:
                archive = self.artifacts.put_stream(stream, media_type="application/vnd.optimization.evidence+zip")
        return self.update(operation, status="completed", bundle_digest=manifest.digest,
            archive=archive.model_dump(mode="json"), summary=self.summary(manifest, exporter.records.values()), finished_at=now())

    @staticmethod
    def summary(manifest, records=()):
        records = list(records)
        values = {record.reference.key: record.data for record in records}
        costs = [record.data for record in records if record.reference.kind == "cost_event"]
        from optimization_framework.assets.accounting import project
        from optimization_framework.assets.catalog import merged_intervals
        from optimization_framework.assets.service_costs import AXES
        assets = {record.reference.id: record.data for record in records if record.reference.kind == "asset"}
        seen, intervals, unknown = set(), {}, 0
        def visit(identity):
            nonlocal unknown
            if identity in seen:
                return
            seen.add(identity)
            asset = assets.get(identity)
            if not asset:
                unknown += 1
                return
            unknown += asset.get("cost_provenance") != "complete"
            for interval in asset["costs"]:
                intervals.setdefault(interval["source_id"], []).append([interval["start"], interval["stop"]])
            for dependency in asset["dependency_ids"]:
                visit(dependency)
        for key in manifest.roots:
            if key in values:
                visit(values[key]["id"])
        intervals = {source: merged_intervals(ranges) for source, ranges in intervals.items()}
        reconciliations = [record.data for record in records if record.reference.kind == "cost_reconciliation"]
        quantities, accounting = project(costs, reconciliations, intervals, AXES, unknown_provenance=unknown)
        return {"bundle_digest": manifest.digest, "record_count": len(manifest.records), "blob_count": len(manifest.blobs),
            "blob_bytes": sum(blob.bytes for blob in manifest.blobs),
            "roots": [{"record_key": key, **({field: values[key].get(field) for field in
                ("id", "title", "kind", "exposure_status", "cost_provenance")} if key in values else {})} for key in manifest.roots],
            "missing": [item.model_dump(mode="json") for item in manifest.missing],
            "provenance": "partial" if manifest.missing else "complete",
            "accounting": accounting["status"], "accounting_basis": accounting, "quantities": quantities,
            "execution": "Historical records remain outside the execution queue; executable reuse requires local correctness evidence and a compatible runtime"}

    def conflicts(self, manifest, records):
        history.validate_conflicts(self.store, records)
        native = [record for record in records if record.reference.owner == "workspace" and record.reference.kind in NATIVE_EVIDENCE]
        identities = {}
        for record in native:
            if record.reference.id in identities and identities[record.reference.id] != record.data:
                raise ValueError("Bundle assigns conflicting content to one immutable record identity")
            identities[record.reference.id] = record.data
        assets = {record.reference.id: record.data for record in native if record.reference.kind == "asset"}
        blobs = {blob.sha256: blob for blob in manifest.blobs}
        for blob in manifest.blobs:
            try:
                existing = self.store.get("bundle_blob_" + blob.sha256, "bundle_blob")
            except KeyError:
                continue
            if existing["blob"] != blob.model_dump(mode="json"):
                raise ValueError("An existing blob identity has conflicting bundle metadata")
        missing_blobs = {item.id for item in manifest.missing if item.kind == "blob"}
        for asset in assets.values():
            for raw in asset["artifacts"]:
                reference = ArtifactReference(**raw)
                if reference.sha256 not in blobs and reference.id not in missing_blobs:
                    raise ValueError("An asset artifact is neither included nor explicitly marked missing")
                if reference.sha256 in blobs and reference.bytes != blobs[reference.sha256].bytes:
                    raise ValueError("An asset artifact size differs from its bundled bytes")
        costs = {(event["source_id"], event["ordinal"]): event for event in self.store.list("cost_event")}
        for record in native:
            NATIVE_EVIDENCE[record.reference.kind](**{key: value for key, value in record.data.items() if key != "content_hash"})
            if "content_hash" not in record.data:
                raise ValueError("Native imported evidence requires its original immutable-record hash")
            try:
                existing = self.store.get_entry(record.reference.id)
            except KeyError:
                existing = None
            if existing and (existing["kind"] != record.reference.kind or existing["data"] != record.data):
                raise ValueError("An existing record identity has conflicting imported evidence")
            if record.reference.kind == "cost_event":
                position = record.data["source_id"], record.data["ordinal"]
                if position in costs and costs[position] != record.data:
                    raise ValueError("An existing physical cost position has conflicting imported evidence")
                costs[position] = record.data
        from optimization_framework.assets.accounting import validate, prefix, project, close
        reconciliations = {row["id"]: row for row in self.store.list("cost_reconciliation")}
        reconciliations.update({record.reference.id: record.data for record in native if record.reference.kind == "cost_reconciliation"})
        validate(list(costs.values()), list(reconciliations.values()))
        for record in native:
            if record.reference.kind != "cost_snapshot":
                continue
            snapshot = record.data
            events = prefix([event for event in costs.values() if event["source_id"] == snapshot["source_id"]], snapshot["stop"])
            if any(event["campaign_id"] != snapshot["campaign_id"] for event in events):
                raise ValueError("An imported service receipt changes the original cost owner")
            values, _ = project(events, list(reconciliations.values()), {snapshot["source_id"]: [(0, snapshot["stop"])]}, snapshot["quantities"])
            if any(value is not None and values[axis]["total"] is not None and not close(value, values[axis]["total"])
                   for axis, value in snapshot["quantities"].items()):
                raise ValueError("An imported cumulative receipt contradicts its measured cost prefix")
        pending, seen = set(), set()
        def visit(identity):
            if identity in pending:
                raise ValueError("Imported assets contain a dependency cycle")
            if identity in seen:
                return
            try:
                asset = assets.get(identity) or self.store.get(identity, "asset")
            except KeyError:
                raise ValueError("An imported asset is missing a declared upstream asset") from None
            pending.add(identity)
            for dependency in asset["dependency_ids"]:
                visit(dependency)
            pending.remove(identity)
            seen.add(identity)
        for identity in assets:
            visit(identity)
        refs = {record.reference.key: record for record in records}
        if any(refs[key].reference.kind != "asset" for key in manifest.roots):
            raise ValueError("A result bundle must have asset roots")

    def materialize(self, manifest):
        base = self.directory / "staged" / manifest.digest
        blobs = {blob.sha256: blob for blob in manifest.blobs}
        for capture in manifest.captures:
            destination = base / capture.record_key / capture.purpose
            destination.mkdir(parents=True, exist_ok=True)
            for name, checksum in capture.files.items():
                path = destination / name
                if path.is_symlink() or not path.resolve().is_relative_to(destination.resolve()):
                    raise ValueError("A captured file resolves outside its staging tree")
                path.parent.mkdir(parents=True, exist_ok=True)
                blob = blobs[checksum]
                reference = ArtifactReference(id="sha256:" + checksum, **blob.model_dump(exclude={"schema_version"}))
                with self.artifacts.open(reference) as source, path.open("wb") as target:
                    shutil.copyfileobj(source, target, 8 * 1024**2)
            if capture.purpose in {"experiment", "compiler"} and "execution-manifest.json" in capture.files:
                from optimization_framework.execution.provenance import verify
                verify(destination, json.loads((destination / "execution-manifest.json").read_text()), runtime=False)
        return base

    def library_request(self, manifest, records):
        library = [record for record in records if record.reference.owner == "library"]
        if not library:
            return None
        values = {record.reference.key: record for record in library}
        artifacts = {}
        blobs = {blob.sha256: blob for blob in manifest.blobs}
        for capture in manifest.captures:
            if capture.purpose != "executable":
                continue
            record = values[capture.record_key]
            checksum = capture.files["artifact.json"]
            reference = ArtifactReference(id="sha256:" + checksum, **blobs[checksum].model_dump(exclude={"schema_version"}))
            with self.artifacts.open(reference) as stream:
                artifact = json.load(stream)
            if record.reference.id in artifacts and artifacts[record.reference.id] != artifact:
                raise ValueError("Executable identity has conflicting artifact captures")
            artifacts[record.reference.id] = artifact
        return {"bundle_digest": manifest.digest, "records": [record.model_dump(mode="json") for record in library],
                "artifacts": [{"version_id": identity, "artifact": value} for identity, value in sorted(artifacts.items())]}

    def inspect(self, operation):
        if operation["status"] == "completed":
            return operation
        reference = self._upload(operation["payload"]["upload_id"])
        self.update(operation, status="verifying")
        with Reader(self.artifacts.resolve(reference)) as reader:
            records = reader.verify(self.artifacts)
            manifest = reader.manifest
        self.conflicts(manifest, records)
        self.update(operation, status="staging", bundle_digest=manifest.digest)
        self.materialize(manifest)
        library = self.library_request(manifest, records)
        staged_library = self.workspace.implementations.client.inspect_import(library) if library else None
        identity = "bundle_inspection_" + content_hash([operation["campaign_id"], manifest.digest])
        with self.store.transaction():
            try:
                inspection = self.store.get(identity, "bundle_inspection")
            except KeyError:
                inspection = self.store.put_immutable("bundle_inspection", {"id": identity, "schema_version": 1,
                    "campaign_id": operation["campaign_id"], "archive": reference.model_dump(mode="json"),
                    "manifest": manifest.model_dump(mode="json"), "summary": self.summary(manifest, records),
                    "library_import_id": staged_library["id"] if staged_library else None,
                    "created_at": now()}, "bundle.inspected")
            return self.update(operation, status="completed", inspection_id=identity, summary=inspection["summary"], finished_at=now())

    def publish(self, operation):
        if operation["status"] == "completed":
            return operation
        inspection = self.store.get(operation["payload"]["inspection_id"], "bundle_inspection")
        if inspection["campaign_id"] != operation["campaign_id"]:
            raise ValueError("Bundle inspection belongs to another campaign")
        identity = "bundle_import_" + content_hash([operation["campaign_id"], inspection["summary"]["bundle_digest"]])
        try:
            receipt = self.store.get(identity, "bundle_import_receipt")
        except KeyError:
            receipt = None
        if receipt:
            return self.update(operation, status="completed", receipt_id=receipt["id"], summary=inspection["summary"], finished_at=now())
        self.update(operation, status="publishing")
        reference = ArtifactReference(**inspection["archive"])
        with Reader(self.artifacts.resolve(reference)) as reader:
            records = reader.verify()
            manifest = reader.manifest
        if manifest.model_dump(mode="json") != inspection["manifest"]:
            raise ValueError("Staged bundle changed after inspection")
        self.conflicts(manifest, records)
        library_receipt = (self.workspace.implementations.client.publish_import(inspection["library_import_id"])
                           if inspection["library_import_id"] else None)
        versions = [self.workspace.implementations.client.version(identity) for identity in library_receipt["version_ids"]] if library_receipt else []
        with self.workspace.lock, self.store.transaction():
            self.conflicts(manifest, records)
            for version in versions:
                self.workspace.implementations.cache_version(version)
            for record in records:
                history.publish(self.store, record)
                if record.reference.owner == "workspace" and record.reference.kind in NATIVE_EVIDENCE:
                    self.store.put_immutable(record.reference.kind, record.data, "bundle.evidence_imported")
                if record.reference.kind == "asset" and record.reference.owner == "workspace":
                    self.store.put_immutable("imported_asset", {"id": "imported_asset_" + content_hash([identity, record.reference.id]),
                        "campaign_id": operation["campaign_id"], "asset_id": record.reference.id,
                        "bundle_digest": manifest.digest, "import_id": identity}, "bundle.asset_linked")
            for blob in manifest.blobs:
                self.store.put_immutable("bundle_blob", {"id": "bundle_blob_" + blob.sha256, "blob": blob.model_dump(mode="json")})
            for capture in manifest.captures:
                self.store.put_immutable("archived_capture", {"id": "archived_capture_" + content_hash(capture.model_dump(mode="json")),
                    "capture": capture.model_dump(mode="json")})
            for edge in manifest.edges:
                self.store.put_immutable("archived_edge", {"id": "archived_edge_" + content_hash(edge.model_dump(mode="json")),
                    "edge": edge.model_dump(mode="json")})
            for missing in manifest.missing:
                evidence = {"missing": missing.model_dump(mode="json"),
                    "record_keys": [missing.record_key] if missing.record_key else sorted(record.reference.key for record in records)}
                self.store.put_immutable("archived_missing", {"id": "archived_missing_" + content_hash(evidence), **evidence})
            receipt = self.store.put_immutable("bundle_import_receipt", {"id": identity, "schema_version": 1,
                "campaign_id": operation["campaign_id"], "bundle_digest": manifest.digest,
                "library_receipt": library_receipt, "root_asset_ids": [next(record.reference.id for record in records
                    if record.reference.key == key) for key in manifest.roots],
                "record_count": len(records), "created_at": now()}, "bundle.import_published")
            return self.update(operation, status="completed", receipt_id=receipt["id"], summary=inspection["summary"], finished_at=now())

    def dispatch(self, effect):
        operation = self.store.get(effect["operation_id"], "bundle_operation")
        if operation["status"] == "failed":
            return operation
        try:
            return getattr(self, operation["action"])(operation)
        except LibraryUnavailable:
            raise  # Keep the durable effect pending for service reconciliation.
        except (ValueError, OSError, KeyError, zipfile.BadZipFile) as exc:
            with self.store.transaction():
                self.workspace.memory.issue(operation["campaign_id"], "bundle_operation", str(exc), affected=operation["id"])
                return self.update(operation, status="failed", error=str(exc), finished_at=now())
