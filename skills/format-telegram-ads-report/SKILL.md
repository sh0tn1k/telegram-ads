---
name: format-telegram-ads-report
description: "Operational format for Telegram Ads reports delivered to the operator: structure, sections, field conventions, currency notation, common tables, and the 'never say X' rules. Use whenever producing a Telegram Ads summary (snapshot, inspect_ad, account_diagnosis, custom report) for the operator consumption. Pairs with operate-telegram-ads-cabinet and create-telegram-ads-campaign-workflow."
version: 1.0.2
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [telegram-ads, operations, reporting, format, output]
    related_skills: [operate-telegram-ads, prepare-and-manage-tg-ads, operate-telegram-ads-cabinet]
---

# Format Telegram Ads Report

Standardised **output format** for any Telegram Ads report delivered to the operator. This is a presentation-layer skill: it does not call tools, it tells the agent how to shape the result. Use it for `snapshot`, `inspect_ad`, `account_diagnosis`, `review-campaign-results`, custom analyses, and ad-hoc summaries.

The goal: any Telegram Ads report reads the same way. the operator can scan it, locate the numbers, and trust them.

## Pointer to support files

- `references/pipe-title-rendering.md` — session-specific condensed reference
  for the markdown rendering rules around pipe (`|`) characters in account /
  ad / campaign titles. Captures the demonstrated failure mode (5-column
  shift bug) and the deterministic safe render templates (code block with
  fixed keys, bullet list). Read this when producing a cabinet-level or
  per-ad report whose title may contain `|`.

## When to Use

- The output of any Telegram Ads tool or workflow is being delivered to the operator.
- A custom analysis (CTR trend, CPA breakdown, budget pacing, audience comparison) is being composed from raw tool output.

## When NOT to Use

- The output is a structured approval request (use `create-telegram-ads-campaign-workflow` §"Approval Request Format" instead).
- The output is a structured error envelope (use the raw `{"ok": false, "error": "…"}` form, do not wrap it).
- The output is for another agent (DeepSeek review) — they get raw data, not the human-facing report.

## Required Sections (in order)

For **standard reports** (snapshot, inspect_ad, account_diagnosis, custom summary):

```
## Summary
## Cabinet
## Campaigns
## Performance
## Anomalies / Data Quality
## Risks
## Recommendations (if any)
## Next actions
## Approval required (if any)
```

For **ad-hoc question** ("how is ad #142 doing?"):

```
## Answer
## Facts
## Anomalies / Data Quality
## Risks (if any)
## Next actions (if any)
```

For **rejection diagnosis** (delegated to `handle-telegram-ads-review-and-declines`):

```
## Rejection — ad #N
## Authoritative reason (verbatim)
## Operator summary (advisory)
## Rewrite proposal
## Next actions
## Approval required (if any)
```

## Section Conventions

### `## Summary`

Two to four lines, no tables. Lead with the most decision-relevant fact: which cabinet, what the headline number is (impressions / spend / balance), and the data quality. No filler.

Example:
> **Cabinet:** Personal Account (TON 💎). 3 active campaigns, 1 declined, 1 limited. Total spend this month 💎42.30. CTR 0.62%, all-cabinet. Data quality: complete.

### `## Cabinet`

Always include `title`, `currency` with emoji (TON 💎 / STARS ⭐️), `balance`, and `account_ref` (never the raw token). If a cabinet list is involved, render as a table:

```
| # | title | currency | balance |
|---|---|---|---|
| 1 | Personal Account | TON 💎 | 50.00 |
| 2 | Marketing Holdco | TON 💎 | 0.00 |
| 3 | `Example Bot — Short Clips` | STARS ⭐️ | 0 |
```

### `## Campaigns`

One of two views:

**List view** (default for snapshot):

```
| # | ad_id | title | status | views | clicks | ctr | spent | cpm | target |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 142 | ExampleApp — clip long videos fast | active | 12,430 | 78 | 0.63% | 💎18.65 | 💎1.50 | @podcast, @marketingtools |
| 2 | 143 | ExampleApp — AI shorts for podcasts | pending_review | — | — | — | — | 💎2.00 | @podcast, @aicontent |
| 3 | 144 | ExampleApp — Try free | declined | — | — | — | — | 💎1.50 | @marketingtools |
| 4 | 145 | ExampleApp — Boost your reach | stopped | 1,200 | 4 | 0.33% | 💎3.00 | 💎2.00 | @marketingtools |
| 5 | 146 | ExampleApp — Niche test | limited | 200 | 0 | 0% | 💎0.40 | 💎2.00 | @podcast |
```

