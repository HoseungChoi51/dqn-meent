"""Comparison cost snapshots keep exact attribution without per-trial ledger scans."""
from collections import Counter
from types import SimpleNamespace

import pytest

from optimization_framework.analysis.general import report
from optimization_framework.assets.accounting import reconcile
from optimization_framework.assets.catalog import AssetCatalog
from optimization_framework.contracts.assets import Asset, CostEvent, CostSlice
from optimization_framework.evaluation.registry import problems
from optimization_framework.storage.sqlite import Store


def event(catalog, source, ordinal, *, owner="origin", **quantities):
    return catalog.record_cost(CostEvent(id=f"cost_{source}_{ordinal}", campaign_id=owner,
        source_id=source, ordinal=ordinal, category="evaluation", quantities=quantities, created_at="test"))


def asset(catalog, identity, slices=(), dependencies=(), *, provenance="complete"):
    return catalog.publish(Asset(id=identity, campaign_id="consumer", kind="solution", title=identity,
        costs=list(slices), dependency_ids=list(dependencies), cost_provenance=provenance,
        authority="researcher", created_at="test"))


def database_rows(store):
    with store.connection() as db:
        return {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in ("records", "events", "cost_positions")}


def comparison_workspace(tmp_path, trial_count=1, *, unknown_upstream=False):
    store = Store(tmp_path)
    catalog = AssetCatalog(store)
    store.put("campaign", {"id": "consumer", "active_study_id": "study"})
    store.put_immutable("study", {"id": "study", "campaign_id": "consumer"})
    problem = problems.resolve("bounded_continuous", {"dimensions": 2}).model_dump(mode="json")
    event(catalog, "shared", 0, worker_seconds=None if unknown_upstream else 5,
        evaluation_requests=1, solver_executions=1)
    asset(catalog, "shared_input", [CostSlice(source_id="shared", stop=1)])
    measurements = {}
    for index in range(trial_count):
        identity = f"trial_{index}"
        event(catalog, identity, 0, owner="consumer", worker_seconds=2,
            evaluation_requests=2, solver_executions=2)
        output = asset(catalog, f"output_{index}", [CostSlice(source_id=identity, stop=1)], ["shared_input"])
        measurements[identity] = [{"elapsed_seconds": 1, "evaluations": 1, "solver_calls": 1,
            "best_objective": 4 + index}]
        store.put("trial", {"id": identity, "campaign_id": "consumer", "study_id": "study",
            "problem": problem, "algorithm": "coordinate", "algorithm_config": {}, "seed": index,
            "schedule_steps": 2, "initial_assets": ["shared_input"], "scientific_source_hash": "test_source",
            "status": "completed", "latest_output_asset_ids": [output["id"]],
            "result": {"elapsed_seconds": 2, "evaluations": 2, "solver_calls": 2,
                "best_objective": 3 + index, "scientific_complete": True}})
    store.put("budget_amendment", {"id": "extension", "campaign_id": "consumer", "experiment_id": "trial_0"})
    return SimpleNamespace(store=store, assets=catalog,
        metrics=lambda identity, *, fields=None: measurements[identity])


@pytest.mark.parametrize("trial_count", [1, 24])
def test_report_loads_cost_ledger_once_independent_of_trial_count(tmp_path, monkeypatch, trial_count):
    workspace = comparison_workspace(tmp_path, trial_count)
    calls = Counter()
    read = workspace.store.list

    def counted(kind, *args, **kwargs):
        calls[kind] += 1
        return read(kind, *args, **kwargs)

    monkeypatch.setattr(workspace.store, "list", counted)
    result = report(workspace, "consumer")
    assert {kind: calls[kind] for kind in ("cost_event", "cost_reconciliation", "asset", "budget_amendment")} == {
        "cost_event": 1, "cost_reconciliation": 1, "asset": 1, "budget_amendment": 1}
    trials = result["groups"][0]["trials"]
    assert len(trials) == trial_count
    assert {trial["id"] for trial in trials if trial["adaptive_extension"]} == {"trial_0"}
    assert all(trial["upstream"]["quantities"]["worker_seconds"]["total"] == 5 for trial in trials)
    assert all(trial["full_cost"]["quantities"]["worker_seconds"]["total"] == 7 for trial in trials)
    assert result["actual_campaign_costs"]["quantities"]["worker_seconds"]["total"] == trial_count * 2


