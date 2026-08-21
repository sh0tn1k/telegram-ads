# Media source and detail-parser pitfalls

## Trigger

Use this when a Telegram Ads create/validation task includes uploaded media and
post-create verification disagrees with validation, or when the user provides
both an explicit `media_path` and an attached image.

## Media source ambiguity

If the request includes two media sources, e.g.:

- `media_path: /path/a.jpg`
- message attachment path `/path/b.jpg`

…and the paths or hashes differ, stop before `upload_media` / `create_ad` and
ask which image is authoritative. Do not silently prioritize the typed field.
Using the wrong creative is a live external side effect even if
`initial_active=false` and no `start_ad` is called.

Recommended preflight:

1. Check both paths exist.
2. Compare dimensions + sha256.
3. If different, ask the operator to choose.
4. Only then upload/create.

## Detail parser false negatives

Observed in a channel-targeted uploaded-photo create test:

- `validate_ad/checkAdPost` reported `valid=true`, `preview.has_photo=true`,
  `media_on=true`, and `creative.media_uploaded=true`.
- `create_ad` approval summary said `media=photo`.
- Post-create `get_ad_creative` reported `has_media=false` and
  `show_picture=true`.
- A typed `telegram_ads_save_screenshot(..., screenshot_name="...png")` of the
  ad detail page showed the real UI had an "Ad photo or video" block, a
  `Change Media` button, and the uploaded photo in preview.

Root cause in the old package version:

- `hermes_tools._ad_detail_views()` computed `has_media` from
  `bool(detail.website_name)` instead of real media DOM fields.
- `show_picture` was hardcoded `True`.
- `pages/ad_detail.py` read `input[name="media"]` but `AdDetail` did not carry
  media/show_picture fields through to the tool response.

Correct investigation protocol:

1. Do not edit/recreate/start the ad.
2. Read `get_ad`, `get_ad_creative`, `get_ad_targeting`, `get_ad_budget_status`.
3. Capture screenshot with a `.png` suffix; screenshot tool rejects names without
   an image extension.
4. If screenshot shows uploaded media, classify as parser/reporting bug.
5. Patch package parser/tests and restart gateway only after separate approval.

## CPM extra authority

For uploaded media, `checkAdPost` / Telegram UI `cpm_extra` is authoritative.
Local CPM modifier tables are estimates. If local estimate says +50% but UI
returns `+80%`, report UI as the source of truth and mark the local value as an
estimate until the package is updated.
