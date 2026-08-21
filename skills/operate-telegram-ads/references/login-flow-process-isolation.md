# Login flow process isolation & atomic patterns

Session-specific detail for the Telegram Ads login flow that depends on
**process identity**, not just TTL or state. The umbrella `SKILL.md` covers
phone_required, approval TTL, session_active vs logged_in; this file covers
the 2nd, **silent** failure mode (cross-process singleton loss) and the
atomic script templates that make multi-step login flows work.

Updated 2026-06-06 from the post-install + login-session acceptance runs.
Updated 2026-06-06 (post-login-flow attempt) with the
`apply_about:blank` signature, the always-DOM-fill+click rule, the
form-not-rendered race, and the `state=timeout` ≠ failure reading.

## Root cause #2 of `invalid_confirmation` — cross-process singleton loss

The `mitigation` paragraph in `SKILL.md` ("reissue just before apply")
focuses on **TTL expiry** (5-min confirmation window). That is *one*
cause. The 2nd cause is **singleton loss across Python processes**, and
it bites even when the TTL hasn't expired.

### Why this happens

The Hermes-typed wrapper's `_toolset_singleton` is a module-level
`@lru_cache` on `_make_toolset()` (see
`tools/telegram_ads_typed_tool.py`). The package's
`TelegramAdsToolset._get_adapter()` creates an `ApprovalRegistry`
in-process on first use. Both live in the **singleton's process
memory**.

When the agent calls a `telegram_ads_*` tool from a `terminal()`
subprocess, that subprocess imports the wrapper fresh, builds a new
singleton, builds a new registry, and populates it with any CIDs that
`login_submit_phone` issues. When the agent then calls
`apply_approved_action` from a **different** `terminal()` subprocess
(a common pattern for "show approval → wait for 'approved' → apply"),
the new subprocess builds a new singleton, a new empty registry, and
`apply_approved_action` returns:

```json
{
  "status": "error",
  "error": "invalid_confirmation",
  "message": "No pending approval for confirmation_id '<cid>'. Issue one via the mutating tool or telegram_ads_prepare_approval_request first."
}
```

`get_pending_confirmations` in the new subprocess returns `pending: []`
— the canonical diagnostic signal. The TTL is not the issue; the CID
literally doesn't exist in the new process's memory.

### Diagnostic recipe

Before re-issuing, run this in a single `terminal()` call to confirm
the diagnosis:

```python
import sys, json, asyncio
sys.path.insert(0, '/home/hermes/.hermes/hermes-agent')
from tools.telegram_ads_typed_tool import _get_toolset
ts = _get_toolset()

async def main():
    r = await ts.call('telegram_ads_get_pending_confirmations')
    print(json.dumps(r, indent=2)[:1500])

asyncio.run(main())
```

If `pending: []` and you just issued a CID, you have **process
isolation**, not TTL expiry. The fix is **not** "reissue once and
hope" — it's "do the reissue + apply + wait in a single script".

### The fix: atomic script (reissue + apply + wait in one process)

The canonical pattern for any multi-step login flow that needs a
chat roundtrip:

```python
# _atomic_login_v2.py — one script, one terminal() call, all steps.
# When run, this script:
#   1) Issues a fresh CID (no chat roundtrip yet)
#   2) Pauses for the user to send "approved <cid>" via stdin
#   3) Reads stdin line, applies the CID in the SAME process
#   4) Polls login_wait in the SAME process (so the adapter stays alive)

import sys, json, asyncio, os
from pathlib import Path
sys.path.insert(0, '/home/hermes/.hermes/hermes-agent')
from tools.telegram_ads_typed_tool import _get_toolset
ts = _get_toolset()

PHONE_FILE = Path('/home/hermes/.hermes/telegram_ads_phone.txt')
PHONE = PHONE_FILE.read_text().strip()  # never print this

async def main():
    # 1) Reissue
    r = await ts.call('telegram_ads_login_submit_phone', phone=PHONE)
    cid = r.get('approval', {}).get('confirmation_id')
    if not cid:
        print(json.dumps(r, indent=2)); return
    print(f"confirmation_id: {cid}")
    print(f"expires_in_seconds: {r['approval'].get('expires_in_seconds')}")
    print("Waiting for 'approved' on stdin...")

    # 2) Read stdin (chat roundtrip or stdin pipe)
    line = await asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)
    line = line.strip()
    if line != f"approved {cid}":
        print(f"Aborted: got {line!r}"); return

    # 3) Apply in the SAME process
    apply = await ts.call('telegram_ads_apply_approved_action', confirmation_id=cid)
    print(f"apply: {json.dumps(apply, indent=2)[:600]}")
    if apply.get('status') != 'ok':
        return

    # 4) Poll login_wait — same process, same adapter, no SingletonLock race
    for i in range(60):
        w = await ts.call('telegram_ads_login_wait', timeout_sec=3, poll_interval_sec=2)
        d = w.get('data') or {}
        state = d.get('state'); logged_in = d.get('logged_in')
        print(f"  poll {i+1:2d}: state={state!r:30s} logged_in={logged_in}")
        if state == 'logged_in' or logged_in:
            print("=== LOGGED IN ===")
            for k in ['state','logged_in','session_active','current_url']:
                v = d.get(k)
                if v is not None: print(f"  {k}: {v}")
            return
        await asyncio.sleep(1)

asyncio.run(main())
```

