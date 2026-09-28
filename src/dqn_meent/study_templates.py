"""Adapter-owned production replication and separately labeled short qualification."""
from dataclasses import asdict
import json
from pathlib import Path

from .replication import PROFILE_ORDER, DEVELOPMENT_SEEDS, CONFIRMATION_SEEDS, TOLERANCE, checkpoint_steps, profile_config


def references():
    return json.loads((Path(__file__).parent / "data/replication_references.json").read_text())["references"]


def reference_sets():
    from .problem import physical_identity
    configuration = asdict(profile_config("P").physics)
    return [{"id": "replication-1100nm-70deg:v1", "title": "Preserved MEENT replication references",
        "description": "The paper candidate and earlier HC/refinement candidates at seeds 0–2 on 1100 nm / 70 degrees. Original production costs and complete exposure history remain unknown.",
        "problem_id": "meent_grating", "configuration": configuration, "scientific_identity": physical_identity(configuration),
        "origin_repository": "dqn-meent", "captured_at": "2026-09-27",
        "solutions": [{**{key: item[key] for key in ("slot", "title", "candidate", "candidate_digest", "role", "seed")},
            "sources": [{"path": item["source_file"], "sha256": item["source_sha256"]},
                {"path": item["captured_reference_file"], "sha256": item["captured_reference_sha256"]}]} for item in references()]}]


