"""Telegram Ads watcher — deterministic, read-only monitoring for Hermes.

A scheduler-driven layer that polls Telegram Ads (read-only, via an existing
``TelegramAdsAdapter``), persists local snapshots to SQLite, diffs them, and
emits internal *watcher events* for Hermes to consume. No AI in the
polling loop; no mutating Telegram Ads actions anywhere in this package.

Quick start::

    from hermes_telegram_ads.watcher import (
        SQLiteWatcherStore,
        TelegramAdsWatcherService,
    )

    store = SQLiteWatcherStore()                      # ~/.hermes/telegram_ads_watcher.db
    service = TelegramAdsWatcherService(
        adapter=telegram_ads_adapter, store=store, project_id="opusclips"
    )

    watch = await service.create_watch(
        kind="moderation_result", ad_id=123, interval_sec=600, invoke_agent=True
    )
    events = await service.run_due_watches()
    unconsumed = await service.list_events(unconsumed=True)

See ``docs/WATCHER.md`` for the full guide.
"""

from hermes_telegram_ads.watcher.coverage import (
    ToolCoverage,
    assert_no_mutating_tools_in_watcher,
    direct_watch_kinds,
    get_tool_coverage,
    list_tool_coverage,
    post_action_watch_kinds,
)
from hermes_telegram_ads.watcher.enqueue import (
    build_agent_turn_prompt,
    enqueue_unconsumed_invoke_agent_events,
    enqueue_watcher_agent_turn,
)
from hermes_telegram_ads.watcher.models import (
    AccountSnapshot,
    AdSnapshot,
    ResourceSnapshot,
    WatcherEvent,
    WatchSpec,
    hash_account_token,
)
from hermes_telegram_ads.watcher.recipes import create_post_action_watches
from hermes_telegram_ads.watcher.scheduler import WatcherScheduler
from hermes_telegram_ads.watcher.service import TelegramAdsWatcherService
from hermes_telegram_ads.watcher.store import SQLiteWatcherStore

__all__ = [
    "AccountSnapshot",
    "AdSnapshot",
    "ResourceSnapshot",
    "SQLiteWatcherStore",
    "TelegramAdsWatcherService",
    "ToolCoverage",
    "WatchSpec",
    "WatcherEvent",
    "WatcherScheduler",
    "assert_no_mutating_tools_in_watcher",
    "build_agent_turn_prompt",
    "create_post_action_watches",
    "direct_watch_kinds",
    "enqueue_unconsumed_invoke_agent_events",
    "enqueue_watcher_agent_turn",
    "get_tool_coverage",
    "hash_account_token",
    "list_tool_coverage",
    "post_action_watch_kinds",
]
