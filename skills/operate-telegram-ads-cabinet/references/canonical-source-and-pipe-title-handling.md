# Canonical Source Hierarchy & Pipe-Title Handling

Session-condensed reference for the cabinet-level audit pattern that emerged from
the 2026-06-06 multi-cabinet balance check. Capture the demonstrated failure mode
and the canonical-source order so a future agent starts with both.

## 1. Canonical source order (for any balance claim)

```
1. choose_account(<target>)            # state change: select the cabinet
2. current_account                     # confirms the selection
3. get_account_budget                  # AUTHORITATIVE balance for the selected cabinet
4. list_accounts.balance               # STALE / low-trust — informational only
```

`list_accounts.balance` is acceptable as a hint, **never** as a source of truth.

In a 2026-06-06 audit of 4 cabinets, `list_accounts.balance` was wrong on 3 of 4
(`Example Personal` 0.00 vs actual 16.75 TON, `Example Bot | Short Clips` 0.00 vs actual
549 ⭐️, `Example Guard Bot` 0.00 vs actual 600 ⭐️). `Example TON` was the only match.
The pattern is not one-off: Telegram's list view is a snapshot that lags the live
state.

When `list_accounts` and `current_account` disagree for the same cabinet
(identified by `(title, currency, account_type)` fingerprint), **trust the
current + budget view for the live state**; surface the disagreement in
`Anomalies / Data Quality` as a `partial` data-quality note.

## 2. Pipe-title demonstrated failure mode (markdown)

The `Example Bot | Short Clips` title contains a literal `|`. Rendering this in
a markdown table — even with the `title_display` field's pipe-escape `\|` — can
fail. Demonstrated breakage on 2026-06-06:

```
| title | account_ref | currency | account_type | balance |
|---|---|---|---|---|
| Example Bot \ | Short Clips | acc_3 | STARS | Bot Account | 549 |
```

After auto-rewriting, the row becomes a 5-column-shift bug:

| Rendered field | Intended value | Actual rendered value |
|---|---|---|
| `title` | `Example Bot \| Short Clips` | `Example Bot \` |
| `account_ref` | `acc_3` | `AI Shorts Maker` |
| `currency` | `STARS` | `acc_3` |
| `account_type` | `Bot Account` | `STARS` |
| `balance` | `549` | `Bot Account` |

The `title_display` field is necessary but not sufficient — the auto-rewriter
in this rendering pipeline also splits on `|` in some configurations. **Code-block
format is the deterministic safe form** for any title containing `|`.

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

For titles without `|`, both tables and code-blocks are acceptable. Code-block
is preferred for visual consistency.

## 3. `is_active` vs per-campaign `status` — distinct concepts

| Field | Meaning | NOT a meaning of |
|---|---|---|
| `is_active: true` on a cabinet | The cabinet is the session's currently selected one (i.e. `choose_account` last pointed here, or `current_account` returned this ref). | Not "this cabinet has running ads". Not "this bot is online". |
| `account_ref_source: "reconciled" \| "current_account" \| "list_accounts"` | The path through which the `account_ref` was obtained. | Not "active / inactive". Not "ad activity". |
| Per-campaign `status: Active \| On Hold \| Declined \| Stopped` | The actual ad-activity state of a single campaign, returned by `telegram_ads_list_ads` / `telegram_ads_get_ad`. | Distinct from cabinet `is_active`. |

**Ad activity claims must be sourced from per-campaign `status`, not from the
cabinet object.** A cabinet can be `is_active: true` and have zero running ads.

## 4. Tool-loop detector false positives

The agent-side tool-loop detector flags repetitive tool-call patterns as a
"loop" even when the tool itself returns `ok: true` each time. Encountered on
2026-06-06: in a 4-cabinet balance audit, the detector fired 4× on
`choose_account` and 4× on `current_account`. All 8 tool responses were
`ok: true` with the expected payloads.

**Rule**: read the actual tool response. If `ok: true` with the expected
payload, the call succeeded; the detector warning is informational. Don't stop
on a detector warning alone; do stop on a tool `ok: false` or unexpected error.

## 5. Pre-Mutation 4-step checklist (re-stated for quick reference)

Before **any** mutating Telegram Ads action:

1. `choose_account(account_ref=<target>)`
2. `current_account` — confirm `title` (pipe-check in code-block), `currency`, `account_type`
3. `get_account_budget` — confirm balance
4. Only then `prepare_approval_request`

If any check fails: stop, surface, do not proceed.

## 6. Pointer into the parent skill

This file is a session-specific condensed reference. The authoritative rules
are in `SKILL.md`:
- §"Cabinet Object Semantics"
- §"Pre-Mutation Re-Confirmation Checklist"
- §"Cabinet Disagreement Resolution"
- §"Common Pitfalls" items #7–#12

The corresponding report-format rules are in `format-telegram-ads-report`:
- §"Account / Ad / Campaign Title Safety"
- §"Canonical Balance and Transaction Semantics"
