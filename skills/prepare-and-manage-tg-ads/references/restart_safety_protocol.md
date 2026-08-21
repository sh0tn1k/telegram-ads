# Restart Safety Protocol — Telegram Ads Gateway / Browser

**Added:** 2026-06-02
**Last updated:** 2026-06-02 (added `close_all()` + dispatcher try/finally references)
**Trigger:** any situation where gateway restart is required to activate
Telegram Ads patches AND orphan Chromium/Playwright processes may still
be alive from a previous gateway instance.

---

## When to use this file

Use it whenever the agent is about to do one of:

- Patch `hermes_telegram_ads/` (editable install) and needs the
  gateway to pick up the new code.
- Patch `gateway/run.py` shutdown path or any other code that the
  gateway imports on startup.
- Diagnose `browser_profile_locked` immediately after a gateway
  restart, where the previous gateway was running for hours and
  accumulated Chromium/Playwright children.
- Answer "should I restart the gateway?" when the user reports
  live-ads errors that survived a previous restart.

Do **not** use this file for routine `/restart` from Telegram — that
path is already gated by user intent and is not an agent decision.

---

## The 3-level decision tree (always ask, never assume)

The agent's job in this situation is to **diagnose and present a
plan**, not to act. The protocol is:

### Level 1 — Restart only (safest, recommended)

Trigger this if the patch is the new shutdown hook itself, or any
lifecycle code that activates on gateway restart.

```
systemctl --user restart hermes-gateway-default.service
```

What happens:
1. systemd sends SIGTERM to the gateway MainPID.
2. Gateway `shutdown_signal_handler` runs.
3. **The new shutdown hook** (`await
   TelegramAdsBrowserProfileManager.get_instance().close_all(timeout=5.0)`
   with 10 s outer backstop) closes Playwright context + driver
   cleanly. See `references/browser_profile_manager.md` §"Graceful
   close_all API" for the full 9-key structured result schema.
4. Chromium main process receives EOF on the Playwright pipe and
   exits; zygote / utility / renderer / gpu-process die with it.
5. Crashpad handlers (reparented to systemd) exit when Chromium
   exits.
6. `SingletonLock` symlink is removed by Chromium on its own exit.
7. systemd `Restart=always` (or `Restart=on-failure`) brings up a
   fresh gateway.
8. New gateway acquires the lock cleanly; no `browser_profile_locked`.

**Why safest:** activates the new code path (Level 1 teardown) AND
naturally cleans up orphan tree through the very mechanism the patch
fixes. No manual kill needed.

**Risk:** if the new shutdown hook itself is buggy and hangs forever
→ atexit fallback fires → SIGTERM-only fallback fires for the
`SingletonLock` PID → if that also fails (extremely unlikely), gateway
still exits because the teardown block is wrapped in `try/except
TimeoutError`. Bounded at 10 s outer + 5 s inner = 15 s total worst
case.

**Dispatcher note (added 2026-06-02):** the workflow dispatcher
(`workflows/__init__.py:run_workflow()`) now wraps `func(adapter, params)`
in its own `try/finally release_adapter()` block. So even if a
workflow raises mid-execution, the adapter release is guaranteed
**before** the gateway SIGTERM handler even runs. Restart safety
is multi-layered: dispatcher-level release → gateway SIGTERM
`close_all()` → atexit fallback → SIGTERM-only orphan kill.

### Level 2 — Manual SIGTERM Chromium main + restart

Trigger this only if the user explicitly says "no gateway restart
right now" or if Level 1 is somehow not viable (e.g. the new shutdown
hook is **itself** the regression being tested, and you want to verify
the existing atexit fallback first).

```bash
# 1. Identify the live Chromium browser main PID
readlink /home/hermes/.hermes/data/telegram_ads/browser_profile/SingletonLock
# → e.g. host-2975916
# The number after the dash is the PID.

# 2. Send SIGTERM to that PID ONLY
kill -TERM 2975916

# 3. Wait 3-5 s and verify
sleep 5
pgrep -af "chromium|playwright"
# Expected: empty output (or only this shell's pgrep itself)

# 4. If anything remains, STOP and report to user.
#    Do NOT escalate to SIGKILL without explicit user approval.
```

**Hard constraint:** each `kill` command requires its own user
approval. "I am about to send SIGTERM to PID X" is the request; the
user types "yes" (or whatever the agreed phrase is) for each one.
Do **not** batch multiple kills into a single approval.

### Level 3 — Leave alone (do nothing)

Trigger this when:
- The user is mid-conversation and the orphan tree is not blocking
  anything right now.
- The next scheduled `/restart` (e.g. nightly via cron) will resolve
  it for free.
- A separate cron reaper task is being designed (future work, not
  in scope for this skill).

**Honest limitation:** the orphan tree holds the
`SingletonLock`. Any code path that tries to start a new Chromium
against this profile will receive `browser_profile_locked`. So
"leave alone" is only safe if the gateway is not going to call
`acquire_adapter()` again before a restart.

