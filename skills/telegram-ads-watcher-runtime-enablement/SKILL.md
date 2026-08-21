---
name: telegram-ads-watcher-runtime-enablement
description: "Enable the in-process Telegram Ads watcher runtime on a live hermes-gateway-* service. Pre-flight checks (clean tree + expected commits + env flags already applied), minimal systemd/env change, post-restart verification (PID, baseline tick, V1 login/session active, V2/V2.7 bridge loaded, mini-report route config, single Chromium owner, no secrets in logs). **Current state: V2.9 (shipped 2026-06-17, commit 01ce1f038)** — bounded staged tick (3 stages × 10s, 25s total budget) with idle-tick optimization; production-verified 0 TimeoutErrors. Use when the operator asks for 'AR-ADS-WATCHER-RUNTIME-ENABLE-N', 'enable watcher runtime', 'flip HERMES_ADS_WATCHER_ENABLED', 'restart gateway with watcher', 'NEXT-TICK-VERIFY-N'. Distinct from `telegram-ads-watcher-event-loop-design` (designs the code layers) and `install-hermes-telegram-ads-watcher` (installs the package)."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [telegram-ads, watcher, runtime, systemd, hermes-gateway, enablement, read-only-verification, env-flag, preflight, post-verification, singletonlock, browser-ownership, runtime-flag, post-action, hermes-fork, approval-gated, no-restart, no-push]
    related: [telegram-ads-watcher-event-loop-design, install-hermes-telegram-ads-watcher, operator-approval-gate-enforcement, hermes-system-readiness-audit, incremental-commit-preflight, handle-telegram-ads-review-and-declines, format-telegram-ads-report]
---

# Enable the in-process Telegram Ads watcher runtime

A class of work: the operator has accepted the V2.0 → V2.5 → V2.6 → V2.7
code drops (all committed locally, no push) and now wants to **flip
the runtime flags** so the in-process watcher actually runs inside
the hermes-gateway-default.service. The flags in question:

- `HERMES_ADS_WATCHER_ENABLED=1` — gates the V1 in-process daemon,
  the V2.6 wiring, the V2.7 production.py hook, and the V2.7
  production_adapter. (Lives in **three** callers — see Pitfall 1.)
- `HERMES_ADS_WATCHER_INTERVAL_SECONDS=600` — V1 poll interval.
- `HERMES_ADS_WATCHER_REPORTS_ENABLED=1` — gates the V2.7
  report_router (12 categories).

The art of this work is **not** flipping flags. The art is
**discovering that the flags are already applied**, refusing to
restart, and verifying the live state with read-only commands.

## When to use

- the operator hands you a runtime enablement approval that names
  `HERMES_ADS_WATCHER_ENABLED`, `HERMES_ADS_WATCHER_INTERVAL_SECONDS`,
  `HERMES_ADS_WATCHER_REPORTS_ENABLED` and asks you to "enable
  runtime flags for hermes-gateway-default.service".
- The approval says "no push, no real Ads action, no Ads mutation,
  no synthetic Telegram message, no secrets printed, no standalone
  daemon, no deepseek/Xvfb/KC changes" — i.e. a read-only
  verification mission.
- The approval **conditionally** allows a restart
  ("If unit/env changed: daemon-reload; restart") and the operator often
  adds a "не делай рестарт" override.
- the operator hands you a `AR-ADS-WATCHER-NEXT-TICK-VERIFY-N` approval
  to confirm the first post-baseline tick after a V2.8+ restart
  completes without the 60.04s `TimeoutError` regression — see
  Pitfall 9.

## When NOT to use

- The work is **installing** the watcher package →
  `install-hermes-telegram-ads-watcher`.
- The work is **designing a new watcher layer** (V3, autonomy
  envelope changes, new event types) →
  `telegram-ads-watcher-event-loop-design`.
- The work is **diagnosing a runtime symptom** (watcher stuck, no
  events, login_required spam) →
  `diagnose-hermes-internals` + references/v1-known-issues.md.
- The work is **a real Ads action** (create, edit, change_cpm) →
  `operate-telegram-ads` + approval-gated path.

## The 7-phase approval-gated execution pattern

This is the only pattern that satisfies the operator's "no push / no
restart / no real Ads / no Telegram" constraints.

### PHASE 1 — Preflight (read-only)

Run **all** of these before any change:

