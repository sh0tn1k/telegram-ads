# Media placement compatibility for Telegram Ads

## Trigger

Use this reference when preparing, validating, or creating Telegram Ads campaigns
with `media_path`, `media_token`, uploaded photo/video creatives, or CPM media
modifiers.

## Durable learning from live test

Live ExampleBot test on 2026-06-06 created ad title
`HERMES_MEDIA_REVIEW_TEST_photo` with `target_type="search"`, uploaded JPEG
16:9 media, and `initial_active=false`.

Observed flow:

1. Local image check passed: JPEG 1280x720, 16:9.
2. `upload_media` returned a 64-character media token.
3. `validate_ad/checkAdPost` accepted the draft and preview reported photo / media uploaded.
4. `create_ad` submitted the ad to moderation.
5. Final ad detail did not contain uploaded media, and Search campaign rendered as a search-result style ad.

Conclusion: this was **placement mismatch**, not a successful uploaded-media
create test.

## Placement rules

- `channels`: supports uploaded photo/video creatives.
- `search`: does not support uploaded photo/video creatives. Use text/query workflow only.
- `bots`: does not support uploaded photo/video creatives. Use bot/channel logo / `show_picture` workflow only.

## Required tool behavior

### Validation guard

If a draft includes `media_path` and `target_type != "channels"`, block with a
structured error before upload:

- reason/capability: `unsupported_media_for_target_type`
- `blocked_before_upload=true`
- include `target_type`, `media_path`, and `supported_target_types=["channels"]`

Do not allow `validate_ad` to upload media or call `checkAdPost` for unsupported
placements. Server preview can be misleading for Search.

### Create guard

If a draft includes `media_path` and `target_type != "channels"`, block before
issuing confirmation and before upload/create:

- no `approval_required`
- no upload
- no create
- no media-less fallback

This prevents a human from approving a misleading create prompt that says media
will be uploaded when the placement will ignore/drop it.

### CPM guard

Only apply `media_photo` / `media_video` CPM modifiers when uploaded media is
supported by the placement (`channels`). For `search` or `bots`, do not report
photo/video modifier as applicable. Prefer diagnostic fields:

- `media_supported_by_target_type=false`
- `media_ignored_by_placement=true`
- `recovery_hint="use target_type=\"channels\" for uploaded media"`

## Suggested code patch shape

Add a small placement guard near media helpers:

```python
SUPPORTED_UPLOADED_MEDIA_TARGET_TYPES = {"channels"}

def uploaded_media_supported_for_target_type(target_type: str | None) -> bool:
    return (target_type or "").lower() in SUPPORTED_UPLOADED_MEDIA_TARGET_TYPES


def assert_uploaded_media_supported_for_target_type(*, target_type: str | None, media_path: str | None) -> None:
    if not media_path:
        return
    if not uploaded_media_supported_for_target_type(target_type):
        raise MediaUnsupportedError(
            "unsupported_media_for_target_type",
            message=(
                "Uploaded photo/video creatives are supported only for channel targeting. "
                "Search campaigns render as search-result style ads; bot targeting uses logo/show_picture."
            ),
            context={
                "target_type": (target_type or "").lower(),
                "media_path": media_path,
                "supported_target_types": ["channels"],
                "blocked_before_upload": True,
            },
        )
```

Call this guard inside shared media resolution before `upload_media`, and also at
the Hermes tool layer before `create_ad` issues `approval_required`.

## Regression tests to add

- `media_path + target_type=search` → `unsupported_media_for_target_type`
- `media_path + target_type=bots` → `unsupported_media_for_target_type`
- `media_path + target_type=channels` → allowed and token reaches payload
- CPM media modifier not applied for search/bots
- `create_ad` refuses unsupported placement before confirmation
- `validate_ad` refuses unsupported placement before upload/checkAdPost

## Operator rule

For uploaded photo/video live-flow tests, use channel targeting. Never use search
targeting. For Search, test only text/query workflows. For Bots, test only
logo/show_picture workflows unless Telegram Ads product behavior changes and is
verified again.
