"""Project conditional cohorts into the existing confirmation/report boundary."""
from optimization_framework.contracts.base import content_hash
from optimization_framework.execution.studies import StudyExecutionService


def view(store, protocol):
    design = store.get(protocol["design_id"], "confirmation_design")
    state = StudyExecutionService(store=store).assess(design["execution_id"])
    bindings = {row["slot_id"]: row for row in state["bindings"]}
    nomination = store.get(state["nomination_id"], "nomination") if state["nomination_id"] else None
    cells, experiments, references, methods = [], [], [], {}
    seen = set()
    for row in state["cells"]:
        if row["scope"] == "development" or row["canonical_cell_id"] in seen:
            continue
        seen.add(row["canonical_cell_id"])
        method_id = row["method_id"] or "unresolved:"+row["slot_id"]
        method = bindings.get(row["slot_id"], {}).get("logical_method", {"algorithm": "unresolved"})
        evidence = row["evidence"] or {"trial_id": None, "method_id": method_id, "method": method, "logical_method": method,
            "problem": row["problem"], "instance_digest": row["instance_digest"], "seed": row["seed"],
            "status": row["status"], "result": {}, "validation": [], "evidence_complete": False,
            "amendments": [], "input_assets": []}
        if row.get("reference_role"):
            evidence = {**evidence, "reference_role": row["reference_role"]}
        methods[method_id] = evidence["method"]
        (references if row["scope"] == "reference" else experiments).append(evidence)
        cells.append({"cell_id": row["id"], "scope": row["scope"], "slot_id": row["slot_id"], "method_id": method_id,
            "instance_digest": row["instance_digest"], "instance_name": store.get(row["task_id"], "task")["name"],
            "seed": row["seed"], "trial_id": row["trial_id"], "task_id": row["task_id"], "status": row["status"],
            "waiting_for": row["waiting_for"], "scientific_complete": row["scientific_complete"],
            "evidence_complete": row["evidence_complete"], "best_objective": evidence["result"].get("best_objective"),
            "objective": row["problem"]["primary_objective"], "required_validation": [{**check, "passed": check["measured_pass"]} for check in evidence["validation"]],
            "diagnostics": evidence.get("diagnostics", {}), "executable_evidence": evidence.get("executable_evidence"),
            "experiment_spec_hash": evidence.get("experiment_spec_hash"),
            "result_digest": evidence.get("result_digest")})
    # Reassessment can withdraw support, but cannot replace the original
    # nomination with a method that used later development observations.
    nomination_review = None
    if nomination and nomination["selected_method_ids"]:
        from optimization_framework.analysis.studies import experiment_evidence
        from optimization_framework.analysis.rules import evaluate
        rows = []
        for frozen in nomination["evidence"]["experiments"]:
            trial = store.get(frozen["trial_id"], "trial")
            current = experiment_evidence(store, {**trial, "result": frozen["result"], "progress": frozen["result"], "status": frozen["status"]})
            rows.append({**frozen, "validation": current["validation"], "executable_evidence": current["executable_evidence"],
                "evidence_complete": frozen["evidence_complete"] and current["evidence_complete"]})
        reviewed = {**nomination["evidence"], "experiments": rows}
        evidence_hash = content_hash(reviewed)
        cache_id = "nomination_review_"+content_hash([nomination["id"], nomination["rule"], evidence_hash])
        try:
            selected = store.get(cache_id, "nomination_reassessment")["selection"]
        except KeyError:
            selected = evaluate(nomination["rule"], reviewed, store=store)
            store.put_immutable("nomination_reassessment", {"id": cache_id, "campaign_id": protocol["campaign_id"],
                "nomination_id": nomination["id"], "rule": nomination["rule"], "evidence_hash": evidence_hash, "selection": selected})
        nomination_review = {"evidence_hash": evidence_hash, "evidence": reviewed, "selection": selected,
            "supported": selected.get("selected_method_ids") == nomination["selected_method_ids"]}
    complete = state["complete"] and bool(nomination_review and nomination_review["supported"])
    analysis_evidence = {"protocol_hash": protocol["content_hash"], "design_hash": design["content_hash"],
        "roster_complete": complete, "experiments": experiments, "references": references,
        "nomination": nomination, "nomination_review": nomination_review}
    releases = [row for row in store.list("confirmation_release", protocol["campaign_id"]) if row["protocol_id"] == protocol["id"]]
    reports = [row for row in store.list("confirmation_report", protocol["campaign_id"]) if row["protocol_id"] == protocol["id"]]
    return {"protocol_id": protocol["id"], "campaign_id": protocol["campaign_id"], "study_id": protocol["study_id"],
        "kind": protocol["kind"], "design_id": design["id"], "execution_id": design["execution_id"],
        "cells": cells, "complete": complete, "methods": methods, "analysis_evidence": analysis_evidence,
        "selection_rule": protocol["selection_rule"], "analysis_rule": protocol.get("analysis"), "required_recipes": [],
        "nomination_id": nomination["id"] if nomination else None, "status": "complete" if complete else "incomplete",
        "release": {key: releases[-1][key] for key in ("id", "outcome", "authority", "rationale", "created_at")} if releases else None,
        "report": reports[-1] if reports else None, "qualification_only": state["qualification_only"],
        "interpretation": "Reduced software qualification; no production replication claim." if state["qualification_only"] else
            "The frozen analysis rule determines the conclusion for the predeclared cohort after release."}
