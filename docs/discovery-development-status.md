# Discovery redesign: implementation status

Date: 2026-09-28

The [public checkpoint](publication-checkpoint.md) records the later handoff repairs: an accepted literature map and four real-model hypotheses now exist, but the critic/revision provider failures leave the full cycle incomplete. The earlier checkpoint counts below are historical.

## Latest checkpoint: real Luna grating run

The current review deployment has replaced the fixture demonstration with **1D grating inverse design — finding an efficient optimizer**. Actual GPT-6 Luna calls produced analyses, literature work, optimizer proposals and a review/revised hypothesis after eight actual MEENT experiments. Discovery is paused for researcher review; the server remains available. See [the real-run report](grating-luna-review.md) and [current review checklist](discovery-review-checklist.md).

This was operator-assisted: source leads were supplied, and a labeled driver launched the empirical screen after an artifact/assignment handoff bug prevented candidate acceptance. There are zero accepted discovery candidates and no independent empirical review in this checkpoint. The readable scientific dialogue retains rejected proposals and original responses. The final model review explicitly discusses the eight measured trials and narrows its hypothesis. Latest focused verification: **63 passed**, plus TypeScript and an isolated frontend build. Earlier reports below retain their historical verification scope.

The governing scope is the [revised discovery plan](agentic-discovery-redesign-plan.md). The objective remains R0–R5, including actual LLM-driven scientific discovery and final qualification. The debugger checkpoint below does not establish those later capabilities.

## R0: agent work log and live viewer

Implemented:

- Immutable diagnostic events in SQLite, separate from scientific update events; numbered additive schema migration 3.
- Append-only campaign `agents/trace.jsonl`, content-addressed payload sidecars, redaction before persistence, bounded history reads and fixed-cursor downloads.
- Recovery after partial writes and after a durable file append whose database cursor did not commit. Completed corrupt lines remain preserved and produce a visible projection error.
- Model requests and ordinary responses captured before schema parsing, role reports and manager handoffs, provider usage/failures, source operations and application-command outcomes. Private provider reasoning is excluded, and provider trace coverage is labeled.
- Independent file projection while no browser is connected. Quiet calls produce throttled runtime liveness events.
- Implementation-job logs and a scoped service API. The workspace imports linked job events with original identities, payload verification and replay deduplication; disconnected services can reconcile later.
- An **Agent log** notebook tab with raw live JSONL, pause/resume, filters, loaded-line search, older pages, copy/download and payload expansion. Viewing does not dispatch scientific work.

Verification:

- [Focused regression report](../runs/discovery/agent-log-20260928/pytest.xml): **91 passed** across diagnostic logs, existing research, manager recovery/context, provider adapters, implementation service, commands and transactions.
- TypeScript and an isolated production build passed. Build artifact: [frontend](../runs/discovery/agent-log-20260928/frontend-dist/index.html). The shared review build was not replaced.
- The browser control test covers live events, pause/catch-up, replay deduplication and filtering without writes.
- [Actual HTTP/browser qualification](../runs/discovery/agent-log-20260928/http-1/qualification.json) runs the existing five-role workflow through actual HTTP transport, the campaign manager, JSONL projection and SSE. It proves logging starts with the browser closed and that the downloaded log matches the file. [Screenshot](../runs/discovery/agent-log-20260928/http-1/agent-log.png) and [browser output](../runs/discovery/agent-log-20260928/http-1/browser.log) are retained.

The HTTP model outputs are explicitly labeled fixtures. No real LLM call or optimizer-quality claim is part of this checkpoint. The service-trace tests exercise authentication, grant scope, delayed availability, payload integrity and deduplication; the HTTP/browser scenario covers the workspace service. Final two-service discovery qualification remains R5 work.

To repeat the HTTP/browser check after building an isolated frontend, use a new output directory:

```bash
.venv/bin/python scripts/qualify_agent_log_http.py \
  --directory runs/discovery/agent-log-review \
  --frontend runs/discovery/agent-log-20260928/frontend-dist
```

## R1–R3: discovery, literature and assessment foundations

Implemented in the current worktree:

- `discovery.start`, `discovery.control` and `discovery.amend` commands, with revision checks and immutable allocation history. Sessions pin an executable development problem. Model calls, source requests, task counts, generation rounds and numerical allocations have separate limits inside campaign authority.
- Persistent task dependencies, independent worker identities, frozen attempt contexts, cumulative call accounting, pause/stop behavior and recovery from saved responses. An uncertain dispatched call retains its reservation and uses the manager's existing reconciliation decision. Completed outputs are not automatically resent.
- Tasks alternate model calls and typed tools. Search, metadata ingestion, full-text reading, evidence inspection, implementation inspection and assessment inspection have durable requests/results. Interrupted external retrievals are not silently repeated. Invalid scientific products can receive at most two correction attempts within the original task allowance.
- HTML and PDF acquisition, captured-content hashes and stable passage IDs. PDF parsing runs in a subprocess with memory, CPU, page and text limits. Scanned PDFs without text produce a coverage gap. HTML coverage remains explicitly partial; access to a page does not prove that the complete paper was supplied. PDF extraction limitations follow the [pypdf extraction documentation](https://pypdf.readthedocs.io/en/stable/user/extract-text.html).
- Typed problem dossiers, literature applicability maps and candidate batches. Technical citations must resolve to an actual captured passage supplied to that task; an unread passage ID in a document index is insufficient. Dynamic generators in a batch share a frozen starting context and cannot read peer proposals through the evidence tool.
- Candidate and methodology-family records, lineage and existing hypothesis-card projections. Missing code is recorded as an implementation requirement. A suggested library version still requires the existing compatibility and correctness checks before attachment.
- New campaigns no longer inject algorithm cards. Installed algorithms remain available as implementations and for explicit manual experiments. Historical cards retain their provenance.
- Structured campaign memory includes discovery sessions, active work, candidates and assessments. Detailed exports under `campaigns/<id>/discovery/` preserve policy, attempt, tool, artifact and candidate records.
- A **Discovery** notebook tab exposes start/pause/resume/stop, allocations, the agenda, task results and comments addressed to the campaign manager. It can prepare draft assessments from saved plan artifacts and inspect or launch ready tuning batches through durable commands.
- Predeclared assessment plans and deterministic explicit/random/grid configuration batches, with numeric/logarithmic ranges, categorical values, conditional parameters, constraints and duplicate rejection. Drafts can exist before code is available; launch checks the whole batch allocation and pins ordinary frozen experiments.
- Adequacy gates for configuration count, seed replication, informative horizon, training updates and required diagnostics. Performance-based family deprioritization requires adequate evidence and independent review. Amendments cannot retrospectively lower adequacy thresholds. Poor configurations and their costs remain in the experiment history.
- New completed specialist evidence wakes the manager once. Unchanged polling does not create repeated model calls.

Verification at this checkpoint:

- [Literature/foundation regression report](../runs/discovery/literature-20260928/pytest.xml): **91 passed** across discovery, citations, full-text extraction, logging, manager recovery/context, commands and existing research. This report predates the final assessment additions below; it is not a claim that every later change was in that run.
- The assessment tests execute six actual bounded-continuous experiments: three configurations with two seeds each. They check idempotent launch, preservation of every result, missing-implementation drafts, training adequacy and rejection without independent review. Model-authored inputs in these tests are fixtures.
- [Actual HTTP discovery qualification](../runs/discovery/literature-20260928/http-2/qualification.json) completes two analyses, a literature search/read/map sequence, two generators sharing one input snapshot and manager synthesis. Application commands and model transport use HTTP; task continuations, extraction, passage checks, candidate projection and logging are real. Model responses and source content are explicitly synthetic fixtures. The downloaded log matches the file produced with no browser connected. This second run includes the final citation-content and trace-identity changes.
- TypeScript and an isolated production build pass: [frontend artifact](../runs/discovery/literature-20260928/frontend-dist/index.html). The discovery controls and agent-log browser tests pass. Environment-dependent campaign/experiment/research browser cases require an actual HTTP qualification server and are not counted when skipped.
- The [broad Python sweep](../runs/discovery/literature-20260928/full-pytest.xml) recorded **550 passed, 6 failed**. Three failures were obsolete expectations of automatically seeded proposals. The others exposed historical implementation-job log compatibility and two cancellation/budget receipt rollback bugs. All six were fixed; the [final focused rerun](../runs/discovery/literature-20260928/checkpoint-fixed-pytest.xml) records **84 passed**, including every previously failing test, discovery, citations, extraction, assessments and logging. The full suite has not been rerun after these fixes; the two reports must be read together.
- [Source snapshot](../runs/discovery/literature-20260928/source-checkpoint.tar.gz), [file hashes](../runs/discovery/literature-20260928/source-manifest.json) and [checkpoint record](../runs/discovery/literature-20260928/checkpoint.json) preserve the code and exact verification boundaries for review.

## Remaining deliveries

| Delivery | Status |
|---|---|
| R1: persistent discovery foundation | Implemented core controller, continuations, commands, allocations, recovery, memory and notebook controls. Integrated acceptance remains open. |
| R2: problem analysis, literature and generation | Implemented typed products, full-text tools, citation checks and independent generator contexts. Actual-provider, two-problem scientific qualification remains open. |
| R3: implementation and empirical assessment | Implemented predeclared assessments, bounded tuning, draft/experiment integration and adequacy gates. Automatic manager commissioning/reuse, full cost attribution across a discovery lineage and richer assessment review still need integration. |
| R4: autonomous iteration and steering | Task-completion wakeups and manager-directed comments are implemented. Numerical/implementation completion dependencies, manager command delivery, evaluation/review/evolution cycles and complete branch controls remain open. |
| R5: integrated qualification and migration | Pending. Include real-provider discovery, numerical evidence, faults/concurrency, migration rehearsal and researcher validation. |

At the implementation checkpoint, the existing review services had not been restarted or migrated. These changes are in the worktree, with isolated verification artifacts. The original annotated architecture questions remain untouched.

For the earlier researcher review, the previous review workspace and library were stopped with explicit authorization and a copied HTTP fixture was served with models disabled. That deployment has now been superseded by the real Luna grating checkpoint described at the top of this document. Original data and qualification evidence remain intact. Use [the review checklist](discovery-review-checklist.md) and `deploy/discovery-review.json` for current lifecycle commands. Removing or changing the existing Tailnet route requires interactive administrator authentication.

The complete autonomous scientific loop is **not finished**. Manager assessment prepare/launch/wait tools now exist and have focused authority/recovery/numerical tests, but the actual campaign encountered an earlier artifact/assignment handoff failure. The real numerical screen therefore used explicitly recorded operator assistance. Automatic implementation commissioning and independent empirical revision remain incomplete. The fixture-based checks above make no real-model scientific claim; the separate real-run report states the narrow empirical findings and their limitations.
