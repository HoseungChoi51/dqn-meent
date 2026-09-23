"""Held-out exposure cannot be reset by aliases, edits, or renewed nomination."""
import copy

import pytest

from dqn_meent.workspace import confirmation
from dqn_meent.workspace.models import CampaignInput, CampaignUpdate, ControlInput, TaskInput, TrialInput
from dqn_meent.workspace.service import Workspace
from dqn_meent.workspace.store import identifier, now


@pytest.fixture
def study(tmp_path):
    workspace = Workspace(tmp_path / "workspace")
    campaign = workspace.create_campaign(CampaignInput(name="Confirmation", compute_budget_seconds=1000,
        validation_reserve_seconds=20, llm_budget_usd=0, tasks=[
            TaskInput(name="Untouched condition", split="test", physics={"n_cells": 8, "fourier_order": 2}),
            TaskInput(name="Other untouched condition", split="test", physics={"n_cells": 8, "fourier_order": 2, "wavelength_nm": 900}),
        ]))
    return workspace, campaign, workspace.current_tasks(campaign["id"])


def nominate(workspace, campaign, algorithm="random", **extra):
    hypothesis = {"id": identifier("hypothesis"), "campaign_id": campaign["id"], "title": "Frozen strategy",
                  "algorithm": algorithm, "algorithm_config": {}, "source": None, "created_at": now(),
                  "charter_version": campaign["version"], **extra}
    hypothesis = confirmation.nominate_finalist(hypothesis)
    workspace.store.put("hypothesis", hypothesis)
    return hypothesis


def launch(workspace, campaign, task, hypothesis, **extra):
    options = {"campaign_id": campaign["id"], "task_id": task["id"], "hypothesis_id": hypothesis["id"],
               "algorithm": hypothesis["algorithm"], "algorithm_config": hypothesis["algorithm_config"],
               "max_steps": 10, "wall_seconds": 20, "schedule_steps": 30, "seed": 0, "confirmatory": True}
    options.update(extra)
    return workspace.create_trial(TrialInput(**options))


def test_first_launch_exposes_condition_and_preserves_preexisting_cohort(study):
    workspace, campaign, tasks = study
    first = nominate(workspace, campaign)
    second = nominate(workspace, campaign, "hillclimb")
    trial = launch(workspace, campaign, tasks[0], first)
    exposed = workspace.store.get(tasks[0]["id"], "task")
    assert exposed["exposed"]
    assert set(exposed["confirmation_cohort_ids"]) == {first["id"], second["id"]}
    assert trial["confirmation_protocol_hash"]
    assert launch(workspace, campaign, tasks[0], second)["confirmation_condition_id"] == trial["confirmation_condition_id"]
    later = nominate(workspace, campaign, "annealing", parent_ids=[first["id"]])
    with pytest.raises(ValueError, match="fresh test conditions"):
        launch(workspace, campaign, tasks[0], later)
    assert launch(workspace, campaign, tasks[1], later)["status"] == "queued"


def test_revisions_and_fidelity_changes_do_not_reset_exposure(study):
    workspace, campaign, tasks = study
    first = nominate(workspace, campaign)
    launch(workspace, campaign, tasks[0], first)
    old_key = confirmation.physical_condition_key(tasks[0]["physics"])
    changed_physics = {**tasks[0]["physics"], "fourier_order": 9, "cache_size": 1, "energy_tolerance": .001}
    revised = workspace.update_campaign(campaign["id"], CampaignUpdate(tasks=[
        TaskInput(name="Renamed same physical condition", split="test", physics=changed_physics)]))
    replacement = workspace.current_tasks(campaign["id"])[0]
    assert replacement["id"] != tasks[0]["id"]
    assert replacement["exposed"] and replacement["physical_condition_key"] == old_key
    late = nominate(workspace, revised, "hillclimb")
    with pytest.raises(ValueError, match="fresh test conditions"):
        launch(workspace, revised, replacement, late)


@pytest.mark.parametrize("change", [
    {"training": {"learning_rate": .003}}, {"schedule_steps": 300},
    {"max_steps": 20}, {"wall_seconds": 21},
])
def test_full_dqn_protocol_is_fixed_but_replication_seed_may_change(study, change):
    workspace, campaign, tasks = study
    hypothesis = nominate(workspace, campaign, "dqn")
    initial = launch(workspace, campaign, tasks[0], hypothesis)
    with pytest.raises(ValueError, match="are frozen"):
        launch(workspace, campaign, tasks[0], hypothesis, **change)
    repeated = launch(workspace, campaign, tasks[0], hypothesis, seed=123, training={"seed": 123})
    assert repeated["confirmation_protocol_hash"] == initial["confirmation_protocol_hash"]
    with pytest.raises(ValueError, match="allocation is frozen"):
        workspace.control(initial["id"], ControlInput(action="extend", max_steps=40))


