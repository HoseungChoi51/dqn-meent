"""Historical executable exchange. Imported production receipts never enter the queue."""
import copy
import hashlib
import json
from pathlib import Path

from pydantic import Field, model_validator

from optimization_framework.contracts.base import Contract, content_hash
from optimization_framework.contracts.bundles import BundleRecord, RecordKey
from optimization_framework.implementations.models import digest, parse_package
from optimization_framework.implementations.revalidation import reports
from optimization_framework.implementations.runtime import write_package
from optimization_framework.storage import history
from optimization_framework.storage.sqlite import atomic_json, identifier, now


class ProductionEvidenceUnavailable(ValueError):
    pass


class ExecutableImport(Contract):
    bundle_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    records: list[BundleRecord] = Field(max_length=10000)
    artifacts: list[dict] = Field(max_length=1000)

    @model_validator(mode="after")
    def ownership(self):
        if any(record.reference.owner != "library" or record.reference.kind not in {
                "implementation_version", "implementation_job", "implementation_validation", "runtime_resolution_receipt"}
                for record in self.records):
            raise ValueError("Library imports contain only executable evidence and historical production receipts")
        return self


def source_id(service):
    with service.lock, service.store.transaction():
        try:
            return service.store.get("library_identity", "library_metadata")["source_id"]
        except KeyError:
            value = {"id": "library_identity", "source_id": identifier("library")}
            service.store.put_immutable("library_metadata", value)
            return value["source_id"]


def production_job(service, version, identity):
    try:
        return service.store.get(identity, "implementation_job")
    except KeyError:
        keys = set(version.get("production_evidence_keys", []))
        candidates = [row["data"] for row in history.find(service.store, identity, "implementation_job")
                      if row["id"] in keys]
        if not candidates:
            raise ProductionEvidenceUnavailable("The executable's historical production receipt is unavailable")
        # Admission order is not production order: importing an old bundle
        # later must not roll a cumulative receipt back to a shorter prefix.
        timestamp = lambda row: (row.get("revision", 0), row.get("updated_at") or row.get("finished_at") or row.get("created_at", ""))
        latest = max(timestamp(row) for row in candidates)
        newest = [row for row in candidates if timestamp(row) == latest]
        if any(row != newest[0] for row in newest):
            raise ProductionEvidenceUnavailable("Historical production receipts have an ambiguous cumulative order")
        return newest[0]


def export(service, version_id):
    owner = source_id(service)
    records, artifacts, seen, missing = {}, [], set(), []
    def add(kind, data):
        archived = [row for row in history.find(service.store, data["id"], kind) if row["data"] == data]
        reference = (RecordKey(**archived[0]["reference"]) if archived else RecordKey(
            owner="library", source_id=owner, kind=kind, id=data["id"], content_digest=content_hash(data)))
        record = BundleRecord(reference=reference, data=data)
        records[reference.key] = record.model_dump(mode="json")
    def visit(identity):
        if identity in seen:
            return
        seen.add(identity)
        bundle = service.artifact(identity, ready=False)
        version = bundle["version"]
        add("implementation_version", version)
        artifacts.append({"version_id": identity, "artifact": bundle["artifact"]})
        for job_id in version.get("production_job_ids", [version["job_id"]]):
            try:
                add("implementation_job", production_job(service, version, job_id))
            except ProductionEvidenceUnavailable:
                missing.append({"version_id": identity, "kind": "implementation_job", "id": job_id,
                    "reason": "The executable's historical production receipt is unavailable"})
        for report in reports(version):
            try:
                add("implementation_validation", service.store.get(report["id"], "implementation_validation"))
            except KeyError:
                pass  # The original report still exists verbatim in the version.
        for receipt in service.store.list("runtime_resolution_receipt"):
            if receipt["version_id"] == identity:
                add("runtime_resolution_receipt", receipt)
        if version["spec"].get("evaluator_version_id"):
            visit(version["spec"]["evaluator_version_id"])
    visit(version_id)
    return {"source_id": owner, "records": list(records.values()), "artifacts": artifacts, "missing": missing}


