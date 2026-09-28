"""Durable literature retrieval receipts; network calls happen outside transactions."""
from copy import deepcopy
import threading

from optimization_framework.contracts.base import content_hash
from optimization_framework.storage.sqlite import now


def schedule(workspace, effect):
    """A slow paper fetch cannot hold the delivery lock for experiment controls."""
    if workspace.shutdown_event.is_set():
        return
    threads = workspace.source_threads
    for identity, thread in list(threads.items()):
        if not thread.is_alive():
            threads.pop(identity, None)
    previous = threads.get(effect["id"])
    if previous and previous.is_alive():
        return
    if len(threads) >= 2:
        return  # The durable pending effect is picked up on the next tick.

    def retrieve():
        try:
            deliver(workspace, effect)
        except Exception as error:
            # Reconcile a committed receipt even when the local acknowledgement
            # was lost. An actual failed retrieval needs an explicit new request.
            try:
                workspace.store.get("retrieval_" + effect["id"], "source_retrieval")
            except KeyError:
                with workspace.lock, workspace.store.transaction():
                    effect.update(status="failed", error=str(error), finished_at=now())
                    workspace.store.put("outbox", effect, "effect.failed")
                workspace.memory.issue(effect["campaign_id"], "source_retrieval", str(error), affected=effect["id"])
            else:
                deliver(workspace, effect)

    thread = threading.Thread(target=retrieve, name="sources-" + effect["id"], daemon=True)
    threads[effect["id"]] = thread
    thread.start()


def deliver(workspace, effect):
    receipt_id = "retrieval_" + effect["id"]
    try:
        receipt = workspace.store.get(receipt_id, "source_retrieval")
    except KeyError:
        receipt = None
    if receipt is None:
        from .evidence import search_literature, ingest_source
        workspace.agent_log.record(effect["campaign_id"], "tool.started", agent_id="literature_service", role="literature_service",
            task_id=effect["id"], event_key="source_start:" + effect["id"], summary=effect["kind"],
            payload={key: effect[key] for key in ("kind", "query", "provider", "limit", "identifier") if key in effect})
        if effect["kind"] == "literature_search":
            result = search_literature(effect["query"], limit=effect["limit"], provider=effect["provider"])
        else:
            result = {"sources": [ingest_source(effect["identifier"])]}
        with workspace.lock, workspace.store.transaction():
            result = deepcopy(result)
            for source in result["sources"]:
                upstream_id = source["id"]
                identity = effect["campaign_id"] + "_" + upstream_id
                try:
                    existing = workspace.store.get(identity, "source")
                except KeyError:
                    existing = None
                if existing:
                    original = {key: value for key, value in existing.items()
                                if key not in {"id", "campaign_id", "created_at", "retrieval_id", "content_hash"}}
                    incoming = {key: value for key, value in source.items() if key != "id"}
                    if original != incoming:
                        identity += "_" + content_hash(incoming)[:16]
                source.update(id=identity, campaign_id=effect["campaign_id"], created_at=now(), retrieval_id=receipt_id)
                try:
                    source.update(workspace.store.get(identity, "source"))
                except KeyError:
                    workspace.store.put_immutable("source", source, "source.retrieved")
            receipt = {"id": receipt_id, "campaign_id": effect["campaign_id"], "effect_id": effect["id"],
                       "request": {key: effect[key] for key in ("kind", "query", "provider", "limit", "identifier") if key in effect},
                       "result": result, "created_at": now()}
            workspace.store.put_immutable("source_retrieval", receipt, "source.retrieval_completed")
            effect.update(status="completed", receipt_id=receipt_id, sources=[s["id"] for s in result["sources"]], finished_at=now())
            workspace.store.put("outbox", effect, "effect.applied")
    else:
        effect.update(status="completed", receipt_id=receipt_id, sources=[s["id"] for s in receipt["result"]["sources"]], finished_at=now())
        workspace.store.put("outbox", effect, "effect.applied")
    return receipt
