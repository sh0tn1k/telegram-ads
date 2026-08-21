"""One-shot real Telegram Ads adapter smoke check (read-only).

ALLOWED (configure per approval):  detect_login_state, browser_healthy, list_accounts
FORBIDDEN:                        create/edit/start/stop/delete, CPM/budget mutations,
                                   set_targeting/set_schedule/set_pixel/set_conversion_event,
                                   login_start/login_submit_phone/OTP, payments, any write.

This script:
  1. acquires a real TelegramAdsAdapter via BrowserProfileManager
  2. wraps it in HermesTelegramAdsReadOnlyAdapter (mutation guard active)
  3. calls exactly the 3 read-only methods named above
  4. releases the adapter
  5. prints a compact report (no secrets, no tokens, no cookies)

Does NOT:
  - add watches
  - start the scheduler with a real adapter
  - read campaign details
  - touch login / OTP / phone
  - perform any mutating Telegram Ads action
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import traceback
from typing import Any

from hermes_telegram_ads.browser_manager import BrowserProfileManager
from hermes_telegram_ads.hermes_tools import TelegramAdsConfig

from ads_watcher_integration import HermesTelegramAdsReadOnlyAdapter

log = logging.getLogger("ads_watcher.real_smoke")

SECRET_KEYS = frozenset({
    "token", "access_token", "refresh_token", "session", "cookie",
    "cookies", "phone", "password", "secret", "api_key", "auth",
})


def _safe(obj: Any) -> Any:
    """Recursively redact secret-looking keys from a dict / model / list."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {k: ("<redacted>" if any(s in k.lower() for s in SECRET_KEYS)
                    else _safe(v)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe(x) for x in obj]
    if hasattr(obj, "model_dump"):
        try:
            return _safe(obj.model_dump())
        except Exception:  # noqa: BLE001
            return str(obj)[:200]
    return obj


async def _run_smoke() -> dict[str, Any]:
    report: dict[str, Any] = {
        "adapter_acquired": False,
        "scheduler_started": False,
        "calls": [],
        "results": {},
        "errors": [],
    }

    manager = BrowserProfileManager.get_instance()
    config = TelegramAdsConfig.default()

    adapter = None
    try:
        log.info("acquiring TelegramAdsAdapter via BrowserProfileManager")
        adapter = await manager.acquire_adapter(config=config, timeout=30.0)
        report["adapter_acquired"] = True

        ro = HermesTelegramAdsReadOnlyAdapter(adapter=adapter)

        # 1. browser_healthy
        try:
            healthy = ro.browser_healthy()
            report["calls"].append({"method": "browser_healthy", "ok": True})
            report["results"]["browser_healthy"] = healthy
            log.info("browser_healthy=%s", healthy)
        except Exception as e:  # noqa: BLE001
            report["calls"].append({"method": "browser_healthy", "ok": False, "error": repr(e)})
            report["errors"].append({"where": "browser_healthy", "error": repr(e)})

        # 2. detect_login_state
        try:
            state = await ro.detect_login_state(navigate=True)
            report["calls"].append({"method": "detect_login_state", "ok": True})
            report["results"]["detect_login_state"] = _safe(state)
            log.info("detect_login_state=%s", _safe(state))
        except Exception as e:  # noqa: BLE001
            report["calls"].append({"method": "detect_login_state", "ok": False, "error": repr(e)})
            report["errors"].append({"where": "detect_login_state", "error": repr(e)})

        # 3. list_accounts
        try:
            accounts = await ro.list_accounts()
            summary = []
            for acc in accounts:
                d = _safe(acc)
                if isinstance(d, dict):
                    summary.append({
                        k: d.get(k) for k in (
                            "id", "account_id", "name", "currency", "balance",
                            "status", "spent", "is_active",
                        ) if k in d
                    })
                else:
                    summary.append(str(d)[:120])
            report["calls"].append({"method": "list_accounts", "ok": True})
            report["results"]["list_accounts"] = {
                "count": len(accounts),
                "summary": summary,
            }
            log.info("list_accounts count=%d", len(accounts))
        except Exception as e:  # noqa: BLE001
            report["calls"].append({"method": "list_accounts", "ok": False, "error": repr(e)})
            report["errors"].append({"where": "list_accounts", "error": repr(e)})

    except Exception as e:  # noqa: BLE001
        report["errors"].append({
            "where": "acquire_adapter",
            "error": repr(e),
            "tb": traceback.format_exc(),
        })
    finally:
        if adapter is not None:
            try:
                manager.release_adapter()
                log.info("adapter released")
            except Exception as e:  # noqa: BLE001
                report["errors"].append({"where": "release_adapter", "error": repr(e)})

    return report


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    report = asyncio.run(_run_smoke())

    print("\n========= READ-ONLY REAL ADAPTER SMOKE REPORT =========")
    print(json.dumps(report, indent=2, default=str))
    print("=======================================================\n")
    print("scheduler_started:    ", report["scheduler_started"])
    print("adapter_acquired:     ", report["adapter_acquired"])
    print("real_telegram_ads_calls:", len(report["calls"]))
    print("mutating_calls:       ", 0)
    print("errors:               ", len(report["errors"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
