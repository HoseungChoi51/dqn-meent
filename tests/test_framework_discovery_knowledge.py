"""Proposal generation is grounded, independent and distinct from validation."""
from copy import deepcopy

import pytest

from framework_fixtures import researcher_idea
from test_framework_discovery import Adapter, setup, start, settle
from optimization_framework.research.discovery import knowledge
from optimization_framework.research.discovery.models import DiscoveryArtifact, DiscoveryResult, DiscoveryTaskBrief
from optimization_framework.storage.sqlite import now


def fixture_artifact(workspace, session, kind, content):
    identity = "fixture_" + kind
    return workspace.store.put_immutable("discovery_artifact", {"id": identity, "campaign_id": session["campaign_id"],
        "session_id": session["id"], "task_id": workspace.discovery.tasks(session)[0]["id"], "kind": kind,
        "title": "Fixture " + kind, "content": content, "stale": False, "created_at": now(), "origin": "fixture"})


def candidate_content(dossier, mapping):
    return {"dossier_ids": [dossier["id"]], "literature_map_ids": [mapping["id"]], "candidates": [{
        "key": "local", "title": "Adaptive local search", "family": "Trust-region family",
        "mechanism": "Adjust local perturbation radius based on improvement", "applicability": "The declared evaluator accepts bounded continuous inputs",
        "assumptions": ["Local improvement is informative"], "predictions": ["Smaller radii improve late precision"],
        "failure_modes": ["Premature convergence"], "parameter_space": {"radius": {"type": "number", "minimum": 0.01, "maximum": 0.5}},
        "startup_requirements": "Evaluate an initial point and a local neighborhood",
        "implementation_needs": "Implement adaptive radius; independent correctness validation is required",
        "cheapest_test": "Test three initial radii with paired random seeds", "conjectures": ["The adaptation transfers to this problem; not yet tested"]}]}


def test_new_campaign_has_no_curated_proposals_and_generators_share_frozen_evidence(setup):
    workspace, campaign = setup
    assert workspace.store.list("hypothesis", campaign["id"]) == []
    session, _ = start(workspace, campaign, max_concurrent_tasks=1)
    settle(workspace, campaign, turns=3)
    dossier = fixture_artifact(workspace, session, "problem_dossier", {"fixture": "declared continuous problem"})
    mapping = fixture_artifact(workspace, session, "literature_map", {"fixture": "explicit coverage gap"})
    content = candidate_content(dossier, mapping)
    class Generator(Adapter):
        def result(self, role, context):
            result = deepcopy(content)
            result["candidates"][0]["title"] = role + " variant"
            return DiscoveryResult(summary="A conjecture to test", artifacts=[DiscoveryArtifact(
                kind="candidate_batch", title="Independent proposal", content=result)])
    workspace.discovery.adapter_factory = Generator
    tasks = workspace.discovery.add_tasks(session, [DiscoveryTaskBrief(key=role, role=role, stage="generate",
        objective="Propose an applicable mechanism", evidence_ids=[dossier["id"], mapping["id"]]) for role in ("local_specialist", "cross_domain_explorer")], batch_id="generation_1")
    settle(workspace, campaign, turns=1)
    researcher_idea(workspace, campaign["id"])
    settle(workspace, campaign, turns=1)
    calls = Adapter.calls[-2:]
    assert calls[0]["context"]["discovery"]["shared_context_hash"] == calls[1]["context"]["discovery"]["shared_context_hash"]
    assert calls[0]["context"]["hypotheses"] == calls[1]["context"]["hypotheses"] == []
    candidates = workspace.store.list("discovery_candidate")
    assert len(candidates) == 2 and len(workspace.store.list("methodology_family")) == 1
    hypotheses = [row for row in workspace.store.list("hypothesis") if row.get("candidate_id")]
    assert all(row["implementation_status"] == "missing" and not row["executable"] for row in hypotheses)
    assert not workspace.store.list("trial") and not workspace.store.list("implementation_grant")
    first = candidates[0]
    second_task = workspace.store.get(tasks[1]["id"])
    for identity in (first["id"], "hypothesis_" + first["id"], tasks[0]["id"], first["artifact_id"]):
        with pytest.raises(ValueError, match="Independent workers"):
            workspace.discovery._evidence(session, identity, task=second_task)
    memory = workspace.memory.sync(campaign["id"])["structured"]["discovery"]
    assert memory["candidate_ids"] == [row["id"] for row in candidates]


