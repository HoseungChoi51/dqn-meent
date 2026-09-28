"""Portable history keeps its identities and never becomes runnable imported work."""
import copy
from pathlib import Path
import shutil
import zipfile

import pytest

from optimization_framework.contracts.assets import Asset, CostEvent, CostSlice
from optimization_framework.contracts.base import content_hash
from optimization_framework.contracts.commands import Command
from optimization_framework.contracts.requests import TrialInput
from optimization_framework.implementations import exchange
from optimization_framework.implementations.models import LibraryUnavailable
from optimization_framework.implementations.runtime_resolution import availability, resolve
from optimization_framework.implementations.service import ImplementationService
from optimization_framework.storage.bundles import Reader, write
from optimization_framework.contracts.bundles import EvidenceBundle
from optimization_framework.storage import history
from test_framework_provenance import campaign, finish
from test_framework_revalidation import campaign_fixture, Client as BaseClient, no_model, check_request
from test_framework_evaluators import specification
from test_framework_runtime_portability import resolution_request


class Client(BaseClient):
    def export_version(self, identity): return exchange.export(self.service, identity)
    def inspect_import(self, request): return exchange.inspect(self.service, request)
    def publish_import(self, identity): return exchange.publish(self.service, identity)


def command(workspace, campaign_id, operation, payload, key):
    campaign = workspace.store.get(campaign_id, "campaign")
    accepted = workspace.commands.execute(Command(id=key, campaign_id=campaign_id, expected_revision=campaign["version"],
        operation=operation, payload=payload))
    result = workspace.store.get(accepted["outcome"]["operation_id"], "bundle_operation")
    assert result["status"] == "completed", result
    return result


def export(workspace, campaign_id, asset_ids, key="export"):
    result = command(workspace, campaign_id, "bundle.export", {"asset_ids": asset_ids}, key)
    from optimization_framework.contracts.experiments import ArtifactReference
    path = workspace.assets.artifacts.resolve(ArtifactReference(**result["archive"]))
    return result, path


def inspect(workspace, campaign_id, path, key="inspect"):
    with Path(path).open("rb") as stream:
        upload = workspace.bundles.upload(stream)
    result = command(workspace, campaign_id, "bundle.inspect", {"upload_id": upload["upload_id"]}, key)
    return workspace.store.get(result["inspection_id"], "bundle_inspection")


def publish(workspace, campaign_id, inspection, key="publish"):
    return command(workspace, campaign_id, "bundle.publish", {"inspection_id": inspection["id"]}, key)


def rewrite_bundle(original, target, transform):
    with Reader(original) as reader:
        manifest, records = reader.manifest.model_dump(mode="json"), list(reader.records())
        manifest, records = transform(manifest, records)
        write(target, EvidenceBundle(**manifest), records, reader.open_blob)
    return target


def test_real_result_moves_with_source_objectives_lineage_and_costs_without_a_live_producer(tmp_path):
    source, owner, task = campaign(tmp_path / "source")
    trial = source.create_trial(TrialInput(campaign_id=owner, task_id=task, algorithm="coordinate", max_steps=4, wall_seconds=10))
    finish(source, trial)
    trial = source.store.get(trial["id"], "trial")
    roots = trial["output_asset_ids"]
    before = source.assets.attributed_costs(roots)
    exported, archive = export(source, owner, roots)
    portable = tmp_path / "result.zip"
    shutil.copyfile(archive, portable)
    source.directory.rename(tmp_path / "retained-original")
    destination, current, _ = campaign(tmp_path / "destination")
    checked = inspect(destination, current, portable)
    assert not destination.store.list("asset") and not destination.store.list("trial")
    assert checked["summary"]["provenance"] == "complete", checked["summary"]
    published = publish(destination, current, checked)
    assert destination.assets.attributed_costs(roots) == before
    assert not destination.store.list("trial")
    assert destination.assets.actual_costs(current)["event_count"] == 0
    assert checked["summary"]["accounting"] == "complete"
    archived = history.find(destination.store, trial["id"], "trial")
    assert len(archived) == 1 and archived[0]["data"] == trial
    assert archived[0]["data"]["problem"]["primary_objective"]["direction"] == "minimize"
    assert {asset["id"] for asset in destination.assets.visible(current)} == set(roots)
    for asset in destination.assets.visible(current):
        assert destination.assets.availability(asset)["status"] == "available"
    counts = {kind: len(destination.store.list(kind)) for kind in ("asset", "cost_event", "archived_record", "imported_asset", "bundle_import_receipt")}
    repeated = publish(destination, current, checked, "repeat_import")
    assert repeated["receipt_id"] == published["receipt_id"]
    assert {kind: len(destination.store.list(kind)) for kind in counts} == counts
    _, again = export(destination, current, roots, "reexport")
    with Reader(portable) as original, Reader(again) as returned:
        assert returned.manifest == original.manifest
        assert list(returned.records()) == list(original.records())
        returned.verify()


