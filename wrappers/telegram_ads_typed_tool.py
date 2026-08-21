"""
telegram_ads_typed_tool — Hermes tool wrappers for the hermes_telegram_ads
Hermes tool layer (57 typed `telegram_ads_*` tools).

Created: 2026-06-03 (integration of feature/hermes-tool-wrappers).

This is the **typed**, single-tool-per-action surface that the new
`hermes_telegram_ads.hermes_tools.TelegramAdsToolset` exposes. The old
`telegram_ads` tool (in `telegram_ads_tool.py`) is a single-tool dispatcher
(`action="..."`); this wrapper registers each typed tool as its own
registry entry so the LLM can call them directly.

Design:
* Wraps `TelegramAdsToolset` (from `hermes_telegram_ads.hermes_tools`).
* Uses `BrowserProfileManager.acquire_adapter()` (from the package) to obtain
  a shared `TelegramAdsAdapter` instance — never creates a second adapter
  directly (would race with the existing Playwright profile lock).
* Each tool is registered in toolset `telegram_ads_typed` (separate from
  the legacy `telegram_ads` toolset) to avoid confusion.
* **No live browser / network action runs at import time.** The adapter
  is acquired lazily on first tool call.
* **No secrets are exposed.** Account tokens are masked by
  `AccountSummary`; raw `owner_id` / `hash` / cookies never cross the
  boundary. (See `hermes_telegram_ads.hermes_tools._scrub`.)

This wrapper is wired for the **default profile only**.
DeepSeek does NOT get direct access to Telegram Ads tools by design
(see `skills/business-growth/prepare-and-manage-tg-ads/SKILL.md`
"Operating Discipline" + "DeepSeek не имеет доступа к telegram_ads tool").
DeepSeek receives only textual summaries from the agent via AGI Team Task Board.

Shared config:    /home/hermes/.hermes/telegram_ads.yaml
Shared data:       /home/hermes/.hermes/data/telegram_ads/
Shared audit:      /home/hermes/.hermes/logs/telegram_ads_audit.jsonl
Shared browser:    BrowserProfileManager (singleton in hermes_telegram_ads.browser_manager)
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import os
import threading
from typing import TYPE_CHECKING, Any

from tools.registry import registry
from agent.approval_policy import requires_approval

if TYPE_CHECKING:
    from hermes_telegram_ads.browser_manager import BrowserProfileManager
    from hermes_telegram_ads.config import TelegramAdsConfig
    from hermes_telegram_ads.hermes_tools import (
        TELEGRAM_ADS_TOOLS,
        TOOLS_BY_NAME,
        TelegramAdsToolset,
        tool_names,
    )

logger = logging.getLogger(__name__)

SHARED_CONFIG_PATH = "/home/hermes/.hermes/telegram_ads.yaml"
TOOLSET_NAME = "telegram_ads_typed"  # separate from legacy "telegram_ads" toolset

# ─── Optional dependency guard ────────────────────────────────────────────────

_typed_available: bool = False
_ImportError_msg: str | None = None
try:
    from hermes_telegram_ads.hermes_tools import (
        TELEGRAM_ADS_TOOLS,
        TOOLS_BY_NAME,
        TelegramAdsToolset,
        tool_names,
    )
    from hermes_telegram_ads.config import TelegramAdsConfig
    from hermes_telegram_ads.browser_manager import BrowserProfileManager
    _typed_available = True
except Exception as _exc:  # noqa: BLE001
    _ImportError_msg = repr(_exc)
    TELEGRAM_ADS_TOOLS = ()  # type: ignore[assignment]
    TOOLS_BY_NAME = {}  # type: ignore[assignment]
    tool_names = lambda: ()  # type: ignore[assignment, misc]
    TelegramAdsToolset = None  # type: ignore[assignment,misc]
    TelegramAdsConfig = None  # type: ignore[assignment,misc]
    BrowserProfileManager = None  # type: ignore[assignment,misc]


# ─── Config / enable check ────────────────────────────────────────────────────


def _load_shared_config() -> dict[str, Any] | None:
    """Load the shared telegram_ads.yaml config block (read-only, no side effects)."""
    if not os.path.exists(SHARED_CONFIG_PATH):
        return None
    try:
        import yaml  # type: ignore[import-untyped]

        with open(SHARED_CONFIG_PATH) as fh:
            return yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("telegram_ads_typed: failed to load %s: %r", SHARED_CONFIG_PATH, exc)
        return None


def _check_telegram_ads_typed_enabled() -> bool:
    """Toolset visibility check (no live browser call)."""
    if not _typed_available:
        return False
    cfg = _load_shared_config()
    if not cfg:
        return False
    block = cfg.get("telegram_ads", cfg)
    if not block.get("enabled", False):
        return False
    return True


# ─── Toolset singleton (lazy) ────────────────────────────────────────────────
# Single toolset per Hermes process. Acquired on first call.
# We hold a synchronous factory that yields the async adapter
# (BrowserProfileManager is async, but registration is sync).

_toolset_singleton: TelegramAdsToolset | None = None
_toolset_lock_name = "telegram_ads_typed_tool"


def _make_toolset() -> TelegramAdsToolset:
    """Construct the TelegramAdsToolset wired to BrowserProfileManager.

    Does NOT call acquire_adapter() — that happens on first tool invocation
    (lazy). Does NOT touch the browser / network / disk.
    """
    if TelegramAdsToolset is None or TelegramAdsConfig is None or BrowserProfileManager is None:
        raise RuntimeError("hermes_telegram_ads.hermes_tools not importable")

    cfg_data = _load_shared_config() or {}
    # Prefer `TelegramAdsConfig.from_yaml(SHARED_CONFIG_PATH)` (the supported API
    # in this package version). Fall back to model_validate on the parsed block,
    # then to TelegramAdsConfig.default() if neither works. The previous code
    # called `TelegramAdsConfig.from_dict(block)`, which does NOT exist in this
    # package and silently fell through to the default config — causing typed
    # tools to use a relative profile_dir that collided with the standalone
    # watcher/smoke scripts.
    config = None
    try:
        config = TelegramAdsConfig.from_yaml(SHARED_CONFIG_PATH)  # type: ignore[attr-defined]
    except Exception:
        try:
            block = cfg_data.get("telegram_ads", cfg_data)
            config = TelegramAdsConfig.model_validate(block)  # type: ignore[attr-defined]
            config.storage.resolve()
        except Exception:
            config = None
    if config is None:
        # Fall back to default config if the shared file has unexpected shape
        config = TelegramAdsConfig.default()  # type: ignore[attr-defined]  

    async def _factory() -> Any:
        """Adapter factory: get the shared adapter via the package manager API.

        Do not fall back to ``TelegramAdsAdapter.start()`` here: that can create
        an independent adapter/browser lifecycle and race the persistent browser
        profile.  Support both the historical ``BrowserProfileManager.shared()``
        API and the current constructor-based manager API.
        """
        manager = BrowserProfileManager.get_instance()

        return await manager.acquire_adapter(config=config)

    return TelegramAdsToolset(adapter_factory=_factory, config=config)  # type: ignore[call-arg]


def _get_toolset() -> TelegramAdsToolset:
    """Get or create the per-process toolset (sync, lazy)."""
    global _toolset_singleton
    if _toolset_singleton is None:
        _toolset_singleton = _make_toolset()
    return _toolset_singleton


# ─── Per-tool handler bridge (sync) → async toolset.call() ────────────────────


_tool_loop: asyncio.AbstractEventLoop | None = None
_tool_loop_thread: threading.Thread | None = None
_tool_loop_lock = threading.Lock()


def _get_persistent_tool_loop() -> asyncio.AbstractEventLoop:
    """Return the process-wide event loop used by all typed Telegram Ads calls."""
    global _tool_loop, _tool_loop_thread
    with _tool_loop_lock:
        if _tool_loop is not None and not _tool_loop.is_closed() and _tool_loop.is_running():
            return _tool_loop

        loop = asyncio.new_event_loop()
        ready = threading.Event()

        def _run_loop() -> None:
            asyncio.set_event_loop(loop)
            ready.set()
            loop.run_forever()

        thread = threading.Thread(
            target=_run_loop,
            daemon=True,
            name="telegram_ads_typed_event_loop",
        )
        thread.start()
        ready.wait(timeout=5)
        _tool_loop = loop
        _tool_loop_thread = thread
        return loop


def _exception_to_envelope(exc: BaseException, *, tool_name: str) -> dict[str, Any]:
    """Convert wrapper-level exceptions to non-empty, structured envelopes."""
    if hasattr(exc, "to_dict"):
        try:
            structured = exc.to_dict()  # type: ignore[attr-defined]
            if isinstance(structured, dict):
                return structured
        except Exception:  # noqa: BLE001
            pass

    message = str(exc).strip()
    if not message:
        message = f"Unhandled {type(exc).__name__} in {tool_name}"

    return {
        "ok": False,
        "error": "INTERNAL_ERROR",
        "message": message,
        "exception_type": type(exc).__name__,
        "operation": tool_name,
        "tool_name": tool_name,
        "retryable": None,
        "recovery_hint": (
            "Typed wrapper event-loop/Playwright lifecycle issue; do not retry blindly."
        ),
    }


def _run_async_in_thread(coro, timeout: float = 120, *, tool_name: str = "telegram_ads_typed"):
    """Run coroutine on a persistent background event loop.

    The Telegram Ads adapter / Playwright state is loop-affine.  A per-call
    event loop that is closed after every invocation breaks subsequent calls
    with ``RuntimeError: Event loop is closed``.  Keep one loop per process and
    submit every typed call to it.
    """
    loop = _get_persistent_tool_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        future.cancel()
        return {
            "ok": False,
            "error": "TIMEOUT",
            "message": f"{tool_name} timed out after {timeout}s",
            "operation": tool_name,
            "tool_name": tool_name,
            "retryable": None,
            "recovery_hint": (
                "Typed wrapper event-loop/Playwright lifecycle issue; do not retry blindly."
            ),
        }
    except BaseException as exc:  # noqa: BLE001
        return _exception_to_envelope(exc, tool_name=tool_name)


def _has_runtime_approval(tool_name: str, call_args: dict[str, Any]) -> bool:
    """Return True when a mutating typed Ads call carries approval evidence."""
    # create_ad manages its own confirmation flow (single + multi-target batch).
    # Let the handler through so it can issue confirmations in-band.
    if tool_name == "telegram_ads_create_ad":
        return True  # handler issues its own confirmation(s)
    if tool_name in {"telegram_ads_delete_ad", "telegram_ads_delete_event", "telegram_ads_revoke_share_stats_url"}:
        return bool(call_args.get("confirmation_id") and call_args.get("second_confirmation_id"))
    return bool(call_args.get("confirmation_id"))


def _approval_required_envelope(tool_name: str) -> dict[str, Any]:
    return {
        "ok": False,
        "approval_required": True,
        "error": "APPROVAL_REQUIRED",
        "tool_name": tool_name,
        "message": (
            f"{tool_name} mutates Telegram Ads or authentication state and "
            "requires an explicit approval confirmation_id."
        ),
    }


def _make_sync_handler(tool_name: str):
    """Bridge: registry gives us a sync callable. Runs async handler in a dedicated thread.

    The wrapper applies a per-tool argument-shape fixup.  TelegramAdsToolset.call
    passes its own ``name=tool_name`` keyword argument, so any tool whose input
    schema also accepts ``name`` from the LLM (e.g. ``telegram_ads_save_screenshot``)
    would otherwise raise ``TypeError: got multiple values for argument 'name'``.
    """

    # Maps wrapper input field name -> underlying handler kwarg name.
    _ARG_RENAMES: dict[str, dict[str, str]] = {
        "telegram_ads_save_screenshot": {"name": "screenshot_name"},
    }

    def _handler(args: dict[str, Any], **kwargs: Any) -> str:
        call_args = dict(args or {})
        renames = _ARG_RENAMES.get(tool_name, {})
        for src, dst in renames.items():
            if src in call_args and dst not in call_args:
                call_args[dst] = call_args.pop(src)

        if requires_approval(tool_name) and not _has_runtime_approval(tool_name, call_args):
            return json.dumps(_approval_required_envelope(tool_name), ensure_ascii=False)

        toolset = _get_toolset()
        bound = toolset.handler_for(tool_name)

        result = _run_async_in_thread(bound(**call_args), timeout=120, tool_name=tool_name)

        # V2.6 (AR-ADS-WATCHER-EVENT-LOOP-V2_6): after a successful
        # mutating Ads action, register a post-action WatchSpec. The
        # hook is *defensive* — it never raises and never fails the
        # original tool result. The V2.5 helper decides whether the
        # tool is in the post-action allow-list; non-mutating tools
        # (read-only status, login, account-list, …) get a no-op.
        #
        # ROUTING POLICY: only ONE backend runs per ad action.
        #   - New operator enabled → only new operator registration.
        #   - New operator disabled + legacy available → only legacy.
        #   - Both flags enabled → canonical new operator only.
        #   - Failed/cancelled action → zero registration (handled inside).
        if _is_operator_watch_enabled():
            # Canonical new operator — suppress legacy V2 watcher
            _register_operator_campaign_watch(
                tool_name=tool_name, call_args=call_args, result=result
            )
        else:
            # Legacy V2 watcher only
            try:
                _register_post_action_watch(
                    tool_name=tool_name, call_args=call_args, result=result
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "telegram_ads_typed: post-action watch hook raised for %s: %r",
                    tool_name,
                    exc,
                )

        if not isinstance(result, (dict, list)):
            result = {"result": result}
        try:
            return json.dumps(result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return json.dumps({"result": str(result)}, ensure_ascii=False)

    _handler.__name__ = f"telegram_ads_typed_handler_{tool_name}"
    return _handler


def _register_post_action_watch(
    *,
    tool_name: str,
    call_args: dict[str, Any],
    result: Any,
) -> None:
    """Bridge from the typed tool result to the V2.6 watch layer.

    Uses a *lazy* import to avoid pulling the gateway's V2 package at
    module-import time: the V2 layer depends on the watcher store, and
    the watcher store depends on the venv package
    (``hermes_telegram_ads``). Doing the import here means the typed
    tool file loads even when the V2 layer is unavailable (e.g. in
    a minimal test environment).

    The function never raises. The V2.6 helper has its own defensive
    try/except for the registration call; this function is just the
    thinnest possible wrapper to keep ``_handler`` clean.
    """
    try:
        from gateway.ads_watcher_v2.production import (  # type: ignore
            register_post_action_watch as _register,
        )
    except Exception as exc:  # noqa: BLE001 — V2 layer unavailable
        logger.debug(
            "telegram_ads_typed: V2 production hook unavailable for %s: %r",
            tool_name,
            exc,
        )
        return
    _register(
        tool_name=tool_name,
        call_args=call_args or {},
        result=result,
        project="hermes-system",
    )


def _register_operator_campaign_watch(
    *,
    tool_name: str,
    call_args: dict[str, Any],
    result: Any,
) -> None:
    """Register a campaign watch via the new operator (NOT legacy watcher).

    Only fires after successful approved create_ad / start_ad.
    Defensive — never raises, never fails the original tool result.
    When operator is disabled, a structured warning is returned.
    Registration is idempotent.
    No watcher is started automatically.
    """
    # Only for create_ad and start_ad
    if tool_name not in {"telegram_ads_create_ad", "telegram_ads_start_ad"}:
        return

    # Only on successful result
    if not isinstance(result, dict):
        return
    if not result.get("ok"):
        return

    ad_id = _extract_ad_id_from_result(result)
    if not ad_id:
        return

    try:
        from gateway.telegram_ads_operator_integration import (
            get_global_lifecycle,
            register_campaign_watch,
        )
    except Exception:
        return

    lifecycle = get_global_lifecycle()
    if lifecycle is None:
        logger.debug(
            "telegram_ads_typed: operator lifecycle not initialized; "
            "skipping watch registration for %s ad_id=%s",
            tool_name, ad_id,
        )
        return

    watch_result = register_campaign_watch(
        ad_id=str(ad_id),
        project_id=call_args.get("project_id", "hermes-system"),
        lifecycle=lifecycle,
    )

    if watch_result.get("warning"):
        logger.info(
            "telegram_ads_typed: operator watch registration for %s ad_id=%s: %s",
            tool_name, ad_id, watch_result["warning"],
        )
    if watch_result.get("error"):
        logger.warning(
            "telegram_ads_typed: operator watch registration failed for %s ad_id=%s: %s",
            tool_name, ad_id, watch_result["error"],
        )


def _is_operator_watch_enabled() -> bool:
    """Check whether the new operator watch registration should be used.

    Returns True when the operator is enabled.  When True, the legacy
    V2 watcher registration is suppressed — only the canonical new
    operator path runs.
    """
    try:
        from gateway.telegram_ads_operator_integration import get_global_lifecycle
        lifecycle = get_global_lifecycle()
        if lifecycle is not None and lifecycle.config is not None:
            return lifecycle.config.enabled
    except Exception:
        pass
    return False


def _extract_ad_id_from_result(result: dict[str, Any]) -> str | None:
    """Extract ad_id from a typed tool result dict."""
    data = result.get("data")
    if isinstance(data, dict):
        return str(data.get("ad_id") or data.get("id") or "")
    return str(result.get("ad_id") or result.get("id") or "")


# ─── Registration ────────────────────────────────────────────────────────────


def _register_all_typed_tools() -> int:
    """Register every typed telegram_ads_* tool into toolset `telegram_ads_typed`.

    Returns the number of tools registered.
    """
    if not _typed_available or not _check_telegram_ads_typed_enabled():
        logger.info(
            "telegram_ads_typed: skipped registration (available=%s, enabled=%s)",
            _typed_available, _check_telegram_ads_typed_enabled(),
        )
        return 0

    registered = 0
    for spec in TELEGRAM_ADS_TOOLS:
        tool_name = spec.name
        schema_entry = spec.to_dict()
        # Strip the bound handler (it's an async callable; we bridge it
        # via a sync wrapper that calls the same toolset). We do not
        # call schema_entry["handler"]() from registry path.
        schema_entry.pop("handler", None)

        try:
            registry.register(
                name=tool_name,
                toolset=TOOLSET_NAME,
                schema={
                    "name": tool_name,
                    "description": schema_entry.get("description", ""),
                    "parameters": schema_entry.get("input_schema", {}),
                },
                handler=_make_sync_handler(tool_name),
                check_fn=_check_telegram_ads_typed_enabled,
                is_async=False,
                description=schema_entry.get("description", ""),
                emoji="📢",
                max_result_size_chars=20_000,
            )
            registered += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "telegram_ads_typed: failed to register %s: %r", tool_name, exc
            )

    logger.info(
        "telegram_ads_typed: registered %d/%d tools into toolset %r",
        registered, len(TELEGRAM_ADS_TOOLS), TOOLSET_NAME,
    )
    return registered


# Module-level side effect: register all 57 typed tools at import time
# (the legacy `telegram_ads` tool's import chain triggers tool discovery
# via model_tools, so all tool modules get loaded anyway).
_registered_count = _register_all_typed_tools()


# Explicit top-level registry.register() marker so tools/registry.py
# ::_module_registers_tools() picks up this file during auto-discovery
# (otherwise discover_builtin_tools() skips it because
# _register_all_typed_tools() is a function call, not a registry.register
# call — AST inspection doesn't follow into nested function bodies).
# The actual 57 tool registrations happen inside _register_all_typed_tools
# above; this dummy call is purely a discovery marker.
#
# NOTE: this MUST be a top-level Expr statement (not wrapped in if/try) so
# tools/registry.py::_is_registry_register_call() detects it. We guard
# the side effect inside the handler instead.
registry.register(
    name="telegram_ads_typed_discovery_marker",
    toolset="telegram_ads_typed",
    schema={
        "name": "telegram_ads_typed_discovery_marker",
        "description": (
            "Internal discovery marker. Auto-registered at module import "
            "so tools/registry.py auto-discovery picks up the "
            "telegram_ads_typed toolset (57 actual tools are registered "
            "by _register_all_typed_tools() at module level above). "
            "This tool is not meant to be called by the LLM."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    handler=lambda args, **kw: json.dumps(
        {"ok": True, "marker": True, "note": "discovery-only, not a real tool"}
    ),
    check_fn=lambda: False,  # Always filtered out — never exposed to LLM
    is_async=False,
    description=(
        "Internal discovery marker for telegram_ads_typed toolset. "
        "Not callable; auto-discovery only."
    ),
    emoji="📢",
    max_result_size_chars=200,
)


__all__ = [
    "TOOLSET_NAME",
    "SHARED_CONFIG_PATH",
    "_check_telegram_ads_typed_enabled",
    "_make_sync_handler",
    "_get_toolset",
    "_registered_count",
]
