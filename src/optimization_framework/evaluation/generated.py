"""Reviewed adapter for data-only manifests; candidate code stays in its host."""
from pathlib import Path
from optimization_framework.contracts.problems import ProblemInstance
from optimization_framework.implementations.models import EvaluatorSpec, digest
from optimization_framework.implementations.runtime import tree_hashes
from optimization_framework.storage.sqlite import read_json


def evaluator_identity(version):
    return digest({"evaluator_version_id": version["id"], "artifact_digest": version["artifact_digest"]})


class DeclaredEvaluator:
    """Compile reviewed operations from manifest data, without an executable."""
    def __init__(self, manifest, evaluator_version, *, recipe_registry=None):
        from optimization_framework.contracts.evaluators import EvaluatorManifest
        from optimization_framework.evaluation.registered_recipes import recipes
        self.manifest = EvaluatorManifest.model_validate(manifest)
        self.evaluator_version = evaluator_version
        self.recipes = recipe_registry or recipes

    def describe(self):
        return self.manifest.describe(self.evaluator_version, recipe_registry=self.recipes)

    def resolve(self, configuration, fidelity=None):
        return self.manifest.resolve(self.evaluator_version, configuration, fidelity)

    def recipe_parameters(self, problem, identity, parameters):
        if identity not in self.manifest.recipe_ids:
            raise ValueError("The evaluator manifest does not declare this recipe version")
        if self.resolve(problem.configuration, problem.fidelity) != problem:
            raise ValueError("Recipe source differs from the captured evaluator instance")
        recipe, _ = self.recipes.get(identity)
        return recipe.parameters(self, problem, parameters)

    def plan_recipe(self, problem, identity, parameters, subjects):
        parameters = self.recipe_parameters(problem, identity, parameters)
        recipe, descriptor = self.recipes.get(identity)
        return {**recipe.plan(self, problem, parameters, subjects), "registered_recipe": descriptor.model_dump(mode="json")}

    def summarize_recipe(self, plan, observations):
        recipe, descriptor = self.recipes.get(plan["recipe_id"])
        if plan.get("registered_recipe") != descriptor.model_dump(mode="json"):
            raise ValueError("Recipe descriptor differs from its frozen procedure")
        return recipe.summarize(plan, observations)

    def evaluator(self, instance):
        raise ValueError("A manifest declaration has no executable evaluator; bind a validated version before execution")


def declared_registry(problem, manifest, *, registry=None):
    from optimization_framework.evaluation.registry import problems
    problem = ProblemInstance.model_validate(problem)
    adapter = DeclaredEvaluator(manifest, problem.evaluator_version)
    if adapter.resolve(problem.configuration, problem.fidelity) != problem:
        raise ValueError("Evaluator declaration differs from the frozen problem")
    return (registry or problems).extended(adapter)


class PublishedEvaluator(DeclaredEvaluator):
    def __init__(self, bundle, package_dir, *, progress=None, recipe_registry=None, timeout_limit=None):
        self.bundle = bundle
        self.package_dir = package_dir
        self.progress = progress
        self.timeout_limit = timeout_limit
        version, artifact = bundle["version"], bundle["artifact"]
        if (version.get("kind") != "evaluator" or digest(artifact) != version["artifact_digest"]
                or tree_hashes(package_dir) != version["package_hashes"]):
            raise ValueError("Published evaluator source identity changed")
        self.spec = EvaluatorSpec.model_validate(artifact["spec"])
        super().__init__(self.spec.manifest, version["id"], recipe_registry=recipe_registry)

    def implementation_fixture(self, configuration, dimensions):
        instance = self.resolve(configuration)
        if instance.candidate_schema.dimensions != dimensions:
            raise ValueError("Optimizer dimension scope must match the declared evaluator manifest")
        return instance

    def evaluator(self, instance):
        from optimization_framework.implementations.evaluator_runtime import PackageEvaluator
        from optimization_framework.implementations.runtime import bundle_runtime_root
        instance = instance if isinstance(instance, ProblemInstance) else ProblemInstance.model_validate(instance)
        if self.resolve(instance.configuration, instance.fidelity) != instance:
            raise ValueError("Evaluator does not match the frozen instance")
        artifact = self.bundle["artifact"]
        timeout = self.spec.operation_timeout_seconds
        if self.timeout_limit is not None:
            timeout = min(timeout, self.timeout_limit)
        return PackageEvaluator(self.package_dir, bundle_runtime_root(self.bundle, Path(self.package_dir).parent), artifact["runtime"], artifact["package"]["entrypoint"], instance,
            progress=self.progress, timeout=timeout, max_checkpoint_bytes=self.spec.max_checkpoint_bytes)

def pinned_evaluator(directory, specification, *, recipe_registry=None):
    """Resolve captured data/source without importing or executing candidate code."""
    from optimization_framework.contracts.experiments import ExperimentSpec
    directory = Path(directory)
    bundle = read_json(directory / "evaluator" / "bundle.json")
    if not bundle:
        raise ValueError("The pinned evaluator bundle is unavailable")
    artifact, version = bundle["artifact"], bundle["version"]
    binding = {key: specification[key] for key in ("evaluator_version_id", "evaluator_artifact_digest", "evaluator_runtime_digest")}
    frozen = ExperimentSpec.model_validate(specification["experiment_spec"])
    if (frozen.digest() != specification["experiment_spec_hash"] or frozen.problem.model_dump(mode="json") != specification["problem"]
            or digest(artifact) != binding["evaluator_artifact_digest"] or version["id"] != binding["evaluator_version_id"]
            or version.get("kind") != "evaluator" or artifact["runtime"]["digest"] != binding["evaluator_runtime_digest"]
            or frozen.schedule.get("evaluator") != binding):
        raise ValueError("Pinned evaluator changed after this experiment was queued")
    adapter = PublishedEvaluator(bundle, directory / "evaluator" / "package", recipe_registry=recipe_registry,
        timeout_limit=max(.05, specification["wall_seconds"] - specification.get("execution_seconds", 0)))
    if adapter.resolve(frozen.problem.configuration, frozen.problem.fidelity) != frozen.problem:
        raise ValueError("The pinned evaluator does not resolve the frozen problem")
    if adapter.spec.validation_mode == "contract_only":
        eligibility = specification.get("evaluator_eligibility", {})
        if (not eligibility or eligibility != bundle.get("eligibility") or eligibility != frozen.schedule.get("evaluator_eligibility")
                or eligibility.get("basis") not in {"waiver", "numerical_validation"} or eligibility.get("study_id") != specification["study_id"]
                or eligibility.get("binding_id") != specification.get("evaluator_binding_id")):
            raise ValueError("The contract-only evaluator requires its frozen evidence or exploratory eligibility")
        if eligibility["basis"] == "numerical_validation":
            from optimization_framework.contracts.base import content_hash
            from optimization_framework.implementations.evaluator_validation import current
            if (not current(version) or version["validation_report"]["id"] != eligibility["validation_report_id"]
                    or content_hash(version["validation_report"]) != eligibility["validation_report_digest"]):
                raise ValueError("Pinned evaluator numerical evidence differs from the frozen authorization")
    return adapter
