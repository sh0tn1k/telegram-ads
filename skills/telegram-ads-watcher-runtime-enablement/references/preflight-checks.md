# Preflight + post-verification shell commands

Exact commands for AR-ADS-WATCHER-RUNTIME-ENABLE-N. Copy-paste
safe; secrets-aware (never prints `AGI_TEAM_BOT_TOKEN` value,
cookies, OTPs).

## 1. Git state

```bash
# Working tree must be empty before runtime enablement.
git status --short

# Expected commits visible (V2.0 → V2.7 chain).
git log --oneline -8
# Should show (in this order, newest first):
#   <hash>  feat(telegram-ads): integrate watcher event loop with runtime  ← V2.7
#   <hash>  feat(telegram-ads): wire V2.5 to V1 watcher store (V2.6)
#   <hash>  feat(telegram-ads): wire watcher events to post-action reports
#   <hash>  feat(telegram-ads): add watcher event loop and post-action watch specs  ← V2.0
#   ...older fixes

# Sanity: HEAD points to V2.7.
git log -1 --format='%H %s'
```

## 2. systemd unit inspection (read-only, no secret print)

```bash
UNIT=hermes-gateway-default.service

# Status (active/inactive, PID, uptime, recent log tail).
systemctl --user status $UNIT --no-pager | head -25

# Environment block + EnvironmentFile + ExecStart (no value print).
systemctl --user show $UNIT \
  -p Environment -p EnvironmentFiles -p ExecStart --no-pager

# Per-flag presence (one-liner per flag).
for f in HERMES_ADS_WATCHER_ENABLED \
         HERMES_ADS_WATCHER_INTERVAL_SECONDS \
         HERMES_ADS_WATCHER_REPORTS_ENABLED; do
  systemctl --user show $UNIT -p Environment --no-pager \
    | grep -q "$f=" && echo "$f: present" || echo "$f: MISSING"
done

# Grep the unit file directly (in case systemd caches stale view).
grep -E 'HERMES_ADS_WATCHER' \
  /home/hermes/.config/systemd/user/$UNIT
```

## 3. Process env (running PID)

```bash
PID=$(systemctl --user show -p MainPID --value $UNIT)

if [ -z "$PID" ] || [ "$PID" = "0" ]; then
  echo "service not running"
  exit 1
fi

# CRITICAL: do NOT print full env. Grep + redact.
for f in HERMES_ADS_WATCHER_ENABLED \
         HERMES_ADS_WATCHER_INTERVAL_SECONDS \
         HERMES_ADS_WATCHER_REPORTS_ENABLED \
         AGI_TEAM_CHAT_ID \
         AGI_TEAM_BOT_TOKEN \
         HERMES_HOME; do
  val=$(tr '\0' '\n' < /proc/$PID/environ 2>/dev/null | grep "^$f=" | head -1)
  if [ -z "$val" ]; then
    echo "  $f=<unset>"
  else
    # Mask AGI_TEAM_BOT_TOKEN and any secret-shaped value.
    if echo "$f" | grep -qiE 'token|cookie|password|secret|otp'; then
      echo "  $f=$(echo "$val" | head -c 12)...<redacted>"
    else
      echo "  $val"
    fi
  fi
done
```

## 4. Profile ownership ground-truth

```bash
# SingletonLock symlink → owning PID.
ls -la /home/hermes/.hermes/data/telegram_ads/browser_profile/SingletonLock
# Output: SingletonLock -> host-<PID>
# The PID must be a descendant of the gateway's MainPID.

# Confirm descent: walk up the process tree.
OWNER_PID=$(readlink /home/hermes/.hermes/data/telegram_ads/browser_profile/SingletonLock \
            | sed 's/^host-//')
GATEWAY_PID=$PID
ancestor=$OWNER_PID
while [ -n "$ancestor" ] && [ "$ancestor" != "0" ]; do
  if [ "$ancestor" = "$GATEWAY_PID" ]; then
    echo "OK: SingletonLock owner $OWNER_PID is a descendant of gateway $GATEWAY_PID"
    break
  fi
  ancestor=$(awk '{print $4}' /proc/$ancestor/stat 2>/dev/null)
done

# All Chromium PIDs.
ps -ef | grep 'chrome.*telegram_ads' | grep -v grep | awk '{print $2, $3}' | head -20
```

## 5. Standalone watcher process check

```bash
# Should be empty — V1 in-process loop is a thread, not a process.
ps -ef | grep -E 'ads_watcher_daemon|hermes_telegram_ads_watcher' | grep -v grep
ps -ef | grep -iE 'watcher.*daemon|daemon.*watcher' | grep -v grep
```

## 6. V1 in-process daemon state (from logs)

```bash
LOG=/home/hermes/.hermes/logs/gateway.log

# Daemon thread started (once per gateway start).
grep '\[ADS-WATCH\] daemon thread started' $LOG | tail -1

# Last baseline tick.
grep '\[ADS-WATCH\] baseline tick' $LOG | tail -1
# Expected: state=logged_in_or_no_change events=0 error=None duration=<small>

# Last 10 ticks (look for recurring TimeoutError pre-existing).
grep '\[ADS-WATCH\] tick' $LOG | tail -10

# Any V2.7 bridge result (only present if V2 polling ran).
grep '\[ADS-WATCH-V2.6\]\|v2_bridge\|run_post_action_polling_tick' $LOG | tail -5
```

## 7. Mini-report route configuration (read-only)

