# Approval templates for runtime enablement

Copy-paste templates for the three most common runtime-related
approvals the operator issues:

1. **AR-ADS-WATCHER-RUNTIME-ENABLE-N** — flip the env flags
   and verify.
2. **AR-ADS-WATCHER-WORKTREE-RECONCILE-N** — handle out-of-scope
   files from a previous session.
3. **AR-ADS-WATCHER-DEFECT-FIX-N** — patch a defect found
   during runtime enablement.

Each template is the **shape** the operator usually uses, not the
literal text. Adapt the per-task specifics.

## Template 1: AR-ADS-WATCHER-RUNTIME-ENABLE-N

```
the agent, proceed with AR-ADS-WATCHER-RUNTIME-ENABLE-<N>. [не делай рестарт гетвея | restart allowed | restart only if env changed]

Project:
hermes-system

Goal:
Enable the already-committed in-process Telegram Ads watcher runtime
and bounded watcher mini-reports.

Current accepted state:
V1 in-process watcher committed.
V2 event model committed.
V2.5 report helpers committed.
V2.6 store bridge committed.
V2.7 production wiring committed.
Working tree clean.
Tests green: <N> passed.
No push needed now.

Approved actions:
Preflight read-only checks:
- git status --short
- git log --oneline -8
- systemctl --user status hermes-gateway-default.service
- inspect current env/unit keys only:
  - HERMES_ADS_WATCHER_ENABLED
  - HERMES_ADS_WATCHER_INTERVAL_SECONDS
  - HERMES_ADS_WATCHER_REPORTS_ENABLED
- do not print secrets.

Enable runtime flags for hermes-gateway-default.service:
- HERMES_ADS_WATCHER_ENABLED=1
- HERMES_ADS_WATCHER_INTERVAL_SECONDS=600
- HERMES_ADS_WATCHER_REPORTS_ENABLED=1

Apply only the minimal systemd/env change needed for the default
gateway.

If unit/env changed:
- run systemctl --user daemon-reload;
- restart only hermes-gateway-default.service.

Post-restart verification:
- default gateway active/running;
- new PID;
- watcher enabled;
- reports enabled;
- V1 login/session watcher active;
- V2/V2.7 post-action watcher bridge loaded;
- last login/session tick completed or scheduled;
- no standalone watcher process;
- no second Chromium owner;
- no profile collision;
- no secrets in logs.

Verify mini-report route configuration without sending a
synthetic/test Telegram message. If a test message is required to
verify sending, stop and ask for separate approval.

Allowed mini-report categories after enablement:
<copy the 12 categories from references/allowed-categories.md>

These are watcher status reports only.

Not approved:
- do not push;
- do not run real Telegram Ads tools;
- do not launch/stop/edit ads;
- do not change CPM/bid/budget;
- do not create real ads;
- do not resubmit rejected ads;
- do not perform payments/refunds;
- do not run login assist;
- do not request/read/print/store OTP/2FA/cookies/session tokens;
- do not start standalone watcher daemon;
- do not create watcher systemd service/timer;
- do not restart hermes-gateway-deepseek.service;
- do not restart hermes-xvfb.service;
- do not touch KC timers/services;
- do not send synthetic/test Telegram reports without separate
  approval.

Stop conditions:
- working tree is not clean;
- expected commits are missing;
- env/systemd change would affect deepseek/Xvfb/KC;
- safe AGI Team Bot report path is not configured;
- enabling reports would expose secrets;
- gateway restart fails;
- profile lock occurs;
- watcher crashes gateway;
- any secret appears in logs/output.

Expected final output:
- preflight status;
- env/systemd changes made;
- whether daemon-reload was run;
- restart result;
- new default gateway PID;
- watcher enabled state;
- reports enabled state;
- last watcher tick / scheduled next tick;
- confirmation:
  - no push;
  - no real Ads action;
  - no Ads mutation;
  - no synthetic Telegram message;
  - no secrets printed;
  - no standalone daemon;
  - no deepseek/Xvfb/KC changes.
```

### What to vary

- **N**: bump for each new enablement round (e.g. N=1 for the
  first, N=2 if env drifted and needs re-apply).
- **Restart clause**: the operator sometimes says "не делай рестарт
  гетвея" (no restart), sometimes "restart allowed" (you can
  restart if the unit changed), sometimes "restart only if env
  changed" (conditional). The template defaults to
  "conditional" — the safest middle ground.
- **Allowed categories**: copy the 12 from
  `references/allowed-categories.md`. **Do not paraphrase.**
  Drift in the category list is a P0 blocker.
- **Approved actions**: this is the "shape" the operator uses. He
  may add or remove bullets based on the specific V-N drop.
  Always include the "do not print secrets" line.

## Template 2: AR-ADS-WATCHER-WORKTREE-RECONCILE-N

