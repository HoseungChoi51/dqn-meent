#!/usr/bin/env bash
set -euo pipefail
mkdir -p "$HOME/.pi/agent" "$HOME/.local/share/code-server/User" /work/sessions
# Refresh the bootstrap credential copy only when explicitly supplied by the host.
# Session refreshes subsequently belong to this workspace and survive reconnects.
if [ -f /bridge/in/auth.json ] && [ ! -f "$HOME/.pi/agent/auth.json" ]; then
    cp /bridge/in/auth.json "$HOME/.pi/agent/auth.json"
    chmod 600 "$HOME/.pi/agent/auth.json"
fi
export NODE_PATH=/usr/local/lib/node_modules/@earendil-works/pi-coding-agent/node_modules
export GRATING_DEVELOPMENT_BRIDGE=/bridge
if ! tmux has-session -t implementation 2>/dev/null; then
    tmux new-session -d -s implementation -c /work/repo \
        'exec pi --provider openai-codex --model gpt-6-sol --thinking xhigh --session /work/sessions/pi.jsonl --extension /opt/workspace/extension.ts'
fi
tmux set-option -g history-limit 100000
exec /opt/code-server/bin/code-server --bind-addr 0.0.0.0:8080 --auth none \
    --disable-telemetry --disable-update-check --disable-workspace-trust /work/repo
