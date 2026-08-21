"""Agent-facing schemas for the Hermes Telegram Ads tool layer.

Every ``telegram_ads_*`` tool returns a JSON-compatible ``dict`` built from
these Pydantic models. No raw Playwright objects, cookies, sessions, or page
handles ever cross this boundary.

Result envelope (every tool returns this shape via ``.model_dump(mode="json")``):

    {
      "ok": bool,
      "status": "ok" | "login_required" | "approval_required"
                 | "policy_violation" | "not_implemented" | "forbidden" | "error",
      "tool": "telegram_ads_...",
      "data": {...} | null,        # success payload
      "approval": {...} | null,    # ApprovalRequest when status == approval_required
      "error": {...} | null,       # ToolError when ok == false
      "warnings": [str, ...],
    }
"""

from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ─── Status / error codes ──────────────────────────────────────────────────────

ToolStatus = Literal[
    "ok",
    "login_required",
    "approval_required",
    "policy_violation",
    "not_implemented",
    "forbidden",
    "error",
]

ErrorCode = Literal[
    "login_required",
    "approval_required",
    "invalid_confirmation",
    "policy_violation",
    "geo_blocked",
    "not_implemented",
    "forbidden",
    "invalid_input",
    "unsupported_media_for_target_type",
    "api_error",
    "owner_bootstrap_failed",
    "browser_error",
    "browser_transient",
    "browser_broken",
    "profile_locked",
    "unknown",
]

BrowserState = Literal["healthy", "broken", "unknown"]

SafetyClassName = Literal[
    "SAFE_READ",
    "DRAFT",
    "APPROVAL_REQUIRED",
    "SENSITIVE_ACCOUNT_ACCESS",
    "FORBIDDEN_OR_DOUBLE_CONFIRM",
]


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


# ─── Helpers ───────────────────────────────────────────────────────────────────


def mask_token(token: str | None) -> str:
    """Return a non-reversible reference for a sensitive token.

    Account tokens, share tokens, owner ids etc. must never cross the agent
    boundary in the clear. We emit ``sha256:<first-12-hex>`` instead, matching
    the audit-log convention.
    """
    if not token:
        return ""
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


# ─── Core envelope ─────────────────────────────────────────────────────────────


class ToolError(_Base):
    """Structured failure detail. Never carries secrets or raw responses.

    Beyond the machine code (``error``) and human ``message``, an error MAY
    carry recovery metadata so the agent can decide what to do without parsing
    free text: which ``operation`` failed, whether it is ``retryable``, a
    ``recovery_hint``, the current ``browser_state``, and an ``artifact_path``
    for any partial result that was written to disk.
    """

    error: ErrorCode
    message: str
    capability: str | None = None
    existing_support: bool | None = None
    operation: str | None = None
    retryable: bool | None = None
    recovery_hint: str | None = None
    browser_state: BrowserState | None = None
    artifact_path: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("message", mode="before")
    @classmethod
    def _never_empty_message(cls, v: Any) -> str:
        """Guarantee a non-empty message — an empty INTERNAL_ERROR is a bug.

        The Hermes gateway maps an empty message to a useless ``INTERNAL_ERROR``
        with no detail; this validator backstops every error path so that can
        never happen.
        """
        if v is None:
            return "Unspecified error (no message provided)."
        text = str(v).strip()
        return text or "Unspecified error (no message provided)."


class TelegramAdsToolResult(_Base):
    """Uniform envelope returned by every ``telegram_ads_*`` tool."""

    ok: bool
    status: ToolStatus
    tool: str
    data: dict[str, Any] | None = None
    approval: dict[str, Any] | None = None
    error: ToolError | None = None
    warnings: list[str] = Field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class LoginRequiredResult(_Base):
    """Payload describing how a human (the operator) should restore the session.

    The agent must surface this verbatim and STOP. It never contains OTP,
    phone, code, cookie, or session material.
    """

    login_required: Literal[True] = True
    reason: str = "session_expired_or_absent"
    message: str
    manual_login_url: str = "https://ads.telegram.org/account"
    profile_dir: str | None = None
    instructions: list[str] = Field(default_factory=list)


