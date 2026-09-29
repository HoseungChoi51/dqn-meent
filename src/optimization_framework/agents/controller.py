"""Durable campaign ownership, Pi assignments, controls, and recovery.

Pi's transcript is reasoning memory. These records remain the authority for
campaign state, grants and scientific outcomes. Network calls never own a SQL
transaction. One workspace service lease serializes all scheduling.
"""
from collections import Counter
from copy import deepcopy
import json
import os
import threading

from optimization_framework.contracts.base import content_hash
from optimization_framework.storage.sqlite import now
from .client import PiClient
from .models import Activate, Message, Control, ROLES

ACTIVE = {"queued", "submitted", "running"}
TERMINAL = {"completed", "failed", "stopped", "interrupted"}


class PiController:
    def __init__(self, workspace, client=None):
        self.workspace, self.store = workspace, workspace.store
        self.client = client or PiClient()
        self.threads = {}
        self.diagnostic_processes = {}
        self.tool_locks = {}
        from .development import DevelopmentWorkspaces
        self.development = DevelopmentWorkspaces(self)

    def rollback(self, campaign_id, payload):
        from .models import Rollback
        values = Rollback.model_validate(payload)
        config = self.configuration(campaign_id)
        if not config or config["status"] not in {"paused", "stopped"}:
            raise ValueError("Pause or stop Pi and wait for active turns before rollback")
        if any(r["status"] in ACTIVE for r in self.store.list("agent_run", campaign_id)):
            raise ValueError("Pi turns are still settling; retry after control acknowledgment")
        config.update(enabled=False, rollback_reason=values.reason, rolled_back_at=now())
        self.store.put("agent_campaign", config, "agent.rolled_back")
        return config

    def configuration(self, campaign_id):
        try:
            return self.store.get("pi_campaign_" + campaign_id, "agent_campaign")
        except KeyError:
            return None

    def owns(self, campaign_id):
        return bool((self.configuration(campaign_id) or {}).get("enabled"))

    def activate(self, campaign_id, payload, command_id):
        values = Activate.model_validate(payload)
        existing = self.configuration(campaign_id)
        if existing and existing.get("enabled"):
            return self.view(campaign_id)
        campaign = self.store.get(campaign_id, "campaign")
        if any(r["status"] in {"running", "stopping", "needs_reconciliation"}
               for r in self.store.list("research_run", campaign_id)):
            raise ValueError("Drain or reconcile active legacy model calls before migrating")
        if any(t.get("status") in {"running", "waiting"} for t in self.store.list("discovery_task", campaign_id)):
            raise ValueError("Drain active discovery assignments before migrating")
        sessions = self.store.list("discovery_session", campaign_id)
        manifest = {"id": "pi_migration_" + command_id, "campaign_id": campaign_id, "created_at": now(),
            "legacy_sessions": [{"id": s["id"], "status": s["status"], "policy": s["policy"]} for s in sessions],
            "legacy_autonomy": campaign["autonomy"],
            "budget_snapshot": {k: campaign.get(k) for k in ("compute_budget_seconds", "implementation_compute_budget_seconds",
                "validation_reserve_seconds", "delegated_trial_seconds", "llm_budget_usd")},
            "outstanding_tasks": [t["id"] for t in self.store.list("discovery_task", campaign_id)
                if t["status"] not in {"completed", "cancelled", "superseded"}],
            "hypothesis_ids": [h["id"] for h in self.store.list("hypothesis", campaign_id)],
            "objective": values.objective, "limitations": "Migration preserves unfinished work; no scientific prerequisites are asserted complete."}
        self.store.put_immutable("agent_migration", manifest, "agent.migrated")
        with self.store.connection() as db:
            event_cursor = db.execute("SELECT COALESCE(MAX(id),0) FROM events WHERE campaign_id=?", (campaign_id,)).fetchone()[0]
        config = {"id": "pi_campaign_" + campaign_id, "campaign_id": campaign_id, "enabled": True,
            "status": "running", "control_revision": 0, "max_subagents": values.max_subagents,
            "objective": values.objective, "migration_id": manifest["id"], "created_at": now(), "event_cursor": event_cursor,
            "provider": {"configured": False, "reason": "Checking Pi connection"}}
        self.store.put("agent_campaign", config, "agent.activated")
        if values.delegated:
            campaign["autonomy"] = "delegated"
            self.store.put("campaign", campaign, "campaign.delegation_changed")
        for session in sessions:
            if session["status"] not in {"completed", "stopped", "exhausted"}:
                session.update(status="stopped", runtime_successor=config["id"], updated_at=now())
                self.store.put("discovery_session", session, "discovery.migrated")
        for index, hypothesis in enumerate(self.store.list("hypothesis", campaign_id), 1):
            self.store.put_immutable("agent_alias", {"id": "pi_alias_" + hypothesis["id"], "campaign_id": campaign_id,
                "label": f"H{index:02d}", "record_id": hypothesis["id"], "candidate_id": hypothesis.get("candidate_id")})
        pi = self.create_agent(campaign_id, "pi", values.objective, "pi_" + campaign_id)
        config["pi_id"] = pi["id"]; self.store.put("agent_campaign", config)
        self.enqueue(pi, "pi_boot_" + command_id, "Continue the migrated campaign. Inspect campaign state and the migration manifest, "
            "reuse completed work, reconcile unfinished assignments, and act on the latest unfulfilled researcher request. "
            "Do not repeat conceptual reviews or treat already runnable evaluators as absent.\n" + values.objective)
        return self.view(campaign_id)

    def create_agent(self, campaign_id, role, objective, identity, *, parent_id=None, evidence_ids=None, grant_id=None, output_schema=None):
        if role not in ROLES:
            raise ValueError("Unknown agent role")
        try:
            saved = self.store.get(identity, "agent_session")
            if saved["role"] != role or saved["campaign_id"] != campaign_id or saved.get("parent_agent_id") != parent_id or saved.get("grant_id") != grant_id:
                raise ValueError("Agent identity belongs to a different assignment")
            return saved
        except KeyError:
            pass
        if parent_id:
            parent = self.store.get(parent_id, "agent_session")
            if parent["campaign_id"] != campaign_id or parent["role"] != "pi":
                raise ValueError("Assignments require this campaign's PI")
        record = {"id": identity, "campaign_id": campaign_id, "parent_agent_id": parent_id,
            "role": role, "objective": objective, "evidence_ids": evidence_ids or [], "grant_id": grant_id,
            "model": "gpt-6-astra" if role in {"pi", "proposal_reviewer", "implementation_validator"} else "gpt-6-sol",
            "reasoning_effort": "xhigh", "status": "queued", "control_revision": 0,
            "created_at": now(), "event_cursor": 0, "usage": {"billing_mode": "subscription", "api_cost_usd": 0},
            "output_schema": output_schema, "output": None}
        return self.store.put("agent_session", record, "agent.created")

    def enqueue(self, agent, identity, text, *, mode="follow_up", manager_command_id=None):
        try:
            saved = self.store.get(identity, "agent_run")
            if saved["agent_id"] != agent["id"] or saved["input"] != text or saved["mode"] != mode:
                raise ValueError("Run identity belongs to a different request")
            return saved
        except KeyError:
            pass
        run = {"id": identity, "campaign_id": agent["campaign_id"], "agent_id": agent["id"], "input": text,
            "mode": mode, "status": "queued", "created_at": now(), "manager_command_id": manager_command_id,
            "guidance_revision": self.workspace.memory.state(agent["campaign_id"])["guidance_revision"]}
        self.store.put("agent_run", run, "agent.run_queued")
        if agent["status"] not in {"paused", "stopped"}:
            agent.update(status="queued", updated_at=now()); self.store.put("agent_session", agent)
        return run

    def admit_legacy(self, command):
        config = self.configuration(command["campaign_id"])
        agent = self.store.get(config["pi_id"], "agent_session")
        request = command["request"]
        run = self.enqueue(agent, "pi_run_" + command["id"], request["message"] + "\nResearch request details: " + json.dumps(request)
            + ("\nReview the frozen decision refresh record " + command["decision_refresh_id"] if command.get("decision_refresh_id") else ""), mode="steer",
                           manager_command_id=command["id"])
        command.update(status="dispatched", agent_run_id=run["id"], agent_id=agent["id"])
        self.store.put("manager_command", command, "manager.message_dispatched")
        try:
            message = self.store.get("message_" + command["id"], "message")
            message["agent_id"] = agent["id"]
            self.store.put("message", message)
        except KeyError:
            pass
        for entry in self.store.list("manager_input", command["campaign_id"]):
            if entry.get("manager_command_id") == command["id"] and entry["status"] == "pending":
                entry.update(status="consumed", agent_run_id=run["id"], consumed_at=now())
                self.store.put("manager_input", entry, "manager.input_consumed")
        return run

    def message(self, campaign_id, payload, command_id):
        values = Message.model_validate(payload)
        config = self.configuration(campaign_id)
        if not config or not config["enabled"]:
            raise ValueError("Activate Pi for this campaign first")
        agent = self.store.get(values.agent_id or config["pi_id"], "agent_session")
        if agent["campaign_id"] != campaign_id:
            raise ValueError("Agent belongs to another campaign")
        if config["status"] == "completed":
            config["status"] = "running"
            self.store.put("agent_campaign", config)
        state = self.workspace.memory.state(campaign_id)
        state["guidance_revision"] += 1
        self.store.put("manager_state", state, "manager.guidance_changed")
        if values.question_id:
            question = self.store.get(values.question_id, "agent_question")
            if question["campaign_id"] != campaign_id or question["status"] != "pending":
                raise ValueError("Question is no longer pending in this campaign")
            question.update(status="answered", answer=values.message, answered_at=now())
            self.store.put("agent_question", question, "agent.question_answered")
        self.store.put("message", {"id": "message_" + command_id, "campaign_id": campaign_id, "role": "user",
            "content": values.message, "created_at": now(), "agent_id": agent["id"]}, "message.created")
        return self.enqueue(agent, "pi_run_" + command_id, values.message, mode=values.mode)

    def control(self, campaign_id, payload, command_id):
        values = Control.model_validate(payload)
        config = self.configuration(campaign_id)
        record = self.store.get(values.agent_id, "agent_session") if values.agent_id else config
        if not record or record["campaign_id"] != campaign_id:
            raise ValueError("Control target belongs to another campaign")
        if record["control_revision"] != values.expected_control_revision:
            raise ValueError("Agent control changed; refresh before continuing")
        status = {"pause": "paused", "stop": "stopped", "resume": "running"}[values.action]
        record.update(status=status, control_revision=record["control_revision"] + 1)
        self.store.put("agent_session" if values.agent_id else "agent_campaign", record, "agent.controlled")
        agents = [record] if values.agent_id else self.store.list("agent_session", campaign_id)
        for agent in agents:
            if values.action == "resume" and agent["status"] in {"completed", "stopped"} and agent["role"] != "pi":
                continue
            agent.update(status=status, pending_control=values.action)
            self.store.put("agent_session", agent)
            for run in self.store.list("agent_run", campaign_id):
                if run["agent_id"] == agent["id"] and run["status"] == "queued" and values.action != "resume":
                    run.update(status=status, undispatched=True)
                    self.store.put("agent_run", run)
            if values.action == "resume":
                undispatched = [r for r in self.store.list("agent_run", campaign_id)
                    if r["agent_id"] == agent["id"] and r["status"] == "paused" and r.get("undispatched")]
                for saved in undispatched:
                    resumed_id = "pi_resume_input_" + content_hash([command_id, saved["id"]])[:28]
                    self.enqueue(agent, resumed_id, saved["input"], mode=saved["mode"])
                    saved.update(status="stopped", superseded_by=resumed_id)
                    self.store.put("agent_run", saved)
                self.enqueue(agent, "pi_resume_" + content_hash([command_id, agent["id"]])[:28],
                    "Resume this assignment from saved session history. Inspect recorded tool receipts and current campaign state before acting; do not duplicate accepted work.")
        return record

    def view(self, campaign_id):
        config = self.configuration(campaign_id)
        return {"configuration": config,
            "agents": [{k: v for k, v in a.items() if k not in {"output_schema", "output"}}
                       for a in self.store.list("agent_session", campaign_id)],
            "questions": [q for q in self.store.list("agent_question", campaign_id) if q["status"] == "pending"],
            "aliases": self.store.list("agent_alias", campaign_id),
            "development": self.development.view(campaign_id)}

    def progress(self, campaign_id):
        view = self.view(campaign_id)
        config, agents = view["configuration"], view["agents"]
        active = any(a["status"] in {"queued", "running"} for a in agents)
        ready = config.get("provider", {}).get("configured", False)
        status = config["status"] if config["status"] in {"paused", "stopped", "completed"} else "running" if active and ready else "waiting"
        return {"status": status, "headline": "PI agent " + ("is working" if status == "running" else status),
            "message": config.get("provider", {}).get("reason") or ("Connect Pi to OpenAI Codex to continue saved work." if not ready
                else "Persistent Pi sessions coordinate the campaign and its subagents."), "active": status == "running",
            "task_counts": {**Counter(a["status"] for a in agents), "total": len(agents)},
            "agents": [{"task_id": a["id"], "role": a["role"], "stage": "campaign" if a["role"] == "pi" else "assignment",
                "status": a["status"], "model": a["model"], "reasoning_effort": a["reasoning_effort"],
                "activity": a.get("activity", a["objective"]), "last_activity_at": a.get("last_activity_at"),
                "active": a["status"] == "running"} for a in agents]}

    def tick(self, campaign_id):
        if self.workspace.shutdown_event.is_set() or not self.owns(campaign_id):
            return
        previous = self.threads.get(campaign_id)
        if previous and previous.is_alive():
            return
        thread = threading.Thread(target=self._sync, args=(campaign_id,), daemon=True, name="pi-sync-" + campaign_id)
        self.threads[campaign_id] = thread
        self.workspace.research_threads["pi_" + campaign_id] = thread
        thread.start()

    def _sync(self, campaign_id):
        status = None
        try:
            self.development.sync(campaign_id)
            status = self.client.status()
            if os.environ.get("GRATING_LLM_ENABLED", "true").lower() == "false" or os.environ.get("GRATING_LLM_DISABLED", "false").lower() == "true":
                status = {**status, "configured": False, "reason": "Model calls are disabled in the server configuration"}
            with self.workspace.lock, self.store.transaction():
                config = self.configuration(campaign_id)
                config["provider"] = status
                config.pop("sync_error", None)
                self.store.put("agent_campaign", config)
            for agent in self.store.list("agent_session", campaign_id):
                if agent.get("pending_control"):
                    self.client.control(agent["id"], agent["pending_control"])
                    with self.workspace.lock:
                        current = self.store.get(agent["id"], "agent_session")
                        if current.get("pending_control") == agent["pending_control"]:
                            current.pop("pending_control", None); self.store.put("agent_session", current)
                remote = self.client.inspect(agent["id"], agent.get("event_cursor", 0))
                if remote:
                    self._receive(agent["id"], remote)
                # A POST can fail before reaching an already existing session.
                # Reconcile the exact run, not merely the existence of the agent.
                with self.workspace.lock:
                    for run in self.store.list("agent_run", campaign_id):
                        if run["agent_id"] == agent["id"] and run["status"] == "submitted" and run["id"] not in (remote or {}).get("runs", {}):
                            current = self.store.get(agent["id"], "agent_session")
                            run["status"] = current["status"] if current["status"] in {"paused", "stopped"} else "queued"
                            run["undispatched"] = True
                            self.store.put("agent_run", run)
            with self.workspace.lock, self.store.transaction():
                config = self.configuration(campaign_id)
                if config["status"] == "completed" and any(c["status"] in {"queued", "waiting_provider", "waiting_discovery"} for c in self.store.list("manager_command", campaign_id)):
                    config["status"] = "running"
                    self.store.put("agent_campaign", config)
                if config["status"] != "running":
                    return
                for command in self.store.list("manager_command", campaign_id):
                    if command["status"] in {"queued", "waiting_provider", "waiting_discovery"}:
                        self.admit_legacy(command)
                self._campaign_events(campaign_id)
                if not status.get("configured"):
                    return
                agents = {a["id"]: a for a in self.store.list("agent_session", campaign_id)}
                runs = self.store.list("agent_run", campaign_id)
                occupied = {r["agent_id"] for r in runs if r["status"] in {"submitted", "running"}}
                children = sum(agents[a]["role"] != "pi" for a in occupied)
                pending = []
                for run in runs:
                    agent = agents[run["agent_id"]]
                    if run["status"] != "queued" or agent["status"] in {"paused", "stopped"}:
                        continue
                    if agent["id"] in occupied and run.get("mode") != "steer":
                        continue
                    if agent["role"] != "pi" and agent["id"] not in occupied and children >= config["max_subagents"]:
                        continue
                    if agent["id"] not in occupied and agent["role"] != "pi":
                        children += 1
                    occupied.add(agent["id"])
                    run.update(status="submitted", dispatched_at=now()); self.store.put("agent_run", run)
                    pending.append((agent, run))
            for agent, run in pending:
                # A lost POST response is reconciled by exact run ID on the next
                # tick. Never generate a new run ID for a transport retry.
                try:
                    self.client.submit(agent, run)
                except ValueError:
                    if self.client.inspect(agent["id"]) is None:
                        with self.workspace.lock:
                            saved = self.store.get(run["id"], "agent_run")
                            saved["status"] = "queued"; self.store.put("agent_run", saved)
                    raise
        except Exception as exc:
            with self.workspace.lock:
                config = self.configuration(campaign_id)
                if config:
                    # A projection/tool error is not a credential failure. Keep
                    # successful authentication visible and report sync separately.
                    if status is None:
                        config["provider"] = {"configured": False, "reason": str(exc)[:1500]}
                    config["sync_error"] = str(exc)[:1500]
                    self.store.put("agent_campaign", config)

    def _receive(self, agent_id, remote):
        with self.workspace.lock, self.store.transaction():
            agent = self.store.get(agent_id, "agent_session")
            for event in remote.get("events", []):
                self.workspace.agent_log.record(agent["campaign_id"], "pi." + event["type"], agent_id=agent_id,
                    role=agent["role"], parent_agent_id=agent.get("parent_agent_id"),
                    event_key=f"pi:{agent_id}:{event['seq']}", summary=event.get("text", event.get("summary", event["type"]))[:2000], payload=event)
                if event["seq"] > agent.get("event_cursor", 0) and event.get("usage"):
                    usage = agent["usage"]
                    for key in ("input", "output", "cacheRead", "cacheWrite"):
                        usage[key] = usage.get(key, 0) + event["usage"].get(key, 0)
                    usage["calls"] = usage.get("calls", 0) + 1
                agent["last_activity_at"] = event["occurred_at"]
                error = event.get("error")
                activity = error if isinstance(error, str) and error else (
                    f"Tool {event.get('tool', 'call')} failed; the agent can inspect its result." if error is True else
                    event.get("text") or event.get("summary") or event["type"])
                agent["activity"] = activity[:500]
            agent.update(event_cursor=remote.get("cursor", agent.get("event_cursor", 0)), pi_session_id=remote.get("session_id"))
            for rid, result in remote.get("runs", {}).items():
                try:
                    run = self.store.get(rid, "agent_run")
                except KeyError:
                    continue
                if run["agent_id"] != agent_id or run["status"] in TERMINAL:
                    continue
                previous = run["status"]
                run.update(status=result["status"], result=result.get("result"), error=result.get("error"))
                self.store.put("agent_run", run, "agent.run_updated" if previous != run["status"] else None)
                if run["status"] in TERMINAL and previous not in TERMINAL:
                    if run.get("manager_command_id"):
                        command = self.store.get(run["manager_command_id"], "manager_command")
                        command.update(status="completed" if run["status"] == "completed" else "handed_off")
                        self.store.put("manager_command", command)
                    text = (run.get("result") or {}).get("text")
                    if text:
                        self.store.put("message", {"id": "message_" + rid, "campaign_id": agent["campaign_id"],
                            "role": "assistant", "origin": "pi", "agent_id": agent_id, "content": text, "created_at": now()}, "message.created")
                    if agent["parent_agent_id"] and (run.get("result") or {}).get("delivery") != "steering_queued":
                        parent = self.store.get(agent["parent_agent_id"], "agent_session")
                        self.enqueue(parent, "pi_child_result_" + content_hash(rid)[:28],
                            f"Subagent {agent_id} ({agent['role']}) finished with status {run['status']}. "
                            f"Read agent run {rid} and its artifacts. Continue the campaign or revise the assignment; a completed response is not proof of scientific completion.")
                    if run["status"] == "interrupted" and not agent.get("grant_id"):
                        self.enqueue(agent, "pi_recover_" + content_hash(rid)[:28],
                            f"Assignment {rid} was interrupted by a harness restart. Read saved receipts and continue from persisted history. Do not repeat committed operations.")
                    if agent["role"] == "pi" and run["status"] == "completed" and (run.get("result") or {}).get("delivery") != "steering_queued":
                        disposition = run.get("disposition", {})
                        if disposition.get("disposition") == "complete":
                            config = self.configuration(agent["campaign_id"])
                            config.update(status="completed", completed_at=now())
                            self.store.put("agent_campaign", config, "agent.objective_completed")
                        elif not disposition:
                            # A response boundary isn't task completion. Continue
                            # with a checkpoint; repeated empty turns ask clearly.
                            stalls = agent.get("uncheckpointed_turns", 0) + 1
                            agent["uncheckpointed_turns"] = stalls
                            if stalls <= 2:
                                self.enqueue(agent, "pi_continue_" + content_hash(rid)[:28],
                                    "Continue the authorized objective using tools. If awaiting work, call yield_work(waiting). If a real blocker needs researcher input, call researcher_ask and yield_work(waiting). Save evidence and use yield_work(complete) only when the objective is achieved.")
                            else:
                                self.store.put("agent_question", {"id": "pi_stalled_" + rid, "campaign_id": agent["campaign_id"],
                                    "agent_id": agent_id, "status": "pending", "created_at": now(),
                                    "question": "The PI stopped making tool-backed progress. Give it a revised next step or resume with a narrower assignment.",
                                    "reason": "Three responses ended without a checkpoint; saved work is retained."}, "agent.question_created")
                        else:
                            agent["uncheckpointed_turns"] = 0
            remaining = [r for r in self.store.list("agent_run", agent["campaign_id"]) if r["agent_id"] == agent_id and r["status"] in ACTIVE]
            if agent["status"] not in {"paused", "stopped"}:
                agent["status"] = "running" if remaining else "waiting" if agent["role"] == "pi" else "completed"
                last_run = next((r for r in reversed(self.store.list("agent_run", agent["campaign_id"])) if r["agent_id"] == agent_id), None)
                if not remaining and last_run and last_run["status"] == "failed":
                    agent.update(status="failed", activity=last_run.get("error") or "Pi turn failed; work is saved. Send a revised direction or resume.")
            self.store.put("agent_session", agent, "agent.progress")

    def _campaign_events(self, campaign_id):
        config = self.configuration(campaign_id)
        rows = self.store.events(campaign_id, after=config.get("event_cursor", 0), limit=500)
        if not rows:
            return
        relevant = [r for r in rows if r["kind"] in {"trial.evidence_cataloged", "trial.failed", "trial.interrupted",
            "implementation.attached", "implementation.ready", "implementation.evidence_updated",
            "validation.measured", "fixed_mask.completed", "campaign.updated"}]
        for row in rows:
            if row["kind"] == "implementation.updated" and row["data"].get("record_id"):
                grant = self.store.get(row["data"]["record_id"], "implementation_grant")
                if grant["status"] in {"failed", "interrupted", "blocked", "cancelled", "needs_reconciliation"}:
                    relevant.append(row)
        config["event_cursor"] = rows[-1]["id"]; self.store.put("agent_campaign", config)
        if relevant:
            agent = self.store.get(config["pi_id"], "agent_session")
            self.enqueue(agent, "pi_events_" + content_hash([r["id"] for r in relevant])[:28],
                "New campaign outcomes are available. Inspect the records and continue:\n" + json.dumps(relevant))
