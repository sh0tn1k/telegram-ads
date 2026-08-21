"""Typed Hermes tools for creating and inspecting Telegram Ads watches.

[GOAL] Let Hermes subscribe a cabinet/campaign (or other watchable resource)
       to the read-only watcher.
[INPUT] Watch kind + optional ad_id/account_id + invoke_agent/session_key.
[OUTPUT] Structured envelopes. Never mutates ads.telegram.org.

These tools are the *subscribe* surface. Polling stays inside the watcher
service and only calls adapter read methods.
"""

from __future__ import annotations

from typing import Any

from hermes_telegram_ads.runtime_kwargs import (
    adopt_hermes_session_key,
    kwargs_for_handler,
)

from hermes_telegram_ads.watcher.coverage import (
    direct_watch_kinds,
    list_tool_coverage,
    post_action_watch_kinds,
)
from hermes_telegram_ads.watcher.models import WatchKind


WATCHER_TOOL_NAMES: tuple[str, ...] = (
    "telegram_ads_watch_create",
    "telegram_ads_watch_list",
    "telegram_ads_watch_disable",
    "telegram_ads_watch_delete",
    "telegram_ads_watch_coverage",
    "telegram_ads_watch_events",
    "telegram_ads_watch_create_post_action",
)


def _ok(tool: str, data: Any) -> dict[str, Any]:
    return {"ok": True, "status": "ok", "tool": tool, "data": data, "error": None}


def _err(tool: str, code: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "error",
        "tool": tool,
        "data": None,
        "error": {"error": code, "message": message},
    }


def _spec_view(spec: Any) -> dict[str, Any]:
    return {
        "id": spec.id,
        "kind": spec.kind,
        "ad_id": spec.ad_id,
        "account_id": spec.account_id,
        "interval_sec": spec.interval_sec,
        "enabled": spec.enabled,
        "invoke_agent": spec.invoke_agent,
        "notify": spec.notify,
        "thresholds": spec.thresholds,
        "created_by": spec.created_by,
        "next_run_at": spec.next_run_at.isoformat() if spec.next_run_at else None,
        "expires_at": spec.expires_at.isoformat() if spec.expires_at else None,
    }