def production():
    total = 2_000_000
    milestones = checkpoint_steps(total)
    convergence = {"recipe_id": "fourier_convergence:v1", "parameters": {"orders": [160, 320], "tolerance": TOLERANCE}, "wall_seconds": 60}
    rollout_check = {"recipe_id": "reevaluate:v1", "parameters": {"orders": [160]}, "wall_seconds": 15}
    seed = {"kind": "affine:v1", "offset": 1_000_000_000, "parent_seed_factor": 10_000_000, "milestone_factor": 20, "episode_factor": 1}
    policy_schedule = {"unit": "optimizer_decisions", "at_counts": milestones, "export_optimizer": True,
        "recipes": [convergence], "rollouts": [{"seed": seed, "epsilon": epsilon, "horizon": 128, "wall_seconds": 15,
            "recipes": [rollout_check]} for epsilon in [0.] + [.01]*10]}
    recovery = {"every_observations": 100_000, "every_seconds": 300}
    methods = {}
    for name in PROFILE_ORDER:
        training = asdict(profile_config(name).training)
        training.pop("seed")
        methods[name] = {"procedure": {"algorithm": "dqn", "training": training,
            "max_steps": total + total//training["horizon"] + 1, "schedule_steps": total,
            "completion": {"unit": "optimizer_decisions", "count": total}, "recovery": recovery,
            "diagnostics": [policy_schedule], "wall_seconds": 86400}}
    methods.update({"HC": {"procedure": {"algorithm": "hillclimb", "max_steps": total,
        "algorithm_config": {"restart_patience": 128, "neighborhood": "random", "accept_equal": True, "initialization": "ones"},
        "recovery": recovery, "wall_seconds": 86400,
        "diagnostics": [{"at_counts": milestones, "export_optimizer": False, "recipes": [convergence]}]}},
        "refine": {"procedure": {"algorithm": "refinement", "max_steps": total//2, "recovery": recovery, "wall_seconds": 86400,
            "input_binding": {"group": "controls", "slot": "HC", "count": total//2},
            "diagnostics": [{"at_counts": [count-total//2 for count in milestones if count > total//2],
                "export_optimizer": False, "recipes": [convergence]}]}},
        "selected": {"select_from": list(PROFILE_ORDER)}})
    groups, requirements = [], {}
    for reference in references():
        slot = reference["slot"]
        requirements[slot] = {"title": reference["title"], "asset_kind": "solution", "candidate_digest": reference["candidate_digest"]}
        methods[slot] = {"procedure": {"algorithm": "evaluate_asset", "max_steps": 1, "wall_seconds": 15,
            "input_binding": {"kind": "declared_asset:v1", "slot": slot}}}
        groups.append({"id": "reference_"+slot, "scope": "reference", "reference_role": reference["role"],
            "slots": [slot], "seeds": [reference["seed"]], "priority": 100})
    # Group order is the stable tie breaker within a priority. Group per seed
    # preserves the sibling's P/selected and HC/refinement seed ordering.
    for seed_value in CONFIRMATION_SEEDS:
        groups.append({"id": f"confirmation_{seed_value}", "scope": "confirmation", "slots": ["P", "selected"],
            "seeds": [seed_value], "admission": "nomination", "priority": 80})
    for name in ("P", "C", "S"):
        groups.append({"id": "development_"+name, "scope": "development", "slots": [name], "seeds": list(DEVELOPMENT_SEEDS), "priority": 60})
    groups.append({"id": "controls", "scope": "confirmation", "slots": ["HC", "refine"],
        "seeds": list(CONFIRMATION_SEEDS), "cell_order": "seed_then_slot", "priority": 40})
    for name in PROFILE_ORDER:
        if name not in {"P", "C", "S"}:
            groups.append({"id": "development_"+name, "scope": "development", "slots": [name], "seeds": list(DEVELOPMENT_SEEDS), "priority": 20})
    def policy(orders):
        return {"required_recipes": ["fourier_convergence:v1"], "validation_wall_seconds": 120,
            "required_recipe_parameters": {"fourier_convergence:v1": {"orders": orders, "tolerance": TOLERANCE}}, "waivable_kinds": []}
    return {"id": "meent-production-replication:v1", "name": "MEENT production replication: 1100 nm / 70 degrees",
        "goal": "Test reproducible DQN advantage over the frozen HC/refinement controls and historical references on the declared condition",
        "methods": methods, "groups": groups, "input_requirements": requirements,
        "selection": {"provider": "meent_grating", "rule_id": "replication_selection:v2"},
        "analysis": {"provider": "meent_grating", "rule_id": "replication_verdict:v2"},
        "development_seconds": 16*3600, "total_seconds": 24*3600, "worker_seconds": 8*24*3600, "max_workers": 8,
        "diagnostic_priority": 90, "validation_priority": 95,
        "validation_policies": {"development": policy([160, 320]), "confirmation": policy([40, 160, 320, 480]),
                                "reference": policy([40, 160, 320, 480])}}


def registered():
    return [{"id": "meent-workflow-qualification:v1", "name": "MEENT workflow qualification",
        "goal": "Qualify early controls, exact-prefix refinement and coalesced confirmation roles",
        "qualification_only": True,
        "methods": {"P": {"procedure": {"algorithm": "hillclimb", "max_steps": 8, "wall_seconds": 5}},
            "HC": {"procedure": {"algorithm": "hillclimb", "max_steps": 16, "wall_seconds": 5,
                "diagnostics": [{"at_counts": [8], "export_optimizer": False}]}},
            "refine": {"procedure": {"algorithm": "refinement", "max_steps": 8, "wall_seconds": 5,
                "input_binding": {"group": "controls", "slot": "HC", "count": 8}}},
            "selected": {"select_from": ["P"]}},
        "groups": [{"id": "development", "scope": "development", "slots": ["P"], "seeds": [10, 11], "priority": 60},
            {"id": "controls", "scope": "confirmation", "slots": ["HC"], "seeds": [100, 101], "priority": 40},
            {"id": "refinement", "scope": "confirmation", "slots": ["refine"], "seeds": [100, 101], "priority": 40},
            {"id": "confirmation", "scope": "confirmation", "slots": ["P", "selected"], "seeds": [100, 101], "admission": "nomination", "priority": 80}],
        "selection": {"rule_id": "median_objective:v1", "parameters": {"seeds": [10, 11]}},
        "analysis": {"rule_id": "paired_improvement:v1", "parameters": {"minimum_wins": 1}},
        "development_seconds": 120, "total_seconds": 240, "worker_seconds": 60, "max_workers": 4}, production()]
