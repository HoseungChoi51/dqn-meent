"""Scalar-objective rules; domain-specific scientific claims live in adapters."""
from collections import defaultdict
import statistics

from pydantic import Field, model_validator

from optimization_framework.contracts.base import Contract
from .rules import Rule


class MedianParameters(Contract):
    seeds: list[int] = Field(default_factory=lambda: [10, 11, 12], min_length=1)
    tie_tolerance: float = Field(default=0, ge=0)

    @model_validator(mode="after")
    def distinct_seeds(self):
        if len(set(self.seeds)) != len(self.seeds) or any(type(seed) is not int or not 0 <= seed < 2**32 for seed in self.seeds):
            raise ValueError("Selection seeds must be distinct unsigned 32-bit integers")
        return self


class PairedParameters(Contract):
    minimum_gain: float = Field(default=0, ge=0)
    minimum_wins: int = Field(default=3, ge=1)


def median_selection(evidence, parameters):
    rows = evidence["experiments"]
    instances = {row["instance_digest"] for row in rows}
    if len(instances) > 1:
        raise ValueError("Median selection compares methods on one resolved instance and fidelity")
    groups = defaultdict(list)
    for row in rows:
        groups[row["method_id"]].append(row)
    candidates, exclusions = [], []
    for method_id, group in sorted(groups.items()):
        selected = [row for row in group if row["seed"] in parameters.seeds]
        if len({row["seed"] for row in selected}) != len(selected):
            raise ValueError("A selection cell has duplicate experiments; declare a new unambiguous study")
        if {row["seed"] for row in selected} != set(parameters.seeds) or not all(row["evidence_complete"] for row in selected):
            exclusions.append({"method_id": method_id, "reason": "Missing or incomplete declared seed evidence"})
            continue
        values = [row["result"].get("best_objective") for row in selected]
        if any(value is None for value in values):
            exclusions.append({"method_id": method_id, "reason": "Missing measured primary objective"})
            continue
        costs = [row["full_worker_seconds"] for row in selected]
        direction = selected[0]["problem"]["primary_objective"]["direction"]
        candidates.append({"method_id": method_id, "median": statistics.median(values), "direction": direction,
            "full_worker_seconds": sum(costs) if all(cost is not None for cost in costs) else None,
            "trial_ids": [row["trial_id"] for row in sorted(selected, key=lambda row: row["seed"])]})
    if not candidates:
        return {"outcome": "inconclusive_selection", "selected_method_ids": [], "candidates": [], "exclusions": exclusions}
    sign = 1 if candidates[0]["direction"] == "maximize" else -1
    best = max(sign * row["median"] for row in candidates)
    tied = [row for row in candidates if sign * row["median"] >= best - parameters.tie_tolerance]
    if len(tied) > 1 and any(row["full_worker_seconds"] is None for row in tied):
        return {"outcome": "inconclusive_selection", "selected_method_ids": [], "candidates": candidates,
                "exclusions": exclusions, "reason": "The declared full-cost tie breaker has unknown upstream costs"}
    winner = sorted(tied, key=lambda row: (row["full_worker_seconds"] or 0, row["method_id"]))[0]
    return {"outcome": "selected", "selected_method_ids": [winner["method_id"]], "candidates": candidates,
            "exclusions": exclusions, "selection": winner,
            "interpretation": "Best median primary objective on the declared seeds; ties use full upstream worker time and method identity."}


