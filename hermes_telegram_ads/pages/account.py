"""Parser for /account homepage and account-switcher dropdown."""

from __future__ import annotations

from contextlib import suppress

from hermes_telegram_ads.browser import BrowserAutomationTool
from hermes_telegram_ads.constants import KNOWN_STATUSES
from hermes_telegram_ads.parser import (
    detect_currency,
    extract_account_token,
    extract_ad_id,
    parse_int,
    parse_money,
    parse_percent,
    sanitize_number,
)
from hermes_telegram_ads.types import Account, AdStatus, AdSummary, Currency, DataQuality

# Cells that mean "no value" for a derived metric (CPC/CPA without clicks, etc.).
_DASH_TOKENS: frozenset[str] = frozenset({"–", "—", "-", ""})

# The ads table renders client-side; reading the DOM immediately after a
# domcontentloaded navigation can beat the render and yield zero rows. Wait this
# long for the table to appear before parsing (tolerated if it never does).
_TABLE_RENDER_TIMEOUT_MS = 8000


def _money_or_none(text: str) -> float | None:
    """Money value, or None when the cell is a dash/empty (no data).

    Unlike :func:`parse_money` (dash → 0.0), CPC/CPA are genuinely *absent* when
    there are no clicks/actions, so a dash maps to None rather than a fake 0.0.
    """
    if sanitize_number(text) in _DASH_TOKENS:
        return None
    return parse_money(text)


async def list_accounts(browser: BrowserAutomationTool) -> list[Account]:
    """Open account dropdown and read all available cabinets.

    Requires being on any authed page.
    """
    # The dropdown trigger holds avatar; click opens menu with .pr-dropdown-item rows.
    js = """
    () => {
      const trigger = document.querySelector('span.pr-dropdown-wrap span.pr-dropdown.dropdown-toggle');
      if (trigger && !trigger.parentElement.classList.contains('open')) {
        trigger.click();
      }
      const items = [...document.querySelectorAll('span.pr-dropdown-wrap.open ul.dropdown-menu li a.pr-dropdown-item[href^="/choose_account/"]')];
      const out = items.map(a => ({
        href: a.getAttribute('href'),
        text: (a.innerText || '').replace(/\\s+/g,' ').trim(),
        photo: a.querySelector('img')?.getAttribute('src') || null,
        is_selected: a.parentElement.classList.contains('photo-selected') ||
                      a.parentElement.classList.contains('selected'),
      }));
      // Close dropdown immediately (we already have data)
      if (trigger) trigger.click();
      return out;
    }
    """
    raw = await browser.evaluate(js) or []

    # Current account name + currency from header
    header_currency = await _header_currency(browser)
    current_name = await _header_account_name(browser)

    accounts: list[Account] = []
    for row in raw:
        href: str = row.get("href") or ""
        token = extract_account_token(href) or ""
        text: str = row.get("text") or ""
        photo: str | None = row.get("photo")
        is_create = href.endswith("/new")
        if is_create or not token:
            continue
        # Detect cabinet type
        is_stars = "⭐" in text or text.startswith("⭐️")
        title = text.lstrip("⭐️").strip()
        account_type = "Bot Account" if is_stars else "Personal Account"
        currency: Currency = "STARS" if is_stars else "TON"
        accounts.append(
            Account(
                account_token=token,
                title=title,
                account_type=account_type,  # type: ignore[arg-type]
                currency=currency,
                balance=0.0,  # filled separately by get_account_budget()
                photo_url=photo,
                is_active=bool(row.get("is_selected")) or (title == current_name),
            )
        )

    # If header currency disagrees with all detected, mark current as that currency
    if header_currency:
        for a in accounts:
            if a.is_active:
                a.currency = header_currency  # type: ignore[assignment]
    return accounts


async def _header_currency(browser: BrowserAutomationTool) -> Currency | None:
    has_ton = await browser.is_visible("span.amount-currency.currency-ton")
    has_stars = await browser.is_visible("span.amount-currency.currency-stars")
    if has_stars:
        return "STARS"
    if has_ton:
        return "TON"
    return None


