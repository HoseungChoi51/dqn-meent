"""Service-owned asset metadata; content blobs remain behind the artifact store."""
from optimization_framework.contracts.assets import Asset, CostEvent, ReuseDecision
from optimization_framework.storage.artifacts import LocalArtifactStore
from optimization_framework.storage import history
from optimization_framework.contracts.experiments import ArtifactReference


def merged_intervals(intervals):
    result = []
    for start, stop in sorted(intervals):
        if stop == start:
            continue
        if result and start <= result[-1][1]:
            result[-1][1] = max(stop, result[-1][1])
        else:
            result.append([start, stop])
    return result


def contributions(assets, asset_ids):
    """Resolve immutable contribution identities against an already read catalog."""
    seen, pending, intervals, unknown = set(), set(), {}, []

    def visit(asset_id):
        if asset_id in pending:
            raise ValueError("Asset dependency cycle")
        if asset_id in seen:
            return
        if asset_id not in assets:
            raise ValueError(f"Missing upstream asset {asset_id}")
        pending.add(asset_id)
        asset = assets[asset_id]
        if asset["cost_provenance"] != "complete":
            unknown.append(asset_id)
        for dependency_id in asset["dependency_ids"]:
            visit(dependency_id)
        for cost in asset["costs"]:
            intervals.setdefault(cost["source_id"], []).append([cost["start"], cost["stop"]])
        pending.remove(asset_id)
        seen.add(asset_id)

    for asset_id in asset_ids:
        visit(asset_id)
    return {"asset_ids": sorted(seen), "intervals": {key: merged_intervals(value) for key, value in intervals.items()},
            "unknown_provenance_asset_ids": sorted(unknown)}


