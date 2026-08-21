# Profile Loader and Browser Lock Semantics

Session-condensed reference for the Telegram Ads profile_dir / config loader /
browser lock architecture. Read this when investigating:

- "Why is the gateway using the wrong Chromium profile?"
- "Why does the standalone smoke fail with `Opening in existing browser session`?"
- "Where does the typed toolset get its `browser.profile_dir` from?"
- "What's the difference between `profile_locked: false` from typed tools and `SingletonLock` on disk?"
- "Should I delete the stale `Singleton*` files in the old profile dir?"

The behavioral model is small. The failure modes are silent and easy to miss.

---

## 1. The config loader: where `browser.profile_dir` actually comes from

There are **two sources of truth** for the Telegram Ads profile directory, and
they routinely disagree:

| Source | Path | When used |
|---|---|---|
| `~/.hermes/telegram_ads.yaml` → `telegram_ads.browser.profile_dir` | `/home/hermes/.hermes/data/telegram_ads/browser_profile` | When the loader calls `TelegramAdsConfig.from_yaml(SHARED_CONFIG_PATH)` or `from_yaml(...)` |
| `TelegramAdsConfig.default().browser.profile_dir` | `Path("./browser_profiles/telegram_ads")` (relative) | When the loader silently falls back to `default()` |
| Resolved-default (gateway CWD = `/home/hermes/.hermes/hermes-agent`) | `/home/hermes/.hermes/hermes-agent/browser_profiles/telegram_ads` | Default-relative resolved against the gateway's CWD |

**The contract (after AR-ADS-WATCHER-ARCH-1, 2026-06-17):** the loader MUST prefer
`from_yaml(SHARED_CONFIG_PATH)` first, fall back to `model_validate(block)` second,
and only reach `default()` as the final fallback. This applies to three call sites:

1. `tools/telegram_ads_typed_tool.py:_make_toolset()` — gateway typed tools
2. `real_adapter_smoke.py:_load_config()` — standalone smoke
3. `start_ads_watcher_readonly_operational.py:_load_config()` — operational watcher

Tests pinning this contract live in
`tests/test_telegram_ads_config_loader.py` (7 tests including
`test_all_three_loaders_resolve_to_same_profile_path`).

### What to do when the symptom appears

If `telegram_ads_login_check` returns a `data.profile_dir` that does not equal
`TelegramAdsConfig.from_yaml(SHARED_CONFIG_PATH).browser.profile_dir`:

1. Do NOT assume the config file is wrong.
2. Check `tools/telegram_ads_typed_tool.py:_make_toolset()` for a
   `TelegramAdsConfig.from_dict` call. If present, it is the silent-fallback
   bug; replace with the contract above.
3. Restart `hermes-gateway-default.service` after the fix — the existing
   gateway has the wrong toolset cached.
4. Verify by re-running `telegram_ads_login_check` and comparing `data.profile_dir`.

The fallback is silent: no warning, no log line, no error. The first symptom
is almost always "smoke can't acquire the profile" or "watcher tools target
a different dir than the typed tools."

---

## 2. The two lock states: typed-tool registry vs Chromium filesystem

There are **two independent locks** on the persistent Chromium profile. They are
NOT the same:

| Lock | Where | What it tracks | Reported by |
|---|---|---|---|
| `BrowserProfileManager._lock` (asyncio) | Python process memory | Concurrent acquisition within ONE process | Not exposed directly; reflected in tool `ok: false` errors |
| `BrowserProfileManager._fingerprint_to_ref` registry lock | Python process memory | Per-process registry of acquired adapters | `telegram_ads_get_browser_profile_info` → `profile_locked` field |
| Chromium `SingletonLock` symlink | Filesystem (`<profile_dir>/SingletonLock` → `host-<pid>`) | Singleton browser instance constraint per OS | `ls -la <profile_dir>/Singleton*` |

The first two are **in-process** — they only prevent a single Python process
from acquiring the same `TelegramAdsAdapter` twice concurrently. The third is
**OS-level** — Chromium refuses to launch a second instance against the same
`--user-data-dir`.

### Why this matters

When the gateway holds a chromium (via the typed tools) and a standalone smoke
script is started, the standalone script:

- Sees `profile_locked: false` if it called `telegram_ads_get_browser_profile_info`
  (registry lock is per-process; the smoke is a different process).
- Fails to launch chromium because `SingletonLock` on disk points to the
  gateway's chromium PID.

`SingletonLock` and the typed-tool registry can disagree. When debugging a
"profile locked" symptom, **read the file** (`ls -la <profile_dir>/Singleton*`,
check the target PID with `ps -p <pid>`), not the registry field.

### Decision matrix for `SingletonLock` diagnosis

| `SingletonLock` target | Profile dir in use | Verdict | Action |
|---|---|---|---|
| Live PID, same dir as gateway's current acquisition | Yes | **Live process lock** — by design | Do nothing; this is the expected steady state |
| Dead PID, recent mtime | Yes | **Stale lock** | Cleanup is optional; chromium auto-clears on next launch if lock holder is gone |
| Live PID, different dir from current acquisition | No (different dir) | **Harmless orphan** | Do NOT delete; different Chromium namespace |
| Dead PID, old mtime, different dir from current acquisition | No (different dir) | **Harmless orphan** | Do NOT delete; same reason |

In all "different dir" cases, the stale lock cannot affect the current
acquisition because Chromium only consults `SingletonLock` under the
`--user-data-dir` it was launched with.

