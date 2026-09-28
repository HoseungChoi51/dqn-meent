"""Fail-closed Linux sandbox for reviewed, researcher-supplied optimizer source.

Protocol (Python standard library only)::

    initialize(n_cells, seed, config) -> JSON state
    propose(state) -> {"design": [0, 1, ...], "state": JSON state}
    observe(state, design, efficiency) -> JSON state

Each call runs in a fresh sandbox process; every persistent value, including RNG
state, must be explicitly returned as JSON. No optimizer executes in the trusted
worker. That worker alone evaluates designs and owns the budget and ledger.
"""
from __future__ import annotations

import ast
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import time

from optimization_framework.contracts.candidates import validate_design


MAX_SOURCE_BYTES = 65536
MAX_STATE_BYTES = 1048576
MAX_OUTPUT_BYTES = MAX_STATE_BYTES + 65536
DEFAULT_TIMEOUT = 2.


class SandboxUnavailable(RuntimeError):
    """Mandatory isolation could not be established; never fall back to exec."""


class CandidateError(RuntimeError):
    """The candidate exceeded its limits or violated the optimizer protocol."""


# This small driver is trusted and passed as an argument, never read from a
# researcher-controlled path. Resource limits are applied before source exec.
DRIVER = r'''
import contextlib, json, math, resource, sys
resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))
resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
resource.setrlimit(resource.RLIMIT_NPROC, (16, 16))
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

def reject_constant(value):
    raise ValueError("Nonfinite JSON constant")

try:
    line = sys.stdin.buffer.readline(1200000)
    request = json.loads(line, parse_constant=reject_constant)
    if request["operation"] == "ping":
        result = {"sandbox": "bubblewrap", "protocol": 1}
    else:
        namespace = {"__name__": "candidate"}
        with contextlib.redirect_stdout(sys.stderr):
            exec(compile(request["source"], "<candidate>", "exec"), namespace)
            function = namespace[request["operation"]]
            if not callable(function):
                raise TypeError("Protocol member is not callable")
            result = function(*request["arguments"])
    response = json.dumps({"ok": True, "result": result}, allow_nan=False, separators=(",", ":"))
except BaseException as exc:
    response = json.dumps({"ok": False, "error": type(exc).__name__ + ": " + str(exc)[:2000]})
sys.stdout.write(response + "\n")
sys.stdout.flush()
'''


def _json_copy(value, limit=MAX_STATE_BYTES):
    try:
        encoded = json.dumps(value, allow_nan=False, separators=(",", ":"))
    except (ValueError, TypeError, RecursionError) as exc:
        raise CandidateError("Candidate state must contain only finite JSON values") from exc
    if len(encoded.encode()) > limit:
        raise CandidateError("Candidate JSON state exceeds the 1 MiB limit")
    return json.loads(encoded)


def validate_source(source):
    if not isinstance(source, str) or not source.strip():
        raise ValueError("Custom optimizer requires Python source")
    if len(source.encode()) > MAX_SOURCE_BYTES:
        raise ValueError("Custom optimizer source exceeds 64 KiB")
    try:
        module = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise ValueError(f"Invalid candidate Python source: {exc}") from exc
    functions = {node.name for node in module.body if isinstance(node, ast.FunctionDef)}
    if not {"initialize", "propose", "observe"} <= functions:
        raise ValueError("Source must define initialize(n_cells,seed,config), propose(state), and observe(state,design,efficiency)")
    return hashlib.sha256(source.encode()).hexdigest()


@lru_cache(maxsize=1)
def _runtime():
    bwrap = shutil.which("bwrap")
    # The system interpreter provides a small standard-library-only runtime;
    # the project's venv and editable source tree are never mounted.
    executable = Path("/usr/bin/python3")
    if not bwrap or not executable.exists():
        raise SandboxUnavailable("Custom algorithms require Linux bubblewrap and /usr/bin/python3. Install them before enabling custom execution.")
    executable = executable.resolve()
    try:
        raw = subprocess.check_output([str(executable), "-I", "-S", "-c",
            "import json,sysconfig; print(json.dumps({'stdlib':sysconfig.get_path('stdlib')}))"],
            timeout=5, text=True)
        stdlib = Path(json.loads(raw)["stdlib"]).resolve()
        libraries = set()
        binaries = [executable, *sorted((stdlib / "lib-dynload").glob("*.so"))]
        for binary in binaries:
            inspected = subprocess.run(["/usr/bin/ldd", str(binary)], capture_output=True, text=True, timeout=5)
            if inspected.returncode:
                raise SandboxUnavailable(f"Cannot inspect standard-library runtime dependency: {binary.name}")
            for path in re.findall(r"(/[^\s()]+)", inspected.stdout):
                if Path(path).is_file():
                    libraries.add(path)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise SandboxUnavailable(f"Cannot prepare the isolated Python runtime: {exc}") from exc
    return bwrap, str(executable), str(stdlib), tuple(sorted(libraries))


