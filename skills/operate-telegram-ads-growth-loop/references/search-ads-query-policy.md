# Search Ads Query Policy

## Live Contract (2026-07-07)

- MAX_SEARCH_QUERIES_PER_AD = 10
- MAX_SEARCH_QUERY_LENGTH = 32 (Unicode characters)
- Minimum word length: 4 characters (Telegram Ads rejects shorter words)
- Search phrases must be COMPLETE — do not split into individual words
  - "диалог гард" → ["диалог гард"], NOT ["диалог", "гард"]
- show_picture MUST be False for search ads

## Validation Rules

### Hard Reject

- Empty string after trimming
- > 32 characters (user-visible, semantic)
- URL (http://, https://)
- t.me/ links
- site: operator
- Control characters
- Transport encoding: +, Base64, %2B, %3B
- Only punctuation/emoji
- Language mismatch with campaign
- Duplicate in cluster

### Needs Review

- Browser-style question (starts with how, what, why, where, when, can i, should i)
- Contains ?
- More than 4 words
- Potential brand name without clearance
- Mixed languages
- Ambiguous intent
- Generic term with weak Telegram search results

### Accept

- Short Telegram-style queries (1-3 meaningful words)
- Product-matching
- Topic/category/task intent
- Evidence-backed search relevance

## Normalization

1. Trim leading/trailing whitespace
2. Collapse repeated internal whitespace to single space
3. Apply Unicode NFC normalization
4. Preserve original and normalized forms
5. Case-insensitive duplicate detection

## Cluster Rules

1. 1 ad = 1 query hypothesis
2. Up to 10 queries allowed only for tight cluster:
   - Same intent
   - Same language
   - Same audience segment
   - One creative angle
   - Clustered synonyms with overlapping Telegram search results
3. Never mix:
   - Generic + competitor intent
   - Different product tasks
   - Different languages
   - Different GEO
   - Different funnel stages
   - Different creative angles
4. When Telegram provides aggregate stats, do NOT attribute to a single query.

## Examples

| Query | Verdict | Reason |
|-------|---------|--------|
| "how to turn videos into viral clips" | REJECT | Browser question, >4 words |
| "what is the best ai video editor" | NEEDS_REVIEW | Browser question |
| "ai video clips" | ACCEPT | Short, product-matching |
| "video clipper" | ACCEPT | Task intent, 2 words |
| "shorts maker" | ACCEPT | Creator tool, 2 words |
| "podcast clips" | ACCEPT | Topic, 2 words |

## Implementation

Module: `agent/telegram_ads_operator/search_queries.py`
Function: `validate_query(raw_query, language="") -> QueryCandidate`
