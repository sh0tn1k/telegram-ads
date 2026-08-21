# V2.8 async-tick stabilization (concrete drop)

The V2.8 fix is the **smallest, safest** patch in the V2.x
lineage. It is **3 files, ~100 lines, no new module**. This
reference records the session detail (PHASE 1-6, code diffs,
test counts, commit chain) so future V-N drops can reference
the actual changes without re-deriving them.

## Session metadata

- **AR:** AR-ADS-WATCHER-V2_8
- **Project:** hermes-system
- **Date:** 2026-06-17
- **Approver:** the operator
- **Constraint:** "code + tests + local commit only; no push;
  no real Telegram Ads actions; no runtime env changes; no
  gateway restart unless a later approval explicitly allows it"
- **Commit:** `660816f56`
- **Predecessor chain:** V1 → V2.0 (43ef86532) → V2.5
  (bee7f4fbe) → V2.6 (16e9aa3af) → V2.7 (be28bd1e1) → **V2.8
  (660816f56)**

## PHASE 1 — Diagnosis

The V1 in-process daemon thread (in
`gateway/ads_watcher_inprocess.py`) runs `_run_tick_blocking`
which wraps `_run_tick_once` in `asyncio.run`. After V1's
`wiring.scheduler.tick()` completes, the V2.6 bridge call
`run_post_action_polling_tick(wiring, project_id=...)` is
invoked **directly inside the running event loop**. The bridge
is a **sync** function whose `get_state_sync` path tried
`asyncio.run(...)` first, which raised `RuntimeError: cannot
be called from a running loop`, then fell through to
`_run_in_worker_thread` with a 30s `thread.join`. Combined
with V1's outer 60s `wait_for`, the whole tick reliably hit
the 60s ceiling.

**Pattern in gateway.log before V2.8:**

```
[ADS-WATCH] baseline tick state=logged_in_or_no_change events=0 error=None duration=4.751
[ADS-WATCH] tick state=None events=0 error=TimeoutError: duration=60.04
[ADS-WATCH] tick state=None events=0 error=TimeoutError: duration=60.04
...
```

First TimeoutError observed 16:15:12 UTC, 11 min after daemon
start. **Pre-existing** since V1, persisted through V2.0–V2.7,
not introduced by V2.8.

## PHASE 2 — Fix design

Three small, surgical changes — no new daemon, no new browser
owner, no new env flag, no gateway restart, no real Ads call,
no second owner path:

| File | Change | Lines |
|---|---|---|
| `gateway/ads_watcher_inprocess.py` | Add `_V2_BRIDGE_TIMEOUT_SECONDS = 10.0`; wrap V2.6 bridge in `asyncio.to_thread(...)` + `asyncio.wait_for(..., timeout=10)`; on `asyncio.TimeoutError` record safe summary | +30 |
| `gateway/ads_watcher_v2/production_adapter.py` | `get_state_sync` / `get_account_state_sync` probe `asyncio.get_running_loop()` first and go straight to worker-thread path; no-loop path keeps `asyncio.run` (faster for CLI/tests) | +40 |
| `gateway/ads_watcher_v2/production_adapter.py` | `_run_in_worker_thread` now takes explicit `timeout_seconds: float = 15.0` (was hard-coded 30s); on stuck worker does best-effort `loop.stop()` and returns `None` | +30 |
| `tests/gateway/test_ads_watcher_v2_8.py` | 20 new tests covering all 12 required categories | +520 |

## PHASE 3 — Tests

20 V2.8 tests, all green in 0.92 s:

