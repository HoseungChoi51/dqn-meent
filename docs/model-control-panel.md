# Campaign model settings

Open **Models** in the workspace sidebar. Settings belong to the selected
campaign and persist across server restarts. Editing this panel does not start
or resume research. Other campaigns retain their own settings.

Use the role preset, adjust any model or reasoning effort, then save:

| Assignment | Model | Reasoning effort |
| --- | --- | --- |
| Campaign manager | `gpt-6-astra` | Extra high (`xhigh`) |
| Literature investigator | `gpt-6-luna` | Extra high (`xhigh`) |
| Methodology specialist | `gpt-6-luna` | Extra high (`xhigh`) |
| Default, including other researchers, analysts, reviewers and implementation agents | `gpt-6-sol` | Extra high (`xhigh`) |

Model identifiers are editable; the suggestions are not a restriction. Access
and supported effort levels depend on the configured provider and account.
The panel saves settings without sending a model request.

Exact role overrides take precedence. The literature investigator and
methodology specialist assignments also cover roles containing those complete
underscore-separated family names, such as `cross_domain_methodology_specialist`
and `methodology_specialist_2`. Other new roles inherit the default. The legacy
`research_synthesizer` role uses the campaign manager assignment unless it has
its own exact override. Effective assignments are visible in the panel.

Before a campaign has saved model settings, the panel shows the server default
and any older discovery session overrides. Saving makes the campaign policy
authoritative for research and implementation work. Older session overrides
remain in historical records but no longer control new calls for that campaign.

## When a change takes effect

Discovery freezes the effective model and effort in each dispatched attempt.
Saving during a call leaves that attempt unchanged; the next newly dispatched
step uses the updated settings, including continuations of an existing task.
Previously dispatched attempts retain their snapshot during recovery.

Legacy research runs and implementation jobs freeze the complete role policy
when admitted. Subsequent calls and repairs within those jobs retain it.
New runs and jobs use the newly saved policy. Implementation model choices
travel to the separate implementation service with its job request.

Each change has an immutable revision and a researcher command receipt.
Concurrent edits are rejected when their revision is stale; reload the panel
before reapplying the edit. Actual provider request receipts include the model
and reasoning effort, available through the research notebook's agent log.

Provider selection, activation, authentication and existing budgets remain
server/resource settings. This panel cannot enable calls or change billing
mode. API users can enter input and output rates per million tokens for each
binding. Changing models never carries over a different model's prices;
budgeted API calls with unknown prices are blocked before dispatch.
Blank rates in a saved policy mean unknown, even for the server's default model.
Compatible chat endpoints currently receive model identifiers but not reasoning
effort; the panel labels that transport limitation. Codex and OpenAI Responses
receive both settings.

## API

Read `GET /api/campaigns/{campaign_id}/models`. Save through
`POST /api/v1/commands` using operation `models.configure`, the current campaign
revision, and payload:

```json
{
  "expected_revision": 0,
  "reason": "Select campaign models",
  "policy": {
    "default": {"model": "gpt-6-sol", "reasoning_effort": "xhigh"},
    "roles": {
      "campaign_manager": {"model": "gpt-6-astra", "reasoning_effort": "xhigh"},
      "literature_investigator": {"model": "gpt-6-luna", "reasoning_effort": "xhigh"},
      "methodology_specialist": {"model": "gpt-6-luna", "reasoning_effort": "xhigh"}
    }
  }
}
```

The inner revision is the model settings revision, separate from the campaign
charter revision. Only researcher commands can change this policy.
