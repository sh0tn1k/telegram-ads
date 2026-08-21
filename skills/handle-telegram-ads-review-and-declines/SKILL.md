---
name: handle-telegram-ads-review-and-declines
description: "Operational workflow for handling Telegram Ads moderation outcomes: read rejection reason, classify the policy category, propose a policy-compliant rewrite of the creative, re-validate the rewrite, and submit the edit (or a fresh create) for re-review. Use when an ad is declined, when the operator asks 'why was it rejected?', or when an existing ad needs a moderation-safe rewrite."
version: 1.0.1
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [telegram-ads, operations, moderation, rejection, decline, review]
    related_skills: [create-telegram-ads-campaign-workflow, format-telegram-ads-report]
---

# Handle Telegram Ads Review and Declines

Operational procedure for handling **moderation outcomes** on Telegram Ads. Two flows:

1. **Declined ad** — read the rejection reason, classify the policy category, propose a rewrite, re-validate, and either re-submit (new ad) or `edit_ad` (existing ad) for re-review.
2. **Approved ad** — verify the approval is reflected in the tool's view, confirm `status: "active"`, and hand off to ongoing monitoring.

This skill is **operational**, not strategic. The actual creative direction comes from `prepare-and-manage-tg-ads`. This skill's job is to drive the tools, classify the rejection, surface the verbatim reason, and gate any submit / edit on the operator's approval.

## When to Use

- A `create_ad` call returned `status: "declined"` or an existing ad shows `status: "rejected"`.
- the operator asks "why was this ad rejected?" / "what's the policy reason?".
- An ad is in moderation limbo (`status: "pending_review"`) and the operator wants to know what to expect.
- A live ad is `status: "limited"` (e.g. reach restricted) and the operator asks why.
- The same draft has been declined twice — this skill is the right place to escalate the diagnosis.

## When NOT to Use

- The ad is `status: "active"` but underperforming → use the `inspect_ad` workflow and `review-campaign-results` for diagnosis.
- The ad is being **created** for the first time → use `create-telegram-ads-campaign-workflow`.
- Bulk-cleaning declined ads → that's a separate cleanup skill / decision.

## Operating Discipline (mandatory)

- Read-only tools (`get_ad`, `get_rejection_info`, `explain_rejection`, `validate_ad`) are SAFE_READ and need no approval.
- **Any submission to moderation** (a fresh `create_ad` after rewrite, or `edit_ad` on an existing declined ad) requires the operator's approval per the standard flow in `create-telegram-ads-campaign-workflow`.
- **No manual browser fallback** to read the rejection page. The typed tools expose the structured reason. If they fail, surface the failure, do not bypass.

## Standard Procedure — Declined Ad

### Step 1 — Pull the ad detail and rejection envelope

```
telegram_ads_get_ad(ad_id=<id>)
telegram_ads_get_rejection_info(ad_id=<id>)
telegram_ads_explain_rejection(ad_id=<id>)
```

Each tool returns a different view:

- `get_ad` — full creative + status field. Confirm `status: "rejected"`. Capture the rendered `title`, `text`, `promote_url`, `cpm`, `targets`.
- `get_rejection_info` — raw decline envelope: `category`, `description`, optional `link`. Use this as the **authoritative** policy reference.
- `explain_rejection` — operator-facing summary: likely cause + concrete fix suggestions. **Advisory only** — `get_rejection_info` is authoritative.

### Step 1.5 — Pre-Mutation Re-Confirmation (mandatory 4-step, before any `edit_ad` / `create_ad` re-submit)

The decline-handling flow ends with a mutating action (`edit_ad` for the same ad, or `create_ad` for a fresh recreation). Before issuing the approval request for the rewrite, run the full pre-mutation checklist from `operate-telegram-ads-cabinet` §"Pre-Mutation Re-Confirmation Checklist":

