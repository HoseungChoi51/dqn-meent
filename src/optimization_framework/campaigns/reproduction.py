"""New exploratory executions of admitted snapshots, under ordinary campaign controls."""
import math

from optimization_framework.assets import captures
from optimization_framework.contracts.base import content_hash
from optimization_framework.contracts.bundles import EvidenceBundle, RecordKey
from optimization_framework.contracts.drafts import DraftSaveInput
from optimization_framework.contracts.experiments import CompletionCondition
from optimization_framework.contracts.reproduction import ReproductionDraftInput, ReproductionIntent
from optimization_framework.contracts.requests import TrialInput
from optimization_framework.execution import provenance
from optimization_framework.storage import history
from optimization_framework.storage.sqlite import now


def close_values(expected, actual, absolute, relative):
    """Candidate structure and nonnumeric values are exact; only numbers have tolerances."""
    if isinstance(expected, bool) or isinstance(actual, bool):
        return type(expected) is type(actual) and expected == actual
    if isinstance(expected, (float, int)) and isinstance(actual, (float, int)):
        return math.isfinite(expected) and math.isfinite(actual) and math.isclose(
            expected, actual, abs_tol=absolute, rel_tol=relative)
    if isinstance(expected, list) and isinstance(actual, list):
        return len(expected) == len(actual) and all(close_values(a, b, absolute, relative) for a, b in zip(expected, actual))
    if isinstance(expected, dict) and isinstance(actual, dict):
        return expected.keys() == actual.keys() and all(close_values(value, actual[key], absolute, relative) for key, value in expected.items())
    return type(expected) is type(actual) and expected == actual