def test_renomination_preserves_freeze_and_cannot_admit_a_later_candidate(study):
    workspace, campaign, tasks = study
    hypothesis = nominate(workspace, campaign)
    launch(workspace, campaign, tasks[0], hypothesis)
    stored = workspace.store.get(hypothesis["id"], "hypothesis")
    old_protocol, old_frozen_at = copy.deepcopy(stored["confirmation_protocol"]), stored["frozen_at"]
    stored["status"] = "archived"
    repeated = confirmation.nominate_finalist(stored)
    assert repeated["frozen_at"] == old_frozen_at
    assert repeated["confirmation_protocol"] == old_protocol
    repeated["algorithm_config"] = {"changed": True}
    with pytest.raises(ValueError, match="fork a new hypothesis"):
        confirmation.nominate_finalist(repeated)


def test_scientific_code_change_after_nomination_is_rejected_without_exposure(study, monkeypatch):
    workspace, campaign, tasks = study
    hypothesis = nominate(workspace, campaign)
    monkeypatch.setattr(confirmation, "scientific_source_hash", lambda *_: "different-source")
    with pytest.raises(ValueError, match="Scientific source changed"):
        launch(workspace, campaign, tasks[0], hypothesis)
    assert not workspace.store.get(tasks[0]["id"], "task")["exposed"]
    assert workspace.store.list("trial", campaign["id"]) == []


def test_confirmation_source_is_already_pinned_while_trial_is_queued(study):
    workspace, campaign, tasks = study
    hypothesis = nominate(workspace, campaign)
    trial = launch(workspace, campaign, tasks[0], hypothesis)
    root = workspace.job_dir(trial["id"]) / "code" / "dqn_meent"
    assert (root / "workspace" / "worker.py").is_file()
    assert confirmation.scientific_source_hash(root) == hypothesis["frozen_scientific_source_hash"]
    assert trial["source_hash"]
    assert trial["status"] == "queued" and trial["attempt"] == 0


def test_prior_development_observation_cannot_be_relabelled_as_fresh_test(study):
    workspace, campaign, tasks = study
    development = workspace.update_campaign(campaign["id"], CampaignUpdate(tasks=[
        TaskInput(name="Development", physics=tasks[0]["physics"])]))
    dev = workspace.current_tasks(campaign["id"])[0]
    workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=dev["id"], algorithm="random", max_steps=3, wall_seconds=10))
    final = workspace.update_campaign(campaign["id"], CampaignUpdate(tasks=[
        TaskInput(name="Same condition disguised as test", split="test", physics=tasks[0]["physics"])]))
    renamed = workspace.current_tasks(campaign["id"])[0]
    assert renamed["exposed"]
    candidate = nominate(workspace, final)
    with pytest.raises(ValueError, match="fresh test conditions"):
        launch(workspace, final, renamed, candidate)


def test_failed_allocation_does_not_consume_confirmation_condition(study):
    workspace, campaign, tasks = study
    hypothesis = nominate(workspace, campaign)
    with pytest.raises(ValueError, match="remaining campaign budget"):
        launch(workspace, campaign, tasks[0], hypothesis, wall_seconds=1001)
    assert not workspace.store.get(tasks[0]["id"], "task")["exposed"]
    assert workspace.store.list("confirmation_condition", campaign["id"]) == []


def test_dependency_change_after_nomination_requires_new_identity(study, monkeypatch):
    workspace, campaign, tasks = study
    hypothesis = nominate(workspace, campaign)
    monkeypatch.setattr(confirmation, "scientific_environment", lambda: {"meent": "different"})
    with pytest.raises(ValueError, match="dependencies changed"):
        launch(workspace, campaign, tasks[0], hypothesis)


def test_queue_snapshot_tampering_prevents_worker_start(study):
    workspace, campaign, tasks = study
    hypothesis = nominate(workspace, campaign)
    trial = launch(workspace, campaign, tasks[0], hypothesis)
    snapshot = workspace.job_dir(trial["id"]) / "code" / "dqn_meent" / "workspace" / "optimizers.py"
    snapshot.write_text(snapshot.read_text() + "\n# altered scientific implementation\n")
    with pytest.raises(ValueError, match="snapshot changed"):
        workspace._start_trial(trial)
    assert not workspace.processes


def test_dependency_change_while_queued_prevents_worker_start(study, monkeypatch):
    workspace, campaign, tasks = study
    hypothesis = nominate(workspace, campaign)
    trial = launch(workspace, campaign, tasks[0], hypothesis)
    monkeypatch.setattr(confirmation, "scientific_environment", lambda: {"numpy": "changed after queue"})
    with pytest.raises(ValueError, match="after the confirmation was queued"):
        workspace._start_trial(trial)
    assert not workspace.processes
