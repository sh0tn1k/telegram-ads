# BrowserProfileManager — Shared Adapter Architecture

## Problem

Two independent module-level `_adapter` singletons in `telegram_ads_tool.py` and `telegram_ads_workflow_tool.py`. Both called `TelegramAdsAdapter.launch(config)` which called `BrowserAutomationTool.launch(config.browser)` → `launch_persistent_context(user_data_dir=<same>)`. Chromium write-locks the profile directory — second process crashed with "Opening in existing browser session".

Additionally, the no-kill policy forbids killing chromium/playwright processes to release the lock.

## Solution

### BrowserProfileManager (singleton)

File: `hermes_telegram_ads/browser_manager.py`

```
BrowserProfileManager (singleton)
  │
  ├── acquire_adapter(config, timeout=30)
  │   ├── 1. asyncio.Lock.acquire() с timeout
  │   │   timeout → BrowserProfileBusyError
  │   ├── 2. Если adapter None:
  │   │     check_profile_lock() → locked → BrowserProfileLockedError
  │   │     TelegramAdsAdapter.launch() — ОДИН РАЗ
  │   ├── 3. active_operations++
  │   └── 4. Lock HELD until release_adapter()
  │
  ├── release_adapter()
  │   ├── active_operations--
  │   └── asyncio.Lock.release()
  │
  ├── shutdown()                  ← legacy (backward-compat)
  │   └── adapter.close() + reset
  │
  ├── close_all(*, timeout=5.0)  ← NEW (added 2026-06-02): graceful structured teardown
  │   └── structured 9-key result (see "Graceful close_all API" below)
  │
  └── use_adapter(config, *, timeout=30.0)  ← NEW (added 2026-06-02): async context manager
      └── acquire_adapter on __aenter__, release_adapter on __aexit__

telegram_ads_tool.py          telegram_ads_workflow_tool.py
       │                               │
       └─── BrowserProfileManager ─────┘
             (один adapter для обоих)
```

### Key principles

1. **Manager owns the adapter.** Tools must NOT call `TelegramAdsAdapter.launch()` directly. Always `BrowserProfileManager.get_instance().acquire_adapter(config)` (or `use_adapter(config)`).

2. **Lock detection order:**
   - If manager already has live adapter → reuse, skip SingletonLock check
   - If adapter doesn't exist → check external lock → if clear, create adapter
   - If adapter exists but another workflow holds asyncio.Lock → wait (with timeout)

3. **No browser close on release.** `release_adapter()` decrements `active_operations` and releases the asyncio.Lock. Adapter stays alive for the next workflow. Close only on explicit `close_all()` / `shutdown()`.

4. **No kill/pkill.** BrowserProfileManager never calls `os.kill()`, `pkill`, or any process termination. Signal 0 (process existence check via `os.kill(pid, 0)`) is explicitly exempted — it's the POSIX way to check if a PID is alive.

5. **Always release, even on failure.** Workflows (and dispatchers) MUST use `async with manager.use_adapter(config) as adapter:` (preferred) OR wrap their `release_adapter()` call in a `try/finally` block. Forgetting the release hangs the next `acquire_adapter()` until the asyncio.Lock timeout fires (default 30 s). This is enforced by the workflow dispatcher (`workflows/__init__.py:run_workflow`) via its own `try/finally release_adapter()` block around `func(adapter, params)`.

### Changes to tool files

Both `telegram_ads_tool.py` and `telegram_ads_workflow_tool.py`:

- Removed module-level `_adapter` global
- Replaced `_get_adapter()` with `BrowserProfileManager.get_instance().acquire_adapter(config)`
- Added `_release_adapter()` calling `manager.release_adapter()`
- Added `finally` block in async handler to always release
- Added structured error handlers for `BrowserProfileLockedError` and `BrowserProfileBusyError`

### Structured error contract

```python
# External process lock
BrowserProfileLockedError(
    profile_path="/home/hermes/.hermes/data/telegram_ads/browser_profile",
    owner_pid=12345
)
# → json: {"ok": false, "error": "browser_profile_locked", ...}

# In-process timeout
BrowserProfileBusyError(
    profile_path="...",
    timeout=30.0
)
# → json: {"ok": false, "error": "browser_profile_busy", ...}
```

