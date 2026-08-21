"""Workflow: snapshot — full Telegram Ads account overview.

Supports multi-account iteration (all / current / selected / project_default).
Returns per-account breakdown + accumulated totals.
Safe — read-only. No drafts, no mutations.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hermes_telegram_ads.adapter import TelegramAdsAdapter
    from hermes_telegram_ads.types import AdSummary

logger = logging.getLogger(__name__)

# ─── Constants ──────────────────────────────────────────────────────────────────

VALID_SCOPES = frozenset({"all", "current", "selected", "project_default"})

# Snapshot timestamps are emitted in UTC. Surfaced explicitly in the result
# envelope (`snapshot_timezone`) so downstream consumers don't have to guess
# from the timestamp's offset suffix.
SNAPSHOT_TIMEZONE = "UTC"

# Known statuses for classification. Unknown is NOT classified as stopped.
STATUS_ACTIVE = "Active"
STATUS_STOPPED = frozenset({"Stopped", "On Hold"})
STATUS_DECLINED = "Declined"
STATUS_LIMITED = "Limited"
STATUS_UNKNOWN = "Unknown"

# Data quality labels
DQ_COMPLETE = "complete"
DQ_PARTIAL = "partial"
DQ_UNRELIABLE = "unreliable"
DQ_UNAVAILABLE = "unavailable"

# Error codes
ERROR_ACCOUNT_SCAN_FAILED = "ACCOUNT_SCAN_FAILED"


# ─── Entry point ────────────────────────────────────────────────────────────────


async def run_snapshot(
    adapter: TelegramAdsAdapter,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Take a snapshot of Telegram Ads accounts.

    Parameters in *params*:
        account_scope (str, optional): Which accounts to scan.
            "all" (default) — all cabinets.
            "current" — only the currently selected cabinet.
            "selected" — specific cabinet by account_token.
            "project_default" — project-defined default (same as current for now).
        account_token (str, optional): Required when scope="selected".
    """
    now = datetime.now(tz=UTC)

    # 1. Login
    await adapter.ensure_logged_in()

    # 2. List all accounts
    all_accounts = await adapter.list_accounts()
    accounts_found = len(all_accounts)

    # 3. Resolve scope
    scope = params.get("account_scope", "all")
    if scope not in VALID_SCOPES:
        scope = "all"

    accounts_to_scan = _resolve_scope(scope, all_accounts, params)

    # 4. Scan each account (using rich parse_ads)
    per_account: list[dict[str, Any]] = []
    total_acc = _TotalAccumulator()
    analyzed = 0
    skipped = 0

    for acct in accounts_to_scan:
        try:
            await adapter.choose_account(acct.account_token)
            current = await adapter.get_current_account()

            # Use parse_ads() instead of list_ads() for rich metadata
            parse_result = await adapter.parse_ads()
            ads_raw: list[AdSummary] = parse_result["ads"]
            data_quality = parse_result["data_quality"]
            parse_warnings: list[str] = parse_result["warnings"]
            budget_column_label: str = parse_result.get("budget_column_label", "")

            budget = await adapter.get_account_budget()

            # Events: best-effort
            try:
                events = await adapter.list_events()
                events_count = len(events)
            except Exception:
                events_count = -1

            entry = _build_account_entry(
                current,
                ads_raw,
                budget,
                events_count,
                data_quality,
                parse_warnings,
                budget_column_label,
            )
            # Combine parser data_quality with anomaly detection
            anomalies = entry.get("warnings", [])
            entry["data_quality"] = _compute_entry_data_quality(
                data_quality, anomalies
            )
            per_account.append(entry)
            total_acc.add(entry)
            analyzed += 1

        except Exception as exc:
            logger.warning("Snapshot: skipped account %s: %s", acct.title, exc)
            per_account.append(_build_skipped_entry(acct, exc))
            skipped += 1

    # ── ACCOUNT_SCAN_FAILED short-circuit ──────────────────────────────
    # If no account could be analyzed, the workflow MUST NOT return a
    # "successful" result with zero metrics. Zero metrics are ambiguous:
    # they could mean "real zero" (account is empty) OR "scan failed"
    # (we have no idea). We resolve the ambiguity by making
    # accounts_analyzed == 0 an explicit, structured failure.
    #
    # This only fires when list_accounts() returned >= 1 account but
    # every choose_account / parse_ads / get_account_budget call failed.
    # Login and list_accounts errors are handled separately by
    # run_workflow's _error_result classifier (LOGIN_REQUIRED, API_ERROR,
    # WORKFLOW_ERROR) and must NOT be folded into ACCOUNT_SCAN_FAILED.
    if analyzed == 0:
        scope_warning = None
        if scope != "all" and per_account:
            first_title = (
                per_account[0].get("account", {}).get("title", "unknown")
            )
            scope_warning = (
                f"Snapshot is scoped to one account ({first_title!r}), "
                f"not all {accounts_found} Telegram Ads accounts."
            )
        return {
            "ok": False,
            "error": ERROR_ACCOUNT_SCAN_FAILED,
            "message": "No reliable ads data collected.",
            "snapshot_timestamp": now.isoformat(),
            "snapshot_timezone": SNAPSHOT_TIMEZONE,
            "snapshot_date": now.strftime("%Y-%m-%d"),
            "account_scope": scope,
            "scope_label": _scope_label(scope, analyzed, accounts_found),
            "accounts_found": accounts_found,
            "accounts_analyzed": 0,
            "accounts_skipped": skipped,
            "accounts": per_account,
            "total": None,
            "metrics": None,
            "data_quality": DQ_UNAVAILABLE,
            "warnings": _build_failure_warnings(
                accounts_found, skipped, per_account, scope_warning
            ),
        }

    # 5. Scope warning
    scope_warning = None
    if scope != "all" and per_account:
        first_title = (
            per_account[0].get("account", {}).get("title", "unknown")
        )
        scope_warning = (
            f"Snapshot is scoped to one account ({first_title!r}), "
            f"not all {accounts_found} Telegram Ads accounts."
        )

    # 6. Assemble result
    result = {
        "snapshot_timestamp": now.isoformat(),
        "snapshot_timezone": SNAPSHOT_TIMEZONE,
        "snapshot_date": now.strftime("%Y-%m-%d"),
        "account_scope": scope,
        "scope_label": _scope_label(scope, analyzed, accounts_found),
        "accounts_found": accounts_found,
        "accounts_analyzed": analyzed,
        "accounts_skipped": skipped,
        "accounts": per_account,
        "total": total_acc.summary(),
        "warnings": _global_warnings(scope_warning, total_acc),
    }

    if scope_warning:
        result["warnings"].insert(0, scope_warning)

    return result


