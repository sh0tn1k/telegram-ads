---
name: create-telegram-ads-campaign-workflow
description: "Operational end-to-end workflow for creating a new Telegram Ads campaign: estimate effective CPM, validate the draft via checkAdPost, save a server-side draft with screenshot, present an approval request, then submit create_ad with confirmation_id. Use when the operator asks to 'create an ad', 'launch a campaign', 'prepare a new ad', or 'submit to moderation'."
version: 1.1.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [telegram-ads, operations, create, campaign, approval, placement, search]
    related_skills: [operate-telegram-ads-cabinet, telegram-ads-cost-modifiers, handle-telegram-ads-review-and-declines, format-telegram-ads-report, prepare-and-manage-tg-ads]
---

# Create Telegram Ads Campaign Workflow

Operational end-to-end procedure for **creating** a Telegram Ads campaign. This skill covers only the operational sequence — the actual creative / targeting / CPM / budget choices come from `prepare-and-manage-tg-ads` (strategy layer). The skill's job is to drive the tools in the correct order, gate on validation, present an approval request, and only then submit.

## When to Use

- the operator asks to "create a new Telegram Ads campaign".
- A draft is already shaped (title, text, promote_url, cpm, budget, target_type, targets) and ready to go to moderation.
- A new variant of an existing ad is being prepared via `duplicate_ad` / `create_similar_draft`.

## When NOT to Use

- The draft itself is not yet shaped — start with `prepare-and-manage-tg-ads` for creative / targeting / CPM / budget.
- Editing a live ad — that uses `edit_ad` and re-triggers moderation; this skill is for new ads.
- Pausing / resuming / changing CPM / budget on an existing ad — separate operational skills.

## Operating Discipline (mandatory)

This skill submits to moderation and spends money on activation. It is governed by `~/.hermes/shared/TELEGRAM_ADS_TOOL_CONTRACT.md` §0 (Operating Discipline):

- **No manual browser fallback** during this workflow. All steps go through typed Telegram Ads tools.
- **No `ps`/`pkill`/gateway restart** without explicit operator approval.
- **Confirmation flow is mandatory**: never call `create_ad` (or any other CONFIRM_REQUIRED action) without first getting an approval from the operator using the structured format in §"Approval Request Format" below.
- **`confirmation_id` is single-use, fingerprint-bound, TTL 300s.** Reuse is rejected; modify-any-byte in the draft between prepare and apply also rejects. Apply must happen in the next tool call after prepare.
- **`initial_active` does NOT skip moderation.** It only controls what happens **after** Telegram approves the ad: with `initial_active: true`, the ad begins serving (and spending) immediately on approval; with `initial_active: false` (the default), the ad stays paused after approval and must be explicitly started via `start_ad`. The moderation step happens either way. **Default for any test / review campaign: `initial_active: false`.** Only set `initial_active: true` for production campaigns that the operator has explicitly approved to begin spending on approval.

## Standard Procedure

The order is fixed. Skipping a step risks spending money on an unvalidated draft.

### Step 1 — Pre-Mutation Re-Confirmation (mandatory 4-step)

Before drafting a `create_ad` flow, run the full pre-mutation checklist from `operate-telegram-ads-cabinet` §"Pre-Mutation Re-Confirmation Checklist":

1. `choose_account(account_ref=<target>)` — select the intended cabinet (if a specific cabinet was named by the operator or inferred from a project hint; otherwise ask first).
2. `current_account` — confirm:
   - `title` matches the intended cabinet (pipe-containing titles must be checked in code-block form, not table — see `format-telegram-ads-report` §"Account / Ad / Campaign Title Safety");
   - `currency` matches the intended budget unit (TON 💎 / STARS ⭐️);
   - `account_type` matches the intended cabinet class (Personal Account / Bot Account / etc.).
3. `get_account_budget` — confirm the balance is sufficient for the planned ad's `cpm × planned_impressions / 1000` (sanity check, not a hard gate; the cabinet-level balance is the source pool, not the per-ad budget).
4. **Only then** proceed to Step 2.

If any of these 4 checks fails, stop and surface to the operator. Do not proceed to draft preparation. **This 4-step replaces the older "just call `current_account()`" step — the older single-step is insufficient for a mutating flow that will spend money.** The Pre-Mutation checklist is the gate; the rest of the create workflow assumes the gate has passed.

