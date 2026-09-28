"""Explicit parent intent, independent textual judgment, then numerical admission."""
from copy import deepcopy
import json

import pytest

from framework_fixtures import researcher_idea
from test_framework_discovery import Adapter, command, setup, settle, start
from test_framework_discovery_knowledge import candidate_content, fixture_artifact
from test_framework_discovery_assessment import prepared
from optimization_framework.contracts.requests import ResearchInput, TrialInput
from optimization_framework.research.discovery import knowledge, proposals
from optimization_framework.research.discovery.models import DiscoveryArtifact, DiscoveryResult, DiscoveryTaskBrief


def exploration(setup, operation="hybrid"):
    workspace, campaign = setup
    session, _ = start(workspace, campaign, max_concurrent_tasks=1, experiment_compute_seconds=60)
    settle(workspace, campaign, turns=3)
    parents = [researcher_idea(workspace, campaign["id"]) for _ in range(2)]
    dossier = fixture_artifact(workspace, session, "problem_dossier", {"fixture": "continuous quadratic"})
    mapping = fixture_artifact(workspace, session, "literature_map", {"fixture": "unverified transfer"})
    workspace.discovery.control(campaign["id"], {"session_id": session["id"], "action": "pause", "expected_control_revision": 0})
    request = {"message": "Explore coherent alternatives with independent review.", "mode": "generate",
               "proposal_operation": operation, "parent_hypothesis_ids": [p["id"] for p in parents[:{"expand": 0, "diversify": 1, "hybrid": 2}[operation]]]}
    workspace.commands.execute(command(workspace, campaign, "research.start", request, "explore"))
    session = workspace.discovery.active(campaign["id"])
    workspace.discovery._admit_guidance(session)
    manager = next(t for t in workspace.discovery.tasks(session) if t.get("manager_command_id") == "turn_explore")
    content = candidate_content(dossier, mapping)
    content["candidates"][0].update(algorithm="coordinate", parent_hypothesis_ids=request["parent_hypothesis_ids"],
                                     revision_basis=["Combine local radius choice with global restarts; fixture only"])
    return workspace, campaign, session, parents, manager, content


def test_proposal_request_rejects_missing_duplicate_and_foreign_parents(setup):
    workspace, campaign = setup
    for parents in ([], ["a"], ["a", "a"]):
        with pytest.raises(ValueError, match="distinct parent"):
            ResearchInput(campaign_id=campaign["id"], message="Hybrid", mode="generate",
                          proposal_operation="hybrid", parent_hypothesis_ids=parents)
    request = ResearchInput(campaign_id=campaign["id"], message="More", mode="generate", proposal_operation="expand")
    with pytest.raises(ValueError, match="Start an optimizer discovery"):
        workspace.manager.validate_request(request)
    session, _ = start(workspace, campaign)
    parent = researcher_idea(workspace, campaign["id"])
    parent["campaign_id"] = "another_campaign"
    workspace.store.put("hypothesis", parent)
    with pytest.raises(ValueError, match="from this campaign"):
        workspace.manager.validate_request(request.model_copy(update={"proposal_operation": "diversify", "parent_hypothesis_ids": [parent["id"]]}))


