"""Progress reflects durable task lineage and real worker/provider activity."""
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from test_framework_discovery import Adapter, command, setup, settle, start
from optimization_framework.research.discovery.models import DiscoveryResult
from optimization_framework.api.app import create_app
from optimization_framework.contracts.requests import ResearchInput
from optimization_framework.research.progress import view


def waiting_for_direction(setup):
    workspace, campaign = setup
    class Asks(Adapter):
        def result(self, role, context):
            if role == "campaign_manager":
                return DiscoveryResult(summary="The implementation handoff is ready; a choice is needed.",
                    session_action="request_researcher_input", questions_for_manager=[
                        "Does H12 mean mask-aware covariance adaptation?", "What implementation allocation do you want?"])
            return super().result(role, context)
    workspace.discovery.adapter_factory = Asks
    session, _ = start(workspace, campaign)
    settle(workspace, campaign)
    manager = next(row for row in workspace.discovery.tasks(session) if row["brief"]["role"] == "campaign_manager")
    return workspace, campaign, workspace.store.get(session["id"]), manager


def test_waiting_progress_exposes_current_manager_request_without_archive_or_mutation(setup):
    workspace, campaign, session, manager = waiting_for_direction(setup)
    # A newer-looking stale manager and a specialist's questions are not the
    # researcher request that put this session into its waiting state.
    for identity, role, status in (("stale_manager", "campaign_manager", "superseded"),
                                   ("specialist_question", "methodology_specialist", "completed")):
        workspace.store.put("discovery_task", {**manager, "id": identity, "brief": {**manager["brief"], "role": role},
            "status": status, "finished_at": "2099-01-01T00:00:00+00:00",
            "result": {"summary": "Wrong request", "questions_for_manager": ["Do not show this question"]}})
    events = workspace.store.events(campaign["id"])
    progress = view(workspace, campaign["id"])
    request = progress["direction_request"]
    assert request["id"] == manager["applied_step_id"] and request["task_id"] == manager["id"]
    assert request["session_id"] == session["id"] and request["kind"] == "questions"
    assert request["summary"] == manager["result"]["summary"]
    assert request["questions"] == manager["result"]["questions_for_manager"]
    assert "context_snapshot" not in json.dumps(request)
    assert workspace.store.events(campaign["id"]) == events


def test_reply_is_durable_hides_answered_prompt_and_wakes_manager_without_changing_allocations(setup):
    workspace, campaign, session, manager = waiting_for_direction(setup)
    request = view(workspace, campaign["id"])["direction_request"]
    workspace.shutdown_event.set()
    calls_before = len(Adapter.calls)
    message = f"Reply to campaign manager request {request['id']} (discovery session {session['id']}):\n\nYes, that is H12. Explain the required allocation first."
    reply = command(workspace, campaign, "research.start", {"mode": "discuss", "message": message}, "reply_to_manager")
    receipt = workspace.commands.execute(reply)
    assert workspace.commands.execute(reply) == receipt
    saved = workspace.store.get(receipt["outcome"]["manager_command_id"])
    assert saved["request"]["message"] == message
    progress = view(workspace, campaign["id"])
    assert progress["direction_request"] is None and progress["status"] == "queued"
    with workspace.lock, workspace.store.transaction():
        workspace.discovery._admit_guidance(session)
    new_task = next(task for task in workspace.discovery.tasks(session) if task.get("manager_command_id") == saved["id"])
    assert new_task["brief"]["objective"] == message and new_task["status"] == "queued"
    assert workspace.store.get(session["id"])["status"] == "running"
    assert workspace.store.get(session["id"])["policy"] == session["policy"]
    assert len(Adapter.calls) == calls_before


