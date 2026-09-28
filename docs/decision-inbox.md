# Decision inbox

The default inbox shows requests for researcher judgment. Clear, current
requests appear before recommendations that need updating or clarification.
Each card separates its title, brief background, explicit proposal, choices,
and the manager's recommendation. Choice descriptions explain their effects;
recommendation badges never select an option automatically.

Full original context, original choices and exact proposed action parameters
are available under **Original context and decision details**. Recorded history
uses the original choice labels. Short display text does not rewrite a saved
decision, change its option IDs, renew authority or widen its scope.

Old questions explicitly addressed to the manager appear under **Manager
follow-ups**. They can be sent for a manager review without requiring a user
answer. Historical questions with unclear alternatives offer **Ask manager to
clarify**; the system does not reinterpret “Follow the proposed direction” as
an invented yes/no approval. These clarifications are requested individually,
separately from the existing bulk update of outdated action recommendations.

Reassessment can use parallel reviewer groups. The inbox exposes a concurrency
limit and shows one progress panel per batch, including the final manager
consolidation stage and links to the agent log. Failed-only retries preserve
completed assessments. See the reconsideration document for scope and recovery
details.

## New requests from agents

`RoleResult.decision_requests` accepts at most three structured direction
requests per manager response. Each has:

- `title`, `background`, and `proposal` with bounded text lengths;
- two to four options with distinct `id`, concrete `label`, and `description`;
- a valid recommended option ID and a `recommendation_reason`.

Only the last completed campaign-manager synthesis publishes these requests.
Specialist questions remain in the role results for the manager. An older
provider's unstructured manager question becomes a manager follow-up requiring
clarification, with no fabricated options. Identical action proposals are
deduplicated while their original role responses remain in the research log.

Direction choices record guidance; they do not directly launch work or change
resource limits. Executable action proposals retain their separate typed
approval path. Their choices describe the actual operation: saving a draft,
requesting a review, creating a study, applying exact campaign changes, or
launching a particular experiment. Defer and Decline record a disposition of
that recommendation and do not cancel unrelated work or reject a method family.

This update does not turn individual answers into standing policies. Existing
campaign authority, validation, revision and resource checks remain in force.
See [decision reconsideration](decision-reconsideration.md) for the review flow.
