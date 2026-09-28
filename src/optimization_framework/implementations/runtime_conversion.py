"""Relocate supported path-dependent runtime identities without rewriting them."""
import json
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4

from optimization_framework.implementations.models import CapabilityUnavailable, digest
from optimization_framework.storage.sqlite import atomic_json


def report(original, resolved):
    """Verify every content measurement the legacy schema actually recorded."""
    if original.get("schema_version", 1) != 1 or resolved.get("schema_version") != 2:
        raise CapabilityUnavailable("Unsupported legacy runtime conversion")
    if digest({key: value for key, value in original.items() if key != "digest"}) != original.get("digest"):
        raise ValueError("Legacy runtime identity changed")
    for field in ("protocol", "python", "executable_hash", "dependencies", "files"):
        if original.get(field) != resolved.get(field):
            raise CapabilityUnavailable("Installed runtime does not match the legacy " + field + " measurement")
    if any(not isinstance(original.get(field), str) or not Path(original[field]).is_absolute()
           for field in ("executable", "stdlib")):
        raise ValueError("Legacy runtime has an invalid interpreter or standard-library path")
    libraries = {}
    for path, checksum in original.get("libraries", {}).items():
        name = Path(path).name
        if not Path(path).is_absolute() or (name in libraries and libraries[name] != checksum):
            raise ValueError("Legacy runtime has conflicting native-library identities")
        libraries[name] = checksum
    if libraries != resolved["libraries"]:
        raise CapabilityUnavailable("Installed native content does not match the legacy runtime libraries")
    value = {"schema_version": 1, "converter": "legacy_runtime_v1", "original_runtime_digest": original["digest"],
        "resolved_runtime_digest": resolved["digest"],
        "verified_fields": ["protocol", "python", "executable_hash", "dependencies", "files", "libraries"],
        "limitations": ["The legacy identity did not hash its standard library. The selected local standard library is now pinned; historical byte equivalence is unknown."]}
    return {"id": "runtime_conversion_" + digest(value), **value}


def resolve(directory, manifest):
    from optimization_framework.implementations.runtime import prepare_runtime, verify_runtime, PROTOCOL
    if manifest.get("protocol") not in {PROTOCOL, "package_evaluator_v1"}:
        raise CapabilityUnavailable("Unsupported legacy runtime protocol")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    kind = "evaluator" if manifest["protocol"] == "package_evaluator_v1" else "optimizer"
    modern_root, modern = prepare_runtime(directory, manifest["dependencies"], kind=kind, allow_download=False)
    conversion = report(manifest, modern)
    root = directory / conversion["id"]
    if root.exists():
        try:
            verify_runtime(root, manifest)
            return root
        except (ValueError, OSError):
            # Keep damage inspectable and avoid replacing a runtime another
            # attempt might still have mounted.
            root = directory / (conversion["id"] + "_" + uuid4().hex)
    with tempfile.TemporaryDirectory(prefix="converting-", dir=directory) as temporary:
        stage = Path(temporary)
        for name in ("site-packages", "stdlib"):
            shutil.copytree(modern_root / name, stage / name)
        shutil.copyfile(modern_root / "binding.json", stage / "binding.json")
        atomic_json(stage / "runtime.json", manifest)
        atomic_json(stage / "resolved-runtime.json", modern)
        atomic_json(stage / "conversion.json", conversion)
        verify_runtime(stage, manifest)
        try:
            stage.rename(root)
        except FileExistsError:
            verify_runtime(root, manifest)
    return root


def verify(root, manifest):
    from optimization_framework.implementations.runtime import verify_runtime
    root = Path(root)
    resolved = json.loads((root / "resolved-runtime.json").read_text())
    conversion = json.loads((root / "conversion.json").read_text())
    if conversion != report(manifest, resolved):
        raise ValueError("Legacy runtime conversion report changed")
    binding = verify_runtime(root, resolved)
    aliases = [(binding["executable"], manifest["executable"]), (binding["stdlib_source"], manifest["stdlib"])]
    for path, checksum in manifest["libraries"].items():
        aliases.append((binding["libraries"][Path(path).name][0], path))
    return {**binding, "conversion": conversion, "legacy_aliases": aliases}


def check_frozen(workspace, trial):
    from optimization_framework.implementations.runtime import bundle_runtime_root, verify_runtime
    if not trial.get("experiment_spec_id"):
        return
    frozen = workspace.store.get(trial["experiment_spec_id"], "experiment_spec")
    expected = frozen["schedule"].get("runtime_conversions", {})
    if trial.get("runtime_conversions", {}) != expected:
        raise ValueError("The frozen runtime conversion changed")
    actual = {}
    for prefix in ("implementation", "evaluator"):
        if trial.get(prefix + "_version_id"):
            directory = workspace.job_dir(trial["id"]) / prefix
            bundle = json.loads((directory / "bundle.json").read_text())
            resolved = verify_runtime(bundle_runtime_root(bundle, directory), bundle["artifact"]["runtime"])
            if resolved.get("conversion"):
                actual[prefix] = resolved["conversion"]
    if actual != expected:
        raise ValueError("The resolved legacy runtime changed after this experiment was frozen; create a new linked experiment")