When `current_account` returns `account_ref_source: "current_account"` (unreconciled, with a `warnings` array), the `account_ref` is unstable. Prefer to call `list_accounts` first to populate `_fingerprint_to_ref`, then re-select by token to land on `account_ref_source: "reconciled"` before proceeding. The 4-step pre-confirm is the right place to do this resolution.

### Step 1.5 — Resolve & gate placement (added 2026-06-18)

Before any `estimate_cpm` / `validate_ad` / `prepare_ad_draft` call, resolve
the placement from the draft's `target_type` and apply the **Placement ×
Field Matrix** (see `prepare-and-manage-tg-ads/references/placement_field_matrix.md`
and `prepare-and-manage-tg-ads/SKILL.md` §"Placement × Field Matrix"):

1. **1.5.a — Resolve placement.**
   - `target_type == "channels"` → placement = `channels`.
   - `target_type == "bots"` → placement = `bots`.
   - `target_type == "search"` → placement = `search`.
   - `target_type` missing / unknown → placement = `unknown`.

2. **1.5.b — Apply matrix.**
   - `channels`: keep `text` and `media_path` as-is.
   - `bots`: strip `media_path` from draft (will return
     `unsupported_media_for_target_type` if left in). Keep `text`.
   - `search`: strip `text` (creative_text_160), `media_path`, and `ad_info`
     from draft. Draft retains only `promote_url`, `targets` (search
     queries), `cpm`, `budget`, and time/schedule fields. The stripped
     fields MUST surface to the operator in the approval request as
     **placement-mandated omissions** (`not_applicable_for_search_placement`),
     NOT as content edits.
   - `unknown`: STOP. Surface structured error `placement_unknown` and
     request the exact per-call approval phrase
     `approve placement <channels | bots | search>` before proceeding. Do
     NOT guess.

3. **1.5.c — Verify fields are clean.** Assert that for `search` placement
   the post-strip draft has none of: `text`, `media_path`, `ad_info`. If any
   leaked through, refuse to call `validate_ad` / `prepare_ad_draft` and
   surface to the operator. Same for `bots`: assert no `media_path`.

4. **1.5.d — Append placement spec to approval request.** Every Step 5
   approval request MUST carry these four fields for the ad creative:
   `placement`, `allowed_fields`, `forbidden_fields`, `creative_text_applicable`
   (see `prepare-and-manage-tg-ads/SKILL.md` §"Recommendation output spec").
   Missing any one of these → approval request is incomplete, surface and
   re-build.

5. **1.5.e — Only then proceed to Step 2.**

### Step 2 — Estimate effective CPM

```
telegram_ads_estimate_cpm(draft={...})
```

Report `modifiers_applied`, `estimated_effective_cpm`, `needs_validation`. If `needs_validation: true` and the stack is non-trivial, suggest running `validate_ad` to read the live `cpm_extra` next. See `telegram-ads-cost-modifiers` for details.

### Step 3 — Validate the draft (server-side)

```
telegram_ads_validate_ad(draft={...})
```

The tool calls `checkAdPost` on Telegram's side. Interpret:

| Result | Action |
|---|---|
| `ok: true, errors: []` | Proceed to Step 4 |
| `ok: false, errors: [...]` with field-level errors | Surface the errors verbatim. Do **not** modify the draft and retry without the operator's input. |
| `ok: false, error: "api_error"` with `cpm_extra` from server | Use the live `cpm_extra` for the approval summary. |
| `ok: false, error: "Search query can't contain less than 4 characters"` | Server-side check that local validation does not reproduce. See "Server Edge Cases" below. |
| `ok: false, error: "CPM can't be less than …"` | The account-specific minimum is higher than the docs suggest. Bump CPM to the minimum and re-validate. See "Server Edge Cases". |

**Never** call `create_ad` if `validate_ad` returned any error. Fix and re-validate first.

### Step 4 — Save server-side draft + screenshot (DRAFT category, no approval)

```
telegram_ads_prepare_ad_draft(draft={...}, screenshot_name="<short>")
```

Output:
- `draft_id` (server-side reference)
- `screenshot_path` (PNG of the draft preview page)

The screenshot is **recommended, not a hard blocker**. If `prepare_ad_draft` (or its built-in screenshot) fails, do not abort the workflow on screenshot alone. Continue the approval flow and verify the draft via `telegram_ads_get_ad` / `telegram_ads_list_ads` / `telegram_ads_get_ad_creative` after `create_ad` apply. In the report, mark the screenshot as a **non-blocking artifact gap** so the operator knows the visual preview was not generated. If verification also fails, then escalate.