def test_shared_prefix_physical_costs_and_unknown_history_survive_repeated_import(tmp_path):
    source, owner, _ = campaign(tmp_path / "source")
    for producer, count in (("prefix-work", 5), ("refine-work", 4)):
        for ordinal in range(count):
            source.assets.record_cost(CostEvent(id=f"cost_{producer}_{ordinal}", campaign_id=owner, source_id=producer,
                ordinal=ordinal, category="evaluation", quantities={"evaluation_requests": 1, "worker_seconds": .1}, created_at="historical"))
    def asset(identity, dependencies, costs, provenance="complete"):
        return source.assets.publish(Asset(id=identity, campaign_id=owner, kind="solution", title=identity,
            dependency_ids=dependencies, costs=costs, cost_provenance=provenance, exposure_status="unknown",
            payload={"candidate": [0, 0]}, authority="researcher", created_at="historical"))
    asset("prefix", [], [CostSlice(source_id="prefix-work", stop=5)])
    asset("first", ["prefix"], [CostSlice(source_id="refine-work", stop=2)])
    asset("second", ["prefix"], [CostSlice(source_id="refine-work", start=2, stop=4)])
    asset("unknown", ["prefix"], [CostSlice(source_id="unrecorded", stop=1)], "unknown")
    _, path = export(source, owner, ["first", "second", "unknown"])
    destination, current, _ = campaign(tmp_path / "destination")
    checked = inspect(destination, current, path)
    assert checked["summary"]["provenance"] == "partial"
    publish(destination, current, checked)
    assert len(destination.store.list("cost_event")) == 9
    assert [destination.assets.attributed_costs([identity])["quantities"]["evaluation_requests"]["total"]
            for identity in ["first", "second"]] == [7, 7]
    assert destination.assets.attributed_costs(["first", "second"])["quantities"]["evaluation_requests"]["total"] == 9
    assert destination.assets.attributed_costs(["unknown"])["quantities"]["evaluation_requests"]["total"] is None
    assert destination.assets.actual_costs(current)["event_count"] == 0
    assert checked["summary"]["accounting"] == "partial"
    _, returned = export(destination, current, ["first", "second", "unknown"], "export_partial")
    with Reader(path) as original, Reader(returned) as repeated:
        assert original.manifest == repeated.manifest
    _, smaller = export(source, owner, ["first"], "export_first")
    publish(destination, current, inspect(destination, current, smaller, "inspect_first"), "publish_first")
    assert len(destination.store.list("cost_event")) == 9
    assert destination.assets.attributed_costs(["first", "second"])["quantities"]["evaluation_requests"]["total"] == 9


