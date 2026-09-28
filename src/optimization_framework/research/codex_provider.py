"""Bounded, non-interactive Codex calls using saved ChatGPT authentication.

This adapter never reads an API key, logs in, or falls back to an API provider.
It is invoked only after the application has explicitly enabled the provider.
The caller must validate the returned JSON against its domain model. Arbitrary
JSON fields are carried inside a strict string envelope because Codex's strict
output schema cannot faithfully express open-ended algorithm configuration.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
from typing import Callable


class CodexProviderError(ValueError):
    """Safe-to-display failure; never contains raw provider output or prompts."""

    def __init__(self, message: str, *, code: str, usage_unknown: bool = False):
        super().__init__(message)
        self.code = code
        self.usage_unknown = usage_unknown


_ENVELOPE = {
    "type": "object",
    "properties": {"result_json": {"type": "string"}},
    "required": ["result_json"],
    "additionalProperties": False,
}

# A small allowlist avoids inheriting API credentials, endpoint overrides,
# plugins, injected Python/Node code, proxies, and unrelated project secrets.
# HOME/CODEX_HOME identify the CLI's own saved login; we do not open auth files.
_ENV_KEYS = {
    "PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE",
    "CODEX_HOME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
    "SSL_CERT_FILE", "SSL_CERT_DIR",
}
_DISABLED_FEATURES = (
    "shell_tool", "unified_exec", "shell_snapshot", "apps", "plugins",
    "remote_plugin", "hooks", "multi_agent", "multi_agent_v2", "browser_use",
    "browser_use_external", "computer_use", "image_generation", "view_image",
    "code_mode", "code_mode_host", "skill_search", "memories",
    "workspace_dependencies", "skill_mcp_dependency_install",
    "unbounded_connection_retries",
)


def _environment() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key in _ENV_KEYS}


def _stop(process: subprocess.Popen) -> None:
    """Terminate the whole CLI process group, including surviving descendants."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass
    # The leader may exit while a descendant ignores SIGTERM.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=2)


