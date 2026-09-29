"""Campaign controls and a same-origin HTTP/WebSocket gateway for code-server."""
from __future__ import annotations

import asyncio
import logging
import os
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, Request, WebSocket
from fastapi.responses import StreamingResponse

from .development import WorkspaceCreate, WorkspaceMessage, WorkspaceControl, WorkspaceValidate


def install(app, workspace):
    development = workspace.pi.development

    @app.get("/api/campaigns/{campaign_id}/implementation-workspaces")
    def list_workspaces(campaign_id: str):
        workspace.store.get(campaign_id, "campaign")
        return development.view(campaign_id)

    @app.post("/api/campaigns/{campaign_id}/implementation-workspaces")
    def create(campaign_id: str, body: WorkspaceCreate):
        workspace.store.get(campaign_id, "campaign")
        return development.create(campaign_id, body.model_dump(), actor="researcher")

    @app.get("/api/campaigns/{campaign_id}/implementation-workspaces/{identity}")
    def inspect(campaign_id: str, identity: str, after: int = 0):
        workspace.store.get(campaign_id, "campaign")
        return development.events(campaign_id, identity, max(0, after))

    @app.post("/api/campaigns/{campaign_id}/implementation-workspaces/{identity}/messages")
    def message(campaign_id: str, identity: str, body: WorkspaceMessage):
        return development.message(campaign_id, identity, body.model_dump(), actor="researcher")

    @app.post("/api/campaigns/{campaign_id}/implementation-workspaces/{identity}/controls")
    def control(campaign_id: str, identity: str, body: WorkspaceControl):
        return development.control(campaign_id, identity, body.model_dump(), actor="researcher")

    @app.post("/api/campaigns/{campaign_id}/implementation-workspaces/{identity}/validations")
    def validate(campaign_id: str, identity: str, body: WorkspaceValidate):
        return development.validate(campaign_id, identity, body.model_dump())

    def port(identity):
        record = workspace.store.get(identity, "development_workspace")
        state = development.driver.inspect(record)
        if not state or not state.get("running") or state.get("paused") or not state.get("port"):
            raise HTTPException(503, "Implementation workspace is starting or paused")
        return state["port"]

    excluded = {"host", "origin", "connection", "upgrade", "keep-alive", "proxy-authenticate", "proxy-authorization",
                "te", "trailer", "transfer-encoding", "content-length", "sec-websocket-key", "sec-websocket-version",
                "sec-websocket-protocol", "sec-websocket-extensions"}

    @app.api_route("/implementation-workspaces/{identity}/ide/{remainder:path}",
                   methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def ide_http(identity: str, remainder: str, request: Request):
        browser_origin = request.headers.get("origin")
        if browser_origin and urlparse(browser_origin).netloc != request.headers.get("host"):
            raise HTTPException(403, "Editor request came from another origin")
        target_port = port(identity)
        target = f"http://127.0.0.1:{target_port}/{remainder}"
        if request.url.query:
            target += "?" + request.url.query
        headers = {k: v for k, v in request.headers.items() if k.lower() not in excluded}
        if browser_origin:
            headers["origin"] = f"http://127.0.0.1:{target_port}"
        headers["x-forwarded-prefix"] = f"/implementation-workspaces/{identity}/ide"
        client = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10), trust_env=False, follow_redirects=False)
        try:
            upstream = await client.send(client.build_request(request.method, target, headers=headers,
                content=request.stream()), stream=True)
        except Exception:
            await client.aclose()
            raise HTTPException(502, "Implementation IDE is reconnecting") from None
        out_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in excluded}
        if "location" in out_headers and out_headers["location"].startswith("/"):
            out_headers["location"] = f"/implementation-workspaces/{identity}/ide" + out_headers["location"]

        async def chunks():
            try:
                async for data in upstream.aiter_raw():
                    yield data
            finally:
                await upstream.aclose()
                await client.aclose()
        return StreamingResponse(chunks(), status_code=upstream.status_code, headers=out_headers)

    @app.websocket("/implementation-workspaces/{identity}/ide/{remainder:path}")
    async def ide_websocket(identity: str, remainder: str, browser: WebSocket):
        import websockets
        browser_origin = browser.headers.get("origin")
        if browser_origin and urlparse(browser_origin).netloc != browser.headers.get("host"):
            await browser.close(code=1008)
            return
        try:
            target_port = port(identity)
            destination = f"ws://127.0.0.1:{target_port}/{remainder}"
        except (KeyError, HTTPException):
            await browser.close(code=1013)
            return
        if browser.url.query:
            destination += "?" + browser.url.query
        headers = {k: v for k, v in browser.headers.items() if k.lower() not in excluded}
        if browser_origin:
            headers["origin"] = f"http://127.0.0.1:{target_port}"
        headers["x-forwarded-prefix"] = f"/implementation-workspaces/{identity}/ide"
        try:
            async with websockets.connect(destination, additional_headers=headers, open_timeout=10,
                                          max_size=16 * 1024 * 1024) as upstream:
                await browser.accept()
                async def from_browser():
                    while True:
                        frame = await browser.receive()
                        if frame["type"] == "websocket.disconnect":
                            return
                        if frame.get("text") is not None:
                            await upstream.send(frame["text"])
                        elif frame.get("bytes") is not None:
                            await upstream.send(frame["bytes"])
                async def from_editor():
                    async for frame in upstream:
                        if isinstance(frame, bytes):
                            await browser.send_bytes(frame)
                        else:
                            await browser.send_text(frame)
                tasks = [asyncio.create_task(from_browser()), asyncio.create_task(from_editor())]
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
        except Exception:
            logging.getLogger(__name__).exception("Implementation IDE WebSocket relay failed")
            try:
                await browser.close(code=1011)
            except RuntimeError:
                pass
