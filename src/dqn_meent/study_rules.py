"""Frozen MEENT replication rules, with evidence read from common study records."""
from dataclasses import asdict

from optimization_framework.analysis.rules import Rule
from optimization_framework.contracts.base import Contract, content_hash
from optimization_framework.optimizers.config import normalize_training
from .replication import (PROFILE_ORDER, DEVELOPMENT_SEEDS, CONFIRMATION_SEEDS, TOLERANCE,
                          profile_config, select_profile, decide)


class FrozenParameters(Contract):
    """Numerical thresholds and seed lists belong to the named protocol version."""


def profile(method):
    if method["algorithm"] != "dqn":
        return None
    values = {**method.get("training", {}), **method.get("algorithm_config", {})}
    values.pop("seed", None)
    initial = values.pop("initial_design", [1] * 64)
    if initial != [1] * 64 or method.get("initial_assets"):
        return None
    if method.get("completion") != {"unit": "optimizer_decisions", "count": 2_000_000} or method["schedule_steps"] != 2_000_000:
        return None
    values = normalize_training(values)
    values.pop("seed")
    for name in PROFILE_ORDER:
        expected = asdict(profile_config(name).training)
        expected.pop("seed")
        if content_hash(values) == content_hash(expected):
            return name
    return None


def compatible(problem):
    from .problem import MeentProblem
    expected = MeentProblem().resolve(asdict(profile_config("P").physics))
    return (problem["scientific_identity"] == expected.scientific_identity and
            problem["primary_objective"] == expected.primary_objective.model_dump(mode="json"))


def control_procedure(method):
    config = method.get("algorithm_config", {})
    if method["algorithm"] == "hillclimb":
        defaults = {"restart_patience": 64, "neighborhood": "permuted", "accept_equal": False, "initialization": "random"}
        expected = {"restart_patience": 128, "neighborhood": "random", "accept_equal": True, "initialization": "ones"}
        return (not method.get("initial_assets") and method["max_steps"] == 2_000_000 and
            method.get("completion") == {"unit": "evaluation_requests", "count": 2_000_000} and
            {**defaults, **config} == expected)
    if method["algorithm"] == "refinement":
        return (method["max_steps"] == 1_000_000 and method.get("completion") == {"unit": "evaluation_requests", "count": 1_000_000}
            and config.get("improvement_tolerance", 1e-12) == 1e-12 and len(method.get("initial_assets", [])) == 1)
    return False


def refinement_prefix(row):
    inputs = row.get("input_assets", [])
    if len(inputs) != 1:
        return False
    asset, producer = inputs[0]["asset"], inputs[0]["producer"]
    graph = inputs[0].get("contributions")
    intervals = graph["intervals"].get(producer["id"], []) if graph and producer else [
        [cost["start"], cost["stop"]] for cost in asset["costs"] if producer and cost["source_id"] == producer["id"]]
    return bool(producer and producer["seed"] == row["seed"] and producer["algorithm"] == "hillclimb" and
        compatible(producer["problem"]) and producer["problem"]["fidelity"] == {"fourier_order": 40} and
        producer["algorithm_config"] == {"restart_patience": 128, "neighborhood": "random", "accept_equal": True, "initialization": "ones"} and
        asset["kind"] == "solution" and asset["cost_provenance"] == "complete" and
        intervals == [[0, 1_000_000]] and not (graph or {}).get("unknown_provenance_asset_ids"))


def convergence(row, orders):
    # A waiver and a measurement at a different tolerance cannot become the
    # numerical assertion required by this particular historical protocol.
    matches = []
    for check in row.get("validation", []):
        allowed = [{"orders": list(orders), "tolerance": TOLERANCE}]
        if tuple(orders) == (320, 480):
            allowed.append({"orders": [40, 160, 320, 480], "tolerance": TOLERANCE})
        if check["recipe_id"] != "fourier_convergence:v1" or check["parameters"] not in allowed:
            continue
        results = [result for result in check["results"] if result["producer"] == "trusted_service"]
        latest = results[-1] if results else None
        if not check["measured_pass"] or not latest or latest["verdict"] != "passed":
            return None  # A failed matching countercheck cannot be hidden by an older pass.
        measurements = latest["measurements"]
        values = {item["fourier_order"]: item["efficiency"] for item in measurements.get("observations", [])}
        if not measurements.get("complete") or any(order not in values for order in orders):
            return None
        matches.append(values)
    return matches[-1] if matches else None


