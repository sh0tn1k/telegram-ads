---
name: operate-telegram-ads
description: >-
  Operate ads.telegram.org through the typed telegram_ads_* tool layer: review
  cabinets and campaigns, snapshot accounts, prepare and validate ad drafts,
  analyze rejections, and run CPM / budget / start / stop / create / delete
  lifecycle actions through a strict human-approval flow. Use whenever the task
  involves Telegram Ads accounts, campaigns, budgets, or pixel events.
version: 1.0.0
---
# Operate Telegram Ads

You drive ads.telegram.org **only** through the `telegram_ads_*` tools. You never
open ads.telegram.org yourself, never run Playwright/Chromium/Xvfb, never pick a
`DISPLAY`, never click the UI, and never call the internal `/api`. The tool layer
owns the browser, the login session, account-token masking, and confirmation
tokens.

## When to use

- Reviewing cabinets, balances, campaigns, stats, or rejections.
- Taking a daily/weekly snapshot of all accounts.
- Preparing, validating, or previewing ad drafts.
- Changing CPM, adding/withdrawing budget, starting/stopping, creating, editing,
  or deleting ads — call the mutating tool; Telegram buttons confirm it.
- Working with pixel conversion events (Stars cabinets).

## When NOT to use

- The action isn't about Telegram Ads.
- A tool returns `not_implemented` / `forbidden` — do not work around it.

## Mandatory tool path

1. Always start a session-touching task with `telegram_ads_status` or
   `telegram_ads_ensure_login`.
2. Use the **most specific** tool for the job (see
   [CAPABILITY_MATRIX.md](../../docs/CAPABILITY_MATRIX.md)).
3. Read the envelope's `status` before anything else.
4. For any mutation, follow the approval flow below — never assume approval.

## login_required handling

If any tool returns `status: "login_required"` (or the operator asks to log in):

1. Call `telegram_ads_login_from_env`. It types `TELEGRAM_ADS_PHONE` from the
   host `.env` (never invent a number; never print it unmasked).
2. Tell the operator the **masked** number was entered and he must tap **Accept** in
   Telegram. Then immediately call `telegram_ads_login_wait`.
3. On `logged_in`, retry the original tool. The Chromium profile keeps the
   cabinet session.
4. Only if `state=code_required`: ask the operator to forward the ads.telegram.org
   code and call `telegram_ads_login_submit_code`. Do not invent a code.
5. Do **not** drive raw Playwright / a second Chromium on this profile.

### Halt-and-ask branch (mandatory during server-side login assist)

If at any point the login flow requests something outside the permitted
set — OTP field, 2FA password field, captcha, anti-fraud challenge,
"verify it's you" prompt that requires a code from chat, suspicious
redirect to a non-`*.telegram.org` domain, or any account-takeover
defense — the agent MUST:

1. Stop the login flow without submitting the field.
2. Reply verbatim: `manual confirmation required`.
3. Surface the exact UI element / error message observed.
4. Wait for the operator's next instruction.

The agent must NOT re-attempt the same flow a second time in the
same turn, fall through to raw Playwright/terminal/execute_code, or use
`telegram_ads_recover_browser_session` to mask the halt as a "browser
issue" — that tool is for browser state, not login state.

### `state=phone_required` is a hard stop, not a "try `login_start` anyway"

The Telegram Ads login funnel has **phone input** as a hard prerequisite
to app-approval. The funnel states progress roughly:
`unknown` → `auth_page` → `phone_required` → `app_approval_pending` →
`logged_in`. If `login_check` (or any read-only tool) returns
`state=phone_required`, the agent MUST stop and ask the operator — calling
`telegram_ads_login_start` will not push the funnel past the phone step
and will not trigger a Telegram app-approval prompt on the operator's phone.
A `login_start` call on a `phone_required` funnel returns
`state=phone_required` or `state=timeout` (login_wait polls empty),
and the agent will appear stuck.

**The two valid exits from `phone_required`:**

- **A.** the operator opens the persistent profile's browser window and types
  the phone + completes the app-approval manually. Agent stays out
  of the loop. After the operator confirms, `login_check` reports `logged_in`.
- **B.** the operator approves `telegram_ads_login_submit_phone` (with phone
  either given in the message or pre-stored in
  `~/.hermes/telegram_ads_phone.txt` per the "phone persistence
  override" section below). Agent submits, Telegram sends app-approval
  to the operator's phone, the operator confirms in the app, `login_wait` reaches
  `logged_in`.

**Stop-and-ask template** (use verbatim when `state=phone_required`):

> 🛑 СТОП — phone_required
>
> `login_check` shows `state=phone_required` and `requires_human_login=true`.
> The Telegram Ads funnel is stuck on phone input. `login_start` will not
> advance past this state without a phone in the form.
>
> Pick one:
> - **A.** Open the persistent profile browser, enter phone + confirm in app.
> - **B.** Approve `telegram_ads_login_submit_phone` (with phone in the message
>   or pre-stored). I'll then submit, you confirm in Telegram app, and I poll
>   `login_wait` to `logged_in`.
> - **C.** Skip the Telegram app path and complete OTP in the browser manually.

### Phone persistence override (file-backed phone, opt-in)

Default behavior per login-flow contract: the phone is **masked in
every output, log, and pending-confirmation view — never persisted in
the clear**. If the operator explicitly asks to store the phone on disk
(typically to avoid re-asking after cookie expiry, **option 3** in the
install-session menu), the agent writes a one-line file with the
following discipline:

- **Path:** `~/.hermes/telegram_ads_phone.txt`
- **Format:** E.164 (e.g. `+100****0000`), one line, no prefix
- **Mode:** `chmod 0600` (owner read/write only)
- **Sister file:** `~/.hermes/telegram_ads_phone.meta.json` with
  `created_at`, `chmod`, `override_guard` (the explicit operator
  acknowledgement that this overrides the "не логируй phone" guard),
  `usage_policy` (always read fresh at use time, always pass through
  `login_flow.mask_phone()` before any agent-visible output, every
  invocation requires explicit approval via
  `telegram_ads_apply_approved_action`, phone never displayed
  unmasked in chat / approval summaries / audit logs).
- **Display mask:** first-3 + `****` + last-2 (e.g. `+10****00`). Used
  in chat, in approval form's `human_summary`, in `pending_confirmations`,
  in audit logs. The raw 12-char E.164 stays on disk only.
- **Revocation:** `rm ~/.hermes/telegram_ads_phone.txt
  ~/.hermes/telegram_ads_phone.meta.json` reverts to per-request phone
  prompt.

**Masking at use time** (when the agent reads the file and submits
through `login_submit_phone`):

```python
from pathlib import Path
from hermes_telegram_ads.login_flow import mask_phone
PHONE = Path("/home/hermes/.hermes/telegram_ads_phone.txt").read_text().strip()
# PHONE is the raw E.164; never log/print it. mask_phone() formats for display.
display = mask_phone(PHONE)   # "+1********00" — safe to print/chat/approval form
```

**Self-imposed guard override.** Storing the phone in cleartext on
disk is a privacy regression vs the package's contract. The agent
should:

1. Refuse to do this by default and surface the conflict (this
   section + option 3 menu).
2. Only proceed on the operator's explicit override message that names
   "override" or equivalent and acknowledges the "не логируй phone"
   guard. Save the exact override text into `meta.json` for future
   auditability.
3. Treat the file as a single-purpose secret: never `cat` it in
   chat, never include it in error reports, never log it.

### `session_active=true` does NOT mean `logged_in=true`

A common read mistake: `telegram_ads_login_check` returns
`session_active=true` while `logged_in=false` and `state=phone_required`.
`session_active` is a Playwright-level signal — the browser session is
up and the persistent profile is loaded. It does NOT mean Telegram
Ads auth cookies are present or valid. The auth signal is
`logged_in` + `state==logged_in`. Always surface both in the
status report to the operator and never claim "session is healthy" from
`session_active` alone.

### State-classifier divergence between tools (a real, observed bug)

The two read-only state tools can return **different** `state` values
for the same page state. Verified 2026-06-06:

- `telegram_ads_login_check` → `state=phone_required`
  (uses `classify_login_dom` — heuristic DOM signature match).
- `telegram_ads_status` → `state=auth_page`
  (uses a different classifier, URL-based + body-text heuristic).

Both are on the same Playwright page, same `current_url=
https://ads.telegram.org/auth?to=account`. Neither is wrong — they
just classify by different signals. **The agent must read both**
when reporting funnel state to the operator, surface the divergence, and
prefer the more specific one (DOM signature usually wins over
URL-only for `phone_required` detection). Never claim "state=phone_required
from status" — `status` doesn't have that granularity.

The full divergence table, the "which one to trust" rule per state,
and the diagnostic recipe for "I have two different `state` values,
which is right?" live in `references/login-flow-process-isolation.md`
§"State-classifier divergence matrix".

### Accumulated-process profile lock (own-tool side-effect)

The persistent browser profile at
`~/.hermes/hermes-agent/browser_profiles/telegram_ads` is **shared
between the live gateway and any sandbox `execute_code` /
`terminal` subprocess that calls `TelegramAdsToolset`**. Each
agent-side `terminal()` call that touches `telegram_ads_*` opens a
new subprocess, which calls `TelegramAdsAdapter.launch(config)`,
which calls `launch_persistent_context` on the same profile dir.

If the previous subprocess did not clean up (e.g. the script
crashed mid-loop, or the process was killed by the supervisor),
the profile holds a `SingletonLock` symlink. The next subprocess
hits `BrowserProfileLockedError` with the message
"Opening in existing browser session. This usually means that the
profile is already in use by another instance of Chromium."

**Verified 2026-06-06:** the agent's own 5+ rapid `_atomic_login*.py`
scripts during a single login flow accumulated enough stale Chromium
processes to lock the canonical profile. This is **not** a package
bug — it's a side effect of running multiple `terminal()` invocations
that each spawn a Chromium.

**Operating Discipline interaction:** §3 forbids `ps` / `pgrep` /
`pkill` / `kill` against stuck Chromium without explicit approval.
The right move on `BrowserProfileLockedError` is:

1. **Stop the flow and surface to the operator** — "profile is locked by
   N stale Chromium processes from prior sandbox invocations".
2. **Request explicit approval** for process inspection + cleanup
   (scope: "Approve process inspection + kill stuck chromium holding
   telegram_ads profile lock"). Do **not** `pkill chrome` without
   approval.
3. **Mitigation for next time** — when running a multi-step login
   flow, **keep all steps in a single Python script** (one terminal
   call) instead of many. The atomic pattern (reissue + apply +
   login_wait in one process) is the canonical fix; see
   `references/login-flow-process-isolation.md` §"Atomic
   login-flow script template".

The SingletonLock symlink target (canonical pre-check) is documented
in the `SingletonLock symlink target is the canonical pre-check`
section below.

### Approval TTL on multi-step login flows (5-min pitfall)

`login_submit_phone` and `login_start` both return
`confirmation_id` with `expires_in_seconds: 300` (5 min). On a
multi-step interactive flow (issue → present to the operator → wait for
"approved" → apply), the 5-min TTL can blow if the chat loop is
slow or the operator responds in a separate turn. The exact failure mode:

```json
{"status": "error", "error": "invalid_confirmation",
 "message": "No pending approval for confirmation_id '<cid>'..."}
```

**Mitigation:** issue the approval request **as close as possible to
the apply call**, not at the start of a multi-turn flow. In practice
this means: in the same turn that the operator says "approved", re-issue
the confirmation_id (if more than ~1 min has passed since the
original ask) and apply immediately. Surface the TTL in every
approval ask:

> expires_in_seconds: 300 (5 min). Re-issued just before apply to
> avoid TTL expiry on slow chat loops.

**Root cause #2: cross-process singleton loss (a 2nd, silent cause of
`invalid_confirmation`).** The Hermes-typed wrapper's
`_toolset_singleton` (in `tools/telegram_ads_typed_tool.py`) and the
package's per-toolset `ApprovalRegistry` (in
`hermes_telegram_ads/hermes_tools.py::_get_adapter`) both live **in
the singleton's process memory**. Each `terminal()` / `execute_code()`
call from the agent spawns a **fresh Python process** → a fresh
singleton → an empty `ApprovalRegistry`. So even if the TTL hasn't
expired, a confirmation issued in process A is invisible to
`apply_approved_action` called in process B. The `get_pending_confirmations`
call in process B returns `pending: []` as the diagnostic signal.

