"""Parallel reconsideration must overlap real adapters without parallel authority."""
from collections import Counter
from copy import deepcopy
import json
import threading

import httpx
import pytest

from optimization_framework.campaigns.manager import CampaignManager
from optimization_framework.contracts.commands import Command
from optimization_framework.contracts.requests import CampaignInput, CampaignUpdate, TaskInput
from optimization_framework.execution.service import Workspace
from optimization_framework.research import engine
from optimization_framework.research.providers import api_spend
from optimization_framework.storage.sqlite import now
from test_workspace_research import configure, isolated_provider


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    configure(monkeypatch)
    monkeypatch.delenv("GRATING_IMPLEMENTATIONS_TOKEN", raising=False)
    monkeypatch.setenv("GRATING_IMPLEMENTATIONS_TOKEN_FILE", str(tmp_path / "missing-service-token"))
    workspace = Workspace(tmp_path / "workspace")
    campaign = workspace.create_campaign(CampaignInput(name="Parallel decision review", autonomy="delegated",
        llm_budget_usd=5, compute_budget_seconds=100, validation_reserve_seconds=0,
        tasks=[TaskInput(name=f"Quadratic instance {index}", problem_id="bounded_continuous") for index in range(5)]))
    manager = CampaignManager(workspace)
    scheduled = []
    monkeypatch.setattr(manager, "_thread", lambda run: scheduled.append(run["id"]))
    return workspace, campaign, manager, scheduled


def command(workspace, campaign, operation, payload, identity):
    current = workspace.store.get(campaign["id"], "campaign")
    return Command(id=identity, campaign_id=campaign["id"], operation=operation,
        expected_revision=current["version"],
        expected_guidance_revision=workspace.memory.state(campaign["id"])["guidance_revision"],
        expected_authority_hash=workspace.commands.authority_hash(current), payload=payload)


def seed_decisions(workspace, campaign, count=5):
    decisions = []
    for index in range(count):
        action = {"id": f"original_action_{index}", "campaign_id": campaign["id"], "kind": "command",
            "command_operation": "draft.save", "command_payload": {"question": f"Compare mechanism {index}",
                "task_id": workspace.current_tasks(campaign["id"])[index % 5]["id"]},
            "title": f"Save mechanism {index} comparison", "rationale": f"Test scientific mechanism {index} at a matched budget.",
            "status": "proposed", "requires_researcher": True, "charter_version": campaign["version"], "guidance_revision": 0}
        decision = {"id": f"original_decision_{index}", "campaign_id": campaign["id"], "action_id": action["id"],
            "title": action["title"], "context": action["rationale"], "charter_version": campaign["version"],
            "status": "pending", "created_at": now(), "resolution_revision": 0,
            "options": [{"id": "accept", "label": "Proceed"}, {"id": "defer", "label": "Defer"}, {"id": "reject", "label": "Decline"}]}
        workspace.store.put("action", action)
        workspace.store.put("decision", decision)
        decisions.append(decision)
    workspace.update_campaign(campaign["id"], CampaignUpdate(objective="Review these mechanisms under the current scientific scope."))
    return decisions


def begin_review(prepared, *, count=5, parallel=2, identity="parallel_review"):
    workspace, campaign, manager, scheduled = prepared
    decisions = seed_decisions(workspace, campaign, count)
    payload = {"decisions": [{"decision_id": item["id"], "expected_resolution_revision": 0} for item in decisions],
        "max_parallel_reviews": parallel, "comment": "Reassess the comparisons together; this is not experiment approval."}
    receipt = workspace.commands.execute(command(workspace, campaign, "decision.refresh", payload, identity))
    assert len(scheduled) == 1
    parent = workspace.store.get(scheduled[0], "research_run")
    assert parent.get("decision_review"), parent
    return parent, payload, decisions, receipt


def review_tasks(workspace, parent):
    return [workspace.store.get(identity, "decision_review_task") for identity in parent["decision_review"]["task_ids"]]