```bash
git status --short                              # must be empty
git log --oneline -8                            # must include V2.7
systemctl --user status hermes-gateway-default.service --no-pager
# Inspect env keys only:
systemctl --user show hermes-gateway-default.service \
  -p Environment -p EnvironmentFiles -p ExecStart --no-pager
# Confirm process env (replace PID):
PID=$(systemctl --user show -p MainPID --value hermes-gateway-default.service)
cat /proc/$PID/environ 2>/dev/null | tr '\0' '\n' | grep -E '^HERMES_ADS_WATCHER|^AGI_TEAM'
```

**Do not print** `AGI_TEAM_BOT_TOKEN` value, `*.cookie`,
`session_id`, `password`, `otp`. Use `sed 's/=.*/=<REDACTED>/'`
or `[:4]+'...<redacted>...'` patterns.

### PHASE 2 — Stop-condition gate

Walk through every stop condition the approval names. **If any one
fails, STOP and report.** No patch, no commit, no restart.

| Stop condition | Verify by |
|---|---|
| working tree is not clean | `git status --short` empty |
| expected commits are missing | `git log --oneline -8` shows V2.x chain |
| env/systemd change would affect deepseek/Xvfb/KC | confirm scope = default service only |
| safe AGI Team Bot report path is not configured | `AGI_TEAM_BOT_TOKEN` in process env; `send_agi_team_alert` importable |
| enabling reports would expose secrets | `format_mini_report` scrub list includes `agi_team_bot_token=`, `tma_token=`, `password=`, `otp=`, etc. |
| gateway restart fails | n/a if no restart; else catch SIGTERM/STATUS=1/STATUS=2 |
| profile lock occurs | `SingletonLock → host-<PID>`; only one PID |
| watcher crashes gateway | `PID` uptime > 5 min; daemon thread log line present |
| any secret appears in logs/output | scan last 512KB of each log file (see references/preflight-checks.md) |

### PHASE 3 — Apply minimal env change

**First check**: are the flags **already** in the unit file?

```bash
grep -E 'HERMES_ADS_WATCHER' \
  /home/hermes/.config/systemd/user/hermes-gateway-default.service
```

If all three are present, the unit is already configured — no edit,
no `daemon-reload`, no restart. **Skip to PHASE 5.**