```bash
cd /home/hermes/.hermes/hermes-agent
source venv/bin/activate

python <<'PY'
import os
# Inherit env to mirror what the running gateway sees.
pid = int(open('/tmp/.pidfile', 'w').write(str(__import__('os').getpid())))  # noop
from gateway.ads_watcher_v2.report_router import load_router_config
cfg = load_router_config()
print('enabled:', cfg.enabled)
print('chat_id:', cfg.chat_id)
print('categories_count:', len(cfg.allowed_categories))

# Exact-match against the 12 approved categories.
APPROVED = {
    'login_required', 'session_lost', 'session_restored',
    'ad_approved', 'ad_rejected', 'ad_delivering', 'ad_not_delivering',
    'spend_threshold_reached', 'budget_near_limit', 'cpm_threshold_exceeded',
    'post_action_verification_failed', 'watch_error',
}
missing = APPROVED - cfg.allowed_categories
extra = cfg.allowed_categories - APPROVED
print('missing_from_router:', missing or 'none')
print('extra_in_router:', extra or 'none')
print('exact_match:', not missing and not extra)
PY
```

## 8. Next-tick arithmetic

```bash
python <<'PY'
import time
# Find the last baseline tick timestamp in gateway.log.
import re, subprocess
log = subprocess.check_output(
    ['grep', '-E', r'\[ADS-WATCH\] baseline tick', '/home/hermes/.hermes/logs/gateway.log'],
    text=True,
)
last = log.strip().splitlines()[-1] if log.strip() else ''
m = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', last)
if m:
    baseline = time.mktime(time.strptime(m.group(1), '%Y-%m-%d %H:%M:%S'))
    interval = 600
    now = time.time()
    next_tick = baseline + interval
    print(f'baseline: {m.group(1)} UTC')
    print(f'now:      {time.strftime("%H:%M:%S", time.gmtime(now))} UTC')
    print(f'next:     {time.strftime("%H:%M:%S", time.gmtime(next_tick))} UTC (~ in {next_tick - now:.0f}s)')
else:
    print('no baseline tick found in log')
PY
```

## 9. Secrets scan (last 512KB of each log)

```bash
python <<'PY'
import re

SECRET_PATTERNS = [
    rb'AGI_TEAM_BOT_TOKEN=[A-Za-z0-9_\-]{10,}',
    rb'password\s*=\s*\S{4,}',
    rb'otp\s*=\s*\d{4,}',
    rb'cookie\s*=\s*[A-Za-z0-9]{8,}',
    rb'tma_token\s*=\s*\S{8,}',
    rb'csrf\s*=\s*[A-Za-z0-9]{8,}',
]

LOGS = [
    '/home/hermes/.hermes/logs/gateway.log',
    '/home/hermes/.hermes/logs/errors.log',
    '/home/hermes/.hermes/logs/agent.log',
    '/home/hermes/.hermes/logs/gateway-exit-diag.log',
]

findings = 0
for p in LOGS:
    try:
        with open(p, 'rb') as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 524288))
            data = f.read()
        for pat in SECRET_PATTERNS:
            matches = re.findall(pat, data)
            real = [m for m in matches
                    if b'<redacted>' not in m and b'***' not in m]
            if real:
                print(f'  {p}: pattern {pat} -> {len(real)} match(es)')
                findings += 1
    except FileNotFoundError:
        pass

print('NO_SECRET_LEAK_DETECTED' if findings == 0
      else f'!! {findings} secret-pattern hit(s)')
PY
```

## 10. Stop-condition gate (single command, exit 0/1)

```bash
cd /home/hermes/.hermes/hermes-agent
bash <<'BASH'
set -e
fail=0

# 1. clean tree
[ -z "$(git status --short)" ] || { echo "FAIL: dirty tree"; fail=1; }

# 2. V2.7 commit present
git log --oneline | grep -q 'integrate watcher event loop with runtime' \
  || { echo "FAIL: V2.7 commit missing"; fail=1; }

# 3. gateway active
systemctl --user is-active --quiet hermes-gateway-default.service \
  || { echo "FAIL: gateway not active"; fail=1; }

# 4. env flags in unit
for f in HERMES_ADS_WATCHER_ENABLED HERMES_ADS_WATCHER_INTERVAL_SECONDS HERMES_ADS_WATCHER_REPORTS_ENABLED; do
  systemctl --user show hermes-gateway-default.service -p Environment --no-pager \
    | grep -q "$f=" || { echo "FAIL: $f missing from unit"; fail=1; }
done

# 5. AGI_TEAM_BOT_TOKEN present in process env
PID=$(systemctl --user show -p MainPID --value hermes-gateway-default.service)
tr '\0' '\n' < /proc/$PID/environ 2>/dev/null | grep -q '^AGI_TEAM_BOT_TOKEN=' \
  || { echo "FAIL: AGI_TEAM_BOT_TOKEN not in process env"; fail=1; }

# 6. SingletonLock owner is descendant of gateway PID
OWNER=$(readlink /home/hermes/.hermes/data/telegram_ads/browser_profile/SingletonLock \
        | sed 's/^host-//')
ancestor=$OWNER
while [ -n "$ancestor" ] && [ "$ancestor" != "0" ]; do
  [ "$ancestor" = "$PID" ] && break
  ancestor=$(awk '{print $4}' /proc/$ancestor/stat 2>/dev/null)
done
[ "$ancestor" = "$PID" ] || { echo "FAIL: SingletonLock owner $OWNER not in gateway tree"; fail=1; }

# 7. V1 baseline tick succeeded
grep -q 'baseline tick state=logged_in_or_no_change events=0 error=None' \
  /home/hermes/.hermes/logs/gateway.log \
  || { echo "FAIL: no successful baseline tick"; fail=1; }

# 8. no standalone watcher
ps -ef | grep -E 'ads_watcher_daemon' | grep -v grep | grep -q . \
  && { echo "FAIL: standalone watcher process found"; fail=1; }

exit $fail
BASH
```

Exit code 0 = all gates pass. Exit code 1 = at least one stop
condition fired; do not proceed; report which.