def start_supervisor(manager, parent_id):
    failures = []
    def execute():
        try:
            manager._run(parent_id)
        except BaseException as exc:
            failures.append(exc)
    thread = threading.Thread(target=execute, daemon=True)
    thread.start()
    return thread, failures


def finish_supervisor(thread, failures):
    thread.join(timeout=10)
    assert not thread.is_alive(), "Review supervisor did not terminate"
    assert not failures, failures


def model_transport(monkeypatch, handler):
    """Keep the real adapter, its reservations and JSON validation; mock HTTP only."""
    local = threading.local()
    original_call = engine.LLMAdapter.call
    def tracked_call(adapter, role, payload, **kwargs):
        local.role, local.payload, local.adapter = role, deepcopy(payload), adapter
        return original_call(adapter, role, payload, **kwargs)
    monkeypatch.setattr(engine.LLMAdapter, "call", tracked_call)
    def respond(request):
        result = handler(local.role, local.payload, local.adapter)
        if isinstance(result, httpx.Response):
            return result
        return httpx.Response(200, json={"usage": {"prompt_tokens": 100, "completion_tokens": 30},
            "choices": [{"message": {"content": json.dumps(result)}}]})
    original_client = httpx.Client
    monkeypatch.setattr(engine.httpx, "Client", lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs))


def supplied_decision_ids(payload):
    context = payload.get("context", payload)
    return [item["decision"]["id"] for item in context["decision_refresh"]["decisions"]]


def proposed_trial(workspace, campaign):
    return {"kind": "command", "title": "A child must not launch this trial", "rationale": "Test the execution boundary.",
        "question": "Does the boundary hold?", "requires_researcher": False,
        "expected_information": "A bounded observation", "stopping_condition": "After two requests",
        "command_operation": "trial.create", "command_payload": {
            "task_id": workspace.current_tasks(campaign["id"])[0]["id"], "algorithm": "coordinate", "max_steps": 2, "wall_seconds": 5}}


def test_reviews_overlap_at_the_cap_and_only_manager_can_publish(prepared, monkeypatch):
    workspace, campaign, manager, _ = prepared
    parent, _, originals, _ = begin_review(prepared, count=5, parallel=2)
    tasks = review_tasks(workspace, parent)
    assert len(tasks) >= 3, "Fixture requires multiple waves of independently scoped reviews"
    lock = threading.Lock()
    release_children, two_in_flight, manager_started, release_manager = (threading.Event() for _ in range(4))
    counts = {"active": 0, "peak": 0, "completed": 0}
    adapters, roles = [], []
    action = proposed_trial(workspace, campaign)

    def handle(role, payload, adapter):
        with lock:
            roles.append(role)
            adapters.append(adapter)
        if role == "research_synthesizer":
            assert all(task["status"] == "completed" for task in review_tasks(workspace, parent))
            assert counts["completed"] == len(tasks)
            manager_started.set()
            assert release_manager.wait(5)
            return {"analysis": "All independent reviews are complete; here is the consolidated proposal.", "actions": [action]}
        assert role == "comparative_reviewer"
        with lock:
            counts["active"] += 1
            counts["peak"] = max(counts["peak"], counts["active"])
            if counts["active"] == 2:
                two_in_flight.set()
        try:
            assert release_children.wait(5)
            return {"analysis": f"Reviewed {supplied_decision_ids(payload)} independently."}
        finally:
            with lock:
                counts["active"] -= 1
                counts["completed"] += 1

    model_transport(monkeypatch, handle)
    thread, failures = start_supervisor(manager, parent["id"])
    try:
        assert two_in_flight.wait(5), "Child adapters did not overlap"
        assert not manager_started.is_set()
        assert sum(task["status"] == "running" for task in review_tasks(workspace, parent)) == 2
        assert not workspace.store.list("trial")
        release_children.set()
        assert manager_started.wait(5), "Manager synthesis did not start after child barrier"
        assert len(workspace.store.list("decision")) == len(originals)
        assert len(workspace.store.list("action")) == len(originals)
        assert all(task.get("result_id") for task in review_tasks(workspace, parent))
    finally:
        release_children.set()
        release_manager.set()
    finish_supervisor(thread, failures)
    assert counts["peak"] == 2
    assert roles.count("comparative_reviewer") == len(tasks)
    assert roles.count("research_synthesizer") == 1
    assert len({id(adapter) for adapter in adapters}) == len(tasks) + 1
    assert not workspace.store.list("trial")
    new_actions = [row for row in workspace.store.list("action") if row["id"] not in {item["action_id"] for item in originals}]
    assert len(new_actions) == 1 and new_actions[0]["requires_researcher"] is True
    new_decisions = [row for row in workspace.store.list("decision") if row["id"] not in {item["id"] for item in originals}]
    assert len(new_decisions) == 1 and new_decisions[0]["research_run_id"] == parent["id"]
    runs = workspace.store.list("research_run", campaign["id"])
    child_runs = [row for row in runs if row.get("parent_review_run_id") == parent["id"]]
    assert len(child_runs) == len(tasks)
    assert all(row["usage"]["calls"] == 1 for row in child_runs)
    current = workspace.store.get(parent["id"], "research_run")
    assert current["usage"]["calls"] == 1
    assert sum(api_spend(row.get("usage")) for row in runs) == pytest.approx((len(tasks) + 1) * .00016)