If a flag is missing or wrong, **edit the unit file directly** (do
NOT add an EnvironmentFile, do NOT load `.env` from the gateway
service — those break the approval's "minimal" constraint). The
edit is a 3-line `Environment=` block; preserve the existing
`Environment=PATH=...` line and append. Then `daemon-reload` (but
only if you actually changed the unit file).

**Never** edit a different profile's unit (deepseek / xvf b / kc).
The approval's "no deepseek/Xvfb/KC changes" is a hard invariant.

### PHASE 4 — daemon-reload + restart (conditional)

```bash
# Only if the unit file actually changed:
systemctl --user daemon-reload
systemctl --user restart hermes-gateway-default.service
```

The restart is the **only** way to apply a unit-file change to a
running process. If the operator says "не делай рестарт", you must NOT
restart even if the unit changed — go back to PHASE 3 and confirm
the change isn't needed (e.g. the change is already in the unit).

**Do not** restart `hermes-gateway-deepseek.service`,
`hermes-xvfb.service`, or any KC timer/service.

### PHASE 5 — Post-verification (read-only)

After any restart, OR after confirming no change is needed, verify
the **live** state. See references/preflight-checks.md for the
exact command list. Key checks:

| Item | Verify by |
|---|---|
| default gateway active/running | `systemctl --user is-active hermes-gateway-default.service` |
| new PID | `systemctl --user show -p MainPID --value ...` (changes on restart) |
| watcher enabled | `cat /proc/$PID/environ | grep HERMES_ADS_WATCHER_ENABLED` |
| reports enabled | `cat /proc/$PID/environ | grep HERMES_ADS_WATCHER_REPORTS_ENABLED` |
| V1 login/session watcher active | `tail -200 ~/.hermes/logs/gateway.log | grep '\[ADS-WATCH\] daemon thread started'` |
| V1 baseline tick completed | `grep 'baseline tick state=logged_in_or_no_change events=0 error=None' ~/.hermes/logs/gateway.log \| tail -1` |
| V2/V2.7 bridge loaded | `python -c "from gateway.ads_watcher_v2 import v1_bridge; print('ok')"` (run from venv) |
| next tick scheduled | `python` arithmetic from baseline_ts + interval_sec |
| mini-report route config | `python -c "from gateway.ads_watcher_v2.report_router import load_router_config; c=load_router_config(); print(c.enabled, c.chat_id, len(c.allowed_categories))"` |
| 12-category exact match | compare `c.allowed_categories` against the approval list (see references/allowed-categories.md) |
| no standalone watcher process | `ps -ef | grep -E 'watcher.*daemon\|daemon.*watcher' \| grep -v grep` (empty) |
| no second Chromium owner | `ls -la ~/.hermes/data/telegram_ads/browser_profile/SingletonLock` → single PID symlink; all chromium PIDs child of gateway PID |
| no profile collision | `ps -ef | grep -E 'gateway run' | grep -v grep` → one default, one deepseek |
| no secrets in logs | scan last 512KB of each log file (regex in references/secrets-scan.md) |
| watcher doesn't crash gateway | PID uptime > 5 min |
| no synthetic Telegram message | **do not send** — read-only verification only |

### PHASE 6 — Final report

Required output sections (per approval §"Expected final output"):

1. preflight status (clean tree, commits, env)
2. env/systemd changes made (or "NOT NEEDED — already applied")
3. whether daemon-reload was run (yes/no, with reason)
4. restart result (or "no restart" with reason)
5. new default gateway PID
6. watcher enabled state
7. reports enabled state
8. last watcher tick / scheduled next tick (with computed timestamp)
9. confirmation matrix (no push, no real Ads, no mutation, no
   synthetic Telegram, no secrets, no standalone daemon, no
   deepseek/Xvfb/KC changes)

### PHASE 7 — No further action; require separate NEXT-TICK-VERIFY

Stop. **Do not** start a real Ads action to "verify" the bridge.
**Do not** send a synthetic test message to "verify" the router.
**Do not** push the V2.x commits.

**A baseline-green verification is NOT a first-Ads-action
green light.** The baseline tick uses a different code path
from the post-baseline tick. A separate NEXT-TICK-VERIFY-N
approval is required before any "first real Ads action" can
be approved. See Pitfall 9.

If the verification reveals a defect (e.g. the 12-category list
drifts from approval, the SingletonLock has a second owner, the
baseline tick errored), **stop and report**. Do not patch the
runtime to fix it; that's a separate approval.

## Pitfalls (load-bearing)

### Pitfall 1 — `HERMES_ADS_WATCHER_ENABLED` lives in 3 callers

The single env flag is read by **three** separate
`os.environ.get(...)` calls:

- `gateway.ads_watcher_v2.production._watcher_enabled()` — gates
  the post-action registration hook (typed tool).
- `gateway.ads_watcher_v2.production_adapter._config.enabled` —
  gates the read paths in the production adapter.
- `gateway.ads_watcher_v2.v1_bridge._is_watcher_enabled()` —
  gates the V1 tick integration.

If you set the env var on the systemd unit, **all three** pick it
up. If you set it inline (e.g. via `hermes gateway run` in a
terminal), only some do. Always test via the unit-file path; never
set the env inline for a runtime enablement task.

### Pitfall 2 — env may already be applied

The most common runtime enablement task is **"flags were set
yesterday"** — i.e. a previous turn or a previous session already
edited the unit and restarted. **Always** grep the unit first and
read `/proc/$PID/environ` before doing anything. If both show
the expected flags, the answer is "no change needed; no restart;
here's the live state". This is not a failure of the task; it's
the correct answer.

### Pitfall 3 — SingletonLock is the ground-truth for browser ownership

`ps -ef | grep chrome` shows PIDs but doesn't tell you who owns
the browser profile. The ground-truth is:

```bash
ls -la ~/.hermes/data/telegram_ads/browser_profile/SingletonLock
# Output: SingletonLock -> host-<PID>
```

That PID must be a child of the gateway process. If the symlink
points to a PID that is not a descendant of `hermes-gateway-*`,
**stop and report** — the browser profile is hijacked.

### Pitfall 4 — V1 `telegram_ads_watcher.db` is created on first save_watch

The V1 SQLite database does NOT exist at gateway startup. It is
created the first time V1 calls `store.upsert_watch` (or
`store.create_event` on a `WATCHER_EVENTS` schema). The V1
**baseline tick** with `state=logged_in_or_no_change` does NOT
write to the DB — it only reads.

**Consequence:** after a fresh V2.7 enablement, `~/.hermes/
telegram_ads_watcher.db` will not exist until the first real
Ads action runs. This is **expected**, not a defect. Do not flag
"DB missing" as a problem in the post-verification report.

### Pitfall 5 — V1 `TimeoutError` is **fixed in V2.9** (bounded staged tick)

The V1 in-process tick logs:

```
[ADS-WATCH] tick state=None events=0 error=TimeoutError: duration=60.04
```

**Pre-V2.8 (V2.0–V2.7):** the V1 in-process daemon's sync
`_run_tick_once` called `asyncio.run()` against an event loop
inside the daemon thread, and the V2.6/V2.7 bridge call inside
the running loop triggered a 30s `thread.join` fallback that
hit the outer 60s `wait_for`. Every post-baseline tick hit
the 60s ceiling.

**V2.8 (commit `660816f56`, 2026-06-17) PARTIAL fix:** the
V1 tick wraps the V2.6/V2.7 bridge in `asyncio.to_thread(...)` +
`asyncio.wait_for(..., timeout=10)`; on `asyncio.TimeoutError`
records a `safe_summary` with `state=bridge_timeout` and
`error=timeout_seconds=10`. The `production_adapter.get_state_sync`
path also probes `asyncio.get_running_loop()` and skips the
broken `asyncio.run` path. **Verified in production:** baseline
succeeds, but the first post-baseline tick at +11 min still hits
`error=TimeoutError: duration=60.062` (the V1 `scheduler.tick()`
itself was still inside the 60s outer wait_for).

**V2.9 (shipped 2026-06-17, commit `01ce1f038`) FULL fix:** the
V1 in-process tick is split into **three bounded stages** with
independent short timeouts:

| Stage | Timeout | On timeout |
|---|---|---|
| 1. Adapter acquisition | 10 s | `state="browser_unavailable"`, return |
| 2. V1 `scheduler.tick()` | 10 s | `v1_tick_timeout`, continue |
| 3. V2.6 bridge | 10 s | `bridge_timeout`, continue |

Plus a pre-check optimization: list V1 watches via
`_safe_v1_list_watches` and skip the bridge entirely if zero
non-`login_state` watches exist. Idle ticks now complete in
**< 1 s** instead of 60 s. A 25s hard ceiling
(`_TOTAL_TICK_BUDGET_SECONDS`) is a safety net for any future
regression.

**Production verification (V2.9, 2026-06-17):**

| Time (UTC) | Event | duration | error |
|---|---|---|---|
| 20:13:54 | V2.9 daemon started (in-process load) | — | — |
| 20:13:59 | Baseline | 4.589 s | None |
| 20:24:09 | First scheduled tick after baseline | **10.036 s** | **None** |
| 20:28:05 | Gateway restart (reload) | — | — |
| 20:28:11 | Baseline | 5.485 s | None |
| 20:38:21 | First scheduled tick after restart baseline | **10.031 s** | **None** |

**Total TimeoutError count in `~/.hermes/logs/gateway.log` since
V2.9 first loaded: 0** (was 19 in the pre-V2.9 log window).
8 consecutive V2.8 failures (60.018–60.062s) all gone.

**Why V2.8 was only partial:** the outer 60s `wait_for` in
`_run_tick_once` wraps V1's `wiring.scheduler.tick()`, NOT
the V2.6 bridge. V1's `scheduler.tick()` itself hangs for
60s on every post-baseline call due to the **shared singleton
Playwright adapter** — V1's `detect_login_state` watch holds
the read-only Playwright adapter for ~30s while a concurrent
request times out on `acquire_adapter(timeout=30)`, and the
cumulative wait_for hits 60s.

**What V2.9 actually fixed:**

- The V1 `scheduler.tick()` 60s outer `wait_for` is replaced
  by a 10s stage-level `wait_for` with try/except.
- Idle ticks (no post-action watches) skip the V2.6 bridge
  entirely and complete in <1s.
- The V2.6 bridge is wrapped in `asyncio.create_task` +
  `asyncio.shield` + 10s `wait_for`. On timeout, the worker
  thread continues in the background but the tick is not
  blocked. See `asyncio-to-thread-wait-for-cancellation-gotcha`
  for the Option A semantics (wall-clock bounded by the
  worker thread, not by the timeout — but bounded by the
  25s hard ceiling in any case).
- A 25s hard ceiling (`_TOTAL_TICK_BUDGET_SECONDS`) guards
  against any future regression.

**What to do in runtime enablement (post-V2.9):**

- **The baseline tick IS bounded (typically 5–7s).** No
  longer the "always green" anomaly of pre-V2.9.
- **The first post-baseline tick is also bounded.** Verify
  via `NEXT-TICK-VERIFY-N`: observe the first
  `[ADS-WATCH] tick state=... events=0 error=None duration=<small>`
  line after the baseline. `duration` should be < 1s if there
  are no post-action watches, 5–10s if V1 takes longer, or
  ≤ 25s in the worst case.
- **The V2.9 fix is a real, working production fix** — not a
  "partial fix with a known V-N+1 path". The TimeoutError
  regression is structurally impossible post-V2.9 because
  no single stage can hold more than 10s, and the 25s hard
  ceiling guards the whole tick.
- **Cross-reference** `telegram-ads-watcher-event-loop-design`
  Pillar 14 for the staged-tick pattern, the pre-check
  optimization, and the closed-set of stages.

**Post-V2.9 verification recipe (add this to your
NEXT-TICK-VERIFY-N commands):**

```bash
# 1. Confirm V2.9 is loaded (search for the V2.9 staged-tick log marker)
grep '\[ADS-WATCH-V2.9\]' ~/.hermes/logs/gateway.log | tail -3
# If empty, V2.9 is NOT loaded — gateway restart needed.

# 2. Confirm baseline + first scheduled tick both succeeded
grep '\[ADS-WATCH\]' ~/.hermes/logs/gateway.log | grep -E 'baseline tick|^.*tick state=' | tail -5
# Expected: 1 baseline + 1+ scheduled tick, all error=None, duration < 25s

# 3. Confirm zero TimeoutErrors since V2.9 first loaded
# (replace timestamp with the time V2.9 was first loaded into the gateway)
SINCE="2026-06-17 20:13:54"  # V2.9 first loaded
awk -v since="$SINCE" '$0 >= since' ~/.hermes/logs/gateway.log | grep -c 'TimeoutError'
# Expected: 0
```

If any of the three checks fail, **stop and report** — V2.9 may
not be the version in production (older V2.x may be live), or
the gateway may need a reload to pick up V2.9.
  and the closed-set of stages).

