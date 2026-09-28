"""Capture dirty source trees and online SQLite backups without copying trajectories.

This is deliberately independent of the application and its schema. Only explicitly
selected source directories are copied; credentials and provider configuration are
not part of the snapshot. Historical blobs are inventoried by content identity.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess


SOURCE_DIRS = ("src", "tests", "docs", "scripts", "deploy", "examples", "configs", "data", "frontend")
EXCLUDED = {"node_modules", ".venv", "__pycache__", ".git", "dist", "test-results", "playwright-report"}
SOURCE_FILES = ("pyproject.toml", "uv.lock", "README.md", "LICENSE", ".gitignore")


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def files(root: Path):
    if not root.is_dir():
        return
    for directory, children, names in __import__("os").walk(root):
        children[:] = sorted(x for x in children if x not in EXCLUDED)
        for name in sorted(names):
            path = Path(directory) / name
            if not path.is_symlink() and not name.startswith(".env") and not name.endswith(".pyc"):
                yield path


def capture(root: Path, output: Path, inventory: bool):
    output.mkdir(parents=True, exist_ok=False)
    paths = [root / name for name in SOURCE_FILES if (root / name).is_file()]
    for name in SOURCE_DIRS:
        paths.extend(files(root / name))
    manifest = []
    for path in sorted(paths):
        relative = path.relative_to(root)
        destination = output / "source" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        manifest.append({"path": str(relative), "sha256": digest(destination), "bytes": destination.stat().st_size})
    state = {"root": str(root), "captured_at": datetime.now(timezone.utc).isoformat(), "files": manifest}
    for name, args in (("head", ["rev-parse", "HEAD"]), ("branch", ["branch", "--show-current"]),
                       ("status", ["status", "--porcelain=v1"]), ("diff", ["diff", "--binary", "--", *SOURCE_DIRS, *SOURCE_FILES])):
        state[name] = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout
    (output / "source-manifest.json").write_text(json.dumps(state, indent=2) + "\n")
    backups = []
    for path in files(root / "runs"):
        if path.suffix != ".sqlite3" or "backups" in path.parts or "consolidation" in path.parts:
            continue
        backups.append(backup(path, output / "databases" / path.relative_to(root)))
    (output / "database-manifest.json").write_text(json.dumps(backups, indent=2) + "\n")
    if inventory:
        count = size = 0
        with (output / "historical-artifacts.jsonl").open("w") as stream:
            for path in files(root / "runs"):
                if "backups" in path.parts or "consolidation" in path.parts or path.suffix in {".sqlite3", ".log"}:
                    continue
                stat = path.stat()
                record = {"origin": str(path.resolve()), "path": str(path.relative_to(root)), "bytes": stat.st_size,
                          "sha256": digest(path), "mtime_ns": stat.st_mtime_ns, "availability": "external"}
                stream.write(json.dumps(record) + "\n")
                count += 1
                size += stat.st_size
        print(json.dumps({"root": str(root), "source_files": len(manifest), "historical_files": count, "historical_bytes": size}), flush=True)


def backup(source: Path, destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True) as original, sqlite3.connect(destination) as copied:
        original.backup(copied)
        integrity = copied.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"Backup failed integrity check: {source}: {integrity}")
        counts = {name: copied.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
                  for (name,) in copied.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return {"origin": str(source.resolve()), "backup": str(destination.resolve()), "sha256": digest(destination),
            "integrity": integrity, "counts": counts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repository", type=Path, action="append", required=True)
    parser.add_argument("--database", type=Path, action="append", default=[])
    parser.add_argument("--skip-artifact-inventory", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    for root in args.repository:
        capture(root.resolve(), args.output / root.name, not args.skip_artifact_inventory)
    backups = [backup(path, args.output / "services" / str(i) / path.name) for i, path in enumerate(args.database)]
    (args.output / "service-databases.json").write_text(json.dumps(backups, indent=2) + "\n")
    (args.output / "COMPLETE").write_text(datetime.now(timezone.utc).isoformat() + "\n")
    print(f"Verified baseline: {args.output.resolve()}", flush=True)


if __name__ == "__main__":
    main()
