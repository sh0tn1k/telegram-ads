"""Read-only smoke checks for hermes_telegram_ads.watcher integration wiring.

Run: cd /home/hermes/.hermes/hermes-agent && python3 -m pip install -q \
        "git+https://github.com/example/telegram-ads-upstream.git@<PINNED_COMMIT>" \
    && python3 ads_watcher_integration.py

Exit code 0 if all checks PASS, 1 otherwise. Prints a structured per-check
report. Does NOT call WatcherScheduler.run_forever(), does NOT hit Telegram
Ads, does NOT mutate any ad/budget/status/cookie/secret.
"""
from __future__ import annotations

import asyncio
import datetime
import sys
from typing import Any

from ads_watcher_integration import (
    FORBIDDEN_MUTATION_TOOLS,
    HermesTelegramAdsReadOnlyAdapter,
    MutationForbiddenError,
    build_wiring,
    run_once,
)
from hermes_telegram_ads.watcher import WatcherEvent, list_tool_coverage


def _check(label: str, ok: bool, detail: str = "") -> tuple[str, bool, str]:
    status = "PASS" if ok else "FAIL"
    return label, ok, f"{status}{(' — ' + detail) if detail else ''}"


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    # 1. build_wiring creates all four pieces
    try:
        w = build_wiring()
        ok = all([w.store, w.service, w.scheduler, w.adapter, w.consumer])
        results.append(_check("build_wiring_creates_all_pieces", ok))
    except Exception as e:  # noqa: BLE001
        results.append(_check("build_wiring_creates_all_pieces", False, repr(e)))

    a = w.adapter

    # 2. adapter exposes read-only methods
    read_only = [
        "get_ad", "get_ad_stats", "get_account_budget", "get_account_stats",
        "list_ads", "list_accounts", "get_share_stats_url", "validate_ad",
        "detect_login_state", "browser_healthy",
        "get_rejection_info", "get_ad_targeting",
    ]
    missing = [m for m in read_only if not hasattr(a, m)]
    results.append(_check("adapter_exposes_readonly_methods", not missing,
                          f"missing={missing}" if missing else ""))

    # 3. mutating methods hard-fail
    leaked: list[str] = []
    for tool in sorted(FORBIDDEN_MUTATION_TOOLS):
        try:
            getattr(a, tool)
            leaked.append(tool)
        except (MutationForbiddenError, AttributeError):
            pass
    results.append(_check("mutation_guard_hard_fails", not leaked,
                          f"leaked={leaked}" if leaked else ""))

    # 4. scheduler.tick() on idle wiring → no events, no exceptions
    try:
        events = asyncio.run(w.scheduler.tick())
        results.append(_check("scheduler_tick_idle", True, f"events={len(events)}"))
    except Exception as e:  # noqa: BLE001
        results.append(_check("scheduler_tick_idle", False, repr(e)))

    # 5. run_once() on idle wiring → safe one-shot
    try:
        events = asyncio.run(run_once(w))
        results.append(_check("run_once_idle", True, f"events={len(events)}"))
    except Exception as e:  # noqa: BLE001
        results.append(_check("run_once_idle", False, repr(e)))

    # 6. consumer routes fake ad_declined without mutation
    async def _route(ev_type: str, ev_id: str, ad_id: int, reason: str,
                     kind_hook: str = "") -> None:
        ev = WatcherEvent(
            id=ev_id,
            project_id="hermes_main",
            source="telegram_ads_watcher",  # Literal, NOT free-form
            event_type=ev_type,
            severity="warning",
            account_id=None,
            account_token_hash=None,
            ad_id=ad_id,
            watch_spec_id="ws-smoke",
            previous=None,
            current=None,
            reason=reason,
            recommended_agent_action=None,
            created_at=datetime.datetime.now(datetime.timezone.utc),  # required datetime
            dedupe_key=ev_id + "-dedupe",  # required, NOT None
            consumed_at=None,
        )
        await w.consumer(ev)

    try:
        asyncio.run(_route("ad_declined", "smoke-ev-1", 12345, "synthetic-rejection"))
        results.append(_check("consumer_routes_ad_declined", True))
    except MutationForbiddenError as e:
        results.append(_check("consumer_routes_ad_declined", False, repr(e)))
    except Exception as e:  # noqa: BLE001
        results.append(_check("consumer_routes_ad_declined", False, repr(e)))

    # 7. consumer routes fake budget_low
    try:
        asyncio.run(_route("budget_low", "smoke-ev-2", 99999, "synthetic-budget-low"))
        results.append(_check("consumer_routes_budget_low", True))
    except MutationForbiddenError as e:
        results.append(_check("consumer_routes_budget_low", False, repr(e)))
    except Exception as e:  # noqa: BLE001
        results.append(_check("consumer_routes_budget_low", False, repr(e)))

    # 8. list_tool_coverage() still returns 62
    try:
        n = len(list_tool_coverage())
        results.append(_check("coverage_count_is_62", n == 62, f"got={n}"))
    except Exception as e:  # noqa: BLE001
        results.append(_check("coverage_count_is_62", False, repr(e)))

    # Print and decide exit code
    print("smoke_checks:")
    for _label, _ok, msg in results:
        print(f"  {msg}")
    all_ok = all(ok for _, ok, _ in results)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
