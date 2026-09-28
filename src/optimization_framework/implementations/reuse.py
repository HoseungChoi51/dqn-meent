"""Explicit decisions about using a library executable for a frozen study."""
from optimization_framework.contracts.assets import Asset, ReuseDecision
from optimization_framework.contracts.base import content_hash
from optimization_framework.contracts.experiments import task_in_study
from optimization_framework.implementations.models import digest
from optimization_framework.storage.sqlite import now


def assess(bridge, campaign_id, version, *, study_id=None, hypothesis_id=None, task_id=None):
    workspace, store = bridge.workspace, bridge.store
    campaign = store.get(campaign_id, "campaign")
    study = store.get(study_id or campaign["active_study_id"], "study")
    if study["campaign_id"] != campaign_id:
        raise ValueError("The intended study belongs to another campaign")
    try:
        if version.get("kind") == "evaluator":
            from optimization_framework.implementations.evaluator_validation import current
            if not task_id:
                raise ValueError("Select the problem that would use this evaluator")
            task = store.get(task_id, "task")
            if task["campaign_id"] != campaign_id or task.get("archived") or not task_in_study(workspace.evaluators.task_view(task), study):
                raise ValueError("Select a current problem within the intended study")
            if version["spec"]["manifest"] != task.get("evaluator_manifest"):
                raise ValueError("This evaluator differs from the problem's exact frozen declaration")
            binding = workspace.evaluators.binding(task)
            if binding and binding["version_id"] != version["id"]:
                raise ValueError("The problem already binds a different evaluator; define a linked problem and study")
            if not current(version, numerical=False):
                raise ValueError(version.get("revocation_reason") or version["validation_report"].get("error") or "Current executable contract evidence is required")
            tasks = [task_id]
            reason = ("Exact evaluator declaration and current numerical evidence match" if current(version)
                      else "Exact declaration and contract checks match; experimentation still needs numerical evidence or an eligible waiver")
        else:
            if not hypothesis_id:
                raise ValueError("Select the idea that would use this optimizer")
            hypothesis = store.get(hypothesis_id, "hypothesis")
            if hypothesis["campaign_id"] != campaign_id or hypothesis["status"] in {"archived", "finalist"}:
                raise ValueError("Select a current idea that is not a frozen finalist")
            if version["status"] != "validated" or not bridge.validation_current(version):
                raise ValueError(version.get("revocation_reason") or version["validation_report"].get("error") or "Current optimizer correctness evidence is required")
            tasks, failures = [], []
            for task in workspace.current_tasks(campaign_id):
                if not task_in_study(task, study):
                    continue
                try:
                    bridge.compatible(version, task, hypothesis.get("algorithm_config", {}))
                    tasks.append(task["id"])
                except ValueError as exc:
                    failures.append(str(exc))
            if not tasks:
                raise ValueError("; ".join(sorted(set(failures))) or "No compatible problem in the intended study")
            reason = "Current optimizer correctness evidence matches the listed study problems"
        return {"eligible": True, "reason": reason, "task_ids": tasks, "validation_report_id": version["validation_report"]["id"]}
    except (ValueError, KeyError) as exc:
        return {"eligible": False, "reason": str(exc), "task_ids": [], "validation_report_id": version.get("validation_report", {}).get("id")}


def decide(bridge, *, identity, campaign_id, study_id, version_id, decision, rationale, authority,
           hypothesis_id=None, task_id=None, expected_context=None):
    workspace, store = bridge.workspace, bridge.store
    request = {"campaign_id": campaign_id, "study_id": study_id, "version_id": version_id, "decision": decision,
        "rationale": rationale, "authority": authority, "hypothesis_id": hypothesis_id, "task_id": task_id}
    fingerprint = content_hash(request)
    try:
        previous = store.get(identity, "reuse_decision")
    except KeyError:
        previous = None
    if previous:
        if previous["consequences"]["request_digest"] != fingerprint:
            raise ValueError("Reuse decision identity already belongs to another request")
        return previous
    bundle = bridge.client.artifact(version_id) if decision == "reuse" else None
    version = bundle["version"] if bundle else bridge.client.version(version_id)
    if version["id"] != version_id:
        raise ValueError("Library returned another executable version")
    if bundle and digest(bundle["artifact"]) != version["artifact_digest"]:
        raise ValueError("Library executable content identity changed")
    if version["spec"].get("evaluator_version_id"):
        bridge.cache_version(bridge.client.version(version["spec"]["evaluator_version_id"]))
    with workspace.lock, store.transaction():
        workspace.check_manager_context(campaign_id, expected_context)
        kind, target = ("task", task_id) if version.get("kind") == "evaluator" else ("hypothesis", hypothesis_id)
        if not target or store.get(target, kind)["campaign_id"] != campaign_id:
            raise ValueError("Select the matching executable target in this campaign")
        assessment = assess(bridge, campaign_id, version, study_id=study_id, hypothesis_id=hypothesis_id, task_id=task_id)
        if decision == "reuse" and not assessment["eligible"]:
            raise ValueError(assessment["reason"])
        bridge.cache_version(version)
        if bundle:
            asset = bridge.cost_asset(bundle)
        else:
            snapshot = {key: version.get(key) for key in ("id", "kind", "artifact_digest", "runtime_digest", "status")}
            snapshot["validation_report_id"] = version["validation_report"]["id"]
            asset = workspace.assets.publish(Asset(id="executable_candidate_" + content_hash(snapshot), campaign_id="implementation_library",
                kind="implementation", title=version["name"], producer_id=version_id,
                payload={"implementation_version_id": version_id, "candidate_snapshot": snapshot},
                applicability={"executable_kind": version.get("kind", "optimizer")},
                cost_provenance="unknown", exposure_status="unknown", exposed_instance_ids=version.get("exposed_conditions", []),
                authority="implementation_library", created_at=version["created_at"]))
        result = workspace.assets.decide(ReuseDecision(id=identity, campaign_id=campaign_id, study_id=study_id,
            asset_id=asset["id"], decision=decision, intended_use="procedure", rationale=rationale,
            authority=authority, created_at=now(), consequences={"request_digest": fingerprint, "version_id": version_id,
                "artifact_digest": version["artifact_digest"], "runtime_digest": version["runtime_digest"],
                "hypothesis_id": hypothesis_id, "task_id": task_id, "applicability": assessment,
                "cost_rule": "Full upstream implementation and validation work is attributed when this executable is used"}))
        if decision == "reuse":
            if kind == "task":
                workspace.evaluators.attach(target, version_id, rationale=rationale, authority=authority, _bundle=bundle)
            else:
                bridge.attach(target, version_id, _bundle=bundle)
        bridge._exposure(campaign_id, version)
        return result