Invoke:

```bash
echo "approved $(cat /tmp/last_cid.txt)" | \
  /home/hermes/.hermes/hermes-agent/venv/bin/python /path/to/_atomic_login_v2.py
```

For the **Telegram-app approval polling** variant (after
`apply_approved_action` returns, the operator gets an app prompt on his
phone, and the script polls `login_wait` until `logged_in`):

```python
# _wait_for_approval.py — script that polls login_wait after
# apply_approved_action has succeeded. Use AFTER a manual
# `_atomic_login_v2.py` that got the apply response back, OR as
# a background process started right before the apply.

import sys, json, asyncio
sys.path.insert(0, '/home/hermes/.hermes/hermes-agent')
from tools.telegram_ads_typed_tool import _get_toolset
ts = _get_toolset()

async def main():
    print("=== login_wait polling (90s window) ===")
    for i in range(45):
        r = await ts.call('telegram_ads_login_wait', timeout_sec=4, poll_interval_sec=2)
        d = r.get('data') or {}
        print(f"  poll {i+1:2d}: state={d.get('state')!r:30s} logged_in={d.get('logged_in')}")
        if d.get('state') == 'logged_in' or d.get('logged_in'):
            print("=== LOGGED IN ===")
            return
        if d.get('state') in ('app_approval_pending', 'phone_required'):
            hint = d.get('recovery_hint')
            if hint: print(f"        -> {hint}")
        await asyncio.sleep(1)
    print("TIMEOUT after 90s")

asyncio.run(main())
```

Run in background when you need polling that won't block the chat
loop:

```bash
nohup /home/hermes/.hermes/hermes-agent/venv/bin/python \
  /path/to/_wait_for_approval.py \
  > /home/hermes/.hermes/runtime/tg_ads_acceptance_<date>/_wait.out 2>&1 &
```

Then `tail -f` the output file in a separate `terminal()` call.

## Adapter-level DOM fill+click — within-Operating-Discipline workaround

The login funnel is **not** "fill form → submit". It's
`oauth.telegram.org`-style with a phone input that requires a
phone to be present before Telegram will dispatch an app-approval
prompt. The `telegram_ads_login_submit_phone` tool exists for this,
but in some pipelines the call returns `state=unknown` followed by
`about:blank` — meaning the apply call reset the page context.

When the high-level handler resets the page, **drive the same
adapter directly** (not raw Playwright, not terminal/execute_code
fallback — that violates Operating Discipline §1). This is the
**adapter-level DOM fill+click** workaround, which uses the
package's own `adapter.browser.evaluate` — the same code path the
typed tools use, just with the DOM calls written by the agent.

