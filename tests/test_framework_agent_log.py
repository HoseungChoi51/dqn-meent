"""Log evidence must survive crashes without dispatching or leaking work."""
import json
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
import pytest

from optimization_framework.api.app import create_app
from optimization_framework.contracts.requests import CampaignInput, TaskInput
from optimization_framework.research.log import AgentLog, INLINE_BYTES
from optimization_framework.storage.sqlite import Store


@pytest.fixture
def log(tmp_path):
    return AgentLog(Store(tmp_path))


def event(log, text="hello", **kw):
    return log.record("campaign_one", "agent.report", agent_id="analyst-1", role="analyst", payload={"text": text}, **kw)


def test_transaction_rollback_and_duplicate_operation_do_not_escape(log):
    with pytest.raises(RuntimeError):
        with log.store.transaction():
            event(log, event_key="rollback")
            with pytest.raises(RuntimeError, match="committed"):
                log.project("campaign_one")
            raise RuntimeError("rollback")
    assert log.status("campaign_one")["latest_seq"] == 0
    first = event(log, event_key="once")
    assert event(log, event_key="once") == first
    log.project("campaign_one")
    assert len(log.page("campaign_one")["events"]) == 1
    assert log.store.events() == []  # diagnostics cannot wake scientific reasoning


def test_restart_after_append_before_cursor_commit_and_partial_final_write(log):
    first, second = event(log), event(log, "second")
    log.project("campaign_one")
    path = log.directory("campaign_one") / "trace.jsonl"
    original = path.read_bytes()
    first_end = original.index(b"\n") + 1
    # Simulate the file reaching disk while the second cursor transaction did not.
    with log.store.connection() as db:
        db.execute("DELETE FROM agent_log_offsets WHERE seq=2")
        db.execute("UPDATE agent_log_projection SET seq=1,bytes=?", (first_end,))
    with path.open("ab") as file:
        file.write(b'{"incomplete":')
    restarted = AgentLog(Store(log.store.directory))
    assert restarted.project("campaign_one")["error"] is None
    assert path.read_bytes() == original
    assert [e["event_id"] for e in restarted.page("campaign_one")["events"]] == [first["event_id"], second["event_id"]]


def test_complete_corruption_is_reported_and_preserved(log):
    event(log)
    log.project("campaign_one")
    path = log.directory("campaign_one") / "trace.jsonl"
    path.write_bytes(path.read_bytes().replace(b"hello", b"xxxxx"))
    corrupt = path.read_bytes()
    assert "differs" in log.project("campaign_one")["error"]
    assert path.read_bytes() == corrupt


def test_payload_redaction_scoping_and_rebuild(log):
    payload = {"api_key": "sensitive-value", "nested": '{"authorization":"Bearer secret-value"}',
               "text": "Bearer another-token", "reasoning_content": "private", "large": "x" * (INLINE_BYTES + 1)}
    row = log.record("campaign_one", "provider.request", payload=payload)
    assert "payload_ref" in row and "payload" not in row
    body = log.payload("campaign_one", row["payload_ref"])
    assert not any(secret.encode() in body for secret in ("sensitive-value", "secret-value", "another-token", '"private"'))
    with pytest.raises(KeyError):
        log.payload("campaign_two", row["payload_ref"])
    log.project("campaign_one")
    sidecar = log.directory("campaign_one") / "payloads" / (row["payload_ref"] + ".json")
    assert sidecar.read_bytes() == body
    # A projection rebuild can recover all content from its authoritative store.
    (log.directory("campaign_one") / "trace.jsonl").unlink()
    sidecar.unlink()
    log.project("campaign_one")
    assert sidecar.read_bytes() == body
    assert len(log.page("campaign_one")["events"]) == 1


def test_bounded_history_and_download_fixed_high_water(log):
    for n in range(12):
        event(log, str(n))
    log.project("campaign_one")
    tail = log.page("campaign_one", limit=3)
    assert [e["seq"] for e in tail["events"]] == [10, 11, 12]
    assert tail["has_older"]
    assert [e["seq"] for e in log.page("campaign_one", before=10, limit=3)["events"]] == [7, 8, 9]
    assert [e["seq"] for e in log.page("campaign_one", after=0, limit=3)["events"]] == [1, 2, 3]
    seq, stream = log.download("campaign_one")
    event(log, "later")
    log.project("campaign_one")
    assert seq == 12
    assert len(b"".join(stream).splitlines()) == 12
    with pytest.raises(ValueError):
        log.page("campaign_one", after=1, before=3)
    with pytest.raises(ValueError):
        log.record("../../other", "fake")


def test_concurrent_writers_and_liveness_have_stable_order(log):
    def writer(n):
        other = AgentLog(Store(log.store.directory))
        return event(other, str(n))
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(writer, range(20)))
    assert sorted(e["seq"] for e in rows) == list(range(1, 21))
    for _ in range(10):
        log.capture("campaign_one", "task-one", {"type": "provider_progress", "role": "analyst", "reservation_id": "call-one"})
    log.project("campaign_one")
    assert len(log.page("campaign_one")["events"]) == 21


