"""Creative CPM modifiers for Telegram Ads.

ads.telegram.org raises the *effective* CPM when certain creative options are
enabled on an ad:

    - Show bot/channel picture .... +30%
    - Custom emoji in ad text ...... +50%
    - Uploaded photo .............. +50% (estimate only — see below)
    - Uploaded video .............. +80%

These percentages are read off the Telegram Ads UI labels. They let us *estimate*
the combined effective CPM for an approval summary, but the estimate is NOT
authoritative — Telegram's live validation / UI is the source of truth, and the
exact stacking behaviour for multiple modifiers is not contractually guaranteed.
This module never mutates ``draft.cpm``; it only reports.

**Uploaded-media surcharges are UI-authoritative.** The static ``media_photo`` /
``media_video`` percentages below are estimates that may vary by placement and
account: a 2026-06 live channel *photo* test showed ``cpm_extra="+80%"``, not the
+50% assumed here. Any uploaded-media modifier therefore sets
``needs_validation=True`` so callers prefer the checkAdPost / UI ``cpm_extra``
value when it is available. Do not rely on the static media modifier alone.

The combined estimate stacks multiplicatively (``effective *= 1 + pct/100`` per
modifier) — deliberately a multiply, not an assignment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

MediaType = Literal["photo", "video"]

# UI-declared surcharge percentages (creative option -> percent).
CPM_MODIFIERS: dict[str, int] = {
    "show_picture": 30,
    "custom_emoji": 50,
    "media_photo": 50,
    "media_video": 80,
}

MODIFIER_SOURCE = "Telegram Ads UI"

# Custom-emoji markers we can detect reliably without a full text-entities layer:
#   - tg://emoji?id=<id>   (markdown link target or raw href)
#   - <tg-emoji emoji-id=> (Telegram HTML)
_CUSTOM_EMOJI_RE = re.compile(r"tg://emoji\?id=|<tg-emoji\b", re.IGNORECASE)


def detect_custom_emoji(text: str | None) -> bool:
    """True if *text* contains a custom/premium emoji marker.

    Detection is conservative (markdown ``tg://emoji?id=`` and the ``<tg-emoji>``
    HTML tag). Plain unicode premium-emoji glyphs are NOT flagged — they cannot be
    told apart from ordinary emoji reliably from text alone. We never rewrite the
    text; we only surface a warning so the operator can account for the +50% CPM.
    """
    if not text:
        return False
    return bool(_CUSTOM_EMOJI_RE.search(text))


@dataclass
class CpmEstimate:
    """Estimated effective CPM after creative modifiers (advisory only)."""

    base_cpm: float
    estimated_effective_cpm: float
    modifiers_applied: list[str] = field(default_factory=list)
    modifier_percents: dict[str, int] = field(default_factory=dict)
    modifier_source: str = MODIFIER_SOURCE
    # "none" (no modifiers), "high" (single modifier), "estimate" (stacked).
    modifier_confidence: str = "none"
    # True when >1 modifier stacks — combined surcharge must be verified via the UI.
    needs_validation: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "base_cpm": self.base_cpm,
            "estimated_effective_cpm": self.estimated_effective_cpm,
            "modifiers_applied": list(self.modifiers_applied),
            "modifier_percents": dict(self.modifier_percents),
            "modifier_source": self.modifier_source,
            "modifier_confidence": self.modifier_confidence,
            "needs_validation": self.needs_validation,
        }


def compute_effective_cpm(
    base_cpm: float,
    *,
    show_picture: bool = False,
    custom_emoji: bool = False,
    media_type: MediaType | None = None,
) -> CpmEstimate:
    """Estimate the effective CPM after creative modifiers.

    Stacks multiplicatively. Does NOT modify the input. With two or more modifiers
    the result is flagged ``needs_validation`` (the combined surcharge is an
    estimate; Telegram's UI/validation is authoritative).

    >>> compute_effective_cpm(65, show_picture=True).estimated_effective_cpm
    84.5
    >>> compute_effective_cpm(65, media_type="video").estimated_effective_cpm
    117.0
    """
    applied: list[str] = []
    if show_picture:
        applied.append("show_picture")
    if custom_emoji:
        applied.append("custom_emoji")
    if media_type == "photo":
        applied.append("media_photo")
    elif media_type == "video":
        applied.append("media_video")

    effective = float(base_cpm)
    for name in applied:
        effective *= 1 + CPM_MODIFIERS[name] / 100  # multiply, never assign

    effective = round(effective, 4)
    media_applied = "media_photo" in applied or "media_video" in applied
    if not applied:
        confidence = "none"
    elif len(applied) == 1:
        confidence = "high"
    else:
        confidence = "estimate"
    # Uploaded-media surcharges are UI-authoritative (may differ from the static
    # table); always flag for validation when media is involved.
    needs_validation = len(applied) >= 2 or media_applied

    return CpmEstimate(
        base_cpm=float(base_cpm),
        estimated_effective_cpm=effective,
        modifiers_applied=applied,
        modifier_percents={name: CPM_MODIFIERS[name] for name in applied},
        modifier_source=MODIFIER_SOURCE,
        modifier_confidence=confidence,
        needs_validation=needs_validation,
    )


__all__ = [
    "CPM_MODIFIERS",
    "MODIFIER_SOURCE",
    "CpmEstimate",
    "MediaType",
    "compute_effective_cpm",
    "detect_custom_emoji",
]
