"""Constants and enums for ads.telegram.org.

Derived from FULL_SITE_MAP.md (Phase 1-7 discovery).
"""

from __future__ import annotations

from typing import Final

BASE_URL: Final = "https://ads.telegram.org"

# ─── URL paths ────────────────────────────────────────────────────────────────

URL_LANDING: Final = "/"
URL_AUTH: Final = "/auth"
URL_GETTING_STARTED: Final = "/getting-started"
URL_GUIDELINES: Final = "/guidelines"
URL_TOS: Final = "/tos"
URL_DOCS_PIXEL: Final = "/docs/pixel"

URL_ACCOUNT: Final = "/account"
URL_ACCOUNT_EDIT: Final = "/account/edit"
URL_ACCOUNT_BUDGET: Final = "/account/budget"
URL_ACCOUNT_STATS: Final = "/account/stats"
URL_ACCOUNT_BUDGET_ADD_STARS: Final = "/account/budget/add_stars"

URL_AD_NEW: Final = "/account/ad/new"
URL_AD: Final = "/account/ad/{id}"
URL_AD_BUDGET: Final = "/account/ad/{id}/budget"
URL_AD_STATS: Final = "/account/ad/{id}/stats"
URL_AD_STATS_SHARE: Final = "/account/ad/{id}/stats/share"
URL_AD_EDIT_CPM: Final = "/account/ad/{id}/edit_cpm"
URL_AD_EDIT_BUDGET: Final = "/account/ad/{id}/edit_budget"
URL_AD_EDIT_STATUS: Final = "/account/ad/{id}/edit_status"

URL_EVENTS: Final = "/account/events"
URL_EVENTS_SETUP: Final = "/account/events/setup"
URL_EVENT_NEW: Final = "/account/event/new"
URL_EVENT_SETUP: Final = "/account/event/{id}/setup"
URL_EVENT_LOG: Final = "/account/event/{id}/log"

URL_CHOOSE_ACCOUNT: Final = "/choose_account"
URL_CHOOSE_ACCOUNT_TOKEN: Final = "/choose_account/{token}"

URL_API: Final = "/api"
URL_FILE_UPLOAD: Final = "/file/upload"

# CSV endpoints
URL_CSV_ACCOUNT_COUNT: Final = "/csv?prefix=account/{token}&period=day"
URL_CSV_ACCOUNT_BUDGET: Final = "/csv?prefix=account/{token}/budget&period=day"
URL_REPORT_ACCOUNT_MONTHLY: Final = "/reports/account/{token}?month={month}"
URL_REPORT_AD_MONTHLY: Final = "/reports/account/{token}/ad/{ad_id}?month={month}"

# External
URL_FRAGMENT_PAY: Final = "https://fragment.com/ads/pay?account={token}"
URL_ADS_MARKDOWN_BOT: Final = "https://t.me/AdsMarkdownBot"
URL_PIXEL_SCRIPT: Final = "https://telegram.org/js/pixel.js"
URL_PUBLIC_STATS: Final = "https://ads.telegram.org/stats/{share_token}"

# ─── API method names ─────────────────────────────────────────────────────────

# IMPORTANT: case-sensitive. editAdCPM has UPPERCASE CPM!
METHOD_SEARCH_CHANNEL: Final = "searchChannel"
METHOD_CHECK_AD_POST: Final = "checkAdPost"
METHOD_CREATE_AD: Final = "createAd"
METHOD_SAVE_AD_DRAFT: Final = "saveAdDraft"
METHOD_CREATE_DRAFT_FROM_AD: Final = "createDraftFromAd"
METHOD_EDIT_AD: Final = "editAd"
METHOD_EDIT_AD_CPM: Final = "editAdCPM"  # UPPERCASE CPM, not editAdCpm
METHOD_EDIT_AD_STATUS: Final = "editAdStatus"
METHOD_INCR_AD_BUDGET: Final = "incrAdBudget"
METHOD_DECR_AD_BUDGET: Final = "decrAdBudget"  # NB: param name is `amount`, not `decr_amount`
METHOD_CREATE_EVENT: Final = "createEvent"
METHOD_DELETE_EVENT: Final = "deleteEvent"  # 2-step with confirm_hash
METHOD_DELETE_AD: Final = "deleteAd"  # 2-step with confirm_hash
METHOD_REVOKE_STATS_URL: Final = "revokeStatsUrl"

# ─── Ad statuses ──────────────────────────────────────────────────────────────