class LoginSessionState(_Base):
    """Structured login/session state returned by the session/login tools.

    ``state`` is the explicit machine state (see ``login_flow.LoginState``); the
    flat fields let the agent decide what to do without parsing prose. Never
    carries OTP, phone (only a mask), cookie, or session material.
    """

    state: str  # LoginState value, e.g. "logged_in" | "phone_required" | ...
    logged_in: bool | None = None
    session_active: bool | None = None
    current_url: str | None = None
    profile_dir: str | None = None
    browser_state: BrowserState = "unknown"
    requires_human_login: bool = False
    recovery_hint: str | None = None
    instructions: list[str] = Field(default_factory=list)
    phone_masked: str | None = None
    phone_submitted: bool | None = None
    already_logged_in: bool | None = None
    operator_message: str | None = None
    diagnostic: str | None = None


# ─── Accounts ──────────────────────────────────────────────────────────────────


class AccountSummary(_Base):
    """One Telegram Ads cabinet, agent-safe (token masked + opaque ref)."""

    account_ref: str  # stable per-session alias, e.g. "acc_1" — use for choose_account
    title: str
    title_display: str = ""  # pipe-escaped title for safe markdown table rendering
    account_type: str
    currency: str  # "TON" | "STARS"
    balance: float = 0.0
    is_active: bool = False
    account_token_masked: str = ""
    account_ref_source: str = ""  # "list_accounts" | "reconciled" | "current_account"
    display_block: str = ""  # pre-rendered bullet-list; use this for output, never a markdown table


class TransactionView(_Base):
    kind: str
    amount: float
    currency: str
    when: str = ""
    ad_title: str | None = None
    reference: str | None = None
    kind_note: str = ""  # human-readable semantics for this transaction kind


class AccountBudgetView(_Base):
    balance: float
    currency: str
    transactions: list[TransactionView] = Field(default_factory=list)
    reserve_release_note: str | None = None  # explains transfer_to_ad / returned_from_ad


# ─── Campaigns (Telegram "ads") ────────────────────────────────────────────────


class CampaignSummary(_Base):
    """Compact ad/campaign row from the cabinet table."""

    ad_id: int
    title: str
    title_display: str = ""  # pipe-escaped title for safe markdown table rendering
    status: str
    target_summary: str = ""
    views: int = 0
    clicks: int = 0
    actions: int = 0
    action_label: str = ""
    ctr: float | None = None  # percent magnitude (3.52 == 3.52%); None when "–"
    cvr: float | None = None  # percent magnitude; None when "–"
    cpm: float = 0.0
    cpc: float | None = None  # money; None when "–"
    cpa: float | None = None  # money; None when "–"
    spent: float = 0.0
    budget: float = 0.0
    currency: str | None = None  # "TON" | "STARS" inferred from money glyph
    date_added: str = ""


class CampaignCreative(_Base):
    """The creative surface of an ad (text, link, media presence)."""

    ad_id: int
    title: str = ""
    text: str = ""
    promote_url: str = ""
    website_name: str = ""
    has_media: bool = False
    media_type: str = ""  # "photo" | "video" | "unknown" | "" (read from AdDetail)
    media_token_present: bool = False
    # None when the picture checkbox is not exposed on the detail page (e.g. an ad
    # in media mode), so it can't be inferred. True/False when the checkbox exists.
    show_picture: bool | None = None
    cta_action: str = ""


class CampaignTargeting(_Base):
    """Targeting as recoverable from the ad detail page.

    Targeting is **immutable after creation**, but the detail page renders the
    values as read-only locked chips, so they are *readable*. For a search ad,
    ``target_queries`` holds those locked query chips.
    """

    ad_id: int
    target_type: str = ""  # channels | bots | search
    target_summary: str = ""  # human label, e.g. "4 channels" / "10 queries"
    target_queries: list[str] = Field(default_factory=list)  # locked chips (search ad → queries)
    target_queries_editable: bool = False  # targeting is immutable after creation
    targeting_mutability: str = "immutable"
    source: str = ""  # e.g. "detail_page_locked_chips" when chips were parsed
    note: str = ""


class CampaignBudgetStatus(_Base):
    """Live budget/lifecycle status of an ad."""

    ad_id: int
    status: str = ""
    is_active: bool = False
    cpm: float = 0.0
    budget: float = 0.0
    spent: float = 0.0
    daily_budget: float | None = None
    status_action: str = ""  # "/edit_status" | "/edit_budget" | ""


class CampaignStatsRow(_Base):
    day: str
    views: int
    amount: float


class CampaignStats(_Base):
    ad_id: int
    title: str = ""
    date_created: str = ""
    cpm: float = 0.0
    budget: float = 0.0
    views: int = 0
    monthly: list[CampaignStatsRow] = Field(default_factory=list)
    has_csv: bool = False
    share_stats_available: bool = False


