import copy
import json

import httpx
import pytest

from dqn_meent.workspace import research


@pytest.fixture(autouse=True)
def isolated_provider(monkeypatch, tmp_path):
    """Tests never discover a developer's real API key or use paid network calls."""
    for key in list(research.os.environ):
        if key.startswith("GRATING_LLM_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GRATING_LLM_KEY_FILE", str(tmp_path / "missing.key"))


def context():
    return {"campaign": {"id": "campaign", "charter": {"objective": "binary +1 efficiency"}},
            "tasks": [{"id": "dev", "split": "development"}, {"id": "SECRET_TEST", "split": "test"}],
            "hypotheses": research.seed_hypotheses(), "trials": []}


def configure(monkeypatch):
    monkeypatch.setenv("GRATING_LLM_ENABLED", "true")
    monkeypatch.setenv("GRATING_LLM_PROVIDER", "compatible")
    monkeypatch.setenv("GRATING_LLM_BASE_URL", "http://localhost:8123/v1")
    monkeypatch.setenv("GRATING_LLM_MODEL", "test-model")
    monkeypatch.setenv("GRATING_LLM_INPUT_USD_PER_MILLION", "1")
    monkeypatch.setenv("GRATING_LLM_OUTPUT_USD_PER_MILLION", "2")


def mock_provider(monkeypatch, responder):
    calls = []

    def handle(request):
        payload = json.loads(request.content)
        calls.append(payload)
        result = responder(payload, len(calls))
        if isinstance(result, httpx.Response):
            return result
        if request.url.path.endswith("/responses"):
            return httpx.Response(200, json={"status": "completed", "usage": {"input_tokens": 100, "output_tokens": 30},
                                           "output": [{"type": "reasoning"}, {"type": "message", "content": [
                                               {"type": "output_text", "text": json.dumps(result)}]}]})
        return httpx.Response(200, json={"usage": {"prompt_tokens": 100, "completion_tokens": 30},
                                       "choices": [{"message": {"content": json.dumps(result)}}]})

    client = httpx.Client
    monkeypatch.setattr(research.httpx, "Client", lambda **kw: client(transport=httpx.MockTransport(handle), **kw))
    return calls


def test_seed_cards_separate_rationale_from_evidence():
    cards = research.seed_hypotheses()
    assert len({c["id"] for c in cards}) == len(cards)
    assert {c["algorithm"] for c in cards if c["status"] == "baseline"} == {"random", "hillclimb", "dqn"}
    assert all(c["rationale"] and c["cheapest_check"] and c["evidence"] == [] for c in cards)
    assert all(c["claim_level"] == "rationale_only" for c in cards)
    cards[0]["assumptions"].append("mutation")
    assert "mutation" not in research.seed_hypotheses()[0]["assumptions"]


def test_offline_idea_and_evolution_preserve_parent_without_fake_llm():
    original = context()
    idea = research.run_research({"mode": "generate", "message": "Use boundary moves weighted by recent stagnation."}, original)
    card = idea["hypotheses"][-1]
    assert card["origin"] == "researcher"
    assert idea["usage"]["calls"] == 0
    assert "no LLM" in idea["messages"][0]["content"]
    original["hypotheses"].append(card)
    revision = research.run_research({"mode": "evolve", "message": "Also preserve a fill-factor constraint.", "hypothesis_id": card["id"]}, original)
    assert revision["hypotheses"][0]["parent_ids"] == [card["id"]]
    assert revision["hypotheses"][0]["id"] != card["id"]
    assert "fill-factor" not in card["protocol"]


