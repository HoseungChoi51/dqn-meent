"""Manual shortlists preserve exact procedures without launching or claiming a winner."""
from copy import deepcopy
import json

import pytest
from fastapi.testclient import TestClient

from optimization_framework.analysis.general import method_identity, report
from optimization_framework.contracts.base import content_hash
from optimization_framework.contracts.commands import Command
from optimization_framework.contracts.requests import CampaignInput, StudyInput, TaskInput, TrialInput
from optimization_framework.evaluation.confirmation import method_definition
from optimization_framework.execution.service import Workspace


def prepare(tmp_path, *, workspace=None):
    workspace = workspace or Workspace(tmp_path)
    campaign = workspace.create_campaign(CampaignInput(name="Choose exact optimizer procedures", autonomy="delegated",
        compute_budget_seconds=500, validation_reserve_seconds=0,
        tasks=[TaskInput(name="Quadratic", problem_id="bounded_continuous")]))
    task = workspace.current_tasks(campaign["id"])[0]
    trials = []
    for seed, wall_seconds in ((1, 5), (2, 5), (3, 8)):
        trial = workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=task["id"],
            algorithm="coordinate", seed=seed, max_steps=3, wall_seconds=wall_seconds))
        trial.update(status="completed", result={"best_objective": seed / 10, "scientific_complete": True})
        workspace.store.put("trial", trial)
        trials.append(workspace.store.get(trial["id"], "trial"))
    return workspace, campaign, trials


def select(workspace, campaign, trial_ids, *, command_id="choose_finalists", study_id=None, **payload):
    current = workspace.store.get(campaign["id"], "campaign")
    return workspace.commands.execute(Command(id=command_id, campaign_id=campaign["id"], expected_revision=current["version"],
        operation="finalist.set", payload={"study_id": study_id or campaign["active_study_id"], "trial_ids": trial_ids, **payload}))


def test_shortlist_persists_seed_groups_distinct_caps_and_auditable_revisions(tmp_path):
    workspace, campaign, trials = prepare(tmp_path)
    ids = [trial["id"] for trial in trials]
    assert len({method_identity(trial)[0] for trial in trials}) == 1
    before = {kind: workspace.store.list(kind) for kind in ("trial", "hypothesis", "research_run", "manager_command", "outbox", "nomination")}
    receipt = select(workspace, campaign, ids, label="Promising coordinate procedures", expected_revision=0)
    selection = workspace.store.get(receipt["outcome"]["finalist_selection_id"], "finalist_selection")
    assert selection["trial_ids"] == ids
    assert selection["prototype_trial_ids"] == [ids[0], ids[2]]
    assert selection["entries"][0]["source_trial_ids"] == ids[:2]
    assert len(selection["selected_method_ids"]) == 1
    assert len(selection["selected_procedure_ids"]) == 2
    assert selection["entries"][1]["procedure"] == method_definition(trials[2])
    for kind, original in before.items():
        assert workspace.store.list(kind) == original
    restarted = Workspace(tmp_path)
    assert restarted.store.get(selection["id"]) == selection
    assert select(restarted, campaign, ids, label=selection["label"], expected_revision=0) == receipt
    cleared = select(restarted, campaign, [], command_id="clear_finalists", expected_revision=1)
    current = restarted.store.get(selection["id"])
    assert cleared["outcome"]["revision"] == 2
    assert current["trial_ids"] == current["prototype_trial_ids"] == []
    assert current["label"] == selection["label"]
    history = restarted.store.list("finalist_selection_revision", campaign["id"])
    assert len(history) == 2 and history[0]["trial_ids"] == ids
    with pytest.raises(ValueError, match="immutable"):
        restarted.store.put("finalist_selection_revision", {**history[0], "label": "rewrite history"})


def test_selection_rejects_stale_shortlist_and_stale_procedure(tmp_path):
    workspace, campaign, trials = prepare(tmp_path)
    ids = [trials[0]["id"]]
    select(workspace, campaign, ids, expected_revision=0)
    with pytest.raises(ValueError, match="selection changed"):
        select(workspace, campaign, [], command_id="stale_selection", expected_revision=0)
    old = content_hash(method_definition(trials[0]))
    changed = {**trials[0], "wall_seconds": 10}
    workspace.store.put("trial", changed)
    with pytest.raises(ValueError, match="prototype procedure changed"):
        select(workspace, campaign, ids, command_id="stale_trial", expected_revision=1,
            expected_procedure_ids={ids[0]: old})
    assert workspace.store.list("finalist_selection")[0]["revision"] == 1


@pytest.mark.parametrize("changes, expected", [
    ({"locked": True}, "Protected"), ({"task_split": "test"}, "Protected"),
    ({"protected_cohort_id": "hidden_cohort"}, "Protected"),
    ({"confirmation_protocol_id": "protocol"}, "Protected"),
    ({"diagnostic_grant_id": "check"}, "diagnostic"),
    ({"status": "running"}, "completed"),
    ({"scientific_source_hash": None}, "source/runtime"),
    ({"method_contract": 3}, "cell-specific"),
])
def test_ineligible_runs_cannot_become_finalists(tmp_path, changes, expected):
    workspace, campaign, trials = prepare(tmp_path)
    workspace.store.put("trial", {**trials[0], **changes})
    with pytest.raises(ValueError, match=expected):
        select(workspace, campaign, [trials[0]["id"]])
    assert not workspace.store.list("finalist_selection")


