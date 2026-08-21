"""Start the Telegram Ads watcher scheduler in read-only mode.

Contract:
- Adapter is HermesTelegramAdsReadOnlyAdapter (mutation guard active).
- No watches are configured at this step — the scheduler ticks every 30s
  and produces 0 events. No real Telegram Ads calls happen until watches
  are added (which is a separate approval gate).
- Graceful shutdown on SIGTERM / SIGINT via WatcherScheduler.stop().

This script does NOT:
- call WatcherScheduler.run_forever() against a real TelegramAdsAdapter
- touch login / phone / OTP
- perform any mutating Telegram Ads action
- modify CPM / budget / status / campaigns

Usage:
    python3 start_ads_watcher.py          # foreground
    # or in background via Hermes terminal(background=true) so the
    # scheduler is tracked in process sessions.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys

from ads_watcher_integration import build_wiring

log = logging.getLogger("ads_watcher.startup")


def main() -> int:
    wiring = build_wiring()  # idle adapter, no watches
    log.info(
        "starting watcher scheduler: project=%s store=%s interval=30s "
        "adapter_idle=%s watches=0",
        "hermes_main",
        "~/.hermes/data/ads_watcher.sqlite3",
        wiring.adapter._adapter is None,
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _request_stop() -> None:
        log.info("shutdown signal received, stopping scheduler")
        wiring.scheduler.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            # Some environments (e.g. threads) don't support add_signal_handler
            signal.signal(sig, lambda *_: _request_stop())

    try:
        loop.run_until_complete(wiring.scheduler.run_forever())
    finally:
        loop.close()
        log.info("scheduler loop closed")
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    sys.exit(main())
