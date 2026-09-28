"""Isolated HTTP qualification; manager inference and literature retrieval are fixtures.

Commands, receipts, persistence and browser recovery use the actual application.
Never point this fixture at a live workspace. No model or external source is called.
"""
import argparse
import os
import time

from optimization_framework.api.app import create_app
from optimization_framework.contracts.commands import Command
from optimization_framework.contracts.requests import CampaignInput, TaskInput
from optimization_framework.research import evidence, coordinator
from optimization_framework.storage.sqlite import now


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--frontend-directory", required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    os.environ["GRATING_LLM_DISABLED"] = "true"
    app = create_app(args.directory, start_workers=False, frontend_directory=args.frontend_directory)
    workspace = app.state.workspace
    workspace.manager._thread = lambda run: None
    coordinator.provider_status = lambda: {"configured": True, "qualification_fixture": True}
    if not workspace.store.list("campaign"):
        campaign = workspace.create_campaign(CampaignInput(name="Research controls HTTP fixture",
            tasks=[TaskInput(name="Quadratic", problem_id="bounded_continuous")]))
        workspace.commands.execute(Command(id="fixture_discussion", campaign_id=campaign["id"],
            operation="research.start", expected_revision=1, payload={"message": "Fixture interrupted discussion"}))
        run = workspace.store.list("research_run")[0]
        run.update(status="interrupted", checkpoint={"stage": "fixture"})
        workspace.store.put("research_run", run)
        workspace.store.put("decision", {"id": "fixture_decision", "campaign_id": campaign["id"],
            "charter_version": 1, "title": "Choose the next comparison", "context": "Fixture decision; no numerical work is requested.",
            "options": [{"id": "defer", "label": "Defer this comparison"}], "status": "pending", "created_at": now()})

    def paper(identifier):
        time.sleep(1)
        return {"id": "fixture_paper", "title": "Fixture source for durable retrieval", "url": "https://example.org/paper",
                "verification": "fixture_metadata", "identifier": identifier}

    evidence.ingest_source = paper
    evidence.search_literature = lambda query, **kwargs: {"provider": "fixture", "sources": [paper(query)]}
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
