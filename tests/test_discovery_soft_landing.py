"""Quota exhaustion preserves ordinary work and never authorizes more spend."""
from copy import deepcopy

from optimization_framework.research.discovery import allowance
from optimization_framework.research.discovery.controller import DiscoveryController
from optimization_framework.research.discovery.models import DiscoveryResult, DiscoveryTaskBrief, DiscoveryToolCall
from optimization_framework.research.progress import view as progress_view
from optimization_framework.research.engine import BudgetUnavailable
from optimization_framework.storage.sqlite import now

from test_framework_discovery import Adapter, fixture_dossier, setup, settle, start


def inspect_tool(key):
    return DiscoveryToolCall(key=key, tool="implementation.inspect")


def test_overflow_defers_whole_batch_and_uses_one_wrap_up_without_corrections(setup):
    workspace, campaign = setup

    class Overflow(Adapter):
        def result(self, role, context):
            if role != "problem_analyst":
                return super().result(role, context)
            step = context["discovery"]["step"]
            if step == 0:
                return DiscoveryResult(summary="Save the contract before inspecting code", disposition="continue",
                    artifacts=[fixture_dossier()], tools=[inspect_tool("first")])
            if step == 1:
                assert context["discovery"]["allocation"]["task_tools_remaining"] == 1
                return DiscoveryResult(summary="A known gap needs two reads", disposition="continue",
                    tools=[inspect_tool("second"), inspect_tool("third")])
            assert context["discovery"]["allocation"]["phase"] == "wrap_up"
            assert context["discovery"]["own_artifacts"]
            return DiscoveryResult(summary="Contract saved; implementation inspection is unfinished.",
                disposition="handoff", questions_for_manager=["Inspect the two remaining interfaces in a scoped follow-up."])

    workspace.discovery = DiscoveryController(workspace, adapter_factory=Overflow)
    session, _ = start(workspace, campaign, max_tools_per_task=2, max_calls_per_task=4)
    settle(workspace, campaign, turns=8)
    task = next(row for row in workspace.discovery.tasks(session) if row["brief"]["role"] == "problem_analyst")
    assert task["status"] == "handed_off" and not task.get("error")
    assert task.get("corrections", 0) == 0
    assert workspace.store.get(task["run_id"])["usage"]["calls"] == 3
    assert len([row for row in workspace.store.list("discovery_tool") if row["task_id"] == task["id"]]) == 1
    handoff = workspace.store.get(task["handoff_id"])
    assert not handoff["scientific_complete"] and handoff["artifact_ids"] == task["artifact_ids"]
    assert handoff["receipt_ids"] and handoff["questions_for_manager"]
    assert [tool["key"] for tool in handoff["deferred_tool_batches"][0]["tools"]] == ["second", "third"]
    managers = [call for call in Adapter.calls if call["role"] == "campaign_manager"]
    assert managers and task["handoff_id"] in str(managers[-1]["context"])
    assert not workspace.store.list("discovery_feedback")


def test_ignoring_wrap_up_does_not_execute_tools_or_loop(setup):
    workspace, campaign = setup

    class IgnoresWrapUp(Adapter):
        def result(self, role, context):
            if role == "problem_analyst":
                return DiscoveryResult(summary="Still asking for code inspection", disposition="continue", tools=[inspect_tool("inspect")])
            return super().result(role, context)

    workspace.discovery = DiscoveryController(workspace, adapter_factory=IgnoresWrapUp)
    session, _ = start(workspace, campaign, max_calls_per_task=1)
    settle(workspace, campaign, turns=7)
    task = next(row for row in workspace.discovery.tasks(session) if row["brief"]["role"] == "problem_analyst")
    assert task["status"] == "handed_off"
    assert workspace.store.get(task["run_id"])["usage"]["calls"] == 1
    assert not workspace.store.list("discovery_tool")
    saved = workspace.store.get(task["handoff_id"])
    assert saved["unexecuted_tools"][0]["key"] == "inspect"
    assert not workspace.store.list("discovery_feedback")


def test_final_task_call_can_complete_scientific_work(setup):
    workspace, campaign = setup

    class Finishes(Adapter):
        def result(self, role, context):
            if role == "problem_analyst":
                if context["discovery"]["step"] == 0:
                    return DiscoveryResult(summary="Inspect then conclude", disposition="continue", tools=[inspect_tool("inspect")])
                assert context["discovery"]["allocation"]["phase"] == "wrap_up"
                assert context["discovery"]["allocation"]["task_calls_remaining"] == 1
                assert context["discovery"]["tool_results"]
            return super().result(role, context)

    workspace.discovery = DiscoveryController(workspace, adapter_factory=Finishes)
    session, _ = start(workspace, campaign, max_calls_per_task=2)
    settle(workspace, campaign, turns=7)
    task = next(row for row in workspace.discovery.tasks(session) if row["brief"]["role"] == "problem_analyst")
    assert task["status"] == "completed" and task["artifact_ids"]
    assert not task.get("handoff_id")
    assert workspace.store.get(task["run_id"])["usage"]["calls"] == 2


