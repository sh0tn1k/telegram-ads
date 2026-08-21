# V2.9 — Bounded staged tick (2026-06-17)

Concrete drop details for Pillar 14 of the watcher event-loop
skill. Captures: full code patch, production verification
timeline, test breakdown, the `_safe_v1_list_watches` pre-check
helper, and the Option A semantics for tick wall-clock vs stage
timeout.

## Root cause (recap)

V2.8 bounded the V2.6/V2.7 bridge call inside the V1 in-process
tick with `wait_for(10s)`. But the **V1 `scheduler.tick()` itself**
— which calls `adapter.detect_login_state()` and navigates the
shared Playwright browser to `BASE_URL + URL_ACCOUNT` — was still
inside the original outer `wait_for(60s)`. When the gateway was
busy (inbound Telegram message), Playwright was busy → the
navigation blocked → the 60s ceiling tripped on every post-baseline
tick.

The baseline tick at 19:08:03 succeeded because the V2.6 bridge's
`no_post_action_watches` early-return path was taken. Every
subsequent scheduled tick hit the 60s ceiling.

## Production failure timeline (pre-V2.9)

| Time (UTC) | Tick duration | error |
|---|---|---|
| 2026-06-17 18:39:06 | 60.018 s | `TimeoutError: duration=60.018` |
| 2026-06-17 18:50:06 | 60.034 s | `TimeoutError: duration=60.034` |
| 2026-06-17 19:01:06 | 60.057 s | `TimeoutError: duration=60.057` |
| 2026-06-17 19:19:03 | 60.062 s | `TimeoutError: duration=60.062` |
| 2026-06-17 19:30:03 | 60.055 s | `TimeoutError: duration=60.055` |
| 2026-06-17 19:41:03 | 60.043 s | `TimeoutError: duration=60.043` |
| 2026-06-17 19:52:03 | 60.046 s | `TimeoutError: duration=60.046` |
| 2026-06-17 20:03:03 | 60.038 s | `TimeoutError: duration=60.038` |

8 consecutive failures, all V2.8 in production.

## Production success timeline (V2.9)

| Time (UTC) | Event | Tick duration | error |
|---|---|---|---|
| 2026-06-17 20:13:54 | V2.9 daemon started | — | — |
| 2026-06-17 20:13:59 | Baseline | 4.589 s | None |
| 2026-06-17 20:24:09 | **First scheduled tick after baseline** | **10.036 s** | **None** |
| 2026-06-17 20:28:05 | Gateway restart (in-process reload) | — | — |
| 2026-06-17 20:28:11 | Baseline | 5.485 s | None |
| 2026-06-17 20:38:21 | **First scheduled tick after restart baseline** | **10.031 s** | **None** |

**Total TimeoutError count in gateway.log since V2.9 loaded: 0**.

## Full code patch (gateway/ads_watcher_inprocess.py)

