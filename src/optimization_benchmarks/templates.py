"""A reduced workflow check, deliberately separate from scientific replication."""


def registered():
    return [{"id": "continuous-workflow-qualification:v1", "name": "Continuous workflow qualification",
        "goal": "Qualify nomination, independent seeds and evaluator checks on a bounded continuous problem",
        "qualification_only": True,
        "methods": {"coordinate": {"procedure": {"algorithm": "coordinate", "max_steps": 8, "wall_seconds": 5}},
            "random": {"procedure": {"algorithm": "random", "max_steps": 8, "wall_seconds": 5}},
            "selected": {"select_from": ["coordinate"]}},
        "groups": [{"id": "development", "scope": "development", "slots": ["coordinate"], "seeds": [10, 11], "priority": 60},
            {"id": "controls", "scope": "confirmation", "slots": ["random"], "seeds": [100, 101], "priority": 40},
            {"id": "confirmation", "scope": "confirmation", "slots": ["coordinate", "selected"], "seeds": [100, 101], "admission": "nomination", "priority": 80}],
        "selection": {"rule_id": "median_objective:v1", "parameters": {"seeds": [10, 11]}},
        "analysis": {"rule_id": "paired_improvement:v1", "parameters": {"minimum_wins": 1}},
        "development_seconds": 120, "total_seconds": 240, "worker_seconds": 60, "max_workers": 2,
        "validation_policies": {scope: {"required_recipes": ["analytic_fixtures:v1"], "validation_wall_seconds": 5}
                                for scope in ("development", "confirmation")}}]