def paired_verdict(evidence, parameters):
    nomination = evidence.get("nomination") or {}
    nominated = nomination.get("selected_method_ids", [])
    if len(nominated) != 1:
        raise ValueError("Paired improvement requires exactly one frozen nominated method")
    selected = nominated[0]
    methods = {row["method_id"] for row in evidence["experiments"]}
    if not methods - {selected}:
        raise ValueError("Paired improvement requires at least one frozen control method")
    groups = defaultdict(dict)
    for row in evidence["experiments"]:
        key = (row["instance_digest"], row["seed"])
        if row["method_id"] in groups[key]:
            raise ValueError("Duplicate paired confirmation cell")
        groups[key][row["method_id"]] = row
    if parameters.minimum_wins > len(groups):
        raise ValueError("The required wins exceed the frozen confirmation roster")
    comparisons = []
    complete = bool(evidence["roster_complete"])
    for (instance, seed), cells in sorted(groups.items()):
        if set(cells) != methods or selected not in cells or not all(row["evidence_complete"] for row in cells.values()):
            complete = False
            continue
        sign = 1 if cells[selected]["problem"]["primary_objective"]["direction"] == "maximize" else -1
        values = {key: row["result"].get("best_objective") for key, row in cells.items()}
        if any(value is None for value in values.values()):
            complete = False
            continue
        control = max(sign * value for key, value in values.items() if key != selected)
        gain = sign * values[selected] - control
        comparisons.append({"instance_digest": instance, "seed": seed, "gain": gain, "win": gain > parameters.minimum_gain})
    wins = sum(row["win"] for row in comparisons)
    outcome = ("inconclusive_execution" if not complete else "declared_improvement_criterion_met" if wins >= parameters.minimum_wins
               else "isolated_improvement" if wins else "no_demonstrated_improvement")
    return {"outcome": outcome, "complete": complete, "winning_cells": wins, "required_wins": parameters.minimum_wins,
            "minimum_gain": parameters.minimum_gain, "comparisons": comparisons,
            "interpretation": "Paired measured objective differences against the best declared control at each seed and instance; this criterion is limited to the frozen roster and does not estimate population-level significance."}


def paired_design(design, parameters):
    selected = (design.get("nomination") or {}).get("selected_method_ids", [])
    if len(selected) != 1 or not set(selected) < set(design["methods"]):
        raise ValueError("Paired improvement needs one nominated method and at least one distinct control prototype")
    if parameters.minimum_wins > len(design["seeds"]) * len(design["instances"]):
        raise ValueError("The required wins exceed the frozen confirmation roster")


def median_design(design, parameters):
    from optimization_framework.contracts.problems import ProblemInstance
    if len({ProblemInstance(**item).digest() for item in design["instances"]}) != 1:
        raise ValueError("Median selection compares methods on one resolved instance and fidelity")


def paired_conditional_design(design, parameters):
    from optimization_framework.contracts.base import content_hash
    template = design["template"]
    selected_slots = [key for key, slot in template["methods"].items() if slot["select_from"]]
    if len(selected_slots) != 1:
        raise ValueError("Paired confirmation needs exactly one rule-selected method slot")
    selected = selected_slots[0]
    groups = [row for row in template["groups"] if row["scope"] == "confirmation"]
    rosters = {}
    for group in groups:
        pairs = {(key, seed) for key in group["task_ids"] or design["task_ids"] for seed in group["seeds"]}
        for slot in group["slots"]:
            rosters.setdefault(slot, set()).update(pairs)
    if selected not in rosters or not rosters[selected] or any(pairs != rosters[selected] for pairs in rosters.values()):
        raise ValueError("Paired confirmation requires matching method, instance and seed rosters")
    if parameters.minimum_wins > len(rosters[selected]):
        raise ValueError("The required wins exceed the declared conditional cohort")
    controls = {content_hash(design["logical_methods"][key]) for key in rosters if key != selected and key in design["logical_methods"]}
    for candidate in template["methods"][selected]["select_from"]:
        if not controls - {content_hash(design["logical_methods"][candidate])}:
            raise ValueError("Every permitted nomination needs a distinct predeclared control procedure")


def registered():
    return [Rule("median_objective:v1", "selection", "Median objective with full-cost tie breaker", MedianParameters, median_selection, median_design),
            Rule("paired_improvement:v1", "verdict", "Paired improvement against declared controls", PairedParameters, paired_verdict, paired_design,
                 conditional_design=paired_conditional_design)]