---

## What the agent must NOT do (Operating Discipline restated)

These are hard no-gos, regardless of orphan-tree state:

- ❌ `pkill chromium` — broad pkill, hits user Chrome, other profiles.
- ❌ `kill -9` / `SIGKILL` — leaves `SingletonLock` stale, profile
  corruption, no graceful Playwright shutdown.
- ❌ `rm ~/.hermes/data/telegram_ads/browser_profile/SingletonLock` —
  Chromium created the symlink, Chromium must remove it. Manual
  removal while PID is alive is a no-op at best, race at worst.
- ❌ `pkill -P <gateway_pid>` — Chromium is **not** in the gateway's
  cgroup and not in its process group (setsid at launch), so
  parent-PID kill is a no-op.
- ❌ Restart as part of the same approval that authorized the patch.
  Patch approval ≠ restart approval. Restart is its own action with
  its own risk (downtime, lost in-flight messages, mid-turn
  interruption). Ask for restart approval **separately**, even if
  the user already approved the patch in the same session.
- ❌ "Just SIGKILL the Chromium main, the SingletonLock will get
  cleaned up next time" — it will not. SingletonLock is a hard
  failure for new acquisitions until the lock file is removed by
  the owning process or by `check_profile_lock`'s PID-alive check
  on a dead PID.
- ❌ "Approved permanently" carrying over. Per Operating Discipline
  rule 9, the user's standing approval to spend on Telegram Ads CPM
  does **not** extend to browser/process/debug actions. Restart,
  kill, SIGTERM, SIGKILL — all need their own explicit approval.

---

## What the agent SHOULD do in the report

When reporting the diagnosis + plan to the user, the report should
contain:

1. **State of the orphan tree:** how many Chromium/Playwright
   processes are alive, with their PID → role mapping (browser
   main, zygote, utility, renderer, gpu, crashpad, Playwright
   driver). Read with `ps -eo pid,ppid,pgid,sid,stat,cmd` filtered
   to `chromium|chrome|playwright` and grouped by parent.

2. **Cgroup situation:** confirm Chromium is in
   `user.slice/user-1004.slice/user@1004.service/...` (not in
   `…/hermes-gateway-default.service/`). One line, no narration.

3. **The recommended action** (Level 1 / 2 / 3) with the exact
   command(s). If Level 2, list each `kill` on its own line with
   the PID it targets, so the user can approve them individually.