def sandbox_command():
    bwrap, executable, stdlib, libraries = _runtime()
    command = [bwrap, "--unshare-user", "--unshare-pid", "--unshare-net", "--unshare-ipc", "--unshare-uts",
               "--disable-userns", "--die-with-parent", "--new-session", "--cap-drop", "ALL",
               "--clearenv", "--setenv", "HOME", "/home/sandbox", "--setenv", "LANG", "C.UTF-8",
               "--ro-bind", executable, executable, "--ro-bind", stdlib, stdlib]
    for library in libraries:
        command.extend(["--ro-bind", library, library])
    # Some distributions put third-party modules under stdlib; mask them even
    # though -I -S also prevents their normal import-path initialization.
    for name in ("site-packages", "dist-packages"):
        target = str(Path(stdlib) / name)
        if Path(target).exists():
            command.extend(["--size", "4096", "--tmpfs", target, "--remount-ro", target])
    command.extend(["--proc", "/proc", "--dev", "/dev",
                    "--size", "16777216", "--tmpfs", "/tmp",
                    "--size", "16777216", "--tmpfs", "/home/sandbox",
                    "--chdir", "/tmp", "--remount-ro", "/", "--",
                    executable, "-I", "-S", "-B", "-u", "-c", DRIVER])
    return command