def retry_payload(parent, original_payload, *, parallel=2):
    return {"decisions": [{"decision_id": item["decision_id"], "expected_resolution_revision": item["expected_resolution_revision"]}
        for item in original_payload["decisions"]], "retry_run_id": parent["id"], "max_parallel_reviews": parallel}


def test_failed_only_retry_keeps_completed_outputs_and_rejects_child_action_publication(prepared, monkeypatch):
    workspace, campaign, manager, scheduled = prepared
    parent, payload, originals, _ = begin_review(prepared, count=3, parallel=2)
    tasks = review_tasks(workspace, parent)
    target = tuple(tasks[1]["decision_ids"])
    attempts = Counter()
    roles = []
    lock = threading.Lock()
    action = proposed_trial(workspace, campaign)
    def handle(role, supplied, adapter):
        with lock:
            roles.append(role)
        if role == "research_synthesizer":
            return {"analysis": "Every selected group now has a completed assessment."}
        ids = tuple(supplied_decision_ids(supplied))
        with lock:
            attempts[ids] += 1
            number = attempts[ids]
        if ids == target and number == 1:
            return {"analysis": "A reviewer tried to publish an action.", "actions": [action]}
        return {"analysis": "Private assessment " + str(ids)}
    model_transport(monkeypatch, handle)
    thread, failures = start_supervisor(manager, parent["id"])
    finish_supervisor(thread, failures)
    partial = workspace.store.get(parent["id"], "research_run")
    assert partial["status"] == "partial"
    assert "research_synthesizer" not in roles
    before = review_tasks(workspace, parent)
    completed = {task["id"]: deepcopy(task) for task in before if task["status"] == "completed"}
    assert len(completed) == len(tasks) - 1
    assert len(workspace.store.list("decision")) == len(originals)
    assert len(workspace.store.list("action")) == len(originals)
    assert not workspace.store.list("trial")
    from optimization_framework.research.decision_review import progress
    assert progress(workspace, partial)["can_retry"]
    guidance = workspace.memory.state(campaign["id"])["guidance_revision"]
    request = command(workspace, campaign, "decision.refresh", retry_payload(parent, payload, parallel=1), "retry_failed")
    accepted = workspace.commands.execute(request)
    assert workspace.commands.execute(request) == accepted
    assert workspace.memory.state(campaign["id"])["guidance_revision"] == guidance
    assert scheduled == [parent["id"], parent["id"]]
    thread, failures = start_supervisor(manager, parent["id"])
    finish_supervisor(thread, failures)
    assert workspace.store.get(parent["id"], "research_run")["status"] == "completed"
    after = review_tasks(workspace, parent)
    for task in after:
        if task["id"] in completed:
            assert task["result_id"] == completed[task["id"]]["result_id"]
            assert task["attempt_run_ids"] == completed[task["id"]]["attempt_run_ids"]
        else:
            assert len(task["attempt_run_ids"]) == 2
    assert attempts[target] == 2 and all(count == 1 for ids, count in attempts.items() if ids != target)
    assert roles.count("research_synthesizer") == 1
    runs = workspace.store.list("research_run", campaign["id"])
    assert sum(row.get("usage", {}).get("calls", 0) for row in runs) == len(tasks) + 2
    assert sum(api_spend(row.get("usage")) for row in runs) == pytest.approx((len(tasks) + 2) * .00016)


