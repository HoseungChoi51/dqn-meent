"""One durable inbox and serialized reasoning stream per campaign."""
from optimization_framework.research import coordinator as research
from optimization_framework.contracts.base import content_hash
from optimization_framework.contracts.commands import Command
from optimization_framework.contracts.requests import ResearchInput
from optimization_framework.storage.sqlite import identifier, now

from . import inbox


class CampaignManager(research.ResearchCoordinator):
    def __init__(self, workspace):
        super().__init__(workspace)
        workspace.manager = self
        workspace.on_manager_tick = self.tick

    def admit(self, request, *, automatic, command_id, feedback_snapshot=None):
        """Pure admission, committed in the application command's transaction."""
        try:
            previous = self.store.get(command_id, "manager_command")
        except KeyError:
            previous = None
        if previous:
            if (previous["request"] != request.model_dump() or previous["automatic"] != automatic
                    or previous.get("feedback_snapshot") != feedback_snapshot):
                raise ValueError("Manager command identity belongs to another request")
            return previous
        command = {"id": command_id, "campaign_id": request.campaign_id,
            "request": request.model_dump(), "automatic": automatic, "status": "queued", "created_at": now(),
            "feedback_snapshot": feedback_snapshot, "input_ids": [],
            "admitted_guidance_revision": self.workspace.memory.state(request.campaign_id)["guidance_revision"]}
        self.store.put("manager_command", command, "manager.message_queued")
        if not automatic:
            inbox.admit_user(self.store, command)
            self.store.put("message", {"id": "message_" + command_id, "campaign_id": request.campaign_id,
                "role": "user", "content": request.message, "mode": request.mode, "created_at": command["created_at"],
                "manager_command_id": command_id}, "message.created")
        return command

    def reply(self, identity):
        command = self.store.get(identity, "manager_command")
        if command.get("research_run_id"):
            return self.public_run(self.store.get(command["research_run_id"], "research_run"))
        return {**command, "message": command["request"]["message"], "mode": command["request"]["mode"]}

    def start(self, request: ResearchInput, automatic=False, command_id=None, guidance_recorded=False, feedback_snapshot=None):
        if command_id is None:
            campaign = self.store.get(request.campaign_id, "campaign")
            receipt = self.workspace.commands.execute(Command(id=identifier("manager_request"),
                campaign_id=campaign["id"], operation="research.start", expected_revision=campaign["version"],
                expected_guidance_revision=self.workspace.memory.state(campaign["id"])["guidance_revision"],
                expected_authority_hash=self.workspace.commands.authority_hash(campaign), payload=request.model_dump()),
                actor="manager" if automatic else "researcher")
            return self.reply(receipt["outcome"]["manager_command_id"])
        # Reconcile accepted effects from before transactional inbox admission.
        with self.workspace.lock, self.store.transaction():
            try:
                self.store.get(command_id, "manager_command")
            except KeyError:
                if not automatic and not guidance_recorded:
                    state = self.workspace.memory.state(request.campaign_id)
                    state["guidance_revision"] += 1
                    self.store.put("manager_state", state)
            self.admit(request, automatic=automatic, command_id=command_id, feedback_snapshot=feedback_snapshot)
        self.tick(request.campaign_id)
        return self.reply(command_id)

    def _dispatch(self, command):
        inputs = [item for item in inbox.pending(self.store, command["campaign_id"])
                  if item.get("manager_command_id") in {None, command["id"]}]
        with self.store.transaction():
            run = super().start(ResearchInput.model_validate(command["request"]), automatic=command["automatic"],
                command_id=command["id"], feedback_snapshot=command.get("feedback_snapshot"),
                input_ids=[item["id"] for item in inputs], dispatch=False,
                decision_refresh_id=command.get("decision_refresh_id"))
            stored = self.store.get(run["id"], "research_run")
            consumed = [self.store.get(key, "manager_input") for key in stored.get("manager_input_ids", [])]
            inbox.bind(self.store, consumed, command["id"], run["id"])
            command.update(status="dispatched", research_run_id=run["id"],
                context_revision_id=stored["context_snapshot"]["manager_context"]["revision_id"],
                input_ids=[item["id"] for item in consumed])
            self.store.put("manager_command", command, "manager.message_dispatched")
            for issue in self.store.list("manager_issue", command["campaign_id"]):
                if issue["code"] == "manager_request" and issue.get("affected") == command["id"] and issue["status"] == "pending":
                    self.store.put("manager_issue", {**issue, "status": "resolved", "resolved_at": now(),
                        "resolution_basis": "saved_request_dispatched", "research_run_id": run["id"],
                        "revision": issue.get("revision", 1) + 1}, "manager.request_recovered")
        self._thread(stored)
        return run

    def _run(self, run_id):
        super()._run(run_id)
        run = self.store.get(run_id, "research_run")
        if run["status"] in {"failed", "needs_reconciliation", "interrupted"}:
            self.workspace.memory.issue(run["campaign_id"], "manager_run",
                run.get("error") or "The manager turn needs attention.", affected=run_id)
        self.workspace.memory.sync(run["campaign_id"])
        self.tick(run["campaign_id"])

    def reconsider_finished(self):
        self.tick()

    def _queue_events(self, campaign):
        inputs = [item for item in inbox.pending(self.store, campaign["id"]) if not item.get("manager_command_id")]
        if not inputs or campaign["autonomy"] != "delegated":
            return None
        identity = "events_" + content_hash([item["id"] for item in inputs])
        refs = list(dict.fromkeys(key for item in inputs for key in item["evidence_ids"]))
        request = ResearchInput(campaign_id=campaign["id"], mode="plan", max_calls=3,
            message=f"Reconsider {len(inputs)} new campaign inputs using current guidance. Recent evidence: {', '.join(refs[-40:])}. "
                "Inspect draft readiness and completed work. Do not duplicate accepted commands or repeat blocked actions. "
                "Proceed within delegation on independent work; route unexpected issues through this campaign manager.")
        with self.store.transaction():
            accepted = self.workspace.commands.execute(Command(id=identity, campaign_id=campaign["id"], operation="research.start",
                expected_revision=campaign["version"], expected_guidance_revision=self.workspace.memory.state(campaign["id"])["guidance_revision"],
                expected_authority_hash=self.workspace.commands.authority_hash(campaign), payload=request.model_dump()), actor="manager")
            command = self.store.get(accepted["outcome"]["manager_command_id"], "manager_command")
            for item in inputs:
                item["manager_command_id"] = command["id"]
                self.store.put("manager_input", item)
        return command

    def tick(self, campaign_id=None):
        if self.workspace.shutdown_event.is_set():
            return
        with self.workspace.lock:
            campaigns = [self.store.get(campaign_id, "campaign")] if campaign_id else self.store.list("campaign")
            for campaign in campaigns:
                cid = campaign["id"]
                inbox.consume_events(self.workspace, cid)
                runs = self.store.list("research_run", cid)
                refreshes = [row for row in self.store.list("manager_command", cid)
                    if row.get("decision_refresh_id") and row["status"] in {"queued", "waiting_provider"}]
                parallel_review = any(run.get("decision_review") and run["status"] in {"running", "stopping", "needs_reconciliation"}
                    for run in runs)
                if self.workspace.discovery.active(cid) and not refreshes and not parallel_review:
                    self.workspace.discovery.tick(cid)
                    continue
                for run in runs:
                    if run["status"] == "running" and run.get("dispatch_phase") == "recovery_pending":
                        self._thread(run)
                if any(run["status"] in {"running", "stopping", "needs_reconciliation"} for run in runs):
                    continue
                queued = [row for row in self.store.list("manager_command", cid)
                          if row["status"] in {"queued", "waiting_provider"}]
                command = refreshes[0] if refreshes else queued[0] if queued else self._queue_events(campaign)
                if command is None:
                    continue
                if not research.provider_status()["configured"]:
                    if command["status"] != "waiting_provider":
                        self.store.put("manager_command", {**command, "status": "waiting_provider"}, "manager.waiting_provider")
                    reopen = any(row["code"] == "manager_provider" and row["status"] == "resolved" and
                        row.get("resolution_basis") == "provider_configuration_available" for row in self.store.list("manager_issue", cid))
                    self.workspace.memory.issue(cid, "manager_provider",
                        "Campaign requests are saved. Configure and enable the research model to continue reasoning. "
                        "Manual controls and already authorized experiments remain available.", affected="manager_" + cid, reopen=reopen)
                    continue
                for issue in self.store.list("manager_issue", cid):
                    if issue["code"] == "manager_provider" and issue["status"] == "pending":
                        self.store.put("manager_issue", {**issue, "status": "resolved", "resolved_at": now(),
                            "resolution_basis": "provider_configuration_available", "revision": issue.get("revision", 1) + 1},
                            "manager.provider_available")
                try:
                    self._dispatch(command)
                except (ValueError, KeyError) as exc:
                    self.store.put("manager_command", {**command, "status": "blocked", "error": str(exc)}, "manager.request_blocked")
                    self.workspace.memory.issue(cid, "manager_request", str(exc), affected=command["id"])
            for trial in self.store.list("trial", campaign_id):
                if trial["status"] in {"failed", "interrupted"}:
                    self.workspace.memory.issue(trial["campaign_id"], "experiment_failure",
                        trial.get("reason") or "The experiment needs attention.", affected=trial["id"])
