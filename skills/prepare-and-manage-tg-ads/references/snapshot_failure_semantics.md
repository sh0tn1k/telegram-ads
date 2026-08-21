# Snapshot Failure Semantics — `ACCOUNT_SCAN_FAILED`

**Tool:** `telegram_ads_workflow(workflow="snapshot")`
**Location:** `hermes_telegram_ads/workflows/_snapshot.py`
**Added:** 2026-06-02

---

## The problem this solves

A snapshot of multiple Telegram Ads accounts can fail in two **structurally
different** ways:

1. **End-to-end failure** — login failed, `list_accounts` returned an
   error, Xvfb died, the adapter crashed. There is no list of accounts
   to iterate. These failures are surfaced as `LOGIN_REQUIRED`,
   `API_ERROR`, `WORKFLOW_ERROR`, or `INFRA_MISSING` — caught at the
   tool/adapter layer.

2. **Per-account failure** — login worked, `list_accounts` returned N
   accounts (N ≥ 1), but **every** account then failed at the
   `choose_account` / `parse_ads` / `get_account_budget` step. The
   workflow itself completed without exception. The old return shape
   was `{ok: true, accounts: [...skipped entries], total: {0, 0, 0,
   0}, ...}` — which is **indistinguishable from a successful scan of
   N genuinely-empty cabinets**.

Class 2 is what `ACCOUNT_SCAN_FAILED` resolves. We refuse to return
zero-aggregated metrics as a "successful" snapshot when nothing was
actually collected.

## Trigger condition

`accounts_analyzed == 0` **after** the per-account loop has run, and
`list_accounts` returned ≥ 1 account.

If `list_accounts` itself returned 0, the workflow layer still
returns `ACCOUNT_SCAN_FAILED` (the same shape) because we have no
reliable ads data either way.

If login failed before `list_accounts` — that is a class 1 failure
and propagates as `LOGIN_REQUIRED` (or `API_ERROR`). **It is NOT
wrapped as `ACCOUNT_SCAN_FAILED`.** This separation is a tested
invariant.

## Output shape (failure)

```json
{
  "ok": false,
  "error": "ACCOUNT_SCAN_FAILED",
  "message": "No reliable ads data collected.",
  "snapshot_timestamp": "2026-06-02T15:30:00+00:00",
  "snapshot_date": "2026-06-02",
  "account_scope": "all",
  "scope_label": "All 3 accounts",
  "accounts_found": 3,
  "accounts_analyzed": 0,
  "accounts_skipped": 3,
  "accounts": [
    {
      "account": {"title": "Account1", "account_type": "...", "currency": "TON"},
      "warnings": ["Account scan failed: <reason>"]
    }
    // ... one per skipped account
  ],
  "total": null,
  "metrics": null,
  "data_quality": "unavailable",
  "warnings": [
    "ACCOUNT_SCAN_FAILED: 0/3 accounts analyzed. 3 account(s) failed. No reliable ads data collected.",
    "[Account1] Account scan failed: <reason>",
    "[Account2] Account scan failed: <reason>",
    "[Account3] Account scan failed: <reason>"
  ]
}
```

### Key fields

- `ok: false` — explicit failure (not a successful "everything zero"
  scan).
- `error: "ACCOUNT_SCAN_FAILED"` — structured error code (string
  constant `ERROR_ACCOUNT_SCAN_FAILED` in `_snapshot.py`).
- `message: "No reliable ads data collected."` — exact wording,
  LLM-friendly.
- `total: null` and `metrics: null` — **null, not zero-aggregated**.
  Consumers (LLM, dashboards, downstream workflows) MUST treat `null`
  as "no data" and zero as "real zero".
- `data_quality: "unavailable"` — new top-level data-quality label
  (constant `DQ_UNAVAILABLE`). This is **distinct** from
  `"unreliable"`: "unreliable" means "we got data but the parser
  flagged issues", "unavailable" means "we have no data at all".
- `accounts[]` is preserved (skipped entries with per-account
  `warnings[]`). This is critical for diagnostics — the LLM can read
  per-account reasons and surface the real problem.
- `warnings[]` is preserved in the same order they would be in
  success path: top-level summary first, then per-account reasons,
  then the scope warning (if `account_scope != "all"`).

## Success path stays unchanged

A successful scan with **0 campaigns** (cabinet genuinely empty):

```json
{
  "ok": true,
  "data": {
    "snapshot_timestamp": "...",
    "accounts_analyzed": 1,
    "accounts_skipped": 0,
    "accounts": [
      {
        "account": {"title": "Empty", "currency": "TON", "balance": 50.0},
        "campaigns": {"total": 0, "active": 0, "stopped": 0, "declined": 0, "limited": 0},
        "performance": {"impressions": 0, "clicks": 0, "ctr": 0.0, "spent": 0.0},
        "data_quality": "complete",
        "events_count": 0,
        "warnings": []
      }
    ],
    "total": {
      "accounts_analyzed": 1,
      "campaigns_total": 0,
      "impressions": 0,
      "clicks": 0,
      "ctr": 0.0,
      "spent_total": 0.0
    },
    "warnings": []
  }
}
```

