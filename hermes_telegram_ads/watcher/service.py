"""TelegramAdsWatcherService — programmatic watcher API for Hermes.

Read-only orchestration over an existing ``TelegramAdsAdapter`` (or any object
with the same read surface). The service NEVER performs a mutating Telegram Ads
action — it only calls the adapter's read methods (``get_ad``, ``get_ad_stats``,
``get_account_budget``, ``get_account_stats``, ``get_share_stats_url``,
``list_ads``, ``list_accounts``, ``validate_ad``, ``detect_login_state``),
saves local snapshots, diffs them, and persists watcher events.

Mutating adapter methods (create/edit/start/stop ad, change CPM, add/withdraw
budget, delete, revoke) are intentionally not referenced anywhere in this
module. For those, Hermes executes the approved action itself and the
watcher only *verifies the observable result* via a post-action watch (see
``recipes.create_post_action_watches`` and ``thresholds['expected']``).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

from hermes_telegram_ads.watcher.diff import (
    build_login_required_event,
    build_verification_event,
    build_watch_error_event,
    diff_account,
    diff_ad,
    diff_resource,
)
from hermes_telegram_ads.watcher.models import (
    AD_KINDS,
    CAMPAIGN_KINDS,
    RESOURCE_KINDS,
    AccountSnapshot,
    AdSnapshot,
    ResourceSnapshot,
    WatcherEvent,
    WatchSpec,
    hash_account_token,
    now_utc,
)
from hermes_telegram_ads.watcher.policies import normalize_status
from hermes_telegram_ads.watcher.store import SQLiteWatcherStore

logger = logging.getLogger(__name__)


def _num(value: Any) -> float | None:
    """Coerce to float, treating ``None`` and ``bool`` (Telegram's ``false``
    sentinel for empty numeric fields) as missing."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _pick(obj: Any, *names: str, default: Any = None) -> Any:
    """Read a field from a Pydantic model, SimpleNamespace, or dict.

    The watcher adapter may return either typed models or JSON dicts (the
    Hermes read-only wrapper always dict-ifies). getattr() on a dict misses
    every field, so snapshot/diff would silently see None.
    """
    if obj is None:
        return default
    if isinstance(obj, dict):
        for name in names:
            if name in obj and obj[name] is not None:
                return obj[name]
        return default
    for name in names:
        if hasattr(obj, name):
            value = getattr(obj, name)
            if value is not None:
                return value
    return default


def _short_hash(value: Any) -> str | None:
    """Stable short hash for change-detection of token-bearing strings (e.g. a
    share-stats URL). We never persist such URLs raw — only this hash."""
    if not value:
        return None
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


