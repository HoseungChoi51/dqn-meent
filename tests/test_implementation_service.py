from pathlib import Path
import json
import time

from fastapi.testclient import TestClient
import pytest

from dqn_meent.implementations.api import create_app
from dqn_meent.implementations.models import BuildResult, ImplementationSpec, JobRequest, Package, ReviewResult
from dqn_meent.implementations.runtime import PackageOptimizer, prepare_runtime, write_package
from dqn_meent.implementations.service import ImplementationService
from optimization_framework.agents.implementation import review_context
from optimization_framework.contracts.base import content_hash


REFERENCE = (Path(__file__).resolve().parents[1] / "examples/implementation-reference/optimizer.py").read_text()


def test_pi_review_report_compacts_full_masks_without_losing_check_results():
    mask = [index % 2 for index in range(256 * 128)]
    report = {"id": "validation_one", "passed": True, "checks": [
        {"name": "full-mask fixture", "passed": True,
         "detail": {"designs": [mask, mask[::-1]], "efficiencies": [.2, .3]}},
        {"name": "covariance cap", "passed": True,
         "detail": {"unshrunk_max": 5.475, "post_shrink_max": 4.0}}]}
    context = {"spec": {"acceptance_criteria": ["cover all criteria"]},
               "package": {"files": [{"path": "optimizer.py", "content": REFERENCE}]},
               "report": report}
    bounded = review_context(context)
    assert len(json.dumps(bounded)) < 100_000
    assert bounded["report"]["full_report_digest"] == content_hash(report)
    assert bounded["report"]["checks"][0]["detail"]["designs"][0] == {
        "array_digest": content_hash(mask), "length": len(mask), "minimum": 0,
        "maximum": 1, "sum": len(mask) // 2}
    assert bounded["report"]["checks"][1] == report["checks"][1]
    assert bounded["package"] == context["package"]
    assert report["checks"][0]["detail"]["designs"][0] == mask


def specification(**changes):
    return ImplementationSpec.model_validate({"name": "Reference seeded one-bit search",
        "mechanism": "Start from a seeded random binary design. Propose exactly one bit flip from the best observed design; accept only strictly improving observations.",
        "acceptance_criteria": ["Propose one bit from the best observed incumbent", "Preserve RNG and incumbent on checkpoint restoration"],
        "dependencies": {"numpy": "2.5.3"}, "n_cells_max": 16,
        "behavior_checks": [{"name": "Reject deteriorating observations", "assertion": "one_bit_from_incumbent", "efficiencies": [.5, .2, .1, .9, .3]}], **changes})


def package(source=REFERENCE):
    return Package(files=[{"path": "optimizer.py", "content": source}])


class FakeAdapter:
    sources = [REFERENCE]

    def __init__(self, **kwargs):
        self.usage = kwargs.get("usage") or {"calls": 0, "api_cost_usd": 0, "subscription_calls": 0}
        self.callback = kwargs["reservation_callback"]

    def call(self, role, payload, *, result_type, instructions):
        self.usage["calls"] += 1
        self.callback({"type": "provider_call_reserved", "usage": self.usage})
        if result_type is BuildResult:
            index = min(len(payload.get("previous_attempts", [])), len(self.sources)-1)
            return BuildResult(explanation="Candidate", package=package(self.sources[index]), blocker=None)
        return ReviewResult(passed=True, criteria=payload["spec"]["acceptance_criteria"], findings=["Checked each criterion against source and report"])


def test_submitted_package_charges_execution_separately_from_model_wait(tmp_path):
    class SlowReviewAdapter(FakeAdapter):
        def call(self, *args, **kwargs):
            time.sleep(1.1)
            return super().call(*args, **kwargs)

    assert 'accounting_mode' not in request().model_dump()
    service = ImplementationService(tmp_path, adapter_factory=SlowReviewAdapter)
    submitted = request(package=package().model_dump(), accounting_mode='execution_v1', compute_seconds=20)
    began = time.monotonic()
    result = service.run_job(service.submit(submitted)['id'])
    elapsed = time.monotonic() - began
    assert result['status'] == 'completed', result
    assert 0 < result['compute_seconds'] < elapsed - .8


def test_submitted_package_failure_returns_to_coding_workspace_without_rebuild(tmp_path):
    class NoRebuildAdapter(FakeAdapter):
        def call(self, role, payload, *, result_type, instructions):
            assert result_type is not BuildResult, "submitted source must not be replaced by a short-form builder"
            return super().call(role, payload, result_type=result_type, instructions=instructions)

    service = ImplementationService(tmp_path, adapter_factory=NoRebuildAdapter)
    invalid = REFERENCE.replace("efficiency > self.score", "True")
    submitted = request(package=package(invalid).model_dump(), accounting_mode="execution_v1")
    result = service.run_job(service.submit(submitted)["id"])
    assert result["status"] == "failed"
    assert len(result["attempts"]) == 1
    assert result["attempts"][0]["report"]["passed"] is False


def test_pre_review_transport_failure_resumes_same_protected_report(tmp_path):
    class LostReviewAdapter(FakeAdapter):
        first = True

        def call(self, role, payload, *, result_type, instructions):
            if role == "implementation_validator" and self.first:
                type(self).first = False
                raise ValueError("Review transport was unavailable")
            return super().call(role, payload, result_type=result_type, instructions=instructions)

    service = ImplementationService(tmp_path, adapter_factory=LostReviewAdapter)
    job = service.submit(request(package=package().model_dump(), accounting_mode="execution_v1"))
    failed = service.run_job(job["id"])
    assert failed["status"] == "failed"
    assert failed["attempts"][0]["report"]["passed"] is True
    report_id = failed["attempts"][0]["report"]["id"]
    assert not failed["attempts"][0].get("review")
    service.control(job["id"], "resume")
    completed = service.run_job(job["id"])
    assert completed["status"] == "completed"
    assert len(completed["attempts"]) == 1
    assert completed["attempts"][0]["report"]["id"] == report_id


def request(**changes):
    return JobRequest.model_validate({"workspace_id": "workspace_one", "campaign_id": "campaign_one", "idempotency_key": "build-one",
        "grant_id": "grant_one", "spec": specification().model_dump(), "compute_seconds": 120, **changes})


def test_service_auth_idempotency_and_capabilities(tmp_path):
    app = create_app(tmp_path, token="secret", start_workers=False, adapter_factory=FakeAdapter)
    with TestClient(app) as client:
        assert client.get("/v1/versions").status_code == 401
        headers = {"Authorization": "Bearer secret"}
        payload = request(spec=specification(capabilities=["gradients"]).model_dump()).model_dump()
        first = client.post("/v1/jobs", json=payload, headers=headers)
        assert first.status_code == 202
        assert client.post("/v1/jobs", json=payload, headers=headers).json()["id"] == first.json()["id"]
        payload["compute_seconds"] = 10
        assert client.post("/v1/jobs", json=payload, headers=headers).status_code == 409
        result = app.state.service.run_job(first.json()["id"])
        assert result["status"] == "blocked" and "gradients" in result["error"]
        assert not result["usage"]


def test_full_repair_validation_and_immutable_artifact(tmp_path):
    class RepairAdapter(FakeAdapter):
        sources = [REFERENCE.replace("efficiency > self.score", "True"), REFERENCE]
    service = ImplementationService(tmp_path, adapter_factory=RepairAdapter)
    job = service.submit(request())
    result = service.run_job(job["id"])
    assert result["status"] == "completed", result
    assert len(result["attempts"]) == 2
    assert result["attempts"][0]["report"]["passed"] is False
    bundle = service.artifact(result["version_id"])
    assert bundle["version"]["validation_report"]["kind"] == "implementation_correctness"
    assert bundle["version"]["exposed_conditions"]
    path = tmp_path / "artifacts" / bundle["version"]["artifact_digest"] / "package/optimizer.py"
    path.write_text("changed")
    with pytest.raises(ValueError, match="changed"):
        service.artifact(result["version_id"])


def test_cancel_is_terminal_and_does_not_spend_again(tmp_path):
    service = ImplementationService(tmp_path, adapter_factory=FakeAdapter)
    job = service.submit(request())
    assert service.control(job["id"], "cancel")["status"] == "cancelled"
    assert service.run_job(job["id"])["status"] == "cancelled"
    with pytest.raises(ValueError, match="Only interrupted"):
        service.control(job["id"], "resume")


def test_control_receipt_lookup_is_authenticated_and_never_reissues_control(tmp_path):
    app = create_app(tmp_path, token="secret", start_workers=False, adapter_factory=FakeAdapter)
    job = app.state.service.submit(request())
    path = f"/v1/jobs/{job['id']}/control-receipt?idempotency_key=cancel_once"
    headers = {"Authorization": "Bearer secret"}
    with TestClient(app) as client:
        assert client.get(path).status_code == 401
        assert client.get(path, headers=headers).json() is None
        response = client.post(f"/v1/jobs/{job['id']}/control", json={"action": "cancel", "idempotency_key": "cancel_once"}, headers=headers)
        assert response.status_code == 200
        receipt = client.get(path, headers=headers).json()
        assert receipt["job_id"] == job["id"] and receipt["action"] == "cancel" and receipt["outcome_status"] == "cancelled"
        assert client.get(path, headers=headers).json() == receipt
        assert len(app.state.service.store.list("implementation_control")) == 1


def test_package_isolation_and_checkpoint(tmp_path):
    root, runtime = prepare_runtime(tmp_path / "runtimes", {"numpy": "2.5.3"})
    source = REFERENCE.replace("self.n = context[\"n_cells\"]", """import os, socket
        assert 'OPENAI_API_KEY' not in os.environ
        assert not os.path.exists('/home/chs/Work/dqn-meent-latest')
        try:
            import meent
        except ImportError:
            pass
        else:
            raise RuntimeError('Evaluator accessible')
        self.n = context['n_cells']""")
    package_dir = write_package(tmp_path / "package", package(source))
    optimizer = PackageOptimizer(package_dir, root, runtime, "optimizer:create_optimizer", {"n_cells": 8, "seed": 3, "parameters": {}})
    try:
        design = optimizer.ask()
        optimizer.tell(design, .5)
        checkpoint = optimizer.state_dict()
        expected = optimizer.ask().tolist()
        optimizer.load_state_dict(checkpoint)
        assert optimizer.ask().tolist() == expected
    finally:
        optimizer.close()


def test_validation_profile_change_requires_revalidation(tmp_path, monkeypatch):
    service = ImplementationService(tmp_path, adapter_factory=FakeAdapter)
    result = service.run_job(service.submit(request(package=package().model_dump()))["id"])
    assert result["status"] == "completed", result
    monkeypatch.setattr("dqn_meent.implementations.service.profile_identity", lambda: "different")
    with pytest.raises(ValueError, match="requires validation"):
        service.artifact(result["version_id"])


class DirectClient:
    """Exercise the real service boundary without a second HTTP test server."""
    def __init__(self, service):
        self.service = service

    def submit(self, payload):
        return self.service.submit(JobRequest.model_validate(payload))

    def job(self, job_id):
        return self.service.store.get(job_id, "implementation_job")

    def artifact(self, version_id):
        return self.service.artifact(version_id)

    def version(self, version_id):
        return self.service.store.get(version_id, "implementation_version")

    def versions(self):
        return self.service.store.list("implementation_version")

    def control(self, job_id, action):
        return self.service.control(job_id, action)


def test_development_validation_does_not_attach_before_selection(tmp_path):
    from dqn_meent.workspace.service import Workspace
    from dqn_meent.workspace.models import CampaignInput
    from optimization_framework.campaigns.hypotheses import create
    from optimization_framework.contracts.requests import HypothesisInput

    service = ImplementationService(tmp_path / "library", adapter_factory=FakeAdapter)
    workspace = Workspace(tmp_path / "workspace", implementation_client=DirectClient(service))
    campaign = workspace.create_campaign(CampaignInput(name="sandbox validation",
        implementation_compute_budget_seconds=120,
        tasks=[{"name": "Small device", "physics": {"n_cells": 8, "fourier_order": 2}}]))
    hypothesis = create(workspace, campaign["id"], HypothesisInput(campaign_id=campaign["id"],
        title="Candidate", algorithm="fourier", mechanism="Seeded search", rationale="Sandbox submission"),
        identity="hypothesis_sandbox_candidate")
    grant = workspace.implementations.commission(hypothesis["id"], specification(), package=package(),
        compute_seconds=120, idempotency_key="sandbox-submission", accounting_mode="execution_v1")
    assert grant["request"]["max_attempts"] == 1
    result = service.run_job(grant["job_id"])
    assert result["status"] == "completed", result
    workspace.implementations.reconcile()
    saved = workspace.store.get(grant["id"], "implementation_grant")
    assert saved["version_id"] == result["version_id"] and not saved.get("attached")
    assert workspace.store.get(hypothesis["id"], "hypothesis").get("implementation_version_id") is None
    workspace.implementations.attach(hypothesis["id"], result["version_id"])
    assert workspace.store.get(hypothesis["id"], "hypothesis")["implementation_version_id"] == result["version_id"]


def test_two_workspaces_reuse_exact_artifact_and_resume_without_service(tmp_path):
    from dqn_meent.workspace.service import Workspace
    from dqn_meent.workspace.models import CampaignInput, TrialInput
    from dqn_meent.workspace.worker import run
    from dqn_meent.workspace.store import atomic_json
    from dqn_meent.workspace.confirmation import condition_exposure
    from dqn_meent.config import PhysicsConfig
    from dataclasses import asdict
    from optimization_framework.campaigns.hypotheses import create
    from optimization_framework.contracts.requests import HypothesisInput
    from optimization_framework.storage.sqlite import identifier

    service = ImplementationService(tmp_path / "library", adapter_factory=FakeAdapter)
    trials = []
    for name in ("first", "second"):
        workspace = Workspace(tmp_path / name, implementation_client=DirectClient(service))
        campaign = workspace.create_campaign(CampaignInput(name=name, implementation_compute_budget_seconds=120,
            tasks=[{"name": "Small device", "physics": {"n_cells": 8, "fourier_order": 2}}]))
        hypothesis = create(workspace, campaign["id"], HypothesisInput(campaign_id=campaign["id"],
            title="Researcher Fourier proposal", algorithm="fourier",
            mechanism="Fourier parameterization", rationale="Explicit implementation-service fixture"),
            identity=identifier("fixture_idea"))
        if name == "first":
            grant = workspace.implementations.commission(hypothesis["id"], specification(), package=package(), compute_seconds=120,
                                                       idempotency_key="reference", max_calls=3)
            result = service.run_job(grant["job_id"])
            assert result["status"] == "completed", result.get("error")
            workspace.implementations.reconcile()
            version_id = result["version_id"]
        else:
            workspace.implementations.catalog(refresh=True)
            workspace.implementations.attach(hypothesis["id"], version_id)
        hypothesis = workspace.store.get(hypothesis["id"])
        assert workspace.implementations.readiness(hypothesis)["runnable"]
        exposure = condition_exposure(workspace.store, campaign["id"], asdict(PhysicsConfig(n_cells=8, fourier_order=2)))
        assert exposure["implementation_version_ids"] == [version_id]
        task = workspace.current_tasks(campaign["id"])[0]
        trial = workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=task["id"],
            hypothesis_id=hypothesis["id"], seed=7, max_steps=8 if name == "first" else 3, schedule_steps=8, wall_seconds=30))
        assert trial["implementation_version_id"] == version_id
        assert (workspace.job_dir(trial["id"]) / "code/dqn_meent/workspace/worker.py").exists()
        trials.append((workspace, trial))
    assert len(service.store.list("implementation_job")) == 1
    full = run(trials[0][0].job_dir(trials[0][1]["id"]))
    directory = trials[1][0].job_dir(trials[1][1]["id"])
    partial = run(directory)
    assert full["status"] == partial["status"] == "completed"
    assert partial["evaluations"] == 3
    # Revocation stops new bindings while pinned historical continuations survive.
    service.revoke(version_id, "Retired for new work")
    with pytest.raises(ValueError, match="not available"):
        trials[1][0].implementations.prepare(version_id, task, {})
    atomic_json(directory / "control.json", {"command": "run", "max_steps": 8, "revision": 1})
    resumed = run(directory)
    assert resumed["status"] == "completed", resumed.get("reason")
    for field in ("best_design", "best_efficiency", "evaluations", "solver_calls", "cache_hits", "archive"):
        assert resumed[field] == full[field]


