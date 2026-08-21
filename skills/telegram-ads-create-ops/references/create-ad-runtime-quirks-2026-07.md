# Create-ad runtime quirks — 2026-07-16

Session: Example Bot channel **flow test** create (STARS bot cabinet).

## Cabinet

```text
title:        Example Bot | Short Clips   # pipe in title → code-block render
account_ref:  acc_2 (after reconcile)
currency:     STARS
list balance: 0          # STALE — do not trust
live balance: 1164 → 1064 after create
```

## Draft submitted

```text
title:           HERMES_CHANNEL_TEST_20260716
ad_id:           23
promote_url:     t.me/ExampleBot
target_type:     channels
targets:         @durov  (detail chip: "Pavel Durov")
text:            Turn long videos into viral shorts in minutes. AI picks the best hooks automatically.
cpm base:        65
est. effective:  84.5 (show_picture +30%)
budget:          100
initial_active:  false
show_picture:    true
media:           none
```

## Tool results

| Step | Result |
|---|---|
| `prepare_targeting` | valid |
| `estimate_cpm` | base 65, eff 84.5, modifiers `[show_picture]` |
| `validate_ad` | valid, preview VIEW BOT → t.me/ExampleBot |
| `prepare_ad_draft` without `.png` | **FAIL** mime type None |
| `prepare_ad_draft` with `.png` | ok, screenshot under `~/.hermes/data/telegram_ads/screenshots/` |
| `prepare_approval_request(tool="create_ad")` | **FAIL** Unknown tool: 'create_ad' |
| bare `create_ad` (no conf id) | approval_required, conf id issued |
| `create_ad` + conf id | ok, `redirect_to: /account` (no ad_id in body) |
| `list_ads` | ad 23, **In Review**, budget 100 |
| `get_ad(23)` | **On Hold**, creative+targeting present |
| `get_account_budget` | `transfer_to_ad` −100 titled HERMES_CHANNEL_TEST_20260716 |
| V2 `register_post_action_watch` | skipped `watcher_disabled` |
| `telegram_ads_register_campaign_watch` | watch_id **619**, project opusclips-bot |

## Status interpretation

- Not live / not spending.
- Dual status is expected partial DQ under `initial_active=false` until moderation settles and (if desired) separate `start_ad` approval.

## Side effects accounting (honest)

1. Cabinet switch to acc_2  
2. Draft validate + prepare + screenshot  
3. Confirmed create_ad apply  
4. ⭐100 reserved on ad #23  
5. Cabinet 1164 → 1064  
6. Operator watch 619  

No start_ad, no CPM change, no delete.
