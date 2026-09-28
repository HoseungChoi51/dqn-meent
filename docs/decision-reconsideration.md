# Updating outdated decisions

See [the decision inbox](decision-inbox.md) for structured requests, explicit
choice descriptions and the separate manager follow-up view.

An execution recommendation belongs to the charter and researcher guidance
under which it was generated. Changing a budget, study scope or guidance can
invalidate it. The inbox shows **Needs update** before offering execution;
Defer and Decline remain available. Experiment plans that lack an executable
design also need a manager review.

Use **Ask manager to update** on a card, or **Update outdated decisions** for
a batch. Notes are preserved exactly. This sends a review request through the
campaign manager's serialized queue. It does not approve the old action or
renew its authority. Related requests are grouped for independent comparative
reviews, followed by one campaign-manager synthesis. Every action
produced by this review requires a new researcher decision.

The **Parallel reviewers** control defaults to three concurrent calls and can
be set from one to eight. This preference is saved for the campaign in the
current browser. It is independent of experiment workers and does not change
the campaign charter. There can be fewer groups than the selected limit. Each
group receives its assigned originals, exact researcher comments, common
campaign evidence and the same frozen charter, guidance and model assignments.
The manager receives all originals and the completed assessments, including
dependencies and dissent, before publishing consolidated requests.

Batch progress shows completed, running and failed reviewer groups, followed
by **Manager consolidating**. The agent log retains each separate call and
response. Reviewers cannot publish user decisions or execute proposed work.
The scheduler keeps an allowance for the final manager call and accounts for
each reviewer independently; parallelism does not multiply numerical budgets.

If a reviewer fails, successful assessments remain saved. **Retry unfinished
reviews** reuses those assessments and starts new attempts only for unfinished
groups. A failed final synthesis can be retried without repeating the reviews.
Retries require unchanged campaign guidance and the same original decision
selection; changed direction requires a new reassessment. Uncertain dispatched
calls require reconciliation before another attempt. No uncertain call is
automatically replayed after a restart.

The inbox shows **Manager update requested** while queued or running and
**Review available** after a current, finalized manager response. Failed,
incomplete or subsequently outdated reviews can be requested again. A review
does not automatically resolve the original cards: its output can combine or
reject several ideas, so the originals remain available with their review
links and history. Accepting a new recommendation advances guidance; other
unexecuted recommendations may then require reconsideration.

## Persistence and command contract

`decision.refresh` is a researcher command with the current campaign revision
and a `decisions` array. Each entry supplies `decision_id`,
`expected_resolution_revision`, an optional exact `comment`, and an optional
`desired_choice`. The command also accepts a batch `comment` and
`max_parallel_reviews` (one through eight, default three). Duplicate
submissions reuse the recorded request rather than dispatching another review.

An explicit retry supplies `retry_run_id` and the complete original selection,
with no new comments or desired choices. It resumes the saved review snapshot
without treating the retry as new scientific guidance. Reviewer task membership,
attempts and results are stored separately; the parent research run owns only
the final manager call. Campaign usage includes each child and parent once.
Older already-admitted reviews retain their original sequential protocol.

The immutable `decision_refresh` record contains the original decision and
action snapshots, comments and authority metadata. The manager command links
to that record and compiles its context at dispatch, using current authority
and evidence. Protected evidence is checked again at dispatch. Campaign text
exports retain the request's source IDs, comments and manager-command link;
full snapshots remain in the saved record.

Large batches supply repeated exact text once using explicit references. The
review receives full command schemas for its scientific follow-up and selected
historical actions. Unselected informational questions retain their titles,
options, status and user comments; omitted historical model analyses are
identified by their saved record IDs. Current guidance, selected originals and
study measurements remain supplied within the existing context ceiling.
The latest non-automatic researcher request is pinned with its exact text and
source metadata, so a procedural refresh cannot lose the current scientific
direction when older conversation history is omitted.

Freshness returned by the state API is a computed projection. It does not
rewrite a decision's charter, an action's guidance revision, or the original
proposal. Direct execution commands still enforce those authority checks.
