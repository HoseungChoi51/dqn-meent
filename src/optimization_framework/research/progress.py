"""Read-only, bounded progress for the latest researcher request.

Agent liveness is reported separately from scientific progress. The projection
never dispatches work, resumes a session, or loads model prompts and responses.
"""
from __future__ import annotations

import json


TASK_STATUSES = ("queued", "running", "waiting", "completed", "failed", "blocked", "cancelled", "superseded")
TERMINAL = {"completed", "failed", "blocked", "cancelled", "superseded", "stopped", "interrupted"}
SESSION_TERMINAL = {"completed", "stopped", "exhausted"}
PROVIDER_EVENTS = ("provider.request", "provider.response", "provider.error", "provider.reserved",
                   "provider.cancelled_before_send", "runtime.liveness")


def _records(store, kind, campaign_id, fields):
    # JSON extraction keeps multi-megabyte context_snapshot/output fields out of
    # Python and the response, including when this endpoint is polled repeatedly.
    columns = ",".join(f"'{field}',json_extract(data,'$.{field}')" for field in fields)
    with store.connection() as db:
        rows = db.execute(f"SELECT json_object({columns}) FROM records WHERE kind=? AND campaign_id=? ORDER BY rowid",
                          (kind, campaign_id)).fetchall()
    return [json.loads(row[0]) for row in rows]


def _latest_provider_events(store, campaign_id, task_ids):
    if not task_ids:
        return {}
    placeholders = ",".join("?" for _ in task_ids)
    event_placeholders = ",".join("?" for _ in PROVIDER_EVENTS)
    with store.connection() as db:
        rows = db.execute(f"""
            SELECT task_id,attempt_id,event_type,occurred_at,summary,error_code,error_message,timeout_seconds FROM (
                SELECT json_extract(body,'$.task_id') AS task_id,
                       json_extract(body,'$.attempt_id') AS attempt_id,
                       json_extract(body,'$.event_type') AS event_type,
                       json_extract(body,'$.occurred_at') AS occurred_at,
                       json_extract(body,'$.summary') AS summary,
                       json_extract(body,'$.payload.error') AS error_code,
                       json_extract(body,'$.payload.error_message') AS error_message,
                       json_extract(body,'$.payload.timeout_seconds') AS timeout_seconds,
                       ROW_NUMBER() OVER (PARTITION BY json_extract(body,'$.task_id') ORDER BY seq DESC) AS rank
                FROM agent_events WHERE campaign_id=?
                    AND json_extract(body,'$.task_id') IN ({placeholders})
                    AND json_extract(body,'$.event_type') IN ({event_placeholders})
            ) WHERE rank=1
        """, (campaign_id, *task_ids, *PROVIDER_EVENTS)).fetchall()
    return {row["task_id"]: dict(row) for row in rows}


def _scope(tasks, request):
    if not request:
        return tasks
    request_id = request["id"]
    included = {task["id"] for task in tasks if task.get("manager_command_id") == request_id
                or task.get("proposal_request_id") == request_id
                or task["id"] == request.get("discovery_task_id")}
    # Automatic reviews and agenda reconciliation predate explicit request
    # lineage. Their dependency edges still establish a real forward handoff.
    while True:
        added = {task["id"] for task in tasks if task["id"] not in included
                 and (not (owner := task.get("proposal_request_id") or task.get("manager_command_id")) or owner == request_id)
                 and (task.get("parent_task_id") in included or included.intersection(task.get("dependencies") or []))}
        if not added:
            break
        included.update(added)
    return [task for task in tasks if task["id"] in included]


def _worker_alive(workspace, task):
    thread = workspace.discovery.threads.get(task["id"]) if not task.get("legacy") else None
    thread = thread or workspace.research_threads.get(task.get("run_id"))
    return bool(thread and thread.is_alive())


