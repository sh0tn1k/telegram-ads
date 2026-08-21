"""Pydantic models for ads.telegram.org domain.

Schemas derived from observed API responses (Phase 4-7 discovery).
All response models tolerant: extra fields ignored, unknown enums fail open.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# ─── Enums ────────────────────────────────────────────────────────────────────

Currency = Literal["TON", "STARS"]
KnownAdStatus = Literal["Active", "On Hold", "Stopped", "In Review", "Declined"]
AdStatus = Literal["Active", "On Hold", "Stopped", "In Review", "Declined", "Unknown"]
DataQuality = Literal["complete", "partial", "unreliable"]
TargetType = Literal["channels", "bots", "search"]
TrgType = Literal["search", "channel", "bot"]  # abbreviated form in /api responses
AccountType = Literal["Personal Account", "Bot Account", "Channel Account", "Organization"]
EventStatus = Literal["Active", "Inactive"]
EventType = Literal[
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
    "landing_view",  # auto-created
]

# ─── Base model with shared config ────────────────────────────────────────────


class _Model(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        str_strip_whitespace=False,
        populate_by_name=True,
        arbitrary_types_allowed=False,
    )


# ─── Account ──────────────────────────────────────────────────────────────────


class Account(_Model):
    """Telegram Ads cabinet (Personal/Bot/Channel/Organization)."""

    account_token: str
    title: str
    account_type: AccountType
    currency: Currency
    balance: float = 0.0
    photo_url: str | None = None
    is_active: bool = False  # currently selected cabinet


class AccountBudget(_Model):
    """Snapshot of /account/budget page."""

    balance: float
    currency: Currency
    transactions: list[Transaction] = Field(default_factory=list)


# ─── Transaction ──────────────────────────────────────────────────────────────


TransactionKind = Literal[
    "payment",  # "Payment: Ref#..." — top-up via Fragment
    "transfer_from_bot",  # Stars-only: bot star balance → ad-account
    "transfer_to_ad",  # − allocate to ad
    "returned_from_ad",  # + return from deleted/decreased ad
    "transfer_from_account",  # + within ad-level
    "transfer_to_account",  # − within ad-level
    "payment_for_views",  # − actual impression billing
    "unknown",
]


class Transaction(_Model):
    kind: TransactionKind
    amount: float  # signed: positive=in, negative=out
    currency: Currency
    when: str  # raw timestamp string from UI; parsed separately if needed
    ad_title: str | None = None  # for ad-related transactions
    reference: str | None = None  # Ref# for payments
    raw_text: str | None = None


# ─── Ad ───────────────────────────────────────────────────────────────────────


class Ad(_Model):
    """Full ad object as returned by /api editAdCPM / editAdStatus / incr/decrAdBudget.

    Also represents the canonical ad shape used internally.
    """

    ad_id: int
    title: str
    text: str = ""  # may be empty for search ads with auto-text
    trg_type: TrgType
    views: int = 0
    opens: int | None = None  # video opens, may be null
    clicks: int = 0
    actions: int | None = None
    action: str = ""  # CTA label: "Start bot" / "Join" / "Open"
    date: int = 0  # unix ts of creation
    ctr: float | bool = 0.0  # may be `false` if no clicks
    cvr: float | bool = 0.0
    cpm: float = 0.0
    cpc: float | bool = 0.0
    cpa: float | bool = 0.0
    budget: float = 0.0
    spent: float = 0.0
    daily_budget: float | bool = False
    daily_spent: float | bool = False
    target: str = ""  # human label: "10 queries" / "4 channels"
    tme_path: str = ""  # destination username (no @ / t.me/)
    status: AdStatus
    status_url: str = ""  # /edit_status | /edit_budget | ""


class AdSummary(_Model):
    """Compact ad info from homepage table row."""

    ad_id: int
    title: str
    status: AdStatus
    target_summary: str  # "10 queries" / "4 channels"
    views: int = 0
    clicks: int = 0
    actions: int = 0
    action_label: str = ""
    ctr: float | None = None  # percent MAGNITUDE (3.52 == 3.52%); None when "–"
    cvr: float | None = None  # percent MAGNITUDE; None when "–"
    cpm: float = 0.0
    cpc: float | None = None  # money; None when "–" (no clicks → no CPC)
    cpa: float | None = None  # money; None when "–" (no actions → no CPA)
    spent: float = 0.0
    budget: float = 0.0
    currency: Currency | None = None  # cabinet currency inferred from money glyph
    date_added: str = ""


class DeclineReason(_Model):
    """Visible decline reason block (may be absent on old declined ads)."""

    category: str  # "Prohibited content" / "Editorial requirements" / ...
    description: str
    read_more_url: str | None = None


class AdDetail(_Model):
    """Snapshot of /account/ad/<id> Info tab."""

    ad: Ad
    decline_reason: DeclineReason | None = None
    # Editable fields current values (for echo)
    promote_url: str = ""
    website_name: str = ""
    daily_budget_value: float | None = None
    views_per_user: int = 1
    is_active: bool = False
    # Uploaded-media state read off the detail page. ``has_media`` is derived from
    # the media token AND/OR a visible preview block — NOT from ``website_name``.
    # ``media_type`` is "photo"/"video" when inferable, "unknown" when media is
    # present but the kind can't be told apart, "" when there is no media.
    # ``show_picture`` is the bot/channel-picture checkbox state, or None when the
    # checkbox is absent (e.g. media mode hides it) so it can't be inferred.
    media_token: str = ""
    has_media: bool = False
    media_type: str = ""  # "photo" | "video" | "unknown" | ""
    show_picture: bool | None = None
    media_preview_present: bool = False
    # Locked targeting values read from the detail page (read-only chips). For a
    # search ad these are the target search queries. Immutable after creation.
    locked_targets: list[str] = Field(default_factory=list)


class AdStatsRow(_Model):
    """One row of monthly stats table on /account/ad/<id>/stats."""

    day: str  # "10 May 2026"
    views: int
    amount: float


class AdStats(_Model):
    ad_id: int
    title: str
    date_created: str
    cpm: float
    budget: float
    views: int
    monthly: list[AdStatsRow] = Field(default_factory=list)
    csv_url: str | None = None
    share_stats_url: str | None = None  # public URL if shared


# ─── Create / Edit drafts ─────────────────────────────────────────────────────


class CreateAdDraft(_Model):
    """User-facing draft for create_ad. Adapter converts to API payload.

    Targeting fields:
        - targets: list of raw user inputs (t.me URLs, @usernames, or query strings).
          Adapter resolves channels/bots via searchChannel API to opaque IDs.
          Search queries are base64-encoded with '+' prefix.
    """

    # Content
    title: str
    text: str
    promote_url: str  # t.me/... or @... or https://...
    website_name: str | None = None
    website_photo_url: str | None = None
    media_path: str | None = None  # local file, uploaded via /file/upload
    ad_info: str | None = None
    show_picture: bool = True

    # Money
    cpm: float
    budget: float = 0.0
    daily_budget: float | None = None

    # Delivery
    views_per_user: int = 1  # 1..4
    initial_active: bool = False

    # Targeting
    target_type: TargetType
    targets: list[str] = Field(default_factory=list)  # raw user inputs
    # Channels to EXCLUDE (ad will not be shown in these). The live create form
    # pairs this only with channel targeting (selectList channels.paired_field =
    # "exclude_channels", up to 100). Ignored for bots/search. Same raw-input
    # format as `targets`; resolved via searchChannel.
    exclude_channels: list[str] = Field(default_factory=list)

    # Schedule
    activate_at: str | None = None  # ISO datetime
    deactivate_at: str | None = None
    weekly_schedule: dict[int, set[int]] | None = None  # {day_idx: {hour, ...}}
    schedule_tz: str | None = None  # IANA TZ

    # Conversion (Stars website ads only)
    conversion_event_id: str | None = None

    # Misc
    placement: str = ""  # reserved, default empty


class EditAdDraft(_Model):
    """Editable subset of an ad (createAd minus immutable fields)."""

    ad_id: int
    title: str
    text: str
    promote_url: str
    website_name: str | None = None
    website_photo_url: str | None = None
    media_token: str | None = None
    ad_info: str | None = None
    cpm: float
    daily_budget: float | None = None
    active: bool = True
    views_per_user: int = 1


# ─── Events / Pixel (Stars only) ──────────────────────────────────────────────


class Event(_Model):
    """Pixel conversion event from Events Manager."""

    event_id: str  # 8-char opaque
    tag_id: str = ""  # "<pixel_id>-<event_id>" — goes into tgp('event', tag_id)
    title: str
    type: str  # human label like "Lead / Sign up" (NOT the enum value)
    status: EventStatus
    ads: int = 0  # how many ads use this as Conversion Event
    used: str = "–"  # UI-ready string for USED IN column
    date: int = 0
    mtime: int | bool = False
    can_edit: bool = True  # false for auto-events (Landing/Page views)


class EventLogEntry(_Model):
    event_time: str
    domain: str


class EventLog(_Model):
    event_id: str
    title: str
    entries: list[EventLogEntry] = Field(default_factory=list)
    window_hours: int = 8


class PixelSnippet(_Model):
    pixel_id: str
    base_code: str
    event_code: str | None = None  # for custom events; None for auto


# ─── API response wrappers ────────────────────────────────────────────────────


class ApiOk(_Model):
    ok: Literal[True]
    redirect_to: str | None = None
    toast: str | None = None


class ApiFieldError(_Model):
    field: str = ""
    error: str


class ApiGenericError(_Model):
    error: str


class ConfirmHashStep(_Model):
    confirm_text: str
    confirm_hash: str


class SearchChannelResult(_Model):
    val: str  # opaque token to use as chip data-val
    name: str
    photo: str = ""  # raw <img> HTML
    username: str = ""
    hidden: bool = False


class SearchChannelResponse(_Model):
    ok: Literal[True]
    channel: SearchChannelResult


class CheckAdPostPreview(_Model):
    from_: str = Field(default="", alias="from")
    from_desc: str = ""
    photo: str = ""
    has_photo: bool = False
    text: str = ""
    button: str = ""  # CTA label
    button_url: str = ""
    color: int = 0
    color_data: int = 0
    accent_colors: list[str] = Field(default_factory=list)
    media: str = ""
    media_on: bool = False
    picture: bool = False
    picture_label: str = ""
    # UI-declared creative CPM surcharge label for this preview, e.g. "+80%".
    # Authoritative when present (overrides the static local modifier estimate).
    cpm_extra: str = ""
    avail_targets: dict[str, bool] = Field(default_factory=dict)


class CheckAdPostResponse(_Model):
    field: str = ""
    error: str = ""
    promote_url: str = ""
    preview_data: CheckAdPostPreview | None = None


class AdMutationResponse(_Model):
    """Response from editAdCPM, editAdStatus, incrAdBudget, decrAdBudget.

    editAdCPM / editAdStatus return the full updated ``ad`` object. The budget
    endpoints (incrAdBudget / decrAdBudget) instead reply ``{"ok": True,
    "owner_budget": "<html>"}`` — the refreshed account-budget widget — and carry
    **no** ``ad``. Both are valid success shapes, so ``ad`` is optional and a
    budget mutation is confirmed by ``ok`` (+ ``owner_budget``)."""

    ok: Literal[True]
    ad: Ad | None = None
    owner_budget: str | None = None
    toast: str | None = None


class CreateEventResponse(_Model):
    ok: Literal[True]
    event: Event
    event_opt: dict[str, str] = Field(default_factory=dict)  # {val, name}
    to_layer: str = ""


class RevokeStatsUrlResponse(_Model):
    ok: Literal[True]
    new_url: str
    toast: str = ""


class MediaUploadResponse(_Model):
    """Inferred shape: {ok, media: <opaque_token>}. Success format not yet confirmed.

    On error: {"error": "<reason>"} (e.g., "The aspect ratio must be 16:9.")
    """

    ok: Literal[True]
    media: str  # opaque token to put in form's hidden input[name='media']


# ─── Public stats share ───────────────────────────────────────────────────────


class ShareStats(_Model):
    ad_id: int
    public_url: str  # https://ads.telegram.org/stats/<share_token>
    share_token: Annotated[str, Field(min_length=8, max_length=32)]


# Resolve forward refs
AccountBudget.model_rebuild()
