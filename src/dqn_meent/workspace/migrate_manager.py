"""Add durable manager context without rewriting historical research records."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile

from .service import Workspace
from .store import now


HISTORICAL = ("campaign", "task", "hypothesis", "trial", "research_run", "decision", "message", "source")


def historical_digest(path):
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        rows = db.execute("SELECT id,kind,campaign_id,data FROM records ORDER BY id").fetchall()
    selected = [row for row in rows if row[1] in HISTORICAL]
    return hashlib.sha256(json.dumps(selected).encode()).hexdigest()


def backup_database(source, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError("Backup destination already exists")
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as original, sqlite3.connect(destination) as copied:
        original.backup(copied)


def migrate(directory):
    directory = Path(directory).resolve()
    before = historical_digest(directory / "workspace.sqlite3")
    workspace = Workspace(directory)
    campaigns = []
    for campaign in workspace.store.list("campaign"):
        context = workspace.memory.sync(campaign["id"])
        hypotheses = workspace.store.list("hypothesis", campaign["id"])
        campaigns.append({"id": campaign["id"], "name": campaign["name"], "context_id": context["id"],
            "implementation_compute_budget_seconds": campaign.get("implementation_compute_budget_seconds", 0),
            "readiness": [{"id": h["id"], "title": h["title"], **workspace.implementations.readiness(h)} for h in hypotheses]})
    after = historical_digest(directory / "workspace.sqlite3")
    if before != after:
        raise RuntimeError("Historical records changed during migration; inspect concurrent writers before continuing")
    report = {"schema": "campaign-manager-v1", "historical_digest": before, "historical_records_unchanged": True,
              "campaigns": campaigns, "created_at": now()}
    workspace.store.put("workspace_metadata", {"id": "manager_migration_v1", **report})
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", default="runs/workspace")
    parser.add_argument("--apply", action="store_true", help="Apply after making a database backup; default checks a disposable copy")
    parser.add_argument("--backup", type=Path, help="Required with --apply; choose a new .sqlite3 file")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    source = Path(args.directory).resolve() / "workspace.sqlite3"
    if args.apply:
        if not args.backup:
            parser.error("--apply requires --backup")
        # Take the same lease as the application; live experiments retain their
        # existing worker snapshots while the HTTP supervisor is stopped.
        import fcntl
        with (source.parent / "service.lock").open("a+") as lease:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            backup_database(source, args.backup)
            report = migrate(source.parent)
    else:
        with tempfile.TemporaryDirectory(prefix="grating-manager-migration-") as temporary:
            backup_database(source, Path(temporary) / source.name)
            report = migrate(temporary)
    encoded = json.dumps({"applied": args.apply, **report}, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(encoded)
    print(encoded)


if __name__ == "__main__":
    main()
