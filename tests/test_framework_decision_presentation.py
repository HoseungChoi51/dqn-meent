"""Decision copy must describe the saved operation without inventing consent."""
from copy import deepcopy

import pytest

from optimization_framework.campaigns.decision_presentation import action_choices, present_decision
from optimization_framework.campaigns.decisions import public_decision
from optimization_framework.contracts.commands import Command
from test_framework_decision_api import expired_inbox


def test_legacy_draft_approval_names_its_effect_and_preserves_original_ids_and_records():
    action = {"id": "draft_action", "kind": "command", "command_operation": "draft.save",
        "title": "Save hillclimb seed-03 draft", "question": "Does the ranking persist?",
        "rationale": "Two seeds do not establish a stable ranking. " * 30}
    record = {"id": "decision", "title": action["title"], "action_id": action["id"], "context": action["rationale"],
        "recommendation": "accept", "options": [{"id": "accept", "label": "Proceed"},
            {"id": "defer", "label": "Defer"}, {"id": "reject", "label": "Decline"}]}
    before = deepcopy((record, action))
    view = present_decision(record, action)
    assert view["options"][0]["label"] == "Save hillclimb seed-03 draft"
    assert "does not launch" in view["options"][0]["description"]
    assert [item["id"] for item in view["options"]] == ["accept", "defer", "reject"]
    assert view["proposal"] == action["title"]
    assert len(view["background"]) < 430 and view["background_is_excerpt"]
    assert view["details"] == action["rationale"]
    assert view["audience"] == "researcher" and not view["needs_clarification"]
    assert (record, action) == before


@pytest.mark.parametrize("prefix", ["Manager:", "For the campaign manager:"])
def test_legacy_manager_question_does_not_get_a_fabricated_yes_no_interpretation(prefix):
    record = {"title": prefix + " Do receipts already cover these experiments?", "context": "A long report.",
        "options": [{"id": "0", "label": "Follow the proposed direction"},
            {"id": "1", "label": "Provide a different direction"}]}
    before = deepcopy(record)
    view = present_decision(record)
    assert view["needs_clarification"] and view["audience"] == "manager"
    assert "no researcher decision is requested" in view["scope_label"]
    assert view["options"] == record["options"]
    assert "not been recorded" in view["proposal"]
    assert record == before


def test_structured_scientific_request_keeps_explicit_alternatives_and_proposal():
    record = {"title": "Prioritize generalization or replication?", "decision_format": "structured",
        "background": "Two methods are close on the original condition.",
        "proposal": "Prioritize replication on fresh seeds before changing wavelength.",
        "context": "Full observed evidence and limitations.", "recommendation": "replicate",
        "recommendation_reason": "This separates seed uncertainty from changes in problem difficulty.",
        "options": [{"id": "replicate", "label": "Prioritize fresh-seed replication", "description": "Keep the current physics and quantify seed variability."},
            {"id": "generalize", "label": "Prioritize wavelength and angle generalization", "description": "Develop a separate comparison under changed conditions."}]}
    view = present_decision(record)
    for key in ("title", "background", "proposal", "options", "recommendation_reason"):
        assert view[key] == record[key]
    assert not view["needs_clarification"]
    assert "does not launch work or change limits" in view["scope_label"]


def test_resource_changes_display_actual_proposed_limits_even_when_the_title_is_vague():
    action = {"kind": "command", "title": "Reconsider comparison allocation", "command_operation": "campaign.update",
        "command_payload": {"delegated_trial_seconds": 105, "validation_reserve_seconds": 300}}
    view = present_decision({"title": action["title"], "options": action_choices(action)}, action)
    assert "Per-experiment limit: 105 seconds" in view["proposal"]
    assert "Validation reserve: 300 seconds" in view["proposal"]
    assert "campaign changes" in view["options"][0]["label"]
    saved = {"title": action["title"], "options": action_choices(action), "proposal": view["proposal"]}
    assert present_decision(saved, action)["proposal"].count("Proposed limits:") == 1


def test_undefined_budget_question_requests_a_concrete_proposal():
    view = present_decision({"title": "Research reasoning budget needs attention", "context": "Call allowance exhausted.",
        "options": [{"id": "0", "label": "Increase the explicit allowance"}, {"id": "1", "label": "Continue with existing evidence"}]})
    assert view["needs_clarification"] and "change limits" in view["scope_label"]


