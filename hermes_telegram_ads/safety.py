"""Safety layer.

Hermes/skill cannot bypass this layer. Every mutating action must either:
    - have RiskLevel.SAFE_READ / DRAFT (free),
    - present a valid Confirmation matching action+params (RiskLevel.CONFIRM_REQUIRED),
    - present TWO valid Confirmations (RiskLevel.DOUBLE_CONFIRM_REQUIRED),
    - or be explicitly enabled via config (RiskLevel.FORBIDDEN by default).

Confirmation identity:
    confirmation_id (uuid4) tied to:
        - action name
        - sha256(params_canonical_json)
        - issued_at + TTL

Re-using a confirmation for a different action or modified params raises
InvalidConfirmationError.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from hermes_telegram_ads.config import SafetyConfig
from hermes_telegram_ads.constants import (
    MAX_AD_TEXT_LEN,
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
    TON_GEO_HARD_BLOCK,
)
from hermes_telegram_ads.errors import (
    ConfirmationRequiredError,
    ForbiddenActionError,
    GeoTargetingError,
    InvalidConfirmationError,
    PolicyViolationError,
)


class RiskLevel(str, Enum):
    SAFE_READ = "safe_read"
    DRAFT = "draft"
    CONFIRM_REQUIRED = "confirm_required"
    DOUBLE_CONFIRM_REQUIRED = "double_confirm_required"
    FORBIDDEN = "forbidden"


# Action → risk level map.
# Keys are action names; for API methods we use the method name directly.
ACTION_RISK_MAP: dict[str, RiskLevel] = {
    # ── SAFE_READ ────────────────────────────────────────────────────────────
    "open_dashboard": RiskLevel.SAFE_READ,
    "ensure_logged_in": RiskLevel.SAFE_READ,
    "detect_login_state": RiskLevel.SAFE_READ,
    "login_check": RiskLevel.SAFE_READ,
    "login_wait": RiskLevel.SAFE_READ,
    "list_accounts": RiskLevel.SAFE_READ,
    "get_current_account": RiskLevel.SAFE_READ,
    "choose_account": RiskLevel.SAFE_READ,
    "list_ads": RiskLevel.SAFE_READ,
    "get_ad": RiskLevel.SAFE_READ,
    "get_account_budget": RiskLevel.SAFE_READ,
    "get_account_stats": RiskLevel.SAFE_READ,
    "get_ad_stats": RiskLevel.SAFE_READ,
    "download_csv_report": RiskLevel.SAFE_READ,
    "screenshot": RiskLevel.SAFE_READ,
    "list_events": RiskLevel.SAFE_READ,
    "get_pixel_snippet": RiskLevel.SAFE_READ,
    "get_event_log": RiskLevel.SAFE_READ,
    "get_share_stats_url": RiskLevel.SAFE_READ,
    # API read methods
    "searchChannel": RiskLevel.SAFE_READ,
    "checkAdPost": RiskLevel.SAFE_READ,
    # ── DRAFT (writes but no irreversible effect) ─────────────────────────────
    "saveAdDraft": RiskLevel.DRAFT,
    "createDraftFromAd": RiskLevel.DRAFT,
    "prepare_ad_draft": RiskLevel.DRAFT,
    "upload_media": RiskLevel.DRAFT,
    # ── CONFIRM_REQUIRED ──────────────────────────────────────────────────────
    # Sensitive account-access (login): opening the auth widget can trigger a
    # Telegram-app approval prompt; submitting a phone touches the account. Both
    # require an explicit human approval before they run.
    "login_start": RiskLevel.CONFIRM_REQUIRED,
    "login_submit_phone": RiskLevel.CONFIRM_REQUIRED,
    # Operator-preauthorized via TELEGRAM_ADS_PHONE in host .env. Not a spend.
    "login_authorize_from_env": RiskLevel.DRAFT,
    "login_submit_code": RiskLevel.CONFIRM_REQUIRED,
    METHOD_CREATE_AD: RiskLevel.CONFIRM_REQUIRED,
    METHOD_EDIT_AD: RiskLevel.CONFIRM_REQUIRED,
    METHOD_EDIT_AD_CPM: RiskLevel.CONFIRM_REQUIRED,
    METHOD_EDIT_AD_STATUS: RiskLevel.CONFIRM_REQUIRED,
    METHOD_INCR_AD_BUDGET: RiskLevel.CONFIRM_REQUIRED,
    METHOD_DECR_AD_BUDGET: RiskLevel.CONFIRM_REQUIRED,
    METHOD_CREATE_EVENT: RiskLevel.CONFIRM_REQUIRED,
    # ── DOUBLE_CONFIRM_REQUIRED ──────────────────────────────────────────────
    METHOD_DELETE_AD: RiskLevel.DOUBLE_CONFIRM_REQUIRED,
    METHOD_DELETE_EVENT: RiskLevel.DOUBLE_CONFIRM_REQUIRED,
    METHOD_REVOKE_STATS_URL: RiskLevel.DOUBLE_CONFIRM_REQUIRED,
    # ── FORBIDDEN BY DEFAULT (require config opt-in) ──────────────────────────
    "transferStars": RiskLevel.FORBIDDEN,
    "external_fragment_payment": RiskLevel.FORBIDDEN,
    "editEvent": RiskLevel.FORBIDDEN,  # not in spec — needs live discovery first
    "createPixel": RiskLevel.FORBIDDEN,  # untested
}


def get_risk_level(action: str) -> RiskLevel:
    return ACTION_RISK_MAP.get(action, RiskLevel.FORBIDDEN)


# ─── Confirmation ────────────────────────────────────────────────────────────


@dataclass
class Confirmation:
    """Token issued by skill/UI to authorize a single mutating action.

    Adapter validates that:
        - id is known and not expired
        - action matches
        - params_fingerprint matches the params being submitted

    Re-using a Confirmation for a different action/params will fail validation.
    """

    id: str
    action: str
    params_fingerprint: str
    issued_at: datetime
    ttl_seconds: int = 300
    note: str = ""

    @classmethod
    def issue(
        cls,
        action: str,
        params: dict[str, Any],
        *,
        ttl_seconds: int = 300,
        note: str = "",
    ) -> Confirmation:
        return cls(
            id=str(uuid.uuid4()),
            action=action,
            params_fingerprint=_fingerprint(params),
            issued_at=datetime.now(tz=UTC),
            ttl_seconds=ttl_seconds,
            note=note,
        )

    def is_expired(self) -> bool:
        return datetime.now(tz=UTC) > self.issued_at + timedelta(seconds=self.ttl_seconds)

    def matches(self, action: str, params: dict[str, Any]) -> bool:
        return self.action == action and self.params_fingerprint == _fingerprint(params)


def _fingerprint(params: dict[str, Any]) -> str:
    """Stable canonical JSON → SHA-256."""
    canon = json.dumps(params, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


# ─── Safety facade ───────────────────────────────────────────────────────────


@dataclass
class TelegramAdsSafety:
    config: SafetyConfig
    _pending: dict[str, Confirmation] = field(default_factory=dict)

    # ── Confirmation lifecycle ──────────────────────────────────────────────

    def issue_confirmation(
        self,
        action: str,
        params: dict[str, Any],
        *,
        note: str = "",
    ) -> Confirmation:
        """Caller (skill/UI) requests authorization for an upcoming action."""
        conf = Confirmation.issue(
            action,
            params,
            ttl_seconds=self.config.confirmation_ttl_seconds,
            note=note,
        )
        self._pending[conf.id] = conf
        return conf

    def consume_confirmation(self, confirmation_id: str, action: str, params: dict[str, Any]) -> None:
        """Atomically validate and remove a confirmation.

        Raises:
            InvalidConfirmationError on unknown/expired/mismatched id.
        """
        conf = self._pending.pop(confirmation_id, None)
        if conf is None:
            raise InvalidConfirmationError(
                f"Unknown confirmation_id: {confirmation_id}",
                context={"action": action},
            )
        if conf.is_expired():
            raise InvalidConfirmationError(
                f"Confirmation expired (TTL {conf.ttl_seconds}s)",
                context={"action": action, "confirmation_id": confirmation_id},
            )
        if not conf.matches(action, params):
            raise InvalidConfirmationError(
                "Confirmation does not match action+params",
                context={
                    "expected_action": conf.action,
                    "actual_action": action,
                    "fingerprint_mismatch": True,
                },
            )

    # ── Gate ────────────────────────────────────────────────────────────────

    def gate(
        self,
        action: str,
        params: dict[str, Any] | None = None,
        *,
        confirmation_id: str | None = None,
        second_confirmation_id: str | None = None,
    ) -> None:
        """Check if action is authorized to proceed. Raises if not.

        For RiskLevel.SAFE_READ / DRAFT — no-op.
        For RiskLevel.CONFIRM_REQUIRED — needs `confirmation_id`.
        For RiskLevel.DOUBLE_CONFIRM_REQUIRED — needs both ids (different).
        For RiskLevel.FORBIDDEN — always raises ForbiddenActionError.
        """
        params = params or {}
        level = get_risk_level(action)

        if level is RiskLevel.SAFE_READ or level is RiskLevel.DRAFT:
            return

        if level is RiskLevel.FORBIDDEN:
            raise ForbiddenActionError(
                f"Action {action!r} is forbidden by safety policy",
                context={"action": action, "risk_level": level.value},
            )

        if level is RiskLevel.CONFIRM_REQUIRED:
            if not confirmation_id:
                raise ConfirmationRequiredError(action, risk_level=level.value, params=params)
            self.consume_confirmation(confirmation_id, action, params)
            return

        if level is RiskLevel.DOUBLE_CONFIRM_REQUIRED:
            if not (confirmation_id and second_confirmation_id):
                raise ConfirmationRequiredError(
                    action,
                    risk_level=level.value,
                    params=params,
                    confirmation_message=(
                        f"{action} requires TWO independent confirmations. "
                        "Issue two separate Confirmation objects and pass both ids."
                    ),
                )
            if confirmation_id == second_confirmation_id:
                raise InvalidConfirmationError(
                    "Double confirmation requires two DIFFERENT confirmation ids",
                    context={"action": action},
                )
            self.consume_confirmation(confirmation_id, action, params)
            self.consume_confirmation(second_confirmation_id, action, params)
            return

        raise AssertionError(f"Unhandled RiskLevel: {level}")

    # ── Policy checks (deterministic) ───────────────────────────────────────

    def validate_ad_text(self, text: str) -> list[str]:
        """Return list of violation strings. Empty list = OK.

        These are HARD deterministic checks. Semantic checks (clickbait, etc.)
        belong in skill's policy_checker (LLM-driven).
        """
        violations: list[str] = []
        if len(text) > MAX_AD_TEXT_LEN:
            violations.append(f"Text length {len(text)} exceeds limit {MAX_AD_TEXT_LEN}")
        if "\n" in text:
            violations.append("Text contains line breaks (forbidden)")
        # Bulleted/numbered list markers
        if re.search(r"^\s*[•\-\*]\s", text, re.MULTILINE):
            violations.append("Text appears to use bulleted list (forbidden)")
        if re.search(r"^\s*\d+[.)]\s", text, re.MULTILINE):
            violations.append("Text appears to use numbered list (forbidden)")
        # Masked profanity
        if re.search(r"\b\w*[*]+\w*\b", text):
            violations.append(
                "Text contains masked text with asterisks (potential masked profanity)"
            )
        # Excessive caps: count uppercase letters > 50% in a window
        letters = re.sub(r"[^A-Za-zА-Яа-я]", "", text)
        if len(letters) > 10:
            uppers = sum(1 for c in letters if c.isupper())
            if uppers / len(letters) > 0.7:
                violations.append("Text appears excessively capitalized")
        return violations

    def validate_promote_url(self, url: str) -> list[str]:
        """Validate that promote_url is allowed format.

        Allowed:
            - t.me/<name> or t.me/<name>/<post_id>
            - @<name>
            - https://<domain>/... (for website ads)
        Forbidden:
            - URL shorteners (bit.ly, etc.)
            - IP addresses
        """
        violations: list[str] = []
        url = url.strip()
        if not url:
            violations.append("promote_url is empty")
            return violations

        is_telegram = (
            url.startswith("@")
            or url.startswith("t.me/")
            or url.startswith("https://t.me/")
            or url.startswith("http://t.me/")
        )
        is_website = url.startswith(("http://", "https://")) and "t.me/" not in url

        if not (is_telegram or is_website):
            violations.append(
                "promote_url must be t.me/<name>, @<name>, or https://<website>"
            )

        # IP address in URL
        if re.search(r"https?://\d+\.\d+\.\d+\.\d+", url):
            violations.append("IP-address URLs are forbidden")

        # Known shorteners
        shorteners = {"bit.ly", "tinyurl.com", "goo.gl", "t.co", "ow.ly", "is.gd", "buff.ly"}
        for s in shorteners:
            if s in url.lower():
                violations.append(f"URL shortener {s!r} is forbidden")

        return violations

    def check_text_link_count(self, text: str) -> list[str]:
        """Max one inline link in text; must be t.me/@-format."""
        violations: list[str] = []
        # crude link detection
        links = re.findall(r"(?:https?://\S+|t\.me/\S+|@\w+)", text)
        if len(links) > 1:
            violations.append(f"Text contains {len(links)} links; max 1 allowed")
        for link in links:
            if not (
                link.startswith("@")
                or link.startswith("t.me/")
                or link.startswith("https://t.me/")
                or link.startswith("http://t.me/")
            ):
                violations.append(f"Link {link!r} must be @<name> or t.me/...")
        return violations

    def raise_if_policy_violations(self, text: str, promote_url: str) -> None:
        violations = (
            self.validate_ad_text(text)
            + self.validate_promote_url(promote_url)
            + self.check_text_link_count(text)
        )
        if violations:
            raise PolicyViolationError(violations)

    # ── Geo / cabinet checks ────────────────────────────────────────────────

    def check_ton_geo_safe(self, target_countries: set[str] | None) -> None:
        """Warn if TON cabinet selected but target audience includes RU/UA/IL/PS.

        These countries are HARD-BLOCKED from seeing TON-cabinet ads.
        For RU/UA/IL/PS audience, only Stars-cabinet works.
        """
        if not target_countries:
            return
        blocked = target_countries & set(TON_GEO_HARD_BLOCK)
        if blocked:
            raise GeoTargetingError(
                f"TON cabinet cannot reach users from {sorted(blocked)}. "
                "Switch to a Stars (Bot Account) cabinet for these countries.",
                context={"blocked": sorted(blocked)},
            )
