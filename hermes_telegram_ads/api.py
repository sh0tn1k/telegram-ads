"""Internal JSON API wrapper for ads.telegram.org.

Endpoint: POST /api?hash=<csrf>
Body:     application/x-www-form-urlencoded
          owner_id=<account_token>&...&method=<methodName>

Auth: cookie session (set by manual Telegram login). The CSRF `hash` is
extracted from the page's inline `ajInit({apiUrl: "/api?hash=..."})`.

This module wraps the 14 discovered methods plus the separate
`POST /file/upload?hash=<csrf>` endpoint (multipart; requires owner_id + target).
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from hermes_telegram_ads.browser import BrowserAutomationTool
from hermes_telegram_ads.constants import (
    METHOD_CHECK_AD_POST,
    METHOD_CREATE_AD,
    METHOD_CREATE_DRAFT_FROM_AD,
    METHOD_CREATE_EVENT,
    METHOD_DECR_AD_BUDGET,
    METHOD_DELETE_AD,
    METHOD_DELETE_EVENT,
    METHOD_EDIT_AD,
    METHOD_EDIT_AD_CPM,
    METHOD_EDIT_AD_STATUS,
    METHOD_INCR_AD_BUDGET,
    METHOD_REVOKE_STATS_URL,
    METHOD_SAVE_AD_DRAFT,
    METHOD_SEARCH_CHANNEL,
)
from hermes_telegram_ads.errors import (
    InvalidMethodError,
    LoginRequiredError,
    TelegramAdsApiError,
)

_CSRF_RE = re.compile(r'apiUrl":"\\?/api\?hash=([a-f0-9]+)"')

# Inline-script ownerId extractor.
# Telegram Ads serializes Aj state as:  ajInit({apiUrl:"/api?hash=...", ownerId:"ACC..."})
# OwnerId may also appear bare in newer bundles:  ownerId:"ACC..."
# Format is non-greedy across both quote styles. Anchored on "ownerId" to avoid
# matching unrelated "owner_id" form field values.
_OWNER_ID_INLINE_RE = re.compile(
    r'ownerId["\\\']?\s*[:=]\s*["\\\']?([A-Za-z0-9_-]{4,64})["\\\']?'
)

# How long to wait for window.Aj.state to populate after a /choose_account?to=…
# redirect. Telegram's bundle initializes asynchronously and bootstrap() may
# run before Aj.state.ownerId is defined. 2s is empirically enough on
# healthy sessions; if Aj never loads we fall through to the next tier.
_AJ_STATE_WAIT_TIMEOUT_MS = 2000

# Hard upper bound on a single owner-id extraction tier (HTML scan / regex).
# Owner-id extraction may block on Aj state or HTML scan; 1.5s is plenty
# on a healthy DOM and caps blast radius if a tier hangs.
_OWNER_ID_FALLBACK_TIMEOUT_S = 1.5


class TelegramAdsApi:
    """Wrapper around POST /api?hash=<csrf>.

    Bootstrap is lazy — first api_request() triggers extraction.
    Call rebootstrap() explicitly after switching accounts.
    """

    def __init__(self, browser: BrowserAutomationTool) -> None:
        self._browser = browser
        self._hash: str | None = None
        self._owner_id: str | None = None
        self._currency_decimals: int | None = None

    # ─── Bootstrap ─────────────────────────────────────────────────────────

    @property
    def hash(self) -> str:
        if self._hash is None:
            raise TelegramAdsApiError("API not bootstrapped — call bootstrap() first")
        return self._hash

    @property
    def owner_id(self) -> str:
        if self._owner_id is None:
            raise TelegramAdsApiError("owner_id not set — call bootstrap() after navigating to /account")
        return self._owner_id

    @property
    def has_owner_id(self) -> bool:
        """Safe check — never raises; use instead of ``owner_id`` in conditionals."""
        return self._owner_id is not None

    @property
    def currency_decimals(self) -> int:
        return self._currency_decimals or 0

    async def bootstrap(self) -> None:
        """Extract csrf hash + owner_id + currency_decimals from current page.

        Must be called after navigating to an authed page (/account, /account/ad/...).
        Re-call after choose_account() because owner_id changes.

        owner_id resolution is 3-tier, in priority order. All tiers are bounded
        by timeouts so a slow tier never blocks bootstrap() indefinitely.

            1) window.Aj.state.ownerId            (primary, awaits Aj to load)
            2) <input name="owner_id"> value      (HTML form-field fallback;
                                                 swallows SelectorNotFoundError)
            3) inline-script regex over <body>   (last-ditch, scanned with
                                                 timeout via asyncio.wait_for)

        Failure: raises TelegramAdsApiError with a descriptive message and
        context={"url": ..., "tiers_tried": [...]}.
        """
        # 1) hash from JS global preferred; fallback to regex on HTML
        try:
            # Phase 1 of 2: wait for Aj state to populate (post-redirect race).
            # wait_for_function is a no-op if Aj is already loaded.
            try:
                await self._browser.wait_for_function(
                    "() => window.Aj && window.Aj.state && window.Aj.state.apiUrl",
                    timeout_ms=_AJ_STATE_WAIT_TIMEOUT_MS,
                )
            except Exception:
                # Aj never appeared within timeout — fall through to HTML fallback
                # for hash. We do NOT raise here; hash has its own fallback path.
                pass

            api_url = await self._browser.evaluate(
                "() => window.Aj && window.Aj.state && window.Aj.state.apiUrl"
            )
        except Exception:
            api_url = None

        if api_url:
            m = re.search(r"hash=([a-f0-9]+)", api_url)
            if m:
                self._hash = m.group(1)

        if self._hash is None:
            # Regex over HTML
            try:
                html = await self._browser.html()
            except Exception:
                html = ""
            m = _CSRF_RE.search(html or "")
            if not m:
                # Not logged in or completely unexpected page
                raise LoginRequiredError(
                    "Could not extract CSRF hash — likely not logged in",
                    context={"url": self._browser.current_url},
                )
            self._hash = m.group(1)

        # 2) owner_id — 3-tier fallback, all bounded by timeouts.
        owner = None
        tiers_tried: list[str] = []

        # Tier 1: window.Aj.state.ownerId (primary).
        # We already waited for Aj to load; capture it now.
        try:
            owner = await self._browser.evaluate(
                "() => window.Aj && window.Aj.state && window.Aj.state.ownerId"
            )
        except Exception:
            owner = None
        if owner:
            self._owner_id = owner
            tiers_tried.append("aj_global")
        else:
            tiers_tried.append("aj_global_missing")

            # Tier 2: <input name="owner_id"> — wrapped so SelectorNotFoundError
            # never propagates as a hard failure (this input is only present on
            # form pages, not on /account homepage).
            try:
                owner = await self._browser.read_attr(
                    "input[name='owner_id'][type='hidden']", "value"
                )
            except Exception:
                # SelectorNotFoundError / PlaywrightTimeoutError / anything else
                # → tier 2 unavailable, fall through to tier 3.
                owner = None
            if owner:
                self._owner_id = owner
                tiers_tried.append("form_input")
            else:
                tiers_tried.append("form_input_missing")

                # Tier 3: inline-script regex over page HTML. Bounded by
                # asyncio.wait_for so a slow Playwright call cannot block.
                try:
                    html_coro = self._browser.html()
                    html = await asyncio.wait_for(
                        html_coro, timeout=_OWNER_ID_FALLBACK_TIMEOUT_S
                    )
                except Exception:
                    html = ""
                m = _OWNER_ID_INLINE_RE.search(html or "")
                if m:
                    self._owner_id = m.group(1)
                    tiers_tried.append("inline_script")
                else:
                    tiers_tried.append("inline_script_missing")
                    # Note: TelegramAdsApiError's signature uses raw_response
                    # (dict), not the base class' context — keep our
                    # structured tier list there.
                    raise TelegramAdsApiError(
                        "Could not extract owner_id (account_token). "
                        "Tried: window.Aj.state.ownerId, "
                        "<input name='owner_id'>, inline-script regex. "
                        "Are you on an authed /account page?",
                        raw_response={
                            "url": self._browser.current_url,
                            "tiers_tried": tiers_tried,
                        },
                    )

        # 3) currency decimals
        try:
            decimals = await self._browser.evaluate(
                "() => window.Aj && window.Aj.state && window.Aj.state.ownerCurrencyDecimals"
            )
            if isinstance(decimals, int):
                self._currency_decimals = decimals
        except Exception:
            self._currency_decimals = None

    async def rebootstrap(self) -> None:
        self._hash = None
        self._owner_id = None
        self._currency_decimals = None
        await self.bootstrap()

    def reset(self) -> None:
        """Drop cached bootstrap state without re-reading the page.

        Used after a browser recovery: the fresh page is blank (about:blank),
        so we must NOT bootstrap() here (it would read an unauthed page).
        Instead we clear the cache so the next real navigation/operation
        re-bootstraps lazily against /account.
        """
        self._hash = None
        self._owner_id = None
        self._currency_decimals = None

    # ─── Low-level API call ────────────────────────────────────────────────

    async def api_request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """POST /api?hash=<csrf> with owner_id+method+params.

        Uses page.evaluate(fetch) so cookies + Aj globals are reused.
        Auto-rebootstraps once on `Bad request` (CSRF rotated).
        """
        if self._hash is None or self._owner_id is None:
            await self.bootstrap()

        body_params: dict[str, str] = {"owner_id": self._owner_id or "", "method": method}
        for k, v in (params or {}).items():
            if v is None:
                continue
            if isinstance(v, bool):
                body_params[k] = "1" if v else "0"
            else:
                body_params[k] = str(v)

        result = await self._post_form(self._hash or "", body_params)

        if isinstance(result, dict) and result.get("error") in {"Bad request"}:
            # CSRF likely rotated. Re-bootstrap and retry once.
            await self.rebootstrap()
            result = await self._post_form(self._hash or "", body_params)

        # Translate HTTP-level errors
        if isinstance(result, dict) and result.get("__http_status") == 400:
            raw = {k: v for k, v in result.items() if k != "__http_status"}
            if raw.get("error") == "Invalid method":
                raise InvalidMethodError(
                    f"Unknown API method: {method}",
                    method=method,
                    http_status=400,
                    raw_response=raw,
                )
            raise TelegramAdsApiError(
                str(raw.get("error", "HTTP 400")),
                method=method,
                http_status=400,
                raw_response=raw,
            )

        if not isinstance(result, dict):
            raise TelegramAdsApiError(
                f"Unexpected API response shape: {type(result).__name__}",
                method=method,
            )
        return result

    async def _post_form(self, csrf_hash: str, body_params: dict[str, str]) -> dict[str, Any]:
        # Build form body manually so JS path matches Aj.apiRequest exactly.
        body = "&".join(
            f"{_urlenc(k)}={_urlenc(v)}" for k, v in body_params.items()
        )

        js = """
        async ({hash, body}) => {
          const r = await fetch('/api?hash=' + hash, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
              'X-Requested-With': 'XMLHttpRequest',
              'Accept': 'application/json'
            },
            body,
            credentials: 'same-origin'
          });
          const text = await r.text();
          let parsed = null;
          try { parsed = JSON.parse(text); } catch (e) { parsed = { __raw_text: text }; }
          if (!r.ok) parsed.__http_status = r.status;
          return parsed;
        }
        """
        return await self._browser.evaluate(js, {"hash": csrf_hash, "body": body})

    async def api_mutate(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """``api_request`` for mutating calls, with embedded-error detection.

        Telegram returns validation failures for createAd/editAd/budget/etc. as
        an **HTTP 200** body shaped like checkAdPost's — ``{"field": ..., "error":
        "..."}`` — so :meth:`api_request` (which only raises on HTTP 400 / "Bad
        request") would hand that back as a *success*. A mutating call must never
        report success when the server rejected it, so a non-empty ``error`` here
        is raised. Read methods (searchChannel, checkAdPost) keep using
        :meth:`api_request` directly because they surface ``error`` as data.
        """
        result = await self.api_request(method, params)
        err = result.get("error")
        if err:
            field = result.get("field")
            msg = f"{err} (field: {field})" if field else str(err)
            raise TelegramAdsApiError(msg, method=method, raw_response=result)
        return result

    # ─── Read methods ──────────────────────────────────────────────────────

    async def search_channel(self, query: str, *, field: str = "channels") -> dict[str, Any]:
        """Resolve t.me/<name> or @<name> to opaque chip token.

        field: "channels" or "bots".
        Returns: {"ok": True, "channel": {"val": "<token>", "name": "...", ...}}
        """
        return await self.api_request(METHOD_SEARCH_CHANNEL, {"query": query, "field": field})

    async def check_ad_post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Live validation + preview-data.

        Pass current form state. Returns {field, error, preview_data}.
        """
        return await self.api_request(METHOD_CHECK_AD_POST, payload)

    # ─── Mutating methods ─────────────────────────────────────────────────

    async def save_ad_draft(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Auto-save draft. Same payload as createAd, different method name."""
        return await self.api_mutate(METHOD_SAVE_AD_DRAFT, payload)

    async def create_ad(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Submit new ad. Triggers moderation (status → In Review).

        Caller MUST gate through Safety.gate(CONFIRM_REQUIRED) before invoking.
        """
        return await self.api_mutate(METHOD_CREATE_AD, payload)

    async def create_draft_from_ad(self, ad_id: int) -> dict[str, Any]:
        """Clone existing ad → draft on /account/ad/new. Used by 'Create similar'."""
        return await self.api_mutate(METHOD_CREATE_DRAFT_FROM_AD, {"ad_id": ad_id})

    async def edit_ad(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Save Changes on /account/ad/<id>. Triggers re-review."""
        return await self.api_mutate(METHOD_EDIT_AD, payload)

    async def edit_ad_cpm(self, ad_id: int, cpm: float) -> dict[str, Any]:
        """⚠️ Method name is editAdCPM (UPPERCASE CPM)."""
        return await self.api_mutate(METHOD_EDIT_AD_CPM, {"ad_id": ad_id, "cpm": cpm})

    async def edit_ad_status(self, ad_id: int, *, active: bool) -> dict[str, Any]:
        """Toggle Active/On Hold. Triggers 2-min cooldown on decr_ad_budget."""
        return await self.api_mutate(METHOD_EDIT_AD_STATUS, {"ad_id": ad_id, "active": active})

    async def incr_ad_budget(self, ad_id: int, amount: float) -> dict[str, Any]:
        """Add to budget. TON cabinet rule: budget after add ≥ 1.00 TON."""
        return await self.api_mutate(METHOD_INCR_AD_BUDGET, {"ad_id": ad_id, "amount": amount})

    async def decr_ad_budget(self, ad_id: int, amount: float) -> dict[str, Any]:
        """Withdraw from budget.

        ⚠️ Parameter is `amount` (NOT `decr_amount` like the HTML form field).
        ⚠️ 2-min cooldown after edit_ad_status toggle.
        """
        return await self.api_mutate(METHOD_DECR_AD_BUDGET, {"ad_id": ad_id, "amount": amount})

    async def create_event(self, title: str, event_type: str) -> dict[str, Any]:
        """Create pixel event (Stars cabinet only)."""
        return await self.api_mutate(METHOD_CREATE_EVENT, {"title": title, "type": event_type})

    async def delete_event_step1(self, event_id: str) -> dict[str, Any]:
        """First step of 2-step delete. Returns {confirm_text, confirm_hash}."""
        return await self.api_request(METHOD_DELETE_EVENT, {"event_id": event_id})

    async def delete_event_step2(self, event_id: str, confirm_hash: str) -> dict[str, Any]:
        return await self.api_mutate(
            METHOD_DELETE_EVENT,
            {"event_id": event_id, "confirm_hash": confirm_hash},
        )

    async def delete_ad_step1(self, ad_id: int) -> dict[str, Any]:
        return await self.api_request(METHOD_DELETE_AD, {"ad_id": ad_id})

    async def delete_ad_step2(self, ad_id: int, confirm_hash: str) -> dict[str, Any]:
        return await self.api_mutate(
            METHOD_DELETE_AD,
            {"ad_id": ad_id, "confirm_hash": confirm_hash},
        )

    async def revoke_stats_url(self, ad_id: int) -> dict[str, Any]:
        """Rotate share-stats token. Old public URL invalidated."""
        return await self.api_mutate(METHOD_REVOKE_STATS_URL, {"ad_id": ad_id})

    # ─── 2-step destructive helpers ───────────────────────────────────────

    async def delete_event_full(self, event_id: str) -> dict[str, Any]:
        """Convenience: do both steps. Caller must already be Safety-gated."""
        step1 = await self.delete_event_step1(event_id)
        if "confirm_hash" not in step1:
            raise TelegramAdsApiError(
                f"deleteEvent step1 did not return confirm_hash: {step1}",
                method=METHOD_DELETE_EVENT,
            )
        return await self.delete_event_step2(event_id, step1["confirm_hash"])

    async def delete_ad_full(self, ad_id: int) -> dict[str, Any]:
        """Convenience: do both steps. Caller must already be Safety-gated."""
        step1 = await self.delete_ad_step1(ad_id)
        if "confirm_hash" not in step1:
            raise TelegramAdsApiError(
                f"deleteAd step1 did not return confirm_hash: {step1}",
                method=METHOD_DELETE_AD,
            )
        return await self.delete_ad_step2(ad_id, step1["confirm_hash"])

    # ─── File upload (separate endpoint) ──────────────────────────────────

    async def upload_media(self, file_path: str, *, target: str = "ad_media") -> dict[str, Any]:
        """POST /file/upload?hash=<csrf> (multipart/form-data).

        The endpoint is **not** cookie-auth-only: it requires the CSRF ``hash``
        (query, like /api), the ``owner_id`` form field, and a ``target`` naming
        the upload bucket. ``target=ad_media`` is the ad-creative bucket (verified
        live 2026-06-05; without owner_id → "Access denied", without target →
        "Unknown target"). Success: ``{"media": "<token>"}``. Error example:
        ``{"error": "The aspect ratio must be 16:9."}``.
        """
        from pathlib import Path

        if self._hash is None or self._owner_id is None:
            await self.bootstrap()

        p = Path(file_path)
        if not p.exists():
            raise TelegramAdsApiError(f"File not found: {file_path}")
        data = p.read_bytes()

        # Use page.evaluate to send via FormData so cookies + Aj globals are reused.
        js = """
        async ({fileName, bytes, mime, hash, ownerId, target}) => {
          const ab = new Uint8Array(bytes).buffer;
          const blob = new Blob([ab], { type: mime });
          const fd = new FormData();
          fd.append('owner_id', ownerId);
          fd.append('target', target);
          fd.append('file', blob, fileName);
          const r = await fetch('/file/upload?hash=' + hash, {
            method: 'POST',
            body: fd,
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            credentials: 'same-origin'
          });
          const text = await r.text();
          let parsed = null;
          try { parsed = JSON.parse(text); } catch (e) { parsed = { __raw_text: text }; }
          if (!r.ok) parsed.__http_status = r.status;
          return parsed;
        }
        """
        mime = _guess_mime(p.suffix.lower())
        return await self._browser.evaluate(
            js,
            {
                "fileName": p.name,
                "bytes": list(data),
                "mime": mime,
                "hash": self._hash or "",
                "ownerId": self._owner_id or "",
                "target": target,
            },
        )


# ─── Helpers ──────────────────────────────────────────────────────────────


def _urlenc(s: str) -> str:
    """URL-encode for application/x-www-form-urlencoded body."""
    from urllib.parse import quote_plus

    return quote_plus(s)


def _guess_mime(ext: str) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
    }.get(ext, "application/octet-stream")
