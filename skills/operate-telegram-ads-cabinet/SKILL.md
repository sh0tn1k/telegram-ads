---
name: operate-telegram-ads-cabinet
description: "Operational workflow for inspecting and switching between Telegram Ads cabinets: status check, login state, list cabinets, select active account, read balance and recent transactions. Use when the operator asks to inspect/select/switch a cabinet, check login, view account balance, or 'which cabinet am I on'."
version: 1.0.2
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [telegram-ads, operations, cabinet, account, read-only]
    related_skills: [operate-telegram-ads, create-telegram-ads-campaign-workflow, format-telegram-ads-report]
---

# Operate Telegram Ads Cabinet

Read-only operational workflow for inspecting Telegram Ads cabinets (accounts). Use this skill when the task is **purely observational** — see which cabinets exist, which one is active, what the balance is, or whether the session is logged in. Do **not** use it for campaign-level work; that belongs in dedicated operational skills.

## Pointer to support files

- `references/canonical-source-and-pipe-title-handling.md` — session-specific
  condensed reference for the canonical source order, the demonstrated
  pipe-title markdown failure mode with safe render templates, the
  `is_active` / `account_ref_source` / per-campaign `status` distinction, and the
  tool-loop detector false-positive pattern. Read this when producing a
  cabinet-level audit or report; the rules below are the canonical form.
- `references/profile-loader-and-browser-lock-semantics.md` — load when
  investigating `profile_dir` divergence, "wrong" Chromium profile, or
  `BrowserProfileLockedError` from a standalone smoke/watcher. Covers the
  `TelegramAdsConfig.from_yaml` vs `default()` contract, the `from_dict`
  AttributeError that silently fell back to defaults, the two distinct lock
  states (typed-tool registry vs Chromium filesystem), and the four
  ownership models for the persistent Chromium profile.

## When to Use

- the operator asks "which cabinet am I on?", "list my Telegram Ads accounts", "what's my balance?".
- Login state needs to be checked before any further Telegram Ads action.
- A task starts with "look at cabinet X" and no other Telegram Ads tool call has been made yet.
- The output of a previous Telegram Ads action is ambiguous about which cabinet it referred to.

## When NOT to Use

- Listing or inspecting individual campaigns → use `inspect-ad` workflow (typed) or manual `list_ads` / `get_ad`.
- Multi-account aggregated snapshot → use `telegram_ads_workflow(workflow="snapshot")`.
- Per-account deep-dive ("why does cabinet X look empty?") → use `telegram_ads_workflow(workflow="account_diagnosis")`.
- Creating, editing, stopping, or changing CPM on a campaign → use the dedicated operational skills.

## Operating Discipline (mandatory)

This skill is read-only. It must still follow `~/.hermes/shared/TELEGRAM_ADS_TOOL_CONTRACT.md` §0 (Operating Discipline). Key reminders:

- **Tool is the only interface.** No `browser_navigate` to `ads.telegram.org`, no `ps`/`pkill`, no second Chromium.
- **`browser_profile_locked` / `browser_profile_busy` is terminal** for the current task. Surface the structured error; do not retry.
- **No approval required** for any of the tools in this skill. All are SAFE_READ.
- **No `screenshot` of the dashboard unless the operator asks** — keep the output text-only and compact.

## Standard Procedure

The default order of tool calls. Skip steps only when their output is already known from a prior call in the same session.

### Step 1 — Verify session health

```
telegram_ads_status()
```

Interpret:
- `session_active: true` → continue.
- `login_required: true` → stop, surface `LOGIN_REQUIRED` envelope to the operator. Do **not** call `telegram_ads_login_start` without explicit approval (login flow is a separate skill topic; treat as sensitive).
- `profile_locked: true` or `browser_profile_busy` → stop, surface the structured error.
- Any other error code → stop, surface verbatim, do not retry blindly.

### Step 2 — List all cabinets

```
telegram_ads_list_accounts()
```

Parse response into a compact table. **Avoid `|` inside table cells** — the auto-rewriter treats every `|` as a column separator. Use em-dash, backticks, or HTML entity `&#124;` for titles that contain a pipe:

| # | title | currency | balance | account_ref | is_current |
|---|---|---|---|---|---|
| 1 | Personal Account | TON | 50.00 💎 | `acc_…` | ✅ |
| 2 | `Example Bot — Short Clips` | STARS | 0 ⭐️ | `acc_…` | ❌ |

Notes for the report:
- **Never** print full account tokens. Use the opaque `account_ref` returned by the tool, which is already safe to display.
- Note the **currency** for each cabinet (TON 💎 vs STARS ⭐️) — affects CPM minimums and parser expectations.
- If `is_current: true` is not present, no cabinet is selected yet. Steps 3-4 must run.

