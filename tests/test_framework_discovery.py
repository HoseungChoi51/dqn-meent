"""Persistent discovery scheduling reuses campaign authority and provider receipts."""
from copy import deepcopy
import json

import pytest

from optimization_framework.campaigns.manager import CampaignManager
from optimization_framework.contracts.commands import Command
from optimization_framework.contracts.requests import CampaignInput, TaskInput
from optimization_framework.execution.service import Workspace
from optimization_framework.research.discovery.controller import DiscoveryController
from optimization_framework.research.discovery.models import DiscoveryArtifact, DiscoveryResult, DiscoveryTaskBrief
from optimization_framework.storage.sqlite import identifier, now


CONFIG = {"configured": True, "enabled": True, "provider": "fixture", "model": "fixture",
          "transport": "chat_completions", "billing_mode": "subscription", "pricing_known": False}


def fixture_dossier():
    return DiscoveryArtifact(kind="problem_dossier", title="Fixture problem analysis", content={
        "representation": "Bounded continuous vectors", "objectives_and_constraints": "Declared objective inside box bounds",
        "available_operations": "Evaluate candidate vectors", "cost_and_noise": "Uncharacterized evaluation cost; fixture only",
        "claims": [{"statement": "The declared evaluator accepts continuous vectors", "basis": "evaluator_contract"}],
        "unknowns": ["Method effectiveness requires measurements"]})


class Adapter:
    calls = []
    def __init__(self, **kwargs):
        self.emit = kwargs["reservation_callback"]
        self.usage = kwargs.get("usage") or {"calls": 0, "subscription_calls": 0, "billing_mode": "subscription", "api_cost_usd": 0}
    def call_with_prompt(self, role, system, context, result_type):
        previous = deepcopy(self.usage)
        call_id = identifier("fixture_call")
        self.usage.update(calls=self.usage["calls"] + 1, subscription_calls=self.usage["subscription_calls"] + 1,
                          pending_reservation={"id": call_id})
        try:
            self.emit({"type": "provider_call_reserved", "role": role, "reservation_id": call_id, "usage": deepcopy(self.usage)})
            self.emit({"type": "provider_request", "role": role, "reservation_id": call_id, "messages": [{"role": "user", "content": json.dumps(context)}]})
        except BaseException:
            self.usage = previous
            self.emit({"type": "provider_call_cancelled_before_send", "role": role, "reservation_id": call_id, "usage": deepcopy(self.usage)})
            raise
        self.calls.append({"role": role, "context": deepcopy(context)})
        result = self.result(role, context)
        self.usage.pop("pending_reservation")
        self.emit({"type": "provider_response", "role": role, "reservation_id": call_id, "output": result.model_dump_json(), "usage": deepcopy(self.usage)})
        return result

    def result(self, role, context):
        artifacts = [fixture_dossier()] if role != "campaign_manager" and context["discovery"]["brief"]["stage"] == "analyze" else []
        return DiscoveryResult(summary=f"Fixture {role} completed", rationale="Inspect the supplied contract.", artifacts=artifacts)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    class Library:
        def versions(self):
            return []
    workspace = Workspace(tmp_path, implementation_client=Library())
    CampaignManager(workspace)
    workspace.discovery = DiscoveryController(workspace, adapter_factory=Adapter)
    monkeypatch.setattr("optimization_framework.research.discovery.controller.provider_status", lambda: dict(CONFIG))
    Adapter.calls = []
    campaign = workspace.create_campaign(CampaignInput(name="Discovery", tasks=[TaskInput(name="Quadratic", problem_id="bounded_continuous")]))
    return workspace, campaign


def command(workspace, campaign, operation, payload, key):
    return Command(id=key, campaign_id=campaign["id"], expected_revision=campaign["version"], operation=operation, payload=payload)


def start(workspace, campaign, **extra):
    payload = {"task_id": workspace.current_tasks(campaign["id"])[0]["id"], **extra}
    cmd = command(workspace, campaign, "discovery.start", payload, "start_discovery")
    return workspace.commands.execute(cmd)["outcome"]["session"], cmd


