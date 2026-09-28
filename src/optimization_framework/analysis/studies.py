"""Freeze the actual evidence behind method selection and scientific reports."""
from optimization_framework.assets.catalog import AssetCatalog
from optimization_framework.contracts.base import content_hash
from optimization_framework.contracts.problems import ProblemInstance
from optimization_framework.evaluation.confirmation import method_definition, resolved_method_definition
from optimization_framework.evaluation.diagnostics import assessment as diagnostics
from optimization_framework.evaluation.executables import assessment as executable_assessment
from optimization_framework.evaluation.policy import ValidationService
from optimization_framework.storage.sqlite import now

from .rules import evaluate


def experiment_evidence(store, trial, *, cutoff_at=None):
    if trial.get("recipe") or trial.get("diagnostic_grant_id") or not trial.get("experiment_spec_hash"):
        raise ValueError("Study selection requires a versioned optimization experiment, not a diagnostic job")
    spec = store.get(trial["experiment_spec_id"], "experiment_spec")
    if content_hash({key: value for key, value in spec.items() if key != "content_hash"}) != trial["experiment_spec_hash"]:
        raise ValueError("The experiment's frozen procedure no longer matches its recorded identity")
    result = trial.get("result") or trial.get("progress") or {}
    study = store.get(trial["study_id"], "study")
    policy = study.get("validation_policy", {})
    checks = []
    for requirement in store.list("validation_requirement", trial["campaign_id"]):
        if requirement["scope"].get("parent_trial_id") != trial["id"]:
            continue
        subject = store.get(requirement["subject_id"])
        if requirement["kind"] != "evaluator_correctness" and subject.get("payload", {}).get("candidate") != result.get("best_candidate", result.get("best_design")):
            continue
        assessment = ValidationService(store).assess(requirement["id"], cutoff_at=cutoff_at)
        checks.append({"requirement_id": requirement["id"], "subject_id": requirement["subject_id"],
            "subject_digest": requirement["subject_digest"], "recipe_id": requirement["recipe_id"],
            "parameters": requirement["scope"].get("parameters", {}), "measured_pass": assessment["measured_pass"],
            "status": assessment["status"], "results": assessment["results"]})
    required = all(any(check["recipe_id"] == recipe and check["measured_pass"] and
        (policy.get("required_recipe_parameters", {}).get(recipe) is None or
         check["parameters"] == policy["required_recipe_parameters"][recipe]) for check in checks)
        for recipe in policy.get("required_recipes", []))
    diagnostic = diagnostics(store, trial)
    executable = executable_assessment(store, trial, cutoff_at=cutoff_at)
    timely = cutoff_at is None or (store.committed_at(trial["id"], "trial.evidence_cataloged") or float("inf")) <= cutoff_at
    if cutoff_at is not None:
        timely &= all((store.committed_at(child["id"], "trial.evidence_cataloged") or float("inf")) <= cutoff_at for child in diagnostic["jobs"])
    assets = trial.get("latest_output_asset_ids", [])
    cost_assets = set(assets)
    # The evidence used for selection includes the declared diagnostics and
    # measured checks. Their snapshots already point to parent prefixes; the
    # catalog's interval union charges those shared prefixes only once.
    evidence_ids = {row["id"] for row in diagnostic["jobs"]}
    evidence_ids.update(identity for check in checks for result in check["results"] for identity in result["evidence_ids"])
    for identity in sorted(evidence_ids):
        try:
            asset = store.get(identity, "asset")
            cost_assets.add(asset["id"])
        except KeyError:
            try:
                producer = store.get(identity, "trial")
                cost_assets.update(producer.get("latest_output_asset_ids", []))
            except KeyError:
                pass  # Other evidence records carry no independently measured execution cost.
    full = AssetCatalog(store).attributed_costs(sorted(cost_assets)) if assets else None
    method = method_definition(trial)
    inputs = []
    for identity in trial.get("initial_assets", []):
        asset = store.get(identity, "asset")
        try:
            producer = store.get(asset["producer_id"], "trial")
            producer = {key: producer.get(key) for key in ("id", "seed", "algorithm", "algorithm_config", "problem", "completion")}
        except (KeyError, TypeError):
            producer = None
        inputs.append({"asset": asset, "producer": producer, "contributions": AssetCatalog(store).contributions([identity])})
    return {"trial_id": trial["id"], "study_id": trial["study_id"], "method_id": content_hash(method),
        "method": resolved_method_definition(trial), "logical_method": method,
        "instance_digest": ProblemInstance(**trial["problem"]).digest(), "problem": trial["problem"], "seed": trial["seed"],
        "status": trial["status"], "result": result, "result_digest": content_hash(result),
        "experiment_spec_hash": trial["experiment_spec_hash"], "validation": checks, "diagnostics": diagnostic,
        "executable_evidence": executable,
        "evidence_complete": timely and trial["status"] == "completed" and bool(result.get("scientific_complete")) and required and diagnostic["complete"]
            and trial.get("asset_capture_attempt", -1) == trial.get("attempt", 0) and executable["supported"],
        "full_cost": full, "full_worker_seconds": full["quantities"]["worker_seconds"]["total"] if full else None,
        "asset_ids": assets, "cost_asset_ids": sorted(cost_assets), "input_assets": inputs,
        "amendments": [item for item in store.list("budget_amendment", trial["campaign_id"]) if item["experiment_id"] == trial["id"]]}


