"""Protected implementation checks. These results are never experiment evidence."""
from __future__ import annotations

from dataclasses import asdict
import math
from pathlib import Path
import time

from optimization_framework.implementations.models import BoundOptimizerSpec, ImplementationSpec, check_parameters, digest
from optimization_framework.implementations.runtime import PackageOptimizer, file_hash
from optimization_framework.evaluation.registry import problems
from optimization_framework.evaluation.legacy_confirmation import physical_condition_key, scientific_environment, scientific_source_hash
from optimization_framework.storage.sqlite import identifier, now


def profile_identity():
    root = Path(__file__).parent
    return digest({name: file_hash(root / name) for name in ("validation.py", "protected_diagnostics.py", "runtime.py", "models.py", "revalidation.py")})


def evaluator_identity():
    return digest({"source": scientific_source_hash(), "environment": scientific_environment()})


def context(spec, n_cells, seed, parameters=None, *, registry=None):
    values = dict(spec.parameters if parameters is None else parameters)
    check_parameters(values, spec.parameter_schema)
    instance = (registry or problems).get(spec.problem_id).implementation_fixture(spec.problem_configuration, n_cells)
    descriptor = instance.descriptor()
    descriptor["extra_metrics"] = [metric.model_dump(mode="json") for metric in instance.extra_metrics]
    return {"n_cells": n_cells, "seed": seed, "parameters": values, "schedule_steps": 64,
            "capabilities": instance.capabilities, "problem": descriptor}