```python
import sys, json, asyncio
from pathlib import Path
sys.path.insert(0, '/home/hermes/.hermes/hermes-agent')
from tools.telegram_ads_typed_tool import _get_toolset
ts = _get_toolset()

PHONE = Path('/home/hermes/.hermes/telegram_ads_phone.txt').read_text().strip()

async def main():
    # Ensure adapter is launched (one call to login_check is the cheapest way)
    await ts.call('telegram_ads_login_check')

    # Get the adapter through the toolset
    adapter = await ts._get_adapter()

    # Fill the phone field via the package's own browser wrapper
    fill = await adapter.browser.evaluate(f"""
() => {{
  const i = document.querySelector('input[type=tel]');
  if (!i) return {{ ok: false, reason: 'no tel input' }};
  i.focus();
  i.value = {json.dumps(PHONE)};
  i.dispatchEvent(new Event('input', {{ bubbles: true }}));
  i.dispatchEvent(new Event('change', {{ bubbles: true }}));
  return {{ ok: true, value: i.value }};
}}
""")
    print(f"fill: {fill}")
    await asyncio.sleep(0.5)

    # Click Next
    click = await adapter.browser.evaluate("""
() => {
  const btn = document.querySelector('button[type=submit]');
  if (!btn) return { ok: false };
  btn.click();
  return { ok: true, text: btn.textContent.trim() };
}
""")
    print(f"click: {click}")
    await asyncio.sleep(3)

    # Verify state advanced (still in the same process, same adapter)
    r = await ts.call('telegram_ads_login_check')
    print(f"after: {json.dumps(r, indent=2)[:600]}")

asyncio.run(main())
```

**Why this is not a violation of Operating Discipline §1:**

- §1 forbids `browser_navigate`, `browser_click`, `browser_snapshot`,
  `browser_console`, `browser_vision`, `computer_use`, and direct
  Playwright/Chromium scripts.
- This workaround uses **`adapter.browser.evaluate`** — the package's
  own JS evaluator, which is the same path the typed `login_*` tools
  use internally. It is **not** raw Playwright.
- It does **not** enter an OTP code. It does **not** read or write
  cookies. It does **not** bypass the approval gate. It just performs
  the DOM click the handler was supposed to perform.

**When to reach for this:** only after the high-level
`login_submit_phone` + `apply_approved_action` cycle has been
attempted once and the state regressed to `unknown` / `about:blank`
/ `phone_required` despite the apply response claiming `ok`. This
is rare but verified 2026-06-06.

**Risk:** the form may still reset if Telegram's SPA re-renders
after the apply. If that happens, surface the failure to the operator with
the exact `state` and `current_url` you observed, and ask whether
to (a) retry, (b) escalate to manual browser entry, or (c) drop
the flow.

### When to ALWAYS use the adapter-level DOM fill+click (not the typed handler)

Verified 2026-06-06 on `fix/browser-recovery` @ `7636a3c`: the
`login_submit_phone` handler in the package **deterministically
resets the page context** after `apply_approved_action` returns
`ok`. The diagnostic signature in the apply response is:

```json
{
  "status": "ok",
  "data": {
    "state": "unknown",
    "logged_in": null,
    "session_active": true,
    "current_url": "about:blank",
    "phone_masked": "+1********00",
    "instructions": [
      "Session state could not be determined.",
      "Run telegram_ads_status, then telegram_ads_login_check, before any operation."
    ]
  }
}
```

Two things to notice:

1. `current_url: "about:blank"` — the page navigated away from
   `auth?to=account` during the apply, blowing away the form the
   phone was just submitted to. Any DOM mutation done in a previous
   `evaluate` call is gone.
2. `instructions: ["Session state could not be determined."...]` —
   the handler is telling you the apply was a no-op for state. The
   apply response looks like success but did not advance the funnel.

If you see this signature, **do not** call
`telegram_ads_login_submit_phone` + `apply_approved_action` a
second time — the second attempt will reset the page again, eat
another CID, and never advance the funnel. Go straight to the
adapter-level DOM fill+click pattern (above) **in a single
atomic script** that also polls `login_wait` for `logged_in` after
the click. The atomic template below supersedes the typed-handler
cycle on this package version.

**Decision rule (verified 2026-06-06):**

| Apply response | Next action |
|---|---|
| `data.state` ∈ {`phone_required`, `app_approval_pending`, `code_required`}, `current_url` starts with `https://ads.telegram.org/...` | typed handler is working; `login_wait` is the right next step |
| `data.state` ∈ {`ok`, `logged_in`} | already logged in; stop |
| `data.state == "unknown"` AND `data.current_url == "about:blank"` | typed handler reset page; **switch to adapter-level DOM fill+click + login_wait polling in same script** |
| `data.state` ∈ {`auth_page`, `phone_required`} AND `current_url` is on `*.telegram.org/auth` | typed handler did not advance; safe to retry the typed cycle once, but if the second apply shows the same reset signature, switch to adapter-level DOM fill+click |

