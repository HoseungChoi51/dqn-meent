"""Exercise real namespace isolation, protocol failures, and trusted evaluation."""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import socket
import time

import pytest

from dqn_meent.config import PhysicsConfig
from dqn_meent.workspace.custom_optimizer import (
    CandidateError, CustomOptimizer, SandboxUnavailable, sandbox_status,
    validate_source, verify_custom_source,
)
from dqn_meent.workspace.worker import atomic_json, run


SOURCE = '''
def initialize(n_cells, seed, config):
    return {"n": n_cells, "rng": seed, "observations": 0, "total": 0.0}

def propose(state):
    bits = []
    for _ in range(state["n"]):
        state["rng"] = (1664525 * state["rng"] + 1013904223) % (2 ** 32)
        bits.append((state["rng"] >> 31) & 1)
    return {"design": bits, "state": state}

def observe(state, design, efficiency):
    state["observations"] += 1
    state["total"] += efficiency
    return state
'''


@pytest.fixture(scope="module")
def isolated_runtime():
    status = sandbox_status()
    if not status["available"]:
        pytest.skip("Mandatory namespace isolation unavailable: " + status["reason"])
    return status


def test_missing_isolation_fails_closed(monkeypatch):
    from dqn_meent.workspace import custom_optimizer

    def unavailable():
        raise SandboxUnavailable("Namespace creation denied")

    monkeypatch.setattr(custom_optimizer, "sandbox_command", unavailable)
    status = sandbox_status()
    assert not status["available"]
    assert "Namespace creation denied" in status["reason"]
    with pytest.raises(SandboxUnavailable):
        CustomOptimizer(4, 0, {"source": SOURCE})


def test_source_review_is_nonexecuting_and_requires_protocol():
    assert validate_source(SOURCE) == hashlib.sha256(SOURCE.encode()).hexdigest()
    with pytest.raises(ValueError, match="must define"):
        validate_source("raise Exception('must never execute')")
    with pytest.raises(ValueError, match="64 KiB"):
        validate_source("x" * 65537)


def test_valid_custom_protocol_is_verified(isolated_runtime):
    result = verify_custom_source(SOURCE, n_cells=4)
    assert result["verified"]
    assert result["source_hash"] == hashlib.sha256(SOURCE.encode()).hexdigest()
    assert result["deterministic_probe"]
    assert "synthetic" in result["note"]
    assert "quality is untested" in result["note"]


def test_verification_detects_rng_state_missing_from_protocol(isolated_runtime):
    source = SOURCE.replace('"rng": seed', '"rng": __import__("secrets").randbits(128)')
    with pytest.raises(CandidateError, match="not reproducible"):
        verify_custom_source(source)


def test_candidate_cannot_access_host_files_environment_or_network(tmp_path, monkeypatch, isolated_runtime):
    secret = tmp_path / ".key"
    secret.write_text("sensitive-data-that-must-stay-outside")
    ledger = tmp_path / "ledger.sqlite"
    ledger.write_text("trusted-ledger")
    project_test = Path(__file__).resolve()
    monkeypatch.setenv("GRATING_LLM_API_KEY", "must-not-leak")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    attacks = f'''
def initialize(n_cells, seed, config):
    import os, socket
    result = {{"n": n_cells}}
    for name, path in {{"key": {str(secret)!r}, "ledger": {str(ledger)!r},
                       "repo": {str(project_test)!r}, "proc_escape": {f'/proc/{os.getpid()}/root{secret}'!r}}}.items():
        try:
            with open(path) as stream:
                result[name] = stream.read()
        except OSError:
            result[name] = "blocked"
    try:
        with open({str(ledger)!r}, "w") as stream:
            stream.write("corrupted")
        result["write"] = "escaped"
    except OSError:
        result["write"] = "blocked"
    result["credential"] = os.environ.get("GRATING_LLM_API_KEY")
    connection = socket.socket()
    connection.settimeout(.1)
    try:
        connection.connect(("127.0.0.1", {port}))
        result["network"] = "escaped"
    except OSError:
        result["network"] = "blocked"
    connection.close()
    try:
        with open("/usr/lib/python3.14/os.py", "w") as stream:
            stream.write("corrupted")
        result["runtime_write"] = "escaped"
    except OSError:
        result["runtime_write"] = "blocked"
    return result

def propose(state):
    return {{"design": [0] * state["n"], "state": state}}

def observe(state, design, efficiency):
    return state
'''
    try:
        candidate = CustomOptimizer(4, 0, {"source": attacks})
        assert candidate.state == {"n": 4, "key": "blocked", "ledger": "blocked", "repo": "blocked",
                                   "proc_escape": "blocked", "write": "blocked", "credential": None,
                                   "network": "blocked", "runtime_write": "blocked"}
        assert ledger.read_text() == "trusted-ledger"
    finally:
        listener.close()


