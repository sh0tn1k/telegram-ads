# `hermes_telegram_ads` package update protocol

How to integrate a new branch from `example/telegram-ads-upstream`
into the running Hermes environment. Use when the operator says "branch X is pushed,
pull it" or "обновлён и запушен".

This is a *recipe* — the architectural "why" lives in
`../SKILL.md` §"Package architecture (verified 2026-06-05, `fix/browser-recovery`)".

## 0. Mental model (read this first)

- `hermes_telegram_ads` is a **separate Python package**, not a `hermes-agent` module.
- The hermes-agent repo has *thin wrappers* (`tools/telegram_ads_tool.py`,
  `tools/telegram_ads_typed_tool.py`) that import from the package.
- The package is **editable-installed** in the shared venv. `pip` does **not**
  download it from PyPI — it's Proprietary.
- Both `default` and `deepseek` profiles share the package and venv. **One**
  install update covers both.
- The local pkg directory is **not a git repo by default** — but as of
  2026-06-06 it IS a git repo in this environment (verified: `git -C
  <pkg> remote -v` returns `origin` pointing at
  `example/telegram-ads-upstream`, branch
  `fix/browser-recovery`). When it is a git repo on the right branch
  with a clean or stashable working tree, use **Strategy C** below
  (FF-merge + `git apply --3way`) — it is much simpler than the
  "convert to a real git checkout" flow in Strategy A. Strategy A is
  still the right path when the pkg dir is a plain checkout.
- A new tool that lives in `hermes_telegram_ads/hermes_tools.py` is registered
  in the `telegram_ads_typed` toolset **automatically** by
  `TelegramAdsToolset.to_hermes_tools()`. `toolsets.py` in hermes-agent does
  *not* need editing for new tools — it just enumerates the current 57 tool
  names statically.
- The `tools/telegram_ads_typed_tool.py` wrapper imports
  `BrowserProfileManager` (deprecated alias). The package keeps
  `TelegramAdsBrowserProfileManager` as canonical but preserves the alias.
  Do not refactor the wrapper file just because the package renamed the
  canonical class.

## 1. Pre-flight (read-only, ~15 s)

```bash
cd /home/hermes/.hermes/hermes-agent

# 1.1. Confirm the branch is fetchable. Use git protocol, not the index.
git ls-remote tg-ads-mgr 'refs/heads/<branch>'

# If empty, the branch isn't on `tg-ads-mgr`. Try the fork search protocol
# from `references/branch-preflight-recipe.md` (steps 1-5).

# 1.2. Show the commits we will integrate.
git fetch tg-ads-mgr 2>&1 | tail -5
git log --oneline tg-ads-mgr/main..tg-ads-mgr/<branch>
git log -1 --format="%H %ci%n%s%n%n%b" tg-ads-mgr/<branch>
```

