"""Unanswered historical questions must not crowd out new scientific replies."""
from copy import deepcopy

from optimization_framework.research.context import (
    LIMIT, _deduplicate_current_context, _index_informational_decision_history, bounded, size,
)
from optimization_framework.research.engine import _safe_context
from test_framework_working_inventory import crowded_context


def test_many_unanswered_backgrounds_keep_new_question_constraints_and_measurements():
    context = crowded_context()
    context["decisions"] = [{"id": f"decision_{index}", "status": "pending", "question": f"Question {index}?",
        "title": f"Question {index}?", "rationale": f"Historical model interpretation {index}. " * 500,
        "options": [{"id": "replicate", "label": "Run fresh seeds", "description": "Compare at the same cost."}],
        "comment": "This researcher comment must remain exact", "choice": "replicate",
        "charter_version": 3, "resolution_revision": 2} for index in range(30)]
    context["decisions"].append({"id": "executable", "status": "pending", "action_id": "action_one",
        "title": "A pending executable proposal", "context": "The proposal's complete requirements.",
        "options": [{"id": "accept", "description": "Exact proposed work and cap"}]})
    original = deepcopy(context)
    compiled = bounded(context, question="How should I choose a method for final validation?")
    assert size(compiled) <= LIMIT
    assert len(compiled["decisions"]) == 31
    for row in compiled["decisions"][:-1]:
        saved = original["decisions"][int(row["id"].split("_")[-1])]
        assert row["omitted_narrative_fields"] == ["rationale"]
        assert row["narrative_record_id"] == row["id"]
        assert {k: row[k] for k in saved if k != "rationale"} == {k: v for k, v in saved.items() if k != "rationale"}
    assert compiled["decisions"][-1] == original["decisions"][-1]
    for key in ("narrative_guidance", "delegation", "resources", "pending_issues", "next_actions"):
        assert compiled["manager_context"]["structured"][key] == original["manager_context"]["structured"][key]
    assert compiled["research_inventory"]["trials"][0]["measurement"]["best_objective"] == .3549
    safe = _safe_context(compiled)
    assert safe["decision_reference_basis"] == compiled["decision_reference_basis"]
    assert "not supplied evidence or current constraints" in safe["decision_reference_basis"]
    assert context == original


def test_selected_decision_background_and_executable_shared_text_remain_supplied():
    text = "Exact original model commentary. " * 20
    context = {"decisions": [
        {"id": "question", "status": "pending", "rationale": text, "context": text},
        {"id": "explicit", "status": "pending", "rationale": "The explicitly requested rationale."},
        {"id": "target", "status": "pending", "rationale": "Target rationale."},
        {"id": "execute", "status": "pending", "action_id": "action", "rationale": text, "context": text},
    ]}
    _deduplicate_current_context(context, "")
    assert context["decisions"][-1]["supplied_text_references"]["rationale"]["decision_id"] == "question"
    _index_informational_decision_history(context, "Explain explicit", "target")
    question, explicit, target, executable = context["decisions"]
    assert question["omitted_narrative_fields"] == ["context", "rationale"]
    assert "supplied_text_references" not in question
    assert explicit["rationale"] == "The explicitly requested rationale."
    assert target["rationale"] == "Target rationale."
    assert executable["rationale"] == executable["context"] == text
    assert "supplied_text_references" not in executable


def test_selected_refresh_decision_is_not_indexed_as_history():
    selected = {"id": "selected", "status": "pending", "rationale": "Required review evidence."}
    context = {"decisions": [deepcopy(selected)], "decision_refresh": {"decisions": [{"decision": selected}]}}
    _index_informational_decision_history(context, "Review selected decisions", None)
    assert context["decisions"] == [selected]


def test_accounting_reconciliation_and_executing_decisions_keep_complete_context():
    rows = [{"id": "reconcile_run", "status": "pending", "context": "Keep the uncertain charge."},
        {"id": "older_record", "status": "pending", "options": [{"id": "close_reserved"}],
            "rationale": "This choice closes an uncertain provider call."},
        {"id": "executing", "status": "executing", "context": "Current committed work."}]
    context = {"decisions": deepcopy(rows)}
    _index_informational_decision_history(context, "What should I do next?", None)
    assert context["decisions"] == rows


def test_default_manager_schema_scope_retains_science_and_explicit_overrides(tmp_path):
    from optimization_framework.campaigns.manager import CampaignManager
    from optimization_framework.contracts.requests import CampaignInput, TaskInput
    from optimization_framework.execution.service import Workspace
    from optimization_framework.research.coordinator import INTERFACE_COMMANDS
    workspace = Workspace(tmp_path)
    campaign = workspace.create_campaign(CampaignInput(name="Scientific question", tasks=[TaskInput(name="Quadratic", problem_id="bounded_continuous")]))
    manager = CampaignManager(workspace)
    context = manager.context(campaign["id"], "Which method should proceed to validation?")
    assert not set(context["application_commands"]) & INTERFACE_COMMANDS
    assert {"campaign.update", "study.create", "study.freeze_template", "trial.create", "draft.launch",
        "validation.require", "validation.execute", "hypothesis.review"} <= set(context["application_commands"])
    assert _safe_context(context)["application_command_scope"] == context["application_command_scope"]
    explicit = manager.context(campaign["id"], "Explain this interface operation", command_operations={"context.import"})
    assert set(explicit["application_commands"]) == {"context.import"}


def test_manager_curve_projection_retains_same_observed_points_without_archives(tmp_path):
    import json
    from optimization_framework.campaigns.manager import CampaignManager
    from optimization_framework.contracts.requests import CampaignInput, TaskInput, TrialInput
    from optimization_framework.execution.service import Workspace
    workspace = Workspace(tmp_path)
    campaign = workspace.create_campaign(CampaignInput(name="Observed curve", tasks=[TaskInput(name="Quadratic", problem_id="bounded_continuous")]))
    trial = workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=workspace.current_tasks(campaign["id"])[0]["id"],
        algorithm="coordinate", max_steps=60, wall_seconds=10))
    rows = [{"step": index, "best_objective": 1 / (index + 1), "elapsed_seconds": index / 10,
        "unknown_worker_cost": False, "archive": [{"candidate": [index]}]} for index in range(60)]
    (workspace.job_dir(trial["id"]) / "metrics.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    context = CampaignManager(workspace).context(campaign["id"], "Describe the observed trajectory")
    projected = next(row for row in context["trials"] if row["id"] == trial["id"])
    expected = [{key: value for key, value in rows[round(i * 59 / 39)].items() if key != "archive"} for i in range(40)]
    assert projected["curve"] == expected
    assert "including endpoints" in projected["curve_projection"]
    assert workspace.metrics(trial["id"]) == rows