def validate_package(spec, package, package_dir, runtime_root, runtime, *, progress=lambda: None, registry=None, evaluator_digest=None):
    spec = spec.model_dump() if isinstance(spec, ImplementationSpec) else spec
    spec = (BoundOptimizerSpec if spec.get("evaluator_version_id") else ImplementationSpec).model_validate(spec)
    registry = registry or problems
    started = time.monotonic()
    report = {"id": identifier("validation"), "created_at": now(), "kind": "implementation_correctness",
              "profile_digest": profile_identity(), "evaluator_digest": evaluator_digest or evaluator_identity(),
              "runtime_digest": runtime["digest"], "spec_digest": digest(spec.model_dump()),
              "checks": [], "passed": False, "exposed_conditions": [],
              "costs": {"evaluation_requests": 0, "solver_executions": 0},
              "scope": {"capabilities": spec.capabilities, "n_cells_min": spec.n_cells_min,
                        "n_cells_max": spec.n_cells_max, "parameter_schema": spec.parameter_schema,
                        "execution_capabilities": spec.execution_capabilities.model_dump(mode="json"),
                        "problem_id": spec.problem_id},
              "limitations": "Protocol, declared behavior, reproducibility, and integration checks; no optimizer performance or convergence claim."}
    if isinstance(spec, BoundOptimizerSpec):
        report["scope"]["evaluator_version_id"] = spec.evaluator_version_id
        adapter = registry.get(spec.problem_id)
        report["scope"]["evaluator_capabilities"] = adapter.describe().capabilities
        from optimization_framework.implementations.evaluator_validation import current
        if not current(adapter.bundle["version"]):
            report["scope"]["evaluator_numerical_status"] = "unverified"
            report["limitations"] += " The dependency evaluator passed contract checks only; its numerical correctness remains unverified."

    def create(n, seed, parameters=None):
        progress()
        return PackageOptimizer(package_dir, runtime_root, runtime, package["entrypoint"],
                                context(spec, n, seed, parameters, registry=registry), progress=progress,
                                max_checkpoint_bytes=spec.max_checkpoint_bytes, contract=package.get("contract", "ask_tell"),
                                supports_failure_observations=spec.supports_failure_observations)

    def check(name, function):
        progress()
        try:
            detail = function()
        except Exception as exc:
            report["checks"].append({"name": name, "passed": False, "detail": f"{type(exc).__name__}: {exc}"[:2000]})
            raise
        report["checks"].append({"name": name, "passed": True, "detail": detail})

    try:
        def replay():
            sizes = sorted({spec.n_cells_min, min(max(8, spec.n_cells_min), spec.n_cells_max), spec.n_cells_max})
            for n in sizes:
                for seed in (0, 7):
                    first = create(n, seed)
                    second = None
                    try:
                        second = create(n, seed)
                        for i in range(6):
                            a, b = first.ask(), second.ask()
                            first.candidate_schema.canonicalize(a)
                            second.candidate_schema.canonicalize(b)
                            if a.tolist() != b.tolist():
                                raise ValueError("Seeded proposals are not reproducible")
                            score = float((a.sum() + i) % (n + 1)) / (n + 1)
                            first.tell(a, score)
                            second.tell(b, score)
                        state = first.state_dict()
                        expected = first.ask().tolist()
                        second.load_state_dict(state)
                        if second.ask().tolist() != expected:
                            raise ValueError("Checkpoint restoration changes the next proposal")
                    finally:
                        first.close()
                        if second:
                            second.close()
            return {"sizes": sizes, "seeds": [0, 7], "steps": 6}
        check("seeded replay and checkpoint continuation", replay)

        def mechanism_invariants():
            if package.get("contract") != "optimizer_v1":
                raise ValueError("Mechanism state checks require optimizer_v1 inspect()")
            n = spec.n_cells_min
            optimizer = create(n, 23)
            try:
                for index in range(12):
                    candidate = optimizer.ask()
                    optimizer.tell(candidate, (index % 5) / 5)
                    snapshot = optimizer.inspect()
                    for case in spec.mechanism_checks:
                        check_invariant(snapshot, case)
                return {"steps": 12, "checks": [case.model_dump() for case in spec.mechanism_checks]}
            finally:
                optimizer.close()
        if spec.mechanism_checks:
            check("frozen mechanism state invariants", mechanism_invariants)

        def diagnostic_capabilities():
            declaration = spec.execution_capabilities
            if package.get("contract", "ask_tell") != "optimizer_v1":
                raise ValueError("Declared diagnostic exports and decision counters require optimizer_v1")
            n = min(max(8, spec.n_cells_min), spec.n_cells_max)
            optimizer = create(n, 19)
            try:
                last = 0
                for index in range(6):
                    candidate = optimizer.ask()
                    optimizer.tell(candidate, index / 6)
                    if "optimizer_decisions" in declaration.completion_units:
                        decisions = optimizer.inspect().get("decisions")
                        if type(decisions) is not int or decisions < last:
                            raise ValueError("The declared decision counter must be a nonnegative monotonic integer")
                        last = decisions
                before = optimizer.state_dict()
                exports = optimizer.export_artifacts()
                if not isinstance(exports, list) or any(not isinstance(item, dict) or "data" not in item or
                        not isinstance(item.get("metadata", {}), dict) for item in exports):
                    raise ValueError("Artifact exports must contain serializable payloads")
                for required in declaration.exports:
                    if sum(required.accepts(item) for item in exports) != 1:
                        raise ValueError("The implementation did not publish exactly one artifact in each declared format")
                after_export = [value.model_dump(mode="json") for value in optimizer.propose(1)]
                optimizer.load_state_dict(before)
                if [value.model_dump(mode="json") for value in optimizer.propose(1)] != after_export:
                    raise ValueError("Exporting diagnostic artifacts changes the next search proposal")
                return {"declaration": declaration.model_dump(mode="json"), "last_observed_decisions": last, "formats": [
                    {key: item.get(key) for key in ("kind", "format", "metadata")} for item in exports]}
            finally:
                optimizer.close()
        if spec.execution_capabilities.exports or "optimizer_decisions" in spec.execution_capabilities.completion_units:
            check("declared diagnostic capabilities and independent export", diagnostic_capabilities)

        if spec.diagnostic_checks:
            from optimization_framework.implementations import protected_diagnostics as fixtures
            if package.get("contract", "ask_tell") != "optimizer_v1":
                raise ValueError("Protected diagnostic fixtures require optimizer_v1")
            for case in spec.diagnostic_checks:
                if case.kind == "h12_replay_context":
                    check(case.name, lambda: fixtures.replay_context(create))
                else:
                    def diagnostic(case=case):
                        optimizer = create(256 * 128, 419)
                        try:
                            if case.kind == "h12_fourier_decoder":
                                return fixtures.fourier_decoder(optimizer)
                            if case.kind == "h12_covariance_refactor":
                                return fixtures.covariance_refactor(optimizer)
                            return fixtures.cache_identity(optimizer)
                        finally:
                            optimizer.close()
                    check(case.name, diagnostic)

        for case in spec.behavior_checks:
            def behavior(case=case):
                optimizer = create(case.n_cells, case.seed, {**spec.parameters, **case.parameters})
                incumbent, best, seen, designs = None, -math.inf, set(), []
                try:
                    for index, score in enumerate(case.efficiencies):
                        design = optimizer.ask().tolist()
                        optimizer.candidate_schema.canonicalize(design)
                        designs.append(design)
                        if case.assertion == "binary" and any(value not in (0, 1) for value in design):
                            raise ValueError("The specification requires binary candidates")
                        if case.assertion in {"one_bit_from_incumbent", "one_coordinate_from_incumbent"} and incumbent is not None:
                            if sum(a != b for a, b in zip(design, incumbent)) != 1:
                                raise ValueError("Proposal is not one bit from the best observed incumbent")
                        if case.assertion == "unique_proposals" and tuple(design) in seen:
                            raise ValueError("Repeated proposal violates the specification")
                        if case.assertion == "exact_designs":
                            if index >= len(case.expected_designs) or design != case.expected_designs[index]:
                                raise ValueError("Proposal differs from the specified reference sequence")
                        seen.add(tuple(design))
                        optimizer.tell(design, score)
                        if score > best:
                            best, incumbent = score, design
                finally:
                    optimizer.close()
                return {"assertion": case.assertion, "designs": designs, "efficiencies": case.efficiencies}
            check(case.name, behavior)

        def numerical():
            n = min(max(8, spec.n_cells_min), spec.n_cells_max)
            instance = registry.get(spec.problem_id).implementation_fixture(spec.problem_configuration, n)
            solver = registry.evaluator(instance)
            optimizer = None
            observations = []
            solver_calls = cache_hits = 0
            try:
                optimizer = create(n, 11)
                for _ in range(4):
                    design = optimizer.ask()
                    instance.candidate_schema.canonicalize(design)
                    report["costs"]["evaluation_requests"] += 1
                    try:
                        result = solver.evaluate(design)
                    except Exception:
                        report["costs"]["solver_executions"] = None
                        raise
                    report["costs"]["solver_executions"] += result.solver_executions
                    # ForwardResult and solver accounting remain trusted application data.
                    efficiency = result.objectives[instance.primary_objective.name]
                    if not math.isfinite(efficiency):
                        raise ValueError("Evaluator returned a nonfinite objective")
                    if optimizer.contract == "optimizer_v1":
                        from optimization_framework.contracts.problems import Observation
                        pending = optimizer.pending[0]
                        optimizer.observe([Observation(id=f"physical_{len(observations)}", experiment_id="correctness_physical",
                            attempt_id="correctness_physical", request_id=f"physical_{len(observations)}",
                            proposal_id=pending.id, candidate=design.tolist(), status="ok",
                            objectives=result.objectives, fidelity=instance.fidelity,
                            evaluator_identity=instance.evaluation_identity,
                            costs={"solver_executions": result.solver_executions})])
                    else:
                        optimizer.tell(design, instance.primary_objective.utility(efficiency))
                    solver_calls += result.solver_executions
                    cache_hits += int(result.cache_hit)
                    observations.append({"candidate": design.tolist(), "objectives": result.objectives})
            finally:
                if optimizer is not None:
                    optimizer.close()
                if hasattr(solver, "close"):
                    solver.close()
            if len(observations) != 4 or solver_calls < 0 or not 0 <= cache_hits <= 4:
                raise ValueError("Evaluator accounting mismatch")
            report["exposed_conditions"].append(instance.scientific_identity)
            return {"problem": instance.model_dump(mode="json"), "observations": observations,
                    "evaluations": 4, "solver_calls": solver_calls, "cache_hits": cache_hits}
        check("bounded trusted evaluator integration", numerical)
        report["passed"] = True
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"[:2000]
    report["elapsed_seconds"] = time.monotonic() - started
    return report


