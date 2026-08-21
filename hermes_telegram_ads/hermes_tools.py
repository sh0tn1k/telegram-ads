"""Hermes agent-facing tool layer for ads.telegram.org.

This is the ONLY surface Hermes agents should touch.
Every capability of :class:`TelegramAdsAdapter` is exposed here as a typed,
JSON-in / JSON-out ``telegram_ads_*`` tool. Agents never drive Xvfb, Chromium,
Playwright clicks, DISPLAY, or the internal ``/api`` endpoint directly.

Design rules enforced here
--------------------------
* No raw Playwright / browser objects cross the boundary — only JSON dicts.
* No secrets cross the boundary — account tokens are masked, ``owner_id`` /
  ``hash`` / ``confirm_hash`` / cookies / phone / email are scrubbed.
* Mutating actions never run without a valid confirmation token. Direct
  ``TelegramAdsToolset.call`` without ``confirmation_id`` still returns
  ``approval_required``. On the live Hermes gateway the persist-safe plugin
  escalates those tools to the native Telegram once / session / always / deny
  buttons; after Accept the same call auto-consumes the token in-process.
  ``telegram_ads_apply_approved_action`` remains the typed-yes fallback.
* ``login_required`` is returned structurally — the agent must surface it and
  STOP. The agent never enters OTP / phone / login codes.

The registry :data:`TELEGRAM_ADS_TOOLS` describes every tool (name, schema,
safety class, mutating flag, approval flag). :meth:`TelegramAdsToolset.handler_for`
binds a callable handler for Hermes registration.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from hermes_telegram_ads.adapter import TelegramAdsAdapter
from hermes_telegram_ads.config import SafetyConfig, TelegramAdsConfig
from hermes_telegram_ads.constants import (
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
)
from hermes_telegram_ads.cpm_modifiers import (
    CPM_MODIFIERS,
    compute_effective_cpm,
    detect_custom_emoji,
)
from hermes_telegram_ads.errors import (
    BrowserBrokenError,
    BrowserError,
    BrowserProfileBusyError,
    BrowserProfileLockedError,
    ConfirmationRequiredError,
    ForbiddenActionError,
    GeoTargetingError,
    HermesTelegramAdsError,
    InvalidConfirmationError,
    InvalidMethodError,
    LoginRequiredError,
    MediaUnsupportedError,
    OwnerBootstrapFailedError,
    PolicyViolationError,
    TelegramAdsApiError,
    TransientBrowserError,
    classify_browser_error,
)
from hermes_telegram_ads.login_flow import (
    LoginState,
    instructions_for,
    mask_phone,
    recovery_hint_for,
    requires_human_login,
)
from hermes_telegram_ads.media import (
    MEDIA_PLACEMENT_RECOVERY_HINT,
    media_type_for_path,
    uploaded_media_supported_for_target_type,
)
from hermes_telegram_ads.operator_approval import OPERATOR_APPROVED_ARG
from hermes_telegram_ads.safety import TelegramAdsSafety
from hermes_telegram_ads.schemas import (
    AccountBudgetView,
    AccountSnapshot,
    AccountsSnapshotResult,
    AccountSummary,
    ApprovalRequest,
    ApprovedActionResult,
    BrowserState,
    CampaignBudgetStatus,
    CampaignCreative,
    CampaignDraft,
    CampaignStats,
    CampaignStatsRow,
    CampaignSummary,
    CampaignTargeting,
    CampaignValidationResult,
    CopyVariantCheck,
    LoginRequiredResult,
    LoginSessionState,
    PendingConfirmationView,
    RejectionAnalysis,
    ReportArtifact,
    SafetyClassName,
    ScreenshotArtifact,
    TransactionView,
    mask_token,
    tool_approval_required,
    tool_browser_error,
    tool_failure,
    tool_forbidden,
    tool_login_required,
    tool_not_implemented,
    tool_ok,
    tool_owner_bootstrap_failed,
    tool_policy_violation,
)
from hermes_telegram_ads.types import CreateAdDraft, EditAdDraft

# ─── Safety classes ────────────────────────────────────────────────────────────


class SafetyClass(str, Enum):
    """High-level safety bucket for a tool (coarser than RiskLevel)."""

    SAFE_READ = "SAFE_READ"
    DRAFT = "DRAFT"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    SENSITIVE_ACCOUNT_ACCESS = "SENSITIVE_ACCOUNT_ACCESS"
    FORBIDDEN_OR_DOUBLE_CONFIRM = "FORBIDDEN_OR_DOUBLE_CONFIRM"


# ─── Tool spec / registry ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    safety_class: SafetyClass
    handler: str  # method name on TelegramAdsToolset
    mutating: bool = False
    requires_approval: bool = False
    returns: str = "TelegramAdsToolResult"
    group: str = "read"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "safety_class": self.safety_class.value,
            "mutating": self.mutating,
            "requires_approval": self.requires_approval,
            "returns": self.returns,
            "group": self.group,
        }


# ─── JSON-schema helpers ───────────────────────────────────────────────────────


def _obj(props: dict[str, Any], required: tuple[str, ...] = (), *, extra: bool = False) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": list(required),
        "additionalProperties": extra,
    }


_STR = {"type": "string"}
_INT = {"type": "integer"}
_NUM = {"type": "number"}
_BOOL = {"type": "boolean"}

_DRAFT_SCHEMA = {
    "type": "object",
    "description": "Ad/campaign draft. Mirrors CreateAdDraft.",
    "properties": {
        "title": _STR,
        "text": _STR,
        "promote_url": {"type": "string", "description": "t.me/<name>, @<name>, or https://<site>"},
        "cpm": _NUM,
        "budget": _NUM,
        "target_type": {"type": "string", "enum": ["channels", "bots", "search"]},
        "targets": {"type": "array", "items": _STR},
        "views_per_user": {"type": "integer", "minimum": 1, "maximum": 4},
        "website_name": _STR,
        "media_path": {
            "type": "string",
            "description": "Local 16:9 image to upload for channel-targeted ads only (photo +50% CPM on supported placement). Search/bot targeting do not support uploaded photo/video creatives. Video is not yet supported.",
        },
        "show_picture": {
            "type": "boolean",
            "description": "Show the bot/channel picture in the ad (+30% CPM). Default true.",
        },
        "exclude_channels": {"type": "array", "items": _STR},
        "initial_active": _BOOL,
    },
    "required": ["title", "text", "promote_url", "cpm", "target_type"],
    "additionalProperties": True,
}


# ─── Sensitive-key scrubbing ───────────────────────────────────────────────────

# Keys whose *values* are internal identifiers / credentials → dropped entirely.
_DROP_KEYS = frozenset(
    {
        "owner_id",
        "hash",
        "confirm_hash",
        "csrf",
        "csrf_hash",
        "cookie",
        "cookies",
        "session",
        "stel_token",
        "stel_ssid",
        "set-cookie",
    }
)
# Keys whose values are sensitive but a reference is useful → masked.
_MASK_KEYS = frozenset({"token", "account_token", "share_token", "phone", "phone_number", "email"})


def _scrub(value: Any) -> Any:
    """Recursively remove/mask secrets from any API result before returning."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            kl = str(k).lower()
            if kl in _DROP_KEYS:
                continue
            if kl in _MASK_KEYS and isinstance(v, str):
                out[k] = mask_token(v)
                continue
            out[k] = _scrub(v)
        return out
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    return value


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _clean(value: Any) -> Any:
    return _scrub(_jsonable(value))


# ─── Rejection guidance (deterministic) ────────────────────────────────────────

_REJECTION_GUIDANCE: list[tuple[tuple[str, ...], str, list[str]]] = [
    (
        ("prohibited", "graphic", "shocking", "sexual", "hate", "violence", "weapon", "drug", "gambling"),
        "The ad falls into a prohibited content category and cannot run on Telegram Ads.",
        [
            "Remove prohibited content (adult, gambling, weapons, drugs, hate/violence, deceptive finance).",
            "Review Telegram Ads guidelines section 5 (Prohibited content).",
            "If the product itself is prohibited, the ad cannot be approved — do not resubmit unchanged.",
        ],
    ),
    (
        ("editorial", "format", "capital", "punctuation", "list", "emoji", "style"),
        "The ad violates editorial / formatting requirements.",
        [
            "Keep text to one line (no line breaks, bullet or numbered lists).",
            "Avoid excessive capitalization and decorative punctuation.",
            "Limit to a single inline link in @name or t.me/ form.",
            "Edit the ad and resubmit — editing triggers re-review.",
        ],
    ),
    (
        ("deceptive", "misleading", "false", "clickbait"),
        "The ad was flagged as deceptive or misleading.",
        [
            "Remove unverifiable claims, fake urgency, and clickbait.",
            "Make the destination clearly match the ad promise.",
            "Add the real product name / brand instead of vague hooks.",
        ],
    ),
    (
        ("trademark", "third-party", "third party", "copyright", "impersonat", "brand"),
        "The ad appears to infringe third-party rights or impersonate a brand.",
        [
            "Remove other companies' trademarks, logos, or names you are not authorized to use.",
            "Promote only channels/bots/sites you own or are authorized to advertise.",
        ],
    ),
    (
        ("link", "url", "destination", "landing"),
        "The promoted link / destination was rejected.",
        [
            "Use a t.me/<name>, @<name>, or a real https:// website (no shorteners, no IPs).",
            "Make sure the destination is reachable and matches the ad text.",
        ],
    ),
]


def _explain_rejection(category: str, description: str) -> tuple[str, list[str]]:
    haystack = f"{category} {description}".lower()
    for keys, expl, fixes in _REJECTION_GUIDANCE:
        if any(k in haystack for k in keys):
            return expl, list(fixes)
    return (
        "The ad was declined. Read the decline reason and align the ad with Telegram Ads guidelines.",
        [
            "Read the decline category and description carefully.",
            "Consult https://ads.telegram.org/guidelines.",
            "Edit the ad to comply and resubmit (editing triggers a new review).",
        ],
    )


# ─── Approval registry ─────────────────────────────────────────────────────────


@dataclass
class PendingAction:
    """A mutating action awaiting human approval.

    Mirrors the confirmation held inside ``adapter.safety`` but additionally
    stores the user-facing params + tool name so the action can be replayed by
    ``telegram_ads_apply_approved_action`` without the agent resending args.
    """

    confirmation_id: str
    tool: str
    action: str
    params: dict[str, Any]
    risk_level: str
    human_summary: str
    second_confirmation_id: str | None = None
    # Masked/redacted params safe to surface to the agent. When set, the view and
    # approval card show THESE instead of ``params`` (which may hold a real phone
    # used only for the eventual replay). ``params`` itself never reaches a view.
    display_params: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    def to_view(self) -> PendingConfirmationView:
        return PendingConfirmationView(
            confirmation_id=self.confirmation_id,
            second_confirmation_id=self.second_confirmation_id,
            tool=self.tool,
            action=self.action,
            risk_level=self.risk_level,
            params=self.display_params if self.display_params is not None else self.params,
            human_summary=self.human_summary,
            created_at=self.created_at.isoformat(),
        )


class ApprovalRegistry:
    def __init__(self) -> None:
        self._pending: dict[str, PendingAction] = {}

    def add(self, action: PendingAction) -> None:
        self._pending[action.confirmation_id] = action

    def get(self, confirmation_id: str) -> PendingAction | None:
        return self._pending.get(confirmation_id)

    def remove(self, confirmation_id: str) -> PendingAction | None:
        return self._pending.pop(confirmation_id, None)

    def list(self) -> list[PendingAction]:
        return list(self._pending.values())


# ─── Toolset ───────────────────────────────────────────────────────────────────


