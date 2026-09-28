"""Capture and verify an isolated revalidation fixture across service restarts.

Run the browser qualification, then ``pause`` and ``before``; restart both
fixture services, then run ``after`` and ``resume``. Pause creates one bounded
experiment using the already checked evaluator. The remaining stages verify
existing records, replay accepted commands and resume that same experiment.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


KINDS = {"campaign", "task", "study", "hypothesis", "trial", "experiment_spec",
    "experiment_draft", "draft_launch", "evaluator_requirement", "evaluator_binding",
    "validation_requirement", "validation_result", "waiver", "waiver_revocation",
    "implementation_grant", "work_command", "reuse_decision", "asset", "cost_event", "execution_attempt"}


def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def records(root, campaigns):
    result = {}
    for owner in ("workspace", "library"):
        database = root / owner / "workspace.sqlite3"
        with sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True) as connection:
            for identity, kind, campaign, raw in connection.execute("SELECT id,kind,campaign_id,data FROM records"):
                if (owner == "library" and kind in {"implementation_version", "implementation_validation", "implementation_job"}) or (
                        owner == "workspace" and ((campaign in campaigns and kind in KINDS) or
                            kind in {"executable_evidence", "implementation_cache"})):
                    result[owner + "/" + identity] = {"kind": kind, "digest": checksum(json.loads(raw))}
    return result


def request(base, path, body=None):
    value = Request(base.rstrip("/") + path, data=json.dumps(body).encode() if body is not None else None,
                    headers={"Content-Type": "application/json"})
    with urlopen(value, timeout=20) as response:
        return json.load(response)


def worker_recovery(args, browser):
    campaign = browser["campaign_id"]
    path = args.directory / "worker-recovery-evidence.json"

    def state():
        return request(args.workspace_url, "/api/v1/state?campaign_id=" + campaign)

    def command(identity, operation, payload):
        return request(args.workspace_url, "/api/v1/commands", {"id": identity, "operation": operation,
            "campaign_id": campaign, "expected_revision": state()["campaign"]["version"], "payload": payload})

    def wait_for(identity, predicate, *, allow_completed=False):
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            trial = next(item for item in state()["trials"] if item["id"] == identity)
            if predicate(trial):
                return trial
            if trial["status"] in {"failed", "stopped"} or (trial["status"] == "completed" and not allow_completed):
                raise ValueError("Recovery probe reached an unexpected terminal state: " + json.dumps(trial.get("result", {})))
            time.sleep(.1)
        raise TimeoutError("Timed out observing the same recovery experiment")

    if args.stage == "pause":
        if path.exists():
            raise ValueError("A recovery experiment is already recorded; inspect it instead of allocating another")
        receipt = command("restart_probe_" + campaign, "trial.create", {"task_id": browser["original"]["task_id"],
            "algorithm": "coordinate", "max_steps": 1200, "wall_seconds": 30,
            "recovery": {"every_observations": 10, "every_seconds": .2}})
        identity = receipt["outcome"]["trial_id"]
        path.write_text(json.dumps({"status": "allocated", "trial_id": identity, "creation_receipt": receipt}, indent=2) + "\n")
        running = wait_for(identity, lambda trial: trial["status"] == "running" and trial.get("progress", {}).get("evaluations", 0) > 0)
        command("pause_probe_" + identity, "trial.control", {"trial_id": identity, "action": "pause"})
        paused = wait_for(identity, lambda trial: trial["status"] == "paused" and trial.get("asset_capture_attempt") == trial["attempt"])
        if paused["attempt"] != 1 or not paused["progress"].get("checkpoint_available"):
            raise ValueError("The first attempt did not produce a resumable checkpoint")
        result = {"status": "paused_for_service_restart", "campaign_id": campaign, "trial_id": identity,
            "creation_receipt": receipt, "running_evaluations": running["progress"]["evaluations"], "paused": paused}
    else:
        result = json.loads(path.read_text())
        resume_id = "resume_probe_" + result["trial_id"]
        try:
            receipt = request(args.workspace_url, "/api/v1/commands/" + resume_id)
        except HTTPError as error:
            if error.code != 404:
                raise
            receipt = None
        if receipt is None:
            before = next(item for item in state()["trials"] if item["id"] == result["trial_id"])
            restart = json.loads((args.directory / "http-restart-evidence.json").read_text())
            identity = "workspace/" + before["id"]
            current = records(args.directory, restart["campaign_ids"])
            # Cataloging can finish between the first pause observation and
            # the before-restart snapshot; use the settled authoritative record.
            if restart["status"] != "verified" or current[identity] != restart["before"][identity] or before["status"] != "paused":
                raise ValueError("The paused experiment changed during service restart")
            result["paused_with_cataloged_evidence"] = before
            receipt = command(resume_id, "trial.control", {"trial_id": before["id"], "action": "resume"})
        if receipt["outcome"]["trial_id"] != result["trial_id"]:
            raise ValueError("The accepted resume receipt refers to a different experiment")
        before = result.get("paused_with_cataloged_evidence", result["paused"])
        result.update(status="resuming", resume_receipt=receipt)
        path.write_text(json.dumps(result, indent=2) + "\n")
        completed = wait_for(before["id"], lambda trial: trial["status"] == "completed" and
            trial.get("asset_capture_attempt") == trial["attempt"], allow_completed=True)
        if completed["attempt"] != 2 or not completed["result"]["scientific_complete"] or completed["result"]["evaluations"] != 1200:
            raise ValueError("The resumed experiment did not complete its original procedure in two attempts")
        for field in ("experiment_spec", "experiment_spec_hash", "source_hash", "evaluator_artifact_digest", "evaluator_eligibility"):
            if completed[field] != before[field]:
                raise ValueError("Recovery changed the frozen " + field)
        asset = request(args.workspace_url, "/api/v1/assets/" + completed["latest_output_asset_ids"][0])
        if asset["full_attributed_cost"]["quantities"]["evaluation_requests"]["total"] != 1220:
            raise ValueError("Recovery lost or duplicated evaluator/experiment contributions")
        from optimization_framework.execution.worker import read_journal
        observations = read_journal(args.directory / "workspace/trials" / before["id"] / "observations.jsonl")
        if len(observations) != 1200 or len({row["request_id"] for row in observations}) != 1200 or len({row["attempt_id"] for row in observations}) != 2:
            raise ValueError("Recovery lost or duplicated observations across its two attempts")
        result.update(status="verified", completed=completed, resume_receipt=receipt, costs=asset["full_attributed_cost"],
            observation_count=len(observations), observation_digest=checksum(observations))
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"stage": args.stage, "status": result["status"], "trial_id": result["trial_id"], "report": str(path)}))


def failure_continuation(args, browser):
    """An actual HTTP recheck failure stays scoped while another job completes."""
    path = args.directory / "failure-continuation-evidence.json"
    existing = json.loads(path.read_text()) if path.exists() else None
    if existing and existing["status"] == "verified":
        print(json.dumps({"stage": "failure", "status": "verified", "campaign_id": existing["campaign_id"], "report": str(path)}))
        return
    spec = json.loads(json.dumps(browser["original_version"]["spec"]))
    spec["manifest"]["id"] += "_counterexample"
    spec.update(name="HTTP counterexample qualification", validation_mode="numerical", contract_cases=[],
        correctness_cases=[{"name": "origin", "basis": "2*(0-1.5)^2-2 = 2.5", "candidate": [0, 0], "objectives": {"energy": 2.5}}])
    campaign = {"id": existing["campaign_id"]} if existing else request(args.workspace_url, "/api/campaigns", {"name": "Scoped executable failure with independent work",
        "compute_budget_seconds": 60, "implementation_compute_budget_seconds": 60, "validation_reserve_seconds": 0,
        "tasks": [{"name": "Evaluator with a discoverable counterexample", "problem_id": spec["manifest"]["id"],
            "evaluator_manifest": spec["manifest"]}, {"name": "Independent installed problem", "problem_id": "bounded_continuous"}]})
    path.write_text(json.dumps({"status": "created", "campaign_id": campaign["id"]}, indent=2) + "\n")

    def state():
        return request(args.workspace_url, "/api/v1/state?campaign_id=" + campaign["id"])

    def command(name, operation, payload):
        return request(args.workspace_url, "/api/v1/commands", {"id": name + "_" + campaign["id"],
            "campaign_id": campaign["id"], "expected_revision": state()["campaign"]["version"], "operation": operation, "payload": payload})

    def wait_for(predicate):
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            current = state()
            if predicate(current):
                return current
            time.sleep(.1)
        raise TimeoutError("Inspect the recorded failure scenario; its existing jobs have not reached the expected state")

    tasks = state()["tasks"]
    target = next(task for task in tasks if task.get("evaluator_requirement_id"))
    independent = next(task for task in tasks if not task.get("evaluator_requirement_id"))
    source = Path("examples/implementation-reference/continuous_evaluator.py").read_text()
    source = source.replace('value = round(value,', 'value = round(value + (1 if candidate == [2, 2] else 0),')
    command("commission_counterexample", "evaluator.commission", {"task_id": target["id"], "spec": spec,
        "compute_seconds": 20, "max_calls": 1, "package": {"kind": "evaluator", "contract": "evaluator_v1",
            "entrypoint": "evaluator:create_evaluator", "files": [{"path": "evaluator.py", "content": source}]}})
    built = wait_for(lambda value: any(task.get("evaluator_version_id") for task in value["tasks"]))
    target = next(task for task in built["tasks"] if task["id"] == target["id"])
    created = command("independent_work", "trial.create", {"task_id": independent["id"], "algorithm": "random",
        "max_steps": 5000, "wall_seconds": 20, "recovery": {"every_observations": 10, "every_seconds": .2}})
    trial_id = created["outcome"]["trial_id"]
    if next(trial for trial in state()["trials"] if trial["id"] == trial_id)["status"] in {"queued", "running"}:
        wait_for(lambda value: any(trial["id"] == trial_id and trial["status"] == "running" for trial in value["trials"]))
    recheck = command("counterexample", "implementation.revalidate", {"version_id": target["evaluator_version_id"], "compute_seconds": 20,
        "checks": {"kind": "evaluator", "rationale": "An independently derived corner value tests the previously unchecked branch",
            "correctness_cases": [{"name": "corner", "basis": "2*(2-1.5)^2-2 = -1.5", "candidate": [2, 2], "objectives": {"energy": -1.5}}]}})
    grant_id = recheck["outcome"]["grant_id"]
    final = wait_for(lambda value: any(job["id"] == grant_id and job["status"] == "failed" and job.get("evidence_reconciled")
        for job in value["implementation_jobs"]) and any(trial["id"] == trial_id and trial["status"] == "completed" and
            trial.get("asset_capture_attempt") == trial["attempt"] for trial in value["trials"]))
    parallel = next(trial for trial in final["trials"] if trial["id"] == trial_id)
    # A fixed resource cutoff is valid incomplete evidence, never a reason to
    # lower the existing procedure's completion criterion. Freeze a separate
    # small continuation to verify new independent work while the issue is open.
    followup = command("independent_completion", "trial.create", {"task_id": independent["id"], "algorithm": "random",
        "max_steps": 20, "wall_seconds": 5})
    completed_id = followup["outcome"]["trial_id"]
    final = wait_for(lambda value: any(trial["id"] == completed_id and trial["status"] == "completed" and
        trial.get("asset_capture_attempt") == trial["attempt"] for trial in value["trials"]))
    time.sleep(2.2)  # Exercise another maintenance cycle against the same failure.
    with sqlite3.connect(args.directory / "workspace/workspace.sqlite3") as connection:
        issues = [json.loads(row[0]) for row in connection.execute("SELECT data FROM records WHERE kind='manager_issue' AND campaign_id=?", (campaign["id"],))]
    scoped = [issue for issue in issues if issue.get("affected") == grant_id]
    job = next(job for job in final["implementation_jobs"] if job["id"] == grant_id)
    trial = next(trial for trial in final["trials"] if trial["id"] == completed_id)
    if len(issues) != 1 or len(scoped) != 1 or scoped[0]["occurrences"] != 1 or not trial["result"]["scientific_complete"]:
        raise ValueError("The failed recheck did not remain one scoped issue alongside completed independent work")
    if job.get("usage", {}).get("calls", 0) != 0 or "independent fixture corner" not in job["error"]:
        raise ValueError("The failure did not come from the independent mechanical countercheck")
    path.write_text(json.dumps({"status": "verified", "campaign_id": campaign["id"], "grant": job, "issue": scoped[0],
        "parallel_trial": parallel, "independent_trial": trial, "recheck_receipt": recheck, "followup_receipt": followup}, indent=2) + "\n")
    print(json.dumps({"stage": "failure", "status": "verified", "campaign_id": campaign["id"], "report": str(path)}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["pause", "before", "after", "resume", "failure"])
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--workspace-url", required=True)
    args = parser.parse_args()
    evidence = sorted(args.directory.glob("browser-results/**/revalidation-reuse-evidence.json"))
    if len(evidence) != 1:
        raise ValueError("Expected exactly one completed browser revalidation scenario")
    browser = json.loads(evidence[0].read_text())
    if args.stage == "failure":
        failure_continuation(args, browser)
        return
    if args.stage in {"pause", "resume"}:
        worker_recovery(args, browser)
        return
    campaigns = [browser["campaign_id"], browser["reuse_campaign_id"]]
    states = [request(args.workspace_url, "/api/v1/state?campaign_id=" + identity) for identity in campaigns]
    if any(row["status"] in {"queued", "running", "pausing", "stopping"} for state in states for row in state["trials"]):
        raise ValueError("The qualified campaigns must have no active experiments before this restart check")
    for state in states:
        for job in state["implementation_jobs"]:
            if job["status"] != "completed" or not job.get("accounting_final", True):
                raise ValueError("The qualified implementation grants must be completed and settled")
    path = args.directory / "http-restart-evidence.json"
    current = records(args.directory, campaigns)
    if args.stage == "before":
        if path.exists():
            raise ValueError("A restart snapshot already exists; preserve it instead of replacing its baseline")
        result = {"campaign_ids": campaigns, "browser_evidence": str(evidence[0].relative_to(args.directory)),
            "before": current, "before_digest": checksum(current), "status": "awaiting_actual_service_restart"}
    else:
        result = json.loads(path.read_text())
        if result["campaign_ids"] != campaigns or result["before"] != current:
            raise ValueError("Scientific, command, executable or accounting records changed across restart")
        reply = request(args.workspace_url, "/api/v1/commands", browser["recheckEnvelope"])
        if reply != browser["recheckReceipt"]:
            raise ValueError("Replayed command did not return the original accepted receipt")
        after = records(args.directory, campaigns)
        if current != after:
            raise ValueError("Replaying the accepted command changed records or allocated work")
        result.update(status="verified", after_digest=checksum(after), identical_record_count=len(current),
            replayed_command_id=browser["recheckEnvelope"]["id"], replayed_receipt=reply,
            costs=browser["costs"], service_restart_process_evidence="service-process-restart.json")
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"stage": args.stage, "status": result["status"], "records": len(current), "report": str(path)}))


if __name__ == "__main__":
    main()
