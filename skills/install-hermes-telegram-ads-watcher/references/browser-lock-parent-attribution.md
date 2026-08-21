# Browser-profile lock parent attribution (read-only)

When `BrowserProfileLockedError` fires, the first question is: **whose chromium is holding the lock?** Stale-lock cleanup is the wrong fix if the lock holder is alive and managed. This reference documents the strict-read-only procedure to attribute the lock holder to a parent process without killing or signaling anything.

## When to use

- `real_adapter_smoke.py` or `WatcherScheduler.run_forever()` fails with `BrowserProfileLockedError`.
- `pgrep -af 'chromium.*telegram_ads'` shows live chromium processes.
- `SingletonLock` symlink target (`host-<pid>`) appears stale but PID is alive.
- You need to write a "what should the operator do?" recommendation without touching the system.

## When NOT to use

- You have explicit operator approval to signal/kill the lock holder — use AR-ADS-WATCHER-LOCK-OWNER-2 pattern instead (separate approval gate).
- You want to delete `Singleton*` lock files (also a separate explicit approval gate).

## Read-only procedure (8 commands)

All commands are pure read. None modify files, signal processes, or restart services.

```bash
# 1. Find PID holding the lock
readlink -f /home/hermes/.hermes/hermes-agent/browser_profiles/telegram_ads/SingletonLock
# → /home/hermes/.hermes/hermes-agent/browser_profiles/telegram_ads/SingletonLock (file itself)
# Actually you want the symlink target — use plain `readlink`:
readlink /home/hermes/.hermes/hermes-agent/browser_profiles/telegram_ads/SingletonLock
# → host-4164123  (convention: hostname-PID)

# 2. Confirm PID is alive
pgrep -af 'chromium|chrome|ms-playwright|telegram_ads' | grep -E '^4164123\b'
# If empty → STALE LOCK (cleanup approval needed, separate gate)
# If matched → LIVE PROCESS (this reference applies)

# 3. Get full PID info: parent, group, session, elapsed, state, command
ps -o pid,ppid,pgid,sid,etime,stat,cmd -p 4164123

# 4. List direct children
ps -o pid,ppid,pgid,sid,etime,stat,cmd --ppid 4164123

# 5. Visual tree
pstree -aps 4164123 2>/dev/null || true

# 6. Working directory and executable
readlink -f /proc/4164123/cwd 2>&1
readlink -f /proc/4164123/exe 2>&1

# 7. Status summary (state, threads, vmrss)
sed -n '1,40p' /proc/4164123/status

# 8. Direct children summary
pgrep -P 4164123 -a
```

Optional cross-checks:

```bash
# All python/playwright/chrome processes
pgrep -af 'python|playwright|chrome|chromium|telegram_ads|browser_profiles/telegram_ads'
```

## Interpretation matrix

| `readlink SingletonLock` | `pgrep -af '4164123'` | `ps -o cmd -p $PPID` | Verdict |
|---|---|---|---|
| `host-<pid>` | matches `<pid>` | `hermes_cli.main ... gateway run` | **Live gateway-owned chromium** — release requires gateway exit or scoped SIGTERM (separate approval) |
| `host-<pid>` | matches `<pid>` | `start_ads_watcher_readonly_operational.py` | **Live watcher-owned chromium** — release requires watcher SIGTERM (separate approval) |
| `host-<pid>` | matches `<pid>` | `playwright/driver/node ... run-driver` | **Live Playwright driver** — see above; the python parent will be one level up |
| `host-<pid>` | empty | n/a | **Stale lock** — safe to delete `Singleton*` (separate explicit approval) |

## Observed patterns (2026-06-17)

| Pattern | Signature | Cause |
|---|---|---|
| Gateway typed tools → live chromium | `PPID = Playwright Node driver thread`, whose parent is the gateway process `hermes_cli.main ... gateway run` | A `telegram_ads_*` typed call launched Chromium; lock files were created lazily ~3 minutes after process start; chromium stays alive for the gateway's lifetime |
| Watcher daemon → live chromium | `PPID = watcher python process` (start_ads_watcher_readonly_operational.py) | Watcher acquired adapter; lock held for daemon's lifetime |
| Stale lock from a killed watch | `readlink` returns `host-<pid>` but `pgrep -af <pid>` is empty | Chromium was `kill -9`'d without cleanup; SingletonLock symlink points to a now-dead PID. Safe to delete symlinks. |
| Stale lock from a system crash | Same as above but the dead PID was a chromium tree of 10+ processes | System OOM or hard reboot left orphan. Safe to delete symlinks. |

## What you can recommend based on the verdict

| Verdict | Recommendation | Approval gate |
|---|---|---|
| Live gateway-owned | **Do nothing.** Chromium is part of the active agent-serving gateway; releasing it requires killing the gateway (which serves Telegram DMs) or scoped SIGTERM to chromium root PID only. | If the operator wants release: AR-ADS-WATCHER-LOCK-OWNER-2 (SIGTERM chromium root) |
| Live watcher-owned | SIGTERM only the watcher process (the chromium tree exits cleanly via PGID). | AR-ADS-WATCHER-LOCK-OWNER-2 (watcher variant) |
| Stale lock | Delete `Singleton*` files only (does NOT kill chromium — there isn't one). | AR-ADS-WATCHER-LOCK-1 (scoped cleanup) |
| Unknown / hybrid | Don't guess. Document the divergence and propose read-only diagnostic. | AR-ADS-WATCHER-LOCK-DIAG-3 |

## Pitfalls

- **Don't skip step 2.** Even if the lock symlink is there, the PID may be dead. The `pgrep -af` check is the only way to disambiguate stale vs live.
- **Don't read `/proc/*/environ`** — explicitly forbidden by the operator. `/proc/<pid>/{cwd,exe,status}` are OK; `cmdline` (visible in `ps -o cmd`) is OK; `environ` is NOT.
- **`pstree -aps` is not installed everywhere** — fall back to `ps --forest` or a recursive `pgrep -P`. The `2>/dev/null || true` swallows the missing-command error so the rest of the procedure still runs.
- **Don't use `kill -0` to check liveness** as a substitute for `pgrep`. `kill -0` is a signal, not a probe, and on some shells it can cause harmless but noisy EAGAIN. `pgrep -af` is cleaner.
- **Process tree depth can be deep.** Playwright spawns 2-3 levels (Python → Node driver → Chromium root → zygote → renderer/gpu). Walking `pgrep -P` recursively is fine; don't `pkill` anything as part of diagnosis.
- **Cron lock retention.** If you're auditing this from a cron job, the lock holder may be the cron session itself (a `WatcherScheduler.run_forever()` started by an earlier cron tick that didn't exit cleanly). Treat the cron job as a process to investigate, not to trust.

## Related approval gates (not in this reference)

- `AR-ADS-WATCHER-LOCK-DIAG-2` — this entire reference is the basis for that gate.
- `AR-ADS-WATCHER-LOCK-OWNER-1` — do nothing.
- `AR-ADS-WATCHER-LOCK-OWNER-2` — scoped SIGTERM to chromium root PID.
- `AR-ADS-WATCHER-LOCK-1` — scoped stale-lockfile cleanup (only valid after this procedure confirms stale).
