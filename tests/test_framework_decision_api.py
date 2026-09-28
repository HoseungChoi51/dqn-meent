"""The public inbox explains expired execution authority before a user approves."""
from copy import deepcopy
import json

from fastapi.testclient import TestClient

from optimization_framework.api.app import create_app
from optimization_framework.contracts.requests import CampaignInput, CampaignUpdate, TaskInput
from optimization_framework.storage.sqlite import now


def expired_inbox(tmp_path, monkeypatch):
    monkeypatch.setenv("GRATING_LLM_DISABLED", "true")
    app = create_app(tmp_path, start_workers=False)
    workspace = app.state.workspace
    campaign = workspace.create_campaign(CampaignInput(name="Decision expiry", autonomy="delegated",
        tasks=[TaskInput(name="Development", problem_id="bounded_continuous")]))
    action = {"id": "old_action", "campaign_id": campaign["id"], "kind": "command",
        "command_operation": "campaign.update", "command_payload": {"compute_budget_seconds": 7200},
        "title": "An older allocation", "rationale": "Earlier assumptions", "status": "proposed",
        "requires_researcher": True, "charter_version": campaign["version"],
        "guidance_revision": workspace.memory.state(campaign["id"])["guidance_revision"],
        "authority_hash": workspace.commands.authority_hash(campaign)}
    decision = {"id": "old_decision", "campaign_id": campaign["id"], "action_id": action["id"],
        "title": "Consider the older allocation", "context": "Preserve this original reasoning.",
        "charter_version": campaign["version"], "status": "pending", "created_at": now(),
        "options": [{"id": "accept", "label": "Proceed"}, {"id": "defer", "label": "Defer"},
                    {"id": "reject", "label": "Decline"}], "recommendation": "accept"}
    workspace.store.put("action", action)
    workspace.store.put("decision", decision)
    campaign = workspace.update_campaign(campaign["id"], CampaignUpdate(objective="Use the new study scope."))
    return app, workspace, campaign, action, decision


def test_http_inbox_exposes_expiry_without_rewriting_original_authority(tmp_path, monkeypatch):
    app, workspace, campaign, action, decision = expired_inbox(tmp_path, monkeypatch)
    original = deepcopy(decision)
    with TestClient(app) as client:
        response = client.get("/api/v1/state", params={"campaign_id": campaign["id"]})
        assert response.status_code == 200
        view = next(row for row in response.json()["decisions"] if row["id"] == decision["id"])
        assert view["freshness"]["stale"] is True
        assert view["freshness"]["can_accept"] is False
        assert view["freshness"]["can_refresh"] is True
        assert view["freshness"]["blocked_choice_ids"] == ["accept"]
        assert view["freshness"]["proposal_charter_version"] == 1
        assert view["freshness"]["current_charter_version"] == 2
        assert view["freshness"]["reason"]
    assert workspace.store.get(decision["id"], "decision") == original
    assert workspace.store.get(action["id"], "action") == action


def test_http_refresh_saves_comment_and_queues_review_without_executing_old_budget(tmp_path, monkeypatch):
    app, workspace, campaign, action, decision = expired_inbox(tmp_path, monkeypatch)
    request = {"id": "refresh_from_inbox", "campaign_id": campaign["id"],
        "operation": "decision.refresh", "expected_revision": campaign["version"],
        "payload": {"decisions": [{"decision_id": decision["id"], "expected_resolution_revision": 0,
            "desired_choice": "accept", "comment": "Keep the reserve and review the new scope first."}]}}
    with TestClient(app) as client:
        first = client.post("/api/v1/commands", json=request)
        assert first.status_code == 200, first.text
        replay = client.post("/api/v1/commands", json=request)
        assert replay.status_code == 200 and replay.json() == first.json()
        view = client.get("/api/v1/state", params={"campaign_id": campaign["id"]}).json()
    commands = workspace.store.list("manager_command", campaign["id"])
    assert len(commands) == 1
    snapshot = workspace.store.get(first.json()["outcome"]["refresh_id"], "decision_refresh")
    assert commands[0]["decision_refresh_id"] == snapshot["id"]
    assert snapshot["decisions"][0]["comment"] == "Keep the reserve and review the new scope first."
    assert not workspace.store.list("trial", campaign["id"])
    assert workspace.store.get(campaign["id"], "campaign")["compute_budget_seconds"] == campaign["compute_budget_seconds"]
    assert workspace.store.get(action["id"], "action") == action
    row = next(row for row in view["decisions"] if row["id"] == decision["id"])
    assert row["freshness"]["state"] == "updating"
    assert row["freshness"]["can_accept"] is False
    assert row["freshness"]["can_refresh"] is False
    assert row["freshness"]["refresh_command_id"]
    workspace.memory.sync(campaign["id"], force=True)
    journal = workspace.directory / "campaigns" / campaign["id"] / "manager" / "records.jsonl"
    projected = next(json.loads(line) for line in journal.read_text().splitlines()
        if json.loads(line).get("kind") == "decision_refresh")
    assert projected["decisions"][0] == {"decision_id": decision["id"], "action_id": action["id"],
        "desired_choice": "accept", "comment": "Keep the reserve and review the new scope first."}
    assert snapshot["decisions"][0]["action"] == action
