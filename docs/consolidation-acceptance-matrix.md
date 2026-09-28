# Consolidation acceptance matrix — 2026-09-28

This matrix describes the local checkpoint on `setup/latest`; it is not release
approval. The governing requirements are in the
[consolidation plan](architecture-consolidation-plan.md). Current work and the
review interface are summarized in [development status](current-development-status.md).

The [LLM-driven discovery and agent-debugging plan](agentic-discovery-redesign-plan.md)
adds R0–R5 as an open gate. The [discovery status](discovery-development-status.md)
records implemented logging, task, literature and assessment foundations. The complete
scientific loop and integrated acceptance remain open; historical evidence below does
not qualify the newer code.

The prior [477-test Python report](../runs/consolidation/experiment-controls-20260927/pytest.xml)
and [40-case browser report](../runs/consolidation/experiment-controls-20260927/browser-complete.log) retain the A–C/D1.1–D1.2 baseline. The current [manager lifecycle checkpoint](../runs/consolidation/manager-lifecycle-20260928) adds D1.3, D1.4 and D2 qualification, including 19 browser cases on build `index-BH0TUqoH.js`. The full regression report and exact source snapshot are linked from the current development status. E and F remain open. Model responses and
semantic reviews are fixtures. Numerical processes, service requests, browser
actions, isolation and database recovery are real. A passing bounded workflow
does not establish a superior optimizer or satisfy a full production study.

## Implemented behavior with local evidence