**Detail view** (for inspect_ad or a single ad):

```
**ad_id:** 142
**title:** ExampleApp — clip long videos fast
**status:** active
**text:**
> Turn any 30-min podcast into 5 viral shorts. ExampleApp picks the best hooks automatically.
**promote_url:** https://t.me/ExampleBot
**cpm:** 💎1.50
**budget:** 💎50 total, 💎5/day
**targets:** @podcast, @marketingtools, @aicontent (channels, views_per_user=1)
**media:** none
```

Sort campaigns: active → pending_review → stopped → limited → declined → unknown. Within each group, sort by `ad_id` ascending.

**Stopped interpretation.** `status: "stopped"` is a final state, not a pending one. The most common cause is that the budget was depleted after active delivery: the ad ran, accumulated spend, and was automatically paused when the budget was reached. When reporting a `stopped` ad, check the budget status (`get_ad_budget_status`):

- If `budget == 0` **or** `spent` has reached the allocated budget, write the interpretation as **`interpretation: likely budget depleted`** alongside the separate fields.
- If the budget is non-zero and `spent` is well below it, the stop is operator-driven (manual `stop_ad` or account-level action). Write **`interpretation: manually stopped`** or note the visible reason.
- Do **not** write "spent X of Y" or imply one is a percentage of the other. Always present `spent:` and `budget:` as separate fields:

```
**ad_id:** 142
**status:** stopped
**interpretation:** likely budget depleted
**spent:** 💎20.00
**budget:** 💎20.00 (allocated, now exhausted)
**views:** 12,430
**clicks:** 78
**ctr:** 0.63%
```

```
**ad_id:** 145
**status:** stopped
**interpretation:** manually stopped (visible reason: account pause)
**spent:** 💎3.00
**budget:** 💎50.00 (allocated, partially used)
**views:** 1,200
```

The "Action needed" field (see below) is the right place to suggest a follow-up like "increase budget to resume" or "leave stopped, end-of-test".

**Action needed field.** For each important campaign in the report (typically: active, pending_review, stopped, limited, declined), add a one-line `action_needed:` field with one of these discrete values:

| Value | Meaning |
|---|---|
| `none` | No follow-up needed. Report is informational. |
| `monitor` | Worth watching; check again in N hours/days. No decision yet. |
| `recreate` | The current ad is in a terminal / non-recoverable state; the right action is to create a new ad (with adjusted creative, targeting, or budget). |
| `approve_needed` | A mutating action is queued and the operator has not yet approved. The "Approval required" section will reference this. |
| `stop_candidate` | Currently active or running, but the data suggests it should be stopped (CPA above target, CTR collapse, etc.). The recommendation section will explain. |
| `scale_candidate` | Currently active, metrics are good, the right action is to consider raising budget / CPM. The recommendation section will explain. |

Render this inline with the campaign entry (list view or detail view). Example:

```
| # | ad_id | status | views | ctr | spent | cpm | action_needed |
|---|---|---|---|---|---|---|---|
| 1 | 142 | active | 12,430 | 0.63% | 💎18.65 | 💎1.50 | monitor |
| 2 | 143 | pending_review | — | — | — | 💎2.00 | none |
| 3 | 144 | stopped | 8,200 | 0.41% | 💎20.00 | 💎1.50 | recreate |
| 4 | 145 | stopped | 1,200 | 0.33% | 💎3.00 | 💎2.00 | none |
| 5 | 146 | declined | — | — | — | 💎1.50 | recreate |
| 6 | 147 | limited | 200 | 0% | 💎0.40 | 💎2.00 | stop_candidate |
```

The `action_needed` value is what drives the "Next actions" and "Approval required" sections of the report — they should be derivable from the union of `action_needed != none` entries, not invented in the recommendation text.

### `## Performance`

Headline metrics + computed rates. Use **per-account** breakdowns when more than one cabinet is in scope — never aggregate TON and STARS.