def test_session_ceiling_stops_repeated_manager_continuation_with_partial_wrap_up(setup):
    workspace, campaign = setup

    class Continues(Adapter):
        def result(self, role, context):
            if role == "campaign_manager":
                return DiscoveryResult(summary="Measurements are still missing", disposition="continue")
            return super().result(role, context)

    workspace.discovery = DiscoveryController(workspace, adapter_factory=Continues)
    session, _ = start(workspace, campaign, model_call_limit=5, synthesis_call_reserve=2)
    settle(workspace, campaign, turns=12)
    current = workspace.store.get(session["id"])
    assert current["status"] == "waiting_for_direction" and current["wrap_up_id"]
    assert len(Adapter.calls) == 5
    assert len(workspace.store.list("discovery_wrap_up")) == 1
    assert workspace.store.get(current["wrap_up_id"])["scientific_complete"] is False
    before = deepcopy(workspace.store.list("discovery_handoff"))
    workspace.discovery.recover()
    settle(workspace, campaign, turns=3)
    assert before == workspace.store.list("discovery_handoff") and len(Adapter.calls) == 5
    assert len(workspace.store.list("discovery_wrap_up")) == 1
    progress = progress_view(workspace, campaign["id"])
    assert progress["status"] == "waiting" and "wrapped up" in progress["headline"]
    assert progress["task_counts"]["handed_off"]


def test_manager_cannot_use_synthesis_reserve_for_new_assignments(setup):
    workspace, campaign = setup

    class Assigns(Adapter):
        def result(self, role, context):
            if role == "campaign_manager":
                return DiscoveryResult(summary="Need a new study", proposed_tasks=[
                    DiscoveryTaskBrief(key="unfunded", role="source_analyst", objective="Read more papers", stage="study")])
            return super().result(role, context)

    workspace.discovery = DiscoveryController(workspace, adapter_factory=Assigns)
    session, _ = start(workspace, campaign, model_call_limit=4, synthesis_call_reserve=2)
    settle(workspace, campaign, turns=10)
    assert len(Adapter.calls) == 4
    assert not any(row["brief"]["key"] == "unfunded" for row in workspace.discovery.tasks(session))
    assert all(row["deferred_tasks"] for row in workspace.store.list("discovery_handoff") if row["task_id"] in
        {task["id"] for task in workspace.discovery.tasks(session) if task["brief"]["role"] == "campaign_manager"})
    assert not workspace.store.list("discovery_feedback")


