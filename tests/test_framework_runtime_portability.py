"""Portable executable identities, explicit local resolution and delivery recovery."""
import copy
import json
from pathlib import Path
import shutil

from fastapi.testclient import TestClient
import pytest

from optimization_framework.contracts.commands import Command
from optimization_framework.contracts.requests import CampaignUpdate, TrialInput
from optimization_framework.evaluation.generated import PublishedEvaluator
from optimization_framework.execution.service import Workspace
from optimization_framework.implementations.api import create_app
from optimization_framework.implementations.models import CapabilityUnavailable, digest
from optimization_framework.implementations.runtime import (
    bundle_runtime_root, prepare_runtime, resolve_runtime, verify_runtime,
)
from optimization_framework.implementations.runtime_resolution import ResolutionRequest, availability, receipt, resolve
from optimization_framework.implementations.service import ImplementationService
from optimization_framework.storage.sqlite import atomic_json
from test_framework_evaluators import MockReviewer, specification
from test_framework_revalidation import campaign_fixture, no_model, publish


def resolution_request(version, key="resolve"):
    return ResolutionRequest(workspace_id="workspace", campaign_id="campaign", idempotency_key=key,
                             runtime_digest=version["runtime_digest"])


def test_identical_executables_in_different_libraries_have_the_same_identity(tmp_path):
    bundles = []
    for name in ("first", "second"):
        service = ImplementationService(tmp_path / name, adapter_factory=MockReviewer)
        version = publish(service)
        bundles.append(service.artifact(version["id"]))
    first, second = bundles
    assert first["runtime_root"] != second["runtime_root"]
    assert first["artifact"] == second["artifact"]
    assert first["version"]["id"] == second["version"]["id"]
    assert first["version"]["runtime_digest"] == second["version"]["runtime_digest"]
    assert str(tmp_path) not in json.dumps(first["artifact"])
    assert "executable" not in first["artifact"]["runtime"]
    assert first["artifact"]["runtime"]["stdlib_files"]
    assert all(not Path(name).is_absolute() for name in first["artifact"]["runtime"]["libraries"])


def test_relocated_library_is_inspectable_before_resolution_and_runs_afterward(tmp_path, monkeypatch):
    original = ImplementationService(tmp_path / "original", adapter_factory=MockReviewer)
    version = publish(original)
    source_bundle = original.artifact(version["id"])
    # This is a copied-library compatibility check, not the future bundle importer.
    shutil.copytree(original.directory, tmp_path / "relocated", ignore=shutil.ignore_patterns("runtimes"))
    original.directory.rename(tmp_path / "retained-original")
    relocated = ImplementationService(tmp_path / "relocated", adapter_factory=no_model)
    original_jobs = relocated.store.list("implementation_job")
    assert availability(relocated, version["id"])["status"] == "unavailable"
    assert relocated.version(version["id"]) == version
    assert relocated.artifact(version["id"], ready=False)["artifact"] == source_bundle["artifact"]
    with monkeypatch.context() as patch:
        # Runtime inspection/resolution cannot invoke any candidate initializer.
        patch.setattr("optimization_framework.implementations.runtime.PackageProcess.__init__", no_model)
        result = resolve(relocated, version["id"], resolution_request(version))
    assert result["status"] == "available", result
    assert result["costs"]["model_calls"] == result["costs"]["downloaded_bytes"] == 0
    assert relocated.store.list("implementation_job") == original_jobs
    assert relocated.version(version["id"]) == version
    bundle = relocated.artifact(version["id"])
    assert bundle["artifact"] == source_bundle["artifact"]
    assert bundle_runtime_root(bundle).is_relative_to(relocated.directory)
    with monkeypatch.context() as patch:
        patch.setattr("optimization_framework.implementations.runtime_resolution.resolve_runtime", no_model)
        assert resolve(relocated, version["id"], resolution_request(version)) == result
    assert len(relocated.store.list("runtime_resolution_receipt")) == 1
    adapter = PublishedEvaluator(bundle, relocated.directory / "artifacts" / version["artifact_digest"] / "package")
    evaluator = adapter.evaluator(adapter.resolve({}))
    try:
        assert evaluator.evaluate([1.5, 1.5]).objectives == {"energy": -2}
    finally:
        evaluator.close()