@pytest.mark.parametrize("operation", ["expand", "diversify", "hybrid"])
def test_request_survives_manager_delegation_and_preserves_selected_lineage(setup, operation):
    workspace, campaign, session, parents, manager, content = exploration(setup, operation)
    context = workspace.discovery._context(session, manager)
    request = context["discovery"]["researcher_request"]["request"]
    assert request["proposal_operation"] == operation
    if operation == "expand":
        assert {p["id"] for p in parents} <= {r["id"] for r in context["discovery"]["evidence"]}
    task = workspace.discovery.add_tasks(session, [DiscoveryTaskBrief(key="generate", role="hybrid_designer", stage="generate",
        objective="Create a candidate", evidence_ids=content["dossier_ids"] + content["literature_map_ids"])],
        batch_id="delegated", parent_task_id=manager["id"])[0]
    task["attempt_id"] = "attempt_lineage"
    child_context = workspace.discovery._context(session, task)
    workspace.store.put_immutable("discovery_attempt", {"id": task["attempt_id"], "context_snapshot": child_context})
    assert child_context["discovery"]["researcher_request"]["request"] == request
    artifact = DiscoveryArtifact(kind="candidate_batch", title="Fixture child", content=content)
    assert knowledge.validate(workspace.discovery, session, task, artifact)
    if operation != "expand":
        broken = deepcopy(content)
        broken["candidates"][0]["parent_hypothesis_ids"] = []
        with pytest.raises(ValueError, match="selected parent"):
            knowledge.validate(workspace.discovery, session, task, artifact.model_copy(update={"content": broken}))


@pytest.mark.parametrize("operation", ["expand", "hybrid"])
def test_multihop_delegation_narrows_evidence_without_losing_user_targets_or_feedback(setup, operation):
    workspace, campaign, session, parents, manager, content = exploration(setup, operation)
    unrelated = fixture_artifact(workspace, session, "review", {"large_unrelated_reading": "x" * 220_000})
    manager["brief"]["evidence_ids"].append(unrelated["id"])
    workspace.store.put("discovery_task", manager)
    message = workspace.store.get(manager["manager_command_id"], "manager_command")
    message["feedback_snapshot"] = [{"id": "saved_feedback", "author": "researcher", "text": "Keep the restart trigger explicit"}]
    workspace.store.put("manager_command", message)
    direct = content["dossier_ids"] + content["literature_map_ids"]
    delegated = workspace.discovery.add_tasks(session, [DiscoveryTaskBrief(key="focused_manager", role="campaign_manager", stage="manage",
        objective="Use only the selected scientific evidence", evidence_ids=[*direct, unrelated["id"]])],
        batch_id="focused_manager", parent_task_id=manager["id"])[0]
    child = workspace.discovery.add_tasks(session, [DiscoveryTaskBrief(key="narrow_generation", role="methodology_specialist", stage="generate",
        objective="Generate within a deliberately narrowed scope", evidence_ids=direct)],
        batch_id="narrow_generation", parent_task_id=delegated["id"])[0]
    expected_parents = [row["id"] for row in parents] if operation == "hybrid" else []
    assert child["brief"]["evidence_ids"] == direct + expected_parents
    assert child["proposal_request_id"] == manager["manager_command_id"]
    assert unrelated["id"] in delegated["brief"]["evidence_ids"]  # Explicit assignment still wins.
    context = workspace.discovery._context(session, child)
    assert unrelated["id"] not in {row["id"] for row in context["discovery"]["evidence"]}
    assert context["discovery"]["researcher_request"]["feedback"] == message["feedback_snapshot"]
    assert context["discovery"]["researcher_request"]["request"] == message["request"]
    if operation == "expand":
        assert not {row["id"] for row in parents} & {row["id"] for row in context["discovery"]["evidence"]}


def test_evolution_target_and_saved_feedback_survive_narrowed_delegation(setup):
    workspace, campaign, session, parents, manager, content = exploration(setup, "expand")
    target = parents[0]
    target["reviews"] = [{"id": "researcher_comment", "author": "researcher", "text": "Define the restart condition", "created_at": "2026-09-28T00:00:00+00:00"}]
    workspace.store.put("hypothesis", target)
    workspace.commands.execute(command(workspace, campaign, "research.start", {
        "mode": "evolve", "hypothesis_id": target["id"], "feedback_review_ids": ["researcher_comment"],
        "message": "Refine this idea using the saved feedback"}, "targeted_evolution"))
    workspace.discovery._admit_guidance(session)
    manager = next(row for row in workspace.discovery.tasks(session) if row.get("manager_command_id") == "turn_targeted_evolution")
    first = workspace.discovery.add_tasks(session, [DiscoveryTaskBrief(key="revision_manager", role="campaign_manager", stage="manage",
        objective="Narrow the revision scope", evidence_ids=content["dossier_ids"] + content["literature_map_ids"])],
        batch_id="revision_manager", parent_task_id=manager["id"])[0]
    child = workspace.discovery.add_tasks(session, [DiscoveryTaskBrief(key="targeted_revision", role="methodology_specialist", stage="generate",
        objective="Clarify the selected target")], batch_id="targeted_revision", parent_task_id=first["id"])[0]
    assert child["brief"]["evidence_ids"] == [target["id"]]
    context = proposals.request_context(workspace.store, child)
    assert context["request"]["hypothesis_id"] == target["id"]
    assert context["feedback"] == target["reviews"]