def test_file_failure_retains_evidence_and_recovers(log, monkeypatch):
    row = event(log)
    from pathlib import Path
    original = Path.open
    def unavailable(path, *args, **kwargs):
        if path.name == "trace.jsonl":
            raise OSError("test disk unavailable")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", unavailable)
    state = log.project("campaign_one")
    assert state["lag"] == 1 and state["error"]
    monkeypatch.setattr(Path, "open", original)
    assert log.project("campaign_one")["lag"] == 0
    assert log.page("campaign_one")["events"][0]["event_id"] == row["event_id"]


def test_http_reads_only_project_scoped_events(tmp_path, monkeypatch):
    monkeypatch.setenv("GRATING_LLM_DISABLED", "true")
    app = create_app(tmp_path, start_workers=False)
    workspace = app.state.workspace
    campaign = workspace.create_campaign(CampaignInput(name="Log test", tasks=[TaskInput(name="Quadratic", problem_id="bounded_continuous")]))
    other = workspace.create_campaign(CampaignInput(name="Other", tasks=[TaskInput(name="Other", problem_id="bounded_continuous")]))
    row = workspace.agent_log.record(campaign["id"], "agent.report", payload={"report": "hello", "large": "x" * INLINE_BYTES})
    snapshot = workspace.store.list("trial"), workspace.store.list("research_run"), workspace.store.list("cost_event")
    base = f"/api/campaigns/{campaign['id']}/agent-log"
    with TestClient(app) as client:
        response = client.get(base)
        assert response.status_code == 200
        assert response.json()["events"][-1]["event_id"] == row["event_id"]
        data = client.get(base + "/download")
        assert data.status_code == 200 and int(data.headers["x-agent-log-cursor"]) == row["seq"]
        assert json.loads(data.content.splitlines()[-1])["event_id"] == row["event_id"]
        assert client.get(base + f"/payloads/{row['payload_ref']}").status_code == 200
        assert client.get(f"/api/campaigns/{other['id']}/agent-log/payloads/{row['payload_ref']}").status_code == 404
        assert client.get(base + "?after=-1").status_code == 422
        assert client.get("/api/campaigns/missing/agent-log").status_code == 404
    assert (workspace.store.list("trial"), workspace.store.list("research_run"), workspace.store.list("cost_event")) == snapshot


