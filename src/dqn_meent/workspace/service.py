"""Single-machine experiment supervisor, independent from LLM execution."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time

from dqn_meent.config import TrainConfig
from .models import CampaignInput, CampaignUpdate, TrialInput, ControlInput, ValidationInput
from .store import Store, atomic_json, identifier, now, read_json


ALGORITHMS = [
    {"id": "random", "name": "Uniform random", "description": "Independent binary designs; reference baseline.", "parameters": {}},
    {"id": "hillclimb", "name": "Restart hill climbing", "description": "Single-cell improvement with restarts; reference baseline.", "parameters": {}},
    {"id": "dqn", "name": "Double DQN", "description": "Learn cell-flip actions using replay; startup requires sufficient training.", "parameters": {}},
    {"id": "annealing", "name": "Simulated annealing", "description": "Accept some downhill moves to escape local optima.", "parameters": {}},
    {"id": "block_tabu", "name": "Adaptive block tabu", "description": "Coordinated cell moves with recent-design memory.", "parameters": {}},
    {"id": "population", "name": "Population search", "description": "Evolve a population using crossover and mutation.", "parameters": {}},
    {"id": "surrogate", "name": "Surrogate-guided search", "description": "Fit observations to rank candidate designs before RCWA evaluation.", "parameters": {}},
]
ACTIVE = {"queued", "running", "pausing", "stopping"}
LIVE = {"running", "pausing", "stopping"}


def process_identity(pid):
    """Linux process start ticks prevent signals to a recycled PID."""
    try:
        raw = Path(f"/proc/{int(pid)}/stat").read_text()
        fields = raw[raw.rfind(")") + 2:].split()
        return None if fields[0] == "Z" else fields[19]
    except (OSError, ValueError, IndexError, TypeError):
        return None


def alive(trial):
    pid, identity = trial.get("pid"), trial.get("process_identity")
    return bool(pid and identity and process_identity(pid) == identity)


class Workspace:
    def __init__(self, directory, max_workers=2, stop_grace_seconds=5):
        self.store = Store(directory)
        self.directory = self.store.directory
        self.max_workers = max(1, min(int(max_workers), 16))
        self.stop_grace_seconds = stop_grace_seconds
        self.lock = threading.RLock()
        self.shutdown_event = threading.Event()
        self.thread = None
        self.processes = {}
        self.last_progress = {}
        self.research_threads = {}
        self.on_trial_finished = None
        self._lease = None

    def start(self):
        self._lease = (self.directory / "service.lock").open("a+")
        try:
            fcntl.flock(self._lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._lease.close()
            raise RuntimeError("Another service owns this workspace directory") from exc
        self.shutdown_event.clear()
        self.reconcile()
        for run in self.store.list("research_run"):
            if run["status"] in {"running", "stopping"}:
                if run.get("usage", {}).get("pending_reservation"):
                    subscription = run.get("usage", {}).get("billing_mode") == "subscription"
                    detail = "Its subscription usage is uncertain and the attempted call is retained" if subscription else "Its conservative cost is retained"
                    run.update(status="needs_reconciliation", error=f"A provider request was in flight. {detail}; automatic replay is disabled.")
                    # Stable identity also survives a crash between the two record writes.
                    decision_id = "reconcile_" + run["id"]
                    self.store.put("decision", {"id": decision_id, "campaign_id": run["campaign_id"],
                        "charter_version": run["charter_version"], "title": "Resolve an interrupted provider call",
                        "context": run["error"], "status": "pending", "created_at": now(), "research_run_id": run["id"],
                        "options": [{"id": "close_reserved", "label": "Keep recorded usage and close",
                                     "description": "Do not replay uncertain work. Start a new discussion with the current provider and allowance."}],
                        "recommendation": "close_reserved"}, "decision.created")
                else:
                    run.update(status="interrupted", error="Service restarted; prior evidence and trace are retained.")
                self.store.put("research_run", run, "research.interrupted")
        self.thread = threading.Thread(target=self._loop, name="experiment-supervisor", daemon=True)
        self.thread.start()

    def close(self):
        self.shutdown_event.set()
        if self.thread:
            self.thread.join(timeout=3)
        if self._lease:
            fcntl.flock(self._lease, fcntl.LOCK_UN)
            self._lease.close()
            self._lease = None

    def job_dir(self, trial_id):
        # Identifiers always come from records or our UUID generator.
        if "/" in trial_id or ".." in trial_id:
            raise ValueError("Invalid trial identifier")
        return self.directory / "trials" / trial_id

    def create_campaign(self, request: CampaignInput):
        from .research import seed_hypotheses
        with self.lock:
            data = request.model_dump(exclude={"tasks"})
            campaign = dict(data, id=identifier("campaign"), version=1, created_at=now(), updated_at=now())
            self.store.put("campaign", campaign, "campaign.created")
            self._add_tasks(campaign, request.tasks)
            for card in seed_hypotheses():
                card = {**card, "id": identifier("hypothesis"), "campaign_id": campaign["id"],
                        "charter_version": 1, "created_at": now()}
                self.store.put("hypothesis", card)
            self.store.put("charter", {**campaign, "id": identifier("charter"), "campaign_id": campaign["id"],
                                        "tasks": [t.model_dump() for t in request.tasks]})
            return campaign

    def _add_tasks(self, campaign, tasks):
        from .confirmation import task_exposure_fields
        for task in tasks:
            record = task.model_dump(exclude={"id"})
            record.update(id=identifier("task"), campaign_id=campaign["id"], charter_version=campaign["version"],
                          archived=False, created_at=now(),
                          **task_exposure_fields(self.store, campaign["id"], record["physics"]))
            self.store.put("task", record)

    def update_campaign(self, campaign_id, request: CampaignUpdate):
        with self.lock:
            campaign = self.store.get(campaign_id, "campaign")
            changes = request.model_dump(exclude_none=True, exclude={"tasks"})
            proposed = {**campaign, **changes}
            if proposed["validation_reserve_seconds"] > proposed["compute_budget_seconds"]:
                raise ValueError("Validation reserve exceeds compute budget")
            used_reserved = self.allocated_seconds(campaign_id)
            if proposed["compute_budget_seconds"] < used_reserved:
                raise ValueError(f"Existing spent/reserved compute is {used_reserved:.1f}s; stop or finish work before lowering this cap")
            campaign.update(changes)
            campaign.update(version=campaign["version"] + 1, updated_at=now())
            if request.tasks is not None:
                for task in self.store.list("task", campaign_id):
                    if not task.get("archived"):
                        self.store.put("task", {**task, "archived": True})
                self._add_tasks(campaign, request.tasks)
            self.store.put("campaign", campaign, "campaign.revised")
            self.store.put("charter", {**campaign, "id": identifier("charter"), "campaign_id": campaign_id,
                                        "tasks": self.current_tasks(campaign_id)})
            for trial in self.store.list("trial", campaign_id):
                trial["charter_superseded"] = True
                self.store.put("trial", trial)
            return campaign

    def current_tasks(self, campaign_id):
        return [t for t in self.store.list("task", campaign_id) if not t.get("archived")]

    def allocated_seconds(self, campaign_id, exclude=None):
        total = 0.0
        for trial in self.store.list("trial", campaign_id):
            if trial["id"] == exclude:
                continue
            spent = max(trial.get("execution_seconds", 0), trial.get("progress", {}).get("elapsed_seconds", 0))
            total += max(spent, trial["wall_seconds"]) if trial["status"] in ACTIVE else spent
        return total

    def _check_allocation(self, campaign, seconds, exclude=None, validation=False):
        ceiling = campaign["compute_budget_seconds"]
        if not validation:
            ceiling -= campaign.get("validation_reserve_seconds", 0)
        remaining = ceiling - self.allocated_seconds(campaign["id"], exclude)
        if seconds > remaining + 1e-6:
            raise ValueError(f"Requested allocation exceeds remaining campaign budget ({max(0, remaining):.1f}s available)")

    def create_trial(self, request: TrialInput, validation=None):
        with self.lock:
            campaign = self.store.get(request.campaign_id, "campaign")
            task = self.store.get(request.task_id, "task")
            if task["campaign_id"] != campaign["id"] or (task.get("archived") and validation is None):
                raise ValueError("Select a current task from this campaign")
            if request.algorithm not in {a["id"] for a in ALGORITHMS} | {"validate", "custom"}:
                raise ValueError("Unknown executable algorithm; proposed code must be verified before execution")
            if request.algorithm == "validate" and validation is None:
                raise ValueError("Use the validation endpoint for independent physical validation")
            hypothesis = None
            if request.hypothesis_id:
                hypothesis = self.store.get(request.hypothesis_id, "hypothesis")
                if hypothesis["campaign_id"] != campaign["id"]:
                    raise ValueError("Hypothesis belongs to another campaign")
            if request.algorithm == "custom":
                if not hypothesis or hypothesis.get("implementation_status") != "verified":
                    raise ValueError("Verify the custom hypothesis protocol before launching it")
                source = hypothesis.get("source", "")
                if hashlib.sha256(source.encode()).hexdigest() != hypothesis.get("verification", {}).get("source_hash"):
                    raise ValueError("Source changed after verification; verify a new version")
                if "source" in request.algorithm_config and request.algorithm_config["source"] != source:
                    raise ValueError("Trial source must match the verified hypothesis")
                selected_config = request.algorithm_config or hypothesis.get("algorithm_config", {})
                request = request.model_copy(update={"algorithm_config": {**selected_config, "source": source}})
            if task["split"] == "test" and validation is None:
                if not request.confirmatory or not hypothesis or hypothesis.get("status") != "finalist":
                    raise ValueError("Test tasks require an explicitly confirmatory run of a nominated finalist")
            self._check_allocation(campaign, request.wall_seconds, validation=validation is not None)
            try:
                training = asdict(TrainConfig(**request.training))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid training configuration: {exc}") from exc
            record = request.model_dump()
            record.update(id=identifier("trial"), charter_version=campaign["version"],
                          task_name=task["name"], task_split=task["split"], physics=task["physics"], training=training,
                          schedule_steps=request.schedule_steps or request.max_steps,
                          status="queued", created_at=now(), updated_at=now(),
                          control_revision=0, attempt=0, execution_seconds=0.0,
                          progress={}, result=None, validation=None, parent_trial_id=None,
                          validation_orders=[], validation_tolerance=0.005, archive_size=10)
            if validation:
                record.update(validation)
            confirmation = None
            if task["split"] == "test" and validation is None:
                from .confirmation import prepare_confirmation
                confirmation = prepare_confirmation(self.store, campaign, task, hypothesis, record)
                record.update(confirmation["trial_fields"])
            directory = self.job_dir(record["id"])
            directory.mkdir(parents=True, exist_ok=False)
            if confirmation:
                from .confirmation import commit_confirmation, snapshot_confirmation_code
                record["source_hash"] = snapshot_confirmation_code(directory, record["scientific_source_hash"])
                commit_confirmation(self.store, confirmation)
            atomic_json(directory / "spec.json", record)
            self.store.put("trial", record, "trial.queued")
            return record

    def validate_trial(self, trial_id, request: ValidationInput):
        trial = self.store.get(trial_id, "trial")
        if trial["algorithm"] == "validate":
            raise ValueError("Select an optimization trial")
        archive = trial.get("progress", {}).get("archive") or (trial.get("result") or {}).get("archive") or read_json(self.job_dir(trial_id) / "archive.json", [])
        if isinstance(archive, dict):
            archive = archive.get("designs", archive.get("archive", []))
        if not archive:
            design = trial.get("progress", {}).get("best_design")
            if not design:
                raise ValueError("No completed design is available to validate")
            archive = [design]
        chosen = archive[:request.max_designs]
        return self.create_trial(TrialInput(campaign_id=trial["campaign_id"], task_id=trial["task_id"],
            algorithm="validate", algorithm_config={"designs": chosen}, seed=trial["seed"],
            max_steps=len(chosen)*len(request.orders), wall_seconds=request.wall_seconds,
            question=f"Validate the top {len(chosen)} archived designs across Fourier orders."),
            validation={"parent_trial_id": trial_id, "validation_orders": request.orders,
                        "validation_tolerance": request.tolerance})

    def control(self, trial_id, command: ControlInput):
        with self.lock:
            trial = self.store.get(trial_id, "trial")
            status = trial["status"]
            if trial.get("confirmation_protocol_hash") and (
                (command.max_steps is not None and command.max_steps != trial["max_steps"]) or
                (command.wall_seconds is not None and command.wall_seconds != trial["wall_seconds"])
            ):
                raise ValueError("A confirmatory allocation is frozen; fork a new exploratory trial instead of extending it")
            if command.action == "prioritize":
                if status != "queued":
                    raise ValueError("Only queued trials can be reprioritized")
                trial["priority"] = command.priority if command.priority is not None else trial["priority"] + 1
            elif command.action == "stop":
                if status not in ACTIVE | {"paused", "interrupted"}:
                    return trial
                trial.update(status="stopping" if alive(trial) else "stopped", stop_requested_at=time.time(),
                             stopped_by="researcher", reason="Stopped by researcher")
            elif command.action == "pause":
                if status not in {"queued", "running"}:
                    raise ValueError("Only queued or running trials can be paused")
                trial.update(status="pausing" if alive(trial) else "paused", pause_requested_at=time.time())
            elif command.action in {"resume", "extend"}:
                if status in {"stopping", "pausing"}:
                    raise ValueError("Wait for the pending control command to finish")
                if command.action == "resume" and status not in {"paused", "interrupted", "stopped", "failed"}:
                    raise ValueError("This trial is not resumable in its current state")
                if status not in ACTIVE and trial["attempt"] and not trial.get("progress", {}).get("checkpoint_available"):
                    raise ValueError("No compatible checkpoint exists; create a new trial")
                if trial["attempt"] and trial.get("progress", {}).get("resume_supported") is False:
                    raise ValueError("This worker failure has no consistent resumable state; fork a new trial")
                if command.max_steps is not None:
                    if command.max_steps < trial["max_steps"]:
                        raise ValueError("An extension cannot decrease the original request budget")
                    trial["max_steps"] = command.max_steps
                if command.wall_seconds is not None:
                    if command.wall_seconds < trial["wall_seconds"]:
                        raise ValueError("An extension cannot decrease the original time budget")
                    trial["wall_seconds"] = command.wall_seconds
                if trial["max_steps"] <= trial.get("progress", {}).get("budget_requests", trial.get("progress", {}).get("step", 0)):
                    raise ValueError("Increase evaluation budget before resuming a finished trial")
                if trial["wall_seconds"] <= trial.get("execution_seconds", 0):
                    raise ValueError("Increase time budget before resuming")
                campaign = self.store.get(trial["campaign_id"], "campaign")
                self._check_allocation(campaign, trial["wall_seconds"], exclude=trial_id,
                                       validation=trial["algorithm"] == "validate")
                if status not in LIVE:
                    trial.update(status="queued", stopped_by=None, reason=None)
                # Original schedule_steps never changes when budget is extended.
            trial["control_revision"] += 1
            trial["updated_at"] = now()
            self._write_control(trial)
            self.store.put("trial", trial, "trial.control")
            return trial

    def _write_control(self, trial):
        command = "stop" if trial["status"] in {"stopping", "stopped"} else "pause" if trial["status"] in {"pausing", "paused"} else "run"
        atomic_json(self.job_dir(trial["id"]) / "control.json", {
            "command": command, "max_steps": trial["max_steps"], "wall_seconds": trial["wall_seconds"],
            "revision": trial["control_revision"]})

    def _start_trial(self, trial):
        directory = self.job_dir(trial["id"])
        if trial.get("confirmation_protocol_hash"):
            from .confirmation import scientific_environment, scientific_source_hash
            if scientific_environment() != trial.get("scientific_environment"):
                raise ValueError("Numerical dependencies changed after the confirmation was queued; no worker was started")
            if scientific_source_hash(directory / "code" / "dqn_meent") != trial.get("scientific_source_hash"):
                raise ValueError("The frozen confirmation source snapshot changed; no worker was started")
        if (directory / "result.json").exists():
            (directory / "result.json").rename(directory / f"result.attempt-{trial['attempt']}.json")
        trial["attempt"] += 1
        trial.update(status="running", attempt_started_at=time.time(),
                     prior_execution_seconds=trial.get("execution_seconds", 0), updated_at=now())
        self._write_control(trial)
        # Pin a source snapshot once so later research edits cannot change a running method.
        code = directory / "code"
        if not code.exists():
            source = Path(__file__).resolve().parents[1]
            shutil.copytree(source, code / "dqn_meent", ignore=shutil.ignore_patterns("__pycache__"))
            digest = hashlib.sha256()
            for path in sorted(code.rglob("*.py")):
                digest.update(str(path.relative_to(code)).encode())
                digest.update(path.read_bytes())
            trial["source_hash"] = digest.hexdigest()
        atomic_json(directory / "spec.json", trial)
        env = os.environ.copy()
        # Numerical jobs have no need to inherit LLM credentials.
        for key in list(env):
            if key.startswith("GRATING_LLM_") or key in {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY"}:
                env.pop(key)
        env.update(PYTHONPATH=str(code), PYTHONUNBUFFERED="1", OPENBLAS_NUM_THREADS="1",
                   OMP_NUM_THREADS="1", MKL_NUM_THREADS="1", MPLBACKEND="Agg")
        with (directory / "worker.log").open("ab") as log:
            process = subprocess.Popen([sys.executable, "-m", "dqn_meent.workspace.worker", "--directory", str(directory)],
                cwd=directory, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        trial.update(pid=process.pid, process_identity=process_identity(process.pid))
        self.processes[trial["id"]] = process
        self.store.put("trial", trial, "trial.started")

    def _terminate(self, trial):
        if alive(trial):
            try:
                if os.getpgid(trial["pid"]) == trial["pid"]:
                    os.killpg(trial["pid"], signal.SIGKILL)
            except ProcessLookupError:
                pass

    def reconcile(self):
        with self.lock:
            for trial in self.store.list("trial"):
                if trial["status"] not in LIVE:
                    continue
                directory = self.job_dir(trial["id"])
                progress = read_json(directory / "progress.json")
                changed = False
                if progress and progress != trial.get("progress"):
                    trial["progress"] = progress
                    changed = True
                result = read_json(directory / "result.json")
                process = self.processes.get(trial["id"])
                if process:
                    process.poll()
                is_alive = alive(trial)
                if is_alive:
                    trial["execution_seconds"] = max(trial.get("execution_seconds", 0),
                        trial.get("prior_execution_seconds", 0) + time.time() - trial.get("attempt_started_at", time.time()))
                    if trial["execution_seconds"] >= trial["wall_seconds"] and trial["status"] == "running":
                        trial.update(status="stopping", stopped_by="budget", reason="Wall-time budget reached", stop_requested_at=time.time())
                        self._write_control(trial)
                        changed = True
                    if trial["status"] == "stopping" and time.time() - trial.get("stop_requested_at", time.time()) >= self.stop_grace_seconds:
                        self._terminate(trial)
                if result and not is_alive:
                    trial["result"] = result
                    if not trial.get("reason"):
                        trial["reason"] = result.get("reason")
                    if trial.get("stopped_by") == "researcher":
                        trial["status"] = "stopped"
                    elif trial.get("stopped_by") == "budget":
                        trial["status"] = "budget_exhausted"
                    else:
                        trial["status"] = result.get("status", "completed")
                    if trial["status"] not in {"completed", "paused", "stopped", "failed", "budget_exhausted"}:
                        trial["status"] = "failed"
                        trial["reason"] = "Worker returned an invalid terminal status"
                    trial["finished_at"] = now()
                    trial["execution_seconds"] = max(trial.get("execution_seconds", 0), result.get("elapsed_seconds", 0))
                    if trial["algorithm"] == "validate" and trial.get("parent_trial_id"):
                        parent = self.store.get(trial["parent_trial_id"], "trial")
                        parent["validation"] = {"trial_id": trial["id"], "status": trial["status"], **result}
                        self.store.put("trial", parent, "trial.validated")
                    self.store.event(trial["campaign_id"], "trial.finished", {"trial_id": trial["id"], "status": trial["status"]})
                    trial["research_pending"] = True
                    changed = True
                elif not is_alive:
                    trial["status"] = "stopped" if trial.get("stopped_by") == "researcher" else "budget_exhausted" if trial.get("stopped_by") == "budget" else "interrupted"
                    trial["reason"] = trial.get("reason") or "Worker exited without a terminal record; inspect logs or resume its checkpoint"
                    trial["finished_at"] = now()
                    self.store.event(trial["campaign_id"], "trial.interrupted", {"trial_id": trial["id"]})
                    changed = True
                trial["updated_at"] = now()
                self.store.put("trial", trial, "trial.progress" if changed else None)

    def _loop(self):
        while not self.shutdown_event.wait(0.4):
            try:
                self.reconcile()
                with self.lock:
                    trials = self.store.list("trial")
                    free = self.max_workers - sum(t["status"] in LIVE for t in trials)
                    queued = sorted((t for t in trials if t["status"] == "queued"), key=lambda t: (-t["priority"], t["created_at"]))
                    for trial in queued[:max(0, free)]:
                        try:
                            self._start_trial(trial)
                        except Exception as exc:
                            trial.update(status="failed", reason=str(exc), finished_at=now())
                            self.store.put("trial", trial, "trial.failed")
                if self.on_trial_finished:
                    self.on_trial_finished()
            except Exception as exc:
                self.store.event(None, "service.error", {"message": str(exc)})

    def metrics(self, trial_id):
        self.store.get(trial_id, "trial")
        path = self.job_dir(trial_id) / "metrics.jsonl"
        if not path.exists():
            return []
        rows = []
        for line in path.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue  # An active writer can leave one incomplete final line.
        return rows
