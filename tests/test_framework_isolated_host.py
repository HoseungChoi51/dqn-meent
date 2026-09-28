"""Real isolated numerical execution and safe publication into ordinary readers."""
from framework_fixtures import researcher_idea
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from optimization_framework.contracts.requests import TrialInput
from optimization_framework.execution.isolated_host import execute
from optimization_framework.execution.publication import PrivateTree, Publisher, InvalidWorkerOutput
from optimization_framework.storage.artifacts import atomic_json
from test_framework_provenance import campaign


def prepared(workspace, owner, task, **options):
    trial = workspace.create_trial(TrialInput(campaign_id=owner, task_id=task,
        algorithm=options.pop("algorithm", "coordinate"), max_steps=4, wall_seconds=15, **options))
    trial.update(attempt=1, status="running")
    atomic_json(workspace.job_dir(trial["id"]) / "spec.json", trial)
    workspace.store.put("trial", trial)
    return trial


def launch(workspace, trial):
    directory = workspace.job_dir(trial["id"])
    host = workspace.directory / "execution-hosts" / trial["id"] / str(trial["attempt"])
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    process = subprocess.run([sys.executable, "-m", "optimization_framework.execution.isolated_host",
        "--directory", str(directory), "--host-directory", str(host)],
        capture_output=True, text=True, env=environment, timeout=45)
    receipt = json.loads((host / "receipt.json").read_text())
    assert process.returncode == 0, (receipt, process.stderr)
    assert receipt["error"] is None and receipt["stopped_by"] is None, receipt
    return host, receipt


def test_actual_worker_publishes_a_result_without_private_paths_or_host_access(tmp_path):
    workspace, owner, task = campaign(tmp_path / "workspace")
    trial = prepared(workspace, owner, task, recovery={"every_observations": 2})
    host, receipt = launch(workspace, trial)
    directory = workspace.job_dir(trial["id"])
    result = json.loads((directory / "result.json").read_text())
    assert result["scientific_complete"] and result["evaluations"] == 4
    assert receipt["published_records"]["observations.jsonl"] == 4
    assert receipt["host_pid"] != os.getpid()
    assert receipt['private_storage']['kind'] == 'bounded_tmpfs'
    assert receipt['private_storage']['bytes_used'] <= receipt['private_storage']['byte_limit']
    assert not (host / 'private').exists()
    lease = json.loads((host / "lease.json").read_text())
    assert lease["pid"] == receipt["host_pid"]
    assert not (directory / "worker-lease.json").exists()
    trial.update(result=result, progress=result, status=result["status"])
    workspace.store.put("trial", trial)
    workspace.capture_evidence(trial)
    trial = workspace.store.get(trial["id"], "trial")
    assert trial["output_asset_ids"]
    assert workspace.assets.attributed_costs(trial["output_asset_ids"])["quantities"]["evaluation_requests"]["total"] == 4
    before = (directory / "observations.jsonl").read_bytes()
    assert execute(directory, host) == receipt
    assert (directory / "observations.jsonl").read_bytes() == before


@pytest.mark.parametrize("kind", ["symlink", "fifo", "directory_link", "hardlink"])
def test_private_output_paths_cannot_make_host_read_outside_the_attempt(tmp_path, kind):
    private = tmp_path / "private"
    private.mkdir()
    secret = tmp_path / "secret.json"
    secret.write_text('{"secret":"host data"}')
    relative = "record.json"
    if kind == "symlink":
        (private / relative).symlink_to(secret)
    elif kind == "hardlink":
        os.link(secret, private / relative)
    elif kind == "fifo":
        os.mkfifo(private / relative)
    else:
        (private / "linked").symlink_to(tmp_path, target_is_directory=True)
        relative = "linked/secret.json"
    with PrivateTree(private) as tree:
        with pytest.raises(InvalidWorkerOutput):
            tree.json(relative)


