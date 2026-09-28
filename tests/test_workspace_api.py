"""HTTP contracts and researcher decisions, without any paid LLM calls."""
import time

from fastapi.testclient import TestClient
import pytest

from dqn_meent.workspace.api import create_app
from dqn_meent.workspace.custom_optimizer import sandbox_status
from dqn_meent.workspace.store import identifier, now


SOURCE = '''
def initialize(n_cells, seed, config):
    return {"n": n_cells, "index": seed}
def propose(state):
    state["index"] += 1
    return {"design": [(state["index"] >> i) & 1 for i in range(state["n"])], "state": state}
def observe(state, design, efficiency):
    return state
'''


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GRATING_LLM_DISABLED", "true")
    app = create_app(tmp_path / "workspace", start_workers=False)
    with TestClient(app) as browser:
        yield browser
    for thread in app.state.workspace.research_threads.values():
        thread.join(timeout=5)


def create_campaign(client, name="API campaign"):
    response = client.post("/api/campaigns", json={"name": name, "llm_budget_usd": 0,
        "compute_budget_seconds": 500, "validation_reserve_seconds": 30,
        "tasks": [{"name": "Development", "physics": {"n_cells": 6, "fourier_order": 1}},
                  {"name": "Locked confirmation", "split": "test", "physics": {"n_cells": 6, "fourier_order": 2}}]})
    assert response.status_code == 201, response.text
    charter = response.json()
    state = client.get("/api/state", params={"campaign_id": charter["id"]}).json()
    return charter, state["tasks"]


def test_state_cursor_advances_beyond_one_event_page_without_skipping_sse_history(client):
    campaign, _ = create_campaign(client)
    store = client.app.state.workspace.store
    identities = [store.event(campaign["id"], "test.progress", {"sequence": index}) for index in range(250)]
    state = client.get("/api/v1/state", params={"campaign_id": campaign["id"]}).json()
    assert state["event_cursor"] == identities[-1]
    assert len(state["events"]) == 200 and state["events"][-1]["id"] == identities[-1]
    # The catch-up API keeps its ascending cursor behavior independently.
    assert store.events(campaign["id"], after=identities[0], limit=3)[0]["id"] == identities[1]
    latest = store.event(campaign["id"], "test.released", {})
    refreshed = client.get("/api/v1/state", params={"campaign_id": campaign["id"]}).json()
    assert refreshed["event_cursor"] >= latest > state["event_cursor"]


