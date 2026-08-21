"""Workflow: account_diagnosis — per-account deep-dive diagnosis.

Read-only. Answers: "Why don't I see campaigns in this account, and what
does the UI actually show?"

Uses ONLY existing safe-read adapter API:
- ensure_logged_in
- list_accounts
- choose_account
- get_current_account
- parse_ads
- get_account_budget

Does NOT call mutating actions, does NOT manipulate UI state, does NOT
clear filters / sort / search.

Honest limitations (intentional):
- filter state probe is not implemented -> filters.present is always null
- archive is "stopped/declined counts" since TG Ads has no separate archive view
- account_type comparison is heuristic (currency + account_type field)
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hermes_telegram_ads.adapter import TelegramAdsAdapter

logger = logging.getLogger(__name__)

# ─── Constants ──────────────────────────────────────────────────────────────────

VALID_EXPECTED_TYPES = frozenset({"TON", "STARS", "Bot"})

# Known status strings (mirror _snapshot.py)
STATUS_ACTIVE = "Active"
STATUS_ON_HOLD = "On Hold"
STATUS_STOPPED = "Stopped"
STATUS_DECLINED = "Declined"
STATUS_LIMITED = "Limited"
STATUS_UNKNOWN = "Unknown"

DQ_COMPLETE = "complete"
DQ_PARTIAL = "partial"
DQ_UNRELIABLE = "unreliable"

# Standard token-hash prefix (non-reversible identifier for display)
_TOKEN_HASH_PREFIX = "ah_"


# ─── Entry point ────────────────────────────────────────────────────────────────


async def run_account_diagnosis(
    adapter: TelegramAdsAdapter,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Run per-account diagnosis.

    Parameters in *params*:
        account_name (str, optional): Cabinet name (substring, case-insensitive)
        account_token (str, optional): Cabinet token (priority over name)
        include_archive (bool, optional, default True): Count stopped/declined/on_hold
        check_filters (bool, optional, default True): Attempt filter state observation
        compare_ui_type (bool, optional, default False): Compare observed vs expected
        expected_account_type (str, optional): "TON" | "STARS" | "Bot"

    Returns structured dict; see projects/account_diagnosis_design.md §5.
    """
    now = datetime.now(tz=UTC)
    warnings: list[str] = []

    # 1. Login
    await adapter.ensure_logged_in()

    # 2. List all accounts
    all_accounts = await adapter.list_accounts()

    # 3. Resolve target
    requested_name = params.get("account_name")
    requested_token = params.get("account_token")

    target, resolve_warnings = _resolve_target_account(
        all_accounts, requested_name, requested_token
    )
    warnings.extend(resolve_warnings)

    if target is None:
        return {
            "diagnosis_timestamp": now.isoformat(),
            "diagnosis_date": now.strftime("%Y-%m-%d"),
            "target": {
                "requested_by": {
                    "account_name": requested_name,
                    "account_token": None,  # never echo raw token on error
                },
                "resolved": None,
                "error": "ACCOUNT_NOT_FOUND",
                "error_message": _not_found_message(
                    all_accounts, requested_name, requested_token
                ),
            },
        }

    include_archive = bool(params.get("include_archive", True))
    check_filters = bool(params.get("check_filters", True))
    compare_ui_type = bool(params.get("compare_ui_type", False))
    expected_type = params.get("expected_account_type")

    # 4. Choose account
    try:
        await adapter.choose_account(target.account_token)
        ui_account = await adapter.get_current_account()
        if ui_account is None:
            ui_account = target
    except Exception as exc:
        logger.warning("choose_account failed: %s", exc)
        return {
            "diagnosis_timestamp": now.isoformat(),
            "diagnosis_date": now.strftime("%Y-%m-%d"),
            "target": {
                "requested_by": {"account_name": requested_name, "account_token": None},
                "resolved": _build_resolved(
                    target, match_type="token" if requested_token else "name"
                ),
                "error": "CHOOSE_ACCOUNT_FAILED",
                "error_message": f"Failed to switch to account: {exc}",
            },
        }

    # 5. parse_ads
    parser_warnings: list[str] = []
    data_quality = DQ_COMPLETE
    data_quality_notes: list[str] = []
    ads_raw: list[Any] = []
    try:
        parse_result = await adapter.parse_ads()
        ads_raw = parse_result.get("ads", [])
        data_quality = parse_result.get("data_quality", DQ_COMPLETE)
        parser_warnings = list(parse_result.get("warnings", []))
        if data_quality == DQ_UNRELIABLE:
            data_quality_notes.append("Parser reported data_quality=unreliable.")
    except Exception as exc:
        logger.warning("parse_ads failed: %s", exc)
        data_quality = DQ_UNRELIABLE
        data_quality_notes.append(f"parse_ads() raised: {exc}")
        parser_warnings.append(f"PARSE_FAILED: {exc}")

    # 6. Account budget (best-effort)
    balance_section: dict[str, Any] = {
        "value": None, "currency": "", "available": False,
    }
    try:
        budget = await adapter.get_account_budget()
        balance_section = {
            "value": getattr(budget, "balance", None),
            "currency": getattr(budget, "currency", "") or "",
            "available": True,
        }
    except Exception as exc:
        warnings.append(f"get_account_budget failed: {exc}")

    # 7. Campaign classification
    campaigns_visible = _classify_campaigns(ads_raw)
    empty_state_detected = campaigns_visible["total"] == 0

    # 8. Archive section
    if include_archive:
        archive = {
            "stopped": campaigns_visible["stopped"],
            "on_hold": campaigns_visible["on_hold"],
            "declined": campaigns_visible["declined"],
            "limited": campaigns_visible["limited"],
            "empty": (
                campaigns_visible["stopped"]
                + campaigns_visible["declined"]
                + campaigns_visible["limited"]
                + campaigns_visible["on_hold"]
                == 0
            ),
        }
    else:
        archive = {"checked": False, "skipped": True}

    # 9. Filters (always null in current design - honest)
    if check_filters:
        filters = {
            "checked": True,
            "present": None,
            "cleared": None,
            "reason_if_unchecked": "DOM probe not implemented in adapter",
        }
    else:
        filters = {"checked": False, "skipped": True}

    # 10. UI type
    observed_type = _observe_account_type(ui_account)
    if compare_ui_type:
        if expected_type is None:
            ui_type = {
                "observed": observed_type,
                "expected": None,
                "match": None,
                "comparison_skipped": True,
            }
        elif expected_type not in VALID_EXPECTED_TYPES:
            warnings.append(
                f"expected_account_type={expected_type!r} not in "
                f"{sorted(VALID_EXPECTED_TYPES)}"
            )
            ui_type = {
                "observed": observed_type,
                "expected": expected_type,
                "match": None,
                "comparison_skipped": True,
            }
        else:
            ui_type = {
                "observed": observed_type,
                "expected": expected_type,
                "match": observed_type == expected_type,
                "comparison_skipped": False,
            }
    else:
        ui_type = {
            "observed": observed_type,
            "expected": None,
            "match": None,
            "comparison_skipped": True,
        }

    # 11. Conclusion
    conclusion = _build_conclusion(
        target=target,
        observed_type=observed_type,
        campaigns=campaigns_visible,
        archive=archive,
        include_archive=include_archive,
        data_quality=data_quality,
        ui_type=ui_type,
        filters=filters,
    )

    return {
        "diagnosis_timestamp": now.isoformat(),
        "diagnosis_date": now.strftime("%Y-%m-%d"),
        "target": {
            "requested_by": {"account_name": requested_name, "account_token": None},
            "resolved": _build_resolved(
                target,
                match_type="token" if requested_token else "name",
            ),
        },
        "balance": balance_section,
        "campaigns_visible": campaigns_visible,
        "empty_state_detected": empty_state_detected,
        "empty_state_text": None,
        "archive_checked": include_archive,
        "archive": archive,
        "filters": filters,
        "ui_type": ui_type,
        "data_quality": data_quality,
        "data_quality_notes": data_quality_notes,
        "warnings": warnings,
        "parser_warnings": parser_warnings,
        "conclusion": conclusion,
    }


