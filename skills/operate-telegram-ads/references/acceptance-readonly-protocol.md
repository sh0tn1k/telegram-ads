# Acceptance read-only protocol — sandbox vs. live gateway

Verified 2026-06-05. When the operator asks for a "server-side Telegram Ads
acceptance read-only" pass (typically a numbered list of
`telegram_ads_status` / `ensure_login` / `snapshot_accounts` /
`list_ads` / `get_ad_stats` / `validate_ad` calls plus mutating-gate
negative tests), there is a hard architectural constraint that the
next session will rediscover without this file.

## The constraint

The agent sandbox **cannot run live `telegram_ads_*` tools directly.**

Every typed tool dispatches through `TelegramAdsAdapter.launch(config)`,
which opens a Playwright Chromium on the persistent browser profile
(`~/.hermes/.../browser_profiles/telegram_ads/`). The persistent
profile is **already held by the live gateway** process (default
`3189541` / deepseek `3189888` in a 2-gateway setup; PIDs change on
restart). A second adapter from the sandbox hits
`BrowserProfileLockedError` because the live gateway's Chromium holds
the `SingletonLock` in the profile dir.

Per Operating Discipline rule 5, `browser_profile_locked` /
`browser_profile_busy` are **terminal**: no retry, no workaround, no
second browser session. Return a structured error to the operator and stop.

**Sandbox tools available:** `terminal`, `execute_code`, `read_file`,
`search_files`, `patch`, `send_message`, `write_file`, `telegram_*` (no
`ads_` prefix), `cronjob`, `delegate_task`, `agi_team_task_*`,
`browser_*` (separate browser stack, not the ads one), etc.

**Sandbox tools NOT available:** `telegram_ads_*` (they're registered
for the live LLM session, not the sandbox).

## Three modes for an acceptance pass

| Mode | What it does | Sandbox path | Live browser | Mutating-tool gate test |
|---|---|---|---|---|
| **A — Live through gateway** | Real `telegram_ads_*` calls via LLM function-calling | yes (message-driven) | yes | yes (real `approval_required` envelope) |
| **B — In-sandbox with FakeAdapter** | `TelegramAdsToolset(_adapter_factory=...)` wired to `tests/fake_adapter.py` | yes | no | yes (real `approval_required` envelope, no network) |
| **C — Pure introspection** | `from hermes_telegram_ads.hermes_tools import TELEGRAM_ADS_TOOLS`, `inspect.getsource(...)` | yes | no | **no** (gate lives inside handlers, not registry) |

### Mode A — Live through gateway

The agent that receives the acceptance pass as a chat message runs
inside a live gateway and has the typed `telegram_ads_*` tools in its
function-calling schema. The agent dispatches them through the running
gateway process, which has the singleton adapter already open. Login
required, browser state, profile lock — all are managed by the live
adapter, no second instance is created.

**When to pick:** when the request includes live read-only probes
(1–9 in the standard 10-item acceptance) and the agent has the
typed tools in schema.

**Flow:**
1. `telegram_ads_status` — initial state probe.
2. `telegram_ads_ensure_login` — if `login_required`, halt and surface
   `data.instructions` (see `login-policy.md`).
3. `telegram_ads_get_browser_profile_info` — diagnostics.
4. `telegram_ads_snapshot_accounts` — full read-only snapshot.
5. `telegram_ads_list_ads` → choose `ad_id` for items 6–8.
6. `telegram_ads_get_ad_stats` — views/spend/CPM.
7. `telegram_ads_get_rejection_info` — decline reason for chosen ad.
8. `telegram_ads_get_ad_targeting` — locked target queries.
9. `telegram_ads_validate_ad` — for a draft to recreate.
10. Mutating-gate negative tests — 7 calls without `confirmation_id`,
    expect `approval_required` (or `double_confirm_required` for
    `delete_ad`).

Stop at first `login_required`, empty `INTERNAL_ERROR`, or
`browser_broken`. Do not chain.

### Mode B — In-sandbox with FakeAdapter

`FakeAdapter` is the test stand-in from
`tests/fake_adapter.py` in the package source. It implements the same
method surface as `TelegramAdsAdapter` (45 methods, all the
`issue_*_confirmation` paths, `add_to_budget`, `change_cpm`,
`create_ad`, etc.) without touching the browser, without network,
without persistent state. The toolset can be wired to it via
`_adapter_factory` so the gate logic runs end-to-end.

**Setup code:**
```python
import sys
sys.path.insert(0, "/home/hermes/.hermes/profiles/deepseek/plugins/packages/hermes_telegram_ads_pkg")
sys.path.insert(0, "/home/hermes/.hermes/profiles/deepseek/plugins/packages/hermes_telegram_ads_pkg/tests")

import hermes_telegram_ads  # noqa
from hermes_telegram_ads.hermes_tools import TelegramAdsToolset
import fake_adapter as fa

# Wire with a fake adapter that returns canned data
cfg = fa.make_config()
adapter = fa.FakeAdapter(cfg)
ts = TelegramAdsToolset(_adapter_factory=lambda: _noop_async(adapter))
```

**When to pick:** when the agent sandbox does **not** have the typed
tools in schema (no live gateway), but the operator still wants gate
verification. Useful for "did the new code break the gate?" regression
checks.