def check_invariant(snapshot, case):
    """Evaluate an independently specified invariant without candidate callbacks."""
    import numpy as np
    def select(pointer):
        value = snapshot
        if not pointer.startswith("/"):
            raise ValueError("Invariant requires a JSON pointer")
        for token in pointer[1:].split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            value = value[int(token)] if isinstance(value, list) else value[token]
        result = np.asarray(value, dtype=float)
        if result.size > 1000000 or not np.isfinite(result).all():
            raise ValueError("Invariant state must be bounded and finite")
        return result
    value = select(case.pointer)
    tolerance = case.tolerance
    if case.assertion == "finite":
        valid = True
    elif case.assertion == "nonnegative":
        valid = bool((value >= -tolerance).all())
    elif case.assertion == "unit_norm":
        valid = abs(float(np.linalg.norm(value)) - 1) <= tolerance
    elif case.assertion == "positive_semidefinite":
        valid = (value.ndim == 2 and value.shape[0] == value.shape[1] and
            np.allclose(value, value.T, atol=tolerance, rtol=0) and np.linalg.eigvalsh(value).min() >= -tolerance)
    elif case.assertion == "tangent":
        if not case.reference_pointer:
            raise ValueError("Tangency check requires a reference vector pointer")
        valid = float(np.linalg.norm(value @ select(case.reference_pointer))) <= tolerance
    else:
        if case.maximum_rank is None:
            raise ValueError("Rank check requires maximum_rank")
        valid = np.linalg.matrix_rank(value, tol=tolerance) <= case.maximum_rank
    if not valid:
        raise ValueError(f"Mechanism invariant failed: {case.name}")
