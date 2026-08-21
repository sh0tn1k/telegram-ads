# Sources and Authority Order

When preparing an ad campaign, use sources in this exact order:

## 1. Live Telegram Ads UI and confirmed cabinet behaviour

The tool contract exposed through `telegram_ads_*` typed tools is authoritative.
The live UI behaviour confirmed via these tools takes precedence over all other sources.

**Confirmed platform contract (2026-07-07):**
- MAX_SEARCH_QUERIES_PER_AD = 10
- MAX_SEARCH_QUERY_LENGTH = 32 (characters)
- MAX_TARGETS_PER_AD = 10
- MAX_AD_TEXT_LENGTH = 160
- MAX_AD_TITLE_LENGTH = 64
- Targeting types: search, channels, bots
- Search mode: show_picture MUST be False (default True → +30% CPM)
- CPM modifiers: show_picture +30%, custom emoji +50%, photo +50%, video +80%

## 2. Official Telegram Ads documentation

- Telegram Ads Getting Started: https://ads.telegram.org/
- Ad Policies and Guidelines
- Bot must respond on mobile and desktop
- Language of targeted channel, ad, and destination must match
- Primary URL and text link must lead to same destination
- Bot link may contain start parameter

## 3. Current tool contract and source code

The `tools/telegram_ads_typed_tool.py` module defines the canonical tool interface.
All typed tools are confirmed against the live UI.

## 4. Tests and runtime evidence

- `tests/gateway/test_ads_watcher_v2*.py`
- `tests/agent/telegram_ads_operator/`
- Runtime logs from `~/.hermes/logs/gateway.log`

## 5. Canonical project memory

`~/.hermes/projects/<project_id>/` — project-specific facts, baselines, thresholds.

## 6. Verified Telegram Research data

Research data from `telegram_research_*` tools, scored and verified.

## 7. Empirical industry practices

Ad platform best practices. Lowest authority level.

## Conflict resolution

1. Code/tests/live UI beats old documentation
2. Approval policy beats any automation instruction
3. Project-specific policy beats general marketing thresholds
4. Generated wiki is NOT canonical memory
5. External article is NOT evidence of platform behaviour
6. Google Ads match types do NOT apply to Telegram Search Ads

## Labelling

Every artefact should label facts with:
- `[platform]` — official Telegram fact
- `[live_contract]` — confirmed by UI/tool
- `[hermes_impl]` — current Hermes implementation
- `[best_practice]` — empirical recommendation
- `[hermes_policy]` — proposed operator policy
- `[hypothesis]` — unverified assumption