def test_published_journals_cannot_be_rewritten_and_external_locations_are_rejected(tmp_path):
    workspace, owner, task = campaign(tmp_path / "workspace")
    trial = prepared(workspace, owner, task)
    private = tmp_path / "private"
    private.mkdir()
    destination = tmp_path / "published"
    destination.mkdir()
    first = {"id": "request_original", "experiment_id": trial["id"]}
    journal = private / "requests.jsonl"
    journal.write_text(json.dumps(first) + "\n")
    publisher = Publisher(private, destination, trial)
    publisher.publish()
    original = (destination / "requests.jsonl").read_bytes()
    journal.write_text(json.dumps({**first, "id": "changed"}) + "\n")
    with pytest.raises(InvalidWorkerOutput, match="already published"):
        publisher.publish()
    assert (destination / "requests.jsonl").read_bytes() == original
    journal.unlink()
    with pytest.raises(InvalidWorkerOutput, match="removed a committed journal"):
        publisher.publish()
    assert (destination / "requests.jsonl").read_bytes() == original
    external = private / "artifacts/external"
    external.mkdir(parents=True)
    (external / "location.json").write_text('{"path":"/unrelated-host-file"}')
    with pytest.raises(InvalidWorkerOutput, match="external artifact locations"):
        publisher.publish()


def test_generated_evaluator_runs_in_a_separate_declared_package_host(tmp_path):
    from test_framework_revalidation import campaign_fixture
    from test_framework_evaluators import specification
    workspace, service, owner, task = campaign_fixture(tmp_path / "generated", spec=specification())
    trial = prepared(workspace, owner["id"], task["id"])
    _, receipt = launch(workspace, trial)
    result = json.loads((workspace.job_dir(trial["id"]) / "result.json").read_text())
    assert result["scientific_complete"] and result["evaluations"] == 4, (result, receipt)
    assert result["objective_definition"]["name"] == "energy"
    assert [item["kind"] for item in receipt["package_processes"]] == ["evaluator"]
    assert all(item["finished_at"] and item["exit_code"] is not None for item in receipt["package_processes"])


def test_two_generated_packages_stream_large_checkpoints_and_restore_under_supervision(tmp_path):
    from optimization_framework.implementations.models import BoundOptimizerSpec, Package
    from optimization_framework.execution.worker import read_journal
    from test_framework_evaluators import specification, MockReviewer, SOURCE
    from test_framework_revalidation import campaign_fixture

    # This check runs both during independent package validation and later in
    # the supervisor's package namespace. Neither may expose the relay or peer.
    isolated = ("import os\nassert not os.path.exists('/run/optimization-package.sock')\n"
        f"assert not os.path.exists({str(tmp_path / 'generated/workspace')!r})\n")
    workspace, service, owner, task = campaign_fixture(tmp_path / "generated", source=isolated + SOURCE, spec=specification())
    service.adapter_factory = MockReviewer
    hypothesis = researcher_idea(workspace, owner["id"])
    source = (Path(__file__).resolve().parents[1] / "examples/implementation-reference/continuous_optimizer_v1.py").read_text()
    source = isolated + source.replace('return pickle.dumps(self.__dict__)',
        'return pickle.dumps(self.__dict__) + b"x" * (20 * 1024**2)')
    optimizer = BoundOptimizerSpec(name="Specialized optimizer", mechanism="Coordinate search on the commissioned problem",
        acceptance_criteria=["Restore the next proposal"], problem_id=task["problem"]["definition_id"],
        evaluator_version_id=task["evaluator_version_id"], capabilities=["continuous", "scalar_objective"],
        n_cells_min=2, n_cells_max=2, max_checkpoint_bytes=32 * 1024**2,
        behavior_checks=[{"name": "Keep the best incumbent", "n_cells": 2,
            "assertion": "one_coordinate_from_incumbent", "efficiencies": [-1, -3, -2, 1]}])
    grant = workspace.implementations.commission(hypothesis["id"], optimizer,
        package=Package(contract="optimizer_v1", files=[{"path": "optimizer.py", "content": source}]),
        compute_seconds=60, max_calls=1, idempotency_key="large-checkpoint")
    built = service.run_job(grant["job_id"])
    assert built["status"] == "completed", built
    workspace.implementations.reconcile()
    trial = prepared(workspace, owner["id"], task["id"], hypothesis_id=hypothesis["id"],
        algorithm="package", recovery={"every_observations": 2, "max_checkpoint_bytes": 64 * 1024**2})
    directory = workspace.job_dir(trial["id"])
    # Pause cooperatively before any search, retaining an actual package state.
    atomic_json(directory / "control.json", {"command": "pause", "revision": 1})
    _, paused = launch(workspace, trial)
    first = json.loads((directory / "result.json").read_text())
    assert first["status"] == "paused" and not first["scientific_complete"], first
    checkpoint = json.loads((directory / "checkpoints/latest.json").read_text())
    manifest = json.loads((directory / "checkpoints" / (checkpoint["id"] + ".json")).read_text())
    assert manifest["bytes"] > 16 * 1024**2
    frozen = trial["experiment_spec_hash"]
    trial.update(attempt=2, execution_seconds=first["elapsed_seconds"])
    atomic_json(directory / "spec.json", trial)
    atomic_json(directory / "control.json", {"command": "run", "revision": 2})
    _, resumed = launch(workspace, trial)
    result = json.loads((directory / "result.json").read_text())
    assert result["scientific_complete"] and result["evaluations"] == 4, result
    assert trial["experiment_spec_hash"] == frozen
    assert {item["kind"] for item in resumed["package_processes"]} == {"optimizer", "evaluator"}
    observations = read_journal(directory / "observations.jsonl")
    assert len(observations) == 4 and len({row["id"] for row in observations}) == 4
    assert paused["attempt"] == 1 and resumed["attempt"] == 2