1. `choose_account(account_ref=<cabinet of the declined ad>)` — re-select the cabinet that owns the declined ad. The original `account_ref` may have changed since the decline; re-confirming is cheap and prevents operating on the wrong cabinet.
2. `current_account` — confirm `title` (with pipe-title code-block check), `currency`, `account_type`. The currency must match what the rejected ad's `cpm` was denominated in.
3. `get_account_budget` — confirm the cabinet still has a balance sufficient for the rewrite's expected CPM × impressions. A declined ad's reserved budget may have been returned; the cabinet's live balance is the source.
4. **Only then** proceed to Step 2 (classify the rejection) and onwards to the rewrite + re-submit.

If any of these 4 checks fails, stop and surface. Do not proceed to the rewrite.

This 4-step is **distinct from the controlled micro-resubmit loop** (which caps attempts on a single declining cycle). The 4-step is the **pre-cycle** gate; the resubmit loop is the **per-attempt** discipline. Both apply: gate, then loop.

### Step 2 — Classify the rejection

Common Telegram Ads policy categories (the exact wording varies — always prefer the verbatim text from `get_rejection_info`):

| Category (typical) | Typical cause | Typical fix |
|---|---|---|
| `misleading_claims` | "guaranteed", "100%", "risk-free", unprovable promises | Remove the claim or soften to a verifiable statement. |
| `prohibited_products` | Restricted verticals (gambling, adult, financial advice) | Pivot angle or pause; do not rephrase. |
| `external_payment` | Asking user to pay outside Telegram | Use Telegram-native payment / remove the CTA. |
| `banned_categories` | Crypto/forex signals, MLM, payday loans | Stop the campaign. |
| `clickbait` | Sensational phrasing, "you won't believe", fake urgency | Rewrite with concrete value prop. |
| `low_quality_creative` | Poor image, tiny text, broken layout | Re-render the media / simplify the text. |
| `targeting_mismatch` | Ad claims X but targets an unrelated audience | Re-align targeting or rewrite the headline. |
| `trademark_violation` | Using a third-party brand / logo | Remove the brand reference. |
| `url_mismatch` | `promote_url` doesn't match the claim | Make URL and text consistent. |

**Always use the exact category text returned by `get_rejection_info`**, not a paraphrase. Telegram's reason text is the contract for what needs to change.

### Step 3 — Propose a policy-compliant rewrite

The proposal must:
1. **Preserve the offer** — the product / value prop is not changed; only the framing is.
2. **Remove the specific claim / element** that triggered the category.
3. **Stay within verifiable statements** — what the product demonstrably does.
4. **Use plain language** — no "guaranteed", no "100%", no superlatives without evidence.
5. **Match the URL** — if the URL is a Telegram bot, the creative must reference the bot's actual function, not a generic benefit.

For privacy/compliance-sensitive products (e.g. Dialog Spy Bot), additional rules apply — see `prepare-and-manage-tg-ads` "Privacy/compliance-sensitive copy policy". The rewrite must stay within the product's real capability, never position it as something it does not do.

### Step 4 — Validate the rewrite (no submit)

```
telegram_ads_validate_ad(draft=<rewritten draft>)
```

Interpret:
- `ok: true, errors: []` → safe to submit (Step 5).
- `ok: false, errors: [...]` → fix the indicated field, re-validate.
- `ok: false, error: "CPM can't be less than …"` → bump CPM, re-validate.

For `edit_ad` flow on an existing declined ad, build the `EditAdDraft` with `ad_id`, new `title`, new `text`, new `cpm` (if changed), and any other fields. The `validate_ad` schema accepts a `CreateAdDraft`; for `edit_ad`, run `validate_ad` against an equivalent `CreateAdDraft` first to catch policy errors, then build the `EditAdDraft` for the actual `edit_ad` call.

### Step 5 — Submit the rewrite (CONFIRM_REQUIRED)

Two paths, depending on the original submission:

**Path A — same ad, edit in place** (preserves history, ad_id stays):

```
telegram_ads_prepare_approval_request(
  tool="edit_ad",
  params={"draft": <EditAdDraft with ad_id and new fields>}
)
```

After the operator approves:

```
telegram_ads_apply_approved_action(confirmation_id="…")
```

`edit_ad` re-triggers moderation. `ad_id` is preserved. Spend pause is automatic during the re-review (no need to call `pause_ad` separately).

**Path B — fresh create** (use when the rewrite is substantially different or `edit_ad` is blocked):