# ─── Account resolution ─────────────────────────────────────────────────────────


def _resolve_target_account(
    all_accounts: list[Any],
    name: str | None,
    token: str | None,
) -> tuple[Any | None, list[str]]:
    """Resolve a target account by token (priority) or name (substring).

    Returns (account, warnings). On no match: (None, []).
    """
    warnings: list[str] = []

    # Token has absolute priority
    if token:
        for a in all_accounts:
            if getattr(a, "account_token", None) == token:
                return a, warnings
        return None, []

    # Name search: case-insensitive substring
    if name:
        needle = name.casefold()
        matches = [
            a for a in all_accounts
            if needle in (getattr(a, "title", "") or "").casefold()
        ]
        if len(matches) == 1:
            return matches[0], warnings
        if len(matches) > 1:
            warnings.append(
                f"ACCOUNT_NAME_AMBIGUOUS: {len(matches)} cabinets match {name!r}; "
                f"using first ({getattr(matches[0], 'title', '?')!r}). "
                f"Use account_token to disambiguate."
            )
            return matches[0], warnings
        return None, []

    # No name, no token -> use first (current)
    if all_accounts:
        return all_accounts[0], []
    return None, []


def _not_found_message(
    all_accounts: list[Any],
    name: str | None,
    token: str | None,
) -> str:
    titles = [getattr(a, "title", "?") for a in all_accounts]
    if name and not token:
        return (
            f"No cabinet matches {name!r}. "
            f"Available cabinets: {titles}"
        )
    if token:
        return (
            f"No cabinet has account_token={token!r}. "
            f"Available cabinets: {titles}"
        )
    return "No cabinets available."


