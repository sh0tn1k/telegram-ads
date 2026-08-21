"""Hermes user plugin: typed Telegram Ads tools + in-process read-only watcher.

Install: ``hermes plugins install <git-url> --enable``. Lives under
``~/.hermes/plugins/telegram-ads`` so ``hermes update`` cannot erase it.

Telegram research, GrokBot identity, and /sessions are not part of this plugin.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _bootstrap_imports() -> None:
    import sys

    root = str(Path(__file__).resolve().parent)
    if root not in sys.path:
        sys.path.insert(0, root)


def register(ctx) -> None:
    """Register typed Ads + watcher tools. No research, no identity prompt."""
    _bootstrap_imports()
    from hermes_telegram_ads.plugin_runtime import (
        default_session_key,
        register_ads_plugin,
        set_injector,
        start_watcher_background,
    )

    counts = register_ads_plugin(ctx)
    logger.info(
        "telegram-ads registered ads=%s watcher=%s skills=%s",
        counts["ads"],
        counts["watcher"],
        counts.get("skills", 0),
    )

    def _inject(content: str, role: str = "user", session_key: str | None = None) -> bool:
        return bool(ctx.inject_message(content, role=role, session_key=session_key))

    set_injector(_inject, session_key=default_session_key())
    start_watcher_background()
