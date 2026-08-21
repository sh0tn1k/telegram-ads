"""Telegram Ads tool → watcher coverage matrix.

This maps every real ``telegram_ads_*`` capability (read from the actual
registry :data:`hermes_telegram_ads.hermes_tools.TELEGRAM_ADS_TOOLS`) onto how
the watcher covers it. It is built *from the live registry* — not a hand-kept
list — so a new tool that ships without a coverage entry surfaces immediately
(``list_tool_coverage`` would carry it with a derived default and the coverage
tests assert every tool is classified).

Watcher support levels:

* ``direct_watch`` — a dedicated read-only watch kind continuously monitors it.
* ``snapshot_only`` — the watcher can capture it but emits limited/no diff events.
* ``post_action_verification`` — a *mutating* tool the watcher never executes; it
  only verifies the observable result after Hermes runs the approved
  action (see ``recipes.create_post_action_watches``).
* ``forbidden_in_watcher`` — mutating/sensitive; the watcher must never call it
  and there is no purely-observable post-action signal wired up.
* ``not_applicable`` — local planning / approval plumbing / navigation with no
  Telegram-Ads state to observe.

The watcher itself is read-only: ``forbidden_in_watcher`` and
``post_action_verification`` both mean "the watcher does NOT execute this".
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from hermes_telegram_ads.hermes_tools import MUTATING_TOOLS, TELEGRAM_ADS_TOOLS

Category = Literal[
    "system",
    "account",
    "campaign",
    "stats",
    "moderation",
    "targeting",
    "validation",
    "mutation",
    "share_stats",
    "reporting",
]

WatcherSupport = Literal[
    "direct_watch",
    "snapshot_only",
    "post_action_verification",
    "not_applicable",
    "forbidden_in_watcher",
]


class ToolCoverage(BaseModel):
    capability: str
    tool_names: list[str]
    category: Category
    watcher_support: WatcherSupport
    watch_kinds: list[str] = []
    event_types: list[str] = []
    notes: str | None = None


# Mutating tools that have an observable post-action result the watcher verifies.
POST_ACTION_TOOLS: frozenset[str] = frozenset(
    {
        "telegram_ads_create_ad",
        "telegram_ads_edit_ad",
        "telegram_ads_change_cpm",
        "telegram_ads_add_to_budget",
        "telegram_ads_withdraw_from_budget",
        "telegram_ads_start_ad",
        "telegram_ads_stop_ad",
        "telegram_ads_delete_ad",
        "telegram_ads_revoke_share_stats_url",
    }
)

# Per-tool coverage. Mutating tools are intentionally derived (forbidden /
# post-action) and only need a note here; read tools spell out watch_kinds +
# event_types. Anything missing falls back to a conservative derived default.
_OVERRIDES: dict[str, dict] = {
    # ── system / session (read) ──────────────────────────────────────────
    "telegram_ads_status": {
        "category": "system",
        "watcher_support": "direct_watch",
        "watch_kinds": ["tool_status", "login_state"],
        "event_types": ["tool_unavailable", "tool_available", "login_required", "login_restored"],
    },
    "telegram_ads_login_check": {
        "category": "system",
        "watcher_support": "direct_watch",
        "watch_kinds": ["login_state", "tool_status"],
        "event_types": ["login_required", "login_restored"],
    },
    "telegram_ads_ensure_login": {
        "category": "system",
        "watcher_support": "snapshot_only",
        "watch_kinds": ["login_state"],
    },
    "telegram_ads_login_wait": {
        "category": "system",
        "watcher_support": "snapshot_only",
        "watch_kinds": ["login_state"],
    },
    "telegram_ads_login_assist": {"category": "system", "watcher_support": "not_applicable"},
    "telegram_ads_login_start": {
        "category": "system",
        "watcher_support": "forbidden_in_watcher",
        "notes": "Sensitive session action; the watcher only detects login_state read-only.",
    },
    "telegram_ads_login_submit_phone": {
        "category": "system",
        "watcher_support": "forbidden_in_watcher",
        "notes": "Sensitive session action; the watcher never submits phone/OTP.",
    },
    "telegram_ads_login_from_env": {
        "category": "system",
        "watcher_support": "forbidden_in_watcher",
        "notes": "Operator env-phone login; watcher only observes login_state read-only.",
    },
    "telegram_ads_login_submit_code": {
        "category": "system",
        "watcher_support": "forbidden_in_watcher",
        "notes": "Sensitive session action; the watcher never submits OTP.",
    },
    "telegram_ads_open_dashboard": {"category": "system", "watcher_support": "not_applicable"},
    "telegram_ads_current_page": {"category": "system", "watcher_support": "not_applicable"},
    "telegram_ads_save_screenshot": {"category": "system", "watcher_support": "not_applicable"},
    "telegram_ads_get_browser_profile_info": {
        "category": "system",
        "watcher_support": "snapshot_only",
        "watch_kinds": ["tool_status"],
    },
    "telegram_ads_recover_browser_session": {
        "category": "system",
        "watcher_support": "not_applicable",
        "notes": "Recovery action; watcher reports tool_unavailable/tool_available instead.",
    },
    # ── accounts ─────────────────────────────────────────────────────────
    "telegram_ads_list_accounts": {
        "category": "account",
        "watcher_support": "direct_watch",
        "watch_kinds": ["accounts_snapshot"],
        "event_types": ["account_added", "account_removed", "account_balance_low"],
    },
    "telegram_ads_snapshot_accounts": {
        "category": "account",
        "watcher_support": "direct_watch",
        "watch_kinds": ["accounts_snapshot"],
        "event_types": ["account_added", "account_removed", "account_balance_low"],
    },
    "telegram_ads_current_account": {
        "category": "account",
        "watcher_support": "snapshot_only",
        "watch_kinds": ["accounts_snapshot"],
    },
    "telegram_ads_choose_account": {
        "category": "account",
        "watcher_support": "not_applicable",
        "notes": "Switches the active cabinet (global side effect); the watcher does not drive it.",
    },
    "telegram_ads_get_account_budget": {
        "category": "account",
        "watcher_support": "direct_watch",
        "watch_kinds": ["account_balance", "account_budget"],
        "event_types": ["account_balance_low", "account_budget_changed", "budget_low"],
    },
    # ── campaign / ad read ───────────────────────────────────────────────
    "telegram_ads_list_ads": {
        "category": "campaign",
        "watcher_support": "direct_watch",
        "watch_kinds": ["campaign_list"],
        "event_types": ["campaign_added", "campaign_removed", "ad_deleted_or_missing"],
    },
    "telegram_ads_get_ad": {
        "category": "campaign",
        "watcher_support": "direct_watch",
        "watch_kinds": [
            "campaign_detail",
            "campaign_status",
            "moderation_result",
            "campaign_budget",
            "campaign_spend",
            "campaign_cpm",
        ],
        "event_types": [
            "ad_status_changed",
            "ad_approved",
            "ad_declined",
            "ad_started",
            "ad_stopped",
            "campaign_status_changed",
            "cpm_changed",
            "budget_changed",
            "budget_low",
        ],
    },
    "telegram_ads_get_ad_creative": {
        "category": "campaign",
        "watcher_support": "snapshot_only",
        "watch_kinds": ["campaign_detail"],
    },
    "telegram_ads_get_ad_budget_status": {
        "category": "campaign",
        "watcher_support": "direct_watch",
        "watch_kinds": ["campaign_budget", "campaign_spend", "campaign_cpm"],
        "event_types": ["budget_low", "spend_threshold_reached", "cpm_changed", "budget_changed"],
    },
    "telegram_ads_get_ad_targeting": {
        "category": "targeting",
        "watcher_support": "direct_watch",
        "watch_kinds": ["campaign_targeting", "targeting_lock_state"],
        "event_types": ["targeting_changed", "targeting_locked", "targeting_unlocked"],
    },
    "telegram_ads_get_rejection_info": {
        "category": "moderation",
        "watcher_support": "direct_watch",
        "watch_kinds": ["rejection_info", "moderation_result"],
        "event_types": ["ad_declined", "rejection_reason_changed"],
    },
    "telegram_ads_explain_rejection": {
        "category": "moderation",
        "watcher_support": "snapshot_only",
        "watch_kinds": ["rejection_info"],
        "notes": "Deterministic explanation derived from the decline reason.",
    },
    # ── stats / reports / share ──────────────────────────────────────────
    "telegram_ads_get_ad_stats": {
        "category": "stats",
        "watcher_support": "direct_watch",
        "watch_kinds": ["campaign_stats", "campaign_performance"],
        "event_types": ["stats_changed", "stats_anomaly", "ctr_drop", "delivery_stalled"],
    },
    "telegram_ads_download_report": {
        "category": "reporting",
        "watcher_support": "snapshot_only",
        "watch_kinds": ["campaign_reports"],
        "event_types": ["report_available", "report_changed"],
        "notes": "Watcher tracks report availability via stats csv_url; it never downloads CSVs on a timer.",
    },
    "telegram_ads_get_share_stats_url": {
        "category": "share_stats",
        "watcher_support": "direct_watch",
        "watch_kinds": ["share_stats_state"],
        "event_types": ["share_stats_available", "share_stats_unavailable", "share_stats_changed"],
        "notes": "Read-only; the watcher stores only availability + a URL hash, never the token-bearing URL.",
    },
    # ── pixel events (read) ──────────────────────────────────────────────
    "telegram_ads_list_events": {
        "category": "campaign",
        "watcher_support": "snapshot_only",
        "notes": "Pixel conversion events (Stars cabinets); snapshot only, no dedicated watch kind.",
    },
    "telegram_ads_get_event_log": {
        "category": "campaign",
        "watcher_support": "snapshot_only",
        "notes": "Pixel event activity log; snapshot only.",
    },
    "telegram_ads_get_pixel_snippet": {
        "category": "campaign",
        "watcher_support": "not_applicable",
        "notes": "Static install snippet; nothing to monitor.",
    },
    "telegram_ads_get_ad_events": {
        "category": "campaign",
        "watcher_support": "not_applicable",
        "notes": "adapter_missing: per-ad event feed is not implemented; use list_events/get_event_log.",
    },
    # ── draft / validation ───────────────────────────────────────────────
    "telegram_ads_validate_ad": {
        "category": "validation",
        "watcher_support": "direct_watch",
        "watch_kinds": ["draft_validation"],
        "event_types": ["draft_validation_passed", "draft_validation_failed"],
        "notes": "Validates stored/provided draft data only; never creates or edits an ad.",
    },
    "telegram_ads_preview_ad": {
        "category": "validation",
        "watcher_support": "snapshot_only",
        "watch_kinds": ["draft_validation"],
    },
    "telegram_ads_save_ad_draft": {
        "category": "validation",
        "watcher_support": "not_applicable",
        "notes": "Writes a server-side draft; the watcher does not drive draft writes.",
    },
    "telegram_ads_prepare_ad_draft": {
        "category": "validation",
        "watcher_support": "not_applicable",
        "notes": "Writes a server-side draft; the watcher does not drive draft writes.",
    },
    "telegram_ads_upload_media": {
        "category": "validation",
        "watcher_support": "not_applicable",
        "notes": "Uploads media for a draft; the watcher does not drive draft writes.",
    },
    "telegram_ads_duplicate_ad": {
        "category": "validation",
        "watcher_support": "not_applicable",
        "notes": "Clones an ad into a draft; the watcher does not drive draft writes.",
    },
    "telegram_ads_estimate_cpm": {"category": "validation", "watcher_support": "not_applicable"},
    "telegram_ads_prepare_campaign_from_brief": {
        "category": "validation",
        "watcher_support": "not_applicable",
    },
    "telegram_ads_prepare_copy_variants": {
        "category": "validation",
        "watcher_support": "not_applicable",
    },
    "telegram_ads_prepare_targeting": {"category": "validation", "watcher_support": "not_applicable"},
    # ── approval plumbing ────────────────────────────────────────────────
    "telegram_ads_prepare_approval_request": {
        "category": "system",
        "watcher_support": "not_applicable",
    },
    "telegram_ads_get_pending_confirmations": {
        "category": "system",
        "watcher_support": "not_applicable",
    },
    "telegram_ads_cancel_confirmation": {"category": "system", "watcher_support": "not_applicable"},
}

# Watch kinds / events attached to a post-action verification for each mutating tool.
_POST_ACTION_DETAIL: dict[str, dict] = {
    "telegram_ads_create_ad": {
        "watch_kinds": [
            "moderation_result",
            "campaign_status",
            "rejection_info",
            "campaign_budget",
            "campaign_stats",
        ],
        "event_types": [
            "ad_approved",
            "ad_declined",
            "ad_status_changed",
            "post_action_verified",
            "post_action_not_verified",
        ],
    },
    "telegram_ads_edit_ad": {
        "watch_kinds": ["moderation_result", "campaign_status", "rejection_info", "campaign_detail"],
        "event_types": [
            "ad_approved",
            "ad_declined",
            "campaign_status_changed",
            "post_action_verified",
            "post_action_not_verified",
        ],
    },
    "telegram_ads_change_cpm": {
        "watch_kinds": ["campaign_cpm", "campaign_stats", "campaign_performance"],
        "event_types": ["cpm_changed", "post_action_verified", "post_action_not_verified"],
    },
    "telegram_ads_add_to_budget": {
        "watch_kinds": ["campaign_budget", "account_balance", "account_budget"],
        "event_types": [
            "budget_changed",
            "account_budget_changed",
            "post_action_verified",
            "post_action_not_verified",
        ],
    },
    "telegram_ads_withdraw_from_budget": {
        "watch_kinds": ["campaign_budget", "account_balance", "account_budget"],
        "event_types": [
            "budget_changed",
            "account_budget_changed",
            "post_action_verified",
            "post_action_not_verified",
        ],
    },
    "telegram_ads_start_ad": {
        "watch_kinds": ["campaign_status", "campaign_stats", "campaign_spend", "campaign_performance"],
        "event_types": ["ad_started", "delivery_stalled", "spend_threshold_reached", "post_action_verified"],
    },
    "telegram_ads_stop_ad": {
        "watch_kinds": ["campaign_status", "campaign_spend"],
        "event_types": ["ad_stopped", "post_action_verified", "post_action_not_verified"],
    },
    "telegram_ads_delete_ad": {
        "watch_kinds": ["campaign_detail", "campaign_list"],
        "event_types": ["ad_deleted_or_missing", "post_action_verified"],
    },
    "telegram_ads_revoke_share_stats_url": {
        "watch_kinds": ["share_stats_state"],
        "event_types": ["share_stats_changed", "share_stats_unavailable", "post_action_verified"],
    },
}


def _build_one(spec) -> ToolCoverage:  # noqa: ANN001
    name = spec.name
    override = _OVERRIDES.get(name)
    if override is not None:
        return ToolCoverage(
            capability=name,
            tool_names=[name],
            category=override["category"],
            watcher_support=override["watcher_support"],
            watch_kinds=override.get("watch_kinds", []),
            event_types=override.get("event_types", []),
            notes=override.get("notes"),
        )

    if spec.mutating:
        if name in POST_ACTION_TOOLS:
            detail = _POST_ACTION_DETAIL.get(name, {})
            return ToolCoverage(
                capability=name,
                tool_names=[name],
                category="mutation",
                watcher_support="post_action_verification",
                watch_kinds=detail.get("watch_kinds", []),
                event_types=detail.get("event_types", []),
                notes=(
                    "Watcher does NOT execute this action. After Hermes runs the "
                    "approved action, the watcher verifies the observable result via the "
                    "listed read-only watch kinds."
                ),
            )
        return ToolCoverage(
            capability=name,
            tool_names=[name],
            category="mutation",
            watcher_support="forbidden_in_watcher",
            notes="Mutating/sensitive — the watcher never executes this action.",
        )

    # Conservative default for any unmapped read tool.
    return ToolCoverage(
        capability=name,
        tool_names=[name],
        category="system",
        watcher_support="not_applicable",
        notes="Unclassified read tool — review and add an explicit coverage entry.",
    )


_COVERAGE: list[ToolCoverage] = [_build_one(s) for s in TELEGRAM_ADS_TOOLS]
_COVERAGE_BY_NAME: dict[str, ToolCoverage] = {c.capability: c for c in _COVERAGE}


def list_tool_coverage() -> list[ToolCoverage]:
    """Full coverage matrix, one entry per real ``telegram_ads_*`` tool."""
    return list(_COVERAGE)


def direct_watch_kinds() -> list[str]:
    """Sorted unique watch kinds classified ``direct_watch`` on the live registry."""
    kinds: set[str] = set()
    for coverage in _COVERAGE:
        if coverage.watcher_support == "direct_watch":
            kinds.update(coverage.watch_kinds)
    return sorted(kinds)


def post_action_watch_kinds() -> list[str]:
    """Sorted unique watch kinds used for post-action verification."""
    kinds: set[str] = set()
    for coverage in _COVERAGE:
        if coverage.watcher_support == "post_action_verification":
            kinds.update(coverage.watch_kinds)
    return sorted(kinds)


def get_tool_coverage(capability: str) -> ToolCoverage | None:
    return _COVERAGE_BY_NAME.get(capability)


def mutating_tool_names() -> frozenset[str]:
    """The actual mutating tool names from the live registry."""
    return MUTATING_TOOLS


def assert_no_mutating_tools_in_watcher(service_or_adapter: object) -> None:
    """Fail loudly if a watcher service/adapter exposes a mutating Telegram Ads
    method as a callable.

    The watcher is read-only. This guard walks the real mutating tool surface
    (``MUTATING_TOOLS``) plus the underlying adapter mutation method names and
    asserts that the watcher *service* never binds them. (A wrapped adapter may
    still define those methods — that's expected; the watcher just must not call
    them. We assert the service object itself has no such attributes.)
    """
    from hermes_telegram_ads.watcher.service import TelegramAdsWatcherService

    adapter_mutation_methods = {
        "create_ad",
        "edit_ad",
        "change_cpm",
        "change_status",
        "add_to_budget",
        "withdraw_from_budget",
        "delete_ad",
        "delete_event",
        "create_event",
        "revoke_share_stats_url",
        "save_ad_draft",
        "create_similar_draft",
        "upload_media",
    }
    if isinstance(service_or_adapter, TelegramAdsWatcherService):
        leaked = [m for m in adapter_mutation_methods if hasattr(service_or_adapter, m)]
        if leaked:
            raise AssertionError(f"Watcher service must not expose mutating methods: {sorted(leaked)}")
        # Also ensure the coverage matrix marks every mutating tool non-executable.
    bad = [
        c.capability
        for c in _COVERAGE
        if c.capability in MUTATING_TOOLS
        and c.watcher_support not in ("forbidden_in_watcher", "post_action_verification")
    ]
    if bad:
        raise AssertionError(f"Mutating tools must not be directly watchable: {bad}")


__all__ = [
    "POST_ACTION_TOOLS",
    "Category",
    "ToolCoverage",
    "WatcherSupport",
    "assert_no_mutating_tools_in_watcher",
    "direct_watch_kinds",
    "get_tool_coverage",
    "list_tool_coverage",
    "mutating_tool_names",
    "post_action_watch_kinds",
]
