"""A bounded manager turn still knows executable proposals and actual results."""
from copy import deepcopy

from optimization_framework.research.context import LIMIT, WORKING_LIMIT, bounded, size
from optimization_framework.research.engine import _safe_context


def crowded_context():
    return {
        "campaign": {"id": "campaign", "objective": "Compare available methods"},
        "tasks": [{"id": "task", "split": "development"}],
        "manager_context": {"document": "history " * 9000,
            "structured": {"narrative_guidance": "Keep the latest decisions and the shared comparison fixed.",
                "delegation": {"per_experiment_seconds": 45}, "resources": {"compute_cap_seconds": 600},
                "pending_issues": [{"id": "issue", "affected_ids": ["unrelated_task"]}],
                "next_actions": [{"id": "decision", "status": "pending"}],
                "findings": [], "counterevidence": [], "reuse_decisions": [], "source_ids": []},
            "retrieved_records": []},
        "application_commands": {"trial.create": {"delegable": True, "payload_schema": {
            "title": "Cosmetic label " * 5000, "type": "object", "additionalProperties": False,
            "dependentRequired": {"title": ["parameters"]},
            "properties": {"title": {"title": "Display title", "type": "string"},
                "parameters": {"$ref": "#/$defs/Parameters"}},
            "required": ["parameters"], "$defs": {"Parameters": {
                "type": "object", "properties": {"title": {"type": "string"}},
                "default": {"title": "This is data, not a schema label"}}}}}},
        "hypotheses": [{"id": "hill", "title": "Restart hill climbing", "algorithm": "hillclimb",
            "algorithm_config": {"restart_patience": 16}, "parent_ids": ["parent"],
            "status": "proposed", "reviews": [{"id": "review", "text": "scientific detail " * 15000}],
            "sources": []}],
        "implementation_readiness": {"hill": {"state": "ready", "runnable": True,
            "reason": "Built-in implementation is available.", "validation_level": "bundled_regression"}},
        "trials": [{"id": "random_trial", "task_id": "task", "task_split": "development", "status": "completed",
            "algorithm": "random", "algorithm_config": {}, "seed": 0, "max_steps": 960, "wall_seconds": 45,
            "question": "Does annealing beat hill climbing?", "hypothesis_id": None,
            "result": {"best_objective": .3549, "evaluations": 960, "elapsed_seconds": 5.79,
                "scientific_complete": True, "archive": ["large saved candidate " * 15000]}}],
        "decisions": [{"id": "decision", "status": "pending", "title": "Keep this current decision",
            "context": "Authoritative unresolved detail " * 300}],
        "evidence_library": [], "history": [], "available_implementations": [],
    }


def test_inventory_survives_when_detailed_science_rows_are_pruned():
    original = crowded_context()
    unchanged = deepcopy(original)
    context = bounded(original, question="Test proposals with available implementations")
    assert size(context) <= LIMIT
    assert context["hypotheses"] == [] and context["trials"] == []
    assert context["decisions"] == original["decisions"]
    for key in ("narrative_guidance", "delegation", "resources", "pending_issues", "next_actions"):
        assert context["manager_context"]["structured"][key] == original["manager_context"]["structured"][key]
    inventory = _safe_context(context)["research_inventory"]
    proposal = inventory["proposals"][0]
    assert proposal["id"] == "hill" and proposal["algorithm_config"] == {"restart_patience": 16}
    assert proposal["implementation_readiness"] == original["implementation_readiness"]["hill"]
    assert proposal["parent_ids"] == ["parent"] and proposal["review_ids"] == ["review"]
    assert "reviews" not in proposal and "sources" not in proposal
    measurement = inventory["trials"][0]
    assert measurement["algorithm"] == "random" and measurement["max_steps"] == 960
    assert measurement["measurement"] == {"best_objective": .3549, "evaluations": 960,
        "elapsed_seconds": 5.79, "scientific_complete": True}
    assert measurement["measurement_basis"] == "result"
    assert "archive" not in measurement["measurement"]
    assert "not supplied" in inventory["basis"]
    assert original == unchanged


