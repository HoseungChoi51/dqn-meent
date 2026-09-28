"""Researcher controls retain authority, original outcomes and committed context."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from optimization_framework.api.app import create_app
from optimization_framework.contracts.base import content_hash
from optimization_framework.contracts.commands import Command
from optimization_framework.contracts.requests import CampaignInput, TrialInput
from optimization_framework.execution.service import Workspace


def creation(identity="create_once", campaign_id="campaign_requested"):
    return Command(id=identity, campaign_id=campaign_id, operation="campaign.create", expected_revision=0,
        payload={"name": "Campaign commands", "compute_budget_seconds": 100, "validation_reserve_seconds": 0,
            "autonomy": "delegated", "llm_budget_usd": 0,
            "tasks": [{"name": "Quadratic", "problem_id": "bounded_continuous", "configuration": {}}]})


def change(workspace, campaign_id, operation, payload, identity="change_once"):
    campaign = workspace.store.get(campaign_id, "campaign")
    return Command(id=identity, campaign_id=campaign_id, operation=operation,
        expected_revision=campaign["version"], payload=payload)


def test_creation_is_atomic_create_if_absent_and_replay_keeps_original_campaign(tmp_path):
    workspace = Workspace(tmp_path)
    request = creation()
    with ThreadPoolExecutor(max_workers=2) as pool:
        accepted = list(pool.map(workspace.commands.execute, [request, request]))
    assert accepted[0] == accepted[1]
    assert [row["id"] for row in workspace.store.list("campaign")] == [request.campaign_id]
    assert len(workspace.store.list("task")) == len(workspace.store.list("study")) == 1
    original = accepted[0]["outcome"]["campaign"]
    workspace.commands.execute(change(workspace, request.campaign_id, "campaign.update", {"name": "Renamed"}))
    restarted = Workspace(tmp_path)
    assert restarted.commands.execute(request) == accepted[0]
    assert restarted.store.get(request.campaign_id, "campaign")["version"] == 2
    assert original["version"] == 1 and original["name"] == "Campaign commands"
    with pytest.raises(ValueError, match="already owns"):
        restarted.commands.execute(creation(identity="different_create"))
    with pytest.raises(ValueError, match="identity"):
        restarted.commands.execute(request.model_copy(update={"payload": {**request.payload, "name": "Changed request"}}))
    assert len(restarted.store.list("campaign")) == 1
    with pytest.raises(ValueError, match="revision zero"):
        Command(**{**request.model_dump(), "expected_revision": 1})
    with pytest.raises(ValueError, match="stable campaign identity"):
        Command(**{**request.model_dump(), "campaign_id": "../escape"})


def test_creation_failure_rolls_back_all_records_and_text(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path)
    apply = workspace.commands._apply
    def fail(command, actor):
        apply(command, actor)
        raise RuntimeError("Commit interrupted")
    monkeypatch.setattr(workspace.commands, "_apply", fail)
    with pytest.raises(RuntimeError, match="Commit interrupted"):
        workspace.commands.execute(creation())
    for kind in ("campaign", "task", "study", "charter", "hypothesis", "work_command", "outbox", "manager_state"):
        assert workspace.store.list(kind) == []
    assert not (tmp_path / "campaigns").exists()


def test_campaign_amendments_preserve_frozen_science_and_reject_stale_updates(tmp_path):
    workspace = Workspace(tmp_path)
    campaign = workspace.commands.execute(creation())["outcome"]["campaign"]
    task = workspace.current_tasks(campaign["id"])[0]
    trial = workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=task["id"], algorithm="coordinate", max_steps=2, wall_seconds=5))
    frozen = workspace.store.get(trial["experiment_spec_id"], "experiment_spec")
    update = change(workspace, campaign["id"], "campaign.update", {"compute_budget_seconds": 200, "rationale": "Authorize the next bounded comparison"})
    outcome = workspace.commands.execute(update)
    assert outcome["outcome"]["campaign"]["active_study_id"] == campaign["active_study_id"]
    amendments = workspace.store.list("campaign_budget_amendment", campaign["id"])
    assert len(amendments) == 1
    assert amendments[0]["previous_limits"] == {"compute_budget_seconds": 100}
    assert amendments[0]["limits"] == {"compute_budget_seconds": 200}
    assert amendments[0]["authority"] == "researcher"
    assert amendments[0]["rationale"] == update.payload["rationale"]
    with pytest.raises(ValueError, match="revision changed"):
        workspace.commands.execute(update.model_copy(update={"id": "stale_update"}))
    scientific = workspace.commands.execute(change(workspace, campaign["id"], "campaign.update", {"objective": "A new scientific question"}, "new_science"))
    study = workspace.store.get(scientific["outcome"]["campaign"]["active_study_id"], "study")
    assert study["parent_study_id"] == campaign["active_study_id"]
    assert workspace.store.get(trial["experiment_spec_id"], "experiment_spec") == frozen
    assert workspace.commands.execute(update) == outcome
    assert len(workspace.store.list("campaign_budget_amendment")) == 1


def test_context_edit_and_projection_commit_with_command_and_recover_once(tmp_path, monkeypatch):
    workspace = Workspace(tmp_path)
    campaign = workspace.commands.execute(creation())["outcome"]["campaign"]
    before = workspace.memory.sync(campaign["id"])
    projection = tmp_path / "campaigns" / campaign["id"] / "manager" / "context.md"
    old_text = projection.read_text()
    edit = change(workspace, campaign["id"], "context.edit", {"content": "Keep the selected baseline fixed.", "expected_revision": before["revision"]})
    apply = workspace.commands._apply
    def fail(command, actor):
        apply(command, actor)
        raise RuntimeError("Commit interrupted")
    with monkeypatch.context() as patch:
        patch.setattr(workspace.commands, "_apply", fail)
        with pytest.raises(RuntimeError):
            workspace.commands.execute(edit)
    assert projection.read_text() == old_text
    assert workspace.memory.state(campaign["id"])["guidance_revision"] == before["guidance_revision"]
    assert workspace.store.list("manager_note") == []
    # Simulate process loss after the scientific transaction, before projection.
    with monkeypatch.context() as patch:
        patch.setattr(workspace, "dispatch_outbox", lambda: None)
        accepted = workspace.commands.execute(edit)
    assert projection.read_text() == old_text
    restarted = Workspace(tmp_path)
    restarted.dispatch_outbox()
    assert "Keep the selected baseline fixed." in projection.read_text()
    assert restarted.commands.execute(edit) == accepted
    notes = restarted.store.list("manager_note", campaign["id"])
    assert len(notes) == 1 and notes[0]["author"] == "researcher"
    assert restarted.memory.state(campaign["id"])["guidance_revision"] == before["guidance_revision"] + 1
    projection.unlink()
    restored = restarted.memory.sync(campaign["id"])
    assert projection.read_text() == restored["document"]
    with pytest.raises(ValueError, match="memory changed"):
        restarted.commands.execute(edit.model_copy(update={"id": "stale_edit"}))


@pytest.mark.parametrize("operation", ["campaign.create", "campaign.update", "context.edit", "issue.resolve"])
def test_researcher_controls_cannot_be_delegated_or_spoofed(tmp_path, operation):
    workspace = Workspace(tmp_path)
    create = creation()
    if operation == "campaign.create":
        command = create
    else:
        campaign = workspace.commands.execute(create)["outcome"]["campaign"]
        command = change(workspace, campaign["id"], operation, {})
        command = command.model_copy(update={"expected_guidance_revision": workspace.memory.state(campaign["id"])["guidance_revision"],
            "expected_authority_hash": workspace.commands.authority_hash(campaign)})
    with pytest.raises(ValueError, match="researcher"):
        workspace.commands.execute(command, actor="manager")
    assert workspace.store.list("command_rejection")[-1]["actor"] == "manager"
    assert len(workspace.store.list("campaign")) == (0 if operation == "campaign.create" else 1)
    with pytest.raises(ValueError, match="Extra inputs"):
        Command(**{**command.model_dump(), "actor": "researcher"})


def test_issue_resolution_checks_scope_and_occurrence_and_replay_cannot_close_new_issue(tmp_path):
    workspace = Workspace(tmp_path)
    owner = workspace.commands.execute(creation())["campaign_id"]
    other = workspace.commands.execute(creation("other_create", "campaign_other"))["campaign_id"]
    first = workspace.memory.issue(owner, "missing_runtime", "Install the matching runtime", affected="proposal_one")
    payload = {"issue_id": first["id"], "expected_revision": first["revision"], "choice": "deferred", "comment": "Continue the independent experiment"}
    with pytest.raises(ValueError, match="another campaign"):
        workspace.commands.execute(change(workspace, other, "issue.resolve", payload, "foreign"))
    stale = change(workspace, owner, "issue.resolve", payload, "stale_issue")
    current = workspace.memory.issue(owner, "missing_runtime", "The matching runtime is damaged", affected="proposal_one")
    with pytest.raises(ValueError, match="issue changed"):
        workspace.commands.execute(stale)
    command = change(workspace, owner, "issue.resolve", {**payload, "expected_revision": current["revision"]}, "resolve_current")
    guidance = workspace.memory.state(owner)["guidance_revision"]
    accepted = workspace.commands.execute(command)
    assert accepted["outcome"]["issue"]["status"] == "deferred"
    assert workspace.memory.state(owner)["guidance_revision"] == guidance + 1
    reopened = workspace.memory.issue(owner, "missing_runtime", "A different runtime requirement is now missing", affected="proposal_one")
    assert workspace.commands.execute(command) == accepted
    assert workspace.store.get(first["id"], "manager_issue") == reopened
    assert len([note for note in workspace.store.list("manager_note") if note["kind"] == "decision"]) == 1


def test_compatibility_routes_share_command_preconditions_and_original_retry_outcomes(tmp_path, monkeypatch):
    monkeypatch.setenv("GRATING_LLM_DISABLED", "true")
    app = create_app(tmp_path, start_workers=False)
    with TestClient(app) as client:
        key = {"Idempotency-Key": "create-request"}
        created = client.post("/api/campaigns", json=creation().payload, headers=key)
        assert created.status_code == 201, created.text
        campaign = created.json()
        assert client.post("/api/campaigns", json=creation().payload, headers=key).json() == campaign
        route = "/api/campaigns/" + campaign["id"]
        updated = client.put(route, json={"name": "Second charter"}, headers={"Idempotency-Key": "update-request", "X-Campaign-Revision": "1"})
        assert updated.status_code == 200 and updated.json()["version"] == 2
        assert client.put(route, json={"name": "Third charter"}, headers={"Idempotency-Key": "third-request"}).status_code == 200
        assert client.put(route, json={"name": "Second charter"}, headers={"Idempotency-Key": "update-request"}).json() == updated.json()
        assert client.post("/api/campaigns", json=creation().payload, headers=key).json() == campaign
        assert client.put(route, json={"name": "Stale"}, headers={"Idempotency-Key": "stale", "X-Campaign-Revision": "1"}).status_code == 409
        assert client.put(route, json={"name": "Different request"}, headers={"Idempotency-Key": "update-request"}).status_code == 409
        context = client.get(route + "/manager/context").json()
        body = {"content": "Keep research in the current problem family.", "expected_revision": context["revision"]}
        headers = {"Idempotency-Key": "edit-context"}
        edited = client.put(route + "/manager/context", json=body, headers=headers)
        assert edited.status_code == 200, edited.text
        assert client.put(route + "/manager/context", json=body, headers=headers).json() == edited.json()
        issue = app.state.workspace.memory.issue(campaign["id"], "test", "One scoped failure", affected="one")
        issue_route = "/api/manager/issues/" + issue["id"] + "/resolve"
        resolved = client.post(issue_route, json={"choice": "deferred"}, headers={"Idempotency-Key": "resolve"})
        assert resolved.status_code == 200, resolved.text
        assert client.post(issue_route, json={"choice": "deferred"}, headers={"Idempotency-Key": "resolve"}).json() == resolved.json()
        operations = {row["request"]["operation"] for row in app.state.workspace.store.list("work_command")}
        assert operations == {"campaign.create", "campaign.update", "context.edit", "issue.resolve"}
        spoofed = {**creation("spoofed", "campaign_spoofed").model_dump(), "actor": "manager"}
        assert client.post("/api/v1/commands", json=spoofed).status_code == 422
        assert len(app.state.workspace.store.list("campaign")) == 1


def test_accepted_historical_envelope_hash_and_outcome_stay_readable(tmp_path):
    workspace = Workspace(tmp_path)
    legacy = {"schema_version": 1, "id": "legacy_command", "campaign_id": "old_campaign", "operation": "trial.create",
        "expected_revision": 1, "expected_guidance_revision": None, "expected_authority_hash": None,
        "proposal_digest": None, "payload": {"task_id": "old_task", "algorithm": "coordinate", "max_steps": 2, "wall_seconds": 5}}
    original = {"id": legacy["id"], "campaign_id": legacy["campaign_id"], "request": deepcopy(legacy),
        "request_hash": content_hash({"command": legacy, "actor": "researcher"}), "actor": "researcher",
        "status": "completed", "outcome": {"trial_id": "old_trial"}}
    workspace.store.put("work_command", original)
    assert Command(**legacy).model_dump(mode="json") == legacy
    assert workspace.commands.execute(legacy) == original
    assert workspace.store.list("trial") == []