def selection_assessment(store, study_id):
    study = store.get(study_id, "study")
    if study["scope"] != "exploratory" or not study.get("selection"):
        raise ValueError("Selection needs an exploratory study with a frozen analysis rule")
    trials = [trial for trial in store.list("trial", study["campaign_id"]) if trial.get("study_id") == study_id
              and not trial.get("recipe") and not trial.get("diagnostic_grant_id")]
    if any(trial.get("task_split") in {"test", "heldout", "confirmation"} or trial.get("locked") for trial in trials):
        raise ValueError("Protected confirmation evidence cannot select a development method")
    evidence = {"schema_version": 1, "study_id": study_id, "study_hash": study["content_hash"],
                "experiments": [experiment_evidence(store, trial) for trial in sorted(trials, key=lambda trial: trial["id"])]}
    result = evaluate(study["selection"], evidence, store=store)
    return {"study_id": study_id, "rule": study["selection"], "ready": bool(result.get("selected_method_ids")),
            "evidence_hash": content_hash(evidence), "evidence": evidence, "result": result}


def nominate(workspace, study_id, *, authority="researcher", expected_evidence_hash=None):
    with workspace.lock, workspace.store.transaction():
        store = workspace.store
        study = store.get(study_id, "study")
        if study["scope"] != "exploratory" or not study.get("selection"):
            raise ValueError("Selection needs an exploratory study with a frozen analysis rule")
        identity = "nomination_" + study_id
        try:
            return store.get(identity, "nomination")
        except KeyError:
            pass
        if any(row["study_ids"].get("development") == study_id for row in store.list("confirmation_design", study["campaign_id"])):
            raise ValueError("This template nominates at its frozen transition; manual nomination cannot bypass pending evidence or its cutoff")
        assessment = selection_assessment(store, study_id)
        if expected_evidence_hash is not None and expected_evidence_hash != assessment["evidence_hash"]:
            raise ValueError("Development evidence changed; review the current selection before freezing it")
        evidence, result = assessment["evidence"], assessment["result"]
        selected = result.get("selected_method_ids", [])
        if not selected:
            raise ValueError("No method can be nominated from the declared evidence: " + result.get("reason", "complete eligible seed results are required"))
        methods, prototypes = {}, {}
        for row in evidence["experiments"]:
            if row["method_id"] in selected and row["evidence_complete"]:
                methods[row["method_id"]] = row.get("logical_method", row["method"])
                prototypes.setdefault(row["method_id"], row["trial_id"])
        if set(methods) != set(selected):
            raise ValueError("The selection rule nominated a method without complete source evidence")
        record = {"schema_version": 1, "id": identity, "campaign_id": study["campaign_id"], "study_id": study_id,
            "rule": study["selection"], "evidence_hash": content_hash(evidence), "evidence": evidence, "result": result,
            "selected_method_ids": selected, "methods": methods, "prototypes": prototypes,
            "authority": authority, "created_at": now()}
        return store.put_immutable("nomination", record, "study.nominated")


def confirmation_evidence(store, protocol, assessment):
    def recheck(frozen):
        # New observations cannot enter a previously selected data set. Only
        # later validation about its frozen subjects can change its support.
        trial = store.get(frozen["trial_id"], "trial")
        current = experiment_evidence(store, {**trial, "result": frozen["result"], "progress": frozen["result"], "status": frozen["status"]})
        return {**frozen, "validation": current["validation"], "executable_evidence": current["executable_evidence"],
                "evidence_complete": frozen["evidence_complete"] and current["evidence_complete"]}

    experiments = []
    for cell in assessment["cells"]:
        if cell["trial_id"]:
            row = experiment_evidence(store, store.get(cell["trial_id"], "trial"))
            row["evidence_complete"] &= cell["evidence_complete"]
        else:
            problem = next(item for item in protocol["instances"] if ProblemInstance(**item).digest() == cell["instance_digest"])
            row = {"trial_id": None, "method_id": cell["method_id"], "method": protocol["methods"][cell["method_id"]],
                "seed": cell["seed"], "instance_digest": cell["instance_digest"], "problem": problem,
                "status": "not_allocated", "result": {}, "validation": [], "evidence_complete": False}
        experiments.append(row)
    nomination = store.get(protocol["nomination_id"], "nomination") if protocol.get("nomination_id") else None
    nomination_review = None
    if nomination:
        original = nomination["evidence"]
        reviewed = {**original, "experiments": [recheck(row) for row in original["experiments"]]}
        current_selection = evaluate(nomination["rule"], reviewed, store=store)
        nomination_review = {"evidence_hash": content_hash(reviewed), "evidence": reviewed,
            "selection": current_selection, "supported": current_selection.get("selected_method_ids") == nomination["selected_method_ids"]}
    references = []
    for frozen in protocol.get("reference_evidence", []):
        references.append(recheck(frozen))
    return {"protocol_hash": protocol["content_hash"], "roster_complete": assessment["complete"] and
                (nomination_review is None or nomination_review["supported"]),
            "experiments": experiments, "nomination": nomination, "nomination_review": nomination_review, "references": references}
