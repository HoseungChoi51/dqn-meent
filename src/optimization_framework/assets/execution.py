"""Idempotent ingestion of worker evidence and immutable experiment outputs."""
from optimization_framework.contracts.assets import Asset, CostEvent, CostSlice
from optimization_framework.contracts.base import content_hash
from optimization_framework.contracts.experiments import ArtifactReference, ExecutionAttempt
from optimization_framework.execution.worker import iter_journal
from optimization_framework.storage.artifacts import LocalArtifactStore
from optimization_framework.storage.sqlite import read_json
import json


ZERO_MODEL_COST = {"model_calls": 0, "model_input_tokens": 0, "model_output_tokens": 0, "api_usd": 0,
                   "model_seconds": 0, "implementation_seconds": 0}


def ingest_costs(catalog, trial, directory, *, prefix_stop=None, snapshot_attempt_id=None):
    """Capture full terminal work or a committed milestone's bounded prefix."""
    cost_rows = iter(iter_journal(directory / "costs.jsonl"))
    measured = next(cost_rows, None)
    observation_rows = iter(iter_journal(directory / "observations.jsonl"))
    observation = next(observation_rows, None)
    batch = []
    requests = 0
    attempt_costs = {}
    attempt_requests = {}
    isolated = trial.get("isolation_policy") is not None
    for ordinal, request in enumerate(iter_journal(directory / "requests.jsonl")):
        if prefix_stop is not None and ordinal >= prefix_stop:
            break
        requests += 1
        attempt_requests[request["attempt_id"]] = attempt_requests.get(request["attempt_id"], 0) + 1
        while measured and measured["request_ordinal"] < ordinal:
            measured = next(cost_rows, None)
        while observation and observation["request_id"] != request["id"]:
            # A missing observation belongs to the last pending request. After
            # recovery it is represented by one explicit uncertain observation.
            observation = next(observation_rows, None)
        if measured and measured["request_ordinal"] == ordinal:
            quantities = {**ZERO_MODEL_COST, **measured["quantities"]}
            status = measured["status"]
        else:
            quantities = {**ZERO_MODEL_COST, "evaluation_requests": 1, "solver_executions": None,
                          "worker_seconds": None, "evaluation_seconds": None}
            if observation:
                quantities["solver_executions"] = observation["costs"].get("solver_executions")
                quantities["evaluation_seconds"] = observation["costs"].get("worker_seconds")
            status = "uncertain"
        event = CostEvent(id="cost_" + content_hash([trial["id"], ordinal]), campaign_id=trial["campaign_id"],
            source_id=request["attempt_id"] if isolated else trial["id"], attempt_id=request["attempt_id"],
            ordinal=attempt_requests[request["attempt_id"]] if isolated else ordinal,
            category="diagnostic" if trial.get("recipe") else "evaluation",
            quantities=quantities, evidence_ids=[request["id"]], status=status, created_at=request.get("created_at", trial["created_at"]))
        value = event.model_dump(mode="json")
        totals = attempt_costs.setdefault(request["attempt_id"], {})
        for axis, quantity in quantities.items():
            totals[axis] = None if quantity is None or totals.get(axis, 0) is None else totals.get(axis, 0) + quantity
        batch.append(("cost_event", {**value, "content_hash": content_hash(value)}, "cost.recorded"))
        if len(batch) >= 500:
            catalog.store.put_many(batch)
            batch = []
        if observation:
            observation = next(observation_rows, None)
    if batch:
        catalog.store.put_many(batch)
    slices = ([CostSlice(source_id=identity, start=1, stop=count + 1) for identity, count in attempt_requests.items()]
              if isolated else [CostSlice(source_id=trial["id"], stop=requests)])
    attempts = {}
    for attempt in iter_journal(directory / "attempts.jsonl"):
        attempt_id = attempt.get("attempt_id", attempt.get("id"))
        attempts.setdefault(attempt_id, {}).update(attempt)
    from optimization_framework.assets import host_costs
    host_receipts = host_costs.receipts(catalog, trial, attempts) if isolated else {}
    enforcement = catalog.store.get(trial["deadline_enforcement_id"], "deadline_enforcement") if trial.get("deadline_enforcement_id") else None
    for attempt_id, attempt in attempts.items():
        prior_host = (isolated and snapshot_attempt_id in attempts and attempt.get("attempt", float("inf"))
                      < attempts[snapshot_attempt_id].get("attempt", 0))
        if prefix_stop is not None and ((attempt_id not in attempt_costs and not prior_host) or attempt_id == snapshot_attempt_id):
            continue
        # No terminal observation means the final overhead was not measured.
        value = attempt.get("attempt_overhead_seconds")
        catalog.record_cost(CostEvent(id="cost_" + content_hash([attempt_id, "overhead"]), campaign_id=trial["campaign_id"],
            source_id=attempt_id, attempt_id=attempt_id, ordinal=0, category="overhead", quantities={**ZERO_MODEL_COST,
                "evaluation_requests": 0, "solver_executions": 0, "evaluation_seconds": 0, "worker_seconds": value},
            evidence_ids=[attempt_id], status="measured" if value is not None else "uncertain",
            created_at=attempt.get("started_at", trial["created_at"])))
        slices.append(CostSlice(source_id=attempt_id, stop=1))
        totals = attempt_costs.setdefault(attempt_id, {})
        totals["worker_seconds"] = None if value is None or totals.get("worker_seconds", 0) is None else totals.get("worker_seconds", 0) + value
        host = host_receipts.get(attempt.get("attempt"))
        if host:
            slices.append(host_costs.record(catalog, trial, attempt_id, attempt, attempt_requests.get(attempt_id, 0),
                totals, host, ZERO_MODEL_COST))
        if prefix_stop is not None:
            continue  # A partial prefix cannot rewrite a complete attempt's totals.
        forced = enforcement if enforcement and enforcement["attempt_id"] == attempt_id else None
        if forced:
            if value is not None:
                # A terminal result can precede a stuck runtime cleanup. Keep
                # its measured overhead and append the unmeasured remainder.
                catalog.record_cost(CostEvent(id="cost_" + content_hash([forced["id"], "cleanup"]), campaign_id=trial["campaign_id"],
                    source_id=forced["id"], attempt_id=attempt_id, ordinal=0, category="overhead", quantities={**ZERO_MODEL_COST,
                        "evaluation_requests": 0, "solver_executions": 0, "evaluation_seconds": 0, "worker_seconds": None},
                    evidence_ids=[forced["id"]], status="uncertain", created_at=trial["finished_at"]))
                slices.append(CostSlice(source_id=forced["id"], stop=1))
            totals["worker_seconds"] = None
            attempt.update(status="budget_exhausted", reason="study_deadline_reached", allocation_stop="study_deadline_reached",
                process_exit=(trial.get("result") or {}).get("process_exit"), scientific_complete=(trial.get("result") or {}).get("scientific_complete", False))
        record = ExecutionAttempt(id=attempt_id, experiment_id=trial["id"],
            status=(trial["status"] if host and host["attempt"] == trial["attempt"] else attempt.get("status", "interrupted")), worker_identity=(
                {"pid": host["host_pid"], "process_identity": host["host_process_identity"],
                 "namespace_pid": attempt.get("pid"), "host_receipt_id": host["id"]} if host else
                {"pid": attempt.get("pid"), "process_identity": attempt.get("process_identity")}),
            checkpoint_id=attempt.get("checkpoint_id"), process_exit=attempt.get("process_exit"),
            allocation_stop=attempt.get("allocation_stop"), scientific_complete=attempt.get("scientific_complete", False),
            enforcement_id=forced["id"] if forced else None,
            actual_costs=totals, reason=attempt.get("reason", "Process exited without a terminal attempt record")).model_dump(mode="json")
        record["campaign_id"] = trial["campaign_id"]
        try:
            previous = catalog.store.get(attempt_id, "execution_attempt")
        except KeyError:
            previous = None
        if previous:
            record["revision"] = previous["revision"]
            if previous != record:
                record["revision"] += 1
        if previous != record:
            catalog.store.put("execution_attempt", record, "attempt.recorded")
    return slices


