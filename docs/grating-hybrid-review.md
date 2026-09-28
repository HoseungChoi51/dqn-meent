# Recorded grating hybrid review

Actual Codex GPT-6 Luna output, September 28, 2026. This ran in an isolated copy of the current campaign; the user campaign and its four proposals were retained. No hybrid implementation or numerical execution occurred.

The initial workflow used nine calls, including response corrections and failed outputs. A fresh independent review with clearer separation of conceptual plausibility from implementation readiness used one further call. Both reviews are retained in the local trace. The earlier review incorrectly described a bundled parent as unavailable; the fresh review below uses current readiness and identifies concrete specification problems.

## Generator → campaign manager

**Restart hill climbing with stagnation-triggered tabu block escapes**

Use the bundled restart hillclimb as the primary search. Track consecutive unsuccessful strict single-bit proposals. On a predeclared stagnation threshold, make a bounded block-flip escape (initially size 2; size 4 as a separate configuration if assessment permits) and record the resulting design in a short-term tabu memory to prevent cycling. Resume single-bit hill climbing after an accepted improvement; otherwise trigger the parent's restart rule. Thus coordinated moves are conditional escapes, not a replacement for hillclimb's empirically promising intensification. This interaction is a testable design hypothesis, not an established result.

Exact parents:

- `candidate_02418a0db8109b43f32acffe4866`
- `candidate_ab1dea7a7a73680fe1d3ecb08909`

## Independent reviewer → campaign manager

**Verdict: revise.**

The conditional escape concept is coherent but not yet reproducible as a unique algorithm. The proposal specifies strict-improvement escape acceptance and mentions retaining rejected block candidates in tabu, but omits crucial details: whether the tabu set stores candidate designs or moves; how tenure expires; how a tabu hit is handled; what exactly triggers/reset stagnation; whether escape acceptance resets the trigger; and whether a parent restart clears or retains tabu state. Its parameter_space lists alternatives but not a fully specified configuration. Clarifying these points is necessary to implement and discriminate the proposed mechanism. This is not rejection of the approach or evidence about performance. The supplied hillclimb screen offers limited motivation for retaining hillclimb as a parent, not evidence for the hybrid interaction.

Required changes:

- Specify exactly what increments and resets the stagnation counter, including following an accepted block move and following restart.
- Define the block proposal distribution and whether k distinct positions are sampled uniformly without replacement.
- Define tabu entries, tenure timing/expiry, behavior on tabu hits, and whether aspiration permits a strictly improving tabu candidate.
- State whether restarts clear or retain tabu history and whether failed escape evaluations are recorded in memory.
- Provide one fully specified parameter configuration and a parent-discriminating test protocol.

Implementation follow-up:

- Current readiness information reports hillclimb and block-tabu parent candidates as bundled and ready, but this hybrid itself has no executable implementation; new implementation and correctness checks remain necessary and are outside the current implementation allocation.

Suggested tests:

- After specification and correctness validation, compare hybrid with both parents on identical seeds and 96-request budgets; plot best-so-far against evaluation requests and annotate each escape, acceptance, tabu hit, and restart.
- Use a toy binary objective with a known improving coordinated move unavailable through immediate strict single-bit improvement to verify trigger, escape acceptance, tabu expiry, and restart-memory behavior before evaluator testing.
- If the hybrid shows an incremental gain, repeat at higher Fourier orders and broader seeds before any physical-design claim.

## Evidence record

- Candidate: `candidate_45805624ea46959840a4ccfccd3b`.
- Generation artifact: `discovery_task_bf6e5cc3728a0d724eb45ec280ba_step_0_artifact_0`.
- Review artifact: `discovery_task_468429575f4a3dde77255c548369_step_0_artifact_0`.
- Full local reports: `runs/proposals/hybrid-luna-20260928/result.json`, `review-result.json`, and `scientific-example.json`.
- Original requests/responses and tool receipts: `runs/proposals/hybrid-luna-20260928/workspace/campaigns/campaign_grating_inverse_luna_20260928/agents/trace.jsonl` and adjacent payload files.
- These local runtime files are ignored by Git. This document preserves the substantive generated mechanism and fresh review for repository readers.

The candidate remains ineligible for numerical testing because its scientific specification needs revision. Missing code is an additional, independent requirement. The copied qualification artifacts were not inserted into the user campaign.
