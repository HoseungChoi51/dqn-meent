"""Scheduler integration for the installed isolated host; no second queue."""
from pathlib import Path
import subprocess
import sys
import time

from optimization_framework.contracts.base import content_hash
from optimization_framework.contracts.isolation import IsolationPolicy
from optimization_framework.execution.worker import fingerprint
from optimization_framework.storage.artifacts import atomic_json
from optimization_framework.storage.sqlite import read_json


def policy(workspace, trial):
    """The immutable record, not a mutable trial flag, decides dispatch mode."""
    if trial.get("execution_contract") != 1:
        return None
    frozen = workspace.store.get(trial["id"] + "_spec", "experiment_spec")
    raw = frozen["schedule"].get("isolation_policy")
    if raw is None and trial.get("isolation_policy") is None:
        return None
    scientific = {key: value for key, value in frozen.items() if key != "content_hash"}
    if (raw is None or raw != trial.get("isolation_policy") or scientific != trial.get("experiment_spec")
            or content_hash(scientific) != trial.get("experiment_spec_hash")):
        raise ValueError("The execution isolation policy differs from the frozen experiment")
    return IsolationPolicy.model_validate(raw)


def directory(workspace, trial):
    return workspace.directory / "execution-hosts" / trial["id"] / str(trial["attempt"])


def identity(trial):
    return {"experiment_id": trial["id"], "attempt": trial["attempt"],
            "fingerprint": fingerprint(trial), "experiment_spec_hash": trial["experiment_spec_hash"]}


def validate(value, trial):
    if any(value.get(key) != expected for key, expected in identity(trial).items()):
        raise ValueError("The execution host record belongs to another frozen attempt")


def lease(workspace, trial, selected):
    value = read_json(directory(workspace, trial) / "lease.json", {})
    if value:
        validate(value, trial)
        limits = {"storage_kind": "bounded_tmpfs", "private_byte_limit": selected.private_bytes,
                  "private_entry_limit": selected.private_entries, "published_byte_limit": selected.published_bytes}
        if any(value.get(key) != expected for key, expected in limits.items()):
            raise ValueError("The execution host lease differs from its frozen resource policy")
    return value


def launch(workspace, trial, selected):
    destination = directory(workspace, trial)
    destination.mkdir(parents=True, exist_ok=False)
    # Start installed code with no captured cwd, PYTHONPATH or model credentials.
    bootstrap = ("import runpy,sys;sys.path.insert(0," + repr(str(Path(__file__).resolve().parents[2]))
                 + ");runpy.run_module('optimization_framework.execution.isolated_host',run_name='__main__')")
    command = [sys.executable, "-I", "-c", bootstrap, "--directory", str(workspace.job_dir(trial["id"])),
               "--host-directory", str(destination), "--private-bytes", str(selected.private_bytes),
               "--private-entries", str(selected.private_entries), "--published-bytes", str(selected.published_bytes)]
    with (destination / "host.log").open("ab") as log:
        return subprocess.Popen(command, cwd=destination, env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "OPENBLAS_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "MPLBACKEND": "Agg"},
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)


def reconcile(workspace, trial, selected):
    """Called only after the trusted host is dead; never launch replacement work."""
    from optimization_framework.execution.isolated_host import execute, process_identity
    destination = directory(workspace, trial)
    original = lease(workspace, trial, selected)
    if original and process_identity(original.get("pid")) == original.get("process_identity") and original.get("process_identity"):
        raise ValueError("Cannot reconcile an execution host that still owns its attempt")
    value = read_json(destination / "receipt.json")
    if not value and original:
        value = execute(workspace.job_dir(trial["id"]), destination,
            private_bytes=selected.private_bytes, private_entries=selected.private_entries,
            published_bytes=selected.published_bytes)
    if not value:
        value = {**identity(trial), "id": "isolated_host_" + content_hash(identity(trial)), "schema_version": 2,
            "host_pid": trial.get("pid"), "host_process_identity": trial.get("process_identity"),
            "started_at": trial["attempt_started_at"], "finished_at": time.time(), "elapsed_seconds": None,
            "stopped_by": "host_startup_failed", "error": "The installed host exited before committing its lease",
            "process_exit": None, "last_publication": None, "published_records": {}, "recovered_after_interruption": True}
        destination.mkdir(parents=True, exist_ok=True)
        atomic_json(destination / "receipt.json", value)
    validate(value, trial)
    if original and (value.get("host_pid") != original["pid"] or value.get("host_process_identity") != original["process_identity"]):
        raise ValueError("Execution host receipt does not match its owning process")
    record = {**value, "campaign_id": trial["campaign_id"]}
    workspace.store.put_immutable("execution_host_receipt", record, "trial.host_reconciled")
    trial["execution_host_receipt_ids"] = sorted(set(trial.get("execution_host_receipt_ids", [])) | {value["id"]})
    trial["execution_host_receipt_id"] = value["id"]
    prior = trial.get("prior_execution_seconds", 0)
    if value["elapsed_seconds"] is not None:
        trial["execution_seconds"] = prior + value["elapsed_seconds"]
        trial["execution_seconds_upper_bound"] = trial.get("prior_budget_execution_seconds", prior) + value["elapsed_seconds"]
        trial["execution_seconds_basis"] = "host_receipt_with_prior_unknown" if trial.get("unknown_execution_seconds") else "host_receipt"
    else:
        # Observation after downtime is not a measurement of the dead process.
        trial["execution_seconds_basis"] = "observed_lower_bound"
        trial["unknown_execution_seconds"] = True
        started_at = (original or {}).get("started_at", trial["attempt_started_at"])
        upper = max(0., value["finished_at"] - started_at)
        if original:
            upper = min(upper, max(0., trial["wall_seconds"] - original["elapsed_before"]) + trial.get("stop_grace_seconds", 5))
        trial["execution_seconds_upper_bound"] = max(trial.get("execution_seconds", 0),
            trial.get("prior_budget_execution_seconds", prior) + upper)
    return record
