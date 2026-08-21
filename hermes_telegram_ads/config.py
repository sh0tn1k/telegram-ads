"""Configuration loading.

Supports YAML files and direct construction via Pydantic model.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field

# Operator-configured Ads login phone. Presence is standing authorization
# for telegram_ads_login_from_env (never invent a number).
ADS_PHONE_ENV_CANDIDATES: tuple[str, ...] = (
    "TELEGRAM_ADS_PHONE",
    "TG_ADS_PHONE",
)


def first_env(*names: str) -> str | None:
    """First non-empty environment value among *names*, stripped."""
    for name in names:
        raw = os.environ.get(name)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return None


def normalize_phone(raw: str) -> str:
    """Collapse spaces/dashes and force a leading '+' (E.164-ish)."""
    s = raw.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if s.startswith("00"):
        s = "+" + s[2:]
    if not s.startswith("+"):
        s = "+" + s
    return s


def ads_phone_from_env() -> str | None:
    """
    [GOAL] Read the Ads cabinet phone from host env (never from the model).
    [INPUT] TELEGRAM_ADS_PHONE or TG_ADS_PHONE
    [OUTPUT] Normalized E.164 string, or None if unset
    """
    raw = first_env(*ADS_PHONE_ENV_CANDIDATES)
    if not raw:
        return None
    return normalize_phone(raw)


class BrowserConfig(BaseModel):
    profile_dir: Path = Field(
        default=Path("./browser_profiles/telegram_ads"),
        description="Persistent browser profile directory (keeps session cookies)",
    )
    headless: bool = False
    viewport_width: int = 1440
    viewport_height: int = 1000
    timeout_ms: int = 15_000
    navigation_timeout_ms: int = 30_000
    user_data_dir: Path | None = None  # alias for profile_dir; if set overrides
    chromium_args: list[str] = Field(
        default_factory=list,
        description=(
            "Extra Chromium flags for launch_persistent_context. "
            "On headless servers set --no-sandbox --disable-dev-shm-usage in YAML."
        ),
    )


class StorageConfig(BaseModel):
    base_path: Path = Field(default=Path("./data/telegram_ads"))
    screenshots_path: Path | None = None
    reports_path: Path | None = None
    drafts_path: Path | None = None

    def resolve(self) -> Self:
        """Auto-derive sub-paths if not explicitly set."""
        if self.screenshots_path is None:
            self.screenshots_path = self.base_path / "screenshots"
        if self.reports_path is None:
            self.reports_path = self.base_path / "reports"
        if self.drafts_path is None:
            self.drafts_path = self.base_path / "drafts"
        return self


class SafetyConfig(BaseModel):
    require_confirmation_for_create: bool = True
    require_confirmation_for_edit: bool = True
    require_confirmation_for_budget: bool = True
    require_double_confirmation_for_delete: bool = True
    require_double_confirmation_for_revoke_stats: bool = True
    forbid_transfer_stars_until_verified: bool = True
    forbid_external_payment_until_verified: bool = True
    confirmation_ttl_seconds: int = 300


class LimitsConfig(BaseModel):
    max_ad_text_chars: int = 160
    max_ads_to_parse: int = 100
    default_timeout_ms: int = 15_000
    navigation_timeout_ms: int = 30_000


class AuditConfig(BaseModel):
    enabled: bool = True
    path: Path = Field(default=Path("./logs/telegram_ads_audit.jsonl"))
    hash_account_tokens: bool = True
    screenshot_retention_days: int = 7


class TelegramAdsConfig(BaseModel):
    """Top-level config. Load from YAML via TelegramAdsConfig.from_yaml(path)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    enabled: bool = True
    base_url: str = "https://ads.telegram.org"

    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)

    @classmethod
    def from_yaml(cls, path: str | os.PathLike[str]) -> Self:
        """Load config from YAML file. Top-level key 'telegram_ads' supported."""
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        if "telegram_ads" in raw:
            raw = raw["telegram_ads"]
        cfg = cls.model_validate(raw)
        cfg.storage.resolve()
        return cfg

    @classmethod
    def default(cls) -> Self:
        cfg = cls()
        cfg.storage.resolve()
        return cfg

    def ensure_paths(self) -> None:
        """Create all storage directories."""
        self.storage.resolve()
        for p in (
            self.browser.profile_dir,
            self.storage.base_path,
            self.storage.screenshots_path,
            self.storage.reports_path,
            self.storage.drafts_path,
            self.audit.path.parent,
        ):
            if p is not None:
                Path(p).mkdir(parents=True, exist_ok=True)
