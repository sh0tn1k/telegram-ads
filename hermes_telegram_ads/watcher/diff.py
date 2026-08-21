"""Snapshot diffing → watcher events.

Deterministic comparison of a previous and current snapshot. Pure functions
(plus the event factories in :mod:`events`) — no I/O, no Telegram Ads, no AI.

Rules implemented (see ``docs/WATCHER.md``):

* status change           → ``ad_status_changed`` (+ semantic ``ad_approved`` /
                            ``ad_declined`` / ``ad_started`` / ``ad_stopped``)
* budget thresholds       → ``budget_low`` / ``spend_threshold_reached``
* account balance         → ``account_balance_low``
* optional stats drift     → ``stats_anomaly`` (only when a stats threshold is set)

Session/login and unexpected adapter failures are turned into ``login_required``
/ ``watch_error`` by the service (see :func:`build_login_required_event` and
:func:`build_watch_error_event`); they are not snapshot diffs.
"""

from __future__ import annotations

from typing import Any

from hermes_telegram_ads.watcher.events import build_event, make_dedupe_key
from hermes_telegram_ads.watcher.models import (
    AccountSnapshot,
    AdSnapshot,
    ResourceSnapshot,
    WatcherEvent,
    WatchSpec,
)
from hermes_telegram_ads.watcher.policies import is_session_problem, normalize_status


def _label(snap: AdSnapshot) -> str | None:
    """The human status label to compare on (raw preferred, else normalized)."""
    return snap.raw_status if snap.raw_status is not None else snap.status


# ─── Status ───────────────────────────────────────────────────────────────────


def _status_events(prev: AdSnapshot, curr: AdSnapshot, spec: WatchSpec) -> list[WatcherEvent]:
    events: list[WatcherEvent] = []
    prev_label, curr_label = _label(prev), _label(curr)
    if prev_label == curr_label:
        return events

    prev_norm = normalize_status(prev_label)
    curr_norm = normalize_status(curr_label)
    pid, ad = spec.project_id, curr.ad_id
    prev_c, curr_c = prev.compact(), curr.compact()

    # General change event (always, when the label actually changed).
    events.append(
        build_event(
            spec,
            event_type="ad_status_changed",
            project_id=pid,
            dedupe_key=make_dedupe_key(pid, ad, "ad_status_changed", prev_label, curr_label),
            ad_id=ad,
            previous=prev_c,
            current=curr_c,
            reason=f"status changed: {prev_label!r} -> {curr_label!r}",
        )
    )

    # Semantic events, keyed off the normalized transition.
    if curr_norm == "declined" and prev_norm != "declined":
        reason = curr.rejection_reason or "ad was declined by moderation"
        events.append(
            build_event(
                spec,
                event_type="ad_declined",
                project_id=pid,
                dedupe_key=make_dedupe_key(pid, ad, "ad_declined", curr_label),
                ad_id=ad,
                previous=prev_c,
                current=curr_c,
                reason=reason,
            )
        )
    elif curr_norm == "active" and prev_norm != "active":
        if prev_norm == "stopped":
            events.append(
                build_event(
                    spec,
                    event_type="ad_started",
                    project_id=pid,
                    dedupe_key=make_dedupe_key(pid, ad, "ad_started", curr_label),
                    ad_id=ad,
                    previous=prev_c,
                    current=curr_c,
                    reason="ad resumed / is running",
                )
            )
        else:
            # pending/unknown/declined -> active == moderation approved.
            events.append(
                build_event(
                    spec,
                    event_type="ad_approved",
                    project_id=pid,
                    dedupe_key=make_dedupe_key(pid, ad, "ad_approved", curr_label),
                    ad_id=ad,
                    previous=prev_c,
                    current=curr_c,
                    reason="ad approved / went active",
                )
            )
    elif curr_norm == "stopped" and prev_norm == "active":
        events.append(
            build_event(
                spec,
                event_type="ad_stopped",
                project_id=pid,
                dedupe_key=make_dedupe_key(pid, ad, "ad_stopped", curr_label),
                ad_id=ad,
                previous=prev_c,
                current=curr_c,
                reason="ad was stopped / paused",
            )
        )

    return events