# ─── Build resolved section ─────────────────────────────────────────────────────


def _build_resolved(account: Any, match_type: str) -> dict[str, Any]:
    """Build a 'resolved' dict with non-reversible token hash."""
    raw_token = getattr(account, "account_token", "") or ""
    return {
        "match_type": match_type,
        "matched_count": 1,
        "title": getattr(account, "title", "unknown"),
        "account_token_hash": _hash_token(raw_token),
        "account_type_observed": _observe_account_type(account),
        "currency": getattr(account, "currency", "") or "",
    }


def _hash_token(token: str) -> str:
    """Stable, non-reversible token hash for display."""
    if not token:
        return _TOKEN_HASH_PREFIX + "0"
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
    return f"{_TOKEN_HASH_PREFIX}{digest}"


# ─── Account type observation ───────────────────────────────────────────────────


def _observe_account_type(account: Any) -> str:
    """Heuristic account type from currency + account_type field.

    Returns canonical forms: "TON" | "STARS" | "Bot" | "Unknown" | <Currency>.
    Currencies are returned uppercase for consistency.
    """
    if account is None:
        return "Unknown"
    currency = (getattr(account, "currency", "") or "").upper()
    explicit = (getattr(account, "account_type", "") or "").upper()

    if explicit in ("TON", "STARS", "BOT"):
        return "Bot" if explicit == "BOT" else explicit

    if currency == "TON":
        return "TON"
    if currency == "STARS":
        return "STARS"
    if currency == "":
        return "Bot"
    return currency  # uppercase fallback


# ─── Campaign classification ────────────────────────────────────────────────────