@pytest.mark.parametrize("interruption", ["stop", "guidance"])
def test_stop_or_new_guidance_stops_queued_work_and_never_synthesizes_old_authority(prepared, monkeypatch, interruption):
    workspace, campaign, manager, _ = prepared
    parent, payload, originals, _ = begin_review(prepared, count=3, parallel=1)
    entered, release = threading.Event(), threading.Event()
    roles = []
    def handle(role, supplied, adapter):
        roles.append(role)
        entered.set()
        assert release.wait(5)
        return {"analysis": "The response already in flight is retained."}
    model_transport(monkeypatch, handle)
    thread, failures = start_supervisor(manager, parent["id"])
    try:
        assert entered.wait(5)
        if interruption == "stop":
            workspace.commands.execute(command(workspace, campaign, "research.control",
                {"run_id": parent["id"], "action": "stop", "expected_control_revision": 0}, "stop_review"))
        else:
            with workspace.lock, workspace.store.transaction():
                memory = workspace.memory.state(campaign["id"])
                memory["guidance_revision"] += 1
                workspace.store.put("manager_state", memory)
    finally:
        release.set()
    finish_supervisor(thread, failures)
    tasks = review_tasks(workspace, parent)
    assert roles == ["comparative_reviewer"]
    assert sum(task["status"] == "completed" for task in tasks) == 1
    assert sum(task["status"] == "stopped" for task in tasks) == len(tasks) - 1
    assert len(workspace.store.list("decision")) == len(originals)
    assert not workspace.store.list("trial")
    saved = workspace.store.get(parent["id"], "research_run")
    assert saved["status"] == ("stopped" if interruption == "stop" else "partial")
    if interruption == "guidance":
        with pytest.raises(ValueError, match="guidance changed"):
            workspace.commands.execute(command(workspace, campaign, "decision.refresh", retry_payload(parent, payload), "unsafe_retry"))


def test_aggregate_reservations_prevent_concurrent_overspend(prepared, monkeypatch):
    workspace, campaign, manager, _ = prepared
    parent, _, _, _ = begin_review(prepared, count=2, parallel=2)
    entered, release, second_cancelled = threading.Event(), threading.Event(), threading.Event()
    original_emit = manager._emit_locked
    injected = []
    def constrained_emit(run_id, event):
        if event["type"] == "provider_call_reserved" and run_id != parent["id"] and not injected:
            reserved = api_spend(event["usage"])
            hold = workspace.store.get(parent["id"], "research_run")["decision_review"]["budget_hold_usd"]
            # A real campaign may discover another completed model receipt while
            # these reviews start. Leave enough for one reservation, not two.
            other_spend = campaign["llm_budget_usd"] - hold - 1.5 * reserved
            assert other_spend > 0
            workspace.store.put("research_run", {"id": "other_charged_work", "campaign_id": campaign["id"],
                "status": "completed", "usage": {"calls": 1, "billing_mode": "api", "api_cost_usd": other_spend}})
            injected.append(other_spend)
        if event["type"] == "provider_call_cancelled_before_send" and run_id != parent["id"]:
            second_cancelled.set()
        return original_emit(run_id, event)
    monkeypatch.setattr(manager, "_emit_locked", constrained_emit)
    roles = []
    def handle(role, supplied, adapter):
        roles.append(role)
        entered.set()
        assert release.wait(5)
        return {"analysis": "Only the funded call was sent."}
    model_transport(monkeypatch, handle)
    thread, failures = start_supervisor(manager, parent["id"])
    try:
        assert entered.wait(5)
        assert second_cancelled.wait(5), "An unfunded parallel call was not cancelled before HTTP dispatch"
        runs = workspace.store.list("research_run", campaign["id"])
        committed = sum(api_spend(row.get("usage")) + (row.get("decision_review") or {}).get("budget_hold_usd", 0) for row in runs)
        assert committed <= campaign["llm_budget_usd"] + 1e-9
    finally:
        release.set()
    finish_supervisor(thread, failures)
    assert roles == ["comparative_reviewer"]
    assert workspace.store.get(parent["id"], "research_run")["status"] == "partial"
    assert sum(api_spend(row.get("usage")) for row in workspace.store.list("research_run", campaign["id"])) == pytest.approx(injected[0] + .00016)