**Coverage:** registry membership, schema validation, mutating
handler gate logic, `approval_required` envelope shape,
`confirmation_id` issuance, `double_confirm` path for `delete_ad`.
**Not covered:** real network, real browser, real Telegram-side
errors, login state, account-token masking against live tokens.

### Mode C — Pure introspection

The lightest mode. No toolset, no adapter, just Python imports and
`inspect.getsource(...)`. ~200 ms, zero side effects.

**Covers:** tool count, `safety_class` distribution, mutating flags,
`requires_approval` flags, handler source code (confirms gate
implementation matches the spec).

**Does not cover:** the actual gate behavior at runtime (you can read
the source, but you can't prove it executes correctly without
calling it). The gate lives inside the handler's try/except — not in
the registry. So introspection alone is **not** a substitute for Mode
A or Mode B for the gate tests.

**Setup code:**
```python
import sys, importlib
for m in list(sys.modules):
    if m.startswith("hermes_telegram_ads"):
        del sys.modules[m]
sys.path.insert(0, "/home/hermes/.hermes/hermes-agent")

from hermes_telegram_ads.hermes_tools import TELEGRAM_ADS_TOOLS, TOOLS_BY_NAME
print(f"count = {len(TELEGRAM_ADS_TOOLS)}")
# Per-tool safety class distribution
classes = {}
for t in TELEGRAM_ADS_TOOLS:
    sc = t.safety_class.name
    classes[sc] = classes.get(sc, 0) + 1
print(classes)
```

### Which mode first

Default: **Mode C → Mode A → Mode B fallback if A unavailable.**

Mode C confirms registry/schema are intact (catches a broken
`__init__.py` or missing tools). Mode A is the real verification. Mode
B is the fallback when Mode A is impossible (no live gateway, no
schema access).

## Verdict template

Use this exact template in the final acceptance report (also
embedded in SKILL.md "Acceptance pass protocol"):

```text
# acceptance run — <date> <profile>

## per-item
- 1. telegram_ads_status         : PASS|FAIL|PARTIAL — <one-line reason>
- 2. telegram_ads_ensure_login   : PASS|FAIL — <login_required / logged_in>
- 3. telegram_ads_get_browser_profile_info : PASS|FAIL
- 4. telegram_ads_snapshot_accounts        : PASS|FAIL|NOT_RUN — <reason>
- 5. telegram_ads_list_ads                 : PASS|FAIL|NOT_RUN
- 6. telegram_ads_get_ad_stats             : PASS|FAIL|NOT_RUN
- 7. telegram_ads_get_rejection_info on <AdName> : <decline reason>
- 8. telegram_ads_get_ad_targeting on <AdName>  : <locked target queries>
- 9. telegram_ads_validate_ad for <draft>        : <policy result>
- 10. approval-gate negative tests :
    - create_ad      : approval_required ✅
    - edit_ad        : approval_required ✅
    - change_cpm     : approval_required ✅
    - add_to_budget  : approval_required ✅
    - start_ad       : approval_required ✅
    - stop_ad        : approval_required ✅
    - delete_ad      : double_confirm_required ✅

## state observed
- browser_state : <string from telegram_ads_status>
- logged_in     : <true|null|false>
- profile_dir   : <path>
- current_url   : <url|null>
- pending_confirmations : <count from get_pending_confirmations>

## artifacts
- snapshot JSON : <path>
- screenshots   : <path per cabinet>
- decline reason: <text from get_rejection_info>
- locked queries: <list from get_ad_targeting>
- validation    : <pass/fail with violations list>

## verdict
- ready for read-only monitoring        : YES|NO — <one line>
- ready for declined recovery planning  : YES|NO — <one line>
- ready for draft/create preparation    : YES|NO — <one line>
- ready for approval-gated live actions : YES|NO — <one line>

## remaining risks
- <numbered list, concrete, actionable>
```

## Red flags that mean "stop the pass"

- `login_required` envelope at any item — stop, surface instructions.
- Empty `INTERNAL_ERROR` envelope at any item — stop, apply the
  3-hypothesis diagnostic from SKILL.md "Browser recovery policy".
- `browser_broken` envelope at any item — stop, request gateway
  restart approval.
- `tool_loop_warning` (repeated identical failure) — stop, do not
  retry.
- The agent loop fires `repeated_exact_failure_warning` — stop, do
  not retry in the same turn.

## Why this protocol exists

Without it, agents consistently try one of these broken approaches:

1. **`execute_code` to call `TelegramAdsToolset.call(...)` from the
   sandbox** — wedges the persistent profile lock, hits
   `BrowserProfileLockedError`, blocks.
2. **`execute_code` to instantiate `TelegramAdsAdapter.launch()`** —
   same problem, even more direct.
3. **Direct `requests`/`curl` to `ads.telegram.org/account` API** —
   bypasses the gate, bypasses the login session, will fail
   authentication or get blocked, and crucially bypasses the safety
   contract.
4. **Driving `browser_navigate` / `browser_click` from the
   generic browser tool** — opens a *second* browser, not on the
   persistent profile, will be a separate session with no cookies, and
   will hit the same login wall.
5. **Calling `telegram_ads_*` tools the agent doesn't have in schema
   anyway** — the LLM rejects them as "unknown tool", wasting a turn.

All five are predictable failure modes. The protocol above is the
defensive playbook that documents the right path for each.
