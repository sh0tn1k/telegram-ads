"""Persist-safe Hermes registration path for typed ads + research + watcher tools.

[GOAL] One registration/dispatch surface the plugin and tests both call.
[INPUT] Tool name + args; optional PluginContext for live register().
[OUTPUT] Structured envelopes; watcher events can enqueue a model turn.

This module does not import Hermes internals at module load. The plugin
``register(ctx)`` is the live gateway hook; tests call ``dispatch_*`` and
``register_tools(ctx)`` against the same functions.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Callable

from hermes_telegram_ads.paths import default_ads_home, plugin_root
from hermes_telegram_ads.runtime_kwargs import (
    HERMES_PLACEHOLDER_IDS as HERMES_PLACEHOLDER_TASK_IDS,
    HERMES_RUNTIME_INJECTED_KEYS,
    RESEARCH_DECLARED_TASK_ID_TOOLS,
    merge_plugin_handler_args,
    prepare_plugin_payload,
    strip_hermes_runtime_args,
)

logger = logging.getLogger(__name__)

_ADS_HOME = default_ads_home()

ADS_CONFIG_PATH = os.environ.get(
    "HERMES_TELEGRAM_ADS_CONFIG",
    str(Path.home() / ".hermes" / "telegram_ads.yaml"),
)
RESEARCH_CONFIG_PATH = os.environ.get(
    "HERMES_TELEGRAM_RESEARCH_CONFIG",
    str(Path.home() / ".hermes" / "telegram_research.yaml"),
)
WATCHER_DB_PATH = os.environ.get(
    "HERMES_ADS_WATCHER_DB",
    str(_ADS_HOME / "ads_watcher.sqlite3"),
)
DEFAULT_SESSION_KEY_ENV = "HERMES_ADS_WATCHER_SESSION_KEY"

# Plugin checkout first (public git install), then live-host persist-safe copies.
PERSIST_PACKAGE_ROOTS = (
    plugin_root(),
    Path.home() / ".hermes" / "plugins" / "packages" / "hermes_telegram_ads_pkg",
    Path.home() / ".hermes" / "plugins" / "packages" / "telegram_research_pkg" / "src",
)

BUNDLED_SKILL_NAMES = (
    "operate-telegram-ads",
    "operate-telegram-ads-cabinet",
    "prepare-and-manage-tg-ads",
    "telegram-ads-create-ops",
    "telegram-ads-copy-moderation",
    "telegram-ads-cost-modifiers",
    "format-telegram-ads-report",
    "handle-telegram-ads-review-and-declines",
    "create-telegram-ads-campaign-workflow",
    "operate-telegram-ads-growth-loop",
    "telegram-ads-watcher-runtime-enablement",
    "telegram-ads-watcher-event-loop-design",
)

ADS_TOOLSET_NAME = "telegram_ads_typed"
RESEARCH_TOOLSET_NAME = "telegram_research"
WATCHER_TOOLSET_NAME = "telegram_ads_watcher"

_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None
_loop_lock = threading.Lock()
_ads_toolset = None
_research_toolset = None
_watcher_service = None
_watcher_toolset = None
_injector: Callable[..., Any] | None = None
_default_session_key: str | None = None


def ensure_persist_import_paths() -> list[str]:
    """Put persist-safe package trees on sys.path (survives checkout wipe)."""
    import sys

    added: list[str] = []
    for root in PERSIST_PACKAGE_ROOTS:
        path = str(root)
        if root.is_dir() and path not in sys.path:
            sys.path.insert(0, path)
            added.append(path)
    return added


def _get_loop() -> asyncio.AbstractEventLoop:
    global _loop, _loop_thread
    with _loop_lock:
        if _loop is not None and _loop.is_running() and not _loop.is_closed():
            return _loop
        loop = asyncio.new_event_loop()
        ready = threading.Event()

        def _run() -> None:
            asyncio.set_event_loop(loop)
            ready.set()
            loop.run_forever()

        thread = threading.Thread(target=_run, name="telegram-ops-loop", daemon=True)
        thread.start()
        ready.wait(timeout=5)
        _loop = loop
        _loop_thread = thread
        return loop


def run_async(coro: Any, timeout: float = 180) -> Any:
    """Run a coroutine on the process-wide loop (Playwright is loop-affine)."""
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None and running.is_running():
        # Nested: schedule on the dedicated loop if this is a different loop.
        loop = _get_loop()
        if running is loop:
            raise RuntimeError("run_async called on the dedicated loop; await instead")
    loop = _get_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        future.cancel()
        return {
            "ok": False,
            "status": "error",
            "error": "TIMEOUT",
            "message": f"tool timed out after {timeout}s",
        }


def load_ads_config() -> Any:
    from hermes_telegram_ads.config import TelegramAdsConfig

    path = Path(ADS_CONFIG_PATH)
    if path.is_file():
        return TelegramAdsConfig.from_yaml(path)
    cfg = TelegramAdsConfig.default()
    home = default_ads_home()
    cfg.browser.profile_dir = home / "browser_profile"
    cfg.storage.base_path = home
    cfg.storage.resolve()
    cfg.audit.path = home / "telegram_ads_audit.jsonl"
    return cfg


def get_ads_toolset() -> Any:
    """Lazy TelegramAdsToolset wired to the singleton browser manager."""
    global _ads_toolset
    if _ads_toolset is not None:
        return _ads_toolset
    from hermes_telegram_ads.browser_manager import BrowserProfileManager
    from hermes_telegram_ads.hermes_tools import TelegramAdsToolset

    config = load_ads_config()
    config.ensure_paths()

    async def _factory() -> Any:
        manager = BrowserProfileManager.get_instance()
        return await manager.acquire_adapter(config=config)

    _ads_toolset = TelegramAdsToolset(adapter_factory=_factory, config=config)
    return _ads_toolset


def get_research_toolset() -> Any:
    global _research_toolset
    if _research_toolset is not None:
        return _research_toolset
    from telegram_research.hermes_tools import TelegramResearchToolset

    path = RESEARCH_CONFIG_PATH if Path(RESEARCH_CONFIG_PATH).is_file() else None
    _research_toolset = TelegramResearchToolset.from_config(config_path=path)
    return _research_toolset


def get_watcher_service(adapter: Any | None = None) -> Any:
    """In-process watcher service. Shares the ads adapter when attached."""
    global _watcher_service
    if _watcher_service is not None:
        if adapter is not None:
            _watcher_service.adapter = adapter
        return _watcher_service
    from hermes_telegram_ads.watcher.service import TelegramAdsWatcherService
    from hermes_telegram_ads.watcher.store import SQLiteWatcherStore

    db = Path(WATCHER_DB_PATH)
    db.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteWatcherStore(db_path=db)
    _watcher_service = TelegramAdsWatcherService(
        adapter=adapter, store=store, project_id="hermes_main"
    )
    return _watcher_service


def get_watcher_toolset() -> Any:
    global _watcher_toolset
    if _watcher_toolset is not None:
        return _watcher_toolset
    from hermes_telegram_ads.watcher.hermes_tools import TelegramAdsWatcherToolset

    _watcher_toolset = TelegramAdsWatcherToolset(get_watcher_service())
    return _watcher_toolset


def set_injector(injector: Callable[..., Any] | None, session_key: str | None = None) -> None:
    global _injector, _default_session_key
    _injector = injector
    if session_key:
        _default_session_key = session_key


def default_session_key() -> str | None:
    if _default_session_key:
        return _default_session_key
    env = os.environ.get(DEFAULT_SESSION_KEY_ENV, "").strip()
    return env or None


def typed_ads_names() -> list[str]:
    from hermes_telegram_ads.hermes_tools import tool_names

    return list(tool_names())


def typed_research_names() -> list[str]:
    try:
        from telegram_research.hermes_tools import TOOL_NAMES
    except ImportError:
        return []
    return list(TOOL_NAMES)


def typed_watcher_names() -> list[str]:
    from hermes_telegram_ads.watcher.hermes_tools import WATCHER_TOOL_NAMES

    return list(WATCHER_TOOL_NAMES)


def dispatch_ads(name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Dispatch one typed ads tool through TelegramAdsToolset.call."""
    payload = prepare_plugin_payload(name, dict(args or {}))
    toolset = get_ads_toolset()
    # login_from_env + login_wait can sit on app-approval for minutes.
    timeout = 300.0 if name in {"telegram_ads_login_from_env", "telegram_ads_login_wait"} else 180.0
    result = run_async(toolset.call(name, **payload), timeout=timeout)
    if name == "telegram_ads_login_from_env" and isinstance(result, dict):
        data = result.get("data") or {}
        if data.get("phone_submitted") or data.get("state") == "app_approval_pending":
            start_ads_login_wait_background()
    return result


