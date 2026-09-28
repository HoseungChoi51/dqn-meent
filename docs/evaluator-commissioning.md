# Commissioning an optimizer and its problem evaluator

The workspace can retain a problem declaration before its evaluator exists. A
saved experiment draft keeps that requirement alongside any missing optimizer.
The implementation service builds or imports each executable separately and
checks it before the workspace can bind an exact version.

This workflow is part of the ongoing [consolidation](architecture-consolidation-plan.md#12-further-development-from-the-current-branch).
Default commissioning requires independent numerical fixtures for generated
evaluators. Explicit contract-only commissioning supports a separate,
study-scoped exploratory-waiver path described below. Generated evaluators can
compose reviewed registered analysis recipes. The library also provides
standalone revalidation and explicit reuse/decline decisions. Their current
qualification state is recorded in the consolidation progress log; real-model
acceptance and the complete product release remain separate gates.

## Researcher workflow

1. Create a campaign and select **Declare a problem · evaluator needed**. Define
   the candidate representation, dimensions, bounds or permitted values, and raw
   objective name, direction and units. The advanced manifest declares additional
   metrics, feasibility constraints, configuration and fidelity schemas/defaults.
   Choose supported analysis recipes before freezing the declaration.
2. Assign an implementation compute cap. Save an experiment draft from an idea
   even while its optimizer or evaluator is missing. Readiness lists the missing
   requirements; saving the design does not allocate numerical work.
3. In the problem workbench, choose **Build or reuse evaluator**. Supply the
   intended computation, acceptance criteria, exact dependencies, independent
   reference candidates/values and their basis. Source can be supplied as a
   package, or the configured model can construct a candidate. The model build
   and isolated candidate process do not receive the protected reference answers.
4. A successful commission binds the published evaluator to its original
   requirement. An existing library version can also be attached with a reuse
   rationale. The manifest must match; a binding cannot be replaced in place.
5. Request the optimizer implementation from its idea. Select the validation
   problem, supported dimension range, capabilities, parameters and optional
   independent behavioral checks. For a commissioned problem, the request pins
   the exact evaluator version used by correctness checks.
6. Open the saved draft and freeze/launch after readiness succeeds. The experiment
   pins both packages, runtime manifests and correctness evidence. A budget-only
   charter revision preserves the task, binding and study. A scientific change
   creates new linked records.

The library distinguishes optimizer and evaluator versions and attachment
targets. Job controls use the same durable command interface as commissioning.
Failures and unavailable versions return through the campaign manager; an issue
does not automatically block independent authorized experiments.

## Analysis with a commissioned evaluator

A problem manifest can declare versioned `recipe_ids`. Two reviewed recipes ship
with the framework:

| Recipe | Parameters and evidence |
|---|---|
| `candidate_reevaluation:v1` | `repeats` and a `fidelity` override object. Records fresh measurements, including for stochastic evaluators; creates no correctness or convergence assertion. |
| `fidelity_comparison:v1` | An ordered `fidelities` array of override objects, `absolute_tolerance` and `relative_tolerance`. For deterministic evaluators, compares the primary objective at the final two distinct settings. Earlier measurements remain available. This is a solution-fidelity check, not proof of numerical correctness or general convergence. |

For example, a quadratic evaluator that declares a `digits` fidelity control can
use this comparison:

```json
{
  "recipe_id": "fidelity_comparison:v1",
  "parameters": {
    "fidelities": [{"digits": 4}, {"digits": 8}],
    "absolute_tolerance": 0.0001,
    "relative_tolerance": 0
  }
}
```

Use **Validation** to select a completed or partially observed source experiment
and its candidates. The form reads that experiment's captured recipe definitions.
The same recipes can be declared in periodic diagnostics; assertion-producing
recipes can also be required by a study or template policy. Structured fidelity
settings use JSON fields. Unsupported recipes and invalid parameters fail before
work is reserved. An unknown declared recipe is an explicit unavailable capability
and does not prevent ordinary optimizer experiments.

Compilation and execution use the source experiment's exact evaluator binding,
captured recipe implementation, parameters and candidate snapshots. A child job
keeps separate observations and costs and includes its upstream search prefix and
implementation contributions once. Pause/resume restores the procedure and each
fidelity-specific evaluator host. Changing or uninstalling an installed recipe
does not replace the captured version.

A passing fidelity comparison does not satisfy a contract-only evaluator's
numerical-correctness requirement. Its exploratory waiver and confirmation
restrictions remain in force. Revalidation of the executable itself belongs to
the independent implementation service.

Historical captures without a recipe catalog expose an explicitly labeled
compatible definition where possible. A generated-evaluator experiment that did
not capture recipe registration requires a new experiment before those analyses
can be run; the workspace does not silently attach newly installed code.

### Adding reviewed recipe implementations

Install extensions as reviewed Python packages, using a versioned entry point:

```toml
[project.entry-points."optimization_framework.recipes"]
"my_measurements:v1" = "my_extension.recipes:Recipe"
```

The recipe implements `describe()` returning a `RecipeDescriptor`,
`parameters(adapter, problem, parameters)` to validate and normalize configuration
before allocation, `plan(adapter, problem, parameters, subjects)` to freeze
worker-owned evaluation cases, and `summarize(plan, observations)` to interpret
the recorded measurements. Assertion-producing recipes additionally declare
their validation kind, fixed subject and evidence rule in the plan. Measurement
recipes must not invent passing validation results.

The [independent extension fixture](../tests/fixtures/recipe_extension.py) and
[integration tests](../tests/test_framework_generated_recipes.py) demonstrate
source capture and execution after changing and unregistering the installed
extension. Generated evaluator packages name reviewed recipe IDs in their data
manifest; they cannot submit executable recipe code or module paths.

## Exploratory use without a numerical oracle

The evaluator request defaults to **Independent numerical references**. If no
independent oracle is available, explicitly choose **Contract checks only ·
exploratory use** and supply candidate/configuration/fidelity probes. These check
finite output, the declared measurement schema, determinism and checkpoint
continuation. They do not establish the numerical truth of the returned values.
Supplied numerical reference answers cannot be ignored by a contract-only request;
they belong to numerical validation.

A successful contract-only build publishes `contract_validated`, with
`numerical_status: unverified`. It can be attached to its frozen problem
requirement. Bounded optimizer implementation checks may use that evaluator,
recording the dependency's unverified numerical status. Experimental use still
requires its own authorization.

1. Define a linked exploratory study and select **Allow scoped evaluator waivers
   for exploratory use**. This permits an individual decision; it does not
   itself authorize an experiment. Manager waiver authority is a separate option.
2. Open **Review evaluator evidence** from the problem workbench. Validation
   identifies the study and numerical evidence requirement. The contract report
   is available as supporting evidence; it is not a numerical passing result.
3. Record a waiver with a rationale and supporting evidence. It binds the exact
   evaluator, requirement and study. A draft in that study can then freeze and
   launch with that authorization captured in its procedure.
4. Review or revoke the waiver in Validation. Revocation blocks new attempts,
   including recovery after restart. An already running attempt retains its
   captured code and receives one scoped manager issue so the researcher can
   decide whether to pause or stop it. Earlier observations retain their original
   evidence and authorization.

Mandatory contract failures cannot be waived. Confirmation cannot inherit an
exploratory waiver. A different study needs a separate decision; a new waiver
cannot silently replace an experiment's revoked authorization. An additional
waiver leaves an earlier experiment's still-active authorization valid.
Requirements are recorded for earlier applicable studies as well as the active
one when a missing evaluator is attached.

Missing numerical evidence remains visible as a requirement with a
**Revalidate evaluator** action. The implementation service checks the existing
executable independently of experiment scheduling.

## Independent revalidation and deliberate reuse

From **Validation and waivers**, select **Revalidate evaluator**, or choose
**Revalidate this executable** in the library. Supply independent numerical
fixtures, a rationale and a time cap. Optimizer versions accept independent
behavior checks through the same service. An empty additions list repeats the
existing checks; a contract-only evaluator needs at least one numerical fixture
to gain numerical evidence.

Revalidation uses the exact published source and runtime, makes no model calls
and preserves the executable identity. It retains prior checks and counterexamples
and appends a new report with measured costs. An interrupted check preserves its
partial work; a completed report can finish publication without rerunning it.
Missing runtime files require restoring that runtime before execution resumes.
Changing source, runtime dependencies or the executable contract creates a new
version.

New numerical evidence can authorize a newly frozen experiment. Earlier
experiments retain their original evidence or waiver and attributed costs. A
known correctness disagreement blocks further use. It also withdraws support
from confirmation assessment; an issued report gains a linked reassessment while
the original report, nomination and observations remain unchanged. Workspace
commit times determine which evidence was available before a selection cutoff.

In the library, select the target idea or problem and enter a **Reason for reuse
or decline**. Reuse checks the full declared problem and exact version, then
records the study, evidence and attribution with its binding. Decline records
why the candidate is unsuitable and leaves a specialized implementation available
as the next action. Neither decision launches an experiment or rebuilds the
candidate. Both decisions appear under **Executable reuse decisions**.

## Evaluator package contract

An `EvaluatorPackage` declares `kind: evaluator`, `contract: evaluator_v1`, an
entry point such as `evaluator:create_evaluator`, and relative source files.
The factory receives a context containing the resolved `problem`. Its object
implements:

```text
evaluate(candidate) -> {objectives: {name: finite number}, constraints?: {name: bool}, metadata?: object}
checkpoint() -> bytes
restore(bytes)
```

See [the small evaluator example](../examples/implementation-reference/continuous_evaluator.py)
and [its separate analytical fixtures](../tests/test_framework_evaluators.py).
Configuration and fidelity are data passed through the manifest; they cannot
contain executable resolver expressions. Optional returned constraints must agree
with the host's declared feasibility rules. Output must include every declared
objective and metric and cannot replace cost, observation or provenance fields.
Package metadata is retained under the `generated_evaluator` namespace.

The worker owns candidate validation, observation identities, deadlines and
accounting. Generated code runs in the isolated package host with declared
dependencies and without campaign files, credentials or network access. Evaluator
packages may declare numerical dependencies that optimizer packages cannot use
to bypass their assigned evaluator. Published source/runtime hashes are checked
before execution. Checkpoints include the host identity and evaluator state as
well as the worker's optimizer, schedule and accounting state.

Independent fixture checks cover raw numerical values, configuration/fidelity,
declared determinism and checkpoint continuation. A positive model review cannot
override a failed numerical check. Fixture agreement establishes evidence for
the recorded cases, not a proof over the whole declared domain or a performance
claim for an optimizer. Cost records count isolated evaluator invocations and
measured elapsed work; they do not instrument arithmetic or internal solver
operations inside candidate code.

## Application operations

The browser and manager submit `POST /api/v1/commands` with a stable command ID,
campaign ID and expected campaign revision. Commissioning operations are
`implementation.commission`, `evaluator.commission`, `implementation.attach`,
`evaluator.attach` and `implementation.control`. Draft operations are
`draft.save` and `draft.launch`. `study.create` freezes the waiver policy;
`validation.waive` and `validation.revoke_waiver` record separate decisions.
The server checks authority and scope and records
grants, delivery and outcomes. Retries retain their command identity.

`GET /api/v1/evaluator-contracts` exposes the declaration/specification/package
schemas. `GET /api/implementations` refreshes library metadata. Service tokens
stay on the server. Existing library publication never changes a frozen
experiment; revocation prevents a new launch, including a new recovery attempt.
Already running work retains its captured code and produces a manager issue.

## Isolated supplied-code qualification

`scripts/serve_commissioning_fixture.py` starts either side of the actual HTTP
connection with a mocked semantic reviewer. Its builder rejects requests without
supplied code. It never contacts a model provider. Run the two roles in separate
terminals, using the same disposable evidence directory:

```bash
.venv/bin/python scripts/serve_commissioning_fixture.py library \
  --directory runs/commissioning-check --port 8768
.venv/bin/python scripts/serve_commissioning_fixture.py workspace \
  --directory runs/commissioning-check --port 8769 \
  --library-url http://127.0.0.1:8768
```

Build the frontend, then run `frontend/tests/commissioning.spec.ts` with
`COMMISSIONING_TEST_URL=http://127.0.0.1:8769`. The scenario declares the problem,
keeps a missing-code draft, commissions both supplied packages, revises only the
budget and launches the original draft. It writes the evidence record and two
screenshots to the test output directory. Real-model qualification remains a
separate release gate.
