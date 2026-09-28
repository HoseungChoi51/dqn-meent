"""Installed supervisor for an imported numerical worker; never a scheduler.

The workspace owns admission. This process owns the host lease, enforcement and
publication boundary independently of the worker's namespace and Python code.
"""
import argparse
import errno
import fcntl
import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import subprocess
import threading
import time

from optimization_framework.contracts.base import content_hash
from optimization_framework.execution import isolation, provenance
from optimization_framework.execution.attempt_volume import AttemptVolume, DEFAULT_PRIVATE_BYTES, DEFAULT_PRIVATE_ENTRIES, write_control
from optimization_framework.execution.package_host import PackageHost
from optimization_framework.execution.publication import Publisher, InvalidWorkerOutput, OutputStorageLimit, JOURNALS, DEFAULT_PUBLISHED_BYTES
from optimization_framework.execution.worker import fingerprint
from optimization_framework.implementations.runtime import bundle_runtime_root, verify_runtime
from optimization_framework.storage.artifacts import atomic_json
from optimization_framework.storage.sqlite import read_json


LOG_BYTES = 16 * 1024**2


class DeadlineGuard:
    """Enforce host-owned deadlines independently of untrusted output parsing."""
    def __init__(self, directory, spec, lease, process, packages, started, monotonic, volume=None):
        self.directory, self.spec, self.lease = directory, spec, lease
        self.process, self.packages = process, packages
        self.started, self.monotonic = started, monotonic
        self.volume = volume
        self.storage_observed = None
        self.stop = threading.Event()
        self.reason = None
        self.control_since = None
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stop.wait(.05) and self.process.poll() is None:
            try:
                if self.volume is not None:
                    usage = self.volume.usage()
                    if usage is not None:
                        self.storage_observed = usage
                        if usage['entries_used'] > usage['entry_limit']:
                            self.reason = 'private_entry_limit'
                        elif usage['bytes_used'] >= usage['byte_limit']:
                            self.reason = 'private_byte_limit'
                control = read_json(self.directory / "control.json", {})
                if control.get("command") in {"pause", "stop"}:
                    if self.control_since is None:
                        self.control_since = time.monotonic()
                    if time.monotonic() - self.control_since >= self.spec.get("stop_grace_seconds", 5):
                        self.reason = self.reason or "control_" + control["command"]
                else:
                    self.control_since = None
                allowance = control.get("wall_seconds", self.spec["wall_seconds"]) - self.lease["elapsed_before"]
                budget = self.monotonic + max(0, allowance)
                deadline = (self.monotonic + self.spec["absolute_deadline"] - self.started
                            if self.spec.get("absolute_deadline") else float("inf"))
                if not self.reason and time.monotonic() >= min(budget, deadline) + self.spec.get("stop_grace_seconds", 5):
                    self.reason = "deadline" if deadline <= budget else "budget"
            except (OSError, ValueError, KeyError, TypeError):
                # A damaged control must not silently disable the guard thread.
                self.reason = "invalid_host_control"
            if self.reason:
                try:
                    self.process.kill()
                except ProcessLookupError:
                    pass
                self.packages.terminate()
                return

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2)


def process_identity(pid):
    try:
        raw = Path(f"/proc/{int(pid)}/stat").read_text()
        fields = raw[raw.rfind(")") + 2:].split()
        return None if fields[0] == "Z" else fields[19]
    except (OSError, ValueError, IndexError, TypeError):
        return None


def runtime_paths(directory):
    paths = set()
    for name in ("implementation", "evaluator"):
        package = directory / name
        if not (package / "bundle.json").exists():
            continue
        bundle = read_json(package / "bundle.json")
        root = bundle_runtime_root(bundle, package)
        binding = verify_runtime(root, bundle["artifact"]["runtime"])
        paths.update((root, Path(binding["executable"]), Path(binding["stdlib_source"])))
        paths.update(Path(path) for values in binding["libraries"].values() for path in values)
        paths.update((str(source), str(target)) for source, target in binding.get("legacy_aliases", []) if source != target)
        historical_root = bundle["artifact"].get("runtime_root")
        if historical_root and str(root) != historical_root:
            paths.add((str(root), historical_root))
    return sorted(paths, key=str)