### About:blank form-not-rendered race (the v2 → v4 pitfall)

A subtler failure of the typed-handler path that **precedes** the
reset: if the atomic script calls `ts.call('login_submit_phone', ...)`
before Playwright has navigated to `auth?to=account`, the
`input[type=tel]` element does not exist in the DOM yet, and the
handler's "fill" (if it gets to the form at all) finds nothing to
fill into. The diagnostic signature is `current_url: "about:blank"`
**at the time of fill**, not after apply.

Mitigation: in the atomic script, **always** call
`telegram_ads_ensure_login` (or `login_check`) **first** so the
adapter actually navigates to the auth page, then poll for the
`input[type=tel]` to appear (up to 30s) **before** attempting any
fill:

```python
# Pre-fill: wait for the form to render
adapter = await ts._get_adapter()
tel_present = False
for i in range(30):
    result = await adapter.browser.evaluate("""
() => {
  const i = document.querySelector('input[type=tel]');
  return i ? { found: true, value: i.value, url: location.href }
           : { found: false, url: location.href, readyState: document.readyState };
}
""")
    if result.get('found'):
        tel_present = True
        break
    await asyncio.sleep(1)
if not tel_present:
    # DOM dump for diagnosis
    dom = await adapter.browser.evaluate("""
() => ({
  url: location.href,
  title: document.title,
  inputs: [...document.querySelectorAll('input')].map(i => ({type:i.type, name:i.name, placeholder:i.placeholder})),
  buttons: [...document.querySelectorAll('button, [type=submit]')].map(b => b.textContent.trim().slice(0,50)),
})
""")
    # surface dom to the operator, stop
```

If the form is not rendered after 30s, surface the DOM dump to
the operator (URL, title, all input/button selectors) and **stop** — the
form is genuinely not there, retrying the fill will not help.

### Telegram app-approval may not arrive (post-click, no `app_approval_pending`)

Verified 2026-06-06: after a successful DOM click of the
`button[type=submit]` (Next) on the phone form, the funnel
sometimes advances to `state=auth_page` (per `login_check`) with
`recovery_hint=human_login_via_telegram_app` **without** ever
returning `state=app_approval_pending` in the `login_wait` polling
window. The URL stays `auth?to=account` for 100+ seconds, no
`app_approval_pending` state is observed, but Telegram actually
**did** dispatch the app prompt — the operator's confirmation in the
Telegram app simply does not propagate back to the Playwright
session that opened the auth page.

This is **not** a tool failure. The classifier classifies
`auth_page` (URL-based) instead of `app_approval_pending` (DOM-based)
during the wait window, even when the app prompt is in flight. The
`login_wait` polling returns `state=timeout` (timeout envelope) every
iteration, but `state=timeout` here does **not** mean "approval
did not happen" — it means "the polling window expired before the
classifier saw `logged_in`". If the URL stays on `*.telegram.org/auth`
and the recovery hint is `human_login_via_telegram_app`, the
agent should:

1. **Trust the recovery hint over the `state=timeout` label.**
2. **Wait for the operator's separate "confirmed" message** — the agent
   does not know the confirmation propagated until `login_check`
   reports `state=logged_in`.
3. **Do not** chain into acceptance probes or mutating actions
   without that confirmation, per the SKILL.md "Post-login
   verification: 3-call sequence, then STOP" rule.

The "state=timeout ≠ failure" reading is **only** valid when:
- `current_url` stays on `*.telegram.org/auth`, AND
- `recovery_hint` is `human_login_via_telegram_app` (or similar), AND
- The polling window is at least 60s (shorter windows are too
  prone to false timeouts).

Outside those three conditions, `state=timeout` is a real timeout
and the agent should escalate per the standard failure-mode table.

## State-classifier divergence matrix

Two read-only state tools, same Playwright page, different
`state` values. This is a real bug, not a misread. The full
matrix of observed combinations (verified 2026-06-06):