def test_infinite_loop_has_bounded_timeout(isolated_runtime):
    source = SOURCE.replace('return {"n": n_cells, "rng": seed, "observations": 0, "total": 0.0}',
                            'while True: pass')
    started = time.monotonic()
    with pytest.raises(CandidateError, match="timeout"):
        CustomOptimizer(4, 0, {"source": source, "request_timeout": .2})
    assert time.monotonic() - started < 3


def test_output_flood_is_bounded(isolated_runtime):
    source = SOURCE.replace('return {"n": n_cells, "rng": seed, "observations": 0, "total": 0.0}',
                            'print("x" * 100000); return {}')
    with pytest.raises(CandidateError, match="output limit"):
        CustomOptimizer(4, 0, {"source": source})


def test_nonfinite_or_invalid_designs_are_rejected_before_evaluation(isolated_runtime):
    nonfinite = SOURCE.replace('"total": 0.0', '"total": float("nan")')
    with pytest.raises(CandidateError, match="JSON"):
        CustomOptimizer(4, 0, {"source": nonfinite})
    malformed = SOURCE.replace('return {"design": bits, "state": state}', 'return {"design": [2] * state["n"], "state": state}')
    candidate = CustomOptimizer(4, 0, {"source": malformed})
    with pytest.raises(ValueError, match="binary"):
        candidate.ask()


def test_only_returned_state_persists_between_sandbox_calls(isolated_runtime):
    source = 'counter = 0\n' + SOURCE.replace('bits = []', 'global counter\n    counter += 1\n    state["global_seen"] = counter\n    bits = []')
    candidate = CustomOptimizer(4, 0, {"source": source})
    for _ in range(3):
        design = candidate.ask()
        candidate.tell(design, .25)
    assert candidate.state["global_seen"] == 1
    assert candidate.state["observations"] == 3


def _prepare(directory, max_steps):
    directory.mkdir()
    atomic_json(directory / "spec.json", {
        "id": "custom-example", "campaign_id": "c1", "charter_version": 1, "task_id": "t1",
        "algorithm": "custom", "algorithm_config": {"source": SOURCE}, "seed": 42,
        "max_steps": max_steps, "schedule_steps": 8, "wall_seconds": 60,
        "physics": asdict(PhysicsConfig(n_cells=4, fourier_order=1)), "archive_size": 3,
    })


def test_custom_resume_is_exact_and_meent_evaluation_stays_in_parent(tmp_path, isolated_runtime):
    full, resumed = tmp_path / "full", tmp_path / "resumed"
    _prepare(full, 8)
    expected = run(full)
    assert expected["status"] == "completed", expected["reason"]
    assert expected["step"] == expected["evaluations"] == 8
    assert expected["solver_calls"] > 0
    assert expected["diagnostics"]["source_hash"] == hashlib.sha256(SOURCE.encode()).hexdigest()
    _prepare(resumed, 3)
    partial = run(resumed)
    assert partial["status"] == "completed", partial["reason"]
    atomic_json(resumed / "control.json", {"command": "run", "max_steps": 8, "revision": 1})
    # Service metadata must not alter the scientific identity during resume.
    spec = json.loads((resumed / "spec.json").read_text())
    spec.update(attempt=2, status="running", progress=partial, updated_at="new timestamp")
    atomic_json(resumed / "spec.json", spec)
    result = run(resumed)
    assert result["status"] == "completed", result["reason"]
    for key in ("step", "evaluations", "solver_calls", "cache_hits", "best_design", "best_efficiency", "archive", "diagnostics"):
        assert result[key] == expected[key]
    expected_rows = [json.loads(line) for line in (full / "metrics.jsonl").read_text().splitlines()]
    resumed_rows = [json.loads(line) for line in (resumed / "metrics.jsonl").read_text().splitlines()]
    assert [row["efficiency"] for row in expected_rows] == [row["efficiency"] for row in resumed_rows]