def seed_projection(directory, private):
    """Only host-published recovery data is eligible to seed a fresh attempt."""
    private.mkdir(exist_ok=True)
    for name in (*JOURNALS, "progress.json", "checkpoints", "artifacts", "outputs", "diagnostics"):
        path = directory / name
        if path.is_dir():
            shutil.copytree(path, private / name)
        elif path.is_file():
            shutil.copyfile(path, private / name)


def execute(directory, host_directory, *, private_bytes=DEFAULT_PRIVATE_BYTES, private_entries=DEFAULT_PRIVATE_ENTRIES,
            published_bytes=DEFAULT_PUBLISHED_BYTES):
    directory, host_directory = Path(directory).resolve(), Path(host_directory).resolve()
    if host_directory.is_relative_to(directory) or directory.is_relative_to(host_directory):
        raise ValueError("Execution host records must be outside the worker's experiment tree")
    host_directory.mkdir(parents=True, exist_ok=True)
    with (host_directory / "host.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        spec = read_json(directory / "spec.json")
        identity = {"experiment_id": spec["id"], "attempt": spec["attempt"], "fingerprint": fingerprint(spec),
                    "experiment_spec_hash": spec["experiment_spec_hash"]}
        previous = read_json(host_directory / "receipt.json")
        if previous:
            if any(previous.get(key) != value for key, value in identity.items()):
                raise ValueError("The execution host receipt belongs to another attempt")
            return previous
        prior_lease = read_json(host_directory / "lease.json")
        if prior_lease and any(prior_lease.get(key) != value for key, value in identity.items()):
            raise ValueError("The execution host lease belongs to another attempt")
        started, monotonic = time.time(), time.monotonic()
        lease = prior_lease or {**identity, "pid": os.getpid(), "process_identity": process_identity(os.getpid()),
                 "started_at": started, "elapsed_before": spec.get("execution_seconds", 0),
                 "launch_digest": content_hash(identity), "storage_kind": "bounded_tmpfs",
                 "private_byte_limit": private_bytes, "private_entry_limit": private_entries,
                 "published_byte_limit": published_bytes}
        if not prior_lease:
            atomic_json(host_directory / "lease.json", lease)
        private = host_directory / "private"
        publisher = None
        process = packages = guard = volume = None
        storage = None
        stopped_by, error, code = None, None, None
        log_file = directory / 'worker.log'
        log_size, truncated = log_file.stat().st_size if log_file.exists() else 0, False
        publication = read_json(host_directory / 'publication.json', {})
        if publication and any(publication.get(key) != value for key, value in identity.items()):
            raise ValueError("The execution host publication belongs to another attempt")

        def publish(*, terminal=False):
            nonlocal publication
            publisher.publish(terminal=terminal)
            publication = {**identity, 'published_at': time.time(), 'terminal': terminal,
                           'worker_exit': code if terminal else None, 'counts': dict(publisher.counts),
                           'committed_bytes': publisher.committed_bytes, 'files': len(publisher.published)}
            atomic_json(host_directory / 'publication.json', publication)
        try:
            if private.exists() or prior_lease:
                # Re-entry after a dead supervisor recovers evidence only.
                # A resume is a new scheduled attempt with a new host directory.
                if private.exists():
                    publisher = Publisher(private, directory, spec, max_bytes=published_bytes)
                    publish(terminal=True)
                stopped_by = "interrupted_host"
            else:
                provenance.verify(directory, spec["execution_manifest"])
                control = read_json(directory / "control.json", {})
                volume = AttemptVolume(max_bytes=private_bytes, max_entries=private_entries)
                volume.__enter__()
                packages = PackageHost(directory, spec)
                packages.__enter__()
                invocation = isolation.command(directory, spec["execution_manifest"], private,
                    ["optimization_framework.execution.worker", "--directory", str(directory)],
                    worker=True, file_bytes=max(64 * 1024**2, spec.get("recovery", {}).get("max_checkpoint_bytes", 4 * 1024**3)),
                    runtime_directories=runtime_paths(directory), package_host=packages, attempt_volume=volume)
                process = subprocess.Popen(invocation, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    env={"PATH": "/usr/bin:/bin"})
                guard = DeadlineGuard(directory, spec, lease, process, packages, started, monotonic, volume)
                descriptor = volume.receive(process)
                # This path denotes a descriptor opened by the installed prelude,
                # not a pathname supplied by imported code. The worker waits for
                # ready() while its host-owned recovery projection is seeded.
                seed_projection(directory, Path(f'/proc/self/fd/{descriptor}'))
                write_control(descriptor, control)
                publisher = Publisher(descriptor, directory, spec, max_bytes=published_bytes)
                volume.ready()
                os.set_blocking(process.stdout.fileno(), False)
                selector = selectors.DefaultSelector()
                selector.register(process.stdout, selectors.EVENT_READ)
                last_publication = 0
                with selector, (directory / "worker.log").open("ab") as log:
                    while True:
                        for _, _ in selector.select(.1):
                            data = os.read(process.stdout.fileno(), 1024 * 1024)
                            if data:
                                kept = data[:max(0, LOG_BYTES - log_size)]
                                log.write(kept)
                                log.flush()
                                log_size += len(kept)
                                truncated |= len(kept) != len(data)
                            else:
                                selector.unregister(process.stdout)
                        updated = read_json(directory / "control.json", {})
                        if updated != control:
                            control = updated
                            write_control(descriptor, control)
                        now = time.monotonic()
                        if now - last_publication >= .5:
                            publish()
                            last_publication = now
                        if process.poll() is not None and not selector.get_map():
                            break
                code = process.wait()
                publish(terminal=True)
        except Exception as exc:
            error = str(exc)
            stopped_by = ('published_storage_limit' if isinstance(exc, OutputStorageLimit)
                          else "invalid_output" if isinstance(exc, InvalidWorkerOutput) else "private_byte_limit"
                          if isinstance(exc, OSError) and exc.errno == errno.ENOSPC else "host_failure")
        finally:
            if guard is not None:
                guard.close()
                stopped_by = guard.reason or stopped_by
            if process is not None:
                if process.poll() is None:
                    process.kill()
                code = process.wait()
                process.stdout.close()
            if packages is not None:
                packages.close()
            if volume is not None:
                storage = volume.usage()
                volume.close()
        receipt = {**identity, "id": "isolated_host_" + content_hash(identity), "schema_version": 2,
            "host_pid": lease["pid"], "host_process_identity": lease["process_identity"],
            "package_processes": packages.records if packages else [],
            "private_storage": storage,
            "published_storage": {'byte_limit': published_bytes, 'committed_bytes': publisher.committed_bytes,
                                  'files': len(publisher.published)} if publisher else None,
            "storage_observed_at_enforcement": guard.storage_observed if guard and guard.reason else None,
            "rejected_package_connections": packages.rejected if packages else 0,
            "started_at": lease['started_at'], "finished_at": time.time(),
            "elapsed_seconds": None if prior_lease else time.monotonic() - monotonic,
            "process_exit": code, "stopped_by": stopped_by, "error": error,
            "log_truncated": truncated, "published_records": publication.get('counts', {}),
            'last_publication': publication or None,
            'reconciler_pid': os.getpid() if prior_lease else None,
            "recovered_after_interruption": bool(prior_lease)}
        atomic_json(host_directory / "receipt.json", receipt)
        return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--host-directory", type=Path, required=True)
    parser.add_argument("--private-bytes", type=int, default=DEFAULT_PRIVATE_BYTES)
    parser.add_argument("--private-entries", type=int, default=DEFAULT_PRIVATE_ENTRIES)
    parser.add_argument("--published-bytes", type=int, default=DEFAULT_PUBLISHED_BYTES)
    args = parser.parse_args()
    result = execute(args.directory, args.host_directory, private_bytes=args.private_bytes, private_entries=args.private_entries,
                     published_bytes=args.published_bytes)
    raise SystemExit(1 if result["error"] or result["process_exit"] not in {0, None} else 0)


if __name__ == "__main__":
    main()
