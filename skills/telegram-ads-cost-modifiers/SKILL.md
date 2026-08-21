---
name: telegram-ads-cost-modifiers
description: "Operational procedure for estimating the effective CPM of a Telegram Ads draft by stacking the UI-declared creative-option surcharges (show_picture +30%, custom emoji +50%, photo +50%, video +80%). Use when the operator asks 'how much will this ad really cost per 1000 views?', 'estimate CPM with modifiers', 'effective CPM', or before preparing any draft with show_picture / custom emoji / uploaded media."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [telegram-ads, operations, cpm, pricing, estimation]
    related_skills: [create-telegram-ads-campaign-workflow, operate-telegram-ads-cabinet]
---

# Telegram Ads Cost Modifiers

Operational procedure for estimating the **effective** CPM a draft will cost, by stacking Telegram's UI-declared creative-option surcharges onto the base `cpm` field. This is an **estimate** — the live `validate_ad` / `checkAdPost` result is the source of truth.

The Telegram Ads UI raises the effective CPM when these creative options are enabled. These four rules are the **canonical reference** for this skill — treat them as fixed, do not extrapolate or "round" them in reports:

| Creative option | Surcharge | Source |
|---|---|---|
| `show_picture` (bot/channel picture) | **+30%** | Telegram Ads UI label |
| `custom_emoji` (premium emoji in text) | **+50%** | Telegram Ads UI label |
| `media_photo` (uploaded photo) | **+50%** | Telegram Ads UI label |
| `media_video` (uploaded video) | **+80%** | Telegram Ads UI label |

> **Authoritative source for the live value is `validate_ad` / `cpm_extra`**, not this table. The four numbers above are the **declared UI rules** and are stable across cabinets. The live `cpm_extra` from `checkAdPost` may differ in edge cases (placement-specific surcharge, account state) — when it does, the live value wins. Do not assume `media_photo` returns `+80%` in any general sense; that has been observed in one specific live test (channel photo, 2026-06) but is not the rule.

Surcharges **stack multiplicatively**, not additively: `effective_cpm = base_cpm × Π(1 + pct/100)`. This stacking is an **estimate** — it is a reasonable approximation, not a contractual guarantee from Telegram.

## When to Use

- the operator asks "how much will this ad really cost per 1000 views?".
- Before preparing a draft that uses `show_picture`, custom emoji, or uploaded media.
- Comparing two drafts: "which is cheaper per 1000 impressions?".
- Pre-approval sanity check: "I said CPM=💎1.50 — what will the live CPM actually be?".
- Whenever a draft's `media_path` is set and `target_type` is `channels`.

## Review-test campaigns (default settings)

For **review-test campaigns** — ads created purely to exercise the moderation / submission flow, not to deliver real reach — use the minimal-modifier profile:

| Field | Value | Why |
|---|---|---|
| `show_picture` | **`false`** | Avoids the +30% surcharge and any policy interpretation risk around bot avatars. |
| `custom_emoji` in text | **none** | Avoids the +50% surcharge and any premium-emoji policy risk. |
| `media_path` | **unset** | No uploaded photo/video — avoids the +50% / +80% surcharge and keeps targeting flexible. |
| text | **plain text only** | No `tg://emoji?id=…`, no `<tg-emoji>` markers, no premium glyphs. |
| stacking | **none** | Effective CPM = base CPM (single modifier, `confidence: "high"`, `needs_validation: false`). |

The expected effective CPM for a review-test draft is exactly the base `cpm` value. This is the recommended default for any campaign whose purpose is to validate the create / edit / decline / approve loop end-to-end.

## When NOT to Use

- Choosing the base CPM for a new draft (strategy / market fit) → use `prepare-and-manage-tg-ads`.
- Reading the **actual** spend of a running campaign → use `telegram_ads_get_ad_stats(ad_id=…)` or the `inspect_ad` workflow.
- Estimating **total cost** (effective CPM × impressions) — this skill does not predict impressions, only the per-1000 rate.
- Computing CTR / CPC / CPA → those are post-run metrics, not creative-option modifiers.

## Operating Discipline (mandatory)

- Use the typed tool `telegram_ads_estimate_cpm` whenever possible. It is the canonical implementation of this skill's logic.
- If the tool is unavailable, fall back to the manual computation in "Manual Procedure" below. **Never** open ads.telegram.org in a browser to read `cpm_extra` — that violates Operating Discipline.
- Do not write back the estimated effective CPM into `draft.cpm`. The tool returns advisory numbers; the draft's `cpm` field is what `validate_ad` / `create_ad` will use.

## Standard Procedure

### Step 1 — Build the draft (no submit)

The draft must include all creative options that affect CPM. Pull the values from whatever draft is being prepared. If no draft exists yet, this skill still runs on a notional dict for estimation.

Required fields for cost-modifier estimation:

```
{
  "cpm": <base_cpm>,             # e.g. 1.50
  "show_picture": <bool>,        # default true
  "text": "<ad text>",           # for custom-emoji detection
  "media_path": "<local path>"   # only for target_type=channels
}
```

### Step 2 — Call the typed estimator

```
telegram_ads_estimate_cpm(draft={...})
```

Parse the response:

| Field | Meaning |
|---|---|
| `base_cpm` | The `cpm` field of the draft, echoed back |
| `estimated_effective_cpm` | `base_cpm × Π(1 + pct/100)` |
| `modifiers_applied` | List of human-readable modifier names that contributed |
| `modifier_percents` | Map of modifier → percent |
| `modifier_confidence` | `"none"` / `"high"` (single modifier) / `"estimate"` (stacked) |
| `needs_validation` | `true` when >1 modifier stacks OR uploaded media is present |