**Verified 2026-06-06.** Pattern: reissue → present CID → chat roundtrip
("approved <cid>") → apply in **a new `terminal()` call** → "No pending
approval for confirmation_id '<cid>'". The fix is to do the entire
reissue-and-apply sequence inside a single `write_file` + single
`terminal()` invocation, with `apply_approved_action` and the original
mutating tool call in the **same Python script**:

```python
# single atomic script
ts = _make_toolset()
# 1) reissue (within script, not in chat loop)
cid_resp = await ts.call('telegram_ads_login_submit_phone', phone=...)
cid = cid_resp['approval']['confirmation_id']
# 2) wait for chat roundtrip via stdin pause, OR run without chat roundtrip
#    by issuing + applying in same block (synchronous atomic)
apply_resp = await ts.call('telegram_ads_apply_approved_action', confirmation_id=cid)
```

For login flows specifically, this is the only path that works
without chat-roundtrip delay. The chat-loop-friendly alternative is
to ask the operator to read the approval and send "approved" **after** the
script is already running and waiting, so the apply happens in the
same process that issued the CID. See
`references/login-flow-process-isolation.md` for the full pattern,
the `_wait_for_approval.py` skeleton, and the `tee` log recipe for
background polling.

### Login flow is **not** a "phone field → submit" form

A common misread: agents assume `ads.telegram.org` has a phone-number
text field like a typical login page. It doesn't. The flow is OAuth-style
via `oauth.telegram.org` (or equivalent), requiring device-bound session,
QR / app-notification confirmation, and 2FA in some cases. Trying to
"enter the phone number in the form" without going through the proper
flow produces `PHONE_CODE_INVALID` / `SESSION_PASSWORD_NEEDED` / 2FA
wall — and each failed attempt is logged on Telegram's auth server with
the agent process's IP/UA, which can trigger `FloodWait` /
`AUTH_KEY_DUPLICATED` anti-fraud on the operator's actual account. The phone
number is a *fallback input* the operator can authorize; the form is not the
flow.

### Login form autofill race (Chrome form-state vs Playwright `fill`)

The persistent browser profile retains Chrome's form-state — autofill
suggestions, last-typed value, session-restored input values. When
Playwright issues `input.fill("+100****0000")` against a field that
already contains a value, Chrome can **re-apply its form-state
autofill AFTER the fill**, overwriting the agent's value with whatever
the profile remembered (e.g. another phone number that was used in a
previous session). The submit then goes with the *wrong* number, and
the operator gets an app-confirmation prompt for a **different account** on
his phone. Verified 2026-06-05: persistent profile contained
`+199****1111` (a different saved number) at the login form's phone field; a
naive `fill("+100****0000")` + `click("Continue")` did not produce an
app-confirmation prompt, and a post-submit read-only diagnostic
showed the field still held `+199****1111` — i.e. autofill had
overwritten the agent's value before submit.

**Mitigation (when running login via debug-fallback Playwright):**

1. **Pre-check the field's current value** before fill. If it is
   non-empty and not the number the operator authorized, **stop and ask
   the operator** before submitting. Don't assume the field is empty.
2. **Defensive fill sequence** (use this if the operator confirms the value
   is wrong but wants the agent to proceed anyway):
   ```python
   field = page.locator('input[type="tel"]').first
   field.click()                                    # focus
   page.keyboard.press("Control+A")                 # select all
   page.keyboard.press("Delete")                    # clear
   page.keyboard.type(PHONE, delay=30)              # type, not fill
   # Verify before submit
   actual = field.input_value()
   assert actual == PHONE, f"autofill race: field has {actual!r}, expected {PHONE!r}"
   ```
3. **Always screenshot the filled field** (`page.screenshot(...)`
   immediately before submit) so a post-mortem can see what was
   actually submitted. Filename: `login_form_phone_filled.png`.
4. **Post-submit URL/title check** — if it equals the pre-submit URL
   exactly, the form failed client-side validation (e.g. the
   format check rejects the value). Halt with
   `manual confirmation required` and surface the screenshot +
   pre-submit and post-submit URL + body text snippet.

### Post-halt read-only diagnostic (permitted)

The "no re-attempt the same flow a second time in the same turn" rule
applies to the **submit action** — it does not forbid **read-only
inspection** of the failed state to understand the halt cause. A
follow-up Playwright pass that only opens the page, reads body text
and input values, takes a screenshot, and closes the browser (with
no submit, no click, no `fill` of any sensitive field) is
**permitted** under the same login-assist approval scope. The
distinction is intent: the diagnostic pass must not advance the
login flow.

