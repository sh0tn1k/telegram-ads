# Bot Targeting Policy

## Hard Gates (REJECT)

- Destination mismatch: target bot language ≠ campaign/ad language
- Channel destination as bot target (use `channel-targeting-policy.md`)
- Mixed GEO within one target cluster
- Bot not responding on mobile AND desktop
- Bot with broken /start flow

## Scoring (score_bot)

Score 0–100 based on:
- Bot open rate (from research data)
- Bot response time
- Bot retention rate
- Content fit with campaign objective
- Language match confirm
- GEO match confirm

## Bot Link Rules

- Bot link may contain start parameter (e.g., `t.me/mybot?start=ad_campaign_1`)
- Primary URL and text link must lead to same bot
- Destination bot must accept inline start parameter

## Implementation

Module: `agent/telegram_ads_operator/target_selection.py`
Function: `score_bot(bot: BotTarget) -> float`