def _kill(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


def _exchange(command, payload, timeout):
    """Bounded JSONL IPC: never buffer untrusted output without a size cap."""
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True, close_fds=True,
                               env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
    stdout, stderr = bytearray(), bytearray()
    pending = memoryview(payload)
    deadline = time.monotonic() + timeout
    selector = selectors.DefaultSelector()
    for stream in (process.stdin, process.stdout, process.stderr):
        os.set_blocking(stream.fileno(), False)
    selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CandidateError(f"Candidate exceeded its {timeout:g}s per-request timeout")
            for key, _ in selector.select(min(remaining, .1)):
                if key.data == "stdin":
                    try:
                        written = os.write(key.fd, pending[:65536])
                        pending = pending[written:]
                    except BrokenPipeError:
                        pending = pending[:0]
                    if not pending:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                else:
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    target = stdout if key.data == "stdout" else stderr
                    target.extend(chunk)
                    limit = MAX_OUTPUT_BYTES if key.data == "stdout" else 65536
                    if len(target) > limit:
                        raise CandidateError("Candidate exceeded its output limit")
        process.wait(timeout=max(.01, deadline - time.monotonic()))
        return process.returncode, bytes(stdout), bytes(stderr)
    except BaseException:
        _kill(process)
        raise
    finally:
        selector.close()
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()


def sandbox_call(source, operation, arguments, timeout=DEFAULT_TIMEOUT):
    if (isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or
            not math.isfinite(timeout) or not .05 <= timeout <= 30):
        raise ValueError("request_timeout must be between 0.05 and 30 seconds")
    payload = json.dumps({"source": source, "operation": operation, "arguments": arguments},
                         allow_nan=False, separators=(",", ":")).encode() + b"\n"
    if len(payload) > 1150000:
        raise CandidateError("Candidate request exceeds the IPC size limit")
    try:
        code, stdout, stderr = _exchange(sandbox_command(), payload, timeout)
    except OSError as exc:
        raise SandboxUnavailable(f"Cannot launch bubblewrap: {exc}") from exc
    if code and not stdout:
        explanation = stderr.decode(errors="replace")[:1000].strip()
        if "bwrap:" in explanation:
            raise SandboxUnavailable("Custom optimizer sandbox unavailable: " + explanation +
                ". Enable unprivileged user namespaces and run the workspace outside an enclosing restricted sandbox. No fallback execution is allowed.")
        raise CandidateError(f"Candidate process exited with status {code}: {explanation}")
    try:
        def reject_constant(value):
            raise ValueError("Nonfinite JSON")
        response = json.loads(stdout, parse_constant=reject_constant)
    except (ValueError, RecursionError) as exc:
        raise CandidateError("Candidate returned malformed JSONL") from exc
    if not isinstance(response, dict) or response.get("ok") is not True:
        message = response.get("error", "Invalid protocol response") if isinstance(response, dict) else "Invalid protocol response"
        raise CandidateError(str(message))
    if code:
        raise CandidateError(f"Candidate exited unsuccessfully after returning output (status {code})")
    return _json_copy(response.get("result"))


def sandbox_status():
    """Actionable feature availability, without executing any candidate code."""
    try:
        result = sandbox_call("", "ping", [], timeout=5)
        return {"available": True, "sandbox": result["sandbox"], "protocol": 1,
                "runtime": "Python standard library only", "network": False}
    except (SandboxUnavailable, CandidateError) as exc:
        return {"available": False, "reason": str(exc), "sandbox": "bubblewrap", "network": False}


class CustomOptimizer:
    def __init__(self, n_cells, seed, config=None):
        config = dict(config or {})
        self.source = config.get("source")
        self.source_hash = validate_source(self.source)
        self.n_cells = n_cells
        self.seed = seed
        self.parameters = _json_copy(config.get("parameters", {}))
        self.timeout = config.get("request_timeout", DEFAULT_TIMEOUT)
        self.state = sandbox_call(self.source, "initialize", [n_cells, seed, self.parameters], self.timeout)
        self.count = 0

    def ask(self):
        proposal = sandbox_call(self.source, "propose", [self.state], self.timeout)
        if not isinstance(proposal, dict) or set(proposal) != {"design", "state"}:
            raise CandidateError("propose(state) must return exactly {design: [...], state: JSON}")
        design = validate_design(proposal["design"], self.n_cells)
        self.state = _json_copy(proposal["state"])
        return design

    def tell(self, design, efficiency):
        if not math.isfinite(efficiency):
            raise CandidateError("Custom optimizer may only observe finite efficiencies")
        self.state = sandbox_call(self.source, "observe", [self.state, design.tolist(), float(efficiency)], self.timeout)
        self.count += 1

    def state_dict(self):
        return {"source_hash": self.source_hash, "state": _json_copy(self.state), "count": self.count}

    def load_state_dict(self, state):
        if state["source_hash"] != self.source_hash:
            raise ValueError("Custom optimizer source differs from checkpoint")
        self.state, self.count = _json_copy(state["state"]), int(state["count"])

    def diagnostics(self):
        return {"source_hash": self.source_hash, "sandbox": "bubblewrap", "protocol": 1,
                "observations": self.count, "request_timeout": self.timeout,
                "state_bytes": len(json.dumps(self.state, separators=(",", ":")).encode())}


def verify_custom_source(source, *, n_cells=8, seed=0, parameters=None, request_timeout=DEFAULT_TIMEOUT):
    """Check the protocol in isolation before registering reviewed source.

    This supplies a synthetic observation, not scientific evidence of quality.
    Registration/approval and provenance remain the workspace service's job.
    """
    candidate = CustomOptimizer(n_cells, seed, {"source": source, "parameters": parameters or {},
                                               "request_timeout": request_timeout})
    repeated = CustomOptimizer(n_cells, seed, {"source": source, "parameters": parameters or {},
                                              "request_timeout": request_timeout})
    if candidate.state != repeated.state:
        raise CandidateError("initialize is not reproducible for the same seed and configuration")
    design = candidate.ask()
    if not (design == repeated.ask()).all() or candidate.state != repeated.state:
        raise CandidateError("propose is not reproducible from identical JSON state; keep RNG state explicit")
    candidate.tell(design, .5)
    repeated.tell(design, .5)
    if candidate.state != repeated.state:
        raise CandidateError("observe is not reproducible from identical JSON state")
    checkpoint = candidate.state_dict()
    expected = candidate.ask()
    repeated.load_state_dict(checkpoint)
    if not (expected == repeated.ask()).all() or candidate.state != repeated.state:
        raise CandidateError("propose did not reproduce after checkpoint restoration")
    return {"verified": True, "source_hash": candidate.source_hash, "protocol": 1,
            "sandbox": "bubblewrap", "runtime": "Python standard library only",
            "deterministic_probe": True,
            "note": "Protocol smoke check with a synthetic objective; optimizer quality is untested."}
