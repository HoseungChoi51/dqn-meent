"""Durable, bounded parallel reassessment with one publishing campaign manager.

Each reviewer attempt owns a research run and an adapter. The parent records only
its manager call, so campaign accounting never charges child calls twice.
"""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from copy import deepcopy
import json
import threading
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from optimization_framework.storage.sqlite import identifier, now
from optimization_framework.research.providers import api_spend


TERMINAL = {"completed", "failed", "stopped", "needs_reconciliation"}


class ReviewerAssessment(BaseModel):
    """Private assessment. Keep the entire JSON response within 4500 UTF-8 bytes.

    Write concise analysis; each dependency or dissent note is at most 200
    characters. Only the final campaign manager publishes choices or actions.
    """
    model_config = ConfigDict(extra="forbid")
    analysis: str = Field(min_length=1, max_length=2000)
    dependencies: list[Annotated[str, Field(max_length=200)]] = Field(default_factory=list, max_length=3)
    dissent: list[Annotated[str, Field(max_length=200)]] = Field(default_factory=list, max_length=3)
    evidence_ids: list[Annotated[str, Field(max_length=200)]] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def bounded_review(self):
        if not self.analysis.strip():
            raise ValueError("A reviewer must provide a substantive assessment")
        if len(json.dumps(self.model_dump(), ensure_ascii=False).encode("utf-8")) > 4500:
            raise ValueError("Keep the reviewer assessment within 4500 UTF-8 bytes for final manager consolidation")
        return self


def _tasks(workspace, parent):
    return [workspace.store.get(key, "decision_review_task") for key in parent["decision_review"]["task_ids"]]


def _current(workspace, parent):
    return (workspace.store.get(parent["campaign_id"], "campaign")["version"] == parent["charter_version"]
        and workspace.memory.state(parent["campaign_id"])["guidance_revision"] == parent.get("guidance_revision", 0))


def _manager_hold(parent):
    from .model_policy import resolve_policy
    request = parent["request"]
    config = resolve_policy(request["provider_snapshot"], request.get("model_policy"), "research_synthesizer")
    if config.get("billing_mode") == "subscription":
        return 0.0
    if not config.get("pricing_known"):
        raise ValueError("Configure API prices for the campaign manager before reserving its final synthesis call")
    # The adapter rejects any larger complete prompt. Reserve that upper bound,
    # then replace the hold with the actual transport reservation atomically.
    return (250_000 * config["input_usd_per_million"]
        + request.get("max_output_tokens", 2048) * config["output_usd_per_million"]) / 1_000_000


def _reserve_manager(workspace, parent):
    hold = 0.0 if parent.get("result_id") else _manager_hold(parent)
    campaign = workspace.store.get(parent["campaign_id"], "campaign")
    spent = sum(api_spend(row.get("usage")) + (row.get("decision_review") or {}).get("budget_hold_usd", 0)
        for row in workspace.store.list("research_run", parent["campaign_id"]))
    spent -= parent["decision_review"].get("budget_hold_usd", 0)
    spent += workspace.implementations.api_committed(parent["campaign_id"])
    if spent + hold > campaign["llm_budget_usd"] + 1e-9:
        raise ValueError("The final campaign manager call does not fit the remaining API allowance; no reviewers were dispatched")
    parent["decision_review"]["budget_hold_usd"] = hold


