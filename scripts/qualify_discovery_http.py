#!/usr/bin/env python3
"""Actual HTTP discovery orchestration with explicitly labeled model/source fixtures."""
import argparse
import json
from pathlib import Path
import socket
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
    from optimization_framework.contracts.commands import Command
    from optimization_framework.contracts.requests import CampaignInput, TaskInput
    from optimization_framework.research import coordinator, engine, evidence, literature
    from optimization_framework.research.discovery import controller
    from optimization_framework.storage.sqlite import atomic_json

    class Library:
        def versions(self):
            return []

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    base = f"http://127.0.0.1:{sock.getsockname()[1]}"
    config = {"configured": True, "enabled": True, "provider": "qualification_fixture", "transport": "chat_completions",
        "billing_mode": "api", "base_url": base + "/qualification/model", "model": "fixture-no-inference",
        "pricing_known": True, "input_usd_per_million": 0, "output_usd_per_million": 0, "reasoning_effort": "low"}
    coordinator.provider_status = engine.provider_status = controller.provider_status = lambda: dict(config)
    evidence.search_literature = lambda query, **kwargs: {"query": query, "sources": [{"id": "source_fixture",
        "url": "https://arxiv.org/abs/2401.00001", "title": "Qualification fixture, not a real paper",
        "verification": "fixture", "excerpt": "Synthetic method passage for transport testing."}]}
    literature.fetch_document = lambda url: (b"<article><h2>Fixture method</h2><p>A local search varies its radius.</p></article>", url, "text/html")
    app = create_app(root / "workspace", implementation_client=Library(), frontend_directory=args.frontend.resolve())
    workspace = app.state.workspace
    requests = []

    @app.post("/qualification/model/chat/completions")
    def model(body: dict):
        context = json.loads(body["messages"][1]["content"])
        state = context["discovery"]
        role = state["brief"]["role"]
        requests.append({"role": role, "step": state["step"], "shared_context_hash": state.get("shared_context_hash")})
        result = {"summary": "Fixture " + role + " result; no real model inference"}
        if state["brief"]["stage"] == "analyze":
            result["artifacts"] = [{"kind": "problem_dossier", "title": "Declared continuous problem", "content": {
                "representation": "Continuous vectors", "objectives_and_constraints": "The supplied objective and box bounds",
                "available_operations": "Candidate evaluation", "cost_and_noise": "Not characterized by this fixture",
                "claims": [{"statement": "Evaluator contract supplied", "basis": "evaluator_contract", "evidence_ids": [context["tasks"][0]["id"]]}]}}]
        elif role == "campaign_manager":
            tasks = state["tasks"]
            dossiers = [key for row in tasks if row["brief"]["stage"] == "analyze" for key in row.get("artifact_ids", [])]
            studies = [row for row in tasks if row["brief"]["stage"] == "study"]
            generators = [row for row in tasks if row["brief"]["stage"] == "generate"]
            if not studies:
                result["proposed_tasks"] = [{"key": "literature", "role": "literature_investigator", "stage": "study",
                    "objective": "Search and inspect actual supplied passages before constructing an applicability map", "evidence_ids": dossiers}]
            elif not generators:
                maps = [key for row in studies for key in row["artifact_ids"]]
                citation = studies[0]["result"]["artifacts"][0]["content"]["methods"][0]["support"][0]
                result["proposed_tasks"] = [{"key": name, "role": name, "stage": "generate",
                    "objective": "Propose a concrete testable variant using the approved dossier and literature map",
                    "evidence_ids": [*dossiers, *maps, *citation["passage_ids"]]} for name in ("local_specialist", "cross_domain_explorer")]
            else:
                result.update(session_action="complete", artifacts=[{"kind": "synthesis", "title": "Qualification completed",
                    "content": {"finding": "The scripted analysis, search, reading and independent proposal workflow completed",
                                "limitation": "No real model inference or optimizer-quality evidence"}}])
        elif state["brief"]["stage"] == "study":
            if state["step"] == 0:
                result.update(disposition="continue", tools=[{"key": "search", "tool": "source.search", "arguments": {"query": "fixture local continuous optimization"}}])
            elif state["step"] == 1:
                source = state["tool_results"][0]["result"]["sources"][0]
                result.update(disposition="continue", tools=[{"key": "read", "tool": "source.read", "arguments": {"source_id": source["id"]}}])
            else:
                search, read = [row["result"] for row in state["tool_results"]]
                capture, passage = read["capture"], read["passages"][0]
                result["artifacts"] = [{"kind": "literature_map", "title": "Fixture applicability map", "content": {
                    "search_strategy": "Search then read the returned primary-text fixture", "retrieval_ids": [search["retrieval_id"]],
                    "methods": [{"name": "Local search", "mechanism": "Vary a local radius", "applicability": "Continuous bounded inputs",
                        "assumptions": ["Local smoothness remains unverified"], "parameter_guidance": "Try several radii",
                        "support": [{"claim": "The supplied text mentions radius variation", "source_id": capture["source_id"],
                                     "capture_id": capture["id"], "passage_ids": [passage["id"]]}]}]}}]
        else:
            records = state["evidence"]
            mapping = next(row for row in records if row.get("kind") == "literature_map")
            result["artifacts"] = [{"kind": "candidate_batch", "title": "Independent fixture proposal", "content": {
                "dossier_ids": [row["id"] for row in records if row.get("kind") == "problem_dossier"], "literature_map_ids": [mapping["id"]],
                "candidates": [{"key": "variant", "title": role + " local variant", "family": "Local search",
                    "mechanism": "Adjust radius after local improvement", "applicability": "The supplied evaluator accepts continuous bounded inputs",
                    "assumptions": ["Useful local structure"], "predictions": ["Radius affects the cost-quality tradeoff"],
                    "failure_modes": ["Premature convergence"], "startup_requirements": "An initial point and local probes",
                    "implementation_needs": "Adaptive variant requires correctness validation", "cheapest_test": "Compare three radii with paired seeds",
                    "support": mapping["content"]["methods"][0]["support"], "conjectures": ["Transfer to this problem is untested"]}]}}]
        return {"choices": [{"message": {"content": json.dumps(result)}}], "usage": {"prompt_tokens": 20, "completion_tokens": 10}}

    campaign = workspace.create_campaign(CampaignInput(name="Discovery HTTP qualification", tasks=[TaskInput(name="Continuous evaluator", problem_id="bounded_continuous")]))
    assert not workspace.store.list("hypothesis", campaign["id"])
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("Qualification server did not start")
            time.sleep(.05)
        with httpx.Client(base_url=base, timeout=30) as client:
            start = Command(id="qualification_start", campaign_id=campaign["id"], expected_revision=1, operation="discovery.start",
                payload={"task_id": workspace.current_tasks(campaign["id"])[0]["id"], "model_call_limit": 24, "max_output_tokens": 6000})
            response = client.post("/api/v1/commands", json=start.model_dump(mode="json"))
            response.raise_for_status()
            session_id = response.json()["outcome"]["session_id"]
            deadline = time.monotonic() + 60
            while workspace.store.get(session_id)["status"] != "completed":
                if time.monotonic() > deadline:
                    atomic_json(root / "incomplete.json", workspace.discovery.view(campaign["id"]))
                    raise AssertionError("Discovery did not complete; inspect incomplete.json and its agent log")
                time.sleep(.1)
            view = client.get(f"/api/campaigns/{campaign['id']}/discovery").json()
            assert len(view["candidates"]) == 2
            generators = [row for row in requests if row["role"] in {"local_specialist", "cross_domain_explorer"}]
            assert len(generators) == 2 and generators[0]["shared_context_hash"] == generators[1]["shared_context_hash"]
            log = client.get(f"/api/campaigns/{campaign['id']}/agent-log/download")
            log.raise_for_status()
            path = workspace.agent_log.directory(campaign["id"]) / "trace.jsonl"
            assert log.content == path.read_bytes()
            assert b"message.handoff" in log.content and b"tool.completed" in log.content
        atomic_json(root / "qualification.json", {"passed": True, "model_outputs": "fixtures; no real inference",
            "literature": "synthetic text and metadata; actual extraction, receipts and citation checks",
            "actual_components": ["HTTP application commands", "HTTP model transport", "discovery scheduler", "tool continuations",
                "candidate projection", "independent context snapshots", "SQLite memory", "JSONL log"],
            "campaign_id": campaign["id"], "session_id": session_id, "calls": requests, "candidates": len(view["candidates"]),
            "download_matches_file": True, "browser_closed_logging": True})
        atomic_json(root / "discovery.json", view)
        print(json.dumps({"passed": True, "artifact": str(root / "qualification.json")}))
    finally:
        server.should_exit = True
        thread.join(timeout=20)
        sock.close()


if __name__ == "__main__":
    main()