# ─── Budget / spend ─────────────────────────────────────────────────────────


def _remaining(curr: AdSnapshot) -> float | None:
    if curr.remaining_budget is not None:
        return curr.remaining_budget
    if curr.budget is not None and curr.spent is not None:
        return curr.budget - curr.spent
    return None


def _budget_events(curr: AdSnapshot, spec: WatchSpec) -> list[WatcherEvent]:
    events: list[WatcherEvent] = []
    th = spec.thresholds or {}
    pid, ad = spec.project_id, curr.ad_id
    curr_c = curr.compact()

    lte = th.get("remaining_budget_lte")
    remaining = _remaining(curr)
    if lte is not None and remaining is not None and remaining <= float(lte):
        events.append(
            build_event(
                spec,
                event_type="budget_low",
                project_id=pid,
                dedupe_key=make_dedupe_key(pid, ad, "budget_low"),
                ad_id=ad,
                current=curr_c,
                reason=f"remaining budget {remaining} <= threshold {lte}",
            )
        )

    pct = th.get("spent_percent_gte")
    if pct is not None and curr.budget is not None and curr.budget > 0 and curr.spent is not None:
        spent_pct = curr.spent / curr.budget * 100.0
        if spent_pct >= float(pct):
            events.append(
                build_event(
                    spec,
                    event_type="spend_threshold_reached",
                    project_id=pid,
                    dedupe_key=make_dedupe_key(pid, ad, "spend_threshold_reached"),
                    ad_id=ad,
                    current=curr_c,
                    reason=f"spent {spent_pct:.1f}% of budget >= threshold {pct}%",
                )
            )

    return events


# ─── Stats drift (optional) ──────────────────────────────────────────────────


def _stats_events(prev: AdSnapshot, curr: AdSnapshot, spec: WatchSpec) -> list[WatcherEvent]:
    th = spec.thresholds or {}
    events: list[WatcherEvent] = []
    pid, ad = spec.project_id, curr.ad_id

    drop_pct = th.get("ctr_drop_pct")
    if drop_pct is not None and prev.ctr is not None and curr.ctr is not None and prev.ctr > 0:
        delta = (prev.ctr - curr.ctr) / prev.ctr * 100.0
        if delta >= float(drop_pct):
            events.append(
                build_event(
                    spec,
                    event_type="stats_anomaly",
                    project_id=pid,
                    dedupe_key=make_dedupe_key(pid, ad, "stats_anomaly", "ctr_drop"),
                    ad_id=ad,
                    previous=prev.compact(),
                    current=curr.compact(),
                    reason=f"CTR dropped {delta:.1f}% ({prev.ctr} -> {curr.ctr})",
                )
            )

    return events


# ─── Account balance ─────────────────────────────────────────────────────────


def _account_balance_events(curr: AccountSnapshot, spec: WatchSpec) -> list[WatcherEvent]:
    th = spec.thresholds or {}
    lte = th.get("balance_lte")
    if lte is None or curr.balance is None or curr.balance > float(lte):
        return []
    return [
        build_event(
            spec,
            event_type="account_balance_low",
            project_id=spec.project_id,
            dedupe_key=make_dedupe_key(spec.project_id, curr.account_id or spec.id, "account_balance_low"),
            account_id=curr.account_id,
            current=curr.compact(),
            reason=f"account balance {curr.balance} <= threshold {lte}",
        )
    ]


# ─── Public entry points ─────────────────────────────────────────────────────


def diff_ad(prev: AdSnapshot | None, curr: AdSnapshot, spec: WatchSpec) -> list[WatcherEvent]:
    """Compare two ad snapshots and return the events the change implies.

    On the first observation (``prev is None``) only threshold-based events
    (budget/spend) can fire — there is no prior state to diff a status against.
    """
    events: list[WatcherEvent] = []
    if prev is not None:
        events += _status_events(prev, curr, spec)
        events += _stats_events(prev, curr, spec)
    events += _budget_events(curr, spec)
    return events


def diff_account(prev: AccountSnapshot | None, curr: AccountSnapshot, spec: WatchSpec) -> list[WatcherEvent]:
    """Compare two account snapshots and return implied events."""
    return _account_balance_events(curr, spec)


