"""Pydantic models for the Telegram Ads watcher layer.

These are *internal Hermes* records — watch rules, local snapshots, and watcher
events. They are NOT Telegram Ads API objects (see ``hermes_telegram_ads.types``
for those). The watcher only ever *reads* Telegram Ads; everything here is local
state persisted in SQLite (see :mod:`hermes_telegram_ads.watcher.store`).

Money fields are typed ``float`` (matching the rest of the package, e.g.
``types.Ad.budget``) so snapshots round-trip cleanly through JSON/SQLite. The
task spec allows ``Decimal | float | None``; we deliberately use plain floats to
avoid lossy Decimal↔JSON conversions in the local store.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ─── Shared helpers ───────────────────────────────────────────────────────────


def now_utc() -> datetime:
    """Timezone-aware UTC now — the single clock used across the watcher."""
    return datetime.now(tz=UTC)


def new_id() -> str:
    """Opaque local id for specs/snapshots/events (uuid4 hex)."""
    return uuid.uuid4().hex


def hash_account_token(token: str | None) -> str | None:
    """SHA-256 truncated hash, mirroring ``audit.AuditLogger._mask``.

    Returned as ``sha256:<16 hex>`` so a raw Telegram Ads ``account_token`` is
    never persisted in the clear by the watcher. ``None`` passes through.
    """
    if not token:
        return None
    h = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"sha256:{h[:16]}"


# ─── Literals ─────────────────────────────────────────────────────────────────

WatchKind = Literal[
    # system / session
    "tool_status",
    "login_state",
    # accounts
    "accounts_snapshot",
    "account_balance",
    "account_budget",
    "account_stats",
    # campaigns
    "campaign_list",
    "campaign_detail",
    "campaign_status",
    "moderation_result",
    "rejection_info",
    "campaign_targeting",
    "targeting_lock_state",
    "campaign_budget",
    "campaign_spend",
    "campaign_cpm",
    "campaign_stats",
    "campaign_performance",
    "campaign_reports",
    # share stats / validation
    "share_stats_state",
    "draft_validation",
]

CreatedBy = Literal["human", "agent", "system"]

EventType = Literal[
    # tool / session
    "tool_unavailable",
    "tool_available",
    "login_required",
    "login_restored",
    # accounts
    "account_added",
    "account_removed",
    "account_balance_low",
    "account_budget_changed",
    "account_stats_changed",
    # campaigns / lifecycle
    "campaign_added",
    "campaign_removed",
    "campaign_status_changed",
    "ad_status_changed",
    "ad_approved",
    "ad_declined",
    "ad_started",
    "ad_stopped",
    "ad_deleted_or_missing",
    # moderation / targeting
    "rejection_reason_changed",
    "targeting_changed",
    "targeting_locked",
    "targeting_unlocked",
    # budget / spend / cpm
    "budget_low",
    "budget_changed",
    "spend_threshold_reached",
    "cpm_changed",
    # stats / performance
    "stats_changed",
    "stats_anomaly",
    "ctr_drop",
    "cvr_drop",
    "cpa_above_threshold",
    "delivery_stalled",
    # reports / share stats
    "report_available",
    "report_changed",
    "share_stats_available",
    "share_stats_unavailable",
    "share_stats_changed",
    # draft validation
    "draft_validation_failed",
    "draft_validation_passed",
    # post-action verification
    "post_action_verified",
    "post_action_not_verified",
    # errors
    "watch_error",
]

Severity = Literal["info", "warning", "critical"]

ResourceType = Literal[
    "tool_status",
    "login_state",
    "accounts",
    "account",
    "campaign_list",
    "campaign",
    "campaign_stats",
    "campaign_targeting",
    "rejection_info",
    "draft_validation",
    "report",
    "share_stats",
]

# Legacy kinds backed by the typed ``AdSnapshot`` path + ``diff_ad`` (single ad).
AD_KINDS: frozenset[str] = frozenset(
    {
        "campaign_status",
        "moderation_result",
        "campaign_budget",
        "campaign_spend",
        "campaign_stats",
    }
)

# Expanded kinds backed by the generic ``ResourceSnapshot`` path + ``diff_resource``.
RESOURCE_KINDS: frozenset[str] = frozenset(
    {
        "tool_status",
        "accounts_snapshot",
        "account_balance",
        "account_stats",
        "campaign_list",
        "campaign_detail",
        "rejection_info",
        "campaign_targeting",
        "targeting_lock_state",
        "campaign_cpm",
        "campaign_performance",
        "campaign_reports",
        "share_stats_state",
        "draft_validation",
    }
)

# Kinds that require an ``ad_id`` (single campaign focus).
CAMPAIGN_KINDS: frozenset[str] = AD_KINDS | frozenset(
    {
        "campaign_detail",
        "rejection_info",
        "campaign_targeting",
        "targeting_lock_state",
        "campaign_cpm",
        "campaign_performance",
        "campaign_reports",
        "share_stats_state",
    }
)


class _WatcherModel(BaseModel):
    """Tolerant base (mirrors ``types._Model``): ignore unknown fields."""

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
        arbitrary_types_allowed=False,
    )

    @model_validator(mode="after")
    def _make_datetimes_aware(self) -> _WatcherModel:
        """Coerce naive datetimes to UTC-aware.

        Callers may pass ``datetime.utcnow()`` (naive) for ``expires_at`` etc.;
        the whole watcher compares against UTC-aware ``now_utc()``, so a naive
        value would raise on comparison. Assume naive == UTC.
        """
        for name, value in list(self.__dict__.items()):
            if isinstance(value, datetime) and value.tzinfo is None:
                object.__setattr__(self, name, value.replace(tzinfo=UTC))
        return self


# ─── WatchSpec ────────────────────────────────────────────────────────────────


class WatchSpec(_WatcherModel):
    """A single watch rule. Deterministic — no AI involved in evaluating it."""

    id: str = Field(default_factory=new_id)
    project_id: str = "default"
    account_id: str | None = None
    account_token_hash: str | None = None
    ad_id: int | str | None = None

    kind: WatchKind

    interval_sec: int = 900
    enabled: bool = True

    thresholds: dict[str, Any] = Field(default_factory=dict)

    notify: bool = True
    invoke_agent: bool = False

    created_by: CreatedBy = "system"
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    expires_at: datetime | None = None

    def is_expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        return (now or now_utc()) >= self.expires_at

    def is_due(self, now: datetime | None = None) -> bool:
        """True when this watch should run: enabled, not expired, next_run reached."""
        now = now or now_utc()
        if not self.enabled or self.is_expired(now):
            return False
        return self.next_run_at is None or self.next_run_at <= now


# ─── Snapshots ────────────────────────────────────────────────────────────────


class AdSnapshot(_WatcherModel):
    """Point-in-time read of a single campaign/ad. Read-only capture."""

    id: str = Field(default_factory=new_id)
    project_id: str = "default"
    account_id: str | None = None
    account_token_hash: str | None = None
    ad_id: int | str

    title: str | None = None
    status: str | None = None  # normalized canonical label (see diff.normalize_status)
    raw_status: str | None = None  # as reported by Telegram Ads
    rejection_reason: str | None = None

    budget: float | None = None
    spent: float | None = None
    remaining_budget: float | None = None
    cpm: float | None = None

    views: int | None = None
    clicks: int | None = None
    ctr: float | None = None

    observed_at: datetime = Field(default_factory=now_utc)
    source: Literal["telegram_ads"] = "telegram_ads"
    raw: dict[str, Any] | None = None

    def compact(self) -> dict[str, Any]:
        """Small JSON-safe view used as event ``previous``/``current`` payload."""
        return {
            "ad_id": self.ad_id,
            "status": self.status,
            "raw_status": self.raw_status,
            "rejection_reason": self.rejection_reason,
            "budget": self.budget,
            "spent": self.spent,
            "remaining_budget": self.remaining_budget,
            "cpm": self.cpm,
            "views": self.views,
            "clicks": self.clicks,
            "ctr": self.ctr,
            "observed_at": self.observed_at.isoformat(),
        }


class AccountSnapshot(_WatcherModel):
    """Point-in-time read of a cabinet's balance/budget. Read-only capture."""

    id: str = Field(default_factory=new_id)
    project_id: str = "default"
    account_id: str | None = None
    account_token_hash: str | None = None

    balance: float | None = None
    budget: float | None = None
    spent: float | None = None

    observed_at: datetime = Field(default_factory=now_utc)
    source: Literal["telegram_ads"] = "telegram_ads"
    raw: dict[str, Any] | None = None

    def compact(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "balance": self.balance,
            "budget": self.budget,
            "spent": self.spent,
            "observed_at": self.observed_at.isoformat(),
        }


