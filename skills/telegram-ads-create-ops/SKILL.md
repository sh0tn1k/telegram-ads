---
name: telegram-ads-create-ops
description: >-
  Live operational pitfalls and confirmed happy path for creating Telegram Ads
  via typed telegram_ads_* tools (STARS/TON cabinets, channel placement tests,
  confirmation mint, post-create verification, watcher registration). Use when
  creating/submitting ads, debugging create_ad confirmation, prepare_ad_draft
  screenshots, missing ad_id after apply, or post-create status/watcher gaps.
  Complements (does not replace) create-telegram-ads-campaign-workflow and
  prepare-and-manage-tg-ads — those may be bundled/read-only from curator.
version: 1.0.0
metadata:
  hermes:
    tags: [telegram-ads, create, operations, confirmation, watcher, channels]
    related_skills:
      - create-telegram-ads-campaign-workflow
      - operate-telegram-ads-cabinet
      - format-telegram-ads-report
      - prepare-and-manage-tg-ads
---

# Telegram Ads Create Ops

Class-level **runtime ops** for the typed create path. Strategy/copy still live in `prepare-and-manage-tg-ads`; full procedural order in `create-telegram-ads-campaign-workflow`. This skill stores **what the live tools actually do** when the docs/skills drift.

## When to use

- the operator: create / submit / test ad (any placement, any cabinet).
- Confirmation mint fails or returns unknown tool.
- Screenshot / draft prepare fails.
- After apply: no `ad_id`, weird status, balance moved, watcher questions.

## Happy path (confirmed)

1. **Pre-mutation 4-step:** `list_accounts` → `choose_account` → `current_account` (prefer `account_ref_source: reconciled`) → `get_account_budget`.
   - **Never trust `list_accounts.balance`.** Example 2026-07-16: Example Bot list=⭐0, live=⭐1164.
2. Placement gate: `channels` keeps text/media; `search` strips text/media; `bots` strips media.
3. STARS bot cabinets (Example Bot): start **CPM ≥ 65** for new ads (older ads at 50 are not a floor).
4. Test defaults: `budget=100`, `initial_active=false`, small target set.
5. `estimate_cpm` → `validate_ad` → `prepare_ad_draft(screenshot_name="….png")`. **Before `validate_ad`: run `telegram_ads_prepare_copy_variants` on the ad text** — it enforces single-line ≤160 (see pitfall 8).
6. Approval from the operator (high-level "создай … подтверждаю" is enough only after draft is fixed and shown or explicitly within stated scope).
7. **Mint confirmation:** `telegram_ads_create_ad(draft=…)` **without** `confirmation_id`.
8. **Apply immediately** with identical draft + `confirmation_id` (TTL ~300s, single-use, fingerprint-bound).
9. **Resolve ad_id:** `list_ads` match title (apply may only return `redirect_to: /account`).
10. Verify: `get_ad`, balance `transfer_to_ad` (−budget allocation, not spend).
11. Watch: `telegram_ads_register_campaign_watch(ad_id=…, project_id=…, auto_mutations=false)`.

## Critical pitfalls

### 1. `prepare_approval_request(tool="create_ad")` → Unknown tool

```
error: invalid_input
message: Unknown tool: 'create_ad'
```

**Do not retry spellings.** Mint via bare `telegram_ads_create_ad(draft)`.

### 2. Screenshot name must end with `.png`

```
Unsupported screenshot mime type for path "…/name_without_ext": None
```

Always `screenshot_name="something.png"`.

### 3. Apply payload often has no `ad_id`

Success shape can be `{ok: true, redirect_to: "/account"}`. Use `list_ads` + title.

### 4. Status split after create (`initial_active=false`)

| Surface | Common value |
|---|---|
| `list_ads` | `In Review` |
| `get_ad` / budget_status | `On Hold` |

Report **both**, `data_quality: partial`. Claim spending only when Active + views/spend move.

### 5. V2 watcher may be disabled

`register_post_action_watch` → `status=skipped, safe_summary=watcher_disabled` is **not** create failure.

- Prefer operator: `telegram_ads_register_campaign_watch`
- Report real `watch_id`; V2 skip honest
- Completeness: tool-verified if live state matches expected

### 6. `transfer_to_ad` is allocation, not spend

Budget leaves cabinet into ad pool. Only `payment_for_views` is real spend. Never write "spent X of Y".

### 7. Confirmation burn rules

- First apply burns `confirmation_id` even on server error
- Any draft byte change → fingerprint mismatch
- Max 2 correction retries per known fixable class; unknown server error → stop for the operator

### 8. Ad text must be single-line ≤160 (validator-enforced)

`telegram_ads_prepare_copy_variants` rejects:
- line breaks — `Text contains line breaks (forbidden)`
- length >160 — `Text length N exceeds limit 160`

Multi-line copy (even if real channels run it, e.g. football promo with "Buy Ads" lines) does **not** pass. Validate copy before `validate_ad` / `prepare_ad_draft`; fix violations by flattening to one line and trimming. Title ≤32 chars.

## Channel test template (flow only)

```text
title:        HERMES_CHANNEL_TEST_YYYYMMDD
promote_url:  t.me/ExampleBot   # project-specific
target_type:  channels
targets:      ["@durov"]          # flow-test only, not product audience
cpm:          65                  # STARS Example floor
budget:       100
show_picture: true                # +30% → est. 84.5
initial_active: false
text:         <≤160 policy-safe offer>
```

## Report obligations after create

Use `format-telegram-ads-report` ad-hoc shape plus:

- Cabinet before/after balance + allocation tx
- `ad_id`, status surfaces (list + detail)
- Screenshot `MEDIA:` path if present
- Watcher block: operator id + V2 state
- Explicit: **not Active / not spending** until proven

## Pointers

- Session detail: `references/create-ad-runtime-quirks-2026-07.md`
- Strategy/placement matrix: `prepare-and-manage-tg-ads`
- Full step order: `create-telegram-ads-campaign-workflow` (bundled; keep this skill if curator cannot patch it)
