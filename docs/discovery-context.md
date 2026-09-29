# Discovery working context

Campaign history is durable. An agent's working context is a bounded selection
from that history, compiled for one task and frozen with each dispatched model
attempt. A long campaign must not require replaying every earlier record in
every call.

Manager context aims for 155 KiB after selection and compaction, with a hard
224 KiB application ceiling. The extra space accommodates a bounded comparison
cohort with every configuration, stopping rule and measured result retained;
it is not permission to replay unlimited history. For example, the completed
32-cell grating comparison exceeded the former 180 KiB ceiling even after
historical metadata was indexed. Current authority and cohort evidence remain
complete while repeated schemas, training settings and decision text use
explicit references to values supplied in the same context. Historical inbox
labels may be excerpts with exact record IDs; their stored requests remain
unchanged. This byte ceiling is an application policy, not a model context-window
specification.
Individual discovery task steps retain their separate 180 KiB ceiling and
bounded evidence-read protocol; the larger manager ceiling does not relax it.
Discovery compacts against a 170 KiB working target, leaving 10 KiB of headroom.
These are serialized UTF-8 byte counts, not model tokens. The common model
adapter also checks a 250,000-byte allowance for instructions, content and
framing. Raising just the discovery constant can therefore move the failure to
the adapter. Neither check specifies the provider's actual model context window.

The bounds apply to one request, not the accumulated research project. Their
purpose is to keep repeated prompts manageable and leave room for new evidence;
the exact numbers are conservative application defaults. Increasing a prompt
allowance requires checking the selected provider's input/output capacity and
the full request envelope. It does not solve indefinite growth of campaign
history. Discovery instead continues across model calls, saved artifacts and
specialist assignments within the researcher's existing allocations.

## Soft landing at an allocation boundary

Task limits are checkpoints, not invalid model responses. Each dispatched step
receives live remaining task calls, tool requests, session calls and source
requests. Agents are warned as the limit approaches; the last task call is for
saving findings or an explicit incomplete handoff. The existing session synthesis
reserve remains available to the manager. No allocations are automatically raised.

A tool batch that would exceed the allowance is deferred as a whole, without
executing any of its requests or consuming validation-correction attempts. If an
authorized model call remains, the agent gets one wrap-up turn using saved
evidence. Otherwise the controller immediately saves a durable handoff containing
the latest returned summary, work products, receipt IDs, deferred requests and
remaining questions. A handoff does not satisfy a completed scientific
prerequisite. The manager can assign a narrower continuation using saved evidence
while the session still has research allocation.

Exhausted source requests get a deferred receipt, not a network failure. API
spending, model-call and numerical compute ceilings still apply. When no
authorized synthesis fits, the controller saves an explicitly partial session
wrap-up and waits for direction; it does not declare scientific success or retry
forever. In-flight calls and uncertain provider reservations retain their normal
receipt/reconciliation handling. An explicit policy amendment can fund further
work. Known legacy task quota failures can be converted into saved handoffs on
recovery without another model call, rewriting a response, or changing guidance.

Under context pressure, unanswered informational decisions keep their exact
questions, choices, statuses and researcher comments, while historical model
rationale is indexed by its saved decision ID. The prompt explicitly labels the
omitted commentary. This does not answer or resolve those decisions. Explicitly
selected decisions, executable proposals, accounting reconciliation and decisions
currently executing retain their full context. Current guidance, authority and
scientific measurements remain supplied. Ordinary manager turns include scientific
action schemas; researcher-interface settings and controls remain available in
the UI and can be included by an explicitly scoped context request.

Manager curve preparation reads scalar measurements and selects the same maximum
40 observed points used by model prompts, including endpoints, before compiling
the full context. Candidate archives and complete journals remain saved with the
experiment. This avoids repeatedly copying large candidate archives into a prompt
that cannot use them.

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

## Paging a task's saved context

Many small records can exceed the working allowance even when every individual
record fits. Under pressure, discovery now pages whole background collections
(retrieval receipts, older proposal cards and navigation catalogs) before paging
assigned scientific bodies. The `discovery.context_pages` map lists each omitted
collection with its exact path, count, snapshot hash and read arguments. An empty
array with a page descriptor means **paged**, not absent evidence.

`context.read` uses the same pointer, offset, limit and byte-cap protocol as
`evidence.read`. Its record ID must be the current task's own research run; its
pointers address only that run's frozen `context_snapshot`. It cannot expose a
peer's context, another session, provider configuration or run trace. A reader
can follow `/discovery/retrieval_receipts`, then select a field of a returned
entry, or read an individually identified source passage with `evidence.read`.
Ordinary durable tool receipts record the actual returned content. Paging an
index does not count as reading its underlying sources.

If the assigned collection itself outgrows the request, it also has an explicit
snapshot page. Current authority, task instructions, guidance, scientific trial
settings/measurements and useful current tool replies stay in the working view.
Current replies are restored after archive paging if they fit, preventing an
index-only read loop. Exact source passages still require full passage content
for citations. No stored research is deleted, no allocation is raised, and no
new session is required to recover a task blocked by archive size.

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

## Source-reference integrity

Every artifact, including free-form syntheses and evidence handoff packets,
validates its structured evidence links before publication. Citation source,
capture and passage IDs must agree with the saved records, and cited passages
must actually have been supplied to that task. A mistaken source ID produces
feedback containing the exact source ID from the declared capture, allowing
the existing bounded correction turn to fix the metadata before successors
inherit it. Dependency expansion uses the same reference fields, including
singular `passage_id` and `dossier_id` links.

For previously saved artifacts, maintenance can record an immutable
`discovery_reference_correction`. This is limited to a nonexistent source ID
whose exact capture and passage records unambiguously identify the same source
in the same campaign. Conflicting existing sources and missing or mismatched
passages require review. Original artifacts, model responses and earlier
attempt snapshots remain unchanged. New evidence reads and the notebook show
the corrected reference with its original value, reason and supporting record
IDs. Raw history exports retain both the original and the linked correction.

After a repair, recovery rechecks assignments blocked before their first
dispatch and requeues those whose complete evidence scope now resolves. It
preserves task IDs, sibling independence, allocations and prior model usage;
it does not retry already sent work or resume stopped sessions.
