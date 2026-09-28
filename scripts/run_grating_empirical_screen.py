#!/usr/bin/env python3
"""Operator-delivered check of a real Luna proposal, with all interventions labeled.

This is bounded test scaffolding while automatic candidate handoff is being fixed.
It uses ordinary admitted commands and actual numerical workers, not result fixtures.
"""
import json
import time
from pathlib import Path

import httpx


def main():
    cid = "campaign_grating_inverse_luna_20260928"
    root = Path("runs/discovery/grating-luna-20260928")
    with httpx.Client(base_url="http://127.0.0.1:8791", timeout=30) as c:
        state = c.get("/api/state", params={"campaign_id": cid}).raise_for_status().json()
        task = state["tasks"][0]
        view = c.get(f"/api/campaigns/{cid}/discovery").raise_for_status().json()
        proposals = [(s, candidate) for s in view["steps"] for artifact in s["result"]["artifacts"]
                     if artifact["kind"] == "candidate_batch" for candidate in artifact["content"]["candidates"]
                     if candidate.get("algorithm") == "hillclimb"]
        step, proposal = proposals[0]
        assert proposal["parameter_space"]["restart_patience"] == [8, 16, 32]
        def command(identity, operation, payload):
            response = c.post("/api/v1/commands", json={"id": identity, "campaign_id": cid,
                "expected_revision": state["campaign"]["version"], "operation": operation, "payload": payload})
            response.raise_for_status()
            return response.json()["outcome"]
        protocol = {"origin": "development-run operator delivery of a real model proposal; not autonomous assessment launch",
            "source_step_id": step["id"], "proposal": proposal, "seeds": [0, 1], "evaluations": 96,
            "wall_seconds_per_trial": 45, "comparison": "Uniform random versus hill climbing at patience 8, 16, 32",
            "prediction": proposal["predictions"], "limitations": ["Two seeds, one operating condition, exploratory Fourier order 15",
                "The automatic candidate batch was rejected for a missing literature-map reference; original responses remain intact."]}
        (root / "empirical-protocol.json").write_text(json.dumps(protocol, indent=2) + "\n")
        ids = []
        for algorithm, patience in [("random", None), ("hillclimb", 8), ("hillclimb", 16), ("hillclimb", 32)]:
            for seed in (0, 1):
                parameters = {} if patience is None else {**proposal["algorithm_config"], "restart_patience": patience}
                result = command(f"grating_screen_{algorithm}_{patience or 0}_{seed}", "trial.create", {
                    "task_id": task["id"], "algorithm": algorithm, "algorithm_config": parameters,
                    "seed": seed, "max_steps": 96, "wall_seconds": 45,
                    "question": f"Operator-delivered test of Luna proposal {step['id']}: does restart hill climbing beat random on both shared seeds at 96 evaluations?"})
                ids.append(result["trial_id"])
        print(json.dumps({"launched_real_trials": ids}), flush=True)
        deadline = time.monotonic() + 170
        while True:
            state = c.get("/api/state", params={"campaign_id": cid}).raise_for_status().json()
            trials = [t for t in state["trials"] if t["id"] in ids]
            if len(trials) == 8 and all(t["status"] not in {"queued", "running", "pausing", "stopping"} for t in trials):
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("Empirical screen still running; inspect existing trials rather than relaunching")
            time.sleep(1)
        rows = [{"trial_id": t["id"], "algorithm": t["algorithm"], "parameters": t["algorithm_config"], "seed": t["seed"],
                 "status": t["status"], "result": t.get("result"), "execution_seconds": t.get("execution_seconds")} for t in trials]
        (root / "empirical-results.json").write_text(json.dumps(rows, indent=2) + "\n")
        compact = [{**{k: row[k] for k in ("trial_id", "algorithm", "parameters", "seed", "status")},
                    "best_efficiency": (row["result"] or {}).get("best_objective", (row["result"] or {}).get("best_efficiency")),
                    "evaluations": (row["result"] or {}).get("evaluations")} for row in rows]
        message = "Development-run operator delivered the empirical screen for the real Luna hill-climbing proposal because its automatic batch handoff failed. This was operator-assisted, not an autonomous assessment. All eight ordinary numerical trials actually ran. Predeclared prediction: at least one patience setting (8,16,32) beats uniform random on both shared seeds at 96 evaluations. Here are the actual measurements: " + json.dumps(compact) + ". Please now return a substantive review or synthesis artifact: test the prediction, discuss the mechanism and counterevidence, and state an explicitly REVISED hypothesis and next test. Do not claim superiority beyond this tiny experiment or reject training-heavy families. Do not re-run problem analysis or literature search. Keep the response focused so the researcher can inspect the dialogue and how the hypothesis changes with real evidence. Leave the larger campaign open for researcher review."
        command("grating_empirical_review_request", "research.start", {"mode": "discuss", "message": message})
        print(json.dumps({"measurements": compact, "review_requested": True}), flush=True)


if __name__ == "__main__":
    main()