def test_restart_preserves_uncertain_call_and_conservatively_charges_grant(tmp_path):
    service = ImplementationService(tmp_path, adapter_factory=FakeAdapter)
    job = service.submit(request())
    service.update_job(job["id"], status="reviewing", execution_started_at=time.time()-10,
        prior_compute_seconds=2, compute_seconds=3, usage={"calls": 1, "pending_reservation": {"id": "uncertain"}})
    service.start()
    try:
        interrupted = service.store.get(job["id"])
        assert interrupted["status"] == "needs_reconciliation"
        assert interrupted["compute_seconds"] >= 12
        with pytest.raises(ValueError, match="Only interrupted"):
            service.control(job["id"], "resume")
        with pytest.raises(ValueError, match="uncertain"):
            service.control(job["id"], "cancel")
        closed = service.control(job["id"], "close_uncertain")
        assert closed["usage"]["pending_reservation"]["id"] == "uncertain"
    finally:
        service.close()


def test_cancel_during_provider_call_retains_uncertain_usage(tmp_path):
    class InterruptingAdapter(FakeAdapter):
        def call(self, role, payload, **kwargs):
            self.usage.update(calls=1, pending_reservation={"id": "active-call"})
            service.control(job["id"], "cancel")
            self.callback({"usage": self.usage})
            raise AssertionError("Cancellation must interrupt before another call")
    service = ImplementationService(tmp_path, adapter_factory=InterruptingAdapter)
    job = service.submit(request())
    result = service.run_job(job["id"])
    assert result["status"] == "needs_reconciliation"
    assert result["usage"]["calls"] == 1
    assert not service.store.list("implementation_version")


