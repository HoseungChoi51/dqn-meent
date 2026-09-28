"""Later executable counterexamples withdraw support without rewriting history."""
import copy
import time

import pytest

from optimization_framework.analysis.studies import experiment_evidence
from optimization_framework.contracts.requests import StudyInput, TrialInput
from optimization_framework.evaluation.executables import assessment
from optimization_framework.execution.service import Workspace
from test_framework_evaluators import SOURCE, specification
from test_framework_generated_recipes import finish
from test_framework_revalidation import Client, campaign_fixture, revalidate_command
from test_framework_templates import drain, template


@pytest.mark.parametrize("conditional", [False, True], ids=["manual-roster", "conditional-design"])
def test_later_evaluator_counterexample_reassesses_the_exact_released_evidence(tmp_path, conditional):
    source = SOURCE.replace('value = round(value,', 'value = round(value + (1 if candidate == [2, 2] else 0),')
    workspace, service, campaign, task = campaign_fixture(tmp_path, source, spec=specification())
    if conditional:
        execution = workspace.study_executions.freeze(campaign["id"], {"template": template(prefix=False), "task_ids": [task["id"]]})
        workspace.study_executions.activate(execution["id"])
        drain(workspace, execution)
        protocol = execution["protocol_id"]
    else:
        prototype = finish(workspace, workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=task["id"],
            algorithm="coordinate", max_steps=4, wall_seconds=5)))
        study = workspace.create_study(campaign["id"], StudyInput(goal="Replicate the frozen procedure",
            scope="confirmation", confirmation_kind="seed_replication", prototype_trial_ids=[prototype["id"]], seeds=[17]))
        protocol = study["confirmation"]["id"]
        for identity in workspace.confirmations.schedule(workspace, protocol)["created_trial_ids"]:
            finish(workspace, workspace.store.get(identity, "trial"))
    before = workspace.confirmations.assess(protocol)
    assert before["complete"]
    trials = copy.deepcopy(workspace.store.list("trial"))
    nominations = copy.deepcopy(workspace.store.list("nomination"))
    release = workspace.confirmations.release(protocol)
    original_report = workspace.store.get(release["report_id"], "confirmation_report")
    workspace.confirmations.reconcile_reports()
    assert len(workspace.store.list("confirmation_report")) == 1
    trial = trials[0]
    cutoff = time.time()
    original_evidence = experiment_evidence(workspace.store, trial, cutoff_at=cutoff)
    assert original_evidence["evidence_complete"]

    command = revalidate_command(workspace, campaign, task["evaluator_version_id"], {"kind": "evaluator",
        "rationale": "Independent analytical counterexample outside the original fixture set",
        "correctness_cases": [{"name": "new corner", "basis": "2*(2-1.5)^2-2 = -1.5",
            "candidate": [2, 2], "objectives": {"energy": -1.5}}]})
    receipt = workspace.commands.execute(command)
    grant = workspace.store.get(receipt["outcome"]["grant_id"], "implementation_grant")
    checked = service.run_job(grant["job_id"])
    assert checked["status"] == "failed", checked
    workspace.implementations.reconcile()
    assert not workspace.evaluators.readiness(task)["runnable"]
    later = workspace.confirmations.assess(protocol)
    assert not later["complete"]
    assert all(not cell["executable_evidence"]["supported"] for cell in later["cells"])
    assert all(checked["validation_report_id"] in cell["executable_evidence"]["executables"][0]["blocking_report_ids"]
        for cell in later["cells"])
    assert experiment_evidence(workspace.store, trial, cutoff_at=cutoff) == original_evidence
    assert not experiment_evidence(workspace.store, trial)["evidence_complete"]
    workspace.confirmations.reconcile_reports()
    reports = workspace.store.list("confirmation_report")
    assert len(reports) == 2 and reports[-1]["claim_level"] == "inconclusive"
    assert reports[-1]["supersedes_report_id"] == original_report["id"]
    if conditional:
        assert not reports[-1]["evidence"]["analysis_evidence"]["nomination_review"]["supported"]
    assert workspace.store.list("nomination") == nominations
    assert workspace.store.list("trial") == trials
    assert workspace.store.get(original_report["id"]) == original_report
    assert workspace.confirmations.release(protocol) == release
    restarted = Workspace(workspace.directory, implementation_client=Client(service))
    restarted.implementations.catalog(refresh=True)
    restarted.confirmations.reconcile_reports()
    assert restarted.store.list("confirmation_report") == reports
    assert not restarted.confirmations.assess(protocol)["complete"]