async def _header_account_name(browser: BrowserAutomationTool) -> str | None:
    try:
        return await browser.read_text("span.pr-header-account-name")
    except Exception:
        return None


async def get_current_account(browser: BrowserAutomationTool) -> Account | None:
    """Read currently active account from header only (no dropdown click)."""
    name = await _header_account_name(browser)
    currency = await _header_currency(browser)
    if not name or not currency:
        return None
    # Read balance from header link
    bal_text = ""
    try:
        bal_text = await browser.read_text(".js-header_owner_budget")
    except Exception:
        bal_text = ""
    balance = parse_money(bal_text)
    return Account(
        account_token="",  # not visible in header; caller fills from list_accounts() if needed
        title=name,
        account_type="Bot Account" if currency == "STARS" else "Personal Account",
        currency=currency,
        balance=balance,
        is_active=True,
    )


# ─── Ads table ─────────────────────────────────────────────────────────────

# Header aliases for column mapping.
# Short headers (cpc, cpa, cpm, ctr, cvr) use EXACT match only.
# Longer headers use includes match for robustness.
_HEADER_ALIASES: dict[str, list[str]] = {
    "ad_title": ["ad title", "title"],
    "views": ["views", "impressions"],
    "clicks": ["clicks"],
    "actions": ["actions"],
    "ctr": ["ctr"],
    "cvr": ["cvr"],
    "cpm": ["cpm"],
    "cpc": ["cpc"],
    "cpa": ["cpa"],
    "spent": ["spent"],
    "budget": ["budget"],
    "target": ["target"],
    "status": ["status"],
    "date_added": ["date added", "added", "created"],
}
_STRICT_HEADERS: frozenset[str] = frozenset({"ctr", "cvr", "cpm", "cpc", "cpa"})


def _normalize_header(text: str) -> str:
    """Normalize header text for matching: lower, strip, collapse spaces."""
    return " ".join(sanitize_number(text.lower()).split())


def _build_column_map(headers: list[str]) -> dict[str, int]:
    """Map known column names to their index in the table header.

    Uses exact match for short headers (ctr, cvr, cpm, cpc, cpa).
    Uses includes match for longer headers.
    """
    col_map: dict[str, int] = {}
    for canonical, aliases in _HEADER_ALIASES.items():
        for idx, raw_header in enumerate(headers):
            normal = _normalize_header(raw_header)
            if canonical in _STRICT_HEADERS:
                if normal in aliases:
                    col_map[canonical] = idx
                    break
            else:
                if normal in aliases or any(a in normal for a in aliases if len(a) > 4):
                    col_map[canonical] = idx
                    break
    return col_map


def _missing_headers(col_map: dict[str, int]) -> list[str]:
    """Return canonical names not found in column map."""
    required = {"ad_title", "status", "spent", "budget"}
    return [h for h in required if h not in col_map]


def _compute_data_quality(
    col_map: dict[str, int],
    missing: list[str],
    ads: list[AdSummary],
) -> DataQuality:
    """Compute data quality based on column coverage and status confidence."""
    if not col_map:
        return "unreliable"
    if len(col_map) < 3:
        return "unreliable"
    if not ads:
        return "unreliable"
    # If all statuses are Unknown, data is unreliable
    if all(a.status == "Unknown" for a in ads):
        return "unreliable"
    # If any known status is present but some headers missing, partial
    if missing:
        return "partial"
    return "complete"


