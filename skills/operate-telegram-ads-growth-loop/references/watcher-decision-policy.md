# Watcher Decision Policy v2.0 — Multi-Window

## Core Principle

**«Threshold breach — это сигнал для анализа, а не команда остановить рекламу.»**

The decision engine CLASSIFIES but NEVER stops an ad automatically.
Stop/scale/CPM-change decisions ALWAYS produce an internal task with
`external_actions_allowed=False` requiring explicit operator approval.

## How the Watcher Integrates

The watcher periodically polls Telegram Ads for ad metrics. The operator's
`GrowthOperatorPoller` creates durable `event_delivery` events at evidence
checkpoints. The `GrowthOperatorConsumer` claims events, runs the
deterministic `decision_engine`, creates internal AGI Team tasks, and
marks events consumed.

## Multi-Window Architecture

The v2 engine maintains **stateful tracking** across consecutive
observation windows via `MultiWindowState`. This distinguishes a one-off
weak hour from a sustained trend.

### State Model

```
MultiWindowState
  ├── current_window: int          # Monotonic counter
  ├── consecutive_windows: list    # Sliding window of WindowMetrics
  │   └── WindowMetrics            # Per-window snapshot
  │       ├── window_index
  │       ├── impressions, clicks, actions, spend
  │       ├── ctr, cpa, cvr
  │       ├── breached_metrics: list[str]
  │       └── severe_metrics: list[str]
  ├── breach_counters: dict[str, int]  # metric → consecutive breach count
  └── classification: str
```

## Classification Tiers

| Tier | Trigger | Action | Notification |
|------|---------|--------|-------------|
| `insufficient_data` | minimum_evidence NOT met | Continue silently | No |
| `early_warning` | Single metric breached, < warning_consecutive_windows | Continue observe | No |
| `sustained_degradation` | Metric breached ≥ warning_consecutive_windows | Analytical warning | Yes |
| `recommend_pause` | 2+ metrics breached ≥ pause_consecutive_windows | Approval request | Yes |
| `recommend_pause` | Single SEVERE metric ≥ pause_consecutive_windows | Approval request | Yes |

### Recovery

When a previously breached metric returns to normal range, its
**consecutive breach counter resets to zero** and the metric is
removed from tracking. This prevents stale breaches from accumulating.

## Evidence Windows

| Impressions | What changes |
|-------------|--------------|
| 250 | Health/anomaly check only — NO classification, NO stop |
| 500 | Preliminary CTR check |
| 1000 | First classification |
| 1500 | Directional classification |
| 3000 | Scale/no-scale decision |

## Decision Classifications (All)

- `insufficient_data` — continue observing, no action
- `healthy` — all metrics within acceptable range
- `early_warning` — single-window breach, observe
- `sustained_degradation` — persistent breach, analytical warning
- `recommend_pause` — persistent + multi-metric or severe, approval required
- `promising` — CTR/CPA within acceptable range
- `weak` — below threshold, inspect
- `winner_candidate` — significantly above baseline, scale proposal
- `loser_candidate` — exceeded max_approved_loss or 0 performance
- `anomaly` — delivery anomaly, investigate
- `budget_risk` — budget utilization warning
- `attribution_missing` — no downstream data

## Budget Guardrails

- 80% utilized → `budget_80_percent` event → notify
- 95% utilized → `budget_95_percent` event → urgent notify
- Spend > `max_approved_loss` → `propose_stop` (overrides all other classifications)

## Configurable Watch Policy

Every campaign watch carries a `WatchPolicyConfig`:

```yaml
minimum_evidence:
  min_impressions: 0
  min_clicks: 0
  min_conversions: 0
  min_observation_windows: 1

persistence:
  warning_consecutive_windows: 3
  pause_consecutive_windows: 2

decision_rule:
  minimum_breached_metrics_for_pause: 2
  allow_severe_single_metric_rule: true

warning_thresholds:
  cpa: 5.0
  ctr: 0.02
  cvr: 0.30

severe_thresholds:
  cpa: 15.0
  ctr: 0.005
  cvr: 0.10

baseline_comparison:
  enabled: false
  maximum_relative_degradation: 0.5
```

**Project-specific values (e.g. Example Guard) are NOT global defaults.**

## Implementation

Module: `agent/telegram_ads_operator/decision_engine.py`
Function: `evaluate_campaign_multi_window(input: DecisionInput) -> DecisionOutput`
Legacy: `evaluate_campaign` — backward-compatible wrapper (stateless, v1 behaviour)