class TelegramAdsWatcherService:
    """Deterministic watcher façade. All public methods are async-compatible."""

    def __init__(
        self,
        adapter: Any,
        store: SQLiteWatcherStore,
        project_id: str = "default",
    ) -> None:
        self.adapter = adapter
        self.store = store
        self.project_id = project_id

    # ─── Watch management ─────────────────────────────────────────────────

    async def create_watch(
        self,
        *,
        kind: str,
        ad_id: int | str | None = None,
        account_id: str | None = None,
        account_token: str | None = None,
        account_token_hash: str | None = None,
        interval_sec: int = 900,
        enabled: bool = True,
        thresholds: dict[str, Any] | None = None,
        notify: bool = True,
        invoke_agent: bool = False,
        created_by: str = "system",
        expires_at: datetime | None = None,
        project_id: str | None = None,
        id: str | None = None,
    ) -> WatchSpec:
        """Create and persist a watch rule, due immediately (``next_run_at`` ==
        creation time). ``account_token`` (raw) is hashed, never stored raw."""
        if kind in CAMPAIGN_KINDS and ad_id is None:
            raise ValueError(f"watch kind {kind!r} requires an ad_id")
        if kind == "draft_validation" and not (thresholds or {}).get("draft"):
            raise ValueError("draft_validation watch requires thresholds['draft']")
        if account_token_hash is None and account_token is not None:
            account_token_hash = hash_account_token(account_token)

        created = now_utc()
        spec = WatchSpec(
            **({"id": id} if id else {}),
            project_id=project_id or self.project_id,
            account_id=account_id,
            account_token_hash=account_token_hash,
            ad_id=ad_id,
            kind=kind,  # type: ignore[arg-type]
            interval_sec=interval_sec,
            enabled=enabled,
            thresholds=thresholds or {},
            notify=notify,
            invoke_agent=invoke_agent,
            created_by=created_by,  # type: ignore[arg-type]
            created_at=created,
            updated_at=created,
            next_run_at=created,
            expires_at=expires_at,
        )
        return self.store.create_watch(spec)

    async def create_post_action_watches(
        self,
        action: str,
        *,
        ad_id: int | str | None = None,
        account_id: str | None = None,
        expected: dict[str, Any] | None = None,
        interval_sec: int = 600,
        expires_in_sec: int | None = 172800,
        project_id: str | None = None,
    ) -> list[WatchSpec]:
        """Create the read-only follow-up watches that verify the observable
        result of an approved mutating action. Performs NO mutation itself."""
        from hermes_telegram_ads.watcher.recipes import create_post_action_watches

        specs = create_post_action_watches(
            action,  # type: ignore[arg-type]
            project_id=project_id or self.project_id,
            account_id=account_id,
            ad_id=ad_id,
            expected=expected,
            interval_sec=interval_sec,
            expires_in_sec=expires_in_sec,
        )
        return [self.store.create_watch(s) for s in specs]

    async def get_watch(self, watch_id: str) -> WatchSpec | None:
        return self.store.get_watch(watch_id)

    async def list_watches(
        self, project_id: str | None = None, enabled: bool | None = None
    ) -> list[WatchSpec]:
        return self.store.list_watches(
            project_id=project_id if project_id is not None else self.project_id,
            enabled=enabled,
        )

    async def disable_watch(self, watch_id: str) -> WatchSpec:
        return self.store.disable_watch(watch_id)

    async def delete_watch(self, watch_id: str) -> None:
        self.store.delete_watch(watch_id)

    # ─── Event consumption ────────────────────────────────────────────────

    async def list_events(
        self,
        project_id: str | None = None,
        unconsumed: bool = False,
        limit: int = 100,
    ) -> list[WatcherEvent]:
        return self.store.list_events(
            project_id=project_id if project_id is not None else self.project_id,
            unconsumed=unconsumed,
            limit=limit,
        )

    async def consume_event(self, event_id: str) -> WatcherEvent:
        return self.store.consume_event(event_id)

    # ─── Coverage introspection ───────────────────────────────────────────

    async def list_coverage(self) -> list[Any]:
        """Return the static Telegram Ads → watcher coverage matrix."""
        from hermes_telegram_ads.watcher.coverage import list_tool_coverage

        return list_tool_coverage()

    # ─── Execution ────────────────────────────────────────────────────────

    async def run_watch_once(self, watch_id: str) -> list[WatcherEvent]:
        """Run a single persisted watch now and return the events it created.

        Skips disabled/expired watches (no adapter call). On any adapter failure
        a ``watch_error`` (or ``login_required`` for session problems) event is
        produced instead of propagating. A successful post-action verification
        disables the watch so it stops polling."""
        spec = self.store.get_watch(watch_id)
        if spec is None:
            raise KeyError(f"watch_spec not found: {watch_id}")

        started = now_utc()
        if not spec.enabled or spec.is_expired(started):
            self.store.record_job_run(
                watch_spec_id=spec.id,
                started_at=started,
                finished_at=now_utc(),
                status="skipped",
            )
            return []

        try:
            raw_events, verified = await self._evaluate_spec(spec)
        except Exception as exc:  # adapter read failed — never crash the loop
            logger.warning("watch %s failed: %s", spec.id, exc)
            stored = [self.store.create_event(build_watch_error_event(spec, exc))]
            self._mark_ran(spec, started)
            self.store.record_job_run(
                watch_spec_id=spec.id,
                started_at=started,
                finished_at=now_utc(),
                status="error",
                events_created=len(stored),
                error=f"{type(exc).__name__}: {exc}",
            )
            return stored

        stored_events = [self.store.create_event(e) for e in raw_events]
        self._mark_ran(spec, started)
        if verified:
            self.store.disable_watch(spec.id)
        self.store.record_job_run(
            watch_spec_id=spec.id,
            started_at=started,
            finished_at=now_utc(),
            status="ok",
            events_created=len(stored_events),
        )
        return stored_events

    async def run_capability_watch_once(self, spec: WatchSpec) -> list[WatcherEvent]:
        """Run a (possibly unpersisted) watch spec once and persist its events.

        Used by the ``snapshot_*`` helpers and by callers that want a one-shot
        capability check without registering a recurring watch. Does not update
        scheduler timing. Adapter failures become ``watch_error`` events."""
        try:
            raw_events, _ = await self._evaluate_spec(spec)
        except Exception as exc:
            logger.warning("capability watch %s failed: %s", spec.kind, exc)
            return [self.store.create_event(build_watch_error_event(spec, exc))]
        return [self.store.create_event(e) for e in raw_events]

    async def run_due_watches(self, now: datetime | None = None) -> list[WatcherEvent]:
        """Run every enabled, non-expired, due watch. One failing watch never
        stops the others (each is isolated inside ``run_watch_once``)."""
        now = now or now_utc()
        due = [w for w in self.store.list_watches(enabled=True) if w.is_due(now)]
        events: list[WatcherEvent] = []
        for spec in due:
            try:
                events.extend(await self.run_watch_once(spec.id))
            except Exception as exc:  # defensive: store-level failure, etc.
                logger.error("run_due_watches: watch %s errored: %s", spec.id, exc)
        return events

    # ─── Convenience one-shot snapshots (read-only) ───────────────────────

    async def snapshot_all_accounts(self, *, balance_lte: float | None = None) -> list[WatcherEvent]:
        spec = self._ephemeral_spec(
            "accounts_snapshot",
            thresholds={"balance_lte": balance_lte} if balance_lte is not None else {},
        )
        return await self.run_capability_watch_once(spec)

    async def snapshot_account(
        self, account_id: str | None = None, *, balance_lte: float | None = None
    ) -> list[WatcherEvent]:
        spec = self._ephemeral_spec(
            "account_balance",
            account_id=account_id,
            thresholds={"balance_lte": balance_lte} if balance_lte is not None else {},
        )
        return await self.run_capability_watch_once(spec)

    async def snapshot_campaign(self, ad_id: int | str) -> list[WatcherEvent]:
        return await self.run_capability_watch_once(self._ephemeral_spec("campaign_detail", ad_id=ad_id))

    async def snapshot_campaign_stats(self, ad_id: int | str) -> list[WatcherEvent]:
        return await self.run_capability_watch_once(self._ephemeral_spec("campaign_performance", ad_id=ad_id))

    async def snapshot_campaign_targeting(self, ad_id: int | str) -> list[WatcherEvent]:
        return await self.run_capability_watch_once(self._ephemeral_spec("campaign_targeting", ad_id=ad_id))

    async def snapshot_rejection_info(self, ad_id: int | str) -> list[WatcherEvent]:
        return await self.run_capability_watch_once(self._ephemeral_spec("rejection_info", ad_id=ad_id))

    async def validate_draft_snapshot(self, draft: dict[str, Any]) -> list[WatcherEvent]:
        """Validate a stored/provided draft (checkAdPost + local policy). Never
        creates or edits an ad."""
        return await self.run_capability_watch_once(
            self._ephemeral_spec("draft_validation", thresholds={"draft": draft})
        )

    # ─── Capture + diff dispatch ──────────────────────────────────────────

    async def _evaluate_spec(self, spec: WatchSpec) -> tuple[list[WatcherEvent], bool]:
        """Capture, diff, and (optionally) verify a spec. Returns the events and
        whether a post-action verification succeeded. May raise on adapter error."""
        events, observed = await self._capture_and_diff(spec)
        verify = build_verification_event(spec, observed)
        verified = bool(verify is not None and verify.event_type == "post_action_verified")
        if verify is not None:
            events = [*events, verify]
        return events, verified

    async def _capture_and_diff(self, spec: WatchSpec) -> tuple[list[WatcherEvent], dict[str, Any]]:
        if spec.kind == "login_state":
            return await self._run_login_state(spec)
        if spec.kind == "account_budget":
            return await self._run_account(spec)
        if spec.kind in AD_KINDS:
            return await self._run_ad(spec)
        if spec.kind in RESOURCE_KINDS:
            return await self._run_resource(spec)
        raise ValueError(f"unsupported watch kind: {spec.kind!r}")

    async def _run_ad(self, spec: WatchSpec) -> tuple[list[WatcherEvent], dict[str, Any]]:
        detail = await self.adapter.get_ad(spec.ad_id)
        snap = self._ad_snapshot_from_detail(spec, detail)
        prev = self.store.get_latest_ad_snapshot(spec.project_id, spec.ad_id)
        self.store.save_ad_snapshot(snap)
        observed = {
            "status": snap.raw_status,
            "raw_status": snap.raw_status,
            "cpm": snap.cpm,
            "budget": snap.budget,
            "spent": snap.spent,
            "remaining_budget": snap.remaining_budget,
        }
        return diff_ad(prev, snap, spec), observed

    async def _run_account(self, spec: WatchSpec) -> tuple[list[WatcherEvent], dict[str, Any]]:
        budget = await self.adapter.get_account_budget()
        snap = self._account_snapshot_from_budget(spec, budget)
        prev = self.store.get_latest_account_snapshot(spec.project_id, spec.account_id)
        self.store.save_account_snapshot(snap)
        observed = {"balance": snap.balance, "budget": snap.budget, "spent": snap.spent}
        return diff_account(prev, snap, spec), observed

    async def _run_login_state(self, spec: WatchSpec) -> tuple[list[WatcherEvent], dict[str, Any]]:
        state = await self.adapter.detect_login_state()
        logged_in = state.get("logged_in") if isinstance(state, dict) else getattr(state, "logged_in", None)
        observed = {"logged_in": logged_in}
        if logged_in is True:
            return [], observed
        hint = state.get("recovery_hint") or state.get("state") if isinstance(state, dict) else None
        return (
            [build_login_required_event(spec, reason=f"login required ({hint})" if hint else None)],
            observed,
        )

    async def _run_resource(self, spec: WatchSpec) -> tuple[list[WatcherEvent], dict[str, Any]]:
        resource_type, resource_id = self._resource_key(spec)
        data = await self._read_resource(spec)
        snap = ResourceSnapshot(
            project_id=spec.project_id,
            resource_type=resource_type,  # type: ignore[arg-type]
            resource_id=resource_id,
            account_id=spec.account_id,
            account_token_hash=spec.account_token_hash,
            ad_id=spec.ad_id,
            data=data,
        )
        prev = self.store.get_latest_resource_snapshot(spec.project_id, resource_type, resource_id)
        self.store.save_resource_snapshot(snap)
        events = diff_resource(prev, snap, spec)
        observed = dict(data)
        if spec.kind == "campaign_list" and spec.ad_id is not None:
            observed["missing"] = str(spec.ad_id) not in (data.get("ad_ids") or [])
        return events, observed

    # ─── Read-only adapter calls per resource kind ────────────────────────

    async def _read_resource(self, spec: WatchSpec) -> dict[str, Any]:
        kind = spec.kind
        if kind == "tool_status":
            state = await self.adapter.detect_login_state()
            logged_in = (
                state.get("logged_in") if isinstance(state, dict) else getattr(state, "logged_in", None)
            )
            browser_state = state.get("browser_state") if isinstance(state, dict) else None
            if browser_state is None:
                try:
                    browser_state = "healthy" if self.adapter.browser_healthy() else "broken"
                except Exception:
                    browser_state = "unknown"
            return {"logged_in": logged_in, "browser_state": browser_state}

        if kind == "accounts_snapshot":
            accounts = await self.adapter.list_accounts()
            accs: list[dict[str, Any]] = []
            keys: list[str] = []
            for a in accounts:
                token = _pick(a, "account_token", "token") or ""
                key = hash_account_token(token) or self._account_fingerprint(a)
                keys.append(key)
                accs.append(
                    {
                        "key": key,
                        "title": _pick(a, "title"),
                        "currency": _pick(a, "currency"),
                        "account_type": _pick(a, "account_type"),
                        "balance": _num(_pick(a, "balance")),
                    }
                )
            return {"keys": keys, "count": len(keys), "accounts": accs}

        if kind == "account_balance":
            budget = await self.adapter.get_account_budget()
            return {
                "balance": _num(_pick(budget, "balance")),
                "currency": _pick(budget, "currency"),
            }

        if kind == "account_stats":
            stats = await self.adapter.get_account_stats()
            url = stats.get("url") if isinstance(stats, dict) else getattr(stats, "url", None)
            return {"stats_url": url}

        if kind == "campaign_list":
            ads = await self.adapter.list_ads()
            ad_ids = [str(_pick(a, "ad_id", "id")) for a in ads]
            by_status: dict[str, int] = {}
            for a in ads:
                s = normalize_status(_pick(a, "status", "raw_status"))
                by_status[s] = by_status.get(s, 0) + 1
            return {"ad_ids": ad_ids, "count": len(ad_ids), "by_status": by_status}

        if kind in {
            "campaign_detail",
            "campaign_cpm",
            "rejection_info",
            "campaign_targeting",
            "targeting_lock_state",
        }:
            return await self._read_ad_detail_resource(spec, kind)

        if kind in {"campaign_performance", "campaign_reports"}:
            stats = await self.adapter.get_ad_stats(spec.ad_id)
            views = _int(_pick(stats, "views"))
            csv_url = _pick(stats, "csv_url")
            if kind == "campaign_performance":
                return {"views": views, "has_report": bool(csv_url)}
            return {"has_report": bool(csv_url), "report_key": _short_hash(csv_url)}

        if kind == "share_stats_state":
            url = await self.adapter.get_share_stats_url(spec.ad_id)
            # Never persist the token-bearing URL — only availability + a hash.
            return {"available": url is not None, "url_hash": _short_hash(url)}

        if kind == "draft_validation":
            draft = (spec.thresholds or {}).get("draft")
            if not draft:
                raise ValueError("draft_validation watch requires thresholds['draft']")
            result = await self.adapter.validate_ad(self._build_draft(draft))
            error = result.get("error") if isinstance(result, dict) else getattr(result, "error", "")
            field = result.get("field") if isinstance(result, dict) else getattr(result, "field", "")
            return {"valid": not bool(error), "field": field or "", "error": error or ""}

        raise ValueError(f"unsupported resource kind: {kind!r}")

    async def _read_ad_detail_resource(self, spec: WatchSpec, kind: str) -> dict[str, Any]:
        expect_missing = bool((spec.thresholds or {}).get("expected", {}).get("missing"))
        try:
            detail = await self.adapter.get_ad(spec.ad_id)
        except Exception:
            # For a delete-verification watch a get_ad failure means "gone".
            if expect_missing or kind == "campaign_detail":
                return {"missing": True}
            raise
        if detail is None:
            return {"missing": True}
        ad = _pick(detail, "ad", default=detail)
        if kind == "campaign_detail":
            raw_status = _pick(ad, "status", "raw_status")
            return {
                "missing": False,
                "raw_status": raw_status,
                "status": normalize_status(raw_status),
                "cpm": _num(_pick(ad, "cpm")),
                "budget": _num(_pick(ad, "budget")),
                "spent": _num(_pick(ad, "spent")),
                "title": _pick(ad, "title"),
            }
        if kind == "campaign_cpm":
            return {"cpm": _num(_pick(ad, "cpm"))}
        if kind == "rejection_info":
            decline = _pick(detail, "decline_reason", "rejection_reason", "rejection")
            if isinstance(decline, str):
                return {"declined": True, "category": None, "description": decline}
            return {
                "declined": decline is not None,
                "category": _pick(decline, "category") if decline else None,
                "description": _pick(decline, "description") if decline else None,
            }
        # campaign_targeting / targeting_lock_state
        targets = list(_pick(detail, "locked_targets", "targets", default=[]) or [])
        return {
            "target_type": _pick(ad, "trg_type", "target_type"),
            "targets": targets,
            "locked": bool(targets),
        }

    # ─── Snapshot extraction (tolerant of real models + simple fakes) ─────

    def _ad_snapshot_from_detail(self, spec: WatchSpec, detail: Any) -> AdSnapshot:
        ad = _pick(detail, "ad", default=detail)
        raw_status = _pick(ad, "status", "raw_status")
        budget = _num(_pick(ad, "budget"))
        spent = _num(_pick(ad, "spent"))
        remaining = budget - spent if budget is not None and spent is not None else None
        decline = _pick(detail, "decline_reason", "rejection_reason", "rejection")
        rejection_reason = None
        if isinstance(decline, str):
            rejection_reason = decline
        elif decline is not None:
            rejection_reason = _pick(decline, "description", "category")
        return AdSnapshot(
            project_id=spec.project_id,
            account_id=spec.account_id,
            account_token_hash=spec.account_token_hash,
            ad_id=spec.ad_id,  # type: ignore[arg-type]
            title=_pick(ad, "title"),
            status=normalize_status(raw_status),
            raw_status=raw_status,
            rejection_reason=rejection_reason,
            budget=budget,
            spent=spent,
            remaining_budget=remaining,
            cpm=_num(_pick(ad, "cpm")),
            views=_int(_pick(ad, "views")),
            clicks=_int(_pick(ad, "clicks")),
            ctr=_num(_pick(ad, "ctr")),
        )

    def _account_snapshot_from_budget(self, spec: WatchSpec, budget: Any) -> AccountSnapshot:
        return AccountSnapshot(
            project_id=spec.project_id,
            account_id=spec.account_id,
            account_token_hash=spec.account_token_hash,
            balance=_num(_pick(budget, "balance")),
            budget=_num(_pick(budget, "budget")),
            spent=_num(_pick(budget, "spent")),
        )

    # ─── Internal helpers ─────────────────────────────────────────────────

    @staticmethod
    def _account_fingerprint(account: Any) -> str:
        return "fp:" + "|".join(str(_pick(account, k) or "") for k in ("title", "currency", "account_type"))

    @staticmethod
    def _build_draft(draft: dict[str, Any]) -> Any:
        from hermes_telegram_ads.types import CreateAdDraft

        try:
            return CreateAdDraft.model_validate(draft)
        except Exception:
            # Fallback for fakes / partial drafts: an attribute bag is enough for
            # read-only validate_ad to run its policy checks.
            return SimpleNamespace(**draft)

    def _resource_key(self, spec: WatchSpec) -> tuple[str, str]:
        ad = spec.ad_id
        acc = spec.account_id or "current"
        mapping: dict[str, tuple[str, str]] = {
            "tool_status": ("tool_status", "tool_status"),
            "accounts_snapshot": ("accounts", "accounts"),
            "account_balance": ("account", f"account_balance:{acc}"),
            "account_stats": ("account", f"account_stats:{acc}"),
            "campaign_list": ("campaign_list", "campaign_list"),
            "campaign_detail": ("campaign", f"campaign:{ad}"),
            "rejection_info": ("rejection_info", f"rejection:{ad}"),
            "campaign_targeting": ("campaign_targeting", f"targeting:{ad}"),
            "targeting_lock_state": ("campaign_targeting", f"targeting_lock:{ad}"),
            "campaign_cpm": ("campaign", f"cpm:{ad}"),
            "campaign_performance": ("campaign_stats", f"performance:{ad}"),
            "campaign_reports": ("report", f"report:{ad}"),
            "share_stats_state": ("share_stats", f"share_stats:{ad}"),
            "draft_validation": ("draft_validation", f"draft:{spec.id}"),
        }
        return mapping[spec.kind]

    def _ephemeral_spec(
        self,
        kind: str,
        *,
        ad_id: int | str | None = None,
        account_id: str | None = None,
        account_token_hash: str | None = None,
        thresholds: dict[str, Any] | None = None,
    ) -> WatchSpec:
        now = now_utc()
        return WatchSpec(
            project_id=self.project_id,
            kind=kind,  # type: ignore[arg-type]
            ad_id=ad_id,
            account_id=account_id,
            account_token_hash=account_token_hash,
            thresholds=thresholds or {},
            created_by="system",
            created_at=now,
            updated_at=now,
            next_run_at=now,
        )

    def _mark_ran(self, spec: WatchSpec, started: datetime) -> None:
        next_run = started + timedelta(seconds=max(1, spec.interval_sec))
        self.store.update_watch(spec.id, last_run_at=started, next_run_at=next_run)


__all__: Sequence[str] = ["TelegramAdsWatcherService"]
