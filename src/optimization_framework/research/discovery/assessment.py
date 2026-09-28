"""Predeclared empirical assessments built from ordinary frozen experiments."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from optimization_framework.contracts.base import Contract, content_hash
from optimization_framework.contracts.drafts import DraftLaunchInput, DraftSaveInput
from optimization_framework.storage.sqlite import now
from .tuning import TuningSpace, sample


class AssessmentPlan(Contract):
    candidate_id: str
    question: str = Field(min_length=1, max_length=10000)
    mechanism_predictions: list[str] = Field(min_length=1, max_length=30)
    comparison_axis: Literal["evaluation_requests", "solver_executions", "worker_seconds"] = "evaluation_requests"
    configurations: TuningSpace
    seeds: list[int] = Field(default_factory=lambda: [0, 1], min_length=1, max_length=10)
    evaluations_per_trial: int = Field(default=128, ge=1, le=10000000)
    schedule_steps: int | None = Field(default=None, ge=1, le=10000000)
    wall_seconds_per_trial: float = Field(default=30, gt=0, le=86400)
    startup_requirements: str = Field(min_length=1, max_length=8000)
    minimum_informative_evaluations: int = Field(ge=1, le=10000000)
    minimum_configurations: int = Field(default=3, ge=1, le=30)
    minimum_seeds_per_configuration: int = Field(default=2, ge=1, le=10)
    minimum_training_updates: int = Field(default=0, ge=0, le=10000000)
    required_diagnostics: list[str] = Field(default_factory=list, max_length=20)
    tuning_waiver: str = Field(default="", max_length=8000)
    extension_rule: str = Field(min_length=1, max_length=8000)
    early_stopping_rule: str = Field(min_length=1, max_length=8000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    predecessor_assessment_id: str | None = None

    @model_validator(mode="after")
    def valid_assessment(self):
        if any(type(seed) is not int or not 0 <= seed < 2**32 for seed in self.seeds) or len(set(self.seeds)) != len(self.seeds):
            raise ValueError("Assessment seeds must be distinct unsigned integers")
        if self.minimum_configurations == 1 and not self.tuning_waiver.strip():
            raise ValueError("A one-configuration assessment needs an explicit tuning waiver")
        if self.schedule_steps is not None and self.schedule_steps < self.evaluations_per_trial:
            raise ValueError("The declared optimizer schedule cannot be shorter than its experiment horizon")
        return self


class AssessmentSave(Contract):
    session_id: str
    plan: AssessmentPlan
    source_artifact_id: str | None = None


class AssessmentLaunch(Contract):
    assessment_id: str
    expected_readiness_hash: str = Field(min_length=64, max_length=64)


class AssessmentDecision(Contract):
    assessment_id: str
    outcome: Literal["implementation_invalid", "assumptions_incompatible", "configuration_unpromising", "under_evaluated",
                     "unaffordable_within_allocation", "adequately_assessed_deprioritized", "promising"]
    rationale: str = Field(min_length=1, max_length=12000)
    evidence_ids: list[str] = Field(min_length=1, max_length=100)
    limitations: list[str] = Field(min_length=1, max_length=30)
    review_artifact_id: str | None = None


class Assessments:
    def __init__(self, controller):
        self.controller, self.workspace, self.store = controller, controller.workspace, controller.store

    def _session(self, campaign_id, identity, *, dispatch=False):
        session = self.store.get(identity, "discovery_session")
        if session["campaign_id"] != campaign_id:
            raise ValueError("Assessment belongs to another campaign")
        if dispatch and session["status"] not in {"running", "waiting_for_direction", "waiting_for_provider"}:
            raise ValueError("Discovery is paused or stopped; new assessment experiments cannot launch")
        return session

    def save(self, campaign_id, values, identity, *, authority):
        values = AssessmentSave.model_validate(values)
        session = self._session(campaign_id, values.session_id)
        if values.source_artifact_id:
            source = self.controller._evidence(session, values.source_artifact_id)
            if source.get("kind") != "assessment_plan" or source.get("stale") or AssessmentPlan.model_validate(source["content"]) != values.plan:
                raise ValueError("Prepare the exact current assessment-plan artifact")
            existing = next((row for row in self.store.list("discovery_assessment", campaign_id)
                             if row.get("source_artifact_id") == source["id"]), None)
            if existing:
                return existing
        candidate = self.controller._evidence(session, values.plan.candidate_id)
        if self.store.get_entry(candidate["id"])["kind"] != "discovery_candidate":
            raise ValueError("Assess an exact discovery candidate revision")
        for evidence_id in values.plan.evidence_ids:
            self.controller._evidence(session, evidence_id)
        if values.plan.predecessor_assessment_id:
            previous = self.store.get(values.plan.predecessor_assessment_id, "discovery_assessment")
            if previous["session_id"] != session["id"] or previous["family_id"] != candidate["family_id"]:
                raise ValueError("Assessment amendments must preserve the problem session and methodology family")
            for field in ("minimum_configurations", "minimum_seeds_per_configuration", "minimum_informative_evaluations", "minimum_training_updates"):
                if getattr(values.plan, field) < previous["plan"][field]:
                    raise ValueError("An assessment amendment cannot retroactively relax its adequacy thresholds")
        configurations = sample(values.plan.configurations)
        hypothesis = self.store.get("hypothesis_" + candidate["id"], "hypothesis")
        campaign = self.store.get(campaign_id, "campaign")
        drafts = []
        for number, configuration in enumerate(configurations):
            for seed in values.plan.seeds:
                procedure = {"task_id": session["problem_task_id"], "hypothesis_id": hypothesis["id"],
                    "algorithm": hypothesis["algorithm"], "algorithm_config": {**candidate["algorithm_config"], **configuration["algorithm_config"]},
                    "training": configuration["training"], "seed": seed, "max_steps": values.plan.evaluations_per_trial,
                    "schedule_steps": values.plan.schedule_steps, "wall_seconds": values.plan.wall_seconds_per_trial,
                    "question": values.plan.question}
                draft = self.workspace.drafts.save(campaign_id, DraftSaveInput(title=f"{candidate['title']} · configuration {number + 1} · seed {seed}",
                    procedure=procedure, follow_proposal_implementation=True), authority=authority)
                drafts.append({"configuration_index": number, "configuration_digest": content_hash(configuration),
                    "seed": seed, "draft_id": draft["id"], "draft_revision_id": draft["revision_id"]})
        record = {"id": identity, "campaign_id": campaign_id, "session_id": session["id"], "candidate_id": candidate["id"],
            "family_id": candidate["family_id"], "created_at": now(), "authority": authority,
            "source_artifact_id": values.source_artifact_id,
            "charter_version": campaign["version"], "guidance_revision": self.workspace.memory.state(campaign_id)["guidance_revision"],
            "problem": session["problem"], "plan": values.plan.model_dump(mode="json"), "configurations": configurations, "cells": drafts}
        return self.store.put_immutable("discovery_assessment", record, "discovery.assessment_predeclared")

    def readiness(self, identity):
        assessment = self.store.get(identity, "discovery_assessment")
        cells = [{**cell, "readiness": self.workspace.drafts.readiness(cell["draft_id"])} for cell in assessment["cells"]]
        pending_seconds = sum(self.store.get(cell["draft_id"], "experiment_draft")["procedure"]["wall_seconds"]
                              for cell in cells if not cell["readiness"]["trial_id"])
        blockers = [blocker for cell in cells for blocker in cell["readiness"]["blockers"]]
        session = self._session(assessment["campaign_id"], assessment["session_id"])
        used = sum(trial["wall_seconds"] for trial in self.store.list("trial", assessment["campaign_id"])
                   if trial.get("discovery_session_id") == session["id"])
        cap = session["policy"].get("experiment_compute_seconds", 0)
        if used + pending_seconds > cap:
            blockers.append({"code": "session_allocation", "message": "The batch exceeds the session's numerical allocation", "actions": ["amend_discovery_allocation"]})
        campaign = self.store.get(assessment["campaign_id"], "campaign")
        try:
            if pending_seconds:
                self.workspace._check_allocation(campaign, pending_seconds)
        except ValueError:
            blockers.append({"code": "campaign_allocation", "message": "The whole batch does not fit the remaining campaign allocation", "actions": ["revise_allocation"]})
        if session["status"] in {"paused", "stopped", "completed", "exhausted"}:
            blockers.append({"code": "session_stopped", "message": "The discovery session is paused or terminal", "actions": ["resume_discovery"]})
        basis = {"assessment_hash": assessment["content_hash"], "cells": cells, "blockers": blockers,
                 "session_control_revision": session["control_revision"]}
        return {"assessment_id": identity, "ready": not blockers, "cells": cells, "blockers": blockers,
                "pending_seconds": pending_seconds, "readiness_hash": content_hash(basis)}

    def launch(self, campaign_id, values, *, authority):
        values = AssessmentLaunch.model_validate(values)
        assessment = self.store.get(values.assessment_id, "discovery_assessment")
        self._session(campaign_id, assessment["session_id"], dispatch=True)
        current = self.readiness(assessment["id"])
        if current["readiness_hash"] != values.expected_readiness_hash:
            raise ValueError("Assessment readiness changed; review the current versions and allocation")
        if not current["ready"]:
            raise ValueError("Assessment cannot launch: " + "; ".join(row["message"] for row in current["blockers"]))
        trial_ids = []
        for cell in current["cells"]:
            if cell["readiness"]["trial_id"]:
                trial_ids.append(cell["readiness"]["trial_id"])
                continue
            draft = self.store.get(cell["draft_id"], "experiment_draft")
            if draft["revision_id"] != cell["draft_revision_id"]:
                raise ValueError("A predeclared assessment draft was edited; create an assessment amendment")
            # Earlier cells consume allocation, so recalculate each readiness
            # inside the same transaction after checking the whole batch cap.
            ready = self.workspace.drafts.readiness(draft["id"])
            launched = self.workspace.drafts.launch(campaign_id, DraftLaunchInput(draft_id=draft["id"],
                expected_draft_revision=draft["revision"], expected_readiness_hash=ready["readiness_hash"]), authority=authority)
            trial = self.store.get(launched["trial_id"], "trial")
            trial.update(discovery_session_id=assessment["session_id"], discovery_assessment_id=assessment["id"],
                         discovery_candidate_id=assessment["candidate_id"], discovery_configuration_digest=cell["configuration_digest"])
            self.store.put("trial", trial, "discovery.assessment_trial_queued")
            trial_ids.append(trial["id"])
        return {"assessment_id": assessment["id"], "trial_ids": trial_ids}

    def evidence(self, identity):
        assessment = self.store.get(identity, "discovery_assessment")
        plan, measured, missing = assessment["plan"], {}, []
        trials = []
        for cell in assessment["cells"]:
            try:
                launch = self.store.get("launch_" + cell["draft_revision_id"], "draft_launch")
                trial = self.store.get(launch["trial_id"], "trial")
            except KeyError:
                missing.append({"draft_id": cell["draft_id"], "reason": "not_executed"})
                continue
            trials.append(trial)
            result = trial.get("result") or {}
            count = result.get("evaluations", result.get("evaluation_requests", 0))
            if trial["status"] not in {"completed", "budget_exhausted"} or not result.get("scientific_complete") or count < plan["minimum_informative_evaluations"]:
                missing.append({"trial_id": trial["id"], "reason": "minimum_informative_horizon_or_completion_not_observed", "observed_evaluations": count})
                continue
            diagnostics = result.get("diagnostics", {})
            if diagnostics.get("updates", 0) < plan.get("minimum_training_updates", 0):
                missing.append({"trial_id": trial["id"], "reason": "minimum_training_updates_not_observed"})
                continue
            if result.get("best_objective") is None:
                missing.append({"trial_id": trial["id"], "reason": "no_feasible_objective_observed"})
                continue
            if any(key not in diagnostics for key in plan["required_diagnostics"]):
                missing.append({"trial_id": trial["id"], "reason": "required_diagnostics_missing"})
                continue
            measured.setdefault(cell["configuration_digest"], set()).add(cell["seed"])
        adequate_configurations = sum(len(seeds) >= plan["minimum_seeds_per_configuration"] for seeds in measured.values())
        adequate = adequate_configurations >= plan["minimum_configurations"]
        return {"assessment_id": identity, "status": "adequate_for_review" if adequate else "under_evaluated",
            "adequate": adequate, "informative_configurations": adequate_configurations,
            "required_configurations": plan["minimum_configurations"], "missing": missing,
            "trial_ids": [trial["id"] for trial in trials], "measurements": [{"trial_id": trial["id"], "status": trial["status"],
                "configuration_digest": trial.get("discovery_configuration_digest"), "seed": trial["seed"],
                "result": trial.get("result"), "costs": trial.get("execution_seconds")} for trial in trials],
            "limitations": ["Development evidence only; final confirmation remains a separate frozen procedure.",
                            "Adequacy against the declared horizon is a gate for review, not proof of methodology effectiveness."]}

    def decide(self, campaign_id, values, identity, *, authority):
        values = AssessmentDecision.model_validate(values)
        assessment = self.store.get(values.assessment_id, "discovery_assessment")
        session = self._session(campaign_id, assessment["session_id"])
        for key in values.evidence_ids:
            self.controller._evidence(session, key)
        evidence = self.evidence(assessment["id"])
        if values.outcome == "adequately_assessed_deprioritized":
            if not evidence["adequate"] or not values.review_artifact_id:
                raise ValueError("Performance-based family deprioritization requires adequate measurements and an independent review")
            review = self.controller._evidence(session, values.review_artifact_id)
            reviewer = self.store.get(review["task_id"], "discovery_task")
            candidate = self.store.get(assessment["candidate_id"], "discovery_candidate")
            if (review["kind"] != "review" or review["stale"] or reviewer["brief"]["stage"] != "review" or
                    reviewer["id"] == candidate["task_id"] or assessment["id"] not in review["evidence_ids"]):
                raise ValueError("The review must independently assess this exact predeclared assessment")
        if values.outcome == "implementation_invalid":
            grants = [self.store.get_entry(key) for key in values.evidence_ids]
            if not any(entry["kind"] == "implementation_grant" and
                       entry["data"].get("hypothesis_id") == "hypothesis_" + assessment["candidate_id"] and
                       any((attempt.get("report") or {}).get("passed") is False for attempt in entry["data"].get("attempts", []))
                       for entry in grants):
                raise ValueError("Implementation invalidity needs separate correctness-validation evidence")
        return self.store.put_immutable("discovery_assessment_decision", {"id": identity, "campaign_id": campaign_id,
            "session_id": session["id"], "family_id": assessment["family_id"], "created_at": now(), "authority": authority,
            "adequacy_snapshot": evidence, **values.model_dump(mode="json")}, "discovery.assessment_reviewed")