STATUS_ACTIVE: Final = "Active"
STATUS_ON_HOLD: Final = "On Hold"
STATUS_STOPPED: Final = "Stopped"
STATUS_IN_REVIEW: Final = "In Review"
STATUS_DECLINED: Final = "Declined"
STATUS_UNKNOWN: Final = "Unknown"

# Known/recognised statuses. Unknown is NOT a known status — always a parse warning.
KNOWN_STATUSES: Final = (STATUS_ACTIVE, STATUS_ON_HOLD, STATUS_STOPPED, STATUS_IN_REVIEW, STATUS_DECLINED)
ALL_STATUSES: Final = KNOWN_STATUSES + (STATUS_UNKNOWN,)

# ─── Currencies ───────────────────────────────────────────────────────────────

CURRENCY_TON: Final = "TON"
CURRENCY_STARS: Final = "STARS"

CURRENCY_DECIMALS = {CURRENCY_TON: 5, CURRENCY_STARS: 0}
CURRENCY_ICON = {CURRENCY_TON: "\U0001f48e", CURRENCY_STARS: "⭐️"}

# ─── Target types ─────────────────────────────────────────────────────────────

TARGET_CHANNELS: Final = "channels"
TARGET_BOTS: Final = "bots"
TARGET_SEARCH: Final = "search"

ALL_TARGET_TYPES: Final = (TARGET_CHANNELS, TARGET_BOTS, TARGET_SEARCH)

# Internal trg_type abbreviation in ad object responses
TRG_TYPE_SEARCH: Final = "search"
TRG_TYPE_CHANNEL: Final = "channel"
TRG_TYPE_BOT: Final = "bot"

# ─── Event types (Pixel) ──────────────────────────────────────────────────────

EVENT_TYPES: Final = (
    "page_view",
    "add_to_cart",
    "add_to_wishlist",
    "customize_product",
    "initiate_checkout",
    "add_payment_info",
    "purchase",
    "contact",
    "lead",
    "schedule",
    "complete_registration",
    "submit_application",
    "start_trial",
    "subscribe",
    "view_content",
    "search",
    "find_location",
    "donate",
    "custom",
)

# ─── Hard limits ──────────────────────────────────────────────────────────────

MAX_AD_TEXT_LEN: Final = 160
MIN_CPM_TON: Final = 0.1
MIN_CPM_STARS_OBSERVED: Final = 50  # точное минимальное TBD
MIN_BUDGET_AFTER_INCR_TON: Final = 1.0
DECR_BUDGET_COOLDOWN_SECONDS: Final = 120  # после editAdStatus
MIN_CHANNEL_SUBSCRIBERS: Final = 1000
MEDIA_ASPECT_RATIO: Final = (16, 9)
TARGET_CHANGES_PROPAGATION_MAX_MIN: Final = 60  # ToS §3.4(b)

# ─── TON cabinet hard geo block ──────────────────────────────────────────────

# Phone country codes BLOCKED FROM SEEING TON ads (not configurable, hard limit).
# Stars cabinet is NOT affected by this restriction.
TON_GEO_HARD_BLOCK: Final = frozenset({"RU", "UA", "IL", "PS"})

# ─── Guidelines categories (Section 5 of /guidelines) ────────────────────────

GUIDELINES_PROHIBITED_CATEGORIES: Final = (
    "graphic_shocking_sexual",
    "hate_violence_harassment",
    "third_party_rights",
    "deceptive_misleading",
    "political_sensitive_religion",
    "gambling",
    "predatory_finance",
    "uncertified_medical",
    "drugs_alcohol_tobacco",
    "weapons",
    "spam_malware_hacking",
    "questionable_legality",
)

# ─── HTTP request headers for internal API ───────────────────────────────────

API_HEADERS: Final = {
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}

# ─── Pixel ───────────────────────────────────────────────────────────────────

PIXEL_GLOBAL_FN: Final = "tgp"
PIXEL_BASE_INIT_TEMPLATE: Final = (
    '<script>(function(t,l,g,r,m){{t[g]||(g=t[g]=function(){{'
    "g.run?g.run.apply(g,arguments):g.queue.push(arguments)}},g.queue=[]),"
    "t=l.createElement(r),t.async=!0,t.src=m,"
    "l=l.getElementsByTagName(r)[0],l.parentNode.insertBefore(t,l)}})"
    "(window,document,'tgp','script','https://telegram.org/js/pixel.js');"
    "tgp('init','{pixel_id}');</script>"
)
PIXEL_EVENT_TEMPLATE: Final = "<script>tgp('event','{tag_id}');</script>"
