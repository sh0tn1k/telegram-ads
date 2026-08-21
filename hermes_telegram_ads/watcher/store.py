"""Local SQLite store for the Telegram Ads watcher.

Single-file, dependency-free (stdlib ``sqlite3`` only — no Redis/Postgres/Celery).
Holds watch rules, snapshots, watcher events, and job-run bookkeeping.

Default location: ``~/.hermes/telegram_ads_watcher.db`` (the ``~/.hermes``
directory is created automatically). Override via the ``db_path`` constructor
arg or the ``HERMES_WATCHER_DB`` environment variable.

Idempotency
-----------
``watcher_events.dedupe_key`` carries a UNIQUE constraint. ``create_event`` is
idempotent: inserting an event whose ``dedupe_key`` already exists does **not**
raise and does **not** duplicate the row — the previously stored event is
returned unchanged. This lets the scheduler re-evaluate watches freely without
spamming duplicate events.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from hermes_telegram_ads.watcher.models import (
    AccountSnapshot,
    AdSnapshot,
    ResourceSnapshot,
    WatcherEvent,
    WatchSpec,
    now_utc,
)

DEFAULT_DB_PATH = Path.home() / ".hermes" / "telegram_ads_watcher.db"
ENV_DB_PATH = "HERMES_WATCHER_DB"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS watch_specs (
    id                 TEXT PRIMARY KEY,
    project_id         TEXT NOT NULL,
    account_id         TEXT,
    account_token_hash TEXT,
    ad_id              TEXT,
    kind               TEXT NOT NULL,
    interval_sec       INTEGER NOT NULL,
    enabled            INTEGER NOT NULL,
    thresholds         TEXT NOT NULL,
    notify             INTEGER NOT NULL,
    invoke_agent       INTEGER NOT NULL,
    created_by         TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    last_run_at        TEXT,
    next_run_at        TEXT,
    expires_at         TEXT
);
CREATE INDEX IF NOT EXISTS ix_watch_specs_project ON watch_specs(project_id);
CREATE INDEX IF NOT EXISTS ix_watch_specs_due ON watch_specs(enabled, next_run_at);

CREATE TABLE IF NOT EXISTS ad_snapshots (
    id                 TEXT PRIMARY KEY,
    project_id         TEXT NOT NULL,
    account_id         TEXT,
    account_token_hash TEXT,
    ad_id              TEXT NOT NULL,
    title              TEXT,
    status             TEXT,
    raw_status         TEXT,
    rejection_reason   TEXT,
    budget             REAL,
    spent              REAL,
    remaining_budget   REAL,
    cpm                REAL,
    views              INTEGER,
    clicks             INTEGER,
    ctr                REAL,
    observed_at        TEXT NOT NULL,
    source             TEXT NOT NULL,
    raw                TEXT
);
CREATE INDEX IF NOT EXISTS ix_ad_snap_lookup
    ON ad_snapshots(project_id, ad_id, observed_at);

CREATE TABLE IF NOT EXISTS account_snapshots (
    id                 TEXT PRIMARY KEY,
    project_id         TEXT NOT NULL,
    account_id         TEXT,
    account_token_hash TEXT,
    balance            REAL,
    budget             REAL,
    spent              REAL,
    observed_at        TEXT NOT NULL,
    source             TEXT NOT NULL,
    raw                TEXT
);
CREATE INDEX IF NOT EXISTS ix_acc_snap_lookup
    ON account_snapshots(project_id, account_id, observed_at);

CREATE TABLE IF NOT EXISTS resource_snapshots (
    id                 TEXT PRIMARY KEY,
    project_id         TEXT NOT NULL,
    resource_type      TEXT NOT NULL,
    resource_id        TEXT,
    account_id         TEXT,
    account_token_hash TEXT,
    ad_id              TEXT,
    observed_at        TEXT NOT NULL,
    source             TEXT NOT NULL,
    data               TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_resource_snap_lookup
    ON resource_snapshots(project_id, resource_type, resource_id, observed_at);

CREATE TABLE IF NOT EXISTS watcher_events (
    id                       TEXT PRIMARY KEY,
    project_id               TEXT NOT NULL,
    source                   TEXT NOT NULL,
    event_type               TEXT NOT NULL,
    severity                 TEXT NOT NULL,
    account_id               TEXT,
    account_token_hash       TEXT,
    ad_id                    TEXT,
    watch_spec_id            TEXT,
    previous                 TEXT,
    current                  TEXT,
    reason                   TEXT,
    recommended_agent_action TEXT,
    created_at               TEXT NOT NULL,
    dedupe_key               TEXT NOT NULL UNIQUE,
    consumed_at              TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_unconsumed
    ON watcher_events(project_id, consumed_at);

CREATE TABLE IF NOT EXISTS job_runs (
    id             TEXT PRIMARY KEY,
    watch_spec_id  TEXT,
    started_at     TEXT NOT NULL,
    finished_at    TEXT,
    status         TEXT NOT NULL,
    events_created INTEGER NOT NULL DEFAULT 0,
    error          TEXT
);
CREATE INDEX IF NOT EXISTS ix_job_runs_spec ON job_runs(watch_spec_id);
"""


