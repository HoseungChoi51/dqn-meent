"""Persist research discussions and convert accepted proposals to concrete work."""
from __future__ import annotations

import threading
from .models import ResearchInput, TrialInput, ControlInput
from .store import identifier, now
from .providers import api_spend, provider_status


class ResearchCancelled(Exception):
    pass


class ResearchCoordinator:
    def __init__(self, workspace):
        self.workspace = workspace
        self.store = workspace.store
        workspace.on_trial_finished = self.reconsider_finished

    def context(self, campaign_id):
        campaign = self.store.get(campaign_id, "campaign")
        tasks = [t for t in self.workspace.current_tasks(campaign_id) if t["split"] != "test"]
        permitted = {t["id"] for t in tasks}
        trials = []
        for t in self.store.list("trial", campaign_id):
            if t["task_id"] not in permitted or t.get("task_split") == "test":
                continue
            trials.append({**t, "curve": self.workspace.metrics(t["id"])})
        hypotheses = self.store.list("hypothesis", campaign_id)
        sources = self.store.list("source", campaign_id)
        for h in hypotheses:
            for source in h.get("sources", []):
                if isinstance(source, dict):
                    sources.append(source)
        return {"campaign": {**campaign, "charter": campaign}, "tasks": tasks, "trials": trials,
                "hypotheses": hypotheses, "decisions": self.store.list("decision", campaign_id),
                "evidence_library": sources, "history": self.store.list("message", campaign_id)[-20:]}

    def start(self, request: ResearchInput, automatic=False):
        with self.workspace.lock:
            campaign = self.store.get(request.campaign_id, "campaign")
            runs = self.store.list("research_run", request.campaign_id)
            if any(r["status"] in {"running", "stopping"} for r in runs):
                raise ValueError("This campaign already has an active research discussion; let it finish or stop it first")
            spent = sum(api_spend(r.get("usage")) for r in runs)
            remaining = max(0, campaign["llm_budget_usd"] - spent)
            context = self.context(request.campaign_id)
            payload = request.model_dump()
            payload["llm_budget_usd"] = remaining
            payload["provider_snapshot"] = provider_status()
            record = {"id": identifier("research"), "campaign_id": request.campaign_id,
                      "charter_version": campaign["version"], "request": payload, "context_snapshot": context,
                      "status": "running", "created_at": now(), "trace": [], "usage": {},
                      "checkpoint": None, "automatic": automatic}
            self.store.put("research_run", record, "research.started")
            self.store.put("message", {"id": identifier("message"), "campaign_id": request.campaign_id,
                "role": "user", "content": request.message, "mode": request.mode, "created_at": now(),
                "research_run_id": record["id"], "automatic": automatic}, "message.created")
            self._thread(record)
            return self.public_run(record)

    def _thread(self, record):
        thread = threading.Thread(target=self._run, args=(record["id"],), daemon=True,
                                  name=f"research-{record['id']}")
        self.workspace.research_threads[record["id"]] = thread
        thread.start()

    @staticmethod
    def public_run(record):
        return {k: v for k, v in record.items() if k not in {"context_snapshot", "checkpoint", "resume_fingerprint"}}

    def _emit(self, run_id, event):
        with self.workspace.lock:
            run = self.store.get(run_id, "research_run")
            if event["type"] == "provider_call_cancelled_before_send":
                run["usage"] = event["usage"]
                self.store.put("research_run", run, "research.reservation_released")
                return
            if run["status"] == "stopping":
                # Preserve completed-call usage even when interruption arrives in flight.
                if event["type"] == "research_checkpoint":
                    run["usage"] = event["state"].get("usage", {})
                    run["checkpoint"] = event["state"]
                    run["resume_fingerprint"] = event["fingerprint"]
                    self.store.put("research_run", run)
                raise ResearchCancelled()
            if event["type"] == "provider_call_reserved":
                campaign = self.store.get(run["campaign_id"], "campaign")
                other_cost = sum(api_spend(other.get("usage"))
                    for other in self.store.list("research_run", run["campaign_id"]) if other["id"] != run_id)
                if event["usage"].get("billing_mode") != "subscription" and other_cost + api_spend(event["usage"]) > campaign["llm_budget_usd"] + 1e-9:
                    run["error"] = "The next call exceeds the current campaign funds after other discussions. No request was sent."
                    self.store.put("research_run", run, "research.budget_blocked")
                    raise ResearchCancelled()
                run["usage"] = event["usage"]
                run["current_role"] = event["role"]
                self.store.put("research_run", run, "research.cost_reserved")
                return
            elif event["type"] == "provider_progress":
                return  # Heartbeat checks cancellation without flooding the journal.
            elif event["type"] == "research_checkpoint":
                run.update(checkpoint=event["state"], resume_fingerprint=event["fingerprint"],
                           usage=event["state"].get("usage", {}), trace=event["state"].get("trace", []))
            elif event["type"] == "role_started":
                run["current_role"] = event["role"]
            self.store.put("research_run", run, "research.progress")

    def _run(self, run_id):
        from .research import run_research
        run = self.store.get(run_id, "research_run")
        request = dict(run["request"])
        if run.get("checkpoint"):
            request.update(resume_state=run["checkpoint"], resume_fingerprint=run["resume_fingerprint"])
        try:
            result = run_research(request, run["context_snapshot"], lambda e: self._emit(run_id, e))
            with self.workspace.lock:
                run = self.store.get(run_id, "research_run")
                if run["status"] == "stopping":
                    run.update(usage=result.get("usage", run.get("usage", {})),
                               trace=result.get("trace", run.get("trace", [])), result=result)
                    self.store.put("research_run", run)
                    raise ResearchCancelled()
                campaign = self.store.get(run["campaign_id"], "campaign")
                if campaign["version"] != run["charter_version"]:
                    result["stale_charter"] = True
                for h in result.get("hypotheses", []):
                    h.update(campaign_id=run["campaign_id"], charter_version=run["charter_version"], created_at=now())
                    self.store.put("hypothesis", h, "hypothesis.created")
                for message in result.get("messages", []):
                    self.store.put("message", {**message, "id": identifier("message"),
                        "campaign_id": run["campaign_id"], "created_at": now(), "research_run_id": run_id,
                        "origin": result.get("mode"), "stale_charter": result.get("stale_charter", False)}, "message.created")
                for decision in result.get("decisions", []):
                    self._save_decision(run, decision)
                for action in result.get("actions", []):
                    action.update(campaign_id=run["campaign_id"], charter_version=run["charter_version"],
                                  research_run_id=run_id, created_at=now())
                    self.store.put("action", action)
                    eligible = (campaign["autonomy"] == "delegated" and not result.get("decisions")
                        and not result.get("stale_charter") and action["kind"] == "probe"
                        and not any(t["status"] in {"running", "queued", "pausing", "stopping"} for t in self.store.list("trial", run["campaign_id"])))
                    if eligible:
                        try:
                            outcome = self.execute_action(action)
                            self.store.put("action", {**action, "status": "executed_within_delegation", "outcome": outcome}, "action.executed")
                            continue
                        except ValueError as exc:
                            action["allocation_issue"] = str(exc)
                    self._save_decision(run, {"id": identifier("decision"), "title": action["title"],
                        "rationale": action["rationale"], "action_id": action["id"], "status": "pending",
                        "options": [{"id": "accept", "label": "Proceed", "description": action.get("expected_information", "")},
                                    {"id": "defer", "label": "Defer", "description": "Keep this direction available without launching it."},
                                    {"id": "reject", "label": "Decline", "description": "Archive this recommendation."}],
                        "recommendation": "accept"})
                run.update(status=result.get("status", "completed"), result=result,
                           usage=result.get("usage", {}), trace=result.get("trace", []), finished_at=now())
                self.store.put("research_run", run, "research.finished")
        except ResearchCancelled:
            with self.workspace.lock:
                run = self.store.get(run_id, "research_run")
                run.update(status="stopped", finished_at=now())
                self.store.put("research_run", run, "research.stopped")
        except Exception as exc:
            with self.workspace.lock:
                run = self.store.get(run_id, "research_run")
                run.update(status="failed", error=f"Research workflow failed ({type(exc).__name__}); completed role evidence is retained.", finished_at=now())
                self.store.put("research_run", run, "research.failed")

    def reconsider_finished(self):
        """One event-driven reconsideration per completed batch, bounded by delegation."""
        with self.workspace.lock:
            for campaign in self.store.list("campaign"):
                if campaign["autonomy"] != "delegated":
                    continue
                trials = self.store.list("trial", campaign["id"])
                pending = [t for t in trials if t.get("research_pending") and t["algorithm"] != "validate"]
                if not pending or any(t["status"] in {"running", "queued", "pausing", "stopping"} for t in trials):
                    continue
                if any(r["status"] in {"running", "stopping"} for r in self.store.list("research_run", campaign["id"])):
                    continue
                if any(d["status"] == "pending" for d in self.store.list("decision", campaign["id"])):
                    continue
                # A researcher's stop is a direction to leave that branch stopped.
                current_tasks = {t["id"] for t in self.workspace.current_tasks(campaign["id"])
                                 if t["split"] != "test"}
                eligible = [t for t in pending if t.get("stopped_by") != "researcher"
                            and t.get("task_split") != "test" and t["task_id"] in current_tasks
                            and t["charter_version"] == campaign["version"]]
                for trial in pending:
                    trial["research_pending"] = False
                    self.store.put("trial", trial)
                if eligible:
                    ids = ", ".join(t["id"] for t in eligible)
                    self.start(ResearchInput(campaign_id=campaign["id"], mode="plan", max_calls=3,
                        message=f"New development evidence from trials {ids}. Reconsider the most informative next action. Do not repeat an identical probe; ask the researcher if startup, numerical reliability, or budget prevents a meaningful decision."), automatic=True)

    def _save_decision(self, run, decision):
        for previous in self.store.list("decision", run["campaign_id"]):
            if (previous["charter_version"] == run["charter_version"] and
                    previous.get("title") == decision.get("title") and
                    previous.get("trial_id") == decision.get("trial_id") and
                    not decision.get("action_id")):
                return  # Keep the researcher's existing answer instead of asking again.
        options = []
        for index, choice in enumerate(decision.get("options", [])):
            options.append(choice if isinstance(choice, dict) else {"id": str(index), "label": choice, "description": ""})
        decision.update(campaign_id=run["campaign_id"], charter_version=run["charter_version"],
                        research_run_id=run["id"], created_at=now(), options=options,
                        context=decision.get("context", decision.get("rationale", "")),
                        recommendation=decision.get("recommendation", str(decision.get("recommended_option", 0))))
        self.store.put("decision", decision, "decision.created")

    def control(self, run_id, action):
        with self.workspace.lock:
            run = self.store.get(run_id, "research_run")
            if action == "stop":
                if run["status"] == "running":
                    run["status"] = "stopping"
                    self.store.put("research_run", run, "research.stop_requested")
            elif action == "resume":
                if run["status"] != "interrupted" or not run.get("checkpoint"):
                    raise ValueError("Only an interrupted discussion with a checkpoint can be resumed")
                campaign = self.store.get(run["campaign_id"], "campaign")
                if campaign["version"] != run["charter_version"]:
                    raise ValueError("Charter changed; start a new discussion with current evidence")
                if any(r["status"] in {"running", "stopping"} for r in self.store.list("research_run", run["campaign_id"])):
                    raise ValueError("Another discussion is active")
                run["status"] = "running"
                self.store.put("research_run", run, "research.resumed")
                self._thread(run)
            else:
                raise ValueError("Unknown research control action")
            return self.public_run(run)

    def resolve(self, decision_id, choice, comment):
        deferred_search = None
        with self.workspace.lock:
            decision = self.store.get(decision_id, "decision")
            if decision["status"] != "pending":
                raise ValueError("This decision has already been resolved")
            choices = {o["id"] for o in decision["options"]}
            if choice not in choices and choice != "custom":
                raise ValueError("Select a listed option or provide a custom direction")
            outcome = None
            campaign = self.store.get(decision["campaign_id"], "campaign")
            if choice == "close_reserved" and decision.get("research_run_id"):
                run = self.store.get(decision["research_run_id"], "research_run")
                if run["status"] != "needs_reconciliation":
                    raise ValueError("This provider call is not awaiting reconciliation")
                run.update(status="closed_uncertain", finished_at=now())
                self.store.put("research_run", run, "research.closed_uncertain")
                outcome = {"research_run_id": run["id"], "reserved_cost_retained": True}
            elif choice == "accept" and decision.get("action_id"):
                if decision["charter_version"] != campaign["version"]:
                    raise ValueError("This recommendation used an older charter; request a current proposal")
                action = self.store.get(decision["action_id"], "action")
                if action["kind"] == "search":
                    # Network retrieval must never hold up trial stop/pause controls.
                    deferred_search = action
                    decision.update(status="executing", choice=choice, comment=comment)
                    self.store.put("decision", decision, "decision.executing")
                else:
                    outcome = self.execute_action(action)
                    self.store.put("action", {**action, "status": "accepted", "outcome": outcome})
            elif choice == "0" and decision.get("trial_id") and decision.get("incremental_solver_calls"):
                trial = self.store.get(decision["trial_id"], "trial")
                extra = decision["incremental_solver_calls"]
                seconds = decision.get("estimated_seconds")
                if seconds is None:
                    raise ValueError("No reliable runtime estimate exists; extend this trial with an explicit time budget")
                outcome = self.workspace.control(trial["id"], ControlInput(action="extend",
                    max_steps=trial["max_steps"] + extra, wall_seconds=trial["wall_seconds"] + max(5, seconds)))
            if deferred_search is None:
                return self._finish_decision(decision, choice, comment, outcome)
        try:
            outcome = self.execute_action(deferred_search)
        except Exception:
            with self.workspace.lock:
                decision["status"] = "pending"
                self.store.put("decision", decision, "decision.action_failed")
            raise
        with self.workspace.lock:
            self.store.put("action", {**deferred_search, "status": "accepted", "outcome": outcome})
            return self._finish_decision(decision, choice, comment, outcome)

    def _finish_decision(self, decision, choice, comment, outcome):
        decision.update(status="resolved", choice=choice, comment=comment, resolved_at=now(), outcome=outcome)
        self.store.put("decision", decision, "decision.resolved")
        label = next((o["label"] for o in decision["options"] if o["id"] == choice), choice)
        self.store.put("message", {"id": identifier("message"), "campaign_id": decision["campaign_id"],
            "role": "user", "content": f"Decision: {decision['title']}\nChoice: {label}\n{comment}", "created_at": now()}, "message.created")
        return decision

    def execute_action(self, action):
        campaign = self.store.get(action["campaign_id"], "campaign")
        hypothesis = self.store.get(action["hypothesis_id"], "hypothesis") if action.get("hypothesis_id") else None
        if action["kind"] == "probe":
            if not action.get("task_id"):
                raise ValueError("This proposal needs a concrete development task")
            task = self.store.get(action["task_id"], "task")
            if task["split"] == "test":
                raise ValueError("Exploratory proposals cannot access test tasks")
            algorithm = action.get("algorithm") or (hypothesis or {}).get("algorithm", "random")
            config = (hypothesis or {}).get("algorithm_config", {})
            proposed_steps = action.get("budget_calls") or 64
            if any(t["task_id"] == task["id"] and t["algorithm"] == algorithm and t["seed"] == 0
                   and t["max_steps"] == proposed_steps and t["algorithm_config"] == config
                   for t in self.store.list("trial", campaign["id"])):
                raise ValueError("An identical probe already exists; choose another seed, task, budget, or strategy")
            trial = self.workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=task["id"],
                algorithm=algorithm, algorithm_config=config, hypothesis_id=hypothesis["id"] if hypothesis else None,
                max_steps=proposed_steps, wall_seconds=campaign["delegated_trial_seconds"],
                question=action["question"]))
            return {"trial_id": trial["id"]}
        if action["kind"] == "nominate" and hypothesis:
            from .confirmation import nominate_finalist
            hypothesis = nominate_finalist(hypothesis)
            self.store.put("hypothesis", hypothesis, "hypothesis.nominated")
            return {"hypothesis_id": hypothesis["id"]}
        if action["kind"] == "search":
            from .evidence import search_literature, EvidenceError
            try:
                result = search_literature(action["question"][:500], limit=5, provider="crossref")
            except EvidenceError as exc:
                raise ValueError(str(exc)) from exc
            for source in result["sources"]:
                source["id"] = campaign["id"] + "_" + source["id"]
                source.update(campaign_id=campaign["id"], created_at=now())
                self.store.put("source", source, "source.retrieved")
            return {"sources": [s["id"] for s in result["sources"]]}
        mode = {"review": "review", "compare": "compare", "evolve": "evolve", "implement": "evolve"}.get(action["kind"])
        if mode:
            return self.start(ResearchInput(campaign_id=campaign["id"], mode=mode,
                hypothesis_id=(hypothesis or {}).get("id"), message=action["question"] + "\n" + action["rationale"]))
        raise ValueError("This action needs an explicit experiment configuration or researcher direction; use the experiment controls")
