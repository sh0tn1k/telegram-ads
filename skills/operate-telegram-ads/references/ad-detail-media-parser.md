# Ad detail media parser mismatch

## Trigger

Use this reference when a Telegram Ads campaign was created/validated with `media_path` / uploaded photo, but post-create typed tools report contradictory creative fields such as:

- `has_media=false` despite UI preview/media block showing a photo/video;
- `show_picture=true` despite the draft/validation having `show_picture=false`;
- validation preview says `media_uploaded=true` but `telegram_ads_get_ad_creative` disagrees.

## Live evidence from ExampleBot channel-media test

Live test on 2026-06-06:

- Cabinet: `Example Bot | Short Clips` (Stars)
- Ad title: `HERMES_MEDIA_CHANNEL_TEST_photo`
- Ad ID: `21`
- Targeting: `target_type="channels"`, target chip shown as `Pavel Durov` for `@durov`
- Draft: `show_picture=false`, uploaded JPEG 1280x720, 16:9, `initial_active=false`
- Validation preview: `valid=true`, `has_photo=true`, `media_on=true`, `picture=false`, `creative.media_uploaded=true`
- Create payload path uploaded media before submit and included media token.

Post-create typed tools initially reported:

```json
{"has_media": false, "show_picture": true}
```

But screenshot from the typed screenshot tool showed the actual UI detail page had:

- the uploaded image visible in the `Ad photo or video` block;
- preview card displaying the uploaded image above the ad text;
- `Change Media` button under the uploaded asset;
- status `On Hold` / later `Declined` with `Prohibited content` rejection.

Conclusion: **media was present in the created ad; the typed creative parser was wrong**.

## Root cause

The wrong fields come from `hermes_telegram_ads/hermes_tools.py::_ad_detail_views`:

```python
creative = CampaignCreative(
    ...
    has_media=bool(getattr(detail, "website_name", "")) or False,
    show_picture=True,
    ...
)
```

Problems:

1. `has_media` is derived from `website_name`, not from media DOM state.
2. `show_picture` is hard-coded to `True`.
3. `pages/ad_detail.py` reads `media_token` in JS, but `types.AdDetail` does not expose `media_token`, `has_media`, `media_type`, or `show_picture`, so the parsed media state is discarded before tool output.

## Correct investigation protocol

1. Do not edit/recreate/start the ad while investigating.
2. Use typed read-only tools only:
   - `telegram_ads_get_ad(ad_id)`
   - `telegram_ads_get_ad_creative(ad_id)`
   - `telegram_ads_get_ad_targeting(ad_id)`
   - `telegram_ads_get_ad_budget_status(ad_id)`
   - `telegram_ads_save_screenshot(screenshot_name="...png", full_page=true)`
3. If screenshot shows uploaded media while `get_ad_creative` says `has_media=false`, classify as parser/tool reporting bug, not failed media creation.
4. If screenshot does not show the media block/preview photo, treat final UI as source of truth and stop before any edit/recreate/start.

## Patch shape

Update the package, not the live ad:

1. `types.py` — extend `AdDetail` with durable creative fields:
   - `media_token: str = ""`
   - `has_media: bool = False`
   - `media_type: str = ""` (`photo`, `video`, or empty)
   - `show_picture: bool | None = None`
2. `pages/ad_detail.py` — parse media state from the detail DOM:
   - `input[name="media"]` value;
   - uploaded-media block (`Ad photo or video`, `Change Media`, media preview image/video selectors);
   - preview/media selectors such as `.pr-ad-media`, `.pr-ad-media-photo`, `<video>` where present;
   - checked state for the bot/channel picture option if the DOM exposes it.
3. `hermes_tools.py::_ad_detail_views` — map `CampaignCreative.has_media` and `show_picture` from `detail`, never from `website_name` or constants.
4. Add regression tests with DOM fixtures:
   - uploaded photo + `show_picture=false` → `has_media=true`, `media_type="photo"`, `show_picture=false`;
   - no uploaded media + logo picture enabled → `has_media=false`, `show_picture=true`;
   - declined media ad still reports media fields correctly.

## CPM caveat discovered in the same test

For the channel-media Stars campaign above, Telegram Ads UI/checkAdPost showed `cpm_extra="+80%"` for uploaded photo, while the local estimator had `media_photo=50` and reported effective CPM as `base * 1.5`.

Treat UI/checkAdPost `cpm_extra` as authoritative when present. If local CPM modifier constants disagree with validation preview, surface the mismatch and avoid presenting the local effective CPM as a fact.