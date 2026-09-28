# Implementation evidence and remaining validation

This map follows the eight sections of the [revised plan](agentic-algorithm-discovery-plan.md). It distinguishes implemented capabilities, the scope of their tests, and scientific or operational claims that still require evidence. Presence of a test is not recorded here as proof that it passed on every host.

The newer two-repository consolidation is governed by the
[consolidation plan](architecture-consolidation-plan.md), with checkpoint evidence
in [consolidation progress](consolidation-progress.md) and the remaining mutations
in the [command inventory](application-command-inventory.md). The dated entries
below describe earlier implementations; they do not establish completion of the
integrated product or its durable manager lifecycle. The live installation remains
on its earlier source while new controls are qualified in isolated workspaces.

## Implementation library and campaign manager update: 2026-09-24

The [implementation service](implementation-service.md) now owns bounded build/repair jobs, protected behavior checks, independent semantic review, exact dependency runtimes, immutable package versions, and shared local reuse. The workbench displays backend readiness with reasons, consumes pinned versions, and keeps correctness validation separate from measured performance. Built-ins retain bundled regression coverage; historical custom-source protocol checks are not upgraded into new validation claims.

The campaign manager has a durable serialized inbox, deduplicated exception issues, versioned editable Markdown guidance, typed findings/decisions, provenance, and full-text retrieval. Context survives restarts and histories longer than the conversation window. Locked observations and derived records are excluded. Researcher guidance changes invalidate stale actions. Implementation compute is separately allocated; API spend remains shared and subscription calls remain distinct.

Verification: the full suite passed **193 Python tests**; a further protected test-planning workflow test also passed (**194 total**). **18 mocked browser tests**, the production frontend build, and **one real browser/API/MEENT workflow** passed. A read-only check of the deployed current campaign also verified all three missing implementation states, the request action, memory editor, and library view with no browser errors. Follow-up accounting and dependency-identity checks passed **58 focused tests**. The live Codex `gpt-6-sol` reference build passed protected checks and independent review in **two subscription calls**, with **zero paid API spend**, **12,831 input tokens**, **1,141 output tokens**, and **32.3 seconds** of implementation time. Its validation includes four actual MEENT evaluations. The acceptance artifact is local at `runs/acceptance/implementation-service-20260924/acceptance.json`. This proves bounded transport and the implementation workflow for a small reference algorithm, not the three unimplemented research proposals or their performance.

The additive migration was dry-run and then applied to the current campaign after a SQLite backup at `runs/backups/workspace-before-manager-20260924.sqlite3`. The exact historical-record digest was preserved. The updated workspace and independent implementation service are running locally on ports 8765 and 8766, and their authenticated connection was verified. Fourier and portfolio remain unimplemented; relaxed gradients additionally require evaluator extensions. The broader scientific knowledge library and researcher-outcome studies below remain separate work.

## Newly planned extensions: 2026-09-23

The broader feature request initially updated the plan only. A subsequent live-testing request implements the individual idea feedback loop described below; the remaining extensions are still planned.

| Requested extension | Existing foundation | Work still planned |
|---|---|---|
| Researcher assessment drives repeated idea generation | Saved comments, direct comment-driven revisions, frozen feedback context, linked descendants with change explanations, and visible agent critiques | Structured batch dispositions, broader kill/revival scope, workspace-wide feedback briefs, and stale-decision dispatch checks |
| Learn from individual and comparative finalist evaluations | Numerical comparisons, validation artifacts, and research summaries | Method–problem assessments, measured problem-characteristic profiles, competing explanations and diagnostic proposals, discussion drafts, and reviewed promotion of conditional findings |
| Reuse learned knowledge and repeated background sources | Campaign source records and source material supplied to research calls | Canonical source/version management, claim-level notes and findings, retrieval before search, contradiction/correction propagation, and linked-workspace collections with inherited confirmation exposure |