def test_waiting_without_a_question_shows_latest_summary_without_old_questions(setup):
    workspace, campaign, session, manager = waiting_for_direction(setup)
    newer = {**manager, "id": "new_manager", "applied_step_id": "new_manager_step_0", "finished_at": "2099-01-01T00:00:00+00:00",
        "result": {"summary": "Assigned work is saved; choose the next focus.", "questions_for_manager": [], "session_action": "continue"}}
    workspace.store.put("discovery_task", newer)
    request = view(workspace, campaign["id"])["direction_request"]
    assert request["kind"] == "open_direction" and not request["questions"]
    assert request["summary"] == newer["result"]["summary"]
    for status in ("running", "paused", "stopped", "completed"):
        session["status"] = status
        workspace.store.put("discovery_session", session)
        assert view(workspace, campaign["id"])["direction_request"] is None


def test_allocation_wrap_up_replaces_old_manager_questions(setup):
    workspace, campaign, session, manager = waiting_for_direction(setup)
    workspace.store.put_immutable("discovery_wrap_up", {"id": "current_wrap_up", "campaign_id": campaign["id"],
        "session_id": session["id"], "summary": "Partial findings saved at the allocation ceiling.", "created_at": manager["finished_at"]})
    session["wrap_up_id"] = "current_wrap_up"
    workspace.store.put("discovery_session", session)
    request = view(workspace, campaign["id"])["direction_request"]
    assert request["id"] == "current_wrap_up" and request["kind"] == "allocation" and not request["questions"]
    assert "allocation ceiling" in request["summary"]


def latest_request(workspace, campaign, session, *, task_status="queued"):
    for task in workspace.discovery.tasks(session):
        task["status"] = "completed"
        workspace.store.put("discovery_task", task)
    task = {"id": "current_request_task", "campaign_id": campaign["id"], "session_id": session["id"],
            "status": task_status, "brief": {"role": "campaign_manager", "stage": "manage"},
            "dependencies": [], "manager_command_id": "current_request", "created_at": "2026-09-28T10:00:00+00:00"}
    request = {"id": "current_request", "campaign_id": campaign["id"], "status": "waiting_discovery",
               "discovery_task_id": task["id"], "automatic": False, "created_at": task["created_at"],
               "request": {"message": "Request 3 proposals with reliable implementations."}}
    workspace.store.put("manager_command", request)
    workspace.store.put("discovery_task", task)
    return request, task


def child(workspace, campaign, session, task, identity, *, status="queued", **extra):
    record = {"id": identity, "campaign_id": campaign["id"], "session_id": session["id"],
              "status": status, "brief": {"role": "proposal_reviewer", "stage": "review"},
              "dependencies": [task["id"]], "created_at": "2026-09-28T10:02:00+00:00", **extra}
    workspace.store.put("discovery_task", record)
    return record