```
**Impressions:** 12,430
**Clicks:** 78
**CTR:** 0.63%
**Spent:** 💎18.65
**CPM (effective):** 💎1.50 (no modifiers)
**CPC:** 💎0.24
**CPA(task_created):** 💎2.49 (based on bot's task_created events attributed via UTM)
```

If `data_quality: partial` or `unreliable`, prefix the section with a warning and a scope note.

### `## Anomalies / Data Quality`

Mandatory section. the operator must see this every time, even when empty.

```
**Data quality:** complete
**Anomalies:** none detected.
```

When the workflow surfaces parser anomalies, list them with the exact label:

```
**Data quality:** partial
**Anomalies:**
- `PARSE_ANOMALY_CTR_CLICKS_MISMATCH` (clicks=0 with 12,430 impressions is suspect — possibly column shift)
- `PARSE_ANOMALY_STATUS_UNKNOWN` (3 campaigns)
```

If `unreliable` or `unavailable`, the report **must** say so and not draw global conclusions:

```
**Data quality:** unreliable
**Anomalies:** `PARSE_ANOMALY_STATUS_UNKNOWN` for all 5 campaigns.
**Conclusion:** No global numbers can be drawn from this scan. Re-run after parser fix.
```

### `## Risks`

Only when relevant. Each risk: one short sentence with its trigger condition.

```
- **Spend cliff:** Daily budget 💎5 may exhaust before evening, killing reach for ad #142.
- **Single-campaign dependency:** 100% of active spend is on ad #142. If declined, no live ads.
```

### `## Recommendations`

Each recommendation: **effect + risk + stop condition + approval?** (matches `prepare-and-manage-tg-ads` convention). Skip this section if there are no recommendations.

```
- **Lower CPM for ad #142 from 💎1.50 → 💎1.20.** Effect: lower cost per impression, more reach per 💎. Risk: lower priority in ad serving, possibly fewer impressions. Stop: pause if CTR drops below 0.4% after 5,000 new impressions. **Approval: required.**
```

### `## Watcher verification` (for approved Telegram Ads actions)

Whenever a report follows an approved mutating Telegram Ads action, include a compact watcher block before `## Next actions`:

```
## Watcher verification
**Post-action watches:** 3 created (`watch_id_1`, `watch_id_2`, `watch_id_3`)
**Verification state:** pending | verified | not_verified | watch_error
**Expected:** status=active / cpm=… / budget_delta=…
**Latest event:** `post_action_verified` at 2026-06-09 18:10 UTC (or `none yet`)
**Action completeness:** complete | blocked_on_watcher | diagnostic_created
```

Rules:

- This section reports watches created by the operational skills via `create_post_action_watches`; those watches must carry `thresholds.approved_action` metadata (see `operate-telegram-ads-cabinet` §"Post-Action Watcher Policy").
- If `post_action_verified` is observed or the expected campaign status/budget/CPM is observed, mark `Action completeness: complete`.
- If `post_action_not_verified` or `watch_error` occurs, mark `Action completeness: diagnostic_created` and list the diagnostic task stub/id.
- If no watcher result exists yet, mark `Action completeness: blocked_on_watcher`; do not claim the mutating action is fully complete.
- Never recommend an auto-fix from watcher output without a new `## Approval required` section.

### `## Next actions`

Concrete tool calls or follow-ups. Each line: `tool(action)` + brief reason.

```
- `telegram_ads_get_ad_stats(ad_id=143)` — check pending_review status after 1h.
- `telegram_ads_explain_rejection(ad_id=144)` — diagnose the decline.
```

### `## Approval required`

Only when a mutating action is queued. Use the structured form from `TELEGRAM_ADS_TOOL_CONTRACT.md` §5 (see also `create-telegram-ads-campaign-workflow` for the extended form). The presence of this section **always** means the operator must explicitly approve before any tool call is made.

## Field Conventions

