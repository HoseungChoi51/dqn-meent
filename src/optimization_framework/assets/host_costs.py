"""An isolated attempt's measured total and unknown individual cost shares."""
from optimization_framework.contracts.assets import CostEvent, CostSlice
from optimization_framework.contracts.base import content_hash
from optimization_framework.assets.accounting import reconcile


def receipts(catalog, trial, attempts):
    result = {}
    for identity in trial.get("execution_host_receipt_ids", []):
        receipt = catalog.store.get(identity, "execution_host_receipt")
        result[receipt["attempt"]] = receipt
        if not any(item.get("attempt") == receipt["attempt"] for item in attempts.values()):
            # Failure before first publication still consumed a process/grant.
            attempt_id = "attempt_" + content_hash([identity, "unpublished"])
            attempts[attempt_id] = {"attempt": receipt["attempt"], "status": "interrupted",
                "started_at": trial["created_at"], "reason": receipt.get("error") or receipt.get("stopped_by")}
    return result


def record(catalog, trial, attempt_id, attempt, request_count, totals, receipt, zero):
    terminal = bool((receipt.get("last_publication") or {}).get("terminal") and attempt.get("finished_at"))
    worker_seconds = totals.get("worker_seconds", 0)
    elapsed = receipt.get("elapsed_seconds")
    remainder = (max(0., elapsed - worker_seconds) if terminal and elapsed is not None and worker_seconds is not None else None)
    for offset, label, quantities in (
            (1, "supervisor", {**zero, "evaluation_requests": 0, "solver_executions": 0,
                "evaluation_seconds": 0, "worker_seconds": remainder}),
            (2, "unpublished", {**zero, **{axis: 0 if terminal else None for axis in
                ("evaluation_requests", "solver_executions", "evaluation_seconds", "worker_seconds")}})):
        catalog.record_cost(CostEvent(id="cost_" + content_hash([receipt["id"], label]), campaign_id=trial["campaign_id"],
            source_id=attempt_id, attempt_id=attempt_id, ordinal=request_count + offset, category="overhead",
            quantities=quantities, evidence_ids=[receipt["id"]],
            status="uncertain" if any(value is None for value in quantities.values()) else "measured",
            created_at=attempt.get("started_at", trial["created_at"])))
    stop = request_count + 3  # worker overhead, requests, supervisor, unpublished suffix
    if elapsed is not None:
        reconcile(catalog, attempt_id, stop, {"worker_seconds": elapsed}, evidence_ids=[receipt["id"]],
            authority="execution_host", rationale="Measured whole-attempt interval; unknown individual shares remain unknown",
            identity="host_cost_" + receipt["id"])
    totals["worker_seconds"] = elapsed
    if not terminal:
        for axis in ("evaluation_requests", "solver_executions", "evaluation_seconds"):
            totals[axis] = None
    return CostSlice(source_id=attempt_id, start=request_count + 1, stop=stop)
