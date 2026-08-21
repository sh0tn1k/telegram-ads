# Legacy `telegram_ads` dispatcher — action mapping

Use this reference when the typed `telegram_ads_*` tools are not in the
LLM session's function-calling API (see SKILL.md §"Tool availability
verification", step 5).

The legacy single-tool dispatcher is wired to the **same**
`TelegramAdsAdapter` instance as the typed toolset, via
`BrowserProfileManager.shared().acquire_adapter()`. No second browser is
created, no Playwright profile lock race occurs, and the same
`confirmation_id` / double_confirm safety gates apply.

Invocation pattern:

```
telegram_ads(action="<action>", **params)
```

## Action → typed tool map

| Legacy `action=` | Typed equivalent | Adapter method | Notes |
|---|---|---|---|
| `status` | `telegram_ads_status` | `open_dashboard` | Returns URL string only. No structured `logged_in` field — use `ensure_logged_in` for that. |
| `open_dashboard` | `telegram_ads_open_dashboard` | `open_dashboard` | Returns URL. |
| `ensure_logged_in` | `telegram_ads_ensure_login` | `ensure_logged_in` | Returns `true` if logged in, `false` otherwise. |
| `list_accounts` | `telegram_ads_list_accounts` | `list_accounts` | List of `Account` (masked tokens). |
| `choose_account` | `telegram_ads_choose_account` | `choose_account` | Needs `account_token`. |
| `current_account` | `telegram_ads_current_account` | `get_current_account` | Account dict with masked `account_token`. |
| `list_ads` | `telegram_ads_list_ads` | `list_ads` | List of ad summaries. |
| `get_ad` | `telegram_ads_get_ad` | `get_ad` | Needs `ad_id`. |
| `get_account_budget` | `telegram_ads_get_account_budget` | `get_account_budget` | — |
| `get_ad_stats` | `telegram_ads_get_ad_stats` | `get_ad_stats` | Needs `ad_id`. |
| `download_report` | `telegram_ads_download_report` | `download_ad_report` | Needs `ad_id`, `month` (YYYYMM). Returns file path. |
| `validate_ad` | `telegram_ads_validate_ad` | `validate_ad` | Needs `draft` (CreateAdDraft). |
| `screenshot` | (no direct typed equivalent) | `screenshot` | Needs `name`, `full_page` (optional). |
| `list_events` | `telegram_ads_list_events` | `list_events` | — |
| `get_pixel_snippet` | `telegram_ads_get_pixel_snippet` | `get_pixel_base_snippet` | — |
| `get_event_log` | `telegram_ads_get_event_log` | `get_event_log` | Needs `event_id`. |
| `get_share_stats_url` | `telegram_ads_get_share_stats_url` | `get_share_stats_url` | Needs `ad_id`. |
| `prepare_draft` | `telegram_ads_prepare_ad_draft` | `prepare_ad_draft` | Needs `draft`. |
| `save_draft` | `telegram_ads_save_ad_draft` | `save_ad_draft` | Needs `draft`. |
| `upload_media` | `telegram_ads_upload_media` | `upload_media` | Needs `file_path`. |
| `create_similar_draft` | (no direct typed equivalent) | `create_similar_draft` | Needs `source_ad_id`. |
| `create_ad` | `telegram_ads_create_ad` | `create_ad` | CONFIRM_REQUIRED — needs `confirmation_id` after first call. |
| `edit_ad` | `telegram_ads_edit_ad` | `edit_ad` | CONFIRM_REQUIRED. |
| `change_cpm` | `telegram_ads_change_cpm` | `change_cpm` | CONFIRM_REQUIRED. |
| `add_to_budget` | `telegram_ads_add_to_budget` | `add_to_budget` | CONFIRM_REQUIRED. |
| `withdraw_from_budget` | `telegram_ads_withdraw_from_budget` | `withdraw_from_budget` | CONFIRM_REQUIRED. |
| `pause_ad` | `telegram_ads_stop_ad` | `change_status` (active=False) | CONFIRM_REQUIRED. **Naming inverted** vs typed tool. |
| `resume_ad` | `telegram_ads_start_ad` | `change_status` (active=True) | CONFIRM_REQUIRED. **Naming inverted** vs typed tool. |
| `create_event` | `telegram_ads_create_event` | `create_event` | CONFIRM_REQUIRED. |
| `delete_ad` | `telegram_ads_delete_ad` | `delete_ad` | DOUBLE_CONFIRM. |
| `delete_event` | `telegram_ads_delete_event` | `delete_event` | DOUBLE_CONFIRM. |
| `revoke_stats_url` | `telegram_ads_revoke_share_stats_url` | `revoke_share_stats_url` | DOUBLE_CONFIRM. |

## Envelope shape

The legacy dispatcher returns a simpler envelope than the typed tools:

- **Success:** `{"ok": true, "data": <payload>}` where `<payload>` may be a
  primitive, dict, list, or `Path` for file outputs.
- **Login required:** `{"ok": false, "error": "LOGIN_REQUIRED", "message": "...",
  "hint": "Manual login required at ads.telegram.org. ..."}`.
- **Confirmation required:** `{"ok": false, "requires_confirmation": true,
  "action": "...", "risk_level": "...", "confirmation_id": "uuid",
  "params_summary": {...}, "message": "..."}`. Destructive actions also
  include `second_confirmation_id`.
- **Forbidden:** `{"ok": false, "error": "FORBIDDEN", "message": "...",
  "context": {...}}`.
- **Internal error:** `{"ok": false, "error": "INTERNAL_ERROR", "message": "..."}`.

There is **no top-level `status:` field**. Read `ok` first, then `data` or
`error`. The legacy envelope does not use `status: "login_required"` or
`status: "approval_required"` — those are typed-tool conventions only.

For large results, the dispatcher may also return a `data_ref` (truncation
pointer) plus a `summary` snippet. The full payload is available on request.

## When to use this map

Use it when:

- Pre-flight check (SKILL.md §"Tool availability verification", step 5)
  shows the typed tools are not in your LLM session's function-calling API.
- The model is mid-conversation and you cannot restart the gateway to
  refresh the schema cache.
- the operator explicitly approves the legacy dispatcher for the current task.

Do **not** use it as a permanent substitute for fixing the schema-caching
gap. If multiple sessions in a row show the same mismatch, that is a real
bug worth investigating (e.g. a model/provider that doesn't pick up the
typed tools, a broken `register()` call in `telegram_ads_typed_tool.py`,
or a stale `models.json` cache).

## Source of truth

- Action enum extracted from
  `~/.hermes/hermes-agent/tools/telegram_ads_tool.py:455-517`
  (`TELEGRAM_ADS_SCHEMA` function `parameters.action.enum`).
- Action-to-method map extracted from
  `~/.hermes/hermes-agent/tools/telegram_ads_tool.py:197-235`
  (`action_map` in `_call_telegram_ads`).
- Safety classification per action (SAFE_READ / DRAFT / CONFIRM /
  DOUBLE_CONFIRM) from `_validate_action` sets in the same file, lines
  147-166.
- Typed-tool safety classes from
  `hermes_telegram_ads.TELEGRAM_ADS_TOOLS[*].safety_class`.

If these source files move, update the line references in this file too.
