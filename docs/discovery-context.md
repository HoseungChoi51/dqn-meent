# Discovery working context

Campaign history is durable. An agent's working context is a bounded selection
from that history, compiled for one task and frozen with each dispatched model
attempt. A long campaign must not require replaying every earlier record in
every call.

The working selection distinguishes explicitly assigned evidence from provenance
links. Selecting a candidate does not select every sibling in its original
batch. Selecting a batch explicitly still supplies its candidates. Parent
lineage, independent reviews, dissent, current guidance and scientific
measurements remain identifiable; indirect material can be retrieved by its
exact saved record ID. An omitted body is marked as omitted, never presented as
read evidence or as a negative result.

Trial projections retain scientific settings and measurements while their full
deployment manifests and candidate archives remain in the original records.
Repeated passages, receipt bodies and candidate descriptions are not copied
multiple times into the same working context. Old tool replies retain their
original timestamps and are distinguished from current task state.

## Reading large records

`evidence.read`, `experiment.inspect` and `assessment.inspect` accept a record
ID, an optional JSON pointer, page offsets, and a `max_bytes` cap of 1–24 KiB.
Small replies retain their
ordinary shape. Large replies provide explicit field indexes or pages, with
exact pointers, continuation arguments and a snapshot hash. Agents can request
the required field or page instead of receiving an oversized full record.

The compiler budgets the combined tool replies and prior steps. The context
includes a feasible byte cap for the next read, so several individually small
replies do not consume the entire working allowance. A narrower assignment
inherits the researcher's selected parents and feedback, not every evidence
record attached to the earlier assignment.

Text pages are labeled as excerpts. They do not satisfy the requirement to have
received a complete source passage before citing it. Campaign boundaries,
independent-review access rules and the immutable tool-receipt log still apply.
The complete stored record is not rewritten by a working-context projection.

## Replacing blocked work

When narrowing an assignment, the campaign manager can call `task.supersede`
with the exact old and replacement task IDs and a reason. This records a formal
replacement instead of leaving the old task open. The initial implementation
only permits retiring an unsent assignment: it cannot erase completed work,
interrupt active model calls or discard reserved usage or tool effects.

An unsent manager task blocked during context preparation can rebuild its
context after an upgrade. Already dispatched attempts retain their frozen
inputs. Recovery does not turn a cancelled or superseded assignment back into
new work.
