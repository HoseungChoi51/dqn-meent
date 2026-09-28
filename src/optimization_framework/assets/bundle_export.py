"""Capture a result's declared evidence graph and materialize all referenced bytes."""
import json
from pathlib import Path

from optimization_framework.contracts.base import content_hash
from optimization_framework.contracts.bundles import (
    BundleBlob, BundleEdge, BundleRecord, CapturedFiles, EvidenceBundle, MissingEvidence, RecordKey,
)
from optimization_framework.contracts.experiments import ArtifactReference
from optimization_framework.storage import history
from optimization_framework.storage.bundles import write


# Operational queues, provider credentials and current manager projections never
# enter the historical graph just because another record happens to name them.
EVIDENCE_KINDS = {"asset", "cost_event", "cost_snapshot", "cost_reconciliation", "trial", "experiment_spec", "execution_attempt",
    "execution_source", "campaign", "task", "study", "hypothesis", "reuse_decision", "research_run", "research_result", "source", "source_retrieval", "finding", "manager_note",
    "implementation_grant", "runtime_resolution_receipt", "evaluator_requirement", "evaluator_binding",
    "validation_requirement", "validation_result", "waiver", "waiver_revocation", "confirmation_protocol", "confirmation_allocation_binding",
    "confirmation_design", "confirmation_release", "confirmation_report", "confirmation_reassessment",
    "nomination", "nomination_reassessment", "study_execution", "study_design", "budget_amendment", "deadline_enforcement",
    "execution_host_receipt", "reproduction_comparison"}