def test_executable_import_never_replays_production_and_recovers_cross_service_publication(tmp_path):
    source, library, owner, task = campaign_fixture(tmp_path / "source", spec=specification())
    source.implementations.client = Client(library)
    trial = source.create_trial(TrialInput(campaign_id=owner["id"], task_id=task["id"], algorithm="coordinate", max_steps=3, wall_seconds=10))
    finish(source, trial)
    trial = source.store.get(trial["id"], "trial")
    before = source.assets.attributed_costs(trial["output_asset_ids"])
    _, path = export(source, owner["id"], trial["output_asset_ids"])
    destination, current, _ = campaign(tmp_path / "destination")
    imported_library = ImplementationService(tmp_path / "imported-library", adapter_factory=no_model)
    client = Client(imported_library)
    destination.implementations.client = client
    checked = inspect(destination, current, path)
    assert not imported_library.store.list("implementation_version")
    assert not imported_library.store.list("implementation_job")
    original_publish = client.publish_import
    def lost_reply(identity):
        original_publish(identity)
        raise LibraryUnavailable("Injected lost library publication reply")
    client.publish_import = lost_reply
    accepted = destination.commands.execute(Command(id="publish_once", campaign_id=current,
        expected_revision=destination.store.get(current, "campaign")["version"], operation="bundle.publish",
        payload={"inspection_id": checked["id"]}))
    assert not destination.store.list("asset")
    assert len(imported_library.store.list("library_import_receipt")) == 1
    assert not imported_library.store.list("implementation_job")
    client.publish_import = original_publish
    from optimization_framework.execution.service import Workspace
    restarted = Workspace(destination.directory, implementation_client=client)
    restarted.dispatch_outbox()
    operation = restarted.store.get(accepted["outcome"]["operation_id"], "bundle_operation")
    assert operation["status"] == "completed", operation
    assert restarted.assets.attributed_costs(trial["output_asset_ids"]) == before
    version_id = trial["evaluator_version_id"]
    version = imported_library.version(version_id)
    assert version["status"] == "validation_required"
    assert restarted.store.get(version_id, "implementation_cache")["status"] == "validation_required"
    assert availability(imported_library, version_id)["status"] == "unavailable"
    with pytest.raises(ValueError, match="not available"):
        imported_library.version(version_id, ready=True)
    assert imported_library.artifact(version_id, ready=False)["production_jobs"]
    assert not imported_library.store.list("implementation_job")
    assert resolve(imported_library, version_id, resolution_request(version))["status"] == "available"
    recheck = imported_library.run_job(imported_library.submit(check_request(version))["id"])
    assert recheck["status"] == "completed", recheck
    assert imported_library.version(version_id, ready=True)["artifact_digest"] == version["artifact_digest"]
    assert len(imported_library.store.list("implementation_job")) == 1  # Only the explicitly requested recheck.


def test_conflicting_cost_position_fails_inspection_before_any_record_is_published(tmp_path):
    source, owner, _ = campaign(tmp_path / "source")
    source.assets.record_cost(CostEvent(id="physical_cost", campaign_id=owner, source_id="original_work", ordinal=0,
        category="evaluation", quantities={"evaluation_requests": 3}, created_at="historical"))
    source.assets.publish(Asset(id="result", campaign_id=owner, kind="solution", title="Result",
        costs=[CostSlice(source_id="original_work", stop=1)], authority="researcher", created_at="historical"))
    _, path = export(source, owner, ["result"])
    destination, current, _ = campaign(tmp_path / "destination")
    destination.assets.record_cost(CostEvent(id="conflicting_cost", campaign_id=current, source_id="original_work", ordinal=0,
        category="evaluation", quantities={"evaluation_requests": 5}, created_at="historical"))
    with path.open("rb") as stream:
        upload = destination.bundles.upload(stream)
    accepted = destination.commands.execute(Command(id="conflict", campaign_id=current, expected_revision=1,
        operation="bundle.inspect", payload={"upload_id": upload["upload_id"]}))
    operation = destination.store.get(accepted["outcome"]["operation_id"], "bundle_operation")
    assert operation["status"] == "failed" and "physical cost position" in operation["error"]
    assert not destination.store.list("asset") and not destination.store.list("archived_record")
    assert len(destination.store.list("cost_event")) == 1
    assert len(destination.store.list("manager_issue", current)) == 1


def test_bundle_members_and_blob_hashes_are_verified_without_extracting_paths(tmp_path):
    source, owner, _ = campaign(tmp_path / "source")
    blob = source.assets.artifacts.put_bytes(b"original evidence")
    source.assets.publish(Asset(id="artifact", campaign_id=owner, kind="dataset", title="Captured bytes", artifacts=[blob],
        authority="researcher", created_at="historical"))
    _, path = export(source, owner, ["artifact"])
    with zipfile.ZipFile(path) as original:
        members = {item.filename: original.read(item) for item in original.infolist()}
    def changed(name, value, target):
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
            for member, data in members.items():
                archive.writestr(member, value if member == name else data)
    corrupted = tmp_path / "corrupt.zip"
    changed("blobs/" + blob.sha256, b"modified evidence", corrupted)
    with Reader(corrupted) as reader, pytest.raises(ValueError, match="blob content"):
        reader.verify()
    unsafe = tmp_path / "unsafe.zip"
    shutil.copyfile(path, unsafe)
    with zipfile.ZipFile(unsafe, "a") as archive:
        archive.writestr("../escaped", b"must never be extracted")
    with pytest.raises(ValueError, match="members differ"):
        Reader(unsafe)
    assert not (tmp_path / "escaped").exists()


