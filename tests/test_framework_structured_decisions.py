"""Only manager-authored, concrete choices reach the researcher inbox."""
from copy import deepcopy
import json

import pytest

from optimization_framework.campaigns.manager import CampaignManager
from optimization_framework.contracts.commands import Command
from optimization_framework.contracts.requests import CampaignInput, TaskInput
from optimization_framework.execution.service import Workspace
from optimization_framework.research import engine
from optimization_framework.research.lifecycle import finalize, save_result
from test_workspace_research import configure, context, isolated_provider, mock_provider


def direction(title="Which generalization check should come first?"):
    return {"title": title, "background": "Two search families have similar scores on the original condition.",
        "proposal": "Compare frozen configurations at a changed wavelength before expanding the search.",
        "options": [
            {"id": "wavelength", "label": "Test another wavelength", "description": "Keep configurations fixed and compare their performance on a changed wavelength."},
            {"id": "angle", "label": "Test another incidence angle", "description": "Keep configurations fixed and compare a changed incidence angle first."}],
        "recommendation": "wavelength", "recommendation_reason": "This isolates one meaningful change in the problem conditions."}


def test_direction_option_cannot_use_the_provider_reconciliation_operation():
    request = direction()
    request["options"][0]["id"] = request["recommendation"] = "close_reserved"
    with pytest.raises(ValueError, match="reserved for provider reconciliation"):
        engine.ResearcherDecisionRequest(**request)


def test_specialist_questions_and_decision_requests_stay_internal(monkeypatch):
    configure(monkeypatch)
    prompts = []
    def respond(payload, count):
        prompts.append(json.loads(payload["messages"][1]["content"]))
        if count == 1:
            return {"analysis": "I need the accepted receipts to resolve this.",
                "questions": ["Manager: Which receipts cover the completed work?"], "decision_requests": [direction()]}
        return {"analysis": "The manager resolved the operational question from the supplied records."}
    calls = mock_provider(monkeypatch, respond)
    result = engine.run_research({"mode": "compare", "max_calls": 2}, context())
    assert len(calls) == 2 and result["status"] == "completed"
    assert result["decisions"] == []
    assert prompts[1]["previous_role_results"][0]["questions"] == ["Manager: Which receipts cover the completed work?"]
    assert prompts[1]["previous_role_results"][0]["decision_requests"] == [direction()]
    assert "Only research_synthesizer may use decision_requests" in calls[0]["messages"][0]["content"]


def test_manager_structured_request_preserves_concrete_options_and_scope(monkeypatch):
    configure(monkeypatch)
    mock_provider(monkeypatch, lambda *_: {"analysis": "Choose the most useful scientific direction.", "decision_requests": [direction()]})
    result = engine.run_research({"mode": "discuss", "max_calls": 1}, context())
    assert result["status"] == "awaiting_researcher" and len(result["decisions"]) == 1
    decision = result["decisions"][0]
    for key, value in direction().items():
        assert decision[key] == value
    assert decision["decision_format"] == "structured"
    assert decision["source_role"] == "research_synthesizer"
    assert decision["scope_label"] == "Research direction in this campaign; does not launch work or change limits."
    assert not decision.get("action_id")


def test_legacy_manager_question_does_not_invent_yes_no_choices(monkeypatch):
    configure(monkeypatch)
    mock_provider(monkeypatch, lambda *_: {"analysis": "The two mechanisms have different evidence gaps.", "questions": ["Which evidence gap matters most?"]})
    result = engine.run_research({"mode": "discuss", "max_calls": 1}, context())
    decision = result["decisions"][0]
    assert decision["title"] == "Which evidence gap matters most?"
    assert decision["options"] == [] and decision["recommendation"] is None
    assert decision["needs_clarification"] is True
    assert decision["decision_format"] == "legacy_question"
    assert decision["audience"] == "manager"