### Step 3 — Read active-cabinet details (if a cabinet is selected)

```
telegram_ads_current_account()
telegram_ads_get_account_budget()
```

Parse:
- `title`, `currency` — confirm matches the cabinet the operator intended.
- `balance` — note current spendable amount.
- `transactions[]` — surface the last 3-5 transactions (date, amount, type) as a small list. Skip older ones to keep the report compact.

### Step 4 — Switch cabinet

`choose_account` is a state change, but for **read-only** inspections it is permitted without a separate approval **iff** the operator explicitly named the cabinet (by title, project, or unambiguous context). This is a clarification of an existing rule — not a new permission.

**Named-cabinet read-only inspection — no separate approval required.** Examples that count as an explicit ask:

- "посмотри ExampleBot" → select the cabinet whose name matches (e.g. "Example Bot — Short Clips" or "Example Bot | Short Clips" or similar).
- "проверь кабинет Example Guard" → select `Example Guard Bot`.
- "сделай отчёт по Example Bot" → select the Example Bot cabinet.
- "Personal Account balance" → select the Personal Account cabinet.
- "look at the STARS cabinet" → resolve to the cabinet with `currency: STARS` (only when exactly one cabinet matches the currency; otherwise ask).
- Any phrase of the form `<verb> <cabinet-name-or-project>` where the cabinet is unambiguously identified.

