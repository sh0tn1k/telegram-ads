# Real adapter smoke procedure (read-only, with explicit approval)

How to run a one-shot smoke against the real Telegram Ads cabinet
without starting the watcher scheduler. Captured 2026-06-09 from
session that ran `real_adapter_smoke.py` end-to-end.

## When this is appropriate

You have a watcher wiring built, scheduler idle (or not started), and
the operator has explicitly approved a *named* read-only call list, e.g.:

```
Approve: run one read-only Telegram Ads adapter smoke check on Hermes server.
Allowed: detect_login_state, browser_healthy, list_accounts
Forbidden: anything else.
```

The smoke itself is safe (no mutations possible — adapter is wrapped in
`HermesTelegramAdsReadOnlyAdapter` with mutation guard active), but it
*does* launch a real Chromium against the persistent browser profile
and makes a real GET to `https://ads.telegram.org/account`. That's
network activity on a real cabinet, which is why it needs approval.

## Canonical call sequence

```python
import asyncio
from hermes_telegram_ads.browser_manager import BrowserProfileManager
from hermes_telegram_ads.hermes_tools import TelegramAdsConfig
from ads_watcher_integration import HermesTelegramAdsReadOnlyAdapter

manager = BrowserProfileManager.get_instance()
config = TelegramAdsConfig.default()
adapter = await manager.acquire_adapter(config=config, timeout=30.0)
try:
    ro = HermesTelegramAdsReadOnlyAdapter(adapter=adapter)
    # call ONLY the approved methods:
    healthy = ro.browser_healthy()
    state = await ro.detect_login_state(navigate=True)
    accounts = await ro.list_accounts()
finally:
    manager.release_adapter()
```

`TelegramAdsAdapter` has no `__aenter__`/`__aexit__`; the async-context
pattern is `acquire_adapter` + `try/finally release_adapter`. The
manager is a process-local singleton via
`BrowserProfileManager.get_instance()`; do not instantiate the manager
class directly.

## What `release_adapter` does NOT do

It releases the asyncio.Lock so other coroutines in the same process
can call `acquire_adapter` again. It does **not** close Chromium or
shut down the browser profile. The package's `atexit` handler does
that on process exit. Harmless warning you'll see in stderr:

```
WARNING hermes_telegram_ads.browser_manager BrowserProfileManager:
adapter close error: Event loop is closed
INFO hermes_telegram_ads.browser_manager BrowserProfileManager:
atexit graceful shutdown OK
```

That's the atexit handler racing with `loop.close()` from the smoke
script. The "graceful shutdown OK" line confirms the actual state is
clean. The warning is informational, not an error. If you want it
silent, structure the smoke as a context manager that lets atexit run
before `loop.close()` — but it's not worth the complexity for a one-off.

## Browser profile lock across processes

`BrowserProfileManager` is per-process (singleton class attribute),
but the *file lock* on `browser_profiles/telegram_ads/SingletonLock`
is per-host. Implications:

- The watcher daemon (process A) and the smoke script (process B)
  coexist fine when the watcher has **no watches configured** —
  scheduler's `tick()` doesn't call `acquire_adapter` because the
  service's `run_due_watches` only acquires when an enabled watch is
  due.
- Once real watches are added, both processes can race on the disk
  lock. The package raises `BrowserProfileLockedError` with
  `owner_pid` to help diagnose. Resolution: stop the other process,
  wait for the lock to release, retry.
- Do NOT run two smoke scripts in parallel against the same profile —
  one will hit the lock.

## Secrets discipline in smoke output

Telegram Ads account dicts contain `phone`, `session_active`, and
sometimes inline `access_token` fields. When reporting, redact them.
Helper used in the live session:

```python
SECRET_KEYS = {"token","access_token","refresh_token","session",
               "cookie","cookies","phone","password","secret",
               "api_key","auth"}

def _safe(obj):
    if obj is None: return None
    if isinstance(obj, dict):
        return {k: ("<redacted>" if any(s in k.lower() for s in SECRET_KEYS)
                    else _safe(v)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe(x) for x in obj]
    if hasattr(obj, "model_dump"):
        return _safe(obj.model_dump())
    return obj
```

Apply to the *output* of every adapter call before printing. The
logger on `ads_watcher.real_smoke` shows redacted values; the JSON
report block uses the same helper. Don't bypass for "just this once" —
the report goes to Telegram.

## Observed 2026-06-09 run

`real_adapter_smoke.py` against the Hermes server:

- `browser_healthy` → `True`
- `detect_login_state(navigate=True)` → `state=logged_in`,
  `current_url=https://ads.telegram.org/account`,
  `browser_state=healthy`, `requires_human_login=False`
- `list_accounts` → **4 accounts** (1× TON, 3× STARS; 1 active,
  3 inactive; all balances 0.0)
- Total elapsed ~5s (3.2s adapter launch + ~1.4s for the 3 calls + 0.3s
  release). All HTTP traffic was GET, no POST/PUT/DELETE.
- 0 errors in the report JSON, 0 mutations, 0 scheduler interaction
  (watcher process was idle in `wait_for(_stop.wait())` throughout).

## What still requires separate approval

Even after this smoke passes, the following are gated:

- Adding any watch via `service.create_watch(...)` — each new watch
  kind triggers real adapter calls on the next tick.
- Wiring a real adapter into the long-running scheduler
  (`HermesTelegramAdsReadOnlyAdapter(adapter=...)` in `build_wiring`).
- Starting `WatcherScheduler.run_forever()` with a real adapter.
- Any mutating Telegram Ads action (forbidden by `FORBIDDEN_MUTATION_TOOLS`
  + `__getattr__` guard regardless of approval).

## Quick reuse

The smoke script is reproducible: `python3 real_adapter_smoke.py` from
`/home/hermes/.hermes/hermes-agent/`. To change the allowed call list,
edit the three `try` blocks in `_run_smoke()` and update the
`Allowed:` line in the module docstring so the contract stays honest.
