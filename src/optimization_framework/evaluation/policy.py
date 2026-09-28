"""Validation evidence remains distinct from the authority to waive a check."""
from optimization_framework.contracts.base import content_hash
from optimization_framework.contracts.validation import ValidationRequirement, ValidationResult, Waiver, WaiverRevocation


DEFAULT_WAIVABLE = {"solution_fidelity", "learner_diagnostics"}


class ValidationService:
    def __init__(self, store):
        self.store = store

    def subject_digest(self, subject_id):
        subject = self.store.get(subject_id)
        if subject.get("experiment_spec_hash"):
            return subject["experiment_spec_hash"]
        return subject.get("content_hash") or content_hash(subject)

    def require(self, requirement):
        requirement = requirement if isinstance(requirement, ValidationRequirement) else ValidationRequirement(**requirement)
        study = self.store.get(requirement.study_id, "study")
        if study["campaign_id"] != requirement.campaign_id:
            raise ValueError("Requirement belongs to another study's campaign")
        if self.subject_digest(requirement.subject_id) != requirement.subject_digest:
            raise ValueError("Requirement does not bind the current subject version")
        return self.store.put_immutable("validation_requirement", requirement.model_dump(mode="json"), "validation.required")

    def _evidence(self, evidence_ids):
        # Evidence must resolve to a durable record; interpretations and imported
        # attestations keep their producer label instead of becoming measurements.
        for item in evidence_ids:
            self.store.get(item)

    @staticmethod
    def _compatible(requirement, record):
        if record.campaign_id != requirement["campaign_id"] or record.subject_digest != requirement["subject_digest"] or record.recipe_id != requirement["recipe_id"]:
            raise ValueError("Validation decision does not match the required subject, scope and recipe version")

    def record_result(self, result):
        result = result if isinstance(result, ValidationResult) else ValidationResult(**result)
        requirement = self.store.get(result.requirement_id, "validation_requirement")
        self._compatible(requirement, result)
        self._evidence(result.evidence_ids)
        return self.store.put_immutable("validation_result", result.model_dump(mode="json"), "validation.measured")

    def waive(self, waiver):
        waiver = waiver if isinstance(waiver, Waiver) else Waiver(**waiver)
        requirement = self.store.get(waiver.requirement_id, "validation_requirement")
        self._compatible(requirement, waiver)
        if waiver.study_id != requirement["study_id"]:
            raise ValueError("A waiver cannot move a requirement into a different study")
        if self.subject_digest(requirement["subject_id"]) != waiver.subject_digest:
            raise ValueError("The subject changed; create a requirement for the new version")
        study = self.store.get(requirement["study_id"], "study")
        policy = study.get("validation_policy", {})
        eligible = set(policy.get("waivable_kinds", DEFAULT_WAIVABLE))
        if requirement["kind"] not in eligible:
            raise ValueError("The frozen study policy does not permit waiving this requirement")
        if requirement.get("scope", {}).get("mandatory_contract"):
            raise ValueError("Mandatory executable contract checks cannot be waived")
        if requirement["recipe_id"] == "evaluator_numerical:v1" and study.get("scope") != "exploratory":
            raise ValueError("Unverified evaluators may only be authorized for an exploratory study")
        results = [item for item in self.store.list("validation_result", requirement["campaign_id"]) if item["requirement_id"] == requirement["id"]]
        if results and results[-1].get("measurements", {}).get("known_specification_failure"):
            raise ValueError("A known executable specification failure cannot be waived; inspect or repair the implementation")
        if requirement["kind"] == "scientific_confirmation" and study.get("scope") == "confirmation":
            raise ValueError("A confirmation criterion cannot be waived while retaining its scientific claim; create a new study")
        if waiver.authority_kind == "manager" and not policy.get("manager_may_waive", False):
            raise ValueError("The manager has no delegated waiver authority in this study")
        self._evidence(waiver.evidence_ids)
        return self.store.put_immutable("waiver", waiver.model_dump(mode="json"), "validation.waived")

    def revoke(self, revocation):
        revocation = revocation if isinstance(revocation, WaiverRevocation) else WaiverRevocation(**revocation)
        waiver = self.store.get(revocation.waiver_id, "waiver")
        if revocation.campaign_id != waiver["campaign_id"]:
            raise ValueError("Waiver revocation belongs to another campaign")
        return self.store.put_immutable("waiver_revocation", revocation.model_dump(mode="json"), "validation.waiver_revoked")

    def assess(self, requirement_id, *, current_recipe=None, current_study=None, cutoff_at=None):
        requirement = self.store.get(requirement_id, "validation_requirement")
        results = [row for row in self.store.list("validation_result", requirement["campaign_id"]) if row["requirement_id"] == requirement_id]
        if cutoff_at is not None:
            results = [row for row in results if (self.store.committed_at(row["id"], "validation.measured") or float("inf")) <= cutoff_at]
        waivers = [row for row in self.store.list("waiver", requirement["campaign_id"]) if row["requirement_id"] == requirement_id]
        revoked = {row["waiver_id"] for row in self.store.list("waiver_revocation", requirement["campaign_id"])}
        active = [row for row in waivers if row["id"] not in revoked]
        stale = (self.subject_digest(requirement["subject_id"]) != requirement["subject_digest"]
                 or current_recipe is not None and current_recipe != requirement["recipe_id"]
                 or current_study is not None and current_study != requirement["study_id"])
        study = self.store.get(requirement["study_id"], "study")
        policy = study.get("validation_policy", {})
        eligible = [row for row in results if row["producer"] != "imported" or policy.get("accept_imported_results", False)]
        # Append order is durable. Later contrary evidence invalidates the prior
        # measurement; a previous pass cannot hide a later failed recheck.
        latest = eligible[-1] if eligible else None
        if latest and latest["verdict"] in {"failed", "error"}:
            active = [row for row in active if "new_failed_result" not in row["invalidation_conditions"]
                      or self.store.record_position(row["id"]) > self.store.record_position(latest["id"])]
        state = "stale" if stale else "passed" if latest and latest["verdict"] == "passed" else "waived" if active else latest["verdict"] if latest else "pending"
        waiver_allowed = (not stale and requirement["kind"] in set(policy.get("waivable_kinds", DEFAULT_WAIVABLE))
            and not (latest and latest.get("measurements", {}).get("known_specification_failure"))
            and not requirement.get("scope", {}).get("mandatory_contract")
            and (requirement["recipe_id"] != "evaluator_numerical:v1" or study.get("scope") == "exploratory")
            and not (requirement["kind"] == "scientific_confirmation" and study.get("scope") == "confirmation"))
        return {"requirement": requirement, "status": state, "satisfied": state in {"passed", "waived"},
                "measured_pass": state == "passed", "scientific_claim_supported": state == "passed",
                "waiver_allowed": waiver_allowed, "manager_may_waive": waiver_allowed and policy.get("manager_may_waive", False),
                "results": results, "waivers": waivers, "active_waiver_ids": [row["id"] for row in active], "revoked_waiver_ids": sorted(revoked)}
