# Architecture consolidation questions

Review date: 2026-09-24

Repositories reviewed: `~/Work/dqn-meent-latest` (`setup/latest`) and `~/Work/dqn-meent` (`main`), including their uncommitted work.

The current repository emphasizes persistent research coordination, experiment management, and reusable implementations. The sibling emphasizes scientific protocols, stronger baselines, DQN diagnostics, and physical analysis. These questions identify the decisions needed to consolidate those strengths.

Please edit inline or use the comment space under each question. Partial answers are welcome; mark unresolved decisions as open. Your comments will inform a subsequent development plan. No choices below are assumed to be approved.

Existing direction to preserve:

- Algorithm implementation and correctness validation belong to a separate service, distinct from measuring performance in the workbench.
- Validated implementations should be reusable in later experiments.
- Unexpected interactions should reach the researcher through the campaign manager, whose context persists in structured text across weeks of work.

## 1. Primary research outcome

**Question:** What is the primary research outcome: finding excellent geometries, discovering broadly useful optimizers, explaining DQN behavior, or demonstrating reusable learned policies? Which should drive the default workflow and evidence requirements?

**Context:** These goals need different success measures. A good geometry found during training does not necessarily demonstrate a useful final policy or a generally superior optimizer.

**Your comments:**

The goal of the 'research' is to find excellenet geometries. The goal of the framework is to help users find broadly useful optimizers. DQN is only an instance of optimzers. But it is a baseline for the specific example problem. The framework's architecture should prioritize the feature to find/develop broadly useful (and reusable) optimizers. 
Having said that, the design goal of the framework is NOT to solve the inverse Meent problem efficiently, but a general optimization-solving architecture that can also solve the inverse Meent problem with enough aid from SOTA LLMs and minimal human intervention.

## 2. Studies within a campaign

**Question:** Should a weeks-long campaign contain multiple explicitly frozen studies, each with its own methods, seed assignments, selection rules, and scientific claim?

**Context:** The workspace currently organizes campaigns and individual trials. The sibling also declares a complete study protocol spanning development, selection, controls, and confirmation. A study could provide that intermediate level of organization.

**Your comments:**

A study (,goal of) is frozen during a campaign.

## 3. Optimizer execution contract

**Question:** Should every optimizer use one ask/tell execution contract, or should specialized trusted training backends run under the same campaign controls?

**Context:** One contract simplifies ownership and accounting. Specialized backends can preserve the sibling's learner semantics and diagnostics more directly, but retain multiple execution paths. The current package interface does not expose frozen-policy evaluation.

**Your comments:**

An experiment should keep a frozen procedure. It's preferrable that every optimizer use one execution contract. But I'm not sure what "ask/tell" contract is.

## 4. Historical algorithm identities

**Question:** Which historical behaviors must remain reproducible? Should the workspace DQN, sibling reconstruction profiles, and both hillclimbers remain separately named methods, even where they eventually share code?

**Context:** The same names currently hide meaningful differences. The hillclimbers differ in initialization, acceptance of equal scores, neighborhood sampling, and restart rules. The DQN variants differ in episode semantics, target updates, initialization, and random streams.

**Your comments:**

Outside of the experiment record, methods that eventually share their code do not have to remain separately named. The trajectory shall live inside the record. It's enough that the final methods reproduce the same results. Not intermediate ones.

## 5. Types of confirmation

**Question:** Should fresh seeds on a known physical condition, unseen physical conditions, and transfer of a frozen policy each have their own explicit confirmation protocol and eligibility rules?

**Context:** The sibling confirms reproducibility using new seeds on the same condition. The workspace's exposure rules protect unseen physical conditions. These answer different questions and cannot share one undifferentiated confirmation label.

**Your comments:**

They are different rules. Each should have their own confirmation protocol.

## 6. Reusable assets

**Question:** Beyond algorithm implementations, which assets should be reusable: trained policies, geometry archives, datasets, study protocols, and reviewed findings?

