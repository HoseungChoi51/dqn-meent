"""Milestone snapshots become ordinary, separately costed workspace jobs."""
from optimization_framework.assets.execution import ingest_costs, ingest_outputs
from optimization_framework.contracts.assets import ReuseDecision
from optimization_framework.contracts.base import content_hash
from optimization_framework.contracts.diagnostics import ArtifactInference, DiagnosticSchedule
from optimization_framework.contracts.requests import TrialInput
from optimization_framework.execution.worker import fingerprint, iter_journal
from optimization_framework.storage.sqlite import now, read_json

from .recipes import recipe_parameters


def prepare(request, problem, capabilities=None, *, registry=None):
    if capabilities is None:
        from optimization_framework.optimizers.registry import capabilities as declared_capabilities
        capabilities = declared_capabilities(request.algorithm, request.algorithm_config)
    from .inference import prepare as prepare_inference, resolve
    schedules = []
    for schedule in request.diagnostics:
        limit = (request.completion or {}).get("count", request.max_steps) if schedule.unit == (request.completion or {}).get("unit", "evaluation_requests") else request.max_steps
        if max(schedule.at_counts) > limit:
            raise ValueError("Diagnostic milestones exceed the frozen procedure's declared count")
        if schedule.unit not in capabilities.completion_units:
            raise ValueError("This implementation has no optimizer decision counter for diagnostics")
        raw = schedule.model_dump(mode="json")
        for index, rollout in enumerate(schedule.rollouts):
            identity, parameters = resolve(rollout)
            _, descriptor, parameters = prepare_inference(identity, problem, parameters)
            if not any(descriptor.artifact.accepts(export) for export in capabilities.exports):
                raise ValueError(f"This implementation does not declare the artifact export required by {descriptor.title}")
            if isinstance(rollout, ArtifactInference):
                raw["rollouts"][index]["parameters"] = parameters
        for count in schedule.at_counts:
            for index, rollout in enumerate(schedule.rollouts):
                rollout.resolved_seed(request.seed, count, index)  # Reject overflow before allocating any parent work.
        for recipe in [*raw["recipes"], *(item for rollout in raw["rollouts"] for item in rollout["recipes"])]:
            recipe["parameters"] = recipe_parameters(problem, recipe["recipe_id"], recipe["parameters"], registry=registry)
        schedules.append(DiagnosticSchedule(**raw))
    if len({item.digest() for item in schedules}) != len(schedules):
        raise ValueError("Declare each diagnostic schedule once")
    return schedules


def reserve(store, trial):
    for raw in trial.get("diagnostics", []):
        schedule = DiagnosticSchedule(**raw)
        for count in schedule.at_counts:
            identity = "diagnostic_" + content_hash([trial["id"], schedule.digest(), count])
            store.put("diagnostic_grant", {"id": identity, "campaign_id": trial["campaign_id"], "study_id": trial["study_id"],
                "parent_trial_id": trial["id"], "schedule": schedule.model_dump(mode="json"), "schedule_digest": schedule.digest(),
                **({"execution_grant_id": trial["execution_grant_id"]} if trial.get("execution_grant_id") else {}),
                "count": count, "reserved_seconds": schedule.allocation(), "status": "reserved", "created_at": now()}, "diagnostic.reserved")
            store.put_immutable("resource_reservation", {"id": "reservation_" + identity, "campaign_id": trial["campaign_id"],
                "owner_id": identity, "owner_kind": "diagnostic", "worker_seconds": schedule.allocation(),
                "grant_id": trial.get("execution_grant_id"), "parent_reservation_id": "reservation_" + trial["id"],
                "created_at": trial["created_at"]}, "resource.reserved")


def descendants(trials, roots):
    identities = set(roots)
    while True:
        children = {trial["id"] for trial in trials if trial.get("parent_trial_id") in identities}
        if children <= identities:
            return identities - set(roots)
        identities.update(children)


def assessment(store, trial):
    trials = store.list("trial", trial["campaign_id"])
    children = descendants(trials, [trial["id"]])
    grants = [grant for grant in store.list("diagnostic_grant", trial["campaign_id"])
              if grant["parent_trial_id"] in {trial["id"], *children}]
    jobs = [child for child in trials if child["id"] in children and child.get("diagnostic_grant_id")]
    return {"complete": all(grant["status"] == "dispatched" for grant in grants) and all(
        child["status"] == "completed" and (child.get("result") or {}).get("scientific_complete") and
        child.get("asset_capture_attempt", -1) == child.get("attempt", 0) for child in jobs),
        "grants": [{key: grant.get(key) for key in ("id", "status", "count", "schedule_digest", "snapshot_id")} for grant in grants],
        "jobs": [{"id": child["id"], "status": child["status"], "scientific_complete": bool((child.get("result") or {}).get("scientific_complete")),
                  "result_digest": content_hash(child.get("result") or {})} for child in jobs]}


def _ready(workspace, trial, grant):
    directory = workspace.job_dir(trial["id"])
    path = directory / "diagnostics" / grant["schedule_digest"] / f"{grant['count']}.json"
    manifest = read_json(path)
    if not manifest:
        count = ((trial.get("result") or trial.get("progress") or {}).get("evaluations", 0) if grant["schedule"]["unit"] == "evaluation_requests"
                 else (trial.get("result") or trial.get("progress") or {}).get("diagnostics", {}).get("decisions", 0))
        if trial["status"] in {"completed", "stopped", "failed", "budget_exhausted"}:
            if count >= grant["count"]:
                raise ValueError("A declared diagnostic milestone has no committed worker snapshot")
            grant.update(status="not_reached", finished_at=now())
            workspace.store.put("diagnostic_grant", grant, "diagnostic.not_reached")
        return None
    if manifest.get("spec_hash") != fingerprint(trial) or manifest.get("diagnostic", {}).get("schedule_digest") != grant["schedule_digest"] or manifest["diagnostic"].get("count") != grant["count"]:
        raise ValueError("Diagnostic snapshot differs from its frozen procedure and grant")
    stop = manifest["diagnostic"]["request_prefix_stop"]
    if not isinstance(stop, int) or stop <= 0:
        raise ValueError("A diagnostic snapshot must name a nonempty observed prefix")
    # The worker publishes inside the request's measured interval. Do not mark
    # a still-open interval uncertain merely because the service polled early.
    if trial["status"] in {"queued", "running", "pausing", "stopping", "paused", "interrupted"}:
        if not any(row["request_ordinal"] == stop - 1 for row in iter_journal(directory / "costs.jsonl")):
            return None
    return manifest