### Lock detection

File: `hermes_telegram_ads/browser.py` — `check_profile_lock(profile_dir)` → dict

Chromium lock files:
- `SingletonLock` — file lock, first line typically contains PID
- `SingletonSocket` — Unix socket, sometimes contains PID in content

Returns:
- `{"locked": True, "owner_pid": 12345}` — another process holds the lock
- `{"locked": False}` — free to use
- `{"locked": False, "stale": True}` — lock file exists but PID is dead

No false positives: PID is verified via `os.kill(pid, SIG_DFL)` (signal 0 = existence check, not a kill).

### Test coverage

37 tests in `tests/test_telegram_ads_browser_lock.py`:

| Test | Checks |
|---|---|
| `test_no_lock_files_async` | unlocked → locked=False |
| `test_singleton_lock_active_async` | alive PID → locked=True |
| `test_singleton_lock_stale_async` | dead PID → locked=False, stale=True |
| `test_acquire_twice_same_adapter` | TelegramAdsAdapter.launch() called once |
| `test_concurrent_second_workflow_times_out` | BrowserProfileBusyError |
| `test_release_does_not_close_adapter` | No close after release (shared mode) |
| `test_no_kill_in_acquire` | No SIGKILL/SIGTERM |
| `test_two_tools_get_same_adapter_through_manager` | Tools share adapter |
| `test_external_lock_on_first_acquire_only` | Lock check only once |

---

## Graceful close_all API (added 2026-06-02)

The original `shutdown()` returns only `{"ok": True/False, "closed_adapter": True/False, ...}`. That's too coarse for a graceful teardown path that needs to distinguish "context close failed but registry cleared" from "registry clear failed too". The new `close_all()` API is the high-level teardown that gateway SIGTERM and the atexit hook call.

### `close_all(timeout=5.0)` — full_result schema

```python
result = await manager.close_all(timeout=5.0)
# → {
#     "ok": bool,                  # True if all steps succeeded AND no errors
#     "contexts_closed": bool,     # BrowserContext.close() succeeded
#     "browser_stopped": bool,     # Playwright.stop() succeeded
#     "registry_cleared": bool,    # _adapter/_config/_closed/_active_operations reset
#     "warnings": list[str],       # soft issues (timeout, etc.)
#     "errors": list[str],         # hard per-step exceptions
#     "duration_ms": int,          # total elapsed (always present, even on error)
#     "profile_path": str,         # for log correlation
#     "had_adapter": bool,         # True if adapter existed at start (no-op when False)
# }
```

**Step order:**
1. `asyncio.wait_for(self._adapter.close(), timeout=timeout)` — closes context + stops Playwright.
2. Clear registry: `_adapter = None; _config = None; _closed = True; _active_operations = 0; _initial_lock_check_done = False`. **Always** runs, even if step 1 failed.
3. Log final result (INFO on full success, WARNING with full per-step details on partial/failure).

**Hard guarantees (verified by tests):**
- **Registry cleared on timeout:** `registry_cleared=True` even if `adapter.close()` times out.
- **Registry cleared on exception:** same — `errors` populated, but `registry_cleared=True`.
- **No silent orphan:** on timeout, a `warnings` entry is appended (`"adapter.close() timed out after 0.1s — orphan browser possible (profile=...)"`) and the gateway's outer 10 s backstop takes over.
- **Never raises:** all exceptions are captured in `errors[]`. The function always returns a dict.
- **Logs are differentiated:** INFO `"close_all: OK (duration=Xms, profile=Y)"` on success; WARNING `"close_all: PARTIAL/FAILED (ok=..., warnings=N, errors=M, duration=Xms, profile=Y)"` on failure.

### `use_adapter(config, *, timeout=30.0)` — async context manager

```python
async with manager.use_adapter(config, timeout=10.0) as adapter:
    accounts = await adapter.list_accounts()
    # ...
# release_adapter() called automatically here, even on exception / cancellation
```

**Behavior:**
- `__aenter__`: `await self.acquire_adapter(config, timeout=timeout)`. Raises `BrowserProfileLockedError` / `BrowserProfileBusyError` on failure.
- `__aexit__`: `self.release_adapter()` — always called, even on:
  - `RuntimeError` (workflow raised)
  - `asyncio.CancelledError` (caller cancelled)
  - `BaseException` (anything propagates)