def prepare(coordinator, parent):
    """Freeze task membership and contexts in the parent admission transaction."""
    workspace = coordinator.workspace
    refresh = workspace.store.get(parent["decision_refresh_id"], "decision_refresh")
    if refresh.get("review_protocol") != "parallel_v1" or parent.get("decision_review"):
        return parent  # Older saved review commands retain their original protocol.
    from .decision_review_context import partition_context, synthesis_context
    from .engine import _safe_context
    safe_context = _safe_context(parent["context_snapshot"])
    # Reserve space for the complete typed manager prompt and every bounded
    # report before sending any reviewer call. Merge related overflow groups
    # when needed; never trim the original scientific evidence to make room.
    for maximum in range(8, 0, -1):
        groups = partition_context(safe_context, max_groups=maximum)
        reserves = [{"task_id": "reserved_" + str(index), "title": group["title"],
            "decision_ids": group["decision_ids"], "role": "comparative_reviewer",
            "result": {"reserved_assessment": str(index) * 4500}} for index, group in enumerate(groups)]
        projected = _payload(parent, synthesis_context(safe_context, reserves), "Final manager synthesis")
        if len(json.dumps(projected, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 20_000 <= 250_000:
            break
    else:
        raise ValueError("This reassessment cannot fit its original evidence and final manager synthesis. Select a smaller group of decisions; no reviewers were dispatched.")
    parent["decision_review"] = {"phase": "queued", "task_ids": [],
        "max_parallel_reviews": refresh.get("max_parallel_reviews", 3), "budget_hold_usd": 0.0}
    for group in groups:
        task = {"id": identifier("decision_review"), "campaign_id": parent["campaign_id"],
            "parent_run_id": parent["id"], "role": "comparative_reviewer", "title": group["title"],
            "decision_ids": group["decision_ids"], "context_snapshot": group["context"],
            "status": "queued", "attempt_run_ids": [], "created_at": now()}
        workspace.store.put("decision_review_task", task, "decision.review_queued")
        parent["decision_review"]["task_ids"].append(task["id"])
    if not groups:
        raise ValueError("Decision reassessment needs at least one selected decision group")
    parent["request"]["max_calls"] = len(groups) + 1
    _reserve_manager(workspace, parent)
    workspace.store.put("research_run", parent, "decision.parallel_review_prepared")
    return parent


def _retry_issue(workspace, parent, tasks):
    if parent["status"] in {"running", "stopping"}:
        return "The current review is still running."
    if parent.get("finalized_result_id"):
        return "The manager already completed this review."
    if not _current(workspace, parent):
        return "Campaign guidance changed; request a new review with current evidence."
    refresh = workspace.store.get(parent["decision_refresh_id"], "decision_refresh")
    for item in refresh["decisions"]:
        current = workspace.store.get(item["decision"]["id"], "decision")
        if (current.get("status") != "pending" or current.get("refresh_id") != refresh["id"]
                or current.get("resolution_revision", 0) != item["decision"].get("resolution_revision", 0)):
            return "A selected decision changed; request a new review of the remaining decisions."
    runs = [parent]
    runs += [workspace.store.get(key, "research_run") for task in tasks for key in task["attempt_run_ids"]]
    if any((row.get("usage") or {}).get("pending_reservation") and row["status"] != "closed_uncertain" for row in runs):
        return "Reconcile the uncertain provider call before any further attempt."
    if any(row["status"] in {"running", "stopping"} for row in workspace.store.list("research_run", parent["campaign_id"])):
        return "Another campaign discussion is active."
    return ""


def progress(workspace, parent):
    tasks = _tasks(workspace, parent)
    issue = _retry_issue(workspace, parent, tasks)
    return {"phase": parent["decision_review"]["phase"], "total": len(tasks),
        "completed": sum(task["status"] == "completed" for task in tasks),
        "running": sum(task["status"] == "running" for task in tasks),
        "failed": sum(task["status"] in {"failed", "needs_reconciliation", "stopped"} for task in tasks),
        "max_parallel_reviews": parent["decision_review"]["max_parallel_reviews"],
        "parent_run_id": parent["id"], "can_retry": not issue, "retry_reason": issue,
        "tasks": [{key: task[key] for key in ("id", "role", "title", "status", "error") if key in task} for task in tasks]}


def retry(workspace, command, values):
    """An explicit retry adds attempts only where no completed response exists."""
    parent = workspace.store.get(values.retry_run_id, "research_run")
    if parent.get("campaign_id") != command.campaign_id or not parent.get("decision_review"):
        raise ValueError("Select a parallel decision review belonging to this campaign")
    tasks = _tasks(workspace, parent)
    issue = _retry_issue(workspace, parent, tasks)
    if issue:
        raise ValueError(issue)
    refresh = workspace.store.get(parent["decision_refresh_id"], "decision_refresh")
    selected = {item.decision_id: item.expected_resolution_revision for item in values.decisions}
    originals = {item["decision"]["id"]: item["decision"].get("resolution_revision", 0) for item in refresh["decisions"]}
    if selected != originals:
        raise ValueError("Retry must retain the complete original decision selection")
    if values.comment or any(item.comment or item.desired_choice for item in values.decisions):
        raise ValueError("Changed researcher direction needs a new review; retry reuses the frozen request")
    parent["decision_review"].update(phase="queued", max_parallel_reviews=values.max_parallel_reviews)
    _reserve_manager(workspace, parent)
    for task in tasks:
        if task["status"] != "completed":
            task.update(status="queued")
            task.pop("error", None)
            workspace.store.put("decision_review_task", task, "decision.review_retry_queued")
    parent.update(status="running", dispatch_phase="queued", control_revision=parent.get("control_revision", 0) + 1)
    parent.pop("error", None)
    parent.pop("finished_at", None)
    parent.pop("provider_response_id", None)
    if parent.get("usage", {}).get("pending_reservation"):
        parent.setdefault("closed_provider_reservations", []).append(parent["usage"].pop("pending_reservation"))
    workspace.store.put("research_run", parent, "decision.review_retried")
    effect = {"id": "research_resume_" + command.id, "campaign_id": command.campaign_id,
        "kind": "research_resume", "run_id": parent["id"], "control_revision": parent["control_revision"],
        "status": "pending", "created_at": now()}
    workspace.store.put("outbox", effect, "effect.queued")
    return {"refresh_id": refresh["id"], "manager_command_id": parent["manager_command_id"],
        "research_run_id": parent["id"], "decision_ids": list(originals), "effect_id": effect["id"], "retried": True}


def _publish_progress(workspace, parent_id, **changes):
    parent = workspace.store.get(parent_id, "research_run")
    parent["decision_review"].update(changes)
    workspace.store.put("research_run", parent, "decision.review_progress")
    return parent


def _check_dispatch(workspace, parent_id):
    from .coordinator import ResearchCancelled
    parent = workspace.store.get(parent_id, "research_run")
    if workspace.shutdown_event.is_set() or parent["status"] in {"stopping", "stopped"}:
        raise ResearchCancelled()
    if not _current(workspace, parent):
        raise ValueError("Campaign guidance changed during review; completed evidence is saved, but a new review is required")
    return parent


def _emit(coordinator, parent_id, run_id, event):
    workspace = coordinator.workspace
    with workspace.lock, workspace.store.transaction():
        if event["type"] in {"role_started", "provider_call_reserved", "provider_request"}:
            _check_dispatch(workspace, parent_id)
        if event["type"] == "provider_call_reserved" and event["usage"].get("billing_mode") != "subscription":
            from .engine import BudgetUnavailable
            current = workspace.store.get(run_id, "research_run")
            campaign = workspace.store.get(current["campaign_id"], "campaign")
            committed = sum(api_spend(other.get("usage")) + (other.get("decision_review") or {}).get("budget_hold_usd", 0)
                for other in workspace.store.list("research_run", current["campaign_id"]) if other["id"] != run_id)
            committed += workspace.implementations.api_committed(current["campaign_id"])
            if committed + api_spend(event["usage"]) > campaign["llm_budget_usd"] + 1e-9:
                raise BudgetUnavailable("This review call exceeds the remaining API allowance while preserving the final manager synthesis allocation. No request was sent.")
        coordinator._emit(run_id, event)
        if event["type"] in {"provider_response", "provider_error"}:
            receipt_id = "decision_review_response_" + event["reservation_id"]
            workspace.store.put_immutable("decision_review_response", {"id": receipt_id,
                "campaign_id": workspace.store.get(run_id, "research_run")["campaign_id"],
                "parent_run_id": parent_id, "research_run_id": run_id,
                "event": deepcopy(event), "created_at": now()})
            current = workspace.store.get(run_id, "research_run")
            current.update(usage=deepcopy(event["usage"]), provider_response_id=receipt_id)
            workspace.store.put("research_run", current)
        if run_id == parent_id and event["type"] == "provider_call_reserved":
            _publish_progress(workspace, parent_id, budget_hold_usd=0.0)


def _adapter(coordinator, parent, run_id, *, usage=None):
    from .engine import LLMAdapter
    request = parent["request"]
    return LLMAdapter(max_calls=(usage or {}).get("calls", 0) + 1,
        max_output_tokens=request.get("max_output_tokens", 2048), budget_usd=request.get("llm_budget_usd", 0),
        usage=usage, config={**deepcopy(request["provider_snapshot"]), "model_policy": deepcopy(request.get("model_policy"))},
        reservation_callback=lambda event: _emit(coordinator, parent["id"], run_id, event))


def _payload(parent, context, instruction):
    return {"researcher_request": {"message": parent["request"]["message"], "mode": "compare"},
        "context": context, "instructions": instruction, "remaining_role_calls": 1,
        "previous_role_results": [], "new_hypotheses": [], "available_roles": []}


def _receipt_answer(workspace, run, result_type):
    receipt = workspace.store.get(run["provider_response_id"], "decision_review_response")["event"]
    if receipt["type"] != "provider_response" or receipt.get("status") == "incomplete":
        raise ValueError("The saved provider response is not a completed assessment")
    value = receipt.get("output")
    if isinstance(value, list):
        value = "".join(part.get("text", part.get("content", "")) or "" for part in value)
    if isinstance(value, str) and value.startswith("```"):
        value = value.split("\n", 1)[1].rsplit("```", 1)[0]
    return result_type.model_validate_json(value)


def _review_result(answer, usage, role):
    return {"status": "completed", "mode": "llm", "assessment": answer.model_dump(),
        "usage": deepcopy(usage), "trace": [{"role": role, "status": "completed", "content": answer.analysis}],
        "messages": [], "actions": [], "decisions": [], "hypotheses": []}


def _review(coordinator, parent_id, task_id):
    workspace, store = coordinator.workspace, coordinator.store
    adapter = None
    child = None
    try:
        with workspace.lock, store.transaction():
            parent = _check_dispatch(workspace, parent_id)
            task = store.get(task_id, "decision_review_task")
            if task["status"] == "completed":
                return
            child = {"id": identifier("research"), "campaign_id": parent["campaign_id"],
                "parent_review_run_id": parent_id, "review_task_id": task_id,
                "charter_version": parent["charter_version"], "guidance_revision": parent["guidance_revision"],
                "request": {**deepcopy(parent["request"]), "max_calls": 1},
                "context_snapshot": deepcopy(task["context_snapshot"]), "created_at": now(),
                "status": "running", "dispatch_phase": "dispatched", "usage": {}, "trace": [],
                "control_revision": 0, "cost_work_revision": 1, "automatic": False}
            task.update(status="running", current_run_id=child["id"])
            task["attempt_run_ids"].append(child["id"])
            store.put_many([("research_run", child, "decision.review_attempt_started"),
                ("decision_review_task", task, "decision.review_started")])
            workspace.research_threads[child["id"]] = threading.current_thread()
        adapter = _adapter(coordinator, parent, child["id"])
        role = task["role"]
        _emit(coordinator, parent_id, child["id"], {"type": "role_started", "role": role})
        answer = adapter.call(role, _payload(parent, child["context_snapshot"],
            "Assess only the selected decision group. Identify obsolete or duplicate requests, evidence gaps, dependencies, "
            "and concrete alternatives. Keep questions internal. Do not request other roles. Your assessment goes only "
            "to the campaign manager; do not publish user decisions or execute work. "
            "Keep the entire JSON response within 4500 UTF-8 bytes; analysis at most 2000 characters and each "
            "dependency or dissent note at most 200 characters."), result_type=ReviewerAssessment)
        _emit(coordinator, parent_id, child["id"], {"type": "role_completed", "role": role, "result": answer.model_dump()})
        result = _review_result(answer, adapter.usage, role)
        from .lifecycle import save_result
        saved = save_result(workspace, child["id"], result)
        with workspace.lock, store.transaction():
            child = store.get(child["id"], "research_run")
            child.update(status="completed", dispatch_phase="finalized", finalized_result_id=saved["id"],
                result=result, finished_at=now(), trace=result["trace"])
            task = store.get(task_id, "decision_review_task")
            task.update(status="completed", result_id=saved["id"], finished_at=now())
            task.pop("error", None)
            store.put_many([("research_run", child, "decision.review_completed"),
                ("decision_review_task", task, "decision.review_completed")])
    except Exception as exc:
        from .coordinator import ResearchCancelled
        from .engine import BudgetUnavailable
        from .lifecycle import reconciliation
        with workspace.lock, store.transaction():
            task = store.get(task_id, "decision_review_task")
            message = str(exc) if isinstance(exc, (ValueError, BudgetUnavailable)) else (
                "Review stopped before another provider call." if isinstance(exc, ResearchCancelled) else
                f"Reviewer failed ({type(exc).__name__}); inspect the saved agent log. No automatic retry was made.")
            status = "stopped" if isinstance(exc, ResearchCancelled) else "failed"
            if child:
                child = store.get(child["id"], "research_run")
                child["usage"] = deepcopy(adapter.usage) if adapter else child.get("usage", {})
                if child["usage"].get("pending_reservation"):
                    reconciliation(workspace, child)
                    status = "needs_reconciliation"
                    message = child["error"]
                else:
                    child.update(status=status, error=message, finished_at=now())
                    store.put("research_run", child, "decision.review_failed")
            task.update(status=status, error=message, finished_at=now())
            store.put("decision_review_task", task, "decision.review_failed")
            if child:
                coordinator._emit(child["id"], {"type": "role_failed", "role": task["role"],
                    "error_type": type(exc).__name__, "summary": message})
    finally:
        if child:
            with workspace.lock:
                if workspace.research_threads.get(child["id"]) is threading.current_thread():
                    workspace.research_threads.pop(child["id"], None)
            _capture_costs(coordinator, child["id"])


def _capture_costs(coordinator, run_id):
    workspace = coordinator.workspace
    run = workspace.store.get(run_id, "research_run")
    try:
        coordinator.capture_costs(run)
    except Exception as exc:
        workspace.memory.issue(run["campaign_id"], "model_cost_capture", str(exc), affected=run_id)


def _synthesize(coordinator, parent_id):
    workspace, store = coordinator.workspace, coordinator.store
    from .engine import RoleResult, _manager_decisions, _safe_context
    from .lifecycle import save_result, finalize
    from .decision_review_context import synthesis_context
    with workspace.lock, store.transaction():
        parent = _check_dispatch(workspace, parent_id)
        if parent.get("result_id"):
            saved = store.get(parent["result_id"], "research_result")
            finalize(coordinator, parent_id, saved)
            _publish_progress(workspace, parent_id, phase="completed", budget_hold_usd=0.0)
            return
        tasks = _tasks(workspace, parent)
        if any(task["status"] != "completed" for task in tasks):
            raise ValueError("Manager synthesis requires every selected reviewer group to finish")
        assessments = [{"task_id": task["id"], "title": task["title"], "role": task["role"],
            "decision_ids": task["decision_ids"],
            "result": store.get(task["result_id"], "research_result")["result"]["assessment"]} for task in tasks]
        context = synthesis_context(_safe_context(parent["context_snapshot"]), assessments)
        _publish_progress(workspace, parent_id, phase="synthesizing")
    adapter = _adapter(coordinator, parent, parent_id, usage=parent.get("usage"))
    try:
        if parent.get("provider_response_id"):
            answer = _receipt_answer(workspace, parent, RoleResult)
            from .model_policy import resolve_policy
            adapter.config = resolve_policy(parent["request"]["provider_snapshot"], parent["request"].get("model_policy"), "research_synthesizer")
        else:
            _emit(coordinator, parent_id, parent_id, {"type": "role_started", "role": "research_synthesizer"})
            answer = adapter.call("research_synthesizer", _payload(parent, context,
                "You are the final campaign manager. Reconcile the independent reviewer assessments, disagreements, "
                "dependencies, duplicate requests and combined resource requirements against the frozen campaign evidence. "
                "Publish one consolidated response and only necessary structured decision requests or concrete actions. "
                "Every proposed action requires researcher approval. Do not request more roles or execute work. "
                "Assessments are opinions, never empirical evidence; preserve dissent and explain your judgment."))
        _emit(coordinator, parent_id, parent_id, {"type": "role_completed", "role": "research_synthesizer", "result": answer.model_dump()})
        if not answer.analysis.strip():
            raise ValueError("The campaign manager must provide a written consolidation of the reviews")
        task_ids = {task.get("id") for task in parent["context_snapshot"].get("tasks", [])}
        actions, seen = [], set()
        from optimization_framework.contracts.base import content_hash
        for proposal in answer.actions:
            action = proposal.model_dump()
            if action.get("task_id") is not None and action["task_id"] not in task_ids:
                continue
            signature = content_hash(action)
            if signature in seen:
                continue
            seen.add(signature)
            actions.append({**action, "id": identifier("action"), "status": "proposed", "requires_researcher": True})
        decisions = _manager_decisions(answer, {"decisions": [], "actions": actions})
        trace = [{"role": "research_synthesizer", "status": "completed", "content": answer.analysis,
            "model": adapter.config["model"], "reasoning_effort": adapter.config.get("reasoning_effort")}]
        result = {"status": "awaiting_researcher" if decisions or actions else "completed", "mode": "llm",
            "messages": [{"role": "assistant", "content": answer.analysis}], "hypotheses": [],
            "actions": actions, "decisions": decisions, "memory_updates": [note.model_dump() for note in answer.memory_updates],
            "trace": trace, "usage": deepcopy(adapter.usage), "provider": deepcopy(parent["request"]["provider_snapshot"]),
            "research_state": {"role_results": [{"role": "research_synthesizer", **answer.model_dump()}]},
            "review_task_ids": [task["id"] for task in tasks]}
        save_result(workspace, parent_id, result)
        with workspace.lock, store.transaction():
            _check_dispatch(workspace, parent_id)
            finalize(coordinator, parent_id, store.get("research_result_" + parent_id, "research_result"))
            _publish_progress(workspace, parent_id, phase="completed", budget_hold_usd=0.0)
    finally:
        with workspace.lock, store.transaction():
            current = store.get(parent_id, "research_run")
            current["usage"] = deepcopy(adapter.usage)
            store.put("research_run", current)


def run(coordinator, parent_id):
    """Supervise durable tasks; worker threads never share an adapter or usage."""
    workspace, store = coordinator.workspace, coordinator.store
    try:
        with workspace.lock, store.transaction():
            parent = _check_dispatch(workspace, parent_id)
            _publish_progress(workspace, parent_id, phase="reviewing")
            queued = [task["id"] for task in _tasks(workspace, parent) if task["status"] == "queued"]
            limit = parent["decision_review"]["max_parallel_reviews"]
        with ThreadPoolExecutor(max_workers=limit, thread_name_prefix="decision-review") as pool:
            futures = set()
            while queued or futures:
                with workspace.lock:
                    active = store.get(parent_id, "research_run")
                    may_dispatch = (active["status"] == "running" and not workspace.shutdown_event.is_set()
                        and _current(workspace, active))
                if may_dispatch:
                    while queued and len(futures) < limit:
                        futures.add(pool.submit(_review, coordinator, parent_id, queued.pop(0)))
                elif queued:
                    with workspace.lock, store.transaction():
                        for task_id in queued:
                            task = store.get(task_id, "decision_review_task")
                            task.update(status="stopped", error="Parent review stopped or guidance changed before dispatch")
                            store.put("decision_review_task", task, "decision.review_stopped")
                    queued = []
                if futures:
                    done, futures = wait(futures, timeout=.2, return_when=FIRST_COMPLETED)
                    for future in done:
                        future.result()
        with workspace.lock, store.transaction():
            parent = _check_dispatch(workspace, parent_id)
            tasks = _tasks(workspace, parent)
            if any(task["status"] != "completed" for task in tasks):
                parent.update(status="partial", error="Some reviewer groups need attention. Completed reviews are saved; retry only unfinished groups.", finished_at=now())
                parent["decision_review"].update(phase="partial", budget_hold_usd=0.0)
                store.put("research_run", parent, "decision.review_partial")
                return
        _synthesize(coordinator, parent_id)
        workspace.dispatch_outbox()
    except Exception as exc:
        from .coordinator import ResearchCancelled
        from .engine import BudgetUnavailable
        from .lifecycle import reconciliation
        with workspace.lock, store.transaction():
            parent = store.get(parent_id, "research_run")
            if (parent.get("usage") or {}).get("pending_reservation"):
                reconciliation(workspace, parent)
            else:
                parent.update(status="stopped" if isinstance(exc, ResearchCancelled) else "partial",
                    error=("Review stopped; completed reviewer responses are saved." if isinstance(exc, ResearchCancelled)
                        else str(exc) if isinstance(exc, (ValueError, BudgetUnavailable)) else
                        f"Decision review failed ({type(exc).__name__}); saved reviewer evidence is retained."), finished_at=now())
            parent["decision_review"].update(phase="stopped" if isinstance(exc, ResearchCancelled) else "partial", budget_hold_usd=0.0)
            store.put("research_run", parent, "decision.review_interrupted")
            workspace.memory.issue(parent["campaign_id"], "manager_run", parent["error"], affected=parent_id)
    finally:
        _capture_costs(coordinator, parent_id)


def recover(workspace):
    """Restore proven unsent tasks and durable results; never replay uncertain calls."""
    from .lifecycle import reconciliation, save_result
    from .engine import RoleResult
    for parent in workspace.store.list("research_run"):
        if not parent.get("decision_review") or parent.get("finalized_result_id"):
            continue
        if parent["status"] not in {"running", "stopping"}:
            continue
        with workspace.lock, workspace.store.transaction():
            stopped = parent["status"] == "stopping"
            for task in _tasks(workspace, parent):
                if task["status"] != "running":
                    continue
                child = workspace.store.get(task["current_run_id"], "research_run")
                if child.get("provider_response_id") and not child.get("result_id"):
                    try:
                        answer = _receipt_answer(workspace, child, ReviewerAssessment)
                        saved = save_result(workspace, child["id"], _review_result(answer, child.get("usage", {}), task["role"]))
                        child = workspace.store.get(child["id"], "research_run")
                    except (ValueError, TypeError, KeyError):
                        pass  # The saved rejection is charged; only an explicit retry may call again.
                if child.get("result_id"):
                    saved = workspace.store.get(child["result_id"], "research_result")
                    child.update(status="completed", dispatch_phase="finalized", finalized_result_id=saved["id"], result=saved["result"])
                    task.update(status="completed", result_id=saved["id"])
                elif (child.get("usage") or {}).get("pending_reservation"):
                    reconciliation(workspace, child)
                    task.update(status="needs_reconciliation", error=child["error"])
                elif not child.get("usage", {}).get("calls"):
                    child.update(status="stopped", error="Service restarted before this attempt sent a call")
                    task.update(status="stopped" if stopped else "queued")
                else:
                    child.update(status="interrupted", error="Completed-call receipt exists without a saved assessment; explicit retry required")
                    task.update(status="failed", error=child["error"])
                workspace.store.put_many([("research_run", child, "decision.review_recovered"),
                    ("decision_review_task", task, "decision.review_recovered")])
            if (parent.get("usage") or {}).get("pending_reservation"):
                reconciliation(workspace, parent)
                parent["decision_review"].update(phase="partial", budget_hold_usd=0.0)
            elif stopped:
                parent.update(status="stopped", finished_at=now())
                parent["decision_review"].update(phase="stopped", budget_hold_usd=0.0)
            elif any(task["status"] in {"failed", "needs_reconciliation", "stopped"} for task in _tasks(workspace, parent)):
                parent.update(status="partial", error="Service restarted; unfinished review groups require an explicit retry")
                parent["decision_review"].update(phase="partial", budget_hold_usd=0.0)
            elif parent.get("usage", {}).get("calls") and not parent.get("result_id"):
                try:
                    _receipt_answer(workspace, parent, RoleResult)
                except (ValueError, TypeError, KeyError):
                    parent.update(status="partial", error="Manager call receipt exists without a valid saved result; explicit retry required")
                    parent["decision_review"].update(phase="partial", budget_hold_usd=0.0)
                else:
                    parent.update(status="running", dispatch_phase="recovery_pending")
            else:
                parent.update(status="running", dispatch_phase="recovery_pending")
            workspace.store.put("research_run", parent, "decision.review_recovered")
