# Login flow patterns (phone override, TTL, phone_required, state machine)

Operational recipes for the Telegram Ads login flow that don't fit in
`SKILL.md` prose. Updated 2026-06-06 from the post-install + login-session
acceptance runs.

## Phone persistence override (file-backed phone)

### When to offer this

Default Telegram Ads contract masks the phone in every output (chat,
approval form, audit log, pending confirmation view) and never persists
it in the clear. Cookie expiry on the persistent profile then forces
the agent to re-ask for the phone every cycle — which the operator finds
friction. He may opt in to a file-backed phone **explicitly**,
overriding the default. The agent must:

1. Refuse to write the file by default.
2. Offer the option-3 menu (file-backed plaintext) only when the operator
   has expressed the "сохранить" intent.
3. Require the operator's explicit override message that names
   "override" and acknowledges the "не логируй phone" guard.
4. Write the file with the discipline below.
5. Save the exact override text into `meta.json` for auditability.

### Files

| Path | Mode | Purpose |
|---|---|---|
| `~/.hermes/telegram_ads_phone.txt` | `0600` | One line, E.164 raw phone. Read at use time only. |
| `~/.hermes/telegram_ads_phone.meta.json` | `0600` | `created_at`, `chmod`, `override_guard` (the exact the operator override text), `usage_policy`, `display_mask`, `consent_message` (verbatim quote of the override). |

Both files are under `~/.hermes/` (Hermes home), **never** inside the
package source repo or inside `~/.hermes/hermes-agent/` (which is a
git repo). `~/.hermes/` is the right location for cross-session
secrets that should not appear in any commit.

### Write discipline

```bash
echo -n '+100****0000' > ~/.hermes/telegram_ads_phone.txt
chmod 600 ~/.hermes/telegram_ads_phone.txt
```

No trailing newline. The `-n` is important — `echo '+...'` adds a
trailing `\n` that some downstream formatters mangle.

### Read discipline

```python
from pathlib import Path
from hermes_telegram_ads.login_flow import mask_phone

PHONE_FILE = Path("/home/hermes/.hermes/telegram_ads_phone.txt")
phone_raw = PHONE_FILE.read_text().strip()    # "+100****0000" — never print
display   = mask_phone(phone_raw)              # "+1********00" — safe to print
```

The raw value stays in memory only. `mask_phone()` is the package's
own helper that produces the `+CC****XX` form (first-2 country code
+ `*` × 8 + last-2 digit).

### Submit discipline

Always submit via `telegram_ads_login_submit_phone` (approval-gated,
`SENSITIVE_ACCOUNT_ACCESS`). The agent's `apply_approved_action` flow
must surface the masked form in `human_summary` only:

```json
{
  "approval": {
    "human_summary": "Submit phone +1********00 to the Telegram Ads login form. Telegram will send a login approval to your app; the agent will NOT enter an OTP code.",
    "confirmation_id": "...",
    "expires_in_seconds": 300
  }
}
```

### Revocation

```bash
rm ~/.hermes/telegram_ads_phone.txt ~/.hermes/telegram_ads_phone.meta.json
```

After revocation, the next cookie expiry triggers a per-request phone
prompt. The agent should not silently fall back to "no phone" — the
funnel needs a phone in the form to advance.

## Approval TTL on multi-step flows

`telegram_ads_login_submit_phone`, `telegram_ads_login_start`, and
all mutating tools (CPM, budget, start/stop, create/edit/delete) return
`confirmation_id` with `expires_in_seconds: 300` (5 min) for
single-confirmation, `1200` (20 min) for some mutating variants, and
require a second `confirmation_id` for destructive ops.

### Failure mode

```json
{
  "status": "error",
  "error": "invalid_confirmation",
  "message": "No pending approval for confirmation_id 'b2c00edc-ec47-4736-96e2-a6dae0b5acfa'. Issue one via the mutating tool or telegram_ads_prepare_approval_request first."
}
```

### Mitigation

1. Re-issue the approval request **as close as possible to** the apply
   call, not at the start of a multi-turn flow. In the same turn that
   the operator says "approved", re-issue if more than ~60s have passed.
2. Surface the TTL in the approval ask so the operator knows the window.
3. Don't re-ask the same approval twice — issue the new one and apply
   in the same Python block to minimize round-trips.
4. For really slow flows, call
   `telegram_ads_prepare_approval_request(tool, params)` instead of
   the mutating tool — it issues the confirmation without consuming
   it on a no-op (avoids the "expired before apply" race).

## `state=phone_required` hard stop

The login funnel states in `login_flow.LoginState` progress roughly:
`UNKNOWN` → `AUTH_PAGE` → `PHONE_REQUIRED` → `APP_APPROVAL_PENDING` →
`LOGGED_IN` (with `CODE_REQUIRED` branching off `PHONE_REQUIRED` for
accounts that need OTP, and `LOGIN_TIMEOUT` /
`PROFILE_LOCKED` / `BROWSER_BROKEN` as error branches).

`PHONE_REQUIRED` is a hard prerequisite to `APP_APPROVAL_PENDING`.
Telegram does not send an app-approval prompt to the user's phone
until the form has a phone number. Therefore:

- **Do not** call `telegram_ads_login_start` while
  `state=phone_required` and expect an app prompt. The funnel will
  not advance and the call will return `state=phone_required` or
  `state=timeout` (from `login_wait` polling empty).
- **Do** call `telegram_ads_login_check` first, then stop and ask
  the operator per the SKILL.md "stop-and-ask template".
- **The two valid exits** are A (manual browser entry + Telegram
  app confirm) or B (approval-gated `login_submit_phone` +
  Telegram app confirm + `login_wait` poll).

## `session_active` vs `logged_in`

`session_active` is a Playwright-level signal: the browser session
is up and the persistent profile is loaded. It does NOT mean
Telegram Ads auth cookies are present or valid.

The Telegram-auth signal is `logged_in` + `state==logged_in`.

A common read mistake: `telegram_ads_login_check` returns
`session_active=true` while `logged_in=false` and
`state=phone_required`. The agent must surface both signals in
status reports and never claim "session is healthy" from
`session_active` alone.

## Login flow stop-rules (summary)

| Condition | Action |
|---|---|
| `state=logged_in` | Continue with the original task. |
| `state=phone_required` | Stop. Present A/B/C menu. |
| `state=app_approval_pending` | Poll `login_wait` up to 2 min. If still pending, present A/B/C menu. |
| `state=code_required` | Stop. Halt-and-ask: "manual confirmation required". Surface exact UI element. Never enter OTP. |
| `state=timeout` | Stop. Present A/B/C menu + ask if app prompt was approved. |
| `state=profile_locked` | Stop. Surface `recovery_hint`. Do NOT kill processes or delete the profile. |
| `state=browser_broken` | Call `telegram_ads_recover_browser_session` once. If still broken, request explicit gateway/browser restart. No loops. |
| `state=unknown` | Run `telegram_ads_status` and `telegram_ads_get_browser_profile_info` to refine. If still unknown, surface to the operator with all available diagnostics. |
| `state=auth_page` | Normal idle state for a session that's been navigated to `/auth?to=account`. Continue with `login_start` (or wait for the operator instruction). |
