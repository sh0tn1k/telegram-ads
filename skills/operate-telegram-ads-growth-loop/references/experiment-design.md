# Experiment Design Policy

## Core Principle: Maximum Isolation

One ad = one hypothesis. Never A/B test within a single ad — Telegram Ads
aggregates stats per ad, not per target/query within an ad.

## Experiment Structure

1. **Baseline ad**: default creative, default CPM, open targeting
2. **Variant A**: different creative, same targeting, same CPM
3. **Variant B**: different targeting cluster, same creative, same CPM
4. **Variant C**: different CPM bid, same creative, same targeting

## Constraints

- Each variant = separate ad with separate budget
- Non-overlapping targets between variants
- Identical budget per variant (or agreed proportional split)
- Sequential evidence windows: 250 → 1000 → 3000 impressions
- Minimum 1000 impressions before first classification
- Stop loss: max_approved_loss per ad (not per experiment)

## Evaluation

At each evidence window:
1. CTR comparison (relative, not absolute)
2. CPA comparison
3. Attribution quality assessment
4. Classification: promising | underperforming | leading_candidate | stop

## Implementation

Module: `agent/telegram_ads_operator/decision_engine.py`
Models: `ExperimentIsolation`, `CampaignBrief`
