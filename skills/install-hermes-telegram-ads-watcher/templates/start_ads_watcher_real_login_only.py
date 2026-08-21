"""Start Telegram Ads watcher with real read-only adapter, login_state only.

Template: copy to the Hermes repo root as `start_ads_watcher_real_login_only.py`,
then replace WATCH_ID / PROJECT_ID / EXPECTED_INTERVAL with operator-approved values.

Approved scope:
- one existing watch only: WATCH_ID / login_state / PROJECT_ID / EXPECTED_INTERVAL
- periodic detect_login_state only
- no additional watches
- no campaign/account budget/stats reads
- no mutations
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys

from hermes_telegram_ads.browser_manager import BrowserProfileManager
from hermes_telegram_ads.hermes_tools import TelegramAdsConfig

from ads_watcher_integration import (
    HermesTelegramAdsReadOnlyAdapter,
    build_wiring,
)

WATCH_ID = "REPLACE_WITH_APPROVED_WATCH_ID"
PROJECT_ID = "hermes_main"
EXPECTED_INTERVAL = 600

log = logging.getLogger("ads_watcher.real_login_only")


async def _validate_single_watch(wiring) -> None:
    watches = await wiring.service.list_watches(project_id=PROJECT_ID, enabled=True)
    if len(watches) != 1:
        raise RuntimeError(f"expected exactly 1 enabled watch, got {len(watches)}")
    w = watches[0]
    if (
        w.id != WATCH_ID
        or w.kind != "login_state"
        or w.project_id != PROJECT_ID
        or w.interval_sec != EXPECTED_INTERVAL
    ):
        raise RuntimeError(
            "unexpected enabled watch: "
            f"id={w.id} kind={w.kind} project={w.project_id} interval={w.interval_sec}"
        )
    log.info("validated single enabled watch id=%s kind=%s interval=%s", w.id, w.kind, w.interval_sec)


async def main_async() -> int:
    manager = BrowserProfileManager.get_instance()
    config = TelegramAdsConfig.default()
    adapter = None
    wiring = None
    try:
        log.info("acquiring real TelegramAdsAdapter via BrowserProfileManager")
        adapter = await manager.acquire_adapter(config=config, timeout=30.0)
        ro_adapter = HermesTelegramAdsReadOnlyAdapter(adapter=adapter)
        wiring = build_wiring(adapter=ro_adapter, project_id=PROJECT_ID, poll_interval_sec=30)
        await _validate_single_watch(wiring)

        # Immediate gate verification: existing watch only, detect_login_state only.
        events = await wiring.service.run_watch_once(WATCH_ID)
        log.info("initial run_watch_once completed events=%d", len(events))

        def _request_stop() -> None:
            log.info("shutdown signal received, stopping scheduler")
            if wiring is not None:
                wiring.scheduler.stop()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _request_stop)
            except NotImplementedError:
                signal.signal(sig, lambda *_: _request_stop())

        await wiring.scheduler.run_forever()
        return 0
    finally:
        if adapter is not None:
            try:
                manager.release_adapter()
                log.info("adapter released")
            except Exception as e:  # noqa: BLE001
                log.warning("adapter release failed: %r", e)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