def test_probe_prefers_discriminating_case_over_easy_or_jointly_flat_hard_case():
    tasks = [{"id": x} for x in ("easy", "hopeless", "useful")]
    trials = []
    for task_id, values in (("easy", (.98, .99)), ("hopeless", (.01, .01)), ("useful", (.2, .55))):
        for algorithm, value in zip(("hill_climb", "random"), values, strict=True):
            trials.append({"task_id": task_id, "algorithm": algorithm, "best_efficiency": value,
                           "curve": [{"best_efficiency": value}] * 8, "solver_calls": 20, "elapsed_seconds": 1})
    probe = research.select_probe(tasks, [research.seed_hypotheses()[3]], trials)
    assert probe["task_id"] == "useful"
    assert "one development" in probe["scope"]
    assert any(a["metrics"]["uniformly_stalled"] for a in probe["alternatives"])


def test_probe_excludes_test_tasks_and_unreliable_fidelity():
    tasks = [{"id": "test", "split": "test"}, {"id": "locked", "locked": True},
             {"id": "bad", "solver_reliable": False}]
    result = research.select_probe(tasks, [], [])
    assert result["task_id"] is None
    assert result["status"] == "needs_validation"
    assert "test" not in json.dumps(result)


def test_slow_start_requires_researcher_instead_of_eliminating():
    ctx = context()
    ctx["trials"] = [{"id": "slow", "task_id": "dev", "algorithm": "surrogate", "status": "completed",
                      "solver_calls": 20, "elapsed_seconds": 10, "hypothesis_id": "seed_surrogate"}]
    output = research.run_research({"mode": "discuss"}, ctx)
    decision = next(d for d in output["decisions"] if d.get("trial_id") == "slow")
    assert decision["incremental_solver_calls"] == 28
    assert decision["estimated_seconds"] == 14
    assert "Defer without declaring failure" in decision["options"]
    assert all(a["kind"] != "eliminate" for a in output["actions"])


def test_independent_generators_dynamic_roles_and_hidden_test_filter(monkeypatch):
    configure(monkeypatch)
    seen = []

    def response(payload, count):
        prompt = json.loads(payload["messages"][1]["content"])
        seen.append(prompt)
        answer = {"analysis": f"Role {count} analysis", "next_roles": ["literature_investigator"] if count == 1 else []}
        if count <= 2:
            answer["hypotheses"] = [{"title": f"Idea {count}", "algorithm": "custom", "mechanism": "Coordinated boundaries",
                                     "rationale": "May preserve promising structures", "assumptions": ["Unverified locality"],
                                     "predictions": ["More accepted improvements"], "failure_modes": ["Nonlocal response"],
                                     "cheapest_check": "Compare paired neighborhoods", "source_ids": ["bocs", "invented"],
                                     "source": "def propose(state): return state"}]
        return answer

    calls = mock_provider(monkeypatch, response)
    ctx = context()
    ctx["trials"] = [{"id": "SECRET_RESULT", "task_id": "SECRET_TEST", "best_efficiency": .99}]
    emitted = []
    result = research.run_research({"mode": "generate", "max_calls": 7, "llm_budget_usd": 1}, ctx, emitted.append)
    assert len(calls) == 6
    assert "initialize(n_cells, seed, config)" in calls[0]["messages"][0]["content"]
    assert "Only the Python standard" in calls[1]["messages"][0]["content"]
    assert seen[0]["previous_role_results"] == [] and seen[1]["previous_role_results"] == []
    assert seen[1]["new_hypotheses"] == []
    assert "SECRET_TEST" not in json.dumps(calls) and "SECRET_RESULT" not in json.dumps(calls)
    assert any(t["role"] == "literature_investigator" for t in result["trace"])
    assert result["hypotheses"][0]["unverified_source_ids"] == ["invented"]
    assert result["hypotheses"][0]["sources"][0]["id"] == "bocs"
    assert result["hypotheses"][0]["implementation_status"] == "proposed_source_not_executed"
    assert result["usage"]["cost_usd"] == pytest.approx(6 * .00016)
    assert len([e for e in emitted if e["type"] == "research_checkpoint"]) == 6