Note: `telegram_ads_save_screenshot` has a known typed-wrapper bug (`multiple values for argument 'name'`). Use `prepare_ad_draft` (which produces a screenshot as a side effect) instead of `save_screenshot` directly. If `prepare_ad_draft`'s screenshot sub-step also fails, that is the artifact-gap case above.

### Step 5 — Show the operator the approval request

Use the exact format from `~/.hermes/shared/TELEGRAM_ADS_TOOL_CONTRACT.md` §5. The request must include:

- **Action:** `create_ad`
- **Project, account, currency**
- **Title, text, promote_url** (text shown only if `placement != search`)
- **Placement:** `channels` | `bots` | `search` | `unknown` (resolved in Step 1.5)
- **Allowed fields:** per the Placement × Field Matrix for this placement
- **Forbidden fields:** per the Placement × Field Matrix for this placement
- **Creative text applicable:** boolean (false for `search`)
- **Placement-mandated omissions:** if any fields were stripped in Step 1.5, list them here as placement-driven removals, NOT as content edits
- **Targeting:** `target_type`, list of `targets`, `views_per_user`
- **Base CPM** + **estimated effective CPM** + **modifiers** (from Step 2)
- **Budget:** total + any `daily_budget` / `weekly_schedule` / `activate_at`
- **Media:** presence of `media_path` (only allowed for `target_type=channels`)
- **Expected effect** + **risks** + **stop condition**
- **Exact tool call** that will execute on approval

Send the screenshot via `MEDIA:<screenshot_path>` so the operator sees the visual preview.

**Do not** call `create_ad` (or `prepare_approval_request`) until the operator has explicitly approved.

### Step 6 — Get confirmation_id

After the operator approves:

```
telegram_ads_prepare_approval_request(
  tool="create_ad",
  params={"draft": <same draft as validated>}
)
```

The tool returns a `confirmation_id` and a `params_summary`. **Do not modify the draft** between this call and the next — the confirmation is fingerprint-bound.

If the call returns a different `params_summary` than the one shown to the operator, stop. Surface the discrepancy and re-confirm.

### Step 7 — Apply (submit to moderation)

```
telegram_ads_apply_approved_action(confirmation_id="…")
```

**This is the call that spends money** (the ad starts accruing spend once Telegram activates it after moderation). Interpret the result:

| Result | Action |
|---|---|
| `ok: true, ad_id: N, status: "pending_review"` | Done. Note `ad_id`, schedule a check for moderation outcome (see Step 8). |
| `ok: false, error: "invalid_confirmation"` | Either the draft was modified between Step 6 and 7, or the 300s TTL expired. Re-prepare approval. |
| `ok: false, error: "api_error"` (e.g. CPM too low) | **The confirmation_id is now burned** by the failed apply. Re-shape the draft (fix the field), re-validate, re-prepare approval, re-apply with a new `confirmation_id`. Do not retry with the same `confirmation_id`. |
| `ok: false, error: "duplicate_ad"` | An ad with the same promote_url + targeting already exists. Surface to the operator. |
| `ok: false, error: "browser_profile_locked"` | Stop. Surface. Do not retry. |

### Step 8 — Create post-action watches (mandatory)

After successful `create_ad` apply, capture the returned `ad_id` and immediately create read-only post-action watches:

```
create_post_action_watches(action="create_ad", ad_id=<created_ad_id>)
```

Before persisting (or immediately after creation via `update_watch`), every generated watch must include approved-action metadata in `thresholds.approved_action`:

```json
{
  "approved_action": {
    "source": "approved_telegram_ads_action",
    "action": "create_ad",
    "ad_id": "<created_ad_id>",
    "approved_by": "operator",
    "created_by": "agent"
  }
}
```

Report the created watch IDs to the operator. Do **not** auto-fix, auto-recreate, auto-start, or auto-change budget/CPM from watcher output without a new explicit approval.

Hard completion rule: the create action is not complete until either:

1. a `post_action_verified` event is observed;
2. the expected moderation/campaign state is observed by the watcher;
3. `post_action_not_verified` / `watch_error` is routed to a diagnostic task.

### Step 9 — Moderation-outcome monitoring

The watcher is now the preferred monitoring path. Manual/cron checks are fallback only if the watcher is unavailable:

```
telegram_ads_get_ad(ad_id=<new ad_id>)
telegram_ads_get_ad_budget_status(ad_id=<new ad_id>)
```

within the typical moderation window (a few minutes to 24h). If declined, the next skill — `handle-telegram-ads-review-and-declines` — takes over. If still pending, record the timestamp and continue watcher monitoring.

## Approval Request Format

Use the structured form from `TELEGRAM_ADS_TOOL_CONTRACT.md` §5, extended with cost-modifier context:

```md
## Approval required

**Action:** `create_ad`
**Project:** ExampleBot
**Account:** Personal Account (TON 💎)
**Ad/Campaign:** new — "ExampleApp — clip long videos fast"
**Budget/CPM:** Base CPM 💎1.50, est. effective CPM 💎2.70 (show_picture +30%, custom_emoji +50%). Budget 💎20 total, 💎2/day.

**Title:** ExampleApp — clip long videos fast
**Text (preview):**
> Turn any 30-min podcast into 5 viral shorts. ExampleApp picks the best hooks automatically.
**Promote URL:** https://t.me/ExampleBot
**Targeting:** type=channels, targets=@podcast,@marketingtools,@aicontent; views_per_user=1
**Media:** none (target_type=channels, no upload)

**Reason:** First low-cost test of ExampleBot funnel entry. Channels targeted at podcast/marketing audiences.
**Expected effect:** ~7,400 effective impressions for 💎20 (at est. CPM).
**Risk:** Pending review may decline; even if approved, no clicks / no bot starts if creative is off.
**Stop condition:** Pause if no bot_start after 5,000 effective impressions OR if CPA(blended) > 💎5.

**Exact tool action after approval:**
telegram_ads_create_ad(draft={…fingerprint match…}, confirmation_id="<to be issued>")

**Proceed?**
```

## Server Edge Cases

These have been seen in production. Treat them as common, not edge.

1. **`Search query can't contain less than 4 characters`** — local validation passes a 9-query list, server rejects on `create_ad`. Cause: server-side word-level min > 4 not reproduced by local checker. After 2-3 identical failures, switch to a 1-query fallback and document. **For review-test search campaigns, use 1–3 safe queries, not 9.** One proven-safe query is acceptable for a pure flow test (server-side checker is more permissive for shorter lists). If you reduce the query list specifically to make the test flow work, **report it explicitly in the approval request** — do not silently shrink targeting for production campaigns. Production campaigns with reduced targeting should be flagged for re-targeting before going to real budget.
2. **`CPM can't be less than ⭐65`** (STARS) — local `validate_ad` does not know the per-account CPM minimum. Bump to the server-declared minimum and re-validate. Don't trust `cpm=50` from older ads in the same cabinet as a lower bound — the minimum may have been raised since.
3. **`api_error: …` with no clear field** — surface verbatim. Do not paraphrase. Do not retry with same params.
4. **`fingerprint_mismatch: true` from `apply_approved_action`** — means the draft changed between prepare and apply. Re-prepare approval with the exact same draft bytes. Treat as an agent bug, not a tool bug.
5. **`target_type=search` does not accept sponsored-message copy** (added 2026-06-18). Search placement is placement-specific; if Step 1.5 was bypassed and `text` / `media_path` / `ad_info` reach `validate_ad` or `create_ad` with `target_type=search`, expect a server-side rejection (no exact error token known yet — surface verbatim and apply the matrix instead). The Step 1.5 placement gate should prevent this from reaching the server. **This patch enforces it at the skill layer because `CreateAdDraft` schema does not expose a placement field that the server validates.**

## Server-Error Correction Retries

When `create_ad` (or `apply_approved_action`) returns a **known class of fixable server error** — e.g. CPM below minimum, search query too short, missing required field — the agent may attempt a correction cycle:

- **Max 2 correction retries** in response to a known fixable server error. Each retry must: identify the failing field, fix the field in the draft, re-run `validate_ad`, re-prepare approval with a new `confirmation_id`, and re-apply.
- **Unknown / unclear server error** (no field identified, error message not in the known-edge-cases list, or first time seen) → **stop and ask the operator**. Do not retry speculatively. The error may be a tooling regression, an account-state issue, or a Telegram policy change that needs human judgement.
- The cap exists to prevent burn loops where a `confirmation_id` is consumed on each retry and the agent progressively degrades the draft to make it pass.
- **Distinct from the controlled micro-resubmit loop in `handle-telegram-ads-review-and-declines`**, which is for moderation re-submits (different problem, different cap).

