# MetricsAdapter regression: Search ads DO have clicks/actions

Captured 2026-07-15. Root cause: misleading comments in `metrics_adapter.py` asserting 
"search ads don't have clicks/actions" — but `telegram_ads_list_ads` returns them.

## Authoritative source
**`telegram_ads_list_ads`** is the authoritative cumulative source for ALL metrics 
(clicks, actions, CTR, CVR, CPC, CPA, spent, impressions) — for ALL targeting types 
including search. Confirmed live: Ad ID 1 returns clicks=936, actions=493, CTR=4.01%, 
CVR=52.67%, CPC=1, CPA=3.

## Files patched (2026-07-15)

### `agent/telegram_ads_operator/metrics_adapter.py`
- Line 293: Changed "clicks are NOT returned by get_ad_stats for search ads" → 
  "clicks/actions sourced from list_ads for ALL types"
- Line 314: Same
- Line 325: Same
- Lines 302-310: Fixed double-counting of spend — `list_ads` lifetime spend now 
  takes precedence over `get_ad_stats` monthly sum

### `tests/agent/telegram_ads_operator/test_metrics_adapter.py`
+7 regression tests (33/33 pass), class `TestSearchAdFullMetrics`:
1. `test_search_ad_returns_clicks_and_actions`
2. `test_capability_is_full_metrics`
3. `test_delta_ctr_cvr_cpc_cpa_from_two_snapshots`
4. `test_missing_field_is_none_not_zero`
5. `test_csv_three_columns_does_not_disable_summary_metrics`
6. `test_channel_bot_search_same_capability_pipeline`
7. `test_mutation_count_is_zero`

## Capability
`WatchCapabilityMode.for_dialog_guard()` → `full_metrics`, `efficiency_decisions=True` 
(confirmed in `models.py:589`).