def verify(service, request):
    request = request if isinstance(request, ExecutableImport) else ExecutableImport(**request)
    history.validate_conflicts(service.store, request.records)
    versions = [record.data for record in request.records if record.reference.kind == "implementation_version"]
    artifacts = {item["version_id"]: item["artifact"] for item in request.artifacts}
    if len(artifacts) != len(request.artifacts) or set(artifacts) != {version["id"] for version in versions}:
        raise ValueError("Imported executable versions and artifacts do not match")
    validation_records, known_reports = {}, {}
    def remember_report(report):
        if report["id"] in known_reports and known_reports[report["id"]] != report:
            raise ValueError("A validation-report identity has conflicting content")
        known_reports[report["id"]] = report
    for record in request.records:
        if record.reference.kind != "implementation_validation":
            continue
        try:
            existing = service.store.get(record.reference.id, "implementation_validation")
        except KeyError:
            existing = validation_records.get(record.reference.id)
        if existing and existing != record.data:
            raise ValueError("An immutable validation record has conflicting content")
        validation_records[record.reference.id] = record.data
        remember_report(record.data["report"])
    for version in versions:
        artifact = artifacts[version["id"]]
        package = parse_package(artifact["package"])
        files = {source.path: hashlib.sha256(source.content.encode()).hexdigest() for source in package.files}
        runtime = artifact["runtime"]
        if (digest(artifact) != version["artifact_digest"] or version["id"] != "impl_" + version["artifact_digest"][:32]
                or artifact["spec"] != version["spec"] or runtime["digest"] != version["runtime_digest"]
                or digest({key: value for key, value in runtime.items() if key != "digest"}) != runtime["digest"]
                or files != version["package_hashes"]):
            raise ValueError("Imported executable identity does not match its source and runtime manifests")
        try:
            existing = service.version(version["id"])
        except KeyError:
            existing = None
        if existing:
            if any(existing[field] != version[field] for field in ("artifact_digest", "runtime_digest", "package_hashes", "spec")):
                raise ValueError("An existing executable identity has conflicting source or runtime content")
            for report in reports(existing):
                remember_report(report)
        for report in [*version.get("validation_history", []), version["validation_report"]]:
            remember_report(report)
        dependency = version["spec"].get("evaluator_version_id")
        if dependency and dependency not in artifacts:
            service.version(dependency)  # Must already exist or be staged together.
    return request


def inspect(service, request):
    with service.lock:
        request = verify(service, request)
        identity = "library_import_" + content_hash(request.model_dump(mode="json"))
        try:
            return service.store.get(identity, "library_import")
        except KeyError:
            record = {"id": identity, "schema_version": 1, "bundle_digest": request.bundle_digest,
                "request": request.model_dump(mode="json"), "status": "staged", "created_at": now()}
            return service.store.put("library_import", record, "library.import_staged")


def publish(service, import_id):
    with service.lock:
        operation = service.store.get(import_id, "library_import")
        if operation.get("receipt_id"):
            return service.store.get(operation["receipt_id"], "library_import_receipt")
        request = verify(service, operation["request"])
        artifacts = {item["version_id"]: item["artifact"] for item in request.artifacts}
        versions = [record.data for record in request.records if record.reference.kind == "implementation_version"]
        for version in versions:
            root = service.directory / "artifacts" / version["artifact_digest"]
            write_package(root / "package", artifacts[version["id"]]["package"])
            path = root / "artifact.json"
            if path.exists() and json.loads(path.read_text()) != artifacts[version["id"]]:
                raise ValueError("An existing artifact file has conflicting content")
            if not path.exists():
                atomic_json(path, artifacts[version["id"]])
                if "runtime_root" in artifacts[version["id"]]:
                    # Preserve legacy JSON as evidence, but do not adopt its
                    # source machine's installation path as a local authority.
                    atomic_json(root / "runtime-location.json", {"runtime_digest": version["runtime_digest"], "root": None})
        with service.store.transaction():
            for record in request.records:
                history.publish(service.store, record)
                if record.reference.kind == "implementation_validation":
                    service.store.put_immutable("implementation_validation", record.data, "implementation.validation_imported")
            for incoming in versions:
                try:
                    version = service.version(incoming["id"])
                except KeyError:
                    version = copy.deepcopy(incoming)
                    version["status"] = incoming["status"] if incoming["status"] in {"revoked", "validation_failed"} else "validation_required"
                    version["imported_correctness_evidence"] = True
                known = {report["id"]: report for report in [*reports(version), *reports(incoming)]}
                version["validation_history"] = [report for identity, report in known.items() if identity != version["validation_report"]["id"]]
                version["production_job_ids"] = list(dict.fromkeys([*version.get("production_job_ids", [version["job_id"]]),
                    *incoming.get("production_job_ids", [incoming["job_id"]])]))
                receipts = [history.archive_identity(record.reference) for record in request.records
                            if record.reference.kind == "implementation_job" and record.reference.id in version["production_job_ids"]]
                version["production_evidence_keys"] = list(dict.fromkeys([*version.get("production_evidence_keys", []), *receipts]))
                if incoming["status"] == "revoked":
                    version.update(status="revoked", revocation_reason=incoming.get("revocation_reason", "Revoked by its source library"))
                elif incoming["status"] == "validation_failed" and version["status"] != "revoked":
                    version.update(status="validation_failed", blocking_validation_report_ids=sorted(set(
                        version.get("blocking_validation_report_ids", []) + incoming.get("blocking_validation_report_ids", [incoming["validation_report"]["id"]]))))
                service.store.put("implementation_version", version, "implementation.history_imported")
            receipt = service.store.put_immutable("library_import_receipt", {"id": import_id + "_receipt", "schema_version": 1,
                "bundle_digest": request.bundle_digest, "version_ids": sorted(artifacts),
                "archived_record_ids": sorted(history.archive_identity(record.reference) for record in request.records),
                "created_at": now()}, "library.import_published")
            operation.update(status="published", receipt_id=receipt["id"])
            service.store.put("library_import", operation, "library.import_completed")
        return receipt