async def parse_ads_table(
    browser: BrowserAutomationTool, *, limit: int = 100
) -> dict:
    """Parse /account ads table with header-based column mapping.

    Returns:
        {
            "ads": list[AdSummary],
            "data_quality": "complete" | "partial" | "unreliable",
            "column_map": {"spent": 9, "status": 12, ...},
            "missing_headers": ["status"],
            "expected_columns": 14,
            "warnings": ["MISSING_HEADER: status not found in table headers"],
            "budget_column_label": "Budget"  # actual Telegram Ads header text
        }
    """
    warnings: list[str] = []
    expected_columns = 0

    # Phase 0: wait for the client-rendered table before reading the DOM.
    # Without this, a parse right after navigation can race the SPA render and
    # silently return zero campaigns (observed live for a cabinet that has ads).
    # Guarded by getattr so lightweight browser stubs (tests) without a
    # wait_for_selector still work; absence is tolerated (ad-less accounts).
    waiter = getattr(browser, "wait_for_selector", None)
    if waiter is not None:
        with suppress(Exception):
            await waiter("table.pr-table", timeout_ms=_TABLE_RENDER_TIMEOUT_MS)

    # Phase 1: Extract headers → column map
    header_js = """
    () => {
      const headerRow = document.querySelector('table.pr-table thead tr');
      if (!headerRow) return null;
      return [...headerRow.querySelectorAll('th')].map(th => th.innerText.trim());
    }
    """
    raw_headers = await browser.evaluate(header_js)
    if not raw_headers:
        # Table might not be rendered or not a table view
        return {
            "ads": [],
            "data_quality": "unreliable",
            "column_map": {},
            "missing_headers": ["no table headers found"],
            "expected_columns": 0,
            "warnings": ["PARSER_ERR: no table headers found — table may not be present"],
            "budget_column_label": "",
        }

    expected_columns = len(raw_headers)
    col_map = _build_column_map(raw_headers)
    missing = _missing_headers(col_map)
    for h in missing:
        warnings.append(f"MISSING_HEADER: {h} not found in table headers")

    # Budget column label (actual Telegram Ads header text)
    budget_col_label = ""
    if "budget" in col_map:
        idx = col_map["budget"]
        if 0 <= idx < len(raw_headers):
            budget_col_label = raw_headers[idx]

    # Phase 2: Parse rows.
    # NOTE: Playwright's page.evaluate passes a SINGLE arg, so this function
    # takes one object and destructures it. A previous `(limit, colMap) => …`
    # signature received the whole dict as `limit` (and `colMap === undefined`),
    # making `slice(0, limit)` → `slice(0, NaN)` → []: every row was dropped and
    # the table parsed as empty. Keep this as a single-arg function.
    row_js = """
    (arg) => {
      const limit = arg.limit;
      const colMap = arg.colMap;
      const rows = [...document.querySelectorAll('table.pr-table tbody tr')].slice(0, limit);
      return rows.map(r => {
        const cells = [...r.querySelectorAll('td')];
        const hasStatusBadge = !!r.querySelector('.pr-status-badge');
        const statusBadgeClass = r.querySelector('.pr-status-badge')?.className || '';
        const titleCellIdx = colMap['ad_title'] ?? 0;
        const titleA = cells[titleCellIdx]?.querySelector("a[href^='/account/ad/']");
        const statusBadgeText = r.querySelector('.pr-status-badge')?.innerText.trim() || '';
        return {
          ad_id_href: titleA?.getAttribute('href') || null,
          title: titleA?.innerText.trim() || cells[titleCellIdx]?.innerText.trim() || '',
          views:           (cells[colMap['views']]     ?? {innerText: ''}).innerText.trim() || '',
          clicks:          (cells[colMap['clicks']]    ?? {innerText: ''}).innerText.trim() || '',
          actions_cell:    (cells[colMap['actions']]   ?? {innerText: ''}).innerText.trim() || '',
          ctr:             (cells[colMap['ctr']]       ?? {innerText: ''}).innerText.trim() || '',
          cvr:             (cells[colMap['cvr']]       ?? {innerText: ''}).innerText.trim() || '',
          cpm:             (cells[colMap['cpm']]       ?? {innerText: ''}).innerText.trim() || '',
          cpc:             (cells[colMap['cpc']]       ?? {innerText: ''}).innerText.trim() || '',
          cpa:             (cells[colMap['cpa']]       ?? {innerText: ''}).innerText.trim() || '',
          spent:           (cells[colMap['spent']]     ?? {innerText: ''}).innerText.trim() || '',
          budget:          (cells[colMap['budget']]    ?? {innerText: ''}).innerText.trim() || '',
          target:          (cells[colMap['target']]    ?? {innerText: ''}).innerText.trim() || '',
          status_text:     statusBadgeText || (cells[colMap['status']] ?? {innerText: ''}).innerText.trim() || '',
          date:            (cells[colMap['date_added']] ?? {innerText: ''}).innerText.trim() || '',
          cell_count: cells.length,
          has_status_badge: hasStatusBadge,
          status_badge_class: statusBadgeClass,
        };
      });
    }
    """
    raw = await browser.evaluate(row_js, {"limit": limit, "colMap": col_map}) or []

    out: list[AdSummary] = []
    unknown_count = 0
    row_cell_counts: list[int] = []

    for row in raw:
        if not row.get("ad_id_href"):
            # Row without ad link — might be a section header or empty row
            continue
        ad_id = extract_ad_id(row.get("ad_id_href"))
        if ad_id is None:
            continue

        cell_count = row.get("cell_count", 0)
        row_cell_counts.append(cell_count)
        if cell_count != expected_columns:
            warnings.append(
                f"COLUMN_COUNT_MISMATCH: ad_id={ad_id} cells={cell_count} expected={expected_columns}"
            )

        # Status: try badge first, then column text
        status_text = (row.get("status_text") or "").strip()
        if status_text in KNOWN_STATUSES:
            status: AdStatus = status_text  # type: ignore[assignment]
        else:
            status = "Unknown"  # type: ignore[assignment]
            unknown_count += 1

        # Actions: "111\\nStart bot" → split
        actions_full = row.get("actions_cell") or ""
        parts = actions_full.split("\n", 1)
        actions_num = parse_int(parts[0]) if parts else 0
        action_label = parts[1].strip() if len(parts) > 1 else ""

        # Cabinet currency from whichever money cell carries the glyph.
        currency = (
            detect_currency(row.get("cpm") or "")
            or detect_currency(row.get("spent") or "")
            or detect_currency(row.get("budget") or "")
        )

        out.append(
            AdSummary(
                ad_id=ad_id,
                title=row.get("title") or "",
                status=status,
                target_summary=row.get("target") or "",
                views=parse_int(row.get("views") or ""),
                clicks=parse_int(row.get("clicks") or ""),
                actions=actions_num,
                action_label=action_label,
                ctr=parse_percent(row.get("ctr") or ""),
                cvr=parse_percent(row.get("cvr") or ""),
                cpm=parse_money(row.get("cpm") or ""),
                cpc=_money_or_none(row.get("cpc") or ""),
                cpa=_money_or_none(row.get("cpa") or ""),
                spent=parse_money(row.get("spent") or ""),
                budget=parse_money(row.get("budget") or ""),
                currency=currency,  # type: ignore[arg-type]
                date_added=row.get("date") or "",
            )
        )

    # Warnings: status parse issues
    if unknown_count > 0:
        warnings.append(
            f"STATUS_PARSE_FAILED: {unknown_count}/{len(out)} statuses are Unknown"
        )

    data_quality = _compute_data_quality(col_map, missing, out)

    return {
        "ads": out,
        "data_quality": data_quality,
        "column_map": col_map,
        "missing_headers": missing,
        "expected_columns": expected_columns,
        "warnings": warnings,
        "budget_column_label": budget_col_label,
    }


async def list_ads(browser: BrowserAutomationTool, *, limit: int = 100) -> list[AdSummary]:
    """Thin wrapper: parse ads table, return just AdSummary list.

    Backward-compatible. For rich result with data_quality + column_map,
    use parse_ads_table() directly.
    """
    result = await parse_ads_table(browser, limit=limit)
    return result["ads"]


async def get_header_balance(browser: BrowserAutomationTool) -> tuple[float, Currency | None]:
    """Read balance + currency from header without opening dropdown."""
    txt = ""
    try:
        txt = await browser.read_text(".js-header_owner_budget")
    except Exception:
        pass
    cur = detect_currency(txt)
    return parse_money(txt), cur  # type: ignore[return-value]


__all__ = [
    "get_current_account",
    "get_header_balance",
    "list_accounts",
    "list_ads",
    "parse_ads_table",
]