class CampaignSnapshot(_Base):
    """Combined read view of a single ad for review workflows."""

    summary: CampaignSummary
    creative: CampaignCreative | None = None
    targeting: CampaignTargeting | None = None
    budget_status: CampaignBudgetStatus | None = None
    rejection: RejectionAnalysis | None = None


# ─── Drafts / validation ───────────────────────────────────────────────────────


class CampaignDraft(_Base):
    """Agent-facing draft mirror (subset of internal CreateAdDraft).

    Used as the documented input shape for prepare/validate/create tools and as
    the output of ``telegram_ads_prepare_campaign_from_brief``.
    """

    title: str
    text: str
    promote_url: str
    cpm: float
    budget: float = 0.0
    target_type: str = "search"  # channels | bots | search
    targets: list[str] = Field(default_factory=list)
    views_per_user: int = 1
    website_name: str | None = None
    media_path: str | None = None
    initial_active: bool = False


class CopyVariantCheck(_Base):
    text: str
    valid: bool
    violations: list[str] = Field(default_factory=list)


class CampaignValidationResult(_Base):
    """Outcome of checkAdPost + local policy checks (no submission)."""

    valid: bool
    field: str = ""
    error: str = ""
    policy_violations: list[str] = Field(default_factory=list)
    preview: dict[str, Any] | None = None


class RejectionAnalysis(_Base):
    """Decline reason + deterministic explanation and remediation hints."""

    ad_id: int
    is_declined: bool
    category: str = ""
    description: str = ""
    read_more_url: str | None = None
    explanation: str = ""
    suggested_fixes: list[str] = Field(default_factory=list)


# ─── Approval flow ─────────────────────────────────────────────────────────────


class ApprovalRequest(_Base):
    """Issued when a mutating tool is called without a valid confirmation.

    The agent must present ``human_summary`` to the operator and, on explicit approval,
    call ``telegram_ads_apply_approved_action`` with ``confirmation_id``.
    """

    confirmation_id: str
    second_confirmation_id: str | None = None  # set for double-confirm actions
    tool: str
    action: str  # canonical safety action (e.g. "editAdCPM")
    safety_class: SafetyClassName
    risk_level: str  # confirm_required | double_confirm_required
    requires_double_confirmation: bool = False
    params: dict[str, Any] = Field(default_factory=dict)
    human_summary: str = ""
    expires_in_seconds: int = 300


class PendingConfirmationView(_Base):
    confirmation_id: str
    second_confirmation_id: str | None = None
    tool: str
    action: str
    risk_level: str
    params: dict[str, Any] = Field(default_factory=dict)
    human_summary: str = ""
    created_at: str = ""


class ApprovedActionResult(_Base):
    """Result of executing a previously-approved mutating action."""

    confirmation_id: str
    tool: str
    action: str
    executed: bool
    result: dict[str, Any] = Field(default_factory=dict)


# ─── Artifacts ─────────────────────────────────────────────────────────────────


class ScreenshotArtifact(_Base):
    path: str
    full_page: bool = False
    label: str = ""


class ReportArtifact(_Base):
    path: str
    scope: str  # "ad" | "account"
    month: str
    ad_id: int | None = None


# ─── Snapshot ──────────────────────────────────────────────────────────────────


class AccountSnapshot(_Base):
    """Per-cabinet snapshot collected by ``telegram_ads_snapshot_accounts``."""

    account: AccountSummary
    budget: AccountBudgetView | None = None
    campaigns: list[CampaignSummary] = Field(default_factory=list)
    screenshot: ScreenshotArtifact | None = None
    warnings: list[str] = Field(default_factory=list)


class AccountsSnapshotResult(_Base):
    """Top-level result of the composite snapshot wrapper."""

    accounts: list[AccountSnapshot] = Field(default_factory=list)
    json_summary_path: str | None = None
    total_accounts: int = 0
    total_campaigns: int = 0
    partial: bool = False
    warnings: list[str] = Field(default_factory=list)


CampaignSnapshot.model_rebuild()


# ─── Envelope constructors ─────────────────────────────────────────────────────


def tool_ok(
    tool: str, data: dict[str, Any] | None = None, *, warnings: list[str] | None = None
) -> dict[str, Any]:
    return TelegramAdsToolResult(
        ok=True, status="ok", tool=tool, data=data, warnings=warnings or []
    ).to_dict()


def tool_login_required(tool: str, payload: LoginRequiredResult) -> dict[str, Any]:
    return TelegramAdsToolResult(
        ok=False,
        status="login_required",
        tool=tool,
        data=payload.model_dump(mode="json"),
        error=ToolError(error="login_required", message=payload.message),
    ).to_dict()


