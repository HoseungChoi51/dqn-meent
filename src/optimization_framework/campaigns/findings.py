"""Findings remain scoped claims with provenance, never unqualified memory."""
from optimization_framework.contracts.assets import Asset
from optimization_framework.contracts.commands import FindingInput
from optimization_framework.storage.sqlite import now


def record(workspace, command, actor):
    store = workspace.store
    values = FindingInput(**command.payload)
    if actor == "manager" and values.interpretation == "researcher_endorsed":
        raise ValueError("Only the researcher can endorse a conclusion")
    campaign = store.get(command.campaign_id, "campaign")
    visible = {item["id"] for _, item in workspace.memory._records(command.campaign_id)}
    visible.update(item["asset_id"] for item in workspace.assets.manager_references(command.campaign_id, campaign.get("active_study_id")) if "payload" in item)
    if set([*values.source_ids, *values.counterevidence_for]) - visible:
        raise ValueError("Finding cites evidence outside the visible context; declare historical reference access first")
    sources = [store.get(identity) for identity in dict.fromkeys([*values.source_ids, *values.counterevidence_for])]
    for identity in values.study_ids:
        if store.get(identity, "study")["campaign_id"] != command.campaign_id:
            raise ValueError("Finding scope belongs to another campaign's study")
    for identity in values.counterevidence_for:
        entry = store.get_entry(identity)
        if entry["kind"] not in {"asset", "manager_note"} or entry["data"].get("kind") not in {"finding", "counterevidence"}:
            raise ValueError("Counterevidence must qualify a recorded finding")
    finding = {"id": "finding_" + command.id, "campaign_id": command.campaign_id,
        "kind": "counterevidence" if values.interpretation == "counterevidence" else "finding",
        **values.model_dump(exclude={"schema_version", "publish"}), "author": actor, "created_at": now()}
    if values.publish:
        finding["asset_id"] = "asset_" + finding["id"]
    store.put_immutable("manager_note", finding, "finding.recorded")
    if values.publish:
        dependencies, exposed = set(), set()
        evidence_covered = True
        for source in sources:
            entry = store.get_entry(source["id"])
            candidates = ([source["id"]] if entry["kind"] == "asset" else source.get("latest_output_asset_ids", [])
                + source.get("research_cost_asset_ids", []) + ([source["asset_id"]] if source.get("asset_id") else []))
            evidence_covered &= bool(candidates)
            dependencies.update(candidates)
            if source.get("problem"):
                exposed.add(source["problem"]["scientific_identity"])
        if actor == "manager" and command.id.startswith("action_"):
            try:
                action = store.get(command.id.removeprefix("action_"), "action")
                run = store.get(action["research_run_id"], "research_run")
            except KeyError:
                evidence_covered = False
            else:
                from optimization_framework.assets.service_costs import capture_research
                dependencies.add(capture_research(workspace, run)["id"])
        assets = [store.get(identity, "asset") for identity in sorted(dependencies)]
        for asset in assets:
            workspace.assets.check_export_exposure(asset)
            exposed.update(asset["exposed_instance_ids"])
        workspace.assets.publish(Asset(id=finding["asset_id"], campaign_id=command.campaign_id, kind="finding",
            title=values.content[:160], payload={"finding_id": finding["id"], "content": values.content,
                "classification": values.interpretation, "evidence_ids": values.source_ids,
                "problem_scope": values.problem_scope, "study_ids": values.study_ids,
                "limitations": values.limitations, "counterevidence_for": values.counterevidence_for},
            applicability={"problem_scope": values.problem_scope, "study_ids": values.study_ids, "limitations": values.limitations},
            producer_id=finding["id"], dependency_ids=sorted(dependencies), exposed_instance_ids=sorted(exposed),
            exposure_status="known" if assets and all(row["exposure_status"] == "known" for row in assets) else "unknown",
            cost_provenance="complete" if evidence_covered and all(row["cost_provenance"] == "complete" for row in assets) else "partial",
            authority=actor, created_at=finding["created_at"]))
    return {"finding_id": finding["id"], **({"asset_id": finding["asset_id"]} if values.publish else {})}
