# Post-baseline tick observation (NEXT-TICK-VERIFY-N)

Added 2026-06-17 after V2.8 regression discovery. The V2.8
partial fix bounded the baseline tick but did NOT bound the
post-baseline tick (V1's `wiring.scheduler.tick()` still
hits 60s `TimeoutError`). Therefore a baseline-green check
is NOT sufficient to declare "ready for first Ads action".
A separate NEXT-TICK-VERIFY-N approval is mandatory.

## 1. Compute expected first post-baseline tick

```bash
python <<'PY'
import re, subprocess, time

log = subprocess.check_output(
    ['grep', '-E', r'\[ADS-WATCH\] baseline tick',
     '/home/hermes/.hermes/logs/gateway.log'],
    text=True,
)
last = log.strip().splitlines()[-1] if log.strip() else ''
m = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', last)
if not m:
    print('no baseline tick found in log')
    raise SystemExit(1)
baseline = time.mktime(time.strptime(m.group(1), '%Y-%m-%d %H:%M:%S'))
interval = 600  # default HERMES_ADS_WATCHER_INTERVAL_SECONDS
now = time.time()
next_tick = baseline + interval
print(f'baseline:    {m.group(1)} UTC')
print(f'now:         {time.strftime("%H:%M:%S", time.gmtime(now))} UTC')
print(f'expected:    {time.strftime("%H:%M:%S", time.gmtime(next_tick))} UTC')
print(f'in:          {next_tick - now:.0f}s')
PY
```

## 2. Wait for the post-baseline tick (read-only, no spinning)

The skill **must not** loop with `sleep 1` until the tick
appears. Use a bounded wait (e.g. 120s) and report the
current state. Two outcomes:

- **Tick observed** (the `[ADS-WATCH] tick` line appeared in
  the log). Proceed to §3.
- **Tick not observed within the wait window** (e.g.
  interval=600s but only 90s have elapsed). Report the
  computed next-tick timestamp and exit; do not classify
  the verification as "failed" — it is "pending".

## 3. Classify the post-baseline tick

```bash
LOG=/home/hermes/.hermes/logs/gateway.log
# Find the FIRST post-baseline tick (line after the most
# recent baseline tick line).
awk '
  /\[ADS-WATCH\] baseline tick/ { baseline_line = NR }
  /\[ADS-WATCH\] tick/ && NR > baseline_line { print; exit }
' $LOG
```

Then classify:

| Pattern | Verdict |
|---|---|
| `state=... events=0 error=None duration=<small>` (≤30s) | ✅ V2.8 verified in production; ready for first Ads action (separate approval) |
| `state=... events=... error=TimeoutError: duration=60.0x` | ❌ V2.8 partial fix; do NOT approve first Ads action; recommend V2.9 |
| `state=None events=0 error=TimeoutError: duration=60.0x` | ❌ same as above |
| `error=acquire_adapter_failed:TimeoutError` | ⚠ V1 read-only adapter unavailable; may self-recover on next tick |
| any other `error=...` | ⚠ unknown; stop and report; do not approve first Ads action |

## 4. Stop-conditions for NEXT-TICK-VERIFY-N

| Stop condition | Action |
|---|---|
| working tree is not clean | refuse; recommend separate cleanup |
| expected V2.8 commit (`660816f56`) is missing | refuse; V2.8 not deployed |
| default gateway restart failed | refuse; no live process to verify |
| post-baseline tick has `error=TimeoutError: duration=60.0x` | refuse; recommend V2.9 / V1.1 |
| profile lock occurs | refuse; recommend gateway restart with separate approval |
| secrets appear in logs | refuse; mask before re-running |
| post-baseline tick error is "safe non-fatal" (e.g. acquire_adapter_failed) | note; re-observe next interval |

## 5. Report shape

The NEXT-TICK-VERIFY-N report MUST contain:

1. **Old PID** (recorded before verification) and **new PID**
   (still alive after verification).
2. **Baseline timestamp** and **expected post-baseline
   timestamp** (computed).
3. **Observed post-baseline timestamp** and full log line
   (state, events, error, duration).
4. **Classification** (✅ verified / ❌ regression / ⚠
   unknown).
5. **Confirmation matrix**: no push, no real Ads, no Ads
   mutation, no synthetic Telegram, no secrets printed, no
   standalone daemon, no deepseek/Xvfb/KC changes.
6. **Final readiness verdict** for first real Ads action.

## 6. Quick reference for the "what is the post-baseline
   tick and why does it matter" question

The V1 daemon thread emits one `[ADS-WATCH]` log line per
tick. The **baseline** is a one-shot call from
`_ensure_baseline_login_state_watch` after daemon start.
The **post-baseline** tick is the recurring
`wiring.scheduler.tick()` call every `interval_seconds`.

- Baseline uses the V1 login_state watch directly. No
  Playwright adapter race. Always succeeds.
- Post-baseline uses V1's polling path, which exercises
  the shared singleton Playwright adapter. May hit 60s
  TimeoutError on the V1 outer `wait_for` due to a race
  with the V2.6 bridge's adapter acquisition.

**A baseline-green check is not a green light for the
post-baseline behavior.** That is the lesson of V2.8.
