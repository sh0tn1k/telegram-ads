# Media channel create test — post-create verification discrepancy

Session learning from a channel-targeted uploaded-photo create flow for ExampleBot (2026-06-06). This is a class-level pitfall for future Telegram Ads media-create tests, not a one-off narrative.

## Scenario

Draft shape:
- `target_type="channels"`
- `targets=["@durov"]`
- `media_path` points to a 16:9 JPEG
- `show_picture=false`
- `initial_active=false`
- uploaded photo via `telegram_ads_upload_media`
- validation/checkAdPost before create

## Observed flow

Pre-create checks worked:
- media file was 1280x720 JPEG, exactly 16:9.
- `telegram_ads_upload_media` returned a non-empty `media_token`.
- `telegram_ads_validate_ad` returned `valid=true`.
- validation preview showed `has_photo=true`, `media_on=true`, `picture=false`.
- validation creative showed `media_uploaded=true`, `media_type=photo`, `media_supported_by_target_type=true`.

Create approval worked after correcting CPM:
- First create attempt with base `cpm=65` failed at API time: `CPM can't be less than ⭐️90`.
- Revalidating with `cpm=90` passed.
- Create approval summary showed `media=photo`, `show_picture=False`, `est_effective_cpm=135.0`.
- Applying the approval created a new ad.

Post-create detail showed a contradiction:
- `list_ads` showed the new campaign with `status="In Review"`, `cpm=90`, `budget=100`, `spent=0`.
- `get_ad` / `get_ad_creative` detail showed `status="On Hold"`, `is_active=false`, but also `has_media=false` and `show_picture=true`.
- `get_ad_targeting` showed `target_type="channels"` and the target chip resolved from `@durov` to `Pavel Durov`.
- `get_ad_stats` immediately after creation reported `budget=0.0` while `list_ads` and `get_ad_budget_status` reported `budget=100.0`; treat stats budget as lagging/inconsistent right after create.

## Durable lessons

1. For Stars channel media campaigns, `validate_ad` may pass at a lower CPM than the final create API accepts. If create returns a minimum-CPM API error, revalidate with the API-stated minimum CPM before issuing a new approval.
2. Do not assume `validate_ad` preview media guarantees post-create `has_media=true`. Final detail can still report `has_media=false` / `show_picture=true` even for channel targeting.
3. After media create, always verify with all three surfaces:
   - `list_ads` for title/status/cpm/budget/spend snapshot;
   - `get_ad_budget_status` for active=false, budget, spend, CPM;
   - `get_ad` or `get_ad_creative` for final media/show_picture fields.
4. If final detail contradicts draft/validation/approval, stop. Do not edit, recreate, delete, start, or change budget/CPM without a fresh explicit approval scope.
5. In the final report, distinguish:
   - `pre-submit media validation: PASS`;
   - `campaign creation: PASS`;
   - `post-create media persistence/detail: FAIL or discrepancy`.

## Suggested verification wording

```text
Post-create discrepancy:
- validation preview: has_photo=true, media_on=true, picture=false
- create approval: media=photo, show_picture=False
- final detail: has_media=false, show_picture=true

I stopped after verification. No edit/recreate/start/budget/CPM actions were taken.
```
