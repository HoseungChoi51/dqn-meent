"""Local HTTP application for the researcher-guided grating laboratory."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from .coordinator import ResearchCoordinator
from .models import (Model, CampaignInput, CampaignUpdate, TrialInput, ControlInput, ValidationInput,
                     HypothesisInput, ReviewInput, DecisionInput, ResearchInput)
from .service import Workspace, ALGORITHMS
from .store import identifier, now
from .providers import api_spend


class StatusInput(Model):
    status: Literal["proposed", "investigating", "archived", "finalist"]


class ResearchControl(Model):
    action: Literal["stop", "resume"]


class SourceInput(Model):
    campaign_id: str
    title: str = Field(min_length=1, max_length=1000)
    url: str = Field(min_length=1, max_length=2000)
    excerpt: str = Field(default="", max_length=20000)
    supports: str = Field(default="", max_length=5000)


class SearchInput(Model):
    campaign_id: str
    query: str = Field(min_length=2, max_length=500)
    provider: Literal["arxiv", "crossref"] = "arxiv"
    limit: int = Field(default=5, ge=1, le=10)


class IngestInput(Model):
    campaign_id: str
    identifier: str = Field(min_length=1, max_length=2000)


class VerifyInput(Model):
    n_cells: int = Field(default=8, ge=2, le=128)
    seed: int = Field(default=0, ge=0, le=2**32 - 1)


def create_app(directory=None, max_workers=2, start_workers=True):
    workspace = Workspace(directory or os.environ.get("GRATING_WORKSPACE", "runs/workspace"), max_workers=max_workers)
    coordinator = ResearchCoordinator(workspace)

    @asynccontextmanager
    async def lifespan(app):
        if start_workers:
            workspace.start()
        yield
        if start_workers:
            workspace.close()

    app = FastAPI(title="Grating Lab", version="0.1.0", lifespan=lifespan)
    app.state.workspace = workspace
    app.state.coordinator = coordinator

    @app.middleware("http")
    async def local_mutation_origin(request, call_next):
        # Local dashboard writes must originate from this app or the Vite dev UI.
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
            from urllib.parse import urlparse
            parsed = urlparse(origin)
            if parsed.netloc != request.headers.get("host") and origin not in {"http://localhost:5173", "http://127.0.0.1:5173"}:
                return JSONResponse({"detail": "Cross-origin write rejected"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": "Workspace record not found"}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "service": "grating-lab", "max_workers": workspace.max_workers}

    @app.get("/api/state")
    def state(campaign_id: str | None = None):
        from .research import provider_status
        campaigns = workspace.store.list("campaign")
        campaign = workspace.store.get(campaign_id, "campaign") if campaign_id else (campaigns[-1] if campaigns else None)
        current = campaign["id"] if campaign else None
        provider = provider_status()
        response = {"campaigns": campaigns, "campaign": campaign, "algorithms": ALGORITHMS,
                    "settings": {"llm_configured": provider["configured"], "model": provider["model"],
                                 "provider": provider, "max_workers": workspace.max_workers}}
        for kind, name in (("task", "tasks"), ("hypothesis", "hypotheses"), ("trial", "trials"),
                           ("decision", "decisions"), ("message", "messages"), ("action", "actions"),
                           ("source", "sources")):
            rows = workspace.store.list(kind, current) if current else []
            if kind == "task":
                rows = [r for r in rows if not r.get("archived")]
            if kind == "trial":
                rows = [public_trial(r) for r in rows]
            response[name] = rows
        response["research_runs"] = [coordinator.public_run(r) for r in workspace.store.list("research_run", current)] if current else []
        response["events"] = workspace.store.events(current) if current else []
        if campaign:
            response["budget"] = {"allocated_seconds": workspace.allocated_seconds(current),
                "spent_seconds": sum(t.get("execution_seconds", 0) for t in response["trials"]),
                "cap_seconds": campaign["compute_budget_seconds"],
                "llm_spent_usd": sum(api_spend(r.get("usage")) for r in response["research_runs"]),
                "subscription_calls": sum((r.get("usage") or {}).get("subscription_calls", 0) for r in response["research_runs"]),
                "llm_cap_usd": campaign["llm_budget_usd"]}
        return response

    @app.get("/api/events")
    async def events(request: Request, campaign_id: str | None = None, after: int = 0):
        async def stream():
            last = after
            try:
                last = max(last, int(request.headers.get("last-event-id", "0")))
            except ValueError:
                pass
            yield "retry: 2000\n\n"
            heartbeat = 0
            while not await request.is_disconnected():
                rows = workspace.store.events(campaign_id, after=last, limit=500)
                if rows:
                    last = rows[-1]["id"]
                    yield f"id: {last}\nevent: update\ndata: {json.dumps({'last_event_id': last})}\n\n"
                else:
                    heartbeat += 1
                    if heartbeat % 15 == 0:
                        yield ": heartbeat\n\n"
                await asyncio.sleep(1)
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/campaigns", status_code=201)
    def create_campaign(body: CampaignInput):
        return workspace.create_campaign(body)

    @app.put("/api/campaigns/{campaign_id}")
    def update_campaign(campaign_id: str, body: CampaignUpdate):
        return workspace.update_campaign(campaign_id, body)

    @app.get("/api/campaigns/{campaign_id}/charters")
    def charters(campaign_id: str):
        workspace.store.get(campaign_id, "campaign")
        return workspace.store.list("charter", campaign_id)

    @app.post("/api/trials", status_code=201)
    def create_trial(body: TrialInput):
        return public_trial(workspace.create_trial(body))

    @app.post("/api/trials/{trial_id}/control")
    def control(trial_id: str, body: ControlInput):
        return public_trial(workspace.control(trial_id, body))

    @app.post("/api/trials/{trial_id}/validate", status_code=201)
    def validate(trial_id: str, body: ValidationInput):
        return public_trial(workspace.validate_trial(trial_id, body))

    @app.get("/api/trials/{trial_id}/metrics")
    def metrics(trial_id: str):
        return workspace.metrics(trial_id)

    @app.get("/api/trials/{trial_id}/logs")
    def logs(trial_id: str):
        workspace.store.get(trial_id, "trial")
        path = workspace.job_dir(trial_id) / "worker.log"
        return {"text": path.read_text(errors="replace")[-20000:] if path.exists() else ""}

    @app.get("/api/trials/{trial_id}/artifacts/{filename}")
    def artifact(trial_id: str, filename: str):
        trial = workspace.store.get(trial_id, "trial")
        if filename not in {"spec.json", "result.json", "archive.json", "metrics.jsonl", "progress.json"}:
            raise KeyError(filename)
        path = workspace.job_dir(trial_id) / filename
        if filename == "archive.json" and not path.is_file():
            return JSONResponse(trial.get("progress", {}).get("archive", []),
                                headers={"Content-Disposition": 'attachment; filename="archive.json"'})
        if not path.is_file():
            raise KeyError(filename)
        return FileResponse(path, filename=filename)

    @app.post("/api/hypotheses", status_code=201)
    def create_hypothesis(body: HypothesisInput):
        campaign = workspace.store.get(body.campaign_id, "campaign")
        for parent in body.parent_ids:
            if workspace.store.get(parent, "hypothesis")["campaign_id"] != body.campaign_id:
                raise ValueError("Parents must belong to this campaign")
        record = {**body.model_dump(), "id": identifier("hypothesis"), "created_at": now(),
                  "charter_version": campaign["version"], "origin": "researcher", "reviews": [],
                  "claim_level": "rationale_only", "executable": body.algorithm in {a["id"] for a in ALGORITHMS}}
        if body.status == "finalist":
            from .confirmation import nominate_finalist
            record = nominate_finalist(record)
        return workspace.store.put("hypothesis", record, "hypothesis.created")

    @app.post("/api/hypotheses/{hypothesis_id}/review")
    def review(hypothesis_id: str, body: ReviewInput):
        with workspace.lock:
            record = workspace.store.get(hypothesis_id, "hypothesis")
            record.setdefault("reviews", []).append({"id": identifier("review"), "text": body.text,
                                                       "author": "researcher", "created_at": now()})
            return workspace.store.put("hypothesis", record, "hypothesis.reviewed")

    @app.post("/api/hypotheses/{hypothesis_id}/status")
    def hypothesis_status(hypothesis_id: str, body: StatusInput):
        with workspace.lock:
            record = workspace.store.get(hypothesis_id, "hypothesis")
            record["status"] = body.status
            if body.status == "finalist":
                from .confirmation import nominate_finalist
                record = nominate_finalist(record)
            return workspace.store.put("hypothesis", record, "hypothesis.status")

    @app.post("/api/hypotheses/{hypothesis_id}/verify")
    def verify_hypothesis(hypothesis_id: str, body: VerifyInput):
        from .custom_optimizer import CandidateError, SandboxUnavailable, verify_custom_source
        record = workspace.store.get(hypothesis_id, "hypothesis")
        if not record.get("source"):
            raise ValueError("Add custom source on a new hypothesis or fork before verifying")
        try:
            verification = verify_custom_source(record["source"], n_cells=body.n_cells, seed=body.seed,
                                                 parameters=record.get("algorithm_config", {}).get("parameters", {}))
        except (CandidateError, SandboxUnavailable) as exc:
            record.update(implementation_status="verification_failed", verification_error=str(exc), executable=False)
            workspace.store.put("hypothesis", record, "hypothesis.verification_failed")
            raise ValueError(str(exc)) from exc
        record.update(algorithm="custom", implementation_status="verified", verification=verification,
                      executable=True, verified_at=now())
        return workspace.store.put("hypothesis", record, "hypothesis.verified")

    @app.post("/api/research", status_code=202)
    def research(body: ResearchInput):
        return coordinator.start(body)

    @app.post("/api/research_runs/{run_id}/control")
    def research_control(run_id: str, body: ResearchControl):
        return coordinator.control(run_id, body.action)

    @app.post("/api/decisions/{decision_id}/resolve")
    def decision(decision_id: str, body: DecisionInput):
        return coordinator.resolve(decision_id, body.choice, body.comment)

    @app.post("/api/sources", status_code=201)
    def source(body: SourceInput):
        workspace.store.get(body.campaign_id, "campaign")
        from urllib.parse import urlparse
        if urlparse(body.url).scheme not in {"https", "http"}:
            raise ValueError("Source URL must use HTTP or HTTPS")
        record = {**body.model_dump(), "id": identifier("source"), "created_at": now(),
                  "verification": "researcher_supplied_unverified"}
        return workspace.store.put("source", record, "source.created")

    @app.post("/api/sources/search")
    def search_sources(body: SearchInput):
        from .evidence import EvidenceError, search_literature
        workspace.store.get(body.campaign_id, "campaign")
        try:
            result = search_literature(body.query, limit=body.limit, provider=body.provider)
        except EvidenceError as exc:
            raise ValueError(str(exc)) from exc
        for source in result.get("sources", []):
            source["id"] = body.campaign_id + "_" + source["id"]
            source.update(campaign_id=body.campaign_id, created_at=now())
            workspace.store.put("source", source, "source.retrieved")
        return result

    @app.post("/api/sources/ingest")
    def ingest_source(body: IngestInput):
        from .evidence import EvidenceError, ingest_source
        workspace.store.get(body.campaign_id, "campaign")
        try:
            source = ingest_source(body.identifier)
        except EvidenceError as exc:
            raise ValueError(str(exc)) from exc
        source["id"] = body.campaign_id + "_" + source["id"]
        source.update(campaign_id=body.campaign_id, created_at=now())
        return workspace.store.put("source", source, "source.retrieved")

    @app.get("/api/campaigns/{campaign_id}/analysis")
    def analysis(campaign_id: str):
        workspace.store.get(campaign_id, "campaign")
        from .analysis import analyze_trials
        trials = workspace.store.list("trial", campaign_id)
        return analyze_trials(trials, {t["id"]: workspace.metrics(t["id"]) for t in trials},
                              workspace.store.list("task", campaign_id))

    @app.get("/api/campaigns/{campaign_id}/export")
    def export(campaign_id: str):
        campaign = workspace.store.get(campaign_id, "campaign")
        markdown = export_markdown(workspace, campaign)
        return Response(markdown, media_type="text/markdown",
                        headers={"Content-Disposition": f'attachment; filename="grating-lab-{campaign_id}.md"'})

    dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}")
    def frontend(path: str):
        if path.startswith("api/"):
            return JSONResponse({"detail": "API route not found"}, status_code=404)
        if (dist / "index.html").is_file():
            candidate = (dist / path).resolve()
            if candidate.is_relative_to(dist) and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")
        return Response("Grating Lab API is running. Build the dashboard with npm ci && npm run build in frontend/.", media_type="text/plain")

    return app


def public_trial(trial):
    return {k: v for k, v in trial.items() if k not in {"pid", "process_identity"}}


def export_markdown(workspace, campaign):
    campaign_id = campaign["id"]
    lines = [f"# {campaign['name']}", "", campaign["objective"], "",
        f"Charter version: {campaign['version']}. Exported: {now()}.", "",
        "## Experiment charter", "", "```json", json.dumps(campaign, indent=2), "```", "",
        "## Tasks", ""]
    for task in workspace.store.list("task", campaign_id):
        lines += [f"### {task['name']} ({task['split']})", "", "```json", json.dumps(task, indent=2), "```", ""]
    lines += ["## Hypotheses and evidence", ""]
    for h in workspace.store.list("hypothesis", campaign_id):
        lines += [f"### {h['title']}", "", f"Origin: {h.get('origin', 'unknown')}; status: {h.get('status', 'proposed')}.", "",
                  h.get("mechanism", ""), "", h.get("rationale", ""), "",
                  "```json", json.dumps(h, indent=2), "```", ""]
    lines += ["## Trials", "", "Partial and stopped trials are retained. Search scores are not automatically Fourier-converged.", ""]
    for trial in workspace.store.list("trial", campaign_id):
        lines += [f"### {trial['algorithm']} — {trial['id']}", "", "```json",
                  json.dumps(public_trial(trial), indent=2), "```", ""]
    lines += ["## Research decisions", ""]
    for d in workspace.store.list("decision", campaign_id):
        lines += [f"### {d['title']}", "", d.get("context", ""), "", "```json", json.dumps(d, indent=2), "```", ""]
    lines += ["## Research notebook", ""]
    for message in workspace.store.list("message", campaign_id):
        lines += [f"### {message['role']} · {message['created_at']}", "", message["content"], ""]
    lines += ["## Model execution and accounting", "", "Subscription allowance and paid API charges are recorded separately.", ""]
    for run in workspace.store.list("research_run", campaign_id):
        lines += [f"### {run['id']}", "", "```json", json.dumps({
            "status": run["status"], "provider": run.get("request", {}).get("provider_snapshot"),
            "usage": run.get("usage", {}), "trace": run.get("trace", [])}, indent=2), "```", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", default="runs/workspace")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--workers", type=int, default=2, help="Maximum concurrent numerical trials")
    args = parser.parse_args(argv)
    import uvicorn
    uvicorn.run(create_app(args.directory, max_workers=args.workers), host=args.host, port=args.port,
                timeout_graceful_shutdown=3)


if __name__ == "__main__":
    main()
