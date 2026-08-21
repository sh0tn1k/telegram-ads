"""Public git-install surface: Ads-only plugin + local MCP catalog."""

from __future__ import annotations

from pathlib import Path

from hermes_telegram_ads.mcp import public_tool_catalog
from hermes_telegram_ads.plugin_runtime import plugin_root


def test_plugin_manifest_exists() -> None:
    root = plugin_root()
    yaml_path = root / "plugin.yaml"
    text = yaml_path.read_text(encoding="utf-8")
    assert "name: telegram-ads" in text
    assert (root / "__init__.py").is_file()
    assert (root / ".claude-plugin" / "plugin.json").is_file()
    assert (root / ".claude-plugin" / "marketplace.json").is_file()
    assert (root / ".mcp.json").is_file()


def test_mcp_catalog_is_ads_and_watcher_only() -> None:
    catalog = public_tool_catalog()
    names = [t["name"] for t in catalog]
    assert "telegram_ads_create_ad" in names
    assert "telegram_ads_list_ads" in names
    assert "telegram_ads_watch_create" in names
    assert all(not n.startswith("telegram_research_") for n in names)
    create = next(t for t in catalog if t["name"] == "telegram_ads_create_ad")
    assert create["mutating"] is True
    watch = next(t for t in catalog if t["name"] == "telegram_ads_watch_create")
    assert watch["mutating"] is False


def test_plugin_root_is_this_checkout() -> None:
    assert plugin_root() == Path(__file__).resolve().parents[1]