def _agent(workspace, campaign_id, session, task, run, attempt, event):
    brief = task.get("brief") or {}
    role = brief.get("role") or (run or {}).get("current_role") or "campaign_manager"
    pending_attempt = not task.get("legacy") and task["status"] in {"queued", "waiting"} and not attempt
    if pending_attempt:
        event = None
    if event and attempt:
        if event.get("attempt_id") and event["attempt_id"] != attempt["id"]:
            event = None
        elif not event.get("attempt_id") and (event.get("occurred_at") or "") < (attempt.get("created_at") or ""):
            event = None
    frozen = (attempt or {}).get("provider_snapshot")
    if not frozen and task.get("legacy"):
        frozen = ((run or {}).get("request") or {}).get("provider_snapshot")
    if frozen and task.get("legacy"):
        from optimization_framework.research.model_policy import resolve_policy
        frozen = resolve_policy(frozen, ((run or {}).get("request") or {}).get("model_policy"), role)
    config = frozen or workspace.models.config(campaign_id, role,
        legacy_roles=((session or {}).get("policy") or {}).get("role_models", {}))
    status = task["status"]
    error_code = task.get("error_code") or ((event or {}).get("error_code") if (event or {}).get("event_type") == "provider.error" else None)
    wait_reason = task.get("wait_reason")
    last_activity = max((value for value in (
        task.get("created_at"), task.get("updated_at"), task.get("finished_at"),
        (run or {}).get("created_at"), (run or {}).get("finished_at"),
        (attempt or {}).get("created_at"), (event or {}).get("occurred_at")) if value), default=None)
    alive = _worker_alive(workspace, task) if status == "running" else False
    provider_active = alive and (event or {}).get("event_type") in {"provider.request", "runtime.liveness"}
    if status == "running":
        if not alive:
            activity = "Worker is not active; waiting for recovery."
            wait_reason = "worker_recovery"
        elif provider_active:
            activity = "Waiting for model output. Runtime activity is not a scientific result."
        elif (event or {}).get("event_type") == "provider.response":
            activity = "Model response received; validating and saving the work product."
        else:
            activity = "Preparing the next model call."
    elif status == "queued":
        if (session or {}).get("status") == "paused":
            activity, wait_reason = "Queued; discovery is paused. No model call is running for this task.", "paused"
        elif (session or {}).get("status") == "waiting_for_provider":
            activity, wait_reason = "Queued; waiting for an enabled model provider.", "provider"
        else:
            activity = "Queued for the scheduler; dependencies and available workers are checked before dispatch."
    elif status == "waiting":
        labels = {"tools": "Waiting for tool results before the next model call.",
                  "manager_review": "Waiting for campaign manager review.",
                  "uncertain_provider": "Waiting for reconciliation of an uncertain model call.",
                  "provider_receipt_reconciliation": "Waiting for reconciliation of a saved provider receipt.",
                  "result_projection": "Waiting for a saved result to be applied.",
                  "context_scope": "The task context needs manager attention.",
                  "problem_scope": "The problem definition needs manager attention."}
        activity = labels.get(wait_reason, "Waiting for the next campaign action.")
        if wait_reason in {"context_scope", "problem_scope"} and task.get("error"):
            activity = task["error"][:600]
    elif status in {"failed", "blocked"}:
        activity = (task.get("error") or "The campaign manager must review this task before continuing.")[:600]
        if error_code == "timeout":
            duration = (event or {}).get("timeout_seconds")
            activity = ((event or {}).get("error_message") or
                (f"The model call timed out after {duration:g} seconds." if isinstance(duration, (float, int)) else
                 "The model call timed out before returning a complete response."))[:600]
            activity += " Completed work and the failed-call record are retained."
    else:
        activity = {"completed": "Work product saved.", "cancelled": "Task cancelled.",
                    "superseded": "Task superseded by newer guidance."}.get(status, "Task is no longer active.")
    return {"task_id": task["id"], "role": role, "stage": brief.get("stage") or "manage", "status": status,
            "model": config.get("model"), "reasoning_effort": config.get("reasoning_effort"),
            "started_at": None if pending_attempt else (attempt or {}).get("created_at") or (run or {}).get("created_at"),
            "last_activity_at": last_activity, "activity": activity, "wait_reason": wait_reason,
            "error_code": error_code,
            "active": provider_active, "worker_active": alive}


