"""Imported source cannot acquire the permissions of an installed framework."""
import json
from pathlib import Path
import shutil
import socket

import pytest

from optimization_framework.contracts.requests import TrialInput
from optimization_framework.evaluation.registry import problems
from optimization_framework.execution import isolation, provenance
from optimization_framework.assets import captures
from optimization_framework.storage import history
from test_framework_bundles import export, inspect, publish
from test_framework_provenance import campaign, finish


def capture(directory):
    directory.mkdir()
    return provenance.capture(directory, problem_ids=["bounded_continuous"])


def test_isolated_captured_preparation_uses_original_source(tmp_path, monkeypatch):
    directory = tmp_path / "imported"
    manifest = capture(directory)
    from optimization_framework.execution.preparation import prepare
    request = TrialInput(campaign_id="destination", task_id="local_problem", algorithm="coordinate", max_steps=4)
    problem = problems.resolve("bounded_continuous", {}, {})
    expected = prepare(request, problem, [])
    def changed(*args, **kwargs):
        pytest.fail("Installed preparation must not replace an imported capture")
    monkeypatch.setattr("optimization_framework.execution.preparation.prepare", changed)
    actual = provenance.invoke(directory, manifest, "trial.prepare", {
        "request": request.model_dump(mode="json"), "problem": problem.model_dump(mode="json"), "assets": []}, isolated=True)
    assert actual == json.loads(json.dumps(expected))


def test_imported_compiler_has_only_source_runtime_and_request_access(tmp_path, monkeypatch):
    directory = tmp_path / "capture"
    capture(directory)
    secret = tmp_path / "workspace-credentials"
    secret.write_text("private workspace contents")
    historical_result = directory / "result.json"
    historical_result.write_text('{"candidate": "an undeclared historical solution"}')
    monkeypatch.setenv("CAPTURE_TEST_SECRET", "private inherited environment")
    source = directory / "code/optimization_framework/execution/frozen_host.py"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        source.write_text('''import argparse, json, os, socket
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--directory');p.add_argument('--request');p.add_argument('--output')
a=p.parse_args()
values={'secret_file':Path(__SECRET_PATH__).exists(), 'historical_result':(Path(a.directory)/'result.json').exists(),
        'environment':os.environ.get('CAPTURE_TEST_SECRET')}
try:
    Path(__file__).write_text('replace captured code')
    values['source_writable']=True
except OSError:
    values['source_writable']=False
try:
    connection=socket.create_connection(('127.0.0.1', PORT), timeout=.2)
    values['host_network']=True
    connection.close()
except OSError:
    values['host_network']=False
Path(a.output).write_text(json.dumps({'result':values}))
'''.replace("__SECRET_PATH__", repr(str(secret))).replace("PORT", str(port)))
        manifest = provenance.capture(directory, problem_ids=["bounded_continuous"])
        actual = provenance.invoke(directory, manifest, "trial.prepare", {}, isolated=True)
    assert actual == {"secret_file": False, "historical_result": False, "environment": None,
                      "source_writable": False, "host_network": False}
    assert secret.read_text() == "private workspace contents"
    provenance.verify(directory, manifest)


def test_imported_compiler_timeout_and_missing_isolation_never_fall_back(tmp_path, monkeypatch):
    directory = tmp_path / "capture"
    capture(directory)
    source = directory / "code/optimization_framework/execution/frozen_host.py"
    source.write_text("import time\ntime.sleep(60)\n")
    manifest = provenance.capture(directory, problem_ids=["bounded_continuous"])
    with pytest.raises(ValueError, match="time allowance"):
        provenance.invoke(directory, manifest, "trial.prepare", {}, isolated=True, timeout=.3)
    original = isolation.shutil.which
    monkeypatch.setattr(isolation.shutil, "which", lambda name: None if name == "bwrap" else original(name))
    with pytest.raises(isolation.IsolationUnavailable, match="bwrap"):
        provenance.invoke(directory, manifest, "trial.prepare", {}, isolated=True)