def test_runtime_integrity_includes_bytecode_and_repair_preserves_the_damaged_copy(tmp_path):
    root, manifest = prepare_runtime(tmp_path, {})
    injected = root / "stdlib" / "__pycache__" / "json.cpython-312.pyc"
    injected.parent.mkdir()
    injected.write_bytes(b"unrecorded executable input")
    with pytest.raises(ValueError, match="standard library changed"):
        verify_runtime(root, manifest)
    repaired = resolve_runtime(tmp_path, manifest)
    assert repaired != root and injected.exists()
    assert not (repaired / "stdlib" / "__pycache__").exists()
    verify_runtime(repaired, manifest)
    dependency = repaired / "site-packages" / "unrecorded.pyc"
    dependency.write_bytes(b"unrecorded dependency")
    with pytest.raises(ValueError, match="runtime files changed"):
        verify_runtime(repaired, manifest)


def test_legacy_runtime_identity_and_reader_remain_unchanged(tmp_path):
    root, modern = prepare_runtime(tmp_path, {})
    binding = verify_runtime(root, modern)
    legacy = {key: modern[key] for key in ("protocol", "python", "executable_hash", "dependencies", "files")}
    legacy.update(executable=binding["executable"], stdlib=binding["stdlib"],
        libraries={path: modern["libraries"][name] for name, paths in binding["libraries"].items() for path in paths})
    legacy["digest"] = digest(legacy)
    original = copy.deepcopy(legacy)
    verify_runtime(root, legacy)
    assert legacy == original
    assert bundle_runtime_root({"artifact": {"runtime_root": str(root)}}) == root
    relocated = resolve_runtime(tmp_path / "another-location", legacy)
    resolved = verify_runtime(relocated, legacy)
    assert resolved["conversion"]["original_runtime_digest"] == original["digest"]
    assert resolved["conversion"]["limitations"]
    assert legacy == original and relocated.is_relative_to(tmp_path / "another-location")
    assert json.loads((relocated / "runtime.json").read_text()) == original
    (relocated / "stdlib" / "unrecorded.py").write_text("changed runtime")
    with pytest.raises(ValueError, match="standard library changed"):
        verify_runtime(relocated, legacy)


@pytest.mark.parametrize("field", ["executable_hash", "files", "dependencies", "libraries"])
def test_legacy_relocation_cannot_infer_content_compatibility_from_dependency_names(tmp_path, field):
    root, modern = prepare_runtime(tmp_path / "source", {})
    binding = verify_runtime(root, modern)
    legacy = {key: modern[key] for key in ("protocol", "python", "executable_hash", "dependencies", "files")}
    legacy.update(executable=binding["executable"], stdlib=binding["stdlib"],
        libraries={path: modern["libraries"][name] for name, paths in binding["libraries"].items() for path in paths})
    legacy[field] = "0" * 64 if field == "executable_hash" else {"missing": "0" * 64}
    legacy["digest"] = digest(legacy)
    with pytest.raises((CapabilityUnavailable, ValueError)):
        resolve_runtime(tmp_path / "destination", legacy)
    assert not list((tmp_path / "destination").glob("runtime_conversion_*"))


def test_mismatched_runtime_is_unavailable_without_changing_correctness_evidence(tmp_path, monkeypatch):
    service = ImplementationService(tmp_path, adapter_factory=MockReviewer)
    version = publish(service)
    location = tmp_path / "artifacts" / version["artifact_digest"] / "runtime-location.json"
    location.unlink()
    monkeypatch.setattr("optimization_framework.implementations.runtime.platform_identity", lambda: {"system": "incompatible"})
    result = resolve(service, version["id"], resolution_request(version))
    assert result["status"] == "unavailable" and "platform" in result["reason"]
    assert service.version(version["id"]) == version
    assert not location.exists()
    assert len(service.store.list("implementation_job")) == 1


def test_corrupt_local_binding_does_not_hide_history_or_prevent_explicit_repair(tmp_path):
    service = ImplementationService(tmp_path, adapter_factory=MockReviewer)
    version = publish(service)
    artifact = service.artifact(version["id"])["artifact"]
    location = tmp_path / "artifacts" / version["artifact_digest"] / "runtime-location.json"
    location.write_text("{broken local binding")
    assert service.artifact(version["id"], ready=False)["artifact"] == artifact
    assert service.version(version["id"]) == version
    assert availability(service, version["id"])["status"] == "unavailable"
    result = resolve(service, version["id"], resolution_request(version))
    assert result["status"] == "available", result
    assert availability(service, version["id"])["status"] == "available"
    assert service.version(version["id"]) == version