# ─── Error → event builders (used by the service, not snapshot diffs) ────────


def build_login_required_event(spec: WatchSpec, *, reason: str | None = None) -> WatcherEvent:
    return build_event(
        spec,
        event_type="login_required",
        project_id=spec.project_id,
        dedupe_key=make_dedupe_key(
            spec.project_id,
            spec.account_token_hash or spec.id,
            "login_required",
        ),
        reason=reason or "Telegram Ads session requires manual login",
    )


def build_watch_error_event(spec: WatchSpec, exc: BaseException) -> WatcherEvent:
    """Build ``watch_error`` (or ``login_required`` for session problems)."""
    if is_session_problem(exc):
        return build_login_required_event(spec, reason=str(exc) or "login required")
    return build_event(
        spec,
        event_type="watch_error",
        project_id=spec.project_id,
        dedupe_key=make_dedupe_key(spec.project_id, spec.id, "watch_error", type(exc).__name__),
        reason=f"{type(exc).__name__}: {exc}",
    )


# ─── Generic resource diff (expanded watcher kinds) ──────────────────────────
#
# These cover the read-only capabilities that don't fit the typed AdSnapshot /
# AccountSnapshot paths. Each watch stores a ``ResourceSnapshot`` whose ``data``
# is a small dict; the differ below switches on ``spec.kind`` (the resource_type
# is only a storage label). Events are emitted only on a real change/transition,
# never every tick.


def _rev(
    spec: WatchSpec,
    event_type: str,
    dedupe_extra: Any,
    *,
    prev: ResourceSnapshot | None,
    curr: ResourceSnapshot,
    reason: str | None = None,
    ad_id: int | str | None = None,
    account_id: str | None = None,
) -> WatcherEvent:
    anchor = spec.ad_id if spec.ad_id is not None else (spec.account_id or spec.id)
    return build_event(
        spec,
        event_type=event_type,
        project_id=spec.project_id,
        dedupe_key=make_dedupe_key(spec.project_id, spec.kind, anchor, event_type, dedupe_extra),
        ad_id=ad_id if ad_id is not None else spec.ad_id,
        account_id=account_id,
        previous=prev.compact() if prev else None,
        current=curr.compact(),
        reason=reason,
    )


def _d(snap: ResourceSnapshot | None) -> dict[str, Any]:
    return snap.data if snap is not None else {}


def diff_resource(
    prev: ResourceSnapshot | None, curr: ResourceSnapshot, spec: WatchSpec
) -> list[WatcherEvent]:
    """Compare two resource snapshots for *spec.kind* and return implied events."""
    handler = _RESOURCE_DIFFERS.get(spec.kind)
    if handler is None:
        return []
    return handler(prev, curr, spec)


def _diff_tool_status(prev, curr, spec):  # noqa: ANN001
    events: list[WatcherEvent] = []
    p, c = _d(prev), _d(curr)
    cur_browser, prev_browser = c.get("browser_state"), p.get("browser_state")
    cur_logged, prev_logged = c.get("logged_in"), p.get("logged_in")
    if cur_browser == "broken" and prev_browser != "broken":
        events.append(
            _rev(
                spec, "tool_unavailable", cur_browser, prev=prev, curr=curr, reason="browser/tool unavailable"
            )
        )
    if cur_browser == "healthy" and prev_browser == "broken":
        events.append(
            _rev(spec, "tool_available", cur_browser, prev=prev, curr=curr, reason="browser/tool recovered")
        )
    if cur_logged is not True:
        events.append(
            _rev(
                spec,
                "login_required",
                "state",
                prev=prev,
                curr=curr,
                reason="Telegram Ads session requires manual login",
            )
        )
    elif prev_logged is False:
        events.append(_rev(spec, "login_restored", "state", prev=prev, curr=curr, reason="login restored"))
    return events