def view(workspace, campaign_id):
    """Project durable state and local worker liveness without changing either."""
    store = workspace.store
    store.get(campaign_id, "campaign")
    requests = _records(store, "manager_command", campaign_id,
        ("id", "request", "created_at", "status", "discovery_task_id", "research_run_id", "wait_reason", "error", "automatic"))
    requests = [row for row in requests if not row.get("automatic")]
    request = max(requests, key=lambda row: row.get("created_at") or "", default=None)
    sessions = _records(store, "discovery_session", campaign_id,
        ("id", "status", "control_revision", "created_at", "updated_at", "policy"))
    session = next((row for row in reversed(sessions) if row["status"] not in SESSION_TERMINAL), sessions[-1] if sessions else None)
    tasks = _records(store, "discovery_task", campaign_id,
        ("id", "session_id", "brief", "status", "created_at", "updated_at", "finished_at", "run_id", "attempt_id",
         "manager_command_id", "proposal_request_id", "parent_task_id", "dependencies", "wait_reason", "error", "error_code"))
    tasks = [task for task in tasks if session and task["session_id"] == session["id"]]
    if (request and session and (request.get("created_at") or "") < (session.get("created_at") or "")
            and not any(task.get("manager_command_id") == request["id"]
                        or task.get("proposal_request_id") == request["id"]
                        or task["id"] == request.get("discovery_task_id") for task in tasks)):
        # A new session has its own initial analysis agenda. An older request
        # from a previous session must not hide those agents behind an empty scope.
        request = None
    tasks = _scope(tasks, request)
    runs = _records(store, "research_run", campaign_id,
        ("id", "status", "request", "created_at", "finished_at", "current_role", "manager_command_id", "error", "usage.pending_reservation"))
    if not tasks and request and request.get("research_run_id"):
        legacy = next((run for run in runs if run["id"] == request["research_run_id"]), None)
        if legacy and not request.get("discovery_task_id"):
            uncertain = legacy["status"] == "needs_reconciliation"
            tasks = [{"id": legacy["id"], "run_id": legacy["id"], "legacy": True,
                      "status": "waiting" if uncertain else legacy["status"],
                      "wait_reason": "uncertain_provider" if uncertain else None,
                      "brief": {"role": legacy.get("current_role") or "campaign_manager", "stage": "manage"},
                      "created_at": legacy.get("created_at"), "finished_at": legacy.get("finished_at"), "error": legacy.get("error")}]
    runs = {run["id"]: run for run in runs}
    attempts = {row["id"]: row for row in _records(store, "discovery_attempt", campaign_id,
        ("id", "created_at", "provider_snapshot"))}
    events = _latest_provider_events(store, campaign_id, [task["id"] for task in tasks])
    agents = [_agent(workspace, campaign_id, session, task, runs.get(task.get("run_id")),
                     attempts.get(task.get("attempt_id")), events.get(task["id"])) for task in tasks]
    counts = {key: sum(task["status"] == key for task in tasks) for key in TASK_STATUSES}
    counts["total"] = len(tasks)
    unfinished = [task for task in tasks if task["status"] not in TERMINAL]
    alive = any(agent["worker_active"] for agent in agents)
    active = any(agent["active"] for agent in agents)
    pending_request = bool(request and request["status"] not in TERMINAL)
    status, headline, message = "idle", "No research request is active", "Develop strategies or send a request to the campaign manager."
    session_status = (session or {}).get("status")
    provider_wait = session_status == "waiting_for_provider" or (pending_request and request["status"] == "waiting_provider")
    if alive:
        status, headline = "running", "Agents are working"
        message = "Waiting for model output; completed work appears in the conversation and agent log." if active else "Agents are preparing a call or saving a returned response."
        if session_status == "paused":
            message += " Discovery is paused; an already dispatched call is finishing."
    elif session_status == "paused":
        status, headline = "paused", "Request saved — discovery is paused" if unfinished or pending_request else "Discovery is paused"
        message = "No agents are running for this request. Resume discovery to let the campaign manager process the saved request." if unfinished or pending_request else "No new model calls are dispatched while discovery is paused. Resume discovery to continue the campaign."
    elif session_status == "exhausted":
        status, headline = "blocked", "Discovery allocation is exhausted"
        message = "Saved work is available for review. The campaign needs a revised allocation before discovery can continue."
    elif session_status == "stopped":
        status, headline = "waiting", "Discovery has stopped"
        message = "No new model calls are dispatched. Review the saved evidence or start another discovery session to continue."
    elif provider_wait and (unfinished or pending_request):
        status, headline = "blocked", "Waiting for a model provider"
        message = "The request is saved. Configure and enable the model provider before agents can run."
    elif any(agent["wait_reason"] in {"worker_recovery", "uncertain_provider", "provider_receipt_reconciliation", "context_scope", "problem_scope", "result_projection"} for agent in agents):
        status, headline = "blocked", "Research needs attention"
        message = "A task is waiting for recovery or manager attention. Its saved activity and evidence are available in the agent log."
    elif counts["queued"] or (pending_request and not tasks):
        status, headline = "queued", "Request queued"
        message = "The campaign manager will process the request when its dependencies and a worker are available. No model call is running yet."
    elif counts["waiting"]:
        status, headline = "waiting", "Research is waiting"
        message = "Agents are waiting for tool results or a campaign manager review; no model call is running for this request."
    elif counts["failed"] or counts["blocked"] or (request and request["status"] in {"failed", "blocked"}):
        status, headline = "failed" if counts["failed"] or (request and request["status"] == "failed") else "blocked", "Research needs manager attention"
        message = "Work for this request encountered an error or a blocked task. Completed evidence is retained."
    elif session_status == "waiting_for_direction":
        status, headline = "waiting", "Campaign manager is waiting for your direction"
        message = "The saved work is ready for review. Send guidance through the campaign manager to continue."
    elif request and request["status"] in {"superseded", "cancelled", "stopped", "interrupted"}:
        status, headline = "waiting", "Research request is no longer active"
        message = "This request was cancelled, interrupted or superseded. Its saved work remains available for review."
    elif tasks and session and not all(task.get("legacy") for task in tasks) and session_status not in SESSION_TERMINAL:
        status, headline = "waiting", "Assigned work is saved"
        message = "The scheduler is waiting for the campaign manager's follow-up. Saved task counts do not establish scientific success."
    elif tasks or (request and request["status"] in TERMINAL):
        status, headline = "completed", "Research work has stopped" if session_status == "stopped" else "Research work is complete"
        message = "Review the saved conversation and evidence for results, limitations and any remaining questions."
    priority = {"running": 0, "waiting": 1, "queued": 2, "blocked": 3, "failed": 4}
    agents.reverse()
    agents.sort(key=lambda agent: priority.get(agent["status"], 5))
    retryable = [agent["task_id"] for agent in agents if agent["status"] == "failed" and agent["error_code"] == "timeout"
        and session_status not in SESSION_TERMINAL and any(task["id"] == agent["task_id"] and not task.get("legacy")
            and not (runs.get(task.get("run_id")) or {}).get("usage.pending_reservation") for task in tasks)]
    stamps = [row.get("last_activity_at") for row in agents]
    stamps.extend([(session or {}).get("updated_at"), (request or {}).get("created_at")])
    return {"status": status, "headline": headline, "message": message, "active": active,
            "session": {key: session.get(key) for key in ("id", "status", "control_revision")} if session else None,
            "request": {"id": request["id"], "message": (request.get("request") or {}).get("message", "")[:1500],
                        "created_at": request.get("created_at"), "status": request["status"]} if request else None,
            "agents": agents[:8], "task_counts": counts,
            "retryable_task_ids": retryable,
            "updated_at": max((stamp for stamp in stamps if stamp), default=None),
            "can_resume": session_status == "paused"}