def test_interrupted_resolution_recovers_an_immutable_receipt_with_unknown_elapsed_work(tmp_path, monkeypatch):
    service = ImplementationService(tmp_path, adapter_factory=MockReviewer)
    version = publish(service)
    request = resolution_request(version)
    def interrupt_after_binding(path, value):
        atomic_json(path, value)
        raise SystemExit("Injected interruption after binding, before receipt commit")
    with monkeypatch.context() as patch:
        patch.setattr("optimization_framework.implementations.runtime_resolution.atomic_json", interrupt_after_binding)
        with pytest.raises(SystemExit):
            resolve(service, version["id"], request)
    assert receipt(service, request.workspace_id, request.idempotency_key) is None
    restarted = ImplementationService(tmp_path, adapter_factory=no_model)
    result = resolve(restarted, version["id"], request)
    assert result["status"] == "available" and result["recovered_after_interruption"]
    assert result["costs"]["elapsed_seconds"] is None
    assert result["costs"]["model_calls"] == 0
    assert restarted.version(version["id"]) == version
    with pytest.raises(ValueError, match="immutable"):
        restarted.store.put("runtime_resolution_receipt", {**result, "status": "unavailable"})
    with pytest.raises(ValueError, match="different request"):
        resolve(restarted, version["id"], request.model_copy(update={"campaign_id": "another"}))


def test_runtime_resolution_http_auth_and_receipt_replay(tmp_path):
    app = create_app(tmp_path, token="test-secret", start_workers=False, adapter_factory=MockReviewer)
    service = app.state.service
    version = publish(service)
    request = resolution_request(version).model_dump(mode="json")
    path = f"/v1/versions/{version['id']}/runtime"
    with TestClient(app) as client:
        assert client.get(path).status_code == 401
        assert client.post(path + "/resolve", json=request).status_code == 401
        headers = {"Authorization": "Bearer test-secret"}
        assert client.get(path, headers=headers).json()["status"] == "available"
        result = client.post(path + "/resolve", json=request, headers=headers)
        assert result.status_code == 200, result.text
        assert client.post(path + "/resolve", json=request, headers=headers).json() == result.json()
        assert client.get("/v1/runtime-resolutions", params={"workspace_id": "workspace", "idempotency_key": "resolve"},
                          headers=headers).json() == result.json()


def test_command_recovers_a_lost_runtime_reply_and_rebinds_the_same_frozen_experiment(tmp_path):
    from test_framework_generated_recipes import finish
    workspace, service, campaign, task = campaign_fixture(tmp_path, spec=specification())
    version = service.version(workspace.evaluators.binding(task)["version_id"])
    trial = workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=task["id"],
        algorithm="coordinate", max_steps=3, wall_seconds=10))
    frozen_bundle = workspace.job_dir(trial["id"]) / "evaluator" / "bundle.json"
    original_bytes = frozen_bundle.read_bytes()
    original_version = copy.deepcopy(version)
    site = bundle_runtime_root(service.artifact(version["id"])) / "site-packages"
    site.rename(site.with_name("retained-damaged-installation"))
    client = workspace.implementations.client
    calls = []
    def lose_reply(identity, body):
        calls.append(body)
        resolve(service, identity, body)
        raise ValueError("Injected lost acknowledgement")
    client.resolve_runtime = lose_reply
    client.runtime_resolution = lambda owner, key: receipt(service, owner, key)
    command = Command(id="resolve_once", campaign_id=campaign["id"], expected_revision=campaign["version"],
                      operation="implementation.resolve_runtime", payload={"version_id": version["id"]})
    accepted = workspace.commands.execute(command)
    effect_id = accepted["outcome"]["effect_id"]
    assert workspace.store.get(effect_id, "outbox")["status"] == "pending"
    assert len(service.store.list("runtime_resolution_receipt")) == 1
    workspace.update_campaign(campaign["id"], CampaignUpdate(name="Later campaign guidance"))
    restarted = Workspace(workspace.directory, implementation_client=client)
    restarted.dispatch_outbox()
    assert restarted.store.get(effect_id, "outbox")["status"] == "completed"
    assert len(calls) == 1
    assert restarted.commands.execute(command) == accepted
    assert len(restarted.store.list("runtime_resolution_receipt")) == 1
    restarted.evaluators.check_launch(trial)
    assert frozen_bundle.read_bytes() == original_bytes
    assert service.version(version["id"]) == original_version
    completed = finish(restarted, trial)
    assert completed["status"] == "completed", completed
    assert completed["id"] == trial["id"]
    assert len(service.store.list("implementation_job")) == 1
    overhead = [row for row in restarted.store.list("cost_event", campaign["id"]) if row["source_id"].startswith("runtime:")]
    assert len(overhead) == 1 and overhead[0]["quantities"]["worker_seconds"] > 0
    graph = restarted.assets.contributions(completed["output_asset_ids"])
    assert graph["intervals"][overhead[0]["source_id"]] == [[0, 1]]
    assert completed["operational_cost_asset_ids"]
    restarted.dispatch_outbox()
    assert [row for row in restarted.store.list("cost_event", campaign["id"]) if row["source_id"].startswith("runtime:")] == overhead