- If `acquire_adapter()` itself fails, `release_adapter()` is **not** called (correct semantics — the lock was never acquired).

**Recommended usage in workflow code:**

```python
# Preferred: async with
async with manager.use_adapter(config) as adapter:
    result = await adapter.list_accounts()
    return result

# Equivalent (manual try/finally):
adapter = await manager.acquire_adapter(config, timeout=30.0)
try:
    result = await adapter.list_accounts()
    return result
finally:
    manager.release_adapter()
```

### Dispatcher try/finally (added 2026-06-02)

`workflows/__init__.py:run_workflow()` now wraps `func(adapter, params)` in a `try/finally` that calls `release_adapter()` on the way out, even if the workflow raises. The pattern is the lower-level equivalent of `use_adapter()` for callers that build the adapter externally (e.g. tool-level handlers that already created the adapter).

```python
_mgr = _get_browser_profile_manager()  # lazy, ImportError-safe (returns None)
if _mgr is not None and _mgr.is_active and adapter is not None:
    try:
        result = await func(adapter, params)
        return {"ok": True, "workflow": workflow, "data": result}
    except Exception as e:
        logger.exception("Workflow %s failed", workflow)
        return _error_result(workflow, e)
    finally:
        try:
            _mgr.release_adapter()
        except Exception as _rel_exc:
            logger.warning(
                "run_workflow: release_adapter error for %s: %s",
                workflow, _rel_exc,
            )
```

This is the **minimum** guarantee for any future workflow that adds `acquire_adapter()` without explicit teardown. The dispatcher does the right thing automatically.

### Backward compatibility

- `shutdown()` is **kept** as an alias. It still returns the simpler 2-key result (`{"ok": True, "closed_adapter": True, ...}`) for any existing caller. New code should use `close_all()`.
- `acquire_adapter()` / `release_adapter()` signatures are **unchanged**. The `use_adapter()` context manager is additive.

### Test coverage for `close_all` + `use_adapter` + dispatcher

16 tests in `tests/test_browser_profile_manager_close_all.py` (added 2026-06-02):

| Test | Checks |
|---|---|
| `test_close_all_no_adapter_noop_success` | no adapter → ok=True, all fields True, no warnings/errors |
| `test_close_all_successful_adapter_close` | success path → ok=True, registry cleared, adapter.close() awaited |
| `test_close_all_adapter_close_raises_exception` | RuntimeError → ok=False, errors populated, **registry still cleared** |
| `test_close_all_adapter_close_times_out` | hang > 0.1s → warnings populated, **registry still cleared** |
| `test_close_all_full_result_schema` | always 9 keys (ok, contexts_closed, browser_stopped, registry_cleared, warnings, errors, duration_ms, profile_path, had_adapter) |
| `test_close_all_logs_final_result` | INFO log on success |
| `test_close_all_logs_warning_on_failure` | WARNING log with "PARTIAL/FAILED" on error |
| `test_close_all_duration_measured` | duration_ms >= 40 when work took ~50ms |
| `test_use_adapter_acquires_and_releases` | __aenter__ → acquire + yield; __aexit__ → release |
| `test_use_adapter_releases_on_exception` | RuntimeError → release still called |
| `test_use_adapter_releases_on_cancelled_error` | CancelledError → release still called |
| `test_use_adapter_propagates_acquire_error` | acquire fails → release **NOT** called (correct semantics) |
| `test_run_workflow_releases_adapter_in_finally` | success path → release_adapter() in finally |
| `test_run_workflow_releases_adapter_on_exception` | workflow raises → release_adapter() still called |
| `test_run_workflow_no_manager_skips_release` | manager=None → no release call (ImportError-safe) |
| `test_run_workflow_logs_release_error_but_continues` | release_adapter() raises → caught, logged WARNING, result.ok=True |

---

## Lifecycle teardown (added 2026-06-02)

