# Registered diagnostic and inference extensions

The worker captures immutable milestone artifacts. The workspace dispatches their
declared diagnostics as ordinary dependent jobs, each with its own seed, limits,
costs and evidence. Inference does not update the parent optimizer or contribute
new solutions to its search comparison.

The bundled adapter is `dqn_policy:v1`. An independent continuous constant-point
format in the [extension fixture](../tests/fixtures/inference_extension.py)
exercises the same registration, source capture, worker and recovery boundaries.
It is test evidence, not an additional installed research method.

## Optimizer declarations

Bundled method metadata and implementation specifications expose
`execution_capabilities`. For example:

```json
{
  "completion_units": ["evaluation_requests", "optimizer_decisions"],
  "exports": [
    {"kind": "policy", "format": "constant-point:v1", "metadata": {}}
  ]
}
```

Evaluation requests are counted by the worker. A declared optimizer decision
counter is the nonnegative integer `decisions` value from `inspect()`. Its meaning
belongs to the method; it is not necessarily a request, a solver execution or a
learning update. An implementation without a decision-counter declaration uses
request-based completion and milestones.

An export identifies an existing asset kind, a versioned format string, and any
required metadata. `export_artifacts()` returns objects containing `kind`,
`format`, `metadata`, `data`, and optionally `media_type`. Generated packages use
JSON-serializable payloads through `optimizer_v1`; reviewed native code can also
return bytes. The worker stores the payload in the artifact store and produces
verified references. Artifact export must not advance the optimizer's search or
random stream.

The implementation service checks declared counters for type and monotonicity,
requires the advertised exports, and checks that export preserves the next
proposal and its identity. Its recorded scope is bounded correctness evidence,
not a claim about policy quality. Counter semantics and algorithm behavior still
belong in the frozen acceptance criteria and independent implementation review.
Existing packages without declarations remain usable for request-based search;
advertising new capabilities requires a new validated version.

## Installed inference adapters

An inference adapter is reviewed installed code in an importable Python package.
Register its version explicitly:

```toml
[project.entry-points."optimization_framework.inference"]
"my_policy:v1" = "my_extension.inference:Adapter"
```

Duplicate identities, including attempts to replace a bundled identity, are
rejected. An agent's generated optimizer cannot submit a module path through an
experiment request. Generated packages can export compatible formats; installing
a new inference adapter remains an installed-extension operation.

The adapter supplies these methods:

| Method | Responsibility |
|---|---|
| `describe()` | Return an `InferenceDescriptor`: exact version, title, accepted artifact kind/format/metadata, candidate representations, constraint support, parameter schema and completion capabilities. This release requires `adaptation="forbidden"`. |
| `prepare(instance, parameters, asset=None)` | Normalize parameters and validate compatibility. Admission can call it before the artifact exists; dispatch calls it with the exact captured asset. |
| `procedure(instance, parameters)` | Return bounded `max_steps`, `schedule_steps` and `completion`. These are validated before admitting parent diagnostic work. Initialization and reset allowances belong here. |
| `create(instance, parameters, seed, asset, artifact_store)` | Load the verified artifact and return a common-lifecycle optimizer, already initialized. Its proposals are evaluated by the ordinary worker. Preserve inference state and input identity across checkpoint/restore. |

See the [DQN adapter](../src/optimization_framework/optimizers/policy.py),
[registry](../src/optimization_framework/evaluation/inference.py), and
[typed declarations](../src/optimization_framework/contracts/capabilities.py).
Parameter schemas support the same explicit types and bounds as implementation
parameters; schema titles and descriptions supply form labels and explanations.

Exactly one exported artifact must match an inference declaration. The format's
metadata predicates can distinguish outputs. A missing or ambiguous match yields
a campaign-manager issue; dispatch does not choose another artifact arbitrarily.
Unknown policy formats remain archived assets and explicit missing capabilities.
An optimizer need not export policies to be useful.

## Declaring a diagnostic

The experiment form exposes compatible adapters from
`GET /api/v1/inference-adapters` and the optimizer's capability declaration.
An equivalent API declaration is:

```json
{
  "unit": "optimizer_decisions",
  "at_counts": [10, 20],
  "export_optimizer": true,
  "rollouts": [
    {
      "kind": "artifact_inference:v1",
      "adapter_id": "dqn_policy:v1",
      "parameters": {"epsilon": 0, "horizon": 8, "tie_break": "first"},
      "seed": {
        "kind": "affine:v1",
        "offset": 100,
        "parent_seed_factor": 1000,
        "milestone_factor": 10,
        "episode_factor": 1
      },
      "wall_seconds": 10,
      "recipes": []
    }
  ],
  "recipes": []
}
```

Seed derivation is the sum of the offset and each declared multiplier times the
parent seed, milestone count or zero-based episode index. Overflow is rejected
before parent allocation. A literal unsigned 32-bit seed is also supported.
Recipes on the schedule check the parent snapshot; recipes inside an inference
check that child's result. Both reserve their own declared wall-time caps.

The historical `PolicyRollout` declaration remains supported. Its serialization
and method identity are preserved; it projects to the bundled DQN semantics.
New declarations name an adapter and its parameters explicitly.

## Frozen execution and recovery

Source capture records installed inference entry points, extension source,
dependencies and compiler operations in the experiment manifest. A milestone
compiles through that captured `inference.compile` operation. Its child inherits
the parent's captured source and any absolute deadline and execution grant.
Changing or removing an installed adapter cannot reinterpret existing work.

Dispatch waits for both the snapshot and its measured cost interval to commit.
Ingestion, input-reuse decisions, reservation transfer and child records commit
in one transaction. A restart retries an uncommitted dispatch; a committed grant
returns its existing children. The child's attributed cost includes the exact
upstream prefix and applicable implementation/validation contributions, not the
parent's later search. Registered read-only inference can also be used in a
policy-transfer confirmation protocol, with an explicit input-reuse decision
for that study.

The [integration tests](../tests/test_framework_inference.py) execute a generated
continuous optimizer and its installed inference extension through archived
workers. They interrupt dispatch after allocation but before commit, restart the
workspace, remove/change the installed adapter, and verify one committed child
set, unchanged snapshots, retained deadlines, nested validation and full cost
lineage. The browser scenario additionally exercises real DQN/MEENT inference
through the form. These are bounded software checks, not a production scientific
verdict or live-model qualification.
