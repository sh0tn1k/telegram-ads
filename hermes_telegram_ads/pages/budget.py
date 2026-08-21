"""Parser for /account/budget and ad-level /account/ad/<id>/budget."""

from __future__ import annotations

import re

from hermes_telegram_ads.browser import BrowserAutomationTool
from hermes_telegram_ads.parser import detect_currency, parse_money
from hermes_telegram_ads.types import AccountBudget, Transaction, TransactionKind


async def get_account_budget(browser: BrowserAutomationTool) -> AccountBudget:
    """Parse /account/budget. Assumes already navigated."""
    js = """
    () => {
      const bal = document.querySelector('.js-header_owner_budget')?.innerText.trim()
                || document.querySelector('.pr-owner-balance-value')?.innerText.trim()
                || '';
      const rows = [...document.querySelectorAll('table.pr-table tbody tr')].map(r => {
        const cells = [...r.querySelectorAll('td')];
        return cells.map(c => c.innerText.trim());
      });
      return { bal, rows };
    }
    """
    raw = await browser.evaluate(js) or {}
    bal_text: str = raw.get("bal") or ""
    currency = detect_currency(bal_text) or "TON"
    txns = [_parse_tx_row(row, currency) for row in (raw.get("rows") or []) if len(row) >= 3]
    return AccountBudget(
        balance=parse_money(bal_text),
        currency=currency,  # type: ignore[arg-type]
        transactions=[t for t in txns if t is not None],
    )


def _parse_tx_row(row: list[str], currency: str) -> Transaction | None:
    # Row shape (typical): [description, sign?, amount, when] or similar.
    text = " | ".join(row).strip()
    # Heuristic: find amount cell (contains 💎 or ⭐️)
    amount_cell = None
    when_cell = ""
    desc_cell = ""
    for cell in row:
        if "💎" in cell or "⭐" in cell or "⭐️" in cell:
            amount_cell = cell
        elif _looks_like_date(cell):
            when_cell = cell
        elif cell and not desc_cell:
            desc_cell = cell

    if amount_cell is None:
        return None

    raw_amount = parse_money(amount_cell)
    is_negative = "-" in amount_cell or amount_cell.startswith("-")
    amount = -raw_amount if is_negative else raw_amount

    kind = _detect_tx_kind(desc_cell)
    ad_title = _extract_ad_title(desc_cell)
    reference = _extract_reference(desc_cell)

    return Transaction(
        kind=kind,
        amount=amount,
        currency=currency,  # type: ignore[arg-type]
        when=when_cell,
        ad_title=ad_title,
        reference=reference,
        raw_text=text,
    )


# A date cell looks like "Jun 4 at 22:41": a word-boundary month, the word
# "at", or a HH:MM time. The old heuristic also matched the bare substring "20",
# which misfires on ad titles/descriptions containing a year-like run (e.g.
# "HERMES_E2E_DO_NOT_USE_20260604_..."), stealing the description cell.
_DATE_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b|\bat\b|\d{1,2}:\d{2}",
    re.IGNORECASE,
)


def _looks_like_date(s: str) -> bool:
    return bool(_DATE_RE.search(s))


def _detect_tx_kind(desc: str) -> TransactionKind:
    desc_l = desc.lower()
    if desc_l.startswith("payment: ref"):
        return "payment"
    if desc_l.startswith("payment for "):
        return "payment_for_views"
    if desc_l.startswith("transfer from bot"):
        return "transfer_from_bot"
    if desc_l.startswith("transfer to the ad"):
        return "transfer_to_ad"
    if desc_l.startswith("returned from the ad"):
        return "returned_from_ad"
    if desc_l.startswith("transfer from the account"):
        return "transfer_from_account"
    if desc_l.startswith("transfer to the account"):
        return "transfer_to_account"
    return "unknown"


def _extract_ad_title(desc: str) -> str | None:
    for prefix in (
        "Transfer to the ad ",
        "Returned from the ad ",
    ):
        if desc.startswith(prefix):
            return desc[len(prefix):].strip()
    return None


def _extract_reference(desc: str) -> str | None:
    m = re.search(r"Ref#(\S+)", desc)
    return m.group(1) if m else None


__all__ = ["get_account_budget"]