def _classify_campaigns(ads_raw: list[Any]) -> dict[str, int]:
    """Break down ads by status. Mirror snapshot semantics, plus 'on_hold' explicit."""
    total = len(ads_raw)
    active = 0
    stopped = 0
    on_hold = 0
    declined = 0
    limited = 0
    unknown = 0

    for ad in ads_raw:
        status = getattr(ad, "status", "") or ""
        if status == STATUS_ACTIVE:
            active += 1
        elif status == STATUS_ON_HOLD:
            on_hold += 1
        elif status == STATUS_STOPPED:
            stopped += 1
        elif status == STATUS_DECLINED:
            declined += 1
        elif status == STATUS_LIMITED:
            limited += 1
        elif status == STATUS_UNKNOWN:
            unknown += 1
        else:
            unknown += 1

    return {
        "total": total,
        "active": active,
        "stopped": stopped,
        "on_hold": on_hold,
        "declined": declined,
        "limited": limited,
        "unknown": unknown,
    }


# ─── Conclusion builder ─────────────────────────────────────────────────────────


def _build_conclusion(
    target: Any,
    observed_type: str,
    campaigns: dict[str, int],
    archive: dict[str, Any],
    include_archive: bool,
    data_quality: str,
    ui_type: dict[str, Any],
    filters: dict[str, Any],
) -> str:
    """Generate honest human-readable conclusion.

    NEVER claims "never had campaigns" or "campaigns were deleted".
    Only describes what the UI shows *as visible*.
    """
    title = getattr(target, "title", "unknown")
    fragments: list[str] = []

    # Priority 1: data quality warning
    if data_quality == DQ_UNRELIABLE:
        fragments.append(
            "WARNING: data quality is unreliable. Parser may be misaligned - "
            "do not trust campaign counts."
        )
        if campaigns["total"] == 0:
            fragments.append(
                f"As visible in the current Telegram Ads dashboard for account "
                f"{title!r} ({observed_type}): 0 campaigns detected, but the parser "
                f"could not confirm this is a true empty state."
            )
        return " ".join(fragments)

    # Priority 2: UI type mismatch
    if (
        not ui_type.get("comparison_skipped", True)
        and ui_type.get("match") is False
    ):
        fragments.append(
            f"UI type ({ui_type.get('observed')}) does not match expected "
            f"({ui_type.get('expected')}). Possible account selection error or "
            f"UI rendering issue."
        )

    # Priority 3: campaign counts
    if campaigns["total"] == 0:
        if include_archive and archive.get("empty", True):
            fragments.append(
                f"No campaigns visible in current Telegram Ads dashboard for "
                f"account {title!r} ({observed_type}). All status counters are 0; "
                f"empty state is consistent with a clean cabinet."
            )
        elif include_archive and not archive.get("empty", True):
            archive_total = (
                archive.get("stopped", 0)
                + archive.get("declined", 0)
                + archive.get("limited", 0)
                + archive.get("on_hold", 0)
            )
            fragments.append(
                f"No active campaigns, but {archive_total} stopped/declined/on_hold/"
                f"limited campaign(s) exist in archive inventory for account {title!r}."
            )
        else:
            fragments.append(
                f"No campaigns visible in current Telegram Ads dashboard for "
                f"account {title!r} ({observed_type}). Archive scan was skipped per request."
            )
    else:
        # Has some campaigns. Distinguish "all non-active" vs "has active".
        if campaigns["active"] == 0 and include_archive and not archive.get("empty", True):
            archive_total = (
                archive.get("stopped", 0)
                + archive.get("declined", 0)
                + archive.get("limited", 0)
                + archive.get("on_hold", 0)
            )
            fragments.append(
                f"No active campaigns, but {archive_total} stopped/declined/on_hold/"
                f"limited campaign(s) exist in archive inventory for account {title!r}."
            )
        else:
            fragments.append(
                f"Account {title!r} shows {campaigns['total']} campaign(s) as visible "
                f"in the UI - see breakdown in campaigns_visible."
            )

    # Priority 4: filter state caveat
    if filters.get("checked") and filters.get("present") is None:
        fragments.append(
            "Filter state could not be observed - counts may exclude campaigns "
            "hidden by current filters."
        )

    # Priority 5: epistemic honesty
    fragments.append(
        "This describes the dashboard's current view; it does not prove that no "
        "campaigns ever existed."
    )

    return " ".join(fragments)
