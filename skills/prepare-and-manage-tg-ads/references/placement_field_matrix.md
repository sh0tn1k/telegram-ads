---
name: placement_field_matrix
description: "Per-placement allowed/forbidden field table for Telegram Ads CreateAdDraft. Search placement blocks creative_text_160, media_path, and ad_info. Channels/bots keep current behavior. Companion to `prepare-and-manage-tg-ads/SKILL.md` §Placement × Field Matrix."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [telegram-ads, placement, creative, search, channels, bots]
    related_skills: [prepare-and-manage-tg-ads, create-telegram-ads-campaign-workflow, operate-telegram-ads, telegram-ads-cost-modifiers]
---

# Placement × Field Matrix (Telegram Ads, added 2026-06-18)

Companion reference for `prepare-and-manage-tg-ads/SKILL.md` §"Placement × Field
Matrix". Holds the per-placement allowed/forbidden field tables with worked
examples. The SKILL.md references this file; this file is the source of truth
for field-level rules.

## Why this exists

Per the operator's 2026-06-18 spec: Search placement в Telegram Ads — это
placement-specific формат продвижения в результатах поиска, **не**
sponsored-message copy. Агент должен:

1. **Не предлагать 160-char creative text** для `target_type=search`.
2. **Output `not_applicable_for_search_placement`** при попытке это сделать.
3. **Treat channels/bots** как placements, поддерживающие sponsored copy.
4. **Не угадывать placement** — если неизвестен, спросить the operator.

## Master matrix

| `target_type` | `text` (≤160) | `media_path` | `ad_info` | Allowed fields | Forbidden fields | Error token |
|---|---|---|---|---|---|---|
| `channels` | ✅ yes | ✅ yes | ✅ yes | `title`, `text`, `promote_url`, `ad_info`, `media_path` | — | — |
| `bots` | ✅ yes | ❌ no | ✅ yes | `title`, `text`, `promote_url`, `ad_info` | `media_path` | `unsupported_media_for_target_type` |
| `search` | ❌ **NO** | ❌ no | ❌ no | `promote_url`, `targets` (queries) | **`text`, `media_path`, `ad_info`** | `not_applicable_for_search_placement` |
| `unknown` | n/a | n/a | n/a | n/a | n/a | `placement_unknown` (gate) |

## Per-placement rules

### `channels`

- Sponsored-message format.
- `text` (≤160 chars) generated normally per policy & niche.
- `media_path` supported (photo +50% CPM, video +80% CPM). Channel is the
  only placement for uploaded-media creative tests.
- `ad_info` allowed.

### `bots`

- Sponsored-message format with bot-targeting.
- `text` (≤160 chars) generated normally.
- `media_path` **forbidden** — Telegram does not support uploaded media on
  bot-targeted ads. Must return `unsupported_media_for_target_type` and
  strip `media_path` from the draft.
- `ad_info` allowed.
- If `show_picture = true`, channel/bot logo is shown in the ad (+30% CPM).

### `search`

- Placement-specific format. **NOT** a sponsored message.
- `text` (≤160 chars) **forbidden** — Search placement does not accept
  sponsored-message copy. Returning a 160-char text here is a
  `not_applicable_for_search_placement` structured error.
- `media_path` **forbidden** — Search does not accept uploaded photo/video.
- `ad_info` **forbidden** — Search has no sponsored-message body.
- Allowed fields: `promote_url`, `targets` (search queries, NOT @channels or
  @bots), `cpm`, `budget`, `views_per_user`, `initial_active`,
  `daily_budget`, `activate_at`, `deactivate_at`, `weekly_schedule`,
  `schedule_tz`.
- Copy generation is **skipped entirely**. Output advertises that
  creative copy is `not_applicable_for_search_placement`.

### `unknown`

- Placement was not derivable from the draft. STOP.
- Surface to the operator as structured error `placement_unknown` and request
  exact per-call approval phrase:
  `approve placement <channels | bots | search>`
- Do **not** guess. Do **not** generate copy until placement is approved.

## Recommendation output spec (mandatory)

Every ad recommendation from `prepare-and-manage-tg-ads` must include these
four fields, no exceptions:

