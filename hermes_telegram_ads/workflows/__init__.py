"""Telegram Ads workflow dispatch — typed high-level operations.

Usage:
    from hermes_telegram_ads.workflows import run_workflow

    result = await run_workflow("snapshot", {"account_token": "..."}, adapter)
    # → {"ok": True, "workflow": "snapshot", "data": {...}}
"""

from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hermes_telegram_ads.adapter import TelegramAdsAdapter

logger = logging.getLogger(__name__)

# ─── Registry ──────────────────────────────────────────────────────────────────

WORKFLOW_REGISTRY: dict[str, tuple[str, str]] = {
    # Phase 1 — read-only
    "snapshot": ("_snapshot", "run_snapshot"),
    "inspect_ad": ("_inspect_ad", "run_inspect_ad"),
    "account_diagnosis": ("_account_diagnosis", "run_account_diagnosis"),
}


# ─── Dispatch ──────────────────────────────────────────────────────────────────

async def run_workflow(
    workflow: str,
    params: dict[str, Any],
    adapter: TelegramAdsAdapter | None = None,
) -> dict[str, Any]:
    """Execute a named workflow and return structured result.

    Returns ``{"ok": True, "workflow": ..., "data": {...}}`` on success,
    or ``{"ok": False, "workflow": ..., "error": ..., "message": ...}`` on failure.

    If *adapter* is None, the caller must handle adapter lifecycle externally.
    """
    entry = WORKFLOW_REGISTRY.get(workflow)
    if entry is None:
        return {
            "ok": False,
            "workflow": workflow,
            "error": "UNKNOWN_WORKFLOW",
            "message": f"Unknown workflow: {workflow!r}. "
                       f"Available: {', '.join(sorted(WORKFLOW_REGISTRY))}",
        }

    if adapter is None:
        return {
            "ok": False,
            "workflow": workflow,
            "error": "NO_ADAPTER",
            "message": "TelegramAdsAdapter is required but was not provided.",
        }

    module_name, func_name = entry
    try:
        module = importlib.import_module(f".{module_name}", __package__)
    except ImportError as e:
        logger.exception("Failed to import workflow module %s", module_name)
        return {
            "ok": False,
            "workflow": workflow,
            "error": "IMPORT_ERROR",
            "message": f"Failed to load workflow module: {e}",
        }

    func = getattr(module, func_name, None)
    if func is None:
        return {
            "ok": False,
            "workflow": workflow,
            "error": "MISSING_HANDLER",
            "message": f"Workflow {workflow!r} has no handler function.",
        }

    # ── Graceful teardown guarantee ────────────────────────────────
    # If the caller provided an adapter, ensure release_adapter() is
    # called even if the workflow raises. This is the dispatcher-level
    # equivalent of an async context manager for BrowserProfileManager.
    # See BrowserProfileManager.use_adapter() for the context manager
    # API; this is the lower-level try/finally path for callers that
    # build the adapter externally.
    _mgr = _get_browser_profile_manager()
    if _mgr is not None and _mgr.is_active and adapter is not None:
        try:
            result = await func(adapter, params)
            return {"ok": True, "workflow": workflow, "data": result}
        except Exception as e:
            logger.exception("Workflow %s failed", workflow)
            return _error_result(workflow, e)
        finally:
            try:
                _mgr.release_adapter()
                logger.debug(
                    "run_workflow: released adapter for %s", workflow
                )
            except Exception as _rel_exc:
                logger.warning(
                    "run_workflow: release_adapter error for %s: %s",
                    workflow, _rel_exc,
                )
    else:
        # No manager or no active adapter — call as-is
        try:
            result = await func(adapter, params)
            return {"ok": True, "workflow": workflow, "data": result}
        except Exception as e:
            logger.exception("Workflow %s failed", workflow)
            return _error_result(workflow, e)


def _get_browser_profile_manager() -> Any:
    """Lazy getter for BrowserProfileManager singleton.

    Returns None if hermes_telegram_ads is not installed (ImportError).
    This allows the dispatcher to work in profiles that don't have
    the Telegram Ads plugin.
    """
    try:
        from hermes_telegram_ads.browser_manager import (
            TelegramAdsBrowserProfileManager,
        )
        return TelegramAdsBrowserProfileManager.get_instance()
    except ImportError:
        return None


# ─── Error classification ─────────────────────────────────────────────────────

def _error_result(workflow: str, exc: Exception) -> dict[str, Any]:
    """Classify exception and return structured error dict."""
    exc_name = type(exc).__name__
    message = str(exc) or exc_name

    # Known error types
    try:
        from hermes_telegram_ads.errors import LoginRequiredError
        if isinstance(exc, LoginRequiredError):
            return {
                "ok": False,
                "workflow": workflow,
                "error": "LOGIN_REQUIRED",
                "message": message,
            }
    except ImportError:
        pass

    try:
        from hermes_telegram_ads.errors import ForbiddenActionError
        if isinstance(exc, ForbiddenActionError):
            return {
                "ok": False,
                "workflow": workflow,
                "error": "FORBIDDEN",
                "message": message,
            }
    except ImportError:
        pass

    try:
        from hermes_telegram_ads.errors import TelegramAdsApiError
        if isinstance(exc, TelegramAdsApiError):
            return {
                "ok": False,
                "workflow": workflow,
                "error": "API_ERROR",
                "message": message,
            }
    except ImportError:
        pass

    # Generic
    return {
        "ok": False,
        "workflow": workflow,
        "error": "WORKFLOW_ERROR",
        "message": f"{exc_name}: {message}",
    }


__all__ = [
    "WORKFLOW_REGISTRY",
    "run_workflow",
]
