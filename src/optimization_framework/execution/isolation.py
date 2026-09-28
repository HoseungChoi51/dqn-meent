"""Host-owned Linux isolation for execution of imported framework captures.

The caller verifies source identity separately. A capture never supplies mounts,
environment variables, resource limits or the process-supervision policy.
"""
from functools import lru_cache
from importlib import metadata
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import sysconfig
import tempfile

from optimization_framework.execution.attempt_volume import SCRATCH_BYTES


class IsolationUnavailable(ValueError):
    pass


def _program(name):
    path = shutil.which(name)
    if not path:
        raise IsolationUnavailable(f"Imported source execution requires {name}")
    return str(Path(path).resolve())


def _dependencies(manifest):
    """Expose only declared installed distributions; never process their .pth files."""
    mounts, sites = set(), set()
    for name, version in manifest["runtime"]["packages"].items():
        if version is None:
            # Older captures record absent optional/platform dependencies too.
            continue
        try:
            distribution = metadata.distribution(name)
        except metadata.PackageNotFoundError as exc:
            raise IsolationUnavailable(f"Captured dependency {name}=={version} is unavailable") from exc
        if distribution.version != version or distribution.files is None:
            raise IsolationUnavailable(f"Captured dependency {name}=={version} has no matching installed files")
        site = Path(distribution.locate_file("")).resolve()
        sites.add(site)
        for member in distribution.files:
            relative = Path(str(member))
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                continue
            # Mount namespace packages and their metadata at the installed
            # location. Do not mount the surrounding repository or user home.
            root = site / relative.parts[0]
            if root.exists() and root.suffix != ".pth":
                if root.is_symlink() or not root.resolve().is_relative_to(site):
                    raise IsolationUnavailable(f"Captured dependency {name} has an external installation binding")
                mounts.add(root)
    return sorted(mounts), sorted(sites)


def _elf(path):
    with Path(path).open("rb") as stream:
        header = stream.read(20)
    return header if header.startswith(b"\x7fELF") else None


@lru_cache(maxsize=8)
def _native_libraries(binaries, loader_cache):
    """Read ELF metadata; do not run any wheel or captured executable's loader."""
    readelf, ldconfig = _program("readelf"), _program("ldconfig")
    system = subprocess.run([ldconfig, "-p"], capture_output=True, text=True, check=True, timeout=10)
    interpreter = _elf(Path(sys.executable).resolve())
    available = {}
    for name, path in re.findall(r"^\s*(\S+)\s+\([^\n]*\)\s+=>\s+(\S+)", system.stdout, re.M):
        header = _elf(path)
        if header and header[:6] == interpreter[:6] and header[18:20] == interpreter[18:20]:
            available.setdefault(name, path)
    paths = [Path(item[0]) for item in binaries]
    bundled = {path.name for path in paths}
    # The Python distribution may carry libpython beside its executable.
    for path in Path(sys.executable).resolve().parent.parent.glob("lib/libpython*.so*"):
        available[path.name] = str(path)
    found, seen = set(), set()
    while paths:
        binary = paths.pop()
        if binary in seen:
            continue
        seen.add(binary)
        inspected = subprocess.run([readelf, "-l", "-d", str(binary)], capture_output=True, text=True, timeout=10)
        if inspected.returncode:
            raise IsolationUnavailable(f"Cannot inspect native runtime dependency {binary.name}")
        for loader in re.findall(r"Requesting program interpreter:\s*([^\]]+)", inspected.stdout):
            if not Path(loader).is_file():
                raise IsolationUnavailable(f"Native interpreter is unavailable: {loader}")
            found.add(loader)
        for name in re.findall(r"\(NEEDED\).*?\[([^\]]+)\]", inspected.stdout):
            if name in bundled:
                continue
            path = available.get(name)
            if not path:
                # Distributions can contain optional GPU/transport binaries
                # that are not loaded by the captured CPU operation. Do not
                # mount a substitute. If used, the loader reports the missing
                # dependency from inside the isolated process.
                if binary in {Path(sys.executable).resolve(), Path(_program("bwrap"))}:
                    raise IsolationUnavailable(f"Native runtime dependency is unavailable: {name}")
                continue
            found.add(path)
            paths.append(Path(path))
    return sorted(found)


