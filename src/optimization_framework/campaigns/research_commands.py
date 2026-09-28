"""Researcher controls commit intent before threads or subordinate commands run."""
from copy import deepcopy

from optimization_framework.contracts.base import content_hash
from optimization_framework.contracts.commands import Command, ResearchControlInput, DecisionResolveInput
from optimization_framework.storage.sqlite import now


def manager(workspace):
    from .manager import CampaignManager
    return getattr(workspace, "manager", None) or CampaignManager(workspace)


def control(workspace, command):
    values = ResearchControlInput(**command.payload)
    run = workspace.commands._target(command, "run_id", "research_run")
    if run.get("control_revision", 0) != values.expected_control_revision:
        raise ValueError("Research controls changed; refresh before submitting another control")
    if run.get("discovery_task_id"):
        if values.action != "stop":
            raise ValueError("Discovery tasks resume through their session; uncertain calls require manager reconciliation")
        task = workspace.store.get(run["discovery_task_id"], "discovery_task")
        task.update(status="cancelled", wait_reason=None, finished_at=now())
        workspace.store.put("discovery_task", task, "discovery.task_cancelled")
        run.update(status="stopping" if run["status"] == "running" else "stopped",
                   control_revision=values.expected_control_revision + 1)
        workspace.store.put("research_run", run, "research.control_requested")
        return {"run_id": run["id"], "research_run": manager(workspace).public_run(run)}
    if values.action == "stop":
        if run["status"] == "running":
            run["status"] = "stopping"
    else:
        if run["status"] != "interrupted" or not run.get("checkpoint"):
            raise ValueError("Only an interrupted discussion with a checkpoint can be resumed")
        campaign = workspace.store.get(command.campaign_id, "campaign")
        if campaign["version"] != run["charter_version"]:
            raise ValueError("Charter changed; start a new discussion with current evidence")
        if workspace.memory.state(command.campaign_id)["guidance_revision"] != run.get("guidance_revision", 0):
            raise ValueError("Researcher guidance changed; start a new discussion with current evidence")
        if (run.get("usage") or {}).get("pending_reservation"):
            raise ValueError("Reconcile the uncertain model call before starting another attempt")
        if any(r["status"] in {"running", "stopping"} for r in workspace.store.list("research_run", command.campaign_id)):
            raise ValueError("Another discussion is active")
        run["status"] = "running"
        run["dispatch_phase"] = "queued"
        effect = {"id": "research_resume_" + command.id, "campaign_id": command.campaign_id,
                  "kind": "research_resume", "run_id": run["id"],
                  "control_revision": values.expected_control_revision + 1, "status": "pending", "created_at": now()}
        workspace.store.put("outbox", effect, "effect.queued")
    run["control_revision"] = values.expected_control_revision + 1
    workspace.store.put("research_run", run, "research.control_requested")
    return {"run_id": run["id"], "research_run": manager(workspace).public_run(run)}


def deliver_resume(workspace, effect):
    with workspace.lock:
        run = workspace.store.get(effect["run_id"], "research_run")
        if run.get("discovery_task_id"):
            raise ValueError("Discovery tasks cannot be dispatched through the legacy discussion runner")
        if run.get("last_resume_effect") == effect["id"]:
            if run["status"] == "running" and run.get("dispatch_phase") in {"queued", "recovery_pending"}:
                manager(workspace)._thread(run)
            return
        if run["status"] != "running" or run.get("control_revision", 0) != effect["control_revision"]:
            return  # A newer stop or restart reconciliation supersedes this delivery.
        run["last_resume_effect"] = effect["id"]
        workspace.store.put("research_run", run, "research.resume_dispatched")
        manager(workspace)._thread(run)


