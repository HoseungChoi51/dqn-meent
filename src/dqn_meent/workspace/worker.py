"""Isolated MEENT experiment process with durable progress and cooperative control.

Run as ``python -m dqn_meent.workspace.worker --directory PATH``. The directory
contains a trusted ``spec.json`` and optional ``control.json``; the worker has no
database dependency. Checkpoints are trusted local pickle files, never uploads.
One step is one objective evaluation request, including DQN episode resets and
cache hits. A running RCWA call cannot be preempted cooperatively; control is
checked before each request and after each completed optimizer update.
"""
from dataclasses import replace
from datetime import datetime, timezone
import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import pickle
import signal
import time
import traceback
import uuid

import numpy as np
from threadpoolctl import threadpool_limits

from ..config import PhysicsConfig
from ..physics import ForwardSolver, validate_design
from .optimizers import make_optimizer


CHECKPOINT_VERSION = 1


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, allow_nan=False, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _integer(value, name, minimum=1):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _seconds(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
        raise ValueError("wall_seconds must be a positive finite number or null")
    return float(value)


def _fingerprint(spec):
    # Budget/control changes preserve scientific identity and the declared
    # schedules. Every other specification field must be compatible on resume.
    scientific_fields = ("id", "campaign_id", "charter_version", "task_id", "algorithm",
                         "algorithm_config", "seed", "schedule_steps", "physics", "training",
                         "validation_orders", "validation_tolerance", "archive_size", "source_hash")
    identity = {key: spec[key] for key in scientific_fields if key in spec}
    return hashlib.sha256(json.dumps(identity, sort_keys=True, allow_nan=False).encode()).hexdigest()


class ExperimentWorker:
    def __init__(self, directory, *, solver_factory=ForwardSolver):
        self.directory = Path(directory).resolve()
        self.started = time.perf_counter()
        self.elapsed_before = 0.
        self.spec = json.loads((self.directory / "spec.json").read_text())
        self.spec.setdefault("schedule_steps", self.spec.get("max_steps"))
        self.spec_hash = _fingerprint(self.spec)
        self.physics = PhysicsConfig(**self.spec.get("physics", {}))
        self.algorithm = self.spec["algorithm"]
        self.max_steps = _integer(self.spec["max_steps"], "max_steps")
        self.schedule_steps = _integer(self.spec.get("schedule_steps", self.max_steps), "schedule_steps")
        self.wall_seconds = _seconds(self.spec.get("wall_seconds"))
        self.archive_size = _integer(self.spec.get("archive_size", 8), "archive_size")
        self.control_revision = -1
        self.last_command = "run"
        self.signal_command = None
        self.step = 0
        self.efficiency = None
        self.archive_pool = []
        self.validation_observations = []
        self.validation_designs, self.orders = [], []
        self.last_metric = None
        self.optimizer = None
        self.solvers = {}
        self.solver_factory = solver_factory
        self.current_order = self.physics.fourier_order
        self.interrupted_requests = 0
        self.unconfirmed_solver_calls = 0
        self.last_recovered_request = None
        self.validation_tolerance = self.spec.get("validation_tolerance", .005)
        if (isinstance(self.validation_tolerance, bool) or not isinstance(self.validation_tolerance, (int, float)) or
                not math.isfinite(self.validation_tolerance) or self.validation_tolerance <= 0):
            raise ValueError("validation_tolerance must be finite and positive")
        if self.algorithm == "validate":
            designs = self.spec.get("algorithm_config", {}).get("designs", [])
            if not isinstance(designs, list) or not designs:
                raise ValueError("Validation requires algorithm_config.designs")
            self.validation_designs = [validate_design(item.get("design") if isinstance(item, dict) else item,
                                                       self.physics.n_cells) for item in designs]
            orders = self.spec.get("validation_orders", [])
            if not orders:
                raise ValueError("Validation requires validation_orders")
            self.orders = sorted(set(_integer(order, "validation order") for order in orders))
        else:
            self.optimizer = make_optimizer(self.algorithm, self.physics.n_cells,
                                            _integer(self.spec.get("seed", 0), "seed", 0),
                                            self.schedule_steps, self.spec.get("algorithm_config"),
                                            self.spec.get("training"))
        checkpoint = self.directory / "checkpoint.pkl"
        previous_result = self.directory / "result.json"
        if previous_result.exists():
            previous = json.loads(previous_result.read_text())
            if previous.get("status") == "failed" and not previous.get("resume_supported", False):
                raise ValueError("The previous failure has no resumable state; create a new trial")
        if checkpoint.exists():
            self.restore(checkpoint)
        self._recover_inflight()
        self._restore_metrics()

    def elapsed(self):
        return self.elapsed_before + time.perf_counter() - self.started

    def solver(self, order):
        if order not in self.solvers:
            self.solvers[order] = self.solver_factory(replace(self.physics, fourier_order=order))
        return self.solvers[order]

    def counters(self):
        return {key: sum(getattr(solver, key) for solver in self.solvers.values())
                for key in ("solver_calls", "cache_hits", "evaluations")}

    def archive(self):
        # Prefer separated designs, but never reject the best observed device.
        chosen = []
        minimum_distance = max(1, self.physics.n_cells // 16)
        for item in sorted(self.archive_pool, key=lambda item: item["efficiency"], reverse=True):
            if all(sum(a != b for a, b in zip(item["design"], previous["design"], strict=True)) >= minimum_distance
                   for previous in chosen):
                chosen.append(item)
            if len(chosen) >= self.archive_size:
                break
        return chosen

    def record_design(self, design, result, order):
        values = design.tolist()
        entry = {"design": values, "efficiency": float(result.efficiency),
                 "fourier_order": order, "step": self.step,
                 "numerical_status": "screening" if self.algorithm != "validate" else "validation_pending"}
        # Each validation design is represented by its highest completed order.
        self.archive_pool = [item for item in self.archive_pool if item["design"] != values]
        self.archive_pool.append(entry)
        self.archive_pool.sort(key=lambda item: item["efficiency"], reverse=True)
        self.archive_pool = self.archive_pool[:max(self.archive_size * 8, self.archive_size)]

    def validation_summary(self):
        summaries = []
        for index, design in enumerate(self.validation_designs):
            observations = sorted((item for item in self.validation_observations if item["design_index"] == index),
                                  key=lambda item: item["fourier_order"])
            last = observations[-1] if observations else None
            delta = (abs(observations[-1]["efficiency"] - observations[-2]["efficiency"])
                     if len(observations) > 1 else None)
            complete = len(observations) == len(self.orders)
            summaries.append({"design_index": index, "design": design.tolist(), "observations": observations,
                              "efficiency": last["efficiency"] if last else None,
                              "fourier_order": last["fourier_order"] if last else None,
                              "delta": delta, "complete": complete,
                              "converged": bool(complete and delta is not None and delta <= self.validation_tolerance),
                              "numerical_status": ("converged" if complete and delta is not None and delta <= self.validation_tolerance
                                                   else "unconverged" if complete and delta is not None else "insufficient_orders"),
                              "tolerance": self.validation_tolerance})
        return summaries

    def progress(self, status, reason=None):
        archive = self.archive()
        validation = self.validation_summary() if self.algorithm == "validate" else None
        if validation is not None:
            summaries = {tuple(item["design"]): item for item in validation}
            archive = [dict(item, numerical_status=summaries[tuple(item["design"])]["numerical_status"],
                            converged=summaries[tuple(item["design"])]["converged"],
                            delta=summaries[tuple(item["design"])]["delta"]) for item in archive]
        result = {"id": self.spec.get("id"), "campaign_id": self.spec.get("campaign_id"),
                  "task_id": self.spec.get("task_id"), "charter_version": self.spec.get("charter_version"),
                  "algorithm": self.algorithm, "seed": self.spec.get("seed", 0),
                  "step": self.step, "efficiency": self.efficiency,
                  "best_efficiency": archive[0]["efficiency"] if archive else None,
                  "best_design": archive[0]["design"] if archive else None,
                  **self.counters(), "elapsed_seconds": self.elapsed(),
                  "interrupted_requests": self.interrupted_requests,
                  "unconfirmed_solver_calls": self.unconfirmed_solver_calls,
                  "budget_requests": self.counters()["evaluations"] + self.interrupted_requests,
                  "checkpoint_available": (self.directory / "checkpoint.pkl").exists(),
                  "status": status, "reason": reason, "archive": archive,
                  "updated_at": datetime.now(timezone.utc).isoformat(),
                  "pid": os.getpid(), "max_steps": self.max_steps,
                  "wall_seconds": self.wall_seconds, "schedule_steps": self.schedule_steps,
                  "control_revision": self.control_revision,
                  "fourier_order": self.current_order,
                  "numerical_status": "screening" if self.algorithm != "validate" else "validation",
                  "diagnostics": self.optimizer.diagnostics() if self.optimizer else {"validation_evaluations": self.step}}
        if self.algorithm == "validate":
            result["validation"] = validation
        return result

    def save_checkpoint(self):
        state = {"version": CHECKPOINT_VERSION, "spec_hash": self.spec_hash,
                 "step": self.step, "efficiency": self.efficiency,
                 "archive_pool": self.archive_pool, "elapsed_seconds": self.elapsed(),
                 "validation_observations": self.validation_observations,
                 "current_order": self.current_order,
                 "optimizer": self.optimizer.state_dict() if self.optimizer else None,
                 "solvers": {order: solver.state_dict() for order, solver in self.solvers.items()},
                 "last_metric": self.last_metric, "control_revision": self.control_revision,
                 "last_command": self.last_command,
                 "max_steps": self.max_steps, "wall_seconds": self.wall_seconds,
                 "interrupted_requests": self.interrupted_requests,
                 "unconfirmed_solver_calls": self.unconfirmed_solver_calls,
                 "last_recovered_request": self.last_recovered_request}
        temporary = self.directory / "checkpoint.pkl.tmp"
        with temporary.open("wb") as stream:
            pickle.dump(state, stream, protocol=pickle.HIGHEST_PROTOCOL)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.directory / "checkpoint.pkl")

    def restore(self, path):
        with path.open("rb") as stream:
            state = pickle.load(stream)  # trusted, service-created local checkpoint
        if state.get("version") != CHECKPOINT_VERSION or state.get("spec_hash") != self.spec_hash:
            raise ValueError("Checkpoint is incompatible with this scientific specification")
        self.step = state["step"]
        self.efficiency = state["efficiency"]
        self.archive_pool = state["archive_pool"]
        self.validation_observations = state["validation_observations"]
        self.current_order = state["current_order"]
        self.elapsed_before = state["elapsed_seconds"]
        # Include checkpoint/output overhead already reported by the last
        # process; resuming never erases charged execution time.
        for name in ("progress.json", "result.json"):
            if (self.directory / name).exists():
                try:
                    reported = json.loads((self.directory / name).read_text())
                    self.elapsed_before = max(self.elapsed_before, float(reported.get("elapsed_seconds", 0)))
                except (ValueError, OSError):
                    pass
        self.last_metric = state.get("last_metric")
        self.control_revision = state.get("control_revision", -1)
        self.last_command = state.get("last_command", "run")
        self.max_steps = state.get("max_steps", self.max_steps)
        self.wall_seconds = state.get("wall_seconds", self.wall_seconds)
        self.interrupted_requests = state.get("interrupted_requests", 0)
        self.unconfirmed_solver_calls = state.get("unconfirmed_solver_calls", 0)
        self.last_recovered_request = state.get("last_recovered_request")
        if self.optimizer:
            self.optimizer.load_state_dict(state["optimizer"])
        for order, solver_state in state["solvers"].items():
            self.solver(order).load_state_dict(solver_state)

    def _recover_inflight(self):
        path = self.directory / "inflight.json"
        if not path.exists():
            return
        request = json.loads(path.read_text())
        if request["spec_hash"] != self.spec_hash:
            raise ValueError("In-flight work belongs to an incompatible specification")
        if request["step"] > self.step and request["request_id"] != self.last_recovered_request:
            # A forced kill leaves the actual completion unknowable. Report the
            # uncertainty explicitly and reserve its cost against the budget.
            self.interrupted_requests += 1
            self.unconfirmed_solver_calls += int(request["solver_call"])
            self.last_recovered_request = request["request_id"]
            self.save_checkpoint()
        path.unlink()

    def _restore_metrics(self):
        path = self.directory / "metrics.jsonl"
        records = {}
        if path.exists():
            for line in path.read_text().splitlines():
                try:
                    item = json.loads(line)
                    if 0 < int(item["step"]) <= self.step:
                        records[int(item["step"])] = item
                except (ValueError, KeyError, TypeError):
                    continue
        if self.last_metric and self.step > 0:
            records[self.step] = self.last_metric
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text("".join(json.dumps(item, allow_nan=False) + "\n" for _, item in sorted(records.items())))
        temporary.replace(path)

    def control(self):
        if self.signal_command:
            self.last_command = self.signal_command
            return self.signal_command
        path = self.directory / "control.json"
        if not path.exists():
            return self.last_command
        data = json.loads(path.read_text())
        command = data.get("command", "run")
        if command not in {"run", "pause", "stop"}:
            raise ValueError("Unknown worker control command")
        revision = _integer(data.get("revision", 0), "control revision", 0)
        if revision <= self.control_revision:
            return self.last_command
        self.control_revision = revision
        self.last_command = command
        self.max_steps = _integer(data.get("max_steps", self.max_steps), "max_steps")
        self.wall_seconds = _seconds(data.get("wall_seconds", self.wall_seconds))
        return command

    def one_step(self):
        design_index = None
        if self.algorithm == "validate":
            design_index, order_index = divmod(self.step, len(self.orders))
            design = self.validation_designs[design_index]
            self.current_order = self.orders[order_index]
        else:
            design = validate_design(self.optimizer.ask(), self.physics.n_cells)
        solver = self.solver(self.current_order)
        atomic_json(self.directory / "inflight.json", {
            "request_id": uuid.uuid4().hex, "spec_hash": self.spec_hash,
            "step": self.step + 1, "solver_call": np.packbits(design).tobytes() not in solver._cache,
            "started_at": datetime.now(timezone.utc).isoformat()})
        result = solver.evaluate(design)
        if not math.isfinite(result.efficiency) or not 0 <= result.efficiency <= 1 + self.physics.energy_tolerance:
            raise FloatingPointError("Solver returned an invalid physical efficiency")
        self.step += 1
        self.efficiency = float(result.efficiency)
        self.record_design(design, result, self.current_order)
        if self.algorithm == "validate":
            self.validation_observations.append({"design_index": design_index,
                                                 "fourier_order": self.current_order, **result.to_dict()})
        else:
            self.optimizer.tell(design, self.efficiency)

    def run(self):
        status, reason = "running", None
        atomic_json(self.directory / "progress.json", self.progress(status))
        try:
            while True:
                command = self.control()
                if command != "run":
                    status, reason = ("paused", "researcher_pause") if command == "pause" else ("stopped", "researcher_stop")
                    break
                if self.counters()["evaluations"] + self.interrupted_requests >= self.max_steps:
                    status, reason = "completed", "step_budget_exhausted"
                    break
                if self.wall_seconds is not None and self.elapsed() >= self.wall_seconds:
                    status, reason = "completed", "wall_budget_exhausted"
                    break
                if self.algorithm == "validate" and self.step >= len(self.validation_designs) * len(self.orders):
                    status, reason = "completed", "validation_complete"
                    break
                self.one_step()
                self.last_metric = self.progress("running")
                # Checkpoint before publishing evidence. Recovery can repair the
                # last JSONL row from the checkpoint if a process dies between.
                self.save_checkpoint()
                (self.directory / "inflight.json").unlink(missing_ok=True)
                self.last_metric["checkpoint_available"] = True
                self.last_metric["elapsed_seconds"] = self.elapsed()
                with (self.directory / "metrics.jsonl").open("a") as stream:
                    stream.write(json.dumps(self.last_metric, allow_nan=False) + "\n")
                    stream.flush()
                atomic_json(self.directory / "progress.json", self.last_metric)
        except Exception as exc:
            status, reason = "failed", f"{type(exc).__name__}: {exc}"
            (self.directory / "error.txt").write_text(traceback.format_exc())
        # Failed trials preserve measured counters and observations, but do not
        # advertise a resumable state after an incomplete optimizer update.
        if status != "failed":
            self.save_checkpoint()
        result = self.progress(status, reason)
        result["resume_supported"] = status != "failed"
        atomic_json(self.directory / "progress.json", result)
        atomic_json(self.directory / "result.json", result)
        return result


def run(directory, *, solver_factory=ForwardSolver):
    """Run one owned worker, returning its final snapshot (also for local tests)."""
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "worker.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another process already owns this trial") from exc
        lock.seek(0)
        lock.truncate()
        lock.write(str(os.getpid()))
        lock.flush()
        try:
            with threadpool_limits(limits=1):
                worker = ExperimentWorker(directory, solver_factory=solver_factory)
                handlers = {}
                # A unit test may run in a thread, where Python disallows
                # installing signal handlers. File controls still function.
                import threading
                if threading.current_thread() is threading.main_thread():
                    for number in (signal.SIGTERM, signal.SIGINT):
                        handlers[number] = signal.getsignal(number)
                        signal.signal(number, lambda signum, frame: setattr(worker, "signal_command", "stop"))
                try:
                    return worker.run()
                finally:
                    for number, previous in handlers.items():
                        signal.signal(number, previous)
        except Exception as exc:
            # Bad specifications and incompatible checkpoints still produce
            # inspectable terminal records. Never overwrite an old checkpoint.
            result = {"status": "failed", "reason": f"{type(exc).__name__}: {exc}",
                      "step": 0, "efficiency": None, "best_efficiency": None,
                      "best_design": None, "evaluations": 0, "solver_calls": 0,
                      "cache_hits": 0, "elapsed_seconds": 0.,
                      "interrupted_requests": 0, "unconfirmed_solver_calls": 0, "budget_requests": 0,
                      "checkpoint_available": (directory / "checkpoint.pkl").exists(),
                      "resume_supported": False, "archive": [],
                      "updated_at": datetime.now(timezone.utc).isoformat(), "pid": os.getpid()}
            # Preserve visible expenditure if a malformed restart fails early.
            if (directory / "progress.json").exists():
                try:
                    previous = json.loads((directory / "progress.json").read_text())
                    for key in ("step", "efficiency", "best_efficiency", "best_design", "evaluations",
                                "solver_calls", "cache_hits", "elapsed_seconds", "archive"):
                        if key in previous:
                            result[key] = previous[key]
                    for key in ("interrupted_requests", "unconfirmed_solver_calls", "budget_requests"):
                        result[key] = previous.get(key, 0)
                except (ValueError, OSError):
                    pass
            (directory / "error.txt").write_text(traceback.format_exc())
            atomic_json(directory / "progress.json", result)
            atomic_json(directory / "result.json", result)
            return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    args = parser.parse_args(argv)
    result = run(args.directory)
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
