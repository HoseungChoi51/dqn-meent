#!/usr/bin/env python3
"""Start one bounded scientific campaign through the normal HTTP command API.

No model, literature, implementation review or numerical result is substituted.
Reusing the command IDs returns the original admitted requests.
"""
import argparse
import json

import httpx


OBJECTIVE = """Find an effective optimizer for the 1D binary silicon grating inverse problem:
maximize absolute transmitted +1 diffraction efficiency at 1100 nm and 50 degrees,
with 64 binary cells, 325 nm thickness and the supplied MEENT RCWA evaluator.
This first bounded research cycle must establish a scientific argument with real literature
and empirical feedback, not only enumerate algorithms. Use independent analysis, then
one focused literature investigator to search and read 1–2 relevant primary sources;
then two independent methodology specialists (one may explore cross-domain methods).
Each should propose 1–2 concrete, falsifiable hypotheses. Include at least one runnable
bundled optimizer as a scientific baseline; distinguish novel changes requiring implementation.
Have a critic challenge the proposals and choose a small empirical screen: at most two
method assessments, each 3 distinct hyperparameter configurations and 2 shared seeds,
96 evaluations per trial and at most 45 seconds per trial. A parameter-free random baseline
may use one configuration with an explicit tuning waiver. Total experiment allocation is
600 seconds. Do not reject DQN or other training-heavy families based on this short horizon.
The campaign manager is authorized to prepare and launch these bounded assessments via
the assessment tools and await the actual results. Commissioning new optimizer code is
outside this first cycle; missing code stays an explicit requirement. After measurements,
assign an independent review and refine at least one candidate with explicit parent IDs
and revision_basis explaining what evidence changed the hypothesis. Preserve disagreement,
negative results and under-evaluation. Finish this bounded cycle with an honest synthesis
and the most useful next experiment. Fourier order 15 is exploratory; higher-order
convergence and broader seed replication are required before any final physical claim.
Write substantive dialogue: state the question, evidence, proposed mechanism, criticism
and response, so a researcher can follow how the hypotheses become more plausible.
Keep this first cycle compact (about 20–30 calls); don't exhaust the allowance on repeated
searches or design an extensive study before the first small empirical screen."""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8791")
    args = parser.parse_args()
    with httpx.Client(base_url=args.url, timeout=30) as client:
        state = client.get("/api/state").raise_for_status().json()
        provider = state["settings"]["provider"]
        if not provider["configured"] or not provider["enabled"] or provider["model"] != "gpt-6-luna":
            raise SystemExit("Enable the explicitly selected gpt-6-luna provider before this real-model run")
        campaign_id = "campaign_grating_inverse_luna_20260928"
        def command(identity, operation, payload, revision=1):
            response = client.post("/api/v1/commands", json={"id": identity, "campaign_id": campaign_id,
                "expected_revision": revision, "operation": operation, "payload": payload})
            if response.is_error:
                raise RuntimeError(response.text)
            return response.json()
        command("grating_luna_campaign", "campaign.create", {
            "name": "1D grating inverse design — finding an efficient optimizer",
            "objective": OBJECTIVE, "autonomy": "delegated", "compute_budget_seconds": 900,
            "validation_reserve_seconds": 180, "llm_budget_usd": 5,
            "delegated_trial_seconds": 45, "implementation_compute_budget_seconds": 0,
            "tasks": [{"name": "Silicon grating: 1100 nm, +1 order at 50 degrees", "problem_id": "meent_grating",
                "physics": {"n_cells": 64, "fourier_order": 15}}]}, revision=0)
        state = client.get("/api/state", params={"campaign_id": campaign_id}).raise_for_status().json()
        result = command("grating_luna_discovery", "discovery.start", {"task_id": state["tasks"][0]["id"],
            "objective": OBJECTIVE, "model_call_limit": 48, "max_calls_per_task": 16, "max_output_tokens": 6000,
            "max_concurrent_tasks": 2, "max_tasks": 40, "max_rounds": 3, "source_request_limit": 24,
            "experiment_compute_seconds": 600, "synthesis_call_reserve": 5})
        print(json.dumps({"campaign_id": campaign_id, "outcome": result["outcome"], "model": provider["model"],
            "billing_mode": provider["billing_mode"], "model_outputs": "actual provider calls; no fixtures"}, indent=2))


if __name__ == "__main__":
    main()
