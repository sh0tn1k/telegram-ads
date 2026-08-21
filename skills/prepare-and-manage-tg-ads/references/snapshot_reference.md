# Snapshot workflow — design reference

**Tool:** `telegram_ads_workflow(workflow="snapshot")`
**Location:** `hermes_telegram_ads/workflows/_snapshot.py`
**Phase:** 1 (read-only)

---

## account_scope

| Value | Behaviour | Use case |
|---|---|---|
| `all` (default) | Iterate all cabinets | "Покажи snapshot" — полная картина |
| `current` | Only the currently selected cabinet | Быстрая проверка |
| `selected` | Specific cabinet by `account_token` | Точечный запрос |
| `project_default` | Project-defined default (same as `current` for now) | Future use |

## Output structure

```python
{
    "snapshot_timestamp": "2026-06-02T15:23:00+00:00",
    "snapshot_date": "2026-06-02",              # from datetime.now(UTC)
    "account_scope": "all",
    "scope_label": "All 3 accounts",            # human-readable
    "accounts_found": 3,
    "accounts_analyzed": 3,
    "accounts_skipped": 0,
    "accounts": [
        {
            "account": {"title": "...", "account_type": "...", "currency": "TON", "balance": 50.0},
            "budget": {"balance": 50.0, "currency": "TON", "last_transactions": [...]},
            "campaigns": {
                "total": 5, "active": 2, "stopped": 2, "declined": 1, "limited": 0,
            },
            "performance": {
                "impressions": 12000, "clicks": 85, "ctr": 0.71,
                "spent": 12.5, "budget_total": 50.0, "budget_used_pct": 25.0,
            },
            "events_count": 2,
            "warnings": [],
        },
    ],
    "total": {
        "accounts_analyzed": 3,
        "campaigns_total": 15, "campaigns_active": 6,
        "campaigns_stopped": 8, "campaigns_declined": 1, "campaigns_limited": 0,
        "impressions": 45000, "clicks": 420,
        "ctr": 0.93,           # weighted: sum(clicks)/sum(impressions)
        "spent": 90.0, "budget_total": 300.0, "budget_used_pct": 30.0,
    },
    "warnings": [],
}
```

## Scope warning

If `account_scope != "all"`, the snapshot sets `warnings[0]`:
"Snapshot is scoped to one account ('{name}'), not all {N} Telegram Ads accounts."

## Error handling

- Login failure → `LoginRequiredError`, caught by tool layer
- Per-account scan failure → account skipped, entry with `warnings: ["Account scan failed: ..."]`
- Events API failure → `events_count = -1` (best-effort)

## Xvfb requirement

Tool checks `$DISPLAY` then `pgrep -x Xvfb`. If neither found → `{"error": "INFRA_MISSING"}`.
No silent fallback, no auto-launch.

## Key rules

- **No global conclusions from partial data.** Per-account metrics only.
- **Date from system clock.** `datetime.now(UTC)` — never hardcoded.
- **Weighted CTR.** `total.ctr = sum(clicks) / sum(impressions)`.
- **Parser integrity.** If snapshot contradicts Telegram Ads UI, see `references/parser_diagnostics.md`.
- **Validation in output.** Snapshot должен содержать `warnings[]` с парсинговыми аномалиями: clicks=0 при значимых impressions, все campaigns stopped при total>0, неизвестные status_text. Эти warning'и показывают, что парсер мог сдвинуться, и snapshot output недостоверен.
