"""Factory helpers for building :class:`WatcherEvent` objects.

Keeps ``dedupe_key`` construction in one place so the same logical change always
produces the same key (and is therefore stored at most once — see
``store.create_event``). These helpers only *build* events; persistence and
idempotency are the store's job.
"""

from __future__ import annotations

from typing import Any

from hermes_telegram_ads.watcher.models import WatcherEvent, WatchSpec
from hermes_telegram_ads.watcher.policies import recommended_action_for, severity_for


def make_dedupe_key(*parts: Any) -> str:
    """Stable dedupe key from parts (``None`` rendered as empty)."""
    return ":".join("" if p is None else str(p) for p in parts)


def build_event(
    spec: WatchSpec | None,
    *,
    event_type: str,
    project_id: str,
    dedupe_key: str,
    ad_id: int | str | None = None,
    account_id: str | None = None,
    account_token_hash: str | None = None,
    previous: dict[str, Any] | None = None,
    current: dict[str, Any] | None = None,
    reason: str | None = None,
) -> WatcherEvent:
    """Build a :class:`WatcherEvent`, deriving severity/recommended action.

    ``account_id`` / ``account_token_hash`` default to the spec's values when not
    given, so an event is always attributable to the cabinet being watched.
    """
    return WatcherEvent(
        project_id=project_id,
        event_type=event_type,  # type: ignore[arg-type]
        severity=severity_for(event_type),  # type: ignore[arg-type]
        account_id=account_id if account_id is not None else (spec.account_id if spec else None),
        account_token_hash=(
            account_token_hash
            if account_token_hash is not None
            else (spec.account_token_hash if spec else None)
        ),
        ad_id=ad_id if ad_id is not None else (spec.ad_id if spec else None),
        watch_spec_id=spec.id if spec else None,
        previous=previous,
        current=current,
        reason=reason,
        recommended_agent_action=recommended_action_for(event_type),
        dedupe_key=dedupe_key,
    )