def test_model_can_request_judgment_and_does_not_launch_jobs(monkeypatch):
    configure(monkeypatch)
    mock_provider(monkeypatch, lambda *_: {"analysis": "Evidence is startup-limited.", "questions": ["Extend the surrogate?"],
        "actions": [{"kind": "extend", "title": "Extend startup", "rationale": "Need model-guided samples", "question": "Does the model repay startup?",
                     "task_id": "dev", "budget_calls": 80, "expected_information": "Improvement after warm-up", "stopping_condition": "After 80 calls"}]})
    result = research.run_research({"mode": "discuss"}, context())
    assert result["status"] == "awaiting_researcher"
    assert result["actions"][0]["status"] == "proposed"
    assert result["actions"][0]["requires_researcher"] is True


def test_dollar_budget_prevents_network_when_pricing_unknown_or_insufficient(monkeypatch):
    configure(monkeypatch)
    calls = mock_provider(monkeypatch, lambda *_: {"analysis": "Unexpected"})
    adapter = research.LLMAdapter(budget_usd=0)
    with pytest.raises(research.BudgetUnavailable, match="spending cap"):
        adapter.call("research_synthesizer", {})
    monkeypatch.delenv("GRATING_LLM_INPUT_USD_PER_MILLION")
    adapter = research.LLMAdapter(budget_usd=1)
    with pytest.raises(research.BudgetUnavailable, match="prices"):
        adapter.call("research_synthesizer", {})
    assert calls == []


def test_malformed_response_is_charged_and_not_retried(monkeypatch):
    configure(monkeypatch)
    calls = mock_provider(monkeypatch, lambda *_: {"analysis": "Invalid claim", "unrecognized": "not allowed"})
    result = research.run_research({"mode": "review"}, context())
    assert len(calls) == 1
    assert result["status"] == "partial"
    assert result["usage"]["calls"] == 1
    assert result["usage"]["cost_usd"] > 0


def test_failed_http_reserves_spending_and_hides_server_secrets(monkeypatch):
    configure(monkeypatch)
    calls = mock_provider(monkeypatch, lambda *_: httpx.Response(500, text="private-key-secret"))
    result = research.run_research({"mode": "discuss"}, context())
    assert len(calls) == 1
    assert result["usage"]["reserved_cost_usd"] > 0
    assert "actual usage unknown" in result["usage"]["token_accounting"]
    assert "private-key-secret" not in json.dumps(result)


def test_resume_checkpoint_does_not_repeat_completed_roles(monkeypatch):
    configure(monkeypatch)
    calls = mock_provider(monkeypatch, lambda *_: {"analysis": "Sound rationale, not proof."})
    checkpoint = None

    def interrupt(event):
        nonlocal checkpoint
        if event["type"] == "research_checkpoint":
            checkpoint = copy.deepcopy(event)
            raise InterruptedError("Service restart")

    request = {"mode": "review", "max_calls": 4}
    with pytest.raises(InterruptedError):
        research.run_research(request, context(), interrupt)
    assert len(calls) == 1
    result = research.run_research({**request, "resume_state": checkpoint["state"], "resume_fingerprint": checkpoint["fingerprint"]}, context())
    assert len(calls) == 2
    assert result["research_state"]["completed"] == ["assumption_reviewer", "research_synthesizer"]
    assert result["usage"]["calls"] == 2
    with pytest.raises(ValueError, match="does not match"):
        research.run_research({**request, "message": "changed context", "resume_state": checkpoint["state"], "resume_fingerprint": checkpoint["fingerprint"]}, context())