class ReproductionService:
    def __init__(self, workspace):
        self.workspace, self.store = workspace, workspace.store

    def references(self, campaign_id):
        """Visibility comes from a published bundle in this campaign, not a global ID."""
        published = {row["bundle_digest"] for row in self.store.list("bundle_import_receipt", campaign_id)}
        references = {}
        for inspection in self.store.list("bundle_inspection", campaign_id):
            if inspection["summary"]["bundle_digest"] not in published:
                continue
            manifest = EvidenceBundle(**inspection["manifest"])
            if manifest.digest not in published:
                raise ValueError("Published evidence manifest changed")
            for reference in manifest.records:
                if reference.owner == "workspace" and reference.kind == "trial":
                    references[reference.key] = reference
        return references

    def original(self, campaign_id, reference):
        reference = RecordKey.model_validate(reference)
        if reference.key not in self.references(campaign_id):
            raise ValueError("Import this exact experiment snapshot into the campaign before reproducing it")
        original = history.exact(self.store, reference).data
        self.check_release(original)
        return original

    def check_release(self, original):
        if original.get("locked") or original.get("task_split") in {"test", "confirmation", "heldout"} or original.get("protected_cohort_id"):
            released = {identity for release in history.releases(self.store, original["campaign_id"]) for identity in release["trial_ids"]}
            if original["id"] not in released:
                raise ValueError("Historical cohort evidence requires its recorded result release before reproduction")

    @staticmethod
    def procedure(original):
        frozen = original.get("experiment_spec")
        if (original.get("execution_contract") != 1 or not frozen
                or content_hash(frozen) != original.get("experiment_spec_hash")):
            raise ValueError("This historical experiment lacks a supported immutable procedure")
        if original.get("recipe") or original.get("inference_adapter") or original.get("parent_trial_id"):
            raise ValueError("Select an optimization experiment; historical child analyses require their own declared inputs")
        parameters = frozen["parameters"]
        if parameters.get("algorithm") in {"custom", "recipe", "validate"}:
            raise ValueError("This historical execution contract requires a supported procedure converter")
        implementation = frozen["implementation"]
        if original.get("implementation_version_id") and any(original.get(key) != expected for key, expected in (
                ("implementation_version_id", implementation["id"]),
                ("implementation_artifact_digest", implementation["source_digest"]),
                ("implementation_runtime_digest", implementation["runtime_digest"]))):
            raise ValueError("Historical optimizer differs from its frozen executable identity")
        evaluator = frozen["schedule"].get("evaluator", {})
        if any(original.get(key) != value for key, value in evaluator.items()):
            raise ValueError("Historical evaluator differs from its frozen executable identity")
        fields = {key: parameters[key] for key in ("algorithm", "algorithm_config", "training")}
        fields.update(seed=frozen["seed"], completion=frozen["completion"], recovery=frozen["recovery"],
            schedule_steps=frozen["schedule"]["steps"], initial_assets=frozen["initial_assets"],
            diagnostics=frozen["diagnostics"], implementation_version_id=original.get("implementation_version_id"),
            dependencies=[], hypothesis_id=None, confirmatory=False, confirmation_protocol_id=None)
        # Current contracts normalize schema/default fields identically on both sides.
        normalized = TrialInput(campaign_id="reference", task_id="reference", **fields).model_dump(mode="json")
        normalized["completion"] = CompletionCondition(**normalized["completion"]).model_dump(mode="json", exclude={"schema_version"})
        return {key: normalized[key] for key in fields}

    def sources(self, campaign_id):
        tasks = [self.workspace.evaluators.task_view(task) for task in self.workspace.current_tasks(campaign_id)]
        result = []
        for key, reference in sorted(self.references(campaign_id).items()):
            original = history.exact(self.store, reference).data
            try:
                self.check_release(original)
                self.procedure(original)
                reason = None
            except (KeyError, ValueError) as exc:
                reason = str(exc)
            result.append({"reference": reference.model_dump(mode="json"), "key": key,
                "algorithm": original.get("algorithm"), "task_name": original.get("task_name"),
                "seed": original.get("seed"), "status": original.get("status"),
                "compatible_task_ids": [task["id"] for task in tasks if task.get("problem") == original.get("problem")],
                "unsupported_reason": reason})
        return result

    def save(self, campaign_id, request, *, authority="researcher"):
        request = ReproductionDraftInput.model_validate(request)
        original = self.original(campaign_id, request.reference)
        procedure = {**self.procedure(original), "campaign_id": campaign_id, "task_id": request.task_id,
            "max_steps": original["max_steps"], "wall_seconds": request.wall_seconds,
            "reuse_decision_ids": request.reuse_decision_ids,
            "question": "Reproduce the result of " + original["id"]}
        return self.workspace.drafts.save(campaign_id, DraftSaveInput(title=request.title, study_id=request.study_id,
            procedure=procedure, reproduction=ReproductionIntent(reference=request.reference, comparison=request.comparison)), authority=authority)

    def check_procedure(self, campaign_id, intent, request):
        original = self.original(campaign_id, intent.reference)
        expected = self.procedure(original)
        actual = TrialInput.model_validate(request).model_dump(mode="json")
        actual["completion"] = CompletionCondition(**actual["completion"]).model_dump(mode="json", exclude={"schema_version"})
        if any(actual[key] != value for key, value in expected.items()):
            raise ValueError("Exact reproduction retains the historical procedure, seed and input assets. Changed science requires a new experiment.")
        return original

    def inputs(self, draft):
        decisions = self.store.list("reuse_decision", draft["campaign_id"])
        result = []
        for identity in draft["procedure"]["initial_assets"]:
            try:
                asset = self.store.get(identity, "asset")
            except KeyError:
                asset = {"title": identity}
            result.append({"asset_id": identity, "title": asset["title"], "decisions": [decision for decision in decisions
                if decision["asset_id"] == identity and decision["study_id"] == draft["study_id"]
                and decision["decision"] == "reuse" and decision["intended_use"] == "optimizer_input"]})
        return result

    def prepare(self, draft, task, request, assets):
        intent = ReproductionIntent.model_validate(draft["reproduction"])
        original = self.check_procedure(draft["campaign_id"], intent, request)
        if task["problem"] != original["experiment_spec"]["problem"]:
            raise ValueError("Select the exact historical problem, fidelity and evaluator version in this campaign")
        if {asset["id"]: asset["content_hash"] for asset in assets} != original["experiment_spec"]["asset_digests"]:
            raise ValueError("Historical input assets are missing or differ from the frozen input identities")
        bindings, conversions = {}, []
        from optimization_framework.implementations.runtime import bundle_runtime_root, verify_runtime
        for prefix in ("implementation", "evaluator"):
            version_id = original.get(prefix + "_version_id")
            if not version_id:
                continue
            bundle = self.workspace.implementations.client.artifact(version_id)
            if (bundle["version"]["artifact_digest"] != original[prefix + "_artifact_digest"]
                    or bundle["version"]["runtime_digest"] != original[prefix + "_runtime_digest"]):
                raise ValueError("The historical " + prefix + " artifact or runtime identity changed")
            runtime = verify_runtime(bundle_runtime_root(bundle), bundle["artifact"]["runtime"])
            if runtime.get("conversion"):
                conversions.append(runtime["conversion"])
            bindings[prefix] = {"version_id": version_id, "artifact_digest": bundle["version"]["artifact_digest"],
                                "runtime_digest": bundle["version"]["runtime_digest"]}
        source, manifest, _ = captures.experiment(self.workspace, intent.reference)
        if bindings:
            from optimization_framework.execution.package_host import captured_driver
            captured_driver(source)
        if original["experiment_spec"]["schedule"].get("execution_manifest_digest") != content_hash(manifest):
            raise ValueError("Captured source differs from the historical frozen procedure")
        implementation = self.store.get(original["implementation_version_id"], "implementation_cache")["spec"] if original.get("implementation_version_id") else None
        prepared = provenance.invoke(source, manifest, "trial.prepare", {"request": request,
            "problem": task["problem"], "assets": assets, "implementation": implementation,
            "evaluator_manifest": task.get("evaluator_manifest")}, isolated=True)
        resolved = {**request, **{key: prepared[key] for key in ("algorithm_config", "training", "completion", "diagnostics")}}
        self.check_procedure(draft["campaign_id"], intent, resolved)
        binding = {**intent.model_dump(mode="json"), "original_experiment_spec_hash": original["experiment_spec_hash"],
            "execution_manifest_digest": content_hash(manifest), "executable_bindings": bindings, "runtime_conversions": conversions}
        return prepared, binding

    def source(self, binding):
        directory, manifest, _ = captures.experiment(self.workspace, binding["reference"])
        if content_hash(manifest) != binding["execution_manifest_digest"]:
            raise ValueError("Reproduction source changed after readiness review")
        return directory, manifest

    def compare(self, trial):
        """Append a result assessment for a committed attempt; recovery never erases it."""
        if not trial.get("reproduction"):
            raise ValueError("Select an experiment launched from a historical reproduction draft")
        if trial["status"] in {"queued", "running", "pausing", "stopping"} or trial.get("asset_capture_attempt") != trial["attempt"]:
            raise ValueError("Wait for the attempt's evidence to be committed before comparing results")
        frozen = self.store.get(trial["experiment_spec_id"], "experiment_spec")
        binding = frozen["schedule"].get("reproduction")
        if (binding != trial["reproduction"] or frozen["content_hash"] != trial["experiment_spec_hash"]
                or content_hash({key: value for key, value in frozen.items() if key != "content_hash"}) != frozen["content_hash"]):
            raise ValueError("The frozen reproduction binding changed")
        intent = ReproductionIntent(**{key: binding[key] for key in ("reference", "comparison")})
        original = history.exact(self.store, intent.reference).data
        expected, actual = original.get("result") or {}, trial.get("result") or {}
        basis = {"reference": binding["reference"], "trial_id": trial["id"], "attempt": trial["attempt"],
            "experiment_spec_hash": trial["experiment_spec_hash"], "comparison": binding["comparison"],
            "original_result_digest": content_hash(expected), "result_digest": content_hash(actual),
            "output_asset_ids": sorted(trial.get("latest_output_asset_ids", []))}
        identity = "reproduction_comparison_" + content_hash(basis)
        try:
            return self.store.get(identity, "reproduction_comparison")
        except KeyError:
            pass
        reasons, measurements = [], {}
        for label, result in (("Historical", expected), ("New", actual)):
            if not result.get("scientific_complete") or result.get("status") != "completed":
                reasons.append(label + " scientific procedure is incomplete or its completion evidence is missing")
            if result.get("best_objective") is None or result.get("objective_definition") is None:
                reasons.append(label + " primary objective evidence is missing")
            if intent.comparison.compare_candidate and result.get("best_candidate") is None:
                reasons.append(label + " best candidate evidence is missing")
        if not reasons:
            rule = intent.comparison
            measurements["primary_objective"] = {"expected": expected["best_objective"], "actual": actual["best_objective"],
                "agrees": expected["objective_definition"] == actual["objective_definition"] and close_values(
                    expected["best_objective"], actual["best_objective"], rule.objective_absolute_tolerance, rule.objective_relative_tolerance)}
            if rule.compare_candidate:
                measurements["best_candidate"] = {"expected": expected["best_candidate"], "actual": actual["best_candidate"],
                    "agrees": close_values(expected["best_candidate"], actual["best_candidate"],
                        rule.candidate_absolute_tolerance, rule.candidate_relative_tolerance)}
        outcome = "inconclusive" if reasons else "agreement" if all(row["agrees"] for row in measurements.values()) else "disagreement"
        record = {"id": identity, "schema_version": 1, "campaign_id": trial["campaign_id"], "study_id": trial["study_id"],
            **basis, "outcome": outcome, "reasons": reasons, "measurements": measurements,
            "runtime_limitations": [text for conversion in binding.get("runtime_conversions", []) for text in conversion["limitations"]],
            "scope": "Best primary objective and declared candidate comparison; no trajectory, superiority or independent correctness claim.",
            "created_at": now()}
        return self.store.put_immutable("reproduction_comparison", record, "reproduction.compared")