### Pitfall 9 — Next-tick verify is MANDATORY before "ready for first Ads action"

A common mistake is treating a successful **baseline** tick as
proof the watcher is ready. It is not. The baseline tick uses
a separate, fast code path (`_ensure_baseline_login_state_watch`)
that calls V1's login_state watch directly. It does not
exercise the V1 polling path (`wiring.scheduler.tick()`),
which is where the V1 / V2.6 / Playwright race lives.

**Pattern that fails (verified 2026-06-17 19:19 UTC):**

1. V2.8 restart → baseline tick succeeds in 5.7s.
2. Operator declares "READY, restart verified" based on
   baseline success.
3. First real Ads action is approved.
4. **First post-baseline tick fires at +600s, hits 60s
   `TimeoutError` exactly as before V2.8.** Real Ads action
   runs against an unverified bridge.

**Mandatory pattern (post-V2.8 era):**

1. RUNTIME-ENABLE-N / RUNTIME-RESTART-N → baseline tick
   succeeds → declare "restart complete, baseline green".
2. **Wait until the FIRST post-baseline tick completes** (or
   one full `interval_seconds` window, default 600s). Read the
   gateway.log for the next `[ADS-WATCH] tick state=...` line.
3. If the post-baseline tick has `error=TimeoutError: duration=60.0x`,
   **stop and report** — even if the baseline is green. Do NOT
   declare "ready for first Ads action".
