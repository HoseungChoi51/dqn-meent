"""Application-owned SQLite records and an append-only event stream."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
import uuid


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
            """)

    @contextmanager
    def connection(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=15)
            db.row_factory = sqlite3.Row
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    def put(self, kind: str, record: dict, event: str | None = None) -> dict:
        encoded = json.dumps(record, allow_nan=False)
        with self.connection() as db:
            db.execute("INSERT INTO records VALUES (?, ?, ?, ?) ON CONFLICT(id) "
                       "DO UPDATE SET data=excluded.data, campaign_id=excluded.campaign_id",
                       (record["id"], kind, record.get("campaign_id"), encoded))
            if event:
                db.execute("INSERT INTO events(campaign_id,kind,created_at,data) VALUES (?,?,?,?)",
                           (record.get("campaign_id", record["id"] if kind == "campaign" else None),
                            event, now(), json.dumps({"record_id": record["id"]})))
        return record

    def get(self, record_id: str, kind: str | None = None) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT kind,data FROM records WHERE id=?", (record_id,)).fetchone()
        if row is None or (kind is not None and row["kind"] != kind):
            raise KeyError(record_id)
        return json.loads(row["data"])

    def list(self, kind: str, campaign_id: str | None = None) -> list[dict]:
        query, args = "SELECT data FROM records WHERE kind=?", [kind]
        if campaign_id is not None:
            query += " AND campaign_id=?"
            args.append(campaign_id)
        query += " ORDER BY rowid"
        with self.connection() as db:
            rows = db.execute(query, args).fetchall()
        return [json.loads(row["data"]) for row in rows]

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
        query += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self.connection() as db:
            rows = db.execute(query, args).fetchall()
        return [{**dict(row), "data": json.loads(row["data"])} for row in reversed(rows)]


def atomic_json(path: Path, value: dict):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, allow_nan=False, indent=2) + "\n")
    temporary.replace(path)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default