def _diff_accounts(prev, curr, spec):  # noqa: ANN001
    events: list[WatcherEvent] = []
    p, c = _d(prev), _d(curr)
    prev_keys = set(p.get("keys") or [])
    cur_keys = set(c.get("keys") or [])
    if prev is not None:
        for added in sorted(cur_keys - prev_keys):
            events.append(
                _rev(spec, "account_added", added, prev=prev, curr=curr, reason=f"cabinet added: {added}")
            )
        for removed in sorted(prev_keys - cur_keys):
            events.append(
                _rev(
                    spec,
                    "account_removed",
                    removed,
                    prev=prev,
                    curr=curr,
                    reason=f"cabinet removed: {removed}",
                )
            )
    lte = (spec.thresholds or {}).get("balance_lte")
    if lte is not None:
        for acc in c.get("accounts") or []:
            bal = acc.get("balance")
            if bal is not None and bal <= float(lte):
                events.append(
                    _rev(
                        spec,
                        "account_balance_low",
                        acc.get("key"),
                        prev=prev,
                        curr=curr,
                        account_id=acc.get("key"),
                        reason=f"balance {bal} <= {lte} ({acc.get('title')})",
                    )
                )
    return events


def _diff_account_balance(prev, curr, spec):  # noqa: ANN001
    events: list[WatcherEvent] = []
    p, c = _d(prev), _d(curr)
    bal = c.get("balance")
    lte = (spec.thresholds or {}).get("balance_lte")
    if lte is not None and bal is not None and bal <= float(lte):
        events.append(
            _rev(
                spec,
                "account_balance_low",
                "thr",
                prev=prev,
                curr=curr,
                account_id=spec.account_id,
                reason=f"balance {bal} <= {lte}",
            )
        )
    if prev is not None and bal is not None and p.get("balance") != bal:
        events.append(
            _rev(
                spec,
                "account_budget_changed",
                "chg",
                prev=prev,
                curr=curr,
                account_id=spec.account_id,
                reason=f"balance {p.get('balance')} -> {bal}",
            )
        )
    return events


def _diff_account_stats(prev, curr, spec):  # noqa: ANN001
    if prev is None or _d(prev) == _d(curr):
        return []
    return [
        _rev(
            spec,
            "account_stats_changed",
            "chg",
            prev=prev,
            curr=curr,
            account_id=spec.account_id,
            reason="account stats changed",
        )
    ]


def _diff_campaign_list(prev, curr, spec):  # noqa: ANN001
    events: list[WatcherEvent] = []
    p, c = _d(prev), _d(curr)
    prev_ids = set(p.get("ad_ids") or [])
    cur_ids = set(c.get("ad_ids") or [])
    if prev is not None:
        for added in sorted(cur_ids - prev_ids):
            events.append(
                _rev(
                    spec,
                    "campaign_added",
                    added,
                    prev=prev,
                    curr=curr,
                    ad_id=added,
                    reason=f"campaign appeared: {added}",
                )
            )
        for removed in sorted(prev_ids - cur_ids):
            events.append(
                _rev(
                    spec,
                    "campaign_removed",
                    removed,
                    prev=prev,
                    curr=curr,
                    ad_id=removed,
                    reason=f"campaign disappeared: {removed}",
                )
            )
    if spec.ad_id is not None and str(spec.ad_id) not in cur_ids:
        events.append(
            _rev(
                spec,
                "ad_deleted_or_missing",
                str(spec.ad_id),
                prev=prev,
                curr=curr,
                reason=f"watched ad {spec.ad_id} not in campaign list",
            )
        )
    return events


def _diff_campaign_detail(prev, curr, spec):  # noqa: ANN001
    events: list[WatcherEvent] = []
    p, c = _d(prev), _d(curr)
    if c.get("missing"):
        events.append(
            _rev(
                spec,
                "ad_deleted_or_missing",
                "missing",
                prev=prev,
                curr=curr,
                reason=f"ad {spec.ad_id} not found",
            )
        )
        return events
    if prev is None:
        return events
    if p.get("raw_status") != c.get("raw_status"):
        events.append(
            _rev(
                spec,
                "campaign_status_changed",
                c.get("raw_status"),
                prev=prev,
                curr=curr,
                reason=f"status {p.get('raw_status')!r} -> {c.get('raw_status')!r}",
            )
        )
    if c.get("cpm") is not None and p.get("cpm") != c.get("cpm"):
        events.append(
            _rev(
                spec,
                "cpm_changed",
                c.get("cpm"),
                prev=prev,
                curr=curr,
                reason=f"cpm {p.get('cpm')} -> {c.get('cpm')}",
            )
        )
    if c.get("budget") is not None and p.get("budget") != c.get("budget"):
        events.append(
            _rev(
                spec,
                "budget_changed",
                c.get("budget"),
                prev=prev,
                curr=curr,
                reason=f"budget {p.get('budget')} -> {c.get('budget')}",
            )
        )
    return events


