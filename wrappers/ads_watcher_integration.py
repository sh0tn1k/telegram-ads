"""Hermes integration wiring for hermes_telegram_ads.watcher.

Wiring only — no scheduler start, no Telegram Ads mutating actions.
Pinned: hermes_telegram_ads 0.1.0 @ d6f7cdb66fff08e6210a117c5a099bcb8cdce883
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from hermes_telegram_ads.watcher import (
    SQLiteWatcherStore,
    TelegramAdsWatcherService,
    WatcherEvent,
    WatcherScheduler,
)
from hermes_telegram_ads.hermes_tools import TelegramAdsAdapter

log = logging.getLogger("hermes.ads_watcher")

DEFAULT_STORE_PATH = Path(
    os.environ.get(
        "HERMES_ADS_WATCHER_DB",
        str(Path.home() / ".hermes" / "data" / "ads_watcher.sqlite3"),
    )
)

# Tools forbidden to the adapter (mutation + sensitive). Listed both as a
# guard for the consumer and to make the contract obvious in code review.
FORBIDDEN_MUTATION_TOOLS: frozenset[str] = frozenset(
    {
        "create_ad", "edit_ad", "change_cpm", "add_to_budget",
        "withdraw_from_budget", "start_ad", "stop_ad", "delete_ad",
        "set_budget", "archive_ad", "set_schedule", "set_targeting",
        "set_conversion_event", "set_pixel", "apply_approved_action",
        "login_start", "login_submit_phone",
    }
)


# ─── Mutation guard ─────────────────────────────────────────────────────────

class MutationForbiddenError(RuntimeError):
    """Raised when the watcher or consumer tries to invoke a mutating tool."""

    def __init__(self, tool_name: str) -> None:
        super().__init__(f"mutation tool {tool_name!r} is forbidden in read-only adapter")
        self.tool_name = tool_name


def _assert_readonly(tool_name: str) -> None:
    if tool_name in FORBIDDEN_MUTATION_TOOLS:
        raise MutationForbiddenError(tool_name)


# ─── Read-only adapter ──────────────────────────────────────────────────────

class HermesTelegramAdsReadOnlyAdapter:
    """Read-only wrapper around the Hermes Telegram Ads tools.

    Maps the 10 async methods the watcher service actually calls, plus two
    convenience methods (get_rejection_info, get_ad_targeting) used by the
    consumer. All calls go through `TelegramAdsAdapter` — the same object
    the typed `telegram_ads_*` tools wrap — so we share the browser profile
    manager and never touch the network by ourselves.

    All mutating methods are absent: any attempt to call them raises
    MutationForbiddenError before reaching the adapter.
    """

    def __init__(self, adapter: Optional[TelegramAdsAdapter] = None) -> None:
        # Lazy by default. Pass a real adapter only when the operator is
        # ready to let the watcher hit Telegram Ads. Until then the
        # adapter methods raise and the scheduler tick produces no events.
        self._adapter = adapter

    # ── private helpers ────────────────────────────────────────────────────

    def _require_adapter(self) -> TelegramAdsAdapter:
        if self._adapter is None:
            raise RuntimeError(
                "HermesTelegramAdsReadOnlyAdapter is in idle mode: no underlying "
                "TelegramAdsAdapter has been attached. Wire one before tick()."
            )
        return self._adapter

    @staticmethod
    def _to_dict(obj: Any) -> dict[str, Any]:
        """Normalise a Pydantic v2 model, dataclass, or plain dict to dict."""
        if obj is None:
            return {}
        if isinstance(obj, dict):
            return obj
        if hasattr(obj, "model_dump"):
            try:
                return obj.model_dump()
            except Exception:  # noqa: BLE001
                pass
        if hasattr(obj, "dict") and callable(obj.dict):
            try:
                return obj.dict()
            except Exception:  # noqa: BLE001
                pass
        return {k: getattr(obj, k) for k in getattr(obj, "__fields__", getattr(obj, "__annotations__", []))}

    # ── 10 methods the watcher service actually calls ──────────────────────

    async def get_ad(self, ad_id: int) -> dict[str, Any]:
        a = self._require_adapter()
        return self._to_dict(await a.get_ad(ad_id))

    async def get_ad_stats(self, ad_id: int) -> dict[str, Any]:
        a = self._require_adapter()
        return self._to_dict(await a.get_ad_stats(ad_id))

    async def get_account_budget(self) -> dict[str, Any]:
        a = self._require_adapter()
        return self._to_dict(await a.get_account_budget())

    async def get_account_stats(self) -> dict[str, Any]:
        # The underlying adapter doesn't expose account-level stats directly.
        # We synthesise a dict with a stats URL placeholder from the current
        # account budget. Returning `{}` would be acceptable too — the service
        # only calls this for `account_stats` watches.
        budget = await self.get_account_budget()
        return {"url": None, "balance": budget.get("balance"), "currency": budget.get("currency")}

    async def list_ads(self) -> list[dict[str, Any]]:
        a = self._require_adapter()
        return [self._to_dict(x) for x in await a.list_ads()]

    async def list_accounts(self) -> list[dict[str, Any]]:
        a = self._require_adapter()
        return [self._to_dict(x) for x in await a.list_accounts()]

    async def get_share_stats_url(self, ad_id: int) -> str | None:
        a = self._require_adapter()
        return await a.get_share_stats_url(ad_id)

    async def validate_ad(self, draft: Any) -> dict[str, Any]:
        a = self._require_adapter()
        result = await a.validate_ad(draft)
        return result if isinstance(result, dict) else self._to_dict(result)

    async def detect_login_state(self, *, navigate: bool = True) -> dict[str, Any]:
        a = self._require_adapter()
        return await a.detect_login_state(navigate=navigate)

    def browser_healthy(self) -> bool:
        a = self._require_adapter()
        return a.browser_healthy()

    # ── 2 convenience methods for the consumer (not in service tick path) ─

    async def get_rejection_info(self, ad_id: int) -> dict[str, Any]:
        ad = await self.get_ad(ad_id)
        return {
            "ad_id": ad_id,
            "status": ad.get("status"),
            "raw_status": ad.get("raw_status"),
            "rejection_reason": ad.get("rejection_reason") or ad.get("rejection"),
        }

    async def get_ad_targeting(self, ad_id: int) -> dict[str, Any]:
        ad = await self.get_ad(ad_id)
        # Targeting info surfaces on AdDetail in the underlying package
        return {
            "ad_id": ad_id,
            "targets": ad.get("targets"),
            "target_type": ad.get("target_type"),
            "exclude_channels": ad.get("exclude_channels"),
        }

    # ── Mutation guard (hard-fail) ─────────────────────────────────────────

    def __getattr__(self, name: str) -> Any:
        # Called only when normal lookup fails. Mutation tools never exist
        # on this class, so this path is reached for them.
        _assert_readonly(name)
        raise AttributeError(
            f"HermesTelegramAdsReadOnlyAdapter has no attribute {name!r}"
        )


# ─── Event consumer with route table ────────────────────────────────────────

@dataclass
class _ConsumerAction:
    """Pluggable action handler. Replace fields in production wiring."""
    send_internal_notification: Any = None
    create_review_task: Any = None
    draft_approval_request: Any = None
    mark_verified: Any = None
    diagnostic_task: Any = None
    manual_login_alert: Any = None
    operational_alert: Any = None


class AdsWatcherConsumer:
    """Stub event consumer with explicit route table.

    Each event_type maps to a single `route_*` method. The methods are
    default no-ops; the integration owner swaps them out for real
    notification/task/approval hooks. This consumer is read-only — it
    does NOT call any mutating Telegram Ads tool.
    """

    def __init__(self, actions: Optional[_ConsumerAction] = None) -> None:
        self.actions = actions or _ConsumerAction()
        self.routes = {
            "ad_approved": self.route_ad_approved,
            "ad_declined": self.route_ad_declined,
            "budget_low": self.route_budget_low,
            "account_balance_low": self.route_account_balance_low,
            "post_action_verified": self.route_post_action_verified,
            "post_action_not_verified": self.route_post_action_not_verified,
            "login_required": self.route_login_required,
            "watch_error": self.route_watch_error,
        }

    async def __call__(self, event: WatcherEvent) -> None:
        handler = self.routes.get(event.event_type)
        if handler is None:
            log.info("ads_watcher no route for event_type=%s id=%s", event.event_type, event.id)
            return
        await handler(event)

    # ── route handlers (read-only, no mutation) ───────────────────────────

    async def route_ad_approved(self, event: WatcherEvent) -> None:
        log.info("ads_watcher ad_approved id=%s ad_id=%s", event.id, event.ad_id)
        if self.actions.send_internal_notification:
            await _maybe_await(self.actions.send_internal_notification(event))

    async def route_ad_declined(self, event: WatcherEvent) -> None:
        log.warning("ads_watcher ad_declined id=%s ad_id=%s reason=%s", event.id, event.ad_id, event.reason)
        if self.actions.create_review_task:
            await _maybe_await(self.actions.create_review_task(event, kind="review_declined_ad"))

    async def route_budget_low(self, event: WatcherEvent) -> None:
        log.warning("ads_watcher budget_low id=%s ad_id=%s", event.id, event.ad_id)
        if self.actions.draft_approval_request:
            await _maybe_await(self.actions.draft_approval_request(event, kind="increase_budget"))

    async def route_account_balance_low(self, event: WatcherEvent) -> None:
        log.warning("ads_watcher account_balance_low id=%s", event.id)
        if self.actions.draft_approval_request:
            await _maybe_await(self.actions.draft_approval_request(event, kind="top_up_account"))

    async def route_post_action_verified(self, event: WatcherEvent) -> None:
        log.info("ads_watcher post_action_verified id=%s ad_id=%s", event.id, event.ad_id)
        if self.actions.mark_verified:
            await _maybe_await(self.actions.mark_verified(event))

    async def route_post_action_not_verified(self, event: WatcherEvent) -> None:
        log.error("ads_watcher post_action_not_verified id=%s ad_id=%s reason=%s", event.id, event.ad_id, event.reason)
        if self.actions.diagnostic_task:
            await _maybe_await(self.actions.diagnostic_task(event))

    async def route_login_required(self, event: WatcherEvent) -> None:
        log.error("ads_watcher login_required id=%s reason=%s", event.id, event.reason)
        if self.actions.manual_login_alert:
            await _maybe_await(self.actions.manual_login_alert(event))

    async def route_watch_error(self, event: WatcherEvent) -> None:
        log.error("ads_watcher watch_error id=%s reason=%s", event.id, event.reason)
        if self.actions.operational_alert:
            await _maybe_await(self.actions.operational_alert(event))


async def _maybe_await(coro_or_value: Any) -> None:
    if coro_or_value is None:
        return
    if asyncio.iscoroutine(coro_or_value):
        await coro_or_value


# ─── Wiring container ───────────────────────────────────────────────────────

@dataclass
class AdsWatcherWiring:
    store: SQLiteWatcherStore
    service: TelegramAdsWatcherService
    scheduler: WatcherScheduler
    adapter: HermesTelegramAdsReadOnlyAdapter
    consumer: AdsWatcherConsumer


def build_wiring(
    adapter: Optional[HermesTelegramAdsReadOnlyAdapter] = None,
    *,
    project_id: str = "hermes_main",
    store_path: Optional[Path] = None,
    poll_interval_sec: int = 30,
    consumer_actions: Optional[_ConsumerAction] = None,
) -> AdsWatcherWiring:
    """Construct store / service / scheduler / adapter / consumer.

    Scheduler is NOT started. Pass `adapter=...` to attach a real
    TelegramAdsAdapter; pass `adapter=None` (default) for idle mode.
    """
    if store_path is None:
        store_path = DEFAULT_STORE_PATH
    store_path.parent.mkdir(parents=True, exist_ok=True)

    store = SQLiteWatcherStore(db_path=store_path)
    ro_adapter = adapter or HermesTelegramAdsReadOnlyAdapter(adapter=None)
    service = TelegramAdsWatcherService(adapter=ro_adapter, store=store, project_id=project_id)
    scheduler = WatcherScheduler(service=service, poll_interval_sec=poll_interval_sec)
    consumer = AdsWatcherConsumer(actions=consumer_actions or _ConsumerAction())

    log.info(
        "ads_watcher wiring built (project=%s, store=%s, interval=%ss, "
        "adapter_idle=%s, scheduler_started=False)",
        project_id,
        store_path,
        poll_interval_sec,
        ro_adapter._adapter is None,
    )
    return AdsWatcherWiring(
        store=store,
        service=service,
        scheduler=scheduler,
        adapter=ro_adapter,
        consumer=consumer,
    )


async def run_once(wiring: AdsWatcherWiring) -> list[WatcherEvent]:
    """Run a single tick + route resulting events through the consumer.

    Use this for unit tests, smoke checks, and operator-driven one-shots.
    Does NOT call WatcherScheduler.run_forever() — that requires explicit
    the operator approval. Returns the events from this tick.
    """
    events = await wiring.scheduler.tick()
    for ev in events:
        await wiring.consumer(ev)
    return events


def run(wiring: AdsWatcherWiring) -> None:
    """DEPRECATED entrypoint. Long-running use requires explicit approval.

    The scheduler's `run_forever()` would call `tick()` in a loop and emit
    events that the consumer routes. We deliberately do NOT call
    `run_forever()` here — the operator must wire that into the gateway
    loop with the operator's explicit approval, after confirming the adapter
    has a live TelegramAdsAdapter attached.
    """
    raise RuntimeError(
        "ads_watcher_integration.run() is disabled. Use `await run_once(wiring)` "
        "for one-shot ticks, or wire WatcherScheduler.run_forever() into the "
        "Hermes gateway with explicit operator approval."
    )


# ─── Smoke checks ───────────────────────────────────────────────────────────

def smoke_checks() -> dict[str, Any]:
    """Run the full read-only smoke suite. No network, no mutation.

    Returns a dict {check: status, details}. Safe to call repeatedly.
    """
    results: dict[str, Any] = {}

    # 1. build_wiring creates all four pieces
    try:
        w = build_wiring()
        ok = all([w.store, w.service, w.scheduler, w.adapter, w.consumer])
        results["build_wiring_creates_all_pieces"] = "PASS" if ok else "FAIL"
    except Exception as e:  # noqa: BLE001
        results["build_wiring_creates_all_pieces"] = f"FAIL: {e!r}"

    # 2. adapter exposes read-only methods
    a = w.adapter
    read_only_methods = [
        "get_ad", "get_ad_stats", "get_account_budget", "get_account_stats",
        "list_ads", "list_accounts", "get_share_stats_url", "validate_ad",
        "detect_login_state", "browser_healthy", "get_rejection_info", "get_ad_targeting",
    ]
    missing = [m for m in read_only_methods if not hasattr(a, m)]
    results["adapter_exposes_readonly_methods"] = "PASS" if not missing else f"FAIL: missing {missing}"

    # 3. mutating methods hard-fail
    forbidden_calls = []
    for tool in sorted(FORBIDDEN_MUTATION_TOOLS):
        try:
            getattr(a, tool)
            forbidden_calls.append(f"{tool} did not raise")
        except MutationForbiddenError:
            pass
        except AttributeError:
            # Acceptable: also hard-fails (no such attribute on adapter)
            pass
    results["mutation_guard_hard_fails"] = "PASS" if not forbidden_calls else f"FAIL: {forbidden_calls}"

    # 4. scheduler.tick() with no due watches does not call Telegram Ads
    try:
        events = asyncio.run(w.scheduler.tick())
        results["scheduler_tick_idle"] = f"PASS (events={len(events)})"
    except Exception as e:  # noqa: BLE001
        # If no watches exist, the tick should be a clean no-op.
        results["scheduler_tick_idle"] = f"FAIL: {e!r}"

    # 4b. run_once() works on idle wiring (no events → no consumer calls)
    try:
        events = asyncio.run(run_once(w))
        results["run_once_idle"] = f"PASS (events={len(events)})"
    except Exception as e:  # noqa: BLE001
        results["run_once_idle"] = f"FAIL: {e!r}"

    # 5. event consumer routes fake ad_declined without mutating
    async def _route_check() -> None:
        from hermes_telegram_ads.watcher.events import WatcherEvent
        ev = WatcherEvent(
            id="ev-test-1",
            project_id="hermes_main",
            source="telegram_ads_watcher",
            event_type="ad_declined",
            severity="warning",
            account_id=None,
            account_token_hash=None,
            ad_id=12345,
            watch_spec_id="ws-test",
            previous=None,
            current=None,
            reason="synthetic-test-rejection",
            recommended_agent_action=None,
            created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            dedupe_key="ev-test-1-dedupe",
            consumed_at=None,
        )
        await w.consumer(ev)
    try:
        asyncio.run(_route_check())
        results["consumer_routes_ad_declined"] = "PASS"
    except MutationForbiddenError as e:
        results["consumer_routes_ad_declined"] = f"FAIL: consumer triggered mutation {e!r}"
    except Exception as e:  # noqa: BLE001
        results["consumer_routes_ad_declined"] = f"FAIL: {e!r}"

    # 6. event consumer routes fake budget_low into approval draft
    async def _budget_check() -> None:
        from hermes_telegram_ads.watcher.events import WatcherEvent
        ev = WatcherEvent(
            id="ev-test-2",
            project_id="hermes_main",
            source="telegram_ads_watcher",
            event_type="budget_low",
            severity="warning",
            account_id=None,
            account_token_hash=None,
            ad_id=99999,
            watch_spec_id="ws-test",
            previous=None,
            current=None,
            reason="synthetic-test-budget-low",
            recommended_agent_action=None,
            created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            dedupe_key="ev-test-2-dedupe",
            consumed_at=None,
        )
        await w.consumer(ev)
    try:
        asyncio.run(_budget_check())
        results["consumer_routes_budget_low"] = "PASS"
    except MutationForbiddenError as e:
        results["consumer_routes_budget_low"] = f"FAIL: consumer triggered mutation {e!r}"
    except Exception as e:  # noqa: BLE001
        results["consumer_routes_budget_low"] = f"FAIL: {e!r}"

    # 7. list_tool_coverage() still returns 62
    try:
        from hermes_telegram_ads.watcher import list_tool_coverage
        n = len(list_tool_coverage())
        results["coverage_count_is_62"] = "PASS" if n == 62 else f"FAIL: got {n}"
    except Exception as e:  # noqa: BLE001
        results["coverage_count_is_62"] = f"FAIL: {e!r}"

    return results


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    w = build_wiring()
    print("wiring_ready:", {
        "store": type(w.store).__name__,
        "service": type(w.service).__name__,
        "scheduler": type(w.scheduler).__name__,
        "adapter": type(w.adapter).__name__,
        "consumer": type(w.consumer).__name__,
        "scheduler_started": False,
    })
    print("\nsmoke_checks:")
    for k, v in smoke_checks().items():
        print(f"  {k}: {v}")
