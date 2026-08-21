"""Parser for Events Manager pages (Stars cabinet only)."""

from __future__ import annotations

import re

from hermes_telegram_ads.browser import BrowserAutomationTool
from hermes_telegram_ads.types import Event, EventLog, EventLogEntry, PixelSnippet

_TGP_INIT_RE = re.compile(r"tgp\('init','([^']+)'\)")
_TGP_EVENT_RE = re.compile(r"tgp\('event','([^']+)'\)")


async def list_events(browser: BrowserAutomationTool) -> list[Event]:
    """Parse /account/events table."""
    js = """
    () => {
      const rows = [...document.querySelectorAll('table.pr-table tbody tr')];
      return rows.map(r => {
        const cells = [...r.querySelectorAll('td')];
        const dropdown = r.querySelector('.pr-dropdown[data-event-id]');
        return {
          name: cells[0]?.innerText.trim() || '',
          type: cells[1]?.innerText.trim() || '',
          status: cells[2]?.innerText.trim() || '',
          used: cells[3]?.innerText.trim() || '–',
          event_id: dropdown?.getAttribute('data-event-id') || '',
        };
      }).filter(r => r.event_id);
    }
    """
    raw = await browser.evaluate(js) or []
    return [
        Event(
            event_id=row["event_id"],
            tag_id="",  # filled when fetching per-event snippet
            title=row["name"],
            type=row["type"],
            status="Active" if row["status"].lower() == "active" else "Inactive",
            used=row.get("used") or "–",
            ads=0,
            can_edit=True,
        )
        for row in raw
    ]


async def get_pixel_base_snippet(browser: BrowserAutomationTool) -> PixelSnippet | None:
    """Read base pixel code from /account/events/setup popup.

    Caller must navigate there first.
    """
    js = """
    () => {
      const ta = document.querySelector('.popup-container.aj_popup textarea.code-sample');
      return ta ? ta.value : null;
    }
    """
    code: str | None = await browser.evaluate(js)
    if not code:
        return None
    m = _TGP_INIT_RE.search(code)
    pixel_id = m.group(1) if m else ""
    return PixelSnippet(pixel_id=pixel_id, base_code=code, event_code=None)


async def get_event_setup_snippet(browser: BrowserAutomationTool) -> PixelSnippet | None:
    """Read pixel snippets from /account/event/<id>/setup popup.

    For custom events returns BOTH base + per-event code.
    For auto-events (Page views, Landing views) only base code.
    """
    js = """
    () => {
      const popup = document.querySelector('.popup-container.aj_popup section');
      if (!popup) return null;
      const textareas = [...popup.querySelectorAll('textarea.code-sample')];
      return {
        base_code: textareas[0]?.value || '',
        event_code: textareas[1]?.value || null,
      };
    }
    """
    raw = await browser.evaluate(js)
    if not raw:
        return None
    base_code = raw.get("base_code") or ""
    if not base_code:
        return None
    m = _TGP_INIT_RE.search(base_code)
    pixel_id = m.group(1) if m else ""
    return PixelSnippet(pixel_id=pixel_id, base_code=base_code, event_code=raw.get("event_code"))


async def get_event_log(browser: BrowserAutomationTool, event_id: str, title: str = "") -> EventLog:
    """Read Event Log popup table. Assumes popup already open."""
    js = """
    () => {
      const rows = [...document.querySelectorAll('.popup-container.aj_popup table.pr-table tbody tr')];
      return rows.map(r => {
        const cells = [...r.querySelectorAll('td')];
        if (cells.length < 2) return null;
        return { time: cells[0]?.innerText.trim() || '', domain: cells[1]?.innerText.trim() || '' };
      }).filter(x => x && x.time && x.time !== 'No recent activity found.');
    }
    """
    rows = await browser.evaluate(js) or []
    return EventLog(
        event_id=event_id,
        title=title,
        entries=[EventLogEntry(event_time=r["time"], domain=r["domain"]) for r in rows],
        window_hours=8,
    )


__all__ = [
    "get_event_log",
    "get_event_setup_snippet",
    "get_pixel_base_snippet",
    "list_events",
]
