"""Correctness evidence for the exact executable that produced an observation."""
from optimization_framework.contracts.base import content_hash
from optimization_framework.implementations.models import digest
from optimization_framework.implementations.revalidation import report_matches


def record(store, version):
    """Keep service-observed evidence history separately from the mutable cache.

    Runtime availability and the currently installed checker do not change a
    historical measurement. A new failed check or revocation does affect support
    for claims made from that measurement. The journal supplies the observation
    time; a library report cannot backdate its admission to a selection cutoff.
    """
    report = version["validation_report"]
    evidence = {"schema_version": 1, "version_id": version["id"],
        "kind": version.get("kind", "optimizer"), "artifact_digest": version["artifact_digest"],
        "runtime_digest": version["runtime_digest"], "spec_digest": digest(version["spec"]),
        "status": version["status"], "report": report,
        "report_matches": report_matches(version, report),
        "blocking_report_ids": version.get("blocking_validation_report_ids", []),
        "revocation_reason": version.get("revocation_reason")}
    evidence["id"] = "executable_evidence_" + content_hash(evidence)
    return store.put_immutable("executable_evidence", evidence, "executable.evidence_recorded")


def assessment(store, trial, *, cutoff_at=None):
    """Assess known evidence without modifying an experiment's frozen basis."""
    evidence = []
    snapshots = store.list("executable_evidence") if trial.get("implementation_version_id") or trial.get("evaluator_version_id") else []
    for role in ("implementation", "evaluator"):
        identity = trial.get(role + "_version_id")
        if not identity:
            continue
        applicable = [row for row in snapshots if row["version_id"] == identity and
            (cutoff_at is None or (store.committed_at(row["id"], "executable.evidence_recorded") or float("inf")) <= cutoff_at)]
        latest = applicable[-1] if applicable else None
        if latest is None:
            evidence.append({"role": role, "version_id": identity, "supported": False,
                "reason": "No executable correctness evidence was recorded within the required evidence window."})
            continue
        report = latest["report"]
        exact = all(latest[field] == trial.get(role + "_" + field) for field in ("artifact_digest", "runtime_digest"))
        supported = (exact and latest["status"] in {"validated", "contract_validated"} and
            latest["report_matches"] and report.get("passed") is True and not latest["blocking_report_ids"])
        evidence.append({"role": role, "version_id": identity, "evidence_id": latest["id"],
            "artifact_digest": latest["artifact_digest"], "runtime_digest": latest["runtime_digest"],
            "validation_report_id": report["id"], "report_digest": content_hash(report),
            "frozen_validation_report_id": trial.get("validation_report_id" if role == "implementation" else "evaluator_validation_report_id"),
            "status": latest["status"], "blocking_report_ids": latest["blocking_report_ids"],
            "supported": supported,
            "reason": "Recorded correctness evidence supports this exact executable." if supported else
                "Executable identity differs from the frozen procedure." if not exact else
                latest.get("revocation_reason") or report.get("error") or "Executable correctness evidence no longer supports this procedure."})
    return {"supported": all(row["supported"] for row in evidence), "executables": evidence}