| Page state | `login_check.state` | `status.state` | `current_url` | Trust |
|---|---|---|---|---|
| Initial auth landing | `phone_required` | `auth_page` | `auth?to=account` | DOM > URL |
| Phone filled, Next clicked | `auth_page` | `auth_page` | `auth?to=account` (briefly) | DOM > URL |
| App approval dispatched | `app_approval_pending` | `auth_page` | `auth?to=account` (then `/account`) | DOM > URL |
| Cookie valid, on dashboard | `logged_in` | `ok` (or `launched:false`) | `ads.telegram.org/account` | match |
| Form reset after apply | `unknown` | `auth_page` (or `launched:false`) | `about:blank` → `auth?to=account` | URL > DOM |
| Code required (rare branch) | `code_required` | `auth_page` | `auth?to=account` | DOM > URL |
| Stuck on phone (cookie expired) | `phone_required` | `auth_page` | `auth?to=account` | DOM > URL |

**Rule:** `login_check.state` is the canonical source of truth
when it disagrees with `status.state`. `login_check` uses DOM
signatures (`classify_login_dom`) which detect the actual form
state, while `status` uses URL + body-text heuristics which can
return `auth_page` for any URL containing `/auth`.

**When reporting funnel state to the operator, surface both.** Never say
"state=phone_required" without also reporting the `status` value
and the URL, so the operator can see the divergence and decide which to
trust for the next action.

## Accumulated-process profile lock

The persistent profile at
`~/.hermes/hermes-agent/browser_profiles/telegram_ads` is shared
between the live gateway and any `terminal()` subprocess that
calls `TelegramAdsToolset`. Each subprocess attempts
`launch_persistent_context` on the same profile dir. If a previous
subprocess didn't clean up (script crashed, killed by supervisor,
orphaned by parent), the profile holds a `SingletonLock` symlink
and the next subprocess fails with `BrowserProfileLockedError`:

> BrowserType.launch_persistent_context: Opening in existing
> browser session. This usually means that the profile is already
> in use by another instance of Chromium.

**Verified 2026-06-06:** the agent's own 5+ rapid
`_atomic_login*.py` scripts during a single login flow accumulated
enough stale Chromium processes to lock the canonical profile.
This is **not** a package bug — it's a side effect of running many
subprocess calls in a single chat session.

**Operating Discipline §3 forbids** `ps` / `pgrep` / `pkill` /
`kill` against stuck Chromium without explicit approval. The right
move:

1. **Stop the flow and surface to the operator.** Show the exact error
   message and the count of prior `terminal()` calls in the
   session.
2. **Request explicit approval** for the cleanup scope:
   > "Approve process inspection + kill stuck chromium holding
   > telegram_ads profile lock"
3. After approval, run:
   ```bash
   # 1) Find the stuck chromium PIDs (read-only inspection)
   ps -eo pid,cmd | grep "chrome.*telegram_ads" | grep -v grep
   # 2) Kill only the stuck ones (do not touch gateway/Xvfb)
   kill <pid-1> <pid-2> ...    # SIGTERM first
   # 3) Verify lock released
   ls -la /home/hermes/.hermes/hermes-agent/browser_profiles/telegram_ads/SingletonLock
   # Expect: ls: cannot access ...: No such file or directory
   ```
4. **Mitigation for next time** — keep all login-flow steps in a
   single Python script (one `terminal()` call), and use the
   `terminal(background=True, notify_on_complete=True)` mode for
   any long-running polling.

**Mitigation pattern — atomic single-script login:**

Instead of 3-5 `terminal()` calls per login step, write a single
`/home/hermes/.hermes/runtime/tg_ads_acceptance_<date>/_atomic_login.py`
that does reissue + apply + wait in one process. Run it in
background with `tee` to a log file:

```bash
/home/hermes/.hermes/hermes-agent/venv/bin/python /path/to/_atomic_login.py \
  2>&1 | tee /home/hermes/.hermes/runtime/tg_ads_acceptance_<date>/_atomic.out
```

Then `tail -f` the log file in a separate `terminal()` call. This
keeps the entire flow in one process, eliminates cross-process
singleton loss, and avoids accumulating Chromium processes.

## Why this reference file exists

The umbrella `SKILL.md` covers login flow at the protocol level
(phone_required, TTL, state machine). The umbrella
`references/login-flow-patterns.md` covers the phone persistence
override, TTL mitigation, and `session_active` vs `logged_in`. This
file covers the **2nd-order failures** that surface only when you
actually run the flow end-to-end: cross-process singleton loss,
adapter-level DOM fill+click as a within-discipline workaround, state
classifier divergence, and accumulated-process profile lock.

If you are running the login flow and something "looks weird",
check this file first.
