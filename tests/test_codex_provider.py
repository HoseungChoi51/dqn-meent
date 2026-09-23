"""Transport contracts exercised with a local fake CLI, never a live model."""

import json
import os
from pathlib import Path
import sys
import textwrap
import time

import pytest

from dqn_meent.workspace.codex_provider import CodexProviderError, run_codex


SCHEMA = {"type": "object", "properties": {"analysis": {"type": "string"}}, "required": ["analysis"]}


def fake_codex(tmp_path, body="", *, auth="Logged in using ChatGPT", status=0):
    executable = tmp_path / "fake-codex"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys, time, subprocess\n"
        "args = sys.argv[1:]\n"
        "if args == ['login', 'status']:\n"
        f"    print({auth!r}, file=sys.stderr)\n"
        f"    sys.exit({status})\n"
        + textwrap.dedent(body or """
            result = pathlib.Path(args[args.index('--output-last-message') + 1])
            result.write_text(json.dumps({'result_json': json.dumps({'analysis': 'Measured evidence is required.'})}))
            print(json.dumps({'type': 'turn.completed', 'usage': {'input_tokens': 81, 'output_tokens': 20, 'cached_input_tokens': 64}}))
        """),
        encoding="utf-8",
    )
    executable.chmod(0o700)
    return {"binary": str(executable), "model": "gpt-6-sol", "timeout_seconds": 5}


def test_subscription_result_and_usage(tmp_path):
    result = run_codex("Research role", "Evaluate this hypothesis", SCHEMA, fake_codex(tmp_path))
    assert result["billing_mode"] == "subscription"
    assert result["usage"] == {"input_tokens": 81, "output_tokens": 20, "cached_input_tokens": 64}
    assert json.loads(result["text"])["analysis"] == "Measured evidence is required."
    assert result["elapsed_seconds"] > 0
    assert "cost_usd" not in result


def test_isolation_flags_environment_schema_and_prompt(tmp_path, monkeypatch):
    capture = tmp_path / "capture.json"
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "GRATING_LLM_API_KEY", "ANTHROPIC_API_KEY",
                "OPENAI_BASE_URL", "NODE_OPTIONS", "PYTHONPATH", "HTTPS_PROXY", "UNRELATED_SECRET"):
        monkeypatch.setenv(key, "must-not-leak")
    body = f"""
        options = {{}}
        for i, arg in enumerate(args[:-1]):
            if arg == '-c':
                key, value = args[i + 1].split('=', 1)
                options[key] = value
        schema = pathlib.Path(args[args.index('--output-schema') + 1])
        instructions = pathlib.Path(json.loads(options['model_instructions_file']))
        pathlib.Path({str(capture)!r}).write_text(json.dumps({{
            'args': args, 'env': dict(os.environ), 'cwd': os.getcwd(),
            'schema': json.loads(schema.read_text()), 'instructions': instructions.read_text(),
            'prompt': sys.stdin.read(), 'options': options,
        }}))
        result = pathlib.Path(args[args.index('--output-last-message') + 1])
        result.write_text(json.dumps({{'result_json': '{{"analysis":"ok"}}'}}))
    """
    result = run_codex("Role-specific instructions", "Visible development evidence", SCHEMA, fake_codex(tmp_path, body))
    observed = json.loads(capture.read_text())
    assert "must-not-leak" not in json.dumps(observed)
    assert observed["prompt"] == "Visible development evidence"
    assert "Role-specific instructions" in observed["instructions"]
    assert observed["schema"]["additionalProperties"] is False
    assert observed["options"]["forced_login_method"] == '"chatgpt"'
    assert observed["options"]["model_provider"] == '"openai"'
    assert observed["options"]["openai_base_url"] == '"https://api.openai.com/v1"'
    assert observed["options"]["web_search"] == '"disabled"'
    assert observed["options"]["default_permissions"] == '"grating_research"'
    assert observed["options"]["permissions.grating_research.network.enabled"] == "false"
    assert observed["options"]["permissions.grating_research.filesystem"] == '{":minimal" = "read", ":workspace_roots" = "read"}'
    assert observed["options"]["mcp_servers"] == "{}"
    assert observed["options"]["project_doc_max_bytes"] == "0"
    assert observed["options"]["skills.max_context_tokens"] == "1"
    assert not any(key.startswith("model_providers.openai.") for key in observed["options"])
    assert "--ignore-user-config" in observed["args"]
    assert "--ignore-rules" in observed["args"]
    assert "--ephemeral" in observed["args"]
    assert "--strict-config" in observed["args"]
    for feature in ("shell_tool", "unified_exec", "hooks", "plugins", "apps", "multi_agent", "browser_use"):
        index = observed["args"].index(feature)
        assert observed["args"][index - 1] == "--disable"
    assert Path(observed["cwd"]) != tmp_path
    assert not Path(observed["cwd"]).exists(), "Prompt, schema, and result artifacts must be removed"
    assert result["usage"] == {}, "Missing token usage must not be fabricated"