def test_schema_compaction_preserves_named_title_fields_refs_and_literal_data():
    context = bounded(crowded_context())
    schema = context["application_commands"]["trial.create"]["payload_schema"]
    assert "title" not in schema
    assert schema["properties"]["title"] == {"type": "string"}
    assert schema["properties"]["parameters"] == {"$ref": "#/$defs/Parameters"}
    assert schema["$defs"]["Parameters"]["properties"]["title"] == {"type": "string"}
    assert schema["$defs"]["Parameters"]["default"] == {"title": "This is data, not a schema label"}
    assert schema["required"] == ["parameters"] and schema["additionalProperties"] is False
    assert schema["dependentRequired"] == {"title": ["parameters"]}


def test_inventory_does_not_bypass_heldout_or_locked_trial_visibility():
    context = bounded(crowded_context())
    base = context["research_inventory"]["trials"][0]
    context["research_inventory"]["trials"] += [
        {**base, "id": "heldout", "task_split": "heldout"},
        {**base, "id": "locked", "locked": True},
        {**base, "id": "foreign", "task_id": "foreign_task"},
        {**base, "id": "released", "task_split": "confirmation", "confirmation_released": True},
    ]
    safe = _safe_context(context)
    assert [row["id"] for row in safe["research_inventory"]["trials"]] == ["random_trial", "released"]


def test_inventory_keeps_missing_readiness_distinct_from_ready():
    context = crowded_context()
    context["hypotheses"].append({"id": "novel", "title": "Needs implementation", "algorithm": "custom", "algorithm_config": {}})
    context["implementation_readiness"]["novel"] = {"state": "missing", "runnable": False, "reason": "No executable."}
    result = bounded(context)
    inventory = {row["id"]: row for row in result["research_inventory"]["proposals"]}
    assert inventory["hill"]["implementation_readiness"]["runnable"] is True
    assert inventory["novel"]["implementation_readiness"]["runnable"] is False
    assert inventory["novel"]["implementation_readiness"]["state"] == "missing"


def test_decision_growth_and_long_request_do_not_displace_scientific_inventory():
    original = crowded_context()
    request = "Run the explicitly bound comparison. " * 500
    original["manager_context"]["structured"]["next_actions"].append(
        {"id": "current_request", "status": "queued", "title": request})
    original["manager_context"]["structured"]["next_actions"].append(
        {"id": "turn_old_blocked", "status": "blocked", "title": "Earlier bounded request.\n" + "Historical instruction " * 1000})
    original["history"] = [{"id": "current_message", "content": request, "role": "user"}]
    rationale = "This is one shared recorded rationale; it is not twenty independent findings. " * 250
    original["decisions"] = [{"id": f"decision_{index}", "status": "pending", "title": f"Exact question {index}?",
        "rationale": rationale, "context": rationale, "options": [{"id": "accept", "label": "Proceed"}],
        "comment": "Keep this exact user comment", "resolution_revision": 2, "charter_version": 3}
        for index in range(20)]
    before = deepcopy(original)
    context = bounded(original, question=request)
    assert size(context) <= 155 * 1024
    assert len(context["research_inventory"]["proposals"]) == 1
    assert len(context["research_inventory"]["trials"]) == 1
    decisions = {row["id"]: row for row in context["decisions"]}
    for row in before["decisions"]:
        current = decisions[row["id"]]
        for key in ("title", "options", "comment", "resolution_revision", "charter_version"):
            assert current[key] == row[key]
        for field in ("rationale", "context"):
            if field in current:
                assert current[field] == rationale
            else:
                ref = current["supplied_text_references"][field]
                assert decisions[ref["decision_id"]][ref["field"]] == rationale
    action = next(row for row in context["manager_context"]["structured"]["next_actions"] if row["id"] == "current_request")
    assert action["title_reference"] == "researcher_request.message"
    historical = next(row for row in context["manager_context"]["structured"]["next_actions"] if row["id"] == "turn_old_blocked")
    assert historical["title"] == "Earlier bounded request." and historical["status"] == "blocked"
    assert historical["title_is_excerpt"] and historical["full_request_record_id"] == "turn_old_blocked"
    assert original == before


