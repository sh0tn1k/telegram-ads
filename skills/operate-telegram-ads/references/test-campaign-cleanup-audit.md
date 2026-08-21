# Test-Campaign Cleanup Audit (read-only)

Pattern for producing a **deletion plan** for a set of Telegram Ads test
campaigns identified by title prefix (e.g. `HERMES_REVIEW_TEST_*`,
`HERMES_MEDIA_REVIEW_TEST_*`, `HERMES_MEDIA_CHANNEL_TEST_*`). The plan is a
report, not an action. **No `delete_ad`, `withdraw_from_budget`, `start_ad`,
`stop_ad`, `change_cpm`, or `add_to_budget` is called until the operator explicitly
approves a specific `ad_id`.**

## When to use

- "подготовь cleanup plan для тестовых кампаний" / "find all
  `HERMES_*_TEST_*` and tell me which can be deleted"
- "что у нас висит как Declined / On Hold" / "audit stale test ads"
- "есть ли cooldown, можно ли безопасно удалить, вернётся ли бюджет"

## Read-only audit recipe (in order)

1. `telegram_ads_list_accounts` — get the `account_ref` for every cabinet.
2. `telegram_ads_choose_account(account_ref=…)` — switch to the cabinet
   under audit (repeat per cabinet if the test prefix may live in more
   than one).
3. `telegram_ads_list_ads` — the **primary** discovery call. Match on
   `title` prefix using a Python `startswith` per test tag.
4. For each candidate, gather the **full read view**:
   - `telegram_ads_get_ad(ad_id=…)` — status, target_type, dates, lifecycle
   - `telegram_ads_get_ad_creative(ad_id=…)` — title, text, media_type,
     show_picture
   - `telegram_ads_get_ad_budget_status(ad_id=…)` — cpm, budget, spent,
     active
   - `telegram_ads_get_rejection_info(ad_id=…)` — if `status == "Declined"`
5. Cross-check via `telegram_ads_get_ad_budget_status` against the
   account-level `_budget_transactions` (snapshot JSON, not live) to
   detect any "returned_from_ad" pair that would prove a campaign was
   already cleaned up server-side.
6. If a known `ad_id` is **missing from `list_ads`** but the user named
   it explicitly, call `telegram_ads_get_ad(id)` directly. Missing from
   list ≠ doesn't exist. The list endpoint can drop empty-creative /
   stale / on-hold rows.
7. Produce the cleanup plan as a **table** (see shape below). Stop.
   Do not call any mutating tool.

## Cleanup-plan table shape (canonical)

| ad_id | title | prefix | status | target_type | budget | spent | active | rejection | safe_to_delete | cooldown/lock | expected_refund |
|---|---|---|---|---|---|---|---|---|---|---|---|

- `prefix` — which user-named test-tag bucket the campaign falls into.
- `safe_to_delete` — `YES` / `LIKELY YES (verify)` / `NO (reason)` /
  `UNKNOWN (need re-check)`.
- `cooldown/lock` — `none` / `2-min decr_budget cooldown after status
  toggle` / `awaiting X`. (The 2-min cooldown is a property of
  `decr_ad_budget`, not of `delete_ad`; cleanup via delete-only path is
  unblocked regardless of recent start/stop.)
- `expected_refund` — `budget - spent`, in cabinet currency, returned to
  cabinet balance automatically on `delete_ad`. No separate
  `withdraw_from_budget` step required for cleanup.

## Per-status deletion verdict (quick reference)

- `Declined` (any reason), `spent=0`, never active → **YES**. Most common
  case for placement-mismatch / creative-policy test campaigns.
- `On Hold` with empty creative (parser returns `title=""`, `text=""`,
  `promote_url=""`) → **LIKELY YES, verify with `get_ad`**. Empty creative
  is the parser's edge-case output, not proof of absence.
- `Active` or `spent > 0` → **NO (without explicit stop + wait)**. Must
  stop first, wait out 2-min cooldown, then re-evaluate. Never delete a
  live-running ad without the operator's specific per-id approval.
- Missing from `list_ads` but named by user → `get_ad(id)` once, then
  classify from the full read view.

## Refund mechanics (confirmed by cabinet `_budget_transactions`)

- On `delete_ad`, the **unspent portion** (`budget - spent`) is returned
  to the cabinet's main balance as a `returned_from_ad` transaction
  (Stars / TON, depending on the cabinet's currency).
- A `returned_from_ad` entry in the same minute as the delete is the
  proof-of-cleanup signal when verifying post-hoc.
- No `withdraw_from_budget` is needed for cleanup; that endpoint is for
  reclaiming budget from a still-live campaign (e.g. to free Stars for
  re-allocation without deleting the ad).

## Approval flow for actual delete (post-audit)

1. Audit produces the table.
2. For each `ad_id` the operator wants gone, re-confirm: "approved, delete
   `ad_id=NN`". Never group-delete on a single "clean it all up".
3. `telegram_ads_delete_ad(ad_id=NN)` (no `confirmation_id`) — issues
   **two** `confirmation_id`s (DOUBLE confirmation, safety class
   `FORBIDDEN_OR_DOUBLE_CONFIRM`).
4. Show both human-summaries to the operator. Wait for both confirmations.
5. `telegram_ads_apply_approved_action(confirmation_id=…)` for each.
6. Verify via `telegram_ads_get_ad_budget_status` (should 404 / not
   found) and account balance delta.

## Pitfalls

- **Do not call `delete_ad` during the audit.** Even an issue-no-execute
  dry-run is not needed: the table itself is the deliverable.
- **`list_ads` coverage is not authoritative.** Always cross-check a
  user-named `ad_id` that doesn't appear in the list via `get_ad`.
- **Empty `get_ad_creative` ≠ doesn't exist.** This is a known parser
  edge case for stale rows. Verify with `get_ad`.
- **`archive_ad` is not implemented.** Plan must use `delete_ad` (or
  `stop_ad` + later delete). Don't propose `archive_ad` as the path.
- **Cooldown is on withdraw, not on delete.** Don't refuse a delete
  just because of a recent start/stop on the same ad — the 2-min
  cooldown is for `decr_ad_budget`, not for `delete_ad`.
- **Don't invent refund values.** If `get_ad_budget_status` reports
  `budget=0` for a campaign that should still hold funds, say so
  explicitly and mark `expected_refund = UNKNOWN (parser returned 0,
  verify via account _budget_transactions)`.

## Cross-references

- `references/media-placement-compatibility.md` — for the source of
  the most common `HERMES_MEDIA_REVIEW_TEST_*` (search placement + photo
  → placement-mismatch rejection, not deletable as "wrong creative").
- Tool surface: `telegram_ads_list_ads`, `telegram_ads_get_ad`,
  `telegram_ads_get_ad_creative`, `telegram_ads_get_ad_budget_status`,
  `telegram_ads_get_rejection_info`, `telegram_ads_delete_ad`,
  `telegram_ads_apply_approved_action`.
