"""Campaign model changes persist without changing authority or in-flight calls."""
from copy import deepcopy
import json
from threading import Event

from fastapi.testclient import TestClient
import pytest

from test_framework_discovery import Adapter, CONFIG, command, fixture_dossier, setup, start
from optimization_framework.api.app import create_app
from optimization_framework.contracts.requests import CampaignInput, TaskInput
from optimization_framework.execution.service import Workspace
from optimization_framework.research.discovery.models import DiscoveryResult
from optimization_framework.research.engine import BudgetUnavailable, LLMAdapter
from optimization_framework.research.model_policy import apply_binding, resolve_policy


def policy():
    return {"default": {"model": "gpt-6-sol", "reasoning_effort": "xhigh"}, "roles": {
        "campaign_manager": {"model": "gpt-6-astra", "reasoning_effort": "xhigh"},
        "literature_investigator": {"model": "gpt-6-luna", "reasoning_effort": "xhigh"},
        "methodology_specialist": {"model": "gpt-6-luna", "reasoning_effort": "xhigh"}}}


def configure(workspace, campaign, *, revision=0, values=None, identity="configure_models"):
    request = command(workspace, campaign, "models.configure", {
        "expected_revision": revision, "policy": values or policy(), "reason": "Researcher selects campaign models"}, identity)
    return workspace.commands.execute(request), request


@pytest.mark.parametrize(("role", "model"), [
    ("campaign_manager", "gpt-6-astra"), ("research_synthesizer", "gpt-6-astra"),
    ("problem_analyst", "gpt-6-sol"), ("skeptical_domain_analyst", "gpt-6-sol"),
    ("combinatorial_generator", "gpt-6-sol"), ("proposal_reviewer", "gpt-6-sol"),
    ("future_researcher", "gpt-6-sol"), ("implementation_builder", "gpt-6-sol"),
    ("implementation_validator", "gpt-6-sol"), ("literature_investigator", "gpt-6-luna"),
    ("literature_investigator_2", "gpt-6-luna"), ("methodology_specialist", "gpt-6-luna"),
    ("cross_domain_methodology_specialist", "gpt-6-luna"),
    ("cross_domain_methodology_specialist_3", "gpt-6-luna"),
    ("notmethodology_specialist", "gpt-6-sol"),
])
def test_role_families_and_unknown_personas_resolve_requested_models(role, model):
    config = resolve_policy(CONFIG, policy(), role)
    assert config["model"] == model
    assert config["reasoning_effort"] == "xhigh"
    assert config["provider"] == CONFIG["provider"] and config["configured"]
    assert CONFIG["model"] == "fixture"


def test_exact_override_wins_over_family_and_alias():
    values = policy()
    for role in ("cross_domain_methodology_specialist_3", "research_synthesizer"):
        values["roles"][role] = {"model": "custom-reviewed-model", "reasoning_effort": "medium"}
        actual = resolve_policy(CONFIG, values, role)
        assert (actual["model"], actual["reasoning_effort"]) == ("custom-reviewed-model", "medium")


def test_policy_is_persistent_revisioned_and_replayable_without_resuming_discovery(setup):
    workspace, campaign = setup
    session, _ = start(workspace, campaign)
    workspace.discovery.control(campaign["id"], {"session_id": session["id"], "action": "pause", "expected_control_revision": 0})
    paused = workspace.store.get(session["id"], "discovery_session")
    before_campaign = workspace.store.get(campaign["id"], "campaign")
    before_guidance = workspace.memory.state(campaign["id"])["guidance_revision"]

    receipt, request = configure(workspace, campaign)
    assert receipt["outcome"]["revision"] == 1
    changed = policy()
    changed["default"]["reasoning_effort"] = "high"
    configure(workspace, campaign, revision=1, values=changed, identity="change_models")
    assert workspace.commands.execute(request) == receipt
    with pytest.raises(ValueError, match="Model settings changed"):
        configure(workspace, campaign, revision=1, identity="stale_models")

    restarted = Workspace(workspace.directory, implementation_client=workspace.implementations.client)
    assert restarted.models.record(campaign["id"])["revision"] == 2
    assert restarted.models.config(campaign["id"], "new_researcher", base=CONFIG)["reasoning_effort"] == "high"
    history = restarted.store.list("model_policy_revision", campaign["id"])
    assert [row["revision"] for row in history] == [1, 2]
    assert history[0]["policy"]["default"]["reasoning_effort"] == "xhigh"
    assert restarted.store.get(session["id"], "discovery_session") == paused
    assert workspace.store.get(campaign["id"], "campaign") == before_campaign
    assert workspace.memory.state(campaign["id"])["guidance_revision"] == before_guidance
    assert not workspace.store.list("research_run", campaign["id"])


