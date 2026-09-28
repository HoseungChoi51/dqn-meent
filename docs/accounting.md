# Recorded expenditure and later receipts

The framework keeps two views of work. **Actual expenditure** belongs to the
campaign that performed it. **Full attributed cost** follows the declared inputs
and production work needed for a result, including reused implementations,
research contributions, runtime setup and validation. Shared contributions are
counted once within a result's lineage. Two results can each attribute the same
upstream work while the actual ledger records that work once.

Each quantity keeps its own unit: evaluation requests, solver executions, worker
time, implementation time, model time, model calls, input/output tokens and API
charges. These columns are separate measurements and must not be summed as if
they had one unit. Implementation elapsed time includes model waits; numerical
worker time excludes measured waits. Subscription calls and tokens remain
visible even when their paid API charge is zero.

## Missing measurements and new evidence

A missing measurement is unknown. It is distinct from an explicit zero, a
conservative reservation, a failed operation and a missing dependency. Failed or
interrupted work can still consume resources. Startup and ongoing reconciliation
capture its available usage without restarting the original work.

Original cost events and asset records are immutable. Service snapshots preserve
the cumulative receipt and the work boundary to which it applies. Additional
execution appends expenditure. A later receipt for existing work appends a
reconciliation; it does not charge the work again or edit old events.

A cumulative total does not establish every contribution inside it. For example,
if two interrupted attempts have unknown token counts and a later receipt reports
30 tokens for both, their combined total becomes 30. A result using only the
first attempt still has an unknown token total. A separate supported receipt for
10 tokens in the first attempt would then establish 20 for the second.

The accounting basis is complete, partial or unknown for the displayed axes.
An asset with complete recorded provenance can still have unknown quantities.
The interface shows measured amounts alongside unknown totals and retains the
receipts behind the projection. Contradictory totals are rejected; they do not
replace accepted measurements or manufacture negative expenditure.

## Record a later receipt

1. Open the result in **Research assets**, then expand **Recorded costs and later
   receipts**.
2. Select the cost source and the boundary covered by the receipt. A boundary
   identifies the first N cost entries; it is not a number of model calls or
   solver requests. **Cost boundaries and receipt history** shows the recorded
   source and receipt metadata.
3. Enter only reported quantities, leaving other fields blank. Reference saved
   findings, receipt snapshots or imported records that support the values, and
   explain their basis.
4. Record the receipt. The result's cost projection refreshes while its original
   asset and cost events remain unchanged.

The browser and manager use `cost.reconcile` through the shared command boundary.
It checks campaign visibility, source membership in the result's lineage,
supporting record availability, current authority and mathematical consistency.
Accepted retries return the original outcome. Rejected commands retain their
reasons. This accounting operation does not change a model provider's invoice or
the separate resource authorization of a job.

## Import, reuse and recovery

[Evidence bundles](evidence-bundles.md) carry cost events, cumulative snapshots,
reconciliations and the original prefix needed to verify each receipt. Original
source and campaign identities survive transfer. Repeated import does not add
charges. Local accounting cursors are derived from immutable evidence and are
not an imported authority.

Re-exporting an older imported asset includes later locally admitted receipts.
An incompatible receipt is rejected during inspection before evidence is
published. Imported production jobs remain archived; recording their costs does
not make them runnable jobs in the destination.

Reusing an executable retains its declared upstream research and production
inputs. Local runtime resolution and independent revalidation add their own
recorded work. Runtime resolution after an experiment was prepared is retained
as an operational contribution without changing its frozen scientific procedure.
Restart and repeated delivery retain the same physical charge.

Service job revisions prevent delayed responses from replacing newer accounting.
An event cursor continues to admit late receipts for settled jobs. A missing
receipt or an unresolved original prefix remains visible and can create a scoped
campaign-manager issue. No missing quantity is filled with a reservation or an
estimate merely to make a comparison complete.

## Isolated numerical attempts

Isolated captures use one cost source per attempt. Its positions contain the
worker overhead, request measurements, supervisor overhead and any unconfirmed
suffix. This permits a host receipt to establish the whole attempt's elapsed
time even when the worker did not report every internal interval. Unknown
request counts and unknown individual shares remain unknown. Package execution
is inside that containing interval and is not charged again as extra worker time.

Validated publication is the durable evidence boundary. A supervisor killed
before its final receipt leaves committed journals and checkpoints, its original
lease and the last publication marker. Reconciliation never runs that attempt
again. Missing private output can represent additional work; the absence of a
journal or terminal record does not establish zero consumption. A new attempt
uses a committed checkpoint and adds its own costs. Earlier events and receipts
retain their identities when the result is exported or reused.

Resource admission separately retains a conservative upper bound when an
interrupted attempt's duration is unknown. Resume and later reservations use
that allowance; the evidence ledger retains the measured lower bound and the
unknown total. An admission bound is never inserted as a measured cost. Earlier
ordinary-worker sources keep their existing cost positions; importing their
records does not convert their accounting layout.