- **Currency:** always paired with emoji — `TON 💎`, `STARS ⭐️`. Never just `TON` or `STARS` in a number. `1.50` alone is ambiguous.
- **CPM in tables:** prefix with the currency emoji: `💎1.50`, `⭐65`.
- **Numbers ≥ 1000:** use thousands separator: `12,430` not `12430`. Money amounts keep two decimals.
- **Percentages:** one decimal: `0.63%` not `0.627%`. Two decimals only when the difference is decision-relevant.
- **Statuses:** spell out in lowercase: `active`, `pending_review`, `stopped`, `declined`, `limited`, `on_hold`, `unknown`. Do not capitalise. Use the exact tool-returned spelling, not a synonym.
- **Timestamps:** `YYYY-MM-DD HH:MM UTC` (no local TZ unless the operator asked). Never bare ISO with `T`.
- **Money totals in the summary:** round to two decimals. If zero, write `0.00` (TON) or `0` (STARS, integer cents).
- **Account tokens:** always use the opaque `account_ref`. Never the raw token in a report.
- **Custom emoji in ad text:** quote the ad text verbatim, with the `tg://emoji?id=…` markers preserved. Do not strip or normalise.

## The "Never Say" Rules

These are non-negotiable. Violating any of them produces a misleading report.

1. **Never write "spent X of Y".** The `spent_total` and `budget_column_total` are two independent columns. The budget column may be total budget, daily budget, remaining, or something else — there is no contract. Always use both fields separately and let the operator read the relationship from the source.
2. **Never aggregate TON and STARS into one number.** Different units, different CPM minimums, different billing. Sum separately per currency.
3. **Never treat `unknown` status as `stopped`.** Unknown is a parse warning, not a real status. Always show it as a separate bucket and pair with `Anomalies`.
4. **Never draw global conclusions from `data_quality: partial` / `unreliable` / `unavailable`.** Per-account observations are fine; "all campaigns are stopped" is not.
5. **Never invent metrics not in the tool response.** Missing data → write "missing data" or "not available", do not interpolate.
6. **Never paraphrase the rejection reason.** Quote verbatim, in quotes, with the category label.
7. **Never show raw `access_token`.** Always use `account_ref`.
8. **Never claim a `pending_review` ad is "live" / "running" / "spending".** It is in moderation. Spend begins only when `status == active`.
9. **Never recommend a mutating action without explicitly saying "Approval: required"** in the recommendation and without an `## Approval required` section.
10. **Never call out a metric as "good" or "bad" without a reference.** CTR 0.63% is a number; whether it is good depends on the segment, the historical baseline, and the goal. If a recommendation hinges on "CTR is good / bad", state the reference explicitly.

## Media Attachments

- **Screenshot from `prepare_ad_draft` / `screenshot`:** send via `MEDIA:<path>` so the operator sees the rendered creative. Reference it once in the relevant section ("see screenshot of draft preview").
- **CSV report from `download_report`:** mention the saved path; do not paste the full CSV. If a snippet is needed (top rows), show ≤ 10 rows in a code block.
- **Screenshots of failures:** include if the failure is visual. For structured errors, the JSON is enough.

## Length Budget

Aim for **compact**. A complete snapshot report for a single cabinet with 5 campaigns is ~30-50 lines of markdown. A `inspect_ad` report is ~15-25 lines. If the report is over 100 lines, it's probably mixing multiple concerns — split into separate reports.

## Account / Ad / Campaign Title Safety (mandatory)

**Rule:** if an account, ad, or campaign `title` contains a `|` character, **do not** render that title inside a markdown table. Use a **bullet list** or a **code block with fixed keys** instead. Also: if the tool provides a pre-rendered `display_block` (or equivalent) field, paste it **verbatim** — do not reformat or re-wrap. The pre-rendered form is designed to be safe by construction (raw title, no `|` escape, fixed key order, no field shift risk).

Why: the markdown table cell separator is `|`. A `|` in a cell value splits the row, even when escaped as `\|`. This is the demonstrated failure mode:

```
| title | account_ref | currency | account_type | balance |
|---|---|---|---|---|
| Example Bot \ | Short Clips | acc_3 | STARS | Bot Account | 549 |
```

…which, after auto-rewriting, becomes a 5-column-shift bug where the next 4 fields land one column to the left of where they belong. The `title_display` field (with pipe-escaped variant) is necessary but not sufficient — in some renderers the escape is also reinterpreted. **Code-block format is the deterministic safe form.**

### Safe render templates

