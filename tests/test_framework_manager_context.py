"""Structured memory remains inspectable across sessions without granting work."""
from framework_fixtures import researcher_idea
from copy import deepcopy
import json

import pytest

from optimization_framework.contracts.commands import Command
from optimization_framework.contracts.manager import CampaignContext
from optimization_framework.contracts.requests import CampaignInput, TaskInput
from optimization_framework.storage.sqlite import now
from optimization_framework.execution.service import Workspace


def prepared(tmp_path):
    workspace = Workspace(tmp_path)
    campaign = workspace.create_campaign(CampaignInput(name="Long-lived memory", tasks=[TaskInput(name="Quadratic", problem_id="bounded_continuous")]))
    return workspace, campaign


def test_large_history_uses_retrieval_without_discarding_current_constraints(tmp_path):
    workspace, campaign = prepared(tmp_path)
    cid = campaign["id"]
    current = workspace.memory.sync(cid)
    guidance = "Keep the original comparison fixed.\n" + "x" * 45000
    workspace.memory.edit(cid, guidance, current["revision"])
    workspace.memory.issue(cid, "scoped_check", "Review this check before reusing its evaluator.", affected="evaluator_one")
    with workspace.store.transaction():
        for index in range(450):
            workspace.store.put("manager_note", {"id": f"historic_{index}", "campaign_id": cid, "kind": "finding",
                "content": ("Cobalt was inconclusive because warmup dominated. " if index == 0 else f"Routine result {index}. ") + "detail " * 70,
                "source_ids": [], "created_at": now()}, "manager.note")
    package = workspace.memory.assemble(cid, "Why was Cobalt inconclusive?")
    assert len(json.dumps(package).encode()) <= 96 * 1024
    assert package["structured"]["narrative_guidance"] == guidance
    assert package["structured"]["pending_issues"][0]["affected_ids"] == ["evaluator_one"]
    assert package["structured"]["delegation"]["authority_hash"] == workspace.commands.authority_hash(campaign)
    assert package["history_counts"]["findings"] == 450
    assert any(record["id"] == "historic_0" for record in package["retrieved_records"])
    exported = json.loads((workspace.directory / "campaigns" / cid / "manager/context.json").read_text())
    assert len(exported["findings"]) == 450


def test_whole_model_view_bounds_accumulated_sources_and_preserves_selected_target(tmp_path):
    from optimization_framework.campaigns.manager import CampaignManager
    from optimization_framework.research.context import LIMIT, size
    workspace, campaign = prepared(tmp_path)
    target = researcher_idea(workspace, campaign["id"])
    with workspace.store.transaction():
        for index in range(80):
            workspace.store.put("source", {"id": f"paper_{index}", "campaign_id": campaign["id"],
                "title": "Cobalt reference" if index == 0 else "Later literature",
                "excerpt": "Reference material " * 700}, "source.created")
        for index in range(50):
            workspace.store.put("hypothesis", {**target, "id": f"later_hypothesis_{index}", "title": "Later proposal",
                "rationale": "A long provisional rationale. " * 100}, "hypothesis.created")
    manager = CampaignManager(workspace)
    context = manager.context(campaign["id"], "Review the Cobalt reference", target_id=target["id"])
    assert size(context) <= LIMIT
    assert target["id"] in {row["id"] for row in context["hypotheses"]}
    assert "paper_0" in {row["id"] for row in context["evidence_library"]}
    assert context["evidence_selection"]["omitted_counts"]["evidence_library"] > 0
    assert len(workspace.store.list("source")) == 80
    assert context["manager_context"]["structured"]["narrative_guidance"] == workspace.memory.state(campaign["id"])["guidance"]