def test_only_final_synthesis_requests_direction_and_earlier_questions_stay_internal(monkeypatch):
    configure(monkeypatch)
    mock_provider(monkeypatch, lambda payload, count: {"analysis": "A bounded set of scientific choices.",
        "decision_requests": [direction(f"Scientific question {count}-{i}?") for i in range(2)],
        "next_roles": ["research_synthesizer"] if count == 1 else []})
    result = engine.run_research({"mode": "discuss", "max_calls": 2}, context())
    assert len(result["decisions"]) == 2
    assert {row["title"] for row in result["decisions"]} == {"Scientific question 2-0?", "Scientific question 2-1?"}
    assert len(result["research_state"]["role_results"][0]["decision_requests"]) == 2
    assert len(result["research_state"]["role_results"][1]["decision_requests"]) == 2


@pytest.mark.parametrize("max_calls", [1, 2])
def test_intermediate_manager_choices_stay_internal_when_more_research_follows(monkeypatch, max_calls):
    configure(monkeypatch)
    mock_provider(monkeypatch, lambda payload, count: {
        "analysis": "This choice needs further analysis.", "decision_requests": [direction()],
        "next_roles": ["problem_analyst"]} if count == 1 else {
        "analysis": "The earlier proposal needs revision before asking the researcher."})
    result = engine.run_research({"mode": "discuss", "max_calls": max_calls}, context())
    assert result["research_state"]["role_results"][0]["decision_requests"] == [direction()]
    assert not any(row.get("decision_format") == "structured" for row in result["decisions"])
    assert result["trace"][-1]["role"] == "problem_analyst"


def test_duplicate_direction_and_exact_action_approval_are_not_added_twice():
    request = direction()
    duplicate = deepcopy(request)
    duplicate["title"] = request["title"].upper()
    approval = direction("Run the proposed experiment")
    approval["proposal"] = "Run the proposed experiment"
    answer = engine.RoleResult(analysis="Analysis", decision_requests=[request, duplicate, approval],
        questions=[request["title"], "Run the proposed experiment"])
    result = engine._manager_decisions(answer, {"decisions": [], "actions": [
        {"title": "Run the proposed experiment", "question": "Is its improvement reproducible?"}]})
    assert len(result) == 1 and result[0]["title"] == request["title"]


@pytest.mark.parametrize("change", [
    lambda request: request.update(recommendation="invented"),
    lambda request: request["options"][1].update(id="wavelength"),
    lambda request: request["options"][1].update(label=request["options"][0]["label"]),
    lambda request: request["options"][0].update(label="Follow the proposed direction"),
    lambda request: request["options"][0].update(description=" "),
    lambda request: request.update(proposal="x" * 601),
])
def test_structured_request_rejects_ambiguous_or_invalid_choices(change):
    request = direction()
    change(request)
    with pytest.raises(ValueError):
        engine.ResearcherDecisionRequest(**request)


def test_structured_role_limit_is_validated():
    with pytest.raises(ValueError):
        engine.RoleResult(analysis="Analysis", decision_requests=[direction(str(i)) for i in range(4)])


@pytest.fixture
def saved_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("GRATING_LLM_DISABLED", "true")
    workspace = Workspace(tmp_path)
    campaign = workspace.create_campaign(CampaignInput(name="Decision wording", tasks=[TaskInput(name="Development", problem_id="bounded_continuous")]))
    manager = CampaignManager(workspace)
    monkeypatch.setattr(manager, "_thread", lambda *_: None)
    return workspace, campaign, manager


def test_clarified_request_replaces_no_original_and_records_only_guidance(saved_workspace):
    workspace, campaign, manager = saved_workspace
    original = {"id": "opaque_question", "title": direction()["title"], "charter_version": campaign["version"],
        "campaign_id": campaign["id"], "status": "pending", "options": [
            {"id": "0", "label": "Follow the proposed direction"}, {"id": "1", "label": "Provide a different direction"}]}
    workspace.store.put("decision", original)
    run = {"id": "new_manager", "campaign_id": campaign["id"], "charter_version": campaign["version"], "guidance_revision": 0}
    decision = engine._manager_decisions(engine.RoleResult(analysis="Analysis", decision_requests=[direction()]), {"decisions": [], "actions": []})[0]
    manager._save_decision(run, decision)
    assert workspace.store.get(original["id"], "decision") == original
    saved = workspace.store.get(decision["id"], "decision")
    assert saved["options"] == direction()["options"]
    manager._save_decision(run, {**deepcopy(decision), "id": "same_question_again"})
    assert len(workspace.store.list("decision")) == 2
    workspace.commands.execute(Command(id="choose_wavelength", campaign_id=campaign["id"], operation="decision.resolve",
        expected_revision=campaign["version"], payload={"decision_id": decision["id"], "expected_resolution_revision": 0,
            "choice": "wavelength", "comment": "Prefer the wavelength check."}))
    assert workspace.store.get(campaign["id"], "campaign") == campaign
    assert not workspace.store.list("trial")
    assert workspace.store.get(decision["id"], "decision")["choice"] == "wavelength"
    assert workspace.memory.state(campaign["id"])["guidance_revision"] == 1