Internet publication, automatic sharing, and community synchronization remain deferred. General scientific knowledge sharing remains planned; known implementation development and validation exposure now follows reused package versions across local workspaces. The [plan's delivery increments](agentic-algorithm-discovery-plan.md#8-implementation-increments-and-acceptance-criteria) define acceptance checks for these additions.

## Individual idea feedback update

An idea now distinguishes saving a researcher comment, requesting an agent critique, and revising with saved feedback. Revision requests capture exact saved researcher comments and the selected parent in their context snapshot; every reasoning role receives that context. Generated descendants retain those links and fields for what changed and how feedback was addressed. Agent assessments appear on the original idea, while its scientific proposal remains intact. Targeted critique cannot create new hypotheses. Targeted critique and revision do not automatically launch numerical trials even in delegated mode.

The implementation rejects feedback from another idea, cross-campaign targets, and revisions of archived ideas. Comments remain saved when model setup or a later request fails. This is a traceable individual revision loop; it does not establish that model revisions are scientifically better, or complete the broader batch-feedback and knowledge-library plan.

Validation: the full Python suite passed **176 tests**, and an additional API/coordinator/engine integration test passed two consecutive feedback-driven revision rounds plus a critique-only request. All **14 mocked browser tests** and the production frontend build passed. These checks use fake model responses; this update makes no new claim about live model reasoning quality.

## Provider configuration update

The current default is **Codex `gpt-6-sol` for all research roles**, with execution disabled pending researcher configuration. The provider adapter preserves structured research records and checkpoints, distinguishes subscription usage from paid API estimates, and does not fall back to `.key`. The dashboard labels deferred setup, subscription calls, and API budgets separately. No Codex subscription login or live model call was performed by this update; the historical API checks below describe the earlier explicit API configuration.

The updated full Python suite passed **168 tests with no skips**. Nineteen fake-process transport tests and fifteen provider integration tests cover cancellation, bounded output/runtime, authentication rejection, deferred activation, model selection, accounting, and no paid fallback. The final transport compatibility changes also passed 43 focused transport/provider/API checks. Installed Codex 0.155.1 accepted the strict configuration in a network-disabled namespace with empty authentication. This verified startup configuration; live GPT6-sol inference was deliberately left for researcher configuration. The production build and all seven mocked browser tests passed. At that check, the restarted local service reported `enabled=false`, `configured=false`, and `model=gpt-6-sol`, with the existing campaign preserved.

## Verification recorded during implementation

Verification performed on 2026-09-23 includes the full Python suite, a production frontend build, mocked browser interactions, and a browser scenario using the real local API and MEENT workers:

```bash
uv run pytest -q
cd frontend
npm run build
npm run test:e2e
npm run test:live
```

The full Python suite passed with **134 tests and no skips**, including the real bubblewrap isolation tests on this host. It exercises real MEENT optimization/resume/validation, concurrent service controls, API decisions, source protocol isolation, research orchestration, and numerical comparison semantics. The production build and both browser test scopes passed. `test:live` uses a separate running local service with model calls disabled; it creates a campaign, reloads the browser while two baseline jobs advance without duplication, stops them through the dashboard, validates a design, inspects comparisons, and opens charter history. No optical optimizer performance claim follows from these software checks.

Under the earlier explicit API configuration, a bounded live **GPT-6-luna Responses API smoke check** completed successfully: one call, 1,577 input tokens, 197 output tokens, and an estimated $0.0002562 at the configured standard rates. A live generation discussion used three calls, costing an estimated $0.0031879, and produced two custom-source strategy dossiers plus assumption review before an explicit reasoning-budget stop. These verify actual model transport, structured multi-role output, cost accounting, and bounded stopping. They do not establish the candidates' scientific usefulness or novelty.

One of those live-generated candidates then passed isolated protocol verification, completed 16 real MEENT evaluations, and submitted two archived designs for validation at Fourier orders 5, 7, and 9. The validator recorded both a passing last-two-order diagnostic and an unconverged design. Local evidence is retained in `runs/acceptance/live-generation.json`, `runs/acceptance/generated-acceptance.json`, and `runs/acceptance/generated-optimizer/`; these generated artifacts are ignored by Git. The total estimated model cost for the four live calls was **$0.0034441**.

Live DOI ingestion also retrieved the supplied Co-Scientist paper's title and metadata successfully. Metadata retrieval is not verification of the paper's scientific claims.

After the held-out audit, `uv run pytest tests/test_workspace_confirmation.py tests/test_workspace_api.py -q` passed. The new regressions cover exposure cohorts, renamed/revised/fidelity-changed conditions, development-to-test relabeling, complete DQN protocol freezing, re-nomination, source/dependency changes, and queued snapshot integrity.

## Plan-to-implementation map

| Plan section | Implemented surface and mechanism | Evidence and limits |
|---|---|---|
| **1. Purpose and operating principles** | Dashboard connects hypotheses, measured trials, decisions, and conversation. Dossiers distinguish origin and claim level; curated and model-backed outputs have different labels. Baselines and new strategies coexist. | Research tests verify honest curated mode, researcher ideas, parent preservation, and rationale/evidence separation. Whether the partnership improves research outcomes is an unperformed study. |
| **2. Interactive problem formulation** | Campaign editor records objective prose, physical task JSON, splits, budgets, reserve, and autonomy. Charter revisions preserve scientific snapshots. Physical evaluation remains the binary +1 transmitted-efficiency problem. | Models validate physics and allocations. Frontend test covers charter revisions. New numerical objectives, fabrication constraints beyond existing binary geometry, and shared-device broadband objectives are not implemented merely by editing prose. |
| **3. Harness and architecture** | FastAPI, React/TypeScript, SSE, SQLite, process workers, immutable code snapshots, and an independent trusted evaluator. LangGraph routes substantive research roles; JSON checkpoints retain completed role results. | Research checkpoint, actual worker/service restart, concurrent cancellation, and browser reload during two running jobs passed. Framework optimality has not been benchmarked. |
| **4. Collaborative algorithm design** | Independent combinatorial/statistical generators; problem, literature, assumption, comparison, diversity, evolution, experiment-design, and synthesis roles. Dynamic additional-role requests are bounded. Source IDs map to the evidence library. Reviewed custom source can become an executable method. | Research tests verify generator independence, dynamic routing, source handling, proposed-code labeling, and lineage. Live generation produced custom-source dossiers and assumption review. Novelty and scientific usefulness are research judgments; role agreement is not evidence of superiority. |
| **5. Adaptive control and interaction** | Research partner, dossier critiques, decision inbox, manual controls, resumable discussions, and proposed research actions. Coordinator can dispatch bounded delegated probes and reconsider completed batches. Slow startup creates a researcher decision instead of automatic elimination. Stops use a separate numerical control channel. | Tests verify startup escalation, accepted action dispatch exactly once, researcher override records, stale-charter rejection, budget reserves, and direct numerical controls. A full delegated campaign remains distinct from these bounded checks. No claim that an autonomous system independently finds the best algorithm is made. |
| **6. Informative configurations** | Transparent selection rubric evaluates development plateaus, between-method differences, coverage, cost, and numerical reliability. Locked test tasks are excluded from research context; unavailable or unreliable probes produce explicit decisions. | Probe tests distinguish informative cases from easy cases and uniformly flat difficult cases. They verify test exclusion and numerical-reliability checks. The selection heuristic has not been calibrated against measured information gain. |
| **7. Performance and reliability** | Paired task/seed comparisons, separate solver/time cost views, common observed support, equal-task aggregate differences, hierarchical bootstrap, censoring, and threshold summaries. Fidelity/physics/charter differences stay separate. A design archive supports higher-order validation. | Analysis tests verify pairing, incompatible-condition exclusions, single-seed uncertainty, duplicate-seed handling, stopped-run censoring, and screening/validated distinctions. Worker tests cover actual MEENT order sweeps. Independent-solver agreement and publication-strength convergence remain unperformed. |
| **8. Delivery increments and partnership evaluation** | Numerical workspace, collaborative design, diagnostic selection, finalist nomination, confirmatory-run controls, and report export are represented in application code. The workspace remains open after finalist evaluation. | End-to-end system acceptance is tracked below. A substantial finalist benchmark and the researcher-plus-agents study have not been run. Software delivery does not claim either outcome. |

## Acceptance and invariant coverage

Test paths below are relative to the repository root; `workspace/` abbreviates `src/dqn_meent/workspace/`. These are concrete evidence locations rather than a blanket completion claim.

| Requirement | Authoritative implementation / verification location | What still must be established |
|---|---|---|
| Two concurrent jobs; stopping one leaves the other intact | Passing `tests/test_workspace_service.py::test_concurrent_stop_isolation_and_cooperative_pause_resume` checks two live processes, stops one, and observes the other advance | Tested with real local workers; distributed or other-OS scheduling is not covered. |
| Browser reconnection and service restart do not duplicate work | Passing `test_service_restart_reconciles_live_pid_without_duplicate_work` verifies unchanged PID/start identity/attempt and exactly one start event; real browser reload preserves both running trial IDs and advancing progress | Long network outages and distributed deployments are outside this local check. |
| Compatible pause/resume keeps algorithm/RNG/cache/schedule state | Passing `tests/test_workspace_worker.py` checks each optimizer's exact resume, cooperative pause, cache, and schedule; service test covers pause/resume | New optimizer adapters must establish the same contract. |
| Stale control cannot restart researcher-stopped work | Passing worker stale-control test and `test_forced_worker_exit_is_visible_and_recovery_never_restarts_stopped_job`; service control revisions | Coordinator must retain the same restriction for every newly introduced autonomous action. |
| Malformed candidate, solver failure, and forced interruption remain accounted | Passing worker failure/interruption tests and custom invalid-design/timeout/output-bound tests | Namespace-dependent results are host-specific; a skipped sandbox test elsewhere is not isolation proof. |
| Candidate cannot read credentials, locked tests, or alter evaluator | Real bubblewrap tests passed: restricted mounts, cleared environment, no network, bounded JSON protocol, parent-only MEENT evaluation | Deployment must keep namespace isolation available; the implementation fails closed when it is unavailable. |
| Custom source implements declared deterministic protocol | `tests/test_custom_optimizer.py` source verification, explicit state, exact resume, trusted MEENT evaluation | Synthetic protocol verification does not demonstrate optimizer effectiveness or correct scientific rationale. |
| Open invention beyond configured built-ins | Hypothesis `source`, proposal protocol/config fields, custom source verification and worker adapter | Numerical-library-dependent and differentiable algorithms need additional reviewed integration. |
| DQN extension preserves exploration schedule | Passing worker checkpoint test and `test_completed_dqn_extension_preserves_schedule_costs_and_history` | Changed-schedule experiments must use a new scientific identity; finalist-freezing coverage is tracked separately below. |
| Small known outcomes validate numerical behavior | Existing `tests/test_physics.py`, `tests/test_environment.py`, and numerical worker tests | These validate the evaluator and execution semantics, not a new algorithm's superiority. |
| LLM calls respect caps and failed-call accounting | Passing research finite-cap, unknown-price, malformed-output, HTTP-failure, key-routing, and reservation tests; bounded live discussion and generation succeeded | Sustained role quality and invoice reconciliation remain separate from transport verification. |
| Model role checkpoints do not replay completed calls | Passing engine and coordinator tests: durable preflight reservation, unfinished-call reconciliation after restart, stable decision identity across a partial restart write, before-send refund, retained in-flight cost, and campaign cap recheck on resume | Provider invoice reconciliation remains manual; uncertain calls retain their conservative reserve and are never automatically replayed. |
| Literature claims retain provenance | `tests/test_workspace_evidence.py` arXiv/Crossref parsing, DOI resolution, allowlist/redirect/size checks; live DOI metadata ingestion succeeded | Retrieval does not verify scientific claims; sources remain inspectable evidence for researcher review. |
| Slow-start method is not automatically discarded | Research slow-start test returns extend/change-startup/defer decision with measured runtime estimate | A real campaign must assess whether escalation helps the researcher. |
| Uninformative hard task can be replaced | Probe discrimination and numerical-reliability tests | Task-selection benefit across real grating regimes is not measured. |
| Charter revisions and test exposure affect eligibility | Passing `tests/test_workspace_confirmation.py` and API tests: conservative exposure at first confirmation launch; cohort frozen before exposure; physical-key provenance survives rename/charter/fidelity changes; full training/schedule/allocation and scientific source/dependency freezes; queued snapshot checks; direct test trials excluded from reasoning | Tracking is scoped to this campaign. External researcher knowledge is not measurable by this mechanism; multiple-seed/statistical protocol quality remains a researcher's responsibility. |
| Partial/failed trials are retained, not converted into full-budget scores | Analysis censoring and mixed-allocation tests; worker artifact retention | A final report must disclose allocation asymmetry and adaptive selection. |
| Claims separate preliminary simulation and validation | Analysis screening/converged fields; worker validation summary | Last-two-order agreement needs further physical audit before a publication claim. |
| Reproducible report links its evidence | Passing API export check and real browser export-link inspection; records link specs, source hashes, metrics, artifacts, and decisions | Preserve artifacts alongside exported prose; a portable one-command reconstruction bundle is not currently promised. |
| Researcher partnership compared against ablations | Required study described in plan section 8; interaction timestamps, trial costs, and model costs are recorded | Researcher-alone, researcher-plus-agents, system-only, and conventional tuning studies remain unperformed. Explicit researcher-attention timing and study-condition instrumentation are not yet implemented. |

## Explicit limits

- The scientific evaluator currently optimizes separate binary gratings for single target configurations. Arbitrary new objectives and gradients require implementation, not only a charter edit.
- Differentiable proposals have rationale cards, but the trusted worker exposes forward binary evaluation. Finite-difference gradient verification remains necessary before such an extension is usable.
- Legacy custom source uses the historical standard-library protocol. New package implementations can use exact locked numerical dependencies in an isolated runtime; unsupported evaluator capabilities still require an extension.
- Bootstrap intervals describe the supplied observations. Small, adaptively selected development samples do not establish broad generalization or state-of-the-art performance.
- Confirmation cohorts and frozen protocols prevent several software-level leakage paths. They cannot certify that the researcher has never seen related conditions externally, or make adaptively chosen test sets representative.
- Training settings and allocations freeze at each finalist's first confirmation launch. Cohort membership alone does not preregister those settings for all finalists, a complete test family, or fresh seed assignments. Comparisons therefore retain exploratory labels; a formal study must declare its joint protocol before inspecting confirmation outcomes.
- No independent electromagnetic solver audit, full finalist campaign, or researcher-outcome study is asserted as completed.
- This is a single-machine, local-user pilot. Distributed scheduling, authentication, multi-tenant isolation, and a production operations program are outside the current deployment.