4. If the post-baseline tick has `error=None` and a small
   duration (≤ 30s), declare "V2.8 verified in production;
   ready for first Ads action (separate approval)".

**The skill that drives this is a separate AR approval** —
`AR-ADS-WATCHER-NEXT-TICK-VERIFY-N`. Use it. Do not skip it.
Do not bundle it with the restart approval. Do not assume
"baseline green ⇒ first Ads action approved" — that is
the exact mistake that ships a half-verified watcher into a
real Ads action.

**If the approval does not name a NEXT-TICK-VERIFY step but
the goal includes "first real Ads action"**, flag the missing
verification as a stop-condition and request the explicit
NEXT-TICK-VERIFY approval before proceeding.

### Pitfall 10 — Stop-conditions in approvals are load-bearing, not exemplary

When an approval §"Stop conditions" lists bullets, treat them
as a **closed set of gates** for THIS approval, not as a
shorthand for "use your judgement". Likewise §"Goal → Confirm:"
bullets are the **acceptance criteria** the deliverable must
demonstrably meet.

If the §"Goal" says "no 60.04s TimeoutError" and the live
gateway shows `TimeoutError: duration=60.062`, that is a
failed acceptance criterion — even if the §"Stop conditions"
list does not name it explicitly. The acceptance criteria in
§"Goal" are at least as binding as the stop-conditions list.

