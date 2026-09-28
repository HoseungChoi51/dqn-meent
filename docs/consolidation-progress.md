# Consolidation implementation record

Authoritative scope: [architecture consolidation plan](architecture-consolidation-plan.md).
Purpose: a reusable user–LLM research harness for the problem at hand. Optimizers
may be specialized. A universal optimizer is not a product goal.

This checklist tracks implementation and evidence, not a reduced replacement plan.
Existing dirty source, historical results, and researcher annotations are inputs.
The live workspace is not a development or test database.

Current checkpoint: [D1/D2 manager and CLI delivery](#research-cli-and-durable-manager-checkpoint--2026-09-28).
The [development status](current-development-status.md) and
[acceptance matrix](consolidation-acceptance-matrix.md) identify what is complete
and the remaining E/F gates. The original checklist below retains its historical
planning context; subsequent dated checkpoints supersede its implementation status.

| Increment | Required deliverables | Status / evidence |
|---|---|---|
| Baseline | Both dirty trees, annotations, online database backups, hashed historical blobs, independent scientific fixtures | Captured under `runs/consolidation/baseline-20260927`; original suites: 194 + 46 passed |
| 1: common foundation | Versioned contracts, repository/artifact boundaries, lazy registries, MEENT and constrained continuous instances, objective direction, common worker/API/UI path | Implemented initial vertical slice; 210 Python tests and 20 browser tests passed (including two real domains). Further API command and comparison integration tracked below |
| 2: execution | One scheduler and optimizer protocol, isolated packages, general correctness checks, periodic chunked checkpoints, attempts, leases, accounting and recovery | Common native/package worker, independent correctness fixtures, chunked checkpoints, leases, recovery, database attempt/cost ingestion and launch revocation checks implemented. A 20 MiB sandboxed package checkpoint restores exactly. Final integrated/live qualification remains |
| 3: numerical consolidation | Shared final DQN options, both HC semantics, refinement with upstream assets, policy/Q diagnostics, F320/F480, fields/sensitivity, RNG isolation | Final DQN/HC/refinement numerical reference checks pass; CLI train/baseline delegate to common worker. Periodic snapshots, affine diagnostic seeds and exact prefix costs have worker/browser evidence. All seven preserved references now exactly match the sibling at F40/F160/F320/F480 in fresh common-worker runs. Registered policy capabilities and complete diagnostic forms/qualification remain |
| 4: scientific lifecycle | Frozen studies/procedures, amendments, dependencies, validation/waivers, three independent confirmation protocols, reuse DAG and full upstream costs | Frozen studies, checks/waivers, nominations and immutable reports implemented. Reduced templates bind same-seed prefixes, coalesce roles and use shared grants with fixed cutoffs. The complete 60-cell production declaration, fixed reference import/bindings and versioned staged rules now have focused and browser evidence. Production diagnostics and final integrated qualification remain |
| 5: manager | General context/actions, delegated authority, durable idempotent commands/outbox, build/reuse/decline/findings, adapter commissioning, deduplicated issues | General context, delegated commands/outbox, implementation grants, explicit reference access and scoped issues implemented. Generated evaluator commissioning, remaining control translation and live-agent qualification remain |
| 6: product/migration | Domain-neutral UI and v1 API, readiness explanations, assets/lineage, idempotent historical imports, migration dry-run/apply/rollback, portable export | Common views, revisioned drafts/readiness/launch, template controls and declared-input selectors implemented. A recorded reference-manifest import preserves candidate/source identities and unknowns. Full v1 mutation migration, remaining forms, full historical imports, portable bundles and live cutover remain |
| 7: qualification | Two-domain Python/browser/live model workflows, implementation build, reuse/decline/waiver/recovery, exact source/runtime/evidence report | Pending |

## Release gates

- [x] Core startup does not import MEENT or torch; optional numerical dependencies.
- [x] Both domains run through the same service and execution lifecycle.
- [ ] Correctness, fidelity, diagnostics and confirmation have distinct evidence.
- [x] Failed/invalid evaluations are typed; original measurements remain authoritative.
- [x] Scientific completion is distinct from clean exit and allocation exhaustion.
- [x] Frozen study changes create linked studies; extensions preserve schedules.
- [x] Fresh-seed replication does not inherit unseen-instance exposure restrictions.
- [ ] Costs distinguish actual work, attributable upstream prefixes, and unknown costs.
- [x] Recovery preserves prior-attempt suffix observations and repeated/uncertain work.
- [x] Checkpoints larger than 16 MiB restore through verified artifact manifests.
- [ ] Executable revocation blocks new launches and reports affected running work.
- [ ] Long-lived manager context reconstructs after restart; one exception interface.
- [ ] Historical imports and migration preserve hashes, unknowns and original sources.
- [ ] Export/reimport preserves portable IDs and artifact references.
- [x] Current CLI training/baseline workflows delegate to the shared lifecycle.
- [ ] Bounded live model qualification and browser evidence are recorded separately.

## Work log

- 2026-09-27: inspected both working trees and live service records. No active
  experiments or implementation jobs were present. Added a credential-excluding
  baseline capture tool using SQLite online backup and external-blob inventories.
- Source capture: 140 current-tree files and 92 sibling files; 1,171 current
  artifacts (65,306,441 bytes), 5,061 sibling artifacts (52,880,727,715 bytes).
  SQLite online backups passed `integrity_check`. The annotated questions were
  copied unchanged. Baseline completion marker and manifests are retained.
- Extracted canonical workspace, implementation service, manager, provider,
  persistence and learner modules into `optimization_framework`; old imports are
  compatibility shims. MEENT physics is unchanged. Domain seeds and the
  historical policy loader remain in `dqn_meent`.
- New experiments pin a problem descriptor, immutable study/procedure, and
  source/runtime snapshot. Continuous quadratic/Rosenbrock minimization and
  binary MEENT maximization run through the existing scheduler and common worker.
  Declared feasibility, cost accounting, scientific completion, cooperative and
  forced recovery, and a chunked checkpoint exceeding 16 MiB have direct tests.
- User-facing forms select installed adapters and compatible methods. Shared
  problem/overview/experiment views show raw objectives, units, direction,
  attempts, costs and completion. The manager receives general study/capability
  context; optical seed knowledge is supplied by its adapter.
- Qualification at this foundation checkpoint: `pytest -q --tb=short`: **210
  passed**, one existing Starlette deprecation warning (38.91 s). Frontend build
  passed. Playwright `workspace.spec.ts` and `consolidation.spec.ts`: **20 passed**
  (10.2 s), including real browser/API/worker runs on both domains. Model calls
  were disabled: these are product checks, not live-agent acceptance evidence.

## Operational handoff while development continues

The live services on ports 8765/8766 run the captured original code from
`runs/consolidation/live-baseline/src`, with its own reconstructed frontend build.
Both services were idle before this reversible switch; their databases stayed
at the original locations, and both health endpoints passed afterward.
The overrides are listed in `runs/consolidation/live-baseline-override.json`:
`~/.config/systemd/user/{dqn-meent-latest.service,grating-implementations.service}.d/90-consolidation-baseline.conf`.
Remove these specific overrides only at qualified migration/cutover, then reload
systemd and restart the services. Do not delete other service configuration.

An isolated development service uses `runs/consolidation/development` on port
8767 with model calls disabled. The current root frontend build belongs to this
development version; the live baseline serves its separate build.

## Remaining work recorded at the foundation checkpoint

This is **not a completed release**. Continue every pending increment above.

- Complete durable attempt/cost integration and package revocation checks. Native
  and ask/tell continuous packages, including large state, already pass sandbox
  qualification with mocked semantic review (no live-model claim).
- Bind refinement to declared versioned starting assets and add frozen-policy
  rollout/transfer through the common worker. Q diagnostics are isolated and
  field/convergence/sensitivity recipes already use the common worker.
- Finish study policy/confirmation/waiver/reuse/dependency/cost services and
  registry manifests, including generated evaluator commissioning.
- Add versioned, idempotent work commands/outbox and full generic comparison,
  asset, lineage, study and validation UI. Only problem catalog is on v1 so far.
- Complete historical migration/import/export/rollback, live service cutover,
  and bounded live-model qualification. Adapt the older live browser test to the
  consolidated UI while preserving its simultaneous-worker/validation coverage.

## Numerical and package integration evidence

- Continuous packages now pass both deterministic replay/restore and independent
  adapter evaluations under the isolated implementation service. Native v1
  observe calls receive raw objectives and typed outcomes. Legacy ask/tell uses
  an explicit direction transform. Checkpoints travel through bounded binary
  pipes instead of JSON/base64; 20 MiB restore has a direct test.
- `scripts/generate_consolidation_references.py` executes the captured sibling
  source independently. Its checked-in fixture pins source hashes and runtime
  versions. Three final learner profiles match traces, replay and online/target
  weights exactly. Both hill-climber and refinement sequences agree, including
  checkpoint restoration. Intermediate historical variants are not added to
  the permanent method catalog.
- New CLI training/baseline runs use the framework worker. Action completion,
  reset requests, bounded recovery allowance and actual expenditure are distinct.
  The historical `checkpoint.pt` filename now holds a JSON manifest reference;
  policy weights are safe NumPy artifacts, separate from internal checkpoints.
  Historical pickle policies remain readable in the trusted compatibility path;
  legacy learner-state resumption requires the recorded historical runtime.
- MEENT field reconstruction and four independent scientific tests were copied
  from the captured sibling source. Fourier convergence, fields and parameter
  sensitivity are frozen recipes run by the common worker with per-evaluation
  identities and costs. F320/F480 planning is tested; bounded integration tests
  execute lower orders and do not claim fresh high-order numerical qualification.
- Q diagnostics and policy export do not advance learner, Python, NumPy or torch
  RNG state. Optical objective bounds are declared by the adapter; the core
  does not assume that arbitrary objectives are fractions or maximized.
- Fresh full Python run after numerical/recipe integration: **229 passed**,
  one existing Starlette warning (40.63 s). Subsequent asset and storage checks:
  **10 passed**. These are separate from the earlier 20 browser checks; browser
  and live-model qualification of the final integrated release remain pending.
- Added asset, explicit reuse/reference/decline, cost event and half-open cost
  interval contracts. DAG accounting deduplicates overlapping shared prefixes
  per result, keeps actual campaign charges separate, and retains unknown cost
  provenance. Source/ordinal uniqueness is transactional (schema migration 2).
  Connection to worker outputs and campaign actions is the next integration step.

## Study, input and accounting integration

- Worker request costs now include measured optimizer/evaluator work. Terminal
  overhead and interrupted/uncertain work remain explicit. Finished attempt
  records and costs are ingested idempotently, with database uniqueness for each
  physical source/ordinal. Reconciliation publishes verified output references
  and individual solution assets without overwriting prior versions.
- Experiments bind asset IDs and content digests. Initial inputs require a reuse
  decision for that study; reference-only and declined decisions do not grant
  worker access. Package inputs are pinned and mounted read-only at `/assets`.
  Refinement requires one solution asset. An end-to-end test charges its source
  search once physically and in full as an upstream contribution.
- New studies freeze their scope, comparison/validation policy and optional
  confirmation roster. Seed replication, unseen-instance confirmation and policy
  transfer have independent eligibility. Fresh seeds on an exposed condition
  are accepted by replication and rejected as unseen. Seed/method/instance cells
  cannot be allocated twice, and confirmation schedules/allocations stay fixed.
- The frozen-policy implementation reads a declared safe NumPy policy artifact,
  makes no learner updates, and restores inference state through the common
  lifecycle. A MEENT transfer test checks frozen weights and upstream pretraining
  attribution. Other policy formats need a validated inference adapter.
- Requirements, results, waivers and waiver revocations are distinct immutable
  records. Waivers require scoped study authority and supporting evidence, cannot
  become measured passes, and cannot preserve a missing fixed-confirmation claim.
  Changed versions/recipes/studies and new contrary evidence invalidate applicable
  waivers. Imported attestations remain labeled and require explicit policy.
- Fresh full Python run at this checkpoint: **245 passed**, one existing
  Starlette deprecation warning (41.44 s). Subsequent changes only preserve cost
  journals during CLI copy-resume, record study changes in charter history, and
  include new record kinds in campaign memory. Browser/live-model qualification
  of this expanded surface remains pending.

## Durable actions, executable validation, and shared evidence views

- Application commands commit outcomes, allocations, and delivery intent in one
  SQLite transaction. Concurrent retries allocate one experiment; rolled-back
  commands neither reserve implementation grants nor send requests. Build
  delivery survives lost acknowledgements and workspace restart using the same
  service idempotency key. Manager turns preserve their identity and guidance
  revision on retry. Delegated actions now use the command boundary with manager
  authority and pinned charter/guidance/resource context.
- Worker controls use a durable outbox that projects the latest committed intent.
  Dependency jobs wait for scientific completion, and a restarted supervisor can
  adopt a worker after a lost process-ID commit. New package starts check current
  revocation; revocation affecting running work produces a manager issue.
- General comparisons retain raw objective direction, separate incompatible
  instances/fidelities, use full upstream cost, and retain censored observations.
  Unknown cost remains unknown. Wall-time amendments now appear as adaptive
  extensions as well as evaluation-budget amendments.
- Validation requirements bind immutable solution snapshots and exact search
  prefixes. Checks can be required first, executed later, or explicitly waived.
  Recipe results produce immutable passed/failed/inconclusive/error records;
  paused work cannot become a passing measurement. A source experiment can
  continue without changing a prior subject or its attributed prefix cost.
  The continuous application's analytic fixtures now execute through the same
  scheduler and bind evaluator identity independently of optimizer performance.
- The shared interface exposes compatible comparisons, assets and lineage,
  explicit reuse/reference/decline decisions, validation and waivers, and linked
  study creation/history. Recipe forms read registered adapter schemas. The
  manager receives typed command schemas and validation assessments; operational
  issues in heldout work remain visible with numerical content redacted.
- Bundled methods publish lifecycle and parameter manifests. Unsupported or
  malformed parameters, non-integral learner counts, and unavailable GPU
  scheduling fail before experiment allocation.
- Fresh full Python run before the UI/schema/parameter additions: **262 passed**
  with one existing Starlette deprecation warning (42.54 s). Subsequent focused
  runs: **40 passed** across contracts/actions/validation/numerics/service, and
  **16 passed** across parameter rejection/worker/comparison. Frontend production
  build passed. Browser suite: **20 passed, 1 skipped** (12.8 s), including real
  MEENT and continuous jobs, comparison views, an explicit waiver, measured
  evaluator validation, decline-reuse decisions, and a new linked study. Models
  were disabled for these browser checks; they are not live-model qualification.
- Development service restarted on port 8767 using the new implementation.
  Live services on 8765/8766 remain pinned to the captured baseline. No live
  database migration or release cutover has taken place.

## Work following the command and cost checkpoint

The subsequent cost integration adds append-only model and implementation usage,
frozen cost-only contribution references (separate from optimizer inputs), and
production-job receipts from the library. A resumed model turn contributes only
the additional work; a reused implementation is charged physically to its origin
campaign and attributed in full to each dependent result. Provider wait time,
local implementation work, tokens/calls, and API charges remain distinct.
Interrupted or unmeasured usage remains unknown. Cross-workspace implementations
whose earlier proposal/dependency costs have not been imported remain **partial**,
not free or fully accounted. Portable upstream contribution import is still needed.

The latest full Python checkpoint is **272 passed**, one existing warning (43.13 s),
and the frontend builds successfully. Explicit manager reference retrieval and a
further heldout-protection check were added afterward. Library accounting tests use
deterministic adapters; no real model qualification has yet been performed. The
port-8767 development process predates these cost changes and must be restarted
before qualifying them through HTTP/browser workflows.

Continue the full plan; this is not a release boundary.

1. Finish full implementation/model cost lineage and explicit manager reference
   retrieval. Add the sibling replication template, frozen selection/verdict rules,
   periodic diagnostics, and confirmation result release. Keep a single scheduler.
2. Complete the remaining command/control translations and frontend v1 migration,
   including typed implementation controls and the full confirmation workflow.
   Persist rejected command attempts and review durable model-call reconciliation.
3. Finish generated evaluator commissioning and portable source/runtime identity.
   Exercise plugin parameter schemas and asset input selection in the general
   experiment form; finish remaining policy/diagnostic controls in the interface.
4. Historical dry-run imports, export/reimport, numbered migrations and rollback,
   final live-model/browser qualification, and qualified live-service cutover are
   still required. Existing live services remain pinned to the captured baseline.

## Confirmation workflow checkpoint — 2026-09-27

- Confirmation protocols now retain the source prototype for each frozen method.
  A roster command fills missing method/instance/seed cells in one transaction;
  retries return the existing allocations. Insufficient campaign budget rolls back
  every new allocation. Failed cells remain visible instead of being replaced.
- New cells verify and copy the prototype's captured source, preserving its
  scientific and runtime identities after the installed workspace changes.
  Changed prototype files and unavailable matching runtimes are explicit errors.
  Distinct fidelity cells on one scientific instance no longer collide.
- Required checks freeze recipe defaults and explicit parameters. A passing
  looser check cannot satisfy the fixed criterion. A separate command schedules
  the required jobs through the common worker without duplicating prior jobs.
  Field analysis retains the source experiment's Fourier-order default.
- Closing a completed roster releases protected evidence and an immutable
  descriptive report containing procedure/result identities and validation
  records. Only the researcher can close missing evidence as inconclusive.
  Later contrary validation creates an additional report linked to the original;
  the original release is preserved. A closed roster cannot be extended.
- The study interface now selects instances, freezes required-check parameters,
  schedules confirmation cells/checks, displays completeness, and releases or
  explicitly closes the result. Browser qualification exercised the entire
  continuous-problem path with actual worker jobs. Screenshots were inspected and
  table wrapping corrected.
- Full Python suite: **280 passed**, one existing Starlette warning (44.02 s).
  The source-preservation test was subsequently strengthened to use a different
  installation tree, in addition to rejecting current-source substitution;
  all **5** confirmation-workflow tests passed afterward (0.88 s).
  Frontend production build passed. Full browser suite: **21 passed, 1 skipped**
  (28.3 s); the skipped test targets the unchanged live service when explicitly
  enabled. Models were disabled; this is not live-agent acceptance evidence.
- Development service on port 8767 was restarted with these changes. Live
  services on 8765/8766 remain on the preserved baseline; no live migration or
  release cutover was performed.

### Current continuation priorities

The full objective remains incomplete. Continue the development plan's remaining
work packages, rather than treating this checkpoint as a release.

1. Complete the sibling's production study template, stage/deadline admission,
   per-seed prefix bindings and derived diagnostic seeds. Selection/verdict rules,
   immutable nomination and periodic diagnostics now have the integration evidence
   below; these do not yet constitute the complete production template.
2. Finish public diagnostic/recovery configuration and policy capability adapters.
   Preserve exact observed prefixes, isolated random streams, frozen methods and
   the single scheduler when completing the template.
3. Complete evaluator commissioning, experiment drafts/readiness, typed remaining
   controls and the full v1 interface, including declared input selection.
4. Complete portable identities, cross-workspace model/implementation provenance,
   terminal model-call reconciliation, historical imports/export and migration.
5. Run bounded live-model qualification, final migration/rollback checks, and
   qualified live cutover. Preserve the complete scope and existing evidence.

## Diagnostics and method-selection checkpoint — 2026-09-27

- Periodic schedules reserve their declared work, capture immutable solution and
  policy snapshots, and dispatch independent checks and policy episodes through
  the common scheduler. Captures bind the exact request prefix. Later parent work
  cannot change their evidence or costs; diagnostic discoveries stay outside the
  source search result and general search comparisons. Worker tests compare DQN
  traces and final weights with and without instrumentation.
- Development studies can freeze an installed selection rule and its parameters.
  The new command creates one immutable nomination containing the evidence hash,
  source prototypes and selected method identities. Concurrent retries return the
  same record. Readiness previews expose missing evidence, and an expected evidence
  hash rejects a nomination based on a stale preview. Later development cannot
  silently replace the selected method.
- Confirmation can bind a nomination and a versioned verdict rule. General scalar
  selection respects objective direction and uses full upstream cost to break ties.
  Its cost includes measured checks and declared diagnostic jobs, deduplicating
  parent prefixes. The paired criterion reports its required gain/wins and limits
  interpretation to the fixed roster; it does not claim population significance.
  Later counterevidence about the selected development results appends an
  inconclusive reassessment while preserving the original nomination and report.
- The sibling's final profile definitions, selection and verdict functions are
  retained in the MEENT adapter. A generator executed the preserved sibling source
  independently to produce **7 selection and 11 verdict fixtures**. The integrated
  production rules check physical scope, full learner procedures, controls and
  numerical evidence. This is rule-equivalence evidence, not a new production
  replication or fresh F320/F480 numerical qualification.
- Production orchestration remains unfinished. Refinement needs the HC prefix of
  the same seed, not a Cartesian product of fixed prefix assets and all seeds.
  Likewise, diagnostic rollout seeds derived from parent seed/milestone must be
  one declared procedure rather than different method identities per seed. The
  plan now specifies typed bindings for both. Stage budgets/cutoffs and template
  expansion still need implementation; the independent sibling controller is not
  imported as another scheduler.
- Browser qualification found two integration gaps and both have regression
  coverage: state projections now advance their cursor beyond the first 200
  events, while SSE catch-up retains ordered history; terminal diagnostics and
  confirmation wait for atomic artifact/validation publication before being
  treated as complete evidence. A failed publication rolls back its records and
  can be reconciled safely.
- Full Python checkpoint: **290 passed**, one existing Starlette warning (47.52 s).
  The subsequent inclusion of validation/diagnostic costs passed **9 focused
  tests** (3.94 s); strengthening stale-preview coverage then passed all **5
  study-rule tests** (1.67 s). Frontend production build passed. Final browser
  suite: **22 passed, 1 skipped** (24.1 s), including actual worker execution for
  nomination, confirmation and a declared diagnostic. Screenshots were inspected.
  Models were disabled; the skipped test targets the unchanged live deployment
  only when explicitly enabled. These results are not live-agent qualification.
- The development service on port 8767 was restarted after verifying it had no
  active work. Live services on 8765/8766 remain on their captured baseline; no live
  migration or cutover was performed. The full development objective remains active.

## Frozen execution and experiment drafts checkpoint — 2026-09-27

- New experiments capture actual source files, registered adapter entry points,
  dependency-lock hashes, and relevant runtime requirements. Scientific identity
  follows the worker/compiler/rule import closure, excluding unrelated interface
  changes. Confirmation preparation, diagnostic compilation, and analysis rules
  execute their captured source in a bounded host. Missing or incompatible source
  fails explicitly; old records are not assigned reconstructed provenance.
  Runtime version requirements are verified; this is not yet binary/wheel
  attestation or full portable-release qualification.
- Recovery policies are configurable and frozen. Diagnostic rollout seeds can use
  an affine declaration over parent seed, milestone, and episode index. The worker
  records resolved seeds without consuming the parent's RNG. Real DQN regression
  covers independent rollouts, stable method identity, and idempotent dispatch.
- Editable experiment drafts now persist unresolved implementation requirements.
  The proposal's Design experiment action opens the form even when code is absent.
  Saving a draft allocates no compute. Launch rechecks its revision and current
  readiness, then commits one immutable experiment and launch binding. Editing
  that draft preserves the earlier experiment. Study changes and conflicting
  implementation selections produce explicit blockers.
- The browser exposes draft revision/readiness, study selection, scientific
  completion, recovery settings, and advanced diagnostic configuration. Manager
  reconstruction includes draft readiness; large source manifests remain linked
  by digest instead of filling the model context. Complete schema-driven forms
  and declared asset selectors remain work in progress.
- Full checkpoint before the final study/conflict/context fixes: **301 Python
  tests passed** (91.95 s), **23 browser tests passed, 1 skipped** (1.2 min).
  The final fixes passed **38 focused Python tests** (3.45 s), the production
  frontend build, and **2 focused browser tests** (12.9 s), including the actual
  missing-code → saved draft → revised procedure → worker/diagnostic workflow.
  Model calls were disabled. These are development checks, not release acceptance.
- The idle development service was verified across all 31 campaigns and restarted
  on port 8767 (PID 119882). Live services on 8765/8766 remain unchanged. Production
  stage/grant orchestration, evaluator commissioning, portable history, real-model
  acceptance, and live migration/cutover still remain under the active objective.

## Staged execution checkpoint — 2026-09-27

- Added frozen study-template/design/cell records, immutable activation and method
  bindings, and a resource ledger shared with ordinary experiments. Templates
  expand into jobs of the existing scheduler. Fixed cutoffs and the worker-seconds
  envelope are separate; child diagnostics and required checks use the containing
  grant without charging its reservation twice.
- Reduced continuous and MEENT templates start controls during development,
  select once from committed development evidence, coalesce selected/control
  roles, and produce released reports. MEENT refinement consumes the matching
  seed's committed prefix while HC can continue. These templates are explicitly
  software-qualification examples; the production replication template is absent.
- Template cells retain logical-method identities, pinned source and individual
  resolved inputs. Outside exposure is checked against the declared cohort.
  Protected evidence stays out of manager context until release. Later independent
  counterchecks can append a reassessment without borrowing a closed grant or
  altering the original nomination and report.
- Browser views now freeze and activate templates and show cell dependencies,
  allocation, bindings and release. The missing-code draft remains editable without
  compute allocation; ordinary additions to a frozen template roster require a
  linked study rather than an unplanned cell.
- Recorded verification: **311 passed** in the full Python checkpoint (127.76 s),
  followed by **20 focused tests passed** (42.35 s) for later scope/countercheck
  changes. The latest full browser run recorded **23 passed, 2 failed, 1 skipped**
  (3.2 min). Both new template workflows completed actual worker execution and
  report release. Older MEENT asset-decision and study-creation workflows failed
  their dialog-close timeout checks. Their cause and resolution still require
  verification; the full browser suite is not passing.
- Subsequent source changes include evidence-cursor caching for report
  reconciliation, template admission on protected test tasks, and a per-attempt
  deadline guard. They have **not yet been verified**. The guard still needs
  enforcement-receipt reconciliation, a frozen grace policy and a subprocess test
  covering a stalled numerical call without the supervisor. Initialization and
  cleanup must also be covered by deadline enforcement. Cooperative cutoff tests
  alone do not establish that property.
- The development server was last recorded on port 8767, PID 125326, and predates
  those latest changes. Verify campaign activity and process ownership before
  restarting it. Live services remain on their captured baseline; no release or
  live migration has occurred. Models were disabled throughout these checks.

The next development sequence is the updated plan's section 12: stabilize these
integrations and complete production declarations; evaluator commissioning;
portable provenance/accounting; the complete common interface and manager cycle;
historical imports and migration; then final qualification and cutover. Earlier
checkpoint lists are historical, not evidence that these later additions are
missing. The implementation objective remains incomplete.

## Deadline and numerical qualification checkpoint — 2026-09-27

- The worker's independent deadline guard now covers initialization, numerical
  calls, checkpointing and runtime cleanup. Shutdown grace is pinned in the
  experiment specification and fingerprint. A forced stop writes a receipt tied
  to the exact experiment, attempt, PID/start identity, deadline and procedure.
- Restart verifies and ingests that receipt as deadline exhaustion. Overshoot
  comes from the termination request rather than the later reconciliation time.
  Actual final costs remain uncertain where the worker could not publish them.
  A result published before stalled cleanup retains its observations and measured
  costs, with the unmeasured cleanup added separately. Reconciliation is idempotent;
  a receipt for another attempt is rejected. No second scheduler was introduced.
- Four actual subprocess tests force stalls in the respective phases while no
  supervisor is running. Further regression checks reject changed grace, exercise
  unseen test-instance template cells and verify that report caching still reacts
  to new counterevidence. Focused checks: **26 passed** (60.36 s).
- The idle development service was verified across 41 campaigns before restart;
  it now runs on port 8767, last recorded PID **128753**. The two formerly failing
  browser workflows passed with their existing assertions. The subsequent full
  checks passed: **317 Python tests**, one existing Starlette warning (154.40 s),
  and **25 browser tests, one skipped** (2.5 min). The skipped test targets the
  unchanged live deployment. Both template screenshots were visually inspected.
- Added [the bounded fidelity qualification script](../scripts/qualify_meent_fidelity.py).
  Its isolated run is recorded in
  [the numerical report](../runs/consolidation/fidelity-20260927/qualification-report.json).
  The preserved 1100 nm / 70-degree paper design was evaluated at F40, F160, F320
  and F480 using the common worker and a frozen convergence recipe. All four
  efficiencies exactly matched the sibling's preserved report; the F320/F480
  difference was approximately `6.336986901e-6`, below the declared `1e-4` tolerance.
  This run performed **5 solver executions** (the source observation plus four
  validation evaluations), recording **6.550903071940411 worker-seconds**. The
  reference's historical upstream cost stays unknown, so full attributed totals
  remain partial. This is one-design fidelity evidence, not a replication verdict.
- Model calls were disabled throughout software checks; the fidelity script made
  no model calls. Live services on 8765/8766 remain on the captured baseline. No
  live migration, release cutover or real-manager qualification has occurred.

Continue with the full production template and explicit reference-asset bindings.
The sibling roster has eleven development profiles with seeds 10–12; confirmation
and HC/refinement use seeds 100–104. Its seven fixed references are the paper
design and the earlier HC/refinement designs at seeds 0–2. Preserve their identities
and original provenance when binding them. The production conditional-rule
validator and prefix-evidence projection still need integration. Evaluator
commissioning, portable lineage, remaining typed controls/forms, historical
migration, real-model qualification and cutover remain under the full objective.

## Production reference input checkpoint — 2026-09-27

- The MEENT adapter now supplies the complete production declaration: eleven
  profiles on development seeds 10–12; P/selected and HC/refinement on seeds
  100–104; and seven fixed paper/earlier-HC/refinement references. Freezing
  verifies 60 declared cells, exact candidate identities, source capture, seed
  order, priorities and criteria before allocating work. The production
  admission test queues seven references and the first P cell without starting
  its million-action jobs.
- Named literal inputs complement same-seed prefixes. Frozen designs pin each
  asset ID and content hash. The `evaluate_asset` worker procedure performs one
  declared reference request, retains its full upstream lineage and keeps its
  evidence separate from development selection. Required reference checks are
  ordinary child jobs. Missing, changed, unavailable and protected inputs are
  rejected; unknown historical exposure prevents an unseen-instance claim.
- Added installed reference manifests and `asset.import_reference_set` through
  the typed command boundary. Repeated imports, including into another campaign,
  preserve the seven asset identities without duplicating costs. Catalog previews
  expose source identities and candidate hashes; candidate values are published
  by the explicit import. Imports preserve unknown production cost and incomplete
  exposure history, and do not create an optimizer-input reuse decision. Full
  producer-history import remains separate work.
- Browser templates now show asset selectors, actionable missing-input reasons,
  the preserved-reference import, and frozen input bindings. The production flow
  freezes all 60 cells without activation; a small continuous workflow actually
  executes its bound reference, completes an independent evaluator check, and
  releases a report with the reference outside the nomination. That small
  browser scenario injects only a declarative catalog fixture; commands, worker
  execution, validation and report generation use the actual service. Both new
  screenshots were inspected.
- Staged MEENT selection/verdict rules are explicitly versioned as `v2`. Their
  time tie breaker includes measured parent and declared child numerical work;
  `v1` retains the historical inline-time interpretation. Missing required
  duration produces an inconclusive nomination and one scoped manager issue.
  Full attribution still deduplicates prefix contributions. Independent sibling
  selection/verdict fixtures remain regression evidence.
- Browser verification exposed integral floats serialized as integers. Training
  values now normalize after strict validation; the production contract accepts
  equivalent JSON spellings while still rejecting changed science. The generic
  hypothesis generator also excludes reference evaluation from search proposals.
  Final Python regression: **322 passed**, one existing Starlette warning
  (177.86 s). The frontend production build passed. Final browser regression:
  **27 passed, one skipped** (4.3 min), including both new scenarios and all
  existing study/draft/confirmation scenarios. The skipped test targets the
  unchanged live deployment.
- Added [the seven-reference qualification script](../scripts/qualify_meent_references.py).
  Its completed [report](../runs/consolidation/references-20260927/qualification-report.json)
  records campaign `campaign_001c1a98b052488e`, execution
  `execution_026463a4330341be`, the exact captured source, original source hashes,
  frozen bindings, validation and the released qualification report. All seven
  designs exactly match all four sibling measurements at F40/F160/F320/F480;
  maximum difference is **0.0**, with every declared convergence check measured
  passed. The entire small workflow used **41 solver executions / requests** and
  **49.558259162295144 measured worker-seconds**. All seven full attributed cost
  totals remain unknown because original production cost is unknown. The study
  closed with all 17 jobs complete and no model calls. This is bounded reference
  fidelity and software evidence, not a production replication verdict.
- Development service 8767 was restarted only after checking all 52 existing
  campaigns for active work. Its current recorded PID is **138310**, with models
  disabled. Live services 8765/8766 were not changed. Researcher annotations still
  have their preserved hash. After browser qualification, all 61 development
  campaigns had no active jobs/reservations. The seven-reference qualification
  likewise has no active work and its execution grant is released.

Continue A with registered policy/artifact capabilities and complete diagnostic
forms and recovery qualification. Then finish evaluator commissioning, portable
lineage/runtime resolution, remaining typed controls and manager qualification,
full historical migration, bounded real-model acceptance and live cutover.
No full-release gate is inferred from this checkpoint.

## Registered diagnostic capability checkpoint — 2026-09-27

- Optimizer implementations now declare supported completion counters and
  exported artifact formats. Registered inference adapters own format
  compatibility, parameter schemas and bounded inference procedures. The core
  diagnostic path uses these declarations; historical DQN rollout declarations
  retain their original serialization and behavior. Implementation correctness
  checks verify declared counters/exports and that exporting leaves the next
  proposal unchanged. See the [extension guide](diagnostic-extensions.md).
- Captured numerical source includes inference entry points and compilation.
  Immutable milestone snapshots dispatch ordinary dependent jobs with separate
  random seeds, resource limits and exact upstream-prefix contributions. Nested
  checks run against the inference child's result. Diagnostic discoveries stay
  outside the parent search comparison. Registered read-only inference also
  participates in policy-transfer confirmation with explicit study input reuse.
- The experiment and draft forms expose supported milestones, compatible
  inference adapters, parameters, fixed/derived seeds, nested recipes and time
  limits. Missing capabilities remain actionable readiness reasons. A missing
  diagnostic catalog preserves the usable form and saved declarations. Browser
  regression also exposed overlapping evidence refreshes; completed reads now
  publish while subsequent SSE revisions coalesce into the next refresh.
- The independent continuous extension fixture exercises a generated optimizer
  exporting a non-DQN format through the actual implementation and numerical
  runtimes. It interrupts child allocation before the dispatch transaction
  commits, then changes/removes the installed extension and restarts the
  workspace. Recovery uses captured source, creates one child set, preserves
  snapshots and the inherited deadline/grant, and records costs once. Its child
  has nine fully attributed requests: four implementation-fixture requests,
  two parent-prefix requests and three inference requests. The semantic-review
  transport is mocked and candidate code is supplied; this is not real-model
  build qualification.
- The saved [browser qualification report](../runs/consolidation/diagnostic-capabilities-20260927/qualification-report.json)
  records campaign `campaign_b72005e03b69463b`, parent
  `trial_f8af29cb559f4c9f`, captured source, frozen specifications, snapshots,
  outputs and lineage. Its five completed jobs are the parent, two inference
  children with seeds 102/104 and their two checks. They used **nine evaluator
  requests / nine solver calls**, with **zero model calls**. This four-cell,
  low-fidelity scenario is bounded software evidence, not a production study.
- Final saved regression reports record **326 Python tests passed** in 195.45 s
  and **28 browser tests passed, one skipped** in 419.92 s. The frontend
  production build passed. The skipped browser case targets the unchanged live
  deployment. Reports are [Python JUnit](../runs/consolidation/diagnostic-capabilities-20260927/pytest.xml)
  and [browser JUnit](../runs/consolidation/diagnostic-capabilities-20260927/browser.xml).
  [Extension evidence](../runs/consolidation/diagnostic-capabilities-20260927/extension-evidence.json)
  and its copied local workspace retain the independent recovery records. That
  workspace is an evidence backup with original runtime references, not a
  portable execution bundle. Diagnostic and resolved-draft screenshots are
  preserved in the same artifact directory.
- A read-only planning review confirmed the completed JUnit results and all
  **419 development trials completed** across **85 campaigns**. Four frozen,
  unactivated study executions remain intents. This does not assert that all
  historical reservation/grant records are released. Live services were not
  changed, and the annotated architecture questions retain SHA256
  `3d966e43d9b641abdbbe4db855656f26347f2a30586bd68bb8aaf218363a1997`.

The [further development plan](architecture-consolidation-plan.md#12-further-development-from-the-current-branch)
now preserves A's diagnostic/scientific workflows as regression gates and makes
B's evaluator commissioning the next delivery. C–F retain portable provenance,
the complete manager/interface workflow, historical import/migration and bounded
real-model release qualification. The plan explicitly settles the evaluator host,
generated versus installed extension boundary, import reconciliation and remaining
command ownership. No further architectural input is required; researcher
preferences can be validated against working scenarios. This checkpoint does not
complete the integrated product or authorize a scientific performance claim.

## Further-development planning review — 2026-09-27

The researcher requested a development plan with architectural choices resolved
and implementation preferences left for later validation. The authoritative
[remaining-work plan](architecture-consolidation-plan.md#12-further-development-from-the-current-branch)
continues to use this branch as the integration base, with the sibling supplying
numerical behavior, scientific protocols, diagnostics and historical evidence.
No additional architectural input is required. The framework helps users develop
optimizers for the active problem; optimizer reuse remains optional.

Source inspection now finds evaluator manifests and packages, an isolated host,
independent correctness checks, immutable requirements/bindings, bound optimizer
validation, typed commissioning/control operations, captured worker execution,
initial forms and focused workflow tests. B is partially implemented. Its library
view still assumes optimizer-shaped versions, and the optimizer request form
still uses MEENT defaults and a legacy route. The complete HTTP/browser workflow,
restart/reuse lifecycle, policy-scoped eligibility, compatible recipe integration
and final regression evidence remain outstanding. The earlier 326-Python and
28-passing-browser checkpoint predates these changes.

The plan now starts by finishing that combined optimizer/evaluator delivery.
It explicitly distinguishes artifact publication, mandatory executable contract
checks, numerical correctness evidence and study-scoped launch eligibility.
Missing scientific evidence remains a blocker by default; an eligible exploratory
waiver cannot become a numerical pass or satisfy a confirmation criterion.
Budget/guidance-only edits must preserve scientific bindings. Qualification must
exercise separate current-source services, exact upstream costs, repeated
commands, interruption, reuse, revocation and one manager-mediated failure.

A remains a regression gate; C–F retain portable evidence/accounting, the complete
manager/interface boundary, historical imports/migration and integrated release
qualification. This planning review adds no runtime acceptance claim. Researcher
annotations and live services remain unchanged.

## Executable commissioning browser checkpoint — 2026-09-27

- The separate implementation service now supports evaluator packages with
  data-only problem manifests and independent numerical fixtures. An isolated
  host enforces the evaluator contract; candidate output cannot set observation
  identities, resource costs or provenance. Both executable kinds retain source,
  runtime and scoped correctness evidence. Wrong numerical results cannot pass
  through a positive semantic reviewer. Missing fixtures block the default path
  before a model call. See the [commissioning guide](evaluator-commissioning.md).
- Unresolved problem declarations create durable requirements. Immutable bindings
  resolve them without rewriting a frozen study. Drafts remain editable with
  missing optimizer/evaluator requirements. Launch rechecks exact versions and
  captures both packages and their upstream contributions. Revocation prevents
  another launch while preserving historical records.
- The browser declares custom problems, commissions or attaches evaluators and
  shows both executable kinds in the library. Optimizer requests use the selected
  problem and exact evaluator version. Build, attachment and implementation
  controls use typed application commands. A budget-only charter round trip now
  compares serialized scientific declarations consistently, preserving the task,
  requirement, binding and active study. An advanced-manifest editor race found
  by the browser scenario was also corrected.
- Focused evidence covers malformed output, independent numerical failure,
  isolation, timeout/crash, checkpoint compatibility, legacy serialization,
  repeated controls, scoped manager issues and unrelated continued work. A
  commissioned pair completes four requests with **28 fully attributed requests**
  (20 evaluator checks, four optimizer integration checks and four experiment
  requests). Reuse in a second experiment pauses, reconstructs both service
  objects, restores evaluator and optimizer state and finishes **200 requests in
  two attempts**, with **224 fully attributed requests** and no repeated build.
- The actual browser/HTTP scenario creates campaign
  `campaign_a72f0131499f4b28`, saves a draft with both executables missing, imports
  and validates the supplied packages, edits only the budget and launches the
  original draft. Browser errors are empty. Its specification, jobs and frozen
  experiment are in
  [browser evidence](../runs/consolidation/evaluator-commissioning-20260927/browser-results/commissioning-commissioned-070a6-nding-and-upstream-evidence/commissioning-evidence.json),
  with library and resolved-draft screenshots in the same directory.
- Both qualification HTTP services were stopped and restarted on their existing
  databases. Scientific records and manager guidance remained identical. Retrying
  the original launch command returned the same receipt without another trial or
  implementation job. The [restart record](../runs/consolidation/evaluator-commissioning-20260927/http-restart-evidence.json)
  and [recovery record](../runs/consolidation/evaluator-commissioning-20260927/recovery-evidence.json)
  retain the evidence. The copied recovery workspace is a local evidence backup
  with original runtime references, not C's portable bundle.
- Final regression: **341 Python tests passed** in 205.20 s; **29 browser tests
  passed, one skipped** in about 3.1 minutes; the frontend production build
  passed. Reports are [Python JUnit](../runs/consolidation/evaluator-commissioning-20260927/pytest.xml)
  and [browser JUnit](../runs/consolidation/evaluator-commissioning-20260927/browser.xml).
  The skipped test targets the unchanged live deployment. All 64 trials in the
  qualification workspace were completed before its HTTP restart.

Model review in these commissioning checks is explicitly mocked and source is
supplied. Numerical validation, HTTP/authentication, persistence, isolation and
worker execution are real. This is not live-model generation or a scientific
performance claim. Qualification uses ports 8768/8769 and separate databases;
the live deployment and researcher annotations remain unchanged.

B remains open for scoped exploratory evaluator eligibility, composition of
registered recipes, explicit reuse/decline and revalidation controls, and the
remaining product failure/recovery cases. A stays a regression gate. C–F retain
portable evidence/accounting, the complete command/manager interface, historical
imports/migration and bounded real-model release qualification. This checkpoint
does not complete or release the integrated product.

## Scoped evaluator eligibility checkpoint — 2026-09-27

- B1 now distinguishes mandatory evaluator contract checks, independent numerical
  evidence and study-specific permission to experiment. Explicit contract-only
  requests publish `contract_validated` with numerical status `unverified`.
  Default commissioning still requires independent fixtures; malformed output and
  known numerical disagreement cannot become a published passing evaluator.
- An immutable binding records contract evidence. Each applicable study receives
  its own numerical requirement, including earlier studies whose drafts were
  waiting for that evaluator. An exploratory waiver pins the study, binding,
  requirement and decision into the experiment. New attempts recheck that exact
  authorization. Confirmation is rejected without the required numerical evidence.
- The browser exposes contract probes, the exploratory study-policy option,
  supporting evidence, waiver creation and revocation. Bound evaluators direct
  users to evidence review. Requirements without an executable check do not offer
  a misleading run action. The library and optimizer integration reports disclose
  an unverified evaluator dependency; bounded implementation checks remain
  separate from experimental authorization.
- The real-worker recovery check revokes eligibility during an active attempt,
  produces one deduplicated manager issue, pauses cooperatively and reconstructs
  both service objects. Neither the revoked waiver nor a replacement can resume
  the frozen experiment. A later waiver leaves an earlier still-active waiver
  valid. A linked study requires its own decision. The local fixture backup and
  [recovery evidence](../runs/consolidation/evaluator-eligibility-20260927/recovery-evidence.json)
  preserve the records; this backup retains original runtime paths and is not a
  portable bundle.
- Manager context includes the evaluator contract report and preserves the exact
  waiver basis in remembered experiment summaries. Its probe heuristic can
  propose permitted exploratory work while explicitly marking numerical
  reliability as unestablished. It cannot label a waived evaluator numerically
  reliable merely because execution is authorized.
- The browser/API scenario commissions a contract-only evaluator, defines an
  eligible linked study, saves a blocked draft, records the waiver, launches the
  draft, rejects confirmation and revokes further authorization. The original
  trial remains complete with its original evidence basis. Its
  [browser evidence](../runs/consolidation/evaluator-eligibility-20260927/browser-results/commissioning-contract-onl-c4ef3--cannot-become-confirmation/eligibility-evidence.json)
  and screenshot identify campaign `campaign_562d1582f1b84bc3` and trial
  `trial_b4134aed4b564b55`.
- Both isolated HTTP services were subsequently stopped and restarted. Campaign,
  study, task, draft, trial and waiver records remained identical. Replaying the
  original waiver command returned the identical receipt without restoring the
  revoked authorization or creating another trial/waiver. See the
  [HTTP restart record](../runs/consolidation/evaluator-eligibility-20260927/http-restart-evidence.json).
- Full regression: **345 Python tests passed** in 211.08 s and **30 browser tests
  passed, one skipped** in about 3.1 minutes; the frontend production build passed.
  The final manager-summary and probe-label corrections then passed a focused
  **45-test regression** in 10.14 s. Reports are
  [full Python](../runs/consolidation/evaluator-eligibility-20260927/pytest.xml),
  [browser](../runs/consolidation/evaluator-eligibility-20260927/browser.xml) and
  [manager regression](../runs/consolidation/evaluator-eligibility-20260927/manager-regression.xml).

All commissioning model reviews remain explicitly mocked and source is supplied.
The HTTP, isolation, contract checks, persistence, worker and recovery paths are
real. Qualification uses separate databases on ports 8770/8771. The live
deployment and researcher annotations remain unchanged.

B remains open for B2 registered-recipe composition, B3 standalone revalidation
and explicit executable reuse/decline, and B4 combined acceptance. C–F still cover
portable evidence/costs, the complete manager/command interface, historical
migration and bounded real-model qualification. B1 is locally verified; the
integrated product is not yet qualified or released.

## Generated evaluator recipe checkpoint — 2026-09-27

B2 is implemented and locally verified. Generated problem manifests declare
reviewed, versioned recipes; they cannot inject analysis code into the workspace.
The scoped adapter resolves the exact evaluator and captured recipe registry for
ordinary experiments, periodic diagnostics, required checks and study templates.

- `candidate_reevaluation:v1` records measurements. `fidelity_comparison:v1`
  compares the primary objective at the final two declared settings of a
  deterministic evaluator. Neither supplies missing independent numerical
  correctness evidence; a passing fidelity check cannot turn a waived evaluator
  into an eligible confirmation evaluator.
- Experiment and study forms use campaign-specific definitions. Trial validation
  reads its captured catalog, including after an installed extension changes or
  is removed. Forms handle structured fidelity arrays, exact definition/evaluator
  identities and unavailable recipes. Invalid or unsupported requests fail before
  allocation. Older generated captures without recipe registration expose an
  explicit limitation instead of silently using newly installed code.
- Eight added Python tests cover declaration compatibility, parameter and
  assertion limits, isolated worker execution, full upstream cost, actual
  multi-host pause/resume, waived evidence, a 13-job template, multiple catalog
  versions and an independent installed recipe extension. Recovery retains
  **26 observations across two attempts and 13 fidelity identities**. Its
  **50 attributed requests** comprise 20 correctness calls, four parent requests
  and 26 child requests; the physical ledger also records 50 requests. See the
  [recovery evidence](../runs/consolidation/generated-recipes-20260927/recovery-evidence.json).
- The final browser scenario creates campaign `campaign_8ed6751f9fe74163`,
  commissions a supplied evaluator, configures study and periodic checks, and
  completes a parent experiment, measurement diagnostic and fidelity validation.
  Its catalog basis is `captured_source`, and recorded browser errors are empty.
  The [browser evidence](../runs/consolidation/generated-recipes-20260927/browser-results/generated-recipes-commissi-e0157-tic-and-validation-controls/generated-recipe-evidence.json)
  and [screenshot](../runs/consolidation/generated-recipes-20260927/browser-results/generated-recipes-commissi-e0157-tic-and-validation-controls/commissioned-fidelity-evidence.png)
  preserve the resulting controls, specifications and evidence.
- An earlier scenario in the same qualification workspace survived restarting
  both HTTP services: 49 campaign records and three trials remained unchanged,
  and replaying a completed command returned the same receipt. Its older capture
  retains the explicitly labeled compatible-current-definition catalog. See the
  [HTTP restart evidence](../runs/consolidation/generated-recipes-20260927/http-restart-evidence.json).
- Full regression records **353 Python tests passed** in 250.43 seconds and
  **31 browser tests passed, one skipped** in 242.03 seconds. The skipped test
  targets the unchanged live deployment. The frontend production build passed.
  Reports are [Python JUnit](../runs/consolidation/generated-recipes-20260927/pytest.xml)
  and [browser JUnit](../runs/consolidation/generated-recipes-20260927/browser.xml).
  The first browser run exposed an incorrect new test expectation: it counted
  only the latest correctness report while the persistent library retained
  earlier commissions of the same executable. The corrected assertion checks
  the full implementation contribution; both its focused rerun and the final
  full suite passed. The initial report remains alongside the final evidence.
- The [checkpoint](../runs/consolidation/generated-recipes-20260927/checkpoint.json)
  links the reports and the 232-file source manifest, whose SHA-256 is
  `a297b20972c9a318151f12fe5c09c3ada7d7caee1d17eae38ba68769cfcf385b`.
  Every captured source file still matches at the subsequent planning review.

Source is supplied and model review is mocked. HTTP, isolation, correctness
probes, workers, accounting and recovery execute normally. Qualification uses
separate databases on ports 8772/8773. The copied recovery fixture is a local
evidence backup retaining original runtime paths, not a portable bundle. The live
deployment and researcher annotations remain unchanged.

The development plan now takes B1/B2 as regression foundations and starts with
B3 standalone revalidation and explicit executable reuse/decline, then B4 combined
acceptance. C–F still cover portable evidence and costs, the complete manager and
command interface, historical migration and bounded real-model qualification.
The plan resolves the architectural choices without another questionnaire;
validation-stage preferences remain configurable. This documentation refresh
does not change application code or qualify the integrated product for release.

## Further-development planning refresh — 2026-09-27

The development plan now reflects the in-progress B3 source: standalone
revalidation requests/jobs, append-only correctness evidence, explicit executable
reuse/decline decisions, associated controls and focused tests are present.
They still require completed integration and qualification on the final source.
The immediate work includes propagation of later failed correctness evidence to
confirmation assessment and linked reassessments, browser coverage, both-service
recovery and a new complete regression record.

The B2 reports were read again: 353 Python tests passed and 31 browser tests passed
with one skipped. Their manifest describes an earlier checkpoint; 23 of its 232
captured files now differ, and new B3 files are not included. Those reports are
not evidence that the current B3 working tree passes. No new runtime tests were
run for this planning refresh.

The remaining sequence is completion of B3/B4, portable evidence and full upstream
costs in C, shared controls and durable manager integration in D alongside those
features, historical import/migration in E, and real-model qualification and
release in F. The plan resolves the architectural decisions without additional
researcher input. Optimizer reuse remains optional; generality belongs to the
harness. Researcher annotations, application source and live services were not
changed by this documentation update.

## Standalone revalidation and executable reuse checkpoint — 2026-09-27

B3 and the combined B4 workflow are implemented and locally verified. Published
optimizers and evaluators can be checked without rebuilding code, changing their
identity or making model calls. Independent additions retain earlier fixtures and
counterexamples. Reports and partial costs survive interruption and publication
recovery; a narrower request cannot conceal a known correctness failure.

- Reuse and decline are explicit decisions about an exact version and study.
  Full problem-definition compatibility, library refresh, applicability rationale
  and upstream attribution are retained. A decline permits a specialized build.
  The library and evaluator forms use the shared commands and display decisions.
- Workspace admission of executable evidence is now recorded independently of
  mutable library metadata. New failures affect manual and conditional
  confirmation, including nomination reassessment. Earlier observations,
  nominations, releases and selection-cutoff evidence remain unchanged. Losing a
  runtime directory alone does not invalidate an earlier measurement. Legacy
  cache projections acquire their actual admission time, never a fabricated
  earlier date.
- These checks exposed and fixed a manual-confirmation binding error for
  commissioned evaluators. A failed recheck whose evidence reply is lost also
  now retries reconciliation after its terminal job receipt has been recorded.
  Focused tests cover both fixes, historical immutability, unissued-claim refusal,
  both confirmation protocols and service reconstruction.
- The browser scenario adds numerical evidence to an existing contract-only
  evaluator, preserves the original experiment's waiver, launches a new experiment
  with its numerical evidence, and records an incompatible decline and compatible
  reuse in another campaign. The old and new experiments retain **9 and 24**
  attributed requests respectively. Browser errors are empty. See the
  [browser evidence](../runs/consolidation/revalidation-reuse-20260927/browser-results/revalidation-independent-r-cb9a9-ble-reuse-records-its-scope/revalidation-reuse-evidence.json)
  and [inspected screenshot](../runs/consolidation/revalidation-reuse-20260927/browser-results/revalidation-independent-r-cb9a9-ble-reuse-records-its-scope/executable-reuse-decisions.png).
- Both isolated HTTP services were stopped and restarted while a real experiment
  was paused. **186 records** remained identical, and replay returned the original
  accepted command receipt without adding work. The same experiment then completed
  **1,200 unique observations across two attempts**, preserving its specification,
  source and numerical authorization. Its **1,220 attributed requests** include
  five original evaluator checks and 15 revalidation requests. See the
  [HTTP restart record](../runs/consolidation/revalidation-reuse-20260927/http-restart-evidence.json),
  [process evidence](../runs/consolidation/revalidation-reuse-20260927/service-process-restart.json)
  and [worker recovery](../runs/consolidation/revalidation-reuse-20260927/worker-recovery-evidence.json).
- A separate actual HTTP countercheck found a previously unchecked numerical
  error, used zero model calls and produced one manager issue, still one after
  another reconciliation cycle. Independent work continued. Its 5,000-request
  procedure reached the frozen 20-second cap at 4,210 observations and remains
  scientifically incomplete; a separately frozen 20-request experiment completed
  while that same issue remained open. The
  [failure/continuation record](../runs/consolidation/revalidation-reuse-20260927/failure-continuation-evidence.json)
  retains both outcomes without changing the first procedure's criterion.
- Final regression: **371 Python tests passed** in 285.41 seconds and **32 browser
  tests passed, one skipped** in 224.28 seconds. The skipped test targets the live
  deployment. The frontend production build passed. The first full Python run
  exposed a feedback fixture that intercepted the new catalog refresh with its
  model transport; an explicit empty-library fixture fixed that isolation, and
  its focused and full reruns passed. Reports are
  [Python](../runs/consolidation/revalidation-reuse-20260927/pytest.xml) and
  [browser](../runs/consolidation/revalidation-reuse-20260927/browser.xml).

The [checkpoint](../runs/consolidation/revalidation-reuse-20260927/checkpoint.json)
links these artifacts and a 241-file source manifest. The restart qualification
is repeatable with [its script](../scripts/check_revalidation_restart.py). Its
database backups and runtime paths are local recovery evidence, not portable
bundles. Qualification used ports 8774/8775. Initial commissioning source is
supplied and semantic review is mocked; numerical checks, HTTP, workers,
accounting and recovery are real. The live deployment and researcher annotations
remain unchanged.

Next is C: portable records, runtime resolution and full upstream provenance,
with D's command/manager/interface completion alongside it. E retains historical
import and migration/rollback work; F retains bounded real-model acceptance,
final qualification and live cutover. The integrated product is not released.

## Portable executable runtime foundation — 2026-09-27

C1 is implemented and locally verified. New optimizer and evaluator artifacts
exclude installation paths from their content identities. Their schema-2 runtime
manifests pin the interpreter, platform/ABI, captured standard library,
dependency files and native libraries. Local bindings retain installation paths
and required loader aliases. Historical schema-1 artifacts retain their original
hashes and reader; automatic relocation of their embedded system paths remains
unsupported pending a verified converter.

The library separates runtime availability from correctness evidence. Missing or
damaged local bindings leave intact source and historical reports inspectable.
The shared `implementation.resolve_runtime` command resolves exact installed
content without model calls, downloads or candidate initialization. Its durable
operation and immutable receipt survive a lost reply or service reconstruction;
unobserved elapsed work remains unknown. Incompatible content becomes a scoped
campaign-manager issue. A damaged installation is retained, and a prepared
experiment can refresh its operational binding while retaining its frozen
bundle, specification and validation basis.

Qualification evidence:

- Nine new Python checks cover identical identities in different library
  directories, a copied library with the original directory unavailable, real
  sandbox evaluation after resolution, bytecode/file tampering, non-destructive
  repair, legacy reading, platform mismatch, corrupt bindings, receipt recovery,
  HTTP authentication/replay and continuation of the same frozen experiment
  after a lost acknowledgement and later campaign guidance.
- The browser removes only its version's local binding, observes unavailable
  status, resolves through the shared command, inspects the receipt and runs a
  three-request experiment. The executable, validation report and production-job
  count remain unchanged. Resolution records zero model calls and zero
  downloaded bytes. The [browser evidence](../runs/consolidation/runtime-portability-20260927/browser-results/runtime-portability-an-exp-3304f-ilding-or-changing-evidence/runtime-resolution-evidence.json)
  and [inspected screenshot](../runs/consolidation/runtime-portability-20260927/browser-results/runtime-portability-an-exp-3304f-ilding-or-changing-evidence/runtime-resolution.png)
  identify the campaign, command, exact runtime and completed experiment.
- Both actual HTTP services were stopped while idle and restarted on ports
  8776/8777. **31 scientific, execution, cost, command and resolution records**
  remained identical. Replaying the original command returned its original
  receipt and created no additional work. See the
  [restart evidence](../runs/consolidation/runtime-portability-20260927/runtime-restart-evidence.json),
  [process identities](../runs/consolidation/runtime-portability-20260927/service-process-restart.json)
  and [repeatable check](../runs/consolidation/runtime-portability-20260927/check-runtime-restart.py).
- The completed source passed **380 Python tests**, **33 browser tests with one
  skipped**, and the frontend production build. The skipped browser scenario
  targets the unchanged live deployment. Reports are
  [Python](../runs/consolidation/runtime-portability-20260927/pytest.xml) and
  [browser](../runs/consolidation/runtime-portability-20260927/browser.xml).
  The [checkpoint](../runs/consolidation/runtime-portability-20260927/checkpoint.json)
  links a 244-file source manifest and the evidence above. Qualification caught
  and fixed native-loader alias loss; a browser screenshot locator was scoped to
  the version card rather than also matching its production job. Final suites
  passed after those corrections.

Commissioning still uses supplied source and mocked semantic review. Runtime
resolution, authentication, numerical execution, workers and recovery are real.
The live deployment and researcher annotations remain unchanged. This checkpoint
is a runtime foundation and copied-library compatibility check, not the portable
result/dependency bundle acceptance gate. C still requires staged bundle
export/import, complete upstream accounting and later-receipt reconciliation,
including resolution overhead. D's remaining command/manager integration, E's
historical migration/rollback and F's real-model qualification/cutover remain.

## Portable evidence bundle foundation — 2026-09-27

C2 is implemented and locally verified. The [bundle workflow](evidence-bundles.md)
exports a result with its declared evidence and captured bytes, verifies it in
another workspace, stages each service's evidence, and publishes through durable
commands. Original record/source identities, objective interpretation, exposure
releases and physical cost intervals remain intact. General asset availability is
a local projection. Missing historical evidence stays associated with its owning
record through re-export.

Historical experiments and production jobs use an explicit archive reader. They
never enter native scientific or implementation queues. Imported executable
source and reports remain inspectable, while the local version requires runtime
resolution and independent revalidation. Import retains known failure/revocation.
Workspace publication admits the library version into its current evidence view
in the same transaction as the imported asset and cost records.

Each service publishes with its own stable receipt. A lost library reply leaves
the workspace effect pending; retry recovers that receipt. A workspace failure
rolls back its entire evidence publication. Repeating an import or importing
overlapping descendants preserves one physical charge for their shared work.

Qualification evidence:

- Nine new Python tests cover a real result with its original directory moved
  away, matching original identities/objectives/lineage/costs, deterministic
  re-export, overlapping imports, two descendants with a shared cost prefix,
  explicit unknown history, protected result releases, conflicting identities,
  malformed archives, missing blob declarations and workspace rollback. The
  executable transfer test recovers a lost library acknowledgement across
  workspace reconstruction, resolves the runtime and revalidates the same
  artifact without replaying its original production jobs. Conflicting reports,
  retained revocation and absent production receipts also have coverage.
- The browser transfers a three-evaluation result from an independently checked,
  supplied evaluator between separate workspaces and libraries on 8778–8781.
  It exports and downloads **21 records and 166 blobs**, verifies that inspection
  publishes no assets/versions, imports the history, compares the original asset
  and attributed costs, and repeats both the command and import. The destination
  has zero native historical experiments/jobs and a `validation_required`
  executable. Recorded provenance is complete; accounting remains partial where
  usage is unknown. See the
  [browser evidence](../runs/consolidation/evidence-bundles-20260927/bundle-browser-final-results/bundles-portable-evidence--a281e-ng-its-historical-producers/bundle-transfer-evidence.json),
  [portable ZIP](../runs/consolidation/evidence-bundles-20260927/bundle-browser-final-results/bundles-portable-evidence--a281e-ng-its-historical-producers/portable-evidence.zip)
  and [inspected screenshot](../runs/consolidation/evidence-bundles-20260927/bundle-browser-final-results/bundles-portable-evidence--a281e-ng-its-historical-producers/imported-evidence.png).
- Both destination HTTP services were stopped while idle and restarted. **722
  workspace records and 28 library records** remained identical, including after
  replaying the original import command. The original receipt, costs and local
  validation-required state survived without starting any historical job. See
  [restart evidence](../runs/consolidation/evidence-bundles-20260927/bundle-restart-evidence.json),
  [process identities](../runs/consolidation/evidence-bundles-20260927/service-process-restart.json)
  and the [repeatable check](../runs/consolidation/evidence-bundles-20260927/check-bundle-restart.py).
  The three new library export/import endpoints also reject unauthenticated
  requests; [HTTP results](../runs/consolidation/evidence-bundles-20260927/library-http-auth.json).
- The final source passed **389 Python tests** and the frontend production build.
  Browser verification covers **34 passing scenarios and one live-deployment
  skip**: 33 regression passes plus the new scenario's focused passing retry.
  The broad run's only failure was an ambiguous locator matching both original
  asset and producer JSON after all transfer/replay assertions had passed. Its
  corrected assertion targets producer history. Original reports remain intact:
  [Python](../runs/consolidation/evidence-bundles-20260927/pytest.xml),
  [broad browser run](../runs/consolidation/evidence-bundles-20260927/browser.xml),
  [focused retry](../runs/consolidation/evidence-bundles-20260927/bundle-browser-final.xml)
  and [scenario-by-scenario verification](../runs/consolidation/evidence-bundles-20260927/browser-verification.json).
  The [checkpoint](../runs/consolidation/evidence-bundles-20260927/checkpoint.json)
  links the 253-file source manifest and all evidence.

Qualification fixed unstable blob media envelopes during re-export, retention of
missing-evidence declarations and immediate admission of published library
evidence into the workspace catalog. Test corrections wait for evidence
publication after worker completion and compare normalized objective records.

C remains open. A [recorded reproducer](../runs/consolidation/evidence-bundles-20260927/accounting-followup.json)
imports two historical cost events with the correct three-call total, then
demonstrates the missing local cursor reconstruction when the same cumulative
receipt is processed. C3 must preserve those events, reconstruct the accounting
position, and append later receipt reconciliations without rebilling the prefix.
Complete model/implementation upstream attribution, runtime-resolution overhead,
explicit reproduction from imported framework captures and legacy runtime
conversion also remain. D's complete command/manager lifecycle, E's historical
imports and rollback, and F's real-model qualification and cutover remain.

Model review is mocked; HTTP transfer, package execution, byte verification,
independent revalidation and service restarts are real. The live deployment on
8765/8766 and the researcher's annotated questions remain unchanged. This is a
local transfer checkpoint, not product qualification or release.

## Accounting reconciliation checkpoint — 2026-09-27

C3 is implemented and locally verified. The [accounting ledger](accounting.md)
keeps original cost events immutable and appends cumulative receipts and scoped
reconciliations. Imported accounting positions are reconstructed from that
evidence, including legacy data without snapshot records. Replaying a receipt
does not rebill its prefix, and later execution adds only its additional work.

Cost projections solve only the totals justified by recorded evidence. An
aggregate can become exact while its individual unknown shares remain unknown.
Contradictory totals and negative implied expenditure are rejected. Producer
work identities and revision checks distinguish more execution from another
measurement of the same work; a conflicting value cannot become a new charge.
An incomplete later message cannot erase an established measurement.

Startup and ongoing reconciliation ingest failed, interrupted and terminal
research/implementation jobs, including late library events after a settled
grant. Conservative reservations remain distinct from measured expenditure.
An imported executable retains its original research and production inputs;
local runtime resolution and independent revalidation add their own costs.
Runtime resolution after experiment preparation is an operational contribution
and leaves its frozen scientific procedure intact.

The asset interface displays every accounting axis and its complete/partial/
unknown basis. The shared `cost.reconcile` command records supporting evidence,
authority and rationale, with revision checks, stable retries and rejection
records. Export includes later local receipts even when its root is an older
imported asset. Inspection checks receipt consistency before publication.

Qualification evidence:

- **15 new accounting tests** cover exact and unknown shares, incompatible
  receipts, legacy cursors, entirely unmeasured attempts, repeated imports,
  re-export after later receipts, conflicting import, startup recovery, late
  terminal usage, stale service replies, command replay/rejection and real
  executable import/revalidation/reuse with original research contributions.
  The existing runtime-recovery test also verifies one overhead charge for an
  already prepared experiment.
- The final source passed **404 Python tests**, **34 browser scenarios with one
  live-deployment skip**, and the frontend production build. The broad browser
  run has no failures. Reports:
  [Python](../runs/consolidation/accounting-reconciliation-20260927/pytest.xml),
  [browser](../runs/consolidation/accounting-reconciliation-20260927/browser.xml).
  An initial successful 403-test/34-scenario run is retained separately; final
  review added the same-work receipt guard and the complete checks were rerun.
- The browser uses two workspaces and separate libraries on 8782–8785. It imports
  a real generated-evaluator result, records a **synthetic fixture receipt of
  123 input tokens**, verifies unchanged asset content and physical event count,
  replays the command, rejects a conflicting total and re-exports the added
  evidence. Unmeasured output tokens and model time remain unknown. See the
  [final browser evidence](../runs/consolidation/accounting-reconciliation-20260927/browser-final-results/bundles-portable-evidence--a281e-ng-its-historical-producers/bundle-transfer-evidence.json)
  and [inspected receipt interface](../runs/consolidation/accounting-reconciliation-20260927/browser-final-results/bundles-portable-evidence--a281e-ng-its-historical-producers/reconciled-cost-evidence.png).
- Both destination HTTP services restarted on the completed source with **642
  workspace records and 21 library records unchanged**. Replaying the import and
  receipt commands preserved their outcomes and costs; no historical job entered
  either queue. The imported executable still requires local validation. See
  [restart evidence](../runs/consolidation/accounting-reconciliation-20260927/restart-evidence.json),
  [process identities](../runs/consolidation/accounting-reconciliation-20260927/accounting-final-restart.json)
  and the [repeatable check](../runs/consolidation/accounting-reconciliation-20260927/check-accounting-restart.py).
- The [checkpoint](../runs/consolidation/accounting-reconciliation-20260927/checkpoint.json)
  links the reports and **296-file source manifest**, verified unchanged after
  the checks. Model review is mocked and the delayed token receipt is synthetic;
  package execution, independent checks, HTTP transfer and service restart are
  real. This is not evidence of a live model bill or optimizer superiority.

Qualification also found that learning a previously missing billing mode could
conflict with an existing immutable asset. The original asset metadata is now
retained; snapshots carry the newly reported billing information and quantities.

C4 remains the next implementation gate. A
[read-only follow-up](../runs/consolidation/accounting-reconciliation-20260927/reproduction-followup.json)
verifies an imported framework capture and matching installed dependencies, but
confirms that no explicit reproduction command exists. Add a linked execution
using that captured evidence without activating the archived original job, and
finish verified legacy runtime relocation.

D1's [mutation inventory](application-command-inventory.md) now identifies the
23 direct HTTP mutation paths, remaining browser callers, direct manager budget
extension and CLI work without workspace command/grant records. These are
remaining implementation tasks. D2's complete manager lifecycle, E's historical
migration/rollback, and F's real-model qualification, researcher validation and
cutover remain open. The live services on 8765/8766 were not changed, and the
researcher's annotated questions retain their original hash.

## Integrated-product planning review after C3 — 2026-09-27

The researcher requested the further development plan with architectural choices
resolved and detail questions deferred to validation. The
[consolidation plan](architecture-consolidation-plan.md#12-further-development-from-the-current-branch)
continues from the verified foundation rather than restarting the original
increments. No further architectural input is required.

This review checked the annotated decisions, both working trees, the sibling's
numerical methods and study controller, the current command/API paths and the
manager, source-dispatch and worker boundaries. All **296 source files** still
match the C3 manifest
`7c5350e00a3b84764bf16a36141b361402ea54fb7155d03d2f61a739b98abdd5`.
The existing XML reports contain **404 passing Python tests** and **34 passing
browser scenarios with one skip**. Those are recorded checks, not fresh test runs
for this documentation update. The annotated questions still hash to
`3d966e43d9b641abdbbe4db855656f26347f2a30586bd68bb8aaf218363a1997`.

The plan now specifies:

- C4's exact archived-snapshot selection, new linked draft/execution, frozen
  comparison and separate reproduction costs. Imported preparation and worker
  execution require isolation; a verified content hash alone does not confer
  trust. Supported legacy relocation appends a verified binding and report.
- D1's connected and local CLI modes, both using the same commands, scheduler,
  grants and durable workspace records. The verified inventory still contains
  **23 direct HTTP mutation paths** requiring translation.
- D2's serialized durable inbox, coalesced events, uncertain-call reconciliation,
  reconstruction of each turn from structured records, stale-guidance checks
  and scoped issues. Model unavailability preserves manual controls and already
  authorized work.
- E's historical import and migration rehearsal, followed by F's final regression,
  bounded real-model workflows, researcher validation and tested cutover.

The review also corrected stale C2 and terminal-reconciliation status references
in the plan. It changed documentation only; C4–F remain implementation and
qualification work. No service was restarted or migrated during this review.

## Imported-source and compiler foundation — 2026-09-27

C4 implementation has started. The new
[source resolver](../src/optimization_framework/assets/captures.py) selects an
exact admitted historical record and reconstructs its code and dependency locks
from verified blobs. The source remains resolvable when the original directory
and import staging tree are unavailable. Historical results, observations and
operational state do not become files available to the compiler.

The new [execution isolation](../src/optimization_framework/execution/isolation.py)
is an explicit mode of captured compiler dispatch. The host selects runtime
mounts and enforces namespaces, read-only source, a clean environment, memory and
file limits, bounded scratch and timeout. It never substitutes ordinary process
execution when isolation is missing. Source verification and runtime identity
checks remain separate from the process permissions.

Six new tests exercise actual isolated execution and blob-based resolution. The
combined imported-execution, bundle and provenance check passed **20 tests**.
They verify captured preparation after installed preparation changes, denied
credential and undeclared-result access, read-only source, host network isolation,
timeout, missing isolation support, bytecode rejection, source recovery without
original/staged paths and explicit rejection of missing or changed snapshots.
No historical job enters the native queue and existing costs remain unchanged.

The checkpoint directory is
[`runs/consolidation/imported-execution-foundation-20260927`](../runs/consolidation/imported-execution-foundation-20260927).
Its 299-file source manifest is
`9d680c40b4193905fef0e813bf9fa54905a15a841d69f90ec3185fce323c9fa0`.
The changes are three new source/test files plus the provenance and history
readers; frontend source is unchanged. The full Python suite passed **410 tests**
with no failures or skips in 393.52 seconds. The
[test report](../runs/consolidation/imported-execution-foundation-20260927/pytest.xml)
and [checkpoint](../runs/consolidation/imported-execution-foundation-20260927/checkpoint.json)
record that result; all 299 source files were rechecked unchanged afterward.
No new browser or live-model qualification is claimed by this foundation.

A separate [probe of the older C3 imported capture](../runs/consolidation/imported-execution-foundation-20260927/c3-capture-probe.json)
ran its actual `trial.prepare` compiler under isolation and exactly matched the
original resolved algorithm configuration, training, completion and diagnostics.
The source remained unchanged. This exercised compatibility with a pre-existing
capture for a generated evaluator, without launching numerical work or allocating
campaign resources.

Further source inspection found a necessary numerical-host boundary: existing
progress, log, artifact, journal and diagnostic readers trust worker-created
local paths. A namespace alone would still permit an imported worker to create
paths that those host readers follow. The
[C4 handoff](imported-reproduction.md#next-integration-boundary) therefore places
the imported worker in a private attempt tree and requires host-owned leases,
supervision and validated output publication before connecting launch. This is
implementation work within the chosen architecture, not a request for another
researcher decision.

The explicit reproduction draft/command/browser flow, isolated numerical host,
generated-package nesting, verified legacy conversion, comparison and new-work
accounting remain C4 requirements. D–F remain open. The live 8765/8766 service
processes were inspected and left unchanged; annotated researcher comments retain
their original hash.

## Further-development planning recheck — 2026-09-27

The [development plan](architecture-consolidation-plan.md#next-implementation-deliveries)
now breaks the remaining work into supervised execution, explicit reproduction,
shared commands, the durable manager lifecycle, history/migration and final
qualification. It specifies code boundaries and completion evidence for each.
The researcher's comments and product clarification are sufficient; no further
architectural answer is required before development continues.

The current tree contains numerical-host and publication work beyond the
410-test source/compiler checkpoint. Running
`.venv/bin/python -m pytest tests/test_framework_isolated_host.py -q --tb=short`
during this review produced **6 passed, 1 failed** in 7.19 seconds. The generated
evaluator fails because its package process cannot create a namespace inside the
captured worker's sandbox. The earlier full-suite pass does not qualify these
new changes.

The chosen execution design puts package launch in the installed supervisor:
only the frozen experiment's declared optimizer/evaluator packages can launch,
in separate sandboxes with a bounded protocol relay. The host owns process
identity, deadlines, shutdown and receipts; imported code cannot choose arbitrary
commands or mounts. The [C4 handoff](imported-reproduction.md#numerical-host-work-in-progress)
records this decision and the remaining supervision, publication, recovery and
accounting work. It does not claim the failing path is repaired.

This planning review changed documentation only. No live service was restarted
or migrated, and the annotated questions retain SHA-256
`3d966e43d9b641abdbbe4db855656f26347f2a30586bd68bb8aaf218363a1997`.

## Imported numerical and package host foundation — 2026-09-27

The generated-evaluator namespace failure from the planning recheck is fixed.
The installed [package host](../src/optimization_framework/execution/package_host.py)
launches only verified frozen optimizer/evaluator invocations as separate
sandboxes. Its [standalone client](../src/optimization_framework/execution/package_client.py)
preserves the captured package protocol by relaying three pipes through a private
Unix socket. No host file or directory descriptor crosses that boundary. Package
code cannot reach the socket or the surrounding workspace. The original captured
scientific source and the host's namespace policy remain unchanged.

The [numerical supervisor](../src/optimization_framework/execution/isolated_host.py)
owns the lease, deadlines and receipt outside the captured worker's writable
tree. A separate guard thread enforces deadlines independently of publication.
The [publisher](../src/optimization_framework/execution/publication.py) validates
regular files and blob identities before exposing them to ordinary evidence
readers; changed or removed committed journals, external artifact mappings,
symlinks, hardlinks and special files are rejected. These modules are not yet
connected to the product scheduler's imported-execution lifecycle.

Twelve isolated-host cases cover actual built-in and generated execution,
generated optimizer and evaluator packages together, checkpoint transport above
16 MiB, cooperative pause and restoration in a second attempt, modified launch
requests, client-disconnection cleanup, mismatched host leases and independent
deadline enforcement. The host/package/imported-compiler group passed 22 checks;
the final full suite also includes the journal-removal check added afterward.

The checkpoint is
[`runs/consolidation/imported-package-host-20260927`](../runs/consolidation/imported-package-host-20260927).
Its [source manifest](../runs/consolidation/imported-package-host-20260927/source-manifest.json)
contains **304 files**, with SHA-256
`75218f7f95a9bc79b6dc504424313a112a8646c4f7717da6ad2e6381b6540a76`.
The [full Python report](../runs/consolidation/imported-package-host-20260927/pytest.xml)
records **422 passed, zero failed or skipped**, in 412.25 seconds. All source
hashes were rechecked unchanged afterward. The
[checkpoint record](../runs/consolidation/imported-package-host-20260927/checkpoint.json)
states the remaining gates. Frontend source is unchanged; no new browser or
real-model qualification is claimed. Semantic reviewers are fixtures, while the
package processes, checkpoints, isolation and numerical workers are real.

The separate [older-capture probe](../runs/consolidation/imported-package-host-20260927/older-capture-probe.json)
uses an actual archived C3 worker whose package launcher predates the current
helper. In another experiment directory, its original source and a copied,
verified evaluator runtime produce all three original candidate/objective
observations exactly. It has a new experiment identity, completes its scientific
criterion in approximately 2.50 host seconds, and leaves the original source and
specification unchanged. It calls no model and allocates no native campaign.
This is direct compatibility evidence; it does not qualify the missing product
command, scheduler accounting or legacy runtime converter.

Continue C4 with aggregate private-storage enforcement, trusted host adoption and
controls through the common scheduler, interruption reconciliation, C3 cost
receipts, inherited isolation for diagnostics, and the draft/command/comparison
and browser workflow. Per-file and publication allowances do not yet constrain
all unreferenced private output. C4.1 and C4.2 remain open, followed by D–F. The
live services on 8765/8766 still have PIDs 74044/74041 with their original start
times; neither was restarted or migrated. The annotated researcher comments
retain their original hash.

## Further-development plan and storage recheck — 2026-09-27

The [plan](architecture-consolidation-plan.md#next-implementation-deliveries)
retains this branch as the integration base and resolves the remaining ownership
and recovery decisions. No further researcher input is needed before development.
The next delivery qualifies the current host foundation and connects its frozen
policy, trusted leases, controls and receipts to the common scheduler. Explicit
historical reproduction, the remaining shared commands, durable manager turns,
history migration and final product qualification follow with separate
acceptance gates.

The current working tree already contains private-storage bounds, cumulative
publication accounting, trusted startup and evidence-only reconciliation after
supervisor failure. A fresh run of
`.venv/bin/python -m pytest tests/test_framework_isolated_host.py tests/test_framework_imported_execution.py tests/test_framework_packages.py -q --tb=short`
passed **28 tests** in **43.43 seconds**, with one dependency deprecation warning.
This is focused verification of the current source, not a replacement for the
422-test checkpoint. A new full-suite run and older-capture probe remain before
qualifying these later changes as a complete foundation.

The plan now makes host publication the durable commit boundary. Private output
lost after a crash remains unknown work; reconciliation preserves the original
attempt and resume creates a new one. Frozen host policy propagates to dependent
diagnostics. Kernel byte limits, observed entry-count enforcement and cumulative
publication allowances are distinct controls; capacity failures cannot silently
erase evidence or change scientific completion.

This planning review changed documentation only and ran the focused checks.
It did not restart or migrate the live services on 8765/8766 or modify the
researcher's annotated questions. Product reproduction, complete command/manager
integration, historical migration and real-model acceptance remain unfinished.

## Imported scheduler and storage checkpoint — 2026-09-27

The isolated host now runs through the ordinary workspace scheduler. A typed
host policy is pinned in the immutable experiment specification; mutable trial
edits cannot downgrade it. Captured-source admission selects isolation, and
source-derived diagnostics retain that requirement for preparation, compilation
and execution. The installed supervisor has a clean environment and a separate
working directory. Workspace reconstruction adopts its matching host lease,
ignoring worker-written namespace process identities.

The storage increment adds a kernel byte limit for the private attempt volume,
an entry-count watchdog with recorded overshoot, bounded scratch/shared memory
and a cumulative publication allowance covering metadata and replacement copies.
An installed prelude obtains the private directory descriptor before captured
imports run. Only validated publication enters the durable experiment view;
private output lost with a supervisor remains unknown evidence.

Pause, stop and resume use the ordinary controls. Uncooperative controls have an
independent host deadline. Reconciliation after supervisor death retains its
original identity and published checkpoint without executing captured code or
requiring that source to be available. Resume creates another attempt. Resource
admission retains conservative bounds for uncertain duration, separately from
the measured-cost ledger.

Isolated attempts group requests, worker overhead, supervisor overhead and any
unconfirmed suffix into one cost source. C3 receipts can establish a measured
whole-attempt total without inventing its unknown internal shares. Generated
package time is not charged again. Executable-production costs remain upstream,
including their unknown quantities. Host receipts survive repeated ingestion
and join the exported evidence graph.

Nine scheduler integration cases cover actual built-in execution, both generated
executable kinds, inherited diagnostic execution, policy tampering, reconstruction
and host adoption, cooperative pause/resume, supervisor loss with missing source,
forced stop, immutable cost replay and exported host receipts. The combined
scheduler/accounting/diagnostic/resource group passed **36 tests** before the
final two integration cases; the final full suite includes those additions.

The checkpoint is
[`runs/consolidation/imported-scheduler-20260927`](../runs/consolidation/imported-scheduler-20260927).
Its [source manifest](../runs/consolidation/imported-scheduler-20260927/source-manifest.json)
contains **310 files**, with SHA-256
`ce972758be307830f1b04f33eb1af15c07f66a92bbd9c222a85b0910b99408e1`.
The [full Python report](../runs/consolidation/imported-scheduler-20260927/pytest.xml)
records **437 passed, zero failed, errored or skipped**, in **469.29 seconds**.
There is one dependency deprecation warning. All source hashes were rechecked
unchanged after the suite and both probes. The
[checkpoint record](../runs/consolidation/imported-scheduler-20260927/checkpoint.json)
retains the exact verification scope and remaining gates.

The [older-capture probe](../runs/consolidation/imported-scheduler-20260927/older-capture-probe.json)
again runs the actual C3 archived worker with its original inline package launcher
and a copied evaluator runtime. Its three observations exactly match the original
trajectory, completing in approximately 2.47 host seconds without changing the
original source or specification. It allocates no native campaign and remains
direct compatibility evidence.

A separate [recovery probe](../runs/consolidation/imported-scheduler-20260927/crash-resume-probe.json)
kills a real supervisor, reconciles while captured source is unavailable, restores
that source and resumes from the published checkpoint. Replay exhausts the original
100-request allowance with scientific completion still false. A recorded test
budget amendment to 110 permits completion in a third attempt; the frozen
procedure remains unchanged. Earlier unknown work remains unknown in the final
costs. This distinguishes a finished process from a completed scientific
procedure and demonstrates that recovery does not silently extend an allowance.

Package reviews are fixtures and executable source is supplied; numerical jobs,
package processes, isolation, controls and workspace reconstruction are real.
There are no new model calls or browser qualification claims. The public
historical-reproduction draft/command/comparison workflow, supported legacy
runtime conversion, complete cross-directory product scenario and D–F remain
open. The live services on 8765/8766 retain PIDs 74044/74041 and their original
start times. Neither was restarted or migrated, and the annotated researcher
questions retain their original hash.

## Historical reproduction checkpoint — 2026-09-27

C4 now exposes a complete local reproduction workflow. The asset library selects
an exact archived snapshot admitted to the campaign. Shared commands save a
linked draft, retain its scientific procedure, expose missing requirements and
launch one new exploratory experiment through the common supervised scheduler.
The comparison rule and local executable bindings freeze with that experiment.
Historical jobs remain archived; protected cohort evidence requires its original
release and executable eligibility is checked for the new use.

Committed attempt evidence creates an immutable agreement, disagreement or
inconclusive comparison. Incomplete science remains inconclusive after a normal
process exit. A later recovery attempt can append agreement without erasing that
earlier assessment. The rule compares the best primary objective and optionally
its candidate; it establishes neither trajectory equality, superiority nor
independent evaluator correctness.

Original optimizer inputs require explicit destination reuse decisions. New
execution costs enter the new attempt; the old result's reference alone does not
charge its execution as an input contribution. Actual reused inputs and executable
production retain their upstream costs. Export includes the exact historical
reference closure and comparison, and manager context receives source metadata
and visible assessments.

Supported legacy runtime conversion verifies the recorded interpreter, dependency
files and native content, preserving the original artifact/version/runtime
identities. It appends a verified local binding and conversion report. The old
schema did not hash the standard library, so its historical byte equivalence
remains unknown and appears in readiness and comparison evidence. The selected
local standard library is now pinned; changing that conversion after freezing
blocks launch. A generated evaluator test performs resolution, independent local
revalidation and captured execution using a representative schema-1 manifest.
This is separate from the earlier actual C3 captured-worker compatibility probe.

The checkpoint is
[`runs/consolidation/historical-reproduction-20260927`](../runs/consolidation/historical-reproduction-20260927).
Its [source manifest](../runs/consolidation/historical-reproduction-20260927/source-manifest.json)
contains **316 files**, with SHA-256
`519ee664648f2c2ed1f3bbe8056d479ea7ef67147d264cb1b36302eed71759f0`.
The [full Python report](../runs/consolidation/historical-reproduction-20260927/pytest.xml)
records **449 passed**, zero failed, errored or skipped, in **529.65 seconds**
(529.88 seconds in pytest's console summary), with one dependency deprecation
warning. All source hashes were rechecked unchanged afterward. The frontend
production build passed during the increment.

The [browser run](../runs/consolidation/historical-reproduction-20260927/browser.log)
passed **19 scenarios** in **21.0 seconds**. It includes an actual archive import,
reproduction draft edit, accepted launch with a lost response, successful retry
and agreement from one numerical experiment while the original directory is
unavailable. Seventeen scenarios needing other fixture setups were skipped;
this run does not replace their earlier checkpoint evidence. The
[browser evidence](../runs/consolidation/historical-reproduction-20260927/browser-final/reproduction-an-exact-impo-20139-on-and-a-recoverable-launch/reproduction-evidence.json)
and screenshots retain the final workflow.

A [separate restart probe](../runs/consolidation/historical-reproduction-20260927/restart-after.json)
restarts both isolated HTTP services with **66 scientific and command records
unchanged**. Replaying that scenario's accepted launch command creates no new
execution or cost event. The [checkpoint record](../runs/consolidation/historical-reproduction-20260927/checkpoint.json)
links its distinct browser evidence and records all qualification limits.
Executable code is supplied and model reviews are fixtures; numerical/package
processes, isolation, HTTP and browser interactions are real. No new model calls
were made.

C4 is locally verified. The [development plan](architecture-consolidation-plan.md)
now starts the remaining work with D1's 23 direct mutation routes, remaining
browser/manager controls and CLI lifecycle. D2 completes the durable manager,
E handles full historical migration and rollback, and F owns combined product,
real-model, researcher and release qualification. Architecture is resolved;
researcher input can wait for working validation scenarios.

The live services on 8765/8766 retain PIDs 74044/74041 and their original start
times; neither was restarted or migrated. The shared frontend build was refreshed
during this increment. The annotated questions retain their original hash.

## Campaign and context command checkpoint — 2026-09-27

The first D1 increment translates campaign creation/update, context editing and
issue resolution into the common application commands. Their browser controls
use those commands directly; the old HTTP shapes translate through the same
authority and revision checks. The [command inventory](application-command-inventory.md)
now identifies **19 remaining direct mutation routes**. Manager controls,
feedback, trial/validation compatibility routes, sources and the CLI remain D1
work; this checkpoint does not close that package.

Creation requests name a stable campaign identity and require revision zero.
Campaign records, tasks, hypotheses, the initial frozen study, the accepted
command and pending text projection commit together. A collision cannot replace
an existing campaign, and an accepted retry retains its original outcome after
later charter changes. Resource changes append an immutable campaign amendment;
scientific changes create a linked study while earlier experiment specifications
remain unchanged.

Context edits check the edited context revision. Issue choices check both
campaign scope and the issue revision, and invalidate stale manager guidance.
These four controls require researcher authority. Typed payloads cannot grant
themselves that authority. Markdown projections run through the outbox after the
database commit; rollback cannot expose uncommitted guidance, and restart can
finish an interrupted projection. Missing projections are rebuildable.

The compatibility translator accepts idempotency and explicit revision headers.
Old clients without those headers act against the state observed at admission;
an unidentified retry cannot acquire a guarantee retrospectively. Browser
controls retain the complete original envelope after an uncertain response,
including its earlier campaign revision even if a background refresh sees a
newer one. Stale edits remain available in the editor for the researcher to
review rather than overwriting current guidance.

The checkpoint is
[`runs/consolidation/campaign-controls-20260927`](../runs/consolidation/campaign-controls-20260927).
Its [source manifest](../runs/consolidation/campaign-controls-20260927/source-manifest.json)
contains **319 files**, with SHA-256
`61ee1b2104238af1dfe614e1fcc6846da92e54c5f7829ac1796c800d858168db`.
The [full Python report](../runs/consolidation/campaign-controls-20260927/pytest.xml)
records **460 passed**, zero failed, errored or skipped, in **532.21 seconds**
(532.31 seconds in pytest's console summary), with one dependency deprecation
warning. All source hashes and the annotated researcher comments were rechecked
unchanged after verification. The [checkpoint record](../runs/consolidation/campaign-controls-20260927/checkpoint.json)
retains the exact scope and the remaining D–F gates.

Focused backend verification passed **40 tests** across command, campaign,
manager and existing API cases. The existing mocked browser group passed
**18 scenarios**. An actual isolated HTTP/browser scenario loses accepted
creation, charter and context replies, observes concurrent changes, then retries
without duplicate work. It rejects a stale edit and records one manager issue.
The combined [browser run](../runs/consolidation/campaign-controls-20260927/browser.log)
passed **20 scenarios** in **24.5 seconds**, including historical reproduction
against the current backend and frontend. Seventeen other fixture-dependent
scenarios were skipped; their earlier evidence retains its own source scope.

Both isolated services restarted with **118 captured scientific and control
records unchanged**. Replaying five accepted commands preserves campaign,
guidance and issue choices, adds no execution or cost event, and retains the
historical reproduction's agreement. The
[restart probe](../runs/consolidation/campaign-controls-20260927/restart-after.json)
and [browser artifacts](../runs/consolidation/campaign-controls-20260927/browser-final)
record the actual cases. No real model calls were made.

Qualification now supplies its own frontend directory. The new build is served
only by the isolated fixture on 8789. After an initial shared build, the preceding
frontend was reconstructed from source whose 20 files matched the C4 manifest,
restoring its `index-BQkJ7P5G.js` output. The live 8765/8766 services were not
restarted or migrated. Future qualification builds can stay separate from their
dashboard assets.


## Experiment, idea and implementation command checkpoint — 2026-09-27

D1.1 and D1.2 are implemented and locally verified. The five study/experiment
routes and seven idea/implementation routes now translate to the shared command
service. The [inventory](application-command-inventory.md) records 16 translated
compatibility mutations and seven remaining direct research/decision/source routes.
Connected/local CLI work, the complete durable manager, historical migration and
final release remain D1.3–F work. The shorter
[status document](current-development-status.md) and
[acceptance matrix](consolidation-acceptance-matrix.md) are the review entry points.

Trial controls check the target revision, retain frozen procedures and schedules,
and deliver worker intent after commit. Legacy validation uses the parent's
captured compiler; accepted retries retain the original subject selection and
reply snapshot. Idea creation retains researcher origin, parent scope and exact
implementation preparation. Comments append once and independently of a model
request; status revisions preserve newer researcher intent and frozen finalist
identity. Both update manager guidance through the committed projection path.
Legacy implementation routes preserve service keys and prior allocations, pin the
original source conversion on retry, and retain durable attachment/control effects.
Asynchronous compatibility replies expose current resources alongside fixed
command receipts.

The browser saves pending requests in a per-workspace journal before dispatch.
Reload recovery checks accepted outcomes before offering an original-envelope
retry. Transient lookup failures retain the record; workspace identity prevents
replay to a different workspace at the same address. Cases cover lost accepted and
unaccepted replies, a newer control in another session, and saved feedback without
model calls. The full browser qualification also exposed and corrected obsolete
test observers and a missing charter-history control in the general problem view.
That view now shows generic problem definitions, fidelity, evaluator declarations
and implementation budgets.

The checkpoint is
[`runs/consolidation/experiment-controls-20260927`](../runs/consolidation/experiment-controls-20260927).
Its [source manifest](../runs/consolidation/experiment-controls-20260927/source-manifest.json)
contains **325 files**, with SHA-256
`f7b5b604a8ae653b37d668123e0d3d74f13e05f0246891671d4b110c940bf338`.
The [Python report](../runs/consolidation/experiment-controls-20260927/pytest.xml)
records **477 passed**, no failures/errors/skips, in **539.09 seconds**. The backend
source remained unchanged; the later charter-history fix changes only the
frontend. Its final isolated build, **index-CN4ReTS2.js**, passed the complete
[browser run](../runs/consolidation/experiment-controls-20260927/browser-complete.log):
**40 passed**, no failures or skips, in **7.4m**.
These are 18 mocked UI cases and 22 actual service/browser workflows; semantic
reviews remain fixtures and no real model calls were made. All final source
hashes and the researcher's annotated questions were rechecked unchanged.
Earlier failed test artifacts remain available with the corrected final results.

Both review-fixture services restarted with **804 authoritative records unchanged**.
Replaying **76 commands** preserved guidance in **15 campaigns** and created no
executions, cost events, grants or comments. Derived projections/caches/cursors
are excluded from the record hash comparison. The
[restart report](../runs/consolidation/experiment-controls-20260927/restart-after.json),
[verified source archive](../runs/consolidation/experiment-controls-20260927/verified-source.tar.gz)
and integrity-checked database copies make the checkpoint inspectable.
The source review also records a remaining D2 edge case—reconciling an accepted
implementation control after a lost reply and later guidance change—in the
[resume notes](../runs/consolidation/experiment-controls-20260927/resume-notes.md).

The new interface is isolated on 8791. Additional browser fixtures used 8793/8795
for both-workspace workflows and were stopped after all jobs and deliveries settled. Original live services 8765/8766 retain
their earlier processes and the shared **index-BQkJ7P5G.js** frontend; neither was
restarted or migrated. This checkpoint leaves seven direct mutation routes,
CLI integration, durable campaign-event consumption, source-specific historical
migration, real-model qualification and researcher acceptance unfinished.

## Research, CLI and durable manager checkpoint — 2026-09-28

D1.3, D1.4 and D2 are implemented and locally qualified. All 23 compatibility
mutations and browser mutations, apart from immutable byte staging, now use the
shared command boundary. The [inventory](application-command-inventory.md)
records their owning commands. E's complete source-specific historical imports
and migration rehearsal, and F's real-model, researcher and release acceptance
remain open. The original two-service architecture and general harness objective
are unchanged.

Research controls and decision choices check target revisions. Accepted decisions
retain their original child commands before delivery, so a lost acknowledgement
and later guidance change cannot duplicate an approved action. Source retrieval
runs outside the outbox's global lock, saves immutable retrieval receipts, and
exposes pending or failed delivery. Research feedback remains saved independently
of model availability. Missing provider configuration retains requests in the
campaign queue with one explanatory manager issue.

The local and connected numerical CLI now creates common campaign records,
reservations, attempts and cost events. A local session owns the scheduler lease;
a connected session retains requests and receipts without opening the server's
database. Legacy output files are projections of verified portable evidence.
Inference is a separate recorded allocation, and convergence checks use captured
code. Old standalone runs enter through retrospective import before new work;
missing historical costs and exposure remain unknown. Pickle-only historical
policies stay archived. This importer is a CLI compatibility path, not the full
two-repository migration required by E. See the [CLI guide](cli-research.md).

Campaign managers now consume a durable inbox with a separate event cursor and
serialized turns. Stable input identities prevent duplicate completion handling.
Repeated identical invalid proposals cannot create an unlimited reaction loop.
Completed model output is saved before its messages, findings and proposed
actions commit together. Action delivery reconciles accepted receipts first.
Stale output remains inspectable and causes reconsideration using current
guidance. A possibly dispatched external call preserves uncertain usage and
blocks later model work until reconciliation. Shutdown retains ownership while
returning model/source writers save their receipts.

Versioned JSON, JSONL and Markdown exports retain structured campaign history.
Model context has explicit size limits and retrieves relevant older evidence
while retaining current constraints, issues and selected work. Findings can be
published with declared problem scope, limitations and evidence; counterevidence
links back to earlier claims. Another campaign must record reference access
before using those findings. Finding publication does not silently endorse a
claim. Implementation-control recovery queries the service's accepted receipt
before checking newer guidance, and supplied package dictionaries are interpreted
according to the commissioned executable kind.

The [checkpoint record](../runs/consolidation/manager-lifecycle-20260928/checkpoint.json)
links the final [Python report](../runs/consolidation/manager-lifecycle-20260928/pytest-stable.xml),
source/runtime manifests, source archive and integrity-checked database copies.
The final suite records **522 passed**, no failures/errors/skips, in **571.34
seconds**; before/after package and lock manifests match. One dependency
deprecation warning remains. The isolated frontend build is **index-BH0TUqoH.js**. Eighteen mocked browser
cases and one actual HTTP/browser research/decision/source recovery case passed.
The prior 40-browser checkpoint retains its original source scope; final combined
product qualification remains F.

The [two-service manager scenario](../runs/consolidation/manager-lifecycle-20260928/restart-http-final/qualification.json)
passed through two library starts and four workspace starts. Eleven manager
turns and fourteen inputs retain two implementation grants, two numerical
experiments and one scoped issue. The scenario commissions missing code while
the workspace is offline, attaches completion after restart, launches the
original draft once, ignores duplicate completion, continues independent work
around a failed implementation check, reconsiders changed guidance and closes an
uncertain provider reservation without replay. The invalid package's actual
failed independent check is retained; its fixture cannot repair it.

[Connected CLI replay](../runs/consolidation/manager-lifecycle-20260928/connected-restart.json)
reconciles ten accepted commands after a server restart with four trials, four
attempts and eighteen cost events unchanged. The
[SIGINT/resume scenario](../runs/consolidation/cli-controls-20260928/interruption-3/qualification.json)
preserves the observation prefix, resumes into another output directory after a
charter edit, and exactly matches uninterrupted DQN results. Earlier failures
exposed and corrected resumed export identity, wall-limit canonicalization,
invalid-proposal reaction loops and indefinitely pending rejected CLI exports.

No real model calls were made. Manager results and semantic implementation
reviews are explicit fixtures; numerical workers, independent package checks,
commands, HTTP, accounting and restarts are real. One full regression run overlapped
an environment refresh and correctly rejected runtime changes. That evidence is
retained; the final run records a stable complete locked environment. Installation
instructions now include the optional numerical dependencies.

All temporary services created for this increment were stopped after their
evidence was retained. The managed review deployment on 8791, its library on
8790 and Tailnet route remain healthy with their earlier processes, data and
frontend. They now have Codex enabled by their existing configuration. Original
services on 8765/8766 were not restarted or migrated. This increment's code and
frontend have not been cut over to either live deployment.
