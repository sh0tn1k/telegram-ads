# Metrics and Attribution

## Available Metrics (from Telegram Ads)

| Metric | Source | Freshness | Granularity |
|--------|--------|-----------|-------------|
| impressions | ad stats / dashboard | ~1 hour | per ad |
| clicks | ad stats / CSV report | ~1 day | per ad, per month |
| CTR | computed: clicks / impressions | ~1 day | per ad |
| CPM | ad detail / budget status | near-real-time | per ad |
| spend | ad budget status | near-real-time | per ad |
| remaining budget | ad budget status | near-real-time | per ad |
| delivery status | ad detail | near-real-time | per ad |
| moderation status | ad detail | near-real-time | per ad |
| budget utilization | computed: spend / total budget | near-real-time | per ad |

## NOT Available (require external sources)

| Metric | Why unavailable | Alternative |
|--------|----------------|-------------|
| channel joins | Telegram Ads does not provide per-ad join tracking | In-channel analytics bot |
| bot starts | No per-ad conversion pixel for bot starts | Bot analytics with start param |
| activated users | Not tracked by ad platform | Product analytics |
| payment events | No conversion pixel integration | Payment system webhook |
| unsubscribe/churn | Not tracked by ad platform | Channel analytics tool |
| views per subscriber | Not per-ad attributable | Channel analytics |
| reactions | Not per-ad attributable | Channel analytics |
| forwards | Not per-ad attributable | Channel analytics |
| conversion by creative | No creative A/B reporting in Telegram Ads | Manual experiment isolation |
| conversion by targeting cluster | No per-cluster stats | Separate ad per cluster |

## Attribution Contract

`build_attribution_snapshot()`:
- `upper_funnel_only()` — True when no conversion tracking exists
- `can_compute_roas()` — False when no revenue/payment data
- `is_attribution_wiring_missing()` — True for all current campaigns

## Implementation

Module: `agent/telegram_ads_operator/attribution.py`
Functions: `build_attribution_snapshot`, `can_compute_roas`, `upper_funnel_only`