def test_local_key_uses_official_responses_defaults_without_exposing_secret(monkeypatch, tmp_path):
    monkeypatch.delenv("GRATING_LLM_KEY_FILE")
    monkeypatch.setenv("GRATING_LLM_ENABLED", "true")
    monkeypatch.setenv("GRATING_LLM_PROVIDER", "openai_api")
    monkeypatch.setenv("GRATING_LLM_MODEL", "gpt-6-luna")
    (tmp_path / ".key").write_text("fake-secret-for-test")
    status = research.provider_status()
    assert status["configured"] and status["model"] == "gpt-6-luna"
    assert status["transport"] == "responses"
    assert "fake-secret" not in json.dumps(status)
    calls = mock_provider(monkeypatch, lambda *_: {"analysis": "A cautiously framed proposal."})
    result = research.run_research({"mode": "discuss", "llm_budget_usd": .05}, context())
    assert result["status"] == "completed"
    assert calls[0]["store"] is False
    assert calls[0]["max_output_tokens"] == 1800
    assert calls[0]["reasoning"]["effort"] == "low"
    assert "max_tokens" not in calls[0]
    assert result["usage"]["cost_usd"] == pytest.approx(.000025)
    monkeypatch.setenv("GRATING_LLM_BASE_URL", "https://another-provider.example/v1")
    monkeypatch.setenv("GRATING_LLM_MODEL", "other-model")
    assert research.provider_status()["configured"] is False


def test_disable_switch_remains_offline_even_with_key(monkeypatch, tmp_path):
    monkeypatch.delenv("GRATING_LLM_KEY_FILE")
    (tmp_path / ".key").write_text("fake-secret-for-test")
    monkeypatch.setenv("GRATING_LLM_DISABLED", "true")
    assert research.run_research({"mode": "discuss"}, context())["mode"] == "curated"


def test_durable_reservation_is_emitted_before_network_and_reconciled_after_response(monkeypatch):
    configure(monkeypatch)
    events = []

    def response(payload, count):
        reservation = next(e for e in events if e["type"] == "provider_call_reserved")
        assert reservation["usage"]["cost_usd"] > 0
        assert reservation["usage"]["pending_reservation"]["id"] == reservation["reservation_id"]
        assert reservation["usage"]["calls"] == 1
        assert "messages" not in reservation and "input" not in reservation
        return {"analysis": "Measured evidence is still needed."}

    mock_provider(monkeypatch, response)
    result = research.run_research({"mode": "discuss"}, context(), events.append)
    reserved = next(e for e in events if e["type"] == "provider_call_reserved")["usage"]["cost_usd"]
    checkpoint = next(e for e in events if e["type"] == "research_checkpoint")
    assert "pending_reservation" not in checkpoint["state"]["usage"]
    assert result["usage"]["cost_usd"] == pytest.approx(.00016)
    assert reserved > result["usage"]["cost_usd"]


def test_cancellation_before_send_releases_reservation_without_charging_unmade_call(monkeypatch):
    configure(monkeypatch)
    calls = mock_provider(monkeypatch, lambda *_: pytest.fail("No request should be sent"))
    events = []

    def cancel(event):
        events.append(event)
        if event["type"] == "provider_call_reserved":
            raise InterruptedError("Researcher stopped before sending")

    adapter = research.LLMAdapter(reservation_callback=cancel)
    with pytest.raises(InterruptedError):
        adapter.call("research_synthesizer", {})
    assert calls == []
    assert adapter.usage["calls"] == 0 and adapter.usage["cost_usd"] == 0
    assert events[-1]["type"] == "provider_call_cancelled_before_send"
    assert events[-1]["usage"]["reserved_cost_usd"] == 0
    assert "pending_reservation" not in events[-1]["usage"]


def test_inflight_uncertainty_blocks_resume_and_failed_calls_keep_single_reservation(monkeypatch):
    configure(monkeypatch)
    events = []
    calls = mock_provider(monkeypatch, lambda *_: httpx.Response(503, text="Unavailable"))
    result = research.run_research({"mode": "discuss"}, context(), events.append)
    reservation = next(e for e in events if e["type"] == "provider_call_reserved")
    assert result["usage"]["cost_usd"] == reservation["usage"]["cost_usd"]
    assert len(calls) == 1
    with pytest.raises(ValueError, match="reconciliation"):
        research.run_research({"mode": "discuss", "resume_state": {"usage": reservation["usage"]}}, context())
    assert len(calls) == 1
