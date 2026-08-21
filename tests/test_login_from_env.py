"""Env-phone Ads login: no secrets leaked, env is the only phone source."""

from __future__ import annotations

from hermes_telegram_ads.config import ads_phone_from_env, normalize_phone
from hermes_telegram_ads.hermes_tools import TelegramAdsToolset, tool_names
from hermes_telegram_ads.login_flow import mask_phone


def test_normalize_phone_adds_plus() -> None:
    assert normalize_phone("10000000000") == "+10000000000"
    assert normalize_phone("+1 000 000-00-00") == "+10000000000"


def test_ads_phone_from_env_reads_telegram_ads_phone(monkeypatch) -> None:
    monkeypatch.delenv("TG_ADS_PHONE", raising=False)
    monkeypatch.setenv("TELEGRAM_ADS_PHONE", "+10000000000")
    assert ads_phone_from_env() == "+10000000000"


def test_mask_phone_hides_middle() -> None:
    masked = mask_phone("+10000000000")
    assert masked == "+1********00"
    assert "+10000000000" not in masked


def test_login_from_env_is_registered() -> None:
    assert "telegram_ads_login_from_env" in tool_names()


async def test_login_from_env_missing_env(monkeypatch) -> None:
    monkeypatch.delenv("TELEGRAM_ADS_PHONE", raising=False)
    monkeypatch.delenv("TG_ADS_PHONE", raising=False)
    ts = TelegramAdsToolset(config=None)
    env = await ts.call("telegram_ads_login_from_env")
    assert env["ok"] is False
    blob = str(env)
    assert "TELEGRAM_ADS_PHONE" in blob or "invalid_input" in blob.lower() or env.get("error")


class _FakeAdapter:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.config = None

    async def login_authorize_from_env(self, phone: str) -> dict:
        self.calls.append(phone)
        return {
            "state": "app_approval_pending",
            "logged_in": False,
            "session_active": False,
            "current_url": "https://ads.telegram.org/auth",
            "profile_dir": "/tmp/profile",
            "browser_state": "healthy",
            "requires_human_login": True,
            "recovery_hint": "approve_in_telegram_app_then_login_wait",
            "instructions": ["Approve in Telegram"],
            "phone_masked": mask_phone(phone),
            "phone_submitted": True,
            "already_logged_in": False,
            "operator_message": f"Entered {mask_phone(phone)}. Tap Accept.",
        }


async def test_login_from_env_submits_env_phone_and_masks(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_ADS_PHONE", "+10000000000")
    adapter = _FakeAdapter()
    ts = TelegramAdsToolset(adapter=adapter)
    env = await ts.call("telegram_ads_login_from_env")
    assert env["ok"] is True
    assert adapter.calls == ["+10000000000"]
    data = env.get("data") or {}
    assert data.get("phone_submitted") is True
    blob = str(env)
    assert "+10000000000" not in blob