---

## 3. BrowserProfileManager lifecycle (per-process)

Each Python process has its OWN `BrowserProfileManager._instance = None`
singleton. The singleton is created on first `acquire_adapter()` call.

| Method | Effect |
|---|---|
| `get_instance() → BrowserProfileManager` | Per-process singleton; different processes have different instances |
| `acquire_adapter(config, timeout=30.0)` | If adapter exists → reuse. If not → external PID check + `TelegramAdsAdapter.launch(config)`. Holds asyncio lock. |
| `release_adapter()` | Decrements counter, releases asyncio lock. Does **NOT** close browser. |
| `use_adapter(config, timeout=30.0)` | Async context manager: `acquire_adapter` on enter, `release_adapter` on exit. **Reuses adapter across invocations.** |
| `close_all(timeout=5.0)` | The clean shutdown path. Closes browser context + stops Playwright + clears registry. **Registered as atexit hook** automatically. |
| `check_profile_lock(profile_dir)` | Read-only, no kill. Detects external Chromium lock via `SingletonLock`. Returns `{locked, owner_pid, stale}`. |

**Cross-process sharing is impossible.** Two processes with the same config
will independently try to launch a chromium against the same `--user-data-dir`,
and Chromium will refuse the second one. The only way for two consumers to
share a chromium is to run in the same Python process.

This is why the "in-process watcher" architecture (Model E in the
2026-06-17 architecture diagnosis) is the structurally cleanest: the watcher
runs as an `asyncio.Task` in the same gateway process, calls
`manager.use_adapter(config)`, and shares the existing `TelegramAdsAdapter`
instance. No new chromium, no new lock, no new login session.

---

## 4. Four ownership models for the persistent profile

Reference table from the 2026-06-17 architecture diagnosis. Each model has a
distinct lifecycle for the persistent chromium:

| Model | Who owns chromium | Watcher location | Lock collision risk | Operational complexity |
|---|---|---|---|---|
| **A. Gateway owns, watcher is internal task** | Gateway (long-lived) | Inside gateway process | None (shared asyncio lock) | Lowest |
| **B. Watcher owns, typed tools serialize** | Watcher (separate daemon) | Standalone daemon | Watcher lock blocks typed tools | High |
| **C. Two profiles (one per role)** | None (split: typed and watcher each own one) | Standalone daemon | None (different dirs) but 2 logins, 2 cookie jars | Medium |
| **D. No daemon — on-demand login_check only** | None (transient) | None | None | Lowest |

The session's working assumption is **A** (in-process watcher) is the
structurally cleanest path. **D** is the lowest-effort fallback if the operator
prefers to defer the watcher architecture.

**Model C's two-login cost** is easy to underestimate: each `app_approval_pending`
cycle is human-driven; doubling the count doubles the manual review burden.

---

## 5. Quick verification commands (read-only, no side effects)

```bash
# Compare loader output to the file:
venv/bin/python -c "from hermes_telegram_ads.config import TelegramAdsConfig; print(TelegramAdsConfig.from_yaml('/home/hermes/telegram_ads.yaml').browser.profile_dir)"

# Inspect Chromium filesystem lock:
ls -la <profile_dir>/Singleton* 2>&1

# Check whether the lock target is alive:
ps -p <pid-extracted-from-SingletonLock> 2>&1

# Find all live chromium processes:
pgrep -af 'chrome.*<profile_dir>'

# Verify all three entrypoints agree on profile_dir (test only — no real chromium):
cd /home/hermes/.hermes/hermes-agent && venv/bin/python -m pytest tests/test_telegram_ads_config_loader.py -v
```

All five commands are non-mutating. The pytest run is the fastest way to
detect loader divergence without touching any real browser.

---

## 6. Restart required after config-loader fix

A change to `tools/telegram_ads_typed_tool.py:_make_toolset()` does NOT take
effect on the running gateway, because the existing `_toolset_singleton` was
constructed at the first typed tool call and is cached. The new code is in
memory only after a `systemctl --user restart hermes-gateway-default.service`.

This is a **separate explicit approval gate**. The default gateway restart
touches the process serving Telegram DMs; a full restart cycle can take
~30s during which inbound DMs queue. Schedule it deliberately.

After restart, the next typed telegram_ads_* call will:
1. Construct a fresh `_toolset_singleton` using the patched loader.
2. Resolve to the yaml's `profile_dir`.
3. Launch a fresh chromium against the corrected dir.

The pre-restart chromium's `Singleton*` files under the old wrong profile dir
become orphans (see §2 "different dir" verdict). They can be left in place;
do not bundle the cleanup with the restart approval.

---

## 7. Common misreads to avoid

- **"The config says X but the loader uses Y"** is not a yaml bug. It's the
  `from_dict` AttributeError fallback. The yaml is correct; the loader ignores it.
- **"The standalone smoke failed because the gateway is using the wrong
  profile"** is the right diagnosis. The fix is the loader, not the smoke.
- **"`profile_locked: false` means the profile is free"** is wrong for
  cross-process scenarios. `SingletonLock` is the OS-level source of truth.
- **"I need to clean up the stale Singleton files"** — only if they are under
  the SAME dir the gateway is currently acquiring. Different dir = orphan.
- **"`telegram_ads_login_check` left a chromium running, so it's broken"** —
  by design. The chromium is reused across typed calls; that's the
  `BrowserProfileManager.use_adapter` contract.
