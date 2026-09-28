"""Append-only receipt reconciliation and exact interval cost projections.

Known event deltas and receipt totals are equations between cumulative boundary
values. A query is exact only if these equations determine its requested sum;
an aggregate receipt never invents an allocation inside an unknown interval.
"""
from collections import defaultdict
import math

from optimization_framework.contracts.assets import CostReconciliation
from optimization_framework.contracts.base import content_hash
from optimization_framework.storage.sqlite import now


def close(first, second):
    return math.isclose(first, second, rel_tol=1e-9, abs_tol=1e-8)


def prefix(events, stop):
    selected = sorted((event for event in events if event["ordinal"] < stop), key=lambda event: event["ordinal"])
    if len(selected) != stop or any(event["ordinal"] != index for index, event in enumerate(selected)):
        raise ValueError("Cost reconciliation requires the complete original event prefix; import its missing evidence first")
    return selected


def event_digest(events):
    return content_hash([[event["id"], event["content_hash"]] for event in events])


class Equations:
    def __init__(self, events, reconciliations, axis):
        self.graph = defaultdict(list)
        for event in events:
            value = event["quantities"].get(axis)
            if value is not None:
                self.edge(event["ordinal"], event["ordinal"] + 1, value)
        for record in reconciliations:
            if axis in record["quantities"]:
                self.edge(0, record["stop"], record["quantities"][axis])
        self.component, self.potential = {}, {}
        for start in list(self.graph):
            if start in self.component:
                continue
            self.component[start], self.potential[start] = start, 0.
            pending = [start]
            while pending:
                node = pending.pop()
                for target, value in self.graph[node]:
                    expected = self.potential[node] + value
                    if target in self.component:
                        if not close(expected, self.potential[target]):
                            raise ValueError("Cost receipt contradicts existing measured expenditure")
                    else:
                        self.component[target], self.potential[target] = start, expected
                        pending.append(target)

    def edge(self, start, stop, value):
        self.graph[start].append((stop, value))
        self.graph[stop].append((start, -value))

    def position(self, node):
        # Unknown boundaries are independent variables, not zero expenditure.
        return self.component.get(node, ("unknown", node)), self.potential.get(node, 0.)

    def project(self, intervals):
        coefficients, total, known = defaultdict(int), 0., 0.
        for start, stop in intervals:
            if start == stop:
                continue
            for node, sign in ((start, -1), (stop, 1)):
                component, potential = self.position(node)
                coefficients[component] += sign
                total += sign * potential
            # Maximum disjoint measured coverage in this requested interval.
            # Zero-cost gaps are a lower bound only, never a complete total.
            best, candidates = 0., {}
            for node in sorted({start, stop, *(node for node in self.component if start < node < stop)}):
                component, potential = self.position(node)
                if component in candidates:
                    best = max(best, candidates[component] + potential)
                candidates[component] = max(candidates.get(component, -math.inf), best - potential)
            known += best
        exact = not any(coefficients.values())
        if exact and (total < -1e-8 or known > total and not close(known, total)):
            raise ValueError("Cost receipt would require negative expenditure within its prefix")
        return (max(0., total), True) if exact else (known, False)


def validate(events, reconciliations):
    sources, receipts = defaultdict(list), defaultdict(list)
    for event in events:
        sources[event["source_id"]].append(event)
    for raw in reconciliations:
        record = CostReconciliation(**{key: value for key, value in raw.items() if key != "content_hash"}).model_dump(mode="json")
        selected = prefix(sources[record["source_id"]], record["stop"])
        if (any(event["campaign_id"] != record["campaign_id"] for event in selected)
                or event_digest(selected) != record["event_digest"]):
            raise ValueError("Cost reconciliation does not match its original event identities and owner")
        receipts[record["source_id"]].append(record)
    for source, records in receipts.items():
        for axis in {axis for record in records for axis in record["quantities"]}:
            equations = Equations(sources[source], records, axis)
            for record in records:
                if axis in record["quantities"]:
                    equations.project([(0, record["stop"])])


