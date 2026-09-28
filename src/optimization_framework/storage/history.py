"""Explicit read-only historical records, kept outside operational queues."""
from optimization_framework.contracts.bundles import BundleRecord, RecordKey


def archive_identity(reference):
    return "archived_" + reference.key


def exact(store, reference):
    """Select an admitted snapshot without guessing among a producer's revisions."""
    reference = reference if isinstance(reference, RecordKey) else RecordKey(**reference)
    try:
        value = store.get(archive_identity(reference), "archived_record")
    except KeyError:
        raise ValueError("The selected historical snapshot has not been imported") from None
    record = BundleRecord(reference=value["reference"], data=value["data"])
    if record.reference != reference:
        raise ValueError("The historical snapshot does not match its selected identity")
    return record


def validate_conflicts(store, records):
    for record in records:
        identity = archive_identity(record.reference)
        try:
            previous = store.get(identity, "archived_record")
        except KeyError:
            continue
        if previous["reference"] != record.reference.model_dump(mode="json") or previous["data"] != record.data:
            raise ValueError("Archived identity already refers to different evidence")


def publish(store, record):
    record = record if isinstance(record, BundleRecord) else BundleRecord(**record)
    value = {"id": archive_identity(record.reference), "schema_version": 1,
             "reference": record.reference.model_dump(mode="json"), "data": record.data}
    return store.put_immutable("archived_record", value, "history.record_archived")


def find(store, identity, kind, *, source_id=None):
    """Return every admitted snapshot; callers choose the relevant evidence basis."""
    return [record for record in store.list("archived_record")
            if record["reference"]["id"] == identity and record["reference"]["kind"] == kind
            and (source_id is None or record["reference"]["source_id"] == source_id)]


def snapshots(store, kind, *, campaign_id=None):
    """Evidence readers opt in. Scheduler/repository get/list remain unchanged."""
    return [record["data"] for record in store.list("archived_record")
            if record["reference"]["kind"] == kind
            and (campaign_id is None or record["data"].get("campaign_id") == campaign_id)]


def producers(store, asset):
    """Use every known producer snapshot when enforcing protected-evidence rules."""
    identity = asset.get("producer_id")
    if not identity:
        return []
    result = [record["data"] for record in find(store, identity, "trial")]
    try:
        result.append(store.get(identity, "trial"))
    except KeyError:
        pass
    return result


def releases(store, campaign_id):
    return store.list("confirmation_release", campaign_id) + snapshots(store, "confirmation_release", campaign_id=campaign_id)