```
TestGetStateSyncRunningLoop::test_get_state_sync_inside_running_loop_uses_worker_thread PASSED
TestGetStateSyncRunningLoop::test_get_state_sync_outside_loop_uses_asyncio_run PASSED
TestWorkerThreadTimeout::test_worker_thread_default_timeout_is_15s PASSED
TestWorkerThreadTimeout::test_worker_thread_returns_none_on_timeout PASSED
TestWorkerThreadTimeout::test_worker_thread_completes_under_timeout PASSED
TestV1InProcessTickBridgeWrap::test_v1_uses_to_thread_and_wait_for_for_bridge PASSED
TestV1InProcessTickBridgeWrap::test_v2_bridge_timeout_constant_is_10s PASSED
TestV1InProcessTickBridgeWrap::test_v2_bridge_timeout_returns_safe_summary PASSED
TestEndToEndBridgeDoesNotBlockV1::test_slow_bridge_does_not_block_v1_tick PASSED
TestNextTickRecovery::test_bridge_timeout_does_not_persist_state PASSED
TestV1LoginSessionStillWorks::test_v1_baseline_path_unchanged PASSED
TestV2StillPersistsEvents::test_bridge_signature_unchanged PASSED
TestReportsStillGated::test_router_disabled_when_flag_off PASSED
TestReportsStillGated::test_router_enabled_when_flag_on PASSED
TestNoSecretsInTimeoutLogs::test_timeout_log_does_not_print_secrets PASSED
TestNoSecretsInTimeoutLogs::test_worker_thread_log_does_not_print_secrets PASSED
TestNoStandaloneDaemon::test_no_new_daemon_introduced PASSED
TestNoAdsMutation::test_production_adapter_does_not_mutate PASSED
TestNoAdsMutation::test_v1_in_process_does_not_mutate PASSED
TestThreadSafety::test_concurrent_bridge_calls_complete PASSED
20 passed in 0.92s
```

**9-marker secret scan** (asserted by
`TestNoSecretsInTimeoutLogs`): `session=`, `token=`,
`tma_token=`, `agi_team_bot_token=`, `cookie=`, `set-cookie:`,
`password=`, `otp=`, `Authorization:` — all absent from V1 and
production_adapter source.

## PHASE 4 — Full regression suite

**300 passed, 0 failed** in 14.80 s:

| Suite | Count |
|---|---|
| V2 (test_ads_watcher_v2.py) | 67 |
| V2.5 (test_ads_watcher_v2_5.py) | 55 |
| V2.6 (test_ads_watcher_v2_6.py) | 36 |
| V2.7 (test_ads_watcher_v2_7.py) | 22 |
| **V2.8 (test_ads_watcher_v2_8.py) — new** | **20** |
| V1 in-process (test_ads_watcher_inprocess.py) | 37 |
| KC runtime (test_kc_runtime.py) | 51 |
| config loader (test_telegram_ads_config_loader.py) | 12 |
| typed wrapper (test_telegram_ads_typed_wrapper.py) | 25 |
| planned stop (test_planned_stop_watcher.py) | 11 |
| **Total** | **336** |

## PHASE 5 — Local commit

```bash
git status --short
# M  gateway/ads_watcher_inprocess.py
# M  gateway/ads_watcher_v2/production_adapter.py
# ?? tests/gateway/test_ads_watcher_v2_8.py

git diff --stat
# gateway/ads_watcher_inprocess.py             | 40 ++++++++++++++-
# gateway/ads_watcher_v2/production_adapter.py | 75 ++++++++++++++++++++++++----
# 2 files changed, 102 insertions(+), 13 deletions(-)

git add gateway/ads_watcher_inprocess.py \
        gateway/ads_watcher_v2/production_adapter.py \
        tests/gateway/test_ads_watcher_v2_8.py

git -c user.name='the agent' -c user.email='agent@hermes.local' \
  commit -m "fix(telegram-ads): stabilize in-process watcher async tick" \
          -m "fix post-action watcher sync/async boundary;
prevent 60s TimeoutError from blocking watcher loop;
isolate tick failures from gateway;
preserve V1 login/session monitoring;
add regression tests for daemon-thread tick, timeout recovery, adapter busy, and secret-safe errors."

# [main 660816f56] fix(telegram-ads): stabilize in-process watcher async tick
# 3 files changed, 523 insertions(+), 13 deletions(-)
# create mode 100644 tests/gateway/test_ads_watcher_v2_8.py

git log --oneline -5
# 660816f56 fix(telegram-ads): stabilize in-process watcher async tick    ← V2.8
# be28bd1e1 feat(telegram-ads): integrate watcher event loop with runtime ← V2.7
# 16e9aa3af feat(telegram-ads): wire V2.5 to V1 watcher store (V2.6)     ← V2.6
# bee7f4fbe feat(telegram-ads): wire watcher events to post-action reports ← V2.5
# 43ef86532 feat(telegram-ads): add watcher event loop + post-action specs ← V2.0

git status --short
# (empty — working tree clean)

echo "git push was NOT executed"
```