# ─── Scope resolution ───────────────────────────────────────────────────────────


def _resolve_scope(
    scope: str,
    all_accounts: list[Any],
    params: dict[str, Any],
) -> list[Any]:
    """Return the subset of accounts to scan based on scope."""
    if scope == "all":
        return all_accounts

    if scope == "current":
        return all_accounts[:1] if all_accounts else []

    if scope == "selected":
        token = params.get("account_token")
        if token:
            matching = [a for a in all_accounts if a.account_token == token]
            if matching:
                return matching
        # Fallback: first account
        return all_accounts[:1] if all_accounts else []

    # project_default — same as current for now
    return all_accounts[:1] if all_accounts else []


# ─── Account entry builders ─────────────────────────────────────────────────────


def _build_account_entry(
    account: Any,
    ads_raw: list[AdSummary],
    budget: Any,
    events_count: int,
    data_quality: str,
    parse_warnings: list[str],
    budget_column_label: str,
) -> dict[str, Any]:
    """Build a per-account snapshot entry."""
    campaigns = _classify_campaigns(ads_raw)
    performance = _compute_performance(ads_raw)
    budget_data = _serialize_budget(budget)

    # Anomaly detection
    anomalies = _detect_anomalies(campaigns, performance)
    all_warnings = list(parse_warnings) + anomalies

    return {
        "account": {
            "title": getattr(account, "title", "unknown"),
            "account_type": getattr(account, "account_type", ""),
            "currency": getattr(account, "currency", ""),
            "balance": getattr(account, "balance", 0.0),
        },
        "budget": budget_data,
        "campaigns": campaigns,
        "performance": performance,
        "data_quality": data_quality,
        "budget_column_label": budget_column_label,
        "events_count": events_count,
        "warnings": all_warnings,
    }


def _build_skipped_entry(account: Any, exc: Exception) -> dict[str, Any]:
    """Build an entry for an account that could not be scanned."""
    return {
        "account": {
            "title": getattr(account, "title", "unknown"),
            "account_type": "",
            "currency": "",
            "balance": 0.0,
        },
        "budget": {"balance": 0.0, "currency": "", "last_transactions": []},
        "campaigns": {
            "total": 0, "active": 0, "stopped": 0,
            "declined": 0, "limited": 0, "unknown": 0,
        },
        "performance": {
            "impressions": 0, "clicks": 0, "ctr": 0.0,
            "spent_total": 0.0, "budget_column_total": 0.0,
        },
        "data_quality": "unreliable",
        "budget_column_label": "",
        "events_count": 0,
        "warnings": [f"Account scan failed: {exc}"],
    }