def test_citations_require_the_actual_passage_not_just_its_identifier(setup, monkeypatch):
    from optimization_framework.research.literature import LiteratureReader
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    task = workspace.discovery.tasks(session)[0]
    source = {"id": "source_fixture", "campaign_id": campaign["id"], "url": "https://arxiv.org/abs/2401.00001"}
    workspace.store.put_immutable("source", source)
    monkeypatch.setattr("optimization_framework.research.literature.fetch_document", lambda url:
        (b"<article><p>Method passage.</p><p>Parameter passage.</p></article>", url, "text/html"))
    read = LiteratureReader(workspace).read(campaign["id"], source_id=source["id"], limit=1)
    capture = read["capture"]
    receipt = workspace.store.put_immutable("source_retrieval", {"id": "retrieval_fixture", "campaign_id": campaign["id"], "result": {"sources": [source]}})
    # A capture indexes both passages, but the agent received only the first.
    context = {"retrieval_id": receipt["id"], "tool_result": read}
    task["attempt_id"] = "fixture_attempt"
    workspace.store.put_immutable("discovery_attempt", {"id": task["attempt_id"], "context_snapshot": context})
    support = {"claim": "Parameter guidance", "source_id": source["id"], "capture_id": capture["id"], "passage_ids": [capture["passage_ids"][1]]}
    content = {"search_strategy": "Read a primary method", "retrieval_ids": [receipt["id"]], "methods": [{
        "name": "Local method", "mechanism": "Local improvement", "applicability": "Bounded continuous problem",
        "assumptions": ["Locality"], "parameter_guidance": "Vary the radius", "support": [support]}]}
    artifact = DiscoveryArtifact(kind="literature_map", title="Applicability", content=content)
    with pytest.raises(ValueError, match="actually supplied"):
        knowledge.validate(workspace.discovery, session, task, artifact)
    support["passage_ids"] = [read["passages"][0]["id"]]
    artifact = DiscoveryArtifact(kind="literature_map", title="Applicability", content=content)
    assert knowledge.validate(workspace.discovery, session, task, artifact)["methods"][0]["support"]


def test_assigned_passages_carry_actual_retrieval_receipts_into_literature_task(setup, monkeypatch):
    from optimization_framework.research.literature import LiteratureReader
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    author = workspace.discovery.tasks(session)[0]
    source = {"id": "source_handoff", "campaign_id": campaign["id"], "url": "https://arxiv.org/abs/2401.00001"}
    workspace.store.put_immutable("source", source)
    monkeypatch.setattr("optimization_framework.research.literature.fetch_document", lambda url:
        (b"<article><p>A local method retains successful perturbations.</p><p>Other unassigned passage.</p></article>", url, "text/html"))
    read = LiteratureReader(workspace).read(campaign["id"], source_id=source["id"], limit=1)
    receipt = workspace.store.put_immutable("discovery_tool_receipt", {
        "id": "receipt_handoff", "campaign_id": campaign["id"], "session_id": session["id"],
        "task_id": author["id"], "tool": "source.read", "result": read, "status": "completed", "error": None})
    task = workspace.discovery.add_tasks(session, [DiscoveryTaskBrief(key="study", role="literature_investigator",
        stage="study", objective="Study assigned passages", evidence_ids=[read["passages"][0]["id"]])], batch_id="handoff")[0]
    context = workspace.discovery._context(session, task)
    assert context["discovery"]["retrieval_receipts"] == [receipt]
    task["attempt_id"] = "handoff_attempt"
    workspace.store.put_immutable("discovery_attempt", {"id": task["attempt_id"], "context_snapshot": context})
    content = {"search_strategy": "Read an inherited primary source", "retrieval_ids": [receipt["id"]], "methods": [{
        "name": "Local method", "mechanism": "Retain improvements", "applicability": "Test local structure",
        "assumptions": ["Useful neighborhoods"], "parameter_guidance": "Not supplied",
        "support": [{"claim": "A local update mechanism", "source_id": source["id"], "capture_id": read["capture"]["id"],
                     "passage_ids": [read["passages"][0]["id"]]}]}]}
    assert knowledge.validate(workspace.discovery, session, task,
        DiscoveryArtifact(kind="literature_map", title="Inherited source", content=content))["retrieval_ids"] == [receipt["id"]]
    content["retrieval_ids"] = [source["id"]]
    with pytest.raises(ValueError, match="retrieval receipts"):
        knowledge.validate(workspace.discovery, session, task, DiscoveryArtifact(kind="literature_map", title="Wrong ID", content=content))