def _event_view(event: Any) -> dict[str, Any]:
    return {
        "id": event.id,
        "event_type": event.event_type,
        "severity": event.severity,
        "watch_spec_id": event.watch_spec_id,
        "ad_id": event.ad_id,
        "reason": event.reason,
        "previous": event.previous,
        "current": event.current,
        "consumed_at": event.consumed_at.isoformat() if event.consumed_at else None,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


class TelegramAdsWatcherToolset:
    """Typed subscribe/inspect API over :class:`TelegramAdsWatcherService`."""

    def __init__(self, service: Any) -> None:
        self.service = service

    async def call(self, name: str, **kwargs: Any) -> dict[str, Any]:
        handlers = {
            "telegram_ads_watch_create": self.create_watch,
            "telegram_ads_watch_list": self.list_watches,
            "telegram_ads_watch_disable": self.disable_watch,
            "telegram_ads_watch_delete": self.delete_watch,
            "telegram_ads_watch_coverage": self.coverage,
            "telegram_ads_watch_events": self.list_events,
            "telegram_ads_watch_create_post_action": self.create_post_action,
        }
        handler = handlers.get(name)
        if handler is None:
            return _err(name, "UNKNOWN_TOOL", f"unknown watcher tool {name}")
        payload = adopt_hermes_session_key(name, dict(kwargs))
        return await handler(**kwargs_for_handler(handler, payload))

    async def create_watch(
        self,
        *,
        kind: str,
        ad_id: int | str | None = None,
        account_id: str | None = None,
        interval_sec: int = 900,
        invoke_agent: bool = True,
        session_key: str | None = None,
        context: str | None = None,
        thresholds: dict[str, Any] | None = None,
        created_by: str = "agent",
        project_id: str | None = None,
    ) -> dict[str, Any]:
        allowed = set(direct_watch_kinds())
        if kind not in allowed:
            return _err(
                "telegram_ads_watch_create",
                "INVALID_KIND",
                f"kind {kind!r} is not a direct_watch kind. Allowed: {sorted(allowed)}",
            )
        merged = dict(thresholds or {})
        if session_key:
            merged["session_key"] = session_key
        if context:
            merged["context"] = context
        spec = await self.service.create_watch(
            kind=kind,
            ad_id=ad_id,
            account_id=account_id,
            interval_sec=interval_sec,
            invoke_agent=invoke_agent,
            thresholds=merged,
            created_by=created_by,  # type: ignore[arg-type]
            project_id=project_id,
        )
        return _ok("telegram_ads_watch_create", _spec_view(spec))

    async def list_watches(
        self, enabled: bool | None = None, project_id: str | None = None
    ) -> dict[str, Any]:
        specs = await self.service.list_watches(project_id=project_id, enabled=enabled)
        return _ok(
            "telegram_ads_watch_list",
            {"watches": [_spec_view(s) for s in specs], "count": len(specs)},
        )

    async def disable_watch(self, watch_id: str) -> dict[str, Any]:
        spec = await self.service.disable_watch(watch_id)
        return _ok("telegram_ads_watch_disable", _spec_view(spec))

    async def delete_watch(self, watch_id: str) -> dict[str, Any]:
        await self.service.delete_watch(watch_id)
        return _ok("telegram_ads_watch_delete", {"deleted": watch_id})

    async def coverage(self) -> dict[str, Any]:
        rows = [
            {
                "capability": c.capability,
                "watcher_support": c.watcher_support,
                "watch_kinds": c.watch_kinds,
                "event_types": c.event_types,
                "category": c.category,
            }
            for c in list_tool_coverage()
        ]
        return _ok(
            "telegram_ads_watch_coverage",
            {
                "tools": rows,
                "direct_watch_kinds": direct_watch_kinds(),
                "post_action_watch_kinds": post_action_watch_kinds(),
            },
        )

    async def list_events(
        self, unconsumed: bool = True, limit: int = 50, project_id: str | None = None
    ) -> dict[str, Any]:
        events = await self.service.list_events(
            project_id=project_id, unconsumed=unconsumed, limit=limit
        )
        return _ok(
            "telegram_ads_watch_events",
            {"events": [_event_view(e) for e in events], "count": len(events)},
        )

    async def create_post_action(
        self,
        action: str,
        *,
        ad_id: int | str | None = None,
        account_id: str | None = None,
        expected: dict[str, Any] | None = None,
        interval_sec: int = 600,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        if action.startswith("telegram_ads_"):
            action = action[len("telegram_ads_") :]
        specs = await self.service.create_post_action_watches(
            action,
            ad_id=ad_id,
            account_id=account_id,
            expected=expected,
            interval_sec=interval_sec,
            project_id=project_id,
        )
        return _ok(
            "telegram_ads_watch_create_post_action",
            {"watches": [_spec_view(s) for s in specs], "count": len(specs)},
        )


def watcher_tool_schemas() -> list[dict[str, Any]]:
    """JSON-schema entries for Hermes ``register_tool``."""
    kind_enum = list(direct_watch_kinds())
    return [
        {
            "name": "telegram_ads_watch_create",
            "description": (
                "Subscribe a Telegram Ads resource to the read-only watcher. "
                "Use when the operator asks to monitor moderation, budget, status, "
                "login, stats, or another direct_watch kind. The watcher never "
                "mutates ads; on change it wakes this conversation as a model turn."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": kind_enum},
                    "ad_id": {"type": ["integer", "string"]},
                    "account_id": {"type": "string"},
                    "interval_sec": {"type": "integer", "default": 900},
                    "invoke_agent": {"type": "boolean", "default": True},
                    "session_key": {"type": "string"},
                    "context": {
                        "type": "string",
                        "description": "Why this watch was set (passed back to the model on fire).",
                    },
                    "thresholds": {"type": "object"},
                },
                "required": ["kind"],
            },
        },
        {
            "name": "telegram_ads_watch_list",
            "description": "List persisted Telegram Ads watches for this project.",
            "parameters": {
                "type": "object",
                "properties": {"enabled": {"type": "boolean"}},
            },
        },
        {
            "name": "telegram_ads_watch_disable",
            "description": "Disable a Telegram Ads watch without deleting its history.",
            "parameters": {
                "type": "object",
                "properties": {"watch_id": {"type": "string"}},
                "required": ["watch_id"],
            },
        },
        {
            "name": "telegram_ads_watch_delete",
            "description": "Delete a Telegram Ads watch.",
            "parameters": {
                "type": "object",
                "properties": {"watch_id": {"type": "string"}},
                "required": ["watch_id"],
            },
        },
        {
            "name": "telegram_ads_watch_coverage",
            "description": (
                "Show which telegram_ads_* tools map to direct_watch / "
                "post_action_verification kinds."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "telegram_ads_watch_events",
            "description": "List watcher events (default: unconsumed).",
            "parameters": {
                "type": "object",
                "properties": {
                    "unconsumed": {"type": "boolean", "default": True},
                    "limit": {"type": "integer", "default": 50},
                },
            },
        },
        {
            "name": "telegram_ads_watch_create_post_action",
            "description": (
                "After an approved mutating ads action, create the read-only "
                "follow-up watches that verify the observable result."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "ad_id": {"type": ["integer", "string"]},
                    "account_id": {"type": "string"},
                    "expected": {"type": "object"},
                    "interval_sec": {"type": "integer", "default": 600},
                },
                "required": ["action"],
            },
        },
    ]


# Keep WatchKind imported so a missing kind in the matrix fails import-time
# reviews that compare against the literal.
_ = WatchKind