def test_campaign_charter_can_be_cited_but_other_campaigns_cannot(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    assert workspace.discovery._evidence(session, campaign["id"])["id"] == campaign["id"]
    other = workspace.create_campaign(CampaignInput(name="Another problem", tasks=[TaskInput(name="Other", problem_id="bounded_continuous")]))
    with pytest.raises(ValueError, match="belong to the campaign"):
        workspace.discovery._evidence(session, other["id"])


def settle(workspace, campaign, turns=3):
    for _ in range(turns):
        workspace.discovery.tick(campaign["id"])
        for thread in list(workspace.discovery.threads.values()):
            thread.join(timeout=5)
            assert not thread.is_alive()
        for thread in list(workspace.source_threads.values()):
            thread.join(timeout=5)
            assert not thread.is_alive()


def test_start_is_idempotent_and_task_dependencies_survive_restart(setup):
    workspace, campaign = setup
    session, cmd = start(workspace, campaign)
    assert workspace.commands.execute(cmd)["outcome"]["session"]["id"] == session["id"]
    tasks = workspace.discovery.tasks(session)
    assert len(tasks) == 3
    assert len(next(task for task in tasks if task["brief"]["role"] == "campaign_manager")["dependencies"]) == 2
    restarted = Workspace(workspace.directory, implementation_client=workspace.implementations.client)
    CampaignManager(restarted)
    restarted.discovery = DiscoveryController(restarted, adapter_factory=Adapter)
    restarted.discovery.recover()
    settle(restarted, campaign)
    assert [row["role"] for row in Adapter.calls][-1] == "campaign_manager"
    assert len(Adapter.calls) == 3
    assert all(task["status"] == "completed" for task in restarted.discovery.tasks(session))
    assert len(restarted.store.list("research_run")) == len(restarted.store.list("discovery_response")) == 3
    independent = [call for call in Adapter.calls if call["role"] != "campaign_manager"]
    assert all(call["context"]["discovery"]["dependencies"] == [] for call in independent)
    restarted.discovery.project_pending()
    root = restarted.directory / "campaigns" / campaign["id"] / "discovery"
    assert (root / "agenda.md").exists() and len((root / "records.jsonl").read_text().splitlines()) >= 7


def test_provider_disabled_retains_agenda_without_model_calls(setup, monkeypatch):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    monkeypatch.setattr("optimization_framework.research.discovery.controller.provider_status", lambda: {"configured": False})
    settle(workspace, campaign)
    assert workspace.store.get(session["id"])["status"] == "waiting_for_provider"
    assert not Adapter.calls and not workspace.store.list("research_run")
    assert len([i for i in workspace.store.list("manager_issue") if i["code"] == "discovery_provider"]) == 1


def test_pause_resume_stop_and_stale_control(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    paused = command(workspace, campaign, "discovery.control", {"session_id": session["id"], "action": "pause", "expected_control_revision": 0}, "pause_session")
    workspace.commands.execute(paused)
    settle(workspace, campaign)
    assert not Adapter.calls
    with pytest.raises(ValueError, match="revision"):
        workspace.commands.execute(paused.model_copy(update={"id": "stale_pause"}))
    resumed = command(workspace, campaign, "discovery.control", {"session_id": session["id"], "action": "resume", "expected_control_revision": 1}, "resume_session")
    workspace.commands.execute(resumed)
    stopped = command(workspace, campaign, "discovery.control", {"session_id": session["id"], "action": "stop", "expected_control_revision": 2}, "stop_session")
    workspace.commands.execute(stopped)
    settle(workspace, campaign)
    assert not Adapter.calls and all(t["status"] == "cancelled" for t in workspace.discovery.tasks(session))


def test_paused_strategy_request_reports_wait_and_recovered_issue_is_resolved(setup):
    from optimization_framework.contracts.requests import ResearchInput
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    settle(workspace, campaign)
    task = workspace.discovery.tasks(session)[0]
    issue = workspace.memory.issue(campaign["id"], "discovery_task", "Old provider failure", affected=task["id"])
    workspace.discovery.control(campaign["id"], {"session_id": session["id"], "action": "pause", "expected_control_revision": 0})
    reply = workspace.manager.start(ResearchInput(campaign_id=campaign["id"], mode="generate", message="Develop strategies"))
    assert reply["status"] == "waiting_discovery" and "paused" in reply["wait_reason"]
    assert workspace.store.get(issue["id"])["status"] == "resolved"
    assert len(Adapter.calls) == 3
    workspace.discovery.control(campaign["id"], {"session_id": session["id"], "action": "resume", "expected_control_revision": 1})
    settle(workspace, campaign)
    assert workspace.store.get(reply["id"], "manager_command")["status"] == "completed"
    assert Adapter.calls[-1]["context"]["discovery"]["brief"]["objective"] == "Develop strategies"


def test_cross_campaign_problem_and_dependency_cycles_are_rejected(setup):
    workspace, campaign = setup
    other = workspace.create_campaign(CampaignInput(name="Other", tasks=[TaskInput(name="Other", problem_id="bounded_continuous")]))
    with pytest.raises(ValueError, match="development problem"):
        start(workspace, campaign, task_id=workspace.current_tasks(other["id"])[0]["id"])
    session, _ = start(workspace, campaign)
    before = len(workspace.discovery.tasks(session))
    with pytest.raises(ValueError, match="cycle"):
        workspace.discovery.add_tasks(session, [DiscoveryTaskBrief(key="a", role="analyst", objective="A", dependencies=["b"]),
                                                DiscoveryTaskBrief(key="b", role="analyst", objective="B", dependencies=["a"])], batch_id="cyclic")
    assert len(workspace.discovery.tasks(session)) == before


def test_future_evidence_keys_and_later_revision_form_distinct_independent_groups(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    briefs = [DiscoveryTaskBrief(key="study", role="literature_investigator", stage="study", objective="Study sources"),
        *[DiscoveryTaskBrief(key=key, role=key, stage="generate", objective="Propose independently",
            dependencies=["study"], evidence_ids=["study"]) for key in ("generator_a", "generator_b")],
        DiscoveryTaskBrief(key="critic", role="independent_critic", stage="review", objective="Critique proposals",
            dependencies=["generator_a", "generator_b"], evidence_ids=["study"]),
        DiscoveryTaskBrief(key="revision", role="methodology_specialist", stage="generate", objective="Revise after critique",
            dependencies=["critic"], evidence_ids=["study", "generator_a"])]
    tasks = workspace.discovery.add_tasks(session, briefs, batch_id="full_cycle")
    a, b, revision = tasks[1], tasks[2], tasks[4]
    assert a["brief"]["evidence_ids"] == [tasks[0]["id"]]
    assert a["context_group_id"] == b["context_group_id"] != revision["context_group_id"]
    assert session["round"] == 2
    assert workspace.discovery.add_tasks(session, briefs, batch_id="full_cycle") == tasks


def test_context_compaction_preserves_assigned_evidence_and_recovers_without_new_usage(setup):
    from optimization_framework.research.context import size, LIMIT
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    task = workspace.discovery.tasks(session)[0]
    context = workspace.discovery._context(session, task)
    context["applicable_assets"] = [{"id": "large_catalog_entry", "name": "Optional reuse", "manifest": "x" * LIMIT}]
    context["discovery"]["evidence"] = [{"id": "assigned", "text": "Scientific evidence stays intact"}]
    run = {"id": "context_wait_run", "campaign_id": campaign["id"], "context_snapshot": context,
           "usage": {"calls": 2}, "status": "running"}
    task.update(run_id=run["id"], status="waiting", wait_reason="context_scope")
    workspace.store.put("research_run", run)
    workspace.store.put("discovery_task", task)
    compiled = workspace.discovery._step_context(task, run)
    assert size(compiled) < LIMIT
    assert compiled["discovery"]["evidence"] == context["discovery"]["evidence"]
    workspace.discovery.recover()
    assert workspace.store.get(task["id"])["status"] == "queued"
    assert workspace.store.get(run["id"])["usage"] == {"calls": 2}
    assert not Adapter.calls


def test_large_experiment_records_preserve_measurements_and_one_copy_of_source_text(setup):
    from optimization_framework.research.context import size, LIMIT
    from optimization_framework.research.discovery.knowledge import supplied_passages
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    task = workspace.discovery.tasks(session)[0]
    context = workspace.discovery._context(session, task)
    trials = [{"id": f"trial_projection_{seed}", "campaign_id": campaign["id"], "task_id": session["problem_task_id"],
        "algorithm": "coordinate", "algorithm_config": {"radius": .2}, "seed": seed,
        "execution_manifest": {"large_environment": "x" * LIMIT},
        "result": {"best_objective": seed / 10, "evaluations": 96, "unknown_solver_cost": True,
                   "archive": [{"candidate": [1, 2]}] * 96}} for seed in range(8)]
    passage = {"id": "passage_actual", "capture_id": "capture_actual", "text": "Actual supplied method passage"}
    context["discovery"]["evidence"] = [*trials, passage, deepcopy(passage)]
    compiled = workspace.discovery._step_context(task, {"context_snapshot": context})
    assert size(compiled) < LIMIT
    assert len(list(supplied_passages(compiled))) == 1
    for original, projected in zip(trials, compiled["discovery"]["evidence"]):
        assert projected["algorithm_config"] == original["algorithm_config"]
        assert projected["result"]["best_objective"] == original["result"]["best_objective"]
        assert projected["result"]["evaluations"] == 96 and projected["result"]["unknown_solver_cost"]
        assert projected["result"]["archived_candidate_count"] == 96
        assert "archive" in original["result"] and "execution_manifest" in original


def test_large_historical_receipt_can_be_indexed_without_losing_assigned_passages(setup):
    from optimization_framework.research.context import LIMIT, size
    from optimization_framework.research.discovery.knowledge import supplied_passages
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    task = workspace.discovery.tasks(session)[0]
    context = workspace.discovery._context(session, task)
    passage = {"id": "assigned_passage", "capture_id": "capture", "text": "Assigned scientific support"}
    context["discovery"]["evidence"] = [deepcopy(passage)]
    context["discovery"]["retrieval_receipts"] = [{"id": "receipt", "tool": "source.read", "request_id": "fetch",
        "status": "completed", "result": {"capture": {"id": "capture"}, "passages": [deepcopy(passage),
            {"id": "unassigned_passage", "capture_id": "capture", "text": "x" * LIMIT}]}}]
    compiled = workspace.discovery._step_context(task, {"context_snapshot": context})
    assert size(compiled) < LIMIT
    assert list(supplied_passages(compiled)) == list(supplied_passages(passage))
    assert compiled["discovery"]["retrieval_receipts"][0]["result"]["passage_ids"] == ["assigned_passage", "unassigned_passage"]
    assert context["discovery"]["retrieval_receipts"][0]["result"]["passages"][1]["text"] == "x" * LIMIT


def test_manager_indexes_indirect_detail_but_never_drops_explicit_evidence(setup):
    from optimization_framework.research.context import LIMIT, size
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    task = next(row for row in workspace.discovery.tasks(session) if row["brief"]["role"] == "campaign_manager")
    trial = {"id": "trial_large_detail", "campaign_id": campaign["id"], "task_id": session["problem_task_id"],
             "algorithm": "coordinate", "result": {"large_diagnostic": "x" * LIMIT}}
    workspace.store.put("trial", trial)
    context = {"discovery": {"evidence": [trial], "dependencies": []}, "manager_context": {}}
    compiled = workspace.discovery._step_context(task, {"context_snapshot": context})
    assert size(compiled) < LIMIT
    assert compiled["discovery"]["evidence"][0]["id"] == trial["id"]
    assert "content_omitted" in compiled["discovery"]["evidence"][0]
    task["brief"]["evidence_ids"] = [trial["id"]]
    with pytest.raises(ValueError, match="too much saved context.*working limit 170 KiB"):
        workspace.discovery._step_context(task, {"context_snapshot": context})


def test_saved_step_applies_without_repeating_inference(setup, monkeypatch):
    workspace, campaign = setup
    session, _ = start(workspace, campaign, max_concurrent_tasks=1)
    # Simulate process loss after the immutable output, before result projection.
    def crash(task_id, step):
        raise OSError("fixture projection interruption")
    monkeypatch.setattr(workspace.discovery, "apply_result", crash)
    workspace.discovery.tick(campaign["id"])
    for thread in list(workspace.discovery.threads.values()):
        thread.join(timeout=5)
    assert len(Adapter.calls) == 1
    task = workspace.discovery.tasks(session)[0]
    restarted = Workspace(workspace.directory, implementation_client=workspace.implementations.client)
    CampaignManager(restarted)
    restarted.discovery.recover()
    restarted.discovery.recover()
    assert len(Adapter.calls) == 1 and restarted.store.get(task["id"])["status"] == "completed"


def test_uncertain_dispatch_cannot_be_restarted_as_a_legacy_role_run(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    task = workspace.discovery.tasks(session)[0]
    run_id = "research_" + task["id"]
    task.update(status="running", run_id=run_id)
    workspace.store.put("discovery_task", task)
    workspace.store.put("research_run", {"id": run_id, "campaign_id": campaign["id"], "discovery_task_id": task["id"],
        "discovery_session_id": session["id"], "status": "running", "created_at": now(),
        "usage": {"calls": 1, "pending_reservation": {"id": "unknown"}}})
    from optimization_framework.research.lifecycle import recover
    recover(workspace)
    assert workspace.store.get(run_id)["status"] == "running"
    workspace.discovery.recover()
    assert workspace.store.get(run_id)["status"] == "needs_reconciliation"
    assert workspace.store.get(task["id"])["wait_reason"] == "provider_receipt_reconciliation"
    assert not Adapter.calls


def test_completed_provider_receipt_is_interpreted_after_restart_without_a_call(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    task = workspace.discovery.tasks(session)[0]
    run_id = "research_" + task["id"]
    task.update(status="running", run_id=run_id)
    workspace.store.put("discovery_task", task)
    usage = {"calls": 1, "billing_mode": "subscription", "subscription_calls": 1, "api_cost_usd": 0}
    workspace.store.put("research_run", {"id": run_id, "campaign_id": campaign["id"], "discovery_task_id": task["id"],
        "discovery_session_id": session["id"], "status": "running", "created_at": now(),
        "charter_version": campaign["version"], "guidance_revision": 0, "usage": {**usage, "pending_reservation": {"id": "returned"}}})
    output = DiscoveryResult(summary="Completed before interruption", artifacts=[fixture_dossier()]).model_dump_json()
    workspace.store.put_immutable("discovery_response", {"id": "discovery_response_returned", "campaign_id": campaign["id"],
        "session_id": session["id"], "task_id": task["id"], "step": 0, "created_at": now(),
        "event": {"type": "provider_response", "output": [{"content": output}], "usage": usage}})
    workspace.discovery.recover()
    workspace.discovery.recover()
    assert not Adapter.calls
    assert workspace.store.get(run_id)["status"] == "completed"
    assert workspace.store.get(task["id"])["result"]["summary"] == "Completed before interruption"


def test_concurrent_tasks_share_session_call_limit_and_preserve_synthesis_reserve(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign, model_call_limit=4, synthesis_call_reserve=1)
    workspace.discovery.add_tasks(session, [DiscoveryTaskBrief(key=f"extra_{n}", role="methodology_specialist", objective="Inspect an alternative") for n in range(5)], batch_id="additional")
    settle(workspace, campaign, turns=5)
    assert len(Adapter.calls) <= 4
    assert any(row["role"] == "campaign_manager" for row in Adapter.calls)
    runs = workspace.store.list("research_run", campaign["id"])
    assert sum(row["usage"].get("calls", 0) for row in runs) == len(Adapter.calls)
    assert any(task["status"] == "handed_off" and "allocation" in task["handoff_reason"] for task in workspace.discovery.tasks(session))
    assert not any(task["status"] == "failed" for task in workspace.discovery.tasks(session))


def test_user_steering_goes_through_manager_and_supersedes_old_work(setup):
    import threading
    from optimization_framework.contracts.requests import ResearchInput
    workspace, campaign = setup
    entered, release = threading.Event(), threading.Event()
    class WaitingAdapter(Adapter):
        def call_with_prompt(self, role, system, context, result_type):
            result = super().call_with_prompt(role, system, context, result_type)
            if role == "problem_analyst":
                entered.set()
                assert release.wait(5)
            return result
    workspace.discovery.adapter_factory = WaitingAdapter
    session, _ = start(workspace, campaign, max_concurrent_tasks=1)
    workspace.discovery.tick(campaign["id"])
    assert entered.wait(5)
    reply = workspace.manager.start(ResearchInput(campaign_id=campaign["id"], message="Prioritize robustness and revisit the analysis"))
    assert reply.get("discovery_task_id")
    release.set()
    for thread in list(workspace.discovery.threads.values()):
        thread.join(timeout=5)
    settle(workspace, campaign)
    tasks = workspace.discovery.tasks(session)
    assert next(task for task in tasks if task["brief"]["role"] == "problem_analyst")["status"] == "superseded"
    steered = next(task for task in tasks if task["id"] == reply["discovery_task_id"])
    assert steered["status"] == "completed" and steered["brief"]["role"] == "campaign_manager"
    assert Adapter.calls[-1]["context"]["discovery"]["brief"]["objective"] == "Prioritize robustness and revisit the analysis"
    assert len([m for m in workspace.store.list("message") if m["role"] == "assistant" and m.get("origin") == "llm"]) == 1


def test_tool_search_read_and_continuation_share_budget_and_survive_restart(setup, monkeypatch):
    from optimization_framework.research.discovery.models import DiscoveryToolCall
    workspace, campaign = setup
    searched, fetched = [], []
    def search(query, **kwargs):
        searched.append(query)
        return {"sources": [{"id": "source_fixture", "title": "Fixture method", "url": "https://arxiv.org/abs/2401.00001",
                             "verification": "metadata_retrieved", "excerpt": "Fixture abstract"}]}
    def fetch(url):
        fetched.append(url)
        return b"<article><h2>Method</h2><p>A fixture trust region method adapts its radius.</p></article>", url, "text/html"
    monkeypatch.setattr("optimization_framework.research.evidence.search_literature", search)
    monkeypatch.setattr("optimization_framework.research.literature.fetch_document", fetch)
    class ReaderAdapter(Adapter):
        def result(self, role, context):
            state = context["discovery"]
            if role != "problem_analyst":
                return super().result(role, context)
            if state["step"] == 0:
                return DiscoveryResult(summary="Search for applicable methods", disposition="continue",
                    tools=[DiscoveryToolCall(key="search", tool="source.search", arguments={"query": "trust region"})])
            if state["step"] == 1:
                source_id = state["tool_results"][0]["result"]["sources"][0]["id"]
                return DiscoveryResult(summary="Read the method", disposition="continue",
                    tools=[DiscoveryToolCall(key="read", tool="source.read", arguments={"source_id": source_id})])
            passage = state["tool_results"][-1]["result"]["passages"][0]
            return DiscoveryResult(summary="I inspected passage " + passage["id"], artifacts=[fixture_dossier()])
    workspace.discovery.adapter_factory = ReaderAdapter
    session, _ = start(workspace, campaign, max_concurrent_tasks=1)
    settle(workspace, campaign, turns=1)
    task = workspace.discovery.tasks(session)[0]
    assert task["wait_reason"] == "tools"
    workspace.discovery.tools.tick(session)
    for thread in list(workspace.source_threads.values()):
        thread.join(5)
    restarted = Workspace(workspace.directory, implementation_client=workspace.implementations.client)
    CampaignManager(restarted)
    restarted.discovery = DiscoveryController(restarted, adapter_factory=ReaderAdapter)
    restarted.discovery.recover()
    settle(restarted, campaign, turns=9)
    task = restarted.store.get(task["id"])
    run = restarted.store.get(task["run_id"])
    assert task["status"] == "completed" and task["step"] == 2, task
    assert len(searched) == len(fetched) == 1
    assert run["usage"]["calls"] == 3
    assert len([c for c in Adapter.calls if c["role"] == "problem_analyst"]) == 3
    assert len(restarted.store.list("discovery_tool_receipt")) == 2
    attempts = [row for row in restarted.store.list("discovery_attempt") if row["task_id"] == task["id"]]
    assert len(attempts) == 3 and "passage" in attempts[-1]["context_snapshot"]["discovery"]["tool_results"][-1]["result"]["passages"][0]["id"]
    assert not [issue for issue in restarted.store.list("manager_issue") if issue["code"] == "model_cost_capture"]


def test_pause_prevents_tool_dispatch_and_interrupted_tools_are_not_repeated(setup, monkeypatch):
    from optimization_framework.research.discovery.models import DiscoveryToolCall
    workspace, campaign = setup
    class ToolAdapter(Adapter):
        def result(self, role, context):
            return DiscoveryResult(summary="Need literature", disposition="continue",
                tools=[DiscoveryToolCall(key="search", tool="source.search", arguments={"query": "fixture"})])
    workspace.discovery.adapter_factory = ToolAdapter
    session, _ = start(workspace, campaign, max_concurrent_tasks=1)
    settle(workspace, campaign, turns=1)
    request = workspace.store.list("discovery_tool")[0]
    workspace.discovery.control(campaign["id"], {"session_id": session["id"], "action": "pause", "expected_control_revision": 0})
    settle(workspace, campaign)
    assert workspace.store.get(request["id"])["status"] == "pending"
    request.update(status="running", dispatched_at=now())  # Simulate a lost process after dispatch.
    workspace.store.put("discovery_tool", request)
    workspace.discovery.tools.recover()
    receipt = workspace.store.get("receipt_" + request["id"])
    assert receipt["uncertain"] and receipt["status"] == "failed"
    workspace.discovery.tools.recover()
    assert len(workspace.store.list("discovery_tool_receipt")) == 1


def test_replaying_assignments_at_capacity_is_idempotent(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign, max_tasks=3)
    briefs = [DiscoveryTaskBrief.model_validate(row["brief"]) for row in workspace.discovery.tasks(session)]
    assert len(workspace.discovery.add_tasks(session, briefs, batch_id="initial")) == 3
    with pytest.raises(ValueError, match="allocation"):
        workspace.discovery.add_tasks(session, [DiscoveryTaskBrief(key="excess", role="analyst", objective="Extra")], batch_id="extra")


def test_new_evidence_wakes_manager_once_and_invalid_results_get_bounded_correction(setup):
    workspace, campaign = setup
    class Correcting(Adapter):
        def result(self, role, context):
            if role == "problem_analyst" and context["discovery"]["step"] == 0:
                return DiscoveryResult(summary="An incomplete analysis without its required dossier")
            if role == "problem_analyst":
                assert "problem_dossier" in context["discovery"]["validation_feedback"][0]["error"]
            return super().result(role, context)
    workspace.discovery.adapter_factory = Correcting
    session, _ = start(workspace, campaign, max_concurrent_tasks=1)
    settle(workspace, campaign, turns=7)
    analyst = workspace.discovery.tasks(session)[0]
    assert analyst["status"] == "completed" and analyst["corrections"] == 1
    assert workspace.store.get(analyst["run_id"])["usage"]["calls"] == 2
    before = len(Adapter.calls)
    session = workspace.store.get(session["id"])
    workspace.discovery.add_tasks(session, [DiscoveryTaskBrief(key="followup", role="domain_specialist", objective="Inspect a new uncertainty")], batch_id="followup")
    settle(workspace, campaign, turns=7)
    assert [row["role"] for row in Adapter.calls[before:]] == ["domain_specialist", "campaign_manager"]
    settled = len(Adapter.calls)
    settle(workspace, campaign, turns=4)
    assert len(Adapter.calls) == settled


def test_amendment_keeps_spent_resources_and_immutable_previous_policy(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    settle(workspace, campaign)
    policy = {**session["policy"], "model_call_limit": 120, "max_concurrent_tasks": 2}
    cmd = command(workspace, campaign, "discovery.amend", {"session_id": session["id"],
        "expected_control_revision": 0, "policy": policy, "reason": "Allow a larger literature study"}, "amend_discovery")
    amended = workspace.commands.execute(cmd)["outcome"]["session"]
    assert amended["control_revision"] == 1 and amended["policy"]["model_call_limit"] == 120
    assert workspace.store.list("discovery_policy")[0]["policy"]["model_call_limit"] == 96
    assert workspace.commands.execute(cmd)["outcome"]["session"] == amended
    bad = cmd.model_copy(update={"id": "bad_amendment", "payload": {**cmd.payload, "expected_control_revision": 1,
        "policy": {**policy, "task_id": "different_problem"}}})
    with pytest.raises(ValueError, match="frozen problem"):
        workspace.commands.execute(bad)