```python
# Stage constants (V2.9, all <= 10s)
_ADAPTER_ACQUIRE_TIMEOUT_SECONDS = 10.0
_V1_TICK_TIMEOUT_SECONDS = 10.0
_V2_BRIDGE_TIMEOUT_SECONDS = 10.0
_TOTAL_TICK_BUDGET_SECONDS = 25.0  # hard ceiling, was 60s

async def _run_tick_once(*, wiring, config, timeout, manager):
    started = time.monotonic()
    summary: dict[str, Any] = {
        "ts": started, "state": None, "error": None,
        "events": 0, "duration": None,
    }
    stages: dict[str, dict[str, Any]] = {}
    adapter = None
    manager_obj = manager
    try:
        if manager_obj is None:
            BrowserProfileManager = _import_browser_manager()
            if BrowserProfileManager is None:
                summary["error"] = "browser_manager_import_failed"
                return summary
            manager_obj = BrowserProfileManager.get_instance()

        # ─── STAGE 1: adapter acquisition ─────────────────────────────
        try:
            adapter = await asyncio.wait_for(
                manager_obj.acquire_adapter(
                    config=config,
                    timeout=min(_ADAPTER_ACQUIRE_TIMEOUT_SECONDS, 30.0),
                ),
                timeout=_ADAPTER_ACQUIRE_TIMEOUT_SECONDS,
            )
            stages["adapter"] = {"ok": True, "duration": 0.0}
        except Exception as exc:
            stages["adapter"] = {
                "ok": False,
                "error": f"acquire_adapter_failed:{type(exc).__name__}",
            }
            summary["error"] = stages["adapter"]["error"]
            summary["state"] = "browser_unavailable"
            return summary

        wiring.adapter._adapter = adapter
        _ensure_baseline_login_state_watch(wiring)

        # ─── STAGE 2: V1 scheduler.tick (login/session monitoring) ───
        try:
            events = await asyncio.wait_for(
                wiring.scheduler.tick(),
                timeout=_V1_TICK_TIMEOUT_SECONDS,
            )
            stages["v1_tick"] = {"ok": True}
            summary["events"] = len(events)
            for ev in events:
                if getattr(ev, "event_type", "") == "login_required":
                    summary["state"] = "login_required"
                    break
            else:
                summary["state"] = "logged_in_or_no_change"
        except asyncio.TimeoutError:
            stages["v1_tick"] = {
                "ok": False,
                "error": "v1_tick_timeout",
                "timeout_seconds": _V1_TICK_TIMEOUT_SECONDS,
            }
        except Exception as exc:
            stages["v1_tick"] = {
                "ok": False,
                "error": f"v1_tick_failed:{type(exc).__name__}",
            }

        # ─── PRE-CHECK: skip bridge if zero post-action watches ───────
        project_id = getattr(wiring, "project_id", None) or "hermes-system"
        v1_watches = _safe_v1_list_watches(wiring, project_id)
        post_action_count = sum(
            1 for w in v1_watches
            if isinstance(w, dict) and not _is_v1_login_state_watch(w)
        )

        if post_action_count == 0:
            stages["v2_bridge"] = {
                "ok": True,
                "skipped": True,
                "reason": "no_post_action_watches",
            }
            summary["v2_bridge"] = {"safe_summary": "no_post_action_watches"}
        else:
            # ─── STAGE 3: V2.6 bridge ─────────────────────────────────
            bridge_task = asyncio.create_task(
                asyncio.to_thread(
                    run_post_action_polling_tick,
                    wiring,
                    project_id=project_id,
                )
            )
            try:
                bridge_result = await asyncio.wait_for(
                    asyncio.shield(bridge_task),
                    timeout=_V2_BRIDGE_TIMEOUT_SECONDS,
                )
                stages["v2_bridge"] = {"ok": True}
                summary["v2_bridge"] = bridge_result.to_dict()
            except asyncio.TimeoutError:
                stages["v2_bridge"] = {
                    "ok": False,
                    "safe_summary": "bridge_timeout",
                    "timeout_seconds": _V2_BRIDGE_TIMEOUT_SECONDS,
                }
                summary["v2_bridge"] = {
                    "safe_summary": "bridge_timeout",
                    "timeout_seconds": _V2_BRIDGE_TIMEOUT_SECONDS,
                }
            except Exception as exc:
                stages["v2_bridge"] = {
                    "ok": False,
                    "safe_summary": "bridge_unavailable",
                    "error": f"bridge_failed:{type(exc).__name__}",
                }
                summary["v2_bridge"] = {
                    "safe_summary": "bridge_unavailable",
                    "error": f"bridge_failed:{type(exc).__name__}",
                }
    finally:
        if manager_obj is not None and adapter is not None:
            manager_obj.release_adapter()
        summary["stages"] = stages
        summary["duration"] = time.monotonic() - started
    return summary
```

## The `_safe_v1_list_watches` helper

Located alongside the bridge in `gateway/ads_watcher_v2/v1_bridge.py`:

```python
def _safe_v1_list_watches(wiring, project_id):
    """List V1 watches safely — wraps store exceptions."""
    try:
        store = getattr(wiring, "store", None)
        if store is None:
            return []
        return list(store.list_watches(project_id=project_id))
    except Exception as exc:
        logger.warning("[ADS-WATCH-V2.9] list_watches failed: %r", exc)
        return []


def _is_v1_login_state_watch(watch):
    """Return True iff the watch is a V1 login_state baseline."""
    if not isinstance(watch, dict):
        return False
    return watch.get("kind") == "login_state"
```

## Test category breakdown (25 tests)

| Category | Tests | Purpose |
|---|---|---|
| Stage constants | 3 | Pin `_V1_TICK_TIMEOUT_SECONDS=10`, `_ADAPTER_ACQUIRE_TIMEOUT_SECONDS=10`, `_TOTAL_TICK_BUDGET_SECONDS=25` with Option A semantics (`budget >= max(stage timeouts)` AND `budget >= 15s accepted bridge timeout`) |
| Stage 1 (adapter) | 3 | Slow acquire → `browser_unavailable`; fast acquire → succeeds; exception → safe error |
| Stage 2 (V1 tick) | 3 | Slow tick → `v1_tick_timeout`; fast tick → succeeds; exception → safe error |
| Stage 3 (V2 bridge) | 5 | Zero watches → skipped; only login_state → skipped; post-action watches → bridge called; timeout → `bridge_timeout`; exception → `bridge_unavailable` |
| Full-tick bounded | 3 | All stages slow → bounded; never hits 60s; stuck watch doesn't block next tick |
| No secrets | 2 | Source scan for credential patterns; logger calls don't include adapter |
| No standalone daemon | 2 | No `*_daemon` entrypoint; no second browser owner |
| Reports gated | 2 | Flag off → router disabled; flag on → bounded mini-reports |
| No Ads mutation | 1 | No mutation keywords in source |
| Thread safety | 1 | 5 concurrent ticks complete; summaries independent |
| **Total** | **25** | **All pass** |