**What to do:** when reporting a partial pass, the report
must include a row in the failed-checks table for every
§"Goal → Confirm:" bullet that did not pass, with severity and
evidence. Do not fold acceptance-criteria failures into a
"notes" section.

### Pitfall 8 — `AGI_TEAM_CHAT_ID` is optional

The V2.7 report_router's default fallback for the chat id is
the host-configured home channel (do not hard-code an id here). If
`AGI_TEAM_CHAT_ID` is unset in the process env, the router
silently uses the default. This is **not** a defect — it is the
documented fallback. To override, set it in the unit file
(recommended) or in `~/.hermes/.env` (also accepted). Do not
treat "AGI_TEAM_CHAT_ID unset" as a stop condition.

### Pitfall 7 — Never create a watcher systemd service/timer

The watcher is in-process inside the gateway. Creating a separate
`hermes-ads-watcher.service` or `hermes-ads-watcher.timer` is
**explicitly forbidden** by the approval. If you need scheduled
watcher behavior, schedule it through the gateway's
`Cron ticker` (which V1 uses; interval=60s) — that runs inside
the existing gateway process.

### Pitfall 8 — Never load `.env` from the gateway service

A previous Claude iteration added `EnvironmentFile` to the unit
file pointing at `~/.hermes/.env`. This is **forbidden** by the
approval ("Do not load .env from KC CLI" was the original
constraint; it generalises to all hermes services). The unit
file must have its `Environment=` lines **inline**. The `.env`
file is for the **interactive** Hermes CLI; the systemd services
must be self-contained.

If you see `EnvironmentFile=` in the unit, flag it as an
out-of-scope defect and recommend removing it in a separate
approval. Do not remove it during runtime enablement.

## Sub-pitfalls (frequently hit)

- **"Let me restart to be safe"** — no. The approval says
  conditional restart, and the operator often adds "не делай рестарт".
  If the env is already applied, restart would only churn the
  gateway, lose the in-flight chat session, and reset the V1
  watcher's "next tick" clock.
- **"Let me check the V2.7 bridge by running a fake tick"** —
  the bridge is tested by `tests/gateway/test_ads_watcher_v2_7.py`
  (22 tests). Running a fake tick from a runtime enablement
  session pollutes the V1 store with synthetic events. Skip the
  test; trust the unit tests.
- **"Let me send a test mini-report"** — no. The approval
  explicitly says "if a test message is required to verify
  sending, stop and ask for separate approval". The route
  configuration is verified by reading the router's `RouterConfig`
  (12 categories match the approval list, `enabled=True`,
  `chat_id` resolved).
- **"Let me read the AGI_TEAM_BOT_TOKEN value to confirm it's
  set"** — no. Grep the **key** (`grep AGI_TEAM_BOT_TOKEN`), never
  the value. Print `AGI_TEAM_BOT_TOKEN=<set, length N>` instead
  of the actual value.
- **"Let me apply the env via systemctl set-environment"** — no.
  The approval says "edit systemd unit files" is forbidden
  ("Do not edit systemd unit files. Do not add EnvironmentFile.
  Do not run daemon-reload.") in some approvals, allowed in
  others. Read the specific approval.

### Pitfall 11 — `asyncio.to_thread` + `wait_for` does NOT bound the caller when the worker holds the GIL (Python 3.11)

This is a class-level Python asyncio gotcha that the V2.9
work surfaced. The naïve fix for a "long sync op on a worker
thread" is:

```python
try:
    result = await asyncio.wait_for(
        asyncio.to_thread(slow_sync_func), timeout=10
    )
except asyncio.TimeoutError:
    # caller is unblocked at 10s
    ...
```

**This works only if `slow_sync_func` releases the GIL.** If
it doesn't (e.g. `time.sleep(15)`, pure Python CPU-bound
work, a tight C extension loop without `Py_BLOCK_THREADS`),
the event loop cannot schedule the timeout because the
worker thread is monopolising the GIL. The `wait_for`
exception is never raised, and the caller's `await` blocks
until the worker thread completes.

