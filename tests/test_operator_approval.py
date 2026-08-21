"""Operator Telegram buttons: hook shape + in-process auto-apply after Accept."""

from __future__ import annotations

import pytest

from hermes_telegram_ads.config import TelegramAdsConfig
from hermes_telegram_ads.constants import (
    METHOD_DELETE_AD,
    METHOD_EDIT_AD_CPM,
    METHOD_EDIT_AD_STATUS,
)
from hermes_telegram_ads.hermes_tools import TelegramAdsToolset
from hermes_telegram_ads.operator_approval import (
    OPERATOR_APPROVED_ARG,
    OPERATOR_GATED_TOOLS,
    hermes_operator_gate_available,
    is_operator_gated,
    operator_gate_enabled,
    pre_tool_call_directive,
    set_operator_gate_enabled,
    summarize_operator_approval,
)
from hermes_telegram_ads.plugin_runtime import make_sync_handler
from hermes_telegram_ads.safety import TelegramAdsSafety


def test_summarize_change_cpm_has_ids_not_tokens() -> None:
    text = summarize_operator_approval(
        "telegram_ads_change_cpm", {"ad_id": 42, "new_cpm": 1.5, "confirmation_id": "secret-cid"}
    )
    assert "42" in text
    assert "1.5" in text
    assert "secret-cid" not in text
    assert "CPM" in text


def test_summarize_create_uses_draft_title() -> None:
    text = summarize_operator_approval(
        "telegram_ads_create_ad",
        {"draft": {"title": "Example EN", "cpm": 80, "budget": 500, "target_type": "search"}},
    )
    assert "Example EN" in text
    assert "search" in text


def test_pre_tool_call_escalates_gated_tools_only() -> None:
    out = pre_tool_call_directive("telegram_ads_change_cpm", {"ad_id": 7, "new_cpm": 2})
    assert out is not None
    assert out["action"] == "approve"
    assert out["rule_key"] == "telegram_ads:telegram_ads_change_cpm"
    assert "7" in out["message"]

    assert pre_tool_call_directive("telegram_ads_list_ads", {}) is None
    assert pre_tool_call_directive("telegram_ads_apply_approved_action", {"confirmation_id": "x"}) is None
    assert pre_tool_call_directive(
        "telegram_ads_change_cpm", {"ad_id": 7, "new_cpm": 2, "confirmation_id": "already"}
    ) is None


def test_login_and_apply_are_not_operator_gated() -> None:
    assert not is_operator_gated("telegram_ads_login_from_env")
    assert not is_operator_gated("telegram_ads_apply_approved_action")
    assert not is_operator_gated("telegram_ads_prepare_approval_request")
    assert "telegram_ads_create_ad" in OPERATOR_GATED_TOOLS
    assert "telegram_ads_delete_ad" in OPERATOR_GATED_TOOLS


def test_hermes_gate_probe_is_boolean() -> None:
    assert isinstance(hermes_operator_gate_available(), bool)


def test_sync_handler_does_not_auto_flag_when_gate_off() -> None:
    set_operator_gate_enabled(False)
    handler = make_sync_handler("not_a_real_tool")
    raw = handler({})
    assert "UNKNOWN_TOOL" in raw or "unregistered" in raw


class _FakeMutatingAdapter:
    def __init__(self) -> None:
        self.config = TelegramAdsConfig.default()
        self.safety = TelegramAdsSafety(self.config.safety)
        self.executed: list[tuple] = []

    def issue_change_cpm_confirmation(self, ad_id: int, new_cpm: float):
        return self.safety.issue_confirmation(METHOD_EDIT_AD_CPM, {"ad_id": ad_id, "cpm": new_cpm})

    async def change_cpm(self, ad_id: int, new_cpm: float, confirmation_id: str):
        self.safety.gate(
            METHOD_EDIT_AD_CPM, {"ad_id": ad_id, "cpm": new_cpm}, confirmation_id=confirmation_id
        )
        self.executed.append(("change_cpm", ad_id, new_cpm))
        return {"ok": True, "ad_id": ad_id, "cpm": new_cpm}

    def issue_change_status_confirmation(self, ad_id: int, *, active: bool):
        return self.safety.issue_confirmation(
            METHOD_EDIT_AD_STATUS, {"ad_id": ad_id, "active": active}
        )

    async def change_status(self, ad_id: int, *, active: bool, confirmation_id: str):
        self.safety.gate(
            METHOD_EDIT_AD_STATUS,
            {"ad_id": ad_id, "active": active},
            confirmation_id=confirmation_id,
        )
        self.executed.append(("change_status", ad_id, active))
        return {"ok": True, "ad_id": ad_id, "active": active}

    def issue_delete_ad_confirmations(self, ad_id: int):
        return (
            self.safety.issue_confirmation(METHOD_DELETE_AD, {"ad_id": ad_id}, note="first"),
            self.safety.issue_confirmation(METHOD_DELETE_AD, {"ad_id": ad_id}, note="second"),
        )

    async def delete_ad(self, ad_id: int, *, confirmation_id: str, second_confirmation_id: str):
        self.safety.gate(
            METHOD_DELETE_AD,
            {"ad_id": ad_id},
            confirmation_id=confirmation_id,
            second_confirmation_id=second_confirmation_id,
        )
        self.executed.append(("delete_ad", ad_id))
        return {"ok": True, "ad_id": ad_id}


@pytest.mark.asyncio
async def test_change_cpm_without_operator_returns_approval_required() -> None:
    adapter = _FakeMutatingAdapter()
    ts = TelegramAdsToolset(adapter=adapter)
    env = await ts.call("telegram_ads_change_cpm", ad_id=9, new_cpm=3.2)
    assert env["status"] == "approval_required"
    assert adapter.executed == []
    assert env["approval"]["confirmation_id"]


@pytest.mark.asyncio
async def test_change_cpm_after_operator_accept_executes() -> None:
    adapter = _FakeMutatingAdapter()
    ts = TelegramAdsToolset(adapter=adapter)
    env = await ts.call(
        "telegram_ads_change_cpm",
        ad_id=9,
        new_cpm=3.2,
        **{OPERATOR_APPROVED_ARG: True},
    )
    assert env["ok"] is True
    assert env["status"] == "ok"
    assert adapter.executed == [("change_cpm", 9, 3.2)]
    assert env["data"]["executed"] is True


@pytest.mark.asyncio
async def test_start_ad_after_operator_accept_executes() -> None:
    adapter = _FakeMutatingAdapter()
    ts = TelegramAdsToolset(adapter=adapter)
    env = await ts.call("telegram_ads_start_ad", ad_id=11, **{OPERATOR_APPROVED_ARG: True})
    assert env["ok"] is True
    assert adapter.executed == [("change_status", 11, True)]


@pytest.mark.asyncio
async def test_delete_ad_after_operator_accept_consumes_both_tokens() -> None:
    adapter = _FakeMutatingAdapter()
    ts = TelegramAdsToolset(adapter=adapter)
    env = await ts.call("telegram_ads_delete_ad", ad_id=4, **{OPERATOR_APPROVED_ARG: True})
    assert env["ok"] is True
    assert adapter.executed == [("delete_ad", 4)]


def test_operator_gate_flag_defaults_off() -> None:
    set_operator_gate_enabled(False)
    assert operator_gate_enabled() is False
    set_operator_gate_enabled(True)
    assert operator_gate_enabled() is True
    set_operator_gate_enabled(False)
