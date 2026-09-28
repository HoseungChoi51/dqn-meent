"""A manager retires obsolete unsent work through an explicit, durable tool."""
from copy import deepcopy

import pytest

from test_framework_discovery import Adapter, CONFIG, command, setup, settle, start
from optimization_framework.research.discovery.controller import DiscoveryController
from optimization_framework.research.discovery.models import DiscoveryArtifact, DiscoveryResult, DiscoveryTaskBrief, DiscoveryToolCall


def prepared(setup, monkeypatch):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    settle(workspace, campaign)
    initial_manager = next(row for row in workspace.discovery.tasks(session) if row["brief"]["role"] == "campaign_manager")
    session = workspace.store.get(session["id"])
    old, replacement, manager = workspace.discovery.add_tasks(session, [
        DiscoveryTaskBrief(key="broad", role="analyst", objective="Overly broad assignment"),
        DiscoveryTaskBrief(key="narrow", role="analyst", objective="Narrow replacement"),
        DiscoveryTaskBrief(key="resolve", role="campaign_manager", stage="manage", objective="Retire obsolete work and synthesize",
            dependencies=["narrow"])], batch_id="replacement", parent_task_id=initial_manager["id"])
    compile_context = workspace.discovery._step_context
    def bounded_context(task, run):
        if task["id"] == old["id"]:
            raise ValueError("Task context exceeds the bounded allowance")
        return compile_context(task, run)
    monkeypatch.setattr(workspace.discovery, "_step_context", bounded_context)
    class ResolvingAdapter(Adapter):
        def result(self, role, context):
            if context["discovery"]["brief"]["key"] != "resolve":
                return super().result(role, context)
            if not context["discovery"]["tool_results"]:
                return DiscoveryResult(summary="Retire the unused broad assignment in favor of its completed replacement",
                    disposition="continue", tools=[DiscoveryToolCall(key="retire_broad", tool="task.supersede", arguments={
                        "task_id": old["id"], "replacement_task_id": replacement["id"], "reason": "The narrower assignment completed the required analysis"})])
            receipt = context["discovery"]["tool_results"][0]
            assert receipt["status"] == "completed" and receipt["result"]["status"] == "superseded"
            return DiscoveryResult(summary="The narrower work is complete and the obsolete assignment is formally retired",
                artifacts=[DiscoveryArtifact(kind="synthesis", title="Bounded fixture synthesis", content={"limits": "Fixture only"})],
                session_action="complete")
    workspace.discovery.adapter_factory = ResolvingAdapter
    for _ in range(3):
        workspace.discovery.tick(campaign["id"])
        for thread in list(workspace.discovery.threads.values()):
            thread.join(5)
            assert not thread.is_alive()
        manager = workspace.store.get(manager["id"])
        if manager.get("wait_reason") == "tools":
            break
    assert manager.get("wait_reason") == "tools"
    old = workspace.store.get(old["id"])
    assert old["wait_reason"] == "context_scope" and not old.get("attempt_id")
    request = workspace.store.get(manager["pending_tool_ids"][0], "discovery_tool")
    return workspace, campaign, session, old, replacement, manager, request


def test_explicit_manager_supersede_preserves_history_and_allows_final_synthesis(setup, monkeypatch):
    workspace, campaign, session, old, replacement, manager, request = prepared(setup, monkeypatch)
    settle(workspace, campaign, turns=3)
    assert workspace.store.get(session["id"])["status"] == "completed"
    retired = workspace.store.get(old["id"])
    assert retired["status"] == "superseded" and retired["wait_reason"] is None
    assert retired["superseded_by_task_id"] == replacement["id"]
    assert retired["error"] == old["error"]  # The failed handoff remains reviewable.
    run = workspace.store.get(old["run_id"])
    assert run["status"] == "stopped" and run["usage"] == {}
    assert not any(row["task_id"] == old["id"] for row in workspace.store.list("discovery_attempt"))
    resolution = workspace.store.get(retired["resolution_id"], "discovery_task_resolution")
    assert resolution["manager_task_id"] == manager["id"] and resolution["manager_step_id"] == request["step_id"]
    assert resolution["previous_error"] == old["error"]
    receipt = workspace.store.get("receipt_" + request["id"], "discovery_tool_receipt")
    assert receipt["result"] == resolution["outcome"] and receipt["status"] == "completed"
    assert workspace.discovery.tools.execute(request) == receipt["result"]  # Already committed effects replay as receipts.
    workspace.discovery.recover()
    assert workspace.store.get(old["id"])["status"] == "superseded"
    workspace.discovery.project_pending()
    records = (workspace.directory / "campaigns" / campaign["id"] / "discovery" / "records.jsonl").read_text()
    assert '"kind": "discovery_task_resolution"' in records