def _diff_rejection(prev, curr, spec):  # noqa: ANN001
    events: list[WatcherEvent] = []
    p, c = _d(prev), _d(curr)
    if c.get("declined") and not (prev is not None and p.get("declined")):
        events.append(
            _rev(
                spec,
                "ad_declined",
                "declined",
                prev=prev,
                curr=curr,
                reason=c.get("description") or "ad declined",
            )
        )
    elif prev is not None and c.get("declined") and p.get("description") != c.get("description"):
        events.append(
            _rev(
                spec,
                "rejection_reason_changed",
                "chg",
                prev=prev,
                curr=curr,
                reason=f"decline reason changed: {c.get('description')}",
            )
        )
    return events


def _diff_targeting(prev, curr, spec):  # noqa: ANN001
    events: list[WatcherEvent] = []
    p, c = _d(prev), _d(curr)
    if prev is None:
        return events
    if p.get("targets") != c.get("targets") or p.get("target_type") != c.get("target_type"):
        events.append(
            _rev(spec, "targeting_changed", "chg", prev=prev, curr=curr, reason="targeting changed")
        )
    if not p.get("locked") and c.get("locked"):
        events.append(
            _rev(
                spec,
                "targeting_locked",
                "lock",
                prev=prev,
                curr=curr,
                reason="targeting is now locked/immutable",
            )
        )
    if p.get("locked") and not c.get("locked"):
        events.append(
            _rev(
                spec,
                "targeting_unlocked",
                "unlock",
                prev=prev,
                curr=curr,
                reason="targeting is no longer locked",
            )
        )
    return events


def _diff_cpm(prev, curr, spec):  # noqa: ANN001
    p, c = _d(prev), _d(curr)
    if prev is None or c.get("cpm") is None or p.get("cpm") == c.get("cpm"):
        return []
    return [
        _rev(
            spec,
            "cpm_changed",
            c.get("cpm"),
            prev=prev,
            curr=curr,
            reason=f"cpm {p.get('cpm')} -> {c.get('cpm')}",
        )
    ]


def _diff_performance(prev, curr, spec):  # noqa: ANN001
    events: list[WatcherEvent] = []
    p, c = _d(prev), _d(curr)
    cur_views, prev_views = c.get("views"), p.get("views")
    if prev is not None and cur_views is not None and prev_views is not None:
        if cur_views == prev_views:
            events.append(
                _rev(
                    spec,
                    "delivery_stalled",
                    cur_views,
                    prev=prev,
                    curr=curr,
                    reason=f"views did not grow (still {cur_views})",
                )
            )
        else:
            events.append(
                _rev(
                    spec,
                    "stats_changed",
                    cur_views,
                    prev=prev,
                    curr=curr,
                    reason=f"views {prev_views} -> {cur_views}",
                )
            )
    return events


def _diff_reports(prev, curr, spec):  # noqa: ANN001
    events: list[WatcherEvent] = []
    p, c = _d(prev), _d(curr)
    if c.get("has_report") and not (prev is not None and p.get("has_report")):
        events.append(
            _rev(
                spec,
                "report_available",
                "avail",
                prev=prev,
                curr=curr,
                reason="monthly report became available",
            )
        )
    elif prev is not None and c.get("has_report") and p.get("report_key") != c.get("report_key"):
        events.append(
            _rev(spec, "report_changed", c.get("report_key"), prev=prev, curr=curr, reason="report changed")
        )
    return events