def resolve(workspace, command):
    values = DecisionResolveInput(**command.payload)
    decision = workspace.commands._target(command, "decision_id", "decision")
    if decision.get("resolution_revision", 0) != values.expected_resolution_revision:
        raise ValueError("Decision resolution changed; refresh before submitting another choice")
    if decision["status"] != "pending":
        raise ValueError("This decision has already been resolved or has a pending delivery")
    if values.choice not in {option["id"] for option in decision["options"]} and values.choice != "custom":
        raise ValueError("Select a listed option or provide a custom direction")
    campaign = workspace.store.get(command.campaign_id, "campaign")
    effect = None
    outcome = None
    if values.choice == "close_reserved" and decision.get("research_run_id"):
        run = workspace.store.get(decision["research_run_id"], "research_run")
        if run["campaign_id"] != command.campaign_id or run["status"] != "needs_reconciliation":
            raise ValueError("This provider call is not awaiting reconciliation in this campaign")
        run.update(status="closed_uncertain", finished_at=now(), control_revision=run.get("control_revision", 0) + 1)
        workspace.store.put("research_run", run, "research.closed_uncertain")
        if run.get("discovery_task_id"):
            task = workspace.store.get(run["discovery_task_id"], "discovery_task")
            task.update(status="failed", wait_reason=None, finished_at=now(), error="Researcher closed an uncertain call; its resource reservation remains charged")
            workspace.store.put("discovery_task", task, "discovery.uncertainty_closed")
        outcome = {"research_run_id": run["id"], "reserved_cost_retained": True}
    elif values.choice == "accept" and decision.get("action_id"):
        if decision["charter_version"] != campaign["version"]:
            raise ValueError("This recommendation used an older charter; request a current proposal")
        action = workspace.store.get(decision["action_id"], "action")
        if action["campaign_id"] != command.campaign_id:
            raise ValueError("The proposed action belongs to another campaign")
        if action.get("guidance_revision", workspace.memory.state(command.campaign_id)["guidance_revision"]) != workspace.memory.state(command.campaign_id)["guidance_revision"]:
            raise ValueError("Researcher guidance changed; ask the manager to update this action")
        effect = {"action": deepcopy(action)}
    elif values.choice == "0" and decision.get("trial_id") and decision.get("incremental_solver_calls"):
        trial = workspace.store.get(decision["trial_id"], "trial")
        if trial["campaign_id"] != command.campaign_id:
            raise ValueError("The experiment belongs to another campaign")
        seconds = decision.get("estimated_seconds")
        if seconds is None:
            raise ValueError("No reliable runtime estimate exists; extend this trial with an explicit time budget")
        if decision["charter_version"] != campaign["version"]:
            raise ValueError("This recommendation used an older charter; request a current proposal")
        child = Command(id="decision_action_" + content_hash(command.id)[:32], campaign_id=command.campaign_id,
            operation="trial.control", expected_revision=campaign["version"],
            expected_guidance_revision=workspace.memory.state(command.campaign_id)["guidance_revision"],
            expected_authority_hash=workspace.commands.authority_hash(campaign),
            payload={"trial_id": trial["id"], "action": "extend", "expected_control_revision": trial["control_revision"],
                     "max_steps": trial["max_steps"] + decision["incremental_solver_calls"],
                     "wall_seconds": trial["wall_seconds"] + max(5, seconds)})
        effect = {"child_command": child.model_dump(mode="json")}
    memory = workspace.memory.state(command.campaign_id)
    memory["guidance_revision"] += 1
    workspace.store.put("manager_state", memory)
    workspace.store.put("outbox", {"id": "decision_projection_" + command.id, "campaign_id": command.campaign_id,
        "kind": "manager_context_projection", "status": "pending", "created_at": now()}, "effect.queued")
    if effect is not None:
        effect["approval"] = {"command_id": command.id, "guidance_revision": memory["guidance_revision"],
                              "authority_hash": workspace.commands.authority_hash(campaign)}
        if "child_command" in effect:
            effect["child_command"]["expected_guidance_revision"] = memory["guidance_revision"]
    decision.pop("delivery_error", None)
    decision.update(resolution_revision=values.expected_resolution_revision + 1, resolution_command_id=command.id)
    if effect is None:
        decision = manager(workspace)._finish_decision(decision, values.choice, values.comment, outcome)
    else:
        decision.update(status="executing", choice=values.choice, comment=values.comment)
        workspace.store.put("decision", decision, "decision.executing")
        effect.update(id="decision_" + command.id, campaign_id=command.campaign_id, kind="decision_resolution",
                      decision_id=decision["id"], resolution_command_id=command.id,
                      status="pending", created_at=now())
        workspace.store.put("outbox", effect, "effect.queued")
    return {"decision_id": decision["id"], "decision": decision,
            **({"effect_id": effect["id"]} if effect is not None else {})}


def deliver_resolution(workspace, effect):
    coordinator = manager(workspace)
    with workspace.lock:
        decision = workspace.store.get(effect["decision_id"], "decision")
        if decision.get("resolution_command_id") != effect["resolution_command_id"]:
            return
        if decision["status"] == "resolved":
            return
    # Child commands have their own stable receipt and original authority. A
    # lost acknowledgement reconciles that receipt before checking newer intent.
    if "child_command" in effect:
        outcome = workspace.commands.execute(effect["child_command"], actor="researcher")["outcome"]
    else:
        outcome = coordinator.execute_action(effect["action"], actor="researcher", approval=effect["approval"])
    with workspace.lock, workspace.store.transaction():
        decision = workspace.store.get(effect["decision_id"], "decision")
        if decision["status"] == "resolved":
            return
        if "action" in effect:
            action = workspace.store.get(effect["action"]["id"], "action")
            workspace.store.put("action", {**action, "status": "accepted", "outcome": outcome})
        coordinator._finish_decision(decision, decision["choice"], decision["comment"], outcome)


def failed_resolution(workspace, effect, error):
    """A definitively rejected child needs a fresh user choice, not a retry loop."""
    with workspace.lock, workspace.store.transaction():
        decision = workspace.store.get(effect["decision_id"], "decision")
        if decision.get("resolution_command_id") == effect["resolution_command_id"] and decision["status"] == "executing":
            decision.update(status="pending", delivery_error=str(error))
            workspace.store.put("decision", decision, "decision.action_failed")
        effect.update(status="failed", error=str(error), finished_at=now())
        workspace.store.put("outbox", effect, "effect.failed")