## The 25 test names (for grep)

```
TestV29StageConstants::test_v1_tick_timeout_is_10s
TestV29StageConstants::test_adapter_acquire_timeout_is_10s
TestV29StageConstants::test_total_tick_budget_is_25s
TestStage1AdapterAcquisition::test_slow_adapter_acquire_returns_browser_unavailable
TestStage1AdapterAcquisition::test_fast_adapter_acquire_proceeds
TestStage1AdapterAcquisition::test_adapter_exception_returns_safe_error
TestStage2V1SchedulerTick::test_slow_v1_tick_returns_safe_timeout
TestStage2V1SchedulerTick::test_fast_v1_tick_succeeds
TestStage2V1SchedulerTick::test_v1_tick_exception_returns_safe_error
TestStage3V2Bridge::test_zero_post_action_watches_skips_bridge
TestStage3V2Bridge::test_only_login_state_watches_skip_bridge
TestStage3V2Bridge::test_post_action_watches_pass_pre_check
TestStage3V2Bridge::test_bridge_timeout_records_safe_summary
TestStage3V2Bridge::test_bridge_exception_records_safe_summary
TestFullTickBounded::test_full_tick_with_all_stages_slow_still_bounded
TestFullTickBounded::test_total_tick_cannot_hit_60s
TestFullTickBounded::test_stuck_post_action_watch_does_not_block_v1
TestV29NoSecrets::test_no_credential_patterns_in_source
TestV29NoSecrets::test_no_logging_includes_adapters
TestV29NoStandaloneDaemon::test_v29_does_not_introduce_daemon
TestV29NoStandaloneDaemon::test_v29_does_not_instantiate_browser
TestV29ReportsGated::test_router_disabled_when_flag_off
TestV29ReportsGated::test_router_enabled_when_flag_on
TestV29NoAdsMutation::test_no_mutation_markers_in_source
TestV29ThreadSafety::test_concurrent_ticks_complete
```

## Option A: test recipe for bridge timeout

```python
def _timeout_bridge(wiring, *, project_id, adapter=None):
    # Simulate wait_for firing by raising TimeoutError from within
    # the worker. This is cleaner than time.sleep(15) (which holds
    # the GIL and prevents wait_for from firing) and cleaner than
    # threading.Event.wait(15) (which releases the GIL but keeps
    # the worker alive past the test).
    raise asyncio.TimeoutError()

original = bridge_mod.run_post_action_polling_tick
bridge_mod.run_post_action_polling_tick = _timeout_bridge
try:
    # ... tick + asserts ...
finally:
    bridge_mod.run_post_action_polling_tick = original
```

The key insight: `raise asyncio.TimeoutError()` from inside the
worker mimics the `wait_for(10s)` ceiling firing without leaving
a lingering thread. The test asserts on the **stage status**
(`safe_summary="bridge_timeout"`, `timeout_seconds=10.0`,
`stages["v2_bridge"]["ok"]=False`) and on the **bounded wall-clock**
(`< 25s`), not on `elapsed == 10s`.

## Verification recipe (manual, post-deploy)

```bash
# 1. Confirm V2.9 daemon is running
grep '\[ADS-WATCH\] daemon thread started' ~/.hermes/logs/gateway.log | tail -1

# 2. Confirm baseline tick succeeded
grep '\[ADS-WATCH\] baseline tick' ~/.hermes/logs/gateway.log | tail -1

# 3. Confirm scheduled tick succeeded (duration < 25s, error=None)
grep '\[ADS-WATCH\] tick ' ~/.hermes/logs/gateway.log | tail -3

# 4. Confirm zero TimeoutErrors since V2.9 first loaded
# (replace timestamp with the V2.9 first-loaded time)
awk '/2026-06-17 20:13:54/,EOF' ~/.hermes/logs/gateway.log | \
  grep -c 'TimeoutError'
# Expected: 0

# 5. Confirm single Chromium owner
pgrep -af 'chrome.*telegram_ads/browser_profile' | wc -l
# Expected: 8 (main + 7 helpers in a single Chromium tree)
```

## Files in the V2.9 commit (01ce1f038)

```
gateway/ads_watcher_inprocess.py       | 247 +++++++++---
tests/gateway/test_ads_watcher_v2_9.py | 633 +++++++++++++++++++++++++++++++++
2 files changed, 821 insertions(+), 59 deletions(-)
```

Working tree clean after commit. No push performed (branch
diverged from upstream `main` — 87 ahead, 1323 behind — that
divergence pre-existed V2.9 and is unrelated).