def _diff_share_stats(prev, curr, spec):  # noqa: ANN001
    events: list[WatcherEvent] = []
    p, c = _d(prev), _d(curr)
    if c.get("available") and not (prev is not None and p.get("available")):
        events.append(
            _rev(
                spec,
                "share_stats_available",
                "avail",
                prev=prev,
                curr=curr,
                reason="share-stats link available",
            )
        )
    elif prev is not None and p.get("available") and not c.get("available"):
        events.append(
            _rev(
                spec,
                "share_stats_unavailable",
                "gone",
                prev=prev,
                curr=curr,
                reason="share-stats link no longer available",
            )
        )
    elif (
        prev is not None
        and c.get("available")
        and p.get("available")
        and p.get("url_hash") != c.get("url_hash")
    ):
        events.append(
            _rev(
                spec,
                "share_stats_changed",
                c.get("url_hash"),
                prev=prev,
                curr=curr,
                reason="share-stats link rotated",
            )
        )
    return events


def _diff_draft_validation(prev, curr, spec):  # noqa: ANN001
    c = _d(curr)
    if c.get("valid"):
        return [
            _rev(
                spec, "draft_validation_passed", "ok", prev=prev, curr=curr, reason="draft passed validation"
            )
        ]
    return [
        _rev(
            spec,
            "draft_validation_failed",
            f"{c.get('field')}:{c.get('error')}",
            prev=prev,
            curr=curr,
            reason=f"draft invalid: {c.get('field')} {c.get('error')}".strip(),
        )
    ]


_RESOURCE_DIFFERS = {
    "tool_status": _diff_tool_status,
    "accounts_snapshot": _diff_accounts,
    "account_balance": _diff_account_balance,
    "account_stats": _diff_account_stats,
    "campaign_list": _diff_campaign_list,
    "campaign_detail": _diff_campaign_detail,
    "rejection_info": _diff_rejection,
    "campaign_targeting": _diff_targeting,
    "targeting_lock_state": _diff_targeting,
    "campaign_cpm": _diff_cpm,
    "campaign_performance": _diff_performance,
    "campaign_reports": _diff_reports,
    "share_stats_state": _diff_share_stats,
    "draft_validation": _diff_draft_validation,
}


# ─── Post-action verification ────────────────────────────────────────────────


def match_expected(observed: dict[str, Any], expected: dict[str, Any]) -> bool:
    """True when every key in *expected* matches *observed*.

    Special keys: ``status`` compares normalized status buckets; ``missing``
    compares truthiness (used for delete verification). Numeric values compare
    with a small tolerance; everything else compares by equality.
    """
    for k, v in expected.items():
        if k == "status":
            if normalize_status(observed.get("raw_status") or observed.get("status")) != (
                normalize_status(v)
            ):
                return False
            continue
        if k == "missing":
            if bool(v) != bool(observed.get("missing") or observed.get("absent")):
                return False
            continue
        obs = observed.get(k)
        if isinstance(v, (int, float)) and isinstance(obs, (int, float)):
            if abs(float(v) - float(obs)) > 1e-6:
                return False
        elif obs != v:
            return False
    return True


def build_verification_event(spec: WatchSpec, observed: dict[str, Any]) -> WatcherEvent | None:
    """Emit ``post_action_verified`` / ``post_action_not_verified`` for a watch
    that carries ``thresholds['expected']``; ``None`` when no expectation is set."""
    expected = (spec.thresholds or {}).get("expected")
    if not expected:
        return None
    if match_expected(observed, expected):
        return build_event(
            spec,
            event_type="post_action_verified",
            project_id=spec.project_id,
            dedupe_key=make_dedupe_key(spec.project_id, spec.id, "post_action_verified"),
            ad_id=spec.ad_id,
            account_id=spec.account_id,
            current=observed,
            reason=f"observed matches expected {expected}",
        )
    return build_event(
        spec,
        event_type="post_action_not_verified",
        project_id=spec.project_id,
        dedupe_key=make_dedupe_key(spec.project_id, spec.id, "post_action_not_verified"),
        ad_id=spec.ad_id,
        account_id=spec.account_id,
        current=observed,
        reason=f"observed does not yet match expected {expected}",
    )
