"""A waived requirement is never represented as a passing measurement."""
import pytest

from optimization_framework.contracts.validation import ValidationRequirement, ValidationResult, Waiver, WaiverRevocation
from optimization_framework.evaluation.policy import ValidationService
from optimization_framework.storage.sqlite import Store


def prepare(tmp_path, *, kind="solution_fidelity", scope="exploratory", manager=True):
    store = Store(tmp_path)
    store.put_immutable("study", {"id": "study", "campaign_id": "campaign", "scope": scope,
        "validation_policy": {"waivable_kinds": [kind], "manager_may_waive": manager}})
    subject = store.put_immutable("asset", {"id": "subject", "campaign_id": "campaign", "version": 1})
    store.put_immutable("finding", {"id": "experience", "campaign_id": "campaign", "text": "Prior checks and limitations"})
    service = ValidationService(store)
    service.require(ValidationRequirement(id="requirement", campaign_id="campaign", study_id="study", subject_id="subject",
        subject_digest=subject["content_hash"], kind=kind, recipe_id="recipe:v1", authority="researcher", created_at="test"))
    waiver = Waiver(id="waiver", campaign_id="campaign", study_id="study", requirement_id="requirement",
        subject_digest=subject["content_hash"], recipe_id="recipe:v1", rationale="Prior checks justify omitting this recheck",
        evidence_ids=["experience"], authority="campaign_manager", authority_kind="manager", created_at="test")
    return service, waiver


def test_waiver_retains_failed_measurement_and_is_invalidated_by_version_or_revocation(tmp_path):
    service, waiver = prepare(tmp_path)
    service.record_result(ValidationResult(id="failed", campaign_id="campaign", requirement_id="requirement", subject_digest=waiver.subject_digest,
        recipe_id="recipe:v1", verdict="failed", evidence_ids=["experience"], rationale="Measurement did not meet the tolerance",
        producer="trusted_service", authority="evaluation_service", created_at="test"))
    service.waive(waiver)
    assessment = service.assess("requirement")
    assert assessment["status"] == "waived" and assessment["satisfied"]
    assert not assessment["measured_pass"] and not assessment["scientific_claim_supported"]
    assert assessment["results"][0]["verdict"] == "failed"
    assert service.assess("requirement", current_recipe="recipe:v2")["status"] == "stale"
    service.revoke(WaiverRevocation(id="revoked", campaign_id="campaign", waiver_id="waiver", rationale="Recheck required after new evidence", authority="researcher", created_at="test"))
    assert service.assess("requirement")["status"] == "failed"


def test_manager_needs_delegated_waiver_authority(tmp_path):
    service, waiver = prepare(tmp_path, manager=False)
    with pytest.raises(ValueError, match="no delegated"):
        service.waive(waiver)


def test_study_permission_cannot_waive_a_mandatory_executable_contract(tmp_path):
    service, waiver = prepare(tmp_path, kind="implementation_correctness")
    original = service.store.get("requirement", "validation_requirement")
    mandatory = ValidationRequirement(**{key: value for key, value in original.items() if key in ValidationRequirement.model_fields})
    service.require(mandatory.model_copy(update={"id": "mandatory", "scope": {"mandatory_contract": True}}))
    with pytest.raises(ValueError, match="Mandatory executable contract"):
        service.waive(waiver.model_copy(update={"requirement_id": "mandatory"}))
    assert not service.assess("mandatory")["waiver_allowed"]
    assert not service.store.list("waiver")


def test_fixed_confirmation_claim_cannot_be_manufactured_by_waiver(tmp_path):
    service, waiver = prepare(tmp_path, kind="scientific_confirmation", scope="confirmation")
    with pytest.raises(ValueError, match="scientific claim"):
        service.waive(waiver)


def test_imported_attestation_does_not_silently_become_a_new_passing_check(tmp_path):
    service, waiver = prepare(tmp_path)
    service.record_result(ValidationResult(id="old", campaign_id="campaign", requirement_id="requirement", subject_digest=waiver.subject_digest,
        recipe_id="recipe:v1", verdict="passed", evidence_ids=["experience"], rationale="Historical attestation",
        producer="imported", authority="importer", created_at="test"))
    assert service.assess("requirement")["status"] == "pending"


def test_new_counterevidence_requires_reconsidering_an_existing_waiver(tmp_path):
    service, waiver = prepare(tmp_path)
    service.waive(waiver)
    assert service.assess("requirement")["status"] == "waived"
    service.record_result(ValidationResult(id="new_failure", campaign_id="campaign", requirement_id="requirement", subject_digest=waiver.subject_digest,
        recipe_id="recipe:v1", verdict="failed", evidence_ids=["experience"], rationale="New contradictory measurement",
        producer="trusted_service", authority="evaluation_service", created_at="test"))
    assert service.assess("requirement")["status"] == "failed"