def command(directory, manifest, writable, arguments, *, memory_bytes=4 * 1024**3,
            file_bytes=64 * 1024**2, runtime_directories=(), compiler_io=None, worker=False, package_host=None,
            attempt_volume=None):
    """Construct the fixed host policy; imported data cannot add arbitrary mounts."""
    if sys.platform != "linux":
        raise IsolationUnavailable("Imported source execution requires Linux process isolation")
    bwrap = _program("bwrap")
    directory, writable = Path(directory).resolve(), Path(writable).resolve()
    if worker and compiler_io is not None:
        raise ValueError("Compiler and numerical worker isolation are separate modes")
    if package_host is not None and not worker:
        raise ValueError("Only numerical workers can use a declared package host")
    if worker != (attempt_volume is not None):
        raise ValueError("Numerical workers require a host-owned bounded private filesystem")
    if directory == writable or directory.is_relative_to(writable):
        raise ValueError("The writable execution directory must not contain its source capture")
    code = directory / "code"
    if any(path.is_symlink() or path.suffix == ".pyc" for path in code.rglob("*")):
        raise ValueError("Imported source must not contain symlinks or unrecorded Python bytecode")
    mounts, sites = _dependencies(manifest)
    executable = Path(sys.executable).resolve()
    stdlib = Path(sysconfig.get_path("stdlib")).resolve()
    native = {executable, Path(bwrap), *list((stdlib / "lib-dynload").glob("*.so"))}
    for root in mounts:
        if root.is_dir():
            native.update(path for path in root.rglob("*.so*") if path.is_file() and _elf(path))
        elif ".so" in root.name and _elf(root):
            native.add(root)
    signature = tuple((str(path), path.stat().st_size, path.stat().st_mtime_ns) for path in sorted(native))
    cache = Path("/etc/ld.so.cache")
    libraries = _native_libraries(signature, cache.stat().st_mtime_ns if cache.exists() else None)
    result = [bwrap, "--unshare-all", "--die-with-parent", "--new-session", "--cap-drop", "ALL", "--clearenv",
        "--proc", "/proc", "--remount-ro", "/proc", "--dev", "/dev",
        "--size", str(SCRATCH_BYTES), "--tmpfs", "/dev/shm", "--remount-ro", "/dev",
        "--size", str(SCRATCH_BYTES), "--tmpfs", "/tmp",
        "--ro-bind", str(executable), str(executable), "--ro-bind", str(stdlib), str(stdlib)]
    for name in ("site-packages", "dist-packages"):
        if (stdlib / name).exists():
            result += ["--tmpfs", str(stdlib / name), "--remount-ro", str(stdlib / name)]
    for path in [*libraries, bwrap, *mounts]:
        result += ["--ro-bind", str(path), str(path)]
    if cache.exists():
        result += ["--ro-bind", str(cache), str(cache)]
    for path in runtime_directories:
        if isinstance(path, tuple):
            source, target = Path(path[0]).resolve(), Path(path[1])
            protected = [directory, writable, Path("/host-tools"), Path("/proc"), Path("/dev"), Path("/sys")]
            if (not target.is_absolute() or ".." in target.parts or str(target) in {"/", "/tmp", "/run"}
                    or target.is_relative_to("/run")
                    or any(target.is_relative_to(root) or root.is_relative_to(target) for root in protected)):
                raise IsolationUnavailable("Legacy runtime alias conflicts with the execution isolation boundary")
            result += ["--ro-bind", str(source), str(target)]
        else:
            result += ["--ro-bind", str(Path(path).resolve()), str(Path(path).resolve())]
    if package_host is not None:
        result += ["--ro-bind", str(package_host.socket_path), "/run/optimization-package.sock",
                   "--ro-bind", str(package_host.client_path), "/host-tools/bwrap"]
    if worker:
        # The kernel bounds all private bytes, including unreferenced junk.
        # The installed prelude sends its root descriptor to the publisher.
        result += ["--size", str(attempt_volume.max_bytes), "--tmpfs", str(directory),
                   "--ro-bind", str(attempt_volume.socket_path), "/run/optimization-attempt.sock",
                   "--ro-bind", str(attempt_volume.bootstrap_path), "/host-tools/attempt-bootstrap.py"]
    for path in (code, directory / "runtime-locks", directory / "execution-manifest.json"):
        result += ["--ro-bind", str(path), str(path)]
    if worker:
        for name in ("spec.json", "inputs", "implementation", "evaluator"):
            path = directory / name
            if path.exists():
                result += ["--ro-bind", str(path), str(path)]
    if compiler_io is not None:
        request, output = map(lambda path: Path(path).resolve(), compiler_io)
        if request.parent != writable or output.parent != writable or request == output:
            raise ValueError("Compiler request and output must belong to its temporary work directory")
        result += ["--size", str(file_bytes), "--tmpfs", str(writable),
                   "--ro-bind", str(request), str(request), "--bind", str(output), "/compiler-result.json"]
    elif not worker:
        result += ["--bind", str(writable), str(writable)]
    result += ["--chdir", str(directory if worker else writable), "--remount-ro", "/"]
    environment = {"HOME": "/tmp", "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
        "PYTHONPATH": os.pathsep.join(map(str, [code, *sites])), "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1", "MPLBACKEND": "Agg",
        "LD_LIBRARY_PATH": ":".join(sorted({str(Path(path).parent) for path in libraries}))}
    if package_host is not None:
        environment["PATH"] = "/host-tools:/usr/bin:/bin"
    for key, value in environment.items():
        result += ["--setenv", key, value]
    # This launcher is installed host code passed as a literal, never taken
    # from the capture. -S prevents execution of ambient startup/.pth hooks.
    launcher = (
        "import resource,runpy,sys;"
        f"resource.setrlimit(resource.RLIMIT_AS,({memory_bytes},{memory_bytes}));"
        f"resource.setrlimit(resource.RLIMIT_FSIZE,({file_bytes},{file_bytes}));"
        "resource.setrlimit(resource.RLIMIT_NOFILE,(128,128));"
        "resource.setrlimit(resource.RLIMIT_NPROC,(128,128));"
        "resource.setrlimit(resource.RLIMIT_CORE,(0,0));"
    )
    if worker:
        launcher += "runpy.run_path('/host-tools/attempt-bootstrap.py',run_name='__main__')"
        arguments = [str(directory), json.dumps(list(map(str, [code, *sites]))), *arguments]
    else:
        launcher += (f"sys.path[:0]={list(map(str, [code, *sites]))!r};"
                     "module=sys.argv.pop(1);runpy.run_module(module,run_name='__main__',alter_sys=True)")
    if compiler_io is not None:
        # The imported module writes into bounded tmpfs. Only this one result
        # file is exported; it cannot create an unbounded host scratch tree.
        launcher += f";import shutil;shutil.copyfile({str(output)!r},'/compiler-result.json')"
    return [*result, "--", str(executable), "-I", "-S", "-B", "-u", "-c", launcher, *arguments]


def run(directory, manifest, writable, arguments, *, timeout=30, **limits):
    """Bound output and terminate the whole namespace on timeout, including forks."""
    invocation = command(directory, manifest, writable, arguments, **limits)
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(invocation, stdout=output, stderr=subprocess.STDOUT,
            start_new_session=True, env={"PATH": "/usr/bin:/bin"})
        try:
            process.wait(timeout=timeout)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise
        output.seek(0)
        detail = output.read(8192).decode("utf-8", errors="replace")
    return subprocess.CompletedProcess(invocation, process.returncode, detail)