def test_insufficient_final_manager_reserve_dispatches_no_children(prepared, monkeypatch):
    workspace, campaign, manager, scheduled = prepared
    workspace.update_campaign(campaign["id"], CampaignUpdate(llm_budget_usd=.01))
    originals = seed_decisions(workspace, campaign, count=2)
    calls = []
    model_transport(monkeypatch, lambda *args: calls.append(args) or {"analysis": "Must never be called"})
    receipt = workspace.commands.execute(command(workspace, campaign, "decision.refresh",
        {"decisions": [{"decision_id": item["id"], "expected_resolution_revision": 0} for item in originals]}, "no_synthesis_budget"))
    turn = workspace.store.get(receipt["outcome"]["manager_command_id"], "manager_command")
    assert turn["status"] == "blocked" and "final campaign manager" in turn["error"]
    assert not calls and not scheduled
    assert not workspace.store.list("decision_review_task")
    assert not workspace.store.list("research_run")


def seed_interrupted_attempt(workspace, parent, task, *, saved=False, uncertain=False):
    from optimization_framework.research.lifecycle import save_result
    child = {"id": "interrupted_" + task["id"], "campaign_id": parent["campaign_id"], "parent_review_run_id": parent["id"],
        "review_task_id": task["id"], "charter_version": parent["charter_version"], "guidance_revision": parent["guidance_revision"],
        "request": {**deepcopy(parent["request"]), "max_calls": 1}, "context_snapshot": deepcopy(task["context_snapshot"]),
        "status": "running", "dispatch_phase": "dispatched", "created_at": now(), "control_revision": 0,
        "usage": {"calls": 1 if saved or uncertain else 0, "billing_mode": "api", "api_cost_usd": .05 if uncertain else .00016 if saved else 0}, "trace": []}
    if uncertain:
        child["usage"]["pending_reservation"] = {"id": "uncertain_receipt", "cost_usd_reserved": .05}
    workspace.store.put("research_run", child)
    task.update(status="running", current_run_id=child["id"], attempt_run_ids=[child["id"]])
    workspace.store.put("decision_review_task", task)
    if saved:
        save_result(workspace, child["id"], {"status": "completed", "mode": "llm",
            "assessment": {"analysis": "The returned assessment was saved before interruption.", "dependencies": [], "dissent": [], "evidence_ids": []},
            "usage": child["usage"], "trace": [{"role": "comparative_reviewer", "status": "completed"}],
            "messages": [], "actions": [], "decisions": [], "hypotheses": []})
    return child