| Requirement | Evidence on the current source | Limit of that evidence |
|---|---|---|
| General harness for distinct problems | MEENT and bounded continuous browser execution and study-template cases in [extended browser evidence](../runs/consolidation/experiment-controls-20260927/browser-complete); [contract tests](../tests/test_framework_contracts.py) and [worker tests](../tests/test_framework_worker.py). | Two supported applications; no universal-optimizer claim or arbitrary-domain support. |
| One scientific scheduler with bounded grants and recovery | [Resource](../tests/test_framework_resources.py), [supervision](../tests/test_framework_supervision.py), [transaction](../tests/test_framework_transactions.py), [CLI tests](../tests/test_framework_cli.py) and worker tests; actual local/connected CLI and browser recovery. | Low-level numerical reference APIs remain available, but shipped CLI work uses the common scheduler. |
| Frozen scientific procedures, studies and selection | [Template](../tests/test_framework_templates.py), [study-rule](../tests/test_framework_study_rules.py), [confirmation](../tests/test_framework_confirmation.py) and [release tests](../tests/test_framework_confirmation_release.py); bounded confirmation browser cases. | Full production scientific outcomes require their actual declared evidence and allocation. |
| Reference inputs and independent diagnostics | [Reference-input](../tests/test_framework_reference_inputs.py), [diagnostic](../tests/test_framework_diagnostics.py) and [inference tests](../tests/test_framework_inference.py); reference and diagnostic browser cases. | Historical missing measurements remain unknown. Diagnostics do not become search or confirmation evidence merely because they ran. |
| A missing optimizer or evaluator remains designable | Missing-code draft browser case and [commissioning evidence](../runs/consolidation/experiment-controls-20260927/browser-complete); [draft tests](../tests/test_framework_drafts.py). | New execution requires exact eligible executable bindings; supplied code and fixture review establish this software path. |
| Separate optimizer/evaluator correctness service | [Evaluator](../tests/test_framework_evaluators.py), [package](../tests/test_framework_packages.py), [isolated-host](../tests/test_framework_isolated_host.py) and implementation-service tests; supplied-code commissioning through both HTTP services. | Mechanical correctness and fixture semantic review do not establish optimizer performance or live-model build quality. |
| Explicit scoped waiver and later contrary evidence | Contract-only evaluator browser case; [validation](../tests/test_framework_validation.py), [revalidation](../tests/test_framework_revalidation.py) and [executable-evidence tests](../tests/test_framework_executable_evidence.py). | Waivers do not turn missing numerical evidence into a pass or silently authorize confirmation. |
| Deliberate executable reuse or decline | [Executable-reuse tests](../tests/test_framework_executable_reuse.py) and the browser revalidation/reuse workflow. | A reusable optimizer is optional; each intended use still checks the problem, study and exact version. |
| Portable runtime, records and historical reproduction | [Runtime portability](../tests/test_framework_runtime_portability.py), [bundles](../tests/test_framework_bundles.py), [imported execution](../tests/test_framework_imported_execution.py) and [reproduction tests](../tests/test_framework_reproduction.py); browser export/import, runtime recovery and reproduction with the original directory unavailable. | Supported historical conversions preserve explicit missing measurements; source-specific imports of both repositories' complete histories remain E work. |
| Full upstream accounting and retained uncertainty | [Accounting](../tests/test_framework_accounting.py), [provenance](../tests/test_framework_provenance.py) and [service-cost tests](../tests/test_framework_service_costs.py); browser delayed-receipt reconciliation. | An unmeasured quantity stays unknown. Ledger correctness is distinct from external-provider reconciliation of every uncertain call. |
| Researcher campaign/context/issue commands | [Campaign command tests](../tests/test_framework_campaign_commands.py) and actual browser lost-reply/stale-edit cases in [main browser evidence](../runs/consolidation/experiment-controls-20260927/browser-complete). | All compatibility routes are now translated; the inventory retains each owning command. |
| Experiment and study commands (D1.1) | [Experiment command tests](../tests/test_framework_experiment_commands.py); browser pause/extend/stop recovery preserves frozen procedure and newer controls. | This closes the five-route group, not CLI or manager lifecycle integration. |
| Idea and implementation controls (D1.2) | [Hypothesis command tests](../tests/test_framework_hypothesis_commands.py), [implementation command tests](../tests/test_framework_implementation_commands.py), browser feedback recovery and supplied-code commissioning. | Saved feedback and model requests remain separate. Lost implementation-control replies now reconcile before later guidance checks. |
| Pending browser requests survive reload | Prior recovery cases plus [research/decision/source HTTP recovery](../runs/consolidation/manager-lifecycle-20260928/browser-http) cover accepted replies lost in transit, lookup/reload, changed revisions and source retrieval. | All browser mutations use the shared hook except immutable byte staging. |
| Research, decision and source commands (D1.3) | [Research command tests](../tests/test_framework_research_commands.py), actual browser HTTP recovery and retained source receipts. | Source network responses in qualification are fixtures; the concurrency, persistence and command paths are real. |
| Local and connected CLI (D1.4) | [Actual SIGINT/resume](../runs/consolidation/cli-controls-20260928/interruption-3/qualification.json), [connected restart/replay](../runs/consolidation/manager-lifecycle-20260928/connected-restart.json), and CLI tests. | Bounded numerical regression; historical pickle-only policies are retained as evidence rather than silently converted. |
| Durable campaign manager (D2) | [Lifecycle fault tests](../tests/test_framework_manager_lifecycle.py), [typed context/reuse tests](../tests/test_framework_manager_context.py), and [actual two-service scenario](../runs/consolidation/manager-lifecycle-20260928/restart-http-final/qualification.json). | Operational lifecycle with fixture model results; real reasoning quality and production campaign acceptance remain F. |
| Both-service restart preserves accepted work | [Restart comparison](../runs/consolidation/experiment-controls-20260927/restart-after.json): 804 authoritative records unchanged; 76 commands replayed; guidance preserved for 15 campaigns; zero new executions, costs, grants or comments. | Derived text/cache/cursor records are excluded from the hash comparison. This does not establish complete durable consumption of manager events. |

## Gates still open

| Gate | Existing foundation | Evidence still required |
|---|---|---|
| R0–R5: LLM-driven discovery and debugging | Durable campaign manager, role runner, source retrieval, experiments, implementation service and accounting. | The discovery plan's acceptance scenarios: source-grounded proposal batches, tuning adequacy, autonomous empirical refinement, researcher steering, persistent agent logs/live viewing and bounded real-model evidence. |
| E: source-specific histories and migration | Portable bundles, immutable source captures, archived receipts and database backups. | Dry-run/apply/repeat imports from both actual repositories; numbered migration and rollback rehearsal preserve earlier records and newly created work. |
| F: product and researcher acceptance | Local numerical, HTTP and browser evidence at the recorded checkpoints. | Final integrated-source qualification after D/E and R0–R5; bounded real-model build/discovery scenarios; researcher workflow validation; rehearsed live cutover and a separate acceptance campaign. |

All architectural choices needed to continue these gates are settled in the plan.
The open gates describe implementation and evidence work, not unanswered product
architecture questions.