def test_paused_submission_is_visible_without_dispatch_or_projection_side_effects(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    workspace.discovery.control(campaign["id"], {"session_id": session["id"], "action": "pause", "expected_control_revision": 0})
    result = workspace.manager.start(ResearchInput(campaign_id=campaign["id"], mode="generate", message="Request 3 proposals"))
    before = workspace.store.list("discovery_task", campaign["id"])
    progress = view(workspace, campaign["id"])
    assert progress["status"] == "paused" and progress["can_resume"] and not progress["active"]
    assert progress["request"]["id"] == result["id"]
    assert progress["task_counts"] == {"total": 1, "queued": 1, "running": 0, "waiting": 0, "completed": 0,
                                      "failed": 0, "blocked": 0, "cancelled": 0, "superseded": 0, "handed_off": 0}
    assert "No model call" in progress["agents"][0]["activity"]
    assert workspace.store.list("discovery_task", campaign["id"]) == before
    assert not workspace.store.list("research_run", campaign["id"])
    assert view(workspace, campaign["id"])["updated_at"] == progress["updated_at"]


def test_resume_overrides_stale_paused_request_message(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    request, _ = latest_request(workspace, campaign, session)
    request["wait_reason"] = "Discovery is paused; resume it to process this request"
    workspace.store.put("manager_command", request)
    progress = view(workspace, campaign["id"])
    assert progress["status"] == "queued" and not progress["can_resume"]
    assert "paused" not in progress["message"]


def test_historical_timeout_receipt_explains_failure_and_offers_retry(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    _, task = latest_request(workspace, campaign, session, task_status="failed")
    task["error"] = "CodexProviderError: Task output or execution failed"
    workspace.store.put("discovery_task", task)
    workspace.agent_log.record(campaign["id"], "provider.error", task_id=task["id"], payload={"error": "timeout"})
    progress = view(workspace, campaign["id"])
    assert progress["status"] == "failed" and not progress["active"]
    assert progress["retryable_task_ids"] == [task["id"]]
    assert progress["agents"][0]["error_code"] == "timeout"
    assert "timed out" in progress["agents"][0]["activity"]
    assert "Task output or execution failed" not in progress["agents"][0]["activity"]


def test_scope_follows_review_and_manager_dependencies_ignores_old_failures_and_automatic_requests(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    request, task = latest_request(workspace, campaign, session, task_status="completed")
    review = child(workspace, campaign, session, task, "new_review", status="completed")
    child(workspace, campaign, session, review, "manager_followup")
    child(workspace, campaign, session, task, "unrelated", status="failed", manager_command_id="another_request")
    unrelated = workspace.discovery.tasks(session)[0]
    unrelated.update(status="failed", error="An old failure")
    workspace.store.put("discovery_task", unrelated)
    workspace.store.put("manager_command", {**request, "id": "automatic_message", "automatic": True,
                                             "created_at": "2099-01-01T00:00:00+00:00"})
    progress = view(workspace, campaign["id"])
    assert progress["request"]["id"] == request["id"]
    assert progress["status"] == "queued"
    assert progress["task_counts"]["total"] == 3 and progress["task_counts"]["failed"] == 0
    assert {agent["task_id"] for agent in progress["agents"]} == {task["id"], review["id"], "manager_followup"}


def test_completed_manager_task_does_not_claim_entire_request_finished(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    request, _ = latest_request(workspace, campaign, session, task_status="completed")
    request["status"] = "completed"
    workspace.store.put("manager_command", request)
    progress = view(workspace, campaign["id"])
    assert progress["status"] == "waiting" and "follow-up" in progress["message"]


def test_new_session_progress_is_not_hidden_by_previous_session_request(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    request, _ = latest_request(workspace, campaign, session, task_status="completed")
    request.update(created_at="2020-01-01T00:00:00+00:00", status="completed")
    workspace.store.put("manager_command", request)
    session["status"] = "stopped"
    workspace.store.put("discovery_session", session)
    newer = workspace.discovery.start(campaign["id"], {"task_id": session["problem_task_id"]}, "next_session")
    progress = view(workspace, campaign["id"])
    assert progress["request"] is None and progress["session"]["id"] == newer["id"]
    assert progress["status"] == "queued" and progress["task_counts"]["total"] == 3


def test_live_provider_wait_uses_saved_heartbeat_and_dispatch_model_without_exposing_prompt(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    _, task = latest_request(workspace, campaign, session, task_status="running")
    task.update(run_id="current_run", attempt_id="current_attempt")
    workspace.store.put("discovery_task", task)
    workspace.store.put("research_run", {"id": task["run_id"], "campaign_id": campaign["id"], "status": "running",
        "created_at": "2026-09-28T10:01:00+00:00", "context_snapshot": {"secret_context": "must-not-be-projected"}})
    workspace.store.put("discovery_attempt", {"id": task["attempt_id"], "campaign_id": campaign["id"],
        "created_at": "2026-09-28T10:01:01+00:00", "provider_snapshot": {"model": "gpt-6-astra", "reasoning_effort": "xhigh"},
        "context_snapshot": {"secret_context": "must-not-be-projected"}})
    workspace.discovery.threads[task["id"]] = SimpleNamespace(is_alive=lambda: True)
    workspace.agent_log.record(campaign["id"], "provider.request", task_id=task["id"],
                               payload={"prompt": "must-not-be-projected"}, occurred_at="2026-09-28T10:01:02+00:00")
    workspace.agent_log.record(campaign["id"], "runtime.liveness", task_id=task["id"],
                               occurred_at="2026-09-28T10:01:32+00:00")
    progress = view(workspace, campaign["id"])
    assert progress["status"] == "running" and progress["active"]
    agent = progress["agents"][0]
    assert agent["model"] == "gpt-6-astra" and agent["reasoning_effort"] == "xhigh"
    assert agent["last_activity_at"] == "2026-09-28T10:01:32+00:00"
    assert "scientific result" in agent["activity"] and "must-not-be-projected" not in json.dumps(progress)
    workspace.agent_log.record(campaign["id"], "provider.response", task_id=task["id"],
                               occurred_at="2026-09-28T10:01:33+00:00")
    returned = view(workspace, campaign["id"])
    assert returned["status"] == "running" and not returned["active"]
    assert "response received" in returned["agents"][0]["activity"]
    workspace.discovery.threads.clear()
    stopped = view(workspace, campaign["id"])
    assert stopped["status"] == "blocked" and not stopped["active"]
    assert stopped["agents"][0]["wait_reason"] == "worker_recovery"


def test_waiting_for_tools_and_provider_are_distinct(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    _, task = latest_request(workspace, campaign, session, task_status="waiting")
    task["wait_reason"] = "tools"
    workspace.store.put("discovery_task", task)
    progress = view(workspace, campaign["id"])
    assert progress["status"] == "waiting" and not progress["active"]
    assert "tool results" in progress["agents"][0]["activity"]
    session["status"] = "waiting_for_provider"
    workspace.store.put("discovery_session", session)
    progress = view(workspace, campaign["id"])
    assert progress["status"] == "blocked" and progress["headline"] == "Waiting for a model provider"


def test_continuation_does_not_reuse_provider_activity_from_previous_attempt(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    _, task = latest_request(workspace, campaign, session, task_status="running")
    task["attempt_id"] = "second_attempt"
    workspace.store.put("discovery_task", task)
    workspace.store.put("discovery_attempt", {"id": task["attempt_id"], "campaign_id": campaign["id"],
        "created_at": "2026-09-28T10:02:00+00:00", "provider_snapshot": {"model": "gpt-6-astra"}})
    workspace.discovery.threads[task["id"]] = SimpleNamespace(is_alive=lambda: True)
    for event_type in ("runtime.liveness", "provider.response"):
        workspace.agent_log.record(campaign["id"], event_type, task_id=task["id"], attempt_id="first_attempt",
                                   occurred_at="2026-09-28T10:01:59+00:00")
        progress = view(workspace, campaign["id"])
        assert not progress["active"] and progress["agents"][0]["activity"] == "Preparing the next model call."
    # Older logs without attempt IDs remain usable only after this attempt began.
    workspace.agent_log.record(campaign["id"], "provider.request", task_id=task["id"],
                               occurred_at="2026-09-28T10:01:59+00:00")
    assert not view(workspace, campaign["id"])["active"]
    workspace.agent_log.record(campaign["id"], "provider.request", task_id=task["id"], attempt_id="second_attempt",
                               occurred_at="2026-09-28T10:02:01+00:00")
    assert view(workspace, campaign["id"])["active"]


def test_exhausted_stopped_and_cancelled_do_not_claim_scientific_completion(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    request, _ = latest_request(workspace, campaign, session, task_status="cancelled")
    for state, expected in (("exhausted", "blocked"), ("stopped", "waiting"), ("paused", "paused")):
        session["status"] = state
        workspace.store.put("discovery_session", session)
        assert view(workspace, campaign["id"])["status"] == expected
    session["status"] = "running"
    request["status"] = "superseded"
    workspace.store.put("discovery_session", session)
    workspace.store.put("manager_command", request)
    assert view(workspace, campaign["id"])["headline"] == "Research request is no longer active"


def test_queued_retry_shows_current_role_policy_instead_of_old_run_environment(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    _, task = latest_request(workspace, campaign, session)
    task["run_id"] = "retried_run"
    workspace.store.put("discovery_task", task)
    workspace.store.put("research_run", {"id": task["run_id"], "campaign_id": campaign["id"], "status": "waiting",
        "created_at": "2026-09-28T09:00:00+00:00",
        "request": {"provider_snapshot": {"model": "gpt-6-luna", "reasoning_effort": "low"}}})
    workspace.agent_log.record(campaign["id"], "provider.error", task_id=task["id"], payload={"error": "timeout"})
    workspace.models.save(campaign["id"], {"expected_revision": 0, "policy": {
        "default": {"model": "gpt-6-sol", "reasoning_effort": "xhigh"},
        "roles": {"campaign_manager": {"model": "gpt-6-astra", "reasoning_effort": "xhigh"}}}})
    agent = view(workspace, campaign["id"])["agents"][0]
    assert agent["status"] == "queued" and not agent["active"]
    assert agent["model"] == "gpt-6-astra" and agent["reasoning_effort"] == "xhigh"
    assert agent["started_at"] is None and agent["error_code"] is None
    task.update(status="waiting", wait_reason="context_scope", error="Assigned evidence exceeds the context allowance")
    workspace.store.put("discovery_task", task)
    waiting = view(workspace, campaign["id"])["agents"][0]
    assert waiting["started_at"] is None and waiting["error_code"] is None
    assert waiting["activity"] == task["error"]


def test_legacy_manager_uses_frozen_role_policy(setup):
    workspace, campaign = setup
    request = {"id": "legacy_request", "campaign_id": campaign["id"], "status": "dispatched", "research_run_id": "legacy_run",
               "created_at": "2026-09-28T10:00:00+00:00", "request": {"message": "Review the evidence"}}
    workspace.store.put("manager_command", request)
    workspace.store.put("research_run", {"id": "legacy_run", "campaign_id": campaign["id"], "status": "running",
        "current_role": "campaign_manager", "created_at": request["created_at"],
        "request": {"provider_snapshot": {"model": "gpt-6-luna"},
                    "model_policy": {"default": {"model": "gpt-6-sol"},
                                     "roles": {"campaign_manager": {"model": "gpt-6-astra", "reasoning_effort": "xhigh"}}}}})
    workspace.research_threads["legacy_run"] = SimpleNamespace(is_alive=lambda: True)
    workspace.agent_log.record(campaign["id"], "provider.request", task_id="legacy_run")
    progress = view(workspace, campaign["id"])
    assert progress["status"] == "running" and progress["agents"][0]["model"] == "gpt-6-astra"
    run = workspace.store.get("legacy_run", "research_run")
    run["status"] = "completed"
    workspace.store.put("research_run", run)
    assert view(workspace, campaign["id"])["status"] == "completed"
    run["status"] = "needs_reconciliation"
    workspace.store.put("research_run", run)
    uncertain = view(workspace, campaign["id"])
    assert uncertain["status"] == "blocked" and uncertain["agents"][0]["wait_reason"] == "uncertain_provider"


def test_http_state_and_read_only_progress_endpoint_agree(tmp_path, monkeypatch):
    monkeypatch.setenv("GRATING_LLM_DISABLED", "true")
    app = create_app(tmp_path, start_workers=False)
    with TestClient(app) as client:
        created = client.post("/api/campaigns", json={"name": "Progress API", "tasks": [{"name": "Example", "problem_id": "bounded_continuous"}]}).json()
        direct = client.get(f"/api/campaigns/{created['id']}/research-progress")
        assert direct.status_code == 200 and direct.json()["status"] == "idle"
        state = client.get("/api/state", params={"campaign_id": created["id"]}).json()
        assert state["research_progress"] == direct.json()
        assert client.get("/api/campaigns/missing/research-progress").status_code == 404


def parallel_review(workspace, campaign, *, phase="reviewing", statuses=("completed", "running", "running")):
    request = {"id": "parallel_request", "campaign_id": campaign["id"], "status": "dispatched",
        "research_run_id": "parallel_parent", "created_at": "2099-01-01T10:00:00+00:00",
        "request": {"message": "Reassess the selected decisions against current evidence."}}
    parent = {"id": request["research_run_id"], "campaign_id": campaign["id"], "status": "running",
        "created_at": request["created_at"], "manager_command_id": request["id"],
        "decision_review": {"phase": phase, "max_parallel_reviews": 3, "task_ids": []},
        "context_snapshot": {"private_context": "must-not-be-projected"},
        "request": {"provider_snapshot": {"model": "gpt-6-luna", "reasoning_effort": "low"},
            "model_policy": {"roles": {"comparative_reviewer": {"model": "gpt-6-sol", "reasoning_effort": "xhigh"},
                "research_synthesizer": {"model": "gpt-6-astra", "reasoning_effort": "xhigh"}}}}}
    tasks = []
    for index, status in enumerate(statuses):
        run_id = f"parallel_child_{index}"
        task = {"id": f"parallel_task_{index}", "campaign_id": campaign["id"], "parent_run_id": parent["id"],
            "current_run_id": run_id, "role": "comparative_reviewer", "title": f"Review group {index}", "status": status,
            "created_at": request["created_at"], "context_snapshot": {"private_context": "must-not-be-projected"}}
        workspace.store.put("decision_review_task", task)
        workspace.store.put("research_run", {"id": run_id, "campaign_id": campaign["id"],
            "parent_review_run_id": parent["id"], "review_task_id": task["id"], "status": status,
            "created_at": "2099-01-01T10:01:00+00:00", "current_role": "comparative_reviewer", "request": parent["request"]})
        parent["decision_review"]["task_ids"].append(task["id"])
        tasks.append(task)
    workspace.store.put("research_run", parent)
    workspace.store.put("manager_command", request)
    return request, parent, tasks


def test_parallel_review_shows_exact_child_workers_and_frozen_models_without_old_discovery_state(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    session["status"] = "paused"
    workspace.store.put("discovery_session", session)
    request, parent, tasks = parallel_review(workspace, campaign)
    workspace.research_threads[parent["id"]] = SimpleNamespace(is_alive=lambda: True)
    for task in tasks[1:]:
        workspace.research_threads[task["current_run_id"]] = SimpleNamespace(is_alive=lambda: True)
        workspace.agent_log.record(campaign["id"], "provider.request", task_id=task["current_run_id"],
            occurred_at="2099-01-01T10:01:01+00:00")
        workspace.agent_log.record(campaign["id"], "runtime.liveness", task_id=task["current_run_id"],
            occurred_at="2099-01-01T10:01:32+00:00")
    before = workspace.store.list("decision_review_task", campaign["id"])
    progress = view(workspace, campaign["id"])
    assert progress["request"]["id"] == request["id"]
    assert progress["status"] == "running" and progress["active"]
    assert progress["session"] is None and not progress["can_resume"]
    assert progress["task_counts"]["running"] == 2 and progress["task_counts"]["completed"] == 1
    assert progress["task_counts"]["total"] == 3
    assert all(agent["role"] == "comparative_reviewer" and agent["model"] == "gpt-6-sol"
        and agent["reasoning_effort"] == "xhigh" for agent in progress["agents"])
    assert sum(agent["active"] for agent in progress["agents"]) == 2
    assert {agent["task_id"] for agent in progress["agents"]} == {task["id"] for task in tasks}
    assert "must-not-be-projected" not in json.dumps(progress)
    assert workspace.store.list("decision_review_task", campaign["id"]) == before
    # The live parent supervisor alone is insufficient evidence of child liveness.
    for task in tasks[1:]:
        workspace.research_threads.pop(task["current_run_id"])
    recovery = view(workspace, campaign["id"])
    assert recovery["status"] == "blocked" and not recovery["active"]
    assert sum(agent["wait_reason"] == "worker_recovery" for agent in recovery["agents"]) == 2


def test_parallel_review_manager_consolidation_has_its_own_call_and_completion(setup):
    workspace, campaign = setup
    _, parent, _ = parallel_review(workspace, campaign, phase="synthesizing", statuses=("completed",) * 3)
    workspace.research_threads[parent["id"]] = SimpleNamespace(is_alive=lambda: True)
    workspace.agent_log.record(campaign["id"], "provider.request", task_id=parent["id"],
        occurred_at="2099-01-01T10:01:00+00:00")
    # A manager retry must not reuse an earlier call's activity or elapsed time.
    workspace.agent_log.record(campaign["id"], "role_started", task_id=parent["id"],
        occurred_at="2099-01-01T10:05:00+00:00")
    progress = view(workspace, campaign["id"])
    assert progress["headline"] == "Manager consolidating" and progress["status"] == "running"
    assert not progress["active"]
    manager = progress["agents"][0]
    assert manager["task_id"] == parent["id"] and manager["role"] == "research_synthesizer"
    assert manager["model"] == "gpt-6-astra" and manager["reasoning_effort"] == "xhigh"
    assert manager["started_at"] == "2099-01-01T10:05:00+00:00"
    assert manager["activity"] == "Preparing the next model call."
    assert progress["task_counts"]["completed"] == 3 and progress["task_counts"]["running"] == 1
    workspace.agent_log.record(campaign["id"], "provider.request", task_id=parent["id"],
        occurred_at="2099-01-01T10:05:01+00:00")
    assert view(workspace, campaign["id"])["active"]
    parent["status"] = "awaiting_researcher"
    parent["decision_review"]["phase"] = "completed"
    workspace.store.put("research_run", parent)
    completed = view(workspace, campaign["id"])
    assert completed["status"] == "completed" and not completed["active"]
    assert completed["task_counts"]["completed"] == 4 and completed["task_counts"]["running"] == 0
    assert "No proposed work was approved" in completed["message"]


def test_parallel_review_retry_hides_old_attempt_activity_and_keeps_reconciliation_distinct(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    session["status"] = "exhausted"
    workspace.store.put("discovery_session", session)
    _, parent, tasks = parallel_review(workspace, campaign, phase="queued", statuses=("completed", "queued", "queued"))
    for task in tasks[1:]:
        workspace.agent_log.record(campaign["id"], "provider.error", task_id=task["current_run_id"], payload={"error": "timeout"})
        workspace.research_threads[task["current_run_id"]] = SimpleNamespace(is_alive=lambda: True)
    queued = view(workspace, campaign["id"])
    assert queued["status"] == "queued" and not queued["active"]
    assert queued["session"] is None and queued["retryable_task_ids"] == []
    for agent in queued["agents"]:
        if agent["status"] == "queued":
            assert agent["started_at"] is None and agent["error_code"] is None and not agent["worker_active"]
    tasks[1].update(status="needs_reconciliation", error="The provider call has uncertain delivery.")
    tasks[2].update(status="failed", error="The reviewer response was invalid.")
    parent["decision_review"]["phase"] = "partial"
    parent["status"] = "partial"
    workspace.store.put("research_run", parent)
    for task in tasks[1:]:
        workspace.store.put("decision_review_task", task)
    partial = view(workspace, campaign["id"])
    assert partial["status"] == "blocked" and not partial["active"]
    assert partial["task_counts"]["waiting"] == 1 and partial["task_counts"]["failed"] == 1
    assert partial["retryable_task_ids"] == []
    assert partial["agents"][0]["wait_reason"] == "uncertain_provider"


def test_new_discovery_session_supersedes_an_older_completed_parallel_review(setup):
    workspace, campaign = setup
    request, parent, _ = parallel_review(workspace, campaign, phase="completed", statuses=("completed",) * 3)
    request.update(created_at="2020-01-01T00:00:00+00:00", status="completed")
    parent["status"] = "completed"
    workspace.store.put("manager_command", request)
    workspace.store.put("research_run", parent)
    session, _ = start(workspace, campaign)
    progress = view(workspace, campaign["id"])
    assert progress["request"] is None and progress["session"]["id"] == session["id"]
    assert progress["status"] == "queued" and progress["task_counts"]["total"] == 3