def test_parallel_active_trials_keep_settings_measurements_and_controls_once():
    context = crowded_context()
    base = context["trials"][0]
    context["trials"] = [{**deepcopy(base), "id": f"active_{index}", "status": "running",
        "hypothesis_id": "hill", "algorithm": "hillclimb", "algorithm_config": {"restart_patience": 16},
        "seed": index, "control_revision": 3, "attempt": 1,
        "result": None, "progress": deepcopy(base["result"])} for index in range(32)]
    context["active_study"] = {"comparison": {"matrix": "Complete study instructions " * 500}}
    before = deepcopy(context)
    out = bounded(context, question="Inspect the running parallel comparison")
    assert size(out) <= 155 * 1024
    assert len(out["trials"]) == 32
    assert out["active_study"] == before["active_study"]
    for row in out["trials"]:
        assert row["algorithm"] == "hillclimb" and row["algorithm_config"] == {"restart_patience": 16}
        assert row["control_revision"] == 3 and row["attempt"] == 1
        assert row["progress"]["best_objective"] == .3549 and "result" not in row
        assert "archive" not in row["progress"] and "context_projection" in row
    indexed = out["research_inventory"]["trials"]
    assert {row["supplied_trial_reference"] for row in indexed} == {row["id"] for row in out["trials"]}
    assert context == before


def test_complete_32_cell_cohort_preserves_every_configuration_and_measurement():
    from optimization_framework.campaigns.commands import CommandService
    context = crowded_context()
    context["application_commands"] = {key: value for key, value in CommandService.describe().items()
        if not key.startswith("discovery.")}
    context["manager_context"]["structured"]["narrative_guidance"] = "Current comparison constraints. " * 1200
    context["trials"] = []
    for index in range(32):
        context["trials"].append({"id": f"completed_{index}", "task_id": "task", "task_split": "development",
            "study_id": "current_study", "status": "completed", "algorithm": "dqn", "hypothesis_id": "hill",
            "algorithm_config": {"learning_starts": 32 + index, "learning_rate": .001},
            "training": {"horizon": 128, "learning_starts": 32 + index}, "seed": index % 2,
            "max_steps": 1536, "schedule_steps": 1536, "wall_seconds": 45,
            "completion": {"unit": "evaluation_requests", "count": 1536},
            "result": {"best_objective": index / 100, "evaluations": 1536, "elapsed_seconds": 12 + index / 10,
                "scientific_complete": True, "diagnostics": {"updates": 1400 - index, "phase": "learning"},
                "archive": ["saved archive body " * 2000]}})
    context["active_study"] = {"id": "current_study", "comparison": {"trial_requests": [
        {key: row[key] for key in ("algorithm", "hypothesis_id", "algorithm_config", "training", "seed", "max_steps", "wall_seconds")}
        for row in context["trials"]], "protocol": "Complete predeclared protocol. " * 500}}
    before = deepcopy(context)
    out = bounded(context, question="Evaluate every cell in the completed comparison")
    assert LIMIT == 224 * 1024 and WORKING_LIMIT == 155 * 1024
    assert size(out) <= LIMIT
    indexed = {row["id"]: row for row in out["research_inventory"]["trials"]}
    assert len(indexed) == 32
    for original in before["trials"]:
        row = indexed[original["id"]]
        for key in ("algorithm_config", "training", "seed", "completion", "max_steps", "wall_seconds"):
            assert row[key] == original[key]
        for key in ("best_objective", "evaluations", "elapsed_seconds", "scientific_complete", "diagnostics"):
            assert row["measurement"][key] == original["result"][key]
    assert out["manager_context"]["structured"]["narrative_guidance"] == before["manager_context"]["structured"]["narrative_guidance"]
    assert context == before
