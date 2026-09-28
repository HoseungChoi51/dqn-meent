"""Explicit exploration intent and independent conceptual review of exact proposals.

Text review grants eligibility for small experiments, never evidence of performance.
Implementation correctness and numerical resource checks remain separate gates.
"""
from typing import Literal

from pydantic import Field

from optimization_framework.contracts.base import Contract


class ProposalReview(Contract):
    candidate_id: str
    verdict: Literal["test", "revise", "reject"]
    rationale: str = Field(min_length=1, max_length=8000)
    mechanism_checks: list[str] = Field(min_length=1, max_length=20)
    conflicts_and_risks: list[str] = Field(default_factory=list, max_length=20)
    required_changes: list[str] = Field(default_factory=list, max_length=20,
        description="Changes to the proposed mechanism/specification. Missing code or funding belongs in implementation_followup, not here.")
    implementation_followup: list[str] = Field(default_factory=list, max_length=20,
        description="Separate implementation/readiness concerns; these do not determine conceptual plausibility.")
    suggested_tests: list[str] = Field(min_length=1, max_length=20)


def request_context(store, task):
    identity = task.get("proposal_request_id") or task.get("manager_command_id")
    if not identity:
        return None
    command = store.get(identity, "manager_command")
    request = command["request"]
    return {"command_id": identity, "request": request,
            "feedback": command.get("feedback_snapshot") or []}


def validate_review(controller, session, task, parsed, supplied):
    candidate = controller._evidence(session, parsed.candidate_id, task=task)
    if (controller.store.get_entry(candidate["id"])["kind"] != "discovery_candidate"
            or candidate["id"] not in supplied):
        raise ValueError("A conceptual review requires an exact supplied candidate revision")
    author = controller.store.get(candidate["task_id"], "discovery_task")
    if (task["brief"]["stage"] != "review" or task["id"] == author["id"]
            or task["brief"]["role"] in {author["brief"]["role"], "campaign_manager"}):
        raise ValueError("Conceptual review requires a separate reviewer persona and task")
    if parsed.verdict == "test" and parsed.required_changes:
        raise ValueError("Resolve required changes in a new candidate revision before recommending tests")


def readiness(store, hypothesis):
    if not hypothesis or not hypothesis.get("requires_concept_review"):
        return {"state": "not_required", "eligible": True, "review_ids": []}
    reviews = [row for row in store.list("discovery_artifact", hypothesis["campaign_id"])
               if row.get("kind") == "proposal_review" and not row.get("stale")
               and row["content"].get("candidate_id") == hypothesis.get("candidate_id")]
    if not reviews:
        return {"state": "pending", "eligible": False, "review_ids": [],
                "reason": "An independent conceptual review is required before testing this proposal."}
    objections = [row for row in reviews if row["content"]["verdict"] != "test"]
    return {"state": "revision_required" if objections else "testable", "eligible": not objections,
            "review_ids": [row["id"] for row in reviews],
            "reason": "An independent reviewer requested changes or rejected this revision. Create a linked revision and review it again."
                if objections else "Independently judged plausible enough for quick tests; effectiveness remains unmeasured."}


def project_review(controller, artifact):
    if artifact["kind"] != "proposal_review" or artifact.get("stale"):
        return
    review = ProposalReview.model_validate(artifact["content"])
    hypothesis = controller.store.get("hypothesis_" + review.candidate_id, "hypothesis")
    if any(row.get("id") == artifact["id"] for row in hypothesis.get("reviews", [])):
        return
    task = controller.store.get(artifact["task_id"], "discovery_task")
    text = f"Conceptual verdict: {review.verdict}. {review.rationale}"
    for label, values in (("Mechanism checks", review.mechanism_checks), ("Risks", review.conflicts_and_risks),
                          ("Required changes", review.required_changes), ("Implementation follow-up", review.implementation_followup),
                          ("Suggested tests", review.suggested_tests)):
        if values:
            text += "\n\n" + label + ":\n" + "\n".join("- " + value for value in values)
    hypothesis.setdefault("reviews", []).append({"id": artifact["id"], "author": task["brief"]["role"],
        "kind": "conceptual", "verdict": review.verdict, "text": text, "created_at": artifact["created_at"]})
    controller.store.put("hypothesis", hypothesis, "hypothesis.concept_reviewed")


def schedule_reviews(controller, session, tasks):
    """The manager's standing protocol sends new conjectures to a different persona."""
    from .models import DiscoveryTaskBrief
    for author in tasks:
        if author["status"] != "completed" or author["brief"]["stage"] != "generate":
            continue
        candidates = [row for row in controller.store.list("discovery_candidate", session["campaign_id"])
                      if row["task_id"] == author["id"] and row.get("requires_concept_review")]
        if not candidates:
            continue
        key = "concept_review_" + author["id"]
        if any(row["batch_id"] == key for row in tasks):
            continue
        controller.add_tasks(session, [DiscoveryTaskBrief(key="conceptual_review", role="proposal_reviewer", stage="review",
            objective="Independently assess EVERY candidate in the assigned batch. Produce one proposal_review artifact per candidate, "
                "using its exact candidate ID. Check whether the mechanism is coherent for this problem, whether parent mechanisms "
                "are compatible, what is new, startup cost, assumptions, and whether the cheapest test can discriminate it from "
                "parents/baselines. Verdict test means plausible enough for bounded tests, not effective. Use revise for required "
                "mechanism/specification changes and reject for incoherence. Missing code or allocation alone is NOT a conceptual "
                "reason to revise/reject: record it in implementation_followup; execution has separate enforced gates. Use the "
                "current implementation_readiness map for parent executability, not an older candidate's provisional implementation_needs. "
                "Preserve objections. Do not generate candidates or launch experiments.",
            dependencies=[author["id"]], evidence_ids=[row["id"] for row in candidates])], batch_id=key)