def tool_approval_required(tool: str, approval: ApprovalRequest) -> dict[str, Any]:
    return TelegramAdsToolResult(
        ok=False,
        status="approval_required",
        tool=tool,
        approval=approval.model_dump(mode="json"),
        error=ToolError(
            error="approval_required",
            message=approval.human_summary or f"{approval.action} requires explicit approval",
        ),
    ).to_dict()


def tool_policy_violation(tool: str, violations: list[str]) -> dict[str, Any]:
    return TelegramAdsToolResult(
        ok=False,
        status="policy_violation",
        tool=tool,
        error=ToolError(
            error="policy_violation",
            message="Ad content violates Telegram Ads guidelines",
            details={"violations": violations},
        ),
    ).to_dict()


def tool_not_implemented(tool: str, capability: str, *, message: str = "") -> dict[str, Any]:
    return TelegramAdsToolResult(
        ok=False,
        status="not_implemented",
        tool=tool,
        error=ToolError(
            error="not_implemented",
            message=message or f"Capability {capability!r} is not supported by the current tool",
            capability=capability,
            existing_support=False,
        ),
    ).to_dict()


def tool_forbidden(tool: str, capability: str, *, message: str = "") -> dict[str, Any]:
    return TelegramAdsToolResult(
        ok=False,
        status="forbidden",
        tool=tool,
        error=ToolError(
            error="forbidden",
            message=message or f"Capability {capability!r} is forbidden by safety policy",
            capability=capability,
            existing_support=False,
        ),
    ).to_dict()


def tool_failure(
    tool: str,
    code: ErrorCode,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return TelegramAdsToolResult(
        ok=False,
        status="error",
        tool=tool,
        error=ToolError(error=code, message=message, details=details or {}),
    ).to_dict()


def tool_owner_bootstrap_failed(
    tool: str,
    message: str,
    *,
    operation: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Structured envelope for owner_id bootstrap failures.

    Retryable — the agent should re-run ensure_login / choose_account then retry
    the original mutation. Confirmation tokens are NOT consumed because bootstrap
    is checked before safety.gate().
    """
    return TelegramAdsToolResult(
        ok=False,
        status="error",
        tool=tool,
        error=ToolError(
            error="owner_bootstrap_failed",
            message=message,
            operation=operation,
            retryable=True,
            recovery_hint=(
                "Run ensure_login / list_accounts / choose_account or retry "
                "after account bootstrap completes."
            ),
            details=details or {},
        ),
    ).to_dict()


def tool_browser_error(
    tool: str,
    code: ErrorCode,
    message: str,
    *,
    operation: str,
    retryable: bool,
    browser_state: BrowserState,
    recovery_hint: str,
    artifact_path: str | None = None,
    details: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Structured envelope for browser/transient/broken failures.

    Always carries the full recovery metadata block (operation, retryable,
    recovery_hint, browser_state, artifact_path) so the agent never has to
    guess. ``message`` is guaranteed non-empty by ``ToolError``.
    """
    return TelegramAdsToolResult(
        ok=False,
        status="error",
        tool=tool,
        error=ToolError(
            error=code,
            message=message,
            operation=operation,
            retryable=retryable,
            recovery_hint=recovery_hint,
            browser_state=browser_state,
            artifact_path=artifact_path,
            details=details or {},
        ),
        warnings=warnings or [],
    ).to_dict()


__all__ = [
    "AccountBudgetView",
    "AccountSnapshot",
    "AccountSummary",
    "AccountsSnapshotResult",
    "ApprovalRequest",
    "ApprovedActionResult",
    "CampaignBudgetStatus",
    "CampaignCreative",
    "CampaignDraft",
    "CampaignSnapshot",
    "CampaignStats",
    "CampaignStatsRow",
    "CampaignSummary",
    "CampaignTargeting",
    "CampaignValidationResult",
    "CopyVariantCheck",
    "LoginRequiredResult",
    "PendingConfirmationView",
    "RejectionAnalysis",
    "ReportArtifact",
    "ScreenshotArtifact",
    "TelegramAdsToolResult",
    "ToolError",
    "TransactionView",
    "mask_token",
    "tool_approval_required",
    "tool_browser_error",
    "tool_failure",
    "tool_forbidden",
    "tool_login_required",
    "tool_not_implemented",
    "tool_ok",
    "tool_owner_bootstrap_failed",
    "tool_policy_violation",
]
