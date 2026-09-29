"""Finish bounded work with a durable handoff, without increasing any allocation."""
from copy import deepcopy

from optimization_framework.contracts.base import content_hash
from optimization_framework.storage.sqlite import now


class ToolAllowanceReached(ValueError):
    """Control flow for a deferred batch, never invalid model output."""


def snapshot(controller, session, task, run=None):
    policy = session["policy"]
    runs = controller._usage(session)
    calls = sum(row.get("usage", {}).get("calls", 0) for row in runs)
    run = run or next((row for row in runs if row.get("discovery_task_id") == task["id"]), {})
    task_calls = run.get("usage", {}).get("calls", 0)
    requests = [row for row in controller.store.list("discovery_tool", session["campaign_id"])
                if row["session_id"] == session["id"]]
    tool_count = sum(row["task_id"] == task["id"] for row in requests)
    source_count = sum(bool(row.get("dispatched_at")) for row in requests if row["call"]["tool"].startswith("source."))
    remaining = max(0, policy["model_call_limit"] - calls)
    reserve = policy["synthesis_call_reserve"]
    task_remaining = max(0, policy["max_calls_per_task"] - task_calls)
    tools_remaining = max(0, policy["max_tools_per_task"] - tool_count)
    manager = task["brief"]["role"] == "campaign_manager"
    dispatch_remaining = remaining if manager else max(0, remaining - reserve)
    reason = task.get("wrap_up_reason")
    if not reason and task_remaining <= 1:
        reason = "The final task call is reserved for saving findings and unresolved work."
    if not reason and tools_remaining == 0:
        reason = "The task tool allowance is exhausted."
    if not reason and remaining <= reserve:
        reason = "Only the session synthesis call reserve remains."
    return {"calls_used_or_reserved": calls, "calls_remaining": remaining,
        "generation_round": session["round"], "task_calls_used": task_calls,
        "task_calls_remaining": task_remaining, "task_tools_used": tool_count,
        "task_tools_remaining": tools_remaining, "source_requests_used": source_count,
        "source_requests_remaining": max(0, policy["source_request_limit"] - source_count),
        "dispatch_calls_remaining": dispatch_remaining, "synthesis_call_reserve": reserve,
        "phase": "wrap_up" if reason else "approaching_limit" if task_remaining <= 2 or tools_remaining <= 6 else "research",
        "reason": reason,
        "instruction": "These are live allowances before this call; concurrent reservations may reduce them. "
            "Reserve the last task call for a final work product or disposition=handoff with findings, exact saved IDs, "
            "gaps and the smallest useful follow-up. In wrap_up do not request tools or new assignments. "
            "Never present unfinished work as complete. If source requests are exhausted, use saved evidence. "
            "The manager can continue a narrower assignment only within the remaining session allocation."}


def handoff(controller, session, task, run, reason, *, step=None):
    """Persist a factual checkpoint even when no model call remains to write one.

    Called under the workspace lock/transaction. Accepted scientific artifacts,
    provider usage and immutable responses are never rewritten.
    """
    if task.get("handoff_id"):
        return controller.store.get(task["handoff_id"], "discovery_handoff")
    steps = [row for row in controller.store.list("discovery_step", task["campaign_id"]) if row["task_id"] == task["id"]]
    latest = step or max(steps, key=lambda row: int(row["id"].rsplit("_", 1)[-1]), default={})
    result = latest.get("result") or task.get("result") or {}
    requests = [row for row in controller.store.list("discovery_tool", task["campaign_id"]) if row["task_id"] == task["id"]]
    deferred = []
    for row in steps:
        if row["id"] not in {latest.get("id"), task.get("deferred_step_id")}:
            continue
        missing = [call for call in row["result"].get("tools", []) if not any(
            request["step_id"] == row["id"] and request["call"]["key"] == call["key"] for request in requests)]
        if missing:
            deferred.append({"step_id": row["id"], "tools": missing})
    record = controller.store.put_immutable("discovery_handoff", {
        "id": "handoff_" + task["id"], "campaign_id": task["campaign_id"], "session_id": session["id"],
        "task_id": task["id"], "created_at": now(), "reason": reason,
        "objective": task["brief"]["objective"], "summary": result.get("summary", "No model work product was returned for this assignment."),
        "scientific_complete": False, "artifact_ids": task.get("artifact_ids", []),
        "last_step_id": latest.get("id"), "receipt_ids": [row["receipt_id"] for row in requests if row.get("receipt_id")],
        "unexecuted_tools": [call for call in result.get("tools", [])
            if not any(row["step_id"] == latest.get("id") and row["call"]["key"] == call["key"] for row in requests)],
        "deferred_tool_batches": deferred,
        "deferred_tasks": result.get("proposed_tasks", []), "questions_for_manager": result.get("questions_for_manager", []),
        "dissent": result.get("dissent", []), "usage": deepcopy((run or {}).get("usage", {})),
        "allocation": snapshot(controller, session, task, run),
        "next_action": "Review the saved findings and receipts. If useful work remains, assign a narrower continuation "
            "with explicit saved evidence within the remaining session allocation; do not restart completed research."},
        "discovery.handoff_saved")
    task.update(status="handed_off", wait_reason=None, finished_at=record["created_at"],
        handoff_id=record["id"], handoff_reason=reason)
    if task.get("error"):
        task["prior_error"] = task.pop("error")
    task.pop("error_code", None)
    if run:
        run.update(status="completed", outcome="handed_off", finished_at=record["created_at"])
        if run.get("error"):
            run["prior_error"] = run.pop("error")
        controller.store.put("research_run", run, "research.finished")
    controller.store.put("discovery_task", task, "discovery.task_handed_off")
    controller.workspace.agent_log.record(task["campaign_id"], "message.handoff", agent_id=task["id"],
        role=task["brief"]["role"], task_id=task["id"], discovery_session_id=session["id"],
        from_agent=task["id"], to_agent="campaign_manager", event_key=record["id"],
        summary=reason + " " + record["summary"], payload=record, result_id=record["id"])
    return record