@pytest.mark.parametrize("operation,label,payload", [
    ("draft.save", "Save a bounded test draft", {}),
    ("trial.create", "Queue the proposed experiment", {"max_steps": 256}),
    ("campaign.update", "Approve the proposed campaign changes", {"compute_budget_seconds": 7200, "validation_reserve_seconds": 300}),
])
def test_action_cards_have_concrete_scoped_choices_without_changing_payload(saved_workspace, operation, label, payload):
    workspace, campaign, manager = saved_workspace
    run = {"id": "action_run", "campaign_id": campaign["id"], "charter_version": campaign["version"],
        "guidance_revision": 0, "status": "running", "request": {"mode": "plan"},
        "context_snapshot": {"campaign": campaign, "manager_context": {}}}
    workspace.store.put("research_run", run)
    action = {"id": "exact_action", "kind": "command", "command_operation": operation,
        "command_payload": payload, "title": "Save a bounded test draft" if operation == "draft.save" else "Concrete scientific action",
        "rationale": "Resolve a specific uncertainty with a bounded comparison.", "question": "Do the candidate methods generalize?",
        "expected_information": "Matched evidence under the recorded settings.", "requires_researcher": True, "status": "proposed"}
    result = {"status": "completed", "mode": "llm", "usage": {"calls": 1}, "messages": [],
        "hypotheses": [], "decisions": [], "actions": [action]}
    finalize(manager, run["id"], save_result(workspace, run["id"], result))
    decision = workspace.store.get("decision_exact_action", "decision")
    assert decision["decision_format"] == "action"
    assert [option["id"] for option in decision["options"]] == ["accept", "defer", "reject"]
    assert decision["options"][0]["label"] == label
    assert all(option["description"] for option in decision["options"])
    assert decision["background"] and decision["proposal"] and decision["recommendation_reason"]
    assert decision["scope_label"] == "Only this proposed action, under its current campaign permissions."
    if operation == "campaign.update":
        assert "7200 seconds" in decision["proposal"] and "300 seconds" in decision["proposal"]
    assert workspace.store.get(action["id"], "action")["command_payload"] == payload
    assert not workspace.store.list("trial")
    assert not [effect for effect in workspace.store.list("outbox") if effect["kind"] == "manager_action"]


def test_exact_repeated_action_appears_once_but_both_role_responses_survive(monkeypatch):
    configure(monkeypatch)
    action = {"kind": "command", "title": "Save this exact draft", "rationale": "Preserve a bounded design for review.",
        "question": "Is the design ready?", "expected_information": "A saved concrete design", "stopping_condition": "After saving the draft",
        "requires_researcher": True, "command_operation": "draft.save", "command_payload": {"question": "The exact same design"}}
    mock_provider(monkeypatch, lambda *_: {"analysis": "Same action independently proposed.", "actions": [action]})
    result = engine.run_research({"mode": "compare", "max_calls": 2}, context())
    assert len(result["actions"]) == 1
    assert all(len(role["actions"]) == 1 for role in result["research_state"]["role_results"])
    assert len(result["research_state"]["role_results"]) == 2


def test_structured_and_legacy_requests_together_remain_bounded():
    answer = engine.RoleResult(analysis="The manager proposes three clear scientific choices.",
        decision_requests=[direction(f"Scientific choice {i}?") for i in range(3)],
        questions=[f"Additional old-style question {i}?" for i in range(6)])
    decisions = engine._manager_decisions(answer, {"actions": [], "decisions": []})
    assert len(decisions) == 3 and all(row["decision_format"] == "structured" for row in decisions)