**Context:** Each asset needs different compatibility, provenance, and exposure rules. Reusing code is different from giving an optimizer previously measured designs or a pretrained policy.

**Your comments:**

All 'can' be resuable, if they are meaningful. That is, a trained policy of a generalized/repeated problem is worth being reused. But a trained policy of a peculiar problem is not. The same holds for geometry archives, datasets, and so on. All can be reusable. But whether to actually reuse them is subject to individual decision (by either human or Agent)

## 7. Recovery precision for long runs

**Question:** Must every completed evaluation survive interruption, or is bounded replay from periodic checkpoints acceptable if repeated work and uncertain cost remain visible?

**Context:** The workspace writes a full checkpoint after every evaluation. The sibling checkpoints periodically. Its million-transition replay buffer contains roughly 500 MiB of arrays, while the current reusable package interface limits checkpoints to 16 MiB. Long training runs need an explicit recovery and storage policy.

**Your comments:**

Periodic checkpoints is acceptable.

## 8. Shared computation and comparison costs

**Question:** Should comparisons show both actual campaign expenditure and the full upstream cost attributable to a method's result? Which cost should determine the primary ranking?

**Context:** A hill-climbing prefix can execute once and feed both a continued baseline and a refinement method. Physical expenditure counts that prefix once; each method's logical budget includes its contribution. Action counts, evaluation requests, actual solver calls, elapsed time, and implementation costs also measure different things.

**Your comments:**

Prioritize full upstream cost.

## 9. Manager authority after a study is frozen

**Question:** Can the campaign manager extend budgets or alter methods within a frozen study, or should scientific changes create a new study revision while preserving the original outcome?

**Context:** The workspace supports adaptive research and experiment extensions. The sibling's verdict depends on a declared protocol and bounded effort. Routine recovery and scientific changes need distinguishable treatment, with unexpected decisions still routed through the manager.

**Your comments:**

Campaign manager can suggest to extend budgets. Other (than budget) changes in science should create a new study.

## 10. Required validation and its timing

**Question:** Which validations should be mandatory, and when? Should higher-order reevaluation happen periodically during search, on candidate promotion, or only at the end? When should policy diagnostics or field analysis become required?

**Context:** Implementation correctness, optical convergence, policy behavior, and reproducibility are separate validation questions. The sibling uses Fourier orders 320 and 480 with tighter study tolerances; the workspace API currently caps orders at 256. Validation work also needs its own recorded costs.

**Your comments:**

It's basically non-E2E framework. All mentioned stage can require validation. But many of the requests will be waived as a campaign evolves

## 11. Authority and structure of persistent context

**Question:** Is the current database with editable Markdown projections appropriate, or should structured text itself be the portable, directly editable campaign record? How should researcher-approved conclusions differ from provisional agent interpretations?

**Context:** Both choices can preserve long-lived structured context. Today SQLite is authoritative and Markdown projections are rebuildable. Large trajectories should remain linked evidence; durable findings need their conditions, limitations, and supporting artifacts preserved.

**Your comments:**

Don't worry about large trajectories yet. Either way will produce comprehensible amount of record anyways. Use one that will make later replacing easier.

## 12. Incorporating existing sibling results

**Question:** Should existing results enter future campaigns as historical reference evidence, as seeds or data available to algorithms, or both through explicit declarations?

**Context:** The sibling contains completed campaigns, reference geometries, and a nine-condition sweep. Reading those results or using their designs affects which later conditions and claims can still be considered fresh. Importing evidence must also preserve the original method identities, fidelity, budgets, and provenance.

**Your comments:**

Making most of past experience is important. Both through explicit declarations.

## Additional constraints or corrections

Use this space for corrections to the review, priorities, requirements that should stay unchanged, or decisions that can be left for later.

Some questions assumes that the framework's goal is to solve the inverse grating design using Meent is the main goal of design. That is NOT the case. As I've stated in my comment to question 1, the goal is to design a general optimization-solving architecture that can also solve the inverse Meent problem with enough aid from SOTA LLMs and minimal human intervention.