def test_snapshot_matches_catalog_for_shared_prefix_overlap_and_multiple_axes(tmp_path):
    catalog = AssetCatalog(Store(tmp_path))
    for source, count in (("shared", 10), ("refine", 4)):
        for ordinal in range(count):
            event(catalog, source, ordinal, worker_seconds=.25, evaluation_requests=1,
                solver_executions=int(ordinal % 2 == 0))
    asset(catalog, "prefix", [CostSlice(source_id="shared", stop=5)])
    asset(catalog, "continued", [CostSlice(source_id="shared", start=3, stop=10)], ["prefix"])
    asset(catalog, "refined", [CostSlice(source_id="refine", stop=4)], ["prefix"])
    view = catalog.cost_view()
    for identities, count in (([], 0), (["prefix"], 5), (["continued"], 10), (["refined"], 9),
            (["continued", "refined"], 14), (["continued", "continued", "refined"], 14)):
        actual = view.attributed_costs(identities)
        assert actual == catalog.attributed_costs(identities)
        assert actual["event_count"] == count
        assert actual["quantities"]["evaluation_requests"]["total"] == count
        assert actual["quantities"]["worker_seconds"]["total"] == count * .25
    for campaign_id in ("origin", "consumer", "absent"):
        assert view.actual_costs(campaign_id) == catalog.actual_costs(campaign_id)
    # Each result gets the full shared work, while local expenditure is not rebilled.
    assert view.actual_costs("origin")["event_count"] == 14
    assert view.actual_costs("consumer")["event_count"] == 0


def test_missing_intervals_and_unknown_provenance_remain_unknown(tmp_path):
    catalog = AssetCatalog(Store(tmp_path))
    event(catalog, "incomplete", 0, worker_seconds=2, evaluation_requests=1)
    event(catalog, "incomplete", 2, worker_seconds=None, evaluation_requests=1)
    asset(catalog, "unknown_import", provenance="unknown")
    asset(catalog, "partial", [CostSlice(source_id="incomplete", stop=4)], ["unknown_import"])
    view = catalog.cost_view()
    actual = view.attributed_costs(["partial"], axes=["evaluation_requests", "worker_seconds"])
    assert actual == catalog.attributed_costs(["partial"], axes=["evaluation_requests", "worker_seconds"])
    assert actual["event_count"] == 2
    assert actual["unknown_provenance_asset_ids"] == ["unknown_import"]
    assert actual["quantities"]["evaluation_requests"] == {
        "known": 2, "unknown_events": 2, "unknown_provenance": 1, "total": None}
    assert actual["quantities"]["worker_seconds"] == {
        "known": 2, "unknown_events": 3, "unknown_provenance": 1, "total": None}
    assert actual["accounting_basis"]["status"] == "partial"


