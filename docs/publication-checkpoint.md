# Public development checkpoint — 2026-09-28

Branch: `setup/latest`. This is the integrated experimental implementation, retaining the grating application and compatibility entry points while moving general functionality into `optimization_framework`.

## Product scope

The framework helps a user and LLM agents develop a suitable optimizer for a particular scientific optimization problem. Reusable optimizers are useful but optional. The software harness should be general and reusable; a universal optimizer is not a goal.

Implemented foundations include executable problem definitions, numerical workers, durable campaign-manager context, typed application commands, a separate implementation/validation service, literature capture and citation validation, bounded tuning assessments, and a browser view of ordinary agent responses and tool interactions.

## Actual scientific run

The development campaign uses a 64-cell binary silicon grating, wavelength 1100 nm, deflection angle 50°, thickness 325 nm, and exploratory Fourier order 15. GPT-6 Luna ran through the Codex provider with subscription authentication.

Eight real MEENT trials completed 96 evaluations each. Uniform random achieved best efficiencies 0.350746 and 0.272040 on seeds 0 and 1. Hill climbing with restart patience 8 achieved 0.484325 and 0.462355; patience 16 and 32 both achieved 0.768054 and 0.857233. This is a small exploratory screen, not general superiority or physical convergence. The numerical screen was explicitly operator-assisted after an automatic handoff failed.

The subsequent repair run saved an accepted literature map and four model-authored hypotheses:

- Bundled restart hill climbing with patience 16–32.
- Adaptive block tabu for coordinated bit changes.
- Bundled surrogate-guided candidate-pool search.
- Elite-local surrogate-guided bit-flip search requiring a new implementation.

Accepted here means the records passed schema and evidence checks. It does not mean the optimizer is scientifically validated. The source study uses an operator-supplied paper lead and actual retrieved passages; that assistance remains part of the provenance.

## Known failures at publication

- The independent critic and subsequent revision returned provider `invalid_output` errors. They did not produce accepted review/revision artifacts. The full cycle remains incomplete.
- Failed prerequisites can still allow dependent tasks to run; scientific stage admission needs stricter handling of missing required work products.
- Large contexts required repairs to remove duplicated source text, deployment manifests and archived candidate lists while retaining exact measurements and citation passages. The full records remain available through tools and logs. Broader long-campaign qualification remains open.
- Provider error diagnostics need to retain more actionable ordinary-output evidence when parsing fails.
- Autonomous implementation commissioning, complete empirical iteration and final migration/qualification remain unfinished.

The campaign is paused. The publication checkpoint does not claim to have resolved every issue reported in the live review.

## Repairs included

Assigned source passages now carry actual retrieval receipts across task boundaries. Valid scientific artifacts survive rejected agenda assignments. Future task references resolve to dependencies, and a later revision gets a different context from its earlier independent generators. Saved candidate batches project onto the Hypotheses board.

The UI distinguishes saved work products from rejected attempts. A request submitted while discovery is paused shows that state and a resume control. An older failed run is no longer presented as the result of a new queued request. Completed tasks resolve their obsolete provider-error issues while preserving the historical log.

## Verification and local artifacts

The latest focused Python regression run recorded **69 passed**. It covers discovery, evidence handoffs, assignment recovery, context projection, assessment authority/measurements, research commands, manager lifecycle, the provider adapter and literature. Two browser regression cases passed, including the Develop strategies → paused request → resume path. TypeScript and the production frontend build passed. The real browser displayed saved scientific outputs without JavaScript errors.

Earlier broader test reports are described in [development status](discovery-development-status.md); they do not replace verification of the later changes. A new full-suite run has not been completed for this public checkpoint.

`runs/`, local campaign databases, provider credentials, agent logs and build outputs are deliberately not committed. Links into `runs/` in development reports refer to local evidence, not downloadable repository files. Reproduction entry points are [run_grating_discovery.py](../scripts/run_grating_discovery.py) and [run_grating_empirical_screen.py](../scripts/run_grating_empirical_screen.py); the latter explicitly identifies its operator-assisted numerical screen.

See the [README](../README.md) for installation and the [discovery redesign plan](agentic-discovery-redesign-plan.md) for the intended complete workflow.
