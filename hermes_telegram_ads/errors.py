"""Structured exceptions for hermes_telegram_ads.

All errors derive from HermesTelegramAdsError so callers can catch broad.
"""

from __future__ import annotations

from typing import Any


class HermesTelegramAdsError(Exception):
    """Base class for all package errors."""

    def __init__(self, message: str, *, context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.context = context or {}


# ─── Auth / session ──────────────────────────────────────────────────────────


class LoginRequiredError(HermesTelegramAdsError):
    """User is not logged in. Hermes MUST surface this — never auto-enter login code."""


class SessionExpiredError(LoginRequiredError):
    """Session cookie expired. Browser profile needs re-login."""


# ─── Internal API ────────────────────────────────────────────────────────────


class TelegramAdsApiError(HermesTelegramAdsError):
    """Generic API error from /api?hash=<csrf>."""

    def __init__(
        self,
        message: str,
        *,
        method: str | None = None,
        field: str | None = None,
        http_status: int | None = None,
        raw_response: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.method = method
        self.field = field
        self.http_status = http_status
        self.raw_response = raw_response or {}


class OwnerBootstrapFailedError(HermesTelegramAdsError):
    """owner_id could not be initialized before a mutation.

    Bootstrap was attempted (navigate to /account + api.bootstrap()) but
    owner_id is still None afterwards. The mutation was NOT attempted and
    confirmation tokens are NOT consumed because this error is raised before
    safety.gate().
    """

    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__(
            f"owner_id is not initialized; bootstrap failed before mutation "
            f"({operation!r}). Retry after account bootstrap completes.",
            context={
                "operation": operation,
                "error_code": "owner_bootstrap_failed",
                "retryable": True,
                "recommended_action": "run_ensure_login_list_accounts_or_choose_account",
            },
        )


class CsrfRotatedError(TelegramAdsApiError):
    """CSRF hash rotated, re-bootstrap required."""


class InvalidMethodError(TelegramAdsApiError):
    """Server returned `Invalid method` — method name wrong (HTTP 400)."""


# ─── Confirmation / safety ───────────────────────────────────────────────────


class ConfirmationRequiredError(HermesTelegramAdsError):
    """Action requires user confirmation. Hermes/skill must obtain one and re-call."""

    def __init__(
        self,
        action: str,
        *,
        risk_level: str,
        params: dict[str, Any] | None = None,
        confirmation_message: str | None = None,
    ) -> None:
        msg = (
            confirmation_message
            or f"Action {action!r} requires user confirmation (risk: {risk_level})"
        )
        super().__init__(
            msg,
            context={"action": action, "risk_level": risk_level, "params": params or {}},
        )
        self.action = action
        self.risk_level = risk_level
        self.params = params or {}
        self.confirmation_message = msg


class InvalidConfirmationError(HermesTelegramAdsError):
    """Provided confirmation_id is expired, unknown, or doesn't match action/params."""


class ForbiddenActionError(HermesTelegramAdsError):
    """Action is forbidden by safety policy regardless of confirmation."""


# ─── Policy ──────────────────────────────────────────────────────────────────


class PolicyViolationError(HermesTelegramAdsError):
    """Ad content violates guidelines (hard rules)."""

    def __init__(self, violations: list[str], *, draft_id: str | None = None) -> None:
        super().__init__(
            f"Policy violations: {'; '.join(violations)}",
            context={"violations": violations, "draft_id": draft_id},
        )
        self.violations = violations
        self.draft_id = draft_id


class GeoTargetingError(HermesTelegramAdsError):
    """Trying to target audience that current cabinet cannot reach.

    Example: TON cabinet targeting RU/UA/IL/PS audience.
    """


# ─── Domain ──────────────────────────────────────────────────────────────────


class AdNotFoundError(HermesTelegramAdsError):
    """Ad with given id not found in current account."""


class ImmutableFieldError(HermesTelegramAdsError):
    """Trying to change immutable field after ad creation.

    Immutable after create:
        - target_type
        - channels / bots / search_queries
        - placement
        - conversion_event (after first save with it)
    """


class AdInWrongStatusError(HermesTelegramAdsError):
    """Action not allowed for ad's current status.

    Examples:
        - editAdStatus on Stopped ad (must incrAdBudget first)
        - decrAdBudget during 2-min cooldown after editAdStatus
    """


# ─── Media ───────────────────────────────────────────────────────────────────


class MediaValidationError(HermesTelegramAdsError):
    """Local media file failed pre-upload validation (aspect ratio, format, size)."""


class MediaUploadError(HermesTelegramAdsError):
    """Server rejected media upload."""


class MediaUnsupportedError(HermesTelegramAdsError):
    """A media kind is recognised but not yet supported (e.g. video upload).

    Carries a ``capability`` so the tool layer can return a structured
    ``not_implemented`` envelope instead of silently dropping the media.
    """

    def __init__(self, capability: str, message: str = "", context: dict | None = None) -> None:
        super().__init__(message or f"{capability} is not supported", context=context)
        self.capability = capability


# ─── Browser ─────────────────────────────────────────────────────────────────


class BrowserError(HermesTelegramAdsError):
    """Generic Playwright/browser-level error."""


class SelectorNotFoundError(BrowserError):
    """Expected DOM selector not present."""


class UnexpectedNavigationError(BrowserError):
    """Page navigated away unexpectedly (e.g., logged out mid-action)."""


# ─── Profile lock ─────────────────────────────────────────────────────────


class BrowserProfileLockedError(BrowserError):
    """Browser profile is locked by another process.

    Chromium write-locks the profile directory (SingletonLock). Only one
    Chrome/Chromium instance can use a profile at a time.

    Hermes must surface this error as a structured JSON response — never
    kill or restart the other process.
    """

    def __init__(
        self,
        profile_path: str,
        *,
        owner_pid: int | None = None,
    ) -> None:
        self.profile_path = profile_path
        self.owner_pid = owner_pid
        pid_str = str(owner_pid) if owner_pid else "unknown"
        super().__init__(
            f"Browser profile is locked by another process (PID: {pid_str}). "
            f"Only one process can use a Chrome profile at a time.",
            context={
                "profile_path": profile_path,
                "owner_pid": owner_pid,
                "error_code": "browser_profile_locked",
                "recommended_action": "retry_later_or_restart_gateway",
            },
        )


class BrowserProfileBusyError(BrowserError):
    """Browser profile is busy with another workflow in the same process.

    The asyncio.Lock is held by another coroutine within the same Hermes
    session. Try again later.
    """

    def __init__(self, profile_path: str, *, timeout: float = 30.0) -> None:
        self.profile_path = profile_path
        self.timeout = timeout
        super().__init__(
            f"Browser profile is busy — another workflow is using it. "
            f"Timed out after {timeout}s.",
            context={
                "profile_path": profile_path,
                "timeout": timeout,
                "error_code": "browser_profile_busy",
                "recommended_action": "retry_later",
            },
        )


# ─── Transient / recoverable browser failures ───────────────────────────────
#
# Playwright surfaces a family of *transient* failures when a navigation is
# aborted or the underlying page/frame/context is torn down mid-flight
# (net::ERR_ABORTED, "frame was detached", "Target closed", "page has been
# closed", ...). These are NOT permanent — closing the broken page/context and
# re-creating a fresh one from the same persistent profile usually restores a
# working browser. They must never collapse the whole adapter into a silent
# broken state or surface as an empty-message INTERNAL_ERROR.

# Substrings (matched case-insensitively against the exception message) that
# mark a Playwright error as a transient, recoverable browser failure. Kept
# specific enough that ordinary domain errors are never misclassified.
_TRANSIENT_BROWSER_SIGNATURES: tuple[str, ...] = (
    "err_aborted",
    "net::err_aborted",
    "frame was detached",
    "frame detached",
    "navigating and changing the content",
    "execution context was destroyed",
    "target closed",
    "target page, context or browser has been closed",
    "page has been closed",
    "page closed",
    "browser has been closed",
    "context or browser has been closed",
    "browser has been disconnected",
    "websocket error",
    "connection closed",
)


class TransientBrowserError(BrowserError):
    """A recoverable Playwright/browser failure (aborted nav, detached frame,
    closed target/page/context).

    Carries enough structure for the tool layer to emit a typed, retryable
    error envelope and to drive a single recovery + retry attempt. Never
    contains cookies/tokens/session material — only the operation name and a
    short, sanitized signature describing the failure class.
    """

    def __init__(
        self,
        *,
        operation: str,
        signature: str = "",
        message: str | None = None,
    ) -> None:
        self.operation = operation
        self.signature = signature
        self.error_code = "browser_transient"
        msg = message or (
            f"Transient browser error during {operation!r} "
            f"({signature or 'page/context torn down'}). "
            f"The page/context can be recovered and the operation retried once."
        )
        super().__init__(
            msg,
            context={
                "operation": operation,
                "signature": signature,
                "error_code": self.error_code,
                "retryable": True,
                "browser_state": "broken",
                "recommended_action": "recover_browser_session_and_retry_once",
            },
        )


class BrowserBrokenError(BrowserError):
    """Browser is broken and could not be recovered automatically.

    Raised after a transient error when either recovery failed or the single
    retry after recovery failed again. The adapter must NOT keep retrying;
    a human (the operator) should approve an explicit gateway / browser restart.
    """

    def __init__(
        self,
        *,
        operation: str,
        reason: str,
        signature: str = "",
        message: str | None = None,
    ) -> None:
        self.operation = operation
        self.reason = reason
        self.signature = signature
        self.error_code = "browser_broken"
        msg = message or (
            f"Browser is broken and could not be recovered "
            f"(operation={operation!r}, reason={reason}). "
            f"Stop retrying and request an explicit gateway/browser restart."
        )
        super().__init__(
            msg,
            context={
                "operation": operation,
                "reason": reason,
                "signature": signature,
                "error_code": self.error_code,
                "retryable": False,
                "browser_state": "broken",
                "recommended_action": "request_explicit_gateway_or_browser_restart",
            },
        )


def is_transient_browser_error(exc: BaseException) -> bool:
    """Return True if *exc* looks like a transient/recoverable browser failure.

    Matches against the exception's string representation (and, where present,
    a ``.message`` attribute) using :data:`_TRANSIENT_BROWSER_SIGNATURES`.
    A :class:`TransientBrowserError` is always transient.
    """
    if isinstance(exc, TransientBrowserError):
        return True
    if isinstance(exc, BrowserBrokenError):
        # Already-classified terminal state — not "transient" anymore.
        return False
    haystacks: list[str] = []
    msg = getattr(exc, "message", None)
    if isinstance(msg, str):
        haystacks.append(msg)
    haystacks.append(str(exc))
    blob = " ".join(haystacks).lower()
    return any(sig in blob for sig in _TRANSIENT_BROWSER_SIGNATURES)


def _matched_signature(exc: BaseException) -> str:
    blob = " ".join(
        h for h in (getattr(exc, "message", None), str(exc)) if isinstance(h, str)
    ).lower()
    for sig in _TRANSIENT_BROWSER_SIGNATURES:
        if sig in blob:
            return sig
    return ""


def classify_browser_error(
    exc: BaseException, *, operation: str
) -> TransientBrowserError | None:
    """Classify *exc* as a transient browser error, or return None.

    If *exc* is already a :class:`TransientBrowserError` it is returned as-is
    (preserving its operation). Otherwise, if the message matches a transient
    signature, a new :class:`TransientBrowserError` is built for *operation*.
    Any non-transient exception returns ``None`` so the caller can re-raise it
    unchanged.
    """
    if isinstance(exc, TransientBrowserError):
        return exc
    if isinstance(exc, BrowserBrokenError):
        return None
    if is_transient_browser_error(exc):
        return TransientBrowserError(
            operation=operation,
            signature=_matched_signature(exc),
        )
    return None


# ─── Timeout ─────────────────────────────────────────────────────────────────


class BrowserLaunchTimeoutError(BrowserError):
    """Browser launch timed out."""

    def __init__(self, profile_path: str, *, timeout: float) -> None:
        self.error_code = "browser_launch_timeout"
        super().__init__(
            f"Browser launch timed out after {timeout}s.",
            context={
                "profile_path": profile_path,
                "timeout": timeout,
                "error_code": self.error_code,
                "recommended_action": "check_stale_processes_or_retry",
            },
        )


class SnapshotTimeoutError(HermesTelegramAdsError):
    """Telegram Ads snapshot timed out (per-account or total)."""

    def __init__(
        self,
        *,
        timeout: float,
        accounts_analyzed: int = 0,
        accounts_skipped: int = 0,
        partial_result: dict | None = None,
        error_code: str = "snapshot_timeout",
    ) -> None:
        self.error_code = error_code
        self.accounts_analyzed = accounts_analyzed
        self.partial_result = partial_result
        super().__init__(
            f"Snapshot timed out after {timeout}s "
            f"({accounts_analyzed} analyzed, {accounts_skipped} skipped).",
            context={
                "timeout": timeout,
                "accounts_analyzed": accounts_analyzed,
                "accounts_skipped": accounts_skipped,
                "error_code": error_code,
                "has_partial_result": partial_result is not None,
                "recommended_action": "retry_or_accept_partial",
            },
        )


class UiLoadTimeoutError(BrowserError):
    """Page/UI element load timed out."""

    def __init__(
        self, selector: str, *, timeout_ms: int, url: str = "", error_code: str = "ui_load_timeout"
    ) -> None:
        self.error_code = error_code
        super().__init__(
            f"UI element {selector!r} not found within {timeout_ms}ms. URL: {url}",
            context={
                "selector": selector,
                "timeout_ms": timeout_ms,
                "url": url,
                "error_code": error_code,
                "recommended_action": "check_network_and_retry",
            },
        )


class CleanupFailedError(HermesTelegramAdsError):
    """Browser profile cleanup failed (stale locks or processes)."""

    def __init__(
        self,
        profile_path: str,
        *,
        reason: str,
        stuck_processes: list[int] | None = None,
    ) -> None:
        self.error_code = "cleanup_failed"
        self.stuck_processes = stuck_processes or []
        super().__init__(
            f"Browser profile cleanup failed: {reason}.",
            context={
                "profile_path": profile_path,
                "reason": reason,
                "stuck_processes": self.stuck_processes,
                "error_code": self.error_code,
                "recommended_action": "manual_cleanup_required",
            },
        )
