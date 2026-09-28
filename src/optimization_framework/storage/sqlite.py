"""Application-owned SQLite records and an append-only event stream."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
import uuid

from optimization_framework.contracts.base import content_hash
from .artifacts import atomic_json


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def identifier(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class Store:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "workspace.sqlite3"
        self.lock = threading.RLock()
        self._local = threading.local()
        self.event_observer = None
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS records (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL,
                    campaign_id TEXT, data TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS records_kind_campaign
                    ON records(kind, campaign_id);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT, kind TEXT NOT NULL,
                    created_at TEXT NOT NULL, data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
                );
            """)
            db.execute("INSERT OR IGNORE INTO schema_migrations VALUES (1, ?)", (now(),))
            db.execute("CREATE TABLE IF NOT EXISTS cost_positions (source_id TEXT NOT NULL, ordinal INTEGER NOT NULL, cost_event_id TEXT NOT NULL UNIQUE, UNIQUE(source_id, ordinal))")
            if not db.execute("SELECT 1 FROM schema_migrations WHERE version=2").fetchone():
                for row in db.execute("SELECT data FROM records WHERE kind='cost_event'"):
                    event = json.loads(row[0])
                    db.execute("INSERT INTO cost_positions VALUES (?,?,?)", (event["source_id"], event["ordinal"], event["id"]))
                db.execute("INSERT INTO schema_migrations VALUES (2, ?)", (now(),))

    @contextmanager
    def connection(self):
        with self.lock:
            existing = getattr(self._local, "connection", None)
            if existing is not None:
                depth = getattr(self._local, "depth", 0) + 1
                self._local.depth = depth
                savepoint = f"nested_{depth}"
                existing.execute(f"SAVEPOINT {savepoint}")
                try:
                    yield existing
                    existing.execute(f"RELEASE SAVEPOINT {savepoint}")
                except BaseException:
                    existing.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    existing.execute(f"RELEASE SAVEPOINT {savepoint}")
                    raise
                finally:
                    self._local.depth -= 1
                return
            db = sqlite3.connect(self.path, timeout=15)
            db.row_factory = sqlite3.Row
            self._local.connection = db
            self._local.depth = 0
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                self._local.connection = None
                db.close()

    @contextmanager
    def transaction(self):
        """One command can commit its outcome and domain mutations atomically."""
        with self.connection() as db:
            if not db.in_transaction:
                db.execute("BEGIN IMMEDIATE")
            yield self

    @property
    def in_transaction(self):
        db = getattr(self._local, "connection", None)
        return db is not None and db.in_transaction

    def identity(self):
        """Stable local workspace identity, separate from portable evidence IDs."""
        with self.transaction():
            try:
                record = self.get("workspace_identity", "workspace_metadata")
            except KeyError:
                record = self.put_immutable("workspace_metadata", {"id": "workspace_identity", "schema_version": 1,
                    "workspace_id": identifier("workspace"), "created_at": now()})
            return record["workspace_id"]

    def put(self, kind: str, record: dict, event: str | None = None) -> dict:
        self.put_many([(kind, record, event)])
        return record

    def put_many(self, entries):
        """Commit related records and their journal entries together."""
        with self.connection() as db:
            for kind, record, event in entries:
                existing = db.execute("SELECT kind, data FROM records WHERE id=?", (record["id"],)).fetchone()
                if existing:
                    previous = json.loads(existing["data"])
                    if existing["kind"] != kind:
                        raise ValueError("A record identifier cannot change kind")
                    if previous.get("content_hash") and previous != record:
                        raise ValueError("Scientific records are immutable; create a linked new record")
                    if previous.get("content_hash") and previous == record:
                        continue
                if kind == "cost_event":
                    try:
                        db.execute("INSERT INTO cost_positions VALUES (?,?,?)", (record["source_id"], record["ordinal"], record["id"]))
                    except sqlite3.IntegrityError as exc:
                        raise ValueError("A physical cost event already owns this source ordinal") from exc
                db.execute("INSERT INTO records VALUES (?, ?, ?, ?) ON CONFLICT(id) "
                           "DO UPDATE SET data=excluded.data, campaign_id=excluded.campaign_id",
                           (record["id"], kind, record.get("campaign_id"), json.dumps(record, allow_nan=False)))
                if event:
                    cursor = db.execute("INSERT INTO events(campaign_id,kind,created_at,data) VALUES (?,?,?,?)",
                               (record.get("campaign_id", record["id"] if kind == "campaign" else None),
                                event, now(), json.dumps({"record_id": record["id"]})))
                    if self.event_observer:
                        self.event_observer(kind, record, event, cursor.lastrowid)

    def put_immutable(self, kind: str, record: dict, event: str | None = None) -> dict:
        value = dict(record)
        value.pop("content_hash", None)
        checksum = content_hash(value)
        if record.get("content_hash", checksum) != checksum:
            raise ValueError("Scientific record content hash mismatch")
        value["content_hash"] = checksum
        self.put(kind, value, event)
        return value

    def get(self, record_id: str, kind: str | None = None) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT kind,data FROM records WHERE id=?", (record_id,)).fetchone()
        if row is None or (kind is not None and row["kind"] != kind):
            raise KeyError(record_id)
        return json.loads(row["data"])

    def get_entry(self, record_id: str) -> dict:
        """Read the native record envelope; archived evidence is a separate API."""
        with self.connection() as db:
            row = db.execute("SELECT kind,data FROM records WHERE id=?", (record_id,)).fetchone()
        if row is None:
            raise KeyError(record_id)
        return {"kind": row["kind"], "data": json.loads(row["data"])}

    def list(self, kind: str, campaign_id: str | None = None) -> list[dict]:
        query, args = "SELECT data FROM records WHERE kind=?", [kind]
        if campaign_id is not None:
            query += " AND campaign_id=?"
            args.append(campaign_id)
        query += " ORDER BY rowid"
        with self.connection() as db:
            rows = db.execute(query, args).fetchall()
        return [json.loads(row["data"]) for row in rows]

    def record_position(self, record_id):
        """Durable insertion order, independent of a producer's claimed clock."""
        with self.connection() as db:
            row = db.execute("SELECT rowid FROM records WHERE id=?", (record_id,)).fetchone()
        if row is None:
            raise KeyError(record_id)
        return row[0]

    def committed_at(self, record_id, event):
        """Service-observed commit time, never the producer's claimed timestamp."""
        with self.connection() as db:
            row = db.execute("SELECT created_at FROM events WHERE kind=? AND json_extract(data, '$.record_id')=? ORDER BY id DESC LIMIT 1",
                             (event, record_id)).fetchone()
        return datetime.fromisoformat(row[0]).timestamp() if row else None

    def event(self, campaign_id: str | None, kind: str, data: dict) -> int:
        with self.connection() as db:
            cursor = db.execute("INSERT INTO events(campaign_id,kind,created_at,data) VALUES (?,?,?,?)",
                                (campaign_id, kind, now(), json.dumps(data, allow_nan=False)))
            return cursor.lastrowid

    def events(self, campaign_id: str | None = None, after: int = 0, limit: int = 200) -> list[dict]:
        query, args = "SELECT * FROM events WHERE id>?", [after]
        if campaign_id:
            query += " AND campaign_id=?"
            args.append(campaign_id)
        # Catch up from the cursor, not the newest page: otherwise an SSE client
        # silently loses events when its backlog exceeds one response.
        query += " ORDER BY id ASC LIMIT ?"
        args.append(limit)
        with self.connection() as db:
            rows = db.execute(query, args).fetchall()
        return [{**dict(row), "data": json.loads(row["data"])} for row in rows]

    def recent_events(self, campaign_id: str | None = None, limit: int = 200) -> list[dict]:
        query, args = "SELECT * FROM events", []
        if campaign_id:
            query += " WHERE campaign_id=?"
            args.append(campaign_id)
        query += " ORDER BY id DESC LIMIT ?"
        with self.connection() as db:
            rows = db.execute(query, [*args, limit]).fetchall()
        return [{**dict(row), "data": json.loads(row["data"])} for row in reversed(rows)]


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default
