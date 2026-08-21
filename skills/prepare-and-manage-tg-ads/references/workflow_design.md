# Telegram Ads Workflow Layer — Architecture

**Status:** Phase 1 implemented. Phases 2–4 designed, not implemented.

---

## Architecture

```
telegram_ads_workflow(workflow="snapshot", ...)
  │
  ▼
tools/telegram_ads_workflow_tool.py    ← 1 Hermes tool, workflow discriminator
  │  dispatch by workflow name
  ▼
hermes_telegram_ads/workflows/         ← Python modules
  │  compose adapter methods, compute metrics
  ▼
hermes_telegram_ads/adapter.py         ← low-level adapter (TelegramAdsAdapter)
  │
  ▼
ads.telegram.org
```

**Key design principles:**
- Analysis workflows (snapshot, inspect_ad, review, fix, prepare) = safe read + draft = no approval
- Execution workflows (launch, change_bid, stop) = confirm-required = require confirmation_id from prior approval
- Workflow output is always structured dict, never raw adapter output
- Each workflow has typed inputs, not generic `params: dict`
- Low-level adapter remains thin — workflow layer computes metrics and prepares approval requests

---

## Phase 1 (implemented)

### Workflow: `snapshot`

Full account overview: balance, all campaigns with computed metrics, events, recommendations.

```
telegram_ads_workflow(workflow="snapshot")
telegram_ads_workflow(workflow="snapshot", account_token="abc123")
```

Low-level calls: `ensure_logged_in` → `list_accounts` (optional) → `list_ads` → `get_account_budget` → `list_events`

Output structure:
```json
{
  "ok": true,
  "workflow": "snapshot",
  "data": {
    "account": {"title": "...", "type": "...", "currency": "TON", "balance": 50.0},
    "budget": {"balance": 50.0, "currency": "TON", "last_transactions": [...]},
    "ads": [
      {"ad_id": 42, "title": "...", "status": "Active", "views": 1200,
       "clicks": 12, "ctr": 1.0, "cpm": 2.0, "spent": 2.4, "budget": 10.0,
       "budget_used_pct": 24.0}
    ],
    "ads_count": 1,
    "events_count": 0,
    "recommendations": ["1 ad(s) running: ~76% budget remaining."]
  }
}
```

Recommendations generated:
- "No ads in this account." when empty
- "{N} ad(s) running: ~X% budget remaining." for active ads
- "High budget usage (#id): consider top-up." when > 80% spent
- "Low CTR (#id): review creative or targeting." when CTR < 0.3% and views > 500
- "No active ads." when all are stopped

Error handling: `list_events` failure → events_count = -1 (best-effort). All other errors propagate through `run_workflow` error classification.

Tests: 5 unit tests (basic, empty, account_token, login_error, events_error).

---

### Workflow: `inspect_ad`

Detailed ad inspection with full detail, stats, computed metrics.

```
telegram_ads_workflow(workflow="inspect_ad", ad_id=42)
```

Low-level calls: `ensure_logged_in` → `get_ad` → `get_ad_stats` → `get_share_stats_url`

Output structure:
```json
{
  "ok": true,
  "workflow": "inspect_ad",
  "data": {
    "ad": { ... },           /* full AdDetail */
    "stats": { ... },        /* AdStats */
    "share_stats_url": "https://...",
    "metrics": {
      "ctr": 1.5,
      "cpc": 0.2,
      "cpa": null,           /* null if no actions */
      "daily_spend_rate": 0.6,
      "budget_remaining": 7.0,
      "budget_used_pct": 30.0
    },
    "decline_reason": {
      "category": "Prohibited content",
      "description": "..."
    }
  }
}
```

Metrics computation:
- CTR = clicks/views × 100 (0 if no views)
- CPC = spent/clicks (null if no clicks)
- CPA = spent/actions (null if no actions)
- daily_spend_rate = spent / days (days = len(monthly) or 1)
- budget_remaining = max(budget - spent, 0)
- budget_used_pct = spent/budget × 100 (0 if no budget)

Error handling: `get_share_stats_url` failure → null. Missing ad_id → ValueError.

Tests: 6 unit tests (basic, declined, not found, missing id, share unavailable, zero metrics).

---

### Workflow: `account_diagnosis` (Phase 1 extension)

Per-account deep-dive diagnosis. Answers: *"Why doesn't this cabinet look the way I expect?"*

Read-only, no approval. See `projects/account_diagnosis_design.md` for full design.

```
telegram_ads_workflow(workflow="account_diagnosis", account_name="the operator")
telegram_ads_workflow(workflow="account_diagnosis", account_token="...", include_archive=True, compare_ui_type=True, expected_account_type="TON")
```

**Parameters:**

