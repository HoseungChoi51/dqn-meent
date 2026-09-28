"""Bounded live-model acceptance of the independent implementation workflow."""
import argparse
import json
from pathlib import Path

from dqn_meent.implementations.models import ImplementationSpec, JobRequest
from dqn_meent.implementations.service import ImplementationService
from dqn_meent.workspace.providers import provider_status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--live-model", action="store_true", required=True)
    args = parser.parse_args()
    provider = provider_status()
    if not provider["configured"] or provider["billing_mode"] != "subscription":
        parser.error("This zero-API-dollar acceptance check requires an explicitly enabled subscription provider")
    spec = ImplementationSpec(name="Acceptance reference: seeded incumbent one-bit search",
        mechanism="Start with a seeded uniformly random binary design. After observations, propose exactly one uniformly random bit flip from the highest-scoring observed design. Keep the earlier incumbent on tied or lower scores. Checkpoint the incumbent, score, and full NumPy RNG state. This is an infrastructure reference, not a new research algorithm.",
        acceptance_criteria=["Every post-initial proposal differs by exactly one bit from the best observed incumbent",
                             "Only strictly improving observations replace the incumbent",
                             "Seeded replay and checkpoint restoration preserve subsequent proposals"],
        dependencies={"numpy": "2.5.3"}, n_cells_max=16,
        behavior_checks=[{"name": "Retain the incumbent across deterioration and ties", "assertion": "one_bit_from_incumbent",
                          "efficiencies": [.5, .2, .5, .9, .3, .9, .1]}])
    service = ImplementationService(args.directory)
    job = service.submit(JobRequest(workspace_id="implementation-acceptance", campaign_id="isolated-acceptance",
        grant_id="acceptance-reference-grant", idempotency_key="reference-build-v1", spec=spec,
        max_calls=4, max_attempts=2, compute_seconds=240, api_budget_usd=0))
    result = service.run_job(job["id"])
    summary = {"status": result["status"], "job_id": result["id"], "version_id": result.get("version_id"),
               "error": result.get("error"), "compute_seconds": result["compute_seconds"], "usage": result["usage"],
               "attempts": [{"number": a["number"], "report": a.get("report"), "review": a.get("review")} for a in result["attempts"]]}
    (args.directory / "acceptance.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({key: value for key, value in summary.items() if key != "attempts"}, indent=2))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
