"""Independent numerical fixtures and lifecycle checks for evaluator packages."""
from pathlib import Path
import math
import time

from optimization_framework.contracts.evaluators import EvaluatorManifest
from optimization_framework.implementations.evaluator_runtime import PackageEvaluator
from optimization_framework.implementations.models import EvaluatorSpec, digest
from optimization_framework.implementations.runtime import file_hash
from optimization_framework.storage.sqlite import identifier, now


def profile_identity():
    root = Path(__file__).parents[1]
    return digest({name: file_hash(root / name) for name in (
        "contracts/base.py", "contracts/problems.py", "contracts/evaluators.py", "implementations/models.py",
        "implementations/runtime.py", "implementations/evaluator_runtime.py", "implementations/evaluator_validation.py",
        "implementations/revalidation.py")})


def evaluator_identity(spec):
    return digest({"manifest": spec.manifest.model_dump(mode="json"), "contract": spec.contract})


def current(version, *, numerical=True):
    from optimization_framework.implementations.revalidation import report_matches
    spec = EvaluatorSpec.model_validate(version["spec"])
    report = version["validation_report"]
    contract_only = report.get("kind") == "evaluator_contract"
    return (version.get("status") in {"validated", "contract_validated"}
            and not version.get("blocking_validation_report_ids")
            and not (numerical and contract_only)
            and report.get("kind") == ("evaluator_contract" if contract_only else "evaluator_correctness")
            and report.get("passed") is True
            and (not contract_only or report.get("numerical_status") == "unverified")
            and report.get("profile_digest") == profile_identity()
            and report.get("evaluator_digest") == evaluator_identity(spec)
            and report_matches(version, report)
            and report.get("runtime_digest") == version["runtime_digest"])


def validate_package(spec, package, package_dir, runtime_root, runtime, *, progress=lambda: None):
    spec = EvaluatorSpec.model_validate(spec)
    started = time.monotonic()
    contract_only = spec.validation_mode == "contract_only"
    cases = spec.contract_cases if contract_only else [*spec.correctness_cases, *spec.contract_cases]
    report = {"id": identifier("validation"), "created_at": now(), "kind": "evaluator_contract" if contract_only else "evaluator_correctness",
        "profile_digest": profile_identity(), "evaluator_digest": evaluator_identity(spec),
        "runtime_digest": runtime["digest"], "spec_digest": digest(spec.model_dump()),
        "checks": [], "passed": False, "exposed_conditions": [],
        "costs": {"evaluation_requests": 0, "solver_executions": 0},
        "scope": {"manifest": spec.manifest.model_dump(mode="json"),
                  "fixture_digest": digest([case.model_dump() for case in spec.correctness_cases])},
        "limitations": "Independent supplied numerical fixtures and executable lifecycle checks; not a proof of correctness outside these cases. Cost counts isolated evaluator invocations."}
    if contract_only:
        report.update(numerical_status="unverified", limitations="Only output contracts, declared determinism and checkpoint continuation were checked. No independent numerical oracle or numerical correctness claim.")
        report["scope"].update(validation_mode="contract_only", probe_digest=digest([case.model_dump() for case in cases]))

    def create(instance):
        progress()
        return PackageEvaluator(package_dir, runtime_root, runtime, package["entrypoint"], instance,
            timeout=spec.operation_timeout_seconds, progress=progress, max_checkpoint_bytes=spec.max_checkpoint_bytes)

    def evaluate(evaluator, candidate):
        report["costs"]["evaluation_requests"] += 1
        try:
            value = evaluator.evaluate(candidate)
        except Exception:
            report["costs"]["solver_executions"] = None
            raise
        if report["costs"]["solver_executions"] is not None:
            report["costs"]["solver_executions"] += 1
        return value

    def check(name, function):
        progress()
        try:
            detail = function()
        except Exception as exc:
            report["checks"].append({"name": name, "passed": False, "detail": f"{type(exc).__name__}: {exc}"[:2000]})
            raise
        report["checks"].append({"name": name, "passed": True, "detail": detail})

    try:
        if package.get("kind") != "evaluator" or package.get("contract") != "evaluator_v1":
            raise ValueError("Evaluator validation requires an evaluator_v1 package")
        if not cases:
            raise ValueError("Independent evaluator correctness fixtures are required")
        for case in cases:
            instance = spec.manifest.resolve("correctness", case.configuration, case.fidelity)
            def numerical(case=case, instance=instance):
                evaluator = create(instance)
                try:
                    result = evaluate(evaluator, case.candidate)
                    for key, expected in getattr(case, "objectives", {}).items():
                        if not math.isclose(result.objectives[key], expected, abs_tol=case.absolute_tolerance,
                                            rel_tol=case.relative_tolerance):
                            raise ValueError(f"Objective {key} disagrees with independent fixture {case.name}")
                    return {"case": case.model_dump(mode="json"), "observation": result.model_dump(mode="json"),
                            "instance": instance.model_dump(mode="json")}
                finally:
                    evaluator.close()
            check(case.name, numerical)
            report["exposed_conditions"].append(instance.scientific_identity)

        def continuation():
            # Cover every declared fixture configuration/fidelity without sending
            # its expected values or basis into the generated process.
            tested = []
            for case in cases:
                instance = spec.manifest.resolve("correctness", case.configuration, case.fidelity)
                first = create(instance)
                second = None
                try:
                    second = create(instance)
                    initial = evaluate(first, case.candidate)
                    independent = evaluate(second, case.candidate)
                    if spec.manifest.deterministic and (initial.objectives, initial.constraints) != (independent.objectives, independent.constraints):
                        raise ValueError("The declared deterministic evaluator changes across identical hosts")
                    state = first.checkpoint()
                    expected = evaluate(first, case.candidate)
                    if spec.manifest.deterministic and (initial.objectives, initial.constraints) != (expected.objectives, expected.constraints):
                        raise ValueError("The declared deterministic evaluator changes across repeated calls")
                    second.restore(state)
                    restored = evaluate(second, case.candidate)
                    if restored.model_dump() != expected.model_dump():
                        raise ValueError("Evaluator checkpoint restoration changes the next result")
                    tested.append({"case": case.name, "state_bytes": len(state["checkpoint"]), "identity": instance.evaluation_identity})
                finally:
                    first.close()
                    if second is not None:
                        second.close()
            return tested
        check("independent hosts and checkpoint continuation", continuation)
        report["passed"] = True
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"[:2000]
    report["exposed_conditions"] = sorted(set(report["exposed_conditions"]))
    report["elapsed_seconds"] = time.monotonic() - started
    return report
