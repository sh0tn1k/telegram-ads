"""Deterministic Telegram Ads login/session state machine.

The agent must never *guess* whether it is logged in, nor poke at Xvfb /
Chromium / DISPLAY, nor fall back to raw Playwright. Instead the adapter probes
a small set of stable DOM signals + the current URL and maps them, through the
pure :func:`classify_login_dom` classifier here, onto one explicit
:class:`LoginState`. Every login/session tool reports that state plus the flat
diagnostic fields the agent needs to decide what to do next.

States (the only ones the agent will ever see)::

    UNKNOWN              nothing probed yet / indeterminate
    LOGGED_IN            authed cabinet visible — proceed
    AUTH_PAGE            on /auth or landing, no phone field yet (Log In / app)
    PHONE_REQUIRED       phone-number input is visible
    APP_APPROVAL_PENDING waiting for the user to approve in the Telegram app
    CODE_REQUIRED        an OTP/code input is visible (manual, human-only)
    LOGIN_TIMEOUT        login_wait polled to timeout without logging in
    PROFILE_LOCKED       Chromium profile owned by another live process
    BROWSER_BROKEN       page/context broken and could not be recovered

No OTP, phone, code, cookie, or session material is ever stored here.
"""

from __future__ import annotations

from enum import Enum
from typing import NamedTuple


class LoginState(str, Enum):
    """The single source of truth for "where are we in login"."""

    UNKNOWN = "unknown"
    LOGGED_IN = "logged_in"
    AUTH_PAGE = "auth_page"
    PHONE_REQUIRED = "phone_required"
    APP_APPROVAL_PENDING = "app_approval_pending"
    CODE_REQUIRED = "code_required"
    LOGIN_TIMEOUT = "timeout"
    PROFILE_LOCKED = "profile_locked"
    BROWSER_BROKEN = "browser_broken"


# States from which a human (not the agent) must act before we can proceed.
_HUMAN_LOGIN_STATES: frozenset[LoginState] = frozenset(
    {
        LoginState.AUTH_PAGE,
        LoginState.PHONE_REQUIRED,
        LoginState.APP_APPROVAL_PENDING,
        LoginState.CODE_REQUIRED,
        LoginState.LOGIN_TIMEOUT,
    }
)


class DomSignals(NamedTuple):
    """Booleans probed from the live page. All default False (nothing seen)."""

    logged_in: bool = False  # authed header (account-name label) visible
    phone_input: bool = False  # phone-number input visible
    code_input: bool = False  # OTP/code input visible
    app_approval: bool = False  # "confirm in your Telegram app" prompt visible
    on_auth_url: bool = False  # current URL is /auth or the public landing


def classify_login_dom(signals: DomSignals) -> LoginState:
    """Map probed DOM signals onto exactly one :class:`LoginState`.

    Pure and deterministic so it can be unit-tested without a browser.
    Precedence follows the real login funnel from furthest-along backwards —
    a logged-in header beats everything; an OTP field (last manual step) beats
    an app-approval prompt, which beats the phone field, which beats a bare
    auth page. Anything indeterminate is :data:`LoginState.UNKNOWN` (never a
    false "logged in").
    """
    if signals.logged_in:
        return LoginState.LOGGED_IN
    if signals.code_input:
        return LoginState.CODE_REQUIRED
    if signals.app_approval:
        return LoginState.APP_APPROVAL_PENDING
    if signals.phone_input:
        return LoginState.PHONE_REQUIRED
    if signals.on_auth_url:
        return LoginState.AUTH_PAGE
    return LoginState.UNKNOWN


def requires_human_login(state: LoginState) -> bool:
    """True when a human must act (log in / approve / enter code) before tools work."""
    return state in _HUMAN_LOGIN_STATES


def logged_in_for(state: LoginState) -> bool | None:
    """Tri-state ``logged_in`` for a state: True / False / None (unknown)."""
    if state is LoginState.LOGGED_IN:
        return True
    if state in (LoginState.UNKNOWN, LoginState.BROWSER_BROKEN, LoginState.PROFILE_LOCKED):
        return None
    return False