When the diagnostic reveals a fact that changes the risk picture
(e.g. "the persistent profile contains a different saved phone
number", "the form expects a different phone format", "the site
redirected to a captcha"), surface that fact to the operator with the
screenshot path and the exact body text snippet, then stop. Do not
make a second submit attempt with the new format or with a
"workaround" — the answer is "ask the operator for a new approval scope
that explicitly authorizes the new submit".

## Server-side login assist via direct Playwright (debug-fallback only)

This section is the operational recipe when the operator has approved
login assist **and** the only path to a working login requires
driving Playwright from the agent process (not through the typed
`BrowserAutomationTool`). This is the **debug-fallback** path — it requires the explicit phrase `approve Telegram Ads debug fallback`
(separate from the `approved, run login assist` scope) and the
agent must follow the halt-and-ask branch on any non-permitted
field. Verified 2026-06-05.

### `launch_persistent_context` returns `BrowserContext`, not `Browser`

A subtle Playwright API trap: `p.chromium.launch_persistent_context(...)`
returns a `BrowserContext` **directly**, not a `Browser` you can
call `.contexts[0]` on. The common error:

```python
browser = p.chromium.launch_persistent_context(user_data_dir=PROFILE, ...)
ctx = browser.contexts[0]   # AttributeError: 'BrowserContext' object has no attribute 'contexts'
```

Fix: use `browser` as the context itself:

```python
browser = p.chromium.launch_persistent_context(...)
ctx = browser                     # the BrowserContext is what launch_persistent_context returns
page = ctx.pages[0] if ctx.pages else ctx.new_page()
# ...
browser.close()                   # closes the BrowserContext, not a Browser wrapper
```

`browser.close()` on a context returned by
`launch_persistent_context` works correctly (closes pages + shuts
down the Chromium process). Verified 2026-06-05.

### Persistent profile path (canonical vs legacy alternates)

Verified 2026-06-05: three sibling profile directories exist in
this environment, but **only one** is wired to the live Chromium:

| Path | Owned by | Status |
|---|---|---|
| `~/.hermes/hermes-agent/browser_profiles/telegram_ads` | live gateway-default Chromium (`--user-data-dir=…`) | **canonical, locked while gateway is up** |
| `~/.hermes/profiles/deepseek/home/.hermes/telegram_ads_browser_profile` | historical install artifact, untouched since Jun 03 | **do not use** |
| `~/.hermes/data/telegram_ads/browser_profile` | historical install artifact, untouched since Jun 03 | **do not use** |

When login maintenance is required, **always target the first
path** — the package's `BrowserProfileManager.shared()` resolves
to it via the venv's editable `.pth` file. Operating on the
other two paths is a no-op against the live Telegram Ads session
(no Chromium is reading from them).

### `SingletonLock` symlink target is the canonical pre-check

Before launching a second Playwright instance against the canonical
profile, verify the lock is released:

```bash
ls -la /home/hermes/.hermes/hermes-agent/browser_profiles/telegram_ads/SingletonLock
# Expected when locked:   lrwxrwxrwx ... SingletonLock -> host-<PID>
# Expected when unlocked: ls: cannot access '...': No such file or directory
```

The `host-<PID>` target is the hostname + Chromium PID. If the
lock target points at a PID that no longer exists (orphaned
shutdown), remove the symlink manually:

```bash
rm /home/hermes/.hermes/hermes-agent/browser_profiles/telegram_ads/SingletonLock \
   /home/hermes/.hermes/hermes-agent/browser_profiles/telegram_ads/SingletonSocket \
   /home/hermes/.hermes/hermes-agent/browser_profiles/telegram_ads/SingletonCookie
```

…and then verify the live gateway's Chromium has actually exited
(via `ps -eo pid,cmd | grep "chrome.*telegram_ads" | grep -v grep`).
Removing the lock while another Chromium is still alive is unsafe
and will corrupt the profile on next launch.

### Post-login verification: 3-call sequence, then STOP

After login maintenance completes and the operator's "confirmed"
message arrives, run the canonical 3-call verification:

1. `telegram_ads_status` — expect `ok` envelope, `launched: true` (if the gateway has spawned the adapter since restart) or `launched: false` (lazy), `logged_in: true`.
2. `telegram_ads_ensure_login` — expect `logged_in: true`. If `login_required`, the app-confirmation didn't propagate to `ads.telegram.org` yet. Wait 30s, retry once. If still `login_required` after 2 minutes, the login is incomplete — re-do the login flow with `telegram_ads_login_assist` instructions for human takeover.
3. `telegram_ads_get_browser_profile_info` — should return a populated profile info. **Persistent bug:** this tool sometimes returns empty `INTERNAL_ERROR` envelope (`{"ok": false, "error": "INTERNAL_ERROR", "message": ""}`) even on healthy sessions, due to the same 3-hypothesis issue described in "Empty `INTERNAL_ERROR` envelope — 3-hypothesis diagnostic" above. If the other two checks pass, this one is non-blocking.

**Then STOP.** Do not chain into the read-only acceptance probes
(items 1–9 of `references/acceptance-readonly-protocol.md`)
without a fresh command from the operator. Each step is gated
explicitly.

## Snapshot workflow

For "show me the state of the accounts" / daily review:

```
telegram_ads_snapshot_accounts()
```

One call ensures login, lists every cabinet, and for each collects budget +
campaigns + a screenshot, then writes a JSON summary. It is **partial-safe**: if
one cabinet fails, you still get the others plus `warnings` and `partial: true`.
Report `total_accounts`, `total_campaigns`, per-account balances, and surface
`warnings`. Reference the `json_summary_path` and screenshot paths; never paste
raw tokens (there are none in the output — only `account_ref` + masked tokens).

## Campaign review workflow

```
telegram_ads_list_ads()                  -> choose ad_id
telegram_ads_get_ad(ad_id)               -> creative + targeting + budget_status + rejection
telegram_ads_get_ad_stats(ad_id)         -> views / spend / monthly
telegram_ads_get_ad_budget_status(ad_id) -> live status, cpm, budget, spent
```

Summarize status, spend vs budget, CTR/CVR context, and any rejection.

## Ad Monitoring — canonical operator path

When the user asks to **monitor an ad**, **watch a campaign**, **set up a budget
threshold alert**, or **auto-decide when to recommend stop/continue**, you MUST
use the **Telegram Ads Targeting Operator** — the canonical monitoring runtime.

### Trigger phrases (route to operator, NOT to script/cron)

- "поставь рекламу на наблюдение"
- "следи за этой рекламой" / "следи за кампаниями"
- "сообщи когда останется N Stars"
- "сам решай когда стоит рекомендовать отключение"
- "watch this ad" / "monitor this campaign"
- "set up budget alert"
- "auto-decide stop/continue"

### Canonical workflow

1. **Find the ad** via `telegram_ads_list_ads` / `telegram_ads_get_ad`
2. **Read budget/status** via `telegram_ads_get_ad_budget_status`
3. **Register the watch** via `telegram_ads_register_campaign_watch`:

```
telegram_ads_register_campaign_watch(
    ad_id="<ad_id>",
    project_id="<project>",
    budget_threshold_stars=100.0,
    decision_checkpoints=True,
    routine_checkpoints_suppressed=True,
    auto_mutations=False,  # NEVER True
)
```

### Operator states and required actions

| Health status | What to do |
|---------------|------------|
| `running` / `ready` | Watch registered. Confirm to user: "Реклама поставлена на наблюдение. Operator будет проверять состояние каждые N минут." |
| `disabled` | **DO NOT write scripts/cron.** Return typed blocker with activation steps. Ask user: "Operator выключен. Нужна активация: `TELEGRAM_ADS_OPERATOR_ENABLED=1` + gateway restart. Без operator'а я не могу поставить рекламу на наблюдение." |
| `blocked` | **DO NOT write scripts/cron.** Return blocker reason + ask user to resolve. |
| `migration_required` | **DO NOT execute DDL.** Return migration details + ask for separate approval. |
| `error` | **DO NOT write scripts/cron.** Return error details + ask user to check gateway logs. |

### HARD GUARD — forbidden alternatives

**NEVER** do any of the following for Telegram Ads monitoring:

- ❌ Write a Python script (`.py` file with polling loop)
- ❌ Write a bash script
- ❌ Create a cron job (`cronjob` tool or `hermes cron create`)
- ❌ Create a new SQLite database
- ❌ Start a background daemon or watcher
- ❌ Install the legacy `hermes_telegram_ads.watcher` package
- ❌ Use `terminal(background=True)` for ad-hoc polling
- ❌ Use `delegate_task` to "build a monitoring system"

**If the operator is disabled, the ONLY valid response is:**
> "Operator выключен. Для наблюдения за рекламой нужна однократная активация operator'а. Без неё я не могу создать систему мониторинга. Напишите 'активируй operator', когда будете готовы."

### Watch policy contract

Every watch registered via `telegram_ads_register_campaign_watch` follows this
contract:

- **Budget threshold**: material notification when `remaining_budget <= threshold`
- **Decision checkpoints**: the decision engine classifies campaign health at
  evidence windows (250 → 1000 → 5000 impressions)
- **Routine checkpoints suppressed**: only material events generate notifications
- **No automatic mutations**: stop/CPM/budget changes ALWAYS require explicit human approval
- **Idempotent**: registering the same ad twice is a no-op

### Auto-registration on create/start

When a NEW ad is created or started via `telegram_ads_create_ad` or
`telegram_ads_start_ad`, a post-action hook auto-registers the watch with the
operator (idempotent). You do NOT need to manually call
`telegram_ads_register_campaign_watch` after create/start — it happens
automatically when the operator is enabled.

## Draft workflow (never goes live)

```
telegram_ads_prepare_campaign_from_brief(brief)   -> typed draft + policy check
telegram_ads_prepare_copy_variants(variants)      -> which copy passes policy
telegram_ads_prepare_targeting(target_type, targets, currency, target_countries)
telegram_ads_validate_ad(draft)                   -> checkAdPost + policy
telegram_ads_preview_ad(draft)                    -> preview_data + screenshot
telegram_ads_save_ad_draft(draft)                 -> server draft (still not live)
telegram_ads_upload_media(file_path)              -> media token for edit drafts
```

### Creative options & CPM modifiers

Uploaded photo/video creatives are **placement-specific**. Verified in a live
ExampleBot test (2026-06-06, ad title prefix `HERMES_MEDIA_REVIEW_TEST_`):
`target_type="search"` can upload/validate a photo and show it in the
`checkAdPost` preview, but the final Search campaign detail does **not** contain
the uploaded media. Treat this as **placement mismatch**, not a successful media
create test.

Placement rules:

- `target_type="channels"` supports uploaded photo/video creatives.
- `target_type="search"` does **not** support uploaded photo/video creatives.
  Search campaigns render as search-result style ads. Use text/query workflow
  only.
- `target_type="bots"` does **not** support uploaded photo/video creatives.
  Bot targeting uses bot/channel logo / `show_picture` workflow, not uploaded
  photo/video.

Tool behavior requirement:

- If `media_path` is provided and `target_type != "channels"`, return a
  structured error with reason/capability `unsupported_media_for_target_type`.
- `validate_ad` must block before upload/checkAdPost for unsupported placements.
- `create_ad` must block before issuing `approval_required`, before upload, and
  before create for unsupported placements.
- CPM estimates must apply `media_photo` / `media_video` modifiers only when the
  target placement supports uploaded media (`channels`). For `search`/`bots`, do
  not report uploaded-media CPM modifiers as applicable; mark
  `media_ignored_by_placement=true` if such a diagnostic field exists.

The schema may accept more than a placement delivers. Do not rely on server
validation alone: it may accept media for Search preview but drop/ignore it in
the final campaign surface. Full historical audit with capability matrix lives
in `references/creative-options-and-cpm-modifiers.md`; the placement-mismatch
lesson and patch checklist live in
`references/media-placement-compatibility.md`.

For uploaded photo/video creative tests, use channel targeting. Never use search
targeting for uploaded-media tests. For search campaigns use text/query workflow
only; for bot targeting use logo/show_picture workflow only.

## Search campaigns — keyword phrase rules

When creating search ads through `telegram_ads_prepare_ad_draft` / `telegram_ads_create_ad`:

1. **`show_picture` must be `False`.** The "Show bot/channel picture" checkbox does
   not apply to search ads, but if left as default (`True`), the Telegram Ads API
   charges a +30% CPM surcharge — raising the minimum CPM from 50 to 65. The
   `CreateAdDraft` model has a `model_validator` that forces `show_picture=False`
   for `target_type="search"`, but always pass it explicitly as `False` in the
   draft to be double-safe.

2. **Each `targets` element is ONE complete keyword phrase.** Do NOT split
   multi-word phrases into individual words. Telegram Ads checks each target
   element as a whole query; if you split a phrase like `"ai chatbot"` into
   `["ai", "chatbot"]`, Telegram Ads creates two separate queries whose individual
   words may be rejected when shorter than 4 characters. The correct form is
   `["ai chatbot"]` — one element, whole phrase.

   | ❌ Wrong | ✅ Correct |
   |---|---|
   | `["диалог", "гард"]` | `["диалог гард"]` |
   | `["ai", "chatbot"]` | `["ai chatbot"]` |
   | `["telegram", "bot"]` | `["telegram bot"]` |
   | `["умный", "помощник"]` | `["умный помощник"]` |

3. **Long phrases (30+ chars) are fine.** The base64 encoder handles any length.
   Reliability depends on the query being a single `targets` element, not on
   length.

4. **Multi-query search: the tool auto-fans out.** The Telegram Ads server
   (`createAd`) accepts only 1 search query per call (since ≈2026-05-31),
   but `build_create_ad_payload` and `checkAdPost` accept multiple. The
   `telegram_ads_create_ad` handler detects `target_type == "search"` with
   `len(targets) > 1` and automatically fans out to N individual ads:
   - **Without confirmation:** issues N confirmations (one per query),
     returns a batch approval envelope with all `confirmation_ids`.
   - **With confirmation:** `confirmation_id` accepts a comma-separated
     string (e.g. `"abc,def,ghi"`), creates N ads, one per query/confirmation.
     Budget is split evenly (`total_budget / N` per ad).
   - The agent should always pass a single phrase per `targets` element and
     let the tool fan-out handle server-side single-query limitation.

**Media source ambiguity guard.** If the operator's request contains both an explicit
`media_path` and an attached image path, compare them before upload/create. If
they differ, stop and ask which source is authoritative — do not silently prefer
one. This is especially important for live/create flows because using the wrong
creative is an external side effect even when the ad is created inactive.

**Post-create media discrepancy protocol.** If validate/checkAdPost reports
`media_uploaded=true` / `has_photo=true` but `get_ad_creative` reports
`has_media=false` or `show_picture=true`, do not immediately conclude create
failed. First capture/read the detail page through typed read-only tools
(`get_ad`, `get_ad_creative`, `get_ad_targeting`, `get_ad_budget_status`, and a
`telegram_ads_save_screenshot` with a `.png` suffix). If the screenshot shows
"Ad photo or video" / `Change Media` / a photo preview, treat this as a
parser/reporting bug, not a live campaign state bug. Do not edit/recreate/start
without a fresh explicit approval.

**Channel-media create pitfall (verified 2026-06-06).** Even with
`target_type="channels"`, a 16:9 uploaded photo, non-empty `media_token`,
`validate_ad(valid=true)` and preview fields `has_photo=true` / `media_on=true`
/ `picture=false`, the final created ad detail may still report
`has_media=false` and `show_picture=true`. Treat this as a post-create
persistence/detail discrepancy: report it and stop. Do **not** edit, recreate,
delete, start, or change CPM/budget/status without a fresh explicit approval.
Use `references/media-channel-create-postcreate-discrepancy.md` for the full
verification pattern.

Targeting is **immutable after creation** — finalize it in the draft.
TON cabinets cannot reach RU/UA/IL/PS audiences; use a Stars cabinet for those.
For an approved media-create test, never stop at `upload_media` +
`validate_ad` success. A live 2026-06-06 ExampleBot test showed this
possible split: upload returned a `media_token`, validation preview had
`has_photo=true` / `creative.media_uploaded=true`, and the create approval
summary said `media=photo`, but after creation `get_ad` / `get_ad_creative`
reported `has_media=false` and `show_picture=true` while the campaign was
created `On Hold` / not active. Therefore:

1. Verify local image ratio before upload (`width * 9 == height * 16`); if
   not 16:9, stop before any create.
2. Require a non-empty `media_token`; if upload fails, stop and do **not**
   create a media-less ad.
3. Pass both `media_path` and `media_token` into validate/create when the
   user explicitly requested uploaded media.
4. After create, verify the server-created ad via `list_ads`,
   `get_ad_budget_status`, and `get_ad`/`get_ad_creative`, then capture a
   typed screenshot of the detail page with
   `telegram_ads_save_screenshot(screenshot_name="...png", full_page=true)`
   if creative/media fields look contradictory.
5. Treat the **final UI screenshot** as the source of truth for media presence
   when typed creative fields disagree. `get_ad_creative.has_media=false` and
   `show_picture=true` can be a parser bug: the current parser historically
   derived `has_media` from `website_name` and hard-coded `show_picture=True`.
6. If final UI screenshot contradicts the draft/approval (no media block,
   wrong placement, empty text, etc.), report a discrepancy and stop — do not
   edit, recreate, delete, or start without fresh explicit approval.

Session-specific details:
- `references/media-live-flow-create-verification.md`
- `references/ad-detail-media-parser.md` — parser mismatch where media exists in UI but typed tools report `has_media=false` / `show_picture=true`. Also notes the UI/checkAdPost `+80%` uploaded-photo CPM caveat.

Targeting is **immutable after creation** — finalize it in the draft.
TON cabinets cannot reach RU/UA/IL/PS audiences; use a Stars cabinet for those.

## Approval workflow (every mutation)

Mutating tools (`telegram_ads_create_ad`, `telegram_ads_edit_ad`,
`telegram_ads_start_ad`, `telegram_ads_stop_ad`, `telegram_ads_change_cpm`,
`telegram_ads_add_to_budget`, `telegram_ads_withdraw_from_budget`,
`telegram_ads_create_event`, and the destructive `telegram_ads_delete_ad` /
`telegram_ads_delete_event` / `telegram_ads_revoke_share_stats_url`) request
confirmation **programmatically**. Do **not** ask the operator to type «да» / yes.

1. Call the mutating tool with its params and **no** `confirmation_id`.
2. The persist-safe `telegram-ops` plugin escalates to Hermes' native Telegram
   card: **Once / Session / Always / Deny** — the same buttons as system
   command confirmations.
3. Wait. After the operator taps **Once** (or Session / Always), the **same** tool
   call executes. Confirm from `data.executed`.
4. If he taps **Deny** or the prompt times out, the tool is blocked. Do not
   retry the same mutation and do not ask him to type yes instead.

`Session` auto-approves that same Ads verb for the rest of this conversation.
`Always` persists it like a Hermes command allowlist entry (`plugin_rule:telegram_ads:<tool>`).
Per-tool scopes: Always on `change_cpm` does **not** allow `delete_ad`.

Fallback only if the envelope is `status: "approval_required"` (CLI / no
gateway buttons): show `human_summary` and use
`telegram_ads_apply_approved_action(confirmation_id)`. Never invent a
`confirmation_id`. Use `telegram_ads_get_pending_confirmations` /
`telegram_ads_cancel_confirmation` for leftovers.

This hook lives in `~/.hermes/plugins/` (not `hermes-agent`), so `hermes update`
does not wipe it.

## Rejection analysis workflow

```
telegram_ads_get_rejection_info(ad_id)   -> raw category + description
telegram_ads_explain_rejection(ad_id)    -> explanation + concrete suggested_fixes
```

Apply the fixes in an edit draft, then run the approval flow for
`telegram_ads_edit_ad` (editing triggers re-review).

## CPM / budget / start / stop approval requirements

These all change live state and are `APPROVAL_REQUIRED`:

- `telegram_ads_change_cpm(ad_id, new_cpm)`
- `telegram_ads_add_to_budget(ad_id, amount)` / `telegram_ads_withdraw_from_budget(ad_id, amount)`
- `telegram_ads_start_ad(ad_id)` / `telegram_ads_stop_ad(ad_id)`

Always state the exact change (ad, old→new) in your approval ask. Note: TON
budget after add must stay ≥ 1.00 TON; withdraw has a 2-minute cooldown after a
status change.

## Output formats

- Lead with `status`. On `ok`, summarize `data` in plain language.
- On `approval_required` (fallback only), show `human_summary`. Prefer the
  Telegram Once/Session/Always buttons — do not ask him to type yes.
- On `login_required`, show the instructions and stop.
- On `not_implemented` / `forbidden`, say so plainly and suggest the supported
  alternative from the error `message`. Do not work around it.
- Reference artifacts by path (screenshots, reports, snapshot JSON). Never print
  tokens, cookies, phone numbers, or session data.

## Failure modes

- `policy_violation` — the ad text/URL breaks guidelines; fix and re-validate.
- `geo_blocked` — TON cabinet vs RU/UA/IL/PS; switch to a Stars cabinet.
- `invalid_confirmation` — token expired/mismatched/used; re-issue and re-ask.
- `api_error` — surface the message; retry once if transient, else report.
- `error` (unknown) — report the message; do not retry blindly.
- **registry/function-calling mismatch** — the typed `telegram_ads_*` tools
  are registered in `tools/registry` (57 entries under toolset
  `telegram_ads_typed`) but the LLM's function-calling API in the current
  session only exposes the legacy `telegram_ads` dispatcher. This typically
  happens after a model/provider switch, gateway restart, or fresh deploy
  before the schema cache has been refreshed. **Do not** treat this as
  "the package is broken" or fall through to raw Playwright/Chromium/Xvfb.
  Use the legacy `telegram_ads` dispatcher (see "Fallback: legacy
  `telegram_ads` dispatcher" below) — it is wired to the same
  `TelegramAdsAdapter` via `BrowserProfileManager.shared()` and the
  safety gates are identical. If the typed envelope is required
  (`status: "login_required"`, `status: "approval_required"`, etc.),
  either restart the gateway to refresh the LLM schema, or report the
  gap to the operator and ask whether to proceed with the simplified legacy
  envelope.

  **Telegram `/tools` UI does not auto-pick up the typed toolset.** The
  `telegram_ads_typed` toolset is registered in `tools/registry` and
  enabled in `platform_toolsets.telegram`, but the Telegram browser
  iterates `hermes_cli/tools_config.py::CONFIGURABLE_TOOLSETS` (a static
  list of `(name, label, desc)` tuples). If the user reports
  "telegram_ads_typed not showing in /tools", the fix is a one-line
  addition to that file, not a runtime change. The three runtime
  surfaces (AST discovery → registry membership → `CONFIGURABLE_TOOLSETS`)
  are independent — all three must be present for the user to see the
  toolset. See `hermes-tool-module-development` §"Verification recipe —
  three runtime surfaces, not one" for the full verification recipe.

  **Diagnostic recipe** (proven 2026-06-03):
  1. `search_files` for `tools/telegram_ads*tool.py` to confirm the file
     exists on disk.
  2. `execute_code` introspect
     `discover_builtin_tools()` — what does it actually import? Look for
     `telegram_ads_typed` in the imported list.
  3. `execute_code` call
     `tools.registry._module_registers_tools(Path("tools/telegram_ads_typed_tool.py"))`
     — returns `True`/`False`. **If `False` despite the file existing, see
     the AST pitfall below.**
  4. `execute_code` `ast.parse` the file and walk `tree.body` — if
     `registry.register(...)` calls live inside `if` / `try` blocks or
     inside function bodies, they are skipped by
     `_is_registry_register_call` / `_module_registers_tools`.
  5. After the fix lands on disk, the LLM's function-calling schema still
     does **not** refresh in the current turn. The fix only takes effect
     on the **next turn** after the operator sends a new message, when the
     gateway recomputes tool definitions via
     `model_tools._compute_tool_definitions()`. Do not retry
     `telegram_ads_snapshot_accounts` mid-turn; surface "fix applied on
     disk, will be visible next turn".

  **AST-based auto-discovery pitfall.** `tools/registry.py` discovers
  self-registering tools via AST inspection (`_module_registers_tools` +
  `_is_registry_register_call`). The check walks **only** `tree.body`
  (top-level statements) and **only** matches `ast.Expr` statements with
  a `registry.register(...)` call. It does **not**:
  - recurse into function bodies — so `def _register_all(): registry.register(...)`
    is invisible to the discovery pass even though it runs at import time;
  - unwrap `if` / `try` / `with` blocks — so a guarded
    `if _typed_available: registry.register(...)` is also invisible.

  The fix is to add an **unconditional top-level `Expr` statement**
  calling `registry.register(...)` in the tool file. If the registration
  should be no-op in some configs, gate it with a `check_fn=lambda: ...`
  argument on the `register` call itself — the discovery scan only looks
  at the call structure, not the call arguments. This pattern was
  required when integrating `telegram_ads_typed_tool.py` into Hermes
  auto-discovery (verified 2026-06-03: 58 typed tools appeared in the
  LLM tool surface once the unconditional `register` call was added,
  and the `check_fn=False` marker was filtered out of the LLM surface
  because registry honors `check_fn` at call time).

## Browser recovery policy

Transient browser errors must never collapse the whole session:

1. For **read-only** tools (`status`, `ensure_login`, `snapshot_accounts`,
   list/get reads) the tool layer catches a transient Playwright error, recovers
   the page/context **once** in place (from the same persistent profile, so the
   login session survives), and retries the operation **exactly once**. No loops.
2. If recovery fails, or the single retry fails again, the tool returns a typed
   `browser_broken` error — **stop and request an explicit gateway/browser
   restart approval from the operator**.
3. `telegram_ads_status` and `telegram_ads_get_browser_profile_info` always
   return a useful diagnostic (with `browser_state` + a `diagnostic` hint) even
   when the page/context is broken — use them to confirm state before retrying.
4. `telegram_ads_recover_browser_session` rebuilds the browser on demand. It is
   **read-only**: it performs no ads actions and enters no login codes. Use it
   when a mutating tool reports `browser_transient`, before re-issuing approval.
5. Recovery is **never** a license for raw Playwright/terminal fallback. Browser
   restart beyond `telegram_ads_recover_browser_session` requires the operator's
   explicit approval (see above).

### Browser state vs login state — DO NOT conflate

`telegram_ads_recover_browser_session` is for **browser state** (broken
page/context, transient Playwright errors, stale profile lock that a
recovery can release). It is **not** for **login state** (session
expired, `login_required` envelope, persistent profile never logged in,
session token missing from cookies).

Conflating them produces silent failures: the recovery tool returns the
same `INTERNAL_ERROR` envelope as the wedged read-only tools, the agent
mistakenly believes recovery is broken, and the real problem (missing
human login) is hidden behind a "browser issue" label. The diagnostic
check is: if the failing tool returned `status: "login_required"` (or
`reason: "session_expired_or_absent"`), the right path is the
`login_required handling` block above, **not** the recovery tool.

### Empty `INTERNAL_ERROR` envelope — 3-hypothesis diagnostic

See also `references/typed-wrapper-event-loop-lifecycle.md` for the proven 2026-06-05 wrapper lifecycle fix: persistent event loop thread, non-empty error envelopes, structured-error preservation, manager API compatibility, and wrapper-level tests.

A common shape after a bad restart / partial install / adapter wedge is:

```json
{"ok": false, "error": "INTERNAL_ERROR", "message": ""}
```

— empty `message`, no `operation`, no `retryable`, no `recovery_hint`, no
`browser_state`. The new code's `ToolError` envelope is supposed to have
all of these; if it doesn't, one of three things is going wrong:

**First split package vs wrapper.** Do not immediately conclude that the whole
Telegram Ads package is broken. Run read-only code-level diagnostics to compare:

1. package import/path/version and `TELEGRAM_ADS_TOOLS` count;
2. direct `TelegramAdsToolset` call, bypassing Hermes registry/wrapper;
3. registered Hermes wrapper call via `tools/registry`;
4. live LLM tool result.

If direct package and fresh registry calls succeed but the live LLM tool returns
an empty `INTERNAL_ERROR`, suspect the running gateway's
`tools/telegram_ads_typed_tool.py` state: stale `_toolset_singleton`, per-call
asyncio loop closure, or exception flattening. Full diagnostic and patch notes:
`references/typed-wrapper-envelope-diagnostics.md`.

1. **Cached old bootstrap state** — the live adapter was initialized
   before the install/restart, hasn't been reimported, and the old error
   path is still in memory. Fix: gateway restart (canonical path:
   `systemctl --user restart hermes-gateway-<profile>.service`).
2. **Dispatch envelope strip** — the package emits the new structured
   `ToolError` correctly, but a higher layer in the live gateway
   (response formatter, transport bridge) drops the fields and returns
   the legacy `INTERNAL_ERROR` shape. Requires investigating gateway
   code with `execute_code` / `read_file` access; **not** a runtime
   action. Open an investigation ticket and stop.
3. **Classifier miss** — the live persistent Chromium is broken in a way
   the new error classifier doesn't recognize (no signature match), so
   the exception doesn't get classified as `TransientBrowserError` /
   `BrowserBrokenError` and falls through to a generic handler that
   returns the legacy shape. Diagnose with `telegram_ads_status` (the
   one tool that *should* still return a useful diagnostic) and check
   `browser_state` / `diagnostic`. If even that returns the empty
   shape, hypothesis 1 is the answer.

**Do not** retry the failing tool a second time in the same turn —
the agent loop warning tool will fire, and the policy is "no loops"
per item 1 above.

## Acceptance pass protocol (read-only verification, no browser actions)

When the operator asks for a "verification", "acceptance", or "read-only
probe" pass against the live Telegram Ads account (status, list ads,
get stats, get rejection info, get targeting, validate draft, plus
approval-gate negative tests), use this protocol. It avoids the most
common pitfall: starting a second browser instance from a sandbox
process and wedging the live gateway's persistent profile.

### Sandbox vs live gateway — the profile-lock pitfall

The typed `telegram_ads_*` tools are **only** reachable through the
LLM-mediated function-calling API of a live gateway that has its
`TelegramAdsBrowserProfileManager` holding the persistent profile. If
the agent tries to instantiate `TelegramAdsToolset` directly in an
`execute_code` / `terminal` / `delegate_task` subprocess, that subprocess
will:

- Call `TelegramAdsAdapter.launch(config)` → tries to acquire the
  persistent profile dir.
- Hit a `BrowserProfileLockedError` because the live gateway's
  Chromium already holds the `SingletonLock` in the profile dir.
- Per Operating Discipline rule 5, return a structured error and
  **stop** — no retry, no workaround, no second browser session.

The agent must therefore drive the acceptance pass through the live
gateway's LLM surface (i.e. the agent's own function-calling tools
when running inside a gateway, or by sending the acceptance pass as a
message to the default gateway so the LLM there processes it with its
own toolset). The `execute_code` path can do **structural** checks
(registry, schema, safety class, gate logic via `FakeAdapter`) but
**not live** read-only probes that touch the live session.