def test_campaign_study_and_researcher_boundaries(tmp_path):
    workspace, campaign, trials = prepare(tmp_path)
    other = workspace.create_campaign(CampaignInput(name="Another campaign",
        tasks=[TaskInput(name="Other instance", problem_id="bounded_continuous")]))
    with pytest.raises(ValueError, match="another campaign"):
        select(workspace, other, [], study_id=campaign["active_study_id"])
    with pytest.raises(ValueError, match="campaign and exploratory study"):
        select(workspace, other, [trials[0]["id"]])
    second = workspace.create_study(campaign["id"], StudyInput(goal="New exploratory scope"))
    with pytest.raises(ValueError, match="campaign and exploratory study"):
        select(workspace, campaign, [trials[0]["id"]], study_id=second["id"])
    current = workspace.store.get(campaign["id"])
    command = Command(id="manager_cannot_select", campaign_id=campaign["id"], operation="finalist.set",
        expected_revision=current["version"], expected_guidance_revision=0,
        expected_authority_hash=workspace.commands.authority_hash(current),
        payload={"study_id": campaign["active_study_id"], "trial_ids": [trials[0]["id"]]})
    with pytest.raises(ValueError, match="researcher authorization"):
        workspace.commands.execute(command, actor="manager")
    assert not workspace.commands.describe()["finalist.set"]["delegable"]


def test_protected_task_cannot_hide_behind_visible_trial_fields(tmp_path):
    workspace, campaign, trials = prepare(tmp_path)
    task = workspace.store.get(trials[0]["task_id"])
    workspace.store.put("task", {**task, "split": "test"})
    with pytest.raises(ValueError, match="Protected"):
        select(workspace, campaign, [trials[0]["id"]])


def test_confirmation_preserves_shortlist_provenance_without_later_changes(tmp_path):
    workspace, campaign, trials = prepare(tmp_path)
    select(workspace, campaign, [trial["id"] for trial in trials[:2]])
    selection = workspace.store.list("finalist_selection")[0]
    # Another saved seed is a valid representative; an extra control stays allowed.
    study = workspace.create_study(campaign["id"], StudyInput(goal="Replicate finalists and a longer control", scope="confirmation",
        confirmation_kind="seed_replication", seeds=[99], prototype_trial_ids=[trials[1]["id"], trials[2]["id"]],
        finalist_selection_id=selection["id"], finalist_selection_revision=selection["revision"]))
    protocol = deepcopy(workspace.store.get(study["confirmation"]["id"]))
    assert len(protocol["methods"]) == 2
    binding = workspace.store.list("finalist_confirmation_binding")[0]
    assert binding["study_id"] == study["id"]
    assert binding["selection_revision"] == 1
    assert binding["prototype_procedure_ids"] == {trials[1]["id"]: content_hash(method_definition(trials[1]))}
    assert workspace.store.get(binding["selection_revision_id"], "finalist_selection_revision")["trial_ids"] == selection["trial_ids"]
    select(workspace, campaign, [], command_id="revise_after_freeze", expected_revision=1)
    assert workspace.store.get(protocol["id"]) == protocol
    assert workspace.store.get(binding["id"]) == binding
    assert not workspace.store.list("nomination")


def test_confirmation_rejects_changed_or_missing_imported_sources(tmp_path):
    workspace, campaign, trials = prepare(tmp_path)
    select(workspace, campaign, [trials[0]["id"]])
    selection = workspace.store.list("finalist_selection")[0]
    base = dict(goal="Confirm imported shortlist", scope="confirmation", confirmation_kind="seed_replication", seeds=[99],
        prototype_trial_ids=[trials[0]["id"]], finalist_selection_id=selection["id"], finalist_selection_revision=1)
    with pytest.raises(ValueError, match="no longer includes"):
        workspace.create_study(campaign["id"], StudyInput(**{**base, "prototype_trial_ids": [trials[2]["id"]]}))
    workspace.store.put("trial", {**trials[0], "wall_seconds": 7})
    with pytest.raises(ValueError, match="prototype procedure changed"):
        workspace.create_study(campaign["id"], StudyInput(**base))
    select(workspace, campaign, [trials[0]["id"]], command_id="updated_source", expected_revision=1)
    with pytest.raises(ValueError, match="selection changed"):
        workspace.create_study(campaign["id"], StudyInput(**base))
    assert not workspace.store.list("confirmation_protocol")


def test_comparison_state_and_memory_expose_descriptive_shortlist(tmp_path, monkeypatch):
    from optimization_framework.api.app import create_app
    monkeypatch.setenv("GRATING_LLM_DISABLED", "true")
    app = create_app(tmp_path, start_workers=False)
    workspace, campaign, trials = prepare(tmp_path, workspace=app.state.workspace)
    # A large runtime inventory is frozen once, but never copied into the manager prompt.
    trials[0]["scientific_environment"]["runtime_inventory"] = "x" * 10000
    workspace.store.put("trial", trials[0])
    receipt = select(workspace, campaign, [trials[0]["id"]])
    selection = workspace.store.get(receipt["outcome"]["finalist_selection_id"])
    result = report(workspace, campaign["id"], cost_axis="evaluation_requests")
    row = next(row for group in result["groups"] for row in group["trials"] if row["id"] == trials[0]["id"])
    assert row["finalist_eligible"]
    assert row["procedure"] == method_definition(trials[0])
    assert row["procedure_id"] == selection["selected_procedure_ids"][0]
    memory = workspace.memory.sync(campaign["id"])["structured"]["finalist_selections"]
    assert memory[0]["trial_ids"] == [trials[0]["id"]]
    assert "runtime_inventory" not in json.dumps(memory)
    assert len(json.dumps(memory)) < 2000
    assert "runtime_inventory" not in workspace.memory._text("finalist_selection", selection)
    with TestClient(app) as client:
        response = client.get("/api/state", params={"campaign_id": campaign["id"]})
        assert response.status_code == 200
        assert response.json()["finalist_selections"] == [selection]
