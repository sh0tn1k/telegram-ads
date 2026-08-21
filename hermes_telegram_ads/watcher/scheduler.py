"""WatcherScheduler — a minimal asyncio polling loop.

Drives :meth:`TelegramAdsWatcherService.run_due_watches` on a fixed interval.
Deliberately tiny: no Celery/cron/Redis, no threads, no AI. It only asks the
service which watches are due and lets the service do the read-only work.

Guarantees:
    * never performs a mutating Telegram Ads action (it can't — it only calls
      ``run_due_watches``);
    * one failing watch/tick is logged and the loop continues;
    * stops cleanly on ``asyncio.CancelledError`` (task cancellation).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hermes_telegram_ads.watcher.models import WatcherEvent
    from hermes_telegram_ads.watcher.service import TelegramAdsWatcherService

logger = logging.getLogger(__name__)


class WatcherScheduler:
    """Run due watches every ``poll_interval_sec`` seconds until cancelled."""

    def __init__(
        self,
        service: TelegramAdsWatcherService,
        poll_interval_sec: int = 30,
    ) -> None:
        self.service = service
        self.poll_interval_sec = max(1, int(poll_interval_sec))
        self._stop = asyncio.Event()

    async def tick(self) -> list[WatcherEvent]:
        """Run one polling cycle. Never raises — failures are logged.

        Returns the events created this tick (empty list on failure)."""
        try:
            return await self.service.run_due_watches()
        except Exception as exc:  # a whole-cycle failure must not kill the loop
            logger.exception("WatcherScheduler.tick failed: %s", exc)
            return []

    async def run_forever(self) -> None:
        """Tick on a fixed interval until cancelled or :meth:`stop` is called.

        Sleeps are cancellation-aware: an ``asyncio.CancelledError`` during the
        wait stops the loop promptly and is re-raised so the caller's task is
        properly cancelled.
        """
        self._stop.clear()
        logger.info("WatcherScheduler started (poll_interval=%ss)", self.poll_interval_sec)
        try:
            while not self._stop.is_set():
                await self.tick()
                # Wait out the interval, but wake early if stop() is signalled.
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval_sec)
        except asyncio.CancelledError:
            logger.info("WatcherScheduler cancelled — stopping")
            raise
        finally:
            logger.info("WatcherScheduler stopped")

    def stop(self) -> None:
        """Request a graceful stop (loop exits after the current tick/sleep)."""
        self._stop.set()