def test_recovery_migrates_quota_failure_only_without_calls_or_guidance_changes(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    tasks = workspace.discovery.tasks(session)
    original = next(row for row in tasks if row["brief"]["role"] == "problem_analyst")
    error = "Result validation: This task's tool allowance is exhausted; ask the manager to reconsider its scope"
    task = deepcopy(original)
    task.update(status="failed", error=error, step=8, run_id="research_" + task["id"])
    usage = {"calls": 8, "subscription_calls": 8, "billing_mode": "subscription", "api_cost_usd": 0}
    workspace.store.put("research_run", {"id": task["run_id"], "campaign_id": campaign["id"], "discovery_session_id": session["id"],
        "discovery_task_id": task["id"], "status": "failed", "usage": usage, "error": error})
    workspace.store.put("discovery_task", task)
    result = DiscoveryResult(summary="Partial interface findings; no measurements", disposition="continue", tools=[inspect_tool("gap")])
    step = {"id": task["id"] + "_step_7", "campaign_id": campaign["id"], "session_id": session["id"],
        "task_id": task["id"], "created_at": now(), "usage": usage, "result": result.model_dump(mode="json")}
    workspace.store.put_immutable("discovery_step", step)
    other = next(row for row in tasks if row["brief"]["role"] == "skeptical_domain_analyst")
    other.update(status="failed", error="An unrelated validation error")
    workspace.store.put("discovery_task", other)
    workspace.memory.issue(campaign["id"], "discovery_task", error, affected=task["id"])
    before = workspace.memory.state(campaign["id"])["guidance_revision"]
    workspace.discovery.recover()
    workspace.discovery.recover()
    recovered = workspace.store.get(task["id"])
    assert recovered["status"] == "handed_off" and recovered["prior_error"] == error
    assert workspace.store.get(other["id"])["status"] == "failed"
    assert len(workspace.store.list("discovery_handoff")) == 1 and not Adapter.calls
    assert workspace.store.get(task["run_id"])["usage"] == usage
    assert workspace.store.get(step["id"])["content_hash"]
    assert workspace.store.get(recovered["handoff_id"])["summary"] == result.summary
    assert all(row["status"] == "resolved" for row in workspace.store.list("manager_issue") if row["affected"] == task["id"])
    assert workspace.memory.state(campaign["id"])["guidance_revision"] == before


def test_live_budget_updates_without_replacing_scientific_snapshot(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    task = workspace.discovery.tasks(session)[0]
    frozen = workspace.discovery._context(session, task)
    run = {"id": "research_" + task["id"], "campaign_id": campaign["id"], "discovery_session_id": session["id"],
        "discovery_task_id": task["id"], "context_snapshot": deepcopy(frozen), "usage": {"calls": 7}}
    workspace.store.put("research_run", run)
    context = workspace.discovery._step_context(task, run)
    current = context["discovery"]["allocation"]
    assert current["calls_remaining"] == session["policy"]["model_call_limit"] - 7
    assert current["task_calls_remaining"] == 1 and current["phase"] == "wrap_up"
    assert run["context_snapshot"] == frozen


def test_source_allocation_yields_deferred_receipt_without_network(setup, monkeypatch):
    workspace, campaign = setup
    sent = []
    monkeypatch.setattr(workspace.discovery.tools, "_run", lambda request: sent.append(request))

    class Searches(Adapter):
        def result(self, role, context):
            if role == "problem_analyst":
                assert context["discovery"]["allocation"]["source_requests_remaining"] == 0
                if context["discovery"]["step"] == 0:
                    return DiscoveryResult(summary="Need a source", disposition="continue", tools=[
                        DiscoveryToolCall(key="search", tool="source.search", arguments={"query": "grating"})])
                receipt = context["discovery"]["tool_results"][0]
                assert receipt["status"] == "deferred" and receipt["result"]["executed"] is False
                return DiscoveryResult(summary="Source coverage remains incomplete", disposition="handoff")
            return super().result(role, context)

    workspace.discovery.adapter_factory = Searches
    session, _ = start(workspace, campaign, source_request_limit=0)
    settle(workspace, campaign, turns=6)
    assert not sent
    requests = workspace.store.list("discovery_tool")
    assert len(requests) == 1 and requests[0]["status"] == "deferred" and not requests[0].get("dispatched_at")
    assert not workspace.store.list("discovery_feedback")


def test_unaffordable_synthesis_saves_partial_wrap_up_without_retrying(setup):
    workspace, campaign = setup

    class Unaffordable(Adapter):
        attempts = 0
        def call_with_prompt(self, role, system, context, result_type):
            if role == "campaign_manager":
                self.__class__.attempts += 1
                raise BudgetUnavailable("The next role cannot fit within the remaining LLM spending cap.")
            return super().call_with_prompt(role, system, context, result_type)

    workspace.discovery.adapter_factory = Unaffordable
    session, _ = start(workspace, campaign)
    settle(workspace, campaign, turns=8)
    assert Unaffordable.attempts == 1 and len(Adapter.calls) == 2
    assert workspace.store.get(session["id"])["wrap_up_id"]
    assert len(workspace.store.list("discovery_wrap_up")) == 1
    assert not any(row["status"] == "failed" for row in workspace.discovery.tasks(session))


def test_handoff_does_not_satisfy_a_scientific_prerequisite(setup):
    workspace, campaign = setup

    class Partial(Adapter):
        def result(self, role, context):
            if role == "problem_analyst":
                return DiscoveryResult(summary="Partial analysis needs more evidence", disposition="handoff")
            return super().result(role, context)

    workspace.discovery.adapter_factory = Partial
    session, _ = start(workspace, campaign)
    first = next(row for row in workspace.discovery.tasks(session) if row["brief"]["role"] == "problem_analyst")
    later = workspace.discovery.add_tasks(session, [DiscoveryTaskBrief(key="dependent", role="source_analyst",
        stage="study", objective="Study after complete analysis", dependencies=[first["id"]])], batch_id="prerequisite")[0]
    settle(workspace, campaign, turns=7)
    assert workspace.store.get(later["id"])["status"] == "blocked"
    assert not any(row["role"] == "source_analyst" for row in Adapter.calls)