def test_recovery_reuses_saved_child_result_without_another_provider_call(prepared, monkeypatch):
    from optimization_framework.research.decision_review import recover
    workspace, campaign, manager, _ = prepared
    parent, _, _, _ = begin_review(prepared, count=2, parallel=2)
    tasks = review_tasks(workspace, parent)
    child = seed_interrupted_attempt(workspace, parent, tasks[0], saved=True)
    saved_result_id = workspace.store.get(child["id"], "research_run")["result_id"]
    recover(workspace)
    assert workspace.store.get(tasks[0]["id"], "decision_review_task")["result_id"] == saved_result_id
    roles = []
    ids = []
    def handle(role, supplied, adapter):
        roles.append(role)
        if role != "research_synthesizer":
            ids.extend(supplied_decision_ids(supplied))
        return {"analysis": "Resume using the stored completed assessment."}
    model_transport(monkeypatch, handle)
    thread, failures = start_supervisor(manager, parent["id"])
    finish_supervisor(thread, failures)
    assert roles.count("comparative_reviewer") == 1 and roles.count("research_synthesizer") == 1
    assert set(ids) == set(tasks[1]["decision_ids"])
    assert workspace.store.get(tasks[0]["id"], "decision_review_task")["attempt_run_ids"] == [child["id"]]
    assert workspace.store.get(parent["id"], "research_run")["status"] == "completed"


def test_uncertain_child_reservation_blocks_retry_and_is_never_replayed(prepared, monkeypatch):
    from optimization_framework.research.decision_review import recover, progress
    workspace, campaign, manager, scheduled = prepared
    parent, payload, _, _ = begin_review(prepared, count=2, parallel=2)
    tasks = review_tasks(workspace, parent)
    child = seed_interrupted_attempt(workspace, parent, tasks[0], uncertain=True)
    calls = []
    model_transport(monkeypatch, lambda *args: calls.append(args) or {"analysis": "Must not replay"})
    recover(workspace)
    current = workspace.store.get(parent["id"], "research_run")
    assert current["status"] == "partial"
    assert workspace.store.get(child["id"], "research_run")["status"] == "needs_reconciliation"
    assert workspace.store.get(tasks[0]["id"], "decision_review_task")["status"] == "needs_reconciliation"
    assert not progress(workspace, current)["can_retry"]
    with pytest.raises(ValueError, match="Reconcile the uncertain"):
        workspace.commands.execute(command(workspace, campaign, "decision.refresh", retry_payload(parent, payload), "no_uncertain_replay"))
    manager.tick(campaign["id"])
    assert not calls and scheduled == [parent["id"]]
    preserved = workspace.store.get(child["id"], "research_run")
    assert preserved["usage"]["pending_reservation"]["id"] == "uncertain_receipt"
    assert api_spend(preserved["usage"]) == .05


def seed_raw_response(workspace, run, response):
    """Model the durable provider receipt boundary before result validation."""
    receipt_id = "raw_response_" + run["id"]
    usage = {"calls": 1, "billing_mode": "api", "api_cost_usd": .00016}
    workspace.store.put_immutable("decision_review_response", {"id": receipt_id,
        "campaign_id": run["campaign_id"], "parent_run_id": run.get("parent_review_run_id", run["id"]),
        "research_run_id": run["id"], "created_at": now(),
        "event": {"type": "provider_response", "status": "completed", "usage": usage, "output": json.dumps(response)}})
    run.update(usage=usage, provider_response_id=receipt_id)
    workspace.store.put("research_run", run)


