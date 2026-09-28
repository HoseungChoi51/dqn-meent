"""Review groups retain exact authority/evidence while reducing prompt scope."""
from copy import deepcopy

import pytest

from optimization_framework.research.context import _share_decision_context
from optimization_framework.research.decision_review_context import partition_context, synthesis_context


def context():
    text = "Keep the exact original scientific qualification and researcher comment. " * 8
    selected = []
    for identity, operation in [("draft_a", "draft.save"), ("draft_b", "draft.launch"), ("budget", "campaign.update")]:
        selected.append({"decision": {"id": identity, "title": identity, "status": "pending", "context": text,
            "options": [{"id": "accept", "label": "Approve", "description": text}], "charter_version": 2},
            "action": {"id": "action_" + identity, "kind": "command", "task_id": "task", "rationale": text,
                "command_operation": operation, "command_payload": {"task_id": "task"}}, "comment": text})
    result = {"campaign": {"id": "campaign", "version": 3, "charter": {"validation_reserve_seconds": 120}},
        "tasks": [{"id": "task", "split": "development"}],
        "research_inventory": {"trials": [{"id": "trial", "task_id": "task", "result": {"score": 0.87}, "seed": 7}]},
        "manager_context": {"guidance_revision": 4, "structured": {"researcher_guidance": text}},
        "decisions": [deepcopy(item["decision"]) for item in selected],
        "decision_refresh": {"decisions": selected, "comment": text,
            "continuing_researcher_request": {"message": "Compare the top two families on new wavelengths."}},
        "application_commands": {"campaign.update": {"payload_schema": {"type": "object", "description": text}}}}
    result["available_methods"] = {"method": {"parameter_schema": {"type": "object", "description": text,
        "properties": {"formula": {"type": "string", "pattern": text, "const": text, "default": text}}}}}
    result["decisions"][0].update(status="resolved", comment=text, choice="defer")
    _share_decision_context(result)
    return result


def expand(value, root):
    if isinstance(value, list):
        return [expand(item, root) for item in value]
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            target = root
            for key in value["$ref"][2:].split("/"):
                target = target[int(key)] if isinstance(target, list) else target[key]
            return expand(target, root)
        return {key: expand(item, root) for key, item in value.items()}
    return value


def test_related_decisions_share_group_without_changing_frozen_sources_or_evidence():
    source = context()
    before = deepcopy(source)
    groups = partition_context(source)
    assert [row["decision_ids"] for row in groups] == [["draft_a", "draft_b"], ["budget"]]
    originals = {item["decision"]["id"]: expand(item, source) for item in source["decision_refresh"]["decisions"]}
    for group in groups:
        scoped = group["context"]
        assert "application_commands" not in scoped
        expanded = expand(scoped, scoped)
        assert expanded["decision_refresh"]["decisions"] == [originals[key] for key in group["decision_ids"]]
        assert expanded["campaign"] == expand(source["campaign"], source)
        assert expanded["research_inventory"] == source["research_inventory"]
        assert scoped["available_methods"] == source["available_methods"]
        assert expanded["decision_refresh"]["continuing_researcher_request"] == source["decision_refresh"]["continuing_researcher_request"]
        current = next(row for row in expanded["decisions"] if row["id"] == "draft_a")
        assert current["status"] == "resolved" and current["choice"] == "defer"
        assert current["comment"] == originals["draft_a"]["comment"]
    assert source == before


def test_synthesis_requires_coverage_and_supplies_all_reviews_with_valid_original_schemas():
    source = context()
    before = deepcopy(source)
    groups = partition_context(source)
    reviews = [{"task_id": "review_" + str(index), "decision_ids": group["decision_ids"], "title": group["title"],
        "role": "comparative_reviewer", "result": {"analysis": "Dependent on the other group's resource assessment."}}
        for index, group in enumerate(groups)]
    with pytest.raises(ValueError, match="Every selected decision"):
        synthesis_context(source, reviews[:1])
    with pytest.raises(ValueError, match="outside"):
        synthesis_context(source, [{**reviews[0], "decision_ids": ["unknown"]}])
    merged = synthesis_context(source, reviews)
    expanded = expand(merged, merged)
    assert expanded["decision_refresh"]["review_results"] == reviews
    assert expanded["decision_refresh"]["decisions"] == expand(source["decision_refresh"]["decisions"], source)
    assert merged["application_commands"] == source["application_commands"]
    assert merged["available_methods"] == source["available_methods"]
    assert source == before


def test_group_cap_combines_related_overflow_without_omitting_a_decision():
    source = context()
    groups = partition_context(source, max_groups=1)
    assert len(groups) == 1
    assert groups[0]["decision_ids"] == ["draft_a", "draft_b", "budget"]
    for count in [0, 9]:
        with pytest.raises(ValueError, match="one through eight"):
            partition_context(source, max_groups=count)