def finish_session(controller, session, reason):
    """Produce an explicit partial wrap-up when no authorized synthesis fits."""
    tasks = controller.tasks(session)
    if any(row["status"] == "running" or row.get("wait_reason") in {
            "tools", "uncertain_provider", "provider_receipt_reconciliation", "result_projection"} for row in tasks):
        return
    from .controller import TASK_TERMINAL
    for task in tasks:
        if task["status"] not in TASK_TERMINAL:
            run = controller.store.get(task["run_id"], "research_run") if task.get("run_id") else None
            handoff(controller, session, task, run, reason)
    tasks = controller.tasks(session)
    identity = "wrap_up_" + content_hash([session["id"], session["control_revision"], [row["id"] for row in tasks]])[:32]
    if session.get("wrap_up_id") == identity and session["status"] == "waiting_for_direction":
        return
    try:
        record = controller.store.get(identity, "discovery_wrap_up")
    except KeyError:
        completed = sum(row["status"] == "completed" for row in tasks)
        unfinished = [row["id"] for row in tasks if row["status"] not in {"completed", "cancelled", "superseded"}]
        summary = (f"Discovery reached its allocation and saved a partial wrap-up: {completed} assignments completed; "
            f"{len(unfinished)} assignments have unresolved work. Findings, tool receipts and continuation notes are preserved. "
            "This is not a claim that the research objective or numerical validation is complete. "
            "Review the saved work with the campaign manager; additional research needs an explicit allocation change.")
        record = controller.store.put_immutable("discovery_wrap_up", {"id": identity,
            "campaign_id": session["campaign_id"], "session_id": session["id"], "created_at": now(),
            "reason": reason, "summary": summary, "scientific_complete": False,
            "unfinished_task_ids": unfinished, "handoff_ids": [row["handoff_id"] for row in tasks if row.get("handoff_id")],
            "artifact_ids": [key for row in tasks for key in row.get("artifact_ids", [])]}, "discovery.wrap_up_saved")
        controller.store.put("message", {"id": "message_" + identity, "campaign_id": session["campaign_id"],
            "role": "assistant", "origin": "system", "content": summary, "created_at": now()}, "message.created")
    session.update(status="waiting_for_direction", wrap_up_id=record["id"], updated_at=now())
    controller.store.put("discovery_session", session, "discovery.allocation_wrapped_up")


def is_allocation_error(error):
    return any(text in str(error) for text in (
        "tool allowance is exhausted", "Task call allocation exhausted", "LLM call allowance is exhausted",
        "session call allocation is exhausted", "session API allocation is exhausted", "remaining LLM spending cap"))


def recover(controller):
    """Migrate only recognized old quota failures, with no dispatch or new spend."""
    from .controller import TERMINAL
    for task in controller.store.list("discovery_task"):
        if task["status"] != "failed" or not is_allocation_error(task.get("error", "")):
            continue
        with controller.workspace.lock, controller.store.transaction():
            session = controller.store.get(task["session_id"], "discovery_session")
            campaign = controller.store.get(task["campaign_id"], "campaign")
            if (session["status"] in TERMINAL or task["guidance_revision"] != session["guidance_revision"] or
                    session["guidance_revision"] != controller.workspace.memory.state(task["campaign_id"])["guidance_revision"] or
                    session["charter_version"] != campaign["version"]):
                continue
            run = controller.store.get(task["run_id"], "research_run") if task.get("run_id") else None
            if run and run.get("usage", {}).get("pending_reservation"):
                continue
            handoff(controller, session, task, run, task["error"])
            # A new checkpoint is new operational evidence even if a previous
            # manager already saw the old failure. Wake it once, without
            # resending the failed task or changing researcher guidance.
            session["agenda_seen_task_ids"] = [key for key in session.get("agenda_seen_task_ids", []) if key != task["id"]]
            controller.store.put("discovery_session", session, "discovery.handoff_review_requested")
            controller._reconcile_task_issues(session)
