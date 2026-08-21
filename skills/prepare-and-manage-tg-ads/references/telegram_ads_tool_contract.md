# Telegram Ads Tool Contract

**File:** `telegram_ads_tool.py` → `tools/telegram_ads_tool.py`  
**Package:** `hermes_telegram_ads`  
**Config:** `/home/hermes/.hermes/telegram_ads.yaml`  
**Toolset:** `telegram_ads`  
**Check fn:** `_check_telegram_ads_enabled()` — package importable + config exists + enabled: true  

---

## Action Registry

### Safe Read (17 actions) — execute immediately, no confirmation

| Action | Adapter method | Input params | Output | Notes |
|---|---|---|---|---|
| `status` | `open_dashboard` | _(none)_ | dashboard URL | Alias for open_dashboard |
| `open_dashboard` | `open_dashboard` | _(none)_ | `str` — current URL | Navigate to /account |
| `ensure_logged_in` | `ensure_logged_in` | _(none)_ | `bool` | Raises LoginRequiredError if not |
| `list_accounts` | `list_accounts` | _(none)_ | `list[Account]` | |
| `choose_account` | `choose_account` | `account_token: str` | `Account \| None` | Switches cabinet, re-bootstraps API |
| `current_account` | `get_current_account` | _(none)_ | `Account \| None` | |
| `list_ads` | `list_ads` | _(none)_ | `list[AdSummary]` | Limit: max_ads_to_parse=100 |
| `get_ad` | `get_ad` | `ad_id: int` | `AdDetail` | |
| `get_account_budget` | `get_account_budget` | _(none)_ | `AccountBudget` | balance + transactions |
| `get_ad_stats` | `get_ad_stats` | `ad_id: int` | `AdStats` | views, monthly rows, csv_url |
| `download_report` | `download_ad_report` | `ad_id: int`, `month: str` (YYYYMM) | `Path` — saved CSV | |
| `validate_ad` | `validate_ad` | `draft: CreateAdDraft` | `dict` — checkAdPost result | Policies checked locally too |
| `screenshot` | `screenshot` | `name: str`, `full_page: bool` | `Path` — saved PNG | |
| `list_events` | `list_events` | _(none)_ | `list[Event]` | Pixel events (Stars only) |
| `get_pixel_snippet` | `get_pixel_base_snippet` | _(none)_ | `PixelSnippet \| None` | |
| `get_event_log` | `get_event_log` | `event_id: str` | `EventLog` | |
| `get_share_stats_url` | `get_share_stats_url` | `ad_id: int` | `str \| None` | Public share URL |

### Draft (4 actions) — execute immediately, no confirmation, audit logged

| Action | Adapter method | Input params | Risk level |
|---|---|---|---|
| `prepare_draft` | `prepare_ad_draft` | `draft: CreateAdDraft`, `screenshot_name: str` | DRAFT |
| `save_draft` | `save_ad_draft` | `draft: CreateAdDraft` | DRAFT |
| `upload_media` | `upload_media` | `file_path: str` | DRAFT |
| `create_similar_draft` | `create_similar_draft` | `source_ad_id: int` | DRAFT |

### Confirm-Required (9 actions) — need `confirmation_id`, need the operator approval

| Action | Adapter method | Input params | Risk level |
|---|---|---|---|
| `create_ad` | `create_ad` | `draft: CreateAdDraft`, `confirmation_id` | CONFIRM_REQUIRED |
| `edit_ad` | `edit_ad` | `draft: EditAdDraft`, `confirmation_id` | CONFIRM_REQUIRED |
| `change_cpm` | `change_cpm` | `ad_id: int`, `new_cpm: float`, `confirmation_id` | CONFIRM_REQUIRED |
| `change_budget` | `add_to_budget` | `ad_id: int`, `amount: float`, `confirmation_id` | CONFIRM_REQUIRED |
| `add_to_budget` | `add_to_budget` | `ad_id: int`, `amount: float`, `confirmation_id` | CONFIRM_REQUIRED |
| `withdraw_from_budget` | `withdraw_from_budget` | `ad_id: int`, `amount: float`, `confirmation_id` | CONFIRM_REQUIRED |
| `pause_ad` | `change_status` | `ad_id: int`, `confirmation_id` | CONFIRM_REQUIRED |
| `resume_ad` | `change_status` | `ad_id: int`, `confirmation_id` | CONFIRM_REQUIRED |
| `create_event` | `create_event` | `title: str`, `event_type: str`, `confirmation_id` | CONFIRM_REQUIRED |

### Double-Confirm Required (3 actions) — need 2× `confirmation_id`, + the operator approval

| Action | Adapter method | Input params | Risk level |
|---|---|---|---|
| `delete_ad` | `delete_ad` | `ad_id`, `confirmation_id`, `second_confirmation_id` | DOUBLE_CONFIRM |
| `delete_event` | `delete_event` | `event_id`, `confirmation_id`, `second_confirmation_id` | DOUBLE_CONFIRM |
| `revoke_stats_url` | `revoke_share_stats_url` | `ad_id`, `confirmation_id`, `second_confirmation_id` | DOUBLE_CONFIRM |

### Forbidden (3 actions) — blocked by safety layer

| Action | Reason |
|---|---|
| `transfer_stars` | `forbid_transfer_stars_until_verified` |
| `external_payment` | `forbid_external_payment_until_verified` |
| `change_status` | Reserved — not a valid API method |

---

## Confirmation Flow

```
Agent calls action without confirmation_id
  → Safety layer raises ConfirmationRequiredError
  → Response: {"requires_confirmation": true, "confirmation_id": "uuid", "params_summary": {...}}
  → Agent shows params_summary to user, asks for approval
  → User approves → Agent calls same action WITH confirmation_id
  → Safety consumes confirmation → API executes
```

- TTL: 300 seconds
- Fingerprint: tied to param hash — tampered params rejected
- Single-use: consumed ID fails on second attempt
- Double confirm: requires TWO different confirmation_ids for delete/revoke

---

## Safety Config

```yaml
safety:
  require_confirmation_for_create: true
  require_confirmation_for_edit: true
  require_confirmation_for_budget: true
  require_double_confirmation_for_delete: true
  require_double_confirmation_for_revoke_stats: true
  forbid_transfer_stars_until_verified: true
  forbid_external_payment_until_verified: true
  confirmation_ttl_seconds: 300
```

---

## Profile Visibility

| Profile | telegram_ads toolset | Can call tool |
|---|---|---|
| **default** (the agent) | ✅ Yes | ✅ Yes |
| **deepseek** | ❌ No (only hermes-cli) | ❌ No |

DeepSeek receives Telegram Ads data only via the agent-delegated summaries.

---

> This is a local mirror. Canonical shared contract: `/home/hermes/.hermes/shared/TELEGRAM_ADS_TOOL_CONTRACT.md`

## Pending gaps (as of 2026-06-02)

1. `download_account_report` — implemented in adapter, not exposed as tool action
2. `get_account_stats` — stub only, not worth exporting
3. **No pytest suite** — only standalone smoke_test scripts
4. **Config headless: false** — requires Xvfb/display for non-TUI automation

### Resolved in this session

- ~~**Schema enum incomplete** — `get_pixel_snippet`, `get_event_log`, `change_budget` added to TELEGRAM_ADS_SCHEMA enum; 2 kwargs bugfixes (get_event_log, change_budget) also applied.~~ ✅ (2026-06-02)
