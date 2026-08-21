"""Shared Hermes runtime-kwarg stripping for ads + watcher dispatch.

[GOAL] One place that knows which keys Hermes injects into plugin handlers.
[INPUT] Tool name + merged args/kwargs from registry.dispatch / model_tools.
[OUTPUT] Payload safe to splat into ads/watcher handlers and Ads HTTP.

Hermes ``tools/registry.py`` ``dispatch(name, args, **kwargs)`` and
``model_tools.handle_function_call`` always pass ``session_id``, ``task_id``,
and ``user_task`` into every plugin handler. Research already rejects those
under ``additionalProperties: false``. Ads handlers historically swallowed
them via ``**_``; watcher handlers have strict signatures. Both must use
this module so a new Hermes key cannot TypeError one surface and not the
other.
"""

from __future__ import annotations

import inspect
from typing import Any

# Hermes registry.dispatch kwargs for plugin tools: session_id, task_id,
# user_task. The rest are hook/middleware fields that must never reach
# Ads HTTP, Playwright, or watcher handler signatures.
HERMES_RUNTIME_INJECTED_KEYS = frozenset(
    {
        "session_id",
        "task_id",
        "user_task",
        "tool_call_id",
        "turn_id",
        "api_request_id",
        "enabled_tools",
        "effective_task_id",
        "conversation_id",
    }
)
RESEARCH_DECLARED_TASK_ID_TOOLS = frozenset({"telegram_research_get_task_summary"})
WATCHER_SESSION_KEY_TOOLS = frozenset({"telegram_ads_watch_create"})
HERMES_PLACEHOLDER_IDS = frozenset({"default", "none", "null"})


def is_hermes_placeholder_id(value: Any) -> bool:
    if not isinstance(value, str):
        return True
    return value.strip().lower() in HERMES_PLACEHOLDER_IDS or not value.strip()


def adopt_hermes_session_key(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """
    [GOAL] Bind watcher invoke_agent to the conversation that created the watch.
    [INPUT] Tool name + raw payload (may still contain Hermes session_id).
    [OUTPUT] Payload with session_key filled from session_id when omitted.

    Constraints:
    - only telegram_ads_watch_create declares session_key
    - never overwrite an explicit session_key from the model
    - never adopt Hermes placeholder ids ('default')
    """
    if name not in WATCHER_SESSION_KEY_TOOLS:
        return payload
    existing = payload.get("session_key")
    if isinstance(existing, str) and existing.strip():
        return payload
    sid = payload.get("session_id")
    if is_hermes_placeholder_id(sid):
        return payload
    out = dict(payload)
    out["session_key"] = str(sid).strip()
    return out


def strip_hermes_runtime_args(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """
    [GOAL] Drop Hermes runtime kwargs before ads/research/watcher dispatch.
    [INPUT] Tool name + merged handler args/kwargs.
    [OUTPUT] Payload without undeclared runtime keys.

    Constraints:
    - keep task_id only for telegram_research_get_task_summary
    - drop Hermes placeholder task_id ('default') even on that tool
    - never forward these keys to Telethon, Ads API, or watcher handlers
    """
    cleaned: dict[str, Any] = {}
    for key, value in payload.items():
        if key == "task_id" and name in RESEARCH_DECLARED_TASK_ID_TOOLS:
            if is_hermes_placeholder_id(value):
                continue
            cleaned[key] = value
            continue
        if key in HERMES_RUNTIME_INJECTED_KEYS:
            continue
        cleaned[key] = value
    return cleaned


def prepare_plugin_payload(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Adopt watcher session_key, then strip undeclared Hermes runtime keys."""
    return strip_hermes_runtime_args(name, adopt_hermes_session_key(name, dict(payload)))


def merge_plugin_handler_args(
    name: str, args: dict[str, Any] | None, kwargs: dict[str, Any]
) -> dict[str, Any]:
    """
    [GOAL] Merge Hermes handler(args, **kwargs) the same way the live plugin does.
    [INPUT] Tool name + positional args dict + injected kwargs.
    [OUTPUT] Payload after session_key adopt + runtime strip.

    Model-supplied keys in ``args`` win. Hermes session_id in kwargs is kept
    long enough for watcher adopt, then stripped.
    """
    payload = dict(args or {})
    for key, value in kwargs.items():
        if key in {"self"} or value is None or key in payload:
            continue
        payload[key] = value
    return prepare_plugin_payload(name, payload)


def kwargs_for_handler(handler: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    """
    [GOAL] Bind only parameters the handler declares.
    [INPUT] Handler + raw call kwargs (may include Hermes runtime extras).
    [OUTPUT] kwargs safe to splat into the handler.

    Constraints:
    - never TypeError on session_id/task_id/user_task
    - never forward undeclared keys even if the handler has no **_
    """
    sig = inspect.signature(handler)
    has_var_kw = any(
        param.kind == inspect.Parameter.VAR_KEYWORD for param in sig.parameters.values()
    )
    allowed = {
        pname
        for pname, param in sig.parameters.items()
        if pname != "self"
        and param.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    cleaned: dict[str, Any] = {}
    for key, value in kwargs.items():
        if key in HERMES_RUNTIME_INJECTED_KEYS and key not in allowed:
            continue
        if key in allowed or has_var_kw:
            cleaned[key] = value
    return cleaned


def bind_handler_kwargs(handler: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Strip runtime keys, then keep only the handler's declared parameters."""
    return kwargs_for_handler(handler, strip_hermes_runtime_args("", dict(kwargs)))


__all__ = [
    "HERMES_PLACEHOLDER_IDS",
    "HERMES_RUNTIME_INJECTED_KEYS",
    "RESEARCH_DECLARED_TASK_ID_TOOLS",
    "WATCHER_SESSION_KEY_TOOLS",
    "adopt_hermes_session_key",
    "bind_handler_kwargs",
    "is_hermes_placeholder_id",
    "kwargs_for_handler",
    "merge_plugin_handler_args",
    "prepare_plugin_payload",
    "strip_hermes_runtime_args",
]
