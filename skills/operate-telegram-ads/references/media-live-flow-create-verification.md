# Media live-flow create verification pitfall

Session learning from a live ExampleBot Telegram Ads test (2026-06-06): a draft with an uploaded 16:9 JPEG can pass `upload_media` and `validate_ad/checkAdPost` with `preview.has_photo=true`, `creative.media_uploaded=true`, and approval summary `media=photo`, yet the created campaign's detail parser may later report `creative.has_media=false` and `show_picture=true`.

## Canonical safe flow for one approved media-create test

1. Select the intended cabinet with `telegram_ads_choose_account` and verify via list/status reads.
2. Verify the local media before any Ads call:
   - file exists;
   - image dimensions are exactly 16:9 (`width * 9 == height * 16`);
   - media type is photo, not video, unless explicitly approved.
3. Build draft with explicit:
   - `media_path`;
   - `show_picture=false` unless separately approved;
   - `initial_active=false`;
   - minimal CPM/budget if requested.
4. Run `telegram_ads_estimate_cpm` and report it as an estimate only.
5. Run `telegram_ads_upload_media(file_path)` and require a non-empty `media_token`. If upload fails, stop; do not create a media-less ad.
6. Run `telegram_ads_validate_ad` with both `media_path` and `media_token`. Treat `preview.has_photo=true` / `creative.media_uploaded=true` as pre-submit evidence, not final proof.
7. Call `telegram_ads_create_ad` with the same draft including `media_token`; apply only if the user's approval explicitly covers this exact create.
8. Post-create, verify with at least:
   - `telegram_ads_list_ads` to find `ad_id`, title, status, CPM, budget, spent;
   - `telegram_ads_get_ad_budget_status` to confirm `is_active=false` and no spend;
   - `telegram_ads_get_ad` or `telegram_ads_get_ad_creative` to inspect `has_media` and `show_picture`.
9. If post-create detail contradicts validation/approval (e.g. `has_media=false` or `show_picture=true`), report a discrepancy and stop. Do not edit, delete, start, or re-create unless the user gives a new explicit approval.

## Reporting rule

Separate evidence by phase:

- Pre-submit evidence: upload token returned, validate preview had photo, approval summary said media/photo.
- Server-created evidence: new `ad_id`, list/detail status, budget, active flag, parser `has_media` / `show_picture`.

Do not claim `media_uploaded=true` for the final created ad solely from validation preview; final detail/list verification is the source of truth after create.
