"""Independent rechecks run real packages and never invoke a model or rebuild."""
import copy
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from optimization_framework.implementations.api import create_app
from optimization_framework.implementations.evaluator_validation import current
from optimization_framework.implementations.models import JobRequest, Package, RevalidationRequest, parse_job_request
from optimization_framework.implementations.service import ImplementationService, JobInterrupted
from test_framework_evaluators import MockReviewer, SOURCE, contract_specification, package, request, specification
from test_framework_packages import SOURCE as OPTIMIZER_SOURCE, spec as optimizer_specification


def publish(service, *, spec=None, source=None):
    job = service.run_job(service.submit(request(spec=spec or specification(), package=package(source or SOURCE)))["id"])
    assert job["status"] == "completed", job
    return service.version(job["version_id"])


def check_request(version, *, key="revalidate", **changes):
    kind = version.get("kind", "optimizer")
    return RevalidationRequest(**{**dict(workspace_id="workspace", campaign_id="campaign",
        idempotency_key=key, grant_id="grant_" + key, version_id=version["id"], compute_seconds=30,
        checks={"kind": kind, "rationale": "Independent check of this exact published executable"}), **changes})


def no_model(**kwargs):
    raise AssertionError("Revalidation must not create a model adapter")


def test_contract_only_version_gains_numerical_evidence_without_changing_its_executable(tmp_path, monkeypatch):
    service = ImplementationService(tmp_path, adapter_factory=MockReviewer)
    original = publish(service, spec=contract_specification())
    bundle = service.artifact(original["id"])
    artifact_file = tmp_path / "artifacts" / original["artifact_digest"] / "artifact.json"
    saved = artifact_file.read_bytes()
    service.adapter_factory = no_model
    monkeypatch.setattr("optimization_framework.implementations.service.prepare_runtime", no_model)
    value = check_request(original, checks={"kind": "evaluator", "rationale": "Add analytical references independently of the supplied source",
        "correctness_cases": [case.model_dump() for case in specification().correctness_cases]})
    job = service.submit(value)
    assert service.submit(value) == job
    with pytest.raises(ValueError, match="Idempotency"):
        service.submit(value.model_copy(update={"compute_seconds": 40}))
    result = service.run_job(job["id"])
    assert result["status"] == "completed", result
    version = service.version(original["id"], ready=True)
    assert version["id"] == original["id"] and version["spec"] == original["spec"]
    assert version["package_hashes"] == original["package_hashes"]
    assert version["artifact_digest"] == original["artifact_digest"] and version["runtime_digest"] == original["runtime_digest"]
    assert artifact_file.read_bytes() == saved and service.artifact(version["id"])["artifact"] == bundle["artifact"]
    assert len(service.store.list("implementation_version")) == 1
    assert current(version) and not current(original)
    assert version["status"] == "validated" and version["spec"]["validation_mode"] == "contract_only"
    assert version["validation_history"] == [original["validation_report"]]
    report = version["validation_report"]
    assert report["kind"] == "evaluator_correctness" and report["passed"]
    assert not report["model_review_performed"] and report["review_basis"] == original["validation_report"]["id"]
    assert report["costs"] == {"evaluation_requests": 40, "solver_executions": 40}
    assert not result["usage"] and result["compute_seconds"] > 0 and result["accounting_final"]
    assert len(service.store.list("implementation_validation")) == 2
    assert len(service.artifact(version["id"])["production_jobs"]) == 2
    assert service.run_job(job["id"]) == result


