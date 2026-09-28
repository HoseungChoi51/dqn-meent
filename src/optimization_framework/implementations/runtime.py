"""Locked Python package runtimes and bounded, isolated optimizer processes."""
from __future__ import annotations

import base64
import fcntl
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import re
import selectors
import shutil
import signal
import subprocess
import sys
import sysconfig
import tempfile
import time

from optimization_framework.implementations.models import CapabilityUnavailable, parse_package, digest
from optimization_framework.implementations.legacy_runtime import CandidateError, SandboxUnavailable
from optimization_framework.storage.sqlite import atomic_json


PROTOCOL = "package_optimizer_v3"
RUNTIME_SCHEMA = 2
MAX_CHECKPOINT = 256 * 1024 * 1024
MAX_RESPONSE = 24 * 1024 * 1024


def file_hash(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def tree_hashes(root, *, include_bytecode=False):
    result = {}
    for path in sorted(Path(root).rglob("*")):
        if path.is_symlink():
            raise ValueError("Runtime and package snapshots cannot contain symlinks")
        if path.is_file() and (include_bytecode or "__pycache__" not in path.parts and path.suffix != ".pyc"):
            result[str(path.relative_to(root))] = file_hash(path)
    return result


def _installed_closure(dependencies, *, kind="optimizer"):
    from packaging.requirements import Requirement
    pending = list(dependencies)
    found = {}
    while pending:
        name = pending.pop()
        canonical = name.lower().replace("_", "-")
        if canonical in found:
            continue
        if canonical == "dqn-meent" or canonical == "meent" and kind != "evaluator":
            raise CapabilityUnavailable("Candidate dependencies cannot include the trusted evaluator")
        dist = metadata.distribution(name)
        wanted = dependencies.get(name)
        if wanted and dist.version != wanted:
            raise metadata.PackageNotFoundError(name)
        found[canonical] = dist
        for raw in dist.requires or []:
            req = Requirement(raw)
            if req.marker and not req.marker.evaluate({"extra": ""}):
                continue
            installed = metadata.distribution(req.name)
            if installed.version not in req.specifier:
                raise metadata.PackageNotFoundError(req.name)
            pending.append(req.name)
    return found


def prepare_runtime(directory, dependencies, *, allow_download=False, timeout=120, kind="optimizer"):
    """Copy installed distributions or acquire wheels; never import candidate code."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    # A build worker and an explicit resolver may request the same environment.
    # Never remove another caller's in-progress staging tree.
    with (directory / ".prepare.lock").open("a+") as lease:
        fcntl.flock(lease.fileno(), fcntl.LOCK_EX)
        return _prepare_runtime(directory, dependencies, allow_download=allow_download, timeout=timeout, kind=kind)


def _prepare_runtime(directory, dependencies, *, allow_download, timeout, kind):
    if kind not in {"optimizer", "evaluator"}:
        raise ValueError("Unknown executable kind")
    protocol = PROTOCOL if kind == "optimizer" else "package_evaluator_v1"
    identity = digest({"dependencies": dependencies, "python": sys.version, "protocol": protocol,
                       "schema_version": RUNTIME_SCHEMA, "platform": platform_identity()})
    root = directory / identity
    if (root / "runtime.json").exists():
        manifest = json.loads((root / "runtime.json").read_text())
        verify_runtime(root, manifest)
        return root, manifest
    stage = directory / (identity + ".building")
    if stage.exists():
        # Only this service's incomplete staging tree; published runtimes never change.
        shutil.rmtree(stage)
    stage.mkdir(parents=True, exist_ok=True)
    site = stage / "site-packages"
    site.mkdir(exist_ok=True)
    try:
        closure = _installed_closure(dependencies, kind=kind)
        for dist in closure.values():
            for member in dist.files or []:
                relative = Path(str(member))
                if relative.is_absolute() or ".." in relative.parts or "__pycache__" in relative.parts or relative.suffix == ".pyc":
                    continue
                source = Path(dist.locate_file(member))
                if source.is_file():
                    target = site / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
        locked = {name: dist.version for name, dist in closure.items()}
    except metadata.PackageNotFoundError as exc:
        if not allow_download:
            raise CapabilityUnavailable(f"Dependency {exc.name} is not available at its requested version. Enable wheel acquisition on the implementation service or supply this runtime.") from exc
        uv = shutil.which("uv")
        if not uv:
            raise CapabilityUnavailable("Wheel acquisition requires uv")
        # Only registry wheels; project sources, editable installs, and build scripts
        # are not executed in the service process.
        acquired = stage / "acquired"
        command = [uv, "pip", "install", "--python", sys.executable, "--target", str(acquired),
                   "--only-binary", ":all:", *[f"{name}=={version}" for name, version in sorted(dependencies.items())]]
        result = subprocess.run(command, capture_output=True, timeout=timeout,
                                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(stage.resolve()),
                                     "UV_CACHE_DIR": str((directory / "wheel-cache").resolve())})
        if result.returncode:
            raise CapabilityUnavailable("Pinned dependency wheels could not be acquired")
        # Failed installed-copy attempts are not part of the acquired environment.
        shutil.rmtree(site)
        acquired.rename(site)
        distributions = list(metadata.distributions(path=[str(site)]))
        locked = {d.metadata["Name"].lower().replace("_", "-"): d.version for d in distributions}
        if "dqn-meent" in locked or "meent" in locked and kind != "evaluator":
            raise CapabilityUnavailable("Candidate dependencies cannot include the trusted evaluator")
    executable = Path(sys.executable).resolve()
    stdlib = Path(sysconfig.get_path("stdlib")).resolve()
    libraries = {}
    # ldd is restricted to trusted Python binaries. Inspect wheel ELF metadata
    # without executing its loader or any of its initialization code.
    binaries = [executable, *sorted((stdlib / "lib-dynload").glob("*.so"))]
    for binary in binaries:
        inspected = subprocess.run(["/usr/bin/ldd", str(binary)], capture_output=True, text=True, timeout=10)
        for raw in re.findall(r"(/[^\s()]+)", inspected.stdout):
            path = Path(raw)
            if path.is_file() and not path.resolve().is_relative_to(site.resolve()):
                libraries[raw] = file_hash(path)
    candidates = list(site.rglob("*.so*"))
    if candidates:
        readelf = shutil.which("readelf")
        if not readelf:
            raise CapabilityUnavailable("Native dependency inspection requires readelf")
        system = subprocess.run(["/sbin/ldconfig", "-p"], capture_output=True, text=True, check=True, timeout=10)
        # Hosts may also install 32-bit or foreign-architecture libraries.
        # Match ELF class, endianness and machine to the trusted interpreter.
        with executable.open("rb") as stream:
            host_header = stream.read(20)
        available = {}
        for name, path in re.findall(r"^\s*(\S+)\s+\([^\n]*\)\s+=>\s+(\S+)", system.stdout, re.M):
            with Path(path).open("rb") as stream:
                header = stream.read(20)
            if header[:6] == host_header[:6] and header[18:20] == host_header[18:20]:
                available.setdefault(name, path)
        bundled = {path.name for path in candidates}
        inspected_paths = set()
        while candidates:
            binary = candidates.pop()
            if str(binary) in inspected_paths:
                continue
            inspected_paths.add(str(binary))
            inspected = subprocess.run([readelf, "-d", str(binary)], capture_output=True, text=True, timeout=10)
            for name in re.findall(r"\(NEEDED\).*?\[([^\]]+)\]", inspected.stdout):
                if name in bundled:
                    continue
                path = available.get(name)
                if not path:
                    raise CapabilityUnavailable(f"Native dependency {name} is unavailable")
                libraries[path] = file_hash(path)
                candidates.append(Path(path))
    # The standard library is executable input too. Capture it without local
    # bytecode or ambient third-party packages and mount only this snapshot.
    shutil.copytree(stdlib, stage / "stdlib", ignore=shutil.ignore_patterns(
        "__pycache__", "*.pyc", "site-packages", "dist-packages"))
    portable_libraries, locations = {}, {}
    for path, file_digest in sorted(libraries.items()):
        name = Path(path).name
        if name in portable_libraries and portable_libraries[name] != file_digest:
            raise CapabilityUnavailable(f"Runtime has conflicting system libraries named {name}")
        portable_libraries[name] = file_digest
        # Keep aliases such as /lib64/ld-linux-*.so.*: the ELF interpreter
        # names its loader by an absolute ABI path, even when the same bytes
        # are also present under /lib/<architecture>.
        locations.setdefault(name, []).append(path)
    manifest = {"schema_version": RUNTIME_SCHEMA, "protocol": protocol, "python": sys.version,
                "platform": platform_identity(), "executable_hash": file_hash(executable),
                "stdlib_files": tree_hashes(stage / "stdlib", include_bytecode=True), "dependencies": locked,
                "libraries": portable_libraries, "files": tree_hashes(site, include_bytecode=True)}
    manifest["digest"] = digest(manifest)
    atomic_json(stage / "runtime.json", manifest)
    atomic_json(stage / "binding.json", {"runtime_digest": manifest["digest"],
        "executable": str(executable), "stdlib": str(stdlib), "libraries": locations})
    stage.rename(root)
    return root, manifest


def platform_identity():
    return {"system": sys.platform, "machine": platform.machine(),
            "implementation": sys.implementation.name, "abi": sysconfig.get_config_var("SOABI")}


def verify_runtime(root, manifest):
    """Verify content against a local binding; legacy identities are never rewritten."""
    root = Path(root)
    if not (root / "site-packages").is_dir():
        raise ValueError("Implementation runtime files are unavailable at their recorded location")
    expected = {key: value for key, value in manifest.items() if key != "digest"}
    if digest(expected) != manifest["digest"]:
        raise ValueError("Implementation runtime identity changed")
    schema = manifest.get("schema_version", 1)
    if schema == 1:
        if (root / "conversion.json").exists():
            from optimization_framework.implementations.runtime_conversion import verify
            return verify(root, manifest)
        binding = {"executable": manifest["executable"], "stdlib": manifest["stdlib"],
                   "libraries": {path: [path] for path in manifest["libraries"]}}
        stdlib_source = binding["stdlib"]
    elif schema == RUNTIME_SCHEMA:
        if manifest["platform"] != platform_identity():
            raise ValueError("Implementation runtime platform is incompatible")
        binding = json.loads((root / "binding.json").read_text())
        if (not isinstance(binding, dict) or binding.get("runtime_digest") != manifest["digest"]
                or not isinstance(binding.get("libraries"), dict)
                or set(binding["libraries"]) != set(manifest["libraries"])
                or any(not isinstance(binding.get(key), str) or not Path(binding[key]).is_absolute()
                       for key in ("executable", "stdlib"))
                or any(not isinstance(paths, list) or not paths
                       or any(not isinstance(path, str) or not Path(path).is_absolute() for path in paths)
                       for paths in binding["libraries"].values())):
            raise ValueError("Implementation runtime binding does not match its identity")
        stdlib_source = root / "stdlib"
        if not stdlib_source.is_dir() or tree_hashes(stdlib_source, include_bytecode=True) != manifest["stdlib_files"]:
            raise ValueError("Implementation runtime standard library changed after validation")
    else:
        raise ValueError("Unsupported implementation runtime schema")
    if file_hash(binding["executable"]) != manifest["executable_hash"]:
        raise ValueError("Implementation runtime interpreter identity changed")
    if tree_hashes(root / "site-packages", include_bytecode=schema == RUNTIME_SCHEMA) != manifest["files"]:
        raise ValueError("Implementation runtime files changed after validation")
    if any(not binding["libraries"][name] or any(file_hash(path) != value for path in binding["libraries"][name])
           for name, value in manifest["libraries"].items()):
        raise ValueError("Implementation runtime system libraries changed")
    return {**binding, "stdlib_source": str(stdlib_source)}


def resolve_runtime(directory, manifest):
    """Explicitly bind exact installed runtime content; never download or run a package."""
    if manifest.get("schema_version", 1) == 1:
        from optimization_framework.implementations.runtime_conversion import resolve
        return resolve(directory, manifest)
    if manifest.get("schema_version", 1) != RUNTIME_SCHEMA:
        raise CapabilityUnavailable("Unsupported implementation runtime schema")
    if digest({key: value for key, value in manifest.items() if key != "digest"}) != manifest.get("digest"):
        raise ValueError("Implementation runtime identity changed")
    if manifest.get("platform") != platform_identity():
        raise CapabilityUnavailable("No compatible local runtime platform")
    protocol = manifest.get("protocol")
    if protocol not in {PROTOCOL, "package_evaluator_v1"}:
        raise CapabilityUnavailable("Unsupported executable runtime protocol")
    kind = "evaluator" if protocol == "package_evaluator_v1" else "optimizer"
    replacement = None
    try:
        try:
            root, candidate = prepare_runtime(directory, manifest["dependencies"], kind=kind, allow_download=False)
        except CapabilityUnavailable:
            raise
        except (OSError, ValueError):
            # Retain a damaged prior installation as evidence. Prepare a new
            # local binding without modifying files pinned by another worker.
            replacement = Path(tempfile.mkdtemp(prefix="resolved-", dir=directory))
            root, candidate = prepare_runtime(replacement, manifest["dependencies"], kind=kind, allow_download=False)
        if candidate != manifest:
            raise CapabilityUnavailable("Installed interpreter, standard library or dependencies do not match the required runtime content")
        verify_runtime(root, manifest)
        return root
    except BaseException:
        if replacement is not None:
            shutil.rmtree(replacement)
        raise


def bundle_runtime_root(bundle, directory=None):
    """Installation paths belong to local bundles, not executable content identities."""
    root = bundle.get("runtime_root") or bundle["artifact"].get("runtime_root")
    location = Path(directory) / "runtime-location.json" if directory is not None else None
    if location is not None and location.exists():
        binding = json.loads(location.read_text())
        if not isinstance(binding, dict) or binding.get("runtime_digest") != bundle["artifact"]["runtime"]["digest"]:
            raise ValueError("Local runtime binding refers to a different executable runtime")
        root = binding.get("root")
    elif bundle.get("runtime_binding_error"):
        raise ValueError(bundle["runtime_binding_error"])
    if not root:
        raise ValueError("The executable runtime has not been resolved in this workspace")
    return Path(root)


def pin_runtime(directory, bundle):
    """Refresh only the operational binding, preserving a frozen bundle's identity."""
    root = bundle_runtime_root(bundle)
    verify_runtime(root, bundle["artifact"]["runtime"])
    atomic_json(Path(directory) / "runtime-location.json", {
        "runtime_digest": bundle["artifact"]["runtime"]["digest"], "root": str(root)})


DRIVER = r'''
import contextlib, hashlib, importlib, json, os, resource, sys
resource.setrlimit(resource.RLIMIT_AS, (4*1024**3, 4*1024**3))
resource.setrlimit(resource.RLIMIT_FSIZE, (32*1024**2, 32*1024**2))
resource.setrlimit(resource.RLIMIT_NOFILE, (64,64))
resource.setrlimit(resource.RLIMIT_NPROC, (64,64))
resource.setrlimit(resource.RLIMIT_CORE, (0,0))
sys.path[:0] = ['/candidate', '/deps']
optimizer = None
checkpoint_limit = 256 * 1024**2
for line in sys.stdin.buffer:
    binary = None
    try:
        if len(line)>24*1024**2: raise ValueError('Request is too large')
        request = json.loads(line)
        op = request['operation']
        with contextlib.redirect_stdout(sys.stderr):
            if op=='initialize':
                contract = request['contract']
                module, factory = request['entrypoint'].split(':')
                optimizer = getattr(importlib.import_module(module),factory)(request['context'])
                checkpoint_limit = request['checkpoint_limit']
                if request['contract']=='optimizer_v1':
                    context = request['context']
                    optimizer.initialize(context['problem'],context['parameters'],context['seed'],context.get('declared_assets',[]))
                result = None
            elif op=='ask':
                result = optimizer.ask()
                if hasattr(result,'tolist'): result = result.tolist()
            elif op=='tell': result = optimizer.tell(request['design'],request['efficiency'])
            elif op=='propose': result = optimizer.propose(request['max_candidates'])
            elif op=='observe': result = optimizer.observe(request['observations'])
            elif op=='evaluate' and contract=='evaluator_v1': result = optimizer.evaluate(request['candidate'])
            elif op=='inspect': result = optimizer.inspect()
            elif op=='export_artifacts': result = optimizer.export_artifacts() if hasattr(optimizer,'export_artifacts') else []
            elif op=='checkpoint':
                raw = optimizer.checkpoint()
                if not isinstance(raw,bytes) or len(raw)>checkpoint_limit: raise ValueError('Checkpoint exceeds its declared byte allowance')
                binary = raw
                result = None
            elif op=='restore':
                size = request['binary_bytes']
                if type(size) is not int or not 0 <= size <= checkpoint_limit: raise ValueError('Oversized checkpoint')
                raw = sys.stdin.buffer.read(size)
                if len(raw)!=size or hashlib.sha256(raw).hexdigest()!=request['sha256']: raise ValueError('Checkpoint transfer integrity mismatch')
                result = optimizer.restore(raw)
            else: raise ValueError('Unknown operation')
        answer = json.dumps({'ok':True,'result':result, **({'binary_bytes':len(binary),'sha256':hashlib.sha256(binary).hexdigest()} if binary is not None else {})},allow_nan=False,separators=(',',':'))
    except BaseException as exc:
        answer = json.dumps({'ok':False,'error':type(exc).__name__+': '+str(exc)[:2000]})
    sys.stdout.write(answer+'\n')
    sys.stdout.flush()
    if binary is not None:
        view = memoryview(binary)
        for offset in range(0,len(view),65536):
            sys.stdout.buffer.write(view[offset:offset+65536])
        sys.stdout.buffer.flush()
'''


def package_command(package_dir, runtime_root, manifest, *, assets_dir=None, driver=None, legacy_namespace=False,
                    legacy_paths=False, request_runtime_root=None):
    """Construct the package namespace from verified local runtime bindings."""
    binding = verify_runtime(runtime_root, manifest)
    if legacy_paths:
        if manifest.get("schema_version", 1) != 1:
            raise ValueError("Historical path commands require a legacy runtime manifest")
        # Recognition only: the installed package host always launches using
        # verified local bindings and its current policy.
        binding = {"executable": manifest["executable"], "stdlib": manifest["stdlib"], "stdlib_source": manifest["stdlib"],
                   "libraries": {path: [path] for path in manifest["libraries"]}}
    libraries = [path for paths in binding["libraries"].values() for path in paths]
    bwrap = shutil.which("bwrap")
    if not bwrap:
        raise SandboxUnavailable("Package execution requires bubblewrap")
    command = [bwrap, "--unshare-all", "--die-with-parent", "--new-session", "--cap-drop", "ALL", "--clearenv",
               "--setenv", "HOME", "/work", "--setenv", "LANG", "C.UTF-8", "--setenv", "OPENBLAS_NUM_THREADS", "1",
               "--setenv", "OMP_NUM_THREADS", "1", "--setenv", "MKL_NUM_THREADS", "1",
               "--setenv", "LD_LIBRARY_PATH", ":".join(sorted({str(Path(path).parent) for path in libraries})),
               "--ro-bind", binding["executable"], binding["executable"],
               "--ro-bind", binding["stdlib_source"], binding["stdlib"]]
    for path in libraries:
        command.extend(["--ro-bind", path, path])
    for name in ("site-packages", "dist-packages"):
        path = Path(binding["stdlib"]) / name
        mask_source = Path(runtime_root) / "stdlib" if legacy_paths and (Path(runtime_root) / "conversion.json").exists() else Path(binding["stdlib_source"])
        if (mask_source / name).exists():
            command.extend(["--tmpfs", str(path), "--remount-ro", str(path)])
    if assets_dir is not None:
        command.extend(["--ro-bind", str(Path(assets_dir).resolve()), "/assets"])
    namespace = ["--proc", "/proc", "--dev", "/dev"] if legacy_namespace else [
        "--proc", "/proc", "--remount-ro", "/proc", "--dev", "/dev", "--size", "67108864",
        "--tmpfs", "/dev/shm", "--remount-ro", "/dev"]
    return command + ["--ro-bind", str(Path(package_dir).resolve()), "/candidate",
                "--ro-bind", str((Path(request_runtime_root or runtime_root) / "site-packages").resolve()), "/deps",
                *namespace, "--size", "67108864", "--tmpfs", "/work",
                "--chdir", "/work", "--remount-ro", "/", "--", binding["executable"], "-I", "-S", "-B", "-u", "-c",
                DRIVER if driver is None else driver]


class PackageProcess:
    def __init__(self, package_dir, runtime_root, manifest, entrypoint, context, *, timeout=10, progress=None, max_checkpoint_bytes=MAX_CHECKPOINT,
                 contract="ask_tell", assets_dir=None):
        self.process = None
        if contract not in {"ask_tell", "optimizer_v1", "evaluator_v1"}:
            raise ValueError("Unsupported executable contract")
        self.contract = contract
        self.context = context
        self.progress = progress
        self.timeout = timeout
        self.runtime_digest = manifest["digest"]
        if type(max_checkpoint_bytes) is not int or not 1024 <= max_checkpoint_bytes <= 4 * 1024**3:
            raise ValueError("Checkpoint byte allowance must be between 1 KiB and 4 GiB")
        self.max_checkpoint_bytes = max_checkpoint_bytes
        command = package_command(package_dir, runtime_root, manifest, assets_dir=assets_dir)
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        start_new_session=True, env={"PATH": "/usr/bin:/bin"})
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            os.set_blocking(stream.fileno(), False)
        try:
            self._request("initialize", entrypoint=entrypoint, context=context, checkpoint_limit=max_checkpoint_bytes, contract=contract)
        except BaseException:
            self.close()
            raise

    def _request(self, operation, *, binary=None, **data):
        payload = memoryview((json.dumps({"operation": operation, **data}, allow_nan=False) + "\n").encode())
        if len(payload) > MAX_RESPONSE:
            raise CandidateError("Package request is too large")
        output, errors = bytearray(), bytearray()
        transfer = memoryview(binary) if binary is not None else memoryview(b"")
        header = None
        expected_bytes = None
        deadline = time.monotonic() + self.timeout
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdin, selectors.EVENT_WRITE, "input")
            selector.register(self.process.stdout, selectors.EVENT_READ, "output")
            selector.register(self.process.stderr, selectors.EVENT_READ, "error")
            try:
                while time.monotonic() < deadline:
                    if self.progress:
                        self.progress()
                    for key, _ in selector.select(.05):
                        if key.data == "input":
                            count = os.write(key.fd, payload[:65536])
                            payload = payload[count:]
                            if not payload:
                                if transfer:
                                    payload, transfer = transfer, memoryview(b"")
                                else:
                                    selector.unregister(key.fileobj)
                        else:
                            chunk = os.read(key.fd, 65536)
                            target = output if key.data == "output" else errors
                            target.extend(chunk)
                            if len(target) > ((self.max_checkpoint_bytes if expected_bytes is not None else MAX_RESPONSE) if key.data == "output" else 65536):
                                raise CandidateError("Package output limit exceeded")
                            if key.data == "output" and header is None and b"\n" in output:
                                raw_header, _, rest = output.partition(b"\n")
                                header = json.loads(raw_header)
                                output = bytearray(rest)
                                if header.get("ok") is not True:
                                    raise CandidateError(header.get("error", "Invalid package reply"))
                                if "binary_bytes" not in header:
                                    if output:
                                        raise CandidateError("Unexpected bytes after package response")
                                    return header.get("result")
                                expected_bytes = header["binary_bytes"]
                                if type(expected_bytes) is not int or not 0 <= expected_bytes <= self.max_checkpoint_bytes:
                                    raise CandidateError("Checkpoint exceeds its declared byte allowance")
                            if key.data == "output" and expected_bytes is not None and len(output) >= expected_bytes:
                                if len(output) != expected_bytes or hashlib.sha256(output).hexdigest() != header.get("sha256"):
                                    raise CandidateError("Checkpoint transfer integrity mismatch")
                                return bytes(output)
                            if not chunk:
                                selector.unregister(key.fileobj)
                    if self.process.poll() is not None:
                        detail = errors.decode(errors="replace")[:1500]
                        if "bwrap:" in detail:
                            raise SandboxUnavailable(detail)
                        raise CandidateError("Package process exited: " + detail)
                raise CandidateError(f"Package exceeded its {self.timeout:g}s operation timeout")
            except BaseException:
                self.close()
                raise

    def close(self):
        process = getattr(self, "process", None)
        if process is not None:
            self.process = None
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()

    def __del__(self):
        self.close()