class AssetCatalog:
    def __init__(self, store, artifacts=None):
        self.store = store
        self.artifacts = artifacts or LocalArtifactStore(store.directory / "artifacts")

    def record_cost(self, event):
        event = event if isinstance(event, CostEvent) else CostEvent(**event)
        # A stable physical source ordinal must never be billed twice, even
        # under a different campaign or event name during import/reconciliation.
        return self.store.put_immutable("cost_event", event.model_dump(mode="json"), "cost.recorded")

    def availability(self, asset):
        """Local byte availability is independent of the immutable producer record."""
        if not asset.get("artifacts"):
            return {"status": "unavailable" if asset.get("availability") == "unavailable" else "available"}
        external = False
        try:
            for raw in asset["artifacts"]:
                reference = ArtifactReference(**raw)
                if hasattr(self.artifacts, "resolve"):
                    path = self.artifacts.resolve(reference)
                    if path.stat().st_size != reference.bytes:
                        raise ValueError("Artifact size differs from its recorded identity")
                    external |= hasattr(self.artifacts, "blobs") and not path.is_relative_to(self.artifacts.blobs)
                else:
                    self.artifacts.verify(reference)
        except (OSError, ValueError) as exc:
            return {"status": "unavailable", "reason": str(exc)}
        return {"status": "external" if external else "available"}

    def visible(self, campaign_id=None):
        if campaign_id is None:
            return self.store.list("asset")
        values = {asset["id"]: asset for asset in self.store.list("asset", campaign_id)}
        for association in self.store.list("imported_asset", campaign_id):
            values[association["asset_id"]] = self.store.get(association["asset_id"], "asset")
        return list(values.values())

    def publish(self, asset):
        asset = asset if isinstance(asset, Asset) else Asset(**asset)
        with self.store.lock:
            for dependency_id in asset.dependency_ids:
                if dependency_id == asset.id:
                    raise ValueError("An asset cannot depend on itself")
                self.store.get(dependency_id, "asset")
                self.contributions([dependency_id])
            for reference in asset.artifacts:
                if asset.availability != "unavailable":
                    self.artifacts.verify(reference)
            return self.store.put_immutable("asset", asset.model_dump(mode="json"), "asset.published")

    def decide(self, decision):
        decision = decision if isinstance(decision, ReuseDecision) else ReuseDecision(**decision)
        asset = self.store.get(decision.asset_id, "asset")
        study = self.store.get(decision.study_id, "study")
        if study["campaign_id"] != decision.campaign_id:
            raise ValueError("Reuse decision belongs to another study's campaign")
        if decision.decision == "reuse" and decision.intended_use == "optimizer_input" and self.availability(asset)["status"] == "unavailable":
            raise ValueError("The referenced input asset is unavailable")
        return self.store.put_immutable("reuse_decision", decision.model_dump(mode="json"), "asset.reuse_decided")

    def inputs(self, campaign_id, study_id, asset_ids, decision_ids, *, cohort_id=None):
        if len(set(asset_ids)) != len(asset_ids):
            raise ValueError("Declare each initial asset once")
        decisions = [self.store.get(item, "reuse_decision") for item in decision_ids]
        result = []
        for asset_id in asset_ids:
            matches = [item for item in decisions if item["asset_id"] == asset_id and item["campaign_id"] == campaign_id
                       and item["study_id"] == study_id and item["decision"] == "reuse" and item["intended_use"] == "optimizer_input"]
            if not matches:
                raise ValueError(f"Asset {asset_id} needs an explicit optimizer-input reuse decision for this study")
            asset = self.store.get(asset_id, "asset")
            self.check_input_exposure(asset, cohort_id=cohort_id)
            if self.availability(asset)["status"] == "unavailable":
                raise ValueError(f"Input asset {asset_id} is unavailable")
            for reference in Asset(**{key: value for key, value in asset.items() if key != "content_hash"}).artifacts:
                self.artifacts.verify(reference)
            result.append(asset)
        return result

    def check_input_exposure(self, asset, *, cohort_id=None, seen=None):
        seen = set() if seen is None else seen
        if asset["id"] in seen:
            return
        seen.add(asset["id"])
        producers = history.producers(self.store, asset)
        if not producers and str(asset.get("producer_id", "")).startswith("trial_"):
            raise ValueError("Historical producer exposure is unavailable; import its evidence before reuse")
        for producer in producers:
            protected = producer.get("protected_cohort_id")
            if protected and protected != cohort_id:
                if not any(row["protocol_id"] == protected for row in history.releases(self.store, producer["campaign_id"])):
                    raise ValueError("Protected cohort evidence cannot become another study's input before release")
        for identity in asset["dependency_ids"]:
            self.check_input_exposure(self.store.get(identity, "asset"), cohort_id=cohort_id, seen=seen)

    def check_export_exposure(self, asset, seen=None):
        seen = set() if seen is None else seen
        if asset["id"] in seen:
            return
        seen.add(asset["id"])
        for producer in history.producers(self.store, asset):
            released = {identity for release in history.releases(self.store, producer["campaign_id"]) for identity in release["trial_ids"]}
            if (producer.get("task_split") in {"test", "confirmation", "heldout"} or producer.get("protected_cohort_id")) and producer["id"] not in released:
                raise ValueError("Protected evidence requires its result release before portable export")
        for identity in asset["dependency_ids"]:
            self.check_export_exposure(self.store.get(identity, "asset"), seen)

    def manager_references(self, campaign_id, study_id):
        """Read only individually declared reference evidence, never worker inputs."""
        import json
        decisions = [item for item in self.store.list("reuse_decision", campaign_id)
            if item["study_id"] == study_id and item["intended_use"] == "manager_evidence" and item["decision"] in {"reference", "reuse"}]

        def protected(asset, seen):
            if asset["id"] in seen:
                return False
            seen.add(asset["id"])
            if asset.get("locked"):
                return True
            if asset.get("producer_id"):
                producers = history.producers(self.store, asset)
                if not producers and str(asset["producer_id"]).startswith("trial_"):
                    return True
                for producer in producers:
                    released = {identity for release in history.releases(self.store, producer.get("campaign_id")) for identity in release["trial_ids"]}
                    if (producer.get("task_split") in {"test", "confirmation", "heldout"} or producer.get("protected_cohort_id")) and producer.get("id") not in released:
                        return True
            return any(protected(self.store.get(identity, "asset"), seen) for identity in asset["dependency_ids"])

        result = []
        for decision in decisions[-20:]:
            asset = self.store.get(decision["asset_id"], "asset")
            reference = {"asset_id": asset["id"], "decision_id": decision["id"], "title": asset["title"],
                "applicability": asset["applicability"], "exposure_status": asset["exposure_status"]}
            if protected(asset, set()):
                reference = {"asset_id": asset["id"], "decision_id": decision["id"],
                    "unavailable_reason": "Heldout measurements require their confirmation protocol's result release"}
            elif len(json.dumps(asset["payload"]).encode()) <= 16000:
                reference.update(payload=asset["payload"], artifact_references=asset["artifacts"])
            else:
                reference.update(artifact_references=asset["artifacts"],
                    unavailable_reason="Payload exceeds the bounded manager context; record a scoped finding linked to this evidence")
            result.append(reference)
        return result

    def contributions(self, asset_ids):
        assets = {item["id"]: item for item in self.store.list("asset")}
        return contributions(assets, asset_ids)

    def cost_view(self):
        """Read a fresh cost snapshot for one report; never cache it on the catalog."""
        from optimization_framework.assets.cost_view import CostView
        return CostView(self)

    @staticmethod
    def summarize_costs(events, axes, *, missing=0, unknown_provenance=0):
        result = {}
        for axis in axes:
            known = sum(event["quantities"].get(axis) or 0 for event in events)
            unknown = sum(event["quantities"].get(axis) is None for event in events) + missing
            result[axis] = {"known": known, "unknown_events": unknown, "unknown_provenance": unknown_provenance,
                            "total": None if unknown or unknown_provenance else known}
        return result

    def actual_costs(self, campaign_id, axes=("evaluation_requests", "solver_executions", "worker_seconds")):
        events = self.store.list("cost_event", campaign_id)
        intervals = {}
        for event in events:
            intervals.setdefault(event["source_id"], []).append([event["ordinal"], event["ordinal"] + 1])
        intervals = {source: merged_intervals(ranges) for source, ranges in intervals.items()}
        from optimization_framework.assets.accounting import project
        quantities, basis = project(events, self.store.list("cost_reconciliation"), intervals, axes)
        return {"view": "actual_expenditure", "campaign_id": campaign_id, "event_count": len(events),
                "quantities": quantities, "accounting_basis": basis}

    def attributed_costs(self, asset_ids, axes=("evaluation_requests", "solver_executions", "worker_seconds")):
        graph = self.contributions(asset_ids)
        events = self.store.list("cost_event")
        selected, covered = [], 0
        for event in events:
            if any(start <= event["ordinal"] < stop for start, stop in graph["intervals"].get(event["source_id"], [])):
                selected.append(event)
                covered += 1
        required = sum(stop-start for intervals in graph["intervals"].values() for start, stop in intervals)
        from optimization_framework.assets.accounting import project
        quantities, basis = project(events, self.store.list("cost_reconciliation"), graph["intervals"], axes,
            unknown_provenance=len(graph["unknown_provenance_asset_ids"]))
        return {"view": "full_attributed_cost", **graph, "event_count": len(selected),
                "quantities": quantities, "accounting_basis": basis}