@pytest.mark.parametrize("verdict", ["test", "revise", "reject"])
def test_generation_automatically_gets_a_distinct_reviewer_and_gates_trials(setup, verdict):
    workspace, campaign, session, parents, manager, content = exploration(setup)
    class Workflow(Adapter):
        def result(self, role, context):
            if role == "campaign_manager":
                if context["discovery"].get("researcher_request"):
                    return DiscoveryResult(summary="Delegate the selected hybrid", proposed_tasks=[DiscoveryTaskBrief(
                        key="hybrid", role="hybrid_designer", stage="generate", objective="Combine the selected parents",
                        evidence_ids=content["dossier_ids"] + content["literature_map_ids"])])
                return DiscoveryResult(summary="Retain the review and its limits")
            if role == "hybrid_designer":
                return DiscoveryResult(summary="A linked fixture candidate", artifacts=[DiscoveryArtifact(
                    kind="candidate_batch", title="Hybrid", content=content)])
            candidate = next(row for row in context["discovery"]["evidence"] if row.get("family_id"))
            return DiscoveryResult(summary="Independent conceptual assessment", artifacts=[DiscoveryArtifact(
                kind="proposal_review", title="Hybrid review", content={"candidate_id": candidate["id"], "verdict": verdict,
                    "rationale": "Fixture judgment, not empirical evidence", "mechanism_checks": ["Retains both parent mechanisms"],
                    "required_changes": [] if verdict == "test" else ["Clarify the restart trigger"],
                    "suggested_tests": ["Compare three radii against both parents on paired seeds"]})])
    workspace.discovery.adapter_factory = Workflow
    workspace.discovery.control(campaign["id"], {"session_id": session["id"], "action": "resume", "expected_control_revision": 1})
    settle(workspace, campaign, turns=2)
    hypothesis = next(row for row in workspace.store.list("hypothesis") if row.get("candidate_id"))
    assert set(hypothesis["parent_ids"]) == {p["id"] for p in parents}
    assert not proposals.readiness(workspace.store, hypothesis)["eligible"]
    request = TrialInput(campaign_id=campaign["id"], task_id=session["problem_task_id"], hypothesis_id=hypothesis["id"],
                         algorithm="coordinate", max_steps=4, wall_seconds=5)
    with pytest.raises(ValueError, match="conceptual review"):
        workspace.create_trial(request)
    draft = workspace.drafts.save(campaign["id"], {"title": "Quick check", "procedure": request.model_dump()})
    assert any(row["code"] == "concept_review_required" for row in workspace.drafts.readiness(draft["id"])["blockers"])
    settle(workspace, campaign, turns=3)
    reviews = [r for r in workspace.store.list("discovery_artifact") if r["kind"] == "proposal_review"]
    assert len(reviews) == 1
    reviewer = workspace.store.get(reviews[0]["task_id"])
    assert reviewer["brief"]["role"] == "proposal_reviewer" and reviewer["status"] == "completed"
    proposals.project_review(workspace.discovery, reviews[0])  # replay cannot duplicate the visible judgment
    hypothesis = workspace.store.get(hypothesis["id"])
    assert len(hypothesis["reviews"]) == 1
    assert proposals.readiness(workspace.store, hypothesis)["eligible"] == (verdict == "test")
    assert workspace.drafts.readiness(draft["id"])["ready"] == (verdict == "test")
    if verdict == "test":
        assert workspace.create_trial(request)["status"] == "queued"
        hypothesis["algorithm"] = "custom"
        workspace.store.put("hypothesis", hypothesis)
        assert not workspace.implementations.readiness(hypothesis)["runnable"]
    else:
        with pytest.raises(ValueError, match="reviewer requested changes"):
            workspace.create_trial(request)