```yaml
recommendation:
  placement: "channels" | "bots" | "search" | "unknown"
  allowed_fields: ["title", "text", ...]   # per the matrix above
  forbidden_fields: ["media_path", ...]   # per the matrix above
  creative_text_applicable: bool          # false for search
```

If any of these four is missing → recommendation is **incomplete**, surface
to the operator as incomplete + add to `Missing data` section. Do not ship a
recommendation that doesn't carry this contract.

## Worked examples

### Example 1 — Search query campaign (no copy)

Input: project context + segments imply discovery via search, no creative
copy needed.

```yaml
placement: "search"
allowed_fields: ["promote_url", "targets", "cpm", "budget", "views_per_user"]
forbidden_fields: ["text", "media_path", "ad_info"]
creative_text_applicable: false
copy_status: "not_applicable_for_search_placement"
draft:
  target_type: "search"
  promote_url: "https://t.me/ExampleBot"
  targets: ["clip video", "video clips ai", "ai shorts maker"]
  cpm: 1.5
  budget: 20
```

### Example 2 — Bot-targeted campaign (copy yes, media no)

Input: project has a competitor-bot-style target list, copy needed.

```yaml
placement: "bots"
allowed_fields: ["title", "text", "promote_url", "ad_info"]
forbidden_fields: ["media_path"]
creative_text_applicable: true
copy_status: "ok"
draft:
  target_type: "bots"
  title: "ExampleApp — clip long videos fast"
  text: "Turn any 30-min podcast into 5 viral shorts. /start in ExampleBot."
  promote_url: "https://t.me/ExampleBot"
  cpm: 1.5
  budget: 20
  targets: ["@example_bot_a", "@example_bot_b"]
  # media_path: STRIPPED — would have triggered unsupported_media_for_target_type
```

### Example 3 — Channel campaign (copy yes, media optional)

Input: classic channel-targeted sponsored message, optional photo.

```yaml
placement: "channels"
allowed_fields: ["title", "text", "promote_url", "ad_info", "media_path"]
forbidden_fields: []
creative_text_applicable: true
copy_status: "ok"
draft:
  target_type: "channels"
  title: "ExampleApp — clip long videos fast"
  text: "Turn any 30-min podcast into 5 viral shorts."
  promote_url: "https://t.me/ExampleBot"
  cpm: 2.7   # effective CPM with show_picture + photo modifier
  budget: 20
  media_path: "/srv/artifacts/opus_creative.png"   # supported on channels
  targets: ["@podcast", "@marketingtools"]
```

### Example 4 — Unknown placement, gate fires

Input: draft has no `target_type`, agent cannot derive placement.

```yaml
placement: "unknown"
allowed_fields: []
forbidden_fields: []
creative_text_applicable: false
copy_status: "placement_unknown"
draft:
  # target_type: MISSING
  promote_url: "https://t.me/ExampleBot"
  cpm: 1.5
  budget: 20
```

Agent response:

```md
## Approval required

**Action:** resolve placement
**Reason:** draft.target_type is missing; cannot derive placement.
**Effect:** unblocks copy generation / draft preparation.
**Exact phrase:** `approve placement <channels | bots | search>`

**Proceed?**
```

Until the operator sends the exact phrase, copy is NOT generated. The draft is held
in a pre-placement state.

## Interaction with existing rules

- **Uploaded media placement matrix** in
  `prepare-and-manage-tg-ads/SKILL.md` §"Uploaded media placement matrix" —
  remains authoritative for `media_path`. This document is the text-copy
  analog (and supersedes the implicit assumption that all placements
  support `text`). Combined, the two matrices give the full
  placement × field coverage.
- **CPM media modifiers** (`media_photo`, `media_video` in
  `telegram-ads-cost-modifiers`) — apply only on `channels`. For search/bots,
  `media_supported_by_target_type = false`, regardless of copy applicability.
- **Privacy/compliance-sensitive copy policy** — still applies when copy IS
  generated (channels/bots). For search, copy is not generated, but the
  promote_url still flows through the same compliance checks.

## Change log

- **1.0.0** (2026-06-18) — initial release. Per the operator's correction: Search
  placement blocks `creative_text_160`, `media_path`, and `ad_info`. Channels
  and bots keep current behavior. Unknown placement is a hard gate requiring
  per-call the operator approval.