| Field | Type | Default | Description |
|---|---|---|---|
| `account_name` | `str` | None | Case-insensitive substring match against `list_accounts[].title` |
| `account_token` | `str` | None | Exact token; **priority** over name |
| `include_archive` | `bool` | True | Compute stopped/declined/limited/on_hold counts |
| `check_filters` | `bool` | True | Reserved: filter state observation (DOM probe not implemented → always null) |
| `compare_ui_type` | `bool` | False | Compare observed UI type with `expected_account_type` |
| `expected_account_type` | `str` | None | `"TON"` \| `"STARS"` \| `"Bot"` |

**Low-level calls:** `ensure_logged_in` → `list_accounts` → `choose_account` → `get_current_account` → `parse_ads` → `get_account_budget`

**Output (top-level keys):**
```json
{
  "diagnosis_timestamp": "...",
  "diagnosis_date": "2026-06-02",
  "target": {"requested_by": {...}, "resolved": {title, account_token_hash, account_type_observed, currency, match_type}},
  "balance": {value, currency, available},
  "campaigns_visible": {total, active, stopped, on_hold, declined, limited, unknown},
  "empty_state_detected": bool,
  "empty_state_text": null,
  "archive_checked": bool,
  "archive": {stopped, on_hold, declined, limited, empty},
  "filters": {checked, present, cleared, reason_if_unchecked},
  "ui_type": {observed, expected, match, comparison_skipped},
  "data_quality": "complete" | "partial" | "unreliable",
  "data_quality_notes": [...],
  "warnings": [...],
  "parser_warnings": [...],
  "conclusion": "human-readable summary"
}
```

**Honest limitations (intentional, in design):**
- `filters.present` and `filters.cleared` are always `null` (DOM probe not in adapter).
- "Archive" is semantic (stopped+declined+limited+on_hold counts), not a separate UI view.
- `account_type` matching is heuristic (currency + account_type field), not authoritative.
- Conclusion **never** claims "campaigns were deleted" or "never had campaigns" — only "as visible in the current dashboard".

**Error handling:** `ACCOUNT_NOT_FOUND` (by name or token), `CHOOSE_ACCOUNT_FAILED`, `parse_ads` failure → `data_quality="unreliable"` + diagnosis continues. `browser_profile_locked` / `browser_profile_busy` propagated as-is.

**Tests:** 45 unit tests covering: empty account, account with active campaigns, archive inventory, name/token resolution, ambiguity, case-insensitivity, parse_ads failure, data_quality propagation, ui_type match/mismatch/skipped, expected_account_type validation, balance failure, archive skipped, filter state null-by-design, no-name-no-token fallback, resolved section structure, choose_account failure, conclusion safety (epistemic honesty), conclusion presence, workflow registration.

---

## Phases 2–4 (designed, not implemented)

| Phase | Workflows | Status |
|---|---|---|
| 2 | `prepare_campaign`, `fix_rejection`, `prepare_bid_change` | Designed |
| 3 | `review_campaign` | Designed |
| 4 | `launch_after_approval`, `change_bid_after_approval`, `stop_after_approval` | Designed |

Full design document: `/home/hermes/.hermes/projects/telegram_ads_workflow_design.md`

---

## Tool registration

| Field | Value |
|---|---|
| **Name** | `telegram_ads_workflow` |
| **Toolset** | `telegram_ads` (same as low-level tool) |
| **Check fn** | `_check_workflow_enabled()` — package importable + config enabled |
| **Check fn** | `_check_workflow_enabled()` — package importable + config enabled |
| **Adapter** | Shared via `BrowserProfileManager` (singleton, `hermes_telegram_ads/browser_manager.py`). Both `telegram_ads_tool` and `telegram_ads_workflow_tool` use the same manager; adapter is created ONCE. Manager handles: (1) external profile lock (SingletonLock from another process) → `browser_profile_locked`, (2) in-process contention (asyncio.Lock) → `browser_profile_busy`. Lock detection order: if manager already has adapter → reuse, skip SingletonLock check. |
| **Schema** | Single tool with `workflow: \"snapshot\" | \"inspect_ad\"` discriminator |
| **Release policy** | `release_adapter()` decrements active_operations, releases asyncio.Lock. Adapter stays alive for next workflow. Close only on explicit `shutdown()`.
| **Schema** | Single tool with `workflow: "snapshot" | "inspect_ad"` discriminator |

## Safety

| Workflow | Safety gate | Approval |
|---|---|---|
| `snapshot` | None (safe read only) | ❌ |
| `inspect_ad` | None (safe read only) | ❌ |

Phase 1 schema does NOT expose `confirmation_id`, `draft`, `new_cpm`, or `pause`.