class ResourceSnapshot(_WatcherModel):
    """Generic read-only capture for the expanded watcher kinds.

    A single typed row that can hold any read-only Telegram Ads resource
    (tool/login state, account list, campaign list, campaign detail, stats,
    targeting, rejection info, draft-validation result, report/share-stats
    state). The payload lives in ``data`` (a small JSON-safe dict produced by the
    service from the adapter's read methods). ``AdSnapshot`` / ``AccountSnapshot``
    remain for the legacy typed paths; this complements them.
    """

    id: str = Field(default_factory=new_id)
    project_id: str = "default"
    resource_type: ResourceType
    resource_id: str | None = None
    account_id: str | None = None
    account_token_hash: str | None = None
    ad_id: int | str | None = None

    observed_at: datetime = Field(default_factory=now_utc)
    source: Literal["telegram_ads"] = "telegram_ads"
    data: dict[str, Any] = Field(default_factory=dict)

    def compact(self) -> dict[str, Any]:
        return {
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "data": self.data,
            "observed_at": self.observed_at.isoformat(),
        }


# ─── WatcherEvent ─────────────────────────────────────────────────────────────


class WatcherEvent(_WatcherModel):
    """Internal Hermes event produced by diffing snapshots.

    NOT a Telegram Ads pixel/conversion event — this is consumed by Hermes
    to decide what (if anything) to do next. ``dedupe_key`` makes event creation
    idempotent: the same logical change re-detected yields the same key and is
    not stored twice (see ``store.create_event``).
    """

    id: str = Field(default_factory=new_id)
    project_id: str = "default"

    source: Literal["telegram_ads_watcher"] = "telegram_ads_watcher"

    event_type: EventType
    severity: Severity = "info"

    account_id: str | None = None
    account_token_hash: str | None = None
    ad_id: int | str | None = None
    watch_spec_id: str | None = None

    previous: dict[str, Any] | None = None
    current: dict[str, Any] | None = None

    reason: str | None = None
    recommended_agent_action: str | None = None

    created_at: datetime = Field(default_factory=now_utc)
    dedupe_key: str
    consumed_at: datetime | None = None
