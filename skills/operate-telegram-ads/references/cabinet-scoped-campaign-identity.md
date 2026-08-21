# Cabinet-scoped campaign identity

## Rule

Treat a numeric `ad_id` as **cabinet-scoped** for all operational reads. The same ID queried while another cabinet is selected can yield a credible but unrelated campaign state.

## Required read-only sequence for a named project

1. List/reconcile cabinets if the current cabinet has not already been confirmed.
2. Select the named project's cabinet with `telegram_ads_choose_account`; this is permitted read-only navigation when the project/cabinet is unambiguous.
3. Re-confirm with `telegram_ads_current_account`.
4. Only then call `telegram_ads_list_ads`, `telegram_ads_get_ad`, or `telegram_ads_get_ad_budget_status`.

## Monitoring versus campaign state

- A campaign can be **Active** even if an operator poll emits `watcher_failure`.
- If monitoring telemetry records an `account_id` different from the live selected cabinet, label it a **watcher-to-cabinet mapping anomaly**.
- Do not infer a campaign's live status from watcher failures. Obtain it from the campaign read in the correctly selected cabinet.

## Diagnostic report wording

Report both facts separately: (1) the live campaign status in its confirmed cabinet; (2) the monitoring health and any account-mapping discrepancy. Do not collapse them into “the campaign is paused” or “monitoring is absent.”
