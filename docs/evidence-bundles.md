# Portable research evidence

The asset library supports exporting a result and its declared evidence, then
inspecting and importing that bundle into another workspace. Import preserves
the original asset, producer and cost-source identities. It associates the
assets with the destination campaign without changing which campaign performed
the work. Historical experiments and implementation jobs remain archived records,
outside both services' operational queues.

## Browser workflow

1. Finish or pause the producing experiment. Protected confirmation evidence
   needs its recorded result release before export.
2. In **Research assets**, select **Asset to export**, then **Export asset and
   dependencies**. Download the completed evidence bundle.
3. In the destination campaign's asset library, select the file and choose
   **Inspect bundle**. Review its scope, record and file counts, accounting basis,
   and missing historical evidence. Inspection verifies and stages the bytes;
   it does not publish research assets or executable versions.
4. Choose **Import inspected history**. The two services publish their own
   records, and the workspace retains a durable receipt. Repeating the import
   returns the same receipt for that campaign and bundle. Shared upstream assets
   and physical cost events are not duplicated by overlapping imports.
5. Open an imported asset to inspect its historical producer, objective
   interpretation, declared dependencies, artifact references and attributed
   costs. Local artifact availability is a separate projection of the bytes
   available in this workspace.

An imported executable enters the library with `validation_required`, unless
failure or revocation already blocks it. Its source and original reports remain
inspectable. Explicitly resolve a compatible local runtime and perform an
independent recheck before using it in a new experiment. Import does not install
dependencies, execute candidate code or start a model call. It also cannot clear
a locally recorded failure or revocation merely by importing an older pass.

## Format and publication

`optimization_evidence_v1` is a ZIP archive of uncompressed regular files. It
contains `manifest.json`, content-addressed record envelopes under `records/`,
and blobs under `blobs/`. Each record envelope identifies its owning service,
original service identity, kind, original record ID and content digest. The
manifest declares roots, dependency edges, file captures and explicit missing
evidence. Blob hashes identify physical bytes; original artifact references
retain their semantic media types.

The exporter includes available original assets, cost intervals and their
events, producer snapshots, frozen specifications, captured experiment/compiler
files, executable packages, production receipts, validation reports and exposure
releases. Operational queues and current manager projections are not imported
as runnable work. Captured paths must be relative and safe; readers do not use
archive extraction. Every record digest, immutable record hash and blob size/hash
is verified. Undeclared, duplicate, compressed, encrypted or nonregular members
are rejected. The current limits are 64 GiB per archive, 64 MiB per JSON member,
256 MiB of record metadata, and 100,000 each of records and blobs.

Inspection promotes verified bytes into local content storage and stages
captures in a private directory. It checks native identity conflicts, physical
cost positions, asset dependency cycles and executable evidence before
publication. Captured source manifests are verified without running their code.
Missing evidence stays attached to its owning record, including through
re-export. A missing source capture is not replaced with the installed framework.

Library publication archives production jobs and admits executable evidence.
Workspace publication then atomically archives the graph and publishes original
immutable asset/cost records plus destination associations. Each service has its
own transaction and stable publication identity; there is no distributed
transaction. A lost library acknowledgement leaves a durable workspace effect
pending. Retrying after restart recovers the library receipt and completes the
workspace transaction. A workspace transaction failure cannot leave half of its
asset or cost records published.

Historical trial snapshots and releases are explicitly consulted by exposure
checks. They are not returned by ordinary operational trial/job readers. Missing
historical trial exposure blocks optimizer-input and manager-reference reuse;
known protected evidence continues to require its release.

## Interfaces

| Owner | Interface | Result |
|---|---|---|
| Workspace | `POST /api/v1/commands`, `bundle.export` with `asset_ids` | Durable export operation |
| Workspace | `GET /api/v1/bundles/operations/{id}/download` | Verified completed ZIP |
| Workspace | `POST /api/v1/bundle-uploads`, raw ZIP body | Content-addressed upload identity |
| Workspace | `POST /api/v1/commands`, `bundle.inspect` with `upload_id` | Verified inspection and staged library evidence |
| Workspace | `POST /api/v1/commands`, `bundle.publish` with `inspection_id` | Publication receipt or recoverable pending operation |
| Workspace | `GET /api/v1/bundles/operations?campaign_id=...` | Campaign operation history |
| Workspace | `GET /api/v1/assets/{id}` | Original asset, local availability, historical producer and attributed costs |
| Library | `GET /v1/versions/{id}/export` | Executable source and historical evidence closure |
| Library | `POST /v1/imports/inspect` | Staged executable import |
| Library | `POST /v1/imports/{id}/publish` | Idempotent library publication receipt |

Workspace commands use the same expected campaign revision, authority and
idempotency envelope as other work. Library endpoints require the service token.
Unexpected delivery failures reach the campaign manager as scoped issues.

## Current boundaries

This increment transfers and preserves the recorded accounting basis; it does
not manufacture absent upstream receipts or convert unknown costs to zero.
Accounting can be complete, partial or unknown independently of byte integrity.
The [accounting ledger](accounting.md) reconstructs imported accounting positions
from original events and receipts. Later measurements append reconciliations;
additional work appends expenditure. Runtime resolution has its own contribution,
and re-exporting older imported assets includes subsequently admitted receipts.
Aggregate evidence can establish a total while individual shares remain unknown.
The consolidation progress report records the qualification state of this work.

Captured framework/compiler source is inspectable history. Explicit reproduction
from an imported capture still needs its complete execution workflow. The
[C4 source/compiler foundation](imported-reproduction.md) resolves exact admitted
snapshots from verified blobs and isolates compiler execution; it does not launch
historical jobs. Importing
an executable package provides the separate local runtime-resolution/revalidation
path described above. Legacy runtime manifests with embedded system paths still
need an explicit verified converter. Complete imports of both repositories and
live migration/rollback remain E; bounded real-model qualification and release
remain F.