### Sandbox-safe structural checks (no live browser)

These never touch the browser and are always available from
`execute_code` or even import-time:

- `from hermes_telegram_ads.hermes_tools import TELEGRAM_ADS_TOOLS,
  TelegramAdsToolset` — list 58 tools, each with `safety_class`,
  `mutating`, `requires_approval`, `input_schema`.
- Inspection of handler source via `inspect.getsource(...)` to confirm
  the approval gate: mutating handlers without `confirmation_id` call
  `adapter.issue_*_confirmation(...)` and return an `approval_required`
  envelope without executing the mutation.
- Approval-gate negative tests using `FakeAdapter` (from
  `tests/fake_adapter.py` in the package source) wired via
  `TelegramAdsToolset(_adapter_factory=lambda: FakeAdapter(...))`. This
  exercises the full gate logic end-to-end without a real browser.

The full Mode A / Mode B / Mode C recipe, the FakeAdapter wiring code,
the verdict template, and the red-flag list are in
`references/acceptance-readonly-protocol.md`.

### Three-mode acceptance split (verified 2026-06-06)

When running an acceptance pass on a session that may be unauthed
(common right after a package install), partition the checks by mode
to avoid wasted live calls and to surface the session state cleanly:

- **Mode 1 — Live read-only via the gateway** (touches browser).
  `telegram_ads_status`, `telegram_ads_ensure_login`,
  `telegram_ads_get_browser_profile_info`, `telegram_ads_login_check`,
  `telegram_ads_login_start`, `telegram_ads_login_wait`,
  `telegram_ads_snapshot_accounts`, `telegram_ads_list_ads`,
  `telegram_ads_get_rejection_info`, `telegram_ads_get_ad_targeting`,
  `telegram_ads_get_ad`, `telegram_ads_get_ad_stats`. These need an
  authed session; if `state != logged_in`, they return structured
  `login_required` envelopes and the agent stops per the failure
  stop-rules below. No fallback to raw Playwright/terminal.