def test_package_launcher_rejects_arbitrary_commands_without_spawning(tmp_path):
    import socket
    import struct
    from optimization_framework.execution.package_host import PackageHost
    workspace, owner, task = campaign(tmp_path / "workspace")
    trial = prepared(workspace, owner, task)
    with PackageHost(workspace.job_dir(trial["id"]), trial) as host:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            connection.connect(str(host.socket_path))
            request = json.dumps(["--ro-bind", "/", "/host", "--", "/bin/sh"]).encode()
            connection.sendall(struct.pack("!I", len(request)) + request)
            assert connection.recv(1) == b"E"
    assert not host.processes
    assert host.records[0]["status"] == "rejected" and "pid" not in host.records[0]
    assert "not declared" in host.records[0]["error"]


def test_package_launcher_rejects_changed_mounts_and_driver_and_reaps_on_disconnect(tmp_path):
    import array
    import socket
    import struct
    from optimization_framework.execution.package_host import PackageHost
    from test_framework_evaluators import specification
    from test_framework_revalidation import campaign_fixture
    workspace, _, owner, task = campaign_fixture(tmp_path / "generated", spec=specification())
    trial = prepared(workspace, owner["id"], task["id"])
    with PackageHost(workspace.job_dir(trial["id"]), trial) as host:
        allowed = list(next(iter(host.allowed)))
        changed_mount = allowed.copy()
        changed_mount[changed_mount.index("--ro-bind") + 1] = str(tmp_path)
        changed_driver = [*allowed[:-1], allowed[-1] + "\nprint('undeclared driver')\n"]
        for arguments in (changed_mount, changed_driver, allowed + ["--ro-bind", "/", "/host"]):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(2)
                connection.connect(str(host.socket_path))
                request = json.dumps(arguments).encode()
                connection.sendall(struct.pack("!I", len(request)) + request)
                assert connection.recv(1) == b"E"
        # The exact launch succeeds; abandoning its owner closes the sandbox.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            connection.connect(str(host.socket_path))
            request = json.dumps(allowed).encode()
            connection.sendall(struct.pack("!I", len(request)) + request)
            ready, ancillary, _, _ = connection.recvmsg(1, socket.CMSG_SPACE(3 * array.array('i').itemsize))
            assert ready == b"R"
            descriptors = array.array('i')
            for level, kind, data in ancillary:
                assert (level, kind) == (socket.SOL_SOCKET, socket.SCM_RIGHTS)
                descriptors.frombytes(data)
            assert len(descriptors) == 3
            with host.lock:
                process = next(iter(host.processes))
        try:
            assert process.wait(timeout=3) < 0
        finally:
            for descriptor in descriptors:
                os.close(descriptor)
    assert all(item["status"] == "rejected" and "pid" not in item for item in host.records[:3])
    assert host.records[3]["status"] == "disconnected"
    assert not host.processes


def test_host_rejects_a_lease_for_another_attempt_before_recovery(tmp_path):
    workspace, owner, task = campaign(tmp_path / "workspace")
    trial = prepared(workspace, owner, task)
    directory = workspace.job_dir(trial["id"])
    host = tmp_path / "execution-host"
    prior = {"experiment_id": trial["id"], "attempt": 99}
    atomic_json(host / "lease.json", prior)
    with pytest.raises(ValueError, match="lease belongs to another"):
        execute(directory, host)
    assert json.loads((host / "lease.json").read_text()) == prior
    assert not (host / "private").exists()


def adversarial_capture(workspace, trial, program):
    """Admitted source may be hostile; this fixture makes no scientific claim."""
    from optimization_framework.execution import provenance
    from optimization_framework.execution.source import snapshot
    directory = workspace.job_dir(trial['id'])
    (directory / 'code/optimization_framework/execution/worker.py').write_text(program)
    trial['execution_manifest'] = provenance.capture(directory, problem_ids=['bounded_continuous'])
    trial['source_hash'] = snapshot(directory)
    atomic_json(directory / 'spec.json', trial)
    return directory