def test_revalidation_retains_version_and_history_without_rebuilding(tmp_path):
    service = ImplementationService(tmp_path, adapter_factory=FakeAdapter)
    first = service.run_job(service.submit(request(package=package().model_dump()))["id"])
    second = service.run_job(service.submit(request(package=package().model_dump(), grant_id="grant_two", idempotency_key="second"))["id"])
    assert second["status"] == "completed", second.get("error")
    assert second["version_id"] == first["version_id"]
    version = service.version(first["version_id"])
    assert len(version["validation_history"]) == 1
    assert version["validation_history"][0]["id"] != version["validation_report"]["id"]


def test_service_designs_and_freezes_checks_before_building(tmp_path):
    from dqn_meent.implementations.service import ValidationPlan
    roles = []
    class PlanningAdapter(FakeAdapter):
        def call(self, role, payload, **kwargs):
            roles.append(role)
            if kwargs['result_type'] is ValidationPlan:
                assert 'package' not in payload
                self.usage['calls'] += 1
                return ValidationPlan(checks=specification().behavior_checks,
                                      rationale='Check rejection of deteriorating proposals', blocker=None)
            if role == 'implementation_builder':
                assert payload['spec']['behavior_checks'][0]['assertion'] == 'one_bit_from_incumbent'
            return super().call(role, payload, **kwargs)
    service = ImplementationService(tmp_path, adapter_factory=PlanningAdapter)
    result = service.run_job(service.submit(request(spec=specification(behavior_checks=[]).model_dump()))['id'])
    assert result['status'] == 'completed', result.get('error')
    assert roles == ['implementation_test_designer', 'implementation_builder', 'implementation_validator']
    assert service.version(result['version_id'])['spec']['behavior_checks'] == result['validation_plan']['checks']