# ─── Classification ─────────────────────────────────────────────────────────────


def _classify_campaigns(ads_raw: list[AdSummary]) -> dict[str, int]:
    """Break down ads by status. Unknown is NOT counted as stopped."""
    total = len(ads_raw)
    active = 0
    stopped = 0
    declined = 0
    limited = 0
    unknown = 0

    for ad in ads_raw:
        status = getattr(ad, "status", "")
        if status == STATUS_ACTIVE:
            active += 1
        elif status in STATUS_STOPPED:
            stopped += 1
        elif status == STATUS_DECLINED:
            declined += 1
        elif status == STATUS_LIMITED:
            limited += 1
        elif status == STATUS_UNKNOWN:
            unknown += 1
        else:
            # Truly unknown/unexpected status — count as unknown, not stopped
            unknown += 1

    return {
        "total": total,
        "active": active,
        "stopped": stopped,
        "declined": declined,
        "limited": limited,
        "unknown": unknown,
    }


def _compute_performance(ads_raw: list[AdSummary]) -> dict[str, Any]:
    """Aggregate spent_total and budget_column_total across all ads in account.

    spent_total = sum of actual spend (spent column)
    budget_column_total = sum of budget column (whatever Telegram Ads calls it)
    """
    impressions = 0
    clicks = 0
    spent_total = 0.0
    budget_column_total = 0.0

    for ad in ads_raw:
        impressions += getattr(ad, "views", 0) or 0
        clicks += getattr(ad, "clicks", 0) or 0
        spent_total += getattr(ad, "spent", 0.0) or 0.0
        budget_column_total += getattr(ad, "budget", 0.0) or 0.0

    ctr = (clicks / impressions * 100.0) if impressions > 0 else 0.0

    return {
        "impressions": impressions,
        "clicks": clicks,
        "ctr": round(ctr, 2),
        "spent_total": round(spent_total, 4),
        "budget_column_total": round(budget_column_total, 4),
    }


def _serialize_budget(budget: Any) -> dict[str, Any]:
    """Convert budget object to compact dict."""
    txns = []
    if hasattr(budget, "transactions") and budget.transactions:
        for t in budget.transactions[:5]:
            txns.append(_serialize(t))

    return {
        "balance": getattr(budget, "balance", 0.0),
        "currency": getattr(budget, "currency", ""),
        "last_transactions": txns,
    }


# ─── Anomaly detection ──────────────────────────────────────────────────────────


def _detect_anomalies(
    campaigns: dict[str, int],
    performance: dict[str, Any],
) -> list[str]:
    """Detect parser-level anomalies and return warning codes."""
    warnings: list[str] = []

    # CTR > 0 but clicks == 0 → data mixup
    if performance["ctr"] > 0 and performance["clicks"] == 0:
        warnings.append("PARSE_ANOMALY_CTR_CLICKS_MISMATCH")

    # Unknown statuses → parser couldn't identify statuses
    unknown = campaigns.get("unknown", 0)
    total = campaigns.get("total", 0)
    if unknown > 0:
        warnings.append(
            f"PARSE_ANOMALY_STATUS_UNKNOWN: {unknown}/{total} statuses unknown"
        )

    # Spending detected but no known statuses → column shift
    if performance["spent_total"] > 0 and unknown == total > 0:
        warnings.append("PARSE_ANOMALY_COLUMN_SHIFT: spending data but no known statuses")

    # Non-zero campaigns but zero spend
    if total > 0 and performance["spent_total"] == 0:
        warnings.append("PARSE_ANOMALY_SPENT_ZERO")

    # Budget column > spent → likely column labels don't match reality
    if performance["budget_column_total"] > 0 and performance["spent_total"] > 0:
        if performance["budget_column_total"] > performance["spent_total"]:
            warnings.append(
                f"PARSE_ANOMALY_BUDGET_EXCEEDS_SPENT: "
                f"budget_column={performance['budget_column_total']} "
                f"spent={performance['spent_total']}"
            )

    return warnings


def _compute_entry_data_quality(
    data_quality: str,
    anomalies: list[str],
) -> str:
    """Combine parser data_quality with anomaly detection."""
    if data_quality == DQ_UNRELIABLE:
        return DQ_UNRELIABLE
    anomaly_codes = {w.split(":")[0] for w in anomalies}
    if "PARSE_ANOMALY_COLUMN_SHIFT" in anomaly_codes:
        return DQ_UNRELIABLE
    if "PARSE_ANOMALY_STATUS_UNKNOWN" in anomaly_codes:
        return DQ_PARTIAL
    if data_quality == DQ_PARTIAL:
        return DQ_PARTIAL
    return data_quality