def test_manager_cannot_change_model_policy_even_with_delegated_authority(setup):
    workspace, campaign = setup
    campaign["autonomy"] = "delegated"
    workspace.store.put("campaign", campaign)
    request = command(workspace, campaign, "models.configure", {"expected_revision": 0, "policy": policy()}, "manager_models")
    request = request.model_copy(update={
        "expected_guidance_revision": workspace.memory.state(campaign["id"])["guidance_revision"],
        "expected_authority_hash": workspace.commands.authority_hash(campaign)})
    assert not workspace.commands.describe()["models.configure"]["delegable"]
    with pytest.raises(ValueError, match="researcher authorization"):
        workspace.commands.execute(request, actor="manager")
    assert workspace.models.record(campaign["id"]) is None


def test_model_switch_cannot_inherit_another_models_api_prices():
    base = {**CONFIG, "billing_mode": "api", "pricing_known": True,
        "input_usd_per_million": 10, "output_usd_per_million": 30}
    routed = resolve_policy(base, policy(), "campaign_manager")
    assert not routed["pricing_known"]
    assert routed["input_usd_per_million"] is None and routed["output_usd_per_million"] is None
    one_price = apply_binding(base, {"model": "another-model", "input_usd_per_million": 2})
    assert not one_price["pricing_known"] and one_price["output_usd_per_million"] is None
    explicit = apply_binding(base, {"model": "another-model", "input_usd_per_million": 2, "output_usd_per_million": 7})
    assert explicit["pricing_known"] and explicit["output_usd_per_million"] == 7
    values = policy()
    values["default"].update(input_usd_per_million=1, output_usd_per_million=2)
    assert not resolve_policy(base, values, "campaign_manager")["pricing_known"]
    same_model = {"default": {"model": base["model"], "reasoning_effort": "high",
        "input_usd_per_million": None, "output_usd_per_million": None}, "roles": {}}
    cleared = resolve_policy(base, same_model, "problem_analyst")
    assert not cleared["pricing_known"]
    assert cleared["input_usd_per_million"] is None and cleared["output_usd_per_million"] is None
    legacy = apply_binding(base, {"reasoning_effort": "high"})
    assert legacy["pricing_known"] and legacy["output_usd_per_million"] == 30
    assert base["input_usd_per_million"] == 10


def test_blank_saved_prices_block_capped_api_before_any_provider_call(monkeypatch):
    events, transports = [], []
    monkeypatch.setattr("optimization_framework.research.engine.httpx.Client", lambda **kwargs: transports.append(kwargs))
    adapter = LLMAdapter(budget_usd=1, reservation_callback=events.append, config={**CONFIG,
        "base_url": "https://fixture.invalid/v1", "billing_mode": "api", "pricing_known": True,
        "input_usd_per_million": 10, "output_usd_per_million": 30,
        "model_policy": {"default": {"model": CONFIG["model"], "reasoning_effort": "high",
            "input_usd_per_million": None, "output_usd_per_million": None}, "roles": {}}})
    with pytest.raises(BudgetUnavailable, match="requires configured input/output model prices"):
        adapter.call_with_prompt("problem_analyst", "Fixture instructions", {}, result_type=DiscoveryResult)
    assert not transports and not events
    assert adapter.usage["calls"] == 0 and adapter.usage["reserved_cost_usd"] == 0
    assert not adapter.config["pricing_known"]


def test_common_adapter_routes_each_role_from_snapshot_without_sticky_overrides(monkeypatch):
    adapter = LLMAdapter(config={**CONFIG, "transport": "codex_exec", "model_policy": policy()})
    observed = []
    monkeypatch.setattr(adapter, "_call_codex", lambda role, *args: observed.append((role, deepcopy(adapter.config))))
    for role in ("campaign_manager", "literature_investigator", "unknown_researcher", "methodology_specialist"):
        adapter.call_with_prompt(role, "Fixture instructions", {}, result_type=DiscoveryResult)
    assert [config["model"] for _, config in observed] == ["gpt-6-astra", "gpt-6-luna", "gpt-6-sol", "gpt-6-luna"]
    assert all(config["reasoning_effort"] == "xhigh" for _, config in observed)
    assert adapter.base_config["model"] == "fixture"