- **Mode 2 — Offline math + classifier + fingerprint** (no browser,
  no network). `cpm_modifiers.compute_effective_cpm`,
  `media.media_type_for_path`, `media.validate_aspect_ratio`,
  `media.media_hash`, `payloads.create_confirmation_params`,
  `safety._fingerprint`. Plus targeted pytest invocations against
  the package's own test suite
  (`tests/test_creative_options.py`,
  `tests/test_login_flow.py`, `tests/test_hermes_inventory.py`,
  `tests/test_hermes_tools.py`) — these use `FakeAdapter` and never
  touch the browser. For generated dummy media, use Pillow to
  write a 16:9 PNG **outside the repo** (e.g. under
  `~/.hermes/runtime/<run-id>/dummy_media/`).
- **Mode 3 — Approval-gate negative tests** (issues
  `approval_required` envelopes, never applies). Call mutating tools
  without `confirmation_id` and confirm the envelope shape:
  `tool: "telegram_ads_create_ad"`, `safety_class: "APPROVAL_REQUIRED"`,
  `confirmation_id` present, `human_summary` non-empty, `expires_in_seconds`
  ≤ 600. After the test, **cancel** the pending confirmations
  via `telegram_ads_cancel_confirmation` to leave no
  unconsumed tokens (they auto-expire after TTL but cancelling is
  cleaner for audit).

A useful acceptance pass runs **Mode 2 first** (always available,
proves the install works end-to-end at the package level) then
**Mode 1** (gated on `state==logged_in`) then **Mode 3** (gated
on having issued mutating calls at all — skip if the task is purely
read-only).

Verdict format per the operator's expectation:

```
## per-mode
- Mode 2 (offline):  PASS  — 7/7 cpm_modifier + media + fingerprint + payload checks
- Mode 1 (live)   : PARTIAL — login_required (state=phone_required), session_active=true
- Mode 3 (gates)  : PASS  — 2/2 approval_required envelopes (login_start, login_submit_phone)

## final verdict
- login/session workflow : READY (tools behave correctly, awaiting operator login)
- creative/media validation : READY (offline math matches expected values)
- read-only monitoring : BLOCKED on session auth
- approval-gated actions : READY (gate + masking + TTL all verified)
```

### Live read-only probes (browser required)

For the actual ads.telegram.org calls (`telegram_ads_status`,
`ensure_login`, `get_browser_profile_info`, `snapshot_accounts`,
`list_ads`, `get_ad_stats`, `get_rejection_info`, `get_ad_targeting`,
`validate_ad`), drive them through the live gateway. Stop and surface
`login_required` (or any of the failure modes below) as soon as it
appears — do not chain.

### Output format (PASS/FAIL per item, structured verdict)

The operator expects this exact output shape for acceptance passes. Do not
deviate:

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

The verdict lines are read by the operator to decide what follow-up task to
authorize. Each YES needs a one-line reason; each NO needs a one-line
reason. "Remaining risks" is required even when verdict is all YES.

### Acceptance pass failure stop-rules

If at any point during the live pass the agent receives `login_required`,
an empty `INTERNAL_ERROR` envelope (per "3-hypothesis diagnostic"
above), or a `browser_broken` envelope — stop the pass immediately. Do
not continue to subsequent items. Surface the failing item number, the
exact envelope received, and the 3-hypothesis diagnosis (or login flow
instructions). Wait for the operator to decide whether to (a) approve a gateway
restart, (b) complete the human Telegram-app login, or (c) skip the
acceptance pass.

## No raw Playwright / terminal fallback

You must not use terminal / `execute_code` / raw Playwright / Chromium / Xvfb for
normal Telegram Ads operations when a `telegram_ads_*` tool exists. Fallback is
allowed **only if all three hold**: (1) the wrapper is missing or broken, (2)
the operator explicitly approves the fallback, and (3) no mutating action is performed.

The "sandbox vs live gateway" pitfall above is the most common breach of this
rule: agents reach for `execute_code` to call `TelegramAdsToolset.call(...)` or
to drive Playwright directly, which (a) bypasses the gate layer entirely and
(b) creates a second browser session that wedges the live gateway's persistent
profile. Both failures are surfaced as structured errors per Operating
Discipline rule 5; retrying or working around them is the wrong move.

## Tool availability verification (pre-flight, no browser)

When you need to confirm which `telegram_ads_*` tools are registered **without
touching the browser** (e.g. after a model/provider switch, a config change, or
a package update):

1. **Check config** — confirm `telegram_ads_typed` is in `platform_toolsets.telegram`
   in `config.yaml`.
2. **Check package** — `uv pip show hermes-telegram-ads` to verify the package is
   installed and what version.
3. **Introspect the registry** — via `execute_code`, import and read
   `TELEGRAM_ADS_TOOLS` from `hermes_telegram_ads.hermes_tools`. This gives the
   complete list of 57 typed tools with their `SafetyClass` without any browser
   call.
4. **Cross-reference** — against `CAPABILITY_MATRIX.md` in the package `docs/`
   directory for the official safety-class mapping.