def test_generator_can_receive_trial_evidence_whose_task_is_a_problem_not_an_agent(setup):
    from optimization_framework.contracts.requests import TrialInput
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    trial = workspace.create_trial(TrialInput(campaign_id=campaign["id"], task_id=session["problem_task_id"],
                                             algorithm="coordinate", max_steps=2, wall_seconds=5))
    task = workspace.discovery.add_tasks(session, [DiscoveryTaskBrief(key="generator", role="methodology_specialist",
        stage="generate", objective="Use the assigned measurement", evidence_ids=[trial["id"]])], batch_id="measured")[0]
    evidence = workspace.discovery._context(session, task)["discovery"]["evidence"]
    assert len(evidence) == 1 and evidence[0]["id"] == trial["id"]
    assert evidence[0]["task_id"] == session["problem_task_id"]


def test_valid_map_survives_bad_assignment_and_dependency_supplies_saved_artifact(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign, max_concurrent_tasks=1)
    settle(workspace, campaign, turns=2)
    manager = next(t for t in workspace.discovery.tasks(session) if t["brief"]["role"] == "campaign_manager")
    receipt = workspace.store.put_immutable("discovery_tool_receipt", {
        "id": "receipt_failed_search", "campaign_id": campaign["id"], "session_id": session["id"],
        "task_id": manager["id"], "tool": "source.search", "result": None, "status": "failed", "error": "Network unavailable"})
    manager["brief"]["evidence_ids"] = [receipt["id"]]
    workspace.store.put("discovery_task", manager)
    class InvalidAgenda(Adapter):
        def result(self, role, context):
            if context["discovery"]["step"]:
                assert context["discovery"]["own_artifacts"][0]["kind"] == "literature_map"
                return DiscoveryResult(summary="Corrected assignment", proposed_tasks=[DiscoveryTaskBrief(
                    key="reader", role="critic", stage="critique", objective="Inspect map", dependencies=[manager["id"]])])
            return DiscoveryResult(summary="A valid gap map with invalid agenda", artifacts=[DiscoveryArtifact(kind="literature_map",
                title="Recorded retrieval gap", content={"search_strategy": "Attempted retrieval", "retrieval_ids": [receipt["id"]],
                    "methods": [{"name": "Local search", "mechanism": "Perturb candidates", "applicability": "Unverified transfer",
                        "assumptions": ["Local structure"], "parameter_guidance": "Unknown", "coverage_gaps": ["Search failed"]}]})],
                proposed_tasks=[DiscoveryTaskBrief(key="bad", role="critic", objective="Inspect map", dependencies=["does_not_exist"])])
    workspace.discovery.adapter_factory = InvalidAgenda
    settle(workspace, campaign, turns=1)
    saved = workspace.store.get(manager["id"])
    assert saved["status"] == "queued" and len(saved["artifact_ids"]) == 1
    mapping = workspace.store.get(saved["artifact_ids"][0])
    settle(workspace, campaign, turns=1)
    assert workspace.store.get(manager["id"])["status"] == "completed"
    child = next(t for t in workspace.discovery.tasks(session) if t["brief"]["key"] == "reader")
    context = workspace.discovery._context(session, child)
    assert mapping in context["discovery"]["evidence"]
    assert len([a for a in workspace.store.list("discovery_artifact") if a["kind"] == "literature_map"]) == 1