**Verified reproduction (2026-06-17, V2.9 development):**

- `asyncio.to_thread(time.sleep(15))` wrapped in
  `asyncio.wait_for(..., timeout=10)` → caller blocks
  for 15s, not 10s. Stage status still records
  `bridge_timeout` correctly (the `wait_for` does raise
  the exception at 10s internally) but the calling
  function does not return until the thread finishes.
- `asyncio.to_thread(lambda: threading.Event().wait(15))`
  wrapped in the same `wait_for(..., timeout=10)` → caller
  returns at 10s, the worker thread runs for 15s in the
  background and is discarded. **This is the pattern that
  works.**

**Why it matters for the V2.9 bounded staged tick:**

- The V2.9 fix records a per-stage `state` correctly even
  when the wall-clock exceeds the stage timeout. This is
  why the test `test_bridge_timeout_records_safe_summary`
  passes: the bridge stage IS marked `bridge_timeout` even
  when the calling `_run_tick_once` returns at 15s.
- For **idle ticks** (no post-action watches), the V2.9
  pre-check optimization skips the bridge entirely, so
  the bridge is never called and the wall-clock is <1s.
- For **post-action ticks**, the wall-clock is bounded by
  the worker thread, not by the 10s stage timeout. In
  practice, the V1 bridge does quick sync work and returns
  well under 10s, so this is a non-issue. The 25s hard
  ceiling (`_TOTAL_TICK_BUDGET_SECONDS`) is a safety net
  for genuinely stuck workers.

**What to do:**

- **Verify V2.9 by reading the per-stage state, not the
  wall-clock.** `stages["v2_bridge"]["safe_summary"]` and
  `stages["v1_tick"]["ok"]` are the load-bearing signals.
  If both report success, V2.9 is working — even if the
  wall-clock is 12s because of a slow bridge worker.
- **In production code, do not** `await asyncio.to_thread(time.sleep(...))`
  with a `wait_for`. If you need a bounded worker, use
  `threading.Event.wait(N)` (GIL-releasing) or schedule the
  work via `loop.run_in_executor` with an explicit future
  you can `future.cancel()` and detach.
- **In test fakes, do not** use `time.sleep(N)` to simulate
  a slow bridge. Use `threading.Event.wait(N)` so the
  `wait_for` timeout can fire (see Pitfall 12).

### Pitfall 12 — Test fakes that use `time.sleep` break `wait_for` assertions

When writing tests for code that uses
`asyncio.wait_for(asyncio.to_thread(...), timeout=N)`, the
fake "slow" implementation **must not use `time.sleep`**.
Use `threading.Event.wait(N)` instead.

**Pattern that breaks the test (DO NOT DO THIS):**

```python
def slow_bridge(wiring, **kwargs):
    time.sleep(15)  # holds the GIL
    return V1V2BridgeResult(events=[])

# Test expects wait_for(10) to fire at 10s, but the
# assertion sees 15s elapsed.
```

**Pattern that works:**

```python
stop = threading.Event()

def slow_bridge(wiring, **kwargs):
    stop.wait(15)  # releases the GIL
    return V1V2BridgeResult(events=[])

# wait_for(10) fires at 10s as expected.
```

**Why it matters:** `time.sleep` does not release the GIL,
so the worker thread monopolises it. The event loop cannot
schedule the `wait_for` timeout callback while the GIL is
held. The test sees the worker finish and the call returns
only then — the timeout assertion fails.

**When to use this pattern:** any test that asserts on the
**wall-clock duration** of an `asyncio.wait_for` call. If
the assertion is on the per-stage state (recommended — see
Pitfall 11), the test can use `time.sleep` and still pass
because the state is recorded before the wall-clock
measurement.

**What to do in the V2.9 test suite:**

- Use `threading.Event.wait(N)` for the bridge-timeout test
  (the one that asserts on wall-clock).
- Use `time.sleep(N)` for the bridge-state-recording test
  (the one that asserts on `safe_summary == "bridge_timeout"`).
- Always set the `threading.Event` in `finally` to avoid
  leaking the worker thread between tests.

## Output format (canonical)

The final report MUST contain these sections, in this order:

1. **Executive summary** — what was done (or "no change needed")
   and the live state.
