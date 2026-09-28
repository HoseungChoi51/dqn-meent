# Proposal exploration and implementation

Status: September 28, 2026. This describes the working controls and their limits, not a claim of an autonomous scientific discovery result.

## User controls

| Intent | Control | Behavior |
|---|---|---|
| Request additional ideas | Hypotheses → Develop strategies | Choose a count and optional direction. The manager receives existing proposals as evidence and requests additional mechanisms. |
| Diversify one idea | Open its dossier → Diversify proposal | Generate substantive alternatives with the selected parent preserved. Hyperparameter changes alone are not diversification. |
| Combine two ideas | Hypotheses → Combine proposals, or a dossier → Combine with another proposal | Select two distinct parents. The generator must explain their contributions and interaction, and retain both parent links. |
| Find existing code | Dossier → Find reusable implementation | Search bundled optimizer descriptions and the implementation service's published packages. |
| Write or import code | Dossier → Request implementation | Freeze a specification, parameters, correctness criteria and a separate implementation allocation. Optionally supply an existing source package. |

Proposal exploration requires an optimizer discovery session. Requests go through the campaign manager's durable inbox; a paused session retains them until resumed. Direction, exact parents, and saved revision feedback are passed through the manager's delegation into specialist context. Original proposals remain available.

The **Research progress** panel above the workspace shows the latest request's
status. **Paused** means the request is saved but new agent calls will not start;
use **Resume discovery** in that panel to continue. Submitting another proposal
request does not override a pause. **Queued** means the scheduler has not yet
dispatched the work. Running agents show their role, model, stage, elapsed time
and last observed activity. A provider heartbeat confirms that the runtime is
waiting; it is not evidence that a scientific result has been produced.

Task counts describe the assigned work, rather than an estimated completion
percentage: the manager can add or revise tasks. Waiting and failed work retain
their reasons. **View agent log** opens the live ordinary responses and tool
receipts in the research notebook. The top bar's **Connected** label describes the
workspace connection, not whether any agent is running.

The campaign history and each agent's bounded working context are separate.
See [discovery working context](discovery-context.md) for evidence selection,
paged record reads, and replacement of obsolete assignments.

For a recorded model timeout, **Retry failed tasks** queues a new attempt for
the affected tasks. Completed work, original responses and receipts, and all
usage remain recorded. Retries use the current model assignments; the manager
waits for its retried prerequisites before reviewing their results. Retrying
does not resume a paused session. Calls with unresolved usage reservations or
accepted output cannot be retried through this control.

Search currently covers installed code. Automatic search for optimizer implementations in public code repositories is not implemented. The discovery agent's `implementation.inspect` tool exposes bundled parameter schemas and published versions; commissioning remains available through the separate implementation UI and application command.

In the current grating campaign, implementation compute is **zero seconds**. Use **Problem workbench → Revise charter → Implementation compute cap (seconds)** to allocate it before commissioning. The request dialog now explains insufficient allocation before submission. The library contains no published packages yet.

The candidate-pool proposal is currently recorded as `custom`, despite its title saying “bundled.” There is a bundled `surrogate` optimizer, but it combines global random candidates with local mutations around the incumbent. Its name alone does not establish that it implements the proposal's exact mechanism. Review the mechanism and parameters before using it in a revised proposal. The elite-local proposal requires a separate implementation or an explicitly justified reuse decision.

## Review and experiment gates

1. A generation task creates immutable candidate records and linked hypotheses. Selected parents are checked when accepting diversify/hybrid outputs.
2. Completed generation automatically queues a separate `proposal_reviewer` persona with the generated candidates and their evidence. Its input excludes the manager's conversational preferences and other reviewers' judgments.
3. The reviewer returns a structured `proposal_review` for each exact candidate revision: `test`, `revise`, or `reject`, with written mechanism checks, risks and suggested tests. Implementation follow-up is recorded separately. Missing code is not itself a conceptual objection.
4. New discovery proposals cannot launch experiments without an accepted independent `test` verdict. A reviewer cannot approve their own generated candidate. An objection requires a linked revision and another review; later praise does not erase the earlier objection.
5. Code availability, correctness validation, evaluator compatibility and allocation remain separate requirements. A conceptual approval cannot bypass them.
6. The existing assessment service supports small seeded tuning batches and retains all results and costs. The manager is instructed to compare with parents/baselines and review measurements before committing further resources. Drafts can be prepared while eligibility issues are unresolved; launch is blocked.

The review is visible in the proposal dossier and Scientific dialogue. Original model responses, validation feedback, assignments and usage remain in the agent log. Failed prerequisites block dependent specialist work while the manager can inspect failures and reassign work.

## Qualification

The UI was exercised for additional proposals, diversification, two-parent combinations, paused requests, installed-code search, and insufficient implementation allocation. Backend regression checks cover parent provenance, independent review, launch gates, restart recovery and bounded correction of malformed model response envelopes.

An isolated copy of the actual 1D grating campaign was also exercised with **Codex GPT-6 Luna**. Ten actual calls were used across the initial workflow and a fresh reviewer check. The generator produced a hill-climbing/tabu hybrid with both original parent IDs. The independent reviewer returned **revise**, identifying underspecified stagnation, tabu and restart semantics. No hybrid implementation or numerical experiment was executed. See [the recorded scientific exchange](grating-hybrid-review.md).

The live run exposed duplicated context, artifact IDs used as task dependencies, and incorrectly placed response fields. Context compilation now retains selected scientific evidence while indexing indirect details when necessary; saved artifacts can satisfy explicit evidence prerequisites; malformed received envelopes can receive up to two budgeted corrections. Original output and usage are retained. Oversized explicitly assigned evidence still requires a smaller task.

This qualification establishes generation and independent textual criticism. It does not establish an uninterrupted autonomous generation → implementation → tuning → empirical review cycle.
