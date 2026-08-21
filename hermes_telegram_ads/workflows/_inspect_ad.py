"""Workflow: inspect_ad — detailed ad inspection.

Returns full ad detail, stats, computed metrics (CTR/CPC/CPA/runway),
share URL, and decline reason if applicable.

Safe — read-only. No mutations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hermes_telegram_ads.adapter import TelegramAdsAdapter


async def run_inspect_ad(
    adapter: TelegramAdsAdapter,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Inspect a single ad in detail.

    Parameters in *params*:
        ad_id (int, required): Ad ID to inspect.
    """
    ad_id = params.get("ad_id")
    if not ad_id:
        raise ValueError("'ad_id' is required for inspect_ad workflow")

    await adapter.ensure_logged_in()

    # 1. Full ad detail
    detail = await adapter.get_ad(ad_id)
    ad = detail.ad

    # 2. Stats
    stats = await adapter.get_ad_stats(ad_id)

    # 3. Share stats URL (best-effort)
    try:
        share_url = await adapter.get_share_stats_url(ad_id)
    except Exception:
        share_url = None

    # 4. Computed metrics
    metrics = _compute_metrics(ad, stats)

    # 5. Decline reason (if any)
    decline = None
    if hasattr(detail, "decline_reason") and detail.decline_reason is not None:
        decline = _serialize(detail.decline_reason)

    return {
        "ad": _serialize(detail),
        "stats": _serialize(stats),
        "share_stats_url": share_url,
        "metrics": metrics,
        "decline_reason": decline,
    }


# ─── Metrics computation ───────────────────────────────────────────────────────


def _compute_metrics(ad: Any, stats: Any) -> dict[str, Any]:
    """Derive performance metrics from raw ad + stats data."""
    views = getattr(ad, "views", 0) or 0
    clicks = getattr(ad, "clicks", 0) or 0
    spent = getattr(ad, "spent", 0.0) or 0.0
    budget = getattr(ad, "budget", 0.0) or 0.0
    actions = getattr(ad, "actions", 0) or 0

    ctr = (clicks / views * 100.0) if views > 0 else 0.0
    cpc = (spent / clicks) if clicks > 0 else None
    cpa = (spent / actions) if actions > 0 else None

    # Daily spend: approximate from stats monthly rows
    monthly = getattr(stats, "monthly", None) or []
    days = len(monthly) if monthly else 1
    daily_spend = spent / max(days, 1)

    budget_remaining = max(budget - spent, 0.0)
    budget_used_pct = (spent / budget * 100.0) if budget > 0 else 0.0

    return {
        "ctr": round(ctr, 2),
        "cpc": round(cpc, 2) if cpc is not None else None,
        "cpa": round(cpa, 2) if cpa is not None else None,
        "daily_spend_rate": round(daily_spend, 4),
        "budget_remaining": budget_remaining,
        "budget_used_pct": round(budget_used_pct, 1),
    }


def _serialize(obj: Any) -> Any:
    """Convert Pydantic model to JSON-safe dict, or return as-is."""
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if hasattr(obj, "dict"):
        return obj.dict()
    return str(obj)