@pytest.mark.parametrize("stage", ["child", "manager"])
def test_recovery_uses_durable_raw_receipts_without_repeating_provider_calls(prepared, monkeypatch, stage):
    from optimization_framework.research.decision_review import recover
    workspace, campaign, manager, _ = prepared
    parent, _, originals, _ = begin_review(prepared, count=2, parallel=2)
    tasks = review_tasks(workspace, parent)
    if stage == "child":
        child = seed_interrupted_attempt(workspace, parent, tasks[0])
        seed_raw_response(workspace, child, {"analysis": "Receipt returned before child result was saved."})
    else:
        for task in tasks:
            seed_interrupted_attempt(workspace, parent, task, saved=True)
        seed_raw_response(workspace, parent, {"analysis": "The manager already consolidated the saved assessments.",
            "actions": [proposed_trial(workspace, campaign)]})
    roles = []
    def handle(role, supplied, adapter):
        roles.append(role)
        return {"analysis": "Finish only the calls that did not already return."}
    model_transport(monkeypatch, handle)
    recover(workspace)
    assert workspace.store.get(tasks[0]["id"], "decision_review_task")["status"] == "completed"
    thread, failures = start_supervisor(manager, parent["id"])
    finish_supervisor(thread, failures)
    if stage == "child":
        assert roles == ["comparative_reviewer", "research_synthesizer"]
    else:
        assert not roles
        actions = [row for row in workspace.store.list("action") if row["id"] not in {item["action_id"] for item in originals}]
        assert len(actions) == 1 and actions[0]["requires_researcher"] is True
        assert not workspace.store.list("trial")
    current = workspace.store.get(parent["id"], "research_run")
    assert current["decision_review"]["phase"] == "completed"
    assert sum(api_spend(row.get("usage")) for row in workspace.store.list("research_run", campaign["id"])) == pytest.approx(.00048)


def test_explicit_uncertainty_closure_retains_charge_and_allows_only_failed_group_retry(prepared, monkeypatch):
    from optimization_framework.research.decision_review import recover, progress
    workspace, campaign, manager, _ = prepared
    parent, payload, _, _ = begin_review(prepared, count=2, parallel=2)
    tasks = review_tasks(workspace, parent)
    uncertain = seed_interrupted_attempt(workspace, parent, tasks[0], uncertain=True)
    complete = seed_interrupted_attempt(workspace, parent, tasks[1], saved=True)
    recover(workspace)
    completed_task = workspace.store.get(tasks[1]["id"], "decision_review_task")
    guidance = workspace.memory.state(campaign["id"])["guidance_revision"]
    receipt = workspace.commands.execute(command(workspace, campaign, "decision.resolve", {
        "decision_id": "reconcile_" + uncertain["id"], "choice": "close_reserved", "expected_resolution_revision": 0,
        "comment": "Keep the uncertain cost; retry the unfinished assessment explicitly."}, "close_uncertainty"))
    assert receipt["outcome"]["decision"]["outcome"]["reserved_cost_retained"] is True
    assert workspace.memory.state(campaign["id"])["guidance_revision"] == guidance
    preserved = workspace.store.get(uncertain["id"], "research_run")
    assert preserved["status"] == "closed_uncertain" and api_spend(preserved["usage"]) == .05
    assert progress(workspace, workspace.store.get(parent["id"], "research_run"))["can_retry"]
    roles, reviewed = [], []
    def handle(role, supplied, adapter):
        roles.append(role)
        if role == "comparative_reviewer":
            reviewed.extend(supplied_decision_ids(supplied))
        return {"analysis": "Use the retained complete assessment and one new attempt."}
    model_transport(monkeypatch, handle)
    workspace.commands.execute(command(workspace, campaign, "decision.refresh", retry_payload(parent, payload), "retry_closed_uncertainty"))
    thread, failures = start_supervisor(manager, parent["id"])
    finish_supervisor(thread, failures)
    assert roles == ["comparative_reviewer", "research_synthesizer"]
    assert set(reviewed) == set(tasks[0]["decision_ids"])
    preserved_task = workspace.store.get(tasks[1]["id"], "decision_review_task")
    assert preserved_task["result_id"] == completed_task["result_id"]
    assert preserved_task["attempt_run_ids"] == [complete["id"]]
    assert sum(api_spend(row.get("usage")) for row in workspace.store.list("research_run", campaign["id"])) == pytest.approx(.05048)


