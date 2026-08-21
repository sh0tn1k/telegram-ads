"""TelegramAdsAdapter — public facade for Hermes.

Composes BrowserAutomationTool + TelegramAdsApi + TelegramAdsSafety + AuditLogger
and exposes a clean async API to Hermes / skills.

All mutating methods go through Safety.gate(action, params, confirmation_id).
Safe reads don't require confirmation.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any, Self

from hermes_telegram_ads.api import TelegramAdsApi
from hermes_telegram_ads.audit import AuditLogger
from hermes_telegram_ads.browser import BrowserAutomationTool
from hermes_telegram_ads.config import TelegramAdsConfig
from hermes_telegram_ads.constants import (
    BASE_URL,
    METHOD_CREATE_AD,
    METHOD_CREATE_EVENT,
    METHOD_DECR_AD_BUDGET,
    METHOD_DELETE_AD,
    METHOD_DELETE_EVENT,
    METHOD_EDIT_AD,
    METHOD_EDIT_AD_CPM,
    METHOD_EDIT_AD_STATUS,
    METHOD_INCR_AD_BUDGET,
    METHOD_REVOKE_STATS_URL,
    URL_ACCOUNT,
    URL_ACCOUNT_BUDGET,
    URL_ACCOUNT_STATS,
    URL_AD,
    URL_AD_NEW,
    URL_AD_STATS,
    URL_AD_STATS_SHARE,
    URL_AUTH,
    URL_CHOOSE_ACCOUNT_TOKEN,
    URL_EVENT_LOG,
    URL_EVENT_SETUP,
    URL_EVENTS,
    URL_EVENTS_SETUP,
)
from hermes_telegram_ads.errors import (
    LoginRequiredError,
    MediaUnsupportedError,
    OwnerBootstrapFailedError,
    TelegramAdsApiError,
)
from hermes_telegram_ads.login_flow import (
    DomSignals,
    LoginState,
    classify_login_dom,
    instructions_for,
    logged_in_for,
    mask_phone,
    recovery_hint_for,
    requires_human_login,
)
from hermes_telegram_ads.media import (
    VIDEO_UPLOAD_SUPPORTED,
    assert_uploaded_media_supported_for_target_type,
    media_type_for_path,
)
from hermes_telegram_ads.media import upload_media as upload_media_helper
from hermes_telegram_ads.pages import account as account_page
from hermes_telegram_ads.pages import ad_detail as ad_detail_page
from hermes_telegram_ads.pages import ad_stats as ad_stats_page
from hermes_telegram_ads.pages import budget as budget_page
from hermes_telegram_ads.pages import events as events_page
from hermes_telegram_ads.pages import profile as profile_page
from hermes_telegram_ads.payloads import (
    build_create_ad_payload,
    build_edit_ad_payload,
    create_confirmation_params,
)
from hermes_telegram_ads.reports import (
    download_account_monthly_report,
    download_ad_monthly_report,
)
from hermes_telegram_ads.safety import Confirmation, RiskLevel, TelegramAdsSafety, get_risk_level
from hermes_telegram_ads.types import (
    Account,
    AccountBudget,
    AdDetail,
    AdMutationResponse,
    AdStats,
    AdSummary,
    CreateAdDraft,
    EditAdDraft,
    Event,
    EventLog,
    PixelSnippet,
)


class TelegramAdsAdapter:
    """High-level adapter for ads.telegram.org.

    Lifecycle:
        async with TelegramAdsAdapter.start(config) as ads:
            await ads.ensure_logged_in()
            ...

    Or manually:
        ads = await TelegramAdsAdapter.launch(config)
        try:
            ...
        finally:
            await ads.close()
    """

    def __init__(
        self,
        config: TelegramAdsConfig,
        browser: BrowserAutomationTool,
        api: TelegramAdsApi,
        safety: TelegramAdsSafety,
        audit: AuditLogger,
    ) -> None:
        self.config = config
        self.browser = browser
        self.api = api
        self.safety = safety
        self.audit = audit

    # ─── Lifecycle ────────────────────────────────────────────────────────

    @classmethod
    @asynccontextmanager
    async def start(cls, config: TelegramAdsConfig) -> AsyncIterator[Self]:
        adapter = await cls.launch(config)
        try:
            yield adapter
        finally:
            await adapter.close()

    @classmethod
    async def launch(cls, config: TelegramAdsConfig) -> Self:
        config.ensure_paths()
        browser = await BrowserAutomationTool.launch(config.browser)
        api = TelegramAdsApi(browser)
        safety = TelegramAdsSafety(config.safety)
        audit = AuditLogger(config.audit)
        return cls(config, browser, api, safety, audit)

    async def close(self) -> None:
        await self.browser.close()

    # ─── Health / recovery ────────────────────────────────────────────────

    def browser_healthy(self) -> bool:
        """Best-effort liveness check for the underlying browser. Never raises."""
        try:
            return self.browser.is_healthy()
        except Exception:
            return False

    async def recover_browser(self) -> dict[str, Any]:
        """Recover a broken browser page/context in place.

        Closes the broken page/context and recreates a fresh one from the same
        persistent profile, then drops cached API bootstrap state so the next
        operation re-bootstraps against /account.

        Read-only: performs NO ads.telegram.org mutations and navigates nowhere
        (the fresh page is blank until the next real operation). Returns the
        browser-level diagnostic dict, augmented with ``browser_state``.
        """
        result = await self.browser.recover()
        # Fresh page → cached csrf/owner_id are stale. Clear so the next
        # navigation re-bootstraps lazily. Never bootstrap here (blank page).
        with suppress(Exception):  # reset is best-effort
            self.api.reset()
        result["browser_state"] = "healthy" if result.get("recovered") else "broken"
        self.audit.log(
            "recover_browser",
            risk_level=RiskLevel.SAFE_READ.value,
            result_status="ok" if result.get("recovered") else "failed",
            extra={"recovered": bool(result.get("recovered")), "steps": result.get("steps", [])},
        )
        return result

    # ─── Auth ─────────────────────────────────────────────────────────────

    async def open_dashboard(self) -> str:
        """Navigate to /account. Returns current URL after navigation."""
        await self.browser.open_url(BASE_URL + URL_ACCOUNT)
        self.audit.log("open_dashboard", risk_level=RiskLevel.SAFE_READ.value)
        return self.browser.current_url

    async def ensure_logged_in(self) -> bool:
        """Check if logged in. Returns True if logged in.

        Raises LoginRequiredError if not. Hermes must NOT auto-enter login code —
        login is manual via Telegram app confirmation.
        """
        if not self.browser.current_url.startswith(BASE_URL):
            await self.browser.open_url(BASE_URL + URL_ACCOUNT)

        # Authed pages have header dropdown with .pr-header-account-name
        if await self.browser.is_visible("span.pr-header-account-name"):
            self.audit.log("ensure_logged_in", result_status="ok")
            return True

        # Otherwise we're on landing/login page
        self.audit.log("ensure_logged_in", result_status="not_logged_in")
        raise LoginRequiredError(
            "Not logged in to ads.telegram.org. "
            "Manual login required via Telegram app confirmation. "
            f"Open browser profile at {self.config.browser.profile_dir} and complete login."
        )

    async def ensure_owner_bootstrap(self, operation: str = "") -> None:
        """Navigate to /account and bootstrap if owner_id is not yet set.

        Must be called BEFORE safety.gate() so that confirmation tokens are
        NOT consumed when a local preflight failure prevents the mutation.

        Raises:
            LoginRequiredError: forwarded from ensure_logged_in().
            OwnerBootstrapFailedError: bootstrap ran but owner_id is still None
                — mutation must NOT proceed and confirmation is not consumed.
        """
        if not self.api.has_owner_id:
            await self.browser.open_url(BASE_URL + URL_ACCOUNT)
            await self.api.bootstrap()
        if not self.api.has_owner_id:
            raise OwnerBootstrapFailedError(operation)

    # ─── Login state machine (deterministic; read-only detect + sensitive acts) ──

    # Stable DOM hooks for login-funnel detection (mirrors selectors.yaml
    # `auth_flow`). `logged_in` is verified; phone/code/app markers are matched
    # defensively and the classifier defaults conservatively when uncertain.
    _LOGGED_IN_MARKER = "span.pr-header-account-name"
    _PHONE_INPUTS = (
        "input[placeholder='+12223334455']",
        "input[name='phone_number'][type='tel']",
        "input[type='tel']",
    )
    _CODE_INPUTS = (
        "input[name='code']",
        "input[autocomplete='one-time-code']",
        "input[inputmode='numeric'][maxlength='5']",
    )
    _APP_APPROVAL_MARKERS = (
        "text=/confirm.*telegram app/i",
        "text=/open telegram on your phone/i",
        "text=/confirm access via telegram/i",
        "text=/we've just sent you a message/i",
        "text=/please confirm access/i",
        ".login-app-confirm",
    )
    _LANDING_LOGIN_BUTTON = "a.pr-btn.login-link"

    async def _first_visible(self, selectors: tuple[str, ...]) -> str | None:
        for sel in selectors:
            if await self.browser.is_visible(sel):
                return sel
        return None

    async def _first_visible_any_frame(self, selectors: tuple[str, ...]) -> str | None:
        for sel in selectors:
            if await self.browser.is_visible_any_frame(sel):
                return sel
        return None

    async def _any_visible(self, selectors: tuple[str, ...]) -> bool:
        return await self._first_visible(selectors) is not None

    async def _probe_login_signals(self) -> DomSignals:
        """Probe the live page for login-funnel signals. Read-only, never raises."""
        url = ""
        with suppress(Exception):
            url = self.browser.current_url or ""
        logged_in = await self.browser.is_visible(self._LOGGED_IN_MARKER)
        # If clearly authed, skip the rest — the header is the source of truth.
        if logged_in:
            return DomSignals(logged_in=True)
        return DomSignals(
            logged_in=False,
            phone_input=await self._first_visible_any_frame(self._PHONE_INPUTS) is not None,
            code_input=await self._first_visible_any_frame(self._CODE_INPUTS) is not None,
            app_approval=await self._first_visible_any_frame(self._APP_APPROVAL_MARKERS) is not None,
            on_auth_url=(URL_AUTH in url) or url.rstrip("/").endswith(BASE_URL),
        )

    async def _session_active(self) -> bool | None:
        try:
            return await self.browser.has_session_cookie()
        except Exception:
            return None

    def _state_payload(
        self, state: LoginState, *, phone_masked: str | None = None
    ) -> dict[str, Any]:
        """Build the agent-safe structured state dict (no secrets)."""
        url = None
        with suppress(Exception):
            url = self.browser.current_url
        return {
            "state": state.value,
            "logged_in": logged_in_for(state),
            "current_url": url,
            "profile_dir": str(self.config.browser.profile_dir),
            "browser_state": "healthy" if self.browser_healthy() else "broken",
            "requires_human_login": requires_human_login(state),
            "recovery_hint": recovery_hint_for(state),
            "instructions": instructions_for(state),
            "phone_masked": phone_masked,
        }

    async def detect_login_state(self, *, navigate: bool = True) -> dict[str, Any]:
        """Classify the current login/session state. Read-only — never mutates,
        never submits a phone or code, never raises on a broken browser.

        When ``navigate`` is True and we are off-site, opens /account first so the
        redirect to /auth (logged-out) or the cabinet (logged-in) is observable.
        """
        if not self.browser_healthy():
            return self._state_payload(LoginState.BROWSER_BROKEN)
        if navigate and not self.browser.current_url.startswith(BASE_URL):
            await self.browser.open_url(BASE_URL + URL_ACCOUNT)
        signals = await self._probe_login_signals()
        state = classify_login_dom(signals)
        payload = self._state_payload(state)
        payload["session_active"] = await self._session_active()
        self.audit.log("detect_login_state", result_status=state.value)
        return payload

    def issue_login_start_confirmation(self) -> Confirmation:
        """Issue the approval token for an upcoming login_start (sensitive)."""
        return self.safety.issue_confirmation("login_start", {})

    def issue_login_submit_phone_confirmation(self, phone: str) -> Confirmation:
        """Issue the approval token for an upcoming phone submission (sensitive).

        The fingerprint binds the exact phone, so the SAME number must be
        re-supplied at apply time — a different one invalidates the token.
        """
        return self.safety.issue_confirmation("login_submit_phone", {"phone": phone})

    def issue_login_submit_code_confirmation(self) -> Confirmation:
        """Issue the approval token for entering the cabinet OTP (sensitive)."""
        return self.safety.issue_confirmation("login_submit_code", {})

    async def login_submit_code(self, code: str, *, confirmation_id: str) -> dict[str, Any]:
        """Fill the ads.telegram.org OTP field and persist the Chromium session.

        [GOAL] Operator-confirmed code → logged-in cabinet profile on disk.
        The code is never audited or returned.
        """
        self.safety.gate("login_submit_code", {}, confirmation_id=confirmation_id)
        if not self.browser_healthy():
            return self._state_payload(LoginState.BROWSER_BROKEN)
        target = await self._first_visible(self._CODE_INPUTS)
        if target is None:
            payload = await self.detect_login_state(navigate=False)
            self.audit.log("login_submit_code", result_status="no_code_field")
            return payload
        await self.browser.fill(target, code.strip())
        with suppress(Exception):
            await self.browser.click("button:has-text('Next')")
        with suppress(Exception):
            await self.browser.click("button:has-text('Submit')")
        payload = await self.detect_login_state(navigate=False)
        self.audit.log("login_submit_code", result_status=payload["state"])
        return payload

    async def login_start(self, *, confirmation_id: str) -> dict[str, Any]:
        """Open the auth page and surface the login state. SENSITIVE: opening the
        Telegram login widget can trigger an app-approval prompt, so it is gated
        by an explicit human approval. Never submits a phone or a code.
        """
        self.safety.gate("login_start", {}, confirmation_id=confirmation_id)
        if not self.browser_healthy():
            return self._state_payload(LoginState.BROWSER_BROKEN)
        # Already authed? Report and do nothing else.
        if await self.browser.is_visible(self._LOGGED_IN_MARKER):
            self.audit.log("login_start", result_status="already_logged_in")
            return await self.detect_login_state(navigate=False)
        # Land on the auth page; click the public "Log In" entry if present.
        await self.browser.open_url(BASE_URL + URL_ACCOUNT)
        still_logged_out = not await self.browser.is_visible(self._LOGGED_IN_MARKER)
        if still_logged_out and await self.browser.is_visible(self._LANDING_LOGIN_BUTTON):
            with suppress(Exception):
                await self.browser.click(self._LANDING_LOGIN_BUTTON)
        payload = await self.detect_login_state(navigate=False)
        self.audit.log("login_start", result_status=payload["state"])
        return payload

    async def login_submit_phone(self, phone: str, *, confirmation_id: str) -> dict[str, Any]:
        """Submit a phone number on the login form. SENSITIVE + approval-gated.

        The phone is used only to fill the form; it is never logged, audited, or
        persisted in the clear — only a mask is recorded/returned. Does not enter
        an OTP code (that stays a manual, human-only step).
        """
        self.safety.gate("login_submit_phone", {"phone": phone}, confirmation_id=confirmation_id)
        masked = mask_phone(phone)
        if not self.browser_healthy():
            return self._state_payload(LoginState.BROWSER_BROKEN, phone_masked=masked)
        # Find the first visible phone field; if none, there is nothing to submit.
        target = await self._first_visible(self._PHONE_INPUTS)
        if target is None:
            payload = await self.detect_login_state(navigate=False)
            payload["phone_masked"] = masked
            self.audit.log("login_submit_phone", result_status="no_phone_field")
            return payload
        await self.browser.fill(target, phone)
        with suppress(Exception):
            await self.browser.click("button:has-text('Next')")
        payload = await self.detect_login_state(navigate=False)
        payload["phone_masked"] = masked
        # Audit records ONLY the mask — never the real number.
        self.audit.log(
            "login_submit_phone", result_status=payload["state"], extra={"phone_masked": masked}
        )
        return payload

    _LOGIN_CLICK_TARGETS = (
        "a.pr-btn.login-link",
        "text=Log In",
        "button:has-text('Log In')",
        "a:has-text('Log In')",
    )
    _NEXT_CLICK_TARGETS = (
        "button:has-text('Next')",
        "button:has-text('Continue')",
        "button[type='submit']",
    )

    async def login_authorize_from_env(self, phone: str) -> dict[str, Any]:
        """
        [GOAL] Type TELEGRAM_ADS_PHONE into ads.telegram.org and reach app-approval.
        [INPUT] Operator phone from host env (never from the model).
        [OUTPUT] Structured LoginState; phone only as a mask.

        Intent:
        - operator-preauthorized via env; no confirmation bounce
        - do not enter OTP; wait for Telegram-app Accept

        Constraints:
        - never audit/return the raw number
        - never start a second Chromium on this profile
        - Chrome autofill must not overwrite the typed phone
        """
        self.safety.gate("login_authorize_from_env", {"phone_masked": mask_phone(phone)})
        masked = mask_phone(phone)
        if not self.browser_healthy():
            return self._state_payload(LoginState.BROWSER_BROKEN, phone_masked=masked)

        current = await self.detect_login_state(navigate=True)
        if current.get("state") == LoginState.LOGGED_IN.value:
            current["phone_masked"] = masked
            current["already_logged_in"] = True
            current["phone_submitted"] = False
            self.audit.log("login_authorize_from_env", result_status="already_logged_in")
            return current
        if current.get("state") == LoginState.APP_APPROVAL_PENDING.value:
            current["phone_masked"] = masked
            current["already_logged_in"] = False
            current["phone_submitted"] = True
            current["operator_message"] = (
                f"Phone {masked} is already submitted. Tap Accept in Telegram."
            )
            self.audit.log("login_authorize_from_env", result_status="already_pending")
            return current

        await self.browser.open_url(BASE_URL + URL_ACCOUNT)
        if not await self.browser.is_visible(self._LOGGED_IN_MARKER):
            for sel in self._LOGIN_CLICK_TARGETS:
                if await self.browser.is_visible_any_frame(sel):
                    with suppress(Exception):
                        await self.browser.click_any_frame(sel)
                    break

        target = None
        for _ in range(20):
            target = await self._first_visible_any_frame(self._PHONE_INPUTS)
            if target:
                break
            await self.browser.sleep(0.4)
        if target is None:
            payload = await self.detect_login_state(navigate=False)
            payload["phone_masked"] = masked
            payload["phone_submitted"] = False
            payload["diagnostic"] = "phone_field_not_found"
            self.audit.log("login_authorize_from_env", result_status="no_phone_field")
            return payload

        actual = await self.browser.fill_tel_defensive_any_frame(target, phone)
        digits_ok = "".join(c for c in (actual or "") if c.isdigit()) == "".join(
            c for c in phone if c.isdigit()
        )
        if not digits_ok:
            payload = await self.detect_login_state(navigate=False)
            payload["phone_masked"] = masked
            payload["phone_submitted"] = False
            payload["diagnostic"] = "phone_autofill_mismatch"
            self.audit.log("login_authorize_from_env", result_status="autofill_mismatch")
            return payload

        for sel in self._NEXT_CLICK_TARGETS:
            if await self.browser.is_visible_any_frame(sel):
                with suppress(Exception):
                    await self.browser.click_any_frame(sel)
                break
        await self.browser.sleep(1.2)
        payload = await self.detect_login_state(navigate=False)
        payload["phone_masked"] = masked
        payload["already_logged_in"] = payload.get("state") == LoginState.LOGGED_IN.value
        payload["phone_submitted"] = True
        payload["operator_message"] = (
            f"Entered {masked} on ads.telegram.org. Tap Accept in Telegram. "
            "I will wait and persist the Chromium session when you accept."
        )
        self.audit.log(
            "login_authorize_from_env",
            result_status=payload["state"],
            extra={"phone_masked": masked},
        )
        return payload

    async def login_wait(
        self, *, timeout_sec: float = 120.0, poll_interval_sec: float = 3.0
    ) -> dict[str, Any]:
        """Poll until logged in or timeout. Read-only — submits no phone/code.

        Do NOT reload or re-navigate while waiting: a reload cancels the
        in-flight Telegram-app confirmation and resets the phone widget.
        """
        import time as _time

        timeout_sec = max(1.0, min(float(timeout_sec), 600.0))
        poll_interval_sec = max(0.5, min(float(poll_interval_sec), 30.0))
        deadline = _time.monotonic() + timeout_sec
        last = await self.detect_login_state(navigate=False)
        while last["state"] != LoginState.LOGGED_IN.value and _time.monotonic() < deadline:
            await self.browser.sleep(poll_interval_sec)
            if await self.browser.is_visible(self._LOGGED_IN_MARKER):
                last = await self.detect_login_state(navigate=False)
                break
            url = ""
            with suppress(Exception):
                url = self.browser.current_url or ""
            if url.startswith(BASE_URL) and URL_AUTH not in url:
                last = await self.detect_login_state(navigate=False)
                if last["state"] == LoginState.LOGGED_IN.value:
                    break
            last = await self.detect_login_state(navigate=False)
        if last["state"] != LoginState.LOGGED_IN.value:
            payload = self._state_payload(LoginState.LOGIN_TIMEOUT)
            payload["session_active"] = await self._session_active()
            self.audit.log("login_wait", result_status="timeout")
            return payload
        self.audit.log("login_wait", result_status="logged_in")
        return last

    async def profile_lock_status(self) -> dict[str, Any]:
        """Best-effort Chromium profile-lock check (read-only filesystem probe)."""
        from hermes_telegram_ads.browser import check_profile_lock

        try:
            return await check_profile_lock(self.config.browser.profile_dir)
        except Exception:
            return {"locked": False}

    # ─── Accounts ─────────────────────────────────────────────────────────

    async def list_accounts(self) -> list[Account]:
        await self.ensure_logged_in()
        accounts = await account_page.list_accounts(self.browser)
        self.audit.log("list_accounts", extra={"count": len(accounts)})
        return accounts

    async def get_current_account(self) -> Account | None:
        await self.ensure_logged_in()
        return await account_page.get_current_account(self.browser)

    async def choose_account(self, account_token: str) -> Account | None:
        """Switch to cabinet by token. Re-bootstraps API automatically."""
        self.safety.gate("choose_account", {"account_token": account_token})
        url = BASE_URL + URL_CHOOSE_ACCOUNT_TOKEN.format(token=account_token) + "?to=account"
        await self.browser.open_url(url)
        # After redirect we land on /account. Re-bootstrap API (owner_id changed).
        # Best-effort: a cabinet with no ads or a TON cabinet may not expose
        # owner_id immediately, which raises TelegramAdsApiError. The API token is
        # only needed for *mutations* (re-bootstrapped lazily there); DOM reads
        # (budget, ads) work regardless. So a bootstrap miss must NOT abort the
        # switch or a read-only snapshot. A genuine logout (LoginRequiredError)
        # still propagates.
        try:
            await self.api.rebootstrap()
        except TelegramAdsApiError:
            self.audit.log("choose_account", account_token=account_token, result_status="bootstrap_skipped")
        self.audit.log("choose_account", account_token=account_token)
        return await self.get_current_account()

    # ─── Ads (read) ───────────────────────────────────────────────────────

    async def list_ads(self) -> list[AdSummary]:
        await self.ensure_logged_in()
        if not self.browser.current_url.endswith("/account"):
            await self.browser.open_url(BASE_URL + URL_ACCOUNT)
        ads = await account_page.list_ads(
            self.browser, limit=self.config.limits.max_ads_to_parse
        )
        self.audit.log("list_ads", extra={"count": len(ads)})
        return ads

    async def parse_ads(self) -> dict:
        """Rich parse result with data_quality, column_map, warnings.

        Returns dict with keys: ads, data_quality, column_map, missing_headers,
        expected_columns, warnings, budget_column_label.
        Safe — read-only.
        """
        await self.ensure_logged_in()
        if not self.browser.current_url.endswith("/account"):
            await self.browser.open_url(BASE_URL + URL_ACCOUNT)
        result = await account_page.parse_ads_table(
            self.browser, limit=self.config.limits.max_ads_to_parse
        )
        self.audit.log(
            "parse_ads",
            extra={
                "count": len(result["ads"]),
                "data_quality": result["data_quality"],
                "missing_headers": result["missing_headers"],
            },
        )
        return result

    async def get_ad(self, ad_id: int) -> AdDetail:
        await self.ensure_logged_in()
        await self.browser.open_url(BASE_URL + URL_AD.format(id=ad_id))
        detail = await ad_detail_page.get_ad_detail(self.browser, ad_id)
        self.audit.log("get_ad", extra={"ad_id": ad_id, "status": detail.ad.status})
        return detail

    async def get_ad_stats(self, ad_id: int) -> AdStats:
        await self.ensure_logged_in()
        await self.browser.open_url(BASE_URL + URL_AD_STATS.format(id=ad_id))
        stats = await ad_stats_page.get_ad_stats(self.browser, ad_id)
        self.audit.log("get_ad_stats", extra={"ad_id": ad_id, "views": stats.views})
        return stats

    async def get_account_budget(self) -> AccountBudget:
        await self.ensure_logged_in()
        await self.browser.open_url(BASE_URL + URL_ACCOUNT_BUDGET)
        budget = await budget_page.get_account_budget(self.browser)
        self.audit.log(
            "get_account_budget",
            extra={"balance": budget.balance, "currency": budget.currency},
        )
        return budget

    async def get_account_stats(self) -> dict[str, Any]:
        """Open account-level stats and return summary."""
        await self.ensure_logged_in()
        await self.browser.open_url(BASE_URL + URL_ACCOUNT_STATS)
        # Lightweight: just confirm page loaded; full chart-data parsing is CSV-only.
        return {"url": self.browser.current_url}

    # ─── Profile ──────────────────────────────────────────────────────────

    async def get_profile(self) -> profile_page.ProfileInfo:
        await self.ensure_logged_in()
        await self.browser.open_url(BASE_URL + "/account/edit")
        return await profile_page.get_profile_info(self.browser)

    # ─── Drafts ───────────────────────────────────────────────────────────

    async def validate_ad(self, draft: CreateAdDraft) -> dict[str, Any]:
        """Live validation via checkAdPost. Returns {field, error, preview_data}.

        Safe action — does not submit anything.
        """
        await self.ensure_logged_in()
        if not self.browser.current_url.endswith("/account/ad/new"):
            await self.browser.open_url(BASE_URL + URL_AD_NEW)
            await self.api.bootstrap()
        # Run basic local policy checks first (cheap)
        self.safety.raise_if_policy_violations(draft.text, draft.promote_url)
        # Upload media (if any) so checkAdPost validates/previews the media-bearing
        # ad; raises on unsupported/failed upload (no silent media-less fallback).
        media_token, media_type = await self._resolve_media(draft)
        payload = await self._build_check_payload(draft, media_token=media_token)
        result = await self.api.check_ad_post(payload)
        self.audit.log(
            "validate_ad",
            extra={
                "valid": not result.get("error"),
                "field": result.get("field"),
                "error": result.get("error"),
                "media_uploaded": media_token is not None,
                "media_type": media_type,
            },
        )
        return result

    async def save_ad_draft(self, draft: CreateAdDraft) -> dict[str, Any]:
        """Save draft on server (Clear Draft will appear on next /account/ad/new visit)."""
        self.safety.gate("saveAdDraft", {"title": draft.title})
        await self.ensure_logged_in()
        if not self.browser.current_url.endswith("/account/ad/new"):
            await self.browser.open_url(BASE_URL + URL_AD_NEW)
            await self.api.bootstrap()
        payload = await build_create_ad_payload(self.api, draft)
        result = await self.api.save_ad_draft(payload)
        self.audit.log("saveAdDraft", risk_level=RiskLevel.DRAFT.value)
        return result

    async def prepare_ad_draft(
        self,
        draft: CreateAdDraft,
        *,
        screenshot_name: str | None = None,
    ) -> dict[str, Any]:
        """Save draft + take preview screenshot. Does NOT submit.

        Returns:
            {
                "draft_saved": bool,
                "preview_screenshot": str (path),
                "validation": <checkAdPost result>,
                "requires_confirmation_for_submit": True,
            }
        """
        validation = await self.validate_ad(draft)
        save_result = await self.save_ad_draft(draft)
        screenshot_path = None
        if self.config.storage.screenshots_path:
            name = screenshot_name or f"draft_preview_{draft.title[:20]}.png"
            screenshot_path = await self.browser.screenshot(
                self.config.storage.screenshots_path / name,
                full_page=True,
            )
        return {
            "draft_saved": bool(save_result.get("ok")),
            "preview_screenshot": str(screenshot_path) if screenshot_path else None,
            "validation": validation,
            "requires_confirmation_for_submit": True,
        }

    # ─── Media resolution (shared by create / validate / preview) ──────────

    async def _resolve_media(self, draft: CreateAdDraft) -> tuple[str | None, str | None]:
        """Upload ``draft.media_path`` (if any) and return ``(media_token, media_type)``.

        - No media_path -> ``(None, None)``.
        - Recognised photo -> uploaded; returns its opaque token + ``"photo"``.
        - Recognised video -> raises :class:`MediaUnsupportedError` while
          ``VIDEO_UPLOAD_SUPPORTED`` is False (never silently dropped).
        - Unrecognised type / upload failure -> raises (MediaValidationError /
          MediaUploadError); the caller MUST NOT proceed to create a media-less ad.
        """
        if not draft.media_path:
            return None, None
        assert_uploaded_media_supported_for_target_type(
            target_type=draft.target_type,
            media_path=draft.media_path,
        )
        media_type = media_type_for_path(draft.media_path)
        if media_type == "video" and not VIDEO_UPLOAD_SUPPORTED:
            raise MediaUnsupportedError(
                "video_upload",
                message=(
                    "Video creative upload is not yet supported by this tool "
                    "(unverified /file/upload path). Use a 16:9 photo, or set the "
                    "media after manual verification."
                ),
                context={"media_path": draft.media_path},
            )
        token = await self.upload_media(draft.media_path)  # raises on failure
        return token, media_type

    # ─── Create ad (CONFIRM_REQUIRED) ─────────────────────────────────────

    async def create_ad(
        self,
        draft: CreateAdDraft,
        *,
        confirmation_id: str,
    ) -> dict[str, Any]:
        """Submit new ad to moderation.

        Hard policy check before submit (raises PolicyViolationError if fails).
        Requires a valid `confirmation_id` matching the create fingerprint
        (see :meth:`_confirmation_params_create`).

        If ``draft.media_path`` is set the media is uploaded first and its token is
        included in the payload; a failed/unsupported upload raises and NO ad is
        created (never a silent media-less submit).
        """
        await self.ensure_logged_in()
        # Hard local policy check
        self.safety.raise_if_policy_violations(draft.text, draft.promote_url)

        params = self._confirmation_params_create(draft)
        self.safety.gate(METHOD_CREATE_AD, params, confirmation_id=confirmation_id)

        if not self.browser.current_url.endswith("/account/ad/new"):
            await self.browser.open_url(BASE_URL + URL_AD_NEW)
            await self.api.bootstrap()
        # Upload media BEFORE create; raises (no ad created) on failure/unsupported.
        media_token, media_type = await self._resolve_media(draft)
        payload = await build_create_ad_payload(self.api, draft, media_token=media_token)
        result = await self.api.create_ad(payload)
        self.audit.log(
            METHOD_CREATE_AD,
            risk_level=RiskLevel.CONFIRM_REQUIRED.value,
            confirmation_id=confirmation_id,
            extra={
                "title": draft.title,
                "cpm": draft.cpm,
                "budget": draft.budget,
                "show_picture": draft.show_picture,
                "media_uploaded": media_token is not None,
                "media_type": media_type,
            },
        )
        return result

    def issue_create_ad_confirmation(self, draft: CreateAdDraft, *, note: str = "") -> Confirmation:
        """Helper for skill: issue confirmation matching draft fingerprint."""
        return self.safety.issue_confirmation(
            METHOD_CREATE_AD,
            self._confirmation_params_create(draft),
            note=note,
        )

    def _confirmation_params_create(self, draft: CreateAdDraft) -> dict[str, Any]:
        """Stable fingerprint params for create-ad confirmation (shared helper)."""
        return create_confirmation_params(draft)

    # ─── Edit (CONFIRM_REQUIRED) ──────────────────────────────────────────

    async def edit_ad(
        self,
        draft: EditAdDraft,
        *,
        confirmation_id: str,
    ) -> dict[str, Any]:
        """Save Changes on /account/ad/<id>. Triggers re-review."""
        await self.ensure_logged_in()
        params = self._confirmation_params_edit(draft)
        self.safety.gate(METHOD_EDIT_AD, params, confirmation_id=confirmation_id)
        await self.browser.open_url(BASE_URL + URL_AD.format(id=draft.ad_id))
        await self.api.bootstrap()
        payload = build_edit_ad_payload(draft)
        result = await self.api.edit_ad(payload)
        self.audit.log(
            METHOD_EDIT_AD,
            risk_level=RiskLevel.CONFIRM_REQUIRED.value,
            confirmation_id=confirmation_id,
            extra={"ad_id": draft.ad_id},
        )
        return result

    def issue_edit_ad_confirmation(self, draft: EditAdDraft) -> Confirmation:
        return self.safety.issue_confirmation(
            METHOD_EDIT_AD, self._confirmation_params_edit(draft)
        )

    def _confirmation_params_edit(self, draft: EditAdDraft) -> dict[str, Any]:
        return {
            "ad_id": draft.ad_id,
            "title": draft.title,
            "text": draft.text,
            "promote_url": draft.promote_url,
            "cpm": draft.cpm,
            "active": draft.active,
        }

    async def change_cpm(
        self,
        ad_id: int,
        new_cpm: float,
        *,
        confirmation_id: str,
    ) -> AdMutationResponse:
        await self.ensure_logged_in()
        await self.ensure_owner_bootstrap("change_cpm")
        params = {"ad_id": ad_id, "cpm": new_cpm}
        self.safety.gate(METHOD_EDIT_AD_CPM, params, confirmation_id=confirmation_id)
        result = await self.api.edit_ad_cpm(ad_id, new_cpm)
        self.audit.log(
            METHOD_EDIT_AD_CPM,
            risk_level=RiskLevel.CONFIRM_REQUIRED.value,
            confirmation_id=confirmation_id,
            extra={"ad_id": ad_id, "cpm": new_cpm},
        )
        return AdMutationResponse.model_validate(result)

    def issue_change_cpm_confirmation(self, ad_id: int, new_cpm: float) -> Confirmation:
        return self.safety.issue_confirmation(METHOD_EDIT_AD_CPM, {"ad_id": ad_id, "cpm": new_cpm})

    async def change_status(
        self,
        ad_id: int,
        *,
        active: bool,
        confirmation_id: str,
    ) -> AdMutationResponse:
        await self.ensure_logged_in()
        await self.ensure_owner_bootstrap("change_status")
        params = {"ad_id": ad_id, "active": active}
        self.safety.gate(METHOD_EDIT_AD_STATUS, params, confirmation_id=confirmation_id)
        result = await self.api.edit_ad_status(ad_id, active=active)
        self.audit.log(
            METHOD_EDIT_AD_STATUS,
            risk_level=RiskLevel.CONFIRM_REQUIRED.value,
            confirmation_id=confirmation_id,
            extra={"ad_id": ad_id, "active": active},
        )
        return AdMutationResponse.model_validate(result)

    def issue_change_status_confirmation(self, ad_id: int, *, active: bool) -> Confirmation:
        return self.safety.issue_confirmation(
            METHOD_EDIT_AD_STATUS, {"ad_id": ad_id, "active": active}
        )

    async def add_to_budget(
        self,
        ad_id: int,
        amount: float,
        *,
        confirmation_id: str,
    ) -> AdMutationResponse:
        """Increase ad budget. TON cabinet rule: budget after add ≥ 1.00 TON."""
        await self.ensure_logged_in()
        await self.ensure_owner_bootstrap("add_to_budget")
        params = {"ad_id": ad_id, "amount": amount}
        self.safety.gate(METHOD_INCR_AD_BUDGET, params, confirmation_id=confirmation_id)
        result = await self.api.incr_ad_budget(ad_id, amount)
        self.audit.log(
            METHOD_INCR_AD_BUDGET,
            risk_level=RiskLevel.CONFIRM_REQUIRED.value,
            confirmation_id=confirmation_id,
            extra={"ad_id": ad_id, "amount": amount},
        )
        return AdMutationResponse.model_validate(result)

    def issue_add_budget_confirmation(self, ad_id: int, amount: float) -> Confirmation:
        return self.safety.issue_confirmation(
            METHOD_INCR_AD_BUDGET, {"ad_id": ad_id, "amount": amount}
        )

    async def withdraw_from_budget(
        self,
        ad_id: int,
        amount: float,
        *,
        confirmation_id: str,
    ) -> AdMutationResponse:
        """Withdraw from ad budget. 2-min cooldown after status changes."""
        await self.ensure_logged_in()
        await self.ensure_owner_bootstrap("withdraw_from_budget")
        params = {"ad_id": ad_id, "amount": amount}
        self.safety.gate(METHOD_DECR_AD_BUDGET, params, confirmation_id=confirmation_id)
        result = await self.api.decr_ad_budget(ad_id, amount)
        self.audit.log(
            METHOD_DECR_AD_BUDGET,
            risk_level=RiskLevel.CONFIRM_REQUIRED.value,
            confirmation_id=confirmation_id,
            extra={"ad_id": ad_id, "amount": amount},
        )
        return AdMutationResponse.model_validate(result)

    def issue_withdraw_budget_confirmation(self, ad_id: int, amount: float) -> Confirmation:
        return self.safety.issue_confirmation(
            METHOD_DECR_AD_BUDGET, {"ad_id": ad_id, "amount": amount}
        )

    # ─── Delete (DOUBLE_CONFIRM_REQUIRED) ─────────────────────────────────

    async def delete_ad(
        self,
        ad_id: int,
        *,
        confirmation_id: str,
        second_confirmation_id: str,
    ) -> dict[str, Any]:
        await self.ensure_logged_in()
        await self.ensure_owner_bootstrap("delete_ad")
        params = {"ad_id": ad_id}
        self.safety.gate(
            METHOD_DELETE_AD,
            params,
            confirmation_id=confirmation_id,
            second_confirmation_id=second_confirmation_id,
        )
        result = await self.api.delete_ad_full(ad_id)
        self.audit.log(
            METHOD_DELETE_AD,
            risk_level=RiskLevel.DOUBLE_CONFIRM_REQUIRED.value,
            confirmation_id=confirmation_id,
            extra={"ad_id": ad_id, "second_id": second_confirmation_id},
        )
        return result

    def issue_delete_ad_confirmations(self, ad_id: int) -> tuple[Confirmation, Confirmation]:
        """Issue two independent confirmations for double-confirm delete."""
        return (
            self.safety.issue_confirmation(METHOD_DELETE_AD, {"ad_id": ad_id}, note="first"),
            self.safety.issue_confirmation(METHOD_DELETE_AD, {"ad_id": ad_id}, note="second"),
        )

    # ─── Create similar / clone ───────────────────────────────────────────

    async def create_similar_draft(self, source_ad_id: int) -> dict[str, Any]:
        """Clone ad → draft on /account/ad/new. Safe (no submit)."""
        await self.ensure_logged_in()
        await self.ensure_owner_bootstrap("create_similar_draft")
        self.safety.gate("createDraftFromAd", {"ad_id": source_ad_id})
        result = await self.api.create_draft_from_ad(source_ad_id)
        self.audit.log("createDraftFromAd", risk_level=RiskLevel.DRAFT.value)
        return result

    # ─── Media ────────────────────────────────────────────────────────────

    async def upload_media(self, file_path: str | Path) -> str:
        """Upload local media file, return opaque token to use in form's media field.

        Performs local 16:9 aspect-ratio pre-check (where possible).
        """
        await self.ensure_logged_in()
        await self.ensure_owner_bootstrap("upload_media")
        self.safety.gate("upload_media", {"file": str(file_path)})
        token = await upload_media_helper(self.api, file_path)
        self.audit.log(
            "upload_media",
            risk_level=RiskLevel.DRAFT.value,
            extra={"file": str(file_path), "token_len": len(token)},
        )
        return token

    # ─── Events / Pixel (Stars only) ──────────────────────────────────────

    async def list_events(self) -> list[Event]:
        await self.ensure_logged_in()
        await self.browser.open_url(BASE_URL + URL_EVENTS)
        events = await events_page.list_events(self.browser)
        self.audit.log("list_events", extra={"count": len(events)})
        return events

    async def get_pixel_base_snippet(self) -> PixelSnippet | None:
        await self.ensure_logged_in()
        await self.browser.open_url(BASE_URL + URL_EVENTS_SETUP)
        await self.browser.sleep(0.5)
        return await events_page.get_pixel_base_snippet(self.browser)

    async def get_event_setup_snippet(self, event_id: str) -> PixelSnippet | None:
        await self.ensure_logged_in()
        await self.browser.open_url(BASE_URL + URL_EVENT_SETUP.format(id=event_id))
        await self.browser.sleep(0.5)
        return await events_page.get_event_setup_snippet(self.browser)

    async def get_event_log(self, event_id: str) -> EventLog:
        await self.ensure_logged_in()
        await self.browser.open_url(BASE_URL + URL_EVENT_LOG.format(id=event_id))
        await self.browser.sleep(0.5)
        return await events_page.get_event_log(self.browser, event_id)

    async def create_event(
        self,
        *,
        title: str,
        event_type: str,
        confirmation_id: str,
    ) -> dict[str, Any]:
        await self.ensure_logged_in()
        await self.ensure_owner_bootstrap("create_event")
        params = {"title": title, "type": event_type}
        self.safety.gate(METHOD_CREATE_EVENT, params, confirmation_id=confirmation_id)
        result = await self.api.create_event(title, event_type)
        self.audit.log(
            METHOD_CREATE_EVENT,
            risk_level=RiskLevel.CONFIRM_REQUIRED.value,
            confirmation_id=confirmation_id,
            extra={"title": title, "type": event_type},
        )
        return result

    def issue_create_event_confirmation(self, title: str, event_type: str) -> Confirmation:
        return self.safety.issue_confirmation(
            METHOD_CREATE_EVENT, {"title": title, "type": event_type}
        )

    async def delete_event(
        self,
        event_id: str,
        *,
        confirmation_id: str,
        second_confirmation_id: str,
    ) -> dict[str, Any]:
        await self.ensure_logged_in()
        await self.ensure_owner_bootstrap("delete_event")
        params = {"event_id": event_id}
        self.safety.gate(
            METHOD_DELETE_EVENT,
            params,
            confirmation_id=confirmation_id,
            second_confirmation_id=second_confirmation_id,
        )
        result = await self.api.delete_event_full(event_id)
        self.audit.log(
            METHOD_DELETE_EVENT,
            risk_level=RiskLevel.DOUBLE_CONFIRM_REQUIRED.value,
            confirmation_id=confirmation_id,
            extra={"event_id": event_id, "second_id": second_confirmation_id},
        )
        return result

    def issue_delete_event_confirmations(
        self,
        event_id: str,
    ) -> tuple[Confirmation, Confirmation]:
        return (
            self.safety.issue_confirmation(
                METHOD_DELETE_EVENT, {"event_id": event_id}, note="first"
            ),
            self.safety.issue_confirmation(
                METHOD_DELETE_EVENT, {"event_id": event_id}, note="second"
            ),
        )

    # ─── Share stats ──────────────────────────────────────────────────────

    async def get_share_stats_url(self, ad_id: int) -> str | None:
        """Open Share Stats popup, read the public URL.

        Safe read — link is pre-generated server-side.
        """
        await self.ensure_logged_in()
        await self.browser.open_url(BASE_URL + URL_AD_STATS_SHARE.format(id=ad_id))
        await self.browser.sleep(0.5)
        url = await self.browser.read_attr("input.js-share-url[readonly]", "value")
        return url

    async def revoke_share_stats_url(
        self,
        ad_id: int,
        *,
        confirmation_id: str,
        second_confirmation_id: str,
    ) -> dict[str, Any]:
        """Rotate share-stats token. Old URL invalidated."""
        await self.ensure_logged_in()
        await self.ensure_owner_bootstrap("revoke_share_stats_url")
        params = {"ad_id": ad_id}
        self.safety.gate(
            METHOD_REVOKE_STATS_URL,
            params,
            confirmation_id=confirmation_id,
            second_confirmation_id=second_confirmation_id,
        )
        result = await self.api.revoke_stats_url(ad_id)
        self.audit.log(
            METHOD_REVOKE_STATS_URL,
            risk_level=RiskLevel.DOUBLE_CONFIRM_REQUIRED.value,
            confirmation_id=confirmation_id,
            extra={"ad_id": ad_id, "second_id": second_confirmation_id},
        )
        return result

    def issue_revoke_stats_url_confirmations(
        self,
        ad_id: int,
    ) -> tuple[Confirmation, Confirmation]:
        return (
            self.safety.issue_confirmation(METHOD_REVOKE_STATS_URL, {"ad_id": ad_id}, note="first"),
            self.safety.issue_confirmation(METHOD_REVOKE_STATS_URL, {"ad_id": ad_id}, note="second"),
        )

    # ─── Reports ──────────────────────────────────────────────────────────

    async def download_ad_report(self, ad_id: int, month: str) -> Path:
        """Download monthly CSV for ad. month format: YYYYMM (e.g. '202605')."""
        await self.ensure_logged_in()
        await self.ensure_owner_bootstrap("download_ad_report")
        assert self.config.storage.reports_path is not None
        dest = await download_ad_monthly_report(
            self.browser,
            account_token=self.api.owner_id,
            ad_id=ad_id,
            month=month,
            dest_dir=self.config.storage.reports_path,
        )
        self.audit.log(
            "download_ad_report",
            extra={"ad_id": ad_id, "month": month, "dest": str(dest)},
        )
        return dest

    async def download_account_report(self, month: str) -> Path:
        await self.ensure_logged_in()
        await self.ensure_owner_bootstrap("download_account_report")
        assert self.config.storage.reports_path is not None
        dest = await download_account_monthly_report(
            self.browser,
            account_token=self.api.owner_id,
            month=month,
            dest_dir=self.config.storage.reports_path,
        )
        self.audit.log("download_account_report", extra={"month": month, "dest": str(dest)})
        return dest

    # ─── Screenshots ──────────────────────────────────────────────────────

    async def screenshot(self, name: str | None = None, *, full_page: bool = False) -> Path:
        assert self.config.storage.screenshots_path is not None
        from datetime import UTC, datetime

        ts = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
        dest = self.config.storage.screenshots_path / (name or f"screen_{ts}.png")
        return await self.browser.screenshot(dest, full_page=full_page)

    # ─── Helpers ──────────────────────────────────────────────────────────

    async def _build_check_payload(
        self, draft: CreateAdDraft, *, media_token: str | None = None
    ) -> dict[str, Any]:
        from hermes_telegram_ads.payloads import build_check_ad_post_payload

        return await build_check_ad_post_payload(self.api, draft, media_token=media_token)

    # ─── Risk introspection ───────────────────────────────────────────────

    @staticmethod
    def get_action_risk(action: str) -> RiskLevel:
        """Public helper for callers to discover required confirmation level."""
        return get_risk_level(action)