class TelegramAdsToolset:
    """Stateful holder that turns adapter capabilities into agent tools.

    Construct one per agent session. Either inject an adapter directly
    (``TelegramAdsToolset(adapter=...)``) or provide a config / factory so the
    browser launches lazily on first use::

        toolset = TelegramAdsToolset(config=TelegramAdsConfig.default())
        result = await toolset.call("telegram_ads_list_accounts")
    """

    def __init__(
        self,
        adapter: TelegramAdsAdapter | None = None,
        *,
        config: TelegramAdsConfig | None = None,
        adapter_factory: Callable[[], Awaitable[TelegramAdsAdapter]] | None = None,
    ) -> None:
        self._adapter = adapter
        self._config = config or (adapter.config if adapter is not None else None)
        self._adapter_factory = adapter_factory
        self._approvals = ApprovalRegistry()
        self._account_refs: dict[str, str] = {}  # ref -> token
        self._token_to_ref: dict[str, str] = {}
        # (title, currency, account_type) -> ref; populated by list_accounts for reconciliation
        self._fingerprint_to_ref: dict[tuple[str, str, str], str] = {}
        # Set only for the duration of call() after Hermes operator Accept.
        self._operator_approved = False

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def _get_adapter(self) -> TelegramAdsAdapter:
        if self._adapter is None:
            if self._adapter_factory is not None:
                self._adapter = await self._adapter_factory()
            elif self._config is not None:
                self._adapter = await TelegramAdsAdapter.launch(self._config)
            else:
                raise HermesTelegramAdsError(
                    "TelegramAdsToolset has no adapter, config, or factory configured."
                )
            if self._config is None:
                self._config = self._adapter.config
        return self._adapter

    async def aclose(self) -> None:
        if self._adapter is not None:
            await self._adapter.close()
            self._adapter = None

    def _cfg(self) -> TelegramAdsConfig | None:
        if self._adapter is not None:
            return self._adapter.config
        return self._config

    def _safety(self) -> TelegramAdsSafety:
        """Static safety/policy engine usable offline (no browser launch)."""
        if self._adapter is not None:
            return self._adapter.safety
        cfg = self._cfg()
        return TelegramAdsSafety(cfg.safety if cfg is not None else SafetyConfig())

    # ── Account reference mapping (keeps raw tokens off the agent surface) ──

    def _ref_for(
        self,
        token: str,
        *,
        fallback_label: str = "",
        title: str = "",
        currency: str = "",
        account_type: str = "",
    ) -> str:
        fingerprint = (title, currency, account_type) if title else None

        if token:
            if token in self._token_to_ref:
                ref = self._token_to_ref[token]
            else:
                ref = f"acc_{len(self._token_to_ref) + 1}"
                self._token_to_ref[token] = ref
                self._account_refs[ref] = token
            if fingerprint:
                self._fingerprint_to_ref[fingerprint] = ref
            return ref

        # No token (e.g. current_account — token not visible in header).
        # Try fingerprint reconciliation against a previous list_accounts call.
        if fingerprint and fingerprint in self._fingerprint_to_ref:
            return self._fingerprint_to_ref[fingerprint]

        key = f"__current__:{fallback_label}"
        if key in self._token_to_ref:
            return self._token_to_ref[key]
        ref = f"acc_{len(self._token_to_ref) + 1}"
        self._token_to_ref[key] = ref
        self._account_refs[ref] = token
        return ref

    def _resolve_account(self, account_ref: str) -> str:
        if account_ref in self._account_refs:
            return self._account_refs[account_ref]
        if account_ref.startswith("acc_"):
            raise HermesTelegramAdsError(
                f"Unknown account_ref {account_ref!r}. Call telegram_ads_list_accounts first.",
            )
        # Power-user / internal path: a raw token was passed through.
        return account_ref

    @staticmethod
    def _account_display_block(
        ref: str,
        title: str,
        account_type: str,
        currency: str,
        balance: float,
        is_active: bool,
    ) -> str:
        """Pre-rendered bullet-list for safe agent output.

        Uses the raw title (not title_display) — |  is not special in a list
        context, only in a markdown table column. Never put this in a table row.
        """
        active = "yes" if is_active else "no"
        bal = f"{balance:g} {currency}"
        return (
            f"- **account_ref:** {ref}\n"
            f"- **title:** {title}\n"
            f"- **type:** {account_type}\n"
            f"- **currency:** {currency}\n"
            f"- **balance:** {bal}\n"
            f"- **active:** {active}"
        )

    def _account_summary(self, acc: Any, *, source: str = "list_accounts") -> AccountSummary:
        fingerprint = (acc.title, acc.currency, acc.account_type)
        pre_reconciled = (
            not acc.account_token
            and bool(acc.title)
            and fingerprint in self._fingerprint_to_ref
        )
        ref = self._ref_for(
            acc.account_token,
            fallback_label=acc.title,
            title=acc.title,
            currency=acc.currency,
            account_type=acc.account_type,
        )
        actual_source = "reconciled" if pre_reconciled else source
        return AccountSummary(
            account_ref=ref,
            title=acc.title,
            title_display=acc.title.replace("|", r"\|"),
            account_type=acc.account_type,
            currency=acc.currency,
            balance=acc.balance,
            is_active=acc.is_active,
            account_token_masked=mask_token(acc.account_token),
            account_ref_source=actual_source,
            display_block=self._account_display_block(
                ref=ref,
                title=acc.title,
                account_type=acc.account_type,
                currency=acc.currency,
                balance=acc.balance,
                is_active=acc.is_active,
            ),
        )

    # ── Dispatch ───────────────────────────────────────────────────────────

    async def call(self, name: str, **kwargs: Any) -> dict[str, Any]:
        """Invoke a tool by name. Always returns a JSON-compatible envelope."""
        spec = TOOLS_BY_NAME.get(name)
        if spec is None:
            return tool_failure(name, "invalid_input", f"Unknown tool: {name!r}")
        handler = getattr(self, spec.handler)
        from hermes_telegram_ads.runtime_kwargs import bind_handler_kwargs

        # Pop the operator-gate flag before bind: it is not a handler param.
        operator_approved = bool(kwargs.pop(OPERATOR_APPROVED_ARG, False))
        # Hermes injects session_id/task_id/user_task; bind by signature so a
        # handler that forgets **_ cannot TypeError the same way research did.
        kwargs = bind_handler_kwargs(handler, kwargs)
        prev_approved = self._operator_approved
        self._operator_approved = operator_approved
        try:
            return await self._invoke_with_recovery(spec, handler, kwargs)
        except LoginRequiredError as e:
            return self._login_required(name, e)
        except ConfirmationRequiredError as e:
            return tool_failure(name, "approval_required", e.message, details=e.context)
        except InvalidConfirmationError as e:
            return tool_failure(name, "invalid_confirmation", e.message, details=e.context)
        except PolicyViolationError as e:
            return tool_policy_violation(name, e.violations)
        except MediaUnsupportedError as e:
            if e.capability == "unsupported_media_for_target_type":
                return tool_failure(name, "unsupported_media_for_target_type", e.message, details=e.context)
            return tool_not_implemented(name, e.capability, message=e.message)
        except GeoTargetingError as e:
            return tool_failure(name, "geo_blocked", e.message, details=e.context)
        except ForbiddenActionError as e:
            return tool_forbidden(name, name, message=e.message)
        except InvalidMethodError as e:
            return tool_failure(name, "api_error", e.message, details={"method": e.method})
        except OwnerBootstrapFailedError as e:
            return tool_owner_bootstrap_failed(
                name,
                e.message,
                operation=e.operation or name,
                details=e.context,
            )
        except TelegramAdsApiError as e:
            return tool_failure(name, "api_error", e.message)
        # ── Browser failures (subclasses of BrowserError → HermesTelegramAdsError);
        #    must be caught BEFORE the generic HermesTelegramAdsError branch. ──
        except BrowserBrokenError as e:
            return self._browser_broken_envelope(name, e)
        except TransientBrowserError as e:
            return self._transient_browser_envelope(name, e)
        except (BrowserProfileLockedError, BrowserProfileBusyError) as e:
            return tool_browser_error(
                name,
                "browser_error",
                e.message,
                operation=name,
                retryable=True,
                browser_state="unknown",
                recovery_hint=str(
                    e.context.get("recommended_action", "retry_later")
                ),
                details=e.context,
            )
        except BrowserError as e:
            return tool_browser_error(
                name,
                "browser_error",
                e.message,
                operation=name,
                retryable=True,
                browser_state=self._current_browser_state(),
                recovery_hint=(
                    "Call telegram_ads_recover_browser_session, then retry."
                ),
                details=e.context,
            )
        except TypeError as e:
            return tool_failure(name, "invalid_input", str(e))
        except HermesTelegramAdsError as e:
            return tool_failure(
                name, "unknown", e.message or f"{type(e).__name__} (no detail)", details=e.context
            )
        except Exception as e:  # noqa: BLE001 — never let a tool crash the agent loop
            return tool_failure(name, "unknown", f"{type(e).__name__}: {e}")
        finally:
            self._operator_approved = prev_approved

    # ── Recovery wrapper ─────────────────────────────────────────────────────

    async def _invoke_with_recovery(
        self,
        spec: ToolSpec,
        handler: Callable[..., Awaitable[dict[str, Any]]],
        kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        """Run *handler*; on a transient browser error recover once and retry.

        Policy (matches the documented recovery contract):
          * SAFE_READ / DRAFT tools: on a transient browser error, attempt a
            single in-place browser recovery, then retry the handler exactly
            once. If recovery fails, or the retry hits another transient error,
            raise :class:`BrowserBrokenError` (no loop).
          * Mutating tools: NEVER auto-retry (a retry could double-submit a
            create/edit/budget action). The transient error is raised as-is so
            the agent can recover explicitly and re-issue with a fresh
            confirmation. Recovery itself performs no mutations.
        """
        try:
            return await handler(**kwargs)
        except Exception as exc:  # noqa: BLE001 — classify, then re-raise
            transient = classify_browser_error(exc, operation=spec.name)
            if transient is None:
                raise
            # Mutating actions must not be auto-retried.
            if spec.mutating:
                raise transient from exc

            recovered = await self._attempt_recovery()
            if not recovered:
                raise BrowserBrokenError(
                    operation=spec.name,
                    reason="recovery_failed",
                    signature=transient.signature,
                ) from exc

            # Single retry after a successful recovery — never loop.
            try:
                return await handler(**kwargs)
            except Exception as exc2:  # noqa: BLE001
                transient2 = classify_browser_error(exc2, operation=spec.name)
                if transient2 is None:
                    raise
                raise BrowserBrokenError(
                    operation=spec.name,
                    reason="retry_after_recovery_failed",
                    signature=transient2.signature,
                ) from exc2

    async def _attempt_recovery(self) -> bool:
        """Recover the shared browser in place. Returns True on success.

        Never raises and never performs a mutating Telegram Ads action.
        """
        if self._adapter is None:
            return False
        try:
            result = await self._adapter.recover_browser()
            return bool(result.get("recovered"))
        except Exception:  # noqa: BLE001 — recovery is best-effort
            return False

    def _current_browser_state(self) -> BrowserState:
        if self._adapter is None:
            return "unknown"
        try:
            return "healthy" if self._adapter.browser_healthy() else "broken"
        except Exception:  # noqa: BLE001
            return "unknown"

    def _transient_browser_envelope(
        self, tool: str, e: TransientBrowserError
    ) -> dict[str, Any]:
        return tool_browser_error(
            tool,
            "browser_transient",
            e.message,
            operation=e.operation or tool,
            retryable=True,
            browser_state=self._current_browser_state(),
            recovery_hint=(
                "Transient browser error. Call "
                "telegram_ads_recover_browser_session, then retry once."
            ),
            details=e.context,
        )

    def _browser_broken_envelope(
        self, tool: str, e: BrowserBrokenError
    ) -> dict[str, Any]:
        return tool_browser_error(
            tool,
            "browser_broken",
            e.message,
            operation=e.operation or tool,
            retryable=False,
            browser_state="broken",
            recovery_hint=(
                "Automatic recovery + one retry already failed. Do NOT retry "
                "in a loop. Request an explicit gateway/browser restart "
                "approval from the operator before continuing."
            ),
            details=e.context,
        )

    def handler_for(self, name: str) -> Callable[..., Awaitable[dict[str, Any]]]:
        """Return a bound coroutine callable for Hermes registration."""

        async def _bound(**kwargs: Any) -> dict[str, Any]:
            return await self.call(name, **kwargs)

        _bound.__name__ = name
        return _bound

    def to_hermes_tools(self) -> list[dict[str, Any]]:
        """Return registry entries with bound handlers for Hermes."""
        out: list[dict[str, Any]] = []
        for spec in TELEGRAM_ADS_TOOLS:
            entry = spec.to_dict()
            entry["handler"] = self.handler_for(spec.name)
            out.append(entry)
        return out

    # ── login_required helper ──────────────────────────────────────────────

    def _login_required(self, tool: str, exc: LoginRequiredError) -> dict[str, Any]:
        cfg = self._cfg()
        profile_dir = None
        if cfg is not None:
            try:
                profile_dir = str(cfg.browser.profile_dir)
            except Exception:
                profile_dir = None
        payload = LoginRequiredResult(
            message=exc.message,
            profile_dir=profile_dir,
            instructions=[
                "Call telegram_ads_login_from_env (types TELEGRAM_ADS_PHONE from host .env).",
                "Tell the operator the masked number was entered; they tap Accept in Telegram.",
                "Then call telegram_ads_login_wait until logged_in. The Chromium profile keeps the session.",
                "Only if state=code_required: ask the operator for the ads.telegram.org code and call telegram_ads_login_submit_code.",
            ],
        )
        return tool_login_required(tool, payload)

    # ── Mutation helpers ───────────────────────────────────────────────────

    def _issue_single(
        self,
        *,
        tool: str,
        action: str,
        conf: Any,
        params: dict[str, Any],
        human_summary: str,
        display_params: dict[str, Any] | None = None,
        safety_class: SafetyClassName = "APPROVAL_REQUIRED",
    ) -> dict[str, Any]:
        self._approvals.add(
            PendingAction(
                confirmation_id=conf.id,
                tool=tool,
                action=action,
                params=params,
                display_params=display_params,
                risk_level="confirm_required",
                human_summary=human_summary,
            )
        )
        approval = ApprovalRequest(
            confirmation_id=conf.id,
            tool=tool,
            action=action,
            safety_class=safety_class,
            risk_level="confirm_required",
            requires_double_confirmation=False,
            # Never surface the raw replay params (may hold a real phone).
            params=display_params if display_params is not None else params,
            human_summary=human_summary,
            expires_in_seconds=conf.ttl_seconds,
        )
        return tool_approval_required(tool, approval)

    def _issue_double(
        self,
        *,
        tool: str,
        action: str,
        conf1: Any,
        conf2: Any,
        params: dict[str, Any],
        human_summary: str,
    ) -> dict[str, Any]:
        self._approvals.add(
            PendingAction(
                confirmation_id=conf1.id,
                second_confirmation_id=conf2.id,
                tool=tool,
                action=action,
                params=params,
                risk_level="double_confirm_required",
                human_summary=human_summary,
            )
        )
        approval = ApprovalRequest(
            confirmation_id=conf1.id,
            second_confirmation_id=conf2.id,
            tool=tool,
            action=action,
            safety_class="FORBIDDEN_OR_DOUBLE_CONFIRM",
            risk_level="double_confirm_required",
            requires_double_confirmation=True,
            params=params,
            human_summary=human_summary,
            expires_in_seconds=conf1.ttl_seconds,
        )
        return tool_approval_required(tool, approval)

    def _resolve_confirmation(self, confirmation_id: str | None, issue: Callable[[], Any]) -> tuple[str | None, Any | None]:
        """Return (id, None) to execute, or (None, issued_conf) to envelope.

        Hermes operator Accept sets ``_operator_approved`` so the issued token
        is consumed in this same call. Direct callers still get the envelope.
        """
        if confirmation_id:
            return confirmation_id, None
        conf = issue()
        if self._operator_approved:
            return conf.id, None
        return None, conf

    def _resolve_double_confirmation(
        self,
        confirmation_id: str | None,
        second_confirmation_id: str | None,
        issue: Callable[[], tuple[Any, Any]],
    ) -> tuple[str | None, str | None, Any | None, Any | None]:
        if confirmation_id and second_confirmation_id:
            return confirmation_id, second_confirmation_id, None, None
        conf1, conf2 = issue()
        if self._operator_approved:
            return conf1.id, conf2.id, None, None
        return None, None, conf1, conf2

    def _finish_mutation(self, *, tool: str, action: str, confirmation_id: str, raw: Any) -> dict[str, Any]:
        self._approvals.remove(confirmation_id)
        data = ApprovedActionResult(
            confirmation_id=confirmation_id,
            tool=tool,
            action=action,
            executed=True,
            result=_clean(raw) if isinstance(raw, dict) else {"value": _clean(raw)},
        )
        return tool_ok(tool, data.model_dump(mode="json"))

    # ════════════════════════════════════════════════════════════════════════
    # A. Session / browser / login
    # ════════════════════════════════════════════════════════════════════════

    def _augment_state_fields(self, info: dict[str, Any], state: LoginState) -> dict[str, Any]:
        """Add the structured state-machine fields (state, requires_human_login,
        recovery_hint, instructions) to a session-diagnostic dict in place."""
        info["state"] = state.value
        info["requires_human_login"] = requires_human_login(state)
        info["recovery_hint"] = recovery_hint_for(state)
        info["instructions"] = instructions_for(state)
        return info

    async def _h_status(self, **_: Any) -> dict[str, Any]:
        """Report tool/session state. ALWAYS returns a useful diagnostic — even
        when the page/context is broken or the profile is locked — never raises."""
        cfg = self._cfg()
        info: dict[str, Any] = {
            "launched": self._adapter is not None,
            "logged_in": None,
            "session_active": None,
            "current_url": None,
            "profile_dir": str(cfg.browser.profile_dir) if cfg else None,
            "headless": cfg.browser.headless if cfg else None,
            "browser_state": "unknown",
            "diagnostic": None,
        }
        state = LoginState.UNKNOWN
        if self._adapter is None:
            # Nothing launched — still useful: detect a profile lock held elsewhere.
            if cfg is not None:
                with contextlib.suppress(Exception):
                    from hermes_telegram_ads.browser import check_profile_lock

                    lock = await check_profile_lock(cfg.browser.profile_dir)
                    if lock.get("locked"):
                        state = LoginState.PROFILE_LOCKED
                        info["diagnostic"] = (
                            "Browser profile is locked by another process. Close it "
                            "or approve a debug restart — the agent will not kill it."
                        )
            return tool_ok("telegram_ads_status", self._augment_state_fields(info, state))

        healthy = self._adapter.browser_healthy()
        info["browser_state"] = "healthy" if healthy else "broken"
        if not healthy:
            state = LoginState.BROWSER_BROKEN
            info["diagnostic"] = (
                "Browser page/context is broken. Call "
                "telegram_ads_recover_browser_session to rebuild it, then retry."
            )
            return tool_ok("telegram_ads_status", self._augment_state_fields(info, state))

        with contextlib.suppress(Exception):
            info["current_url"] = self._adapter.browser.current_url
        with contextlib.suppress(Exception):
            info["session_active"] = await self._adapter.browser.has_session_cookie()
        try:
            await self._adapter.ensure_logged_in()
            info["logged_in"] = True
            state = LoginState.LOGGED_IN
        except LoginRequiredError:
            info["logged_in"] = False
            state = LoginState.AUTH_PAGE
        except Exception as exc:  # noqa: BLE001 — status must not raise
            info["logged_in"] = None
            state = LoginState.UNKNOWN
            info["diagnostic"] = (
                f"Login check failed ({type(exc).__name__}). "
                "Call telegram_ads_recover_browser_session, then retry."
            )
        return tool_ok("telegram_ads_status", self._augment_state_fields(info, state))

    def _login_data(self, state: dict[str, Any]) -> dict[str, Any]:
        """Validate a raw adapter state dict through LoginSessionState (drops any
        stray keys, guarantees the agent-safe shape — no cookies/tokens)."""
        return LoginSessionState.model_validate(state).model_dump(mode="json")

    async def _h_ensure_login(self, **_: Any) -> dict[str, Any]:
        adapter = await self._get_adapter()
        await adapter.ensure_logged_in()  # raises LoginRequiredError -> envelope
        # Enrich with the full structured state on success.
        data = await adapter.detect_login_state(navigate=False)
        return tool_ok("telegram_ads_ensure_login", self._login_data(data))

    async def _h_login_check(self, **_: Any) -> dict[str, Any]:
        """Lightweight read-only login/session detection (state machine).

        Like status, but actively navigates to /account so a logged-out redirect
        to /auth is observed. Never submits a phone/code, never mutates.
        """
        tool = "telegram_ads_login_check"
        adapter = await self._get_adapter()
        data = self._login_data(await adapter.detect_login_state())
        return tool_ok(tool, data)

    async def _h_login_start(self, confirmation_id: str | None = None, **_: Any) -> dict[str, Any]:
        """Open the Telegram Ads auth page and begin login. SENSITIVE: gated by an
        explicit human approval (opening the login widget can trigger an app
        prompt). Never submits a phone or an OTP code.
        """
        tool = "telegram_ads_login_start"
        adapter = await self._get_adapter()
        if not confirmation_id:
            # Read-only pre-check: if already logged in, no approval is needed.
            current = self._login_data(await adapter.detect_login_state())
            if current.get("state") == LoginState.LOGGED_IN.value:
                return tool_ok(tool, current)
            conf = adapter.issue_login_start_confirmation()
            return self._issue_single(
                tool=tool,
                action="login_start",
                conf=conf,
                params={},
                safety_class="SENSITIVE_ACCOUNT_ACCESS",
                human_summary=(
                    "Open the Telegram Ads auth page and begin login. This may trigger a "
                    "login-approval prompt in your Telegram app. No phone number or OTP "
                    "code will be submitted by the agent."
                ),
            )
        raw = await adapter.login_start(confirmation_id=confirmation_id)
        self._approvals.remove(confirmation_id)
        return tool_ok(tool, self._login_data(raw))

    async def _h_login_submit_phone(
        self, phone: str | None = None, confirmation_id: str | None = None, **_: Any
    ) -> dict[str, Any]:
        """Submit a phone number to the login form. SENSITIVE + approval-gated.

        Only runs if the operator explicitly provides the phone. The number is never
        logged/persisted in the clear (only a mask); no OTP code is entered.
        """
        tool = "telegram_ads_login_submit_phone"
        if not phone:
            from hermes_telegram_ads.config import ads_phone_from_env

            phone = ads_phone_from_env()
        if not phone or not _looks_like_phone(phone):
            return tool_failure(
                tool,
                "invalid_input",
                "No usable phone: pass phone explicitly or set TELEGRAM_ADS_PHONE in the host .env. "
                "The agent never invents phone numbers.",
            )
        adapter = await self._get_adapter()
        masked = mask_phone(phone)
        if not confirmation_id:
            conf = adapter.issue_login_submit_phone_confirmation(phone)
            return self._issue_single(
                tool=tool,
                action="login_submit_phone",
                conf=conf,
                params={"phone": phone},  # real phone — replay only, never shown/logged
                display_params={"phone_masked": masked},
                safety_class="SENSITIVE_ACCOUNT_ACCESS",
                human_summary=(
                    f"Submit phone {masked} to the Telegram Ads login form. Telegram will "
                    "send a login approval to your app; the agent will NOT enter an OTP code."
                ),
            )
        raw = await adapter.login_submit_phone(phone, confirmation_id=confirmation_id)
        self._approvals.remove(confirmation_id)
        return tool_ok(tool, self._login_data(raw))

    async def _h_login_submit_code(
        self, code: str | None = None, confirmation_id: str | None = None, **_: Any
    ) -> dict[str, Any]:
        """Submit the ads.telegram.org OTP the operator just confirmed.

        [GOAL] Persist the Chromium cabinet session after the human types the code.
        The code is never logged or returned.
        """
        tool = "telegram_ads_login_submit_code"
        trimmed = (code or "").strip()
        if not trimmed or not trimmed.replace(" ", "").isdigit() or len(trimmed.replace(" ", "")) < 4:
            return tool_failure(
                tool,
                "invalid_input",
                "A numeric login code from Telegram is required (the operator pastes it).",
            )
        adapter = await self._get_adapter()
        if not confirmation_id:
            conf = adapter.issue_login_submit_code_confirmation()
            return self._issue_single(
                tool=tool,
                action="login_submit_code",
                conf=conf,
                params={"code": trimmed},
                display_params={"code": "[redacted]"},
                safety_class="SENSITIVE_ACCOUNT_ACCESS",
                human_summary=(
                    "Submit the Telegram Ads login code the operator just confirmed. "
                    "The Chromium profile on this host will keep the cabinet session."
                ),
            )
        raw = await adapter.login_submit_code(trimmed, confirmation_id=confirmation_id)
        self._approvals.remove(confirmation_id)
        return tool_ok(tool, self._login_data(raw))

    async def _h_login_wait(
        self, timeout_sec: float = 120.0, poll_interval_sec: float = 3.0, **_: Any
    ) -> dict[str, Any]:
        """Poll until logged in or timeout. Read-only — no phone/code submission."""
        tool = "telegram_ads_login_wait"
        adapter = await self._get_adapter()
        raw = await adapter.login_wait(
            timeout_sec=timeout_sec, poll_interval_sec=poll_interval_sec
        )
        data = self._login_data(raw)
        if data.get("state") == LoginState.LOGGED_IN.value:
            return tool_ok(tool, data)
        # Timed out (or otherwise not logged in) — a human must still act.
        payload = LoginRequiredResult(
            reason=str(data.get("state") or "login_timeout"),
            message=(
                "Login did not complete within the wait window. Approve the login in "
                "your Telegram app, then re-run telegram_ads_login_wait."
            ),
            profile_dir=data.get("profile_dir"),
            instructions=data.get("instructions") or [],
        )
        out = tool_login_required(tool, payload)
        # Carry the structured state alongside the login_required payload.
        out["data"] = {**(out.get("data") or {}), **data}
        return out

    async def _h_login_from_env(self, **_: Any) -> dict[str, Any]:
        """
        [GOAL] Log into ads.telegram.org using TELEGRAM_ADS_PHONE and wait for Accept.
        [INPUT] Host env only — no phone argument from the model.
        [OUTPUT] Masked phone + login state; browser profile persists the session.

        Tell the operator the number was entered, then they tap Accept in Telegram.
        """
        tool = "telegram_ads_login_from_env"
        from hermes_telegram_ads.config import ads_phone_from_env

        phone = ads_phone_from_env()
        if not phone or not _looks_like_phone(phone):
            return tool_failure(
                tool,
                "invalid_input",
                "TELEGRAM_ADS_PHONE is missing or not an E.164 number in the host .env.",
            )
        adapter = await self._get_adapter()
        raw = await adapter.login_authorize_from_env(phone)
        data = self._login_data(raw)
        data["phone_submitted"] = bool(raw.get("phone_submitted"))
        data["already_logged_in"] = bool(raw.get("already_logged_in"))
        if raw.get("operator_message"):
            data["operator_message"] = raw["operator_message"]
        if raw.get("diagnostic"):
            data["diagnostic"] = raw["diagnostic"]
        if data.get("state") == LoginState.LOGGED_IN.value:
            return tool_ok(tool, data)
        if raw.get("phone_submitted") or data.get("state") == LoginState.APP_APPROVAL_PENDING.value:
            return tool_ok(tool, data)
        return tool_ok(tool, data)

    async def _h_recover_browser_session(self, **_: Any) -> dict[str, Any]:
        """Recover a broken browser page/context in place (read-only).

        Closes the broken page/context and recreates a fresh one from the same
        persistent profile. Performs NO ads.telegram.org mutations and enters no
        OTP/login codes. On success the saved session survives; on failure the
        agent must request an explicit gateway/browser restart from the operator.
        """
        tool = "telegram_ads_recover_browser_session"
        if self._adapter is None:
            # Nothing launched yet — a recovery is a no-op; report unknown state.
            return tool_ok(
                tool,
                {
                    "recovered": False,
                    "reason": "no_adapter",
                    "browser_state": "unknown",
                    "healthy_before": None,
                    "steps": [],
                    "errors": [],
                    "note": "No browser is launched yet; nothing to recover.",
                },
            )
        healthy_before = self._adapter.browser_healthy()
        result = await self._adapter.recover_browser()
        recovered = bool(result.get("recovered"))
        data = {
            "recovered": recovered,
            "healthy_before": healthy_before,
            "browser_state": "healthy" if recovered else "broken",
            "steps": result.get("steps", []),
            "errors": result.get("errors", []),
            "mutating": False,
        }
        if not recovered:
            return tool_browser_error(
                tool,
                "browser_broken",
                "Browser recovery failed — the page/context could not be rebuilt.",
                operation=tool,
                retryable=False,
                browser_state="broken",
                recovery_hint=(
                    "Recovery could not rebuild the browser. Request an explicit "
                    "gateway/browser restart approval from the operator. Do NOT fall back "
                    "to raw Playwright/terminal without explicit approval."
                ),
                details=data,
            )
        return tool_ok(tool, data)

    async def _h_login_assist(self, **_: Any) -> dict[str, Any]:
        cfg = self._cfg()
        payload = LoginRequiredResult(
            reason="manual_login_help",
            message="Telegram Ads login is manual via the Telegram app. The agent cannot log in for you.",
            profile_dir=str(cfg.browser.profile_dir) if cfg else None,
            instructions=[
                "Open the persistent browser profile (a human, not the agent).",
                "Navigate to https://ads.telegram.org/account.",
                "Approve the login in your Telegram app — never type a code into the agent.",
                "Once the cabinet loads, the session is saved in the profile; re-run tools.",
            ],
        )
        # login_assist is informational and safe even when already logged in.
        return tool_ok("telegram_ads_login_assist", payload.model_dump(mode="json"))

    async def _h_open_dashboard(self, **_: Any) -> dict[str, Any]:
        adapter = await self._get_adapter()
        url = await adapter.open_dashboard()
        return tool_ok("telegram_ads_open_dashboard", {"current_url": url})

    async def _h_current_page(self, **_: Any) -> dict[str, Any]:
        adapter = await self._get_adapter()
        return tool_ok("telegram_ads_current_page", {"current_url": adapter.browser.current_url})

    async def _h_save_screenshot(
        self, screenshot_name: str | None = None, full_page: bool = False, **_: Any
    ) -> dict[str, Any]:
        adapter = await self._get_adapter()
        path = await adapter.screenshot(name=screenshot_name, full_page=full_page)
        art = ScreenshotArtifact(
            path=str(path), full_page=full_page, label=screenshot_name or ""
        )
        return tool_ok("telegram_ads_save_screenshot", art.model_dump(mode="json"))

    async def _h_get_browser_profile_info(self, **_: Any) -> dict[str, Any]:
        """Return non-sensitive browser/profile diagnostics. Never raises and
        stays useful even when the page/context is broken."""
        cfg = self._cfg()
        info: dict[str, Any] = {
            "profile_dir": str(cfg.browser.profile_dir) if cfg else None,
            "headless": cfg.browser.headless if cfg else None,
            "viewport": (
                {"width": cfg.browser.viewport_width, "height": cfg.browser.viewport_height} if cfg else None
            ),
            "launched": self._adapter is not None,
            "session_active": None,
            "browser_state": "unknown",
            "profile_locked": None,
            "diagnostic": None,
        }
        # Profile-lock check works whether or not an adapter is launched.
        if cfg is not None:
            with contextlib.suppress(Exception):
                from hermes_telegram_ads.browser import check_profile_lock

                lock = await check_profile_lock(cfg.browser.profile_dir)
                info["profile_locked"] = bool(lock.get("locked"))
                if lock.get("locked"):
                    info["diagnostic"] = (
                        "Browser profile is locked by another process. Close it or "
                        "approve a debug restart — the agent will not kill it."
                    )
        if self._adapter is not None:
            healthy = self._adapter.browser_healthy()
            info["browser_state"] = "healthy" if healthy else "broken"
            if healthy:
                try:
                    info["session_active"] = await self._adapter.browser.has_session_cookie()
                except Exception as exc:  # noqa: BLE001 — diagnostic must not raise
                    info["session_active"] = None
                    info["diagnostic"] = f"session probe failed ({type(exc).__name__})"
            elif not info["diagnostic"]:
                info["diagnostic"] = (
                    "Browser page/context is broken. Call "
                    "telegram_ads_recover_browser_session to rebuild it."
                )
        # Never include cookie values / file contents.
        return tool_ok("telegram_ads_get_browser_profile_info", info)

    # ════════════════════════════════════════════════════════════════════════
    # B. Accounts / cabinets
    # ════════════════════════════════════════════════════════════════════════

    async def _h_list_accounts(self, **_: Any) -> dict[str, Any]:
        adapter = await self._get_adapter()
        accounts = await adapter.list_accounts()
        summaries = [self._account_summary(a) for a in accounts]
        return tool_ok(
            "telegram_ads_list_accounts",
            {"accounts": [s.model_dump(mode="json") for s in summaries], "count": len(summaries)},
        )

    async def _h_choose_account(self, account_ref: str, **_: Any) -> dict[str, Any]:
        adapter = await self._get_adapter()
        token = self._resolve_account(account_ref)
        acc = await adapter.choose_account(token)
        data: dict[str, Any] = {"account_ref": account_ref, "selected": acc is not None}
        if acc is not None:
            data["current_account"] = self._account_summary(acc).model_dump(mode="json")
        return tool_ok("telegram_ads_choose_account", data)

    async def _h_current_account(self, **_: Any) -> dict[str, Any]:
        adapter = await self._get_adapter()
        acc = await adapter.get_current_account()
        if acc is None:
            return tool_ok("telegram_ads_current_account", {"current_account": None})
        summary = self._account_summary(acc, source="current_account")
        warnings: list[str] | None = None
        if summary.account_ref_source == "current_account":
            # Token not visible in page header AND no list_accounts fingerprint match yet.
            warnings = [
                "account_ref may be unstable: token not visible in page header and no "
                "matching list_accounts entry. Call telegram_ads_list_accounts first for a "
                "stable account_ref."
            ]
        return tool_ok(
            "telegram_ads_current_account",
            {"current_account": summary.model_dump(mode="json")},
            warnings=warnings,
        )

    _TRANSACTION_KIND_NOTES: dict[str, str] = {
        "payment_for_views": "Real spend — deducted from account balance for delivered views.",
        "transfer_to_ad": (
            "Budget allocation — reserved from account balance into ad budget. "
            "Not spend; the money moves from account balance to ad budget."
        ),
        "returned_from_ad": (
            "Budget release — returned from ad budget back to account balance. "
            "Not income or spend; paired with an earlier transfer_to_ad."
        ),
        "payment": "Top-up via Fragment payment — balance credited.",
        "transfer_from_bot": "Stars transfer from bot account — balance credited.",
        "transfer_from_account": "Ad-level inflow — balance moved from ad budget back to account.",
        "transfer_to_account": "Ad-level outflow — balance moved from account to ad budget.",
    }

    _RESERVE_RELEASE_NOTE = (
        "transfer_to_ad = budget allocation (reserved, not spent); "
        "returned_from_ad = budget release (returned, not income). "
        "Only payment_for_views is real spend."
    )

    def _make_budget_view(self, budget: Any) -> AccountBudgetView:
        """Build an annotated AccountBudgetView from any AccountBudget-like object.

        Shared by _h_get_account_budget and the snapshot path so kind_note and
        reserve_release_note are always populated consistently.
        """
        kinds_present = {t.kind for t in budget.transactions}
        has_reserve_release = bool(kinds_present & {"transfer_to_ad", "returned_from_ad"})
        return AccountBudgetView(
            balance=budget.balance,
            currency=budget.currency,
            transactions=[
                TransactionView(
                    kind=t.kind,
                    amount=t.amount,
                    currency=t.currency,
                    when=t.when,
                    ad_title=t.ad_title,
                    reference=t.reference,
                    kind_note=self._TRANSACTION_KIND_NOTES.get(t.kind, ""),
                )
                for t in budget.transactions
            ],
            reserve_release_note=self._RESERVE_RELEASE_NOTE if has_reserve_release else None,
        )

    async def _h_get_account_budget(self, **_: Any) -> dict[str, Any]:
        adapter = await self._get_adapter()
        budget = await adapter.get_account_budget()
        view = self._make_budget_view(budget)
        return tool_ok("telegram_ads_get_account_budget", view.model_dump(mode="json"))

    # ── Timeout constants for snapshot ──────────────────────────────

    _SNAPSHOT_PER_ACCOUNT_TIMEOUT = 60.0   # per-account budget+ads+screenshot
    _SNAPSHOT_TOTAL_TIMEOUT = 240.0        # overall snapshot (all accounts)
    _STATUS_TIMEOUT = 30.0                 # telegram_ads_status

    async def _h_snapshot_accounts(
        self,
        full_page: bool = False,
        max_campaigns_per_account: int | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        tool = "telegram_ads_snapshot_accounts"
        adapter = await self._get_adapter()
        await adapter.ensure_logged_in()  # login_required -> stops here safely

        accounts = await adapter.list_accounts()
        snapshots: list[AccountSnapshot] = []
        warnings: list[str] = []
        partial = False
        total_campaigns = 0
        snapshot_start = asyncio.get_event_loop().time()
        analyzed = 0
        skipped = 0

        async def _scan_one_account(acc: Any) -> AccountSnapshot:
            summary = self._account_summary(acc)
            try:
                result = await asyncio.wait_for(
                    _scan_account_inner(adapter, acc, summary, max_campaigns_per_account),
                    timeout=self._SNAPSHOT_PER_ACCOUNT_TIMEOUT,
                )
                return result
            except TimeoutError:
                return AccountSnapshot(
                    account=summary,
                    budget=None,
                    campaigns=[],
                    screenshot=None,
                    warnings=[f"account timeout after {self._SNAPSHOT_PER_ACCOUNT_TIMEOUT}s"],
                )

        async def _scan_account_inner(
            adapter_inner, acc_inner, summary, max_campaigns
        ) -> AccountSnapshot:
            acc_warnings: list[str] = []
            budget_view: AccountBudgetView | None = None
            campaigns: list[CampaignSummary] = []
            shot: ScreenshotArtifact | None = None

            if acc_inner.account_token:
                try:
                    await adapter_inner.choose_account(acc_inner.account_token)
                except Exception as e:
                    acc_warnings.append(f"choose_account failed: {type(e).__name__}")
                    return AccountSnapshot(
                        account=summary, budget=None, campaigns=[],
                        screenshot=None, warnings=acc_warnings,
                    )

            try:
                b = await adapter_inner.get_account_budget()
                budget_view = self._make_budget_view(b)
            except Exception as e:
                acc_warnings.append(f"budget unavailable: {type(e).__name__}")

            try:
                ads = await adapter_inner.list_ads()
                if max_campaigns is not None:
                    ads = ads[:max_campaigns]
                campaigns = [self._campaign_summary(a) for a in ads]
            except Exception as e:
                acc_warnings.append(f"campaigns unavailable: {type(e).__name__}")

            try:
                p = await adapter_inner.screenshot(
                    name=f"snapshot_{summary.account_ref}.png", full_page=full_page,
                )
                shot = ScreenshotArtifact(
                    path=str(p), full_page=full_page, label=summary.account_ref,
                )
            except Exception as e:
                acc_warnings.append(f"screenshot failed: {type(e).__name__}")

            return AccountSnapshot(
                account=summary, budget=budget_view, campaigns=campaigns,
                screenshot=shot, warnings=acc_warnings,
            )

        for acc in accounts:
            # Check total timeout before each account
            elapsed = asyncio.get_event_loop().time() - snapshot_start
            if elapsed >= self._SNAPSHOT_TOTAL_TIMEOUT:
                warnings.append(
                    f"Snapshot total timeout ({self._SNAPSHOT_TOTAL_TIMEOUT}s) "
                    f"reached after {analyzed} account(s). "
                    f"{len(accounts) - analyzed - skipped} account(s) skipped."
                )
                skipped += len(accounts) - analyzed - skipped
                partial = True
                break

            try:
                entry = await _scan_one_account(acc)
                snapshots.append(entry)
                total_campaigns += len(entry.campaigns)
                if entry.warnings:
                    partial = True
                    warnings.extend(
                        f"{entry.account.title}: {w}" for w in entry.warnings
                    )
                analyzed += 1
            except Exception as e:
                summary = self._account_summary(acc)
                snapshots.append(AccountSnapshot(
                    account=summary, budget=None, campaigns=[],
                    screenshot=None,
                    warnings=[f"account scan failed: {type(e).__name__}: {e}"],
                ))
                skipped += 1
                partial = True

        json_path = self._write_snapshot_json(snapshots)
        result = AccountsSnapshotResult(
            accounts=snapshots,
            json_summary_path=json_path,
            total_accounts=len(accounts),
            total_campaigns=total_campaigns,
            partial=partial,
            warnings=warnings,
        )
        return tool_ok(tool, result.model_dump(mode="json"), warnings=warnings)

    def _write_snapshot_json(self, snapshots: list[AccountSnapshot]) -> str | None:
        cfg = self._cfg()
        if cfg is None or cfg.storage.base_path is None:
            return None
        try:
            out_dir = Path(cfg.storage.base_path) / "snapshots"
            out_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
            dest = out_dir / f"snapshot_{ts}.json"
            payload = [s.model_dump(mode="json") for s in snapshots]
            dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return str(dest)
        except Exception:
            return None

    # ════════════════════════════════════════════════════════════════════════
    # C. Campaign / ad read actions
    # ════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _campaign_summary(a: Any) -> CampaignSummary:
        return CampaignSummary(
            ad_id=a.ad_id,
            title=a.title,
            title_display=a.title.replace("|", r"\|"),
            status=a.status,
            target_summary=getattr(a, "target_summary", "") or "",
            views=a.views,
            clicks=a.clicks,
            actions=getattr(a, "actions", 0) or 0,
            action_label=getattr(a, "action_label", "") or "",
            ctr=getattr(a, "ctr", None),
            cvr=getattr(a, "cvr", None),
            cpm=a.cpm,
            cpc=getattr(a, "cpc", None),
            cpa=getattr(a, "cpa", None),
            spent=a.spent,
            budget=a.budget,
            currency=getattr(a, "currency", None),
            date_added=getattr(a, "date_added", "") or "",
        )

    async def _h_list_ads(self, **_: Any) -> dict[str, Any]:
        adapter = await self._get_adapter()
        ads = await adapter.list_ads()
        return tool_ok(
            "telegram_ads_list_ads",
            {
                "campaigns": [self._campaign_summary(a).model_dump(mode="json") for a in ads],
                "count": len(ads),
            },
        )

    async def _h_get_ad(self, ad_id: int, **_: Any) -> dict[str, Any]:
        adapter = await self._get_adapter()
        detail = await adapter.get_ad(ad_id)
        creative, targeting, budget_status, rejection = self._ad_detail_views(ad_id, detail)
        return tool_ok(
            "telegram_ads_get_ad",
            {
                "ad_id": ad_id,
                "status": detail.ad.status,
                "creative": creative.model_dump(mode="json"),
                "targeting": targeting.model_dump(mode="json"),
                "budget_status": budget_status.model_dump(mode="json"),
                "rejection": rejection.model_dump(mode="json") if rejection else None,
            },
        )

    @staticmethod
    def _ad_detail_views(
        ad_id: int, detail: Any
    ) -> tuple[CampaignCreative, CampaignTargeting, CampaignBudgetStatus, RejectionAnalysis | None]:
        ad = detail.ad
        creative = CampaignCreative(
            ad_id=ad_id,
            title=ad.title,
            text=ad.text,
            promote_url=getattr(detail, "promote_url", "") or "",
            website_name=getattr(detail, "website_name", "") or "",
            # Read media state from the parsed AdDetail media fields — NOT from
            # website_name (which is unrelated to whether media was uploaded).
            has_media=bool(getattr(detail, "has_media", False)),
            media_type=getattr(detail, "media_type", "") or "",
            media_token_present=bool(getattr(detail, "media_token", "") or ""),
            show_picture=getattr(detail, "show_picture", None),
            cta_action=getattr(ad, "action", "") or "",
        )
        locked = list(getattr(detail, "locked_targets", []) or [])
        targeting = CampaignTargeting(
            ad_id=ad_id,
            target_type=_trg_to_target_type(ad.trg_type),
            target_summary=getattr(ad, "target", "") or "",
            target_queries=locked,
            target_queries_editable=False,
            targeting_mutability="immutable",
            source="detail_page_locked_chips" if locked else "",
            note=(
                "Targeting is visible as read-only locked chips on the detail page "
                "but immutable after creation. To change targeting (e.g. search "
                "queries), recreate the ad — it cannot be edited in place."
            ),
        )
        budget_status = CampaignBudgetStatus(
            ad_id=ad_id,
            status=ad.status,
            is_active=bool(getattr(detail, "is_active", False)),
            cpm=ad.cpm,
            budget=ad.budget,
            spent=ad.spent,
            daily_budget=getattr(detail, "daily_budget_value", None),
            status_action=getattr(ad, "status_url", "") or "",
        )
        rejection: RejectionAnalysis | None = None
        dr = getattr(detail, "decline_reason", None)
        if dr is not None:
            expl, fixes = _explain_rejection(dr.category, dr.description)
            rejection = RejectionAnalysis(
                ad_id=ad_id,
                is_declined=True,
                category=dr.category,
                description=dr.description,
                read_more_url=dr.read_more_url,
                explanation=expl,
                suggested_fixes=fixes,
            )
        return creative, targeting, budget_status, rejection

    async def _h_get_ad_stats(self, ad_id: int, **_: Any) -> dict[str, Any]:
        adapter = await self._get_adapter()
        stats = await adapter.get_ad_stats(ad_id)
        view = CampaignStats(
            ad_id=stats.ad_id,
            title=stats.title,
            date_created=stats.date_created,
            cpm=stats.cpm,
            budget=stats.budget,
            views=stats.views,
            monthly=[CampaignStatsRow(day=r.day, views=r.views, amount=r.amount) for r in stats.monthly],
            has_csv=bool(stats.csv_url),
            share_stats_available=bool(stats.share_stats_url),
        )
        return tool_ok("telegram_ads_get_ad_stats", view.model_dump(mode="json"))

    async def _h_get_ad_creative(self, ad_id: int, **_: Any) -> dict[str, Any]:
        adapter = await self._get_adapter()
        detail = await adapter.get_ad(ad_id)
        creative, _tg, _bs, _rj = self._ad_detail_views(ad_id, detail)
        return tool_ok("telegram_ads_get_ad_creative", creative.model_dump(mode="json"))

    async def _h_get_ad_targeting(self, ad_id: int, **_: Any) -> dict[str, Any]:
        adapter = await self._get_adapter()
        detail = await adapter.get_ad(ad_id)
        _cr, targeting, _bs, _rj = self._ad_detail_views(ad_id, detail)
        return tool_ok("telegram_ads_get_ad_targeting", targeting.model_dump(mode="json"))

    async def _h_get_ad_budget_status(self, ad_id: int, **_: Any) -> dict[str, Any]:
        adapter = await self._get_adapter()
        detail = await adapter.get_ad(ad_id)
        _cr, _tg, budget_status, _rj = self._ad_detail_views(ad_id, detail)
        return tool_ok("telegram_ads_get_ad_budget_status", budget_status.model_dump(mode="json"))

    async def _h_get_rejection_info(self, ad_id: int, **_: Any) -> dict[str, Any]:
        adapter = await self._get_adapter()
        detail = await adapter.get_ad(ad_id)
        _cr, _tg, _bs, rejection = self._ad_detail_views(ad_id, detail)
        if rejection is None:
            rejection = RejectionAnalysis(ad_id=ad_id, is_declined=False)
        return tool_ok("telegram_ads_get_rejection_info", rejection.model_dump(mode="json"))

    async def _h_explain_rejection(self, ad_id: int, **_: Any) -> dict[str, Any]:
        adapter = await self._get_adapter()
        detail = await adapter.get_ad(ad_id)
        _cr, _tg, _bs, rejection = self._ad_detail_views(ad_id, detail)
        if rejection is None:
            rejection = RejectionAnalysis(
                ad_id=ad_id,
                is_declined=False,
                explanation="This ad is not declined; no rejection to explain.",
            )
        return tool_ok("telegram_ads_explain_rejection", rejection.model_dump(mode="json"))

    async def _h_download_report(self, month: str, ad_id: int | None = None, **_: Any) -> dict[str, Any]:
        adapter = await self._get_adapter()
        if ad_id is not None:
            path = await adapter.download_ad_report(ad_id, month)
            art = ReportArtifact(path=str(path), scope="ad", month=month, ad_id=ad_id)
        else:
            path = await adapter.download_account_report(month)
            art = ReportArtifact(path=str(path), scope="account", month=month)
        return tool_ok("telegram_ads_download_report", art.model_dump(mode="json"))

    async def _h_get_share_stats_url(self, ad_id: int, **_: Any) -> dict[str, Any]:
        adapter = await self._get_adapter()
        url = await adapter.get_share_stats_url(ad_id)
        return tool_ok(
            "telegram_ads_get_share_stats_url",
            {"ad_id": ad_id, "share_stats_url": url, "exists": bool(url)},
        )

    async def _h_list_events(self, **_: Any) -> dict[str, Any]:
        adapter = await self._get_adapter()
        events = await adapter.list_events()
        return tool_ok(
            "telegram_ads_list_events",
            {
                "events": [
                    {
                        "event_id": e.event_id,
                        "title": e.title,
                        "type": e.type,
                        "status": e.status,
                        "used": e.used,
                    }
                    for e in events
                ],
                "count": len(events),
            },
        )

    async def _h_get_event_log(self, event_id: str, **_: Any) -> dict[str, Any]:
        adapter = await self._get_adapter()
        log = await adapter.get_event_log(event_id)
        return tool_ok(
            "telegram_ads_get_event_log",
            {
                "event_id": log.event_id,
                "title": log.title,
                "window_hours": log.window_hours,
                "entries": [{"event_time": en.event_time, "domain": en.domain} for en in log.entries],
            },
        )

    async def _h_get_pixel_snippet(self, event_id: str | None = None, **_: Any) -> dict[str, Any]:
        adapter = await self._get_adapter()
        if event_id:
            snippet = await adapter.get_event_setup_snippet(event_id)
        else:
            snippet = await adapter.get_pixel_base_snippet()
        if snippet is None:
            return tool_ok("telegram_ads_get_pixel_snippet", {"pixel_id": None, "available": False})
        return tool_ok(
            "telegram_ads_get_pixel_snippet",
            {
                "pixel_id": snippet.pixel_id,
                "base_code": snippet.base_code,
                "event_code": snippet.event_code,
                "available": True,
            },
        )

    async def _h_get_ad_events(self, ad_id: int | None = None, **_: Any) -> dict[str, Any]:
        # Per-ad event feed does not exist in the current tool. Pixel conversion
        # events are account-level — use telegram_ads_list_events / get_event_log.
        return tool_not_implemented(
            "telegram_ads_get_ad_events",
            "get_ad_events",
            message=(
                "Per-ad event feed is not provided by ads.telegram.org. Use "
                "telegram_ads_list_events and telegram_ads_get_event_log for "
                "account-level pixel conversion events."
            ),
        )

    # ════════════════════════════════════════════════════════════════════════
    # D. Draft / preparation actions
    # ════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _draft_from_dict(draft: dict[str, Any]) -> CreateAdDraft:
        return CreateAdDraft.model_validate(draft)

    @staticmethod
    def _creative_summary(
        d: CreateAdDraft,
        *,
        media_uploaded: bool | None = None,
        actual_server_cpm: float | None = None,
        actual_ui_cpm_extra: str | None = None,
    ) -> dict[str, Any]:
        """Creative-options + effective-CPM summary for validate/preview/create.

        Pure (no I/O): derived from the draft. The CPM is an ESTIMATE — Telegram's
        UI/validation is authoritative, especially when modifiers stack.
        """
        media_type = media_type_for_path(d.media_path) if d.media_path else None
        media_supported = uploaded_media_supported_for_target_type(d.target_type)
        effective_media_type = media_type if media_supported else None
        custom_emoji = detect_custom_emoji(d.text)
        est = compute_effective_cpm(
            d.cpm,
            show_picture=d.show_picture,
            custom_emoji=custom_emoji,
            media_type=effective_media_type if effective_media_type in ("photo", "video") else None,
        )
        warnings: list[str] = []
        if custom_emoji:
            warnings.append("Custom emoji detected in text — Telegram adds +50% CPM.")
        if d.show_picture:
            warnings.append("Show bot/channel picture is enabled — +30% CPM.")
        if media_type == "photo" and media_supported:
            warnings.append(
                "Uploaded photo — local estimate +50% CPM, but the uploaded-media "
                "surcharge is UI-authoritative (a live channel photo showed +80%); "
                "rely on the checkAdPost/UI cpm_extra value when available."
            )
        if media_type == "video" and media_supported:
            warnings.append("Uploaded video — +80% CPM (video upload is not yet supported by this tool).")
        if media_type and not media_supported:
            warnings.append(
                "Uploaded media is ignored/unsupported for this placement. "
                f"{MEDIA_PLACEMENT_RECOVERY_HINT}."
            )
        if est.needs_validation:
            warnings.append(
                "Multiple CPM modifiers stack — the effective CPM is an estimate; "
                "verify the exact value in the Telegram Ads UI before relying on it."
            )
        summary = est.to_dict()
        summary.update(
            {
                "show_picture": d.show_picture,
                "media_present": bool(d.media_path),
                "media_type": media_type,
                "media_supported_by_target_type": media_supported,
                "media_ignored_by_placement": bool(d.media_path and not media_supported),
                "recovery_hint": MEDIA_PLACEMENT_RECOVERY_HINT if d.media_path and not media_supported else None,
                "custom_emoji_detected": custom_emoji,
                "actual_server_cpm": actual_server_cpm,
                "warnings": warnings,
            }
        )
        # UI-authoritative creative surcharge from checkAdPost (e.g. "+80%").
        # When present it overrides the static local media modifier estimate.
        ui_pct = _parse_pct(actual_ui_cpm_extra)
        if ui_pct is not None:
            local_media_pct = (
                CPM_MODIFIERS.get(f"media_{effective_media_type}")
                if effective_media_type in ("photo", "video")
                else None
            )
            summary.update(
                {
                    "actual_ui_cpm_extra": actual_ui_cpm_extra,
                    "actual_ui_cpm_extra_pct": ui_pct,
                    "actual_ui_effective_cpm": round(float(d.cpm) * (1 + ui_pct / 100), 4),
                    "modifier_source": "Telegram Ads checkAdPost/UI",
                    "local_estimate_stale": (
                        local_media_pct is not None and float(local_media_pct) != ui_pct
                    ),
                }
            )
        else:
            summary.update(
                {
                    "actual_ui_cpm_extra": None,
                    "actual_ui_cpm_extra_pct": None,
                    "actual_ui_effective_cpm": None,
                    "local_estimate_stale": False,
                }
            )
        if media_uploaded is not None:
            summary["media_uploaded"] = media_uploaded
        return summary

    @staticmethod
    def _unsupported_media_for_target_type(tool: str, d: CreateAdDraft) -> dict[str, Any] | None:
        """Return structured block envelope when media_path is incompatible.

        This is intentionally tool-layer preflight so create_ad can refuse before
        issuing approval_required and validate/preview can refuse before browser
        upload/checkAdPost.
        """
        if not d.media_path or uploaded_media_supported_for_target_type(d.target_type):
            return None
        target_type = (d.target_type or "").lower()
        return tool_failure(
            tool,
            "unsupported_media_for_target_type",
            (
                "unsupported_media_for_target_type: uploaded photo/video creatives "
                "are supported only for target_type='channels'. Search campaigns "
                "use text/query workflow only; bot targeting uses logo/show_picture "
                "workflow only."
            ),
            details={
                "reason": "unsupported_media_for_target_type",
                "target_type": target_type,
                "media_path": d.media_path,
                "supported_target_types": ["channels"],
                "blocked_before_upload": True,
                "blocked_before_check_ad_post": tool in {
                    "telegram_ads_validate_ad",
                    "telegram_ads_preview_ad",
                },
                "blocked_before_confirmation": tool == "telegram_ads_create_ad",
                "blocked_before_create": tool == "telegram_ads_create_ad",
                "media_ignored_by_placement": True,
                "recovery_hint": MEDIA_PLACEMENT_RECOVERY_HINT,
            },
        )

    async def _h_validate_ad(self, draft: dict[str, Any], **_: Any) -> dict[str, Any]:
        d = self._draft_from_dict(draft)
        unsupported = self._unsupported_media_for_target_type("telegram_ads_validate_ad", d)
        if unsupported:
            return unsupported
        adapter = await self._get_adapter()
        # Compute local policy violations without raising, for a full report.
        violations = (
            adapter.safety.validate_ad_text(d.text)
            + adapter.safety.validate_promote_url(d.promote_url)
            + adapter.safety.check_text_link_count(d.text)
        )
        if violations:
            result = CampaignValidationResult(
                valid=False, error="policy_violation", policy_violations=violations
            )
            data = result.model_dump(mode="json")
            data["creative"] = self._creative_summary(d, media_uploaded=False)
            return tool_ok("telegram_ads_validate_ad", data)
        raw = await adapter.validate_ad(d)
        result = CampaignValidationResult(
            valid=not raw.get("error"),
            field=raw.get("field", "") or "",
            error=raw.get("error", "") or "",
            preview=_clean(raw.get("preview_data")) if raw.get("preview_data") else None,
        )
        data = result.model_dump(mode="json")
        # validate_ad uploaded the media (if any) before checkAdPost.
        data["creative"] = self._creative_summary(
            d,
            media_uploaded=bool(d.media_path),
            actual_server_cpm=_server_cpm(raw.get("preview_data")),
            actual_ui_cpm_extra=_server_cpm_extra(raw.get("preview_data")),
        )
        return tool_ok("telegram_ads_validate_ad", data)

    async def _h_preview_ad(
        self, draft: dict[str, Any], screenshot_name: str | None = None, **_: Any
    ) -> dict[str, Any]:
        d = self._draft_from_dict(draft)
        unsupported = self._unsupported_media_for_target_type("telegram_ads_preview_ad", d)
        if unsupported:
            return unsupported
        adapter = await self._get_adapter()
        raw = await adapter.validate_ad(d)
        data: dict[str, Any] = {
            "valid": not raw.get("error"),
            "field": raw.get("field", "") or "",
            "error": raw.get("error", "") or "",
            "preview": _clean(raw.get("preview_data")) if raw.get("preview_data") else None,
            "screenshot": None,
            "creative": self._creative_summary(
                d,
                media_uploaded=bool(d.media_path),
                actual_server_cpm=_server_cpm(raw.get("preview_data")),
                actual_ui_cpm_extra=_server_cpm_extra(raw.get("preview_data")),
            ),
        }
        try:
            path = await adapter.screenshot(name=screenshot_name or "ad_preview.png", full_page=True)
            data["screenshot"] = ScreenshotArtifact(
                path=str(path), full_page=True, label="preview"
            ).model_dump(mode="json")
        except Exception:
            pass
        return tool_ok("telegram_ads_preview_ad", data)

    async def _h_save_ad_draft(self, draft: dict[str, Any], **_: Any) -> dict[str, Any]:
        adapter = await self._get_adapter()
        d = self._draft_from_dict(draft)
        raw = await adapter.save_ad_draft(d)
        return tool_ok("telegram_ads_save_ad_draft", {"saved": bool(raw.get("ok")), "result": _clean(raw)})

    async def _h_prepare_ad_draft(
        self, draft: dict[str, Any], screenshot_name: str | None = None, **_: Any
    ) -> dict[str, Any]:
        adapter = await self._get_adapter()
        d = self._draft_from_dict(draft)
        raw = await adapter.prepare_ad_draft(d, screenshot_name=screenshot_name)
        return tool_ok("telegram_ads_prepare_ad_draft", _clean(raw))

    async def _h_upload_media(self, file_path: str, **_: Any) -> dict[str, Any]:
        adapter = await self._get_adapter()
        token = await adapter.upload_media(file_path)
        # The media token is an opaque upload handle (not an auth credential);
        # the agent needs it to reference media in an edit_ad draft.
        return tool_ok(
            "telegram_ads_upload_media",
            {"media_token": token, "token_length": len(token), "file_path": file_path},
        )

    async def _h_duplicate_ad(self, ad_id: int, **_: Any) -> dict[str, Any]:
        adapter = await self._get_adapter()
        raw = await adapter.create_similar_draft(ad_id)
        return tool_ok(
            "telegram_ads_duplicate_ad",
            {"source_ad_id": ad_id, "draft_created": bool(raw.get("ok", True)), "result": _clean(raw)},
        )

    async def _h_estimate_cpm(self, draft: dict[str, Any] | None = None, **_: Any) -> dict[str, Any]:
        """Estimate the effective CPM after the creative modifiers Telegram applies.

        Offline + deterministic: there is no server audience-CPM endpoint, so this
        only multiplies the base CPM by the declared UI surcharges (show picture
        +30%, custom emoji +50%, photo +50%, video +80%). With more than one
        modifier the result is flagged ``needs_validation`` — the live UI is
        authoritative. Never mutates the draft's CPM.
        """
        tool = "telegram_ads_estimate_cpm"
        if not draft:
            return tool_failure(tool, "invalid_input", "estimate_cpm requires a draft with at least cpm.")
        d = self._draft_from_dict(draft)
        return tool_ok(tool, self._creative_summary(d))

    async def _h_prepare_campaign_from_brief(self, brief: dict[str, Any], **_: Any) -> dict[str, Any]:
        # Deterministic + offline: shape a brief into a CampaignDraft + local policy checks.
        draft = CampaignDraft.model_validate(
            {
                "title": brief.get("title", "")[:40] or "untitled",
                "text": brief.get("text", ""),
                "promote_url": brief.get("promote_url", ""),
                "cpm": float(brief.get("cpm", 0) or 0),
                "budget": float(brief.get("budget", 0) or 0),
                "target_type": brief.get("target_type", "search"),
                "targets": list(brief.get("targets", []) or []),
                "views_per_user": int(brief.get("views_per_user", 1) or 1),
                "website_name": brief.get("website_name"),
                "media_path": brief.get("media_path"),
                "initial_active": False,
            }
        )
        safety = self._safety()
        violations = (
            safety.validate_ad_text(draft.text)
            + safety.validate_promote_url(draft.promote_url)
            + safety.check_text_link_count(draft.text)
        )
        return tool_ok(
            "telegram_ads_prepare_campaign_from_brief",
            {
                "draft": draft.model_dump(mode="json"),
                "policy_violations": violations,
                "ready_for_validation": not violations,
                "note": "Draft only — nothing was submitted. Validate, then request approval to create.",
            },
        )

    async def _h_prepare_copy_variants(
        self, variants: list[str], promote_url: str | None = None, **_: Any
    ) -> dict[str, Any]:
        safety = self._safety()
        checks: list[CopyVariantCheck] = []
        for text in variants:
            v = safety.validate_ad_text(text) + safety.check_text_link_count(text)
            if promote_url:
                v = v + safety.validate_promote_url(promote_url)
            checks.append(CopyVariantCheck(text=text, valid=not v, violations=v))
        return tool_ok(
            "telegram_ads_prepare_copy_variants",
            {
                "variants": [c.model_dump(mode="json") for c in checks],
                "valid_count": sum(1 for c in checks if c.valid),
            },
        )

    async def _h_prepare_targeting(
        self,
        target_type: str,
        targets: list[str],
        target_countries: list[str] | None = None,
        currency: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        warnings: list[str] = []
        errors: list[str] = []
        if target_type not in ("channels", "bots", "search"):
            errors.append(f"target_type must be channels|bots|search, got {target_type!r}")
        if not targets:
            errors.append("targets must be a non-empty list")
        # TON cabinet geo hard-block check (deterministic, offline).
        if currency and currency.upper() == "TON" and target_countries:
            try:
                self._safety().check_ton_geo_safe({c.upper() for c in target_countries})
            except GeoTargetingError as e:
                errors.append(e.message)
        return tool_ok(
            "telegram_ads_prepare_targeting",
            {
                "target_type": target_type,
                "targets": targets,
                "target_countries": target_countries or [],
                "valid": not errors,
                "errors": errors,
                "warnings": warnings,
                "note": "Targeting is immutable after an ad is created — finalize before create_ad.",
            },
        )

    async def _h_prepare_approval_request(
        self, tool: str, params: dict[str, Any] | None = None, **_: Any
    ) -> dict[str, Any]:
        spec = TOOLS_BY_NAME.get(tool)
        if spec is None:
            return tool_failure(
                "telegram_ads_prepare_approval_request", "invalid_input", f"Unknown tool: {tool!r}"
            )
        if not spec.requires_approval:
            return tool_failure(
                "telegram_ads_prepare_approval_request",
                "invalid_input",
                f"Tool {tool!r} does not require approval (safety_class={spec.safety_class.value}).",
            )
        # Re-enter the target tool with NO confirmation -> it issues + returns approval_required.
        return await self.call(tool, **(params or {}))

    # ════════════════════════════════════════════════════════════════════════
    # E. Mutating lifecycle actions (single confirmation)
    # ════════════════════════════════════════════════════════════════════════

    async def _h_create_ad(
        self, draft: dict[str, Any], confirmation_id: str | None = None, **_: Any
    ) -> dict[str, Any]:
        tool = "telegram_ads_create_ad"
        d = self._draft_from_dict(draft)
        unsupported = self._unsupported_media_for_target_type(tool, d)
        if unsupported:
            return unsupported
        adapter = await self._get_adapter()
        # Hard policy check up front (raises PolicyViolationError -> envelope).
        adapter.safety.raise_if_policy_violations(d.text, d.promote_url)
        params = {"draft": draft}
        confirmation_id, conf = self._resolve_confirmation(
            confirmation_id, lambda: adapter.issue_create_ad_confirmation(d)
        )
        if conf is not None:
            creative = self._creative_summary(d)
            mods = "+".join(creative["modifiers_applied"]) or "none"
            media_desc = creative["media_type"] or "none"
            human_summary = (
                f"Submit NEW ad {d.title!r} to moderation. "
                f"base_cpm={d.cpm}, est_effective_cpm={creative['estimated_effective_cpm']} "
                f"(modifiers: {mods}; {'ESTIMATE — verify in UI' if creative['needs_validation'] else creative['modifier_confidence']}), "
                f"show_picture={d.show_picture}, media={media_desc} (uploaded on submit), "
                f"custom_emoji={creative['custom_emoji_detected']}, budget={d.budget}."
            )
            return self._issue_single(
                tool=tool,
                action=METHOD_CREATE_AD,
                conf=conf,
                params=params,
                human_summary=human_summary,
            )
        raw = await adapter.create_ad(d, confirmation_id=confirmation_id)
        return self._finish_mutation(
            tool=tool, action=METHOD_CREATE_AD, confirmation_id=confirmation_id, raw=raw
        )

    async def _h_edit_ad(
        self, draft: dict[str, Any], confirmation_id: str | None = None, **_: Any
    ) -> dict[str, Any]:
        tool = "telegram_ads_edit_ad"
        adapter = await self._get_adapter()
        d = EditAdDraft.model_validate(draft)
        params = {"draft": draft}
        confirmation_id, conf = self._resolve_confirmation(
            confirmation_id, lambda: adapter.issue_edit_ad_confirmation(d)
        )
        if conf is not None:
            return self._issue_single(
                tool=tool,
                action=METHOD_EDIT_AD,
                conf=conf,
                params=params,
                human_summary=f"Edit live ad {d.ad_id} (triggers re-review).",
            )
        raw = await adapter.edit_ad(d, confirmation_id=confirmation_id)
        return self._finish_mutation(
            tool=tool, action=METHOD_EDIT_AD, confirmation_id=confirmation_id, raw=raw
        )

    async def _h_start_ad(self, ad_id: int, confirmation_id: str | None = None, **_: Any) -> dict[str, Any]:
        return await self._change_status("telegram_ads_start_ad", ad_id, True, confirmation_id)

    async def _h_stop_ad(self, ad_id: int, confirmation_id: str | None = None, **_: Any) -> dict[str, Any]:
        return await self._change_status("telegram_ads_stop_ad", ad_id, False, confirmation_id)

    async def _change_status(
        self, tool: str, ad_id: int, active: bool, confirmation_id: str | None
    ) -> dict[str, Any]:
        adapter = await self._get_adapter()
        params = {"ad_id": ad_id}
        confirmation_id, conf = self._resolve_confirmation(
            confirmation_id,
            lambda: adapter.issue_change_status_confirmation(ad_id, active=active),
        )
        if conf is not None:
            verb = "START (resume)" if active else "STOP (pause)"
            return self._issue_single(
                tool=tool,
                action=METHOD_EDIT_AD_STATUS,
                conf=conf,
                params=params,
                human_summary=f"{verb} ad {ad_id}.",
            )
        raw = await adapter.change_status(ad_id, active=active, confirmation_id=confirmation_id)
        return self._finish_mutation(
            tool=tool, action=METHOD_EDIT_AD_STATUS, confirmation_id=confirmation_id, raw=raw
        )

    async def _h_change_cpm(
        self, ad_id: int, new_cpm: float, confirmation_id: str | None = None, **_: Any
    ) -> dict[str, Any]:
        tool = "telegram_ads_change_cpm"
        adapter = await self._get_adapter()
        if new_cpm <= 0:
            return tool_failure(tool, "invalid_input", "new_cpm must be > 0")
        params = {"ad_id": ad_id, "new_cpm": new_cpm}
        confirmation_id, conf = self._resolve_confirmation(
            confirmation_id, lambda: adapter.issue_change_cpm_confirmation(ad_id, new_cpm)
        )
        if conf is not None:
            return self._issue_single(
                tool=tool,
                action=METHOD_EDIT_AD_CPM,
                conf=conf,
                params=params,
                human_summary=f"Change CPM of ad {ad_id} to {new_cpm}.",
            )
        raw = await adapter.change_cpm(ad_id, new_cpm, confirmation_id=confirmation_id)
        return self._finish_mutation(
            tool=tool, action=METHOD_EDIT_AD_CPM, confirmation_id=confirmation_id, raw=raw
        )

    async def _h_add_to_budget(
        self, ad_id: int, amount: float, confirmation_id: str | None = None, **_: Any
    ) -> dict[str, Any]:
        tool = "telegram_ads_add_to_budget"
        adapter = await self._get_adapter()
        if amount <= 0:
            return tool_failure(tool, "invalid_input", "amount must be > 0")
        params = {"ad_id": ad_id, "amount": amount}
        confirmation_id, conf = self._resolve_confirmation(
            confirmation_id, lambda: adapter.issue_add_budget_confirmation(ad_id, amount)
        )
        if conf is not None:
            return self._issue_single(
                tool=tool,
                action=METHOD_INCR_AD_BUDGET,
                conf=conf,
                params=params,
                human_summary=f"Add {amount} to budget of ad {ad_id}.",
            )
        raw = await adapter.add_to_budget(ad_id, amount, confirmation_id=confirmation_id)
        return self._finish_mutation(
            tool=tool, action=METHOD_INCR_AD_BUDGET, confirmation_id=confirmation_id, raw=raw
        )

    async def _h_withdraw_from_budget(
        self, ad_id: int, amount: float, confirmation_id: str | None = None, **_: Any
    ) -> dict[str, Any]:
        tool = "telegram_ads_withdraw_from_budget"
        adapter = await self._get_adapter()
        if amount <= 0:
            return tool_failure(tool, "invalid_input", "amount must be > 0")
        params = {"ad_id": ad_id, "amount": amount}
        confirmation_id, conf = self._resolve_confirmation(
            confirmation_id, lambda: adapter.issue_withdraw_budget_confirmation(ad_id, amount)
        )
        if conf is not None:
            return self._issue_single(
                tool=tool,
                action=METHOD_DECR_AD_BUDGET,
                conf=conf,
                params=params,
                human_summary=f"Withdraw {amount} from budget of ad {ad_id}.",
            )
        raw = await adapter.withdraw_from_budget(ad_id, amount, confirmation_id=confirmation_id)
        return self._finish_mutation(
            tool=tool, action=METHOD_DECR_AD_BUDGET, confirmation_id=confirmation_id, raw=raw
        )

    async def _h_create_event(
        self, title: str, event_type: str, confirmation_id: str | None = None, **_: Any
    ) -> dict[str, Any]:
        tool = "telegram_ads_create_event"
        adapter = await self._get_adapter()
        params = {"title": title, "event_type": event_type}
        confirmation_id, conf = self._resolve_confirmation(
            confirmation_id, lambda: adapter.issue_create_event_confirmation(title, event_type)
        )
        if conf is not None:
            return self._issue_single(
                tool=tool,
                action=METHOD_CREATE_EVENT,
                conf=conf,
                params=params,
                human_summary=f"Create pixel conversion event {title!r} (type={event_type}).",
            )
        raw = await adapter.create_event(title=title, event_type=event_type, confirmation_id=confirmation_id)
        return self._finish_mutation(
            tool=tool, action=METHOD_CREATE_EVENT, confirmation_id=confirmation_id, raw=raw
        )

    # ════════════════════════════════════════════════════════════════════════
    # E'. Destructive lifecycle actions (double confirmation)
    # ════════════════════════════════════════════════════════════════════════

    async def _h_delete_ad(
        self,
        ad_id: int,
        confirmation_id: str | None = None,
        second_confirmation_id: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        tool = "telegram_ads_delete_ad"
        adapter = await self._get_adapter()
        params = {"ad_id": ad_id}
        confirmation_id, second_confirmation_id, c1, c2 = self._resolve_double_confirmation(
            confirmation_id,
            second_confirmation_id,
            lambda: adapter.issue_delete_ad_confirmations(ad_id),
        )
        if c1 is not None and c2 is not None:
            return self._issue_double(
                tool=tool,
                action=METHOD_DELETE_AD,
                conf1=c1,
                conf2=c2,
                params=params,
                human_summary=f"PERMANENTLY DELETE ad {ad_id}. This cannot be undone.",
            )
        raw = await adapter.delete_ad(
            ad_id, confirmation_id=confirmation_id, second_confirmation_id=second_confirmation_id
        )
        return self._finish_mutation(
            tool=tool, action=METHOD_DELETE_AD, confirmation_id=confirmation_id, raw=raw
        )

    async def _h_delete_event(
        self,
        event_id: str,
        confirmation_id: str | None = None,
        second_confirmation_id: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        tool = "telegram_ads_delete_event"
        adapter = await self._get_adapter()
        params = {"event_id": event_id}
        confirmation_id, second_confirmation_id, c1, c2 = self._resolve_double_confirmation(
            confirmation_id,
            second_confirmation_id,
            lambda: adapter.issue_delete_event_confirmations(event_id),
        )
        if c1 is not None and c2 is not None:
            return self._issue_double(
                tool=tool,
                action=METHOD_DELETE_EVENT,
                conf1=c1,
                conf2=c2,
                params=params,
                human_summary=f"PERMANENTLY DELETE pixel event {event_id}. This cannot be undone.",
            )
        raw = await adapter.delete_event(
            event_id, confirmation_id=confirmation_id, second_confirmation_id=second_confirmation_id
        )
        return self._finish_mutation(
            tool=tool, action=METHOD_DELETE_EVENT, confirmation_id=confirmation_id, raw=raw
        )

    async def _h_revoke_share_stats_url(
        self,
        ad_id: int,
        confirmation_id: str | None = None,
        second_confirmation_id: str | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        tool = "telegram_ads_revoke_share_stats_url"
        adapter = await self._get_adapter()
        params = {"ad_id": ad_id}
        confirmation_id, second_confirmation_id, c1, c2 = self._resolve_double_confirmation(
            confirmation_id,
            second_confirmation_id,
            lambda: adapter.issue_revoke_stats_url_confirmations(ad_id),
        )
        if c1 is not None and c2 is not None:
            return self._issue_double(
                tool=tool,
                action=METHOD_REVOKE_STATS_URL,
                conf1=c1,
                conf2=c2,
                params=params,
                human_summary=f"Revoke (rotate) the public share-stats URL for ad {ad_id}. Old link stops working.",
            )
        raw = await adapter.revoke_share_stats_url(
            ad_id, confirmation_id=confirmation_id, second_confirmation_id=second_confirmation_id
        )
        return self._finish_mutation(
            tool=tool, action=METHOD_REVOKE_STATS_URL, confirmation_id=confirmation_id, raw=raw
        )

    # ════════════════════════════════════════════════════════════════════════
    # E''. Stubs for capabilities the underlying tool does not support
    # ════════════════════════════════════════════════════════════════════════

    async def _h_set_budget(
        self, ad_id: int | None = None, amount: float | None = None, **_: Any
    ) -> dict[str, Any]:
        return tool_not_implemented(
            "telegram_ads_set_budget",
            "set_budget",
            message=(
                "Absolute budget set is not supported. Use telegram_ads_add_to_budget / "
                "telegram_ads_withdraw_from_budget to reach the target amount."
            ),
        )

    async def _h_archive_ad(self, ad_id: int | None = None, **_: Any) -> dict[str, Any]:
        return tool_not_implemented(
            "telegram_ads_archive_ad",
            "archive_ad",
            message="ads.telegram.org has no archive action. Use telegram_ads_stop_ad, or delete_ad to remove.",
        )

    async def _h_set_schedule(
        self, ad_id: int | None = None, schedule: Any = None, **_: Any
    ) -> dict[str, Any]:
        return tool_not_implemented(
            "telegram_ads_set_schedule",
            "set_schedule",
            message=(
                "Standalone schedule editing is not exposed. Provide weekly_schedule/activate_at "
                "in the draft for telegram_ads_create_ad instead."
            ),
        )

    async def _h_set_targeting(self, ad_id: int | None = None, **_: Any) -> dict[str, Any]:
        return tool_not_implemented(
            "telegram_ads_set_targeting",
            "set_targeting",
            message=(
                "Targeting is immutable after ad creation on ads.telegram.org. Use "
                "telegram_ads_prepare_targeting before create, or duplicate the ad with new targeting."
            ),
        )

    async def _h_set_conversion_event(
        self, ad_id: int | None = None, event_id: str | None = None, **_: Any
    ) -> dict[str, Any]:
        return tool_not_implemented(
            "telegram_ads_set_conversion_event",
            "set_conversion_event",
            message=(
                "Conversion event can only be attached when creating a Stars website ad "
                "(conversion_event_id in the draft). Standalone change is unsupported."
            ),
        )

    async def _h_set_pixel(self, **_: Any) -> dict[str, Any]:
        return tool_forbidden(
            "telegram_ads_set_pixel",
            "set_pixel",
            message="createPixel is untested/unsupported and forbidden by safety policy.",
        )

    # ════════════════════════════════════════════════════════════════════════
    # F. Approval execution
    # ════════════════════════════════════════════════════════════════════════

    async def _h_apply_approved_action(self, confirmation_id: str, **_: Any) -> dict[str, Any]:
        tool = "telegram_ads_apply_approved_action"
        pa = self._approvals.get(confirmation_id)
        if pa is None:
            return tool_failure(
                tool,
                "invalid_confirmation",
                f"No pending approval for confirmation_id {confirmation_id!r}. "
                "Issue one via the mutating tool or telegram_ads_prepare_approval_request first.",
            )
        kwargs = dict(pa.params)
        kwargs["confirmation_id"] = pa.confirmation_id
        if pa.second_confirmation_id:
            kwargs["second_confirmation_id"] = pa.second_confirmation_id
        return await self.call(pa.tool, **kwargs)

    async def _h_get_pending_confirmations(self, **_: Any) -> dict[str, Any]:
        pending = [pa.to_view().model_dump(mode="json") for pa in self._approvals.list()]
        return tool_ok(
            "telegram_ads_get_pending_confirmations",
            {"pending": pending, "count": len(pending)},
        )

    async def _h_cancel_confirmation(self, confirmation_id: str, **_: Any) -> dict[str, Any]:
        tool = "telegram_ads_cancel_confirmation"
        pa = self._approvals.remove(confirmation_id)
        # Also drop the underlying safety confirmation(s) so they can't be reused.
        if self._adapter is not None:
            self._adapter.safety._pending.pop(confirmation_id, None)
            if pa and pa.second_confirmation_id:
                self._adapter.safety._pending.pop(pa.second_confirmation_id, None)
        return tool_ok(
            tool,
            {"cancelled": pa is not None, "confirmation_id": confirmation_id},
        )


# ─── Helpers ───────────────────────────────────────────────────────────────────


def _looks_like_phone(phone: str) -> bool:
    """Cheap sanity check: a leading '+' and 7–15 digits. Not a validator —
    just enough to reject obviously-bogus input before touching the form."""
    s = phone.strip()
    if not s.startswith("+"):
        return False
    digits = [c for c in s if c.isdigit()]
    return 7 <= len(digits) <= 15


def _trg_to_target_type(trg_type: str) -> str:
    return {"channel": "channels", "bot": "bots", "search": "search"}.get(trg_type, trg_type)


def _server_cpm(preview_data: Any) -> float | None:
    """Pull a server-reported CPM out of checkAdPost preview_data, if present.

    Telegram's preview usually does NOT echo an effective CPM, so this is most
    often None; when it does, it is the authoritative value (vs. our estimate).
    """
    if not isinstance(preview_data, dict):
        return None
    for key in ("cpm", "effective_cpm", "real_cpm"):
        v = preview_data.get(key)
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str):
            try:
                return float(v.replace(",", "").strip())
            except ValueError:
                continue
    return None


def _server_cpm_extra(preview_data: Any) -> str | None:
    """Pull the UI-authoritative creative CPM surcharge label (e.g. "+80%").

    checkAdPost echoes a ``cpm_extra`` label for the uploaded-media surcharge.
    When present it overrides the static local modifier estimate (which may be
    stale — a live channel photo showed +80%, not the assumed +50%).
    """
    if not isinstance(preview_data, dict):
        return None
    v = preview_data.get("cpm_extra")
    if isinstance(v, str) and v.strip():
        return v.strip()
    if isinstance(v, (int, float)):
        return f"+{v:g}%" if v >= 0 else f"{v:g}%"
    return None


def _parse_pct(label: str | None) -> float | None:
    """Parse a percent magnitude out of a label like "+80%" -> 80.0."""
    if not label:
        return None
    m = re.search(r"[-+]?\d+(?:\.\d+)?", label)
    return float(m.group()) if m else None


# ─── Registry ──────────────────────────────────────────────────────────────────

TELEGRAM_ADS_TOOLS: list[ToolSpec] = [
    # A. Session / browser / login
    ToolSpec(
        "telegram_ads_status",
        "Report tool/session state: launched, logged_in, current_url, profile dir. Never asks for login codes.",
        _obj({}),
        SafetyClass.SAFE_READ,
        "_h_status",
        group="session",
    ),
    ToolSpec(
        "telegram_ads_ensure_login",
        "Ensure the Telegram Ads session is valid. Returns login_required (and stops) if a human must log in.",
        _obj({}),
        SafetyClass.SAFE_READ,
        "_h_ensure_login",
        group="session",
    ),
    ToolSpec(
        "telegram_ads_login_assist",
        "Return human-facing instructions for restoring the manual Telegram login. Does not enter codes.",
        _obj({}),
        SafetyClass.SAFE_READ,
        "_h_login_assist",
        group="session",
    ),
    ToolSpec(
        "telegram_ads_login_check",
        (
            "Read-only deterministic login/session detection. Navigates to /account "
            "and returns the structured state (logged_in | auth_page | phone_required "
            "| app_approval_pending | code_required | unknown) with recovery_hint and "
            "requires_human_login. Submits no phone/code, never mutates."
        ),
        _obj({}),
        SafetyClass.SAFE_READ,
        "_h_login_check",
        group="session",
    ),
    ToolSpec(
        "telegram_ads_login_start",
        (
            "Open the Telegram Ads auth page and begin login. SENSITIVE account access: "
            "requires an explicit human approval (issues approval_required first; apply "
            "via telegram_ads_apply_approved_action). May trigger a login-approval prompt "
            "in the Telegram app. Never submits a phone number or OTP code. If already "
            "logged in, returns ok without requiring approval."
        ),
        _obj({"confirmation_id": _STR}),
        SafetyClass.SENSITIVE_ACCOUNT_ACCESS,
        "_h_login_start",
        requires_approval=True,
        group="session",
        returns="LoginSessionState",
    ),
    ToolSpec(
        "telegram_ads_login_submit_phone",
        (
            "Submit a phone number to the Telegram Ads login form. SENSITIVE account "
            "access: requires an explicit human approval AND the phone supplied in the "
            "call. The phone is never logged or persisted in the clear (only masked); the "
            "agent never enters an OTP code — prefer Telegram-app approval."
        ),
        _obj({"phone": _STR, "confirmation_id": _STR}, ("phone",)),
        SafetyClass.SENSITIVE_ACCOUNT_ACCESS,
        "_h_login_submit_phone",
        requires_approval=True,
        group="session",
        returns="LoginSessionState",
    ),
    ToolSpec(
        "telegram_ads_login_submit_code",
        (
            "Submit the ads.telegram.org OTP the operator confirmed in chat. "
            "SENSITIVE: first call returns approval_required; apply with the same code. "
            "The code is never logged. On success the Chromium profile keeps the session."
        ),
        _obj({"code": _STR, "confirmation_id": _STR}, ("code",)),
        SafetyClass.SENSITIVE_ACCOUNT_ACCESS,
        "_h_login_submit_code",
        requires_approval=True,
        group="session",
        returns="LoginSessionState",
    ),
    ToolSpec(
        "telegram_ads_login_wait",
        (
            "Poll the browser until logged in or timeout. Read-only: submits no phone/code "
            "and never mutates. Returns logged_in on success or a login_required envelope "
            "with state=timeout when the wait window expires."
        ),
        _obj({"timeout_sec": _NUM, "poll_interval_sec": _NUM}),
        SafetyClass.SAFE_READ,
        "_h_login_wait",
        group="session",
        returns="LoginSessionState",
    ),
    ToolSpec(
        "telegram_ads_login_from_env",
        (
            "Authorize ads.telegram.org using TELEGRAM_ADS_PHONE from the host .env. "
            "Types the number (never invents one, never prints it unmasked), tells the "
            "operator it was entered, and leaves the funnel on app-approval. Then tell "
            "the operator to tap Accept in Telegram and call telegram_ads_login_wait. "
            "The persistent Chromium profile keeps the cabinet session after Accept."
        ),
        _obj({}),
        SafetyClass.SENSITIVE_ACCOUNT_ACCESS,
        "_h_login_from_env",
        group="session",
        returns="LoginSessionState",
    ),
    ToolSpec(
        "telegram_ads_open_dashboard",
        "Navigate to the /account dashboard and return the resulting URL.",
        _obj({}),
        SafetyClass.SAFE_READ,
        "_h_open_dashboard",
        group="session",
    ),
    ToolSpec(
        "telegram_ads_current_page",
        "Return the current browser URL.",
        _obj({}),
        SafetyClass.SAFE_READ,
        "_h_current_page",
        group="session",
    ),
    ToolSpec(
        "telegram_ads_save_screenshot",
        "Capture a screenshot of the current page and return its artifact path.",
        _obj({"screenshot_name": _STR, "full_page": _BOOL}),
        SafetyClass.SAFE_READ,
        "_h_save_screenshot",
        group="session",
    ),
    ToolSpec(
        "telegram_ads_get_browser_profile_info",
        "Return non-sensitive browser/profile info (profile dir, headless, viewport, session_active). No cookies.",
        _obj({}),
        SafetyClass.SAFE_READ,
        "_h_get_browser_profile_info",
        group="session",
    ),
    ToolSpec(
        "telegram_ads_recover_browser_session",
        (
            "Recover a broken browser page/context in place after a transient "
            "Playwright error (net::ERR_ABORTED, frame detached, target/page/context "
            "closed). Read-only: rebuilds the Chromium context from the persistent "
            "profile, performs no ads actions, enters no login codes. Returns "
            "browser_state and structured error if recovery fails."
        ),
        _obj({}),
        SafetyClass.SAFE_READ,
        "_h_recover_browser_session",
        group="session",
    ),
    # B. Accounts / cabinets
    ToolSpec(
        "telegram_ads_list_accounts",
        "List all Telegram Ads cabinets (token masked, opaque account_ref for selection).",
        _obj({}),
        SafetyClass.SAFE_READ,
        "_h_list_accounts",
        group="accounts",
    ),
    ToolSpec(
        "telegram_ads_choose_account",
        "Switch the active cabinet by account_ref (from telegram_ads_list_accounts).",
        _obj({"account_ref": _STR}, ("account_ref",)),
        SafetyClass.SAFE_READ,
        "_h_choose_account",
        group="accounts",
    ),
    ToolSpec(
        "telegram_ads_current_account",
        "Return the currently selected cabinet.",
        _obj({}),
        SafetyClass.SAFE_READ,
        "_h_current_account",
        group="accounts",
    ),
    ToolSpec(
        "telegram_ads_get_account_budget",
        "Return the active cabinet balance, currency, and recent transactions.",
        _obj({}),
        SafetyClass.SAFE_READ,
        "_h_get_account_budget",
        group="accounts",
    ),
    ToolSpec(
        "telegram_ads_snapshot_accounts",
        "Composite: ensure login, list cabinets, for each collect budget + campaigns + screenshot, save JSON summary.",
        _obj({"full_page": _BOOL, "max_campaigns_per_account": _INT}),
        SafetyClass.SAFE_READ,
        "_h_snapshot_accounts",
        group="accounts",
        returns="AccountsSnapshotResult",
    ),
    # C. Campaign / ad read
    ToolSpec(
        "telegram_ads_list_ads",
        "List ads (campaigns) in the active cabinet.",
        _obj({}),
        SafetyClass.SAFE_READ,
        "_h_list_ads",
        group="read",
    ),
    ToolSpec(
        "telegram_ads_get_ad",
        "Get full read view of one ad: creative, targeting, budget status, rejection (if declined).",
        _obj({"ad_id": _INT}, ("ad_id",)),
        SafetyClass.SAFE_READ,
        "_h_get_ad",
        group="read",
        returns="CampaignSnapshot",
    ),
    ToolSpec(
        "telegram_ads_get_ad_stats",
        "Get monthly stats for an ad (views, amounts, CSV availability).",
        _obj({"ad_id": _INT}, ("ad_id",)),
        SafetyClass.SAFE_READ,
        "_h_get_ad_stats",
        group="read",
        returns="CampaignStats",
    ),
    ToolSpec(
        "telegram_ads_get_ad_creative",
        "Get the creative surface of an ad (title, text, link, media presence).",
        _obj({"ad_id": _INT}, ("ad_id",)),
        SafetyClass.SAFE_READ,
        "_h_get_ad_creative",
        group="read",
        returns="CampaignCreative",
    ),
    ToolSpec(
        "telegram_ads_get_ad_targeting",
        "Get targeting info recoverable from the ad detail page.",
        _obj({"ad_id": _INT}, ("ad_id",)),
        SafetyClass.SAFE_READ,
        "_h_get_ad_targeting",
        group="read",
        returns="CampaignTargeting",
    ),
    ToolSpec(
        "telegram_ads_get_ad_budget_status",
        "Get live budget/lifecycle status of an ad (status, cpm, budget, spent).",
        _obj({"ad_id": _INT}, ("ad_id",)),
        SafetyClass.SAFE_READ,
        "_h_get_ad_budget_status",
        group="read",
        returns="CampaignBudgetStatus",
    ),
    ToolSpec(
        "telegram_ads_get_rejection_info",
        "Return the raw decline reason for an ad (category, description, link) if declined.",
        _obj({"ad_id": _INT}, ("ad_id",)),
        SafetyClass.SAFE_READ,
        "_h_get_rejection_info",
        group="read",
        returns="RejectionAnalysis",
    ),
    ToolSpec(
        "telegram_ads_explain_rejection",
        "Explain why an ad was declined and suggest concrete fixes.",
        _obj({"ad_id": _INT}, ("ad_id",)),
        SafetyClass.SAFE_READ,
        "_h_explain_rejection",
        group="read",
        returns="RejectionAnalysis",
    ),
    ToolSpec(
        "telegram_ads_download_report",
        "Download a monthly CSV report for an ad (ad_id given) or the whole account. month=YYYYMM.",
        _obj({"month": _STR, "ad_id": _INT}, ("month",)),
        SafetyClass.SAFE_READ,
        "_h_download_report",
        group="read",
        returns="ReportArtifact",
    ),
    ToolSpec(
        "telegram_ads_get_share_stats_url",
        "Return the public share-stats URL for an ad (read-only; does not create or rotate it).",
        _obj({"ad_id": _INT}, ("ad_id",)),
        SafetyClass.SAFE_READ,
        "_h_get_share_stats_url",
        group="read",
    ),
    ToolSpec(
        "telegram_ads_list_events",
        "List pixel conversion events (Stars cabinets).",
        _obj({}),
        SafetyClass.SAFE_READ,
        "_h_list_events",
        group="read",
    ),
    ToolSpec(
        "telegram_ads_get_event_log",
        "Read the recent activity log for a pixel event.",
        _obj({"event_id": _STR}, ("event_id",)),
        SafetyClass.SAFE_READ,
        "_h_get_event_log",
        group="read",
    ),
    ToolSpec(
        "telegram_ads_get_pixel_snippet",
        "Return the pixel base snippet, or per-event snippet if event_id is given.",
        _obj({"event_id": _STR}),
        SafetyClass.SAFE_READ,
        "_h_get_pixel_snippet",
        group="read",
    ),
    ToolSpec(
        "telegram_ads_get_ad_events",
        "[not implemented] Per-ad event feed; use list_events / get_event_log instead.",
        _obj({"ad_id": _INT}),
        SafetyClass.SAFE_READ,
        "_h_get_ad_events",
        group="read",
    ),
    # D. Draft / preparation
    ToolSpec(
        "telegram_ads_validate_ad",
        "Validate a draft via checkAdPost + local policy checks. Does not submit.",
        _obj({"draft": _DRAFT_SCHEMA}, ("draft",)),
        SafetyClass.DRAFT,
        "_h_validate_ad",
        group="draft",
        returns="CampaignValidationResult",
    ),
    ToolSpec(
        "telegram_ads_preview_ad",
        "Render the ad preview (checkAdPost preview_data) and optionally a screenshot. Does not submit.",
        _obj({"draft": _DRAFT_SCHEMA, "screenshot_name": _STR}, ("draft",)),
        SafetyClass.DRAFT,
        "_h_preview_ad",
        group="draft",
    ),
    ToolSpec(
        "telegram_ads_save_ad_draft",
        "Save a draft on the server (no submission to moderation).",
        _obj({"draft": _DRAFT_SCHEMA}, ("draft",)),
        SafetyClass.DRAFT,
        "_h_save_ad_draft",
        group="draft",
    ),
    ToolSpec(
        "telegram_ads_prepare_ad_draft",
        "Validate + save a draft + take a preview screenshot. Does not submit.",
        _obj({"draft": _DRAFT_SCHEMA, "screenshot_name": _STR}, ("draft",)),
        SafetyClass.DRAFT,
        "_h_prepare_ad_draft",
        group="draft",
    ),
    ToolSpec(
        "telegram_ads_upload_media",
        "Upload a local media file (16:9), returning an opaque media token for use in an edit draft.",
        _obj({"file_path": _STR}, ("file_path",)),
        SafetyClass.DRAFT,
        "_h_upload_media",
        group="draft",
    ),
    ToolSpec(
        "telegram_ads_duplicate_ad",
        "Clone an existing ad into a new draft (Create similar). Does not submit.",
        _obj({"ad_id": _INT}, ("ad_id",)),
        SafetyClass.DRAFT,
        "_h_duplicate_ad",
        group="draft",
    ),
    ToolSpec(
        "telegram_ads_estimate_cpm",
        (
            "Estimate the effective CPM after Telegram's creative-option surcharges "
            "(show picture +30%, custom emoji +50%, photo +50%, video +80%). Offline "
            "estimate from the draft; with >1 modifier it is flagged needs_validation "
            "(the UI is authoritative). No audience/market CPM endpoint exists."
        ),
        _obj({"draft": _DRAFT_SCHEMA}, ("draft",)),
        SafetyClass.DRAFT,
        "_h_estimate_cpm",
        group="draft",
    ),
    ToolSpec(
        "telegram_ads_prepare_campaign_from_brief",
        "Shape a free-form brief into a typed CampaignDraft and run local policy checks. Draft only.",
        _obj({"brief": {"type": "object", "additionalProperties": True}}, ("brief",)),
        SafetyClass.DRAFT,
        "_h_prepare_campaign_from_brief",
        group="draft",
        returns="CampaignDraft",
    ),
    ToolSpec(
        "telegram_ads_prepare_copy_variants",
        "Policy-check a list of ad copy variants and report which pass. Draft only.",
        _obj({"variants": {"type": "array", "items": _STR}, "promote_url": _STR}, ("variants",)),
        SafetyClass.DRAFT,
        "_h_prepare_copy_variants",
        group="draft",
    ),
    ToolSpec(
        "telegram_ads_prepare_targeting",
        "Validate/normalize targeting (type, targets, TON geo block). Draft only.",
        _obj(
            {
                "target_type": {"type": "string", "enum": ["channels", "bots", "search"]},
                "targets": {"type": "array", "items": _STR},
                "target_countries": {"type": "array", "items": _STR},
                "currency": {"type": "string", "enum": ["TON", "STARS"]},
            },
            ("target_type", "targets"),
        ),
        SafetyClass.DRAFT,
        "_h_prepare_targeting",
        group="draft",
    ),
    ToolSpec(
        "telegram_ads_prepare_approval_request",
        "Issue an approval request for any mutating tool (validates input, returns confirmation_id).",
        _obj({"tool": _STR, "params": {"type": "object", "additionalProperties": True}}, ("tool",)),
        SafetyClass.SAFE_READ,
        "_h_prepare_approval_request",
        group="approval",
    ),
    # E. Mutating lifecycle (single confirmation)
    ToolSpec(
        "telegram_ads_create_ad",
        "Submit a NEW ad to moderation. On Hermes Telegram this sends Once/Session/Always/Deny buttons; after Accept the same call executes. Do not ask the operator to type yes.",
        _obj({"draft": _DRAFT_SCHEMA, "confirmation_id": _STR}, ("draft",)),
        SafetyClass.APPROVAL_REQUIRED,
        "_h_create_ad",
        mutating=True,
        requires_approval=True,
        group="mutate",
        returns="ApprovedActionResult",
    ),
    ToolSpec(
        "telegram_ads_edit_ad",
        "Edit a live ad (triggers re-review). Sends Telegram Once/Session/Always/Deny buttons; do not ask the operator to type yes.",
        _obj(
            {"draft": {"type": "object", "additionalProperties": True}, "confirmation_id": _STR}, ("draft",)
        ),
        SafetyClass.APPROVAL_REQUIRED,
        "_h_edit_ad",
        mutating=True,
        requires_approval=True,
        group="mutate",
        returns="ApprovedActionResult",
    ),
    ToolSpec(
        "telegram_ads_start_ad",
        "Start/resume an ad. Sends Telegram Once/Session/Always/Deny buttons; do not ask the operator to type yes.",
        _obj({"ad_id": _INT, "confirmation_id": _STR}, ("ad_id",)),
        SafetyClass.APPROVAL_REQUIRED,
        "_h_start_ad",
        mutating=True,
        requires_approval=True,
        group="mutate",
        returns="ApprovedActionResult",
    ),
    ToolSpec(
        "telegram_ads_stop_ad",
        "Stop/pause an ad. Sends Telegram Once/Session/Always/Deny buttons; do not ask the operator to type yes.",
        _obj({"ad_id": _INT, "confirmation_id": _STR}, ("ad_id",)),
        SafetyClass.APPROVAL_REQUIRED,
        "_h_stop_ad",
        mutating=True,
        requires_approval=True,
        group="mutate",
        returns="ApprovedActionResult",
    ),
    ToolSpec(
        "telegram_ads_change_cpm",
        "Change an ad's CPM. Sends Telegram Once/Session/Always/Deny buttons; do not ask the operator to type yes.",
        _obj({"ad_id": _INT, "new_cpm": _NUM, "confirmation_id": _STR}, ("ad_id", "new_cpm")),
        SafetyClass.APPROVAL_REQUIRED,
        "_h_change_cpm",
        mutating=True,
        requires_approval=True,
        group="mutate",
        returns="ApprovedActionResult",
    ),
    ToolSpec(
        "telegram_ads_add_to_budget",
        "Add funds to an ad's budget. Sends Telegram Once/Session/Always/Deny buttons; do not ask the operator to type yes.",
        _obj({"ad_id": _INT, "amount": _NUM, "confirmation_id": _STR}, ("ad_id", "amount")),
        SafetyClass.APPROVAL_REQUIRED,
        "_h_add_to_budget",
        mutating=True,
        requires_approval=True,
        group="mutate",
        returns="ApprovedActionResult",
    ),
    ToolSpec(
        "telegram_ads_withdraw_from_budget",
        "Withdraw funds from an ad's budget. Sends Telegram Once/Session/Always/Deny buttons; 2-min cooldown after status changes. Do not ask the operator to type yes.",
        _obj({"ad_id": _INT, "amount": _NUM, "confirmation_id": _STR}, ("ad_id", "amount")),
        SafetyClass.APPROVAL_REQUIRED,
        "_h_withdraw_from_budget",
        mutating=True,
        requires_approval=True,
        group="mutate",
        returns="ApprovedActionResult",
    ),
    ToolSpec(
        "telegram_ads_create_event",
        "Create a pixel conversion event (Stars cabinet). Sends Telegram Once/Session/Always/Deny buttons; do not ask the operator to type yes.",
        _obj({"title": _STR, "event_type": _STR, "confirmation_id": _STR}, ("title", "event_type")),
        SafetyClass.APPROVAL_REQUIRED,
        "_h_create_event",
        mutating=True,
        requires_approval=True,
        group="mutate",
        returns="ApprovedActionResult",
    ),
    # E'. Destructive (double confirmation)
    ToolSpec(
        "telegram_ads_delete_ad",
        "PERMANENTLY delete an ad. Sends Telegram Once/Session/Always/Deny buttons; do not ask the operator to type yes.",
        _obj({"ad_id": _INT, "confirmation_id": _STR, "second_confirmation_id": _STR}, ("ad_id",)),
        SafetyClass.FORBIDDEN_OR_DOUBLE_CONFIRM,
        "_h_delete_ad",
        mutating=True,
        requires_approval=True,
        group="destructive",
        returns="ApprovedActionResult",
    ),
    ToolSpec(
        "telegram_ads_delete_event",
        "PERMANENTLY delete a pixel event. Sends Telegram Once/Session/Always/Deny buttons; do not ask the operator to type yes.",
        _obj({"event_id": _STR, "confirmation_id": _STR, "second_confirmation_id": _STR}, ("event_id",)),
        SafetyClass.FORBIDDEN_OR_DOUBLE_CONFIRM,
        "_h_delete_event",
        mutating=True,
        requires_approval=True,
        group="destructive",
        returns="ApprovedActionResult",
    ),
    ToolSpec(
        "telegram_ads_revoke_share_stats_url",
        "Rotate/revoke the public share-stats URL for an ad. Sends Telegram Once/Session/Always/Deny buttons; do not ask the operator to type yes.",
        _obj({"ad_id": _INT, "confirmation_id": _STR, "second_confirmation_id": _STR}, ("ad_id",)),
        SafetyClass.FORBIDDEN_OR_DOUBLE_CONFIRM,
        "_h_revoke_share_stats_url",
        mutating=True,
        requires_approval=True,
        group="destructive",
        returns="ApprovedActionResult",
    ),
    # E''. Stubs (not implemented / forbidden)
    ToolSpec(
        "telegram_ads_set_budget",
        "[not implemented] Use add_to_budget / withdraw_from_budget instead.",
        _obj({"ad_id": _INT, "amount": _NUM}),
        SafetyClass.APPROVAL_REQUIRED,
        "_h_set_budget",
        mutating=True,
        requires_approval=True,
        group="stub",
    ),
    ToolSpec(
        "telegram_ads_archive_ad",
        "[not implemented] No archive action; use stop_ad or delete_ad.",
        _obj({"ad_id": _INT}),
        SafetyClass.APPROVAL_REQUIRED,
        "_h_archive_ad",
        mutating=True,
        requires_approval=True,
        group="stub",
    ),
    ToolSpec(
        "telegram_ads_set_schedule",
        "[not implemented] Provide schedule in the create draft.",
        _obj({"ad_id": _INT, "schedule": {"type": "object", "additionalProperties": True}}),
        SafetyClass.APPROVAL_REQUIRED,
        "_h_set_schedule",
        mutating=True,
        requires_approval=True,
        group="stub",
    ),
    ToolSpec(
        "telegram_ads_set_targeting",
        "[not implemented] Targeting is immutable after creation.",
        _obj({"ad_id": _INT}),
        SafetyClass.APPROVAL_REQUIRED,
        "_h_set_targeting",
        mutating=True,
        requires_approval=True,
        group="stub",
    ),
    ToolSpec(
        "telegram_ads_set_conversion_event",
        "[not implemented] Attach conversion event only at creation of a Stars website ad.",
        _obj({"ad_id": _INT, "event_id": _STR}),
        SafetyClass.APPROVAL_REQUIRED,
        "_h_set_conversion_event",
        mutating=True,
        requires_approval=True,
        group="stub",
    ),
    ToolSpec(
        "telegram_ads_set_pixel",
        "[forbidden] createPixel is untested and blocked by safety policy.",
        _obj({}),
        SafetyClass.FORBIDDEN_OR_DOUBLE_CONFIRM,
        "_h_set_pixel",
        mutating=True,
        requires_approval=True,
        group="stub",
    ),
    # F. Approval execution
    ToolSpec(
        "telegram_ads_apply_approved_action",
        "Execute a previously-issued, human-approved mutating action by confirmation_id.",
        _obj({"confirmation_id": _STR}, ("confirmation_id",)),
        SafetyClass.APPROVAL_REQUIRED,
        "_h_apply_approved_action",
        mutating=True,
        requires_approval=True,
        group="approval",
        returns="ApprovedActionResult",
    ),
    ToolSpec(
        "telegram_ads_get_pending_confirmations",
        "List pending approval requests awaiting execution.",
        _obj({}),
        SafetyClass.SAFE_READ,
        "_h_get_pending_confirmations",
        group="approval",
    ),
    ToolSpec(
        "telegram_ads_cancel_confirmation",
        "Cancel a pending approval so it can no longer be applied.",
        _obj({"confirmation_id": _STR}, ("confirmation_id",)),
        SafetyClass.SAFE_READ,
        "_h_cancel_confirmation",
        group="approval",
    ),
]

TOOLS_BY_NAME: dict[str, ToolSpec] = {t.name: t for t in TELEGRAM_ADS_TOOLS}

# Tool-name groupings (handy for tests / docs / agent routing).
MUTATING_TOOLS: frozenset[str] = frozenset(t.name for t in TELEGRAM_ADS_TOOLS if t.mutating)
APPROVAL_FLOW_TOOLS: frozenset[str] = frozenset(
    {
        "telegram_ads_apply_approved_action",
        "telegram_ads_get_pending_confirmations",
        "telegram_ads_cancel_confirmation",
        "telegram_ads_prepare_approval_request",
    }
)
STUB_TOOLS: frozenset[str] = frozenset(t.name for t in TELEGRAM_ADS_TOOLS if t.group == "stub")


def tool_names() -> list[str]:
    return [t.name for t in TELEGRAM_ADS_TOOLS]


__all__ = [
    "APPROVAL_FLOW_TOOLS",
    "ApprovalRegistry",
    "MUTATING_TOOLS",
    "PendingAction",
    "STUB_TOOLS",
    "SafetyClass",
    "TELEGRAM_ADS_TOOLS",
    "TOOLS_BY_NAME",
    "TelegramAdsToolset",
    "ToolSpec",
    "tool_names",
]