def reconcile(catalog, source_id, stop, quantities, *, evidence_ids, authority, rationale, identity=None):
    with catalog.store.transaction():
        events = [event for event in catalog.store.list("cost_event") if event["source_id"] == source_id]
        selected = prefix(events, stop)
        values = {"campaign_id": selected[0]["campaign_id"], "source_id": source_id, "stop": stop,
            "event_digest": event_digest(selected), "quantities": quantities, "evidence_ids": sorted(set(evidence_ids)),
            "authority": authority, "rationale": rationale}
        identity = identity or "cost_reconciliation_" + content_hash(values)
        try:
            existing = catalog.store.get(identity, "cost_reconciliation")
        except KeyError:
            existing = None
        if existing:
            if any(existing[field] != value for field, value in values.items()):
                raise ValueError("This cost receipt identity already belongs to different evidence")
            return existing
        record = CostReconciliation(id=identity, **values, created_at=now()).model_dump(mode="json")
        previous = [row for row in catalog.store.list("cost_reconciliation") if row["source_id"] == source_id]
        validate(events, [*previous, record])
        return catalog.store.put_immutable("cost_reconciliation", record, "cost.reconciled")


def project(events, reconciliations, intervals, axes, *, unknown_provenance=0):
    sources, receipts = defaultdict(list), defaultdict(list)
    for event in events:
        if event["source_id"] in intervals:
            sources[event["source_id"]].append(event)
    for record in reconciliations:
        if record["source_id"] in intervals:
            receipts[record["source_id"]].append(record)
    quantities = {}
    for axis in axes:
        known, unknown = 0., 0
        for source, ranges in intervals.items():
            equations = Equations(sources[source], receipts[source], axis)
            subtotal, exact = equations.project(ranges)
            known += subtotal
            if not exact:
                required = sum(stop - start for start, stop in ranges)
                resolved = sum(equations.position(event["ordinal"])[0] == equations.position(event["ordinal"] + 1)[0]
                    for event in sources[source] if any(start <= event["ordinal"] < stop for start, stop in ranges))
                unknown += max(1, required - resolved)
        quantities[axis] = {"known": known, "unknown_events": unknown, "unknown_provenance": unknown_provenance,
                            "total": None if unknown or unknown_provenance else known}
    status = "complete" if all(value["total"] is not None for value in quantities.values()) else (
        "partial" if any(value["known"] or value["total"] is not None for value in quantities.values()) else "unknown")
    return quantities, {"status": status, "axes": list(axes),
                        "reconciliation_ids": sorted(record["id"] for rows in receipts.values() for record in rows)}


def sources(catalog, asset_id):
    graph = catalog.contributions([asset_id])
    events = defaultdict(list)
    snapshots, reconciliations = defaultdict(list), defaultdict(list)
    for event in catalog.store.list("cost_event"):
        if event["source_id"] in graph["intervals"]:
            events[event["source_id"]].append(event)
    for row in catalog.store.list("cost_snapshot"):
        snapshots[row["source_id"]].append(row)
    for row in catalog.store.list("cost_reconciliation"):
        reconciliations[row["source_id"]].append(row)
    result = []
    for source, intervals in graph["intervals"].items():
        rows = events[source]
        result.append({"source_id": source, "intervals": intervals,
            "event_count": len(rows), "observed_stop": max((event["ordinal"] + 1 for event in rows), default=0),
            "axes": sorted({axis for event in rows for axis in event["quantities"]}),
            "receipt_count": len(snapshots[source]), "reconciliation_count": len(reconciliations[source]),
            "latest_receipts": [{key: value for key, value in row.items() if key != "receipt"} for row in snapshots[source][-20:]],
            "latest_reconciliations": reconciliations[source][-20:]})
    return result
