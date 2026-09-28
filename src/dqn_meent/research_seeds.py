"""MEENT research seeds and historical probe heuristic; not framework policy."""
import copy
import math
import statistics
import uuid
from collections import defaultdict

def _identifier(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:16]}"

SOURCES = [
    {"id": "bocs", "title": "Bayesian Optimization of Combinatorial Structures",
     "url": "https://proceedings.mlr.press/v80/baptista18a.html",
     "verification": "curated_reference", "supports": "Combinatorial surrogate optimization precedent; not evidence of grating performance."},
    {"id": "meent", "title": "MEENT: Differentiable Electromagnetic Simulator for Machine Learning",
     "url": "https://arxiv.org/abs/2406.12904", "verification": "curated_reference",
     "supports": "Differentiable simulation capability; gradients and binary projection still require verification."},
    {"id": "coscientist", "title": "Accelerating scientific discovery with Co-Scientist",
     "url": "https://www.nature.com/articles/s41586-026-10644-y",
     "verification": "researcher_supplied_reference",
     "supports": "Specialized hypothesis generation, reflection, ranking, evolution, and meta-review precedent."},
    {"id": "uncertainty", "title": "Deep Reinforcement Learning at the Edge of the Statistical Precipice",
     "url": "https://arxiv.org/abs/2108.13264", "verification": "curated_reference",
     "supports": "Uncertainty and performance profiles when comparing stochastic algorithms."},
]


def seed_hypotheses() -> list[dict]:
    """Return editable starting dossiers. These are hypotheses, not measured wins."""
    entries = [
        ("random", "Random search", "baseline", "Independent binary samples establish a cost-matched reference.",
         "Measures whether elaborate search improves over uninformed sampling.",
         "No search mechanism is assumed to outperform random sampling.",
         "Use matched seeds, task, fidelity, and solver-call limits.", 0, [], "baseline", {}),
        ("hillclimb", "Restart hill climbing", "local", "Accept improving bit flips and restart after stagnation.",
         "Cheap local proposals can exploit useful neighboring geometries.",
         "Useful local neighborhoods exist; one-bit traps may prevent progress.",
         "Record plateau lengths and improvement rates on a representative development task.", 0, [], "baseline", {}),
        ("dqn", "DQN reference", "learned", "Learn state-dependent bit-flip actions from repeated interaction.",
         "A learned policy may reuse experience when revisiting related structures.",
         "The policy receives enough training to improve over its exploration policy.",
         "Measure replay warm-up, loss, and greedy-policy behavior before judging policy quality.", 512, ["uncertainty"], "baseline", {}),
        ("block_tabu", "Adaptive block moves with tabu memory", "coordinated", "Alternate local flips with coordinated contiguous moves and avoid recent designs.",
         "Coordinated changes may escape one-bit traps while tabu memory reduces repeated solver work.",
         "Stalled designs contain improving coordinated moves; useful effects need not be contiguous.",
         "Compare matched one-bit and block neighborhoods around the same archived stalled designs.", 0, [], "proposed adaptation", {"max_block_size": 8}),
        ("surrogate", "Surrogate-guided discrete search", "surrogate", "Fit a model to measured designs, score a candidate pool, and refine selected proposals locally.",
         "A surrogate could use recurring binary interactions to spend fewer expensive solver calls.",
         "Out-of-sample ranking must beat chance and startup cost must be repaid.",
         "Evaluate held-out design rankings from development data, then run a small prospective probe.", 32, ["bocs"], "adaptation", {"warmup": 32}),
        ("population", "Population search and pattern recombination", "population", "Maintain diverse binary designs and recombine fragments with adaptive mutation.",
         "Reusable cell groups could preserve useful structure while exploring coordinated changes.",
         "Fragments retain value across contexts; electromagnetic interactions may be strongly nonlocal.",
         "Compare offspring from selected and shuffled fragments at matched solver cost.", 16, [], "proposed adaptation", {"population_size": 16}),
        ("fourier", "Fourier-informed binary proposals", "spectral", "Bias proposal sampling toward spatial-frequency components associated with the target diffraction order.",
         "The target diffraction order suggests a physically motivated proposal prior worth testing.",
         "Fourier descriptors correlate with full RCWA efficiency; this has not been established.",
         "Compare matched informed and uninformed proposals with exact binary RCWA evaluation.", 0, [], "proposed research direction", {}),
        ("relaxed_gradient", "Relaxed gradients with binary repair", "differentiable", "Optimize a continuous relaxation, project to feasible binary cells, and repair discretization losses.",
         "Gradients may coordinate many variables at lower cost than enumerating discrete neighbors.",
         "Stable derivatives and low projection damage are prerequisites; forward-only workers do not provide this method.",
         "Check finite-difference derivatives and binary projection damage before implementing a full optimizer.", 0, ["meent"], "proposed adaptation", {}),
        ("portfolio", "Adaptive optimizer portfolio", "portfolio", "Allocate calls among complementary proposal mechanisms using measured marginal improvement.",
         "Different methods may be effective in different regimes or phases of search.",
         "Complementary behavior repays switching and model-maintenance overhead.",
         "Inspect crossing cost-quality curves before testing an adaptive allocation rule.", 0, [], "proposed adaptation", {}),
    ]
    cards = []
    for algorithm, title, group, mechanism, rationale, assumption, check, startup, sources, novelty, config in entries:
        cards.append({
            "id": f"seed_{algorithm}", "title": title, "algorithm": algorithm,
            "algorithm_config": config, "mechanism": mechanism, "rationale": rationale,
            "assumptions": [f"Unverified: {assumption}"], "predictions": [],
            "failure_modes": [assumption], "risks": [assumption], "cheapest_check": check,
            "expected_startup_calls": startup, "novelty": novelty, "parent_ids": [],
            "sources": [copy.deepcopy(s) for s in SOURCES if s["id"] in sources],
            "evidence": [], "status": "baseline" if novelty == "baseline" else "proposed",
            "origin": "curated", "protocol": mechanism, "source": None, "reviews": [],
            "diversity_group": group, "claim_level": "rationale_only",
            "executable": algorithm in {"random", "hillclimb", "dqn", "block_tabu", "surrogate", "population"},
        })
    return cards