def _dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(value: str | None) -> Any:
    if value is None:
        return None
    return json.loads(value)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class SQLiteWatcherStore:
    """SQLite-backed persistence for the watcher.

    Thread-safe for the simple access pattern used by the async scheduler: a
    single shared connection guarded by a lock (sqlite serializes writes anyway).
    """

    def __init__(self, db_path: str | os.PathLike[str] | None = None) -> None:
        self.db_path = self._resolve_path(db_path)
        if str(self.db_path) != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._create_schema()

    @staticmethod
    def _resolve_path(db_path: str | os.PathLike[str] | None) -> Path | str:
        if db_path is not None:
            return ":memory:" if str(db_path) == ":memory:" else Path(db_path)
        env = os.environ.get(ENV_DB_PATH)
        if env:
            return ":memory:" if env == ":memory:" else Path(env)
        return DEFAULT_DB_PATH

    def _create_schema(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ─── watch_specs ──────────────────────────────────────────────────────

    def create_watch(self, spec: WatchSpec) -> WatchSpec:
        row = self._spec_to_row(spec)
        cols = ", ".join(row.keys())
        placeholders = ", ".join(f":{k}" for k in row)
        with self._lock:
            self._conn.execute(f"INSERT INTO watch_specs ({cols}) VALUES ({placeholders})", row)
            self._conn.commit()
        return spec

    def get_watch(self, watch_id: str) -> WatchSpec | None:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM watch_specs WHERE id = ?", (watch_id,))
            r = cur.fetchone()
        return self._row_to_spec(r) if r else None

    def list_watches(self, project_id: str | None = None, enabled: bool | None = None) -> list[WatchSpec]:
        clauses: list[str] = []
        params: list[Any] = []
        if project_id is not None:
            clauses.append("project_id = ?")
            params.append(project_id)
        if enabled is not None:
            clauses.append("enabled = ?")
            params.append(1 if enabled else 0)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            cur = self._conn.execute(f"SELECT * FROM watch_specs{where} ORDER BY created_at ASC", params)
            rows = cur.fetchall()
        return [self._row_to_spec(r) for r in rows]

    def update_watch(self, watch_id: str, **fields: Any) -> WatchSpec:
        spec = self.get_watch(watch_id)
        if spec is None:
            raise KeyError(f"watch_spec not found: {watch_id}")
        data = spec.model_dump()
        data.update(fields)
        data["id"] = watch_id  # id is immutable
        data["updated_at"] = fields.get("updated_at", now_utc())
        updated = WatchSpec.model_validate(data)
        row = self._spec_to_row(updated)
        assignments = ", ".join(f"{k} = :{k}" for k in row if k != "id")
        with self._lock:
            self._conn.execute(f"UPDATE watch_specs SET {assignments} WHERE id = :id", row)
            self._conn.commit()
        return updated

    def disable_watch(self, watch_id: str) -> WatchSpec:
        return self.update_watch(watch_id, enabled=False)

    def delete_watch(self, watch_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM watch_specs WHERE id = ?", (watch_id,))
            self._conn.commit()

    # ─── ad_snapshots ─────────────────────────────────────────────────────

    def save_ad_snapshot(self, snapshot: AdSnapshot) -> AdSnapshot:
        row = self._ad_snapshot_to_row(snapshot)
        cols = ", ".join(row.keys())
        placeholders = ", ".join(f":{k}" for k in row)
        with self._lock:
            self._conn.execute(f"INSERT INTO ad_snapshots ({cols}) VALUES ({placeholders})", row)
            self._conn.commit()
        return snapshot

    def get_latest_ad_snapshot(self, project_id: str, ad_id: int | str) -> AdSnapshot | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM ad_snapshots WHERE project_id = ? AND ad_id = ? "
                "ORDER BY observed_at DESC, rowid DESC LIMIT 1",
                (project_id, str(ad_id)),
            )
            r = cur.fetchone()
        return self._row_to_ad_snapshot(r) if r else None

    # ─── account_snapshots ────────────────────────────────────────────────

    def save_account_snapshot(self, snapshot: AccountSnapshot) -> AccountSnapshot:
        row = self._account_snapshot_to_row(snapshot)
        cols = ", ".join(row.keys())
        placeholders = ", ".join(f":{k}" for k in row)
        with self._lock:
            self._conn.execute(f"INSERT INTO account_snapshots ({cols}) VALUES ({placeholders})", row)
            self._conn.commit()
        return snapshot

    def get_latest_account_snapshot(self, project_id: str, account_id: str | None) -> AccountSnapshot | None:
        # ``account_id IS ?`` so a NULL account_id (single-cabinet projects) matches.
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM account_snapshots WHERE project_id = ? "
                "AND account_id IS ? ORDER BY observed_at DESC, rowid DESC LIMIT 1",
                (project_id, account_id),
            )
            r = cur.fetchone()
        return self._row_to_account_snapshot(r) if r else None

    # ─── resource_snapshots ───────────────────────────────────────────────

    def save_resource_snapshot(self, snapshot: ResourceSnapshot) -> ResourceSnapshot:
        row = self._resource_snapshot_to_row(snapshot)
        cols = ", ".join(row.keys())
        placeholders = ", ".join(f":{k}" for k in row)
        with self._lock:
            self._conn.execute(f"INSERT INTO resource_snapshots ({cols}) VALUES ({placeholders})", row)
            self._conn.commit()
        return snapshot

    def get_latest_resource_snapshot(
        self,
        project_id: str,
        resource_type: str,
        resource_id: str | None = None,
    ) -> ResourceSnapshot | None:
        # ``resource_id IS ?`` so a NULL resource_id (singletons like accounts /
        # tool_status) matches.
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM resource_snapshots WHERE project_id = ? "
                "AND resource_type = ? AND resource_id IS ? "
                "ORDER BY observed_at DESC, rowid DESC LIMIT 1",
                (project_id, resource_type, resource_id),
            )
            r = cur.fetchone()
        return self._row_to_resource_snapshot(r) if r else None

    # ─── watcher_events ───────────────────────────────────────────────────

    def create_event(self, event: WatcherEvent) -> WatcherEvent:
        """Insert an event, idempotent on ``dedupe_key``.

        If an event with the same ``dedupe_key`` already exists, the stored event
        is returned unchanged (no duplicate row, no exception).
        """
        row = self._event_to_row(event)
        cols = ", ".join(row.keys())
        placeholders = ", ".join(f":{k}" for k in row)
        with self._lock:
            try:
                self._conn.execute(f"INSERT INTO watcher_events ({cols}) VALUES ({placeholders})", row)
                self._conn.commit()
                return event
            except sqlite3.IntegrityError:
                # UNIQUE(dedupe_key) collision → return the existing event.
                self._conn.rollback()
                cur = self._conn.execute(
                    "SELECT * FROM watcher_events WHERE dedupe_key = ?",
                    (event.dedupe_key,),
                )
                existing = cur.fetchone()
        return self._row_to_event(existing) if existing else event

    def list_events(
        self,
        project_id: str | None = None,
        unconsumed: bool = False,
        limit: int = 100,
    ) -> list[WatcherEvent]:
        clauses: list[str] = []
        params: list[Any] = []
        if project_id is not None:
            clauses.append("project_id = ?")
            params.append(project_id)
        if unconsumed:
            clauses.append("consumed_at IS NULL")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(int(limit))
        with self._lock:
            cur = self._conn.execute(
                f"SELECT * FROM watcher_events{where} ORDER BY created_at DESC, rowid DESC LIMIT ?",
                params,
            )
            rows = cur.fetchall()
        return [self._row_to_event(r) for r in rows]

    def get_event(self, event_id: str) -> WatcherEvent | None:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM watcher_events WHERE id = ?", (event_id,))
            r = cur.fetchone()
        return self._row_to_event(r) if r else None

    def consume_event(self, event_id: str) -> WatcherEvent:
        ts = _iso(now_utc())
        with self._lock:
            cur = self._conn.execute(
                "UPDATE watcher_events SET consumed_at = ? WHERE id = ? AND consumed_at IS NULL",
                (ts, event_id),
            )
            self._conn.commit()
            if cur.rowcount == 0:
                # Either already consumed or unknown id.
                check = self._conn.execute(
                    "SELECT * FROM watcher_events WHERE id = ?", (event_id,)
                ).fetchone()
                if check is None:
                    raise KeyError(f"watcher_event not found: {event_id}")
                return self._row_to_event(check)
            row = self._conn.execute("SELECT * FROM watcher_events WHERE id = ?", (event_id,)).fetchone()
        return self._row_to_event(row)

    # ─── job_runs ─────────────────────────────────────────────────────────

    def record_job_run(
        self,
        *,
        watch_spec_id: str | None,
        started_at: datetime,
        finished_at: datetime | None,
        status: str,
        events_created: int = 0,
        error: str | None = None,
    ) -> str:
        from hermes_telegram_ads.watcher.models import new_id

        run_id = new_id()
        with self._lock:
            self._conn.execute(
                "INSERT INTO job_runs "
                "(id, watch_spec_id, started_at, finished_at, status, events_created, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    watch_spec_id,
                    _iso(started_at),
                    _iso(finished_at),
                    status,
                    int(events_created),
                    error,
                ),
            )
            self._conn.commit()
        return run_id

    # ─── row <-> model mapping ────────────────────────────────────────────

    @staticmethod
    def _spec_to_row(spec: WatchSpec) -> dict[str, Any]:
        return {
            "id": spec.id,
            "project_id": spec.project_id,
            "account_id": spec.account_id,
            "account_token_hash": spec.account_token_hash,
            "ad_id": None if spec.ad_id is None else str(spec.ad_id),
            "kind": spec.kind,
            "interval_sec": int(spec.interval_sec),
            "enabled": 1 if spec.enabled else 0,
            "thresholds": _dumps(spec.thresholds) or "{}",
            "notify": 1 if spec.notify else 0,
            "invoke_agent": 1 if spec.invoke_agent else 0,
            "created_by": spec.created_by,
            "created_at": _iso(spec.created_at),
            "updated_at": _iso(spec.updated_at),
            "last_run_at": _iso(spec.last_run_at),
            "next_run_at": _iso(spec.next_run_at),
            "expires_at": _iso(spec.expires_at),
        }

    @staticmethod
    def _row_to_spec(r: sqlite3.Row) -> WatchSpec:
        return WatchSpec.model_validate(
            {
                "id": r["id"],
                "project_id": r["project_id"],
                "account_id": r["account_id"],
                "account_token_hash": r["account_token_hash"],
                "ad_id": r["ad_id"],
                "kind": r["kind"],
                "interval_sec": r["interval_sec"],
                "enabled": bool(r["enabled"]),
                "thresholds": _loads(r["thresholds"]) or {},
                "notify": bool(r["notify"]),
                "invoke_agent": bool(r["invoke_agent"]),
                "created_by": r["created_by"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "last_run_at": r["last_run_at"],
                "next_run_at": r["next_run_at"],
                "expires_at": r["expires_at"],
            }
        )

    @staticmethod
    def _ad_snapshot_to_row(s: AdSnapshot) -> dict[str, Any]:
        return {
            "id": s.id,
            "project_id": s.project_id,
            "account_id": s.account_id,
            "account_token_hash": s.account_token_hash,
            "ad_id": str(s.ad_id),
            "title": s.title,
            "status": s.status,
            "raw_status": s.raw_status,
            "rejection_reason": s.rejection_reason,
            "budget": s.budget,
            "spent": s.spent,
            "remaining_budget": s.remaining_budget,
            "cpm": s.cpm,
            "views": s.views,
            "clicks": s.clicks,
            "ctr": s.ctr,
            "observed_at": _iso(s.observed_at),
            "source": s.source,
            "raw": _dumps(s.raw),
        }

    @staticmethod
    def _row_to_ad_snapshot(r: sqlite3.Row) -> AdSnapshot:
        return AdSnapshot.model_validate(
            {
                "id": r["id"],
                "project_id": r["project_id"],
                "account_id": r["account_id"],
                "account_token_hash": r["account_token_hash"],
                "ad_id": r["ad_id"],
                "title": r["title"],
                "status": r["status"],
                "raw_status": r["raw_status"],
                "rejection_reason": r["rejection_reason"],
                "budget": r["budget"],
                "spent": r["spent"],
                "remaining_budget": r["remaining_budget"],
                "cpm": r["cpm"],
                "views": r["views"],
                "clicks": r["clicks"],
                "ctr": r["ctr"],
                "observed_at": r["observed_at"],
                "source": r["source"],
                "raw": _loads(r["raw"]),
            }
        )

    @staticmethod
    def _account_snapshot_to_row(s: AccountSnapshot) -> dict[str, Any]:
        return {
            "id": s.id,
            "project_id": s.project_id,
            "account_id": s.account_id,
            "account_token_hash": s.account_token_hash,
            "balance": s.balance,
            "budget": s.budget,
            "spent": s.spent,
            "observed_at": _iso(s.observed_at),
            "source": s.source,
            "raw": _dumps(s.raw),
        }

    @staticmethod
    def _row_to_account_snapshot(r: sqlite3.Row) -> AccountSnapshot:
        return AccountSnapshot.model_validate(
            {
                "id": r["id"],
                "project_id": r["project_id"],
                "account_id": r["account_id"],
                "account_token_hash": r["account_token_hash"],
                "balance": r["balance"],
                "budget": r["budget"],
                "spent": r["spent"],
                "observed_at": r["observed_at"],
                "source": r["source"],
                "raw": _loads(r["raw"]),
            }
        )

    @staticmethod
    def _resource_snapshot_to_row(s: ResourceSnapshot) -> dict[str, Any]:
        return {
            "id": s.id,
            "project_id": s.project_id,
            "resource_type": s.resource_type,
            "resource_id": s.resource_id,
            "account_id": s.account_id,
            "account_token_hash": s.account_token_hash,
            "ad_id": None if s.ad_id is None else str(s.ad_id),
            "observed_at": _iso(s.observed_at),
            "source": s.source,
            "data": _dumps(s.data) or "{}",
        }

    @staticmethod
    def _row_to_resource_snapshot(r: sqlite3.Row) -> ResourceSnapshot:
        return ResourceSnapshot.model_validate(
            {
                "id": r["id"],
                "project_id": r["project_id"],
                "resource_type": r["resource_type"],
                "resource_id": r["resource_id"],
                "account_id": r["account_id"],
                "account_token_hash": r["account_token_hash"],
                "ad_id": r["ad_id"],
                "observed_at": r["observed_at"],
                "source": r["source"],
                "data": _loads(r["data"]) or {},
            }
        )

    @staticmethod
    def _event_to_row(e: WatcherEvent) -> dict[str, Any]:
        return {
            "id": e.id,
            "project_id": e.project_id,
            "source": e.source,
            "event_type": e.event_type,
            "severity": e.severity,
            "account_id": e.account_id,
            "account_token_hash": e.account_token_hash,
            "ad_id": None if e.ad_id is None else str(e.ad_id),
            "watch_spec_id": e.watch_spec_id,
            "previous": _dumps(e.previous),
            "current": _dumps(e.current),
            "reason": e.reason,
            "recommended_agent_action": e.recommended_agent_action,
            "created_at": _iso(e.created_at),
            "dedupe_key": e.dedupe_key,
            "consumed_at": _iso(e.consumed_at),
        }

    @staticmethod
    def _row_to_event(r: sqlite3.Row) -> WatcherEvent:
        return WatcherEvent.model_validate(
            {
                "id": r["id"],
                "project_id": r["project_id"],
                "source": r["source"],
                "event_type": r["event_type"],
                "severity": r["severity"],
                "account_id": r["account_id"],
                "account_token_hash": r["account_token_hash"],
                "ad_id": r["ad_id"],
                "watch_spec_id": r["watch_spec_id"],
                "previous": _loads(r["previous"]),
                "current": _loads(r["current"]),
                "reason": r["reason"],
                "recommended_agent_action": r["recommended_agent_action"],
                "created_at": r["created_at"],
                "dedupe_key": r["dedupe_key"],
                "consumed_at": r["consumed_at"],
            }
        )