RESEARCH_LOGIN_TOOLS = frozenset(
    {
        "telegram_research_login_status",
        "telegram_research_login_start",
        "telegram_research_login_submit_code",
    }
)


def dispatch_research(name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Dispatch one typed research tool through TelegramResearchToolset.call."""
    payload = prepare_plugin_payload(name, dict(args or {}))
    toolset = get_research_toolset()

    async def _call() -> dict[str, Any]:
        ts = toolset
        # Login tools open an unauthorized Telethon client themselves.
        if (
            name not in RESEARCH_LOGIN_TOOLS
            and hasattr(ts, "tool")
            and getattr(ts.tool, "_client", None) is None
        ):
            try:
                await ts.tool.connect()
            except Exception as exc:  # noqa: BLE001
                from telegram_research.envelope import error_envelope_from_exc
                from telegram_research.safety import SAFETY_REGISTRY

                spec = SAFETY_REGISTRY.get(name)
                mode = getattr(spec, "required_mode", "read_only") if spec else "read_only"
                safety = getattr(spec, "safety_class", "SAFE_READ") if spec else "SAFE_READ"
                return error_envelope_from_exc(
                    name, exc, mode=mode, safety_class=safety
                )
        return await ts.call(name, payload)

    return run_async(_call())


def dispatch_watcher(name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = prepare_plugin_payload(name, dict(args or {}))
    toolset = get_watcher_toolset()
    return run_async(toolset.call(name, **payload))


def dispatch_registered(name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """Same dispatch table the plugin handlers use."""
    if name.startswith("telegram_ads_watch_"):
        return dispatch_watcher(name, args)
    if name.startswith("telegram_research_") or name.startswith("telegram_inspect_") or name.startswith(
        "telegram_score_"
    ) or name.startswith("telegram_build_") or name.startswith("telegram_owned_"):
        return dispatch_research(name, args)
    if name.startswith("telegram_ads_"):
        return dispatch_ads(name, args)
    return {
        "ok": False,
        "status": "error",
        "error": "UNKNOWN_TOOL",
        "message": f"unregistered tool {name}",
    }


def _dumps(result: Any) -> str:
    if not isinstance(result, (dict, list)):
        result = {"result": result}
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return json.dumps({"ok": False, "error": "SERIALIZE", "message": str(result)})


def make_sync_handler(name: str) -> Callable[..., str]:
    def _handler(args: dict | None = None, **kwargs: Any) -> str:
        from hermes_telegram_ads.operator_approval import (
            OPERATOR_APPROVED_ARG,
            is_operator_gated,
            operator_gate_enabled,
        )

        payload = merge_plugin_handler_args(name, args, kwargs)
        # Only after Hermes showed Once/Session/Always/Deny and the operator accepted.
        # If the persist plugin hook is not live, keep the approval_required envelope.
        if (
            operator_gate_enabled()
            and is_operator_gated(name)
            and not payload.get("confirmation_id")
        ):
            payload[OPERATOR_APPROVED_ARG] = True
        return _dumps(dispatch_registered(name, payload))

    _handler.__name__ = f"handler_{name}"
    return _handler


def register_tools(
    ctx: Any,
    *,
    include_research: bool | None = None,
    include_portfolio_prompt: bool | None = None,
) -> dict[str, int]:
    """Register typed ads + watcher tools. Research is optional.

    Live telegram-ops keeps the default (research + portfolio prompt when the
    research package is importable). The public Ads plugin calls
    :func:`register_ads_plugin` instead.
    """
    from hermes_telegram_ads.hermes_tools import TELEGRAM_ADS_TOOLS
    from hermes_telegram_ads.watcher.hermes_tools import watcher_tool_schemas

    research_specs: list[Any] = []
    try:
        from telegram_research.hermes_tools import TELEGRAM_RESEARCH_TOOLS

        research_specs = list(TELEGRAM_RESEARCH_TOOLS)
    except ImportError:
        research_specs = []

    if include_research is None:
        include_research = bool(research_specs)
    if include_portfolio_prompt is None:
        include_portfolio_prompt = bool(research_specs)

    counts = {"ads": 0, "research": 0, "watcher": 0, "skills": 0}
    for spec in TELEGRAM_ADS_TOOLS:
        ctx.register_tool(
            name=spec.name,
            toolset=ADS_TOOLSET_NAME,
            schema={
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.input_schema,
            },
            handler=make_sync_handler(spec.name),
            description=spec.description,
            emoji="📢",
        )
        counts["ads"] += 1
    if include_research:
        for spec in research_specs:
            ctx.register_tool(
                name=spec.name,
                toolset=RESEARCH_TOOLSET_NAME,
                schema={
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.input_schema,
                },
                handler=make_sync_handler(spec.name),
                description=spec.description,
                emoji="🔎",
            )
            counts["research"] += 1
    for schema in watcher_tool_schemas():
        ctx.register_tool(
            name=schema["name"],
            toolset=WATCHER_TOOLSET_NAME,
            schema={
                "name": schema["name"],
                "description": schema["description"],
                "parameters": schema["parameters"],
            },
            handler=make_sync_handler(schema["name"]),
            description=schema["description"],
            emoji="👁",
        )
        counts["watcher"] += 1
    from hermes_telegram_ads.operator_approval import register_operator_approval

    register_operator_approval(ctx)
    if include_portfolio_prompt:
        register_channel_portfolio_prompt(ctx)
    return counts


def register_bundled_skills(ctx: Any) -> int:
    """Register SKILL.md trees shipped next to this package."""
    if not hasattr(ctx, "register_skill"):
        return 0
    root = plugin_root() / "skills"
    n = 0
    for name in BUNDLED_SKILL_NAMES:
        path = root / name
        if not (path / "SKILL.md").is_file():
            continue
        try:
            ctx.register_skill(name, str(path))
            n += 1
        except Exception:  # noqa: BLE001 — older Hermes skill API
            logger.warning("could not register skill %s", name)
    return n


def register_ads_plugin(ctx: Any) -> dict[str, int]:
    """Public Ads-only surface: no research, no GrokBot/portfolio prompt."""
    ensure_persist_import_paths()
    counts = register_tools(
        ctx,
        include_research=False,
        include_portfolio_prompt=False,
    )
    counts["skills"] = register_bundled_skills(ctx)
    return counts


PORTFOLIO_PROMPT_SECTION_ID = "telegram-ops.channel-portfolio"
PORTFOLIO_PROMPT_SECTION = (
    "Owned-channel portfolio memory is host-local, not part of this plugin. "
    "Do not invent subscribers, CPM, demand, or revenue. "
    "Content production is out of scope."
)


def register_channel_portfolio_prompt(ctx: Any) -> None:
    """Keep GrokBot pointed at the persist-safe channel memory tree.

    [GOAL] Inject identity + portfolio path after MEMORY so hermes update
           cannot erase who the agent is or where channel facts live.
    """
    if not hasattr(ctx, "register_system_prompt_section"):
        return
    try:
        ctx.register_system_prompt_section(
            PORTFOLIO_PROMPT_SECTION_ID,
            PORTFOLIO_PROMPT_SECTION,
            position="after_memory",
            max_chars=900,
        )
    except Exception:  # noqa: BLE001 — older Hermes may reject unknown kwargs
        logger.warning("telegram-ops could not register channel-portfolio prompt section")


async def tick_and_enqueue() -> list[Any]:
    """One watcher tick, then enqueue invoke_agent events as model turns."""
    from hermes_telegram_ads.watcher.enqueue import enqueue_unconsumed_invoke_agent_events
    from hermes_telegram_ads.watcher.scheduler import WatcherScheduler

    service = get_watcher_service()
    # Attach the shared ads adapter so ticks reuse the single browser owner.
    if getattr(service, "adapter", None) is None:
        try:
            adapter = await get_ads_toolset()._get_adapter()
            service.adapter = adapter
        except Exception as exc:  # noqa: BLE001
            logger.warning("watcher adapter attach failed: %s", exc)
    scheduler = WatcherScheduler(service=service, poll_interval_sec=30)
    events = await scheduler.tick()
    if _injector is not None:
        await enqueue_unconsumed_invoke_agent_events(
            service, _injector, session_key=default_session_key()
        )
    return events


_watcher_started = False
_ads_login_wait_started = False


def start_ads_login_wait_background(timeout_sec: float = 300.0) -> None:
    """Poll Ads login after env-phone submit; inject when the operator taps Accept."""
    global _ads_login_wait_started
    if _ads_login_wait_started:
        return
    loop = _get_loop()
    asyncio.run_coroutine_threadsafe(_ads_login_wait_and_inject(timeout_sec), loop)
    _ads_login_wait_started = True
    logger.info("telegram-ads env-login waiter scheduled timeout=%ss", timeout_sec)


async def _ads_login_wait_and_inject(timeout_sec: float) -> None:
    global _ads_login_wait_started
    try:
        adapter = await get_ads_toolset()._get_adapter()
        raw = await adapter.login_wait(timeout_sec=timeout_sec, poll_interval_sec=3.0)
        state = raw.get("state")
        logger.info("telegram-ads env-login waiter finished state=%s", state)
        if state != "logged_in" or _injector is None:
            return
        msg = (
            "[telegram-ads] Login accepted. Browser session is saved in the persistent "
            "Chromium profile. Telegram Ads tools can run now."
        )
        _injector(msg, role="user", session_key=default_session_key())
    except Exception:  # noqa: BLE001
        logger.exception("telegram-ads env-login waiter failed")
    finally:
        _ads_login_wait_started = False


def start_watcher_background(poll_interval_sec: int = 30) -> None:
    """Start the watcher on the process-wide ads loop (same as typed tools)."""
    global _watcher_started
    if _watcher_started:
        return
    loop = _get_loop()
    asyncio.run_coroutine_threadsafe(watcher_loop(poll_interval_sec), loop)
    _watcher_started = True
    logger.info("telegram-ops watcher background task scheduled")


async def watcher_loop(poll_interval_sec: int = 30) -> None:
    """In-process scheduler. Idle (no adapter I/O) until a watch exists."""
    from hermes_telegram_ads.watcher.enqueue import enqueue_unconsumed_invoke_agent_events
    from hermes_telegram_ads.watcher.scheduler import WatcherScheduler

    service = get_watcher_service()
    scheduler = WatcherScheduler(service=service, poll_interval_sec=poll_interval_sec)
    logger.info("telegram-ops watcher loop started interval=%ss", poll_interval_sec)
    try:
        while True:
            due = [w for w in await service.list_watches(enabled=True) if w.is_due()]
            if due and getattr(service, "adapter", None) is None:
                try:
                    service.adapter = await get_ads_toolset()._get_adapter()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("watcher skip tick, adapter unavailable: %s", exc)
                    await asyncio.sleep(poll_interval_sec)
                    continue
            await scheduler.tick()
            if _injector is not None:
                await enqueue_unconsumed_invoke_agent_events(
                    service, _injector, session_key=default_session_key()
                )
            await asyncio.sleep(poll_interval_sec)
    except asyncio.CancelledError:
        logger.info("telegram-ops watcher loop cancelled")
        raise


__all__ = [
    "ADS_TOOLSET_NAME",
    "RESEARCH_TOOLSET_NAME",
    "WATCHER_TOOLSET_NAME",
    "default_session_key",
    "dispatch_ads",
    "dispatch_registered",
    "dispatch_research",
    "dispatch_watcher",
    "ensure_persist_import_paths",
    "get_ads_toolset",
    "get_research_toolset",
    "get_watcher_service",
    "make_sync_handler",
    "register_ads_plugin",
    "register_bundled_skills",
    "register_tools",
    "run_async",
    "set_injector",
    "start_ads_login_wait_background",
    "start_watcher_background",
    "tick_and_enqueue",
    "typed_ads_names",
    "typed_research_names",
    "typed_watcher_names",
    "watcher_loop",
    "HERMES_RUNTIME_INJECTED_KEYS",
    "HERMES_PLACEHOLDER_TASK_IDS",
    "RESEARCH_DECLARED_TASK_ID_TOOLS",
    "strip_hermes_runtime_args",
    "prepare_plugin_payload",
]