@pytest.mark.parametrize("auth,status", [("Logged in using an API key - sk-secret", 0), ("Not logged in", 1), ("Unexpected format", 0)])
def test_rejects_non_chatgpt_auth_before_inference(tmp_path, auth, status):
    marker = tmp_path / "inference-started"
    config = fake_codex(tmp_path, f"pathlib.Path({str(marker)!r}).touch()", auth=auth, status=status)
    with pytest.raises(CodexProviderError) as raised:
        run_codex("role", "context", SCHEMA, config)
    assert raised.value.code == "subscription_required"
    assert raised.value.usage_unknown is False
    assert "sk-secret" not in str(raised.value)
    assert not marker.exists()


def test_callback_cancels_and_reaps_child_process(tmp_path):
    marker = tmp_path / "pids.json"
    config = fake_codex(tmp_path, f"""
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'])
        pathlib.Path({str(marker)!r}).write_text(json.dumps([os.getpid(), child.pid]))
        time.sleep(20)
    """)

    class Cancelled(Exception):
        pass

    def cancel(progress):
        assert set(progress) == {"stage", "elapsed_seconds"}
        if progress["stage"] == "inference" and marker.exists():
            raise Cancelled("researcher stopped")

    with pytest.raises(Cancelled, match="researcher stopped"):
        run_codex("role", "context", SCHEMA, config, on_progress=cancel)
    for pid in json.loads(marker.read_text()):
        status = Path(f"/proc/{pid}/status")
        # An orphan may briefly remain a zombie until PID 1 reaps it.
        assert not status.exists() or "State:\tZ" in status.read_text()


def test_timeout_stops_cli_with_uncertain_usage(tmp_path):
    config = fake_codex(tmp_path, "time.sleep(20)")
    config["timeout_seconds"] = .4
    started = time.monotonic()
    with pytest.raises(CodexProviderError) as raised:
        run_codex("role", "context", SCHEMA, config)
    assert raised.value.code == "timeout"
    assert raised.value.usage_unknown is True
    assert time.monotonic() - started < 3


def test_output_overflow_is_bounded(tmp_path):
    config = fake_codex(tmp_path, "os.write(1, b'x' * 200000); time.sleep(20)")
    config["max_output_bytes"] = 2048
    with pytest.raises(CodexProviderError) as raised:
        run_codex("role", "context", SCHEMA, config)
    assert raised.value.code == "output_limit"


@pytest.mark.parametrize("response", ['not JSON', '{}', '{"result_json":"not JSON"}', '{"result_json":"[]"}', '{"result_json":"{}","extra":true}'])
def test_invalid_structured_result_rejected_without_echo(tmp_path, response):
    config = fake_codex(tmp_path, f"""
        pathlib.Path(args[args.index('--output-last-message') + 1]).write_text({response!r})
    """)
    with pytest.raises(CodexProviderError) as raised:
        run_codex("role", "context", SCHEMA, config)
    assert raised.value.code == "invalid_output"
    assert raised.value.usage_unknown is True


@pytest.mark.parametrize("failure,code", [("usage limit reached sk-secret", "quota_exhausted"), ("authentication invalid sk-secret", "login_required"), ("arbitrary private content", "provider_failed")])
def test_errors_are_redacted_and_do_not_fall_back(tmp_path, failure, code):
    config = fake_codex(tmp_path, f"""
        print(json.dumps({{'type':'turn.failed', 'error': {{'message': {failure!r}}}}}))
        print({failure!r}, file=sys.stderr)
        sys.exit(1)
    """)
    with pytest.raises(CodexProviderError) as raised:
        run_codex("role", "context", SCHEMA, config)
    assert raised.value.code == code
    assert failure not in str(raised.value)
    assert raised.value.usage_unknown is True


def test_unexpected_tool_activity_is_rejected(tmp_path):
    config = fake_codex(tmp_path, """
        print(json.dumps({'type':'item.completed','item':{'type':'command_execution','command':'private command'}}))
    """)
    with pytest.raises(CodexProviderError) as raised:
        run_codex("role", "context", SCHEMA, config)
    assert raised.value.code == "tool_access_blocked"
    assert "private command" not in str(raised.value)


def test_invalid_config_does_not_start_cli(tmp_path):
    config = fake_codex(tmp_path)
    with pytest.raises(CodexProviderError, match="object response schema"):
        run_codex("role", "context", {"type": "array"}, config)
    config["timeout_seconds"] = float("nan")
    with pytest.raises(CodexProviderError, match="runtime"):
        run_codex("role", "context", SCHEMA, config)


def test_missing_executable_has_no_usage(tmp_path):
    with pytest.raises(CodexProviderError) as raised:
        run_codex("role", "context", SCHEMA, {"binary": str(tmp_path / "absent")})
    assert raised.value.code == "unavailable"
    assert raised.value.usage_unknown is False