**Code block (preferred for per-cabinet / per-ad detail):**

```text
title:           Example Bot | Short Clips
account_ref:     acc_3
currency:        STARS
account_type:    Bot Account
current balance: 549 STARS
list_accounts balance: 0 STARS
mismatch:        yes
```

**Bullet list (when code-block is too dense for the surrounding report):**

- title: `Example Bot | Short Clips`
- account_ref: `acc_3`
- currency: STARS
- account_type: Bot Account
- current balance: 549 STARS
- list_accounts balance: 0 STARS
- mismatch: yes

For titles **without** `|`, both tables and code-blocks are acceptable; the code-block format is used in this skill for visual consistency across the audit.

## Canonical Balance and Transaction Semantics (mandatory)

These rules apply to **every** cabinet-level or campaign-level report:

1. **`list_accounts.balance` is low-trust / stale.** The list view is a snapshot that may lag the live cabinet state. It is acceptable as a hint but **never** as the source of truth for a balance.
2. **Canonical balance source order:** `choose_account` (selects cabinet in browser/session) → `current_account` (confirms the selection) → `get_account_budget` (returns the authoritative balance for the selected cabinet). When the audit compares a `current balance` to a `list_accounts balance`, the **current** value wins.
3. **If `list_accounts` and `current_account` disagree for the same cabinet** (identified by `(title, currency, account_type)` fingerprint), trust `current_account` + `get_account_budget` for the current state. Surface the disagreement as a data-quality note (`Anomalies` section), not as a hidden bug.
4. **Transaction semantics:**
   - `transfer_to_ad` = **budget allocation** (reserved from account balance into ad budget). **Not spend.** The money moves from the account pool to the ad pool.
   - `returned_from_ad` = **budget release** (returned from ad budget back to account balance). **Not income.** It is the same money returning; the round-trip is allocation + release.
   - `payment_for_views` is the **only** transaction kind that is real spend. Any other kind in a transaction log is a movement between pools, not a spend.
5. **Do not** write "returned = spent" anywhere. A `returned_from_ad` row is **not** the inverse of a `payment_for_views` row. The two are different transaction kinds; their amounts and timing are independent.
6. **The tool-supplied `kind_note` per row and the global `reserve_release_note` are advisory but well-formed.** Quote them when explaining a transaction. Do not paraphrase "budget allocation" or "budget release" — those are the canonical terms from the parser/display fix (`ef6973c`).

## Common Pitfalls

1. **Writing "spent X of Y" anywhere in the report.** `spent_total` and `budget_column_total` are independent. State both, separately, and let the reader infer the relationship.
2. **Mixing currencies into a single sum.** TON and STARS never combine. If two cabinets are in scope, show them in two separate sub-blocks.
3. **Treating `unknown` status as `stopped`.** `unknown` is a parse warning, not a real status. Always show it in its own bucket and pair with `## Anomalies / Data Quality`.
4. **Drawing global conclusions from `data_quality: partial` / `unreliable` / `unavailable`.** Per-account observations are fine; global numbers are not.
5. **Inventing metrics not in the tool response.** If a field is absent, write "not available" or "missing data". Do not interpolate.
6. **Paraphrasing the rejection reason.** Quote verbatim, in quotes, with the category label. Telegram's wording is the contract.
7. **Showing the raw `access_token`.** Always use the opaque `account_ref`.
8. **Calling a `pending_review` ad "live" / "running" / "spending".** Spend begins only when `status == active`. Until then, the ad is in moderation.
9. **Recommending a mutating action without "Approval: required"** in the recommendation and a matching `## Approval required` section.
10. **Labeling a metric as "good" / "bad" without a reference.** State the baseline (segment average, historical CTR, target CPA) explicitly. Bare "good" / "bad" is editorialising, not reporting.
11. **Wrapping structured errors in human-friendly prose.** If the tool returns `{"ok": false, "error": "browser_profile_locked", ...}`, return it as-is. Do not translate to "there was a problem with the browser" — the structured form is what the operator needs to act on.
12. **Reporting `stopped` without the budget interpretation.** `stopped` is ambiguous without context — most often it means budget depleted, but it can also be a manual stop. Always check `get_ad_budget_status` and add an `interpretation:` line. Do not guess.
13. **Omitting `action_needed` for non-active campaigns.** Active campaigns are not the only ones needing follow-up. A `declined` ad needs a decision; a `stopped` ad with budget left may be a `recreate` candidate; a `limited` ad may be a `stop_candidate`. The `action_needed` value drives the rest of the report — leaving it out for any non-active status forces the operator to do the classification work.
14. **Using `action_needed` values outside the six discrete set.** `none` / `monitor` / `recreate` / `approve_needed` / `stop_candidate` / `scale_candidate` — that is the full vocabulary. Free-form values like "review" or "tbd" are forbidden; pick the closest discrete value.
15. **Rendering a pipe-containing title in a markdown table.** A `|` in a cell value splits the row, even when escaped as `\|`. If an account / ad / campaign `title` contains `|`, render the entry as a code block (with fixed keys) or a bullet list. See §"Account / Ad / Campaign Title Safety".
16. **Using `list_accounts.balance` as the authoritative balance.** The list view is a stale snapshot. Canonical source is `choose_account` → `current_account` → `get_account_budget` for the selected cabinet. See §"Canonical Balance and Transaction Semantics".
17. **Writing "returned = spent" or any equivalent phrasing.** `returned_from_ad` is a budget release, not the inverse of a `payment_for_views`. They are different transaction kinds with independent amounts and timing.
18. **Treating `account_ref_source: "reconciled"` as "active cabinet" or "active ads".** `reconciled` / `current_account` / `list_accounts` describe the source path of the `account_ref`, not the activity state of the cabinet or its campaigns. For ad activity, use the per-campaign `status` (Active / On Hold / Declined / Stopped).

