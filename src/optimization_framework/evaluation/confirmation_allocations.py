"""Final-run budgets derive procedures without altering their source experiments."""
from copy import deepcopy

from optimization_framework.contracts.base import content_hash
from optimization_framework.contracts.requests import PrototypeAllocationInput


def allocated_method(source_method, allocation):
    """Whitelist allocation changes; retain the exact implementation and inputs."""
    method = deepcopy(source_method)
    values = allocation.model_dump(exclude_none=True, exclude={"expected_control_revision"})
    for key in ("max_steps", "wall_seconds", "schedule_steps"):
        if key in values:
            method[key] = values[key]
    completion = method["completion"]
    if "completion_count" in values:
        completion["count"] = values["completion_count"]
    elif "max_steps" in values and completion["unit"] == "evaluation_requests":
        completion["count"] = values["max_steps"]
    if completion["unit"] == "evaluation_requests" and completion["count"] > method["max_steps"]:
        raise ValueError("The final evaluation completion target exceeds its evaluation request limit")
    for schedule in method.get("diagnostics", []):
        limit = completion["count"] if schedule["unit"] == completion["unit"] else method["max_steps"]
        if max(schedule["at_counts"]) > limit:
            raise ValueError("Final allocation cannot exclude the prototype's frozen diagnostic milestones")
    if method["algorithm"] == "evaluate_asset" and (method["max_steps"] != 1 or completion != {"unit": "evaluation_requests", "count": 1}):
        raise ValueError("Reference evaluation freezes one request; repeated evaluations need separate declared cells")
    return method, values


def derive(prototype, source_method, allocation):
    """Check the displayed prototype revision before freezing a derived procedure."""
    revision = prototype.get("control_revision", 0)
    if allocation.expected_control_revision != revision:
        raise ValueError("The prototype allocation changed; reload its current resource limits before freezing confirmation")
    method, overrides = allocated_method(source_method, allocation)
    return method, {"source_trial_id": prototype["id"], "source_procedure_id": content_hash(source_method),
        "source_control_revision": revision, "source_procedure": deepcopy(source_method),
        "allocation_overrides": overrides}


def binding_id(protocol_id):
    return "allocations_for_" + protocol_id


def source_matches(store, protocol, method_id, prototype):
    """A final allocation may differ from its source only by its frozen overrides.

    Historical protocols have no allocation binding and retain their exact source
    identity check. New bindings are immutable sidecars, so introducing this
    feature does not change any historical protocol or study hash.
    """
    from .confirmation import method_definition

    source = method_definition(prototype)
    try:
        binding = store.get(binding_id(protocol["id"]), "confirmation_allocation_binding")
    except KeyError:
        return content_hash(source) == method_id
    if binding["content_hash"] != content_hash({key: value for key, value in binding.items() if key != "content_hash"}):
        raise ValueError("The frozen confirmation allocation binding changed")
    if binding["protocol_id"] != protocol["id"] or binding["study_id"] != protocol["study_id"] or binding["campaign_id"] != protocol["campaign_id"]:
        raise ValueError("The frozen confirmation allocation belongs to another study")
    entry = binding["methods"].get(method_id)
    if entry is None:
        return content_hash(source) == method_id
    # The control revision prevents stale editing at freeze. Later priority or
    # pause controls may change that revision without changing the procedure;
    # only scientific source/allocation changes invalidate its frozen binding.
    if (entry["source_trial_id"] != prototype["id"] or
            entry["source_procedure_id"] != content_hash(source) or entry["source_procedure"] != source):
        return False
    allocation = PrototypeAllocationInput(expected_control_revision=entry["source_control_revision"], **entry["allocation_overrides"])
    method, _ = allocated_method(source, allocation)
    return method == protocol["methods"][method_id] and content_hash(method) == method_id
