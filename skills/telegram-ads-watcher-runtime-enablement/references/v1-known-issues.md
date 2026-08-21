# V1 known issues (pre-existing, not introduced by V2.x)

These are bugs / quirks in the V1 `hermes_telegram_ads.watcher`
package (and V1's in-process gateway loop) that **predate** the
V2.0 → V2.7 work. If you see them in post-verification logs,
**report them as observations, not as defects introduced by
this task**. Do not attempt to fix them during a runtime
enablement task — fixing V1 is a separate approval (likely a
V1.1 or V2.9 drop).

## 1. V1 `TimeoutError` on every post-baseline tick — **FIXED in V2.9** (bounded staged tick)

**Status (post-V2.9, in development as of 2026-06-17):**
V2.9 splits the V1 in-process tick into **three bounded stages**
with independent short timeouts:

| Stage | Timeout | Behavior on timeout |
|---|---|---|
| 1. Adapter acquisition | 10 s | `state="browser_unavailable"`, return early |
| 2. V1 `scheduler.tick()` | 10 s | record `v1_tick_timeout`, continue to stage 3 |
| 3. V2.6 bridge | 10 s | record `bridge_timeout`, continue |

Plus a **pre-check optimization**: list V1 watches and **skip
the bridge entirely** if there are zero non-`login_state`
watches (idle tick no longer touches the V2 adapter at all).
Idle ticks complete in **< 1 s** instead of 60 s.

Total tick budget: **25 s** hard ceiling
(`_TOTAL_TICK_BUDGET_SECONDS`).

**History of fixes:**

- **V2.0–V2.7 (commits 34f348ac1 → bee7f4fbe):** baseline works,
  every post-baseline tick hits 60 s `TimeoutError`.
- **V2.8 (commit `660816f56`, 2026-06-17):** PARTIAL fix. The
  V2.6/V2.7 bridge call is bounded by 10 s. The outer 60 s
  `wait_for` on V1's `wiring.scheduler.tick()` is **not** fixed.
  Verified in production: baseline succeeds, first post-baseline
  at +11 min still hits `error=TimeoutError: duration=60.062`.
- **V2.9 (in development 2026-06-17, commit pending):** FULL fix.
  Stage-level timeouts on every stage. Verified: idle ticks
  complete in <1 s; max tick wall-clock bounded by 25 s
  (though see Pitfall 11 in the main SKILL.md about the Python
  3.11.15 `asyncio.to_thread` + `wait_for` semantics — wall-clock
  may still be bound by a slow worker, but the per-stage `state`
  is correctly recorded).

**Symptom (pre-V2.9, in `~/.hermes/logs/gateway.log`):**

```
[ADS-WATCH] tick state=None events=0 error=TimeoutError: duration=60.04
```

**Frequency (pre-V2.9):** every ~10 minutes (the V1 tick
interval), but irregular — sometimes multiple in a row,
sometimes gaps.

**First observed:** 16:15:12 UTC (well before V2.5/V2.6/V2.7
were designed). **Confirmed still observed in production as
of V2.8 on 2026-06-17 19:19 UTC.**

**Root cause (V2.9-era, after full fix):**

The outer `asyncio.wait_for(60)` in `_run_tick_once` wraps V1's
`wiring.scheduler.tick()`, NOT the V2.6 bridge. V1's
`scheduler.tick()` itself hangs for 60 s on every post-baseline
call. The underlying cause is the **shared singleton Playwright
adapter**:

- V1's `detect_login_state` watch holds the read-only Playwright
  adapter for ~30 s on each call.
- The V2.6 bridge may attempt a quick check on the same adapter
  concurrently.
- The bridge's `acquire_adapter(timeout=30)` times out at 30 s;
  the cumulative wait_for hits 60 s on the outer V1 wrapper.

**V2.9 fix (`gateway/ads_watcher_inprocess.py`, one file,
~150 lines changed, no new module, no restart required to
land):**

- Add three constants:
  `_V1_TICK_TIMEOUT_SECONDS = 10.0`,
  `_ADAPTER_ACQUIRE_TIMEOUT_SECONDS = 10.0`,
  `_TOTAL_TICK_BUDGET_SECONDS = 25.0`.
- Wrap each stage in `asyncio.wait_for(..., timeout=<stage>)`
  with independent `try/except` blocks. A timeout in one stage
  records a safe error and continues to the next stage.
- Pre-check: call `_safe_v1_list_watches(wiring, project_id)`
  and count non-`login_state` watches. If the count is zero,
  skip the V2.6 bridge entirely (the bridge is a no-op when
  there are no post-action watches, so this is just an
  optimization — but it also prevents the bridge from racing
  with the main gateway's Playwright adapter).
- V2.6 bridge: wrap in
  `asyncio.create_task(asyncio.to_thread(...))` +
  `await asyncio.wait_for(asyncio.shield(task), timeout=10)`.
  On `asyncio.TimeoutError` record `bridge_timeout`; the
  worker thread continues in the background but is not awaited.
- Add `summary["stages"]` and `summary["budget_exceeded"]` to
  the result dict for observability.

**Why it does not crash the gateway:** V1's daemon thread is
isolated; an unhandled exception in a daemon thread does not
kill the parent process. V1 also has a try/except in
`_run_tick_once` that captures the error and returns a default
`TickResult(events=0, state=None, error=TimeoutError(...))`.

**What to do in runtime enablement (post-V2.9):**

- **Baseline tick success is necessary but still NOT sufficient
  for "ready for first Ads action".** The post-baseline tick
  exercises different code paths (V1 polling, V2.6 bridge pre-check,
  V2.6 bridge call). Always require a separate
  `NEXT-TICK-VERIFY-N` approval to observe at least one
  post-baseline tick with `error=None` and a small duration.
- **The bridge-timeout stage status is recorded correctly even
  when the wall-clock is bound by a slow worker.** Verify
  `stages["v2_bridge"]["safe_summary"] == "bridge_timeout"`
  OR `stages["v2_bridge"]["ok"] == True`, not just wall-clock
  duration (see Pitfall 11 in the main SKILL.md).
- **Idle ticks (no post-action watches) complete in <1 s.**
  This is the V2.9 optimization: the bridge is not even called.
  Verify by checking `stages["v2_bridge"]["skipped"] == True`
  and `safe_summary == "no_post_action_watches"`.

**What to do in runtime enablement (post-V2.8, pre-V2.9):**

- **The post-baseline TimeoutError is the V2.8-known partial
  state, not a "restart-gap observation".** Reporting it as
  "pre-existing, not introduced by this task" is **incorrect**:
  it IS a known V2.8 limitation with a known V2.9 fix path.
- **Never** attempt to fix the V1 TimeoutError during a
  runtime enablement task — V2.9 is the canonical fix; do not
  re-derive it in this task.
**What to do in runtime enablement (post-V2.8):**

- **Baseline tick success is necessary but NOT sufficient.**
  Always require a separate `NEXT-TICK-VERIFY-N` approval
  before any "first real Ads action" can be approved.
- **The post-baseline TimeoutError is the V2.8-known partial
  state, not a "restart-gap observation".** Reporting it as
  "pre-existing, not introduced by this task" is **incorrect**:
  it IS a known V2.8 limitation with a known V2.9 fix path.
- **Never** attempt to fix the V1 TimeoutError during a
  runtime enablement task — V2.9 is the canonical fix; do not
  re-derive it in this task.

## 2. V1 `telegram_ads_watcher.db` is created on first save_watch

**Symptom:** `ls -la ~/.hermes/telegram_ads_watcher.db` →
"cannot access: No such file or directory".

**Frequency:** every fresh gateway start, until the first real
Ads action.

**Root cause:** V1 creates the SQLite database file lazily on
the first call to `SqliteStore.upsert_watch` or
`SqliteStore.create_event`. The V1 baseline tick (which only
reads login state) does **not** call either, so the file is
never created by a baseline tick alone.

**Why this is expected:** the V1 watcher's baseline
`state=logged_in_or_no_change` is a "no change" event. V1 only
persists watches when state actually changes, OR when a
**new** watch is registered via `register_post_action_watch`
(the V2.7 production hook).

**What to do in runtime enablement:** note in the report that
"DB not yet present; will be created on first real Ads action".
**Do not** flag it as a defect. **Do not** create the DB
manually (e.g. `sqlite3 ~/.hermes/telegram_ads_watcher.db
< schema.sql`) — V1 has its own schema version; manual
schema creation will conflict with V1's auto-migration.

## 3. V1 baseline tick uses a separate code path from V2 polling

**Symptom:** when reading `gateway.log`, you see two
log signatures for ADS-WATCH:

- `[ADS-WATCH] baseline tick state=... events=0 error=None
  duration=<small>` — runs once per gateway start.
- `[ADS-WATCH] tick state=... events=... error=...` — runs every
  ~10 minutes (V1 interval), may error with TimeoutError.

**Why two code paths:** V1's baseline tick is the
`_ensure_baseline_login_state_watch` function in
`gateway/ads_watcher_inprocess.py`. It runs once at gateway
startup to register the login_state watch if it doesn't exist
yet. After the baseline, all subsequent ticks go through
`wiring.scheduler.tick()` which is the V1 polling path that
calls V2.6's `run_polling_tick`.

**Why this matters for runtime enablement:** the baseline tick
**always** succeeds (no TimeoutError, by design — it does not
exercise the V1 polling path). The first thing you should
verify in post-enable is that the baseline tick completed
successfully. **But the baseline alone is not enough** — see
issue #1. A separate `NEXT-TICK-VERIFY-N` approval must
observe at least one post-baseline tick to confirm V2.8 is
effective in production.

## 4. V1 V2 bridge call is skipped on V1 error

**Symptom:** you won't see V2.7 bridge result logs even after
runtime enablement, until the first V1 tick completes without
TimeoutError.

**Why:** `gateway/ads_watcher_inprocess.py:_run_tick_blocking`
returns a `TickResult` on V1 error; the calling code checks
the result and **skips** the V2.7 bridge call when the result
indicates an error. This is by design — the V2 bridge is only
invoked when V1 successfully polled.

**What to do:** this is correct behavior. The bridge will run
on the first successful V1 tick. If you want to see the bridge
run sooner, the V1 TimeoutError needs to be fixed first
(separate approval — V2.9 candidate).

## 5. V1 sync tick is incompatible with the main gateway's asyncio loop — **PARTIALLY fixed in V2.8** (regression confirmed 2026-06-17)

**Status (post-V2.8):** **PARTIALLY fixed** in commit
`660816f56` (2026-06-17). See issue #1 for full root cause
analysis and verified production evidence.

**Symptom (pre-V2.8 and post-V2.8):** see #1 — TimeoutError on
every tick after baseline.

**Root cause (V2.8-era, partial fix scope):**

- **Fixed by V2.8:** the V2.6/V2.7 bridge call no longer
  deadlocks the daemon thread's event loop. The 10s
  `wait_for` and the worker-thread path in
  `production_adapter` prevent the bridge from being the
  60s blocker.
- **Not fixed by V2.8:** V1's own `wiring.scheduler.tick()`
  still hangs for 60s on the outer `asyncio.wait_for(60)`
  because the V1 polling path itself does Playwright adapter
  acquisition that races with concurrent V2.6 bridge calls.

**V2.8 fix:** the V1 tick now wraps the V2.6/V2.7 bridge call
in `asyncio.to_thread(...)` + `asyncio.wait_for(..., timeout=10)`,
so the bridge runs in a worker thread and is bounded by a
short timeout. The `production_adapter.get_state_sync` path
also probes `asyncio.get_running_loop()` and skips the broken
`asyncio.run` path. The 60s ceiling is **not** eliminated;
only the bridge is bounded.

**What to do in runtime enablement (post-V2.8):**

- Baseline tick is bounded → V2.8 partial fix verified at
  baseline level.
- **Post-baseline tick is NOT bounded → V2.8 fix did not
  reach the V1 polling path.** This is the V2.8-known
  partial state.
- A separate `NEXT-TICK-VERIFY-N` approval is mandatory before
  any "first real Ads action" can be approved.

## 6. V1 watch ID vs V2 watch ID

**Symptom:** when looking at `telegram_ads_watcher.db:watches`,
the V1 IDs are UUIDs (`v1_internal_id` column), not the
human-readable V2 watch IDs.

**Why:** V1's `SqliteStore.upsert_watch` always mints a fresh
UUID for the `id` column. V2's `watch_id` (e.g.
`create_ad:moderation_result`) is stored in the `external_id`
column for cross-reference.

**What to do:** for any future debugging, look up watches by
**both** IDs:
- By V1 UUID: `SELECT * FROM watches WHERE id = ?`
- By V2 external_id: `SELECT * FROM watches WHERE external_id = ?`

**Do not** assume the V2 `watch_id` is the row's primary key.

## 7. V1 event_type enum is closed (19 entries)

**Symptom:** if V2 adds a new event_type that V1 doesn't know
about, V2.6's `persist_normalized_events` raises `KeyError`.

**Why:** V1 has a closed-set `EventType` enum (19 entries,
named after the original V1 watch types). V2.6's
`V2_EVENT_TYPE_TO_STORE_EVENT_TYPE` maps all 19 V2 emitter
types to V1 targets. Any new V2 event_type must be added to
this map first.

**What to do:** if a future V-N drop adds a new V2 event type,
edit `gateway/ads_watcher_v2/wiring.py`'s
`V2_EVENT_TYPE_TO_STORE_EVENT_TYPE` map and add a new V1 target
if needed. Update `tests/gateway/test_ads_watcher_vN.py` to
cover the new mapping.

## 8. V1 `safe_summary` is a property, not a method

**Symptom:** if you try to call `spec.safe_summary()` as a
method, you get a `TypeError: 'str' object is not callable`.

**Why:** V2's `WatchSpec.safe_summary` is a `@property` that
returns a string. V2.5's `format_mini_report` reads it as
`spec.safe_summary` (no parens).

**What to do:** never call `safe_summary()` as a function. If
you see this in code, it's a V2.5-era bug — patch it.

## 9. V1 `login_state` watch uses a fixed cron schedule

**Symptom:** the login_state baseline tick runs once per gateway
start, not on a recurring schedule.

**Why:** V1's `login_state` watch is registered with
`interval_seconds=None`, which means "tick on the next manual
trigger". The V1 in-process loop's baseline call is the only
manual trigger. After the baseline, the watch sits idle until
the next gateway restart.

**What to do:** this is the V1 design — login state is checked
on gateway start, not on a recurring schedule. If the operator wants
recurring login checks, that's a V1.1 or V2.8 feature
(separate approval).

## 10. V1's `tma_token` (Telegram Mini App token) is never used

**Symptom:** the unit env may have `TMA_TOKEN` set (the operator's
home mini-app token), but V1 never references it.

**Why:** V1's watcher is for **Telegram Ads**, not for Telegram
Mini Apps. `TMA_TOKEN` is for an entirely different feature.

**What to do:** ignore `TMA_TOKEN` in runtime enablement. It's
unrelated to the watcher. If you grep for "tma_token" in the
watcher code, you may find a placeholder for the V2.7 router's
secret-scrub list (which intentionally blocks `tma_token=...`
from being printed in reports) — that is correct, not a defect.