## Verification Checklist

- [ ] `## Summary` is 2-4 lines, decision-relevant, no filler.
- [ ] Currency is paired with emoji everywhere a number is shown.
- [ ] `## Anomalies / Data Quality` is present, even if empty.
- [ ] Per-account breakdowns when more than one cabinet is in scope; never mixed-currency aggregation.
- [ ] `unknown` status shown in its own bucket, not merged with `stopped`.
- [ ] Rejection reasons quoted verbatim if any.
- [ ] Mutating actions have a matching `## Approval required` section and explicit "Approval: required" tag in the recommendation.
- [ ] No "spent X of Y" phrasing anywhere; `spent:` and `budget:` are separate fields.
- [ ] Account references shown as `account_ref`, never raw tokens.
- [ ] Every `stopped` campaign has an `interpretation:` line (`likely budget depleted` or `manually stopped` with reason).
- [ ] Every non-active campaign has an `action_needed:` field from the discrete set (`none` / `monitor` / `recreate` / `approve_needed` / `stop_candidate` / `scale_candidate`).
- [ ] Total length fits the budget (≤ 50 lines for a snapshot, ≤ 25 for `inspect_ad`).
- [ ] For reports after approved Telegram Ads actions, `## Watcher verification` is present with watch IDs, verification state, expected result, latest event, and action completeness.
- [ ] No approved Telegram Ads action is labelled fully complete unless watcher verification / expected state observation / diagnostic routing is reported.
- [ ] No auto-fix/auto-recreate/auto-budget/auto-CPM recommendation is made from watcher output without a new `## Approval required` section.
- [ ] No account / ad / campaign `title` containing `|` is rendered inside a markdown table. All such entries are in code blocks (with fixed keys) or bullet lists. See §"Account / Ad / Campaign Title Safety".
- [ ] `list_accounts.balance` is shown only as a `list_accounts balance` field next to a `current balance` field. The two are explicitly compared; the current value is labelled as canonical. See §"Canonical Balance and Transaction Semantics".
- [ ] `account_ref_source: "reconciled"` / `"current_account"` / `"list_accounts"` is never interpreted as ad / bot activity. The report's `is_active` claims are sourced from per-campaign `status` (Active / On Hold / Declined / Stopped), not from the cabinet object.
- [ ] Transaction logs quote the per-row `kind_note` and the global `reserve_release_note` verbatim when explaining a row. No paraphrasing of "budget allocation" / "budget release".
- [ ] No phrase "returned = spent" or any equivalent. `returned_from_ad` is described as a budget release; `payment_for_views` is the only kind described as real spend.
