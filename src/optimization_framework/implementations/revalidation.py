"""Bounded checks of an immutable executable, with append-only evidence."""
import copy
import time
from pathlib import Path

from optimization_framework.implementations.models import (
    BoundOptimizerSpec, CapabilityUnavailable, EvaluatorCheckSpec, EvaluatorSpec,
    ImplementationSpec, RevalidationRequest, digest,
)
from optimization_framework.implementations.runtime import verify_runtime, bundle_runtime_root
from optimization_framework.storage.sqlite import now


def reports(version):
    values = [*version.get("validation_history", []), version["validation_report"]]
    return list({report["id"]: report for report in values}.values())


def migrate(service):
    """Numbered library migration; original versions, jobs and hashes stay intact."""
    with service.store.transaction(), service.store.connection() as db:
        db.execute("CREATE TABLE IF NOT EXISTS implementation_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
        if db.execute("SELECT 1 FROM implementation_migrations WHERE version=1").fetchone():
            return
        jobs = {attempt["report"]["id"]: job for job in service.store.list("implementation_job")
                for attempt in job.get("attempts", []) if attempt.get("report")}
        for version in service.store.list("implementation_version"):
            for report in reports(version):
                try:
                    service.store.get(report["id"], "implementation_validation")
                except KeyError:
                    producer = jobs.get(report["id"], {})
                    service.store.put_immutable("implementation_validation", {
                        "id": report["id"], "version_id": version["id"], "report": report,
                        "job_id": producer.get("id"), "campaign_id": producer.get("campaign_id"),
                        "origin": "historical_library_projection",
                    }, "implementation.validation_imported")
        db.execute("INSERT INTO implementation_migrations VALUES (1, ?)", (now(),))


def merge_checks(groups):
    """Never let a smaller check request hide an earlier counterexample."""
    result = {}
    for group in groups:
        for case in group:
            name = case["name"]
            if name in result and result[name] != case:
                raise ValueError(f"Existing check {name!r} has different frozen fixtures; prior evidence cannot be replaced")
            result[name] = case
    return list(result.values())


def plan(version, checks):
    evaluator = version.get("kind") == "evaluator"
    if evaluator != isinstance(checks, EvaluatorCheckSpec):
        raise ValueError("Check specification must match the executable kind")
    original = version["spec"]
    evidence = reports(version)
    previous = [report["validation_spec"] for report in evidence if report.get("validation_spec")]
    if evaluator:
        fields = ("correctness_cases", "contract_cases")
        values = {field: merge_checks([original.get(field, []), *[value.get(field, []) for value in previous],
                    [case.model_dump() for case in getattr(checks, field)]]) for field in fields}
        if checks.validation_mode == "contract_only" and values["correctness_cases"]:
            raise ValueError("Existing independent numerical fixtures must be checked; select numerical revalidation")
        values["validation_mode"] = checks.validation_mode
        spec = EvaluatorSpec.model_validate({**original, **values})
        if spec.validation_mode == "numerical" and not spec.correctness_cases:
            raise ValueError("Supply independent numerical fixtures to revalidate this contract-only evaluator")
    else:
        cases = merge_checks([original.get("behavior_checks", []), *[value.get("behavior_checks", []) for value in previous],
            [case.model_dump() for case in checks.behavior_checks]])
        spec_type = BoundOptimizerSpec if original.get("evaluator_version_id") else ImplementationSpec
        spec = spec_type.model_validate({**original, "behavior_checks": cases})
        if not spec.behavior_checks:
            raise ValueError("Revalidation requires independent behavior checks; it does not call a test-design model")
    return {"spec": spec.model_dump(mode="json"), "basis_report_ids": [report["id"] for report in evidence],
        "checks": checks.model_dump(mode="json"), "checks_digest": digest(checks.model_dump()),
        "version_id": version["id"], "artifact_digest": version["artifact_digest"],
        "runtime_digest": version["runtime_digest"], "spec_digest": digest(original)}


def report_matches(version, report):
    """Bind appended evidence to the original executable and an explicit check spec."""
    declaration = report.get("revalidation")
    if not declaration:
        return report.get("spec_digest") == digest(version["spec"])
    if any(declaration.get(key) != expected for key, expected in {
        "version_id": version["id"], "artifact_digest": version["artifact_digest"],
        "runtime_digest": version["runtime_digest"], "spec_digest": digest(version["spec"]),
    }.items()):
        return False
    checked = report.get("validation_spec", {})
    permitted = ({"validation_mode", "correctness_cases", "contract_cases"} if version.get("kind") == "evaluator"
                 else {"behavior_checks"})
    unchanged = lambda value: {key: item for key, item in value.items() if key not in permitted}
    return (unchanged(checked) == unchanged(version["spec"])
        and report.get("spec_digest") == digest(version["spec"])
        and report.get("validation_spec_digest") == digest(checked)
        and declaration.get("checks_digest") == digest(declaration.get("checks")))


def publish(service, job, report):
    """Keep publication atomic with the job receipt and preserve revocation."""
    version_id = job["request"]["version_id"]
    with service.lock, service.store.transaction():
        version = service.version(version_id)
        history = reports(version)
        if report["id"] not in {item["id"] for item in history}:
            service.store.put_immutable("implementation_validation", {
                "id": report["id"], "version_id": version_id, "campaign_id": job["campaign_id"],
                "job_id": job["id"], "report": report,
            }, "implementation.validation_recorded")
            blocked = set(version.get("blocking_validation_report_ids", []))
            if report["passed"]:
                blocked.difference_update(job["validation_plan"]["basis_report_ids"])
            else:
                blocked.add(report["id"])
            version.update(validation_history=history, validation_report=report,
                blocking_validation_report_ids=sorted(blocked),
                exposed_conditions=sorted(set(version.get("exposed_conditions", []) + report.get("exposed_conditions", []))),
                production_job_ids=list(dict.fromkeys([*version.get("production_job_ids", [version["job_id"]]), job["id"]])))
            if version["status"] != "revoked":
                version["status"] = ("validation_failed" if blocked else
                    "contract_validated" if report["kind"] == "evaluator_contract" else "validated")
            service.store.put("implementation_version", version, "implementation.revalidated")
        return service.update_job(job["id"], version_id=version_id,
            status="completed" if report["passed"] else "failed", finished_at=now(),
            validation_report_id=report["id"], error=None if report["passed"] else report.get("error", "Revalidation failed"))


def run(service, job):
    # Import here to keep the service's existing interruption contract shared.
    from optimization_framework.implementations.service import JobInterrupted
    request = RevalidationRequest.model_validate(job["request"])
    started, spent = time.monotonic(), job["compute_seconds"]
    last_accounted = started
    interrupted = None

    def progress():
        nonlocal last_accounted, interrupted
        current = service.store.get(job["id"], "implementation_job")
        elapsed = time.monotonic() - started
        if time.monotonic() - last_accounted >= 1:
            service.update_job(job["id"], compute_seconds=spent + elapsed)
            last_accounted = time.monotonic()
        if service.stopping.is_set() or current.get("cancel_requested"):
            interrupted = "Revalidation stopped"
        elif spent + elapsed >= request.compute_seconds:
            interrupted = "Revalidation compute allocation exhausted"
        if interrupted:
            raise JobInterrupted(interrupted)

    try:
        progress()
        bundle = service.artifact(request.version_id, ready=False)
        version, artifact = bundle["version"], bundle["artifact"]
        frozen = job["validation_plan"]
        if version["status"] == "revoked":
            raise CapabilityUnavailable("Revoked executables require a corrected version")
        if (version["id"] != frozen["version_id"] or digest(version["spec"]) != frozen["spec_digest"]
                or any(version[key] != frozen[key] for key in ("artifact_digest", "runtime_digest"))):
            raise ValueError("Executable identity differs from the revalidation request")
        try:
            verify_runtime(bundle_runtime_root(bundle), artifact["runtime"])
        except (OSError, ValueError) as exc:
            raise CapabilityUnavailable(f"The exact published runtime is unavailable: {exc}") from exc
        attempts = copy.deepcopy(job["attempts"])
        if attempts and attempts[-1].get("complete_report"):
            publish(service, job, attempts[-1]["report"])
        else:
            attempt = {"number": len(attempts) + 1, "started_at": now()}
            attempts.append(attempt)
            service.update_job(job["id"], attempts=attempts)
            registry, evaluator_digest = None, None
            if version.get("kind") == "evaluator":
                from optimization_framework.implementations.evaluator_validation import validate_package
            else:
                from optimization_framework.implementations.validation import validate_package
                if artifact["spec"].get("evaluator_version_id"):
                    from optimization_framework.evaluation.generated import PublishedEvaluator, evaluator_identity
                    from optimization_framework.evaluation.registry import problems
                    dependency = service.artifact(artifact["spec"]["evaluator_version_id"])
                    registry = problems.extended(PublishedEvaluator(dependency,
                        service.directory / "artifacts" / dependency["version"]["artifact_digest"] / "package", progress=progress))
                    evaluator_digest = evaluator_identity(dependency["version"])
            report = validate_package(frozen["spec"], artifact["package"],
                service.directory / "artifacts" / version["artifact_digest"] / "package",
                bundle_runtime_root(bundle), artifact["runtime"], progress=progress,
                **({"registry": registry, "evaluator_digest": evaluator_digest} if version.get("kind") != "evaluator" else {}))
            report.update(spec_digest=frozen["spec_digest"], validation_spec=frozen["spec"],
                validation_spec_digest=digest(frozen["spec"]), revalidation={key: value for key, value in frozen.items() if key != "spec"},
                model_review_performed=False,
                review=version["validation_report"].get("review"), review_basis=version["validation_report"].get("review_basis", version["validation_report"]["id"]))
            attempt.update(report=report, complete_report=not interrupted, finished=True, finished_at=now())
            service.update_job(job["id"], attempts=attempts)
            if interrupted:
                raise JobInterrupted(interrupted)
            publish(service, job, report)
    except JobInterrupted as exc:
        current = service.store.get(job["id"], "implementation_job")
        service.update_job(job["id"], status="cancelled" if current.get("cancel_requested") else "interrupted", error=str(exc))
    except CapabilityUnavailable as exc:
        service.update_job(job["id"], status="blocked", error=str(exc))
    except Exception as exc:
        service.update_job(job["id"], status="failed", error=f"Revalidation failed ({type(exc).__name__}): {str(exc)[:1500]}")
    finally:
        current = service.store.get(job["id"], "implementation_job")
        changes = {"compute_seconds": spent + time.monotonic() - started, "accounting_final": True}
        if current.get("cancel_requested") and current["status"] not in {"completed", "failed"}:
            changes["status"] = "cancelled"
        service.update_job(job["id"], **changes)
    return service.store.get(job["id"], "implementation_job")
