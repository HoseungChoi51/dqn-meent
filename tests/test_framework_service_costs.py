"""Repeated reconciliation preserves actual work and full reusable attribution."""
from copy import deepcopy

import pytest

from optimization_framework.assets.catalog import AssetCatalog
from optimization_framework.assets.service_costs import research_asset, implementation_asset, model_quantities
from optimization_framework.contracts.assets import Asset, CostEvent
from optimization_framework.storage.sqlite import Store


def usage(calls=1, elapsed=2):
    return {"calls": calls, "timed_calls": calls, "elapsed_seconds": elapsed, "billing_mode": "api",
        "input_tokens": 10 * calls, "output_tokens": 5 * calls, "api_cost_usd": .1 * calls, "token_accounting": "provider_reported"}


def test_resumed_model_turn_appends_only_new_usage_and_freezes_old_prefix(tmp_path):
    catalog = AssetCatalog(Store(tmp_path))
    run = {"id": "turn", "campaign_id": "origin", "created_at": "test", "usage": usage()}
    first = research_asset(catalog, run)
    assert research_asset(catalog, run) == first
    run["usage"] = usage(3, 5)
    later = research_asset(catalog, run)
    assert catalog.attributed_costs([first["id"]], axes=["model_calls"])["quantities"]["model_calls"]["total"] == 1
    assert catalog.attributed_costs([later["id"]], axes=["model_calls"])["quantities"]["model_calls"]["total"] == 3
    assert catalog.actual_costs("origin", axes=["model_seconds"])["quantities"]["model_seconds"]["total"] == 5
    assert len(catalog.store.list("cost_event")) == 2


def test_exact_cost_receipt_replay_avoids_global_cost_history_scan(tmp_path, monkeypatch):
    catalog = AssetCatalog(Store(tmp_path))
    run = {"id": "turn", "campaign_id": "origin", "created_at": "test", "usage": usage()}
    first = research_asset(catalog, run)
    list_records = catalog.store.list
    before = list_records("cost_event")

    def forbid_cost_scan(kind, *args, **kwargs):
        assert kind != "cost_event", "An unchanged receipt must not parse all numerical cost events"
        return list_records(kind, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(catalog.store, "list", forbid_cost_scan)
        assert research_asset(catalog, run) == first
    assert list_records("cost_event") == before
    # A changed receipt still performs full reconciliation and appends only the
    # newly observed work, without changing the earlier immutable prefix.
    later = research_asset(catalog, {**run, "usage": usage(2, 5)})
    assert len(list_records("cost_event")) == 2
    assert catalog.attributed_costs([first["id"]], axes=["model_calls"])["quantities"]["model_calls"]["total"] == 1
    assert catalog.attributed_costs([later["id"]], axes=["model_calls"])["quantities"]["model_calls"]["total"] == 2


@pytest.mark.parametrize("ordinal,owner,error", [(2, "origin", "complete original event prefix"),
    (1, "other_campaign", "original campaign owner")])
def test_exact_receipt_replay_keeps_prefix_and_owner_checks(tmp_path, ordinal, owner, error):
    catalog = AssetCatalog(Store(tmp_path))
    run = {"id": "turn", "campaign_id": "origin", "created_at": "test", "usage": usage()}
    research_asset(catalog, run)
    catalog.record_cost(CostEvent(id="later_cost", campaign_id=owner, source_id="model:turn", ordinal=ordinal,
        category="model", quantities={"model_calls": 1}, created_at="test"))
    with pytest.raises(ValueError, match=error):
        research_asset(catalog, run)


def test_unknown_usage_is_not_the_conservative_reservation_or_zero():
    values = model_quantities({**usage(), "pending_reservation": {"usd": 5}, "cost_accounting": "contains_conservative_reservations; actual charge unknown"})
    assert values["model_input_tokens"] is values["model_output_tokens"] is values["api_usd"] is None
    assert model_quantities({**usage(), "billing_mode": "subscription"})["api_usd"] == 0


def test_reused_implementation_costs_stay_with_the_original_campaign_and_are_attributed_in_full(tmp_path):
    catalog = AssetCatalog(Store(tmp_path))
    job = {"id": "build", "campaign_id": "origin", "created_at": "test", "accounting_final": True, "usage": usage(2, 4),
        "compute_seconds": 12, "attempts": [{"report": {"costs": {"evaluation_requests": 4, "solver_executions": 3}}}]}
    version = {"id": "version", "name": "Specialized optimizer", "created_at": "test", "spec": {"problem_id": "bounded_continuous"}}
    implementation = implementation_asset(catalog, version, [job], upstream_complete=True)
    assert implementation_asset(catalog, version, [job], upstream_complete=True) == implementation
    for name in ("a", "b"):
        catalog.publish(Asset(id=name, campaign_id="consumer", kind="solution", title=name,
            dependency_ids=[implementation["id"]], cost_provenance="complete", authority="worker", created_at="test"))
        total = catalog.attributed_costs([name])["quantities"]
        assert total["worker_seconds"]["total"] == 8 and total["solver_executions"]["total"] == 3
    assert catalog.actual_costs("consumer")["quantities"]["worker_seconds"]["total"] == 0
    assert catalog.actual_costs("origin")["quantities"]["worker_seconds"]["total"] == 8
    assert catalog.attributed_costs(["a", "b"])["quantities"]["worker_seconds"]["total"] == 8
    uncertain = deepcopy(job)
    uncertain.update(id="interrupted", unknown_compute_cost=True)
    another = implementation_asset(catalog, {**version, "id": "another"}, [uncertain])
    assert catalog.attributed_costs([another["id"]])["quantities"]["worker_seconds"]["total"] is None