def test_archived_protected_exposure_remains_enforced_and_releases_travel_with_results(tmp_path):
    source, owner, _ = campaign(tmp_path / "source")
    producer = {"id": "trial_sealed", "campaign_id": owner, "status": "completed", "task_split": "confirmation",
                "protected_cohort_id": "protocol_sealed"}
    source.store.put("trial", producer)
    asset = source.assets.publish(Asset(id="sealed_result", campaign_id=owner, kind="solution", title="Sealed result",
        producer_id=producer["id"], exposure_status="known", authority="researcher", created_at="historical"))
    with pytest.raises(ValueError, match="result release"):
        source.assets.check_export_exposure(asset)
    release = source.store.put_immutable("confirmation_release", {"id": "release_sealed", "campaign_id": owner,
        "protocol_id": "protocol_sealed", "trial_ids": [producer["id"]]})
    _, original = export(source, owner, [asset["id"]])
    def remove_release(manifest, records):
        keys = {record.reference.key for record in records if record.reference.id == release["id"]}
        manifest["records"] = [record for record in manifest["records"] if record["id"] != release["id"]]
        manifest["edges"] = [edge for edge in manifest["edges"] if edge["source"] not in keys and edge["dependency"] not in keys]
        return manifest, [record for record in records if record.reference.key not in keys]
    missing = rewrite_bundle(original, tmp_path / "without-release.zip", remove_release)
    destination, current, _ = campaign(tmp_path / "destination")
    publish(destination, current, inspect(destination, current, missing))
    imported = destination.store.get(asset["id"], "asset")
    assert not destination.store.list("trial")
    with pytest.raises(ValueError, match="before release"):
        destination.assets.check_input_exposure(imported)
    with pytest.raises(ValueError, match="result release"):
        destination.assets.check_export_exposure(imported)
    publish(destination, current, inspect(destination, current, original, "inspect_release"), "publish_release")
    destination.assets.check_input_exposure(imported)
    destination.assets.check_export_exposure(imported)
    assert history.releases(destination.store, owner) == [release]
    # Following archived references also works for newly published descendants.
    descendant = destination.assets.publish(Asset(id="descendant", campaign_id=current, kind="solution", title="Descendant",
        producer_id=producer["id"], dependency_ids=[asset["id"]], authority="researcher", created_at="now"))
    export(destination, current, [descendant["id"]], "export_descendant")


def test_workspace_publication_rolls_back_and_retries_after_a_process_failure(tmp_path, monkeypatch):
    source, owner, _ = campaign(tmp_path / "source")
    source.assets.record_cost(CostEvent(id="charge", campaign_id=owner, source_id="source_work", ordinal=0,
        category="evaluation", quantities={"evaluation_requests": 1}, created_at="historical"))
    source.assets.publish(Asset(id="answer", campaign_id=owner, kind="solution", title="Answer",
        costs=[CostSlice(source_id="source_work", stop=1)], cost_provenance="complete", authority="researcher", created_at="historical"))
    _, path = export(source, owner, ["answer"])
    destination, current, _ = campaign(tmp_path / "destination")
    inspection = inspect(destination, current, path)
    put = destination.store.put_immutable
    def fail_before_receipt(kind, *args, **kwargs):
        if kind == "bundle_import_receipt":
            raise RuntimeError("Injected process loss before the workspace publication receipt")
        return put(kind, *args, **kwargs)
    monkeypatch.setattr(destination.store, "put_immutable", fail_before_receipt)
    envelope = Command(id="publish_before_failure", campaign_id=current,
        expected_revision=destination.store.get(current, "campaign")["version"], operation="bundle.publish",
        payload={"inspection_id": inspection["id"]})
    accepted = destination.commands.execute(envelope)
    for kind in ("asset", "cost_event", "archived_record", "imported_asset", "bundle_import_receipt"):
        assert not destination.store.list(kind)
    assert destination.store.get(accepted["outcome"]["operation_id"], "bundle_operation")["status"] == "publishing"
    from optimization_framework.execution.service import Workspace
    restarted = Workspace(destination.directory)
    restarted.dispatch_outbox()
    assert restarted.commands.execute(envelope) == accepted
    assert len(restarted.store.list("cost_event")) == 1
    assert len(restarted.store.list("bundle_import_receipt")) == 1
    assert restarted.assets.attributed_costs(["answer"])["quantities"]["evaluation_requests"]["total"] == 1