def ingest_outputs(catalog, trial, directory, cost_slices, *, manifest=None):
    milestone = manifest is not None
    manifest = manifest or read_json(directory / "outputs.json")
    if not manifest:
        return []
    if not milestone and manifest.get("attempt_id") != (trial.get("result") or trial.get("progress", {})).get("attempt_id"):
        return []
    if manifest["id"] != "outputs_" + content_hash({key: value for key, value in manifest.items() if key != "id"}):
        raise ValueError("Worker output manifest failed integrity verification")
    if manifest.get("experiment_id") != trial["id"]:
        raise ValueError("Worker output manifest belongs to another experiment")
    local = LocalArtifactStore(directory / "artifacts")
    output_assets = []
    input_ids = trial.get("initial_assets", []) + trial.get("contribution_asset_ids", trial.get("implementation_cost_asset_ids", [])) + trial.get("recipe_subject_asset_ids", []) + trial.get("operational_cost_asset_ids", [])
    dependencies = [catalog.store.get(item, "asset") for item in input_ids]
    exposure = {trial["problem"]["scientific_identity"]}
    exposure.update(case["problem"]["scientific_identity"] for case in trial.get("recipe", {}).get("cases", []))
    for item in dependencies:
        exposure.update(item["exposed_instance_ids"])
    exposure_known = trial["algorithm"] != "package" and all(item["exposure_status"] == "known" for item in dependencies)
    for index, output in enumerate(manifest["outputs"]):
        reference = ArtifactReference(**output["reference"])
        local.verify(reference)
        reference = catalog.artifacts.register_external(local.resolve(reference), reference.sha256, media_type=reference.media_type)
        asset = catalog.publish(Asset(id="asset_" + content_hash([manifest["id"], index]), campaign_id=trial["campaign_id"],
            kind=output["kind"], title=f"{trial['algorithm']}: {output['kind']}", artifacts=[reference],
            payload={"format": output.get("format"), "metadata": output.get("metadata", {}), "experiment_id": trial["id"],
                     "checkpoint_id": manifest["checkpoint_id"], **({"diagnostic": manifest["diagnostic"]} if milestone else {})}, producer_id=trial["id"],
            dependency_ids=list(dict.fromkeys(input_ids)), costs=cost_slices,
            cost_provenance="partial" if trial["algorithm"] == "package" and not trial.get("implementation_cost_asset_ids") else "complete",
            exposure_status="known" if exposure_known else "unknown", exposed_instance_ids=sorted(exposure),
            applicability={"problem_id": trial["problem"]["definition_id"], "candidate_schema": trial["problem"]["candidate_schema"]},
            authority="experiment_worker", created_at=trial["created_at"]))
        output_assets.append(asset["id"])
        if output["kind"] == "solution_archive" and output.get("authority") == "worker":
            with local.open(ArtifactReference(**output["reference"])) as stream:
                archive = json.load(stream)
            for rank, entry in enumerate(archive["archive"]):
                solution = Asset(id="asset_" + content_hash([asset["id"], rank]), campaign_id=trial["campaign_id"],
                    kind="solution", title=f"{trial['algorithm']}: archived solution {rank + 1}",
                    payload={**entry, "problem": trial["problem"]}, producer_id=trial["id"], dependency_ids=[asset["id"]],
                    cost_provenance="complete", exposure_status=asset["exposure_status"], exposed_instance_ids=asset["exposed_instance_ids"],
                    applicability=asset["applicability"], authority="experiment_worker", created_at=trial["created_at"])
                output_assets.append(catalog.publish(solution)["id"])
    return output_assets