def test_snapshot_uses_joint_receipt_without_guessing_individual_unknown_shares(tmp_path):
    catalog = AssetCatalog(Store(tmp_path))
    for ordinal, value in enumerate((None, 7, None)):
        event(catalog, "shared", ordinal, worker_seconds=value)
    receipt = reconcile(catalog, "shared", 3, {"worker_seconds": 30}, evidence_ids=["provider_receipt"],
        authority="researcher", rationale="Measured cumulative work")
    asset(catalog, "first", [CostSlice(source_id="shared", stop=1)])
    asset(catalog, "third", [CostSlice(source_id="shared", start=2, stop=3)])
    view = catalog.cost_view()
    for identities in (["first"], ["third"], ["first", "third"], ["first", "third", "first"]):
        assert view.attributed_costs(identities, axes=["worker_seconds"]) == catalog.attributed_costs(
            identities, axes=["worker_seconds"])
    assert view.attributed_costs(["first"], axes=["worker_seconds"])["quantities"]["worker_seconds"]["total"] is None
    assert view.attributed_costs(["third"], axes=["worker_seconds"])["quantities"]["worker_seconds"]["total"] is None
    combined = view.attributed_costs(["first", "third"], axes=["worker_seconds"])
    assert combined["quantities"]["worker_seconds"]["total"] == 23
    assert combined["accounting_basis"]["reconciliation_ids"] == [receipt["id"]]
    assert view.actual_costs("origin", axes=["worker_seconds"])["quantities"]["worker_seconds"]["total"] == 30
    assert view.actual_costs("consumer", axes=["worker_seconds"])["quantities"]["worker_seconds"]["total"] == 0


def test_new_report_observes_later_reconciliation_without_mutating_original_costs(tmp_path):
    workspace = comparison_workspace(tmp_path, unknown_upstream=True)
    original_events = workspace.store.list("cost_event")
    frozen = workspace.assets.cost_view()
    first = report(workspace, "consumer")
    assert first["groups"][0]["trials"][0]["curve"] == []
    assert first["groups"][0]["trials"][0]["unknown_cost"] is True
    receipt = reconcile(workspace.assets, "shared", 1, {"worker_seconds": 5},
        evidence_ids=["original_receipt"], authority="researcher", rationale="Recovered measured worker cost")
    # In-flight reads retain one accounting boundary; a subsequent request gets new evidence.
    assert frozen.attributed_costs(["shared_input"], axes=["worker_seconds"])["quantities"]["worker_seconds"]["total"] is None
    second = report(workspace, "consumer")
    trial = second["groups"][0]["trials"][0]
    assert trial["unknown_cost"] is False
    assert [point["cost"] for point in trial["curve"]] == [6, 7]
    assert trial["upstream"]["accounting_basis"]["reconciliation_ids"] == [receipt["id"]]
    assert workspace.store.list("cost_event") == original_events
    assert first["actual_campaign_costs"] == second["actual_campaign_costs"]


def test_actual_campaign_costs_do_not_use_another_campaigns_events_on_shared_source(tmp_path):
    catalog = AssetCatalog(Store(tmp_path))
    event(catalog, "same_source", 0, owner="origin", worker_seconds=9)
    event(catalog, "same_source", 1, owner="consumer", worker_seconds=2)
    asset(catalog, "result", [CostSlice(source_id="same_source", stop=2)])
    view = catalog.cost_view()
    assert view.attributed_costs(["result"], axes=["worker_seconds"])["quantities"]["worker_seconds"]["total"] == 11
    for campaign_id, expected in (("origin", 9), ("consumer", 2)):
        actual = view.actual_costs(campaign_id, axes=["worker_seconds"])
        assert actual == catalog.actual_costs(campaign_id, axes=["worker_seconds"])
        assert actual["quantities"]["worker_seconds"]["total"] == expected
        assert actual["event_count"] == 1


def test_comparison_and_cost_view_reads_do_not_mutate_records_or_ledger(tmp_path, monkeypatch):
    workspace = comparison_workspace(tmp_path, trial_count=3)
    before = database_rows(workspace.store)

    def forbidden(*args, **kwargs):
        pytest.fail("A comparison read must not update scientific records or cost receipts")

    for name in ("put", "put_immutable", "put_many", "event"):
        monkeypatch.setattr(workspace.store, name, forbidden)
    first = report(workspace, "consumer")
    assert report(workspace, "consumer") == first
    view = workspace.assets.cost_view()
    assert view.actual_costs("consumer")["event_count"] == 3
    assert view.attributed_costs(["output_0"])["event_count"] == 2
    assert database_rows(workspace.store) == before
