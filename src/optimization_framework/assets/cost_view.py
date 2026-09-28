"""Request-scoped cost reads shared by every trial in a comparison report."""
from collections import defaultdict

from optimization_framework.assets.accounting import CostProjection
from optimization_framework.assets.catalog import contributions, merged_intervals


class CostView:
    def __init__(self, catalog):
        # One short read transaction gives assets, events, and later receipts the
        # same boundary. Calculating projections never retains the database lock.
        with catalog.store.connection() as db:
            if not db.in_transaction:
                db.execute("BEGIN")
            self.assets = {item["id"]: item for item in catalog.store.list("asset")}
            events = catalog.store.list("cost_event")
            self.receipts = catalog.store.list("cost_reconciliation")
        self.projection = CostProjection(events, self.receipts)
        self.campaign_events = defaultdict(list)
        for event in events:
            self.campaign_events[event["campaign_id"]].append(event)

    def attributed_costs(self, asset_ids, axes=("evaluation_requests", "solver_executions", "worker_seconds")):
        graph = contributions(self.assets, asset_ids)
        event_count = sum(any(start <= event["ordinal"] < stop for start, stop in ranges)
            for source, ranges in graph["intervals"].items() for event in self.projection.sources[source])
        quantities, basis = self.projection.project(graph["intervals"], axes,
            unknown_provenance=len(graph["unknown_provenance_asset_ids"]))
        return {"view": "full_attributed_cost", **graph, "event_count": event_count,
                "quantities": quantities, "accounting_basis": basis}

    def actual_costs(self, campaign_id, axes=("evaluation_requests", "solver_executions", "worker_seconds")):
        events = self.campaign_events[campaign_id]
        intervals = {}
        for event in events:
            intervals.setdefault(event["source_id"], []).append([event["ordinal"], event["ordinal"] + 1])
        intervals = {source: merged_intervals(ranges) for source, ranges in intervals.items()}
        # Attribution may include imported expenditure, while actual spending
        # must use only this campaign's original events, even for a shared source.
        quantities, basis = CostProjection(events, self.receipts, cache_equations=False).project(intervals, axes)
        return {"view": "actual_expenditure", "campaign_id": campaign_id, "event_count": len(events),
                "quantities": quantities, "accounting_basis": basis}