def _capture(
    command: list[str], *, directory: Path, environment: dict[str, str],
    timeout: float, input_bytes: bytes = b"", output_limit: int,
    on_progress: Callable[[dict], None] | None, stage: str,
    output_file: Path | None = None,
) -> tuple[int, bytes, bytes]:
    """Drain both pipes without unbounded communicate buffers or pipe deadlocks."""
    started = time.monotonic()
    with tempfile.TemporaryFile() as prompt:
        prompt.write(input_bytes)
        prompt.seek(0)
        try:
            process = subprocess.Popen(
                command, cwd=directory, env=environment, stdin=prompt,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError:
            raise CodexProviderError(
                "Codex could not be started. Check the configured CLI executable.",
                code="unavailable",
            ) from None
        chunks: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
        total = 0
        next_progress = started
        selector = selectors.DefaultSelector()
        assert process.stdout is not None and process.stderr is not None
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        try:
            while selector.get_map() or process.poll() is None:
                elapsed = time.monotonic() - started
                if elapsed >= timeout:
                    raise CodexProviderError(
                        f"Codex {stage} exceeded its {timeout:.1f}-second time limit; no fallback was used.",
                        code="timeout", usage_unknown=stage == "inference",
                    )
                if on_progress is not None and time.monotonic() >= next_progress:
                    # Deliberately exclude streamed text, paths, commands, and stderr.
                    on_progress({"stage": stage, "elapsed_seconds": elapsed})
                    next_progress = time.monotonic() + .25
                if output_file is not None and output_file.exists() and output_file.stat().st_size > output_limit:
                    raise CodexProviderError(
                        "Codex response exceeded the configured output limit.",
                        code="output_limit", usage_unknown=True,
                    )
                for key, _ in selector.select(timeout=min(.1, timeout - elapsed)):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(chunk)
                    if total > output_limit:
                        raise CodexProviderError(
                            "Codex response exceeded the configured output limit.",
                            code="output_limit", usage_unknown=stage == "inference",
                        )
                    chunks[key.data].extend(chunk)
            return process.wait(), bytes(chunks["stdout"]), bytes(chunks["stderr"])
        finally:
            selector.close()
            _stop(process)
            process.stdout.close()
            process.stderr.close()


def _failure(raw: str) -> CodexProviderError:
    message = raw.lower()
    if any(text in message for text in ("usage limit", "quota", "rate limit", "rate_limit", "429")):
        return CodexProviderError(
            "Codex usage is temporarily unavailable or exhausted. Start a new discussion after your allowance is available; no paid fallback was used.",
            code="quota_exhausted", usage_unknown=True,
        )
    if any(text in message for text in ("not logged in", "unauthorized", "authentication", "401")):
        return CodexProviderError(
            "Codex authentication needs attention. Sign in with ChatGPT outside the application.",
            code="login_required", usage_unknown=True,
        )
    return CodexProviderError(
        "Codex did not complete the request. Check CLI compatibility, model access, and subscription availability; no fallback was used.",
        code="provider_failed", usage_unknown=True,
    )


def run_codex(
    system: str, content: str, schema: dict, config: dict,
    on_progress: Callable[[dict], None] | None = None,
) -> dict:
    """Run one tool-free research turn, propagating callback cancellation.

    Configuration: ``model`` (default gpt-6-sol), ``binary`` (codex),
    ``timeout_seconds`` (120), ``reasoning_effort`` (low), and optional
    ``max_output_bytes`` (2 MiB). ``max_output_tokens`` is only a prompt hint:
    the CLI does not expose a hard output-token cap. This function enforces
    byte and elapsed-time caps instead. It reports token usage when present;
    those tokens are not assigned an API dollar price.
    """
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise CodexProviderError("Codex requires an object response schema.", code="invalid_config")
    try:
        timeout = float(config.get("timeout_seconds", 120))
        output_limit = int(config.get("max_output_bytes", 2 * 1024 * 1024))
    except (TypeError, ValueError, OverflowError):
        raise CodexProviderError("Invalid Codex runtime or output limit.", code="invalid_config") from None
    if not math.isfinite(timeout) or not 0 < timeout <= 3600 or not 1024 <= output_limit <= 16 * 1024 * 1024:
        raise CodexProviderError("Invalid Codex runtime or output limit.", code="invalid_config")
    effort = config.get("reasoning_effort", "low")
    if effort not in {"low", "medium", "high", "xhigh", "max", "ultra"}:
        raise CodexProviderError("Unsupported Codex reasoning effort.", code="invalid_config")
    model = str(config.get("model", "gpt-6-sol"))
    if not model or len(model) > 128 or any(character.isspace() for character in model):
        raise CodexProviderError("Invalid Codex model identifier.", code="invalid_config")
    binary = shutil.which(str(config.get("binary", "codex")))
    if binary is None:
        raise CodexProviderError("Codex CLI is not installed or its executable is unavailable.", code="unavailable")
    environment = _environment()
    started = time.monotonic()
    # A fresh directory excludes repository instructions and hidden test files.
    # Tools additionally receive only minimal runtime + workspace read access.
    with tempfile.TemporaryDirectory(prefix="grating-codex-") as temporary:
        directory = Path(temporary)
        code, stdout, stderr = _capture(
            [binary, "login", "status"], directory=directory, environment=environment,
            timeout=min(timeout, 10), output_limit=65536,
            on_progress=on_progress, stage="authentication",
        )
        status = (stdout + b"\n" + stderr).decode("utf-8", errors="replace")
        if code != 0 or not any(line.strip() == "Logged in using ChatGPT" for line in status.splitlines()):
            # Check before forced_login_method: incompatible login methods can be
            # signed out by Codex when that setting is applied during execution.
            raise CodexProviderError(
                "Codex needs a saved ChatGPT login. Configure it outside the application; API-key authentication is not accepted.",
                code="subscription_required",
            )
        schema_file = directory / "output-schema.json"
        schema_file.write_text(json.dumps(_ENVELOPE), encoding="utf-8")
        instructions = directory / "instructions.md"
        instructions.write_text(
            "You are a research reasoning component. Use only the supplied context. "
            "Do not directly use Codex CLI tools, access files, browse, execute commands, or spawn agents. "
            "You MAY request the application's declared tools and specialist assignments by returning "
            "their typed descriptions in the requested JSON fields (for example tools and proposed_tasks). "
            "The surrounding application, not the Codex CLI, executes those requests after authority checks "
            "and supplies the resulting evidence in a later turn. Do not claim a requested operation "
            "has completed until its actual result is supplied. "
            "Return the requested research JSON as a JSON-encoded string in result_json.\n\n"
            + system + "\n\nThe decoded result_json must match this schema:\n"
            + json.dumps(schema, ensure_ascii=False), encoding="utf-8",
        )
        result_file = directory / "response.json"
        overrides = {
            "model_provider": "openai",
            # Let Codex select its ChatGPT subscription endpoint. An explicit
            # API base URL sends subscription credentials to the API-key route.
            "chatgpt_base_url": "https://chatgpt.com/backend-api",
            "forced_login_method": "chatgpt",
            "model_reasoning_effort": effort,
            "approval_policy": "never",
            "default_permissions": "grating_research",
            "permissions.grating_research.filesystem": {":minimal": "read", ":workspace_roots": "read"},
            "permissions.grating_research.network.enabled": False,
            "web_search": "disabled",
            "mcp_servers": {},
            "notify": [],
            "project_doc_max_bytes": 0,
            # The CLI requires a nonzero budget; one token cannot fit a skill.
            "skills.max_context_tokens": 1,
            "features.skip_host_skill_discovery": True,
            "model_instructions_file": str(instructions),
            "analytics.enabled": False,
        }
        # Config values use JSON-compatible TOML scalars; inline maps need '='.
        command = [binary, "exec", "--ignore-user-config", "--ignore-rules", "--strict-config",
                   "--ephemeral", "--skip-git-repo-check", "--json", "--color", "never",
                   "--model", model, "--cd", str(directory),
                   "--output-schema", str(schema_file), "--output-last-message", str(result_file)]
        for key, value in overrides.items():
            if isinstance(value, dict):
                encoded = "{" + ", ".join(f"{json.dumps(k)} = {json.dumps(v)}" for k, v in value.items()) + "}"
            else:
                encoded = json.dumps(value)
            command.extend(["-c", key + "=" + encoded])
        for feature in _DISABLED_FEATURES:
            command.extend(["--disable", feature])
        command.append("-")
        prompt = content
        if config.get("max_output_tokens"):
            prompt += "\nKeep the final response concise; target at most " + str(config["max_output_tokens"]) + " output tokens."
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise CodexProviderError(f"Codex authentication exceeded the {timeout:.1f}-second call time limit.", code="timeout")
        code, stdout, stderr = _capture(
            command, directory=directory, environment=environment, timeout=remaining,
            input_bytes=prompt.encode("utf-8"), output_limit=output_limit,
            on_progress=on_progress, stage="inference", output_file=result_file,
        )
        events = []
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(event, dict):
                events.append(event)
        failed = [event for event in events if event.get("type") in {"error", "turn.failed"}]
        if code != 0 or failed:
            raise _failure(stderr.decode("utf-8", errors="replace") + json.dumps(failed))
        if any(event.get("item", {}).get("type") in {
            "command_execution", "mcp_tool_call", "web_search", "file_change", "collab_tool_call",
        } for event in events if isinstance(event.get("item"), dict)):
            raise CodexProviderError("Codex attempted a disabled research tool.", code="tool_access_blocked", usage_unknown=True)
        usage: dict[str, int] = {}
        for event in events:
            if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
                for key in ("input_tokens", "output_tokens", "cached_input_tokens"):
                    value = event["usage"].get(key)
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                        usage[key] = value
        if not result_file.exists() or result_file.stat().st_size > output_limit:
            raise CodexProviderError("Codex did not return a bounded structured response.", code="invalid_output", usage_unknown=True)
        try:
            envelope = json.loads(result_file.read_text(encoding="utf-8"))
            if not isinstance(envelope, dict) or set(envelope) != {"result_json"} or not isinstance(envelope["result_json"], str):
                raise ValueError
            text = envelope["result_json"]
            if not isinstance(json.loads(text), dict):
                raise ValueError
        except (ValueError, OSError, UnicodeError):
            raise CodexProviderError("Codex returned an invalid structured response.", code="invalid_output", usage_unknown=True) from None
        return {"text": text, "usage": usage, "elapsed_seconds": time.monotonic() - started,
                "billing_mode": "subscription"}