2. **Preflight status** — clean tree, commits, env flags in unit
   and in `/proc/$PID/environ`.
3. **env/systemd changes made** — list each edit (or "none").
4. **daemon-reload + restart** — `was_daemon_reload_run` and
   `was_restart_run` flags with reasons.
5. **Post-enable verification** — table with expected vs actual
   for each of the 15+ checks in PHASE 5.
6. **Allowed mini-report categories (post-enable)** — exact
   match to the approval list. Never drop a category; never add
   one.
7. **Stop-condition check** — every stop condition the approval
   named, with pass/fail.
8. **Notes & known issues (read-only observations)** —
   pre-existing issues like V1 `TimeoutError`, watch DB
   creation timing, etc. **Explicitly mark them as
   pre-existing**, not as defects introduced by this task.
9. **Confirmation matrix** — every "do not" constraint from the
   approval, marked pass.
10. **Open approval-required items** — for any follow-on
    decisions (e.g. first real Ads action, V1 TimeoutError
    cleanup, AGI_TEAM_CHAT_ID override).

## Canonical commit message + body

Runtime enablement is **not** a code change. There is no commit.
If a follow-up patch is needed (e.g. to fix a defect uncovered
during verification), use:

```
fix(telegram-ads): <one-line summary>

<3-5 line body explaining the root cause and the fix>.

AR-ADS-WATCHER-RUNTIME-ENABLE-N follow-up. Code + tests + local
commit only. No push, no gateway restart, no env change.

Co-authored-by: telegram-ads contributors
```

If the runtime enablement **uncovered no defect** — no commit.

### Pitfall 13 — Canonical operator alerts are a distinct queue-and-report path

The in-process V1/V2 watcher runtime and the canonical
`telegram_ads_register_campaign_watch` operator are not interchangeable.
For an operator low-budget alert, verify the chain
`EventStore → GrowthOperatorConsumer → durable report sink → MiniReportRouter`.
A gateway may be healthy while this chain is blocked by an old claimed
lifecycle event, stale-zero metrics, a CPA threshold accidentally used as a
budget threshold, or one of the two report flags being disabled. Never repair
live event state or restart merely to test the path: develop and test the
queue/freshness/threshold behaviour first, then obtain a separate deployment
and outbound-notification approval. See
`references/operator-budget-alert-recovery.md`.

## Linked references

- `references/operator-budget-alert-recovery.md` — canonical operator
  diagnosis, queue/freshness/threshold invariants, report gates, and the
  deployment boundary for missed low-budget alerts.
- `references/preflight-checks.md` — exact shell commands for
  every preflight and post-verification check, including the
  secrets-scan regex.
- `references/v1-known-issues.md` — pre-existing V1 issues
  (TimeoutError, watch-DB creation, async-in-sync). Future V-N
  drops that touch V1 should consult this first.
- `references/allowed-categories.md` — the 12 mini-report
  categories exact-match list, with the diff strategy for
  catching drift.
- `references/approval-templates.md` — copy-paste templates
  for the RUNTIME-ENABLE-N approval body, the WORKTREE-RECONCILE
  reconciliation report, and the post-restart verification
  table.

## Related skills

- `telegram-ads-watcher-event-loop-design` — V2.0–V2.7 design.
  Use this skill AFTER a V2.x drop is accepted and you need to
  flip the runtime flags.
- `install-hermes-telegram-ads-watcher` — install the package
  and wire it. Distinct from "enable the runtime" — installation
  happens once per host; runtime enablement happens once per
  V2.x acceptance.
- `operator-approval-gate-enforcement` — "one approval per
  stage" discipline. RUNTIME-ENABLE-N is a stage. It is NOT
  combined with a code commit, a push, or a real Ads action.
- `hermes-system-readiness-audit` — read-only system audit
  across multiple subsystems. RUNTIME-ENABLE-N is a single
  subsystem (Telegram Ads watcher). Use the audit skill when
  the operator wants a full pre-project health check.
- `incremental-commit-preflight` — the commit step (git status,
  secret scan, pytest, ruff, selective staging). Runtime
  enablement usually has NO commit; if it does, follow the
  preflight.
- `handle-telegram-ads-review-and-declines` — when an ad is
  rejected and a human must decide. Unrelated to runtime
  enablement, but shares the "no Ads mutation" discipline.
- `format-telegram-ads-report` — the mini-report formatter.
  Runtime enablement verifies the formatter's category set; the
  formatter's content design is here.
