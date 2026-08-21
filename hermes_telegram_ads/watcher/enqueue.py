"""Enqueue a Hermes *model turn* when a watcher event fires.

[GOAL] Wake the operator conversation as a real user/agent turn, not a canned
       system/status bubble.
[INPUT] A persisted WatcherEvent plus the WatchSpec that produced it.
[OUTPUT] A prompt string + a boolean from the host injector.

The watcher itself never writes the user-facing reply. It asks the model to
interpret the event (kind, previous/current snapshot, why the watch was set)
and answer in the operator thread.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from hermes_telegram_ads.watcher.models import WatcherEvent, WatchSpec

logger = logging.getLogger(__name__)

Injector = Callable[..., Any]


def build_agent_turn_prompt(
    event: WatcherEvent,
    spec: WatchSpec | None = None,
    *,
    extra_context: str | None = None,
) -> str:
    """Build the user-role turn the model must answer.

    This is intentionally *not* a finished "system: status changed" notice.
    The model is asked to interpret the payload and write the operator reply.
    """
    kind = spec.kind if spec else None
    created_by = spec.created_by if spec else None
    thresholds = (spec.thresholds if spec else None) or {}
    watch_id = spec.id if spec else event.watch_spec_id
    context_note = extra_context or thresholds.get("context") or thresholds.get("note")
    body = {
        "event_id": event.id,
        "event_type": event.event_type,
        "severity": event.severity,
        "reason": event.reason,
        "recommended_agent_action": event.recommended_agent_action,
        "watch_id": watch_id,
        "watch_kind": kind,
        "created_by": created_by,
        "invoke_agent": bool(spec.invoke_agent) if spec else True,
        "ad_id": event.ad_id,
        "account_id": event.account_id,
        "project_id": event.project_id,
        "previous": event.previous,
        "current": event.current,
        "watch_thresholds": {
            k: v
            for k, v in thresholds.items()
            if k not in {"session_key", "draft"} and not str(k).endswith("_token")
        },
        "context": context_note,
    }
    payload = json.dumps(body, ensure_ascii=False, default=str, indent=2)
    return (
        "A Telegram Ads watcher you previously set has fired.\n"
        "You are the model in this operator conversation. Interpret the event "
        "and write a concise update to the operator in your own words. Do not emit a "
        "canned system/status bubble — decide what changed (moderation, budget, "
        "status, login, stats, …) and what, if anything, you recommend.\n\n"
        f"{payload}\n"
    )


def resolve_session_key(
    spec: WatchSpec | None = None,
    *,
    fallback: str | None = None,
) -> str | None:
    """Session key for gateway inject: watch thresholds, then caller fallback."""
    if spec is not None:
        key = (spec.thresholds or {}).get("session_key")
        if isinstance(key, str) and key.strip():
            return key.strip()
    if fallback and fallback.strip():
        return fallback.strip()
    return None


def enqueue_watcher_agent_turn(
    event: WatcherEvent,
    spec: WatchSpec | None,
    injector: Injector,
    *,
    session_key: str | None = None,
    extra_context: str | None = None,
) -> bool:
    """Call the Hermes injector with a *user* turn that asks the model to reply.

    ``injector`` is typically ``PluginContext.inject_message``:
    ``injector(content, role="user", session_key=...)``.
    Returns True when the host accepted the queue request.
    """
    content = build_agent_turn_prompt(event, spec, extra_context=extra_context)
    key = resolve_session_key(spec, fallback=session_key)
    try:
        result = injector(content, role="user", session_key=key)
    except TypeError:
        # Some injectors are (content, session_key=) only.
        result = injector(content, session_key=key)
    return bool(result)


async def enqueue_unconsumed_invoke_agent_events(
    service: Any,
    injector: Injector,
    *,
    session_key: str | None = None,
    limit: int = 50,
) -> list[WatcherEvent]:
    """Consume unconsumed events whose watch has ``invoke_agent=true``.

    Each matching event is enqueued as a model turn, then marked consumed so
    the same fire is not delivered twice.
    """
    events = await service.list_events(unconsumed=True, limit=limit)
    delivered: list[WatcherEvent] = []
    for event in events:
        spec = None
        if event.watch_spec_id:
            spec = await service.get_watch(event.watch_spec_id)
        if spec is not None and not spec.invoke_agent:
            continue
        if spec is None and not session_key:
            logger.info("skip event %s: no watch spec and no session_key", event.id)
            continue
        ok = enqueue_watcher_agent_turn(
            event, spec, injector, session_key=session_key
        )
        if not ok:
            logger.warning(
                "inject_message refused for event %s watch %s",
                event.id,
                event.watch_spec_id,
            )
            continue
        await service.consume_event(event.id)
        delivered.append(event)
    return delivered


__all__ = [
    "build_agent_turn_prompt",
    "enqueue_unconsumed_invoke_agent_events",
    "enqueue_watcher_agent_turn",
    "resolve_session_key",
]
