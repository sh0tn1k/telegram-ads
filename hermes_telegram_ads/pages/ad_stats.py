"""Parser for /account/ad/<id>/stats and CSV-link extraction."""

from __future__ import annotations

from hermes_telegram_ads.browser import BrowserAutomationTool
from hermes_telegram_ads.parser import parse_int, parse_money
from hermes_telegram_ads.types import AdStats, AdStatsRow


async def get_ad_stats(browser: BrowserAutomationTool, ad_id: int) -> AdStats:
    """Parse stats page including monthly aggregation table."""
    js = """
    () => {
      const text = (sel) => document.querySelector(sel)?.innerText.trim() || '';
      // Headerbox info
      const lines = (document.body.innerText || '').split('\\n').map(l => l.trim());

      // Reports table rows: each row is [date, views, amount]
      const rows = [...document.querySelectorAll('table.pr-table tbody tr')].map(r => {
        const cells = [...r.querySelectorAll('td')];
        return cells.map(c => c.innerText.trim());
      }).filter(r => r.length >= 3 && !/^Total/i.test(r[0] || ''));

      // CSV link
      const csvLink = document.querySelector("a.csv_link[href*='/reports/']")?.getAttribute('href') || null;
      const shareBtn = document.querySelector("a.pr-link-btn[href$='/stats/share']")?.getAttribute('href') || null;

      // Try to extract Date created / CPM / Views from headerbox
      const title = document.title.replace(/ – Telegram Ads$/, '').trim();
      return { lines, rows, csv_link: csvLink, share_btn_href: shareBtn, title };
    }
    """
    raw = await browser.evaluate(js) or {}
    lines: list[str] = raw.get("lines") or []
    rows: list[list[str]] = raw.get("rows") or []

    # Extract structured fields from lines (Date created, CPM, Budget, Views)
    def _val_after(label: str) -> str:
        for i, ln in enumerate(lines):
            if ln == label and i + 1 < len(lines):
                return lines[i + 1].strip()
        return ""

    date_created = _val_after("Date created")
    cpm = parse_money(_val_after("CPM"))
    budget = parse_money(_val_after("Budget"))
    views_total = parse_int(_val_after("Views"))

    monthly = [
        AdStatsRow(
            day=row[0],
            views=parse_int(row[1]),
            amount=parse_money(row[2]),
        )
        for row in rows
        if len(row) >= 3
    ]

    return AdStats(
        ad_id=ad_id,
        title=raw.get("title") or "",
        date_created=date_created,
        cpm=cpm,
        budget=budget,
        views=views_total,
        monthly=monthly,
        csv_url=raw.get("csv_link"),
        share_stats_url=raw.get("share_btn_href"),
    )


__all__ = ["get_ad_stats"]