# Human-facing instructions per state. Verbatim-safe: no secrets, no codes.
_INSTRUCTIONS: dict[LoginState, list[str]] = {
    LoginState.AUTH_PAGE: [
        "Open ads.telegram.org in the persistent browser profile (human, not the agent).",
        "Click Log In and approve the login in your Telegram app.",
        "Do NOT type any OTP / phone / login code through the agent.",
    ],
    LoginState.PHONE_REQUIRED: [
        "A phone number is required on the Telegram Ads login form.",
        "Call telegram_ads_login_from_env — it types TELEGRAM_ADS_PHONE from the host .env.",
        "Then tell the operator the masked number was entered and they must tap Accept in Telegram.",
        "Prefer Telegram-app approval over an OTP code.",
    ],
    LoginState.APP_APPROVAL_PENDING: [
        "Telegram sent a login confirmation to your Telegram app.",
        "Approve it there; then telegram_ads_login_wait will detect the session.",
        "No code needs to be entered through the agent.",
    ],
    LoginState.CODE_REQUIRED: [
        "Telegram is asking for an OTP code on ads.telegram.org.",
        "Give the code to the agent; it will call telegram_ads_login_submit_code.",
        "The Chromium profile on this host keeps the cabinet session.",
    ],
    LoginState.LOGIN_TIMEOUT: [
        "Login did not complete within the wait window.",
        "Approve the login in your Telegram app, then re-run telegram_ads_login_wait.",
    ],
    LoginState.PROFILE_LOCKED: [
        "The browser profile is locked by another running process.",
        "Close the other browser using this profile, or approve a debug restart — "
        "the agent will not kill processes on its own.",
    ],
    LoginState.BROWSER_BROKEN: [
        "The browser page/context is broken and recovery failed.",
        "Approve an explicit gateway/browser restart; do not retry in a loop.",
    ],
    LoginState.UNKNOWN: [
        "Session state could not be determined.",
        "Run telegram_ads_status, then telegram_ads_login_check, before any operation.",
    ],
}


def instructions_for(state: LoginState) -> list[str]:
    """Verbatim, secret-free guidance the agent surfaces to the human."""
    return list(_INSTRUCTIONS.get(state, []))


# Short, machine-actionable recovery hint per state (mirrors error envelopes).
_RECOVERY_HINTS: dict[LoginState, str] = {
    LoginState.AUTH_PAGE: "human_login_via_telegram_app",
    LoginState.PHONE_REQUIRED: "call_telegram_ads_login_from_env_then_approve_in_app",
    LoginState.APP_APPROVAL_PENDING: "approve_in_telegram_app_then_login_wait",
    LoginState.CODE_REQUIRED: "submit_code_via_telegram_ads_login_submit_code",
    LoginState.LOGIN_TIMEOUT: "approve_in_app_then_retry_login_wait",
    LoginState.PROFILE_LOCKED: "close_other_browser_or_approve_debug_restart",
    LoginState.BROWSER_BROKEN: "request_explicit_gateway_or_browser_restart",
}


def recovery_hint_for(state: LoginState) -> str | None:
    return _RECOVERY_HINTS.get(state)


def mask_phone(phone: str | None) -> str:
    """Mask a phone for display/logs: keep the country prefix + last 2 digits.

    Keeps the country prefix and last 2 digits; never returns the full number.
    An empty or too-short value collapses to a constant mask.
    """
    if not phone:
        return ""
    s = phone.strip()
    digits = [c for c in s if c.isdigit()]
    if len(digits) < 4:
        return "*" * len(s)
    keep_prefix = 2 if s.startswith("+") else 1
    prefix = s[:keep_prefix]
    last2 = "".join(digits[-2:])
    masked_len = max(len(s) - keep_prefix - 2, 2)
    return f"{prefix}{'*' * masked_len}{last2}"
