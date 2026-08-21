"""CSV report downloading.

Uses Playwright BrowserContext to fetch CSV with current session cookies.
Saves to config.storage.reports_path.
"""

from __future__ import annotations

from pathlib import Path

from hermes_telegram_ads.browser import BrowserAutomationTool
from hermes_telegram_ads.constants import (
    BASE_URL,
    URL_CSV_ACCOUNT_BUDGET,
    URL_CSV_ACCOUNT_COUNT,
    URL_REPORT_ACCOUNT_MONTHLY,
    URL_REPORT_AD_MONTHLY,
)


async def _download_csv(browser: BrowserAutomationTool, url_path: str, dest: Path) -> Path:
    """Fetch CSV via page.evaluate (uses session cookies). Save to dest."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    full_url = BASE_URL.rstrip("/") + url_path
    js = """
    async (url) => {
      const r = await fetch(url, { credentials: 'same-origin' });
      if (!r.ok) return { __http_status: r.status };
      return { text: await r.text() };
    }
    """
    result = await browser.evaluate(js, full_url)
    if not isinstance(result, dict) or "text" not in result:
        raise RuntimeError(f"Failed to download CSV: {url_path}; response={result}")
    dest.write_text(result["text"], encoding="utf-8")
    return dest


async def download_ad_monthly_report(
    browser: BrowserAutomationTool,
    *,
    account_token: str,
    ad_id: int,
    month: str,  # YYYYMM
    dest_dir: Path,
) -> Path:
    url_path = URL_REPORT_AD_MONTHLY.format(token=account_token, ad_id=ad_id, month=month)
    dest = dest_dir / f"ad_{ad_id}_{month}.csv"
    return await _download_csv(browser, url_path, dest)


async def download_account_monthly_report(
    browser: BrowserAutomationTool,
    *,
    account_token: str,
    month: str,
    dest_dir: Path,
) -> Path:
    url_path = URL_REPORT_ACCOUNT_MONTHLY.format(token=account_token, month=month)
    dest = dest_dir / f"account_{month}.csv"
    return await _download_csv(browser, url_path, dest)


async def download_account_count_csv(
    browser: BrowserAutomationTool,
    *,
    account_token: str,
    dest_dir: Path,
) -> Path:
    url_path = URL_CSV_ACCOUNT_COUNT.format(token=account_token)
    dest = dest_dir / "account_count.csv"
    return await _download_csv(browser, url_path, dest)


async def download_account_budget_csv(
    browser: BrowserAutomationTool,
    *,
    account_token: str,
    dest_dir: Path,
) -> Path:
    url_path = URL_CSV_ACCOUNT_BUDGET.format(token=account_token)
    dest = dest_dir / "account_budget.csv"
    return await _download_csv(browser, url_path, dest)


__all__ = [
    "download_account_budget_csv",
    "download_account_count_csv",
    "download_account_monthly_report",
    "download_ad_monthly_report",
]