class PackageOptimizer(PackageProcess):
    def __init__(self, package_dir, runtime_root, manifest, entrypoint, context, *, timeout=10, progress=None,
                 max_checkpoint_bytes=MAX_CHECKPOINT, contract="ask_tell", supports_failure_observations=False, assets_dir=None):
        if contract not in {"ask_tell", "optimizer_v1"}:
            raise ValueError("Unsupported optimizer contract")
        self.supports_failure_observations = supports_failure_observations
        self.pending = []
        self.count = 0
        self.n_cells = context["n_cells"]
        from optimization_framework.contracts.problems import CandidateSchema
        schema = context.get("problem", {}).get("candidate_schema")
        self.candidate_schema = CandidateSchema(**schema) if schema else CandidateSchema(representation="binary", dimensions=self.n_cells)
        super().__init__(package_dir, runtime_root, manifest, entrypoint, context, timeout=timeout, progress=progress,
                         max_checkpoint_bytes=max_checkpoint_bytes, contract=contract, assets_dir=assets_dir)

    def ask(self):
        import numpy as np
        # The worker records intent and validates before evaluation. Never coerce
        # fractional, malformed, or infeasible proposals into a legal candidate.
        return np.asarray(self.propose(1)[0].candidate if self.contract == "optimizer_v1" else self._request("ask"))

    def tell(self, design, efficiency):
        import math
        if not math.isfinite(efficiency):
            raise CandidateError("Observation must be finite")
        candidate = self.candidate_schema.canonicalize(design)
        if self.contract == "optimizer_v1":
            from optimization_framework.contracts.problems import Objective, Observation
            objective = Objective(**self.context["problem"]["primary_objective"])
            self.observe([Observation(id=f"fixture_{self.count}", experiment_id="correctness_fixture", attempt_id="correctness_fixture",
                request_id=f"fixture_{self.count}", proposal_id=self.pending[0].id, candidate=candidate,
                status="ok", objectives={objective.name: objective.utility(efficiency)}, evaluator_identity="protected_fixture")])
            return
        self._request("tell", design=candidate, efficiency=float(efficiency))
        self.count += 1

    def state_dict(self):
        return {"runtime_digest": self.runtime_digest, "checkpoint": self._request("checkpoint"), "count": self.count,
                "pending": [p.model_dump(mode="json") for p in self.pending], "contract": self.contract}

    def load_state_dict(self, state):
        if state["runtime_digest"] != self.runtime_digest:
            raise ValueError("Checkpoint runtime changed")
        if state.get("contract", "ask_tell") != self.contract:
            raise ValueError("Checkpoint optimizer contract changed")
        raw = state["checkpoint"]
        if not isinstance(raw, bytes) or len(raw) > self.max_checkpoint_bytes:
            raise CandidateError("Oversized checkpoint")
        self._request("restore", binary=raw, binary_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        self.count = state["count"]
        from optimization_framework.contracts.problems import Proposal
        self.pending = [Proposal(**p) for p in state.get("pending", [])]

    def initialize(self, problem_descriptor, parameters, seed, declared_assets):
        if (problem_descriptor != self.context["problem"] or parameters != self.context["parameters"]
                or seed != self.context["seed"] or declared_assets != self.context.get("declared_assets", [])):
            raise ValueError("A package runtime is initialized exactly once with its frozen context")

    def propose(self, max_candidates=1):
        from optimization_framework.contracts.problems import Proposal
        if self.contract != "optimizer_v1":
            raise ValueError("Legacy packages use the explicit ask/tell lifecycle adapter")
        if self.pending:
            raise CandidateError("Observe the outstanding batch before proposing again")
        proposals = self._request("propose", max_candidates=max_candidates)
        if not isinstance(proposals, list) or not 1 <= len(proposals) <= max_candidates:
            raise CandidateError("Package violated the declared batch size")
        self.pending = [Proposal(**p) for p in proposals]
        if len({p.id for p in self.pending}) != len(self.pending):
            raise CandidateError("Proposal identities must be unique within a batch")
        return self.pending.copy()

    def observe(self, observations):
        if [o.proposal_id for o in observations] != [p.id for p in self.pending]:
            raise CandidateError("Observation identities or order do not match the pending batch")
        if any(o.status != "ok" for o in observations) and not self.supports_failure_observations:
            raise CandidateError("Package does not declare support for failed evaluation outcomes")
        self._request("observe", observations=[o.model_dump(mode="json") for o in observations])
        self.pending = []
        self.count += len(observations)

    def checkpoint(self):
        return self.state_dict()

    def restore(self, manifest):
        self.load_state_dict(manifest)

    def inspect(self):
        return self._request("inspect") if self.contract == "optimizer_v1" else self.diagnostics()

    def export_artifacts(self):
        return self._request("export_artifacts") if self.contract == "optimizer_v1" else []

    def diagnostics(self):
        return {"protocol": PROTOCOL, "runtime_digest": self.runtime_digest, "observations": self.count}

def write_package(directory, package):
    package = parse_package(package)
    directory = Path(directory)
    expected = {source.path: hashlib.sha256(source.content.encode()).hexdigest() for source in package.files}
    if directory.exists():
        if tree_hashes(directory) != expected:
            raise ValueError("An existing package snapshot differs from the requested immutable package")
        return directory
    directory.mkdir(parents=True, exist_ok=True)
    for source in package.files:
        target = directory / source.path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or not target.resolve().is_relative_to(directory.resolve()):
            raise ValueError("Package path escapes its snapshot")
        target.write_text(source.content)
    return directory