## Common Pitfalls

1. **Skipping `validate_ad` to save time.** It is a single read-only call. Skipping it risks hitting the server with an obviously bad draft and burning a `confirmation_id`.
2. **Modifying the draft between prepare and apply.** Any byte change re-fingerprints the draft. Apply fails. Re-prepare approval.
3. **Reusing a `confirmation_id` after it burned on a server error.** Each `apply_approved_action` consumes its `confirmation_id` regardless of success/failure. Always re-prepare.
4. **Submitting without showing the screenshot.** The screenshot is the only way the operator sees the actual rendered creative. Always include it in the approval request.
5. **Submitting `target_type=channels` with `media_path` for bot/search targeting.** Uploaded media is only supported on `channels` targeting. For `search` or `bots`, the `media_path` is silently ignored — the ad goes up without media. Surface that mismatch to the operator before approval.
6. **Assuming the new ad is live immediately.** `create_ad` returns `status: "pending_review"`. Spend begins only after Telegram approves the ad. Don't report "ad is running" until `get_ad(ad_id).status == "active"`.
7. **Switching cabinets between prepare and apply.** The draft is bound to a cabinet at save time. Switching mid-flow invalidates the `draft_id`. Restart from Step 4.
9. **Calling `create_ad` with `initial_active: true` and treating it as "skip moderation".** It does not skip moderation — the ad still goes through `checkAdPost` and may be declined. `initial_active: true` only means "begin serving automatically when (and if) Telegram approves". For any test or review campaign, keep the default `initial_active: false` so the ad does not start spending the moment it's approved. Surface the `initial_active` choice in the approval request.
10. **Skipping the Pre-Mutation Re-Confirmation 4-step.** This skill submits to moderation and starts spending on activation. The 4-step re-confirm (`choose_account` → `current_account` → `get_account_budget` → only then `prepare_approval_request`) is mandatory. The older "just call `current_account`" step in Step 1 is no longer sufficient for a mutating flow. If a future edit relaxes Step 1 back to a single `current_account` call, that's a regression — keep the 4-step.
11. **Trusting `list_accounts.balance` for the campaign budget sanity check.** The list view is a stale snapshot. The Pre-Mutation 4-step uses `get_account_budget` for the current cabinet's live balance. If the two disagree (they often do), the budget value is the authoritative one.

## Verification Checklist

- [ ] `validate_ad` returned `ok: true` with empty `errors` before any approval was issued.
- [ ] Effective CPM was estimated and reported; `needs_validation` was surfaced if set.
- [ ] Screenshot was attempted via `prepare_ad_draft`; if it failed, the workflow continued and `get_ad` / `list_ads` verification was used, and the screenshot gap was reported as non-blocking.
- [ ] **Step 1.5 placement gate ran:** placement was resolved (`channels` / `bots` / `search` / `unknown`); for `search` the draft had `text` / `media_path` / `ad_info` stripped before any further step; for `bots` `media_path` was stripped; for `unknown` the operator's `approve placement` phrase was received before proceeding.
- [ ] Approval request used the exact format from `TELEGRAM_ADS_TOOL_CONTRACT.md` §5 and **includes** the four placement fields (`placement`, `allowed_fields`, `forbidden_fields`, `creative_text_applicable`) plus any placement-mandated omissions.
- [ ] `create_ad` was called **only** after the operator's explicit approval, with a `confirmation_id` from a fresh `prepare_approval_request`.
- [ ] No draft bytes changed between `prepare_approval_request` and `apply_approved_action`.
- [ ] Server-error correction retries: at most 2 per fixable-error class; unknown errors stopped and escalated to the operator.
- [ ] The result is interpreted against the `create_ad` return table in Step 7.
- [ ] Post-action watches were created for the new `ad_id` and every watch has `thresholds.approved_action.source == "approved_telegram_ads_action"`.
- [ ] The created post-action watch IDs were reported to the operator.
- [ ] The create action was not marked complete until watcher verification, expected state observation, or diagnostic routing (`post_action_not_verified` / `watch_error`).
- [ ] The Pre-Mutation Re-Confirmation 4-step (Step 1) was executed before any draft was prepared; all 4 checks passed; `account_ref_source: "reconciled"` was confirmed.