def _selection(evidence, parameters, *, staged):
    rows, sources = [], {}
    for item in evidence["experiments"]:
        name = profile(item["method"])
        if name is None or not compatible(item["problem"]) or item["problem"]["fidelity"] != {"fourier_order": 40}:
            continue
        if item["seed"] not in DEVELOPMENT_SEEDS:
            continue
        cell = (name, item["seed"])
        if cell in sources:
            raise ValueError("The replication selection contains duplicate profile/seed cells")
        sources[cell] = item
        values = convergence(item, (160, 320))
        # The sibling measured diagnostics inline. With separate common-worker
        # jobs, their full physical work must remain in this elapsed-work tie
        # breaker rather than disappearing from the parent process's duration.
        elapsed = item.get("full_worker_seconds") if staged else item["result"].get("elapsed_seconds")
        rows.append({"profile": name, "seed": item["seed"], "phase": "development",
            "status": "completed" if item["evidence_complete"] and not item["amendments"] else "incomplete",
            "converged": values is not None, "efficiency_F320": values[320] if values else None,
            "elapsed_seconds": elapsed})
    if any(row["converged"] and row["status"] == "completed" and row["elapsed_seconds"] is None for row in rows):
        if staged:
            return {"outcome": "inconclusive_selection", "selected_method_ids": [], "rows": rows,
                "needs_attention": True, "reason": "A completed profile lacks measured full numerical worker time required by the frozen tie breaker"}
        raise ValueError("The historical elapsed-time tie breaker needs measured duration")
    result = select_profile(rows)
    if result is None:
        return {"outcome": "inconclusive_selection", "selected_method_ids": [], "reason": "No full production profile has all three converged development seeds"}
    selected = [item for (name, _), item in sources.items() if name == result["selected_profile"]]
    identities = {item["method_id"] for item in selected}
    if len(identities) != 1:
        raise ValueError("The selected profile used different implementations or procedures across development seeds")
    return {**result, "outcome": "selected", "selected_method_ids": sorted(identities), "rows": rows,
            "elapsed_time_basis": "fully attributed numerical worker seconds" if staged else "historical inline elapsed seconds",
            "interpretation": "The frozen 1100 nm / 70 degree replication development rule; selection is not confirmation evidence."}


def selection(evidence, parameters):
    return _selection(evidence, parameters, staged=False)


def staged_selection(evidence, parameters):
    return _selection(evidence, parameters, staged=True)


def conditional_design(design, parameters):
    from optimization_framework.contracts.templates import StudyTemplateVersion
    from .study_templates import production
    if len(design["instances"]) != 1 or not compatible(design["instances"][0]) or design["instances"][0]["fidelity"] != {"fourier_order": 40}:
        raise ValueError("Production replication requires the single frozen 1100 nm / 70 degree condition at F40")
    actual = StudyTemplateVersion(**design["template"]).model_dump(mode="json")
    expected = StudyTemplateVersion(**production()).model_dump(mode="json")
    for value in (actual, expected):
        for key in ("id", "name", "goal"):
            value.pop(key)
        for group in value["groups"]:
            group["task_ids"] = []  # The one physical instance was checked above.
        for slot in value["methods"].values():
            if slot["procedure"] and slot["procedure"]["algorithm"] == "dqn":
                slot["procedure"]["training"] = normalize_training(slot["procedure"]["training"])
    if content_hash(actual) != content_hash(expected):
        raise ValueError("The production replication design must preserve its profiles, references, seeds, checks, priorities and deadlines; use a different rule for altered science")
    if any(profile(design["logical_methods"][name]) != name for name in PROFILE_ORDER):
        raise ValueError("A normalized development profile differs from the frozen production procedure")


