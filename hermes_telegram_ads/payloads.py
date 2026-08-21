"""Payload builders: CreateAdDraft → API form params.

Handles:
    - schedule encoding (7-csv 24-bit bitmask)
    - search query base64 encoding (+<base64(query)>)
    - channel/bot URL resolution via searchChannel API
    - active=1/0, schedule=0;0;...
"""

from __future__ import annotations

import base64
from typing import Any

from hermes_telegram_ads.api import TelegramAdsApi
from hermes_telegram_ads.cpm_modifiers import detect_custom_emoji
from hermes_telegram_ads.errors import TelegramAdsApiError
from hermes_telegram_ads.media import media_hash, media_type_for_path
from hermes_telegram_ads.types import CreateAdDraft, EditAdDraft, TargetType

# ─── Schedule encoding ─────────────────────────────────────────────────────────


def encode_schedule(weekday_hours: dict[int, set[int]] | None) -> str:
    """Encode {day_idx: {hour, ...}} → "d1;d2;d3;d4;d5;d6;d7" (Mon→Sun).

    Each day = 24-bit integer (bit N = hour N selected).

    >>> encode_schedule({0: {0, 9}, 6: {23}})
    '513;0;0;0;0;0;8388608'
    """
    days = []
    src = weekday_hours or {}
    for d in range(7):
        hours = src.get(d, set())
        mask = sum(1 << h for h in hours if 0 <= h < 24)
        days.append(str(mask))
    return ";".join(days)


def decode_schedule(s: str) -> dict[int, set[int]]:
    """Inverse of encode_schedule. Empty string → {}.

    >>> decode_schedule('513;0;0;0;0;0;8388608')[0] == {0, 9}
    True
    """
    if not s or s == "0;0;0;0;0;0;0":
        return {}
    parts = s.split(";")
    out: dict[int, set[int]] = {}
    for d in range(min(7, len(parts))):
        try:
            mask = int(parts[d])
        except ValueError:
            mask = 0
        if mask:
            out[d] = {h for h in range(24) if mask & (1 << h)}
    return out


def is_schedule_active(weekly_schedule: dict[int, set[int]] | None) -> bool:
    return bool(weekly_schedule) and any(hours for hours in (weekly_schedule or {}).values())


# ─── Search query encoding ────────────────────────────────────────────────────


def encode_search_query(query: str) -> str:
    """Encode search-target query as +<urlsafe-base64-without-padding>.

    >>> encode_search_query('dialogguard')
    '+ZGlhbG9nZ3VhcmQ'
    """
    raw = base64.urlsafe_b64encode(query.encode("utf-8")).rstrip(b"=").decode("ascii")
    return "+" + raw


def decode_search_query(encoded: str) -> str:
    """Inverse. Strips '+' prefix; pads base64 back."""
    if not encoded.startswith("+"):
        return encoded
    body = encoded[1:]
    pad = "=" * (-len(body) % 4)
    return base64.urlsafe_b64decode((body + pad).encode("ascii")).decode("utf-8")


# ─── Targets resolution ──────────────────────────────────────────────────────


async def resolve_targets(
    api: TelegramAdsApi,
    target_type: TargetType,
    targets: list[str],
) -> str:
    """Resolve user-input targets to CSV of opaque IDs / encoded queries.

    - For 'channels' / 'bots': calls searchChannel API for each item.
    - For 'search': base64-encodes each query.

    Returns CSV string ready for form payload.
    """
    if not targets:
        return ""

    if target_type == "search":
        return ",".join(encode_search_query(q) for q in targets)

    if target_type in ("channels", "bots"):
        field = "channels" if target_type == "channels" else "bots"
        tokens: list[str] = []
        for raw in targets:
            r = await api.search_channel(raw, field=field)
            if not r.get("ok"):
                raise TelegramAdsApiError(
                    f"Could not resolve {raw!r}: {r.get('error', 'unknown error')}",
                    method="searchChannel",
                    raw_response=r,
                )
            ch = r.get("channel") or {}
            val = ch.get("val")
            if not val:
                raise TelegramAdsApiError(
                    f"searchChannel returned no val for {raw!r}",
                    method="searchChannel",
                    raw_response=r,
                )
            tokens.append(val)
        return ",".join(tokens)

    raise ValueError(f"Unknown target_type: {target_type}")


# ─── Create confirmation fingerprint ──────────────────────────────────────────


