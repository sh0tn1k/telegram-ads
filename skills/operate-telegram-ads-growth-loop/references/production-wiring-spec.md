# Production Runtime Wiring Specification
# Telegram Ads Autonomous Growth Operator — Gateway Integration
# STATUS: DESIGN ONLY — NOT IMPLEMENTED

## Production Rollout Sequence (STRICT ORDER)

### Phase 0: Deploy code with operator DISABLED

```yaml
# config.yaml
telegram_ads_operator:
  enabled: false
```

- Gateway starts normally with NO operator components loaded.
- No schema migration, no event store, no consumer construction.

### Phase 1: Inspect production schema (read-only)

```python
from agent.telegram_ads_operator.event_store import EventStore

store = EventStore(
    db_path="~/.hermes/data/ads_watcher.sqlite3",
    production_mode=False,
    auto_migrate=False,  # NO DDL
)
info = store.inspect_schema()
print(f"Current: v{info['current_version']}, target: v{info['target_version']}")
print(f"Migration required: {info['migration_required']}")
```

### Phase 2: Separate approval for DB migration

the operator approves explicit migration command:

```python
result = store.apply_migrations()
# {"applied_versions": [2, 3, 4], "error": ""}
```

### Phase 3: Verify schema and indexes

```python
store = EventStore(
    db_path="~/.hermes/data/ads_watcher.sqlite3",
    production_mode=True,  # Read-only check — no DDL
)
info = store.inspect_schema()
assert info["current_version"] == 4
assert "event_delivery" in info["tables"]
```

### Phase 4: Construct runtime stores (all production_mode=True)

```python
from agent.telegram_ads_operator.event_store import EventStore
from agent.telegram_ads_operator.snapshot_store import SqliteSnapshotStore
from agent.telegram_ads_operator.metrics_adapter import MetricsAdapter

event_store = EventStore(
    db_path="~/.hermes/data/ads_watcher.sqlite3",
    production_mode=True,  # Schema check passes — already migrated
)
snapshot_store = SqliteSnapshotStore(db_path=event_store._db_path)
metrics = MetricsAdapter(allowlist=[
    "telegram_ads_get_ad",
    "telegram_ads_get_ad_stats",
    "telegram_ads_get_ad_budget_status",
])
```

### Phase 5: Configure real AgiTeamTaskSink

```python
from agent.telegram_ads_operator.task_sink import AgiTeamTaskSink

# Wire real Task Board callable from gateway tool registry
def task_board_callable(tool_name: str, **kwargs) -> dict:
    # Dispatch to agi_team_task_create via the gateway's tool registry.
    raise NotImplementedError("Wire real AGI Team Task Board callable here")

task_sink = AgiTeamTaskSink(
    task_board_callable=task_board_callable,
    production=True,
)
assert task_sink.is_durable is True
assert task_sink.is_production_capable is True
```

### Phase 6: Construct Poller + Consumer

```python
from agent.telegram_ads_operator.runtime import (
    GrowthOperatorPoller,
    GrowthOperatorConsumer,
)

poller = GrowthOperatorPoller(
    metrics_adapter=metrics,
    event_store=event_store,
    snapshot_store=snapshot_store,
)

consumer = GrowthOperatorConsumer(
    event_store=event_store,
    task_sink=task_sink,
    production_mode=True,  # Enforces is_durable + is_production_capable
)
```

### Phase 7: Enable operator in gateway (reports OFF)

```yaml
# config.yaml
telegram_ads_operator:
  enabled: true
  poll_interval_sec: 600
  production_mode: true
  reports_enabled: false  # Separate approval gate
```

### Phase 8: Enable bounded notifications (reports ON) — separate approval

Requires `bounded_notification_authorization` in config with TTL, event type allowlist, max messages/hour.

---

## DB Migration

Migration is EXPLICIT. `EventStore.__init__(production_mode=True)` performs a
read-only schema check and raises `SchemaMigrationRequired` if the DB is
outdated, or `IncompatibleSchemaError` if unknown/incompatible. **No DDL runs
at gateway startup.** Migration must be explicitly invoked via
`EventStore.apply_migrations()` after separate the operator approval.

## Files for Future Change

| File | Change |
|------|--------|
| `hermes_cli/gateway.py` | Import and construct operator components per Phase 4-6 |
| `~/.hermes/config.yaml` | New section: `telegram_ads_operator` (Phase 0, 7) |

## Failure Behavior

| Failure | Behavior |
|---------|----------|
| Outdated schema | `SchemaMigrationRequired` — gateway fails HEALTHY |
| Unknown schema | `IncompatibleSchemaError` — gateway fails HEALTHY |
| No task sink | `MissingTaskSinkConfiguration` in Consumer.__init__ |
| Non-durable sink | `MissingTaskSinkConfiguration` — "not durable" |
| Non-production-capable sink | `MissingTaskSinkConfiguration` — "not production-capable" |
| Task Board callable fails | Event → retry_wait → dead_letter |
| Empty task_id from callable | Event → retry_wait, not consumed |

## Health Checks

- `event_store.inspect_schema()` — verify version, tables, columns
- `event_store.get_stats()` — count per status
- `consumer.consume_one()` — returns None if no eligible events