4. **Restart requirement:** if the patch activates on gateway
   restart, say so explicitly ("this patch requires
   `systemctl --user restart hermes-gateway-default.service` to
   take effect — restart approval is separate from patch
   approval"). If the patch is editable-package-only, no restart
   is required.

5. **Rollback path:** the env-var escape hatches
   (`HERMES_TG_ADS_SKIP_ATEXIT_TEARDOWN=1`,
   `HERMES_TG_ADS_SKIP_SIGTERM_FALLBACK=1`) plus `git revert`.

6. **Stale SingletonLock cleanup** (only relevant if Level 3 was
   used and a manual `kill -TERM` was applied). After Chromium
   exits, the `SingletonLock` / `SingletonCookie` / `SingletonSocket`
   symlinks remain as dangling symlinks pointing to a now-dead PID.
   The next `acquire_adapter()` will see them and call
   `check_profile_lock()` which detects the dead PID via
   `os.kill(pid, 0)` → returns `{"locked": False, "stale": True}`.
   The stale symlinks are then overwritten by the new Chromium at
   next launch. **Manual deletion is NOT required and not
   recommended** — it would race with Chromium's own
   `SingletonLock` writer on next launch.

7. **Honest limitations:** what the plan does not cover (e.g. cron
   reaper is a separate task, browser cgroup scoping is a systemd
   change the agent will not make without separate approval).

The report should **not** include:

- Speculation about what the user "probably wants" or which
  action they would "likely approve".
- Markdown tables describing PID/process/PGID for every Chromium
  process. A summary count + the relevant PID is enough.
- "I will now run X" language. The agent is presenting a plan;
  the user approves or rejects.
- Any mention of ads.telegram.org UI screenshots, Xvfb restart,
  or second browser sessions.

---

## Worked example (2026-06-02)

**Observed:** 10 alive processes (1 Playwright driver + 1 Chromium
main + 2 zygote + 2 crashpad + 1 network + 1 storage + 1 renderer +
1 gpu). All Chromium processes in `user.slice/user@1004.service/...`
cgroup, not in `…/hermes-gateway-default.service/`. Gateway's
`KillMode=mixed` cannot reach them.

**Patch applied (2026-06-02, 2 iterations):**
- `browser_manager.py` — atexit hook + SIGTERM-only fallback (level 2 + 3)
- `gateway/run.py` — explicit `close_all(timeout=5.0)` in SIGTERM path (level 1)
- `workflows/__init__.py` — try/finally `release_adapter()` in dispatcher
- 3 test files: `test_browser_profile_manager_atexit.py` (18 tests),
  `test_browser_profile_manager_close_all.py` (16 tests),
  updated `test_browser_profile_manager_atexit.py` (2 assertions
  updated for `close_all` rename)
- **Total:** 227/227 tests passed across 9 test files (was 211 after
  first iteration; +16 from `close_all` + `use_adapter` API).

**Plan presented to user (Level 1, recommended):**

> Patch applied: `browser_manager.py` + `gateway/run.py` +
> `workflows/__init__.py` + 2 test files. 227/227 tests green.
> To activate in runtime, gateway restart is required:
>
> ```
> systemctl --user restart hermes-gateway-default.service
> ```
>
> This is the safest path: the new `close_all()` shutdown hook
> will close Playwright + Chromium cleanly through the same
> teardown that the patch fixed. After restart: 0 orphan
> processes, SingletonLock removed by Chromium.
>
> No processes were killed. No browser was opened. Restart
> approval is separate from patch approval.

**Fallback plan presented (Level 2):**

> If gateway restart is not acceptable right now, the orphan
> tree can be cleaned up manually with one SIGTERM at a time
> (each requires its own approval):
>
> ```
> kill -TERM 2975916  # Chromium browser main
> ```
>
> Wait 5 s, verify with `pgrep -af chromium`. If anything
> remains, stop and report — do not escalate to SIGKILL.

**Fallback plan presented (Level 3):**

> If neither restart nor manual cleanup is acceptable, the
> orphan tree will hold the `SingletonLock` until the next
> natural restart. Any `telegram_ads*` tool call that tries to
> start a new Chromium will fail with `browser_profile_locked`.

**Actual outcome:** gateway SIGTERM during the agent's mid-turn
interrupt caused natural teardown. Cleanup commands were run
**after** the interrupt (with separate explicit approval per the
3-kill plan: `kill -TERM 2975916` only — Chromium and Playwright
both exited cleanly). 3 stale SingletonLock* symlinks
(`SingletonLock`, `SingletonCookie`, `SingletonSocket`) were
removed by hand (they pointed to a now-dead PID, and the next
Chromium launch would overwrite them anyway).

---

## Pitfalls

- **Path.exists() returns False for broken symlinks.** The
  SingletonLock is a dangling symlink (`host-<pid>` with target
  removed at Chromium exit). Use `lstat()`, not `exists()`. See
  `references/browser_profile_manager.md` §"Why the symlink PID
  extraction matters" for the full gotcha.
- **Chromium creates its own PGID/SID at launch.** `kill -- -<pgid>`
  with the gateway's PGID does **not** reach Chromium. systemd
  cgroup-based kill also fails. The browser tree is detached from
  the gateway's cgroup from the moment `launch_persistent_context`
  returns.
- **"Restart gateway" does not require the user to reload
  ads.telegram.org in the browser.** The persistent profile
  preserves cookies; on next `telegram_ads_workflow(snapshot)` the
  adapter reuses the existing session.
- **"Restart will fix browser_profile_locked" is only true if
  Level 1 teardown runs cleanly.** If the gateway is hard-killed
  (OOM, kill -9), atexit does not run, and the orphan tree
  remains. This is why Level 3 (cron reaper) is a separate
  future task.
- **Do not propose `kill -9` as "simpler" or "more reliable".**
  SIGKILL on Chromium leaves the profile in an undefined state;
  on Playwright it leaks the driver process; the manager
  records `_closed=True` so the next start would do a fresh
  launch but the profile might still be mid-write.
- **`shutdown()` vs `close_all()` confusion.** `close_all()` is
  the new structured per-step API (9-key result, timeout=5.0
  default). `shutdown()` is kept as a backward-compat alias
  (2-key result, no built-in timeout). When writing new teardown
  code, **always use `close_all()`** — the structured result makes
  gateway logging much more useful for diagnosing partial
  failures.
- **Dispatcher `try/finally` is additive, not replacing.** Even
  if a workflow uses `use_adapter()` correctly, the dispatcher's
  outer `try/finally release_adapter()` is a second safety net.
  They compose; they don't conflict. (The dispatcher's release
  is a no-op if the workflow already released via
  `use_adapter()` — `release_adapter()` is idempotent and just
  decrements `active_operations` and releases the asyncio.Lock.)
- **Patch + restart are SEPARATE approvals.** Even if the user
  approved the patch in this session, do not bundle "I will now
  restart the gateway" into the same response. Ask for restart
  approval explicitly. Restart has its own cost (downtime,
  in-flight message loss risk) and is the user's call, not the
  agent's. Per Operating Discipline rule 9, the user's standing
  approval to spend on CPM does NOT extend to gateway restart.