def create_confirmation_params(draft: CreateAdDraft) -> dict[str, Any]:
    """Stable fingerprint inputs for the create-ad confirmation token.

    The confirmation is invalidated if ANY of these change between issue and
    apply: title, text, promote_url, cpm, budget, target_type, targeting
    (targets + exclude_channels), and the creative options — show_picture,
    media (path/hash/type), and whether a custom emoji is present in the text.

    Single source of truth shared by the adapter and the test fake so the
    fingerprint can never silently drift between them.
    """
    return {
        "title": draft.title,
        "text": draft.text,
        "promote_url": draft.promote_url,
        "cpm": draft.cpm,
        "budget": draft.budget,
        "target_type": draft.target_type,
        "targets": list(draft.targets),
        "exclude_channels": list(draft.exclude_channels),
        "show_picture": draft.show_picture,
        "media_path": draft.media_path or "",
        "media_hash": media_hash(draft.media_path) if draft.media_path else "",
        "media_type": media_type_for_path(draft.media_path) if draft.media_path else "",
        "custom_emoji_detected": detect_custom_emoji(draft.text),
    }


# ─── Payload builders ────────────────────────────────────────────────────────


async def build_check_ad_post_payload(
    api: TelegramAdsApi,
    draft: CreateAdDraft,
    *,
    media_token: str | None = None,
) -> dict[str, Any]:
    """Build payload for checkAdPost (validation + preview only — no submit)."""
    channels_csv = ""
    bots_csv = ""
    queries_csv = ""
    exclude_channels_csv = ""
    if draft.target_type == "channels":
        channels_csv = await resolve_targets(api, "channels", draft.targets)
        # exclude_channels is paired with channel targeting only (live form config).
        exclude_channels_csv = await resolve_targets(api, "channels", draft.exclude_channels)
    elif draft.target_type == "bots":
        bots_csv = await resolve_targets(api, "bots", draft.targets)
    elif draft.target_type == "search":
        queries_csv = await resolve_targets(api, "search", draft.targets)

    return {
        "text": draft.text,
        "promote_url": draft.promote_url,
        "website_name": draft.website_name or "",
        "website_photo": draft.website_photo_url or "",
        "media": media_token or "",
        "target_type": draft.target_type,
        "placement": draft.placement or "",
        "channels": channels_csv,
        "bots": bots_csv,
        "search_queries": queries_csv,
        "exclude_channels": exclude_channels_csv,
    }


async def build_create_ad_payload(
    api: TelegramAdsApi,
    draft: CreateAdDraft,
    *,
    media_token: str | None = None,
) -> dict[str, Any]:
    """Build full payload for createAd (or saveAdDraft — same shape, different method)."""
    base = await build_check_ad_post_payload(api, draft, media_token=media_token)
    payload: dict[str, Any] = {
        "title": draft.title,
        **base,
        "ad_info": draft.ad_info or "",
        "cpm": _fmt_money(draft.cpm),
        "budget": _fmt_money(draft.budget),
        "daily_budget": _fmt_money(draft.daily_budget) if draft.daily_budget else "0",
        "active": 1 if draft.initial_active else 0,
        "views_per_user": draft.views_per_user,
        "picture": 1 if draft.show_picture else 0,
        "confirmed": "on",
    }

    # Schedule
    if draft.weekly_schedule is not None:
        payload["use_schedule"] = 1
        payload["schedule"] = encode_schedule(draft.weekly_schedule)
        if draft.schedule_tz:
            payload["schedule_tz_custom"] = 1
            payload["schedule_tz"] = draft.schedule_tz
        else:
            payload["schedule_tz_custom"] = 0

    # Schedule date/time (start/end)
    if draft.activate_at:
        date, _, time = draft.activate_at.partition("T")
        payload["ad_activate_date"] = date
        payload["ad_activate_time"] = time[:5] if time else ""
    if draft.deactivate_at:
        date, _, time = draft.deactivate_at.partition("T")
        payload["ad_deactivate_date"] = date
        payload["ad_deactivate_time"] = time[:5] if time else ""

    # Conversion event (Stars website ads)
    if draft.conversion_event_id:
        payload["conversion_event"] = draft.conversion_event_id

    return payload


def build_edit_ad_payload(draft: EditAdDraft) -> dict[str, Any]:
    """Payload for editAd (Save Changes).

    Excludes immutable fields (budget, target_type, channels/bots/queries,
    placement, confirmed). Schedule fields included if caller passes them
    explicitly via extra params (handled by adapter, not here).
    """
    return {
        "ad_id": draft.ad_id,
        "title": draft.title,
        "text": draft.text,
        "promote_url": draft.promote_url,
        "website_name": draft.website_name or "",
        "website_photo": draft.website_photo_url or "",
        "media": draft.media_token or "",
        "ad_info": draft.ad_info or "",
        "cpm": _fmt_money(draft.cpm),
        "daily_budget": _fmt_money(draft.daily_budget) if draft.daily_budget else "0",
        "active": 1 if draft.active else 0,
        "views_per_user": draft.views_per_user,
    }


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _fmt_money(v: float | None) -> str:
    """Format money for form payload. Strips unnecessary trailing zeros."""
    if v is None:
        return ""
    if v == 0:
        return "0"
    # Use enough precision for TON (5 decimals) but trim trailing zeros
    s = f"{float(v):.5f}".rstrip("0").rstrip(".")
    return s if s else "0"