def test_failed_prerequisite_cannot_authorize_specialist_work(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    author = workspace.discovery.tasks(session)[0]
    author.update(status="failed", error="Fixture provider failure")
    workspace.store.put("discovery_task", author)
    child = workspace.discovery.add_tasks(session, [DiscoveryTaskBrief(key="child", role="designer", stage="generate",
        objective="Do not proceed", dependencies=[author["id"]])], batch_id="failed_parent")[0]
    settle(workspace, campaign)
    assert workspace.store.get(child["id"])["status"] == "blocked"
    assert not any(call["role"] == "designer" for call in Adapter.calls)


def test_concept_review_rejects_self_review_and_unresolved_required_changes(setup):
    workspace, campaign, session, assessment = prepared(setup)
    candidate = workspace.store.get(assessment["candidate_id"])
    author = workspace.store.get(candidate["task_id"])
    reviewer = workspace.discovery.add_tasks(session, [DiscoveryTaskBrief(key="review", stage="review",
        role=author["brief"]["role"], objective="Review the exact candidate", evidence_ids=[candidate["id"]])], batch_id="review_gate")[0]
    reviewer["attempt_id"] = "review_gate_attempt"
    workspace.store.put_immutable("discovery_attempt", {"id": reviewer["attempt_id"], "context_snapshot": {"candidate": candidate}})
    review = proposals.ProposalReview(candidate_id=candidate["id"], verdict="test", rationale="Fixture rationale",
                                     mechanism_checks=["Check the algorithm"], suggested_tests=["Paired seeds"])
    with pytest.raises(ValueError, match="separate reviewer persona"):
        proposals.validate_review(workspace.discovery, session, reviewer, review, {candidate["id"]})
    reviewer["brief"]["role"] = "independent_reviewer"
    proposals.validate_review(workspace.discovery, session, reviewer, review, {candidate["id"]})
    with pytest.raises(ValueError, match="Resolve required changes"):
        proposals.validate_review(workspace.discovery, session, reviewer,
                                  review.model_copy(update={"required_changes": ["Define missing acceptance rule"]}), {candidate["id"]})


def test_saved_artifact_prerequisites_become_explicit_evidence(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    artifact = fixture_artifact(workspace, session, "problem_dossier", {"fixture": "approved evidence"})
    briefs = [DiscoveryTaskBrief(key="reader", role="reader", objective="Read approved dossier", dependencies=[artifact["id"]])]
    task = workspace.discovery.add_tasks(session, briefs, batch_id="artifact_prerequisite")[0]
    assert task["dependencies"] == []
    assert task["brief"]["evidence_ids"] == [artifact["id"]]
    assert workspace.discovery.add_tasks(session, briefs, batch_id="artifact_prerequisite")[0] == task
    with pytest.raises(ValueError, match="dependencies must belong"):
        workspace.discovery.add_tasks(session, [DiscoveryTaskBrief(key="invalid", role="reader", objective="Read",
            dependencies=["invented_record"])], batch_id="unknown_prerequisite")


def test_older_candidate_projection_is_not_rewritten_on_replay(setup):
    from optimization_framework.contracts.base import content_hash
    workspace, campaign, session, parents, manager, content = exploration(setup)
    manager = {**manager, "run_id": "legacy_projection_run"}
    artifact = fixture_artifact(workspace, session, "candidate_batch", content)
    identity = "candidate_" + content_hash([artifact["id"], content["candidates"][0]["key"]])[:28]
    legacy = workspace.store.put_immutable("discovery_candidate", {"id": identity, "campaign_id": campaign["id"],
        "session_id": session["id"], "task_id": manager["id"], "artifact_id": artifact["id"],
        **content["candidates"][0]})
    knowledge.project_candidates(workspace.discovery, session, manager, artifact)
    knowledge.project_candidates(workspace.discovery, session, manager, artifact)
    assert workspace.store.get(identity) == legacy
    assert not workspace.store.get("hypothesis_" + identity)["requires_concept_review"]


@pytest.mark.parametrize("recover", [False, True])
def test_invalid_response_envelope_gets_costed_bounded_correction(setup, monkeypatch, recover):
    from pydantic import ValidationError
    from test_framework_discovery import fixture_dossier
    from optimization_framework.storage.sqlite import now
    workspace, campaign = setup
    session, _ = start(workspace, campaign, max_concurrent_tasks=1)
    original = workspace.discovery.tasks(session)[0]
    class InvalidEnvelope(Adapter):
        def call_with_prompt(self, role, system, context, result_type):
            if context["discovery"]["step"]:
                assert "evidence_ids" in context["discovery"]["validation_feedback"][-1]["error"]
                assert context["discovery"]["rejected_responses"][-1]["output"]
                return super().call_with_prompt(role, system, context, result_type)
            self.usage.update(calls=1, subscription_calls=1)
            output = json.dumps({"summary": "Scientific output with misplaced metadata", "evidence_ids": ["not_an_envelope_field"]})
            self.emit({"type": "provider_response", "reservation_id": "invalid_envelope", "role": role,
                       "output": output, "usage": deepcopy(self.usage)})
            try:
                DiscoveryResult.model_validate_json(output)
            except ValidationError as exc:
                raise ValueError("Provider result was invalid") from exc
        def result(self, role, context):
            return DiscoveryResult(summary="Corrected scientific envelope", artifacts=[fixture_dossier()])
    workspace.discovery.adapter_factory = InvalidEnvelope
    if recover:
        # Simulate interruption after saving the provider receipt, before its
        # validation failure was projected. Recovery does not resend that call.
        original.update(status="running", run_id="run_invalid_envelope", attempt_id="attempt_invalid")
        workspace.store.put("discovery_task", original)
        context = workspace.discovery._context(session, original)
        workspace.store.put("research_run", {"id": original["run_id"], "campaign_id": campaign["id"],
            "discovery_session_id": session["id"], "charter_version": campaign["version"], "guidance_revision": 0,
            "status": "running", "usage": {"calls": 1, "subscription_calls": 1}, "context_snapshot": context,
            "request": {"provider_snapshot": {"provider": "fixture", "model": "fixture", "billing_mode": "subscription"}}})
        workspace.store.put_immutable("discovery_response", {"id": "response_invalid", "campaign_id": campaign["id"],
            "task_id": original["id"], "step": 0, "created_at": now(),
            "event": {"type": "provider_response", "output": '{"summary":"Invalid","evidence_ids":[]}', "usage": {"calls": 1, "subscription_calls": 1}}})
        workspace.discovery.recover()
    else:
        settle(workspace, campaign, turns=1)
    task = workspace.store.get(original["id"])
    assert task["status"] == "queued" and task["step"] == 1 and task["corrections"] == 1
    assert workspace.store.get(task["run_id"])["usage"]["calls"] == 1
    assert len(workspace.store.list("discovery_rejected_response")) == 1
    settle(workspace, campaign, turns=1)
    task = workspace.store.get(original["id"])
    assert task["status"] == "completed", task.get("error")
    assert workspace.store.get(task["run_id"])["usage"]["calls"] == 2
