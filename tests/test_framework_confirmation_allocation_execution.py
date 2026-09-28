"""Main-run allocations execute the chosen implementation from a fresh start."""
from copy import deepcopy
import json
import shutil
import subprocess

import pytest

from optimization_framework.contracts.base import content_hash
from optimization_framework.contracts.requests import CampaignInput, ControlInput, StudyInput, TaskInput, TrialInput
from optimization_framework.evaluation.confirmation import method_definition
from optimization_framework.execution.service import Workspace


def execute_captured(workspace, identity):
    """Exercise launch preflight and the actual archived worker subprocess."""
    workspace._start_trial(workspace.store.get(identity, "trial"))
    process = workspace.processes[identity]
    try:
        assert process.wait(timeout=20) == 0, (workspace.job_dir(identity) / "worker.log").read_text()
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
        pytest.fail("The bounded continuous optimizer did not finish its small allocation")
    result = json.loads((workspace.job_dir(identity) / "result.json").read_text())
    assert result["scientific_complete"], result
    trial = workspace.store.get(identity, "trial")
    trial.update(status=result["status"], result=result, progress=result)
    workspace.store.put("trial", trial)
    workspace.capture_evidence(trial)
    return result


def test_larger_confirmation_runs_fresh_with_original_captured_implementation(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path / "workspace")
    campaign = workspace.create_campaign(CampaignInput(name="Allocated confirmation", compute_budget_seconds=100,
        validation_reserve_seconds=0, tasks=[TaskInput(name="Quadratic", problem_id="bounded_continuous")]))
    task = workspace.current_tasks(campaign["id"])[0]
    source = workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=task["id"], algorithm="coordinate",
        seed=1, max_steps=3, wall_seconds=5))
    assert execute_captured(workspace, source["id"])["evaluations"] == 3
    source = workspace.store.get(source["id"], "trial")
    unchanged_source = deepcopy(source)
    original_spec = (workspace.job_dir(source["id"]) / "spec.json").read_bytes()

    study = workspace.create_study(campaign["id"], StudyInput(goal="Longer finalist runs", scope="confirmation",
        confirmation_kind="seed_replication", prototype_trial_ids=[source["id"]], seeds=[11, 12],
        prototype_allocations={source["id"]: {"expected_control_revision": source["control_revision"],
            "max_steps": 9, "wall_seconds": 15, "schedule_steps": 9, "completion_count": 9}}))
    protocol = study["confirmation"]
    assert len(workspace.store.list("trial")) == 1  # Freeze does not run a longer intermediate prototype.
    assert len(workspace.store.list("confirmation_allocation_binding")) == 1
    method_id, method = next(iter(protocol["methods"].items()))
    assert method_id != content_hash(method_definition(source))
    assert method["max_steps"] == method["schedule_steps"] == method["completion"]["count"] == 9
    assert method["wall_seconds"] == 15

    changed_root = tmp_path / "later_installation"
    shutil.copytree(workspace.job_dir(source["id"]) / "code", changed_root)
    changed_worker = changed_root / "optimization_framework/execution/worker.py"
    changed_worker.write_text(changed_worker.read_text() + "\nraise RuntimeError('Current installation must not execute the frozen run')\n")
    monkeypatch.setattr("optimization_framework.execution.source.source_root", lambda: changed_root)
    monkeypatch.setattr(workspace, "_snapshot_code", lambda _: pytest.fail("Must reuse the source implementation"))
    monkeypatch.setattr("optimization_framework.execution.preparation.prepare",
        lambda *_args, **_kwargs: pytest.fail("Must prepare the new allocation with captured source"))

    identities = workspace.confirmations.schedule(workspace, protocol["id"])["created_trial_ids"]
    assert len(identities) == 2
    assert workspace.confirmations.schedule(workspace, protocol["id"])["existing_trial_ids"] == identities
    for identity in identities:
        trial = workspace.store.get(identity, "trial")
        assert trial["source_prototype_id"] == source["id"]
        assert trial["source_hash"] == source["source_hash"]
        assert trial["execution_manifest"] == source["execution_manifest"]
        assert trial["scientific_source_hash"] == source["scientific_source_hash"]
        assert trial["scientific_environment"] == source["scientific_environment"]
        assert trial["experiment_spec_hash"] != source["experiment_spec_hash"]
        assert trial["seed"] in {11, 12}
        assert trial["attempt"] == 0
        assert not (workspace.job_dir(identity) / "checkpoints/latest.json").exists()
        assert method_definition(trial) == method
        result = execute_captured(workspace, identity)
        assert result["evaluations"] == 9
        assert result["schedule_steps"] == 9
        assert result["recovered_suffix_observations"] == 0
        with pytest.raises(ValueError, match="frozen"):
            workspace.control(identity, ControlInput(action="extend", max_steps=12, wall_seconds=20))

    assert workspace.store.get(source["id"], "trial") == unchanged_source
    assert (workspace.job_dir(source["id"]) / "spec.json").read_bytes() == original_spec
    assert workspace.confirmations.assess(protocol["id"])["complete"]
    assert workspace.confirmations.release(protocol["id"])["outcome"] == "completed_roster"