If `git fetch` fails or returns no new refs, stop — the branch is not on
`tg-ads-mgr`. Ask the operator (A/B/C/D/E menu in the SKILL's "Pre-fetch branch
verification protocol").

## 2. Byte-level diff against the installed source (read-only)

The branch's `hermes_telegram_ads/` is the package source. The installed
copy is at `~/.hermes/profiles/deepseek/plugins/packages/hermes_telegram_ads_pkg/hermes_telegram_ads/`.
Diff them byte-for-byte so we know exactly what will change.

```python
# scripts in this recipe use `git` and `os.walk`. Run via `execute_code` or
# copy into a one-shot Python invocation.
import subprocess, os

PKG = "/home/hermes/.hermes/profiles/deepseek/plugins/packages/hermes_telegram_ads_pkg"
LOCAL = os.path.join(PKG, "hermes_telegram_ads")
REPO = "/home/hermes/.hermes/hermes-agent"
BRANCH = "fix/browser-recovery"   # parameterize

# Files in the branch (excluding __pycache__, .pytest_tmp)
r = subprocess.run(
    ["git", "-C", REPO, "ls-tree", "-r", "--name-only", f"tg-ads-mgr/{BRANCH}"],
    capture_output=True, text=True,
)
branch_files = [l[len("hermes_telegram_ads/"):] for l in r.stdout.splitlines()
                if l.startswith("hermes_telegram_ads/") and "__pycache__" not in l]

# Byte-level compare
mismatches, missing = [], []
for rel in branch_files:
    p = os.path.join(LOCAL, rel)
    if not os.path.exists(p):
        missing.append(rel); continue
    blob = subprocess.run(
        ["git", "-C", REPO, "show", f"tg-ads-mgr/{BRANCH}:hermes_telegram_ads/{rel}"],
        capture_output=True,
    ).stdout
    if blob != open(p, "rb").read():
        mismatches.append((rel, len(blob), os.path.getsize(p)))

# Also: top-level files in the branch that aren't under hermes_telegram_ads/
# (CHANGELOG.md, pyproject.toml, docs/, skills/, tests/, .github/, .gitignore, etc.)
top_level_branch = [l for l in r.stdout.splitlines()
                    if not l.startswith("hermes_telegram_ads/") and "__pycache__" not in l]
# Compare against the pkg dir's top level:
local_top = []
for f in os.listdir(PKG):
    if f in ("__pycache__", ".pytest_tmp", ".pytest_cache", "hermes_telegram_ads.egg-info"):
        continue
    local_top.append(f)
print(f"top-level branch files: {len(top_level_branch)}")
print(f"top-level local files:  {len(local_top)}")
```

Expected output for `fix/browser-recovery` on 2026-06-05:
- 29 source files in `hermes_telegram_ads/`, all exist locally, **16 differ**
- 16 new top-level files (CHANGELOG.md, .github/, docs/UI_CAPABILITY_PARITY.md,
  6 new test files, 3 new fixture files, .gitignore, .gitattributes, etc.)
- ~52 files total in the branch-to-installed delta

If the diff is **larger than expected** (e.g. 100+ files, or new
dependencies in `pyproject.toml`), treat it as a major version bump and
add a new approval gate in step 4.

## 3. Skill merge (read-only, requires writing the installed SKILL.md)

The branch ships `skills/operate-telegram-ads/SKILL.md`. The installed skill
(`~/.hermes/skills/devops/operate-telegram-ads/SKILL.md`) is **larger** —
it has additional operational sections accumulated across sessions. **Never
overwrite.** Use the diff to find new sections to append.

```bash
# Extract the new section heading(s) from the branch's skill:
git -C /home/hermes/.hermes/hermes-agent show tg-ads-mgr/<branch>:skills/operate-telegram-ads/SKILL.md \
  | grep -E "^## "

# Compare to the installed skill:
grep -E "^## " ~/.hermes/skills/devops/operate-telegram-ads/SKILL.md
```

If the branch adds `## X` and the installed skill lacks it, **append** the
section. Don't reorder or remove existing sections. For `fix/browser-recovery`
on 2026-06-05, the only new section is `## Browser recovery policy`.

## 4. Approval gate (NEEDS THE OPERATOR'S EXPLICIT "approved, install")

State the install plan as a structured message with:

1. Current package version + last commit (e.g. `0.1.0` @ local HEAD before pull).
2. Incoming version implied by the branch (CHANGELOG.md, pyproject.toml bump if any).
3. Number of files changed / added (from step 2 diff).
4. **New tool names** if any (from the package's `to_hermes_tools()`).
5. Skill diff (one-line: "append `## Browser recovery policy` to the installed SKILL.md").
6. Restart scope (one profile or both).
7. **Explicit list of mutating actions that will NOT be touched** (every tool
   in the `APPROVAL_REQUIRED` and `FORBIDDEN_OR_DOUBLE_CONFIRM` safety classes).

Do not proceed past this point without "approved, install".

## 5. Apply the update (mutating; requires approval from step 4)

The pkg dir is not a git repo. Pick **one** of the two strategies:

**Strategy A — convert to a real git checkout (preferred; gives you diffs
and history going forward):**

```bash
cd ~/.hermes/profiles/deepseek/plugins/packages/hermes_telegram_ads_pkg
# Move the ~10 untracked top-level trees (README.md, hermes_telegram_ads/,
# tests/, docs/, skills/, pyproject.toml, ...) out of the way — git
# checkout refuses to switch branches when they exist. Verified 2026-06-05:
# a bare `git checkout -B fix/browser-recovery FETCH_HEAD` errors with
# "Please move or remove them before you switch branches. Aborting".
HOLD=/tmp/hermes_pkg_hold_$$
mkdir -p "$HOLD"
for d in README.md config.example.yaml docs examples \
         hermes_telegram_ads.egg-info hermes_telegram_ads \
         pyproject.toml skills tests; do
  [ -e "$d" ] && mv "$d" "$HOLD/"
done
# keep .pytest_cache, .pytest_tmp; .git will be created below
git init -q
git remote add origin https://github.com/example/telegram-ads-upstream.git
git fetch --depth=20 origin fix/browser-recovery
git checkout -B fix/browser-recovery FETCH_HEAD    # now succeeds
git status --short                                  # MUST be empty
```

The `__pycache__/` directories inside the moved trees are intentionally
left behind; git checkout regenerates them. `tests/`, `docs/`, `skills/`
landed inside the pkg dir but they don't affect the venv (the editable
`.pth` only resolves `hermes_telegram_ads/` as a Python package; the
rest sits as data files). Cleanup: `rm -rf "$HOLD"` at the very end of
the install, **after** the smoke test and pytest pass — never before.

After this, the pkg dir IS a git repo on the right branch. Future pulls
are `git pull`. The editable install path doesn't change because the .pth
file points to a subdirectory (`hermes_telegram_ads/`) inside the pkg dir,
and `git checkout` updates files in place.

**Strategy B — temp worktree + rsync (use if the pkg dir is on a different
filesystem or you don't want a `.git/` inside it):**

```bash
WORKTREE=/tmp/hermes-telegram-ads-fetch
git -C /home/hermes/.hermes/hermes-agent worktree add --detach $WORKTREE tg-ads-mgr/<branch>
rsync -a --delete \
  --exclude='__pycache__' --exclude='.pytest_tmp' --exclude='.pytest_cache' \
  --exclude='*.egg-info' --exclude='.git' \
  $WORKTREE/ ~/.hermes/profiles/deepseek/plugins/packages/hermes_telegram_ads_pkg/
git -C /home/hermes/.hermes/hermes-agent worktree remove --force $WORKTREE
```

**Strategy C — fast-forward merge + 3-way patch re-apply (use when the
pkg dir IS already a git repo on the right branch).** Verified
2026-06-06 integrating `fix/browser-recovery` from `aed0ea8` →
`7636a3c` with 3 incoming commits and 1 local unstaged rename
(`name` → `screenshot_name` in `_h_save_screenshot` + its `ToolSpec`).
This is the simplest flow when the precondition holds:

```bash
cd ~/.hermes/profiles/deepseek/plugins/packages/hermes_telegram_ads_pkg

# 0. Confirm precondition — pkg dir IS a git repo on the right branch.
git remote -v                     # must show origin = the package repo
git branch --show-current         # must match the branch the operator named
git status -sb                    # if dirty, you need step 0.1 below

# 0.1. Stash / save local dirty changes BEFORE fast-forwarding
# (git pull/merge refuses with uncommitted changes that would conflict).
# Save the patch to a backup dir so it survives stash drop.
BACKUP=/home/hermes/.hermes/backups/hermes_telegram_ads_pkg_$(date +%Y%m%d_%H%M%S)
mkdir -p "$BACKUP"
git stash push -m "pre-install-rename" -- hermes_telegram_ads/hermes_tools.py
git diff > "$BACKUP/local_dirty.patch"  # safety net even if stash is dropped
# (If you only ran git diff before the stash, the patch is empty;
# the stash holds the real change. Use $BACKUP/local_dirty.patch
# as a belt-and-suspenders backup of the pre-stash state.)

# 1. Fetch + fast-forward merge. This is clean only when local HEAD
# is an ancestor of origin/<branch> (no local commits).
git fetch --no-tags origin <branch> 2>&1 | tail -3
git merge --ff-only origin/<branch> 2>&1 | tail -10

# If FF is rejected because local has unpushed commits, fall back to
# Strategy A or B; do NOT use `git pull` (it does a non-FF merge
# and creates a merge commit that didn't exist in upstream history).

# 2. Re-apply the local patch on top of the incoming code.
# `git apply --3way` resolves rename-vs-content conflicts that plain
# `git apply` would reject. In the 2026-06-06 case: incoming kept
# `name=` while local wanted `screenshot_name=`, and `--3way` cleanly
# applied the local rename.
git apply --3way "$BACKUP/local_dirty.patch"
# Verify:
git status -sb                     # branch clean, only the original dirty file
git diff --stat                    # should match the pre-install diff

# 3. Refresh the editable install. PREFER `python -m pip` over
# the `pip3` binary — `python -m pip` is the version that ships
# with the venv's own python and avoids path issues. Verified
# 2026-06-06: `venv/bin/pip` and `venv/bin/pip3` were both
# unavailable; `python -m pip install -e . --no-deps` worked.
cd ~/.hermes/profiles/deepseek/plugins/packages/hermes_telegram_ads_pkg
/home/hermes/.hermes/hermes-agent/venv/bin/python -m pip install -e . --no-deps
# Drop the stash only AFTER the smoke test passes (step 6):
git stash drop
```

The two key advantages over Strategy A:
- **No `/tmp` hold dir dance** — no need to move untracked trees
  out of the way, no risk of losing the hold dir before cleanup.
- **Local dirty changes survive the merge** — the `git apply --3way`
  pattern is robust to the common case where local has a rename
  or small refactor that the incoming branch did not include.

Pitfall: if `git apply --3way` reports a *real* conflict (not a
clean three-way resolution), abort the install, restore the dirty
patch, and surface the conflict to the operator. `--3way` is permissive
about content-vs-rename collisions, but it will not invent
semantics if both sides edited the same lines differently.

Then re-point the editable install (one-liner, refreshes dist-info mtimes
without rebuilding anything because the package is in dev mode):

```bash
cd /home/hermes/.hermes/hermes-agent
venv/bin/pip install -e ~/.hermes/profiles/deepseek/plugins/packages/hermes_telegram_ads_pkg \
  --no-deps --quiet
```

`--no-deps` because all deps (`playwright>=1.49`, `pydantic>=2.6`,
`pyyaml>=6.0`, `httpx>=0.27`) are already pinned in the venv from the
original install.

## 6. Smoke test the package import (read-only)

```bash
cd /home/hermes/.hermes/hermes-agent
venv/bin/python -c "
import hermes_telegram_ads
print('version path:', hermes_telegram_ads.__file__)
from hermes_telegram_ads.hermes_tools import TelegramAdsToolset
print('typed tools in package:', len(TelegramAdsToolset().to_hermes_tools()))
"
```

Expected output: a path under the pkg dir, and a number matching the count
of `telegram_ads_*` names in the package's `hermes_tools.py` registration
list. **Counts seen in this environment (verify against incoming CHANGELOG):**
- 2026-06-05: 58 tools (+1 `telegram_ads_recover_browser_session`)
- 2026-06-06: 62 tools (+4 login workflow:
  `telegram_ads_login_check` · `telegram_ads_login_start` ·
  `telegram_ads_login_submit_phone` · `telegram_ads_login_wait`,
  gated by `SENSITIVE_ACCOUNT_ACCESS` safety class with phone
  masking in all outputs)

After the editable reinstall, the new tool names appear in
`TOOLS_BY_NAME` immediately (no restart needed for fresh Python
imports). The live gateway process needs a separate restart for
the LLM's function-calling schema to pick them up — that is a
separate explicit approval per Operating Discipline.

## 7. Run the new test suite (read-only)

```bash
cd ~/.hermes/profiles/deepseek/plugins/packages/hermes_telegram_ads_pkg
/home/hermes/.hermes/hermes-agent/venv/bin/python -m pytest -x -q 2>&1 | tail -30
```

Tests use the `fake_adapter` mock and golden HTML fixtures. They do **not**
talk to a real browser. If pytest fails:

- `ImportError: cannot import name 'X' from hermes_telegram_ads` —
  the package source is out of sync with the test. Re-run step 5.
- `FAILED tests/test_<X>.py` — paste the failure to the operator. Do not
  edit the test to make it pass; the test is the regression guard.
- `playwright._impl._errors.Error: BrowserType.launch` — test tried
  to spawn a real browser. Unusual; the test suite is designed not to.
  If it happens, the test file is misconfigured; surface to the operator.

## 8. Skill merge (mutating; requires approval)

Append, do not replace. For `## Browser recovery policy` (or any new
section) from the branch's skill:

```bash
# Extract the new section from the branch:
git -C /home/hermes/.hermes/hermes-agent show tg-ads-mgr/<branch>:skills/operate-telegram-ads/SKILL.md \
  | awk '/^## Browser recovery policy$/,/^## [^B]/' | head -n -1 \
  > /tmp/new-skill-section.md

# Verify it is a real new section:
head -3 /tmp/new-skill-section.md
wc -l /tmp/new-skill-section.md

# Append to the installed skill, before the final "## " (or at end if none):
echo "" >> ~/.hermes/skills/devops/operate-telegram-ads/SKILL.md
cat /tmp/new-skill-section.md >> ~/.hermes/skills/devops/operate-telegram-ads/SKILL.md
```

This is the only file under `~/.hermes/` that gets written during an
update. The skill loader re-reads it on the next agent turn.

## 9. Restart plan — REQUIRES SEPARATE APPROVAL ("approved, restart")

A "approved, install" mandate does **not** authorize a gateway restart. State
the restart plan separately with exact PIDs (find via `ps -ef | grep
hermes_cli`).

**Canonical path: `systemctl --user restart` on the gateway unit, NOT
`kill -TERM` on the PID.** Verified 2026-06-05: both Hermes gateway
profiles in this environment are managed by systemd user units
(`hermes-gateway-default.service` and
`hermes-gateway-deepseek.service`, both `Type=simple`, `Restart=always`,
`RestartForceExitStatus=75`). The deepseek unit has
`TimeoutStopSec=210` and `KillMode=mixed` (drain-friendly). The
default unit has `TimeoutStopSec=30` (misconfigured; see warning in
its startup log — fix is `hermes gateway service install --replace`).
The unit's `ExecStart` is the same command you'd hand-roll
(`python -m hermes_cli.main --profile <name> gateway run [--replace]`),
so a single `systemctl --user restart` is equivalent to TERM + drain +
respawn, with the bonus that systemd handles `Restart=on-failure` /
`RestartSec=5` / `RestartMaxDelaySec=300` / `RestartSteps=5` correctly.

**Pre-flight (read-only, do not skip):**

```bash
# 0. Process table — confirm current PIDs and which profile is on
#    which code version (a system supervisor may have already
#    restarted the default gateway for you between "approved, install"
#    and "approved, restart"; in that case the TERM step is unnecessary
#    for that profile and may drop in-flight kanban / cron state).
ps -ef | grep -E "hermes_cli.main|playwright|Xvfb" | grep -v grep
systemctl --user status hermes-gateway-default.service --no-pager | head -10
systemctl --user status hermes-gateway-deepseek.service --no-pager | head -10

# 1. Cron + AGI Team task list
hermes cron list
# AGI Team: check for in-flight tasks (use the dedicated agi_team_task_list tool)

# 2. Tail both gateway logs for recent activity and errors
tail -n 30 ~/.hermes/logs/gateway.log                 # default profile
tail -n 30 ~/.hermes/profiles/deepseek/logs/gateway.log   # deepseek profile
# Note: the log path is `gateway.log`, NOT `gateway-default.log` /
# `gateway-deepseek.log` — those names in the plan are wrong.
```

**Drain:** wait for any in-flight `telegram_ads_*` call to finish
(`telegram_ads_status` is idle; `telegram_ads_get_browser_profile_info`
shows `session_active: false` or the Playwright driver is not running
a navigation). The default gateway log shows
`Shutdown phase: notify_active_sessions` and
`Sent shutdown notification to home channel` when it begins draining —
a normal exit, not an error.

**Restart only the profile whose code is stale.** Verified 2026-06-05:
after "approved, install" but before "approved, restart", the default
gateway was already restarted by the system supervisor (PID changed
from Jun 03 to 10:42) and was on the new code, while deepseek was
still on the Jun 03 PID with the old module in memory. Restarting
the default again was unnecessary churn and risked losing the kanban
dispatcher's in-memory session state. The correct move is
`systemctl --user status <unit>` to see `Active: active (running)
since <timestamp>` and `Main PID: <pid>`, and only restart the units
whose PID is older than the install.

```bash
# Restart the unit(s) whose PID predates the install
systemctl --user restart hermes-gateway-deepseek.service
# (or both, if both are stale)
systemctl --user restart hermes-gateway-default.service
```

Each restart takes ~1-2 s for `ExecStart` → Telegram connect → ready.
Verify with `systemctl --user status <unit>` (should show
`Active: active (running)` and a fresh `Main PID`).

**Do not** touch:
- `Xvfb :99` (Telegram Ads browser screen) — restarting it loses the
  persistent profile and the manual Telegram login.
- `playwright/driver/node` (Playwright child process) — owned by the
  gateway, will exit naturally when the gateway exits.
- `ps`/`pkill`/`kill -KILL` on any gateway process — the unit
  supervisor handles lifecycle; manual kills disrupt the
  `Restart=on-failure` flow and may trigger the misconfigured
  `TimeoutStopSec=30` SIGKILL on the default unit before drain
  finishes.
- Any `hermes-gateway-*.service` *config* (unit file) — modifying the
  unit is a separate `hermes gateway service install --replace` task.

## 10. Post-restart verification (read-only)

```bash
# 10.1. New tool is in the LLM schema
hermes tools --toolset telegram_ads_typed --list | wc -l       # expect 58 (was 57)
hermes tools --toolset telegram_ads_typed --list | grep recover_browser_session

# 10.2. Package state
telegram_ads_status                                              # alive, no errors
telegram_ads_get_browser_profile_info                            # profile intact
telegram_ads_list_accounts                                       # read-only
telegram_ads_recover_browser_session --dry-run                   # the new tool works

# 10.3. No regression in the legacy dispatcher
telegram_ads action=status                                       # legacy path

# 10.4. Skill loaded
grep -c "^## " ~/.hermes/skills/devops/operate-telegram-ads/SKILL.md

# 10.5. Both gateway logs show "Gateway running with 1 platform(s)"
#       and "Connected to Telegram (polling mode)" since the fresh
#       start timestamp. Wrong log filenames (`gateway-default.log`)
#       will give "No such file" — actual paths are `gateway.log` under
#       each profile's `~/.hermes/logs/` dir.
tail -n 5 ~/.hermes/logs/gateway.log
tail -n 5 ~/.hermes/profiles/deepseek/logs/gateway.log
```

If `telegram_ads_recover_browser_session` returns a `not_implemented` /
`forbidden` envelope, the package update did not register the new tool
— re-run step 5 (the editable install may not have refreshed the .pth
file's mtime, or the gateway is still using the old in-memory
`hermes_telegram_ads.hermes_tools` module).

If `telegram_ads_status` returns `login_required` after the restart
(not present before), the Playwright session was lost. **Do not** try to
recover via the new `recover_browser_session` tool blindly — surface to
the operator and check the Xvfb `:99` and persistent profile state manually.

## 11. Things that go wrong (and what to do)

| Symptom | Cause | Fix |
|---|---|---|
| `pip install -e ...` says "no such option --no-deps" | older pip in venv | use `venv/bin/pip install -e ...` and let it resolve deps (slower, but correct) |
| `venv/bin/pip` says "No such file or directory" | the hermes-agent venv only ships `pip3` and `pip3.11` | use `venv/bin/pip3 install -e <pkg> --no-deps` (or `pip3.11`). Do **not** try `python -m ensurepip` — the venv is PEP 668-locked and the editable install doesn't need it. Verified 2026-06-05. |
| After `pip install -e`, package still imports the old code | `.pyc` cache stale | `find <pkg> -name __pycache__ -exec rm -rf {} +` then re-run smoke test |
| Gateway restart fails with "profile in use" | the new gateway process spawned but the old one didn't exit | `ps -ef \| grep hermes_cli`; manually `kill -KILL` the old PID; cold-start again |
| `telegram_ads_*` tools missing from LLM schema after restart | the gateway process started before the editable install pointed at the new files | restart again, after a 1-2s sleep |
| `ImportError: cannot import name 'TelegramAdsBrowserProfileManager'` | someone refactored the alias out | revert; the alias is load-bearing (used by `tools/telegram_ads_typed_tool.py`) |
| Browser profile lock — Playwright says "SingletonLock" | another process is using the profile | `pkill -f playwright/driver/node` is forbidden by Operating Discipline; surface to the operator for explicit debug-fallback approval |
| Test suite green but the new tool returns `not_implemented` | the package's `to_hermes_tools()` doesn't include the new tool's name in its return list | check `hermes_telegram_ads/hermes_tools.py` for the new `register` call; report to the operator |
| `git checkout -B <branch> FETCH_HEAD` errors with "Please move or remove them before you switch branches" | the pkg dir has ~10 untracked top-level trees from a previous install (README.md, hermes_telegram_ads/, tests/, docs/, skills/, pyproject.toml, ...) | move them to `/tmp/hermes_pkg_hold_$$` first, then checkout, then verify `git status` is empty; clean up the hold dir at the end of the install |
| `kill -TERM <pid>` SIGKILLed mid-drain, "Previous gateway exited cleanly" missing from log | `hermes-gateway-default.service` has `TimeoutStopSec=30` (misconfigured for `drain_timeout=180`) | use `systemctl --user restart hermes-gateway-default.service` instead — the unit handles SIGTERM, drain, and respawn correctly. Permanent fix: `hermes gateway service install --replace` to regenerate the unit with `TimeoutStopSec=210` |
| `systemctl --user status <unit>` shows `Main PID: <new-pid> since <install-time>` before I sent restart | the system supervisor restarted the gateway automatically after the package install (e.g. when the editable install rebuilt the wheel and the supervisor noticed) | only `systemctl --user restart` the units whose PID predates the install; for the already-restarted ones, skip the restart and go straight to step 10 verification |

## 12. Provenance

- First used in production: 2026-06-05, integrating `fix/browser-recovery`
  into the live Hermes environment (default + deepseek profiles, shared
  venv, single editable install of `hermes_telegram_ads 0.1.0`).
- Restart path corrected: 2026-06-05 second pass. Originally documented
  `kill -TERM <pid>` + nohup cold start, but the live environment runs
  both gateways under `hermes-gateway-*.service` systemd user units.
  `systemctl --user restart` is the canonical path; `kill -TERM` is
  fragile (misconfigured `TimeoutStopSec=30` on the default unit
  SIGKILLs mid-drain) and bypasses the unit's `Restart=on-failure` /
  `RestartSteps` flow.
- Also: between "approved, install" and "approved, restart", the system
  supervisor can pre-restart the default gateway for you. Always
  `ps` + `systemctl --user status` before sending restart, and skip
  units that are already on the new code.
- Author: the agent (default profile), operator-approved step-by-step.
- Source for the architectural facts: live inspection of
  `__editable__.hermes_telegram_ads-0.1.0.pth` mapping, byte-diff
  against `tg-ads-mgr/fix/browser-recovery`, and
  `pyproject.toml` of the package.
