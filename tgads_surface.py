"""Standalone typed Telegram Ads surface (not wired into Orthanc ToolRuntime).

[GOAL] Expose the live typed telegram_ads_* inventory and a read-only watcher.
[INPUT] Vendored hermes_telegram_ads (this directory on sys.path).
[OUTPUT] tool list, coverage, watcher tick helpers, mutation guard.

Intent:
- Keep the working Hermes typed package and watcher in the repo for later
  Orthanc integration. Cabinet I/O stays behind an injected adapter.

Constraints:
- Does not register Orthanc ToolDescriptors or touch ToolRuntime.
- Does not call ads.telegram.org or load cabinet secrets.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hermes_telegram_ads.hermes_tools import (
    MUTATING_TOOLS,
    TELEGRAM_ADS_TOOLS,
    TelegramAdsToolset,
)
from hermes_telegram_ads.watcher import (
    SQLiteWatcherStore,
    TelegramAdsWatcherService,
    WatcherEvent,
    WatcherScheduler,
    list_tool_coverage,
)

PACKAGE_ROOT = Path(__file__).resolve().parent
SKILLS_DIR = PACKAGE_ROOT / "skills"
HERMES_PACKAGE_DIR = PACKAGE_ROOT / "hermes_telegram_ads"
TYPED_WRAPPER = PACKAGE_ROOT / "wrappers" / "telegram_ads_typed_tool.py"
WATCHER_WRAPPER = PACKAGE_ROOT / "wrappers" / "ads_watcher_integration.py"

FORBIDDEN_MUTATION_TOOLS: frozenset[str] = frozenset(
    {
        "create_ad",
        "edit_ad",
        "change_cpm",
        "add_to_budget",
        "withdraw_from_budget",
        "start_ad",
        "stop_ad",
        "delete_ad",
        "set_budget",
        "archive_ad",
        "set_schedule",
        "set_targeting",
        "set_conversion_event",
        "set_pixel",
        "apply_approved_action",
        "login_start",
        "login_submit_phone",
    }
)


class MutationForbiddenError(RuntimeError):
    """Raised when a watcher-path caller invokes a mutating cabinet action."""

    def __init__(self, tool_name: str) -> None:
        super().__init__(f"mutation tool {tool_name!r} is forbidden on the watcher path")
        self.tool_name = tool_name


def _assert_readonly(tool_name: str) -> None:
    if tool_name in FORBIDDEN_MUTATION_TOOLS:
        raise MutationForbiddenError(tool_name)


class ReadOnlyCabinetAdapter:
    """Read-only wrapper: forwards watcher reads, hard-fails mutations.

    [GOAL] Watcher path never reaches create/edit/cpm/budget/login-submit.
    [INPUT] Injected cabinet adapter (fake in tests).
    [OUTPUT] Read results; MutationForbiddenError on mutating names.
    """

    def __init__(self, adapter: Any | None = None) -> None:
        self._adapter = adapter

    def _require(self) -> Any:
        if self._adapter is None:
            raise RuntimeError("ReadOnlyCabinetAdapter has no underlying adapter")
        return self._adapter

    async def get_ad(self, ad_id: int) -> Any:
        return await self._require().get_ad(ad_id)

    async def get_ad_stats(self, ad_id: int) -> Any:
        return await self._require().get_ad_stats(ad_id)

    async def get_account_budget(self) -> Any:
        return await self._require().get_account_budget()

    async def get_account_stats(self) -> dict[str, Any]:
        inner = self._require()
        if hasattr(inner, "get_account_stats"):
            raw = await inner.get_account_stats()
            return raw if isinstance(raw, dict) else {"value": raw}
        budget = await self.get_account_budget()
        data = budget if isinstance(budget, dict) else getattr(budget, "__dict__", {})
        return {"url": None, "balance": data.get("balance"), "currency": data.get("currency")}

    async def list_ads(self) -> list[Any]:
        return list(await self._require().list_ads())

    async def list_accounts(self) -> list[Any]:
        return list(await self._require().list_accounts())

    async def get_share_stats_url(self, ad_id: int) -> str | None:
        return await self._require().get_share_stats_url(ad_id)

    async def validate_ad(self, draft: Any) -> Any:
        return await self._require().validate_ad(draft)

    async def detect_login_state(self, *, navigate: bool = True) -> Any:
        inner = self._require()
        try:
            return await inner.detect_login_state(navigate=navigate)
        except TypeError:
            return await inner.detect_login_state()

    def browser_healthy(self) -> bool:
        inner = self._require()
        if hasattr(inner, "browser_healthy"):
            return bool(inner.browser_healthy())
        return True

    def __getattr__(self, name: str) -> Any:
        _assert_readonly(name)
        raise AttributeError(f"ReadOnlyCabinetAdapter has no attribute {name!r}")


@dataclass
class WatcherSurface:
    store: SQLiteWatcherStore
    service: TelegramAdsWatcherService
    scheduler: WatcherScheduler
    adapter: ReadOnlyCabinetAdapter


def typed_tool_ids() -> list[str]:
    """Sorted live telegram_ads_* names from TELEGRAM_ADS_TOOLS."""
    return sorted(spec.name for spec in TELEGRAM_ADS_TOOLS)


def typed_tool_schemas() -> dict[str, dict[str, Any]]:
    """tool name → JSON input schema from the live typed registry."""
    return {spec.name: spec.input_schema for spec in TELEGRAM_ADS_TOOLS}


def toolset_with_adapter(adapter: Any) -> TelegramAdsToolset:
    """TelegramAdsToolset bound to an injected adapter (no browser launch)."""
    return TelegramAdsToolset(adapter=adapter)


def watcher_coverage() -> list[dict[str, Any]]:
    """Coverage matrix; mutating tools reported as forbidden_in_watcher."""
    rows: list[dict[str, Any]] = []
    for item in list_tool_coverage():
        payload = item.model_dump()
        name = payload["capability"]
        if name in MUTATING_TOOLS or payload.get("watcher_support") == "post_action_verification":
            payload["watcher_support"] = "forbidden_in_watcher"
            payload.setdefault(
                "notes",
                "Mutating/sensitive — the watcher never executes this action.",
            )
        rows.append(payload)
    return rows


def forbidden_mutation_names() -> frozenset[str]:
    return frozenset(
        row["capability"]
        for row in watcher_coverage()
        if row["watcher_support"] == "forbidden_in_watcher"
    ) | MUTATING_TOOLS


def build_watcher_surface(
    adapter: Any | None = None,
    *,
    project_id: str = "orthanc",
    store_path: str | Path,
) -> WatcherSurface:
    ro = adapter if isinstance(adapter, ReadOnlyCabinetAdapter) else ReadOnlyCabinetAdapter(adapter)
    store = SQLiteWatcherStore(db_path=Path(store_path))
    service = TelegramAdsWatcherService(adapter=ro, store=store, project_id=project_id)
    scheduler = WatcherScheduler(service=service, poll_interval_sec=30)
    return WatcherSurface(store=store, service=service, scheduler=scheduler, adapter=ro)


async def tick_watcher(surface: WatcherSurface) -> list[WatcherEvent]:
    return await surface.scheduler.tick()


def skill_pack_dirs() -> list[Path]:
    return sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir() and (p / "SKILL.md").is_file())


def inventory() -> dict[str, Any]:
    """Repeatable launch payload: typed tools + watcher coverage + skill ids."""
    tools = typed_tool_ids()
    coverage = watcher_coverage()
    skills = [p.name for p in skill_pack_dirs()]
    return {
        "tool_ids": tools,
        "tool_count": len(tools),
        "coverage_count": len(coverage),
        "coverage_forbidden_count": sum(
            1 for row in coverage if row["watcher_support"] == "forbidden_in_watcher"
        ),
        "forbidden_mutations": sorted(forbidden_mutation_names()),
        "mutating_typed_tools": sorted(MUTATING_TOOLS),
        "skill_ids": skills,
        "package_root": str(PACKAGE_ROOT),
        "hermes_package": str(HERMES_PACKAGE_DIR),
        "typed_wrapper": str(TYPED_WRAPPER),
        "watcher_wrapper": str(WATCHER_WRAPPER),
    }