def test_unreferenced_private_files_hit_the_kernel_aggregate_byte_limit(tmp_path):
    workspace, owner, task = campaign(tmp_path / 'workspace')
    trial = prepared(workspace, owner, task)
    directory = adversarial_capture(workspace, trial, '''import errno, os, time
from pathlib import Path
try:
    for number in range(30):
        Path('unreferenced_' + str(number)).write_bytes(b'x' * (512 * 1024))
except OSError as exc:
    assert exc.errno == errno.ENOSPC
    print('kernel_private_quota_reached', flush=True)
time.sleep(60)
''')
    host = tmp_path / 'host'
    receipt = execute(directory, host, private_bytes=2 * 1024**2)
    assert receipt['stopped_by'] == 'private_byte_limit', receipt
    assert receipt['process_exit'] < 0
    assert receipt['private_storage']['bytes_used'] == 2 * 1024**2
    assert 'kernel_private_quota_reached' in (directory / 'worker.log').read_text()
    assert not list(directory.glob('unreferenced_*'))
    assert not (host / 'private').exists()


def test_private_entry_guard_counts_unreferenced_empty_files(tmp_path):
    workspace, owner, task = campaign(tmp_path / 'workspace')
    trial = prepared(workspace, owner, task)
    directory = adversarial_capture(workspace, trial, '''import time
from pathlib import Path
for number in range(500):
    Path('empty_' + str(number)).touch()
print('empty_files_created', flush=True)
time.sleep(60)
''')
    receipt = execute(directory, tmp_path / 'host', private_bytes=2 * 1024**2, private_entries=100)
    assert receipt['stopped_by'] == 'private_entry_limit', receipt
    assert receipt['storage_observed_at_enforcement']['entries_used'] > 100
    assert receipt['private_storage']['bytes_used'] < 2 * 1024**2
    assert not list(directory.glob('empty_*'))


def test_worker_has_no_unbounded_writable_device_or_shared_memory_directory(tmp_path):
    workspace, owner, task = campaign(tmp_path / 'workspace')
    trial = prepared(workspace, owner, task)
    directory = adversarial_capture(workspace, trial, '''import errno, os
from pathlib import Path
try:
    Path('/dev/unreferenced').write_bytes(b'x')
    raise AssertionError('device directory permits file creation')
except OSError as exc:
    assert exc.errno == errno.EROFS
for directory in ('/tmp', '/dev/shm'):
    stats=os.statvfs(directory)
    assert stats.f_blocks * stats.f_frsize == 64 * 1024**2
    try:
        with open(directory + '/unreferenced', 'wb') as stream:
            for _ in range(65): stream.write(b'x' * 1024**2)
        raise AssertionError('scratch byte allowance was exceeded')
    except OSError as exc:
        assert exc.errno == errno.ENOSPC
print('scratch_bounds_verified', flush=True)
''')
    receipt = execute(directory, tmp_path / 'host', private_bytes=2 * 1024**2)
    assert receipt['process_exit'] == 0 and receipt['error'] is None, receipt
    assert 'scratch_bounds_verified' in (directory / 'worker.log').read_text()


def test_publication_budget_includes_journals_metadata_staging_and_prior_attempts(tmp_path):
    workspace, owner, task = campaign(tmp_path / 'workspace')
    trial = prepared(workspace, owner, task)
    private, destination = tmp_path / 'private', tmp_path / 'published'
    private.mkdir(); destination.mkdir()
    record = json.dumps({'experiment_id': trial['id'], 'message': 'x' * 200}) + '\n'
    (private / 'requests.jsonl').write_text(record)
    (private / 'observations.jsonl').write_text(record)
    publisher = Publisher(private, destination, trial, max_bytes=len(record.encode()) + 10)
    with pytest.raises(InvalidWorkerOutput, match='aggregate byte allowance'):
        publisher.publish()
    assert (destination / 'requests.jsonl').read_text() == record
    assert not (destination / 'observations.jsonl').exists()
    assert not list(destination.glob('publish-*')) and publisher.staged_bytes == 0
    restarted = Publisher(private, destination, trial, max_bytes=len(record.encode()) + 10)
    with pytest.raises(InvalidWorkerOutput, match='aggregate byte allowance'):
        restarted.publish()
    assert restarted.committed_bytes == len(record.encode())
    # Progress is metadata, and requires room for both the old record and the
    # atomic replacement while staging; it cannot grow outside the same budget.
    (private / 'requests.jsonl').unlink(); (private / 'observations.jsonl').unlink()
    clean = tmp_path / 'metadata'
    clean.mkdir()
    atomic_json(private / 'progress.json', {'message': 'x' * 200})
    publisher = Publisher(private, clean, trial, max_bytes=400)
    publisher.publish()
    original = (clean / 'progress.json').read_bytes()
    with pytest.raises(InvalidWorkerOutput, match='aggregate byte allowance'):
        publisher.publish()
    assert (clean / 'progress.json').read_bytes() == original and publisher.staged_bytes == 0


