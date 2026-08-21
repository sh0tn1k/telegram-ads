# Canonical Telegram Ads Operator: missed low-budget alert recovery

Use when a campaign was registered through `telegram_ads_register_campaign_watch` with a Star threshold, but no low-budget notification arrived.

## Diagnose in this order (read-only)

1. Read the current value with `telegram_ads_get_ad_budget_status(ad_id)`. Treat this point-in-time `budget` field as authoritative; do not infer it from an old dashboard/list snapshot or from a failed metrics payload.
2. Inspect the canonical operator DB (`~/.hermes/data/telegram_ads_operator.db`) for `event_delivery` state and the matching decision-state snapshots. A low `remaining_budget=0` paired with `data_freshness=stale` or missing observation time is a failed read, **not** a real low-balance crossing.
3. Inspect the gateway logs for browser-profile lock/contention and check that the material-event queue advances beyond the oldest registration event.
4. Inspect both reporting gates:
   - `TELEGRAM_ADS_OPERATOR_REPORTS_ENABLED=1` enables the canonical operator’s report sink.
   - `HERMES_ADS_WATCHER_REPORTS_ENABLED=1` enables the bounded `MiniReportRouter` safe-send path.
   Both are required when the operator is expected to notify Telegram rather than only persist an internal task/event.

## Invariants / regression tests

Keep these behaviours covered:

- `EventStore.mark_consumed()` must permit a successfully handled terminal lifecycle event to move from `claimed` to `consumed`; otherwise an old `watch_registered` row is endlessly reclaimed and starves fresh threshold events.
- `data_freshness != "fresh"` must not establish a baseline, alter a budget episode, or create a budget alert. It may be terminally consumed with a `data_stale` decision.
- Compare the point-in-time balance only against `WatchPolicyConfig.budget_threshold_stars`; never reuse a CPA/CPM threshold.
- A first **fresh** sample at or below the threshold must emit one initial-below alert rather than silently bootstrap.
- If legacy/stale state says `remaining_below` but has no `delivered_at`, a subsequent fresh low sample must repair that phantom state and emit one alert. A delivered below-threshold episode remains deduplicated until it re-arms above the threshold.
- The report adapter must send only a material `budget_threshold` event through `MiniReportRouter`, with a stable dedupe reference, secret-scrubbed body, and `approval_required=True`; it must never mutate the ad.

## Deployment boundary

Code/tests can be prepared locally. Do **not** restart the gateway, change systemd environment, clear live state, or cause an outbound Telegram alert without the next explicit approval stage. Before restart, run the normal clean-worktree/secret/preflight checks. After restart, verify the service is active, both flags are present in the live process environment, the consumer has a durable report sink, and a fresh poll sees the actual balance. Report any message sent and its evidence honestly.
