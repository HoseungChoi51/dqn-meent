"""Persist incomplete designs and bind their executables only at launch."""
from optimization_framework.contracts.base import content_hash
from optimization_framework.contracts.drafts import DraftSaveInput, DraftLaunchInput
from optimization_framework.contracts.requests import TrialInput
from optimization_framework.contracts.experiments import task_in_study
from optimization_framework.execution.preparation import prepare
from optimization_framework.storage.sqlite import identifier, now


class DraftService:
    def __init__(self, workspace):
        self.workspace = workspace
        self.store = workspace.store

    def save(self, campaign_id, request, *, authority="researcher"):
        request = request if isinstance(request, DraftSaveInput) else DraftSaveInput(**request)
        with self.workspace.lock, self.store.transaction():
            campaign = self.store.get(campaign_id, "campaign")
            previous = self.store.get(request.draft_id, "experiment_draft") if request.draft_id else None
            if previous and (previous["campaign_id"] != campaign_id or previous["revision"] != request.expected_draft_revision):
                raise ValueError("Draft revision or campaign changed; reload before saving")
            if previous and previous.get("reproduction") and (request.reproduction is None or
                    request.reproduction.reference.model_dump(mode="json") != previous["reproduction"]["reference"]):
                raise ValueError("A reproduction draft must retain its selected historical snapshot")
            procedure = dict(request.procedure)
            if procedure.get("campaign_id", campaign_id) != campaign_id:
                raise ValueError("Draft procedure belongs to another campaign")
            procedure["campaign_id"] = campaign_id
            intent = TrialInput(**procedure)
            task = self.store.get(intent.task_id, "task")
            if task["campaign_id"] != campaign_id:
                raise ValueError("Draft problem belongs to another campaign")
            study_id = request.study_id or (previous or {}).get("study_id") or campaign["active_study_id"]
            if self.store.get(study_id, "study")["campaign_id"] != campaign_id:
                raise ValueError("Draft study belongs to another campaign")
            if intent.hypothesis_id:
                if self.store.get(intent.hypothesis_id, "hypothesis")["campaign_id"] != campaign_id:
                    raise ValueError("Draft proposal belongs to another campaign")
            if request.follow_proposal_implementation and not intent.hypothesis_id:
                raise ValueError("Following an implementation requires a declared proposal")
            if request.reproduction:
                self.workspace.reproductions.check_procedure(campaign_id, request.reproduction, intent)
                if request.follow_proposal_implementation:
                    raise ValueError("A reproduction draft pins its historical executable")
            revision = previous["revision"] + 1 if previous else 1
            identity = previous["id"] if previous else identifier("draft")
            record = {"schema_version": 1, "id": identity, "campaign_id": campaign_id, "study_id": study_id,
                "revision": revision, "title": request.title, "procedure": intent.model_dump(mode="json"),
                "follow_proposal_implementation": request.follow_proposal_implementation,
                "required_capabilities": sorted(set(request.required_capabilities)), "authority": authority,
                "reproduction": request.reproduction.model_dump(mode="json") if request.reproduction else None,
                "created_at": previous["created_at"] if previous else now(), "updated_at": now()}
            saved = {**record, "id": f"{identity}_revision_{revision}", "draft_id": identity}
            self.store.put_immutable("draft_revision", saved, "draft.revision_saved")
            record["revision_id"] = saved["id"]
            return self.store.put("experiment_draft", record, "draft.saved")

    def readiness(self, draft_id):
        draft = self.store.get(draft_id, "experiment_draft")
        campaign = self.store.get(draft["campaign_id"], "campaign")
        study = self.store.get(draft["study_id"], "study")
        request = dict(draft["procedure"])
        task = self.workspace.evaluators.task_view(self.store.get(request["task_id"], "task"))
        blockers, waiting = [], []

        def block(code, message, *actions):
            blockers.append({"code": code, "message": message, "actions": list(actions)})

        if study["id"] != campaign.get("active_study_id"):
            block("study_changed", "This draft belongs to an earlier study. Save a revision under the intended active study.", "revise_draft")
        if study["scope"] == "confirmation" or request.get("confirmation_protocol_id"):
            block("confirmation_roster", "Confirmation work must be allocated from its frozen roster.", "open_confirmation")
        if any(study["id"] in design["study_ids"].values() for design in self.store.list("confirmation_design", campaign["id"])):
            block("template_roster", "Use the declared template cells, or create a linked study for an additional experiment.", "open_study", "define_study")
        if task.get("archived") or not task_in_study(task, study):
            block("problem_scope", "The selected problem is outside the current study's available instances.", "revise_draft")
        if task.get("split") == "test":
            block("protected_problem", "Use a frozen confirmation protocol for protected problem instances.", "define_confirmation")
        evaluator = self.workspace.evaluators.readiness(task, study_id=study["id"])
        if not evaluator["runnable"]:
            block("evaluator_" + evaluator["state"], evaluator["reason"],
                *( ["review_validation", "record_waiver"] if evaluator.get("waiver_allowed") else ["commission_evaluator", "select_evaluator"]))
        hypothesis = self.store.get(request["hypothesis_id"], "hypothesis") if request.get("hypothesis_id") else None
        if draft["follow_proposal_implementation"]:
            if hypothesis.get("implementation_version_id"):
                request.update(algorithm="package", implementation_version_id=hypothesis["implementation_version_id"])
            else:
                request.update(algorithm=hypothesis.get("algorithm", ""), implementation_version_id=None)
        version_id = request.get("implementation_version_id") or (hypothesis or {}).get("implementation_version_id")
        conflict = bool(version_id and request["algorithm"] not in {"", "package"}) or bool(
            request.get("implementation_version_id") and (hypothesis or {}).get("implementation_version_id") not in {None, request["implementation_version_id"]})
        if conflict:
            block("implementation_conflict", "The selected strategy conflicts with the proposal's attached implementation. Select that implementation or revise the proposal.",
                  "select_implementation", "revise_proposal")
        candidate = {**(hypothesis or {}), "campaign_id": campaign["id"], "algorithm": request["algorithm"],
            "algorithm_config": request["algorithm_config"], "implementation_version_id": version_id}
        implementation = ({"state": "captured", "runnable": True,
            "reason": "Historical built-in method will be checked by its isolated captured compiler."}
            if draft.get("reproduction") and not version_id else self.workspace.implementations.readiness(candidate, task))
        if not implementation["runnable"]:
            block("implementation_" + implementation["state"], implementation["reason"], "commission_implementation", "select_implementation")
        elif version_id and not conflict:
            version = self.store.get(version_id, "implementation_cache")
            request.update(algorithm="package", implementation_version_id=version_id,
                algorithm_config={**version["spec"]["parameters"], **request["algorithm_config"]})
        missing = set(draft["required_capabilities"]) - set(task["problem"]["capabilities"])
        if missing:
            block("missing_evaluator_capability", "Evaluator capabilities are missing: " + ", ".join(sorted(missing)), "commission_evaluator", "revise_draft")
        for dependency_id in request["dependencies"]:
            try:
                dependency = self.store.get(dependency_id, "trial")
                if dependency["campaign_id"] != campaign["id"]:
                    raise ValueError("Experiment dependencies must belong to this campaign")
                if dependency["status"] != "completed" or not (dependency.get("result") or {}).get("scientific_complete"):
                    waiting.append(dependency_id)
            except (KeyError, ValueError) as exc:
                block("dependency_unavailable", str(exc), "revise_draft")
        assets = []
        try:
            assets = self.workspace.assets.inputs(campaign["id"], study["id"], request["initial_assets"], request["reuse_decision_ids"])
        except (ValueError, KeyError, OSError) as exc:
            block("input_unavailable", str(exc), "declare_input_reuse", "revise_draft")
        reproduction = None
        if implementation["runnable"]:
            try:
                if draft.get("reproduction"):
                    resolved, reproduction = self.workspace.reproductions.prepare(draft, task, request, assets)
                else:
                    specification = self.store.get(version_id, "implementation_cache")["spec"] if version_id and not conflict else None
                    resolved = prepare(request, task["problem"], assets, specification)
                request.update(algorithm_config=resolved["algorithm_config"], training=resolved["training"],
                               completion=resolved["completion"], diagnostics=resolved["diagnostics"])
            except (ValueError, KeyError, OSError) as exc:
                if draft.get("reproduction"):
                    block("reproduction_unavailable", str(exc), "restore_source", "resolve_runtime", "declare_input_reuse", "select_problem")
                else:
                    block("procedure_invalid", str(exc), "revise_draft")
        try:
            intent = TrialInput(**request)
            self.workspace._check_allocation(campaign, intent.wall_seconds)
            seconds = sum(len(schedule.at_counts) * schedule.allocation() for schedule in intent.diagnostics)
            if seconds:
                self.workspace._check_allocation(campaign, intent.wall_seconds + seconds, validation=True)
        except ValueError as exc:
            block("allocation_unavailable", str(exc), "revise_allocation", "discuss_with_manager")
        try:
            binding = self.store.get("launch_" + draft["revision_id"], "draft_launch")
        except KeyError:
            binding = None
        basis = {"draft_revision_id": draft["revision_id"], "study_id": study["id"], "problem": task["problem"],
                 "resolved_procedure": request, "implementation": implementation, "evaluator": evaluator, "blockers": blockers,
                 "reproduction": reproduction,
                 "input_digests": {asset["id"]: asset["content_hash"] for asset in assets}}
        return {"draft_id": draft_id, "revision": draft["revision"], "ready": not blockers,
                "blockers": blockers, "waiting_for": waiting, "readiness_hash": content_hash(basis),
                "reproduction": reproduction,
                "reproduction_inputs": self.workspace.reproductions.inputs(draft) if draft.get("reproduction") else [],
                "resolved_procedure": request, "implementation": implementation, "evaluator": evaluator, "trial_id": binding["trial_id"] if binding else None}

    def launch(self, campaign_id, request, *, authority="researcher"):
        request = request if isinstance(request, DraftLaunchInput) else DraftLaunchInput(**request)
        with self.workspace.lock, self.store.transaction():
            draft = self.store.get(request.draft_id, "experiment_draft")
            if draft["campaign_id"] != campaign_id or draft["revision"] != request.expected_draft_revision:
                raise ValueError("Draft revision or campaign changed; review the current draft before launching")
            identity = "launch_" + draft["revision_id"]
            try:
                return self.store.get(identity, "draft_launch")
            except KeyError:
                pass
            readiness = self.readiness(draft["id"])
            if not readiness["ready"]:
                raise ValueError("Draft is not ready: " + "; ".join(item["message"] for item in readiness["blockers"]))
            if readiness["readiness_hash"] != request.expected_readiness_hash:
                raise ValueError("Draft readiness changed; review the current executable and inputs before launching")
            intent = TrialInput(**readiness["resolved_procedure"])
            if authority == "manager":
                campaign = self.store.get(campaign_id, "campaign")
                jobs = [job for schedule in intent.diagnostics for job in
                    [*schedule.recipes, *schedule.rollouts, *(recipe for rollout in schedule.rollouts for recipe in rollout.recipes)]]
                if campaign["autonomy"] != "delegated" or any(seconds > campaign["delegated_trial_seconds"] for seconds in
                        [intent.wall_seconds, *(job.wall_seconds for job in jobs)]):
                    raise ValueError("Draft exceeds the manager's delegated per-experiment allowance")
            reproduction = readiness.get("reproduction")
            trial = self.workspace.create_trial(intent,
                captured_source=self.workspace.reproductions.source(reproduction) if reproduction else None,
                reproduction=reproduction)
            binding = {"id": identity, "campaign_id": campaign_id, "study_id": draft["study_id"], "draft_id": draft["id"],
                "draft_revision_id": draft["revision_id"], "readiness_hash": readiness["readiness_hash"],
                "trial_id": trial["id"], "experiment_spec_hash": trial["experiment_spec_hash"], "authority": authority, "created_at": now()}
            return self.store.put_immutable("draft_launch", binding, "draft.launched")