def test_imported_unrecorded_bytecode_is_rejected_before_execution(tmp_path):
    directory = tmp_path / "capture"
    manifest = capture(directory)
    (directory / "code/unrecorded.pyc").write_bytes(b"unrecorded Python input")
    with pytest.raises(ValueError, match="unrecorded Python bytecode"):
        provenance.invoke(directory, manifest, "trial.prepare", {}, isolated=True)


def test_captured_modules_cannot_shadow_the_resource_limit_bootstrap(tmp_path):
    directory = tmp_path / 'capture'
    capture(directory)
    (directory / 'code/resource.py').write_text("raise AssertionError('untrusted resource module ran before limits')\n")
    source = directory / 'code/optimization_framework/execution/frozen_host.py'
    source.write_text('''import argparse,json,resource
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('--directory');p.add_argument('--request');p.add_argument('--output')
a=p.parse_args()
Path(a.output).write_text(json.dumps({'result':{'limit':resource.getrlimit(resource.RLIMIT_AS)[1]}}))
''')
    manifest = provenance.capture(directory, problem_ids=['bounded_continuous'])
    assert provenance.invoke(directory, manifest, 'trial.prepare', {}, isolated=True) == {'limit': 4 * 1024**3}


def test_exact_imported_capture_resolves_without_original_or_staging_paths(tmp_path):
    source, owner, task = campaign(tmp_path / "source")
    trial = source.create_trial(TrialInput(campaign_id=owner, task_id=task,
        algorithm="coordinate", max_steps=4, wall_seconds=10))
    finish(source, trial)
    trial = source.store.get(trial["id"], "trial")
    _, archive = export(source, owner, trial["output_asset_ids"])
    portable = tmp_path / "evidence.zip"
    shutil.copyfile(archive, portable)
    source.directory.rename(tmp_path / "original-unavailable")
    destination, current, _ = campaign(tmp_path / "destination")
    inspection = inspect(destination, current, portable)
    publish(destination, current, inspection)
    record = history.find(destination.store, trial["id"], "trial")[0]
    costs = destination.store.list("cost_event")
    shutil.rmtree(destination.directory / "bundles/staged")
    directory, manifest, original = captures.experiment(destination, record["reference"])
    assert original == trial and manifest == trial["execution_manifest"]
    assert not (directory / "result.json").exists()
    assert not (directory / "spec.json").exists()
    assert not list(directory.glob("*.jsonl"))
    result = provenance.invoke(directory, manifest, "trial.prepare", {
        "request": {key: value for key, value in trial.items() if key in TrialInput.model_fields},
        "problem": trial["problem"], "assets": trial["declared_assets"]}, isolated=True)
    assert result["completion"] == trial["completion"]
    assert captures.experiment(destination, record["reference"])[0] == directory
    assert not destination.store.list("trial")
    assert destination.store.list("cost_event") == costs
    assert destination.assets.actual_costs(current)["event_count"] == 0
    assert history.exact(destination.store, record["reference"]).data == trial
    changed = {**record["reference"], "content_digest": "0" * 64}
    with pytest.raises(ValueError, match="has not been imported"):
        captures.experiment(destination, changed)


def test_missing_or_changed_imported_source_does_not_select_another_revision(tmp_path):
    source, owner, task = campaign(tmp_path / "source")
    trial = source.create_trial(TrialInput(campaign_id=owner, task_id=task,
        algorithm="coordinate", max_steps=2, wall_seconds=10))
    finish(source, trial)
    trial = source.store.get(trial["id"], "trial")
    _, archive = export(source, owner, trial["output_asset_ids"])
    destination, current, _ = campaign(tmp_path / "destination")
    publish(destination, current, inspect(destination, current, archive))
    record = history.find(destination.store, trial["id"], "trial")[0]
    directory, manifest, _ = captures.experiment(destination, record["reference"])
    member = next(name for name in manifest["files"] if name.endswith(".py"))
    path = directory / "code" / member
    path.write_text("changed captured code")
    with pytest.raises(ValueError, match="Materialized captured source changed"):
        captures.experiment(destination, record["reference"])
    assert path.read_text() == "changed captured code"
    assert not destination.store.list("trial")
    assert history.exact(destination.store, record["reference"]).data == trial