@pytest.mark.parametrize("discovery_status", ["running", "paused"])
def test_explicit_review_is_not_absorbed_by_existing_discovery(prepared, monkeypatch, discovery_status):
    workspace, campaign, manager, scheduled = prepared
    discovery_ticks = []
    monkeypatch.setattr(workspace.discovery, "active", lambda cid: {"id": "existing_discovery", "status": discovery_status})
    monkeypatch.setattr(workspace.discovery, "tick", lambda cid: discovery_ticks.append(cid))
    parent, _, _, _ = begin_review(prepared, count=2, parallel=2)
    assert scheduled == [parent["id"]] and not discovery_ticks
    manager.tick(campaign["id"])
    assert not discovery_ticks
    assert all(task["status"] == "queued" for task in review_tasks(workspace, parent))


def test_reviewer_output_limits_are_visible_in_provider_schema():
    from optimization_framework.research.decision_review import ReviewerAssessment
    schema = ReviewerAssessment.model_json_schema()
    for field in ("dependencies", "dissent"):
        assert schema["properties"][field]["items"]["maxLength"] == 200
        with pytest.raises(ValueError):
            ReviewerAssessment.model_validate({"analysis": "Private opinion", field: ["x" * 201]})
    assert schema["additionalProperties"] is False
    assert "4500" in schema["description"]


def test_failed_manager_retry_reuses_all_completed_reviews(prepared, monkeypatch):
    workspace, campaign, manager, _ = prepared
    parent, payload, _, _ = begin_review(prepared, count=2, parallel=2)
    roles = []
    def handle(role, supplied, adapter):
        roles.append(role)
        if role == "research_synthesizer" and roles.count(role) == 1:
            return {"analysis": "Invalid consolidation", "actions": [{"kind": "invented_operation"}]}
        return {"analysis": "Persisted reviewer evidence is sufficient for this consolidation."}
    model_transport(monkeypatch, handle)
    thread, failures = start_supervisor(manager, parent["id"])
    finish_supervisor(thread, failures)
    assert workspace.store.get(parent["id"], "research_run")["status"] == "partial"
    saved = {task["id"]: (task["result_id"], task["attempt_run_ids"]) for task in review_tasks(workspace, parent)}
    assert len(saved) == 2
    workspace.commands.execute(command(workspace, campaign, "decision.refresh", retry_payload(parent, payload), "retry_only_manager"))
    thread, failures = start_supervisor(manager, parent["id"])
    finish_supervisor(thread, failures)
    assert roles.count("comparative_reviewer") == 2 and roles.count("research_synthesizer") == 2
    assert {task["id"]: (task["result_id"], task["attempt_run_ids"]) for task in review_tasks(workspace, parent)} == saved
    current = workspace.store.get(parent["id"], "research_run")
    assert current["status"] == "completed" and current["usage"]["calls"] == 2
    assert sum(api_spend(row.get("usage")) for row in workspace.store.list("research_run", campaign["id"])) == pytest.approx(.00064)


def test_unknown_reviewer_prices_never_bypass_campaign_api_budget(prepared, monkeypatch):
    workspace, campaign, manager, _ = prepared
    workspace.models.save(campaign["id"], {"expected_revision": 0, "policy": {
        "default": {"model": "unpriced-reviewer", "reasoning_effort": "high"},
        "roles": {"campaign_manager": {"model": "test-model", "reasoning_effort": "high",
            "input_usd_per_million": 1, "output_usd_per_million": 2}}}})
    parent, _, _, _ = begin_review(prepared, count=2, parallel=2)
    calls = []
    model_transport(monkeypatch, lambda *args: calls.append(args) or {"analysis": "Must not call an unpriced API model"})
    thread, failures = start_supervisor(manager, parent["id"])
    finish_supervisor(thread, failures)
    assert not calls
    tasks = review_tasks(workspace, parent)
    assert all(task["status"] == "failed" and "prices" in task["error"].lower() for task in tasks)
    runs = workspace.store.list("research_run", campaign["id"])
    assert all(not run.get("usage", {}).get("calls") for run in runs)
    assert sum(api_spend(run.get("usage")) for run in runs) == 0
    assert workspace.store.get(parent["id"], "research_run")["decision_review"]["budget_hold_usd"] == 0