def test_counterexample_blocks_use_and_cannot_be_hidden_by_rechecking_a_smaller_scope(tmp_path):
    service = ImplementationService(tmp_path, adapter_factory=MockReviewer)
    bad_source = SOURCE.replace('value = round(value,', 'value = round(value + (1 if candidate == [2, 2] else 0),')
    original = publish(service, source=bad_source)
    service.adapter_factory = no_model
    checks = {"kind": "evaluator", "rationale": "Independent counterexample outside the initial fixture set", "correctness_cases": [
        {"name": "new corner", "basis": "2*(2-1.5)^2-2 = -1.5", "candidate": [2, 2], "objectives": {"energy": -1.5}}]}
    result = service.run_job(service.submit(check_request(original, checks=checks))["id"])
    assert result["status"] == "failed", result
    version = service.version(original["id"])
    assert version["status"] == "validation_failed" and not current(version, numerical=False)
    assert version["validation_history"] == [original["validation_report"]]
    assert "independent fixture new corner" in version["validation_report"]["error"]
    with pytest.raises(ValueError, match="not available"):
        service.artifact(version["id"])
    repeat = service.submit(check_request(version, key="smaller"))
    assert len(repeat["validation_plan"]["spec"]["correctness_cases"]) == 5
    repeated = service.run_job(repeat["id"])
    assert repeated["status"] == "failed" and repeated["attempts"][0]["report"]["costs"]["evaluation_requests"] == 5
    rewritten = copy.deepcopy(checks)
    rewritten["correctness_cases"][0]["objectives"]["energy"] = -.5
    with pytest.raises(ValueError, match="different frozen fixtures"):
        service.submit(check_request(version, key="rewrite", checks=rewritten))
    assert len(service.store.list("implementation_job")) == 3


def test_revalidation_interrupt_retains_partial_cost_and_resumes_the_same_job(tmp_path, monkeypatch):
    from optimization_framework.implementations.evaluator_runtime import PackageEvaluator
    service = ImplementationService(tmp_path, adapter_factory=MockReviewer)
    original = publish(service)
    service.adapter_factory = no_model
    job = service.submit(check_request(original))
    evaluate = PackageEvaluator.evaluate
    def stop_after_one(self, candidate):
        value = evaluate(self, candidate)
        service.stopping.set()
        return value
    with monkeypatch.context() as patch:
        patch.setattr(PackageEvaluator, "evaluate", stop_after_one)
        stopped = service.run_job(job["id"])
    assert stopped["status"] == "interrupted", stopped
    assert stopped["attempts"][0]["report"]["costs"]["evaluation_requests"] == 1
    assert not stopped["attempts"][0]["complete_report"]
    assert service.version(original["id"]) == original
    service = ImplementationService(tmp_path, adapter_factory=no_model)
    service.control(job["id"], "resume")
    complete = service.run_job(job["id"])
    assert complete["status"] == "completed" and len(complete["attempts"]) == 2
    assert complete["compute_seconds"] >= stopped["compute_seconds"]
    assert sum(attempt["report"]["costs"]["evaluation_requests"] for attempt in complete["attempts"]) == 21
    from optimization_framework.assets.catalog import AssetCatalog
    from optimization_framework.assets.service_costs import record_implementation_job
    from optimization_framework.storage.sqlite import Store
    catalog = AssetCatalog(Store(tmp_path / "costs"))
    first = record_implementation_job(catalog, complete)
    assert record_implementation_job(catalog, complete) == first
    events = catalog.store.list("cost_event")
    assert len(events) == 1 and events[0]["quantities"]["evaluation_requests"] == 21
    assert events[0]["quantities"]["model_calls"] == 0


def test_completed_report_survives_interruption_before_publication_without_rerunning_checks(tmp_path, monkeypatch):
    import optimization_framework.implementations.revalidation as revalidation
    service = ImplementationService(tmp_path, adapter_factory=MockReviewer)
    original = publish(service)
    service.adapter_factory = no_model
    job = service.submit(check_request(original))
    def interrupt(*args):
        raise JobInterrupted("Injected interruption after saving the complete report")
    with monkeypatch.context() as patch:
        patch.setattr(revalidation, "publish", interrupt)
        stopped = service.run_job(job["id"])
    assert stopped["status"] == "interrupted" and stopped["attempts"][0]["complete_report"]
    service = ImplementationService(tmp_path, adapter_factory=no_model)
    service.control(job["id"], "resume")
    with monkeypatch.context() as patch:
        patch.setattr("optimization_framework.implementations.evaluator_validation.validate_package", no_model)
        complete = service.run_job(job["id"])
    assert complete["status"] == "completed"
    assert complete["attempts"] == stopped["attempts"]
    assert service.version(original["id"])["validation_report"]["id"] == stopped["attempts"][0]["report"]["id"]


