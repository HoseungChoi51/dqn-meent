# Paper2Agent trial for the DQN–MEENT reconstruction

Assessed 2026-09-18. Upstream: [jmiao24/Paper2Agent](https://github.com/jmiao24/Paper2Agent), commit [`8c2d059165ef8cdcb70dbea76655b9c2b55b38e6`](https://github.com/jmiao24/Paper2Agent/commit/8c2d059165ef8cdcb70dbea76655b9c2b55b38e6), dated 2026-09-17. Repository README, skill instructions, and commit were also read through the selected GitHub connector.

## Outcome

**Paper2Agent was actually tried on the requested paper, and its PDF ingestion worked.** Its `Paper2Skill` component prepared source snapshots and extracted all seven pages. The generated review queue remains unreviewed; no completed paper skill, MCP server, or scientific reproduction is claimed from this trial.

The current project is a skill-driven workflow operated by a coding-agent host. It has two relevant components:

- **Paper2Skill:** source ingestion, page-level extraction, review, and a verified reading package. This helped establish a traceable source-reading workflow.
- **Paper2MCP:** selection and verification of thin MCP wrappers around existing research repository code. It expressly requires each tool to bind to existing code and says not to invent missing scientific algorithms. It could wrap MEENT's forward solver, but it does not itself supply the DQN environment, action design, reward, replay training, or evaluation missing from MEENT.

For this request, recreating the DQN loop directly around MEENT is therefore the useful next step. An MCP wrapper is optional integration work after the scientific application exists. The user requested a working inverse-design setup rather than MCP packaging.

## Actual commands and results

The following commands ran from the cloned repository unless an absolute path is shown. `WORK` below abbreviates `/workspace/scratch/d36d27445c2a/research/Paper2Agent/assessment-work`; `PDF` abbreviates `/workspace/scratch/d36d27445c2a/research/2022ACSPhotonics.pdf`. They are explanatory placeholders, not required environment variables.

| Attempt | Result |
|---|---|
| `git clone --depth 1 https://github.com/jmiao24/Paper2Agent.git /workspace/scratch/d36d27445c2a/research/Paper2Agent` | Succeeded. |
| `git rev-parse HEAD` | `8c2d059165ef8cdcb70dbea76655b9c2b55b38e6`. |
| `python skills/paper2agent/paper2skill/scripts/paper_bundle.py --help` | Exit 0; supported commands are `prepare`, `extract`, `reuse-review`, `review-aid`, `build`, and `verify`. |
| `python skills/paper2agent/paper2mcp/scripts/verify_workflow.py --help` | Exit 0; this checks recorded workflow artifacts and agent separation; it does not launch a reproduction pipeline. |
| `python skills/paper2agent/paper2skill/scripts/paper_bundle.py prepare PDF --work WORK --name dqn-metagrating-paper --title 'Deep Reinforcement Learning for Inverse Design of Optical Metasurfaces' --main PDF` | Exit 0; one source inventoried and snapshotted. The initial descriptive title was subsequently replaced in the editable `bundle.json` by the exact paper title read from page 1: *Structural Optimization of a One-Dimensional Freeform Metagrating Deflector via Deep Reinforcement Learning*. |
| `python skills/paper2agent/paper2skill/scripts/paper_bundle.py extract --work WORK` | Exit 1: `ModuleNotFoundError: No module named 'pymupdf4llm'` in the existing interpreter. |
| `uv run skills/paper2agent/paper2skill/scripts/paper_bundle.py extract --work WORK` | Exit 0. The recommended isolated script environment installed 14 packages, inspected pages 1–7, and returned `status: needs_agent_review`. |
| `uv run skills/paper2agent/paper2skill/scripts/paper_bundle.py review-aid --work WORK` | Exit 0; produced two contact sheets, a seven-page review queue with 60 queue items, and `status: review_aid_ready`. Queue items are review prompts, not 60 demonstrated scientific errors. |

The script declares Python ≥3.11 and pins `pymupdf==1.28.2`, `pymupdf4llm==1.28.2`, `pypdf==6.18.1`, and `pillow==12.2.0`. The first direct-Python attempt used Python 3.12.14 with different preinstalled PDF packages; the successful `uv run` used the script's isolated declared dependencies.

No API credentials, paid LLM invocation, global skill installation, MCP client registration, or remote deployment was needed for this ingestion trial.

## Scope and retained evidence

Working evidence lives under `research/Paper2Agent/assessment-work/`:

- `inventory.json`, `bundle.json`, and `originals/` preserve inputs and source metadata.
- `documents/s001-2022acsphotonics/pages/` contains extracted page JSON.
- `documents/s001-2022acsphotonics/evidence/` and `previews/` preserve extraction evidence and rendered pages.
- `review-aid/review-queue.json` and contact sheets support subsequent page review.

Those are paths in the trial workspace, not paths inside the delivered code ZIP. The raw paper snapshots and extraction bundle are not redistributed; the commands and pinned upstream commit above allow the ingestion trial to be repeated from the public PDF.

The trial deliberately stops before page-by-page reviewed conversion and the strict verification gate. No review flags were marked complete. No Paper2MCP execution was claimed. This assessment is a workflow-fit check and successful ingestion trial, not a validation of Paper2Agent's overall scientific-reproduction capabilities.

## Primary sources

- [Paper2Agent README at tested commit](https://github.com/jmiao24/Paper2Agent/blob/8c2d059165ef8cdcb70dbea76655b9c2b55b38e6/README.md)
- [Paper2Agent routing skill](https://github.com/jmiao24/Paper2Agent/blob/8c2d059165ef8cdcb70dbea76655b9c2b55b38e6/skills/paper2agent/SKILL.md)
- [Paper2Skill workflow](https://github.com/jmiao24/Paper2Agent/blob/8c2d059165ef8cdcb70dbea76655b9c2b55b38e6/skills/paper2agent/paper2skill/SKILL.md)
- [Paper2MCP workflow and existing-code constraint](https://github.com/jmiao24/Paper2Agent/blob/8c2d059165ef8cdcb70dbea76655b9c2b55b38e6/skills/paper2agent/paper2mcp/SKILL.md)
- [Requested ACS Photonics paper](https://www.janglab.org/documents/publications/2022ACSPhotonics.pdf)
- [MEENT repository](https://github.com/kc-ml2/meent)
