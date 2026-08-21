# English-Market Playbook

## Intent Taxonomy (Telegram Search Ads)

English-language Telegram Search Ads use a funnel-ordered intent taxonomy:

```
direct_problem → creator_profession → workflow_tools → agency → distribution → broad_adjacent
```

### Examples

| Intent Level | Query Examples |
|-------------|----------------|
| `direct_problem` | "video clips", "shorts maker", "podcast clips" |
| `creator_profession` | "video editor", "content creator", "social media manager" |
| `workflow_tools` | "ai video editor", "auto caption", "transcription tool" |
| `agency` | "video production", "content agency", "social media agency" |
| `distribution` | "youtube growth", "tiktok strategy", "instagram reels" |
| `broad_adjacent` | "digital marketing", "online business", "creator economy" |

## Supported GEOs

- US (en-US) — largest market, highest CPM
- UK (en-GB) — second largest English market
- CA (en-CA) — English + French split; target en-CA separately
- AU (en-AU) — smaller but high engagement

**Language ≠ GEO.** An English channel can have global audience. Verify
actual audience GEO before targeting search queries by location intent.

## Cultural Adaptation Rules

1. **US English** — direct, benefit-focused, no British spellings
2. **UK English** — British spelling (colour, optimisation), understated tone
3. **Banned in copy:** emoji overuse, ALL CAPS, excessive punctuation
4. **Avoid:** Russian cultural references, USSR-era terms, untranslated idioms
5. **Test:** native English speaker review for every ad variant

## Content Strategy for English Channels

1. **Time-zone-aware publishing:** target US Eastern (UTC-5) primetime
2. **English content model:** original + curated RSS/Twitter rewrites
3. **Channel health:** post frequency ≥3/day, engagement rate >5%
4. **Subscriber retention:** track unfollow rate per 1000 subscribers

## Implementation

Module: `agent/telegram_ads_operator/constants.py`
Constants: `SUPPORTED_ENGLISH_GEOS`, `IntentClass`

Module: `agent/telegram_ads_operator/search_queries.py`
Function: `classify_intent(query: str) -> IntentClass`
