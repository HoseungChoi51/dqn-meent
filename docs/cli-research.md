# Recorded CLI research

The numerical CLI uses the same commands, reservations, execution attempts and
evidence ledger as the web workspace. Install the branch with `uv sync --frozen --all-extras`
before using the console entry points below. This includes the optional MEENT and
PyTorch dependencies needed for these numerical examples. A core-only installation
can use `uv sync --frozen` for framework commands and the continuous application.

## Local execution

```bash
uv run --no-sync dqn-meent baseline --config configs/smoke.json \
  --method random --budget 12 --seed 7 --wall-seconds 30 \
  --output runs/cli-baseline
```

Without a connection option, the command creates a durable workspace under
`runs/cli-baseline/.workspace` and a manual campaign. Use
`--workspace runs/my-workspace` to share an existing local workspace. One process
holds its scheduler lease; a second local owner receives an ownership error.

The output directory contains `cli-run.json`, the command journal, a verified
`evidence.zip`, `workspace-trial.json`, and familiar numerical exports such as
`summary.json`, `metrics.csv`, `best_design.npy` and the applicable checkpoint.
These files are projections of the recorded experiment.

## Connected execution

```bash
uv run --no-sync dqn-meent train --config configs/smoke.json --steps 256 \
  --wall-seconds 120 --output runs/connected-training \
  --workspace-url http://127.0.0.1:8791
```

Connected mode leaves scheduling to that server and retains requests/receipts
locally. Add `--campaign CAMPAIGN_ID --task TASK_ID` to use existing matching
campaign tasks and their allocations. Otherwise the CLI creates a manual campaign.
Use the matching server address, including a configured private Tailnet URL when
appropriate. The journal pins workspace identity, so changing the server at the
same address cannot silently replay an older request into another workspace.

## Interruption, recovery and evaluation

Ctrl-C requests a recorded pause, waits for the worker and exports available
evidence. Resume DQN training with its original configuration and checkpoint:

```bash
uv run --no-sync dqn-meent train --config configs/smoke.json --steps 256 \
  --wall-seconds 120 --resume runs/connected-training/checkpoint.pt \
  --output runs/resumed-training --workspace-url http://127.0.0.1:8791

uv run --no-sync dqn-meent evaluate --run runs/resumed-training \
  --orders 5 7 --wall-seconds 120 \
  --workspace-url http://127.0.0.1:8791
```

Resume retains the original admission and procedure even after an unrelated
charter edit or a change of export directory. A changed numerical configuration
requires a new experiment. Evaluating an exported policy creates a separate
inference job and recorded convergence jobs with their own allocations; use
`--skip-policy` for only the saved solution's check. An old standalone run first
enters through a hashed retrospective import. Unmeasured historical cost and
exposure remain unknown; unsupported pickle-only policies remain archived evidence.

## Other application operations

```bash
uv run --no-sync optimization-research --workspace runs/my-workspace state
uv run --no-sync optimization-research --workspace-url http://127.0.0.1:8791 \
  --journal runs/command-journal command request.json
```

`request.json` contains a v1 command envelope: a stable `id`, `campaign_id`,
`operation`, `expected_revision` and typed `payload`. Campaign creation uses
revision zero. The [command schema catalog](../src/optimization_framework/campaigns/commands.py)
exposes the payloads through `CommandService.describe()`; the
[typed contracts](../src/optimization_framework/contracts/commands.py) define their fields.
The command journal is written before admission; retrying the same saved request
reconciles its accepted receipt before attempting new work.

Asynchronous operations wait for recorded delivery or report an actionable
interruption. Connected mode also supports `command request.json --no-wait`.
Inspect current delivery with `GET /api/v1/commands/{id}/delivery`; the original
admission receipt stays immutable. Implementation commissioning uses the separate
implementation service in both modes.

Operational evidence is linked from [current development status](current-development-status.md).