def select_probe(tasks: list[dict], hypotheses: list[dict], trials: list[dict]) -> dict:
    """Pick a development probe using an inspectable, uncalibrated rubric.

    Discrimination and mechanism fit take precedence over low absolute scores.
    No trial is run by this function, and locked test configurations are excluded.
    """
    from optimization_framework.research.engine import _development, _metric, _curve, _split, _finite
    groups = {str(h.get("diversity_group", h.get("algorithm", ""))) for h in hypotheses}
    records = []
    for task in tasks:
        if not _development(task):
            continue
        task_id = task.get("id", task.get("task_id"))
        observations = [t for t in trials if t.get("task_id") == task_id and not t.get("locked") and _split(t) not in {"test", "confirmation", "heldout"}]
        by_algorithm: dict[str, list[float]] = defaultdict(list)
        plateaus, costs = [], []
        unreliable = task.get("solver_reliable") is False or task.get("numerically_reliable") is False
        for trial in observations:
            value = _metric(trial, "best_efficiency")
            if value is not None:
                by_algorithm[str(trial.get("algorithm", "unknown"))].append(value)
            curve = _curve(trial)
            if len(curve) >= 4:
                tail = curve[max(0, len(curve) * 2 // 3):]
                plateaus.append(max(tail) - min(tail) < 1e-3)
            calls, seconds = _metric(trial, "solver_calls"), _metric(trial, "elapsed_seconds")
            if calls and seconds is not None:
                costs.append(seconds / calls)
            validation = trial.get("validation", {}) or {}
            if isinstance(validation, dict) and (validation.get("converged") is False or validation.get("reliable") is False):
                unreliable = True
        means = {name: statistics.mean(values) for name, values in by_algorithm.items()}
        best = max(means.values(), default=None)
        spread = max(means.values()) - min(means.values()) if len(means) > 1 else 0.0
        plateau = statistics.mean(plateaus) if plateaus else 0.0
        # Distinguish a difficult useful probe from a jointly flat, hopeless one.
        uniformly_stalled = len(means) >= 2 and plateau > .75 and spread < .005 and best is not None and best < .05
        mechanism_fit = 0.0
        reasons = []
        if plateau and groups & {"coordinated", "block_tabu", "local"}:
            mechanism_fit += plateau
            reasons.append("Observed plateaus make coordinated-move escape a discriminating question.")
        if len(observations) >= 3 and groups & {"surrogate", "learned", "dqn"}:
            mechanism_fit += .35
            reasons.append("Existing development trajectories can support startup and prediction checks.")
        novelty = 1.0 / (1 + len(observations))
        cost = statistics.median(costs) if costs else _finite(task.get("estimated_seconds_per_call"))
        difficulty = 1 - best if best is not None else 0.0
        score = 3 * min(spread * 4, 1) + mechanism_fit + .4 * novelty + .35 * difficulty
        if best is not None and best > .95:
            score -= 1.0
            reasons.append("Existing searches already nearly solve this case; keep it as a sanity check.")
        if uniformly_stalled:
            score -= 2.0
            reasons.append("All measured approaches are flat and indistinguishable; low efficiency alone does not make this informative.")
        if unreliable:
            score -= 100
            reasons.append("Numerical reliability is unresolved; verify fidelity before using this case to rank strategies.")
        if spread:
            reasons.append(f"Observed between-algorithm mean spread is {spread:.4f}; task/seed/budget matching still needs checking.")
        if not observations:
            reasons.append("No measurements: this is a coverage pilot, not evidence that the task is difficult.")
        elif not reasons:
            reasons.append("A moderately difficult development case may reveal differences; available evidence remains limited.")
        records.append({"task_id": task_id, "title": task.get("name", task.get("label", str(task_id))),
                        "score": score, "rationale": " ".join(reasons), "seconds_per_call": cost,
                        "metrics": {"algorithm_means": means, "spread": spread, "plateau_fraction": plateau,
                                    "observations": len(observations), "uniformly_stalled": uniformly_stalled,
                                    "numerically_reliable": not unreliable}, "task": task})
    if not records:
        return {"task_id": None, "status": "needs_researcher", "rationale": "Add an unlocked development configuration before selecting a probe.", "alternatives": [], "scope": "development_only"}
    known_costs = [r["seconds_per_call"] for r in records if r["seconds_per_call"] is not None and r["seconds_per_call"] > 0]
    if known_costs:
        scale = statistics.median(known_costs)
        for record in records:
            if record["seconds_per_call"] is not None:
                record["score"] -= .2 * math.log1p(record["seconds_per_call"] / scale)
    records.sort(key=lambda r: (-r["score"], str(r["task_id"])))
    winner = records[0]
    alternatives = [{k: v for k, v in r.items() if k != "task"} for r in records[1:4]]
    if not winner["metrics"]["numerically_reliable"]:
        return {"task_id": None, "status": "needs_validation", "rationale": winner["rationale"],
                "alternatives": [{k: v for k, v in winner.items() if k != "task"}] + alternatives, "scope": "development_only"}
    question = "Which strategy improves validated binary efficiency at matched cost on this development configuration?"
    if winner["metrics"]["plateau_fraction"] and groups & {"coordinated", "block_tabu"}:
        question = "Do coordinated moves escape measured one-bit plateaus more often than matched single-bit moves?"
    return {**{k: v for k, v in winner.items() if k != "task"}, "status": "proposed", "question": question,
            "alternatives": alternatives, "scope": "one development configuration; no generalization claim",
            "cost_estimate": {"seconds_per_solver_call": winner["seconds_per_call"], "basis": "observed median" if winner["seconds_per_call"] is not None else "unknown"},
            "selection_method": "transparent heuristic; not a calibrated information-gain estimate"}
