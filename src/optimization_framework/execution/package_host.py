"""Host-owned launcher for the packages pinned by one frozen experiment.

The captured worker supplies a package invocation, never launch authority. Only
exact verified invocations are accepted. Their pipes, not host filesystem
descriptors, cross the namespace boundary. This is not a scheduler.
"""
import array
import ast
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time

from optimization_framework.execution.package_client import MAX_REQUEST
from optimization_framework.implementations.models import digest
from optimization_framework.implementations.runtime import bundle_runtime_root, package_command, tree_hashes
from optimization_framework.storage.sqlite import read_json


# Enforce limits before interpreting the captured driver inside the package
# namespace. Captured code cannot raise these hard limits.
LIMITS = """import resource
resource.setrlimit(resource.RLIMIT_AS, (4*1024**3, 4*1024**3))
resource.setrlimit(resource.RLIMIT_FSIZE, (32*1024**2, 32*1024**2))
resource.setrlimit(resource.RLIMIT_NOFILE, (64,64))
resource.setrlimit(resource.RLIMIT_NPROC, (64,64))
resource.setrlimit(resource.RLIMIT_CORE, (0,0))
"""


def captured_driver(directory):
    source = Path(directory) / "code/optimization_framework/implementations/runtime.py"
    with source.open('rb') as stream:
        text = stream.read(MAX_REQUEST + 1)
    if len(text) > MAX_REQUEST:
        raise ValueError('Captured package launcher exceeds the host parsing allowance')
    for node in ast.parse(text).body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "DRIVER" for target in node.targets):
            value = ast.literal_eval(node.value)
            if isinstance(value, str) and len(value.encode()) < MAX_REQUEST // 2:
                return value
    raise ValueError("Captured package runtime has no supported literal driver")


def allowed_commands(directory, spec):
    directory = Path(directory).resolve()
    result = {}
    for name, kind, prefix in (("implementation", "optimizer", "implementation"), ("evaluator", "evaluator", "evaluator")):
        package = directory / name
        if not (package / "bundle.json").exists():
            if spec.get(prefix + "_version_id"):
                raise ValueError(f"The frozen {kind} package is unavailable")
            continue
        bundle = read_json(package / "bundle.json")
        artifact, version = bundle["artifact"], bundle["version"]
        if (version["id"] != spec.get(prefix + "_version_id") or version.get("kind", "optimizer") != kind
                or digest(artifact) != spec.get(prefix + "_artifact_digest")
                or artifact["runtime"]["digest"] != spec.get(prefix + "_runtime_digest")
                or tree_hashes(package / "package") != version["package_hashes"]):
            raise ValueError(f"The frozen {kind} package identity changed")
        driver = captured_driver(directory)
        invocation = package_command(package / "package", bundle_runtime_root(bundle, package), artifact["runtime"],
            driver=driver, assets_dir=directory / "inputs" if kind == "optimizer" and spec.get("declared_assets") else None)
        legacy = package_command(package / "package", bundle_runtime_root(bundle, package), artifact["runtime"],
            driver=driver, assets_dir=directory / "inputs" if kind == "optimizer" and spec.get("declared_assets") else None,
            legacy_namespace=True)
        # The captured runtime resolves bwrap to the relay inside its namespace.
        # Everything after that executable must match the fixed host policy.
        result[tuple(invocation[1:])] = (kind, [*invocation[:-1], LIMITS + driver])
        # Earlier captured launchers omit the shared-memory byte bound. Their
        # exact request is recognized, but always runs the current host policy.
        result[tuple(legacy[1:])] = (kind, [*invocation[:-1], LIMITS + driver])
        if artifact["runtime"].get("schema_version", 1) == 1:
            for namespace in (False, True):
                for root in {None, artifact.get("runtime_root")}:
                    historical = package_command(package / "package", bundle_runtime_root(bundle, package), artifact["runtime"],
                        driver=driver, assets_dir=directory / "inputs" if kind == "optimizer" and spec.get("declared_assets") else None,
                        legacy_namespace=namespace, legacy_paths=True, request_runtime_root=root)
                    result[tuple(historical[1:])] = (kind, [*invocation[:-1], LIMITS + driver])
    return result