def test_reusable_findings_require_scope_and_keep_limits_across_campaign_reference(tmp_path):
    workspace, campaign = prepared(tmp_path)
    cid = campaign["id"]
    source = workspace.commands.execute(Command(id="source", campaign_id=cid, expected_revision=1, operation="source.record",
        payload={"title": "Historical benchmark", "url": "https://example.org/paper", "excerpt": "A small benchmark."}))["outcome"]["source_id"]
    values = {"content": "Coordinate search is promising for this instance.", "source_ids": [source], "publish": True}
    command = Command(id="reusable", campaign_id=cid, expected_revision=1, operation="finding.record", payload=values)
    with pytest.raises(ValueError, match="scope and explicit limitations"):
        workspace.commands.execute(command)
    values.update(problem_scope="The recorded bounded quadratic instance only.", limitations=["One historical example; no universal optimizer claim."],
        study_ids=[campaign["active_study_id"]])
    command = command.model_copy(update={"payload": values})
    accepted = workspace.commands.execute(command)
    asset = workspace.store.get(accepted["outcome"]["asset_id"], "asset")
    assert asset["payload"]["classification"] == "interpretation" and asset["cost_provenance"] == "partial"
    second = workspace.create_campaign(CampaignInput(name="New problem", tasks=[TaskInput(name="Other", problem_id="bounded_continuous")]))
    assert workspace.assets.manager_references(second["id"], second["active_study_id"]) == []
    workspace.commands.execute(Command(id="read_finding", campaign_id=second["id"], expected_revision=1, operation="asset.reuse",
        payload={"asset_id": asset["id"], "study_id": second["active_study_id"], "decision": "reference",
            "intended_use": "manager_evidence", "rationale": "Consider the restricted prior claim while designing a fresh comparison."}))
    referenced = workspace.assets.manager_references(second["id"], second["active_study_id"])[0]
    assert referenced["payload"]["evidence_ids"] == [source]
    assert referenced["payload"]["limitations"] == values["limitations"]
    counter = workspace.commands.execute(Command(id="counter", campaign_id=cid, expected_revision=1, operation="finding.record",
        payload={"content": "The prior source did not match our instance.", "source_ids": [source],
            "interpretation": "counterevidence", "counterevidence_for": [accepted["outcome"]["finding_id"]],
            "publish": True, "problem_scope": values["problem_scope"], "limitations": values["limitations"]}))
    structured = workspace.memory.sync(cid)["structured"]
    assert structured["counterevidence"][0]["id"] == counter["outcome"]["finding_id"]
    assert workspace.commands.execute(command) == accepted
    from optimization_framework.assets.bundle_export import Exporter
    from optimization_framework.storage.bundles import Reader
    path = tmp_path / "qualified-finding.zip"
    Exporter(workspace).export([counter["outcome"]["asset_id"]], path)
    with Reader(path) as reader:
        records = reader.verify()
        assert {asset["id"], accepted["outcome"]["finding_id"], counter["outcome"]["finding_id"], source} <= {row.reference.id for row in records}


def test_structured_context_exports_and_imports_only_researcher_guidance(tmp_path):
    workspace, campaign = prepared(tmp_path)
    before = workspace.memory.sync(campaign["id"])
    root = workspace.directory / "campaigns" / campaign["id"] / "manager"
    document = json.loads((root / "context.json").read_text())
    assert CampaignContext(**document).objective == campaign["objective"]
    assert (root / "records.jsonl").is_file() and (root / "revisions/00000001.json").is_file()
    document["narrative_guidance"] = "Keep the coordinate baseline; compare at equal full upstream cost."
    request = Command(id="import_context", operation="context.import", campaign_id=campaign["id"], expected_revision=1,
        payload={"document": document})
    accepted = workspace.commands.execute(request)
    restarted = Workspace(tmp_path)
    assert restarted.commands.execute(request) == accepted
    after = restarted.memory.assemble(campaign["id"], "baseline")
    assert after["structured"]["narrative_guidance"] == document["narrative_guidance"]
    assert after["guidance_revision"] == before["guidance_revision"] + 1
    assert not restarted.store.list("trial")
    with pytest.raises(ValueError, match="memory changed"):
        restarted.commands.execute(request.model_copy(update={"id": "stale_import"}))
    changed = deepcopy(after["structured"])
    changed["resources"]["compute_cap_seconds"] *= 2
    with pytest.raises(ValueError, match="owning commands"):
        restarted.commands.execute(request.model_copy(update={"id": "smuggled_budget", "payload": {"document": changed}}))
    assert restarted.store.get(campaign["id"], "campaign")["compute_budget_seconds"] == campaign["compute_budget_seconds"]


def test_findings_keep_observations_interpretations_endorsements_and_counterevidence_separate(tmp_path):
    workspace, campaign = prepared(tmp_path)
    source = researcher_idea(workspace, campaign["id"])
    for level in ("observation", "interpretation", "researcher_endorsed"):
        workspace.commands.execute(Command(id="finding_" + level, campaign_id=campaign["id"], operation="finding.record",
            expected_revision=1, payload={"content": "Scoped claim: " + level, "source_ids": [source["id"]], "interpretation": level}))
    workspace.memory.add_notes(campaign["id"], [{"kind": "counterevidence", "content": "A later sample contradicts the provisional claim.",
        "source_ids": [source["id"]]}], [source["id"]])
    context = workspace.memory.sync(campaign["id"])["structured"]
    assert {row["classification"] for row in context["findings"]} == {"observation", "provisional_interpretation", "researcher_endorsement"}
    assert context["counterevidence"][0]["classification"] == "counterevidence"
