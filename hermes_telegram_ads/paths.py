"""Portable Ads data paths. Hermes home if present, else ~/.telegram-ads."""

from __future__ import annotations

import os
from pathlib import Path


def default_ads_home() -> Path:
    """Directory for browser profile, watcher DB, screenshots, audit log.

    Resolution order:
    1. TELEGRAM_ADS_HOME
    2. $HERMES_HOME/data/telegram_ads when HERMES_HOME is set
    3. ~/.hermes/data/telegram_ads when ~/.hermes already exists
    4. ~/.telegram-ads for non-Hermes agents (Claude Code, Cursor, …)
    """
    override = os.environ.get("TELEGRAM_ADS_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    hermes_home = os.environ.get("HERMES_HOME", "").strip()
    if hermes_home:
        return Path(hermes_home).expanduser() / "data" / "telegram_ads"
    hermes_dir = Path.home() / ".hermes"
    if hermes_dir.is_dir():
        return hermes_dir / "data" / "telegram_ads"
    return Path.home() / ".telegram-ads"


def plugin_root() -> Path:
    """Repo / plugin directory (parent of the hermes_telegram_ads package)."""
    return Path(__file__).resolve().parent.parent