def test_missing_blob_declarations_are_required_and_invalid_json_is_a_scoped_failure(tmp_path):
    source, owner, _ = campaign(tmp_path / "source")
    blob = source.assets.artifacts.put_bytes(b"historical bytes")
    source.assets.publish(Asset(id="dataset", campaign_id=owner, kind="dataset", title="Dataset", artifacts=[blob],
        authority="researcher", created_at="historical"))
    _, path = export(source, owner, ["dataset"])
    def silently_drop_blob(manifest, records):
        manifest["blobs"] = []
        return manifest, records
    missing = rewrite_bundle(path, tmp_path / "silent-missing.zip", silently_drop_blob)
    invalid = tmp_path / "invalid-json.zip"
    with zipfile.ZipFile(invalid, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("manifest.json", "[]")
    destination, current, _ = campaign(tmp_path / "destination")
    for index, archive in enumerate((missing, invalid)):
        with archive.open("rb") as stream:
            upload = destination.bundles.upload(stream)
        accepted = destination.commands.execute(Command(id=f"inspect_invalid_{index}", campaign_id=current,
            expected_revision=destination.store.get(current, "campaign")["version"], operation="bundle.inspect",
            payload={"upload_id": upload["upload_id"]}))
        operation = destination.store.get(accepted["outcome"]["operation_id"], "bundle_operation")
        assert operation["status"] == "failed", operation
    assert not destination.store.list("asset") and not destination.store.list("archived_record")
    assert len(destination.store.list("manager_issue", current)) == 2


def test_library_import_checks_conflicting_evidence_and_preserves_revocation_and_missing_receipts(tmp_path):
    from test_framework_revalidation import publish as build, MockReviewer
    source = ImplementationService(tmp_path / "source", adapter_factory=MockReviewer)
    version = build(source)
    original = exchange.export(source, version["id"])
    request = {"bundle_digest": "1" * 64, "records": original["records"], "artifacts": original["artifacts"]}
    destination = ImplementationService(tmp_path / "destination", adapter_factory=no_model)
    conflicting = copy.deepcopy(request)
    duplicate = copy.deepcopy(next(record for record in conflicting["records"] if record["reference"]["kind"] == "implementation_version"))
    duplicate["data"]["validation_report"]["passed"] = False
    duplicate["reference"]["content_digest"] = content_hash(duplicate["data"])
    conflicting["records"].append(duplicate)
    with pytest.raises(ValueError, match="validation-report identity"):
        exchange.inspect(destination, conflicting)
    assert not destination.store.list("library_import")
    checked = exchange.inspect(destination, request)
    exchange.publish(destination, checked["id"])
    destination.revoke(version["id"], "Locally withdrawn after evidence review")
    later = exchange.inspect(destination, {**request, "bundle_digest": "2" * 64})
    exchange.publish(destination, later["id"])
    assert destination.version(version["id"])["status"] == "revoked"
    assert not destination.store.list("implementation_job")
    with source.store.connection() as db:
        db.execute("DELETE FROM records WHERE kind='implementation_job'")
    readable = source.artifact(version["id"], ready=False)
    assert readable["artifact"] == original["artifacts"][0]["artifact"]
    assert readable["production_jobs"] == [] and readable["missing_production_job_ids"] == [version["job_id"]]
    exported = exchange.export(source, version["id"])
    assert exported["missing"][0]["id"] == version["job_id"]
    assert exported["artifacts"] == original["artifacts"]
