"""Connect immutable validation subjects, scheduler jobs, and measured results."""
from optimization_framework.contracts.assets import Asset, CostSlice
from optimization_framework.contracts.base import content_hash
from optimization_framework.contracts.validation import ValidationRequirement, ValidationResult
from optimization_framework.execution.worker import iter_journal
from optimization_framework.storage.sqlite import now


def snapshot_solutions(workspace, trial, candidates):
    """Freeze the observed search prefix without pausing or changing the learner.

    Prefix costs resolve against the same append-only ledger as final outputs.
    Pending ingestion stays unknown; later work lies outside the fixed interval.
    """
    directory = workspace.job_dir(trial["id"])
    requests = list(iter_journal(directory / "requests.jsonl"))
    positions = {row["id"]: index for index, row in enumerate(requests)}
    observations = [row for row in iter_journal(directory / "observations.jsonl") if row["request_id"] in positions]
    if not observations:
        raise ValueError("No authoritative observation journal is available for this candidate snapshot")
    stop = max(positions[row["request_id"]] for row in observations) + 1
    final_attempt = requests[stop - 1]["attempt_id"]
    prior_attempts = sorted({row["attempt_id"] for row in requests[:stop] if row["attempt_id"] != final_attempt})
    costs = [CostSlice(source_id=trial["id"], stop=stop),
             *[CostSlice(source_id=identity, stop=1) for identity in prior_attempts]]
    if trial.get("isolation_policy"):
        from optimization_framework.assets.execution import ingest_costs
        costs = ingest_costs(workspace.assets, trial, directory, prefix_stop=stop, snapshot_attempt_id=final_attempt)
    dependencies = trial.get("initial_assets", []) + trial.get("contribution_asset_ids", trial.get("implementation_cost_asset_ids", []))
    exposed = {trial["problem"]["scientific_identity"]}
    exposure_known = trial["algorithm"] != "package"
    for identity in dependencies:
        asset = workspace.store.get(identity, "asset")
        exposed.update(asset["exposed_instance_ids"])
        exposure_known &= asset["exposure_status"] == "known"
    result = []
    for candidate in candidates:
        matching = [row for row in observations if row["candidate"] == candidate and row["status"] == "ok"]
        if not matching:
            raise ValueError("The candidate has no successful authoritative observation in this search prefix")
        observation = matching[-1]
        request = requests[positions[observation["request_id"]]]
        result.append(workspace.assets.publish(Asset(
            id="snapshot_" + content_hash([trial["id"], stop, observation["id"]]), campaign_id=trial["campaign_id"],
            kind="solution", title=f"Observed candidate at request {stop}", producer_id=trial["id"],
            payload={"candidate": candidate, "objectives": observation["objectives"], "observation": observation,
                     "problem": trial["problem"], "request_prefix_stop": stop},
            dependency_ids=dependencies, costs=costs,
            cost_provenance="partial" if trial["algorithm"] == "package" and not trial.get("implementation_cost_asset_ids") else "complete",
            exposure_status="known" if exposure_known else "unknown", exposed_instance_ids=sorted(exposed),
            applicability={"problem_id": trial["problem"]["definition_id"], "candidate_schema": trial["problem"]["candidate_schema"]},
            authority="observation_journal", created_at=request["created_at"])))
    return result


def requirements(workspace, trial, recipe, subjects, authority):
    rule = recipe.get("validation_rule")
    if not rule:
        return []  # A diagnostic without an assertion must not invent a pass.
    result = []
    if rule.get("subject") == "evaluator":
        task = workspace.evaluators.task_view(workspace.store.get(trial["task_id"], "task"))
        subjects = [workspace.store.get(task["problem_instance_id"], "problem_instance")]
    for index, subject in enumerate(subjects):
        scope = {"parameters": recipe["parameters"], "rule": rule, "parent_trial_id": trial["id"],
                 "subject_index": index, "recipe_cases_hash": content_hash(recipe["cases"]), "recipe": recipe}
        identity = "requirement_" + content_hash([trial["study_id"], subject["id"], recipe["recipe_id"], scope])
        try:
            required = workspace.store.get(identity, "validation_requirement")
        except KeyError:
            required = workspace.validations.require(ValidationRequirement(id=identity, campaign_id=trial["campaign_id"],
                study_id=trial["study_id"], subject_id=subject["id"], subject_digest=workspace.validations.subject_digest(subject["id"]),
                kind=rule["kind"], recipe_id=recipe["recipe_id"], scope=scope,
                evidence_requirements=rule.get("evidence_requirements", []), authority=authority, created_at=now()))
        result.append(required["id"])
    return result


def finish(workspace, trial):
    if not trial.get("validation_requirement_ids"):
        return
    terminal = trial.get("result") or trial.get("progress") or {}
    summary = terminal.get("recipe_result", {})
    findings = summary.get("subjects", [])
    for index, requirement_id in enumerate(trial["validation_requirement_ids"]):
        requirement = workspace.store.get(requirement_id, "validation_requirement")
        rule = requirement["scope"]["rule"]
        measurements = summary if rule.get("subject") == "evaluator" else findings[index] if index < len(findings) else {}
        if trial["status"] == "failed":
            verdict, rationale = "error", "The validation job failed; its measurements remain available."
        elif not terminal.get("scientific_complete") or not measurements.get("complete", False):
            verdict, rationale = "inconclusive", "The declared validation procedure did not finish."
        else:
            measured = measurements.get("verdict")
            verdict = measured if measured in {"passed", "failed", "inconclusive"} else "inconclusive"
            rationale = measurements.get("rationale", "The adapter assessed the frozen recipe's declared assertion.")
        result = ValidationResult(id="validation_" + content_hash([trial["id"], terminal.get("attempt_id"), requirement_id]),
            campaign_id=trial["campaign_id"], requirement_id=requirement_id, subject_digest=requirement["subject_digest"],
            recipe_id=requirement["recipe_id"], verdict=verdict, evidence_ids=[trial["id"], *trial.get("latest_output_asset_ids", [])],
            measurements=measurements, rationale=rationale, producer="trusted_service", authority="experiment_service",
            created_at=trial.get("finished_at") or trial["created_at"])
        workspace.validations.record_result(result)
        if verdict in {"failed", "error", "inconclusive"}:
            workspace.memory.issue(trial["campaign_id"], "validation_requires_attention", rationale,
                affected=requirement_id, evidence=[trial["id"], result.id])
