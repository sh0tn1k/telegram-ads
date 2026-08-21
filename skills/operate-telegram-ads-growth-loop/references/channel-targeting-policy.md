# Channel Targeting Policy

## Hard Gates (REJECT)

- Destination mismatch: target channel language ≠ campaign/ad language
- Bot destination as channel target (use `bot-targeting-policy.md`)
- Mixed GEO within one target cluster
- Channel with <10 posts in last 30 days
- Channel that is not publicly accessible

## Scoring (score_channel)

Score 0–100 based on:
- Subscriber count (log scale)
- Post frequency (posts/day)
- Engagement (views per subscriber ratio)
- Content fit with campaign objective
- Language match confirm
- GEO match confirm
- No prior ad saturation signals

## Cluster Rules

- MAXIMUM isolation: 1 target = 1 ad (preferred)
- TIGHT_CLUSTER: up to 10 channels with same audience, language, GEO, intent
- Never mix developer + marketing audiences
- Never mix different languages
- Never mix different GEO

## Implementation

Module: `agent/telegram_ads_operator/target_selection.py`
Function: `score_channel(channel: ChannelTarget) -> float`
