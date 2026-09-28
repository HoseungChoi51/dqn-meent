# Imported-source reproduction

This is the C4 implementation handoff from the
[consolidation plan](architecture-consolidation-plan.md#c4--make-reproduction-an-explicit-new-execution).
Source resolution, compiler isolation and the supervised numerical-worker
lifecycle support an explicit reproduction draft and frozen comparison through
the shared commands. Importing a bundle keeps its historical jobs archived;
reproduction allocates a separate exploratory experiment. The product workflow
and supported legacy conversion are described below. Recorded checkpoints retain
the verification scope of each increment.

## Implemented source boundary

`storage.history.exact` selects an admitted `RecordKey`, including its source ID
and content digest. It verifies the selected record without choosing a newer
snapshot of the same mutable producer.

`assets.captures.experiment` reconstructs the selected trial's code, execution
manifest and dependency locks from verified content-addressed blobs. It does not
depend on the original directory or an import's temporary staging location.
Repeated resolution uses the same content-derived cache. Damaged materialized
source remains an explicit error rather than being replaced under a reader.
Historical results, observations and the old operational specification are not
copied into that source cache.

`execution.provenance.invoke(..., isolated=True)` runs a captured compiler using
the installed host's `execution.isolation` policy. It verifies the source and
recorded runtime requirements before execution. The Linux namespace contains
the source, selected installed runtime distributions and native libraries, the
request and bounded scratch space. It has no inherited credentials, host network
or surrounding workspace files. Standard Python startup hooks are disabled;
unrecorded source bytecode is rejected. Only one bounded result file leaves the
compiler's scratch filesystem. Timeout terminates the namespace, and missing
isolation support does not trigger ordinary subprocess execution.

Framework runtime schema 1 records interpreter/platform requirements, package
versions and lock files. These checks retain that historical scope; they are not
a new claim that those old manifests fingerprint every installed runtime byte.
Generated optimizer/evaluator packages retain their separate content-verified
runtime and correctness requirements.

The new tests execute actual captured Python through bubblewrap. They cover
equivalent preparation after installed preparation changes, blocked credential
and undeclared-result reads, read-only source, unavailable host networking,
timeout, missing isolation support, unrecorded bytecode,
source resolution after directory removal, and changed/missing snapshot
identities. Model services are not involved.

The [foundation checkpoint](consolidation-progress.md#imported-source-and-compiler-foundation--2026-09-27)
records 410 passing Python tests and an isolated probe of an actual older C3
capture that matches its original prepared procedure. That checkpoint qualified
the compiler boundary; subsequent numerical and scheduler evidence is below.

## Numerical-host foundation

The current tree adds `execution.isolated_host`, `execution.publication` and the
supervisor-owned `execution.package_host`/`package_client` pair. The previously
failing generated-evaluator case now passes. Package sandboxes start as separate
supervised processes; they no longer require creating a nested namespace from
inside the captured worker. The subsequent storage changes additionally bound
scratch and shared memory and make the device directory read-only.

The package host verifies the frozen version, artifact, package files and runtime
before accepting an exact launch vector. It reads the captured runtime's literal
driver without executing it in the host. That driver runs inside the package
sandbox with host-enforced hard resource limits. Arbitrary commands, changed
mounts and changed drivers are rejected. A standalone installed launcher relays
the captured worker's existing package protocol; the archived source is unchanged.

A private Unix socket transfers only the launched package's three pipe
descriptors, never host file/directory descriptors. The relay preserves separate
stdout/stderr and uses at most 64 KiB of pending bytes per stream. Handshakes are
bounded at 1 MiB and five seconds. The host limits concurrent connections and
total launches, terminates packages on owner disconnect or deadline, and retains
their process outcomes in its receipt. The package namespace has no access to
the launch socket, other packages or the surrounding workspace.

Numerical workers write into a private attempt filesystem with a kernel byte
limit. An installed Python prelude runs with isolated startup before captured
imports, transfers its directory descriptor to the host, and waits while the
host seeds committed recovery state. A watchdog enforces the entry allowance
and records observed overshoot; this is not a hard inode limit. Descriptor-relative reads
reject symlinks, hardlinks, special files, external artifact-location declarations
and altered or removed journal prefixes before copying evidence into the
host-owned experiment projection. Blob hashes and sizes are checked before
publishing their manifests. A separate deadline thread terminates work without
waiting for output publication. Host leases and receipts live outside the private
tree; re-entry with a lease from another attempt fails before recovery.
Publication accounts for existing evidence across attempts, metadata and
temporary replacement copies under one allowance. Limit exhaustion preserves
committed evidence and produces an explicit resource outcome.

Real checks cover built-in and generated executables, a checkpoint above 16 MiB,
cooperative pause and restoration in a second attempt, rejected package-launch
mutations, client-disconnection cleanup and independent deadline enforcement.
An [older-capture probe](../runs/consolidation/imported-package-host-20260927/older-capture-probe.json)
also executes an actual C3 worker with its original inline package launcher and
a copied verified evaluator runtime. Its three candidate/objective observations
exactly match the historical trajectory. The original source/specification stays
unchanged, the probe has a new experiment identity, and no native campaign is
allocated. This is direct host compatibility evidence, not a product command or
a complete accounting/reproduction workflow.

The [host checkpoint](consolidation-progress.md#imported-numerical-and-package-host-foundation--2026-09-27)
records 422 passing Python tests on a 304-file source manifest. The source was
unchanged after verification. Frontend source is unchanged and no new browser
or real-model qualification is claimed by these host checks.

The latest focused run of `test_framework_isolated_host.py`,
`test_framework_imported_execution.py` and `test_framework_packages.py` passes
**28 tests** in 43.43 seconds. It covers private byte and entry enforcement,
scratch/shared-memory limits, cumulative publication allowances, trusted startup
before captured imports, and actual supervisor death. In that death case,
children terminate and reconciliation preserves the original lease, committed
observations and checkpoint without executing code again, even with captured
source unavailable. The original elapsed work remains unknown. This focused run
precedes the scheduler integration and 437-test checkpoint below; the earlier
422-test checkpoint applies to its own recorded source.

Validated host publication is the durable commit boundary. Unpublished private
data may disappear when the supervisor dies; neither an absent journal suffix
nor a missing elapsed measurement proves zero work. Reconcile that uncertainty
using C3's ledger, without restarting the same attempt. Resume is a new attempt
from a committed checkpoint. Package execution time is part of the containing
worker/host interval and must not be charged a second time.

## Scheduler and ledger integration

`execution.supervision` now connects the host to the ordinary workspace scheduler.
The immutable experiment specification pins a typed isolation/storage policy;
editing a mutable trial cannot remove or relax it. Internal captured-source
admission selects that policy automatically. Source-derived recipes and inference
compilers use isolation, and diagnostic children retain the parent's requirement.
This admission path also serves the historical reproduction draft and command
described below.

The workspace launches installed supervisor code with a clean environment and
its own working directory. After reconstruction it adopts only a matching host
lease, ignoring worker-written namespace leases. Pause, stop and resume use the
ordinary control/outbox path. The host independently bounds uncooperative
controls; resume starts another attempt and preserves the frozen procedure.
An interrupted host is reconciled without requiring its captured source to be
available and without rerunning its numerical work.

Host receipts enter the immutable evidence store and exported dependency graph.
For these new isolated attempts, request costs, worker overhead, supervisor
overhead and any unconfirmed suffix share one attempt cost source. The C3 ledger
can establish a measured whole-attempt duration while preserving unknown
individual shares and request counts. Upstream executable production evidence
remains part of result costs. Conservative time bounds retained for admission
do not replace missing measurements; see [accounting](accounting.md#isolated-numerical-attempts).

The focused scheduler checks use real workers and generated packages with
fixture semantic reviews. They cover both executable kinds, host adoption,
pause/resume, policy tampering, inherited diagnostics, missing source after host
death, forced stop, repeated reconciliation and exported receipts. The final
full-suite [checkpoint](consolidation-progress.md#imported-scheduler-and-storage-checkpoint--2026-09-27)
records **437 passing tests**, a 310-file unchanged source manifest and another
exact older-capture probe. An additional supervisor-loss probe resumes from a
published checkpoint, exhausts its original request allowance, then completes
after an explicit budget amendment with the same frozen procedure. Unknown
earlier costs remain visible. This evidence remains separate from browser
reproduction and real-model qualification.

## Product workflow

The asset library exposes historical optimization experiments from bundles
published into the current campaign. Each choice selects an exact `RecordKey`,
including its source and record digest. A globally archived record does not by
itself grant a different campaign access. Protected historical cohorts require
their recorded result release. Unsupported historical procedures remain
inspectable with an explanation.

1. Select the archived experiment and a compatible destination problem, then
   design a reproduction. The shared `reproduction.draft` command saves a linked
   draft without allocating execution.
2. Review its original procedure, required inputs and readiness. Parameters,
   seeds, completion, recovery, diagnostics and input identities remain fixed.
   Resource allowances and the numerical comparison rule can be edited before
   launch. Each save creates a revision. The original inputs require explicit
   reuse decisions scoped to the destination; using a result as a comparison
   reference does not implicitly provide its assets to the optimizer.
3. Resolve missing compatible runtimes and satisfy current executable eligibility.
   Imported versions may require local revalidation. Readiness checks the exact
   scientific problem, evaluator, source capture, executable identities and input
   digests. Missing source or isolation support blocks allocation with a reason.
4. Launch through the ordinary `draft.launch` command. It freezes the reference,
   captured-source identity, local runtime conversion, procedure and comparison
   rule in a new exploratory experiment. An accepted retry returns the same
   experiment. The old job remains archived, with its original authority and
   confirmation membership.
5. The common scheduler runs the captured worker under the installed supervisor.
   Its controls, publication boundary, periodic recovery and cost receipts apply
   as described above. Descendant diagnostics retain the frozen isolation policy.
6. Committed terminal attempt evidence produces an immutable comparison. The
   shared `reproduction.compare` operation can request the same assessment
   idempotently. Recovery can add a later comparison without erasing an earlier
   inconclusive outcome.

`result_v1` compares the best primary objective under the frozen absolute and
relative tolerances, and optionally its candidate. Both results must have
scientific completion evidence. A stopped or incompletely evidenced procedure
is inconclusive; a mismatch in complete evidence is disagreement. Agreement
describes these outputs, not the complete trajectory, optimizer superiority or
independent evaluator correctness. The interface and report retain that scope.

New execution and host work enter the new attempt's ledger once. The historical
execution remains a comparison reference; only actual input dependencies carry
its costs upstream. Optimizer/evaluator production, local resolution and new
revalidation retain their ordinary lineage. Export carries the comparison and
the exact archived reference closure. Manager context receives source metadata
and visible comparisons through the existing evidence boundary.

## Supported legacy runtime conversion

`implementations.runtime_conversion` supports path-dependent schema-1 optimizer
and evaluator runtime manifests. It verifies every recorded content measurement:
protocol, Python version, interpreter hash, dependency declarations, dependency
files and native libraries. A mismatch leaves the runtime unavailable. Resolution
uses matching local content without downloading or rewriting historical package,
version or runtime identities.

The converter appends a report and a schema-2 local runtime binding. Legacy
manifests did not hash the standard library: historical byte equivalence remains
unknown and is displayed in readiness and comparison evidence. The selected
local standard library is now hashed and pinned. This is supported reproduction
with an explicit evidence limitation, not proof of a byte-identical historical
environment. Replacing that local conversion after an experiment freezes blocks
execution and requires a new linked experiment.

Only the installed host constructs the read-only aliases required by the
verified legacy launcher. Imported code cannot request arbitrary host commands
or mounts. Unsupported execution contracts and historical child analyses need
their own procedure/input conversion; their readable history is retained.

## Qualification and remaining integration

The reproduction checks cover another directory with the original unavailable,
captured preparation despite installed-code changes, frozen draft revisions,
missing prerequisites, explicit input reuse, recovery, comparison retention,
costs and export. A generated evaluator case uses a representative schema-1
manifest, verifies local conversion and independent revalidation, then executes
the captured worker. It also rejects a later change to the frozen conversion.
The earlier actual C3 worker probe remains separate compatibility evidence;
the representative manifest test is not an exhaustive historical converter.

The browser imports an archive, edits a reproduction draft, loses an accepted
launch response, retries and inspects agreement from one new experiment. Both
isolated services also restart without changing the recorded scientific or
command evidence or adding costs when the original launch command is replayed.
See the [reproduction checkpoint](consolidation-progress.md#historical-reproduction-checkpoint--2026-09-27)
for exact source and test scope. Model reviews are fixtures; the numerical
processes and HTTP/browser workflow are real.

Preserve this workflow while completing D1's remaining application controls,
D2's durable manager lifecycle, E's history import/migration and F's real-model
and researcher qualification. Those gates remain required before release.