def test_optimizer_revalidation_reuses_original_behavior_and_semantic_evidence(tmp_path, monkeypatch):
    service = ImplementationService(tmp_path, adapter_factory=MockReviewer)
    submitted = service.submit(request(spec=optimizer_specification(), package=Package(files=[{"path": "optimizer.py", "content": OPTIMIZER_SOURCE}])))
    built = service.run_job(submitted["id"])
    assert built["status"] == "completed", built
    original = service.version(built["version_id"])
    service.adapter_factory = no_model
    monkeypatch.setattr("optimization_framework.implementations.service.prepare_runtime", no_model)
    checked = service.run_job(service.submit(check_request(original))["id"])
    assert checked["status"] == "completed", checked
    latest = service.version(original["id"], ready=True)
    assert latest["spec"] == original["spec"] and latest["runtime_digest"] == original["runtime_digest"]
    assert latest["validation_report"]["review"] == original["validation_report"]["review"]
    assert not checked["usage"]


def test_http_revalidation_is_authenticated_idempotent_and_preserves_legacy_envelopes(tmp_path):
    app = create_app(tmp_path, token="test", start_workers=False, adapter_factory=MockReviewer)
    service = app.state.service
    original = publish(service)
    service.adapter_factory = no_model
    payload = check_request(original).model_dump()
    path = f"/v1/versions/{original['id']}/validations"
    with TestClient(app) as client:
        assert client.post(path, json=payload).status_code == 401
        headers = {"Authorization": "Bearer test"}
        submitted = client.post(path, json=payload, headers=headers)
        assert submitted.status_code == 202, submitted.text
        assert client.post(path, json=payload, headers=headers).json() == submitted.json()
        assert client.post(path, json={**payload, "version_id": "another"}, headers=headers).status_code == 409
        checked = service.run_job(submitted.json()["id"])
        assert checked["status"] == "completed", checked
        history = client.get(path, headers=headers).json()
        assert len(history) == 2 and all(value["version_id"] == original["id"] for value in history)
        legacy = request(idempotency_key="legacy-check", grant_id="legacy-grant", package=None).model_dump()
        translated = client.post(path, json=legacy, headers=headers)
        assert translated.status_code == 202, translated.text
        assert translated.json()["request"]["max_calls"] == 0
        assert translated.json()["request"]["operation"] == "revalidation"
        assert service.run_job(translated.json()["id"])["status"] == "completed"


def test_library_migration_preserves_historical_version_and_receipt_hashes(tmp_path):
    service = ImplementationService(tmp_path, adapter_factory=MockReviewer)
    original = publish(service)
    jobs = service.store.list("implementation_job")
    with service.store.connection() as db:
        db.execute("DELETE FROM records WHERE kind='implementation_validation'")
        db.execute("DELETE FROM implementation_migrations")
    service = ImplementationService(tmp_path, adapter_factory=no_model)
    assert service.version(original["id"]) == original
    assert service.store.list("implementation_job") == jobs
    report = service.store.list("implementation_validation")[0]
    assert report["report"] == original["validation_report"]
    assert report["job_id"] == jobs[0]["id"] and report["origin"] == "historical_library_projection"
    again = ImplementationService(tmp_path, adapter_factory=no_model)
    assert again.store.list("implementation_validation") == [report]


def test_missing_exact_runtime_blocks_revalidation_without_erasing_passing_evidence(tmp_path):
    service = ImplementationService(tmp_path, adapter_factory=MockReviewer)
    original = publish(service)
    service.adapter_factory = no_model
    bundle = service.artifact(original["id"])
    runtime = Path(bundle["runtime_root"]) / "site-packages"
    unavailable = runtime.with_name("unavailable-dependencies")
    runtime.rename(unavailable)
    try:
        submitted = service.submit(check_request(original))
        blocked = service.run_job(submitted["id"])
        assert blocked["status"] == "blocked" and "exact published runtime is unavailable" in blocked["error"]
        assert not blocked["attempts"] and not blocked["usage"]
        assert service.version(original["id"]) == original
    finally:
        unavailable.rename(runtime)
    service.control(submitted["id"], "resume")
    complete = service.run_job(submitted["id"])
    assert complete["status"] == "completed", complete


class Client:
    def __init__(self, service): self.service = service
    def submit(self, payload): return self.service.submit(parse_job_request(payload))
    def version(self, identity): return self.service.version(identity)
    def artifact(self, identity): return self.service.artifact(identity)
    def versions(self): return self.service.store.list("implementation_version")
    def job(self, identity): return self.service.store.get(identity, "implementation_job")


