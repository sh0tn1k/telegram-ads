# Telegram Ads — three-layer skill model (added 2026-06-06)

Telegram Ads work in Hermes spans **three distinct skill layers**, each
with a separate concern. Loading the wrong layer for a request produces
either over-engineering (strategy when the operator wants a procedure) or
hand-waving (procedure when the operator wants a tool call).

| # | Layer | Concern | Skill(s) |
|---|---|---|---|
| 1 | **Strategy** | What to advertise, to whom, with what message, CPM choice, audience selection, ROAS analysis | `prepare-and-manage-tg-ads` (in `business-growth/`) |
| 2 | **Operational workflow** | Deterministic procedure for a specific scenario — which tools to call, in what order, how to interpret statuses, how to handle errors, how to gate on approval, how to format the output for the operator | `telegram-ads-operations/*` (in `devops/`) — example scenarios: cabinet inspection, end-to-end create flow, decline handling, report format, cost-modifier estimation |
| 3 | **Tools** (this umbrella) | What each typed `telegram_ads_*` tool does, the `telegram_ads_workflow` typed workflows, the Operating Discipline contract | `operate-telegram-ads` (this file) |

## Routing rules

Use the highest layer that fully covers the request:

1. **Strategy layer** (`prepare-and-manage-tg-ads`) when the question is
   "what should this ad / campaign look like?" — creative direction,
   targeting choice, CPM range, audience-vs-segment fit, ROAS diagnosis.
2. **Operational workflow layer** (`telegram-ads-operations/*`) when the
   request names a specific scenario — "create a new campaign",
   "investigate the rejection", "estimate the effective CPM for this
   draft", "format the snapshot for the operator". The workflow drives the
   tool sequence; the operator does not improvise.
3. **Tools layer** (this skill) when the action is a single tool call
   not covered by a workflow, or when the workflow is unavailable and
   the operator must fall back to direct tool calls. This layer is
   also the source for the typed workflow surface (`snapshot`,
   `inspect_ad`, `account_diagnosis`) and for the Operating Discipline
   contract.

## Anti-patterns

- Loading the strategy skill to write a one-line approval request —
  that is an operational-workflow job.
- Loading this tools-layer skill to "decide what to bid" — that is
  strategy.
- Improvising a tool sequence for a scenario that already has a
  workflow skill (e.g. ad-hoc `validate_ad → prepare_draft →
  create_ad` chain when `create-telegram-ads-campaign-workflow`
  already codifies the exact order, error handling, and approval
  format).
- Treating the workflow skill's procedure as a hint rather than the
  canonical sequence. If the workflow says "Step 3 is `validate_ad`
  before `prepare_ad_draft`", that ordering is the contract; do not
  reorder.

## Skill loader cache caveat

Skill content is loaded fresh each turn, but the **skill registry**
(skills visible to `skills_list` and `skill_view`) is cached at session
start. New operational-workflow skills installed in this session
become visible in the next session, not the current one. Plan
accordingly: do not promise an agent a skill that is not yet visible.
