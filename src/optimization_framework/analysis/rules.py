"""Versioned, deterministic selection and claim rules supplied by installed code."""
from dataclasses import dataclass
import inspect
from pathlib import Path
from typing import Callable

from optimization_framework.contracts.base import content_hash
from optimization_framework.contracts.study_rules import RuleRequest


@dataclass(frozen=True)
class Rule:
    id: str
    kind: str
    title: str
    parameters: type
    evaluate: Callable
    design: Callable | None = None
    dependencies: tuple = ()
    conditional_design: Callable | None = None

    def digest(self):
        sources = {inspect.getmodule(value).__name__: Path(inspect.getfile(value)).read_text()
                   for value in (self.parameters, self.evaluate, self.design, self.conditional_design, *self.dependencies) if value is not None}
        return content_hash({"id": self.id, "kind": self.kind, "sources": sources})


def rules(provider):
    if provider == "framework":
        from .standard_rules import registered
        return registered()
    from optimization_framework.evaluation.registry import problems
    adapter = problems.get(provider)
    return adapter.study_rules() if hasattr(adapter, "study_rules") else []


def catalog():
    from optimization_framework.evaluation.registry import problems
    result = []
    for provider in ["framework", *problems.ids()]:
        for rule in rules(provider):
            schema = rule.parameters.model_json_schema()
            schema["properties"].pop("schema_version", None)
            result.append({"provider": provider, "rule_id": rule.id, "kind": rule.kind, "title": rule.title,
                "parameters_schema": schema, "defaults": rule.parameters().model_dump(mode="json", exclude={"schema_version"}),
                "source_digest": rule.digest()})
    return result


def resolve(provider, identity, kind):
    rule = next((rule for rule in rules(provider) if rule.id == identity and rule.kind == kind), None)
    if rule is None:
        raise ValueError("The requested study rule is not installed for this purpose")
    return rule


def freeze(request, kind, instances, *, store=None):
    request = request if isinstance(request, RuleRequest) else RuleRequest(**request)
    if request.provider != "framework" and {item.definition_id for item in instances} != {request.provider}:
        raise ValueError("The analysis rule does not support all of this study's problem instances")
    if request.provider != "framework" and any(item.evaluator_id.startswith("package:") for item in instances):
        raise ValueError("A generated problem requires a framework analysis rule; domain rules belong to reviewed installed adapters")
    if store is not None:
        from optimization_framework.execution.provenance import archive, invoke_archive
        source = archive(store, problem_ids=[item.definition_id for item in instances if not item.evaluator_id.startswith("package:")], purpose="rule")
        binding = invoke_archive(store, source["id"], "rule.freeze", {"request": request.model_dump(mode="json"),
            "kind": kind, "instances": [item.model_dump(mode="json") for item in instances]})
        return {**binding, "execution_source_id": source["id"], "execution_source_digest": content_hash(source["manifest"])}
    rule = resolve(request.provider, request.rule_id, kind)
    parameters = rule.parameters(**request.parameters).model_dump(mode="json", exclude={"schema_version"})
    return {"provider": request.provider, "rule_id": request.rule_id, "kind": kind,
            "parameters": parameters, "source_digest": rule.digest()}


def _captured(binding, operation, payload, store):
    from optimization_framework.execution.provenance import invoke_archive
    if store is None:
        raise ValueError("The frozen analysis source needs its owning artifact repository")
    source = store.get(binding["execution_source_id"], "execution_source")
    if content_hash(source["manifest"]) != binding["execution_source_digest"]:
        raise ValueError("The frozen analysis source identity changed")
    original = {key: value for key, value in binding.items() if key not in {"execution_source_id", "execution_source_digest"}}
    return invoke_archive(store, source["id"], operation, {"binding": original, **payload})


def evaluate(binding, evidence, *, store=None):
    if binding.get("execution_source_id"):
        return _captured(binding, "rule.evaluate", {"evidence": evidence}, store)
    rule = resolve(binding["provider"], binding["rule_id"], binding["kind"])
    if rule.digest() != binding["source_digest"]:
        raise ValueError("The frozen analysis rule source is unavailable; current code cannot replace it")
    return rule.evaluate(evidence, rule.parameters(**binding["parameters"]))


def check_design(binding, design, *, store=None):
    if binding.get("execution_source_id"):
        return _captured(binding, "rule.check_design", {"design": design}, store)
    rule = resolve(binding["provider"], binding["rule_id"], binding["kind"])
    if rule.digest() != binding["source_digest"]:
        raise ValueError("The frozen analysis rule source is unavailable; current code cannot replace it")
    if design.get("conditional"):
        if not rule.conditional_design:
            raise ValueError("This analysis rule does not declare a conditional-cohort design contract")
        return rule.conditional_design(design, rule.parameters(**binding["parameters"]))
    if rule.design:
        return rule.design(design, rule.parameters(**binding["parameters"]))