def test_provider_request_and_malformed_output_are_saved_before_validation(log, monkeypatch):
    import httpx
    from optimization_framework.research.engine import LLMAdapter
    calls = []
    def respond(request):
        calls.append(json.loads(request.content))
        # The diagnostic request must already be durable before dispatch.
        with log.store.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM agent_events").fetchone()[0] == 2
        return httpx.Response(200, json={"choices": [{"message": {"content": "not valid JSON", "reasoning_content": "PRIVATE"}}],
                                        "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
    original = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    adapter = LLMAdapter(reservation_callback=lambda value: log.capture("campaign_one", "task-one", value),
        config={"configured": True, "provider": "compatible", "transport": "chat_completions", "billing_mode": "api",
                "base_url": "http://provider.test/v1", "model": "fixture", "pricing_known": False, "reasoning_effort": "low"})
    with pytest.raises(ValueError, match="invalid structured"):
        adapter.call("problem_analyst", {"problem": "bounded real variables"})
    assert len(calls) == 1
    log.project("campaign_one")
    events = log.page("campaign_one")["events"]
    assert [row["event_type"] for row in events] == ["provider.reserved", "provider.request", "provider.response"]
    assert events[1]["payload"]["messages"] == calls[0]["messages"]
    assert "not valid JSON" in json.dumps(events[-1]) and "PRIVATE" not in json.dumps(events)


def test_linked_implementation_trace_reconciles_once_and_checks_grant(tmp_path):
    from optimization_framework.implementations.api import create_app as library_app
    from optimization_framework.execution.service import Workspace
    library = library_app(tmp_path / "library", token="fixture-token", start_workers=False)
    service = library.state.service
    workspace = Workspace(tmp_path / "workspace")
    campaign = workspace.create_campaign(CampaignInput(name="Library trace", tasks=[TaskInput(name="Quadratic", problem_id="bounded_continuous")]))
    grant = {"id": "grant_one", "campaign_id": campaign["id"], "job_id": "job_one", "source_job_revision": 1}
    service.store.put("implementation_job", {"id": "job_one", "status": "completed", "revision": 1,
        "request": {"workspace_id": workspace.store.identity(), "grant_id": grant["id"]}})
    original = service.agent_log.record("job_one", "provider.response", agent_id="builder", role="implementation_builder",
                                       payload={"ordinary_output": "candidate source", "large": "x" * INLINE_BYTES})
    with TestClient(library) as client:
        params = {"workspace_id": workspace.store.identity(), "grant_id": grant["id"]}
        endpoint = "/v1/jobs/job_one/agent-log"
        assert client.get(endpoint, params=params).status_code == 401
        assert client.get(endpoint, params={**params, "grant_id": "wrong"}, headers={"Authorization": "Bearer fixture-token"}).status_code == 403
        class Client:
            available = False
            def get(self, path, **query):
                if not self.available:
                    raise ValueError("temporarily disconnected")
                response = client.get(path, params=query, headers={"Authorization": "Bearer fixture-token"})
                assert response.status_code == 200
                return response.json()
            def agent_log(self, job, workspace_id, grant_id, after):
                return self.get(f"/v1/jobs/{job}/agent-log", workspace_id=workspace_id, grant_id=grant_id, after=after)
            def agent_payload(self, job, ref, workspace_id, grant_id):
                return self.get(f"/v1/jobs/{job}/agent-log/payloads/{ref}", workspace_id=workspace_id, grant_id=grant_id)
        remote = Client()
        workspace.implementations.client = remote
        workspace.implementations.reconcile_agent_logs([grant])
        remote.available = True
        workspace.implementations.reconcile_agent_logs([grant])
        workspace.implementations.reconcile_agent_logs([grant])
    workspace.agent_log.project(campaign["id"])
    mirrored = [row for row in workspace.agent_log.page(campaign["id"])["events"] if row.get("origin_event_id")]
    assert len(mirrored) == 1 and mirrored[0]["origin_event_id"] == original["event_id"]
    assert workspace.agent_log.payload(campaign["id"], mirrored[0]["payload_ref"]) == service.agent_log.payload("job_one", original["payload_ref"])


def test_sse_reconnect_uses_last_event_id(tmp_path):
    import asyncio
    from types import SimpleNamespace
    app = create_app(tmp_path, start_workers=False)
    workspace = app.state.workspace
    campaign = workspace.create_campaign(CampaignInput(name="SSE", tasks=[TaskInput(name="Quadratic", problem_id="bounded_continuous")]))
    for n in range(5):
        workspace.agent_log.record(campaign["id"], "agent.report", payload={"n": n})
    endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", "").endswith("/agent-log/stream"))
    async def disconnected():
        return False
    async def read():
        response = await endpoint(campaign["id"], SimpleNamespace(headers={"last-event-id": "3"}, is_disconnected=disconnected), after=1)
        iterator = response.body_iterator
        assert await anext(iterator) == "retry: 2000\n\n"
        first, second = await anext(iterator), await anext(iterator)
        assert first.startswith("id: 4\n") and second.startswith("id: 5\n")
        await iterator.aclose()
    asyncio.run(read())


def test_existing_role_workflow_logs_prompts_reports_and_handoffs(tmp_path, monkeypatch):
    import httpx
    from optimization_framework.campaigns.manager import CampaignManager
    from optimization_framework.contracts.requests import ResearchInput
    from optimization_framework.execution.service import Workspace
    from optimization_framework.research import engine, coordinator
    class Library:
        def versions(self):
            return []
    workspace = Workspace(tmp_path, implementation_client=Library())
    manager = CampaignManager(workspace)
    campaign = workspace.create_campaign(CampaignInput(name="Agent chain", tasks=[TaskInput(name="Quadratic", problem_id="bounded_continuous")]))
    config = {"configured": True, "provider": "compatible", "transport": "chat_completions", "billing_mode": "api",
              "base_url": "http://provider.test/v1", "model": "fixture", "pricing_known": True,
              "input_usd_per_million": 0, "output_usd_per_million": 0, "reasoning_effort": "low"}
    monkeypatch.setattr(engine, "provider_status", lambda: config)
    monkeypatch.setattr(coordinator, "provider_status", lambda: config)
    calls = []
    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"analysis": "Fixture review of the declared problem."})}}],
                                        "usage": {"prompt_tokens": 20, "completion_tokens": 10}})
    original = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    run = manager.start(ResearchInput(campaign_id=campaign["id"], message="Generate and review methods", mode="generate"))
    workspace.research_threads[run["id"]].join(timeout=10)
    assert workspace.store.get(run["id"])["status"] == "completed"
    workspace.agent_log.project(campaign["id"])
    events = workspace.agent_log.page(campaign["id"])["events"]
    requests = [row for row in events if row["event_type"] == "provider.request"]
    handoffs = [row for row in events if row["event_type"] == "message.handoff"]
    assert len(calls) == len(requests) == len(handoffs) == 5
    assert len({row["agent_id"] for row in requests}) == 5
    assert all(row["to_agent"] == "campaign_manager" and row["parent_event_id"] for row in handoffs)
    assert workspace.store.list("research_result") and not workspace.store.list("trial")