# ─── Total accumulator ──────────────────────────────────────────────────────────


class _TotalAccumulator:
    """Accumulate totals across all scanned accounts."""

    def __init__(self) -> None:
        self.accounts = 0
        self.impressions = 0
        self.clicks = 0
        self.spent_total = 0.0
        self.budget_column_total = 0.0
        self.campaigns_total = 0
        self.campaigns_active = 0
        self.campaigns_stopped = 0
        self.campaigns_declined = 0
        self.campaigns_limited = 0
        self.campaigns_unknown = 0

    def add(self, entry: dict[str, Any]) -> None:
        self.accounts += 1
        p = entry.get("performance", {})
        self.impressions += p.get("impressions", 0)
        self.clicks += p.get("clicks", 0)
        self.spent_total += p.get("spent_total", 0.0)
        self.budget_column_total += p.get("budget_column_total", 0.0)

        c = entry.get("campaigns", {})
        self.campaigns_total += c.get("total", 0)
        self.campaigns_active += c.get("active", 0)
        self.campaigns_stopped += c.get("stopped", 0)
        self.campaigns_declined += c.get("declined", 0)
        self.campaigns_limited += c.get("limited", 0)
        self.campaigns_unknown += c.get("unknown", 0)

    def summary(self) -> dict[str, Any]:
        ctr = (self.clicks / self.impressions * 100.0) if self.impressions > 0 else 0.0
        return {
            "accounts_analyzed": self.accounts,
            "campaigns_total": self.campaigns_total,
            "campaigns_active": self.campaigns_active,
            "campaigns_stopped": self.campaigns_stopped,
            "campaigns_declined": self.campaigns_declined,
            "campaigns_limited": self.campaigns_limited,
            "campaigns_unknown": self.campaigns_unknown,
            "impressions": self.impressions,
            "clicks": self.clicks,
            "ctr": round(ctr, 2),
            "spent_total": round(self.spent_total, 4),
            "budget_column_total": round(self.budget_column_total, 4),
        }


# ─── Scope label ────────────────────────────────────────────────────────────────


def _scope_label(scope: str, analyzed: int, found: int) -> str:
    """Human-readable scope description."""
    if scope == "all":
        return f"All {found} accounts"
    if scope == "current":
        return "Current account only"
    if scope == "selected":
        return "Selected account"
    if scope == "project_default":
        return "Project default account"
    return f"{analyzed} account(s)"


def _build_failure_warnings(
    accounts_found: int,
    skipped: int,
    per_account: list[dict[str, Any]],
    scope_warning: str | None,
) -> list[str]:
    """Assemble warnings for ACCOUNT_SCAN_FAILED case.

    Preserves all per-account skip reasons and the scope warning.
    Adds a top-level summary so the LLM can act on it.

    NOTE: the first warning starts with ``ACCOUNT_SCAN_FAILED:`` so callers
    can string-match on it without parsing JSON.
    """
    warnings: list[str] = []

    # Top-level summary first (most actionable)
    warnings.append(
        f"ACCOUNT_SCAN_FAILED: 0/{accounts_found} accounts analyzed. "
        f"{skipped} account(s) failed. No reliable ads data collected."
    )

    # Per-account skip reasons
    for entry in per_account:
        acct = entry.get("account", {})
        title = acct.get("title", "unknown")
        acct_warnings = entry.get("warnings", [])
        for w in acct_warnings:
            warnings.append(f"[{title}] {w}")

    # Scope warning (if any)
    if scope_warning:
        warnings.append(scope_warning)

    return warnings


def _global_warnings(scope_warning: str | None, total: _TotalAccumulator) -> list[str]:
    """Assemble snapshot-level warnings."""
    warnings: list[str] = []
    if total.accounts == 0:
        warnings.append("No accounts could be scanned.")
    elif total.campaigns_active == 0 and total.campaigns_total > 0:
        # Check if all are unknown status
        if total.campaigns_unknown == total.campaigns_total:
            warnings.append(
                "All campaigns have Unknown status — parser may be misaligned."
            )
        else:
            warnings.append("No active campaigns found.")
    elif total.campaigns_active == 0 and total.campaigns_total == 0:
        warnings.append("No campaigns found across scanned accounts.")
    if total.campaigns_unknown > 0:
        warnings.append(
            f"{total.campaigns_unknown} campaign(s) have Unknown status — "
            f"data quality may be affected."
        )
    return warnings


# ─── Serialization helper ───────────────────────────────────────────────────────


def _serialize(obj: Any) -> Any:
    """Convert Pydantic model to JSON-safe dict, or return as-is."""
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if hasattr(obj, "dict"):
        return obj.dict()
    return str(obj)