Rationale: the user has explicitly told us which cabinet to inspect. The state change is bounded (only flips the session's `current_account` pointer) and serves the read-only task they asked for. It is **read-only navigation** inside the Telegram Ads cabinet system, not a mutation of any ad, budget, or campaign.

**Ambiguous intent — do not switch on your own.** If the operator's intent is ambiguous (no project name, no cabinet identifier, multiple cabinets match the hint, or the phrase is generic like "check Telegram Ads"), do **not** switch. Ask the operator to disambiguate. Examples that require disambiguation:

- "проверь рекламу" (no cabinet named)
- "посмотри кабинеты" (read-only listing is fine, but no specific cabinet to switch to)
- "Example" (could mean "Example Bot | Short Clips" or some other Example project in the future)

**Mutating actions — separate explicit operator approval is still required.** The following actions always require explicit operator approval, regardless of whether the cabinet was named:

- `create_ad`
- `edit_ad`
- `start_ad`
- `stop_ad`
- `change_cpm`
- `add_to_budget`
- `withdraw_from_budget`
- `delete_ad`
- `revoke_share_stats_url` (revoke stats URL)
- `create_event` / `delete_event` (pixel conversion events)
- `set_pixel` (forbidden; do not attempt)
- Any other action with external, account-level, or budget-level consequences

Even if the operator said "создай тестовую кампанию в Example Bot" (named cabinet + mutating action), the **mutation** still requires approval. The named-cabinet rule only relaxes the *navigation* step (Step 4 of this skill), not the *mutation* step (which is gated by §"Pre-Mutation Re-Confirmation Checklist" below).

```
telegram_ads_choose_account(account_ref="acc_…")
```

After switching, **always** re-run `telegram_ads_current_account()` to confirm the switch took effect. If the tool returns a different cabinet than requested, surface the discrepancy — do not silently proceed.

## Output Format

```md
## Cabinet inspection

**Session:** ✅ logged in
**Active cabinet:** `Example Bot — Short Clips` (STARS ⭐️)
**Balance:** 0 ⭐️
**Recent transactions:**
- 2026-06-05  −500 ⭐️  ad_spend  (ad #142)
- 2026-06-04  −250 ⭐️  ad_spend  (ad #142)
- 2026-06-03  +1000 ⭐️ top_up

**Other cabinets (N):**
| # | title | currency | balance |
|---|---|---|---|
| 1 | Personal Account | TON | 50.00 💎 |
| 2 | Marketing Holdco | TON | 0 💎 |
```

If the operation failed:

```md
## Cabinet inspection — failed

**Error:** `browser_profile_locked` (profile_path=…, owner_pid=…)
**Recommended action:** Retry later or restart `hermes-gateway-default.service` (requires explicit operator approval).
```

## Server Error Handling

| Error code | Meaning | Action |
|---|---|---|
| `login_required` | Session expired / not logged in | Surface to the operator; request explicit approval for `login_start` |
| `browser_profile_locked` | Another process holds the Chromium profile | Stop, surface structured error, do **not** retry |
| `browser_profile_busy` | Adapter busy in this process | Stop, surface, do not retry |
| `api_error` | Telegram API error | Surface the verbatim `error.message`; do not paraphrase |
| `network_error` | Transient fetch error | One retry after 2s; if it fails again, surface |
| `invalid_param` | Tool got a bad arg | Stop, fix the call, do not retry same input |

**Never** retry more than twice for transient errors. After two failures, surface the error to the operator with full context.

## Cabinet Object Semantics (mandatory)

These rules apply to **every** cabinet-level operation, not just inspection:

1. **`is_active` / `current_account` describes the currently selected cabinet in the browser/session.** It is **not** an indicator of ad activity, bot activity, or campaign status. A cabinet can be `is_active: true` and have zero running ads; a cabinet can be `is_active: false` and have live campaigns.
2. **For ad activity, use the per-campaign `status` field** returned by `telegram_ads_list_ads` / `telegram_ads_get_ad`. The valid campaign-status vocabulary is: **Active / On Hold / Declined / Stopped** (lowercase per the report formatter: `active` / `on_hold` / `declined` / `stopped`). Do not infer ad activity from the cabinet's `is_active`.
3. **`account_ref_source: "reconciled"` / `"current_account"` / `"list_accounts"` describes how the `account_ref` was obtained** (by token re-confirmation, by current-account-only path, by list-only path). It is **not** an activity indicator either.

## Pre-Mutation Re-Confirmation Checklist (mandatory)

Before **any** mutating Telegram Ads action (`create_ad`, `edit_ad`, `change_cpm`, `start_ad`, `stop_ad`, `add_to_budget`, `withdraw_from_budget`, `delete_ad`, `set_pixel`, `create_event`, `delete_event`, etc.), the following read-only sequence is required:

1. `choose_account(account_ref=<target>)` — select the intended cabinet.
2. `current_account` — confirm:
   - `title` matches the intended cabinet (pipe-containing titles must be checked in code-block form, not table — see `format-telegram-ads-report` §"Account / Ad / Campaign Title Safety");
   - `currency` matches the intended budget unit (TON 💎 / STARS ⭐️);
   - `account_type` matches the intended cabinet class (Personal Account / Bot Account / etc.).
3. `get_account_budget` — confirm the balance is what is expected.
4. Only then prepare the approval request (`prepare_approval_request`) and proceed to the mutating action.

If any of those 4 checks fails, **stop and surface** to the operator. Do not proceed to the mutating action.

## Post-Action Watcher Policy (mandatory)

After **every approved Telegram Ads mutating action**, the agent must create read-only post-action watches before reporting the action as complete. This applies to all approved actions executed through Telegram Ads tools:

- `create_ad` → `create_post_action_watches(action="create_ad", ad_id=<created_ad_id>)`
- `edit_ad` → `create_post_action_watches(action="edit_ad", ad_id=<ad_id>)`
- `change_cpm` → `create_post_action_watches(action="change_cpm", ad_id=<ad_id>, expected={"cpm": <new_cpm>})`
- `add_to_budget` → `create_post_action_watches(action="add_to_budget", ad_id=<ad_id>, expected={...})`
- `withdraw_from_budget` → `create_post_action_watches(action="withdraw_from_budget", ad_id=<ad_id>, expected={...})`
- `start_ad` → `create_post_action_watches(action="start_ad", ad_id=<ad_id>, expected={"status": "active"})`
- `stop_ad` → `create_post_action_watches(action="stop_ad", ad_id=<ad_id>, expected={"status": "stopped"})`
- `delete_ad` → `create_post_action_watches(action="delete_ad", ad_id=<ad_id>)`

Every generated post-action watch must carry `thresholds.approved_action` metadata before it is persisted or immediately after creation via `update_watch`:

```json
{
  "approved_action": {
    "source": "approved_telegram_ads_action",
    "action": "<action>",
    "ad_id": "<ad_id if campaign-level>",
    "approved_by": "operator",
    "created_by": "agent"
  }
}
```

Hard completion rule: the agent must not consider an approved Telegram Ads action complete until one of these is true:

1. a `post_action_verified` event is observed;
2. the expected campaign status / budget / CPM is observed by the watcher;
3. a `post_action_not_verified` or `watch_error` event is routed to a diagnostic task.

Watcher runtime policy:

- baseline `login_state` watches are allowed as health monitoring;
- campaign/account post-action watches are allowed only when tied to an approved action via `thresholds.approved_action`;
- arbitrary campaign/account watches require separate explicit operator approval;
- watcher remains read-only;
- all Telegram Ads mutations remain approval-gated;
- watcher must never auto-fix, auto-recreate, auto-start/stop, or auto-change budget/CPM without a new explicit approval.

## Cabinet Disagreement Resolution (mandatory)

When `list_accounts` and `current_account` disagree for the same cabinet (identified by `(title, currency, account_type)` fingerprint):

- For the **currently selected** cabinet, **trust** `current_account` + `get_account_budget` for the live state.
- `list_accounts.balance` is a stale / low-trust snapshot; treat it as informational only.
- Surface the disagreement in the report's `Anomalies / Data Quality` section as a `partial` data-quality note, not as a hidden bug.
- Do not use the list-view's `is_active` flag to second-guess the session's selected cabinet — they are independent.

## Common Pitfalls

1. **Printing full account tokens.** Always use `account_ref` (opaque, safe). The raw `access_token` field returned by the low-level tool is for internal use only.
2. **Assuming the active cabinet.** After listing accounts, always re-confirm via `telegram_ads_current_account()` before treating any cabinet as "current". The list and the current state can be out of sync.
3. **Switching cabinets on ambiguous intent.** If the operator says "look at the ads" without naming a project / cabinet, list them first and ask. Do not guess. If the hint is unambiguous (named project, named cabinet, explicit currency), switch without a second approval — that single mention is the approval for a read-only inspection.
4. **Combining balance from different currencies.** Never sum TON and STARS — different units, different CPM minimums, different billing. Report each cabinet's balance in its own currency.
5. **Silent retry on `browser_profile_locked`.** That error means another process owns the profile. Retrying will not help and may make the situation worse. Stop and surface.
6. **Calling `telegram_ads_login_*` from this skill.** Login is sensitive (requires the operator's explicit approval and phone number). Treat as a separate workflow.
7. **Inferring ad activity from `is_active` on the cabinet.** `is_active: true` only means the cabinet is the session's selected one. It says nothing about whether the cabinet has running ads, paused ads, or any campaigns at all. For ad activity, use the per-campaign `status` field (Active / On Hold / Declined / Stopped).
8. **Using `list_accounts.balance` as the authoritative balance.** It is a stale snapshot. Canonical source for the live balance is `get_account_budget` on the currently selected cabinet, reached via `choose_account` → `current_account`. If the two disagree, trust the budget view; surface the disagreement as a data-quality note.
9. **Inferring ad activity from `account_ref_source: "reconciled"`.** `account_ref_source` describes the ref-acquisition path, not the activity state. A `reconciled` ref can point to a cabinet with zero running ads; the per-campaign `status` is the right source.
10. **Skipping the Pre-Mutation Re-Confirmation Checklist.** Any mutating action without the 4-step re-confirm (`choose_account` → `current_account` → `get_account_budget` → only then `prepare_approval_request`) risks operating on the wrong cabinet, the wrong currency, or a stale balance. The checklist is mandatory; no exceptions.
11. **Treating `current_account` `account_ref` as the only safe ref.** When `current_account` returns `account_ref_source: "current_account"` (unreconciled, with a `warnings` array), the ref is unstable. Prefer to call `list_accounts` first to populate `_fingerprint_to_ref`, then re-select by token to land on `account_ref_source: "reconciled"` before any mutating action.
12. **Treating a `same_tool_failure_warning` / `repeated_exact_failure_warning` from the agent-side tool-loop detector as a real tool failure.** The detector flags repetitive tool-call patterns (e.g. `choose_account` + `current_account` + `get_account_budget` × 4 cabinets in a row) as a "loop" even when the tool itself returns `ok: true` each time. The detector is structural, not semantic. Read the actual tool response — if it's `ok: true` with the expected payload, the call succeeded; the detector warning is informational. Don't stop the workflow on a detector warning alone; do stop on a tool `ok: false` or unexpected error. **Pattern encountered**: in a 4-cabinet balance audit, the detector fired 4× on `choose_account` and 4× on `current_account`. All 8 tool responses were `ok: true`. The detector was right that the pattern looked repetitive; it was wrong that anything was failing.
13. **Conflating "named cabinet" with "approved mutation".** the operator naming a cabinet (e.g. "посмотри ExampleBot", "проверь кабинет Example Guard", "сделай отчёт по Example Bot") is **explicit approval for read-only navigation** (`choose_account` + `current_account` + `get_account_budget`). It is **not** approval for any mutating action. A subsequent "create a test campaign in Example Bot" still requires the separate mutation approval, even though Example is the named cabinet. The named-cabinet rule relaxes the **navigation gate**; the **mutation gate** is independent.
14. **Trusting `TelegramAdsConfig.from_dict` exists — it does not.** The typed toolset loader (`tools/telegram_ads_typed_tool.py:_make_toolset()`) historically called `TelegramAdsConfig.from_dict(block)`, which raises `AttributeError` and silently fell through to `TelegramAdsConfig.default()`. Default's `BrowserConfig.profile_dir = Path("./browser_profiles/telegram_ads")` is **relative** and resolves against the gateway's CWD — typically `/home/hermes/.hermes/hermes-agent/browser_profiles/telegram_ads`, NOT the `~/.hermes/telegram_ads.yaml` profile (`/home/hermes/.hermes/data/telegram_ads/browser_profile`). The fallback is **silent**: no warning, no log. The first symptom is usually "the gateway uses the wrong profile dir" or "a standalone smoke can't acquire the profile because the gateway's chromium holds the wrong one." **Fix contract (always available, no need to re-discover):** the loader's first try should be `TelegramAdsConfig.from_yaml(SHARED_CONFIG_PATH)`, with `model_validate(block)` as the second try, and `default()` as the final fallback. When debugging a profile-related symptom, run `TelegramAdsConfig.from_yaml(SHARED_CONFIG_PATH)` in isolation and compare its `browser.profile_dir` to what `telegram_ads_login_check` actually reports in `data.profile_dir`. If they disagree, the loader fell back.
15. **Conflating "profile locked" with "wrong profile" without reading `data.profile_dir`.** `telegram_ads_login_check` returns `data.profile_dir` on every call. If you only see `state: logged_in` and `browser_state: healthy` but you are debugging a profile issue, you skipped the load-bearing field. **Always read `data.profile_dir` and confirm it matches the `browser.profile_dir` from `TelegramAdsConfig.from_yaml(SHARED_CONFIG_PATH)`.** A mismatch means the loader fell back to `default()` (see pitfall 14) and the gateway's chromium is using a different profile than the yaml says.
16. **Treating `telegram_ads_get_browser_profile_info`'s `profile_locked: false` as proof the profile is free.** The `profile_locked` field returned by typed tools is the **typed-tool registry's Python-level lock** (in-process `BrowserProfileManager._lock`). The **Chromium filesystem lock** (`SingletonLock` under the persistent profile dir) is a separate state. A profile can be `profile_locked: false` (registry says free) while `SingletonLock` points at a live Chromium process. When diagnosing a standalone smoke that fails with `Opening in existing browser session`, the file-level `SingletonLock` symlink and its target PID (e.g. `host-4164123` → PID 4164123) is the source of truth, not the typed-tool registry field.
17. **Expecting `telegram_ads_login_check` to close the chromium it acquired.** It does not. The typed tools acquire the chromium via `BrowserProfileManager.use_adapter(config)` and hold it across calls until the gateway process exits. The chromium PID you observe after `login_check` returns is the **expected steady state**, not a leak. If you must confirm a chromium is running for verification, the read-only check is `pgrep -af 'chrome.*<data.profile_dir>'` — but only do this as a separate explicit approval gate, since `login_check` itself does not report chromium PIDs.
18. **Cleaning up "stale" `Singleton*` files under the old wrong-profile dir.** The default behavior should be: **leave them.** Stale `SingletonLock` / `SingletonCookie` / `SingletonSocket` under a profile dir that the gateway no longer references are **harmless orphans** — different directory = different Chromium profile namespace = different lock. The cleanup cost (filesystem mutation, future audit confusion) is higher than the benefit (a few KB reclaimed). Cleanup is only warranted when (a) the profile is still actively in use and the lock points to a dead PID that the next launch will collide with, OR (b) the profile dir is being retired entirely. In both cases, require an explicit approval gate.

## Verification Checklist

- [ ] `telegram_ads_status()` returned `session_active: true` before any other call.
- [ ] Every cabinet listed has its currency clearly marked (TON 💎 / STARS ⭐️).
- [ ] `account_ref` is shown (not raw tokens).
- [ ] The active cabinet is re-confirmed after `choose_account`.
- [ ] Output is text-only by default; no screenshot of the dashboard unless the operator asked.
- [ ] Any structured error from the tool is surfaced verbatim — never paraphrased or hidden.
- [ ] Before any mutating action was taken, the Pre-Mutation Re-Confirmation Checklist (4 steps) was executed and all 4 checks passed.
- [ ] No claim of "ad activity" or "bot activity" was sourced from the cabinet's `is_active` or `account_ref_source` field. Such claims are sourced from per-campaign `status` (Active / On Hold / Declined / Stopped).
- [ ] No `list_accounts.balance` is reported as the authoritative balance. If shown, it is paired with a `current balance` from `get_account_budget`, and the disagreement (if any) is in `Anomalies / Data Quality`.