```
telegram_ads_prepare_approval_request(
  tool="create_ad",
  params={"draft": <new CreateAdDraft>}
)
telegram_ads_apply_approved_action(confirmation_id="…")
```

This produces a new `ad_id`. The old ad is left in `status: "declined"` until the operator decides to delete or leave it (delete is DOUBLE_CONFIRM — never automatic).

### Step 6 — Create post-action watches (mandatory)

After a successful approved `edit_ad` or fresh `create_ad` re-submit, the agent must create read-only post-action watches before considering the re-submit complete:

- `edit_ad` → `create_post_action_watches(action="edit_ad", ad_id=<ad_id>)`
- fresh `create_ad` → `create_post_action_watches(action="create_ad", ad_id=<created_ad_id>)`

Every generated watch must carry `thresholds.approved_action` metadata:

```json
{
  "approved_action": {
    "source": "approved_telegram_ads_action",
    "action": "edit_ad | create_ad",
    "ad_id": "<ad_id>",
    "approved_by": "operator",
    "created_by": "agent"
  }
}
```

Report the watch IDs. Do not auto-fix, auto-recreate, auto-delete, auto-start/stop, or auto-change budget/CPM from watcher output without a new explicit approval.

Hard completion rule: the re-submit is not complete until either `post_action_verified` is observed, the expected moderation/campaign state is observed, or `post_action_not_verified` / `watch_error` is routed to a diagnostic task.

### Step 7 — Schedule / continue re-review monitoring

The watcher is now the preferred monitoring path. Manual polling via `telegram_ads_get_ad(ad_id=...)` is fallback only if watcher verification is unavailable.

## Controlled Micro-Resubmit Loop

When a rewrite is itself declined, the agent may attempt a tightly-scoped resubmit. The rules are strict — they exist to prevent the agent from oscillating between near-identical drafts and burning moderation capacity on the operator's account.

- **Never resubmit unchanged.** A re-submit must include at least one visible change. Examples of "one visible change per attempt":
  - Add or remove a single dot / punctuation.
  - Small phrase change (e.g. "Get started" → "Try it free").
  - Remove a risky word (e.g. "guaranteed", "best", "free money").
  - Remove an emoji from text (custom or otherwise).
  - Remove uploaded media.
  - Disable `show_picture` (`show_picture: true` → `show_picture: false`).
