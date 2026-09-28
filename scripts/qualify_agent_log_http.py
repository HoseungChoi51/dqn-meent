#!/usr/bin/env python3
"""Isolated real HTTP/browser/file qualification, using labeled model fixtures."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time

import httpx
import uvicorn


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--frontend", type=Path, required=True)
    args = parser.parse_args()
    root = args.directory.resolve()
    root.mkdir(parents=True, exist_ok=False)
    from optimization_framework.api.app import create_app
    from optimization_framework.contracts.requests import CampaignInput, ResearchInput, TaskInput
    from optimization_framework.research import coordinator, engine
    from optimization_framework.storage.sqlite import atomic_json

    class Library:
        def versions(self):
            return []

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    config = {"configured": True, "enabled": True, "provider": "qualification_fixture", "transport": "chat_completions",
              "billing_mode": "api", "base_url": base + "/qualification/model", "model": "fixture-no-inference",
              "pricing_known": True, "input_usd_per_million": 0, "output_usd_per_million": 0, "reasoning_effort": "low"}
    coordinator.provider_status = engine.provider_status = lambda: dict(config)
    app = create_app(root / "workspace", implementation_client=Library(), frontend_directory=args.frontend.resolve())
    workspace = app.state.workspace
    requests = []

    @app.post("/qualification/model/chat/completions")
    def model(body: dict):
        requests.append(body)
        time.sleep(1.5)  # Give the real viewer an observable in-flight call.
        role = body["messages"][0]["content"].split(" in a researcher-guided", 1)[0].removeprefix("You are the ")
        return {"choices": [{"message": {"content": json.dumps({"analysis": f"Fixture {role}: inspect the declared problem and retain uncertainty."})}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 10}}

    campaign = workspace.create_campaign(CampaignInput(name="Agent log HTTP qualification",
        tasks=[TaskInput(name="Continuous problem", problem_id="bounded_continuous")]))
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("Qualification server did not start")
            time.sleep(.05)
        run = app.state.manager.start(ResearchInput(campaign_id=campaign["id"], message="Generate and review problem-specific methods.", mode="generate", max_calls=5))
        path = workspace.agent_log.directory(campaign["id"]) / "trace.jsonl"
        deadline = time.monotonic() + 5
        while not path.exists() or '"event_type":"provider.request"' not in path.read_text():
            if time.monotonic() > deadline:
                raise AssertionError("Provider request was not projected with the browser closed")
            time.sleep(.05)
        atomic_json(root / "before-browser.json", {"run_id": run["id"], "status": workspace.store.get(run["id"])["status"],
            "file_exists": True, "bytes": path.stat().st_size, "requests": len(requests)})
        repo = Path(__file__).resolve().parents[1]
        environment = {**os.environ, "AGENT_LOG_HTTP_TEST_URL": base, "AGENT_LOG_HTTP_OUTPUT": str(root)}
        test = subprocess.run([str(repo / "frontend/node_modules/.bin/playwright"), "test", "tests/agent-log-http.spec.ts", "--workers=1"],
            cwd=repo / "frontend", env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=90)
        (root / "browser.log").write_text(test.stdout)
        if test.returncode:
            raise RuntimeError(f"Browser qualification failed; see {root / 'browser.log'}")
        with httpx.Client(base_url=base) as client:
            download = client.get(f"/api/campaigns/{campaign['id']}/agent-log/download")
            download.raise_for_status()
        records = [json.loads(line) for line in download.content.splitlines()]
        assert len(requests) == 5
        assert sum(row["event_type"] == "message.handoff" for row in records) == 5
        assert download.content == path.read_bytes()
        atomic_json(root / "qualification.json", {"passed": True, "model_outputs": "fixtures; no real inference",
            "actual_components": ["HTTP provider transport", "five-role runner", "campaign manager", "JSONL projector", "SSE", "browser"],
            "campaign_id": campaign["id"], "run_id": run["id"], "model_requests": len(requests),
            "events": len(records), "log_path": str(path), "download_matches_file": True, "browser_closed_logging": True})
        print(json.dumps({"passed": True, "artifact": str(root / "qualification.json")}))
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        sock.close()


if __name__ == "__main__":
    main()
