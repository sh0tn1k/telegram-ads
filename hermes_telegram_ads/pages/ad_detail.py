"""Parser for /account/ad/<id> Info tab."""

from __future__ import annotations

import re

from hermes_telegram_ads.browser import BrowserAutomationTool
from hermes_telegram_ads.parser import parse_money
from hermes_telegram_ads.types import Ad, AdDetail, AdStatus, DeclineReason, TrgType


def _parse_current_budget(display: str) -> float:
    """Pull the total budget out of a 'Current budget in Stars ⭐️ 100 …' block."""
    m = re.search(r"current budget[^\d]*([\d][\d.,\s]*)", display, re.IGNORECASE)
    return parse_money(m.group(1)) if m else 0.0


async def get_ad_detail(browser: BrowserAutomationTool, ad_id: int) -> AdDetail:
    """Parse /account/ad/<id> page. Assumes browser already navigated there."""
    js = r"""
    () => {
      const get = (sel, attr) => {
        const el = document.querySelector(sel);
        if (!el) return null;
        if (attr === '__text') return (el.innerText || '').trim();
        if (attr === '__value') return el.value || '';
        return el.getAttribute(attr);
      };
      const decline = document.querySelector('.pr-decline-block');
      // ``mediaWrap`` is the field container labelled "Ad photo or video".
      // Computed once and reused by all media signals below.
      const mediaWrap = (() => {
        const c = document.querySelector(
          '.js-field-media, [class*="field-media"], [class*="media-field"]');
        if (c) return c;
        for (const lbl of document.querySelectorAll(
               'label, .pr-form-label, .field-label, .pr-form-info-block')) {
          if (/photo or video/i.test(lbl.innerText || ''))
            return lbl.closest('.pr-form-row, .field, .form-group')
                   || lbl.parentElement || lbl;
        }
        return null;
      })();
      // True when an element inside ``root`` carries a CSS background-image
      // (Telegram renders the uploaded photo preview as a background).
      const hasBgImage = (root) => {
        if (!root) return false;
        const nodes = [root, ...root.querySelectorAll('*')];
        for (const el of nodes) {
          let bg = '';
          try { bg = getComputedStyle(el).backgroundImage || ''; } catch (e) { bg = ''; }
          const inline = (el.getAttribute && el.getAttribute('style')) || '';
          if (/url\(/i.test(bg) || /background-image\s*:\s*url\(/i.test(inline)) return true;
        }
        return false;
      };
      return {
        title: get('input[name="title"]', '__value') || '',
        text: get('textarea[name="text"]', '__value') || '',
        promote_url: get('input[name="promote_url"]', '__value') || '',
        website_name: get('input[name="website_name"]', '__value') || '',
        media_token: get('input[name="media"]', '__value') || '',
        // Uploaded-media signals. Derivation happens in Python; here we only
        // collect raw DOM presence (booleans) so the mapping stays unit-testable.
        media_block_present: !!(mediaWrap
          && (mediaWrap.querySelector('img, video')
              || /change media/i.test(mediaWrap.innerText || ''))),
        media_preview_img: (() => {
          if (mediaWrap && (mediaWrap.querySelector('img') || hasBgImage(mediaWrap)))
            return true;
          return !!document.querySelector(
            '.media-preview img, img.ad-media-preview, [class*="media"] img.preview');
        })(),
        media_preview_video: (() => {
          if (mediaWrap && (mediaWrap.querySelector('video')
              || mediaWrap.querySelector('video source, source[type^="video"]')))
            return true;
          return !!document.querySelector(
            '.media-preview video, video.ad-media-preview, video source');
        })(),
        change_media_button: (() => {
          for (const b of document.querySelectorAll('button, a, .button, .btn')) {
            if (/change media/i.test(b.innerText || '')) return true;
          }
          return false;
        })(),
        picture_checkbox_present: !!document.querySelector("input[name='picture']"),
        picture_checked: !!document.querySelector("input[name='picture']:checked"),
        cpm: get('input[name="cpm"]', '__value') || '0',
        daily_budget: get('input[name="daily_budget"]', '__value') || '',
        active: !!document.querySelector("input[name='active'][value='1']:checked"),
        views_per_user: parseInt(
          document.querySelector("input[name='views_per_user']:checked")?.value || '1', 10
        ),
        target_type: document.querySelector("input[name='target_type'][type='hidden']")?.value
                    || document.querySelector("input[name='target_type']:checked")?.value
                    || '',
        ad_status_text: (document.querySelector('a.ad-declined')?.innerText
                          || document.title || '').trim(),
        // Decline block (optional)
        decline_category: decline?.querySelector('.pr-decline-reason')?.innerText.trim() || null,
        decline_desc: decline?.querySelector('.pr-decline-reason-desc')?.innerText.trim() || null,
        decline_link_href: decline?.querySelector('.pr-decline-reason-desc a')?.href || null,
        // Locked targeting values rendered as read-only chips (immutable after
        // creation): for a search ad these are the target search queries.
        locked_targets: [...document.querySelectorAll('.pr-form-info-block.plus .value')]
          .map(e => (e.innerText || '').trim()).filter(Boolean),
        // Total allocated budget is a read-only display ("Current budget in
        // Stars ⭐️ 100"), NOT the daily_budget input. Capture its block text.
        budget_display: (() => {
          for (const b of document.querySelectorAll(
                 '.js-field-budget-wrap, [class*="budget"], .pr-form-info-block')) {
            const t = (b.innerText || '').replace(/\s+/g, ' ').trim();
            if (/current budget/i.test(t) && t.length < 240) return t;
          }
          return '';
        })(),
      };
    }
    """
    raw = await browser.evaluate(js) or {}

    # Locked targeting chips (read-only on the detail page; immutable after
    # creation). Dedupe preserving order.
    locked_targets: list[str] = []
    _seen: set[str] = set()
    for t in raw.get("locked_targets") or []:
        if t and t not in _seen:
            _seen.add(t)
            locked_targets.append(t)

    decline: DeclineReason | None = None
    if raw.get("decline_category"):
        decline = DeclineReason(
            category=raw.get("decline_category") or "",
            description=raw.get("decline_desc") or "",
            read_more_url=raw.get("decline_link_href"),
        )

    # Status not reliably visible on detail page. Caller should pass status from
    # list_ads() result. We default to "In Review" if decline present means Declined.
    if decline is not None:
        status: AdStatus = "Declined"
    else:
        status = "Active" if raw.get("active") else "On Hold"

    trg: TrgType = (
        "channel"
        if raw.get("target_type") == "channels"
        else "bot"
        if raw.get("target_type") == "bots"
        else "search"
    )

    ad = Ad(
        ad_id=ad_id,
        title=raw.get("title") or "",
        text=raw.get("text") or "",
        trg_type=trg,
        cpm=parse_money(raw.get("cpm") or "0"),
        budget=_parse_current_budget(raw.get("budget_display") or ""),
        status=status,
        status_url="",
    )

    # ── Uploaded-media state (derived; NEVER from website_name) ──────────────
    # has_media is true when a media token is present OR a visible media preview
    # block exists. media_type is inferred from preview kind; "unknown" when
    # media is present but the kind can't be told apart; "" when no media.
    media_token = raw.get("media_token") or ""
    prev_img = bool(raw.get("media_preview_img"))
    prev_video = bool(raw.get("media_preview_video"))
    media_block = bool(raw.get("media_block_present")) or bool(raw.get("change_media_button"))
    media_preview_present = prev_img or prev_video or media_block
    has_media = bool(media_token) or media_preview_present
    if not has_media:
        media_type = ""
    elif prev_video:
        media_type = "video"
    elif prev_img:
        media_type = "photo"
    else:
        media_type = "unknown"
    # show_picture: the picture checkbox state, or None when the checkbox is not
    # exposed (media mode can hide it) so it cannot be inferred.
    show_picture: bool | None = (
        bool(raw.get("picture_checked")) if raw.get("picture_checkbox_present") else None
    )

    return AdDetail(
        ad=ad,
        decline_reason=decline,
        promote_url=raw.get("promote_url") or "",
        website_name=raw.get("website_name") or "",
        daily_budget_value=parse_money(raw.get("daily_budget") or "0") or None,
        views_per_user=int(raw.get("views_per_user") or 1),
        is_active=bool(raw.get("active")),
        media_token=media_token,
        has_media=has_media,
        media_type=media_type,
        show_picture=show_picture,
        media_preview_present=media_preview_present,
        locked_targets=locked_targets,
    )


__all__ = ["get_ad_detail"]
