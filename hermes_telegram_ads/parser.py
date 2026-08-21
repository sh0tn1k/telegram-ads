"""Shared parsing helpers used by page-specific parsers.

Server-rendered HTML → typed objects. All helpers are pure functions over
strings, so they're trivially unit-testable from raw_*.json fixtures.
"""

from __future__ import annotations

import re
from typing import Final

# ─── Sanitization ──────────────────────────────────────────────────────────

_NON_BREAKING_SPACE: Final = "\u00a0"
_NARROW_NO_BREAK_SPACE: Final = "\u202f"
_THIN_SPACE: Final = "\u2009"
_EN_SPACE: Final = "\u2002"
_EM_SPACE: Final = "\u2003"
_FIGURE_SPACE: Final = "\u2007"
_ALL_SPECIAL_SPACES: Final = re.compile(
    "["
    + re.escape(
        _NON_BREAKING_SPACE
        + _NARROW_NO_BREAK_SPACE
        + _THIN_SPACE
        + _EN_SPACE
        + _EM_SPACE
        + _FIGURE_SPACE
    )
    + "]"
)


def sanitize_number(text: str) -> str:
    """Normalise whitespace, remove special spaces, collapse spaces, strip.

    Handles:
      - non-breaking spaces (\\u00a0)
      - narrow no-break space (\\u202f)
      - thin space (\\u2009)
      - en/em spaces
      - HTML &nbsp; (already decoded by browser to \\u00a0)
      - leading/trailing whitespace
      - multiple consecutive spaces → single space
      - comma separators (thousand separators) removed
    """
    if not text:
        return ""
    cleaned = _ALL_SPECIAL_SPACES.sub(" ", text)
    # Remove commas (thousand separators)
    cleaned = cleaned.replace(",", "")
    # Collapse multiple spaces into one
    while "  " in cleaned:
        cleaned = cleaned.replace("  ", " ")
    return cleaned.strip()


# Money pattern matches:  💎11.97 / ⭐️549 / 💎0.4 / 💎1.87 / ⭐ 97 / +💎0.0035
# Drops emoji/SVG noise around the digit. Handles space between star and number.
_MONEY_RE: Final = re.compile(r"([0-9][0-9]*(?:\.[0-9]+)?)")
_INT_RE: Final = re.compile(r"-?[0-9][0-9]*")
_PCT_RE: Final = re.compile(r"([0-9]+(?:[,.][0-9]+)?)\s*%")

_TON_GLYPHS: Final = {"💎"}  # one is U+1F48E
_STARS_GLYPHS: Final = {"⭐️", "⭐"}


def parse_money(text: str) -> float:
    """Parse '💎11.97' / '⭐️549' / '⭐ 97' / '+💎0.0035' etc. → float.

    Handles space between glyph and number.
    Handles space-separated thousands: '⭐ 1 592' → 1592.
    Returns 0.0 for '–' / empty.
    """
    if not text:
        return 0.0
    cleaned = sanitize_number(text)
    if cleaned in {"–", "—", "-", "0", ""}:
        return 0.0
    # Drop leading + and currency glyphs
    cleaned = cleaned.lstrip("+")
    # Remove all remaining spaces (thousand separators)
    cleaned = cleaned.replace(" ", "")
    m = _MONEY_RE.search(cleaned)
    if not m:
        return 0.0
    return float(m.group(1))


def parse_int(text: str) -> int:
    """Parse '5,391' / '1 592' / '0' / '–' → int.

    Handles:
      - comma separators
      - non-breaking spaces as thousand separators
      - regular spaces as thousand separators
      - special spaces
      - em dash / en dash
    """
    if not text:
        return 0
    cleaned = sanitize_number(text)
    if cleaned in {"–", "—", "-", "0", ""}:
        return 0
    # Drop all spaces (thousand separators)
    cleaned = cleaned.replace(" ", "")
    m = _INT_RE.search(cleaned)
    return int(m.group(0)) if m else 0


def parse_percent(text: str) -> float | None:
    """Parse '3.91%' → 3.91. '1 592,28%' → 1592.28. None for '–' / empty.

    Handles space + % suffix.
    Handles comma as decimal separator: '45,28%' → 45.28.
    Does NOT use sanitize_number (which removes comma thousand separators).
    """
    if not text:
        return None
    # Remove special spaces, collapse, strip — but KEEP commas for decimal detection
    cleaned = _ALL_SPECIAL_SPACES.sub(" ", text)
    cleaned = cleaned.strip()
    if cleaned in {"–", "—", "-", ""}:
        return None
    m = _PCT_RE.search(cleaned)
    if not m:
        return None
    # Comma is decimal separator in EU notation
    raw = m.group(1).replace(" ", "").replace(",", ".")
    return float(raw)


# ─── Currency detection ────────────────────────────────────────────────────


def detect_currency(text: str) -> str | None:
    """Return 'TON' / 'STARS' / None based on glyph presence."""
    if any(g in text for g in _TON_GLYPHS):
        return "TON"
    if any(g in text for g in _STARS_GLYPHS):
        return "STARS"
    return None


# ─── Ad-id helpers ────────────────────────────────────────────────────────


_AD_HREF_RE = re.compile(r"/account/ad/(\d+)")
_ACCOUNT_HREF_RE = re.compile(r"/choose_account/([A-Za-z0-9_-]+)")
_EVENT_HREF_RE = re.compile(r"/account/event/([A-Za-z0-9_-]+)")
_SHARE_TOKEN_RE = re.compile(r"/stats/([A-Za-z0-9_-]+)")


def extract_ad_id(href: str | None) -> int | None:
    if not href:
        return None
    m = _AD_HREF_RE.search(href)
    return int(m.group(1)) if m else None


def extract_account_token(href: str | None) -> str | None:
    if not href:
        return None
    m = _ACCOUNT_HREF_RE.search(href)
    return m.group(1) if m else None


def extract_event_id(href: str | None) -> str | None:
    if not href:
        return None
    m = _EVENT_HREF_RE.search(href)
    return m.group(1) if m else None


def extract_share_token(url: str | None) -> str | None:
    if not url:
        return None
    m = _SHARE_TOKEN_RE.search(url)
    return m.group(1) if m else None
