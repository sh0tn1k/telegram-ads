"""Drive the shipped watcher subscribe → tick → enqueue path.

The adapter is a test double (no live Telegram). The service, store, coverage
matrix, subscribe tools, and enqueue helper are the real shipped functions.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hermes_telegram_ads.watcher.coverage import direct_watch_kinds, list_tool_coverage
from hermes_telegram_ads.watcher.enqueue import (
    build_agent_turn_prompt,
    enqueue_unconsumed_invoke_agent_events,
)
from hermes_telegram_ads.watcher.hermes_tools import TelegramAdsWatcherToolset
from hermes_telegram_ads.watcher.service import TelegramAdsWatcherService
from hermes_telegram_ads.watcher.store import SQLiteWatcherStore


class ScriptedAdAdapter:
    """Read-only adapter that returns a scripted get_ad payload sequence."""

    def __init__(self, ads: dict[int, list[dict]]) -> None:
        self._ads = ads
        self.calls: list[str] = []

    async def get_ad(self, ad_id):
        self.calls.append(f"get_ad:{ad_id}")
        queue = self._ads[int(ad_id)]
        payload = queue.pop(0) if len(queue) > 1 else queue[0]
        return payload

    async def get_ad_stats(self, ad_id):
        return {"views": 0, "csv_url": None}

    async def get_account_budget(self):
        return {"balance": 10.0, "currency": "TON"}

    async def get_account_stats(self):
        return {"url": None}

    async def list_ads(self):
        return []

    async def list_accounts(self):
        return []

    async def get_share_stats_url(self, ad_id):
        return None

    async def validate_ad(self, draft):
        return {"error": "", "field": ""}

    async def detect_login_state(self, *, navigate: bool = True):
        return {"logged_in": True}

    def browser_healthy(self) -> bool:
        return True


def _service(tmp_path, adapter) -> TelegramAdsWatcherService:
    store = SQLiteWatcherStore(db_path=tmp_path / "watcher.sqlite3")
    return TelegramAdsWatcherService(adapter=adapter, store=store, project_id="test")


@pytest.mark.asyncio
async def test_coverage_direct_watch_kinds_are_creatable(tmp_path) -> None:
    kinds = direct_watch_kinds()
    required = {
        "tool_status",
        "login_state",
        "accounts_snapshot",
        "account_balance",
        "account_budget",
        "campaign_list",
        "campaign_detail",
        "campaign_status",
        "moderation_result",
        "rejection_info",
        "campaign_targeting",
        "targeting_lock_state",
        "campaign_budget",
        "campaign_spend",
        "campaign_cpm",
        "campaign_stats",
        "campaign_performance",
        "share_stats_state",
        "draft_validation",
    }
    missing = required - set(kinds)
    assert not missing, f"coverage matrix missing direct_watch kinds: {sorted(missing)}"

    adapter = ScriptedAdAdapter({1: [{"status": "in_review", "title": "x"}]})
    tools = TelegramAdsWatcherToolset(_service(tmp_path, adapter))
    created = []
    for kind in kinds:
        kwargs = {"kind": kind, "invoke_agent": True, "interval_sec": 60}
        if kind == "draft_validation":
            kwargs["thresholds"] = {"draft": {"title": "t", "text": "hello", "cpm": 1}}
        elif kind not in {
            "tool_status",
            "login_state",
            "accounts_snapshot",
            "account_balance",
            "account_budget",
            "campaign_list",
        }:
            kwargs["ad_id"] = 1
        result = await tools.create_watch(**kwargs)
        assert result["ok"] is True, (kind, result)
        created.append(result["data"]["id"])
        await tools.disable_watch(watch_id=result["data"]["id"])
    assert len(created) == len(kinds)


@pytest.mark.asyncio
async def test_moderation_change_emits_event_and_enqueues_model_turn(tmp_path) -> None:
    adapter = ScriptedAdAdapter(
        {
            42: [
                {"status": "in_review", "title": "Camp", "budget": 100, "spent": 0},
                {
                    "status": "declined",
                    "title": "Camp",
                    "budget": 100,
                    "spent": 0,
                    "rejection_reason": "misleading claims",
                },
            ]
        }
    )
    service = _service(tmp_path, adapter)
    tools = TelegramAdsWatcherToolset(service)

    created = await tools.create_watch(
        kind="moderation_result",
        ad_id=42,
        interval_sec=1,
        invoke_agent=True,
        session_key="agent:main:telegram:dm:test",
        context="watch this campaign after we launched it",
    )
    assert created["ok"] is True
    watch_id = created["data"]["id"]
    assert created["data"]["invoke_agent"] is True

    first = await service.run_watch_once(watch_id)
    # First observation stores a snapshot; no status-diff events yet.
    assert all(e.event_type != "ad_declined" for e in first)

    # Force the watch due again.
    spec = await service.get_watch(watch_id)
    service.store.update_watch(watch_id, next_run_at=spec.created_at)

    second = await service.run_watch_once(watch_id)
    types = {e.event_type for e in second}
    assert "ad_declined" in types or "ad_status_changed" in types, types
    fired = next(e for e in second if e.event_type in {"ad_declined", "ad_status_changed"})
    assert fired.previous is not None
    assert fired.current is not None

    recorded: list[dict] = []

    def injector(content, role="user", session_key=None):
        recorded.append({"content": content, "role": role, "session_key": session_key})
        return True

    delivered = await enqueue_unconsumed_invoke_agent_events(
        service, injector, session_key="fallback-session"
    )
    assert delivered, "enqueue helper must consume invoke_agent events"
    assert recorded, "injector must be called"
    turn = recorded[0]
    assert turn["role"] == "user"
    assert turn["session_key"] == "agent:main:telegram:dm:test"
    prompt = turn["content"]
    assert "You are the model" in prompt
    assert "canned system/status bubble" in prompt
    assert watch_id in prompt
    assert "ad_declined" in prompt or "ad_status_changed" in prompt
    assert "watch this campaign after we launched it" in prompt

    leftover = await service.list_events(unconsumed=True)
    leftover_ids = {e.id for e in leftover}
    assert fired.id not in leftover_ids


def test_agent_turn_prompt_is_not_a_finished_status_bubble() -> None:
    event = SimpleNamespace(
        id="ev1",
        event_type="budget_low",
        severity="warning",
        reason="remaining 5",
        recommended_agent_action="review_budget",
        watch_spec_id="w1",
        ad_id=7,
        account_id="acc",
        project_id="p",
        previous={"budget": 20},
        current={"budget": 5},
    )
    spec = SimpleNamespace(
        id="w1",
        kind="campaign_budget",
        created_by="agent",
        invoke_agent=True,
        thresholds={"context": "after top-up"},
    )
    prompt = build_agent_turn_prompt(event, spec)  # type: ignore[arg-type]
    assert "You are the model" in prompt
    assert "system: status changed" not in prompt.lower()
    assert "budget_low" in prompt
    assert "after top-up" in prompt


def test_coverage_classifies_live_registry() -> None:
    rows = list_tool_coverage()
    assert len(rows) >= 40
    names = {r.capability for r in rows}
    assert "telegram_ads_create_ad" in names
    assert "telegram_ads_get_ad" in names
    create = next(r for r in rows if r.capability == "telegram_ads_create_ad")
    assert create.watcher_support == "post_action_verification"
    get_ad = next(r for r in rows if r.capability == "telegram_ads_get_ad")
    assert get_ad.watcher_support == "direct_watch"
    assert "moderation_result" in get_ad.watch_kinds
