"""Portable results retain why the main procedure differs from its prototype."""
import json

from optimization_framework.campaigns.memory import CampaignMemory
from optimization_framework.contracts.requests import StudyInput, TrialInput
from optimization_framework.storage import history
from optimization_framework.storage.bundles import Reader
from test_framework_bundles import export, inspect, publish
from test_framework_provenance import campaign, finish


def test_final_allocation_provenance_survives_export_import_and_reexport(tmp_path, monkeypatch):
    source, owner, task = campaign(tmp_path / "source")
    prototype = source.create_trial(TrialInput(campaign_id=owner, task_id=task,
        algorithm="coordinate", max_steps=3, wall_seconds=5))
    finish(source, prototype)
    study = source.create_study(owner, StudyInput(goal="Longer allocated confirmation", scope="confirmation",
        confirmation_kind="seed_replication", prototype_trial_ids=[prototype["id"]], seeds=[19],
        prototype_allocations={prototype["id"]: {"expected_control_revision": 0,
            "max_steps": 9, "wall_seconds": 10, "schedule_steps": 9}}))
    protocol_id = study["confirmation"]["id"]
    identity = source.confirmations.schedule(source, protocol_id)["created_trial_ids"][0]
    finish(source, source.store.get(identity, "trial"))
    source.confirmations.release(protocol_id)
    binding = source.store.list("confirmation_allocation_binding")[0]
    compact = json.loads(CampaignMemory._text("confirmation_allocation_binding", binding))
    assert compact["protocol_id"] == protocol_id
    assert "source_procedure" not in next(iter(compact["methods"].values()))
    assert next(iter(compact["methods"].values()))["allocation_overrides"]["max_steps"] == 9
    roots = source.store.get(identity, "trial")["output_asset_ids"]
    _, path = export(source, owner, roots)
    with Reader(path) as bundle:
        records = list(bundle.records())
        portable = next(record for record in records if record.reference.kind == "confirmation_allocation_binding")
        assert portable.data == binding
        protocol = next(record for record in records if record.reference.id == protocol_id)
        assert any(edge.source == protocol.reference.key and edge.dependency == portable.reference.key
            and edge.role == "final_allocation" for edge in bundle.manifest.edges)
        assert any(record.reference.id == prototype["id"] for record in records)

    destination, current, _ = campaign(tmp_path / "destination")
    inspection = inspect(destination, current, path)
    publish(destination, current, inspection)
    archived = history.find(destination.store, binding["id"], "confirmation_allocation_binding")
    assert len(archived) == 1 and archived[0]["data"] == binding
    assert not destination.store.list("confirmation_allocation_binding")
    assert not destination.store.list("trial")
    _, reexported = export(destination, current, roots, key="reexport")
    with Reader(reexported) as bundle:
        copied = next(record for record in bundle.records() if record.reference.kind == "confirmation_allocation_binding")
        assert copied == portable

    # Damage to the source-to-final derivation must be declared, not silently
    # turn the exported confirmation into an apparently complete evidence graph.
    get_entry = source.store.get_entry
    def missing_binding(identity, *args, **kwargs):
        if identity == binding["id"]:
            raise KeyError(identity)
        return get_entry(identity, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(source.store, "get_entry", missing_binding)
        _, incomplete = export(source, owner, roots, key="missing_allocation_export")
    with Reader(incomplete) as bundle:
        assert any(item.id == binding["id"] for item in bundle.manifest.missing)
