"""Post-action watch recipes.

The watcher never executes mutating Telegram Ads actions. But Hermes
*does* (after human approval). This module builds the read-only follow-up
:class:`WatchSpec` objects that *verify the observable result* of such an action
— e.g. after an approved ``change_cpm``, create a ``campaign_cpm`` watch that
confirms the new CPM is reflected and emits ``cpm_changed`` / ``post_action_verified``.

These functions are **pure**: they only construct ``WatchSpec`` objects. They do
not touch the adapter, the store, or perform any Telegram Ads action. Persist the
results via :meth:`TelegramAdsWatcherService.create_post_action_watches`.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal

from hermes_telegram_ads.watcher.models import WatchSpec, now_utc

PostAction = Literal[
    "create_ad",
    "edit_ad",
    "change_cpm",
    "add_to_budget",
    "withdraw_from_budget",
    "start_ad",
    "stop_ad",
    "delete_ad",
    "share_stats",
    "revoke_share_stats_url",
]

_ACCOUNT_KINDS = {"account_balance", "account_budget", "accounts_snapshot", "account_stats"}

# action -> (watch kinds to create, kinds that carry the verification expectation,
#            default expected dict merged under the caller's ``expected``).
_RECIPES: dict[str, dict[str, Any]] = {
    "create_ad": {
        "kinds": [
            "moderation_result",
            "campaign_status",
            "rejection_info",
            "campaign_budget",
            "campaign_stats",
        ],
        "verify_kinds": [],
        "default_expected": {},
    },
    "edit_ad": {
        "kinds": ["moderation_result", "campaign_status", "rejection_info", "campaign_detail"],
        "verify_kinds": ["campaign_detail"],
        "default_expected": {},
    },
    "change_cpm": {
        "kinds": ["campaign_cpm", "campaign_stats", "campaign_performance"],
        "verify_kinds": ["campaign_cpm"],
        "default_expected": {},
    },
    "add_to_budget": {
        "kinds": ["campaign_budget", "account_balance", "account_budget"],
        "verify_kinds": ["campaign_budget"],
        "default_expected": {},
    },
    "withdraw_from_budget": {
        "kinds": ["campaign_budget", "account_balance", "account_budget"],
        "verify_kinds": ["campaign_budget"],
        "default_expected": {},
    },
    "start_ad": {
        "kinds": ["campaign_status", "campaign_stats", "campaign_spend", "campaign_performance"],
        "verify_kinds": ["campaign_status"],
        "default_expected": {"status": "active"},
    },
    "stop_ad": {
        "kinds": ["campaign_status", "campaign_spend"],
        "verify_kinds": ["campaign_status"],
        "default_expected": {"status": "stopped"},
    },
    "delete_ad": {
        "kinds": ["campaign_detail", "campaign_list"],
        "verify_kinds": ["campaign_detail", "campaign_list"],
        "default_expected": {"missing": True},
    },
    "share_stats": {
        "kinds": ["share_stats_state"],
        "verify_kinds": [],
        "default_expected": {},
    },
    "revoke_share_stats_url": {
        "kinds": ["share_stats_state"],
        "verify_kinds": [],
        "default_expected": {},
    },
}

# Actions whose watch set is purely account-level (none today) would skip the
# ad_id requirement; every action below targets a campaign, so ad_id is required.
_REQUIRES_AD_ID = frozenset(_RECIPES) - frozenset()


def create_post_action_watches(
    action: PostAction,
    project_id: str,
    *,
    account_id: str | None = None,
    ad_id: int | str | None = None,
    expected: dict[str, Any] | None = None,
    interval_sec: int = 600,
    expires_in_sec: int | None = 172800,
) -> list[WatchSpec]:
    """Build (do not persist) the read-only follow-up watches for *action*.

    The verification watches carry ``thresholds['expected']`` so a later run can
    emit ``post_action_verified`` / ``post_action_not_verified``. All watches are
    enabled and expire after ``expires_in_sec`` (default 48h) so verification
    never polls forever.
    """
    recipe = _RECIPES.get(action)
    if recipe is None:
        raise ValueError(f"unknown post-action: {action!r}")
    if action in _REQUIRES_AD_ID and ad_id is None:
        raise ValueError(f"post-action {action!r} requires an ad_id")

    created = now_utc()
    expires_at = created + timedelta(seconds=expires_in_sec) if expires_in_sec else None
    final_expected = {**recipe["default_expected"], **(expected or {})}
    verify_kinds = set(recipe["verify_kinds"])

    specs: list[WatchSpec] = []
    for kind in recipe["kinds"]:
        is_account_kind = kind in _ACCOUNT_KINDS
        thresholds: dict[str, Any] = {}
        if kind in verify_kinds and final_expected:
            thresholds = {"expected": dict(final_expected)}
        specs.append(
            WatchSpec(
                project_id=project_id,
                kind=kind,  # type: ignore[arg-type]
                ad_id=None if is_account_kind else ad_id,
                account_id=account_id,
                enabled=True,
                interval_sec=interval_sec,
                thresholds=thresholds,
                notify=True,
                invoke_agent=False,
                created_by="system",
                created_at=created,
                updated_at=created,
                next_run_at=created,
                expires_at=expires_at,
            )
        )
    return specs


__all__ = ["PostAction", "create_post_action_watches"]