def dispatch(workspace, trial, grant, manifest):
    """Grant consumption, evidence publication and child allocations commit once."""
    schedule = DiagnosticSchedule(**grant["schedule"])
    directory = workspace.job_dir(trial["id"])
    costs = ingest_costs(workspace.assets, trial, directory, prefix_stop=manifest["diagnostic"]["request_prefix_stop"], snapshot_attempt_id=manifest["attempt_id"])
    assets = [workspace.store.get(identity, "asset") for identity in ingest_outputs(workspace.assets, trial, directory, costs, manifest=manifest)]
    grant.update(status="dispatching", snapshot_id=manifest["id"], asset_ids=[asset["id"] for asset in assets])
    workspace.store.put("diagnostic_grant", grant)  # Release this reservation inside the allocation transaction.
    children = []
    solutions = [asset for asset in assets if asset["kind"] == "solution"]
    for recipe_request in schedule.recipes:
        subjects = solutions[:recipe_request.subject_limit]
        recipe = workspace.compile_recipe(trial, recipe_request.recipe_id, recipe_request.parameters,
            [asset["payload"]["candidate"] for asset in subjects])
        request = TrialInput(campaign_id=trial["campaign_id"], task_id=trial["task_id"], algorithm="recipe", seed=trial["seed"],
            max_steps=len(recipe["cases"]), wall_seconds=recipe_request.wall_seconds,
            question=f"{recipe_request.recipe_id} at the frozen {schedule.unit} milestone {grant['count']}")
        child = workspace._queue_recipe(trial, recipe, request, frozen_subjects=subjects,
            authority="frozen_diagnostic_procedure", extra={"diagnostic_grant_id": grant["id"], "source_trial_id": trial["id"]})
        children.append(child["id"])
    for index, rollout in enumerate(schedule.rollouts):
        compiled = workspace.compile_inference(trial, rollout.model_dump(mode="json"), assets)
        decision = workspace.assets.decide(ReuseDecision(id="reuse_" + grant["id"] + "_" + compiled["asset_id"],
            campaign_id=trial["campaign_id"], study_id=trial["study_id"],
            asset_id=compiled["asset_id"], decision="reuse", intended_use="optimizer_input", authority="frozen_diagnostic_procedure",
            rationale="The accepted experiment declares independent inference from this milestone's frozen artifact",
            created_at=grant["created_at"]))
        seed = rollout.resolved_seed(trial["seed"], grant["count"], index)
        diagnostics = [DiagnosticSchedule(unit=compiled["completion"]["unit"], at_counts=[compiled["completion"]["count"]],
            export_optimizer=False, recipes=rollout.recipes)] if rollout.recipes else []
        request = TrialInput(campaign_id=trial["campaign_id"], task_id=trial["task_id"], seed=seed,
            **{key: compiled[key] for key in ("algorithm", "algorithm_config", "max_steps", "schedule_steps", "completion")},
            initial_assets=[compiled["asset_id"]], reuse_decision_ids=[decision["id"]],
            diagnostics=diagnostics, wall_seconds=rollout.wall_seconds,
            question=f"Independent {compiled['adapter']['title']} at milestone {grant['count']}")
        child = workspace.create_trial(request, validation={"parent_trial_id": trial["id"], "study_id": trial["study_id"],
            "diagnostic_grant_id": grant["id"], "diagnostic_kind": "artifact_inference" if isinstance(rollout, ArtifactInference) else "policy_rollout",
            "source_trial_id": trial["id"], "inference_adapter": compiled["adapter"],
            "diagnostic_seed_binding": {"declaration": rollout.model_dump(mode="json")["seed"],
                "parent_seed": trial["seed"], "milestone": grant["count"], "episode_index": index, "resolved_seed": seed}})
        children.append(child["id"])
    grant.update(status="dispatched", trial_ids=children, dispatched_at=now())
    workspace.store.put("diagnostic_grant", grant, "diagnostic.dispatched")
    return grant


def reconcile(workspace):
    for grant in workspace.store.list("diagnostic_grant"):
        if grant["status"] != "reserved":
            continue
        try:
            with workspace.lock, workspace.store.transaction():
                grant = workspace.store.get(grant["id"], "diagnostic_grant")
                if grant["status"] != "reserved":
                    continue
                trial = workspace.store.get(grant["parent_trial_id"], "trial")
                if trial.get("absolute_deadline"):
                    import time
                    if time.time() >= trial["absolute_deadline"]:
                        grant.update(status="deadline_reached", finished_at=now())
                        workspace.store.put("diagnostic_grant", grant, "diagnostic.deadline_reached")
                        continue
                manifest = _ready(workspace, trial, grant)
                if manifest:
                    dispatch(workspace, trial, grant, manifest)
        except Exception as exc:
            workspace.memory.issue(grant["campaign_id"], "diagnostic_requires_attention", str(exc),
                affected=grant["id"], evidence=[grant["parent_trial_id"]])
