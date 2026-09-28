# Development status — 2026-09-28

Latest status: [public development checkpoint](publication-checkpoint.md). The D1/D2 verification and deployment descriptions below describe an earlier increment.

Branch: `setup/latest` (base commit `e22ffec`). Development resumed at the researcher's request. The integrated product remains experimental; live cutover has not occurred.

The current increment completes D1's remaining command and CLI paths and adds D2's durable campaign-manager lifecycle. The product is a general research harness for developing an optimizer suited to the problem at hand. Finding a universal optimizer remains a non-goal.

Discovery update: the [LLM-driven discovery and agent-debugging plan](agentic-discovery-redesign-plan.md) defines R0–R5. R0's log/viewer and the R1/R2 task, literature and proposal foundations are implemented; R3 now has predeclared tuning assessments and numerical checks. The autonomous empirical loop and integrated qualification remain open. See [discovery development status](discovery-development-status.md) for exact scope and evidence. The D1/D2 verification below retains its original source snapshot and does not qualify the new scientific discovery loop.

## Implemented in this increment

- All 23 compatibility mutations and all browser mutations use the shared command boundary, apart from byte-upload staging. Research controls, decisions and source retrieval retain original requests and receipts across retries. Slow source retrieval cannot block experiment controls.
- Local and connected CLI training, baselines, policy inference and convergence evaluation create the same campaign, grant, attempt and cost records. Local mode holds the exclusive scheduler lease. Legacy files are exports of verified evidence bundles; old standalone runs enter through retrospective import before new measurements.
- Each campaign has a durable inbox, a separate event cursor and serialized turns. User requests, relevant completions, validation changes and authority failures retain stable identities. Duplicate completion events and repeated identical rejected proposals cannot create an unlimited sequence of model turns.
- A completed model result is saved before its messages, findings and actions are projected. Those projections and proposed deliveries commit together. Recovery reuses saved output and accepted command receipts. A possibly dispatched external call retains its uncertain usage and requires reconciliation.
- Guidance and authority are checked before dispatch. Stale output remains evidence and produces a reconsideration against current context. Scoped issues leave independent authorized work available. When the provider is unavailable, requests remain queued and one manager issue explains the blocker.
- Typed campaign context is exported as versioned JSON, JSONL and readable Markdown. Large histories use bounded recent/retrieved evidence while retaining current constraints and issues. Reusable findings require scope and limitations, preserve their evidence and classification, and need an explicit reference decision before another campaign reads them.
- Implementation-control acknowledgement recovery checks the service's accepted control receipt before rejecting stale unaccepted work. Supplied optimizer packages with omitted default fields are interpreted using their commissioned specification.

## Verification

Current artifacts: [checkpoint record](../runs/consolidation/manager-lifecycle-20260928/checkpoint.json), [source and database snapshot](../runs/consolidation/manager-lifecycle-20260928/snapshot), and [verified source archive](../runs/consolidation/manager-lifecycle-20260928/verified-source.tar.gz).

- The [full Python regression report](../runs/consolidation/manager-lifecycle-20260928/pytest-stable.xml) records **522 passed**, no failures, errors or skips, in **571.34 seconds** with the complete locked numerical environment. The before/after package manifests match.
- The isolated TypeScript/build check passed; the build is `index-BH0TUqoH.js`. Shared `frontend/dist` was not replaced.
- Eighteen mocked browser cases and [one actual HTTP/browser recovery case](../runs/consolidation/manager-lifecycle-20260928/browser-http) passed. The latter loses accepted replies, reloads, and reconciles original receipts for research, decisions and sources.
- The [actual two-service lifecycle scenario](../runs/consolidation/manager-lifecycle-20260928/restart-http-final/qualification.json) passed across two library starts and four workspace starts. It retains 11 manager turns, 14 inbox inputs, two implementation grants, two numerical experiments and one scoped issue. A missing-code draft is commissioned and launched after restart; duplicate completion is ignored; an independent experiment continues during the issue; changed guidance causes reconsideration; an uncertain external call is closed without replay.
- The [failed independent check](../runs/consolidation/manager-lifecycle-20260928/restart-http-final/independent-failure-evidence.json) retains its report. The fixture deliberately cannot repair the invalid package; this is an operational recovery test, not a model-repair claim.
- [Actual connected CLI restart](../runs/consolidation/manager-lifecycle-20260928/connected-restart.json) replays ten accepted commands with four trials, four attempts and eighteen cost events unchanged.
- [Actual CLI SIGINT/resume](../runs/consolidation/cli-controls-20260928/interruption-3/qualification.json) preserves observations, resumes into a different export directory after a charter edit, and matches uninterrupted DQN results. Actual connected DQN, inference and convergence outputs are retained in [CLI evidence](../runs/consolidation/cli-controls-20260928).

No real LLM calls were made for this increment. Manager responses and semantic implementation reviews are fixtures; commands, HTTP, package checks, numerical processes, accounting and service restarts are real. Earlier failed qualification attempts are retained alongside corrected results. One regression run overlapped an environment refresh and correctly rejected changed runtime identities; final verification uses the restored complete lock. Setup instructions now include the optional numerical dependencies and avoid resynchronizing them during a run.

The previous [477-test / 40-browser checkpoint](../runs/consolidation/experiment-controls-20260927/checkpoint.json) remains the regression foundation for A–C and D1.1–D1.2. Those browser results describe their original source snapshot; the current increment's browser coverage is the 19 cases above.

## Remaining work

| Delivery | Current boundary |
|---|---|
| D1 / D2 | Locally implemented and operationally qualified within the evidence above; preserve these controls through migration and final combined qualification. |
| R0–R5: discovery and debugging | R0's log/viewer and the R1/R2 discovery, literature and proposal foundations have local qualification. R3 adds predeclared tuning assessments and adequacy gates. Complete manager action delivery, implementation/numerical completion dependencies and empirical review/evolution before real-model qualification; see the discovery status and plan. |
| E: history and migration | Complete source-specific imports of both repositories, numbered migrations, dry-run/repeat-import reconciliation and rollback rehearsal on copied service data. The CLI importer does not constitute the complete historical migration. |
| F: product acceptance | Final combined browser/regression checks, bounded real-model discovery scenarios for both applications after R0–R5, scientific checks actually claimed, researcher validation and the rehearsed live release. |

The [acceptance matrix](consolidation-acceptance-matrix.md) and [progress log](consolidation-progress.md) preserve the evidence boundaries. No architectural question is waiting on the researcher.

## Running services

The managed review deployment remains at [localhost:8791](http://127.0.0.1:8791), with its existing Tailnet endpoint and prior frontend build. Its configuration now enables Codex; it is no longer the earlier model-disabled fixture. This increment did not restart or migrate that deployment or the original services on 8765/8766.

The new verification data and frontend are isolated under `runs/consolidation/manager-lifecycle-20260928`. Use the checkpoint artifacts to review this increment; the existing review URL does not yet represent these source changes. The [server-control guide](server-control.md) documents the managed deployment.