class Exporter:
    def __init__(self, workspace):
        self.workspace, self.store, self.catalog = workspace, workspace.store, workspace.assets
        self.source_id = workspace.implementations.workspace_id
        self.records, self.blobs, self.captures, self.edges, self.missing = {}, {}, {}, set(), {}
        self.seen, self.executable_ids = {}, set()
        self.archived = self.store.list("archived_record")
        self.archived_by_key = {RecordKey(**record["reference"]).key: record for record in self.archived}
        self.archived_by_identity, self.archived_by_content, self.archived_edges, self.archived_captures = {}, {}, {}, {}
        for record in self.archived:
            ref = record["reference"]
            self.archived_by_identity.setdefault(ref["id"], []).append(record)
            self.archived_by_content[(ref["kind"], ref["id"], ref["content_digest"])] = record
        for record in self.store.list("archived_edge"):
            self.archived_edges.setdefault(record["edge"]["source"], []).append(record["edge"])
        for record in self.store.list("archived_capture"):
            self.archived_captures.setdefault(record["capture"]["record_key"], []).append(record["capture"])
        self.archived_seen = set()
        self.archived_missing = {}
        for record in self.store.list("archived_missing"):
            for key in record["record_keys"]:
                self.archived_missing.setdefault(key, []).append(record["missing"])

    def missing_evidence(self, owner, kind, identity, reason, record_key=None):
        if any(item.owner == owner and item.kind == kind and item.id == str(identity) and item.record_key == record_key
               for item in self.missing.values()):
            return
        value = MissingEvidence(owner=owner, kind=kind, id=str(identity), reason=str(reason)[:2000], record_key=record_key)
        self.missing[content_hash(value.model_dump(mode="json"))] = value

    def envelope(self, kind, data, *, owner="workspace", source_id=None):
        # Re-export imported records under the original producer identity.
        checksum = content_hash(data)
        existing = self.archived_by_content.get((kind, data["id"], checksum))
        reference = (RecordKey(**existing["reference"]) if existing else RecordKey(owner=owner,
            source_id=source_id or self.source_id, kind=kind, id=data["id"], content_digest=checksum))
        value = BundleRecord(reference=reference, data=data)
        self.records[reference.key] = value
        return reference.key

    def edge(self, parent, dependency, role):
        if parent and dependency:
            self.edges.add((parent, dependency, role))

    def blob(self, reference, record_key=None):
        reference = reference if isinstance(reference, ArtifactReference) else ArtifactReference(**reference)
        try:
            self.catalog.artifacts.verify(reference)
        except (OSError, ValueError) as exc:
            self.missing_evidence("workspace", "blob", reference.id, exc, record_key)
            return None
        # A byte blob can serve several semantic roles. Those media types stay
        # on the original artifact references; the physical blob has one stable
        # envelope regardless of which dependency was traversed first.
        self.blobs[reference.sha256] = BundleBlob(sha256=reference.sha256, bytes=reference.bytes)
        return reference.sha256

    def capture(self, record_key, directory, purpose):
        directory = Path(directory)
        if not directory.is_dir():
            self.missing_evidence("workspace", "capture", record_key, "Captured source or experiment files are unavailable", record_key)
            return
        files = {}
        for path in sorted(directory.rglob("*")):
            relative = path.relative_to(directory)
            if "__pycache__" in relative.parts or path.suffix == ".pyc" or path.name in {"service.lock", "control.lock"}:
                continue
            if str(relative) in {"implementation/runtime-location.json", "evaluator/runtime-location.json", "control.json"}:
                continue
            if path.is_symlink():
                raise ValueError("Captured evidence cannot contain symbolic links")
            if path.is_file():
                reference = self.catalog.artifacts.register_external(path)
                checksum = self.blob(reference, record_key)
                if checksum:
                    files[relative.as_posix()] = checksum
        self.captures[(record_key, purpose)] = CapturedFiles(record_key=record_key, purpose=purpose, files=files)

    def archived_capture(self, key):
        for capture in self.archived_captures.get(key, []):
            value = CapturedFiles(**capture)
            for checksum in value.files.values():
                raw = self.store.get("bundle_blob_" + checksum, "bundle_blob")
                if not self.blob(ArtifactReference(id="sha256:" + checksum, **raw["blob"]), key):
                    raise ValueError("An archived source capture is unavailable; restore its captured bytes before re-export")
            self.captures[(key, value.purpose)] = value

    def include_archived(self, key):
        if key in self.archived_seen:
            return
        self.archived_seen.add(key)
        raw = self.archived_by_key[key]
        record = BundleRecord(reference=RecordKey(**raw["reference"]), data=raw["data"])
        self.records[key] = record
        for raw_missing in self.archived_missing.get(key, []):
            value = MissingEvidence(**raw_missing)
            self.missing[content_hash(value.model_dump(mode="json"))] = value
        if record.reference.owner == "workspace" and record.reference.kind == "asset":
            self.catalog.check_export_exposure(record.data)
            for reference in record.data["artifacts"]:
                self.blob(reference, key)
            # Imported content stays immutable, but later local receipts can
            # improve its accounting. Carry both the original archive closure
            # and the currently admitted evidence for its cost sources.
            self.costs(record.data, key)
        self.archived_capture(key)
        for edge in self.archived_edges.get(key, []):
            self.include_archived(edge["dependency"])
            self.edge(key, edge["dependency"], edge["role"])

    def add(self, identity, *, required=False, parent=None, role="evidence"):
        if not identity:
            return None
        if identity in self.seen:
            key = self.seen[identity]
            self.edge(parent, key, role)
            return key
        try:
            entry = self.store.get_entry(identity)
            kind, data = entry["kind"], entry["data"]
        except KeyError:
            matches = [record for record in self.archived_by_identity.get(identity, [])
                       if record["reference"]["owner"] == "workspace"]
            if not matches:
                if required:
                    self.missing_evidence("workspace", "record", identity, "The declared source record is unavailable", parent)
                return None
            # Preserve every admitted historical snapshot rather than choose an
            # apparently latest mutable job state as scientific authority.
            keys = [self.envelope(record["reference"]["kind"], record["data"]) for record in matches]
            self.seen[identity] = keys[0]
            for key in keys:
                self.include_archived(key)
                self.edge(parent, key, role)
            return keys[0]
        if kind not in EVIDENCE_KINDS:
            return None
        key = self.envelope(kind, data)
        self.seen[identity] = key
        self.edge(parent, key, role)
        if key in self.archived_by_key:
            self.include_archived(key)
            return key
        if kind == "asset":
            self.catalog.check_export_exposure(data)
            for dependency in data["dependency_ids"]:
                self.add(dependency, required=True, parent=key, role="asset_dependency")
            for reference in data["artifacts"]:
                self.blob(reference, key)
            self.add(data.get("producer_id"), required=bool(data.get("producer_id")) and not str(data.get("producer_id", "")).startswith("impl_"),
                     parent=key, role="producer")
            self.costs(data, key)
            executable = data.get("payload", {}).get("implementation_version_id")
            if executable:
                self.executable(executable, key)
        elif kind == "trial":
            if data["status"] in {"queued", "running", "pausing", "stopping"}:
                raise ValueError("Pause or finish the experiment before exporting its captured evidence")
            self.capture(key, self.workspace.job_dir(identity), "experiment")
            for executable in (data.get("implementation_version_id"), data.get("evaluator_version_id")):
                if executable:
                    self.executable(executable, key)
            for release in history.releases(self.store, data["campaign_id"]):
                if identity in release["trial_ids"]:
                    released = self.envelope("confirmation_release", release)
                    self.edge(key, released, "exposure_release")
        elif kind == "execution_source":
            self.capture(key, self.store.directory / "sources" / identity, "compiler")
        elif kind == "confirmation_protocol":
            from optimization_framework.evaluation.confirmation import method_definition
            from optimization_framework.evaluation.confirmation_allocations import binding_id
            # Allocations have immutable sidecars so historical protocol digests
            # remain unchanged. Carry the source-to-final derivation with results.
            changed_allocation = False
            for method_id, prototype_id in data.get("prototypes", {}).items():
                self.add(prototype_id, required=True, parent=key, role="source_prototype")
                try:
                    prototype = self.store.get(prototype_id, "trial")
                    changed_allocation |= content_hash(method_definition(prototype)) != method_id
                except (KeyError, ValueError):
                    pass  # Missing source evidence is already recorded above.
            self.add(binding_id(identity), required=changed_allocation, parent=key, role="final_allocation")
        elif kind in {"cost_snapshot", "cost_reconciliation"}:
            # A cumulative receipt needs the original prefix to verify its
            # equations even when a root attributes only a later subinterval.
            for event in self.store.list("cost_event"):
                if event["source_id"] == data["source_id"] and event["ordinal"] < data["stop"]:
                    self.add(event["id"], required=True, parent=key, role="receipt_basis")
        # Follow actual record references, not arbitrary prose, algorithm names
        # or external URLs. Missing optional metadata never invents a producer.
        def links(value, field=""):
            if isinstance(value, dict):
                if set(value) == {"schema_version", "owner", "source_id", "kind", "id", "content_digest"}:
                    reference = RecordKey(**value)
                    if reference.key in self.archived_by_key:
                        self.include_archived(reference.key)
                        self.edge(key, reference.key, "historical_reference")
                    else:
                        self.missing_evidence(reference.owner, reference.kind, reference.id,
                            "The exact referenced historical snapshot is unavailable", key)
                    return
                for name, child in value.items():
                    if name not in {"id", "content_hash", "payload", "context_snapshot", "parameters", "configuration"}:
                        links(child, name)
            elif isinstance(value, list):
                for child in value:
                    links(child, field)
            elif isinstance(value, str) and (field.endswith("_id") or field.endswith("_ids") or field in {"dependencies", "counterevidence_for"}):
                self.add(value, parent=key, role=field)
        links(data)
        return key

    def costs(self, asset, key):
        intervals = asset["costs"]
        selected = [event for event in self.store.list("cost_event") if any(
            interval["source_id"] == event["source_id"] and interval["start"] <= event["ordinal"] < interval["stop"] for interval in intervals)]
        for event in selected:
            self.add(event["id"], required=True, parent=key, role="cost")
        available = {(event["source_id"], event["ordinal"]) for event in selected}
        for interval in intervals:
            missing = interval["stop"] - interval["start"] - sum(
                source == interval["source_id"] and interval["start"] <= ordinal < interval["stop"] for source, ordinal in available)
            if missing:
                self.missing_evidence("workspace", "cost_interval", interval["source_id"], f"{missing} cost positions are unavailable", key)
        for kind in ("cost_snapshot", "cost_reconciliation"):
            for record in self.store.list(kind):
                if any(interval["source_id"] == record.get("source_id") for interval in intervals):
                    self.add(record["id"], parent=key, role=kind)

    def executable(self, identity, parent):
        if identity in self.executable_ids:
            for key, record in self.records.items():
                if record.reference.kind == "implementation_version" and record.reference.id == identity:
                    self.edge(parent, key, "executable")
            return
        self.executable_ids.add(identity)
        try:
            bundle = self.workspace.implementations.client.export_version(identity)
        except (ValueError, AttributeError) as exc:
            self.missing_evidence("library", "implementation_version", identity, exc, parent)
            return
        for raw in bundle["records"]:
            record = BundleRecord(**raw)
            self.records[record.reference.key] = record
            self.edge(parent, record.reference.key, "executable" if record.reference.kind == "implementation_version" else "production_evidence")
        for missing in bundle.get("missing", []):
            version = next(record for record in self.records.values() if record.reference.kind == "implementation_version"
                           and record.reference.id == missing["version_id"])
            self.missing_evidence("library", missing["kind"], missing["id"], missing["reason"], version.reference.key)
        for artifact in bundle["artifacts"]:
            version = next(record for record in self.records.values() if record.reference.kind == "implementation_version"
                           and record.reference.id == artifact["version_id"])
            reference = self.catalog.artifacts.put_bytes(json.dumps(artifact["artifact"], sort_keys=True, separators=(",", ":")).encode(),
                                                        media_type="application/json")
            checksum = self.blob(reference)
            self.captures[(version.reference.key, "executable")] = CapturedFiles(record_key=version.reference.key,
                purpose="executable", files={"artifact.json": checksum})

    def export(self, asset_ids, path):
        roots = [self.add(identity, required=True) for identity in asset_ids]
        if any(key is None or self.records[key].reference.kind != "asset" for key in roots):
            raise ValueError("Bundle roots must identify existing research assets")
        manifest = EvidenceBundle(roots=sorted(set(roots)), records=[self.records[key].reference for key in sorted(self.records)],
            blobs=[self.blobs[key] for key in sorted(self.blobs)], captures=[self.captures[key] for key in sorted(self.captures)],
            edges=[BundleEdge(source=source, dependency=dependency, role=role) for source, dependency, role in sorted(self.edges)],
            missing=[self.missing[key] for key in sorted(self.missing)])
        def open_blob(blob):
            return self.catalog.artifacts.open(ArtifactReference(id="sha256:" + blob.sha256, **blob.model_dump(exclude={"schema_version"})))
        write(path, manifest, list(self.records.values()), open_blob)
        return manifest