def hypothesis(client, charter, **kwargs):
    payload = {"campaign_id": charter["id"], "title": "Mechanism with explicit assumptions",
               "algorithm": "block_tabu", "algorithm_config": {"max_block_size": 3},
               "mechanism": "Escape measured single-bit plateaus with coordinated moves.",
               "rationale": "Contiguous interactions may reward block proposals.",
               "assumptions": ["Unverified: block locality"], "risks": ["Nonlocal optical effects"]}
    payload.update(kwargs)
    response = client.post("/api/hypotheses", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def trial_payload(charter, task, **kwargs):
    payload = {"campaign_id": charter["id"], "task_id": task["id"], "algorithm": "random",
               "wall_seconds": 10, "max_steps": 8}
    payload.update(kwargs)
    return payload


def pending_decision(client, charter, task, *, title="Run the discriminating probe"):
    store = client.app.state.workspace.store
    action = {"id": identifier("action"), "campaign_id": charter["id"], "charter_version": charter["version"],
              "kind": "probe", "title": title, "rationale": "A paired neighborhood probe can test the mechanism.",
              "question": "Does the new proposal improve the plateau?", "task_id": task["id"],
              "algorithm": "random", "budget_calls": 8, "status": "proposed"}
    decision = {"id": identifier("decision"), "campaign_id": charter["id"], "charter_version": charter["version"],
                "title": title, "context": action["rationale"], "status": "pending", "action_id": action["id"],
                "options": [{"id": value, "label": value} for value in ("accept", "defer", "reject")],
                "created_at": now()}
    store.put("action", action)
    store.put("decision", decision)
    return decision


def test_campaign_revision_keeps_trial_physics_and_archives_old_tasks(client):
    charter, tasks = create_campaign(client)
    response = client.post("/api/trials", json=trial_payload(charter, tasks[0]))
    assert response.status_code == 201, response.text
    original_trial = response.json()
    changed = client.put(f"/api/campaigns/{charter['id']}", json={"objective": "Compare more complex binary devices",
        "tasks": [{"name": "Revised development", "physics": {"n_cells": 8, "fourier_order": 1}}]})
    assert changed.status_code == 200, changed.text
    assert changed.json()["version"] == 2
    state = client.get("/api/state", params={"campaign_id": charter["id"]}).json()
    assert len(state["tasks"]) == 1 and state["tasks"][0]["physics"]["n_cells"] == 8
    retained = next(item for item in state["trials"] if item["id"] == original_trial["id"])
    assert retained["physics"]["n_cells"] == 6
    assert retained["charter_version"] == 1 and retained["charter_superseded"]
    assert client.post("/api/trials", json=trial_payload(charter, tasks[0])).status_code == 409
    versions = client.app.state.workspace.store.list("charter", charter["id"])
    assert [item["version"] for item in versions] == [1, 2]


def test_external_origins_and_invalid_inputs_cannot_launch_jobs(client):
    charter, tasks = create_campaign(client)
    blocked = client.post("/api/trials", json=trial_payload(charter, tasks[0]), headers={"Origin": "https://unrelated.example"})
    assert blocked.status_code == 403
    invalid = client.post("/api/trials", json=trial_payload(charter, tasks[0], max_steps=-1))
    assert invalid.status_code == 422
    assert client.get("/api/trials/missing/artifacts/checkpoint.pkl").status_code == 404
    assert client.app.state.workspace.store.list("trial", charter["id"]) == []


def test_lineage_and_researcher_reviews_preserve_parent_and_source_provenance(client):
    charter, _ = create_campaign(client)
    source = client.post("/api/sources", json={"campaign_id": charter["id"], "title": "Researcher reference",
        "url": "https://example.org/paper", "excerpt": "A supplied excerpt", "supports": "Mechanism precedent only"})
    assert source.status_code == 201
    assert source.json()["verification"] == "researcher_supplied_unverified"
    parent = hypothesis(client, charter, sources=[source.json()])
    child = hypothesis(client, charter, title="Boundary-aware revision", parent_ids=[parent["id"]],
                       algorithm_config={"max_block_size": 5})
    reviewed = client.post(f"/api/hypotheses/{child['id']}/review", json={"text": "The cheap probe must distinguish boundary and arbitrary moves."})
    assert reviewed.status_code == 200
    assert reviewed.json()["reviews"][-1]["author"] == "researcher"
    assert client.post(f"/api/hypotheses/{child['id']}/status", json={"status": "archived"}).status_code == 200
    store = client.app.state.workspace.store
    assert store.get(parent["id"], "hypothesis") == parent
    assert store.get(child["id"], "hypothesis")["parent_ids"] == [parent["id"]]
    other, _ = create_campaign(client, name="Separate campaign")
    rejected = client.post("/api/hypotheses", json={"campaign_id": other["id"], "title": "Invalid cross-campaign fork", "parent_ids": [parent["id"]]})
    assert rejected.status_code == 409


def test_saved_feedback_is_not_a_model_call_and_revision_captures_exact_notes(client, monkeypatch):
    charter, _ = create_campaign(client)
    parent = hypothesis(client, charter)
    coordinator = client.app.state.coordinator
    store = client.app.state.workspace.store
    monkeypatch.setattr(coordinator, "_thread", lambda record: None)
    monkeypatch.setattr("dqn_meent.workspace.coordinator.provider_status", lambda: {"configured": True})
    saved = client.post(f"/api/hypotheses/{parent['id']}/review", json={"text": "Keep block moves, but remove the surrogate startup cost."})
    note = saved.json()["reviews"][-1]
    assert note["author"] == "researcher"
    assert store.list("research_run", charter["id"]) == []
    assert client.post(f"/api/hypotheses/{parent['id']}/review", json={"text": "  \n "}).status_code == 422
    submitted = client.post("/api/research", json={"campaign_id": charter["id"], "mode": "evolve",
        "hypothesis_id": parent["id"], "feedback_review_ids": [note["id"]], "message": "Revise using my feedback."})
    assert submitted.status_code == 202, submitted.text
    run = store.get(submitted.json()["id"], "research_run")
    assert run["context_snapshot"]["revision_context"] == {"hypothesis_id": parent["id"], "reviews": [note]}
    client.post(f"/api/hypotheses/{parent['id']}/review", json={"text": "Use this later comment in the next round."})
    frozen = store.get(run["id"], "research_run")["context_snapshot"]
    assert frozen["revision_context"]["reviews"] == [note]
    assert len(next(h for h in frozen["hypotheses"] if h["id"] == parent["id"])["reviews"]) == 1
    assert store.list("trial", charter["id"]) == []


def test_revision_queues_disabled_model_but_rejects_foreign_feedback_and_archived_ideas(client, monkeypatch):
    charter, _ = create_campaign(client)
    parent = hypothesis(client, charter)
    other = hypothesis(client, charter, title="A separate idea")
    note = client.post(f"/api/hypotheses/{parent['id']}/review", json={"text": "Prefer simpler proposals."}).json()["reviews"][-1]
    payload = {"campaign_id": charter["id"], "mode": "evolve", "hypothesis_id": parent["id"],
               "feedback_review_ids": [note["id"]], "message": "Revise this idea."}
    response = client.post("/api/research", json=payload)
    assert response.status_code == 202
    assert response.json()["status"] == "waiting_provider"
    assert response.json()["feedback_snapshot"] == [note]
    monkeypatch.setattr("dqn_meent.workspace.coordinator.provider_status", lambda: {"configured": True})
    assert client.post("/api/research", json={**payload, "hypothesis_id": other["id"]}).status_code == 409
    assert client.post("/api/research", json={**payload, "feedback_review_ids": ["invented-review"]}).status_code == 409
    assert client.post("/api/research", json={**payload, "mode": "review"}).status_code == 409
    assert client.post("/api/research", json={**payload, "hypothesis_id": None}).status_code == 409
    second_campaign, _ = create_campaign(client, name="Unrelated workspace")
    assert client.post("/api/research", json={**payload, "campaign_id": second_campaign["id"]}).status_code == 409
    client.post(f"/api/hypotheses/{parent['id']}/status", json={"status": "archived"})
    response = client.post("/api/research", json=payload)
    assert response.status_code == 409 and "Revive" in response.json()["detail"]
    store = client.app.state.workspace.store
    assert store.get(parent["id"], "hypothesis")["reviews"] == [note]
    assert store.list("research_run", charter["id"]) == []


@pytest.mark.parametrize("mode", ["review", "evolve"])
def test_targeted_agent_work_saves_critiques_and_never_launches_delegated_trials(client, monkeypatch, mode):
    charter, tasks = create_campaign(client)
    client.put(f"/api/campaigns/{charter['id']}", json={"autonomy": "delegated"})
    parent = hypothesis(client, charter)
    note = client.post(f"/api/hypotheses/{parent['id']}/review", json={"text": "Explain how locality affects this method."}).json()["reviews"][-1]
    coordinator = client.app.state.coordinator
    store = client.app.state.workspace.store
    monkeypatch.setattr(coordinator, "_thread", lambda record: None)
    monkeypatch.setattr("dqn_meent.workspace.coordinator.provider_status", lambda: {"configured": True})
    submitted = client.post("/api/research", json={"campaign_id": charter["id"], "mode": mode,
        "hypothesis_id": parent["id"], "message": "Assess this idea and address my comments."})
    assert submitted.status_code == 202, submitted.text
    run_id = submitted.json()["id"]
    snapshot = store.get(run_id, "research_run")["context_snapshot"]
    children = []
    if mode == "evolve":
        assert snapshot["revision_context"]["reviews"] == [note]
        children = [{"id": identifier("hypothesis"), "title": "A revised mechanism", "parent_ids": [parent["id"]],
                     "revision_context": snapshot["revision_context"], "change_summary": "Adds a locality diagnostic.",
                     "feedback_response": "The diagnostic tests your locality concern before restricting moves.",
                     "status": "proposed", "reviews": []}]
    result = {"status": "completed", "mode": "llm", "hypotheses": children, "messages": [], "decisions": [],
              "provider": {"model": "gpt-6-sol"}, "usage": {"calls": 2, "billing_mode": "subscription"},
              "research_state": {"role_results": [{"role": "assumption_reviewer", "analysis": "Locality needs evidence; compare against arbitrary moves."}]},
              "actions": [{"id": identifier("action"), "kind": "probe", "title": "A suggested locality check",
                           "rationale": "Distinguish locality from general coordinated moves.", "task_id": tasks[0]["id"],
                           "hypothesis_id": parent["id"], "algorithm": "random", "budget_calls": 8}]}
    monkeypatch.setattr("dqn_meent.workspace.research.run_research", lambda *args: result)
    coordinator._run(run_id)
    run = store.get(run_id, "research_run")
    assert run["status"] == "completed", run
    revised_parent = store.get(parent["id"], "hypothesis")
    assert revised_parent["mechanism"] == parent["mechanism"]
    assert revised_parent["reviews"][0] == note
    critique = revised_parent["reviews"][1]
    assert critique["author"] == "agent" and critique["research_run_id"] == run_id
    assert "Locality needs evidence" in critique["text"]
    coordinator._save_critiques(run, result)
    assert len(store.get(parent["id"], "hypothesis")["reviews"]) == 2
    assert store.list("trial", charter["id"]) == []
    assert len(store.list("decision", charter["id"])) == 1
    if children:
        child = store.get(children[0]["id"], "hypothesis")
        assert child["revision_context"]["reviews"] == [note]
        assert child["research_run_id"] == run_id


def test_hidden_test_trials_require_matching_frozen_finalist_and_are_absent_from_research_context(client):
    charter, tasks = create_campaign(client)
    test_task = next(item for item in tasks if item["split"] == "test")
    assert client.post("/api/trials", json=trial_payload(charter, test_task)).status_code == 409
    finalist = hypothesis(client, charter, status="finalist")
    mismatch = client.post("/api/trials", json=trial_payload(charter, test_task, algorithm="random",
        confirmatory=True, hypothesis_id=finalist["id"]))
    assert mismatch.status_code == 409, "A finalist id must not authorize a different algorithm"
    altered = client.post("/api/trials", json=trial_payload(charter, test_task, algorithm="block_tabu",
        algorithm_config={"max_block_size": 5}, confirmatory=True, hypothesis_id=finalist["id"]))
    assert altered.status_code == 409, "Confirmatory parameters must match the frozen method"
    accepted = client.post("/api/trials", json=trial_payload(charter, test_task, algorithm="block_tabu",
        algorithm_config=finalist["algorithm_config"], confirmatory=True, hypothesis_id=finalist["id"]))
    assert accepted.status_code == 201, accepted.text
    context = client.app.state.coordinator.context(charter["id"])
    assert test_task["id"] not in {item["id"] for item in context["tasks"]}
    assert accepted.json()["id"] not in {item["id"] for item in context["trials"]}


def test_researcher_decision_launches_once_and_keeps_override_in_notebook(client):
    charter, tasks = create_campaign(client)
    decision = pending_decision(client, charter, tasks[0])
    accepted = client.post(f"/api/decisions/{decision['id']}/resolve", json={"choice": "accept", "comment": "Run this probe before extending the surrogate."})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "resolved"
    assert accepted.json()["outcome"]["trial_id"]
    repeated = client.post(f"/api/decisions/{decision['id']}/resolve", json={"choice": "accept"})
    assert repeated.status_code == 409
    deferred = pending_decision(client, charter, tasks[0], title="Expensive follow-up")
    response = client.post(f"/api/decisions/{deferred['id']}/resolve", json={"choice": "defer", "comment": "Preserve this direction; evidence is insufficient."})
    assert response.status_code == 200
    state = client.get("/api/state", params={"campaign_id": charter["id"]}).json()
    assert len(state["trials"]) == 1
    assert any("evidence is insufficient" in item["content"] for item in state["messages"])
    assert len([item for item in state["decisions"] if item["status"] == "resolved"]) == 2


def test_charter_revision_invalidates_old_action_without_spending_compute(client):
    charter, tasks = create_campaign(client)
    decision = pending_decision(client, charter, tasks[0])
    assert client.put(f"/api/campaigns/{charter['id']}", json={"objective": "Revised scientific question"}).status_code == 200
    stale = client.post(f"/api/decisions/{decision['id']}/resolve", json={"choice": "accept"})
    assert stale.status_code == 409
    assert "charter" in stale.json()["detail"].lower()
    assert client.app.state.workspace.store.list("trial", charter["id"]) == []


def test_offline_requests_are_saved_and_exported_without_fake_model_turns(client):
    charter, _ = create_campaign(client)
    submitted = client.post("/api/research", json={"campaign_id": charter["id"], "mode": "generate",
        "message": "Investigate boundary-preserving block moves before a long DQN training run."})
    assert submitted.status_code == 202, submitted.text
    store = client.app.state.workspace.store
    assert submitted.json()["status"] == "waiting_provider"
    assert store.list("research_run", charter["id"]) == []
    state = client.get("/api/state", params={"campaign_id": charter["id"]}).json()
    assert not state["settings"]["llm_configured"]
    assert any("boundary-preserving" in str(item).lower() for item in state["manager_commands"])
    report = client.get(f"/api/campaigns/{charter['id']}/export")
    assert report.status_code == 200
    assert "text/markdown" in report.headers["content-type"]
    assert "boundary-preserving" in report.text.lower()
    assert len(store.list("manager_issue", charter["id"])) == 1
    assert state["trials"] == []


def test_legacy_protocol_verification_does_not_bypass_implementation_validation(client):
    charter, tasks = create_campaign(client)
    custom = hypothesis(client, charter, algorithm="custom", algorithm_config={"parameters": {"variant": "reviewed"}}, source=SOURCE)
    request = trial_payload(charter, tasks[0], algorithm="custom", hypothesis_id=custom["id"])
    assert client.post("/api/trials", json=request).status_code == 409
    client.post(f"/api/hypotheses/{custom['id']}/review", json={"text": "The source implements the dossier and stores all state explicitly."})
    verified = client.post(f"/api/hypotheses/{custom['id']}/verify", json={"n_cells": 8, "seed": 0})
    assert verified.status_code == 409
    assert "implementation compute allocation" in verified.json()["detail"]
    stored = client.app.state.workspace.store.get(custom["id"], "hypothesis")
    stored.update(implementation_status="verified", executable=True, verification={"verified": True})
    client.app.state.workspace.store.put("hypothesis", stored)
    accepted = client.post("/api/trials", json=request)
    assert accepted.status_code == 409
    state = client.get('/api/state').json()
    card = next(h for h in state['hypotheses'] if h['id'] == custom['id'])
    assert card['implementation_readiness']['state'] == 'validation_required'
    assert state['manager_issues']
    substituted = client.post("/api/trials", json={**request, "algorithm_config": {"source": SOURCE + "\n# Unreviewed revision"}})
    assert substituted.status_code == 409


def test_interrupted_provider_reservation_is_closed_once_without_refund_or_replay(client, monkeypatch):
    charter, _ = create_campaign(client)
    revised = client.put(f"/api/campaigns/{charter['id']}", json={"llm_budget_usd": 1})
    assert revised.status_code == 200
    coordinator = client.app.state.coordinator
    workspace = client.app.state.workspace
    monkeypatch.setattr(coordinator, "_thread", lambda record: None)
    monkeypatch.setattr("optimization_framework.research.coordinator.provider_status", lambda: {"configured": True})
    submitted = client.post("/api/research", json={"campaign_id": charter["id"], "message": "A request interrupted after reserving cost"})
    assert submitted.status_code == 202
    run_id = submitted.json()["id"]
    coordinator._emit(run_id, {"type": "provider_call_reserved", "role": "research_synthesizer", "usage": {
        "calls": 1, "cost_usd": .4, "reserved_cost_usd": .4,
        "pending_reservation": {"id": "provider-attempt", "cost_usd_reserved": .4}}})
    workspace.start()
    try:
        state = client.get("/api/state", params={"campaign_id": charter["id"]}).json()
        assert state["budget"]["llm_spent_usd"] == .4
        decision = next(item for item in state["decisions"] if item.get("research_run_id") == run_id)
        assert client.post(f"/api/research_runs/{run_id}/control", json={"action": "resume"}).status_code == 409
        response = client.post(f"/api/decisions/{decision['id']}/resolve", json={"choice": "close_reserved", "comment": "Keep the conservative charge; do not resend."})
        assert response.status_code == 200, response.text
        assert response.json()["outcome"]["reserved_cost_retained"]
        closed = workspace.store.get(run_id, "research_run")
        assert closed["status"] == "closed_uncertain"
        assert closed["usage"]["cost_usd"] == .4
        repeated = client.post(f"/api/decisions/{decision['id']}/resolve", json={"choice": "close_reserved"})
        assert repeated.status_code == 409
        next_run = client.post("/api/research", json={"campaign_id": charter["id"], "message": "Continue using unreserved funds"})
        assert next_run.status_code == 202
        assert next_run.json()["request"]["llm_budget_usd"] == pytest.approx(.6)
        assert client.get("/api/state", params={"campaign_id": charter["id"]}).json()["budget"]["llm_spent_usd"] == .4
    finally:
        workspace.close()