def test_implementation_details_keep_the_exact_compute_and_model_allocation():
    action = {"kind": "implement", "title": "Implement the proposed surrogate",
        "implementation_compute_seconds": 120, "implementation_api_budget_usd": 2.5,
        "implementation_max_calls": 4, "requires_researcher": True,
        "stopping_condition": "Stop after the bounded validation suite."}
    view = present_decision({"title": action["title"], "options": action_choices(action)}, action)
    assert view["action_details"] == action


def test_legacy_provider_background_remains_brief_with_full_original_text_available():
    analysis = "Check the existing experiment receipts before asking the researcher. " * 100
    view = present_decision({"title": "Which receipts are missing?", "audience": "manager",
        "background": analysis, "context": analysis, "options": [], "decision_format": "legacy_question"})
    assert len(view["background"]) < 430 and view["background_is_excerpt"]
    assert view["details"] == analysis and view["audience"] == "manager"


def test_ambiguous_choice_is_blocked_before_guidance_changes_but_clarification_is_queued(tmp_path, monkeypatch):
    _, workspace, campaign, _, _ = expired_inbox(tmp_path, monkeypatch)
    decision = {"id": "unclear_question", "campaign_id": campaign["id"], "charter_version": campaign["version"],
        "title": "Manager: Which receipts are missing?", "context": "Original reasoning remains available.",
        "status": "pending", "options": [{"id": "0", "label": "Follow the proposed direction"},
            {"id": "1", "label": "Provide a different direction"}]}
    workspace.store.put("decision", decision)
    revision = workspace.memory.state(campaign["id"])["guidance_revision"]
    view = public_decision(workspace, decision)
    assert view["freshness"]["blocked_choice_ids"] == ["0", "1"]
    assert view["freshness"]["can_refresh"] and not view["freshness"]["stale"]
    with pytest.raises(ValueError, match="internal question"):
        workspace.commands.execute(Command(id="opaque_choice", campaign_id=campaign["id"],
            expected_revision=campaign["version"], operation="decision.resolve",
            payload={"decision_id": decision["id"], "choice": "0", "comment": "", "expected_resolution_revision": 0}))
    assert workspace.memory.state(campaign["id"])["guidance_revision"] == revision
    assert workspace.store.get(decision["id"], "decision") == decision
    accepted = workspace.commands.execute(Command(id="clarify_question", campaign_id=campaign["id"],
        expected_revision=campaign["version"], operation="decision.refresh", payload={"decisions": [
            {"decision_id": decision["id"], "comment": "Explain the missing evidence yourself.", "expected_resolution_revision": 0}]}))
    snapshot = workspace.store.get(accepted["outcome"]["refresh_id"], "decision_refresh")
    assert snapshot["decisions"][0]["decision"] == decision
    assert snapshot["decisions"][0]["comment"] == "Explain the missing evidence yourself."
    assert workspace.store.get(decision["id"], "decision")["status"] == "pending"


def test_saved_direction_cannot_close_its_provider_call(tmp_path, monkeypatch):
    _, workspace, campaign, _, _ = expired_inbox(tmp_path, monkeypatch)
    run = {"id": "uncertain_run", "campaign_id": campaign["id"], "status": "needs_reconciliation"}
    workspace.store.put("research_run", run)
    decision = {"id": "scientific_direction", "campaign_id": campaign["id"], "charter_version": campaign["version"],
        "research_run_id": run["id"], "title": "Which scientific direction?", "status": "pending",
        "decision_format": "structured", "options": [{"id": "close_reserved", "label": "Focus on seed replication"}]}
    workspace.store.put("decision", decision)
    revision = workspace.memory.state(campaign["id"])["guidance_revision"]
    with pytest.raises(ValueError, match="Only the provider reconciliation decision"):
        workspace.commands.execute(Command(id="wrong_reconciliation", campaign_id=campaign["id"],
            expected_revision=campaign["version"], operation="decision.resolve",
            payload={"decision_id": decision["id"], "choice": "close_reserved", "expected_resolution_revision": 0}))
    assert workspace.store.get(run["id"], "research_run") == run
    assert workspace.memory.state(campaign["id"])["guidance_revision"] == revision