- **Default max attempts: 3** (the original submission counts as attempt 1; the next 2 are the loop's budget).
- **Max 5 attempts total only with explicit operator approval per case.** The default 3-attempt cap is hard; exceeding it requires the operator to say "approve 4th/5th resubmit" with reasoning.
- **One visible change per attempt.** Stacking multiple changes per attempt is forbidden because it removes the ability to attribute the cause when a category repeats.
- **Log every attempt.** Each iteration must record: attempt number, the exact change made, the new moderation outcome, the new category if still declined.
- **Stop the loop immediately if:**
  - The same rejection category repeats on the second attempt (the framing is not the problem; something structural is).
  - The product itself appears to be in a prohibited-content category (e.g. "prohibited_products", "banned_categories", "external_payment" requiring Telegram-native payment that does not exist for the product). Re-phrasing will not fix a product-level issue.
  - the operator says to stop.

The log of the loop should be reported to the operator at the end (or at the loop-stop trigger) so the operator can decide whether the next iteration is the right one or whether a different strategy is needed.

## Search-Targeting Specific Rules

For `target_type: "search"` campaigns, the rules above interact with the way Telegram Ads handles search queries:

- **Search queries are locked at create time.** `validate_ad` and `explain_rejection` will show you the queries the draft was submitted with, but `edit_ad` cannot change the query list of a search campaign — it is immutable post-create. If the rejection is rooted in a query / product-level issue, the only path forward is `create_ad` (a fresh ad with corrected queries), not `edit_ad`.
- **Decision rule for search campaigns:**
  - If the rejection category is **query / product-level** (e.g. category implies the product cannot be advertised at all, or the queries themselves triggered the policy) → **recreate new** (`create_ad` with corrected queries). Do not try to `edit_ad`.
  - If the rejection category is **editable creative-level** (e.g. the text contains a banned word, but the queries and product are fine) → **edit and resubmit** (`edit_ad` with the corrected text). The query list stays the same.
  - If the creative is clean but the queries look suspicious in retrospect → **recreate new** with adjusted queries. Do not retry the same queries on a new creative.
- Always run `validate_ad` on the new draft (recreate path) or the new `EditAdDraft` (edit path) before approval.

## Standard Procedure — Approved Ad

When the ad is approved and active, this skill's job is to confirm the state and hand off:

```
telegram_ads_get_ad(ad_id=<id>)
telegram_ads_get_ad_budget_status(ad_id=<id>)
```

Report:
- `status: "active"` (or `"limited"` if reach is restricted)
- `cpm`, `budget`, `spent` so far
- Any `warning` from the budget status

If `status: "active"` → hand off to ongoing monitoring. If `status: "limited"` → surface the limitation reason; do not change CPM / budget without the operator's approval.

## Output Format — Declined Ad Diagnosis

```md
## Rejection — ad #142

**Status:** rejected
**Authoritative reason** (from `get_rejection_info`):
> "Ad contains misleading claims about guaranteed results (category: `misleading_claims`)."

**Operator summary** (from `explain_rejection`, advisory):
> Likely cause: superlative / guarantee phrasing in line 2 of the ad text.
> Suggested fix: remove "guaranteed"; replace with a verifiable statement.

## Rewrite proposal

**Old text:**
> Guaranteed 5 viral clips from any 30-min podcast with ExampleApp. Risk-free.

**New text:**
> Turn a 30-min podcast into 5 short clips. ExampleApp picks the strongest moments automatically.

**Validation:** `validate_ad(draft=new)` → `ok: true, errors: []`. No new policy flags.

## Next actions
- Show the operator the rewrite + screenshot; request approval for `edit_ad`.
- On approval: `edit_ad` (preserves ad_id 142) with the new text.

## Approval required
**Action:** `edit_ad` on ad #142 with rewritten text. See `create-telegram-ads-campaign-workflow` for the full approval-request format.
```

## Server Error Handling

| Error | Action |
|---|---|
| `get_rejection_info` returns `null` (ad not actually rejected) | Re-run `get_ad` to refresh status. The state may have just transitioned. |
| `explain_rejection` returns a generic message | Defer to `get_rejection_info` verbatim. Do not invent specifics. |
| `validate_ad` returns `ok: false` on the rewrite | Fix the indicated field, re-validate. Do not submit. |
| `edit_ad` returns `fingerprint_mismatch` | Re-prepare approval. The EditAdDraft bytes changed between prepare and apply. |
| `edit_ad` returns `api_error: …` | The `confirmation_id` is burned. Re-prepare with fixed params, re-apply. |

## Common Pitfalls

1. **Paraphrasing the rejection reason.** Telegram's wording is the contract. Use it verbatim, in quotes, with the category label. Do not soften or reword.
2. **Rewriting the offer, not just the framing.** The skill is for compliance, not strategy. If the product itself violates policy, the right action is to stop, not to rephrase.
3. **Auto-resubmitting without approval.** Even if the rewrite is obviously policy-clean, `edit_ad` / `create_ad` is CONFIRM_REQUIRED. Always show the rewrite + screenshot to the operator first.
4. **Treating `explain_rejection` as authoritative.** It is advisory. The authoritative source is `get_rejection_info`.
5. **Submitting a rewrite without re-validating.** Even small changes can trigger new policy issues. Always re-run `validate_ad` on the new draft before approval.
6. **Calling `pause_ad` before `edit_ad`.** `edit_ad` already pauses the ad during re-review. A separate `pause_ad` is redundant and burns an extra `confirmation_id`.
7. **Mistaking "limited" for "rejected".** `status: "limited"` means the ad is running but reach is restricted (e.g. few impressions). The fix path is different (CPM / targeting / creative refresh), not a policy rewrite. Treat as a separate diagnosis, not a decline.
8. **Deleting the declined ad on autopilot.** `delete_ad` is DOUBLE_CONFIRM. Never delete without explicit operator approval, even if `status: "rejected"`. The declined ad may still be useful as a historical record or as a reference for the rewrite.
9. **Resubmitting the same draft unchanged.** Even a single-byte change is the minimum bar for a re-attempt. Submitting the same draft is a guaranteed repeat rejection and burns a `confirmation_id`.
10. **Stacking multiple changes per resubmit attempt.** Defeats the attribution principle. One visible change per attempt so the next category can be tied to the next change.
11. **Exceeding the 3-attempt cap without explicit operator approval.** Cap exists to prevent oscillation loops. If 3 attempts have not resolved it, the issue is likely structural — escalate.
12. **Treating "limited" status like a decline for the resubmit loop.** `status: "limited"` is a reach-restriction, not a moderation rejection. The fix path is CPM / targeting / creative refresh, not a policy rewrite. Do not enter the controlled micro-resubmit loop on a `limited` ad.
13. **Attempting `edit_ad` on a search campaign to change queries.** Queries are immutable after create. If the issue is query-level, you must `create_ad` a new ad.
14. **Resubmitting after a product-level prohibited-content category.** If Telegram has flagged the product itself, rephrasing is futile. Pause, report to the operator, and consider a different product framing or stopping the campaign.
15. **Skipping the Pre-Mutation Re-Confirmation 4-step (Step 1.5).** This skill ends with a mutating `edit_ad` or `create_ad` re-submit. The 4-step (`choose_account` → `current_account` → `get_account_budget` → only then `prepare_approval_request`) is mandatory before any re-submit, not just for fresh creates. The decline of an old ad is not a substitute for the live cabinet re-confirm.
16. **Trusting `list_accounts.balance` for the rewrite's budget sanity check.** A declined ad's reserved budget may have been returned to the cabinet. The live balance is the source; `list_accounts.balance` is a stale snapshot.
17. **Interpreting numeric suffixes in campaign names as quantity.** Ad titles like "Search 2", "Example 1", "V1", "Test 3" are part of the title, not a count. "Search 2 declined" means one campaign whose name is "Search 2" and whose `status` is `declined` — not two declined search campaigns. Tokenise the title as a single string. The `ad_id` is the only authoritative campaign identifier; the title is a free-form display label. This applies to: parsing agent messages, parsing tool responses (e.g. `get_ad_stats` ad_title fields), parsing transaction logs (e.g. `get_account_budget` `ad_title` for `transfer_to_ad` / `returned_from_ad`), and writing reports.

## Verification Checklist

- [ ] `get_ad`, `get_rejection_info`, `explain_rejection` were all called; the verbatim category + description is in the report.
- [ ] The rewrite preserves the offer; only the framing was changed.
- [ ] The rewrite was re-validated with `validate_ad` (no errors) before any approval was requested.
- [ ] The approval request showed the exact tool call (`edit_ad` or `create_ad`) with the new params.
- [ ] The `confirmation_id` was fresh from `prepare_approval_request`; no reuse.
- [ ] No `pause_ad` was called before `edit_ad` (redundant).
- [ ] Post-action watches were created after the approved `edit_ad` / `create_ad`, with `thresholds.approved_action.source == "approved_telegram_ads_action"`.
- [ ] Created watch IDs were reported to the operator, and the action was not marked complete until watcher verification / expected state observation / diagnostic routing.
- [ ] If entering the controlled micro-resubmit loop: each attempt is logged (attempt #, change, outcome, category if declined), at most one visible change per attempt, default cap of 3, and the loop has stopped on category repetition or product-level prohibition.
- [ ] For search campaigns: query-level issues were routed to `create_ad` (recreate), not `edit_ad`; creative-level issues were routed to `edit_ad`, with the query list preserved.
- [ ] The Pre-Mutation Re-Confirmation 4-step (Step 1.5) was executed before any rewrite was re-submitted; all 4 checks passed; the cabinet matches the declined ad's original cabinet.
- [ ] Any numeric suffix in an ad title (e.g. "Search 2", "Example 1") was treated as part of the title, not as a quantity. Reports and routing decisions were based on `ad_id`, not on a parsed count.
