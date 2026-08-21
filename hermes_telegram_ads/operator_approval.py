"""Escalate Ads mutations to Hermes native operator buttons.

[GOAL] Request create / CPM / budget / start / stop / delete confirmation
       through the same Telegram once / session / always / deny buttons Hermes
       already uses for dangerous commands.
[INPUT] pre_tool_call tool_name + args from the live gateway plugin hook.
[OUTPUT] {"action": "approve", "message": ..., "rule_key": ...} or None.

Intent:
- Hermes calls the mutating telegram_ads_* tool. This hook escalates BEFORE
  the handler runs. The gateway sends the operator an exec-approval card with the
  same scopes as system confirmations: once, session, always, deny.
- After he taps Accept, the same tool call continues and the toolset consumes
  an internal confirmation_id in-process. the operator does not type "да".

Constraints:
- Lives in the persist-safe plugin package (~/.hermes/plugins/packages/...),
  never inside ~/.hermes/hermes-agent, so `hermes update` cannot wipe it.
- Do not start a second Telegram getUpdates poller. Reuse Hermes' bot.
- Auto-apply only when this process registered the hook AND Hermes exposes
  request_tool_approval. Otherwise keep the old approval_required envelope.
- Per-tool rule_key so "always" on change_cpm does not allow delete_ad.
- Login / apply_approved_action / stubs are not gated here.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

OPERATOR_APPROVED_ARG = "_hermes_operator_approved"

# Live spend / destructive Ads tools. Not login, not apply_approved_action,
# not stubs. rule_key is telegram_ads:<tool> so scopes stay per-verb.
OPERATOR_GATED_TOOLS: frozenset[str] = frozenset(
    {
        "telegram_ads_create_ad",
        "telegram_ads_edit_ad",
        "telegram_ads_start_ad",
        "telegram_ads_stop_ad",
        "telegram_ads_change_cpm",
        "telegram_ads_add_to_budget",
        "telegram_ads_withdraw_from_budget",
        "telegram_ads_create_event",
        "telegram_ads_delete_ad",
        "telegram_ads_delete_event",
        "telegram_ads_revoke_share_stats_url",
    }
)

SYSTEM_PROMPT_SECTION_ID = "telegram-ops.ads-operator-confirm"
SYSTEM_PROMPT_SECTION = (
    "Telegram Ads mutations (create, edit, CPM, budget, start, stop, delete) "
    "request confirmation programmatically. Calling the mutating telegram_ads_* "
    "tool sends the operator a host approval card with the same buttons as Hermes "
    "system approvals: Once / Session / Always / Deny. Do NOT ask them to type "
    "да or yes. Do NOT call telegram_ads_apply_approved_action unless a "
    "confirmation_id already exists. After they tap Accept, that same tool call "
    "executes."
)

_gate_lock = threading.Lock()
_operator_gate_enabled = False


def is_operator_gated(tool_name: str) -> bool:
    return tool_name in OPERATOR_GATED_TOOLS


def set_operator_gate_enabled(enabled: bool) -> None:
    global _operator_gate_enabled
    with _gate_lock:
        _operator_gate_enabled = bool(enabled)


def operator_gate_enabled() -> bool:
    with _gate_lock:
        return _operator_gate_enabled


def hermes_operator_gate_available() -> bool:
    """True only inside a Hermes process that can show once/session/always."""
    try:
        from tools.approval import request_tool_approval  # type: ignore

        return callable(request_tool_approval)
    except Exception:  # noqa: BLE001 — missing on unit-test / non-Hermes imports
        return False


def _fmt_num(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, (int, float)):
        return f"{value:g}"
    return str(value)


def summarize_operator_approval(tool_name: str, args: dict[str, Any] | None) -> str:
    """Human card text for the Hermes exec-approval prompt. No secrets."""
    args = dict(args or {})
    args.pop("confirmation_id", None)
    args.pop("second_confirmation_id", None)
    args.pop(OPERATOR_APPROVED_ARG, None)

    if tool_name == "telegram_ads_create_ad":
        draft = args.get("draft") if isinstance(args.get("draft"), dict) else {}
        title = draft.get("title") or "(untitled)"
        return (
            f"Telegram Ads: submit NEW ad {title!r} to moderation. "
            f"cpm={_fmt_num(draft.get('cpm'))}, budget={_fmt_num(draft.get('budget'))}, "
            f"target={draft.get('target_type') or '?'}, promote={draft.get('promote_url') or '?'}."
        )
    if tool_name == "telegram_ads_edit_ad":
        draft = args.get("draft") if isinstance(args.get("draft"), dict) else {}
        return f"Telegram Ads: edit live ad {draft.get('ad_id')}. Editing triggers re-review."
    if tool_name == "telegram_ads_start_ad":
        return f"Telegram Ads: START (resume) ad {args.get('ad_id')}."
    if tool_name == "telegram_ads_stop_ad":
        return f"Telegram Ads: STOP (pause) ad {args.get('ad_id')}."
    if tool_name == "telegram_ads_change_cpm":
        return f"Telegram Ads: change CPM of ad {args.get('ad_id')} to {_fmt_num(args.get('new_cpm'))}."
    if tool_name == "telegram_ads_add_to_budget":
        return f"Telegram Ads: add {_fmt_num(args.get('amount'))} to budget of ad {args.get('ad_id')}."
    if tool_name == "telegram_ads_withdraw_from_budget":
        return (
            f"Telegram Ads: withdraw {_fmt_num(args.get('amount'))} from budget of ad {args.get('ad_id')}."
        )
    if tool_name == "telegram_ads_create_event":
        return (
            f"Telegram Ads: create pixel event {args.get('title')!r} "
            f"(type={args.get('event_type')})."
        )
    if tool_name == "telegram_ads_delete_ad":
        return f"Telegram Ads: PERMANENTLY DELETE ad {args.get('ad_id')}. This cannot be undone."
    if tool_name == "telegram_ads_delete_event":
        return (
            f"Telegram Ads: PERMANENTLY DELETE pixel event {args.get('event_id')}. "
            "This cannot be undone."
        )
    if tool_name == "telegram_ads_revoke_share_stats_url":
        return f"Telegram Ads: revoke/rotate the public share-stats URL for ad {args.get('ad_id')}."
    return f"Telegram Ads: {tool_name} requires operator confirmation."


def pre_tool_call_directive(tool_name: str, args: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, str] | None:
    """Escalate a gated Ads mutation to Hermes once/session/always/deny."""
    del kwargs
    if not is_operator_gated(tool_name):
        return None
    payload = dict(args or {})
    if payload.get("confirmation_id"):
        # Already bound to an issued token (apply / replay). Do not double-prompt.
        return None
    return {
        "action": "approve",
        "message": summarize_operator_approval(tool_name, payload),
        "rule_key": f"telegram_ads:{tool_name}",
    }


def register_operator_approval(ctx: Any) -> bool:
    """Attach the hook + prompt section on a persist-safe PluginContext.

    Returns True when Hermes can actually show the button card and the
    plugin handlers may auto-consume confirmation_id after Accept.
    """
    if hasattr(ctx, "register_hook"):
        ctx.register_hook("pre_tool_call", pre_tool_call_directive)
        logger.info("telegram-ops registered pre_tool_call Ads operator approval hook")
    if hasattr(ctx, "register_system_prompt_section"):
        try:
            ctx.register_system_prompt_section(
                SYSTEM_PROMPT_SECTION_ID,
                SYSTEM_PROMPT_SECTION,
                position="after_memory",
                max_chars=1200,
            )
        except Exception:  # noqa: BLE001 — older Hermes may reject unknown kwargs
            logger.warning("telegram-ops could not register Ads confirmation prompt section")
    enabled = hermes_operator_gate_available()
    set_operator_gate_enabled(enabled)
    if enabled:
        logger.info("telegram-ops Ads operator gate enabled (Hermes request_tool_approval)")
    else:
        logger.warning(
            "telegram-ops Ads operator gate disabled: Hermes request_tool_approval missing; "
            "mutating tools keep the approval_required envelope"
        )
    return enabled


__all__ = [
    "OPERATOR_APPROVED_ARG",
    "OPERATOR_GATED_TOOLS",
    "SYSTEM_PROMPT_SECTION",
    "SYSTEM_PROMPT_SECTION_ID",
    "hermes_operator_gate_available",
    "is_operator_gated",
    "operator_gate_enabled",
    "pre_tool_call_directive",
    "register_operator_approval",
    "set_operator_gate_enabled",
    "summarize_operator_approval",
]