def campaign_fixture(tmp_path, source=SOURCE, *, spec=None):
    from optimization_framework.contracts.commands import Command
    from optimization_framework.contracts.requests import CampaignInput, TaskInput
    from optimization_framework.execution.service import Workspace
    service = ImplementationService(tmp_path / "library", adapter_factory=MockReviewer)
    workspace = Workspace(tmp_path / "workspace", implementation_client=Client(service))
    spec = spec or contract_specification()
    campaign = workspace.create_campaign(CampaignInput(name="Independent revalidation campaign", compute_budget_seconds=100,
        implementation_compute_budget_seconds=120, validation_reserve_seconds=0,
        tasks=[TaskInput(name="Unverified quadratic", problem_id=spec.manifest.id, evaluator_manifest=spec.manifest)]))
    task = workspace.current_tasks(campaign["id"])[0]
    receipt = workspace.commands.execute(Command(id="build", campaign_id=campaign["id"], expected_revision=1,
        operation="evaluator.commission", payload={"task_id": task["id"], "spec": spec.model_dump(mode="json"),
            "package": package(source).model_dump(mode="json"), "compute_seconds": 30, "max_calls": 1}))
    grant = workspace.store.get(receipt["outcome"]["grant_id"], "implementation_grant")
    job = service.run_job(grant["job_id"])
    assert job["status"] == "completed", job
    workspace.implementations.reconcile()
    service.adapter_factory = no_model
    return workspace, service, campaign, workspace.current_tasks(campaign["id"])[0]


def revalidate_command(workspace, campaign, version, checks, key="recheck"):
    from optimization_framework.contracts.commands import Command
    current_campaign = workspace.store.get(campaign["id"], "campaign")
    return Command(id=key, campaign_id=campaign["id"], expected_revision=current_campaign["version"],
        operation="implementation.revalidate", payload={"version_id": version, "checks": checks, "compute_seconds": 30})


def test_campaign_revalidation_authorizes_new_experiments_and_preserves_a_frozen_waiver(tmp_path):
    from optimization_framework.contracts.commands import Command
    from optimization_framework.contracts.requests import StudyInput, TrialInput
    from optimization_framework.contracts.validation import WaiverRevocation
    from optimization_framework.storage.sqlite import now
    from test_framework_generated_recipes import finish
    workspace, service, campaign, task = campaign_fixture(tmp_path)
    study = workspace.create_study(campaign["id"], StudyInput(goal="Explore before independent numerical evidence",
        validation_policy={"waivable_kinds": ["evaluator_correctness"]}))
    ready = workspace.evaluators.readiness(task)
    binding = workspace.evaluators.binding(task)
    revision = workspace.store.get(campaign["id"], "campaign")["version"]
    waiver = workspace.commands.execute(Command(id="waiver", campaign_id=campaign["id"], expected_revision=revision,
        operation="validation.waive", payload={"requirement_id": ready["requirement_id"],
            "rationale": "Explore only under the recorded unverified evidence basis", "evidence_ids": [binding["validation_evidence_id"]]}))
    old = workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=task["id"], algorithm="coordinate", max_steps=4, wall_seconds=10))
    frozen_spec = copy.deepcopy(old["experiment_spec"])
    frozen_file = (workspace.job_dir(old["id"]) / "evaluator/bundle.json").read_bytes()
    version_id = task["evaluator_version_id"]
    command = revalidate_command(workspace, campaign, version_id, {"kind": "evaluator", "rationale": "Add analytical correctness fixtures",
        "correctness_cases": [case.model_dump() for case in specification().correctness_cases]})
    receipt = workspace.commands.execute(command)
    assert workspace.commands.execute(command) == receipt
    grant = workspace.store.get(receipt["outcome"]["grant_id"], "implementation_grant")
    checked = service.run_job(grant["job_id"])
    assert checked["status"] == "completed", checked
    workspace.implementations.reconcile()
    counts = {kind: len(workspace.store.list(kind)) for kind in ("validation_result", "cost_event", "implementation_grant")}
    workspace.implementations.reconcile()
    assert counts == {kind: len(workspace.store.list(kind)) for kind in counts}
    assessment = workspace.validations.assess(ready["requirement_id"])
    assert assessment["measured_pass"] and assessment["active_waiver_ids"] == [waiver["outcome"]["waiver_id"]]
    assert assessment["results"][-1]["evidence_ids"][0] in workspace.memory.sync(campaign["id"])["source_ids"]
    assert workspace.evaluators.readiness(task, allow_waived=False)["runnable"]
    completed_old = finish(workspace, old)
    assert completed_old["experiment_spec"] == frozen_spec
    assert (workspace.job_dir(old["id"]) / "evaluator/bundle.json").read_bytes() == frozen_file
    assert json.loads(workspace.memory._text("trial", completed_old))["claim_level"] == "exploratory_unverified_evaluator"
    new = finish(workspace, workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=task["id"], algorithm="coordinate", max_steps=4, wall_seconds=10)))
    assert new["evaluator_eligibility"]["basis"] == "numerical_validation"
    assert new["evaluator_eligibility"]["validation_report_id"] == checked["validation_report_id"]
    assert json.loads(workspace.memory._text("trial", new))["claim_level"] == "measured_screening"
    assert workspace.assets.attributed_costs(completed_old["latest_output_asset_ids"])["quantities"]["evaluation_requests"]["total"] == 24
    assert workspace.assets.attributed_costs(new["latest_output_asset_ids"])["quantities"]["evaluation_requests"]["total"] == 64
    workspace.validations.revoke(WaiverRevocation(id="revoke-original", campaign_id=campaign["id"],
        waiver_id=waiver["outcome"]["waiver_id"], rationale="Later numerical evidence is available", authority="researcher", created_at=now()))
    with pytest.raises(ValueError, match="frozen evaluator authorization"):
        workspace.evaluators.check_launch(old)
    workspace.evaluators.check_launch(new)
    newer_study = workspace.create_study(campaign["id"], StudyInput(goal="Use independently checked evaluator evidence"))
    assert newer_study["id"] != study["id"]
    requirements = [item for item in workspace.store.list("validation_requirement") if item["study_id"] == newer_study["id"]]
    assert len(requirements) == 1 and workspace.validations.assess(requirements[0]["id"])["measured_pass"]


