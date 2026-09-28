"""Project authoritative records into structured long-lived campaign memory."""
from optimization_framework.contracts.manager import CampaignContext


def assemble(workspace, campaign, state, records):
    notes = [row for kind, row in records if kind == "manager_note" and row.get("kind") in {"finding", "counterevidence"}]
    findings = [{"id": row["id"], "classification": ("counterevidence" if row["kind"] == "counterevidence" else
        "researcher_endorsement" if row.get("interpretation") == "researcher_endorsed" else
        "observation" if row.get("interpretation") == "observation" else "provisional_interpretation"),
        "text": row.get("content", row.get("claim", "")), "evidence_ids": row.get("source_ids", row.get("evidence_ids", [])),
        "study_ids": row.get("study_ids", [row["study_id"]] if row.get("study_id") else []), "limitations": row.get("limitations", [])} for row in notes]
    # Observations retain their own classification; a completed numerical job is
    # not automatically an endorsed conclusion about a problem family.
    for kind, row in records:
        if kind != "trial" or row["status"] not in {"completed", "budget_exhausted", "failed", "interrupted"}:
            continue
        result = row.get("result") or row.get("progress") or {}
        if result.get("best_objective") is None:
            continue
        findings.append({"id": "observation_" + row["id"], "classification": "observation",
            "text": f"{row['algorithm']}: best observed objective {result['best_objective']}; experiment status {row['status']}.",
            "evidence_ids": [row["id"]], "study_ids": [row["study_id"]] if row.get("study_id") else [],
            "limitations": ["Applies to the recorded problem instance, procedure and allocation."]})
    active_ids = {campaign.get("active_study_id")}
    active_ids.update(row.get("study_id") for kind, row in records if kind == "trial" and row["status"] in {"queued", "running", "paused", "interrupted"})
    return CampaignContext(campaign_id=campaign["id"], revision=state["revision"] + 1,
        charter_version=campaign["version"], guidance_revision=state["guidance_revision"], objective=campaign["objective"],
        narrative_guidance=state["guidance"], active_studies=[{key: row[key] for key in ("id", "goal", "scope")}
            for kind, row in records if kind == "study" and row["id"] in active_ids],
        delegation={"autonomy": campaign["autonomy"], "per_experiment_seconds": campaign["delegated_trial_seconds"],
            "authority_hash": workspace.commands.authority_hash(campaign)},
        resources={"compute_cap_seconds": campaign["compute_budget_seconds"],
            "compute_committed_seconds": workspace.allocated_seconds(campaign["id"]),
            "validation_reserve_seconds": campaign["validation_reserve_seconds"],
            "implementation_cap_seconds": campaign.get("implementation_compute_budget_seconds", 0),
            "implementation_committed_seconds": workspace.implementations.compute_committed(campaign["id"]),
            "api_cap_usd": campaign["llm_budget_usd"]},
        findings=[row for row in findings if row["classification"] != "counterevidence"],
        counterevidence=[row for row in findings if row["classification"] == "counterevidence"],
        reuse_decisions=[{key: row[key] for key in ("id", "asset_id", "study_id", "decision", "intended_use", "rationale")}
            for kind, row in records if kind == "reuse_decision"],
        pending_issues=[{"id": row["id"], "code": row["code"], "message": row["message"],
            "affected_ids": [row["affected"]] if row.get("affected") else []}
            for kind, row in records if kind == "manager_issue" and row["status"] == "pending"],
        next_actions=[{"id": row["id"], "title": row.get("title", (row.get("request") or {}).get("message", "Pending campaign action")), "status": row["status"],
            "evidence_ids": row.get("evidence_ids", [])} for kind, row in records if kind in {"action", "decision", "manager_command"}
            and row.get("status") in {"pending", "proposed", "blocked", "needs_reconsideration", "queued", "waiting_provider"}],
        discovery={"sessions": [{key: row.get(key) for key in ("id", "status", "problem_task_id", "round", "control_revision", "policy")}
                        for kind, row in records if kind == "discovery_session"],
            "active_tasks": [{key: row.get(key) for key in ("id", "session_id", "brief", "status", "wait_reason", "artifact_ids")}
                for kind, row in records if kind == "discovery_task" and row["status"] not in {"completed", "failed", "cancelled", "superseded"}],
            "candidate_ids": [row["id"] for kind, row in records if kind == "discovery_candidate"],
            "assessment_ids": [row["id"] for kind, row in records if kind == "discovery_assessment"],
            "recent_artifacts": [{key: row.get(key) for key in ("id", "kind", "title", "task_id", "stale", "limitations")}
                for kind, row in records if kind == "discovery_artifact"][-30:],
            "history_location": "discovery/context.json and discovery/records.jsonl"},
        source_ids=[row["id"] for _, row in records])


def import_edit(workspace, campaign_id, values):
    document = values.document
    if document.campaign_id != campaign_id:
        raise ValueError("The context document belongs to another campaign")
    state = workspace.memory.state(campaign_id)
    if state["revision"] != document.revision:
        raise ValueError("Campaign memory changed; inspect the latest revision before importing")
    current = workspace.store.get(state["context_id"], "context_revision")
    if not current.get("structured"):
        raise ValueError("Export the current structured context before importing edits")
    raw = document.model_dump(mode="json")
    raw["narrative_guidance"] = current["structured"]["narrative_guidance"]
    if raw != current["structured"]:
        raise ValueError("Edit narrative_guidance in this document; authority, evidence and work change through their owning commands")
    return workspace.memory.edit(campaign_id, document.narrative_guidance, document.revision, values.reason)