5. **Verify LLM session surface (CRITICAL)** — the registry having 57 typed
   tools does **not** mean your LLM session can call them. After a model/
   provider switch, gateway restart, or fresh deploy, the LLM's
   function-calling schema may lag behind the registry. Verify by:
   - Looking at your own tool list in this turn: do you see
     `telegram_ads_status`, `telegram_ads_ensure_login`, etc.? If you only see
     the legacy `telegram_ads` single dispatcher, the typed tools are **not
     in your function-calling API**.
   - Cross-check by attempting to call a typed tool — if the LLM rejects it
     as "unknown tool", the registry/function-calling gap is real.
   - Optional: `execute_code` introspects `model_tools.discover_builtin_tools()`
     to enumerate what the LLM can actually invoke.

   When this gap exists, **do not** fall back to raw Playwright/Chromium/Xvfb.
   Use the legacy `telegram_ads` dispatcher (see "Fallback: legacy
   `telegram_ads` dispatcher" below) — it is always wired to the same
   `TelegramAdsAdapter` instance via
   `BrowserProfileManager.shared().acquire_adapter()`.

Use `execute_code` for step 3 — it's a read-only Python introspection, not a
Telegram Ads operation. This is a legitimate exception to the no-execute_code
rule because it never touches the browser, never invokes an adapter method, and
has zero side effects.

Classification query example:
```python
from hermes_telegram_ads.hermes_tools import TELEGRAM_ADS_TOOLS
classes = {}
for t in TELEGRAM_ADS_TOOLS:
    sc = t.safety_class.name  # SAFE_READ / DRAFT / APPROVAL_REQUIRED / FORBIDDEN_OR_DOUBLE_CONFIRM
    classes[sc] = classes.get(sc, 0) + 1
# classes → {'SAFE_READ': 29, 'DRAFT': 10, 'APPROVAL_REQUIRED': 14, 'FORBIDDEN_OR_DOUBLE_CONFIRM': 4}
```

Start with this pre-flight whenever the operator asks "проверь tool availability" or
"какие tools доступны" in a new session.

## Why a toolset doesn't show in `/tools` (Telegram UI) — registry chain

The Telegram `/tools` slash command (handler at `gateway/run.py::_handle_tools_command`) is **not** the same registry source as the LLM function-calling schema. It builds its visible list from a **single static table**:

- `hermes_cli/tools_config.py::CONFIGURABLE_TOOLSETS` — a hard-coded `List[Tuple[str, str, str]]` of `(toolset_name, label, description)`.

For each entry, the command then checks `platform_toolsets.telegram` in `config.yaml` to see which profiles have it enabled, and `config.yaml` is the **runtime filter** on top of the static table.

Implication: a toolset can be fully registered and callable by the LLM, present in `config.yaml`, and even defined in `toolsets.py::TOOLSETS` — and still be **invisible to `/tools` on Telegram**, because the static `CONFIGURABLE_TOOLSETS` table has no row for it.

Real example (2026-06-03): `telegram_ads_typed` was added to `config.yaml` `platform_toolsets.telegram` and to `toolsets.py::TOOLSETS` with all 57 tool names, the 57 tools were correctly registered via `tools/telegram_ads_typed_tool.py::_register_all_typed_tools()`, but `/tools` on Telegram only showed `telegram_ads` (legacy). Root cause: `telegram_ads_typed` was missing from `CONFIGURABLE_TOOLSETS`. One-line addition there fixed the UI listing (no code change to the registry, no gateway restart needed for the config file, but a gateway restart is needed for the Python module reimport).

**Full registry chain (highest → lowest precedence for what `/tools` shows):**

1. `hermes_cli/tools_config.py::CONFIGURABLE_TOOLSETS` — gates visibility in `/tools` Telegram UI. **Edit this first** when a toolset is "registered but missing from `/tools`".
2. `~/.hermes/config.yaml` `platform_toolsets.telegram` — per-profile enable/disable filter. Toolset must be listed here for each profile that should see it.
3. `toolsets.py::TOOLSETS["<name>"]["tools"]` — the canonical tool list for the toolset; must contain the registered tool names. (Plugins can also add toolset entries at runtime via `registry.register_toolset_alias`, but that's a separate path.)
4. `tools/<name>_tool.py` — must pass AST-based auto-discovery (`_module_registers_tools` must return `True`) and execute a `registry.register(...)` at module load that puts the tools into the named toolset. See `hermes-tool-module-development` skill for the discovery-marker pattern.
5. `tools/registry.py` — runtime registry; the source the LLM function-calling schema is built from each turn via `model_tools._compute_tool_definitions()`.

A toolset passes `/tools` visibility only when **all five** layers line up. A tool fails the LLM function-calling schema only when layers 4 and 5 break.

**Diagnostic recipe** when `/tools` doesn't show a toolset:

```bash
# 1. Is the toolset in CONFIGURABLE_TOOLSETS?
grep -n "telegram_ads_typed" ~/.hermes/hermes-agent/hermes_cli/tools_config.py

# 2. Is it in config.yaml?
grep -n "telegram_ads_typed" ~/.hermes/config.yaml

# 3. Is it in toolsets.py?
grep -n "telegram_ads_typed" ~/.hermes/hermes-agent/toolsets.py

# 4. Are the tools actually registered at runtime?
python3 -c "
import sys; sys.path.insert(0, '/home/hermes/.hermes/hermes-agent')
from tools.registry import registry
names = {t.name for t in registry.all() if t.toolset == 'telegram_ads_typed'}
print(f'{len(names)} tools in telegram_ads_typed')
"
```

`/tools` will show the toolset only if **all four** checks return positive.

**Restart requirement:** the `CONFIGURABLE_TOOLSETS` table is a Python list, so a gateway restart (`systemctl restart hermes-gateway-default.service`) is required for the change to be picked up. Per Operating Discipline, gateway restart is a **separate explicit approval** — it is not covered by a "fix the tool UI" mandate. State the restart in the approval ask, with the exact service name.

For a deeper write-up of the full chain, see `references/telegram-tools-ui-registry-chain.md` (layered: Python `CONFIGURABLE_TOOLSETS` → runtime YAML → static `TOOLSETS` → AST discovery → registry).

## Gateway restart via systemd (verified 2026-06-05)

This environment runs both Hermes gateway profiles under **systemd user units**, not bare nohup processes. The full lifecycle — TERM, drain, respawn, restart-on-failure, restart-step backoff — is owned by the unit, not by hand-rolled shell:

| Profile | Unit | KillMode | TimeoutStopSec | Restart | RestartForceExitStatus |
|---|---|---|---|---|---|
| `default` (the agent) | `hermes-gateway-default.service` | `control-group` | 30 ⚠ (misconfigured) | always | 75 |
| `deepseek` | `hermes-gateway-deepseek.service` | mixed | 210 ✅ | always | 75 |

**Canonical restart command:** `systemctl --user restart hermes-gateway-<profile>.service`. This is equivalent to TERM + drain + respawn, with the bonus that systemd honors `RestartSec=5` / `RestartMaxDelaySec=300` / `RestartSteps=5` correctly. Avoid `kill -TERM` to the user-level PID — it bypasses the unit's `Restart=on-failure` flow and (for the default unit) can be SIGKILLed mid-drain by the misconfigured `TimeoutStopSec=30`.

**Default-unit misconfiguration fix** (separate task; not in the
restart scope): regenerate the unit with
`hermes gateway service install --replace`. The new unit will set
`TimeoutStopSec=210` to match the gateway's 180s drain. Until then,
only restart the default unit via `systemctl --user restart` so
systemd's own respawn logic handles the 30s SIGKILL gracefully
(the `Restart=always` + `RestartSec=5` will pick it up).

**Pre-restart supervisor caveat:** between "approved, install" and
"approved, restart", the system supervisor can pre-restart a
gateway automatically. Always run
`ps -ef | grep hermes_cli` + `systemctl --user status hermes-gateway-*.service`
before sending restart; skip the units whose `Main PID` already
predates the install. Sending TERM to a fresh PID anyway is
unnecessary churn and can drop in-flight kanban / cron state.

## Fallback: legacy `telegram_ads` dispatcher

When the typed `telegram_ads_*` tools are not in your LLM session's
function-calling API (see pre-flight step 5), the **legacy single-tool
dispatcher** is always available as a fallback. It is wired to the same
underlying `TelegramAdsAdapter` via `BrowserProfileManager.shared()`, so:

- **No second browser instance** is created.
- **No Playwright profile lock race** is introduced.
- The same safety gates (confirmation_id, double_confirm) apply.

Invocation pattern:

```
telegram_ads(action="<action_name>", ...)
```

Full mapping of typed tool name → legacy `action` enum value → underlying
adapter method is in `references/legacy-action-mapping.md`. Common
equivalents:

| Typed tool | Legacy `action=` | Notes |
|---|---|---|
| `telegram_ads_status` | `"status"` | Returns URL string, not structured envelope |
| `telegram_ads_ensure_login` | `"ensure_logged_in"` | Returns `true` if logged in |
| `telegram_ads_current_account` | `"current_account"` | Returns account dict with masked token |
| `telegram_ads_list_ads` | `"list_ads"` | List of ad summaries |
| `telegram_ads_get_ad` | `"get_ad"` | Needs `ad_id` |
| `telegram_ads_snapshot_accounts` | **no single equivalent** | Compose manually — see "Snapshot via legacy dispatcher" below |
| `telegram_ads_stop_ad` | `"pause_ad"` | Naming is inverted (stop=pause, start=resume) |
| `telegram_ads_start_ad` | `"resume_ad"` | Naming is inverted |

**Important:** the legacy dispatcher returns a simpler envelope than the typed
tools — typically `{"ok": true, "data": <payload>}` or
`{"ok": false, "error": "...", "message": "..."}`. There is no top-level
`status:` field; read `ok` and `data`/`error` instead. The `login_required`
condition surfaces as `{"ok": false, "error": "LOGIN_REQUIRED", "message": "..."}`
rather than `{"status": "login_required", "data": {"instructions": "..."}}`.
If the typed envelope is required (e.g. for an approval workflow that checks
`status:` explicitly), restart the gateway to refresh the LLM's function-calling
schema, or report the gap to the operator and ask whether to proceed with the
simplified legacy envelope.

This fallback exists **only** because the underlying `TelegramAdsAdapter` is
identical to the typed-tool path. It is **not** a license to bypass approval
flow or to call mutating actions without `confirmation_id`.

### Snapshot via legacy dispatcher (no typed `telegram_ads_snapshot_accounts`)

The legacy `telegram_ads` dispatcher has **no** `snapshot_accounts` action
(the action set is 32 entries: SAFE_READ + DRAFT + CONFIRM + DOUBLE_CONFIRM,
see `references/legacy-action-mapping.md` for the full list). There is also
no `telegram_ads_workflow` action despite older docs claiming so. When
typed tools are unavailable, compose the snapshot manually with 4–5 calls:

```
telegram_ads(action="status")                           # alive check
telegram_ads(action="list_accounts")                    # all cabinets, masked tokens
telegram_ads(action="choose_account", account_token=…)  # for each active cabinet
telegram_ads(action="list_ads")                         # per cabinet
telegram_ads(action="get_account_budget")               # per cabinet
telegram_ads(action="screenshot", name="<cabinet>")     # per cabinet (optional, browser side-effect)
```

Inactivity check: `list_accounts` returns `is_active` for each cabinet.
Deep-dive (`choose_account` + downstream) only the **active** ones — the
inactive cabinets' balance is already in the `list_accounts` payload, and
calling `choose_account` on them can be a no-op or redirect.

Per-cabinet screenshot is **optional** in the legacy path. The typed
`snapshot_accounts` produces them automatically per cabinet, so when falling
back you either accept "no screenshot" or call `screenshot` per cabinet
(extra browser round-trip, prefer the typed tool when available).

Save the assembled payload to `~/.hermes/data/telegram_ads/snapshots/`
as `snapshot_legacy_<UTC>.json` with these top-level keys:

```json
{
  "snapshot_at_utc": "...",
  "scope": "partial snapshot via legacy telegram_ads dispatcher (typed toolset telegram_ads_snapshot_accounts not in LLM session schema)",
  "registry_function_calling_gap": true,
  "fallback_used": "legacy telegram_ads dispatcher (single-tool) — wired to same TelegramAdsAdapter via BrowserProfileManager.shared()",
  "accounts": [...],
  "summary": {"total_accounts": N, "active_account": "...", "active_account_balance_<currency>": X, "total_active_campaigns": M, "total_spent_last_visible_window_<currency>": Y},
  "warnings": [...],
  "partial": true
}
```

Mark `partial: true` whenever any cabinet wasn't deep-dived, or per-campaign
metrics (views/clicks/CTR/CVR/CPM/CPC/CPA) couldn't be attributed because
`list_ads` returned `[]` for the active cabinet.

**Heuristic — distinguish test churn from real funnel.** When
`get_account_budget` returns transactions like:

- Many `transfer_to_ad (-100)` and `returned_from_ad (+100)` pairs in
  short time windows (hours), and
- Ad titles like `Test`, `Test 1`, `Test 2`, single letters, or the bot's
  own `@handle` repeated,

it's **ad experimentation / creative A/B testing**, not a live funnel. Do
**not** report CTR/CVR/ROAS — there is no real spend and no real
conversion. Instead, summarize as "29 test transactions, net spend = 0,
no live campaigns" and flag in `warnings` that the cabinet is in
test-mode.

## Related skills (companion architecture)

This skill is the **tool operation layer**
the strict human-approval flow for live Telegram Ads actions. It complements:

- `prepare-and-manage-tg-ads` (`~/.hermes/skills/business-growth/`) — the
  **business/strategy layer**: campaign planning, creative drafting, audience
  analysis, CPM/budget recommendations, CTR/CPC/CPA/ROAS review, post-hoc
  analysis. Triggers on Russian phrases like «подготовь рекламу», «почему
  отклонили», «CPM/бюджет», «срез кабинета» (snapshot), «проверьть объявление»
  (inspect_ad).
- `review-campaign-results` (`~/.hermes/skills/business-growth/`) — the
  **post-hoc analysis layer**: tie campaign results to bot funnel metrics,
  decide continue/stop/iterate.

**Layering rule:** if the operator's question is about *preparing* or *analyzing* a
campaign, lead with `prepare-and-manage-tg-ads` (it will delegate to the typed
tools when it needs them). If the operator's question is about *executing* a live
action against the dashboard (change CPM, start/stop ad, run a snapshot, fetch
ad stats), lead with **this** skill. The two skills do not contradict — they
cover different layers of the same domain.

**Trigger overlap is expected and intentional.** Do not collapse them; do not
refuse to load both; do not present one as "primary". The companion architecture
is by design (approved 2026-06-03).

## DeepSeek profile access

Both `default` (the agent) and `deepseek` (DeepSeek Companion) Hermes profiles
are wired to the same `telegram_ads` and `telegram_ads_typed` toolsets in
`platform_toolsets.telegram` (overridden by the operator on 2026-06-03; previously
the policy was "DeepSeek by design без telegram_ads"). However, **mutating
actions still require the operator's per-call `confirmation_id`** — DeepSeek may
use the tools for read-only review and analysis delegated via AGI Team
Task Board, but it cannot trigger a live CPM change, ad creation, or any
spend-affecting action without the operator's explicit approval token.

This means: when delegating Telegram Ads work to DeepSeek Companion for a
second-opinion review, you can pass the read-only tools freely
(`telegram_ads_status`, `telegram_ads_snapshot_accounts`, `telegram_ads_get_ad`,
etc.) and DeepSeek will see them in its toolset. Mutating tools will be
present in DeepSeek's schema but will return `status: "approval_required"`
with `confirmation_id`, which DeepSeek must surface to the operator via AGI Team
Task Board rather than execute on its own.

## Pre-fetch branch verification protocol (the operator says "branch X is pushed, pull it")

When the operator gives you a branch name and says it's been pushed ("обновлён и
запушен", "pull latest branch", "подтянуть обновления"), **do not run
`git pull` against it**. The branch may not actually be on any configured
remote — the operator may have pushed to a fork that's not added locally, or to
the wrong branch, or not at all. Running `git pull` against a non-existent
ref fails with a generic "couldn't find remote ref" error that gives you
nothing to act on. Worse, if the ref *partially* matches (e.g. typo like
`fix/browser-recovery` vs. `fix/browser-recover`), git may fetch nothing
and you'll silently think you're up to date.

Run this 6-step pre-flight first. It is read-only, takes ~10 seconds, and
turns a blind `git pull` into a structured question you can bring back to
the operator. Verified pattern from 2026-06-05 (`fix/browser-recovery` requested
but absent from both `origin` and `upstream`).

1. **Verify the branch exists on every configured remote** (use the git
   protocol directly, not the index — `git ls-remote` reflects reality):

   ```bash
   git ls-remote origin 'refs/heads/<branch>'        # exact match
   git ls-remote origin 'refs/heads/<prefix>*'      # prefix wildcard
   git ls-remote upstream 'refs/heads/<branch>'     # also check upstream
   git ls-remote origin                              # full ref list of origin
   ```

   Empty output for an exact ref + non-empty for a prefix = likely a typo.
   Empty for both origin and upstream = branch was never pushed, or lives
   on a third remote not yet configured.

2. **Check local-only sources** for the branch (in case it was merged,
   rebased, or lives on a worktree):

   ```bash
   git branch -a | grep -E "<branch>|<fuzzy-substring>"
   git worktree list
   git stash list
   git tag | grep -iE "<fuzzy-substring>"
   git reflog -10 | grep -iE "<fuzzy-substring>"
   ```

3. **Check related names** when the exact branch is missing:

   ```bash
   # exact
   git branch -a | grep -F "<branch>"
   # case-insensitive substring
   git branch -a | grep -iE "<substring>"
   # if it's a fix/* branch, list sibling fix/* branches — common sibling
   # names for `fix/browser-recovery` were fix/browser-command-registry,
   # fix/browser-session-race, fix/upgrade-agent-browser-0.26
   git branch -a | grep -E "fix/<keyword>"
   ```

4. **Cross-check the public ref namespace** (only when step 1 is empty for
   a fork — `origin` may be a private repo):

   ```text
   site:github.com <org>/<repo> <branch>     # search engine
   https://github.com/<org>/<repo>/branches  # public branch list
   https://github.com/<org>/<repo>/pulls?q=is%3Apr+branch%3A<branch>
   ```

   Private repos won't show, so an empty result is not a final negative —
   it just means "not publicly visible, ask the operator for the right remote".

5. **Recover context from orphan artifacts** (when files referenced by the
   branch have already landed in the working tree, or were partially
   merged and removed). Two powerful techniques:

   - **Orphan `.pyc` introspection** — if `__pycache__/<module>.cpython-311.pyc`
     exists but the `.py` source is gone, the bytecode still encodes the
     module's docstring, function names, and `co_names` (the imports and
     top-level calls). Decoding it tells you what the deleted/branch-only
     file did, which is a strong hint about what the branch is meant to
     introduce:

     ```python
     import os, marshal
     os.chdir("<repo>")
     with open("__pycache__/<module>.cpython-311.pyc", "rb") as f:
         f.read(16)  # skip 16-byte CPython 3.7+ header
         code = marshal.load(f)
     print(code.co_consts[0])  # module docstring
     print("co_names:", code.co_names)  # imports + top-level names
     # enumerate inner code objects
     for c in code.co_consts:
         if hasattr(c, 'co_code'):
             print(f"  {c.co_name}({c.co_varnames[:c.co_argcount]})")
     ```

     This is a legitimate `execute_code` use even when the task is
     "read-only Telegram Ads verification" — it doesn't touch the
     browser, the adapter, or any I/O.

   - **Reflog archaeology** — `git reflog -20` shows every commit the
     local HEAD has visited. If the branch was rebased away locally, the
     commit SHAs may still be in the reflog even though the ref is gone.
     `git show <sha> --stat` resurrects the diff.

6. **Stop and surface the gap to the operator with a structured question**, not a
   free-form "branch not found". Use the **A/B/C/D/E menu** pattern from
   2026-06-05:

   - **A.** Wrong branch name (typo, renamed, different scope). Provide
     the closest sibling names from step 3.
   - **B.** Branch lives on a remote not configured here. Provide the
     exact `git remote add` command.
   - **C.** Branch lives in a PR (not in fork's refs). Provide the
     `git fetch origin refs/pull/<n>/head:fix/<branch>` recipe.
   - **D.** Branch needs to be pushed first from the source side.
   - **E.** Not a branch — refers to local working-copy edits. In that
     case, show `git status` and `git diff` to reveal uncommitted changes
     that match the description.

Only after the operator picks an option (or the branch is fetchable) do you
proceed to the pre-installation protocol below. Full step-by-step
recipes (exact git protocol queries, `marshal.load` decodes, reflog
archaeology) are in `references/branch-preflight-recipe.md`.

### Plan-only deliverable format (for branch-pull requests)

When the operator asks to "pull a branch, integrate it, restart", he expects a
structured **plan-only** response before any mutating action. The
9-section format the operator used on 2026-06-05 is the canonical shape — write
it with these section headers in this order:

```
0. Blocker         # if branch isn't fetchable, lead with the A/B/C/D/E menu
1. Current commit  # `git log -1 --oneline` on the local branch
2. Incoming commits  # `git log --oneline <local>..origin/<branch>` — only after fetch
3. Changed files   # `git diff --stat <local>..origin/<branch>` — only after fetch
4. Install plan    # pip / venv / no-op
5. Registry / toolset plan  # which toolsets/tools are affected, profile config diff
6. Skill check     # the umbrella skill md5 + whether the branch ships an updated copy
7. Restart plan    # exact PIDs, kill -TERM sequence, no Xvfb/Playwright touch
8. Post-restart verification  # read-only smoke tests, NO mutating actions
9. Mutating actions scope    # explicit list of tools that will NOT be touched
```

If the branch is not fetchable, sections 2/3 say "cannot show — branch is
not fetchable. Answer A/B/C/E above." This makes the blocker visible
without inventing diffs. Verified 2026-06-05: the operator engaged with the
A/B/C/D/E menu rather than re-pushing or renaming the branch, which
saved a roundtrip.

## Code patch discipline (proposed-patch-first, then apply, then verify)

When the user asks to **change code** in the installed `hermes_telegram_ads`
package (fix a bug, add a guard, rename an arg, change a handler), the
discipline is:

1. **Propose the patch first.** Show the proposed diff/file change as
   markdown, with exact code blocks, before any write. Do not run live
   `telegram_ads_*` calls during this phase. The skill's job here is to
   show the proposed shape; the user's job is to approve or modify.
2. **Wait for explicit approval per file/scope.** A single approval
   message covers one patch scope, not the entire session. Do not chain
   "patch A → patch B → patch C" under one approval.
3. **Apply with `cross_profile=true`** because the package lives in
   `~/.hermes/profiles/deepseek/plugins/packages/hermes_telegram_ads_pkg/`
   and the active profile is `default`. This is a soft guard, not a
   security boundary — it is required for the file edits to succeed.
4. **Run unit tests after apply.** Use the venv's python explicitly:
   `/home/hermes/.hermes/hermes-agent/venv/bin/python -m pytest -q
   <relevant-test-files>`. The full suite is `405+` tests in <10s.
5. **Live tool surface is NOT updated until gateway restart.** After a
   code patch, the running gateway's already-imported `TELEGRAM_ADS_TOOLS`
   registry still reflects the OLD code. Calling a live `telegram_ads_*`
   tool will produce old-behavior output even though the package source
   has been patched. Verified 2026-06-06: applied placement-aware media
   guard to the package, unit tests passed (`405 passed`), but live
   `telegram_ads_estimate_cpm` for `target_type=search + media_path`
   still returned `modifiers_applied: ["media_photo"]` and
   `estimated_effective_cpm: 97.5` — the old behavior. The patch was
   correct; the live tool surface needed a gateway restart to re-import
   the package. **Do not conclude the patch is broken from a live
   test alone.** Always:
   - Confirm patch via unit tests on the package directly.
   - Surface the live/restart gap to the user explicitly.
   - Do not request a restart without separate explicit approval
     (per Operating Discipline and gateway-restart discipline above).
6. **When commit is approved, do not push without separate approval.**
   Push needs GitHub credentials. In this environment the credentials
   often live as an embedded PAT in another repo's `origin` URL
   (`https://<user>:<PAT>@github.com/...`). Extracting that PAT into a
   `git remote set-url` call requires separate approval and must not
   print the PAT in chat (mask with truncation, `s[:12]+"…"`).
7. **Don't touch installed skills in `~/.hermes/skills/<category>/`
   in the same atomic change as a package patch.** They are not in
   the same git repo. The package repo can be staged + committed +
   pushed atomically; the installed skill copy is a separate file
   under `~/.hermes/` and requires its own `cross_profile=true` write
   and its own approval. Surface this as a separate decision.

## Pre-installation / pre-mutation verification protocol

When the task involves **integrating a new feature branch** (or any code
change) into the installed `hermes_telegram_ads` package — *before* any
write — run the read-only verification protocol. This is a hard rule; the operator
explicitly demanded it on 2026-06-03 and earlier ("сначала покажи plan + diff,
ничего не применять"). The protocol is:

1. **Locate the target**: confirm installed package path, current git
   branch/commit (likely "not a git repo" for installed), and the version
   that will be replaced.
2. **Verify file existence** of every expected file from the source branch
   (e.g. `hermes_telegram_ads/hermes_tools.py`, `schemas.py`, `docs/*.md`,
   `skills/operate-telegram-ads/SKILL.md`, `tests/test_hermes_*.py`).
3. **Show line counts and SHA256** of source files; this catches a stub
   (1-line placeholder) before it's installed.
4. **Classify changed files** into new / modified / divergent (where
   installed has local additions that the branch did not include).
5. **Show exact diff** of every file you plan to touch, before any
   `write_file`/`patch`/`cp`.
6. **List approval gates** explicitly: which steps require `cross_profile=True`,
   which require the operator's explicit "approved" per step, which require gateway
   restart, which require live browser (none should).
7. **Stop and wait for the operator's "approved"** before any mutating action.

After approval, follow the additive-merge procedure in
`references/feature-branch-integration.md` (8-step procedure, includes
backup → classify → copy new → patch `__init__.py` additively → patch
`conftest.py` additively → add inventory exclusion for branch-vs-installed
drift → install skill in two places → run hermes + core tests).

### Package architecture (verified 2026-06-05, `fix/browser-recovery`)

The `telegram_ads_*` typed tools come from a **separate Python package**,
not from the `hermes-agent` repo. Misreading this leads to wasted git
operations on the wrong repo, wrong toolsets.py, and wrong restart scope.

**Source repo and branch (default for the operator's setup):**
- Repo: `https://github.com/example/telegram-ads-upstream`
  (note: this is a *different* `example/*` repo from
  `example/hermes-fork`; both are forks but of different
  upstreams). **The operator runs two `example/*` repos side-by-side** —
  `hermes-fork` is the customizations fork of
  `NousResearch/hermes-agent` (lives in the hermes-agent working
  tree, branch `main`), and `telegram-ads-upstream` is
  the Telegram Ads manager tool's own repo (lives in the
  `hermes_telegram_ads_pkg` editable install path, branch
  `fix/browser-recovery` as of 2026-06-05). The two repos do **not**
  share branches, do not share remotes under the same alias, and
  do not share commit history. If you `git fetch` from the wrong
  one, you will get the wrong diff. **Always confirm the remote
  URL with `git remote -v` before fetching.**
- Default branch is `main`; the operator's active dev branch is `fix/browser-recovery`
  (as of 2026-06-05). Use the branch the operator named, not `main`.
- The branch typically has multiple commits ahead of `main` (7 commits on
  `fix/browser-recovery` at HEAD `aed0ea818`); don't fall for a stale local
  `main` checkout.

**Where the package source lives in the Hermes environment:**
- Install path (editable): `~/.hermes/profiles/deepseek/plugins/packages/hermes_telegram_ads_pkg/`
  (note the `_pkg` suffix; not a profile-specific install — both
  `default` and `deepseek` profiles share it through the single shared
  venv at `~/.hermes/hermes-agent/venv/`).
- venv pointer: `venv/lib/python3.11/site-packages/__editable__.hermes_telegram_ads-0.1.0.pth`
  with `MAPPING = {'hermes_telegram_ads': '/home/hermes/.hermes/profiles/deepseek/plugins/packages/hermes_telegram_ads_pkg/hermes_telegram_ads'}`.
- The pkg dir is **not** a git repo by default — it's a plain checkout
  copied here on initial install. `git fetch` from this dir fails until
  you `git init` it (or fetch into a temp worktree and rsync the result).

**What stays in `hermes-agent` and what lives in the package:**
| Lives in `hermes-agent` repo | Lives in `hermes_telegram_ads` package |
|---|---|
| `tools/telegram_ads_tool.py` (legacy single dispatcher, 1 tool) | `hermes_telegram_ads/hermes_tools.py` (typed tools, ~58) |
| `tools/telegram_ads_typed_tool.py` (wrapper that registers 57 typed tools) | `hermes_telegram_ads/browser.py`, `browser_manager.py`, `adapter.py`, `api.py`, `errors.py`, `pages/*`, `schemas.py`, `types.py`, `payloads.py`, `parser.py` |
| `toolsets.py` (toolset definitions for `telegram_ads` + `telegram_ads_typed`) | `docs/HERMES_INTEGRATION.md`, `docs/UI_CAPABILITY_PARITY.md` |
| `~/.hermes/config.yaml` and `~/.hermes/profiles/*/config.yaml` `platform_toolsets.telegram` (enable/disable) | `skills/operate-telegram-ads/SKILL.md` (the package's own copy) |
| `~/.hermes/skills/devops/operate-telegram-ads/SKILL.md` (this file, the installed skill) | `tests/`, `pyproject.toml`, `CHANGELOG.md` |

**Update flow for a new `fix/<branch>` in the package repo:**

1. `cd /home/hermes/.hermes/hermes-agent && git remote add tg-ads-mgr https://github.com/example/telegram-ads-upstream.git` (one-time; `tg-ads-mgr` is a stable alias, not a per-branch name).
2. `git fetch tg-ads-mgr` and inspect: `git log --oneline tg-ads-mgr/main..tg-ads-mgr/fix/<branch>`.
3. **Diff against the installed source, not against `main`:**
   ```bash
   # 29 source files in hermes_telegram_ads/ + 16 new files + docs/, skills/, tests/
   diff -rq <pkg>/hermes_telegram_ads <(git ls-tree -r --name-only tg-ads-mgr/fix/<branch> | grep '^hermes_telegram_ads/' | sed 's|^|<worktree>/|')
   ```
   Use a python loop (mismatches + missing + extras) for byte-level comparison — see
   `references/hermes-telegram-ads-update-protocol.md` for the exact script.
4. **For each changed file, check whether the change requires edits in `hermes-agent`**:
   - Tool name added/removed → does the package still register it via
     `TelegramAdsToolset.to_hermes_tools()`? If yes, `toolsets.py` is
     automatically consistent (it enumerates the 57 known names statically;
     a new name requires a one-line `toolsets.py` addition).
   - `BrowserProfileManager` (deprecated alias) is the symbol imported by
     `tools/telegram_ads_typed_tool.py`. The package keeps
     `TelegramAdsBrowserProfileManager` as canonical **and** preserves
     `BrowserProfileManager` as a deprecated alias. Don't refactor the
     hermes-agent tools file just because the package renamed the
     canonical class — the alias is load-bearing for backward compat.
   - Skill: branch ships `skills/operate-telegram-ads/SKILL.md`. The
     installed skill at `~/.hermes/skills/devops/operate-telegram-ads/SKILL.md`
     is *usually larger* than the branch's copy (it has additional
     sections like "Tool availability verification", "Pre-fetch branch
     verification protocol", "DeepSeek profile access" written during
     earlier sessions). **Append** the new section to the
     installed copy, do not overwrite.
5. **Apply the update without `git pull`** (because the pkg dir isn't a
   git repo by default — see pitfall). The pkg dir contains ~80
   untracked source files from the previous install, so a bare
   `git checkout -B <branch> FETCH_HEAD` will refuse with
   *"Please move or remove them before you switch branches"*.
   Move the untracked trees to a `/tmp` hold dir first, then checkout,
   then verify `git status` is clean:
   ```bash
   cd /home/hermes/.hermes/profiles/deepseek/plugins/packages/hermes_telegram_ads_pkg
   HOLD=/tmp/hermes_pkg_hold_$$ && mkdir -p "$HOLD"
   for d in README.md config.example.yaml docs examples \
            hermes_telegram_ads.egg-info hermes_telegram_ads \
            pyproject.toml skills tests; do
     [ -e "$d" ] && mv "$d" "$HOLD/"
   done
   # keep .git, .pytest_cache, .pytest_tmp
   git init -q && git remote add origin https://github.com/example/telegram-ads-upstream.git
   git fetch --depth=20 origin fix/browser-recovery
   git checkout -B fix/browser-recovery FETCH_HEAD    # now succeeds
   git status --short   # MUST be empty before continuing
   # Refresh the editable install (use `pip3` if `pip` is not present in the venv):
   cd /home/hermes/.hermes/hermes-agent
   venv/bin/pip3 install -e /home/hermes/.hermes/profiles/deepseek/plugins/packages/hermes_telegram_ads_pkg --no-deps
   # Cleanup the hold dir at the very end of the install, after the smoke test passes:
   rm -rf "$HOLD"
   ```
   Pitfall: in this venv the pip binary is `pip3` (and `pip3.11`), not
   `pip`. Using `venv/bin/pip` errors with *"No such file or directory"*.
   The `hermes-agent` venv at `~/.hermes/.hermes-agent/venv/` is Python
   3.11; `pip3` resolves to the correct version. If you see "no such file
   or directory" on a pip command in this environment, the fix is
   always `pip3` (or `pip3.11`), not an install step.
6. **Smoke test the package import** (read-only):
   ```bash
   venv/bin/python -c "import hermes_telegram_ads; \
     from hermes_telegram_ads.hermes_tools import TelegramAdsToolset; print('ok')"
   ```
7. **Run the new test suite** (also read-only — uses mocked adapter and
   golden fixtures, no live browser):
   ```bash
   cd <pkg> && venv/bin/python -m pytest -x -q 2>&1 | tail -30
   ```
8. **Restart the stale gateway profiles** (separate explicit approval —
   not covered by a generic "apply the update" mandate). The canonical
   path in this environment is `systemctl --user restart
   hermes-gateway-<profile>.service`, NOT `kill -TERM <pid>` + nohup
   cold start. Both gateway units are managed by systemd user units
   with `Restart=always` / `RestartForceExitStatus=75`; the deepseek
   unit has a drain-friendly `TimeoutStopSec=210`, the default unit
   has a misconfigured `TimeoutStopSec=30` that SIGKILLs mid-drain.
   A bare `kill -TERM` to a systemd-managed gateway bypasses the
   unit's `Restart=on-failure` flow and risks that 30s SIGKILL.
   **Before** sending restart, run `ps -ef | grep hermes_cli` +
   `systemctl --user status hermes-gateway-*.service` — the system
   supervisor can pre-restart a gateway for you between "approved,
   install" and "approved, restart" (verified 2026-06-05: default
   gateway came up on new code automatically, deepseek stayed stale).
   Only `systemctl --user restart` the units whose PID predates the
   install.
   **Do not** touch Xvfb `:99` (Telegram Ads browser screen),
   **do not** touch Playwright, **do not** use `pkill`/`kill -KILL`
   on gateway PIDs.
9. **Post-restart verification** (read-only): `telegram_ads_status`,
   `telegram_ads_list_accounts`, `telegram_ads_get_browser_profile_info`,
   and the *new* tool introduced by the branch (e.g.
   `telegram_ads_recover_browser_session` on `fix/browser-recovery`).
   Also confirm both gateway logs show
   `Connected to Telegram (polling mode)` since the fresh start —
   actual log paths are `~/.hermes/logs/gateway.log` (default) and
   `~/.hermes/profiles/deepseek/logs/gateway.log` (deepseek), NOT
   `gateway-default.log` / `gateway-deepseek.log`.

**Common pitfalls (each costs a roundtrip):**

- **"Branch not on `upstream`"** — the operator's branches often live on
  `tg-ads-mgr` (a custom remote) or on a private fork, not on the
  `NousResearch/hermes-agent` upstream. `git ls-remote upstream` is
  the wrong place to look for a Telegram Ads tool update.
- **"I edited `toolsets.py` for the new tool"** — almost always wrong.
  The package's `to_hermes_tools()` is the source of truth; the
  hermes-agent `toolsets.py` only enumerates the *currently registered*
  toolset members. If the new tool is meant for the `telegram_ads_typed`
  toolset, the package auto-registers it; `toolsets.py` needs a one-line
  addition only if you also want the static tool-name list to be
  exhaustively documented (it does not block runtime).
- **"I'll `pip install hermes-telegram-ads`"** — the package is
  *editable*-installed at version 0.1.0. PyPI does not host it (it's
  Proprietary per `pyproject.toml`). Pull-then-reinstall via `pip install
  -e --no-deps` is the only path.
- **"Restart both gateways at once"** — you drop the LLM's
  function-calling schema for both profiles simultaneously. Restart one,
  verify, then the other. Better: only restart the profile that needs
  the new tool surface (typically `default` for the operator's primary work).
  Use `systemctl --user restart hermes-gateway-<profile>.service`, not
  `kill -TERM` (which SIGKILLs the default unit mid-drain because of
  its misconfigured `TimeoutStopSec=30`).
- **"`venv/bin/pip` says no such file"** — the hermes-agent venv at
  `~/.hermes/.hermes-agent/venv/` ships only `pip3` and `pip3.11`, not
  `pip`. Use `venv/bin/pip3 install -e ... --no-deps` (or
  `venv/bin/pip3.11`). Do **not** try to install pip via
  `python -m ensurepip` — the venv is PEP 668-locked and the
  editable install doesn't need it. Verified 2026-06-05.
- **"The installed skill is bigger — overwrite it"** — the installed
  skill accumulated operational sections across multiple sessions
  (registry chain, pre-fetch protocol, pre-installation protocol,
  DeepSeek profile access, etc.). `diff` and `merge` the new section
  in, do not `cp` the branch's copy over.
- **"Default gateway is on old code, so I need to TERM both"** —
  between "approved, install" and "approved, restart", the system
  supervisor can pre-restart a gateway automatically (visible as a
  fresh `Main PID` and `Active: active (running) since <install-time>`
  in `systemctl --user status`). Check the unit's `Main PID` age
  before sending restart; skip the units that are already on the
  new code. Sending TERM to a fresh PID anyway is unnecessary churn
  and can drop in-flight kanban / cron state.

Full update procedure with exact commands, byte-diff script, and the
worktree/rsync alternative lives in
`references/hermes-telegram-ads-update-protocol.md`.

## Typed wrapper event-loop lifecycle bug class

If a typed `telegram_ads_*` call returns an empty legacy envelope such as
`{"ok": false, "error": "INTERNAL_ERROR", "message": ""}`, do not immediately
classify the Telegram Ads package as broken. First split package-level behavior
from Hermes wrapper/runtime behavior: direct package call vs `tools.registry`
wrapper dispatch vs live LLM function-call output. A common root cause is the
Hermes wrapper creating and closing a new asyncio event loop per typed call while
keeping a process-level `TelegramAdsToolset` / adapter / Playwright singleton.
The fix is a persistent process-wide event-loop thread, preserving structured
errors, and never emitting an empty `INTERNAL_ERROR.message`. Detailed diagnostic
and patch pattern: `references/typed-wrapper-event-loop-lifecycle.md`.

## Typed wrapper: arg-name collision with `call(name=)`

A second recurring failure mode is `TypeError: TelegramAdsToolset.call() got
multiple values for argument 'name'`, which surfaces through the wrapper as
an empty legacy envelope. The cause: `TelegramAdsToolset.call(self, name, **kwargs)`
already passes `name=tool_name` as a kwarg; if the LLM input schema for a tool
also accepts a `name` field, both collide in `**kwargs`. The canonical
instance was `telegram_ads_save_screenshot` — fixed 2026-06-05 by renaming
the LLM-facing field to `screenshot_name` in the package `ToolSpec` and
handler signature.

**Rule for new typed tools:** never use a field name in the LLM-facing input
schema that matches any reserved kwarg on `TelegramAdsToolset.call` (the
`name` positional) or on the per-tool handler signature. If the natural
name would be `name`, use a domain-specific rename in the schema
(`screenshot_name`, `artifact_name`, `ad_name`, `account_name`,
`campaign_name`) and keep the internal handler kwarg stable.

**"Fix in both places" workflow (the operator's preferred pattern):** when a bug
spans the package source AND the Hermes wrapper, apply the fix in both
layers in one cycle — package source gets the canonical rename
(schema, handler signature, docstring), the Hermes wrapper gets a
per-tool argument-rename map for backward compatibility with older LLM
prompts that still send the old name. After the package fix, a gateway
restart is required so the live `TELEGRAM_ADS_TOOLS` registry re-imports
the corrected `ToolSpec` — the wrapper fix alone is not enough because
the LLM-facing schema is computed from the package, not the wrapper.
Detailed patch pattern + read-only repro + regression tests:
`references/typed-wrapper-arg-name-collision.md`.

## Reference index

The umbrella skill ships the following `references/*.md` for session-specific
detail. New agents should consult these in the order listed when the SKILL.md
alone is not enough:
- `references/media-live-flow-create-verification.md` — exact pre-create and post-create verification steps for uploaded media live tests.
- `references/ad-detail-media-parser.md` — parser mismatch where uploaded media exists in final UI but typed creative tools report `has_media=false` / `show_picture=true`; includes patch shape and CPM `+80%` caveat.
- `references/media-source-and-detail-parser-pitfalls.md` — media_path vs attachment ambiguity, detail-parser false negatives, and UI `cpm_extra` authority.
- `references/login-policy.md` — login_required halt-and-ask branch, phone-format pitfalls, OAuth-style flow
- `references/login-flow-patterns.md` — phone persistence override (option 3), approval TTL pitfall, phone_required hard stop, session_active vs logged_in, full state-machine stop-rules table
- `references/login-flow-process-isolation.md` — cross-process singleton / ApprovalRegistry loss (root cause #2 of `invalid_confirmation`), atomic login-flow script template (reissue+apply+wait in one process), adapter-level DOM fill+click via `adapter.browser.evaluate` (within-Operating-Discipline workaround that does NOT require `approve Telegram Ads debug fallback`), state-classifier divergence matrix between `login_check` and `status`, accumulated-process profile lock side-effect and approval scope for cleanup
- `references/acceptance-readonly-protocol.md` — Mode A/B/C recipe, FakeAdapter wiring, verdict template, red-flag list
- `references/typed-wrapper-event-loop-lifecycle.md` — persistent event loop thread, structured error preservation, manager API compatibility, wrapper-level tests
- `references/typed-wrapper-arg-name-collision.md` — `name` collision pattern, "fix in both places" workflow, repro code, regression tests
- `references/media-placement-compatibility.md` — placement support matrix, search/bot media guard behavior, block-before-upload/checkAdPost expectations
- `references/media-channel-create-postcreate-discrepancy.md` — channel-targeted uploaded-photo create flow: CPM minimum mismatch, post-create `has_media=false` / `show_picture=true` discrepancy, verification/reporting pattern
- `references/creative-options-and-cpm-modifiers.md` — show_picture / upload_media / emoji audit, media_path drop bug, CPM modifier table, patch recipes
- `references/code-patch-live-verification-gotcha.md` — after a code patch, unit tests pass but live `telegram_ads_*` calls return old behavior until gateway restart; how to validate pre-restart with FakeAdapter
- `references/typed-wrapper-envelope-diagnostics.md` — empty INTERNAL_ERROR 3-hypothesis split
- `references/telegram-tools-ui-registry-chain.md` — `/tools` UI vs LLM schema vs registry 5-layer chain
- `references/feature-branch-integration.md` — additive-merge procedure after `git fetch` of a `fix/<branch>`
- `references/hermes-telegram-ads-update-protocol.md` — exact pre-installation / pre-mutation byte-diff protocol
- `references/branch-preflight-recipe.md` — `git ls-remote` / `marshal.load` / reflog archaeology for "branch not found"
- `references/active-runtime-patch-verification.md` — on-disk patch ≠ active runtime (gateway caches old imports); pre-mutating-action gate, live test plan template, restart scope discipline, and "patch not live" diagnostic recipe. Use when a code patch is applied to the editable-install package but a live `telegram_ads_*` call still returns old behavior.
- `references/legacy-action-mapping.md` — typed-tool name → legacy `action=` enum mapping