def kill(process):
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class PackageHost:
    def __init__(self, directory, spec, *, max_live=16, max_launches=4096):
        self.allowed = allowed_commands(directory, spec)
        self.max_live, self.max_launches = max_live, max_launches
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.clients, self.processes, self.threads = set(), set(), set()
        self.records = []
        self.rejected = 0
        self.temporary = None

    def __enter__(self):
        # Keep the actual socket path within Linux's sockaddr_un length limit.
        self.temporary = tempfile.TemporaryDirectory(prefix="optimization-package-")
        root = Path(self.temporary.name)
        self.socket_path = root / "socket"
        self.client_path = root / "bwrap"
        client = Path(__file__).with_name("package_client.py").read_text()
        self.client_path.write_text(f"#!{Path(sys.executable).resolve()} -S\n" + client)
        self.client_path.chmod(0o500)
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(self.socket_path))
        self.socket_path.chmod(0o600)
        self.listener.listen(self.max_live)
        self.listener.settimeout(.1)
        self.acceptor = threading.Thread(target=self._accept, daemon=True)
        self.acceptor.start()
        return self

    def _accept(self):
        while not self.stop.is_set():
            try:
                connection, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with self.lock:
                if self.stop.is_set() or len(self.clients) >= self.max_live or len(self.records) >= self.max_launches:
                    self.rejected += 1
                    connection.close()
                    continue
                self.clients.add(connection)
                record = {"started_at": time.time(), "kind": None, "status": "requested"}
                self.records.append(record)
                thread = threading.Thread(target=self._serve, args=(connection, record), daemon=True)
                self.threads.add(thread)
            thread.start()

    def _read(self, connection, size, deadline):
        data = bytearray()
        while len(data) < size:
            if self.stop.is_set() or time.monotonic() >= deadline:
                raise ValueError("Package launch handshake timed out")
            try:
                chunk = connection.recv(min(65536, size - len(data)))
            except socket.timeout:
                continue
            if not chunk:
                raise ValueError("Package launch disconnected")
            data.extend(chunk)
        return data

    def _serve(self, connection, record):
        process = None
        started = time.monotonic()
        connection.settimeout(.1)
        try:
            deadline = started + 5
            size = struct.unpack("!I", self._read(connection, 4, deadline))[0]
            if not 0 < size <= MAX_REQUEST:
                raise ValueError("Package launch request exceeds its allowance")
            request = self._read(connection, size, deadline)
            record["request_sha256"] = hashlib.sha256(request).hexdigest()
            arguments = json.loads(request)
            if not isinstance(arguments, list) or not all(isinstance(item, str) for item in arguments):
                raise ValueError("Package launch requires a string argument vector")
            permitted = self.allowed.get(tuple(arguments))
            if permitted is None:
                raise ValueError("Package invocation is not declared by the frozen experiment")
            kind, invocation = permitted
            with self.lock:
                if self.stop.is_set():
                    raise ValueError("Package supervisor is stopping")
                process = subprocess.Popen(invocation, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    start_new_session=True, env={"PATH": "/usr/bin:/bin"})
                self.processes.add(process)
                record.update(kind=kind, status="running", pid=process.pid)
            descriptors = array.array("i", [process.stdin.fileno(), process.stdout.fileno(), process.stderr.fileno()])
            connection.sendmsg([b"R"], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, descriptors)])
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
            with selectors.DefaultSelector() as selector:
                selector.register(connection, selectors.EVENT_READ)
                while not self.stop.is_set() and process.poll() is None:
                    if selector.select(.1):
                        data = connection.recv(1)
                        record["status"] = "disconnected" if not data else "invalid_protocol"
                        break
            if process.poll() is not None:
                connection.sendall(struct.pack("!i", process.returncode))
                record["status"] = "exited"
            elif self.stop.is_set():
                record["status"] = "host_stopped"
        except (OSError, ValueError, RecursionError) as exc:
            record.update(status="rejected" if process is None else "disconnected", error=str(exc)[:2000])
            if process is None:
                try:
                    connection.sendall(b"E")
                except OSError:
                    pass
        finally:
            if process is not None:
                kill(process)
                record["exit_code"] = process.wait()
                for stream in (process.stdin, process.stdout, process.stderr):
                    stream.close()
            connection.close()
            with self.lock:
                record.update(finished_at=time.time(), elapsed_seconds=time.monotonic() - started)
                self.clients.discard(connection)
                self.processes.discard(process)
                self.threads.discard(threading.current_thread())

    def terminate(self):
        self.stop.set()
        with self.lock:
            for process in self.processes:
                kill(process)
            for connection in self.clients:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def close(self):
        self.terminate()
        if self.temporary is not None:
            self.acceptor.join(timeout=2)
            self.listener.close()
            with self.lock:
                threads = list(self.threads)
            for thread in threads:
                thread.join(timeout=2)
            self.temporary.cleanup()
            self.temporary = None

    def __exit__(self, *_):
        self.close()