def test_supervisor_death_retains_committed_evidence_and_reconciles_without_execution(tmp_path):
    from optimization_framework.execution.isolated_host import process_identity
    from optimization_framework.contracts.requests import TrialInput
    from test_framework_evaluators import specification, SOURCE
    from test_framework_revalidation import campaign_fixture
    source = 'import time\n' + SOURCE.replace('def evaluate(self, candidate):',
        'def evaluate(self, candidate):\n        time.sleep(.03)')
    workspace, _, owner, task = campaign_fixture(tmp_path / 'generated', source=source, spec=specification())
    trial = workspace.create_trial(TrialInput(campaign_id=owner['id'], task_id=task['id'], algorithm='coordinate',
        max_steps=200, wall_seconds=30, recovery={'every_observations': 2}))
    trial.update(attempt=1, status='running')
    directory = workspace.job_dir(trial['id'])
    atomic_json(directory / 'spec.json', trial)
    host = tmp_path / 'execution-host'
    process = subprocess.Popen([sys.executable, '-m', 'optimization_framework.execution.isolated_host',
        '--directory', str(directory), '--host-directory', str(host)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')})
    try:
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail(process.communicate()[1].decode())
            marker = host / 'publication.json'
            if marker.exists() and json.loads(marker.read_text())['counts'].get('observations.jsonl', 0) >= 4:
                break
            time.sleep(.02)
        else:
            pytest.fail('The supervisor did not publish the expected observations')
        children = set()
        for path in Path(f'/proc/{process.pid}/task').glob('*/children'):
            children.update(int(value) for value in path.read_text().split())
        identities = {pid: process_identity(pid) for pid in children}
        assert identities
        process.kill()
        process.wait(timeout=5)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and any(process_identity(pid) == identity for pid, identity in identities.items() if identity):
            time.sleep(.02)
        assert all(process_identity(pid) != identity for pid, identity in identities.items() if identity)
        lease = (host / 'lease.json').read_bytes()
        committed = (directory / 'observations.jsonl').read_bytes()
        checkpoint = (directory / 'checkpoints/latest.json').read_bytes()
        assert not (host / 'receipt.json').exists() and not (host / 'private').exists()
        # Reconciliation must not need a working execution runtime/source or
        # run the captured worker again to preserve earlier committed evidence.
        (directory / 'code').rename(directory / 'unavailable-code')
        recovered = execute(directory, host)
        assert recovered['stopped_by'] == 'interrupted_host' and recovered['error'] is None
        assert recovered['elapsed_seconds'] is None and recovered['recovered_after_interruption']
        assert recovered['host_pid'] == process.pid and recovered['reconciler_pid'] == os.getpid()
        assert recovered['published_records']['observations.jsonl'] >= 4
        assert (host / 'lease.json').read_bytes() == lease
        assert (directory / 'observations.jsonl').read_bytes() == committed
        assert (directory / 'checkpoints/latest.json').read_bytes() == checkpoint
        assert execute(directory, host) == recovered
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdout.close(); process.stderr.close()


def test_deadline_guard_terminates_a_worker_without_waiting_for_publication(tmp_path):
    from optimization_framework.execution.isolated_host import DeadlineGuard
    class Packages:
        terminated = False
        def terminate(self): self.terminated = True
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    packages = Packages()
    started, monotonic = time.time(), time.monotonic()
    guard = DeadlineGuard(tmp_path, {"wall_seconds": .15, "stop_grace_seconds": 0},
        {"elapsed_before": 0}, process, packages, started, monotonic)
    try:
        # No publication or main-loop polling runs here.
        assert process.wait(timeout=2) < 0
        guard.close()
        assert guard.reason == "budget" and packages.terminated
    finally:
        guard.close()
        process.kill()
        process.wait()
