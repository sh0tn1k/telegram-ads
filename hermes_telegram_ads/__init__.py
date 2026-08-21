"""Hermes Telegram Ads — adapter for ads.telegram.org.

Public API:
    TelegramAdsAdapter — main facade
    TelegramAdsConfig — settings
    BrowserAutomationTool — universal Playwright wrapper
    workflows.run_workflow — typed high-level operations

Usage:
    from hermes_telegram_ads import TelegramAdsAdapter, TelegramAdsConfig

    config = TelegramAdsConfig.from_yaml("config.yaml")
    async with TelegramAdsAdapter.start(config) as ads:
        await ads.ensure_logged_in()
        accounts = await ads.list_accounts()
        await ads.choose_account(accounts[0].account_token)
        ads_list = await ads.list_ads()
"""

from hermes_telegram_ads import workflows  # noqa: F401 — typed workflow dispatch (installed, kept)
from hermes_telegram_ads.adapter import TelegramAdsAdapter
from hermes_telegram_ads.api import TelegramAdsApi
from hermes_telegram_ads.browser import BrowserAutomationTool
from hermes_telegram_ads.config import TelegramAdsConfig
from hermes_telegram_ads.errors import (
    BrowserBrokenError,
    BrowserError,
    BrowserProfileLockedError,
    CleanupFailedError,
    ConfirmationRequiredError,
    HermesTelegramAdsError,
    LoginRequiredError,
    PolicyViolationError,
    SnapshotTimeoutError,
    TelegramAdsApiError,
    TransientBrowserError,
)
from hermes_telegram_ads.hermes_tools import (  # added 2026-06-03: Hermes tool layer (feature/hermes-tool-wrappers)
    TELEGRAM_ADS_TOOLS,
    TOOLS_BY_NAME,
    SafetyClass,
    TelegramAdsToolset,
    ToolSpec,
    tool_names,
)
from hermes_telegram_ads.safety import Confirmation, RiskLevel, TelegramAdsSafety
from hermes_telegram_ads.types import (
    Account,
    Ad,
    AdStatus,
    CreateAdDraft,
    Event,
    Transaction,
)

__version__ = "0.2.0"

__all__ = [
    "TELEGRAM_ADS_TOOLS",  # added 2026-06-03
    "TOOLS_BY_NAME",  # added 2026-06-03
    "Account",
    "Ad",
    "AdStatus",
    "BrowserAutomationTool",
    "BrowserBrokenError",
    "BrowserError",
    "BrowserProfileLockedError",
    "CleanupFailedError",
    "Confirmation",
    "ConfirmationRequiredError",
    "CreateAdDraft",
    "Event",
    "HermesTelegramAdsError",
    "LoginRequiredError",
    "PolicyViolationError",
    "RiskLevel",
    "SafetyClass",  # added 2026-06-03
    "SnapshotTimeoutError",
    "TelegramAdsAdapter",
    "TelegramAdsApi",
    "TelegramAdsApiError",
    "TelegramAdsConfig",
    "TelegramAdsSafety",
    "TelegramAdsToolset",  # added 2026-06-03
    "ToolSpec",  # added 2026-06-03
    "Transaction",
    "TransientBrowserError",
    "__version__",
    "tool_names",  # added 2026-06-03
]