## PHASE 6 — Confirmation matrix

| Item | Status |
|---|---|
| No push | ✅ local commit only |
| No runtime change | ✅ no env / systemd / config edit |
| No gateway restart | ✅ per the operator's explicit instruction (V2.8 will be picked up on the next restart) |
| No real Ads action | ✅ tests use `_FakeAdsAdapter` and `adapter_factory=...` |
| No Ads mutation | ✅ 7 mutation keywords absent from V1 and production_adapter source |
| No synthetic Telegram message | ✅ no Telegram send path in V2.8 changes |
| No secrets printed | ✅ 9-marker scan in two modules clean |
| No standalone daemon | ✅ v1_bridge has zero `daemon=True`; production_adapter has exactly one `threading.Thread` |
| No deepseek / Xvfb / KC changes | ✅ zero touches to those subsystems |
| No CPM / bid / budget changes | ✅ n/a |
| No payments / refunds | ✅ n/a |
| No login assist / OTP / 2FA / cookies / session tokens accessed | ✅ no such calls added |
| No new watcher systemd service / timer | ✅ none created |

## Stop-condition check (approval)

| Stop condition | Result |
|---|---|
| fix requires broad refactor | **NO** — 102 lines changed, 3 files |
| fix requires standalone daemon | **NO** — uses existing in-process daemon thread |
| fix requires second browser owner | **NO** — singleton via `acquire_adapter` preserved |
| fix requires real Ads calls | **NO** — tests use fakes |
| fix requires secrets | **NO** — zero secret access |
| tests fail and cannot be fixed in this narrow scope | **NO** — 20/20 V2.8 + 300/300 full suite green |

## Open approval-required items (separate approval needed)

1. **Gateway restart** to pick up V2.8 (V2.0–V2.7 are already in
   memory; V2.8 is staged). **The operator must explicitly approve
   `systemctl --user restart hermes-gateway-default.service`**
   before the fix takes effect on the live gateway.
2. **Push to fork** (4 commits ready: V2.5 + V2.6 + V2.7 + V2.8).
3. **Optional DeepSeek review** of V2.8 changes (small,
   surgical — likely a quick sign-off).
4. **First real approved Ads action** to verify end-to-end
   (after restart).

## Lessons captured for future V-N drops

These three lessons are load-bearing for any V-N that touches
the V1 in-process tick. Codify them in Pillar 13's sub-pitfalls
(see SKILL.md):

1. **`asyncio.run` from inside a running loop is a footgun.**
   Always probe `asyncio.get_running_loop()` first. The
   fallback path (worker thread) is fine for the runtime, but
   the boundary **must** be explicit.

2. **Bridge calls inside a long-running tick need a bounded
   timeout.** The outer 60s wait_for is the **outer** safety
   net; the inner bridge needs its own short timeout (≤30s,
   ideally 10s) to fail fast and let the next tick recover.

3. **No `inspect.getsource` monkeypatch of `asyncio.run` from
   pytest threads.** `asyncio.run` is patched at module level,
   not thread level; if the test thread has a running loop
   (which pytest sometimes does), the patch does not apply.
   Use `asyncio.run(_coro())` (wrap the call in a real
   coroutine) to get a clean running loop, or call
   `get_state_sync` directly from a sync context.
