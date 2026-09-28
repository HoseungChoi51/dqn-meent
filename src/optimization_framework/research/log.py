"""Durable observable agent work, with a rebuildable, tail-able JSONL projection.

These events are deliberately separate from application events: liveness and
provider transcripts must not wake the scientific manager or enter its context.
Only ordinary model output is recorded, never provider-private reasoning.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time

from optimization_framework.storage.sqlite import identifier, now


INLINE_BYTES = 64 * 1024
PAGE_BYTES = 2 * 1024 * 1024
REDACTED = "[REDACTED]"
SECRET_KEY = re.compile(r"^(?:authorization|proxy.authorization|cookie|set.cookie|api.?key|access.?token|refresh.?token|id.?token|password|passwd|secret|client.?secret|credentials|environment|environ|env)$", re.I)
SECRET_TEXT = re.compile(r"(?i)(\b(?:bearer\s+|(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)\s*[=:]\s*[\"']?))([^\s\"',;}]+)")
PRIVATE_KEYS = {"chain_of_thought", "reasoning_content", "encrypted_content", "private_reasoning"}


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)


def redact(value, secrets=()):
    """Redact before hashing/persistence, including JSON nested inside messages."""
    if isinstance(value, dict):
        return {str(k): (REDACTED if SECRET_KEY.match(str(k)) or str(k) in PRIVATE_KEYS else redact(v, secrets))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item, secrets) for item in value]
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError):
            decoded = None
        if isinstance(decoded, (dict, list)):
            return encode(redact(decoded, secrets))
        for secret in secrets:
            if secret and len(secret) >= 6:
                value = value.replace(secret, REDACTED)
        value = SECRET_TEXT.sub(lambda m: m[1] + REDACTED, value)
        return re.sub(r"\bsk-[A-Za-z0-9_-]{16,}", REDACTED, value)
    return value


class AgentLog:
    def __init__(self, store, *, scope_directory="campaigns", origin_service="workspace"):
        self.store = store
        self.scope_directory = scope_directory
        self.origin_service = origin_service
        self.lock = threading.RLock()
        self.heartbeat_lock = threading.Lock()
        self.heartbeats = {}
        self.initialize()

    def initialize(self):
        with self.store.connection() as db:
            # Numbered, additive migration; old records and update cursors stay intact.
            db.executescript("""
                CREATE TABLE IF NOT EXISTS agent_payloads (
                    digest TEXT PRIMARY KEY, body BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_events (
                    campaign_id TEXT NOT NULL, seq INTEGER NOT NULL,
                    event_id TEXT NOT NULL UNIQUE, event_key TEXT NOT NULL,
                    body TEXT NOT NULL, payload_ref TEXT,
                    PRIMARY KEY(campaign_id, seq), UNIQUE(campaign_id, event_key)
                );
                CREATE TABLE IF NOT EXISTS agent_log_offsets (
                    campaign_id TEXT NOT NULL, seq INTEGER NOT NULL,
                    start INTEGER NOT NULL, end INTEGER NOT NULL,
                    PRIMARY KEY(campaign_id, seq)
                );
                CREATE TABLE IF NOT EXISTS agent_log_projection (
                    campaign_id TEXT PRIMARY KEY, seq INTEGER NOT NULL DEFAULT 0,
                    bytes INTEGER NOT NULL DEFAULT 0, error TEXT
                );
            """)
            db.execute("INSERT OR IGNORE INTO schema_migrations VALUES (3, ?)", (now(),))

    def directory(self, campaign_id):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,160}", campaign_id):
            raise ValueError("Invalid agent log scope")
        return self.store.directory / self.scope_directory / campaign_id / "agents"

    def record(self, campaign_id, event_type, *, agent_id="runtime", role="runtime", payload=None,
               event_key=None, summary="", secrets=(), **fields):
        self.directory(campaign_id)
        event_key = event_key or identifier("agent_operation")
        with self.store.connection() as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT body FROM agent_events WHERE campaign_id=? AND event_key=?",
                             (campaign_id, event_key)).fetchone()
            if old:
                return json.loads(old[0])
            seq = db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM agent_events WHERE campaign_id=?", (campaign_id,)).fetchone()[0]
            stamp = now()
            content = redact(payload if payload is not None else {}, secrets)
            raw = encode(content).encode("utf-8")
            checksum = hashlib.sha256(raw).hexdigest()
            metadata = redact(fields, secrets)
            event = {**metadata, "schema_version": 1, "campaign_id": campaign_id, "seq": seq,
                     "event_id": identifier("agent_event"), "occurred_at": fields.get("occurred_at", stamp),
                     "recorded_at": stamp, "origin_service": fields.get("origin_service", self.origin_service),
                     "agent_id": agent_id, "role": role, "event_type": event_type,
                     "summary": redact(summary, secrets), "payload_bytes": len(raw), "payload_hash": checksum,
                     "redaction": "credentials and private reasoning excluded"}
            reference = None
            if len(raw) > INLINE_BYTES:
                reference = checksum
                db.execute("INSERT OR IGNORE INTO agent_payloads VALUES (?,?)", (checksum, raw))
                event.update(payload_ref=checksum, payload_preview=raw[:1024].decode("utf-8", errors="replace"))
            else:
                event["payload"] = content
            db.execute("INSERT INTO agent_events VALUES (?,?,?,?,?,?)", (campaign_id, seq, event["event_id"], event_key, encode(event), reference))
        # Projection never runs inside a domain transaction: an uncommitted event
        # must not escape into a file or SSE. The service projector handles it.
        return event

    def observe(self, kind, record, event, event_id):
        """Record domain handoffs in their original transaction, without recursion."""
        campaign_id = record.get("campaign_id")
        if not campaign_id:
            return
        actor = "campaign_manager"
        if kind == "research_run" and event in {"research.started", "research.attempt_started", "research.stale_result", "research.interrupted", "research.failed", "research.completed"}:
            payload = {k: record[k] for k in ("request", "status", "error", "guidance_revision", "manager_input_ids") if k in record}
            event_type = "task.assigned" if event == "research.started" else event
        elif kind == "research_result":
            payload = record.get("result", {})
            event_type = "task.result"
        elif kind in {"action", "work_command", "command_rejection", "manager_issue", "manager_input", "source_retrieval"}:
            payload, event_type = record, event
            actor = "command_service" if kind == "work_command" else actor
        elif kind == "trial" and event not in {"trial.progress", "trial.updated"}:
            payload = {k: record[k] for k in ("id", "status", "algorithm", "result", "error", "attempt", "hypothesis_id", "experiment_spec_id") if k in record}
            event_type, actor = event, "experiment_scheduler"
        else:
            return
        task_id = record.get("research_run_id", record["id"])
        self.record(campaign_id, event_type, agent_id=actor, role=actor, payload=payload,
                    task_id=task_id, event_key=f"domain:{event_id}", summary=event.replace(".", " "),
                    record_id=record["id"], application_event_id=event_id,
                    **({"from_agent": "campaign_manager", "to_agent": task_id} if event_type == "task.assigned" else {}))

    def capture(self, campaign_id, task_id, event, **fields):
        """Common adapter callback used by workspace and implementation workers."""
        kind = event["type"]
        role = event.get("role", "runtime")
        call_id = event.get("reservation_id") or event.get("call_id")
        if kind == "provider_progress":
            key = (campaign_id, task_id, call_id, role)
            with self.heartbeat_lock:
                current = time.monotonic()
                if current - self.heartbeats.get(key, float("-inf")) < 30:
                    return None
                self.heartbeats[key] = current
            event = {"type": "provider_progress", "role": role, "summary": "Runtime is waiting for the provider; no new model output reported."}
        mapping = {"provider_call_reserved": "provider.reserved", "provider_request": "provider.request",
                   "provider_response": "provider.response", "provider_error": "provider.error",
                   "provider_progress": "runtime.liveness", "provider_call_cancelled_before_send": "provider.cancelled_before_send"}
        key = f"{task_id}:{call_id}:{kind}" if call_id and kind != "provider_progress" else None
        return self.record(campaign_id, mapping.get(kind, kind), agent_id=f"{task_id}:{role}", role=role,
                           task_id=task_id, call_id=call_id, event_key=key, summary=event.get("summary", kind),
                           payload={k: v for k, v in event.items() if k not in {"type", "role", "summary"}}, **fields)

    @contextmanager
    def _writer(self, campaign_id):
        root = self.directory(campaign_id)
        root.mkdir(parents=True, exist_ok=True)
        # A short advisory lock also covers read-only API instances and CLI use.
        # The workspace service is the normal projector; readers never dispatch.
        with self.lock, (root / ".projection.lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield root
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _payload_file(self, root, reference, db):
        path = root / "payloads" / f"{reference}.json"
        if path.is_file():
            return
        row = db.execute("SELECT body FROM agent_payloads WHERE digest=?", (reference,)).fetchone()
        if row is None:
            raise ValueError("Agent payload is missing")
        path.parent.mkdir(exist_ok=True)
        temporary = path.with_suffix(".tmp")
        with temporary.open("wb") as file:
            file.write(row[0])
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(path)

    def project(self, campaign_id, *, limit=500):
        if self.store.in_transaction:
            raise RuntimeError("Agent log projection requires committed events")
        try:
            with self._writer(campaign_id) as root, self.store.connection() as db:
                path = root / "trace.jsonl"
                state = db.execute("SELECT * FROM agent_log_projection WHERE campaign_id=?", (campaign_id,)).fetchone()
                seq, offset = (state["seq"], state["bytes"]) if state else (0, 0)
                path.touch(exist_ok=True)
                # Lost file/index can be rebuilt. A non-empty truncated file is
                # preserved before reconstruction, rather than silently edited.
                if path.stat().st_size < offset:
                    if path.stat().st_size:
                        path.replace(root / (identifier("corrupt") + ".jsonl"))
                    path.touch()
                    seq = offset = 0
                    db.execute("DELETE FROM agent_log_offsets WHERE campaign_id=?", (campaign_id,))
                with path.open("r+b") as file:
                    # Validate the last acknowledged line in bounded time.
                    last = db.execute("SELECT start,end FROM agent_log_offsets WHERE campaign_id=? AND seq=?", (campaign_id, seq)).fetchone()
                    if last:
                        file.seek(last["start"])
                        expected = db.execute("SELECT body FROM agent_events WHERE campaign_id=? AND seq=?", (campaign_id, seq)).fetchone()
                        if expected is None or file.read(last["end"] - last["start"]) != (expected[0] + "\n").encode():
                            raise ValueError("Agent log differs from committed events; preserve it before rebuilding")
                    file.seek(offset)
                    # Recover an append that reached disk before its DB cursor.
                    while True:
                        start = file.tell()
                        line = file.readline(INLINE_BYTES * 2)
                        if not line:
                            break
                        if not line.endswith(b"\n"):
                            if len(line) >= INLINE_BYTES * 2:
                                raise ValueError("Unexpected oversized agent log line")
                            file.truncate(start)
                            break
                        expected = db.execute("SELECT body FROM agent_events WHERE campaign_id=? AND seq=?", (campaign_id, seq + 1)).fetchone()
                        if expected is None or line != (expected[0] + "\n").encode():
                            raise ValueError("Agent log has an unexpected completed line; preserve it before rebuilding")
                        seq += 1
                        offset = file.tell()
                        db.execute("INSERT OR REPLACE INTO agent_log_offsets VALUES (?,?,?,?)", (campaign_id, seq, start, offset))
                    rows = db.execute("SELECT seq,body,payload_ref FROM agent_events WHERE campaign_id=? AND seq>? ORDER BY seq LIMIT ?",
                                      (campaign_id, seq, limit)).fetchall()
                    file.seek(offset)
                    for row in rows:
                        if row["payload_ref"]:
                            self._payload_file(root, row["payload_ref"], db)
                        start = file.tell()
                        file.write((row["body"] + "\n").encode("utf-8"))
                        seq, offset = row["seq"], file.tell()
                        db.execute("INSERT OR REPLACE INTO agent_log_offsets VALUES (?,?,?,?)", (campaign_id, seq, start, offset))
                    file.flush()
                    os.fsync(file.fileno())
                db.execute("INSERT INTO agent_log_projection VALUES (?,?,?,NULL) ON CONFLICT(campaign_id) DO UPDATE SET seq=excluded.seq,bytes=excluded.bytes,error=NULL",
                           (campaign_id, seq, offset))
        except (OSError, ValueError) as exc:
            with self.store.connection() as db:
                db.execute("INSERT INTO agent_log_projection(campaign_id,error) VALUES (?,?) ON CONFLICT(campaign_id) DO UPDATE SET error=excluded.error",
                           (campaign_id, f"{type(exc).__name__}: {redact(str(exc))}"))
        return self.status(campaign_id)

    def project_pending(self):
        with self.store.connection() as db:
            scopes = [row[0] for row in db.execute("SELECT e.campaign_id FROM agent_events e LEFT JOIN agent_log_projection p ON e.campaign_id=p.campaign_id GROUP BY e.campaign_id HAVING MAX(e.seq)>COALESCE(MAX(p.seq),0) OR MAX(p.error) IS NOT NULL")]
        for scope in scopes:
            self.project(scope)

    def status(self, campaign_id):
        with self.store.connection() as db:
            state = db.execute("SELECT * FROM agent_log_projection WHERE campaign_id=?", (campaign_id,)).fetchone()
            latest = db.execute("SELECT COALESCE(MAX(seq),0) FROM agent_events WHERE campaign_id=?", (campaign_id,)).fetchone()[0]
        projected = state["seq"] if state else 0
        return {"path": str(self.directory(campaign_id) / "trace.jsonl"), "latest_seq": latest,
                "projected_seq": projected, "lag": latest - projected, "error": state["error"] if state else None}

    def page(self, campaign_id, *, after=None, before=None, limit=200, max_bytes=PAGE_BYTES):
        if after is not None and before is not None:
            raise ValueError("Use either after or before, not both")
        if any(value is not None and value < 0 for value in (after, before)):
            raise ValueError("Log cursors must be nonnegative")
        limit = max(1, min(int(limit), 500))
        max_bytes = max(INLINE_BYTES * 2, min(int(max_bytes), PAGE_BYTES))
        status = self.status(campaign_id)
        if status["error"]:
            return {**status, "lines": [], "events": [], "next_cursor": after or 0, "oldest_cursor": None, "has_older": False}
        direction = "ASC" if after is not None else "DESC"
        condition, cursor = ("seq>?", after) if after is not None else ("seq<?", before if before is not None else status["projected_seq"] + 1)
        with self.store.connection() as db:
            rows = db.execute(f"SELECT seq,start,end FROM agent_log_offsets WHERE campaign_id=? AND {condition} AND seq<=? ORDER BY seq {direction} LIMIT ?",
                              (campaign_id, cursor, status["projected_seq"], limit)).fetchall()
        chosen, size = [], 0
        for row in rows:
            if size + row["end"] - row["start"] > max_bytes:
                break
            chosen.append(row)
            size += row["end"] - row["start"]
        lines = []
        if chosen:
            with Path(status["path"]).open("rb") as file:
                for row in sorted(chosen, key=lambda value: value["seq"]):
                    file.seek(row["start"])
                    lines.append(file.read(row["end"] - row["start"]).decode("utf-8"))
        events = [json.loads(line) for line in lines]
        return {**status, "lines": lines, "events": events, "next_cursor": events[-1]["seq"] if events else (after or 0),
                "oldest_cursor": events[0]["seq"] if events else None, "has_older": bool(events and events[0]["seq"] > 1)}

    def payload(self, campaign_id, reference):
        if not re.fullmatch(r"[a-f0-9]{64}", reference):
            raise KeyError(reference)
        with self.store.connection() as db:
            row = db.execute("SELECT p.body FROM agent_payloads p WHERE p.digest=? AND EXISTS (SELECT 1 FROM agent_events e WHERE e.campaign_id=? AND e.payload_ref=p.digest)",
                             (reference, campaign_id)).fetchone()
        if not row:
            raise KeyError(reference)
        return bytes(row[0])

    def download(self, campaign_id):
        # A fixed byte high-water mark gives a coherent, bounded-memory snapshot.
        with self.store.connection() as db:
            row = db.execute("SELECT bytes,seq FROM agent_log_projection WHERE campaign_id=?", (campaign_id,)).fetchone()
        size, seq = (row[0], row[1]) if row else (0, 0)
        path = self.directory(campaign_id) / "trace.jsonl"
        def chunks():
            remaining = size
            if not remaining:
                return
            with path.open("rb") as file:
                while remaining:
                    chunk = file.read(min(64 * 1024, remaining))
                    if not chunk:
                        raise OSError("Log changed during download")
                    remaining -= len(chunk)
                    yield chunk
        return seq, chunks()
