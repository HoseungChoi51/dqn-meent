"""Read-only, bounded access to the campaign's projected agent log."""
import asyncio
import json

from fastapi import Query, Request
from fastapi.responses import Response, StreamingResponse
from starlette.concurrency import run_in_threadpool


def install(app, workspace):
    def check(campaign_id):
        workspace.store.get(campaign_id, "campaign")

    @app.get("/api/campaigns/{campaign_id}/agent-log")
    def history(campaign_id: str, after: int | None = Query(None, ge=0), before: int | None = Query(None, ge=0),
                limit: int = Query(200, ge=1, le=500)):
        check(campaign_id)
        workspace.agent_log.project(campaign_id)
        return workspace.agent_log.page(campaign_id, after=after, before=before, limit=limit)

    @app.get("/api/campaigns/{campaign_id}/agent-log/payloads/{artifact_id}")
    def payload(campaign_id: str, artifact_id: str):
        check(campaign_id)
        return Response(workspace.agent_log.payload(campaign_id, artifact_id), media_type="application/json",
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})

    @app.get("/api/campaigns/{campaign_id}/agent-log/download")
    def download(campaign_id: str):
        check(campaign_id)
        workspace.agent_log.project(campaign_id)
        seq, chunks = workspace.agent_log.download(campaign_id)
        return StreamingResponse(chunks, media_type="application/x-ndjson",
            headers={"Content-Disposition": f'attachment; filename="{campaign_id}-agents.jsonl"',
                     "X-Agent-Log-Cursor": str(seq), "Cache-Control": "no-store"})

    @app.get("/api/campaigns/{campaign_id}/agent-log/stream")
    async def stream(campaign_id: str, request: Request, after: int = Query(0, ge=0)):
        check(campaign_id)
        try:
            cursor = max(after, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            cursor = after

        async def lines():
            nonlocal cursor
            yield "retry: 2000\n\n"
            while not await request.is_disconnected():
                await run_in_threadpool(workspace.agent_log.project, campaign_id)
                page = await run_in_threadpool(workspace.agent_log.page, campaign_id, after=cursor, limit=200)
                for event, line in zip(page["events"], page["lines"]):
                    cursor = event["seq"]
                    yield f"id: {cursor}\nevent: line\ndata: {line.rstrip()}\n\n"
                status = {key: value for key, value in page.items() if key not in {"lines", "events"}}
                yield f"event: status\ndata: {json.dumps(status)}\n\n"
                if not page["events"] or cursor >= page["projected_seq"]:
                    yield ": heartbeat\n\n"
                    await asyncio.sleep(1)

        return StreamingResponse(lines(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