def verdict_design(design, parameters, *, selection_rule="replication_selection:v1"):
    nomination = design.get("nomination") or {}
    if nomination.get("rule", {}).get("rule_id") != selection_rule or nomination.get("rule", {}).get("provider") != "meent_grating":
        raise ValueError("The replication verdict requires its frozen production-profile nomination")
    if design["kind"] != "seed_replication" or design["seeds"] != list(CONFIRMATION_SEEDS) or len(design["instances"]) != 1:
        raise ValueError("The production replication verdict requires the declared five fresh seeds on one condition")
    if not compatible(design["instances"][0]) or design["instances"][0]["fidelity"] != {"fourier_order": 40}:
        raise ValueError("The production replication verdict is scoped to the frozen 1100 nm / 70 degree MEENT instance at F40")
    if not design["reference_evidence"]:
        raise ValueError("Freeze the declared comparison references before confirmation begins")
    if any(not compatible(row["problem"]) for row in design["reference_evidence"]):
        raise ValueError("A replication reference belongs to a different scientific problem")
    if not {"hillclimb", "refinement"} <= {method["algorithm"] for method in design["methods"].values()}:
        raise ValueError("The production replication roster requires HC and refinement controls")
    selected = nomination["result"]["selected_profile"]
    profiles = [profile(method) for method in design["methods"].values() if method["algorithm"] == "dqn"]
    if set(profiles) != {"P", selected} or len(profiles) != len(set(profiles)):
        raise ValueError("Freeze the selected production profile and P once each; reuse P when it is selected")
    if any(not control_procedure(method) for method in design["methods"].values() if method["algorithm"] != "dqn"):
        raise ValueError("Replication controls must preserve the declared two-million-request HC and one-million-prefix refinement procedures")


def staged_verdict_design(design, parameters):
    return verdict_design(design, parameters, selection_rule="replication_selection:v2")


def verdict(evidence, parameters):
    nomination = evidence["nomination"]
    selected = nomination["result"]["selected_profile"]
    rows, references, cells = [], [], set()
    for item in evidence["experiments"]:
        method = item["method"]
        name = profile(method)
        kind = {"hillclimb": "hc", "refinement": "refine"}.get(method["algorithm"], "train")
        values = convergence(item, (320, 480))
        complete = item["evidence_complete"] and compatible(item["problem"]) and not item.get("amendments")
        if kind in {"hc", "refine"}:
            complete &= control_procedure(method)
        if kind == "refine":
            complete &= refinement_prefix(item)
        key = (name if kind == "train" else kind, item["seed"])
        if key in cells:
            raise ValueError("Duplicate logical profile/control cells cannot establish a replication verdict")
        cells.add(key)
        rows.append({"profile": name, "kind": kind, "seed": item["seed"],
            "phase": "confirmation" if kind == "train" else "control",
            "status": "completed" if complete else "incomplete", "converged": values is not None,
            **({"efficiency_F480": values[480]} if values else {})})
    for item in evidence["references"]:
        values = convergence(item, (320, 480))
        references.append({"method": item.get("reference_role") or item["method"]["algorithm"], "converged": bool(values and item["evidence_complete"]),
                           **({"efficiency_F480": values[480]} if values else {})})
    result = decide(rows, selected, references)
    result["complete"] &= evidence["roster_complete"]
    if not result["complete"]:
        result["verdict"] = "inconclusive_execution"
    return {**result, "outcome": result["verdict"], "rows": rows, "references": references,
            "interpretation": result["scope"]}


def registered():
    dependencies = (profile_config, select_profile, decide)
    return [Rule("replication_selection:v1", "selection", "MEENT production replication: development selection", FrozenParameters,
                 selection, dependencies=dependencies),
            Rule("replication_verdict:v1", "verdict", "MEENT production replication: five-seed verdict", FrozenParameters,
                 verdict, verdict_design, dependencies=dependencies),
            Rule("replication_selection:v2", "selection", "MEENT staged replication: selection with full numerical worker time", FrozenParameters,
                 staged_selection, dependencies=dependencies),
            Rule("replication_verdict:v2", "verdict", "MEENT staged replication: five-seed verdict and fixed references", FrozenParameters,
                 verdict, staged_verdict_design, dependencies=dependencies, conditional_design=conditional_design)]
