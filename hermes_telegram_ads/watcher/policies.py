"""Deterministic classification policy for the watcher.

Pure functions only — no I/O, no Telegram Ads calls, no AI. This is where the
"what does this status/threshold *mean*" knowledge lives so :mod:`diff` stays a
thin orchestration layer and everything here is trivially unit-testable.

Telegram Ads surfaces human status labels ("Active", "In Review", "Declined",
"On Hold", "Stopped", ...). Real labels drift and the internal API sometimes
reports lowercase/abbreviated variants, so we normalise to a small canonical set
rather than matching exact strings.
"""

from __future__ import annotations

from typing import Literal

NormalizedStatus = Literal["active", "declined", "stopped", "pending", "unknown"]

# Substring markers (matched case-insensitively) → canonical bucket. Ordered by
# precedence within each bucket; buckets are checked in the order below so the
# most decisive signal wins (declined/rejected beats everything else).
_DECLINED_MARKERS = ("declin", "reject", "disapprove", "refus", "denied")
_ACTIVE_MARKERS = ("active", "running", "approved", "live", "ongoing")
_STOPPED_MARKERS = ("stopped", "paused", "on hold", "onhold", "hold", "inactive", "disabled")
_PENDING_MARKERS = ("review", "pending", "moderation", "checking", "processing", "submitted")


def normalize_status(raw: str | None) -> NormalizedStatus:
    """Map a raw Telegram Ads status label to a canonical bucket.

    Precedence: declined > pending > stopped > active > unknown. Declined wins
    outright (it is the most consequential). Pending is checked before stopped/
    active so "In Review" is never misread as anything else. Unknown/blank →
    ``"unknown"`` (never silently coerced to a real state).
    """
    if not raw:
        return "unknown"
    s = raw.strip().lower()
    if any(m in s for m in _DECLINED_MARKERS):
        return "declined"
    if any(m in s for m in _PENDING_MARKERS):
        return "pending"
    if any(m in s for m in _STOPPED_MARKERS):
        return "stopped"
    if any(m in s for m in _ACTIVE_MARKERS):
        return "active"
    return "unknown"


# Severity + recommended-agent-action per event type. ``recommended_agent_action``
# is an advisory, secret-free hint for Hermes; it never triggers anything
# on its own and never implies a mutating Telegram Ads action by the watcher.
_EVENT_SEVERITY: dict[str, str] = {
    # tool / session
    "tool_unavailable": "critical",
    "tool_available": "info",
    "login_required": "critical",
    "login_restored": "info",
    # accounts
    "account_added": "info",
    "account_removed": "warning",
    "account_balance_low": "critical",
    "account_budget_changed": "info",
    "account_stats_changed": "info",
    # campaigns / lifecycle
    "campaign_added": "info",
    "campaign_removed": "warning",
    "campaign_status_changed": "info",
    "ad_status_changed": "info",
    "ad_approved": "info",
    "ad_started": "info",
    "ad_stopped": "warning",
    "ad_declined": "warning",
    "ad_deleted_or_missing": "warning",
    # moderation / targeting
    "rejection_reason_changed": "warning",
    "targeting_changed": "warning",
    "targeting_locked": "info",
    "targeting_unlocked": "info",
    # budget / spend / cpm
    "budget_low": "warning",
    "budget_changed": "info",
    "spend_threshold_reached": "warning",
    "cpm_changed": "info",
    # stats / performance
    "stats_changed": "info",
    "stats_anomaly": "warning",
    "ctr_drop": "warning",
    "cvr_drop": "warning",
    "cpa_above_threshold": "warning",
    "delivery_stalled": "warning",
    # reports / share stats
    "report_available": "info",
    "report_changed": "info",
    "share_stats_available": "info",
    "share_stats_unavailable": "warning",
    "share_stats_changed": "info",
    # draft validation
    "draft_validation_failed": "warning",
    "draft_validation_passed": "info",
    # post-action verification
    "post_action_verified": "info",
    "post_action_not_verified": "warning",
    # errors
    "watch_error": "warning",
}

_RECOMMENDED_ACTION: dict[str, str] = {
    "tool_unavailable": "recover_browser_session_or_request_restart",
    "tool_available": "resume_normal_operation",
    "login_required": "ask_human_to_login_in_browser",
    "login_restored": "resume_normal_operation",
    "account_added": "review_new_cabinet",
    "account_removed": "review_missing_cabinet",
    "account_balance_low": "consider_account_topup_with_human_approval",
    "account_budget_changed": "note_account_budget_change",
    "account_stats_changed": "review_account_stats",
    "campaign_added": "review_new_campaign",
    "campaign_removed": "confirm_campaign_was_deleted_intentionally",
    "campaign_status_changed": "review_ad_status_change",
    "ad_status_changed": "review_ad_status_change",
    "ad_approved": "notify_owner_ad_live",
    "ad_started": "notify_owner_ad_running",
    "ad_stopped": "review_why_ad_stopped",
    "ad_declined": "review_decline_reason_then_decide_resubmit",
    "ad_deleted_or_missing": "confirm_deletion_or_investigate",
    "rejection_reason_changed": "re_read_decline_reason",
    "targeting_changed": "review_targeting_change",
    "targeting_locked": "note_targeting_is_immutable",
    "targeting_unlocked": "review_targeting_state",
    "budget_low": "consider_topping_up_ad_budget_with_human_approval",
    "budget_changed": "note_budget_change",
    "spend_threshold_reached": "review_spend_pace_and_decide",
    "cpm_changed": "note_cpm_change",
    "stats_changed": "review_campaign_stats",
    "stats_anomaly": "inspect_campaign_stats",
    "ctr_drop": "inspect_creative_and_targeting",
    "cvr_drop": "inspect_funnel_and_landing",
    "cpa_above_threshold": "review_cost_per_action",
    "delivery_stalled": "check_delivery_and_budget",
    "report_available": "fetch_report_with_human_approval",
    "report_changed": "refetch_report",
    "share_stats_available": "share_stats_link_ready",
    "share_stats_unavailable": "share_stats_link_missing",
    "share_stats_changed": "share_stats_link_rotated",
    "draft_validation_failed": "fix_draft_then_revalidate",
    "draft_validation_passed": "draft_ready_for_human_approval",
    "post_action_verified": "approved_action_confirmed",
    "post_action_not_verified": "approved_action_not_yet_reflected_keep_watching",
    "watch_error": "inspect_watch_error_then_retry",
}


def severity_for(event_type: str) -> str:
    return _EVENT_SEVERITY.get(event_type, "info")


def recommended_action_for(event_type: str) -> str | None:
    return _RECOMMENDED_ACTION.get(event_type)


def is_session_problem(exc: BaseException) -> bool:
    """True when an adapter exception means a human must (re)log-in.

    Recognises the package's ``LoginRequiredError`` / ``SessionExpiredError`` by
    type, and otherwise sniffs the message for login/session-expiry phrasing so a
    generically-raised auth error still maps to ``login_required`` rather than
    ``watch_error``.
    """
    try:
        from hermes_telegram_ads.errors import LoginRequiredError

        if isinstance(exc, LoginRequiredError):
            return True
    except Exception:  # pragma: no cover - errors module always importable
        pass
    blob = f"{getattr(exc, 'message', '')} {exc}".lower()
    markers = (
        "not logged in",
        "login required",
        "session expired",
        "session has expired",
        "log in",
        "re-login",
        "relogin",
        "unauthorized",
    )
    return any(m in blob for m in markers)