def test_failed_campaign_revalidation_creates_one_issue_and_cannot_be_waived(tmp_path):
    from optimization_framework.contracts.requests import StudyInput
    from optimization_framework.contracts.validation import Waiver
    from optimization_framework.storage.sqlite import now
    bad_source = SOURCE.replace('value = round(value,', 'value = round(value + 1,')
    workspace, service, campaign, task = campaign_fixture(tmp_path, bad_source)
    workspace.create_study(campaign["id"], StudyInput(goal="Explore the unverified evaluator",
        validation_policy={"waivable_kinds": ["evaluator_correctness"]}))
    original = workspace.evaluators.readiness(task)
    command = revalidate_command(workspace, campaign, task["evaluator_version_id"], {"kind": "evaluator", "rationale": "Check independent analytical truth",
        "correctness_cases": [specification().correctness_cases[0].model_dump()]})
    receipt = workspace.commands.execute(command)
    grant = workspace.store.get(receipt["outcome"]["grant_id"], "implementation_grant")
    assert service.run_job(grant["job_id"])["status"] == "failed"
    workspace.implementations.reconcile()
    workspace.implementations.reconcile()
    assert not workspace.evaluators.readiness(task)["runnable"]
    issues = [issue for issue in workspace.store.list("manager_issue") if issue["affected"] == grant["id"]]
    assert len(issues) == 1 and "independent fixture" in issues[0]["message"]
    assessment = workspace.validations.assess(original["requirement_id"])
    assert assessment["status"] == "failed" and not assessment["waiver_allowed"]
    requirement = assessment["requirement"]
    with pytest.raises(ValueError, match="known executable specification failure"):
        workspace.validations.waive(Waiver(id="ignore-failure", campaign_id=campaign["id"], study_id=requirement["study_id"],
            requirement_id=requirement["id"], subject_digest=requirement["subject_digest"], recipe_id=requirement["recipe_id"],
            rationale="Ignore a known error", evidence_ids=assessment["results"][-1]["evidence_ids"],
            authority="researcher", authority_kind="researcher", created_at=now()))