```
the agent, AR-ADS-WATCHER-WORKTREE-RECONCILE-<N> accepted. V2.<N>
production wiring is complete in a previous session, but the
working tree has out-of-scope modified/untracked files from that
session.

Modified files:
<list each M file with size and one-line description>

Untracked files:
<list each ?? file with size and one-line description>

Approve AR-ADS-WATCHER-WORKTREE-RECONCILE-1. Goal: classify each
file A/B/C/D, integrate Class A as V2.<N+1>, leave Class B/D
uncommitted, do NOT commit Class C.

Constraints:
- no push;
- no enable runtime;
- no restart gateway;
- no env/systemd change;
- no real Telegram Ads tools.

Workflow:
PHASE 1: git status + git diff --stat + read each file
PHASE 2: classify A/B/C/D per file (with 9-check matrix)
PHASE 3: decision (integrate / leave / unsafe / dead)
PHASE 4: per-file constraint audit
PHASE 5: write tests covering <N> approval bullets
PHASE 6: local commit only if safe
PHASE 7: final report with classification table

Stop conditions:
- any file is Class C (unsafe);
- tests fail;
- env change required to make any file work;
- file is dead but deleting it would affect working code.

Expected final output:
- classification table (file, class, size, role, references,
  read-only?, mutates?, sends Telegram?, gated?, secrets?,
  daemon?, tests?);
- integration outcome (files in commit, working tree clean);
- tests run (count, all green);
- confirmation matrix (no push, no runtime, no env, no Ads,
  no Telegram, no daemon, no secrets).
```

### What to vary

- **N**: bump for each reconciliation round.
- **Modified files** / **Untracked files**: paste the actual
  `git status --short` output with sizes from `git diff --stat`
  and `wc -l`.
- **Approval bullets**: the per-file 9-check matrix. See
  `telegram-ads-watcher-event-loop-design` Pillar 12 for the
  full list.

## Template 3: AR-ADS-WATCHER-DEFECT-FIX-N

```
the agent, AR-ADS-WATCHER-RUNTIME-ENABLE-<N> surfaced a defect:
<one-line description>.

Defect:
<root cause, observed behavior, expected behavior, severity
P0/P1/P2/P3, evidence: log line / file path / test name>

Approve AR-ADS-WATCHER-DEFECT-FIX-<M>. Goal: code-only fix
+ tests + local commit; no push, no runtime, no restart, no
env/systemd change.

Workflow:
PHASE 1: read affected file(s) and existing test coverage
PHASE 2: write a failing test that pins the defect
PHASE 3: apply the minimal code change
PHASE 4: run full test suite (all V2.x suites) — must be green
PHASE 5: local commit
PHASE 6: final report with diff stats + test result

Stop conditions:
- fix would require env change (defer to runtime enablement);
- fix would require restart (defer to runtime enablement);
- fix affects > 2 files (escalate to V2.<N+1> drop);
- fix changes a public API (escalate to breaking-change
  approval).

Expected final output:
- changed files (with diff stats);
- new tests (with count);
- full test suite result (count, all green);
- confirmation matrix (no push, no runtime, no restart, no
  env, no Ads, no Telegram).
```

### What to vary

- **N** (the runtime enablement that surfaced the defect) /
  **M** (the defect-fix approval number).
- **Defect**: be specific. Include the log line, file path
  + line, or test name that demonstrates the issue.
- **Severity**: P0 = blocks runtime enablement; P1 = reduces
  confidence; P2 = cosmetic; P3 = future improvement.
- **Stop conditions**: keep all four. They define the boundary
  of "defect fix" vs "new feature".

## Common pitfalls when adapting these templates

- **"Do not run real Telegram Ads tools"** — this means no
  `telegram_ads_create_ad`, no `telegram_ads_start_ad`, etc.
  The watcher itself uses `get_ad` / `get_account_budget` /
  `api_request` (GET) — those are read-only and OK.
- **"Do not send synthetic/test Telegram reports"** — the
  mini-report router is configured but should not actually
  fire during enablement. Read-only verification of the
  router's `RouterConfig` is OK.
- **"Do not restart hermes-gateway-deepseek.service"** — the
  DeepSeek profile is a separate gateway instance with its own
  watcher state. Runtime enablement is for the **default**
  profile only.
- **"Do not touch KC timers/services"** — Knowledge Compiler
  has its own scheduler and watchdog. Runtime enablement is
  unrelated to KC. Even if KC has known issues, fixing them
  is a separate approval.
- **"AGI_TEAM_BOT_TOKEN"** — never print the value. Use
  `grep AGI_TEAM_BOT_TOKEN` to confirm presence; mask in any
  output (e.g. `AGI_TEAM_BOT_TOKEN=***`).

## Final output template (for the report)

```markdown
# AR-ADS-WATCHER-RUNTIME-ENABLE-<N> — Final Report

**Date:** <UTC> · **Project:** hermes-system · **Mode:** enable runtime flags.

## Executive summary
<one paragraph: what was done (or "no change needed") and the live state>

## Preflight status
- working tree: clean / dirty
- commits: <list V2.x chain>
- systemd unit: <path>, env flags: <present/missing>
- process env: <flags visible at /proc/$PID/environ>

## env/systemd changes made
| Change | Required? | Made? |
|---|---|---|
| <each edit> | yes/no | yes/no |
| daemon-reload | conditional | <yes/no, reason> |
| restart | conditional | <yes/no, reason> |

## Post-enable verification
<table with 15+ checks>

## Allowed mini-report categories (post-enable)
<exact match to approval>

## Stop-condition check
<table>

## Notes & known issues (read-only observations)
- <pre-existing V1 issues observed in this session>

## Confirmation matrix
| Item | Status |
|---|---|
| no push | ✅ |
| no real Ads action | ✅ |
| ... | ... |

## Open approval-required items
1. <follow-on decision 1>
2. <follow-on decision 2>
```

This is the canonical output format. Adapt per-task, but
**always include the confirmation matrix and the
pre-existing-issues section** — those are how the operator
distinguishes a clean enablement from a quietly-broken one.