def test_supersede_receipt_recovers_after_committed_effect_without_reapplying(setup, monkeypatch):
    workspace, campaign, session, old, replacement, manager, request = prepared(setup, monkeypatch)
    request["status"] = "running"
    workspace.store.put("discovery_tool", request)
    outcome = workspace.discovery.tools.execute(request)
    assert not workspace.store.list("discovery_tool_receipt")
    workspace.discovery.tools.recover()
    receipt = workspace.store.get("receipt_" + request["id"], "discovery_tool_receipt")
    assert receipt["result"] == outcome and not receipt["uncertain"]
    workspace.discovery.tools.recover()
    assert len(workspace.store.list("discovery_task_resolution")) == 1
    assert len(workspace.store.list("discovery_tool_receipt")) == 1


@pytest.mark.parametrize("change", ["supersede", "cancel", "pause", "stop", "dependency_requeued"])
def test_dispatch_rechecks_controls_and_dependencies_after_queue_snapshot(setup, monkeypatch, change):
    workspace, campaign, session, old, replacement, manager, request = prepared(setup, monkeypatch)
    old.update(status="queued", wait_reason=None)
    if change == "dependency_requeued":
        old["dependencies"] = [replacement["id"]]
    workspace.store.put("discovery_task", old)
    monkeypatch.setattr(workspace.discovery, "_step_context", lambda task, run:
        DiscoveryController._step_context(workspace.discovery, task, run))
    monkeypatch.setattr(workspace.discovery.tools, "tick", lambda session: None)
    gate = {"next_snapshot": False, "before_lock": False, "changed": False}
    underlying = workspace.lock
    def intervene():
        gate["changed"] = True
        if change == "supersede":
            workspace.discovery.tools.execute(request)
        elif change == "cancel":
            workspace.commands.execute(command(workspace, campaign, "research.control", {
                "run_id": old["run_id"], "action": "stop", "expected_control_revision": 0}, "cancel_before_dispatch"))
        elif change in {"pause", "stop"}:
            current = workspace.store.get(session["id"])
            workspace.discovery.control(campaign["id"], {"session_id": session["id"], "action": change,
                "expected_control_revision": current["control_revision"]})
        else:
            dependency = workspace.store.get(replacement["id"])
            dependency["status"] = "queued"
            workspace.store.put("discovery_task", dependency)
    class InterleavingLock:
        def __enter__(self):
            if gate["before_lock"]:
                gate["before_lock"] = False
                intervene()
            return underlying.__enter__()
        def __exit__(self, *args):
            return underlying.__exit__(*args)
    monkeypatch.setattr(workspace, "lock", InterleavingLock())
    def configured_provider():
        gate["next_snapshot"] = True
        return dict(CONFIG)
    monkeypatch.setattr("optimization_framework.research.discovery.controller.provider_status", configured_provider)
    saved_tasks = workspace.discovery.tasks
    def snapshot(current):
        result = saved_tasks(current)
        if gate["next_snapshot"]:
            gate["next_snapshot"] = False
            gate["before_lock"] = True
        return result
    monkeypatch.setattr(workspace.discovery, "tasks", snapshot)
    calls_before = len(Adapter.calls)
    workspace.discovery.tick(campaign["id"])
    for thread in list(workspace.discovery.threads.values()):
        thread.join(5)
        assert not thread.is_alive()
    assert gate["changed"]
    assert workspace.store.get(old["id"])["status"] == {
        "supersede": "superseded", "cancel": "cancelled", "pause": "queued", "stop": "cancelled",
        "dependency_requeued": "queued"}[change]
    assert len(Adapter.calls) == calls_before
    assert not any(row["task_id"] == old["id"] for row in workspace.store.list("discovery_attempt"))
    assert workspace.store.get(old["run_id"])["usage"] == {}