def test_dispatched_attempt_keeps_old_model_and_continuation_uses_saved_revision(setup):
    workspace, campaign = setup
    configure(workspace, campaign)
    entered, release = Event(), Event()
    observed = []

    class BarrierAdapter(Adapter):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.config = deepcopy(kwargs["config"])

        def call_with_prompt(self, role, system, context, result_type):
            observed.append((role, deepcopy(self.config)))
            if len(observed) == 1:
                entered.set()
                assert release.wait(5), "Test did not release the in-flight fixture call"
            return super().call_with_prompt(role, system, context, result_type)

        def result(self, role, context):
            if len(observed) == 1:
                return DiscoveryResult(summary="Continue the analysis", disposition="continue")
            return DiscoveryResult(summary="Analysis completed", artifacts=[fixture_dossier()])

    workspace.discovery.adapter_factory = BarrierAdapter
    session, _ = start(workspace, campaign, max_concurrent_tasks=1)
    workspace.discovery.tick(campaign["id"])
    try:
        assert entered.wait(5), "Fixture call was not dispatched"
        task = next(row for row in workspace.discovery.tasks(session) if row["status"] == "running")
        first_attempt = workspace.store.get(task["attempt_id"], "discovery_attempt")
        assert first_attempt["model_policy_revision"] == 1
        updated = policy()
        updated["default"] = {"model": "new-research-model", "reasoning_effort": "medium"}
        configure(workspace, campaign, revision=1, values=updated, identity="update_during_call")
    finally:
        release.set()
        for thread in list(workspace.discovery.threads.values()):
            thread.join(timeout=5)
            assert not thread.is_alive()
    continued = workspace.store.get(task["id"], "discovery_task")
    assert continued["status"] == "queued" and continued["step"] == 1
    assert workspace.store.get(first_attempt["id"], "discovery_attempt") == first_attempt
    workspace.discovery.tick(campaign["id"])
    for thread in list(workspace.discovery.threads.values()):
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert len(observed) == 2 and observed[0][0] == observed[1][0]
    assert (observed[0][1]["model"], observed[0][1]["reasoning_effort"]) == ("gpt-6-sol", "xhigh")
    assert (observed[1][1]["model"], observed[1][1]["reasoning_effort"]) == ("new-research-model", "medium")
    attempts = [row for row in workspace.store.list("discovery_attempt", campaign["id"]) if row["task_id"] == task["id"]]
    assert [row["model_policy_revision"] for row in attempts] == [1, 2]


def test_http_panel_reads_effective_models_but_cannot_enable_provider_or_write_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("GRATING_LLM_DISABLED", "true")
    monkeypatch.setenv("GRATING_LLM_PROVIDER", "openai_api")
    monkeypatch.setenv("GRATING_LLM_API_KEY", "fixture-secret-never-return-in-model-panel")
    app = create_app(tmp_path, start_workers=False)
    workspace = app.state.workspace
    campaign = workspace.create_campaign(CampaignInput(name="Model panel", tasks=[TaskInput(name="Quadratic", problem_id="bounded_continuous")]))
    with TestClient(app) as browser:
        url = f"/api/campaigns/{campaign['id']}/models"
        initial = browser.get(url).json()
        assert initial["revision"] == 0 and not initial["configured"]
        request = command(workspace, campaign, "models.configure", {"expected_revision": 0, "policy": policy()}, "http_models")
        response = browser.post("/api/v1/commands", json=request.model_dump(mode="json"))
        assert response.status_code == 200, response.text
        current = browser.get(url).json()
        assert current["revision"] == 1 and not current["configured"]
        assert current["provider"] == initial["provider"]
        roles = {row["role"]: row for row in current["roles"]}
        assert roles["campaign_manager"]["model"] == "gpt-6-astra"
        assert roles["cross_domain_methodology_specialist"]["matched_role"] == "methodology_specialist"
        state = browser.get("/api/state", params={"campaign_id": campaign["id"]}).json()
        assert state["settings"]["model_policy"] == current
        assert state["settings"]["model"] == "gpt-6-astra"
        assert not state["settings"]["llm_configured"]
        assert "fixture-secret-never-return-in-model-panel" not in json.dumps(state)
        for key, value in (("enabled", True), ("api_key", "other-secret"), ("provider", "compatible")):
            bad = request.model_copy(update={"id": "forbidden_" + key,
                "payload": {"expected_revision": 1, "policy": policy(), key: value}})
            response = browser.post("/api/v1/commands", json=bad.model_dump(mode="json"))
            assert response.status_code in {409, 422}, response.text
        assert browser.get(url).json()["revision"] == 1
        assert browser.get("/api/campaigns/missing/models").status_code == 404