def test_optimizer_revocation_blocks_an_unissued_claim_and_missing_runtime_does_not_rewrite_evidence(tmp_path):
    from test_framework_executable_reuse import setup, command
    workspace, service, campaign, hypothesis, version = setup(tmp_path)
    workspace.commands.execute(command(campaign, hypothesis, version, "reuse"))
    task = workspace.current_tasks(campaign["id"])[0]
    prototype = finish(workspace, workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=task["id"],
        hypothesis_id=hypothesis["id"], algorithm="package", max_steps=4, wall_seconds=5)))
    study = workspace.create_study(campaign["id"], StudyInput(goal="Replicate the exact commissioned optimizer", scope="confirmation",
        confirmation_kind="seed_replication", prototype_trial_ids=[prototype["id"]], seeds=[17]))
    protocol = study["confirmation"]["id"]
    for identity in workspace.confirmations.schedule(workspace, protocol)["created_trial_ids"]:
        finish(workspace, workspace.store.get(identity, "trial"))
    assert workspace.confirmations.assess(protocol)["complete"]
    before = assessment(workspace.store, prototype)
    from pathlib import Path
    runtime = Path(service.artifact(version["id"])["runtime_root"]) / "site-packages"
    runtime.rename(runtime.with_name(runtime.name + ".unavailable"))
    assert assessment(workspace.store, prototype) == before
    service.revoke(version["id"], "Independent review withdrew correctness support")
    workspace.implementations.catalog(refresh=True)
    assert not workspace.confirmations.assess(protocol)["complete"]
    with pytest.raises(ValueError, match="Incomplete confirmation"):
        workspace.confirmations.release(protocol, authority="manager")
    closed = workspace.confirmations.release(protocol, allow_incomplete=True, rationale="Retain measurements with withdrawn correctness support")
    assert closed["outcome"] == "inconclusive"


def test_failed_recheck_retries_evidence_reconciliation_after_lost_reply(tmp_path, monkeypatch):
    source = SOURCE.replace('value = round(value,', 'value = round(value + 1,')
    workspace, service, campaign, task = campaign_fixture(tmp_path, source)
    receipt = workspace.commands.execute(revalidate_command(workspace, campaign, task["evaluator_version_id"], {
        "kind": "evaluator", "rationale": "Check the independent analytical origin value",
        "correctness_cases": [specification().correctness_cases[0].model_dump()]}))
    grant_id = receipt["outcome"]["grant_id"]
    grant = workspace.store.get(grant_id, "implementation_grant")
    assert service.run_job(grant["job_id"])["status"] == "failed"
    with monkeypatch.context() as patch:
        def unavailable(*args):
            raise ValueError("Lost the library evidence reply after receiving the terminal job receipt")
        patch.setattr(workspace.implementations.client, "version", unavailable)
        workspace.implementations.reconcile()
    unsettled_evidence = workspace.store.get(grant_id, "implementation_grant")
    assert unsettled_evidence["status"] == "failed" and not unsettled_evidence.get("evidence_reconciled")
    workspace.implementations.reconcile()
    assert workspace.store.get(grant_id, "implementation_grant")["evidence_reconciled"]
    assert workspace.store.get(task["evaluator_version_id"], "implementation_cache")["status"] == "validation_failed"
    records = {kind: workspace.store.list(kind) for kind in ("executable_evidence", "cost_event", "validation_result")}
    workspace.implementations.reconcile()
    assert records == {kind: workspace.store.list(kind) for kind in records}