The manager above only describes the **happy path**. There is a real
failure mode: when the gateway exits (SIGTERM, restart, crash, OOM),
the Playwright-driven Chromium browser process tree is **not** in the
gateway's cgroup, and Chromium's main process creates its **own
PGID/SID** at launch. The kernel does not propagate SIGTERM to it, and
systemd `KillMode=mixed` / `control-group` cannot reach it because the
browser is not in the gateway's cgroup.

Without explicit teardown, the gateway restarts and immediately
receives `browser_profile_locked` because the previous Chromium is
still alive and holding the `SingletonLock` symlink. This was the root
cause of an orphan process tree observed on 2026-06-02 (10 Chromium
processes surviving a gateway restart).

### The 3-level teardown pattern

1. **Level 1 — Explicit gateway shutdown hook (PRIMARY).**
   `gateway/run.py` calls `await
   TelegramAdsBrowserProfileManager.get_instance().close_all(timeout=5.0)`
   with a 10-second outer backstop, **before** raising `SystemExit`.
   This is the clean path: `BrowserAutomationTool.close()` →
   `context.close()` + `playwright.stop()` → Chromium exits, Playwright
   driver exits, `SingletonLock` is removed by Chromium on its own
   exit.

   - **Why `close_all()` (not `shutdown()`)?** `close_all()` is the
     graceful, structured per-step API (see "Graceful close_all API"
     above). It returns a 9-key structured result with per-step status
     (`contexts_closed`, `browser_stopped`, `registry_cleared`,
     `warnings`, `errors`, `duration_ms`, `profile_path`,
     `had_adapter`). `shutdown()` is kept as a backward-compat alias
     that returns a simpler 2-key result. Gateway logging benefits
     from the richer per-step diagnostics on partial failure.
   - **Why `timeout=5.0` (inner) + 10 s outer?** `close_all()`
     internally wraps the entire `adapter.close()` flow in
     `asyncio.wait_for(timeout=5.0)`. If `close_all()` itself
     misbehaves (deadlock, never returns), the outer
     `asyncio.wait_for(timeout=10.0)` is a hard backstop. On either
     timeout, atexit takes over.
   - **Why before `SystemExit`?** the asyncio loop is still alive;
     after `SystemExit` the loop is closed and the await would never
     complete.
   - **Why guarded with `is_active`?** no-op when no adapter was ever
     created, so the call is free.
   - **Why wrapped in `try/except ImportError`?** the package is not
     installed in every profile; default profile must not break.

2. **Level 2 — `atexit` fallback (BACKUP for crash / OOM / hard kill).**
   `TelegramAdsBrowserProfileManager.__init__` registers
   `self._atexit_shutdown_safely` with `atexit.register`. The hook:

   - If `self._adapter is None or self._closed`: returns immediately.
   - Otherwise, runs `asyncio.wait_for(self.shutdown(), timeout=5.0)`
     on a fresh event loop (the original loop is closed at atexit
     phase).
   - If graceful close succeeds, logs and returns.
   - If the 5-second timeout fires, escalates to Level 3.

3. **Level 3 — SIGTERM-only fallback (LAST RESORT for stuck
   browsers).** If Level 2's graceful close times out (or errors),
   `_sigterm_orphan_chromium` reads **only** the PID from the
   `SingletonLock` symlink in the manager's recorded profile path,
   pings it with `os.kill(pid, 0)`, and — if alive and same uid —
   sends `SIGTERM` (never `SIGKILL`).

   - **Hard guarantees:**
     - Reads **only** the path the manager itself recorded (never
       scans `/proc`, never `pkill chromium`).
     - Sends `SIGTERM` only; `SIGKILL` is never referenced in the
       module (verified by `grep`).
     - Skips silently on `ProcessLookupError` (PID already dead),
       `PermissionError` (different uid), or missing lock file.
     - Does **not** delete the `SingletonLock`; Chromium removes it
       on its own exit.
     - Honors `HERMES_TG_ADS_SKIP_SIGTERM_FALLBACK=1` (extra-safety
       env var).

### Why the symlink PID extraction matters

Chromium's persistent-context lock is a **symlink** whose target is
`host-<pid>` (hostname + dash + PID), and the target file is
**removed at Chromium exit** — leaving a dangling symlink. Two
subtleties that bit us in test design:

- `Path("SingletonLock").exists()` returns `False` for a dangling
  symlink. The check must use `lstat()` (or `is_symlink() | exists()`)
  so the broken symlink is still detected. `browser_manager.py` does
  `try: lock_path.lstat() except (OSError, FileNotFoundError): return`.
- `_read_lock_pid(SingletonLock)` (in `browser.py`) tries
  `read_bytes()` first, which on a dangling symlink raises `OSError`
  and returns `None`. The SIGTERM fallback therefore needs a
  secondary extractor that reads `os.readlink(target)` and parses
  `target.rsplit("-", 1)[-1]` as the PID.

This is tested in `tests/test_browser_profile_manager_atexit.py` — both
the symlink format and the regular-file format are covered. When
writing test fixtures, **always use `lock.symlink_to("host-99999")`**
(real Chromium format), **not** `lock.write_text("host-99999\n")`,
unless you specifically want to test the regular-file fallback path.

### Environment-variable rollback path

If the teardown patch causes a regression in production, set these env
vars in the systemd unit (`systemctl --user edit hermes-gateway-*.service`)
**without removing the code**:

- `HERMES_TG_ADS_SKIP_ATEXIT_TEARDOWN=1` — disables atexit
  registration entirely. Level 1 (gateway hook) still runs; Level 2
  and 3 do not.
- `HERMES_TG_ADS_SKIP_SIGTERM_FALLBACK=1` — keeps atexit but disables
  the SIGTERM escalation. Level 2 still attempts graceful close, but
  on timeout logs a warning and returns; no SIGTERM is sent.

These are read at module import time, so a `systemctl --user daemon-reload
&& systemctl --user restart hermes-gateway-default.service` is
required to pick them up. Use this as the rollback path before
`git revert`-ing the patch.

### Test coverage for lifecycle teardown

18 tests in `tests/test_browser_profile_manager_atexit.py` (added
2026-06-02):

| Test | Checks |
|---|---|
| `test_atexit_registered_on_singleton_construction` | atexit hook registered on `get_instance()` |
| `test_atexit_is_idempotent` | singleton: same instance, hook re-registered zero times |
| `test_atexit_disabled_by_env` | `HERMES_TG_ADS_SKIP_ATEXIT_TEARDOWN=1` disables registration |
| `test_atexit_no_adapter_is_noop` | empty manager: no raise, no SIGTERM |
| `test_atexit_graceful_shutdown_succeeds` | graceful close: SIGTERM fallback **not** called |
| `test_atexit_graceful_shutdown_timeout_falls_through_to_sigterm` | timeout: SIGTERM fallback **is** called |
| `test_sigterm_fallback_only_targets_singletonlock_pid` | symlink `host-99999` → ping + SIGTERM (never SIGKILL) |
| `test_sigterm_fallback_works_with_regular_file_lock` | regular file `99999\n` → same flow |
| `test_sigterm_fallback_skips_dead_pid` | `ProcessLookupError` → no SIGTERM |
| `test_sigterm_fallback_skips_missing_lock` | no lock file → no SIGTERM (no broad scan) |
| `test_sigterm_fallback_skips_unreadable_lock` | garbage target → no SIGTERM |
| `test_sigterm_fallback_skips_permission_error` | different uid → no SIGTERM (no cross-uid escalation) |
| `test_sigterm_fallback_disabled_by_env` | `HERMES_TG_ADS_SKIP_SIGTERM_FALLBACK=1` skips escalation |
| `test_sigterm_fallback_no_profile_path_is_noop` | empty `_profile_path` → silent no-op |
| `test_gateway_shutdown_calls_browser_profile_manager_shutdown` | source-level: gateway code imports + calls `close_all(timeout=5.0)` (NOT `shutdown()`) |
| `test_gateway_shutdown_invokes_shutdown_when_adapter_active` | runtime: `is_active=True` → `await close_all()` |
| `test_gateway_shutdown_timeout_does_not_block_exit` | hang → 10 s timeout, never blocks |
| `test_gateway_shutdown_no_adapter_is_fast` | `is_active=False` → skip, fast path |

Total Telegram Ads suite as of 2026-06-02: **227/227 passed** across
9 test files (211 prior + 16 new in `test_browser_profile_manager_close_all.py`).