@pytest.mark.parametrize("action", ["pause", "stop"])
@pytest.mark.parametrize("configured", [True, False])
def test_provider_availability_cannot_overwrite_researcher_control(setup, monkeypatch, action, configured):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    session["status"] = "waiting_for_provider" if configured else "running"
    workspace.store.put("discovery_session", session)
    issue = workspace.memory.issue(campaign["id"], "discovery_provider", "Provider was unavailable", affected=session["id"])
    def provider_check_with_control():
        workspace.commands.execute(command(workspace, campaign, "discovery.control", {
            "session_id": session["id"], "action": action, "expected_control_revision": 0}, "control_during_provider_check"))
        return {**CONFIG, "configured": configured}
    monkeypatch.setattr("optimization_framework.research.discovery.controller.provider_status", provider_check_with_control)
    workspace.discovery.tick(campaign["id"])
    current = workspace.store.get(session["id"])
    assert current["status"] == {"pause": "paused", "stop": "stopped"}[action]
    assert current["control_revision"] == 1
    assert workspace.store.get(issue["id"])["status"] == "pending"
    assert not Adapter.calls and not workspace.store.list("discovery_attempt")


@pytest.mark.parametrize("mutation,match", [
    ("specialist", "Only the campaign manager"), ("paused", "active discovery"),
    ("stale", "guidance changed"), ("used", "unsent assignments"),
    ("reserved", "unsent assignments"), ("attempt", "already has an attempt"),
    ("tool", "tool effect"), ("completed", "Only queued"),
    ("foreign", "frozen problem"), ("failed_replacement", "completed successfully"),
    ("dependent_replacement", "replacement cannot depend"), ("self", "cannot supersede itself"),
    ("active_worker", "active worker"),
])
def test_supersede_rejects_unsafe_or_unauthorized_mutation(setup, monkeypatch, mutation, match):
    workspace, campaign, session, old, replacement, manager, request = prepared(setup, monkeypatch)
    if mutation == "specialist":
        manager["brief"]["role"] = "analyst"
        workspace.store.put("discovery_task", manager)
    elif mutation == "paused":
        session["status"] = "paused"
        workspace.store.put("discovery_session", session)
    elif mutation == "stale":
        request["guidance_revision"] -= 1
    elif mutation in {"used", "reserved"}:
        run = workspace.store.get(old["run_id"])
        run["usage"] = {"calls": 1} if mutation == "used" else {"pending_reservation": {"id": "pending"}}
        workspace.store.put("research_run", run)
    elif mutation in {"attempt", "tool"}:
        workspace.store.put("discovery_" + mutation, {"id": "existing_effect", "campaign_id": campaign["id"], "task_id": old["id"]})
    elif mutation == "completed":
        old["status"] = "completed"
        workspace.store.put("discovery_task", old)
    elif mutation in {"foreign", "failed_replacement", "dependent_replacement"}:
        replacement = workspace.store.get(replacement["id"])
        if mutation == "foreign":
            replacement["session_id"] = "other_session"
        elif mutation == "failed_replacement":
            replacement["status"] = "failed"
        else:
            replacement["dependencies"] = [old["id"]]
        workspace.store.put("discovery_task", replacement)
    elif mutation == "self":
        request["call"]["arguments"]["task_id"] = manager["id"]
    elif mutation == "active_worker":
        class Worker:
            def is_alive(self):
                return True
        workspace.discovery.threads[old["id"]] = Worker()
    previous = deepcopy(workspace.store.get(old["id"]))
    with pytest.raises(ValueError, match=match):
        workspace.discovery.tools.execute(request)
    assert workspace.store.get(old["id"]) == previous
    assert not workspace.store.list("discovery_task_resolution")
