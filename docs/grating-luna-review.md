# GPT-6 Luna: real 1D grating discovery checkpoint

This report describes the first assisted run. See the [later publication checkpoint](publication-checkpoint.md) for the subsequent accepted literature map, four hypotheses and remaining critic/revision failures.

Date: 2026-09-28. Discovery paused after the requested approximately 30-minute development/test window. Web UI remains running at **https://x670.tail096b61.ts.net:8449**.

Campaign: **1D grating inverse design — finding an efficient optimizer** (`campaign_grating_inverse_luna_20260928`).

## What was misunderstood

The previous “Discovery HTTP qualification” campaign exercised HTTP transport, logging and scripted workflow transitions. Its responses and sources were synthetic fixtures. That is useful engineering verification, but it cannot establish scientific discovery or give a researcher meaningful evidence about model dialogue. It should not have been presented as the scientific example to review.

The product's goal remains a general harness for users and LLMs to develop effective optimizers for a particular problem. Finding a universal optimizer is not the goal. This checkpoint uses one concrete inverse-design problem and retains uncertainty, missing implementations and failed workflow transitions.

## Actual model work and dialogue

The configured provider is Codex with **gpt-6-luna**, using the existing ChatGPT subscription login. No model response in this campaign was supplied by a fixture. Scientific dialogue now displays ordinary model responses, reported rationale, work products, assignments and tool receipts together. Earlier attempts and corrections can be expanded; the raw agent log preserves the exact requests and responses.

The run includes problem analysis and skeptical analysis, literature investigation, independent methodology proposals and campaign-manager review. Luna proposed restart hill climbing, adaptive block tabu, population search and a coordinated block-mutation idea requiring implementation. These proposals include falsifiable predictions, hyperparameters and limitations. The candidate batches failed metadata validation, so their text is visible in dialogue but there are **zero accepted discovery candidates** at this checkpoint.

Literature retrieval encountered real failures. The operator supplied two explicit paper leads through the campaign manager. A PDF of [Colburn and Majumdar's inverse-design study](https://arxiv.org/abs/2011.03626) was retrieved and passages supplied to the model. It concerns differentiable inverse design and does not directly validate discrete hill climbing. A PMC lead returned a download-preparation page; Luna recognized that it did not provide usable paper evidence. The retrieval failures and assistance remain recorded.

## Predeclared numerical screen

Problem: 64 binary cells, wavelength 1100 nm, deflection angle 50°, thickness 325 nm, incident index 1.45 and exit index 1.0. Objective: maximize absolute transmitted +1 diffraction efficiency. Evaluator: actual MEENT RCWA, exploratory Fourier order 15.

Luna proposed hill-climbing restart patience values **8, 16 and 32**. The screen tested the prediction that at least one setting would outperform uniform random on both shared seeds within **96 evaluations per trial**. Hill climbing used permuted single-bit neighborhoods, strict improvement and random initialization. Each trial had a 45-second cap; all eight completed all 96 evaluations.

The operator saved the protocol before launch and used normal `trial.create` commands and ordinary numerical workers. This intervention was necessary because the automatic candidate/assessment handoff failed. It is not attributed to autonomous agent execution.

| Method | Seed 0: best efficiency | Seed 1: best efficiency |
|---|---:|---:|
| Uniform random | 0.350746 | 0.272040 |
| Hill climbing, patience 8 | 0.484325 | 0.462355 |
| Hill climbing, patience 16 | 0.768054 | 0.857233 |
| Hill climbing, patience 32 | 0.768054 | 0.857233 |

These are observed results for two seeds on one instance. They do not establish general superiority, convergence in physical fidelity or evidence against training-heavy methods such as DQN.

## How Luna changed its hypothesis

The eight measured results and their trial IDs were delivered to the campaign manager. Its saved review explicitly revised the hypothesis:

> For this instance at 96 evaluations, permuted single-bit restart hill climbing with patience 16–32 is promising relative to uniform random; the evidence does not resolve 16 versus 32 or generalize beyond these seeds.

The manager noted that every tested patience exceeded random in this screen, patience 8 trailed 16/32, and 16/32 tied exactly. It described local improvement as a possible mechanism, not an established causal explanation. Its proposed next test is a larger preregistered shared-seed comparison of random versus patience 16/32, retaining trajectories and wall time, followed by higher-Fourier-order checks of selected designs. It explicitly kept training-heavy families open for a separately budgeted test.

This is an actual model response to numerical evidence. An independent empirical review and accepted parent/child candidate lineage are still missing. The manager also repeated its review on a subsequent wakeup; reducing redundant context and synthesis remains necessary.

## Evidence files

- [Predeclared empirical protocol](../runs/discovery/grating-luna-20260928/empirical-protocol.json)
- [All eight measured results and trial IDs](../runs/discovery/grating-luna-20260928/empirical-results.json)
- [Original Luna empirical review responses and provider usage](../runs/discovery/grating-luna-20260928/luna-empirical-review.json)
- [Paused discovery snapshot: tasks, steps, artifacts, receipts and feedback](../runs/discovery/grating-luna-20260928/discovery-review-snapshot.json)
- [Append-only agent trace](../runs/discovery/grating-luna-20260928/workspace/campaigns/campaign_grating_inverse_luna_20260928/agents/trace.jsonl)
- [Real-model campaign launcher](../scripts/run_grating_discovery.py)
- [Explicit operator-assisted empirical driver](../scripts/run_grating_empirical_screen.py)

## Engineering changes and remaining problems

The scaffolding adds a readable scientific-dialogue view, manager assessment prepare/launch/wait tools, durable results and replay checks. The live run exposed and corrected a provider instruction conflict that prohibited application-tool requests, task-completion ambiguity, campaign evidence resolution, dependency-name resolution, continuation validation and excessive duplicated context. Source retrieval gained an arXiv metadata fallback. Native Codex shell/network execution remains separate from the framework's typed application tools.

The principal unresolved blocker is the coupling between scientific artifact persistence and agenda admission: a manager produced a literature map, but invalid assignments rolled back that response's artifacts. Later tasks assumed the map existed and produced candidate batches with missing/invalid map references. The system needs explicit saved-artifact receipts and reliable dependency/context closure before another autonomous acceptance run. Failed proposals must remain inspectable without appearing as accepted hypotheses.

This run is a meaningful **assisted** scientific test. It does not finish the analyze → literature → proposal → independent criticism → autonomous assessment → independent review → accepted revision cycle.

Verification: **63 focused Python tests passed**, including the provider adapter, discovery controller, assessment authority/replay/real numerical measurements, knowledge, literature and existing research. TypeScript and the isolated production frontend build passed. The full regression suite was not rerun for this checkpoint. [Focused test report](../runs/discovery/grating-luna-20260928/pytest.xml).

## Researcher comments

Please review the scientific argument and its revision through **Notebook → Discovery → Scientific dialogue**, then compare with **Experiments**. Useful feedback is whether the evidence justifies the narrower hypothesis and which additional dialogue/context would let you steer the next experiment.

<!-- Add review comments here. -->