**Critical rules:**
- When `needs_validation: true`, the report **must** say so and point to the live `checkAdPost` / `cpm_extra` as authoritative.
- For uploaded media, the static estimate may be wrong (live test 2026-06 showed `+80%` for channel photo, not the assumed `+50%`). Always defer to the live UI value if `validate_ad` exposes it.
- For `target_type` other than `channels`, uploaded media is not supported — `media_path` should not be set. If it is, the estimator will return `media_supported_by_target_type: false, media_ignored_by_placement: true` and a recovery hint to use `target_type: "channels"`.

### Step 3 — Custom-emoji detection (fallback)

`estimate_cpm` detects custom-emoji via `tg://emoji?id=…` and `<tg-emoji>` markers in `text`. Plain unicode premium-emoji glyphs (rendered as coloured emoji) are **not** detected, because they cannot be told apart from ordinary emoji reliably from text alone. If the draft text uses raw premium-emoji glyphs and the operator wants the +50% modifier applied, the operator must confirm explicitly — do not auto-detect.

### Step 4 — Report

Output format:

```md
## CPM estimate

**Base CPM:** 💎1.50
**Modifiers:**
- `show_picture`: +30%
- `custom_emoji`: +50% (detected `tg://emoji?id=…`)

**Estimated effective CPM:** 💎2.70
**Confidence:** `estimate` (stacked)
**Needs live validation:** yes — the live `cpm_extra` from `checkAdPost` is authoritative.
```

If `needs_validation: false`:

```md
**Confidence:** `high` (single modifier)
**Needs live validation:** no.
```

### Step 5 — Optional pre-submit cross-check

If the operator is about to submit, suggest running `telegram_ads_validate_ad(draft=…)` after the estimate and reading the live `cpm_extra` field. `validate_ad` is SAFE_READ and does not require approval.

## Manual Procedure (only if `telegram_ads_estimate_cpm` is unavailable)

Use this only as a fallback. Always prefer the tool.

```
from hermes_telegram_ads.cpm_modifiers import (
    CPM_MODIFIERS,
    detect_custom_emoji,
)

# Identify active modifiers
modifiers: list[str] = []
percents: dict[str, int] = {}

if draft.get("show_picture", True):
    modifiers.append("show_picture")
    percents["show_picture"] = CPM_MODIFIERS["show_picture"]  # 30

if detect_custom_emoji(draft.get("text", "")):
    modifiers.append("custom_emoji")
    percents["custom_emoji"] = CPM_MODIFIERS["custom_emoji"]  # 50

# Uploaded media modifiers only for target_type == "channels"
if draft.get("target_type") == "channels":
    if draft.get("media_path"):
        # Heuristic: photo vs video requires inspecting the file (extension or MIME)
        # Without that, default to photo modifier.
        modifiers.append("media_photo")
        percents["media_photo"] = CPM_MODIFIERS["media_photo"]  # 50

# Stack multiplicatively
factor = 1.0
for p in percents.values():
    factor *= 1 + p / 100

effective = draft["cpm"] * factor
needs_validation = len(modifiers) > 1 or "media_photo" in modifiers or "media_video" in modifiers
```

## Common Pitfalls

1. **Adding modifiers instead of multiplying them.** The surcharges stack multiplicatively, not additively. Two +30% modifiers do **not** equal +60%; they equal `1.30 × 1.30 = 1.69` (i.e. +69%).
2. **Assuming `media_photo` returns `+80%` in general.** A live 2026-06 channel-photo test returned `+80%` from the UI; that is an **observation**, not the rule. The canonical UI-declared surcharge is `+50%`. Always defer to the live `cpm_extra` when available; do not write reports that imply `+80%` is the default.
3. **Estimating with `media_path` for `target_type` in `search` or `bots`.** Uploaded media is only supported for `target_type=channels`. For other targets, `media_path` is not honoured, and the modifier should not be applied.
4. **Auto-detecting raw premium-emoji glyphs.** The detector only catches explicit `tg://emoji?id=…` and `<tg-emoji>` markers. Coloured Unicode emoji are indistinguishable from ordinary emoji from text alone. If the operator believes a draft uses premium emoji but the detector finds no marker, ask for confirmation.
5. **Writing `estimated_effective_cpm` back into `draft.cpm`.** The estimator is advisory. The `cpm` field in the draft is what `create_ad` will use. Do not mutate it.
6. **Forgetting `show_picture` default.** Many drafts omit `show_picture` from the JSON, but the field defaults to `true`. Treat the default as on when estimating.
7. **Confusing CPM with total cost.** This skill gives a per-1000-impressions rate. Impressions are not predicted here. Total cost = effective CPM × impressions / 1000.

## Verification Checklist

- [ ] `telegram_ads_estimate_cpm` was preferred; manual procedure used only as fallback.
- [ ] `modifiers_applied` list is reported to the operator in plain language.
- [ ] `needs_validation: true` is surfaced and the live `cpm_extra` is referenced as authoritative.
- [ ] Uploaded-media modifier flagged the UI-authoritative caveat (no `+50%` blind trust).
- [ ] Custom-emoji detection limited to explicit markers (no raw-glyph guessing).
- [ ] `target_type` is `channels` for any `media_path` to be honoured.
- [ ] No `draft.cpm` mutation; estimator is advisory only.