**The `ok: true` here is critical.** A genuine empty cabinet is a
valid snapshot; the user wants to know "yes, I scanned, and yes, it's
empty" — not a failure. The `ACCOUNT_SCAN_FAILED` shape is reserved
for the case where **we could not determine** whether the cabinet was
empty.

## Partial scan (some accounts analyzed)

A scan where 1 of 3 accounts was analyzed:

- `ok: true`
- `accounts_analyzed: 1`, `accounts_skipped: 2`
- `total` aggregates only the 1 analyzed account
- `warnings` includes `ACCOUNT_SCAN_FAILED: 0/X`-like summary only
  **if 0 were analyzed**; otherwise warnings reflect the partial
  state without elevating to `ACCOUNT_SCAN_FAILED`

**Rule:** `ACCOUNT_SCAN_FAILED` fires **only** when `analyzed == 0`.

## Interplay with `run_workflow` wrapper (PITFALL)

`hermes_telegram_ads.workflows.run_workflow` currently wraps the
result of `func(adapter, params)` as:

```python
return {"ok": True, "workflow": workflow, "data": result}
```

regardless of `result["ok"]`. This means the LLM-facing envelope is:

- `{"ok": True, "data": {"ok": False, "error": "ACCOUNT_SCAN_FAILED",
  "total": null, ...}}`

The **outer** `ok: True` reflects "workflow did not raise an
exception". The **inner** `data.ok: False` reflects "snapshot data
is not usable". Consumers must check `data.ok` for snapshot-level
semantic success, **not** the outer envelope.

**This is a known design wart** (not a bug per se, but easy to
misread). The fix is to make `run_workflow` honor `result["ok"]` if
present, but that's a separate refactor and out of scope for the
ACCOUNT_SCAN_FAILED patch. Tests against `run_snapshot` directly
(see `tests/test_telegram_ads_snapshot_scan_failed.py`) use the
inner shape.

## Tests

`tests/test_telegram_ads_snapshot_scan_failed.py` — 8 unit tests
covering:

1. `test_all_accounts_skipped_returns_account_scan_failed` — 0/3
   analyzed → `ok=False`, `error="ACCOUNT_SCAN_FAILED"`, `total=None`.
2. `test_partial_scan_returns_ok_true_with_real_zeros` — 1/2
   analyzed → `ok=True`, real zero metrics, **not** failure.
3. `test_total_is_none_on_failure` — `total=None`, `metrics=None`
   (not zero-aggregated).
4. `test_warnings_preserve_per_account_skip_reasons` — per-account
   reasons + scope warning all preserved.
5. `test_success_path_with_zero_campaigns_is_ok_true` — successful
   scan of an empty cabinet → `ok=True`, no false
   `ACCOUNT_SCAN_FAILED`.
6. `test_login_error_does_not_become_account_scan_failed` — login
   exception propagates as `LOGIN_REQUIRED` / `WORKFLOW_ERROR`,
   **not** masked as `ACCOUNT_SCAN_FAILED`.
7. `test_list_accounts_error_does_not_become_account_scan_failed` —
   `list_accounts` exception propagates, **not** masked.
8. `test_data_quality_unavailable_only_on_failure` —
   `data_quality="unavailable"` is top-level failure only, never
   per-account.

All 8 tests run alongside the 13 tests in
`tests/test_telegram_ads_workflow_args.py` and the existing
`test_telegram_ads_workflows.py` (35 tests), `test_telegram_ads_browser_lock.py`
(44 tests), `test_telegram_ads_account_diagnosis.py` (45 tests),
`test_telegram_ads_parser.py` (42 tests), `test_telegram_ads_workflow_tool_lock.py`
(7 tests) — total **193/193 passed** as of 2026-06-02.

## How to extend

If a future workflow needs a similar "collected nothing" semantic:

1. Pick a constant name: `ERROR_<WORKFLOW>_COLLECTED_NOTHING` or
   `ERROR_<WORKFLOW>_INCOMPLETE`.
2. Reuse the `DQ_UNAVAILABLE` data-quality label.
3. Set `total: null` and the workflow-specific metric fields to
   `null`.
4. Preserve per-item skip reasons in `warnings[]` and per-item
   entries.
5. Do **not** mask end-to-end failures (login, list_X errors) — they
   must propagate through the dispatcher unchanged.
6. Add a minimum of 4 unit tests: all-skipped, partial, real-zero,
   and "end-to-end error does not become collected-nothing".
