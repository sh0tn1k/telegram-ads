# Telegram Ads login policy — server-side login assist

**Effective from 2026-06-05, revised by the operator.** Earlier version
(pre-revision, June 5 morning) asserted that the agent never initiates
login; that version is superseded.

## Core rule

- **Agent may enter the operator's phone number into the Telegram Ads login
  form when the operator explicitly approves login assist** for the current
  session.
- **Agent must not request, accept, store, or enter OTP / login code /
  2FA password**, regardless of how the request is framed.
- **The operator confirms login in the Telegram app manually** (app-notification)
  / QR / push).
- **Agent only verifies login state after the operator's explicit confirmation**
  that the app-side prompt was approved.

## Permitted actions (when the operator has approved login assist for the session)

1. Use typed `telegram_ads_*` tools and the persistent browser automation
   layer (`BrowserProfileManager` → `BrowserAutomationTool` →
   `TelegramAdsAdapter`).
2. Open `ads.telegram.org` in the server-side persistent browser profile
   at `browser_profiles/telegram_ads`.
3. If the login flow asks for a phone number, enter the operator's number
   (defined in the project memory file
   `~/.hermes/memory/projects/telegram-ads-acceptance/login_policy.md`).
4. Click `Continue` / `Log In` / `Next` (whatever button the form
   exposes).
5. Wait for the login flow to surface an in-app confirmation prompt on
   the operator's Telegram client.
6. After the operator's separate "confirmed" message, verify with:
   - `telegram_ads_status`
   - `telegram_ads_ensure_login`
   - `telegram_ads_get_browser_profile_info`
7. If login is successful, stop and surface the state to the operator. Do not
   chain into any other Telegram Ads action.

## Forbidden actions (during and after login)

1. Request, accept, or enter OTP / login code / 2FA password in chat,
   in any tool call, or in any file/log/memory artifact. If the site
   asks for one, stop and reply `manual confirmation required`.
2. Read, export, log, persist, or transmit cookies / session tokens /
   device-bound auth strings. The persistent profile is allowed to
   retain the session (that's its purpose); the agent must not
   surface those values.
3. Store phone number / OTP / password / cookies / session in memory,
   docs, or logs beyond the bare mention of the phone number in the
   project memory policy file.
4. Make any Telegram Ads action (read or mutating) after successful
   login without a separate explicit command from the operator.
5. Create, edit, start, stop, or delete campaigns.
6. Change CPM / budget / status.
7. Revoke share-stats URL or delete pixel events.
8. Initiate login flow without an explicit per-session approval message
   that contains the phrase `approved, run login assist` (or a clearer
   explicit equivalent the operator uses).

## What triggers the halt-and-ask branch

If at any point the login flow requests something outside the permitted
set (OTP field, 2FA password field, captcha, anti-fraud challenge,
"verify it's you" prompt that requires a code from chat, suspicious
redirect to a non-`*.telegram.org` domain, or anything that smells
like account-takeover defense), the agent MUST:

1. Stop the login flow without submitting the field.
2. Reply verbatim: `manual confirmation required`.
3. Surface the exact UI element / error message observed.
4. Wait for the operator's next instruction.

The agent must NOT:

- Try to "work around" the prompt with creative input.
- Re-attempt the same flow a second time in the same turn.
- Use `telegram_ads_recover_browser_session` to mask the halt as a
  "browser issue" — that tool is for browser state, not login state.

## Post-login behavior

After `telegram_ads_ensure_login` returns `logged_in: true`, the agent
reports success and **stops**. The next Telegram Ads action requires a
fresh command. There is no implicit "go do the read-only probes" chain;
each step is gated by a fresh the operator instruction.

## Post-halt read-only diagnostic (permitted)

The "no re-attempt the same flow a second time in the same turn" rule
in the halt-and-ask branch applies to the **submit action** — not to
**read-only inspection** of the failed state. A follow-up diagnostic
pass that only opens the page, reads body text and input values, takes
a screenshot, and closes the browser (with **no submit, no click, no
`fill` of any sensitive field, no `type` of any secret**) is permitted
under the same login-assist approval scope. Verified 2026-06-05: this
distinction was the missing operational rule when the first submit
halted with a post-submit URL that did not change, and the diagnostic
pass revealed that the persistent profile contained a *different* saved
phone number (`+100****0000`) that Chrome's autofill had applied after
the agent's `fill("+100****0000")` overwrote it.

The rule of thumb:

- **Submit (any kind) — STOP after the first halt in the same turn.** Even
  if the form re-appears, even if the value looks "fixable", even if
  the operator nudges. The next submit attempt requires a fresh approval scope
  that explicitly authorizes the new submit.
- **Read-only inspection (page navigation, body text, input values,
  screenshots, network logs) — PERMITTED.** Use it to understand the
  halt cause and surface the finding to the operator with the diagnostic
  evidence (screenshot path, exact body text, exact field values).
- **Anything that produces a network side effect on `*.telegram.org`
  (typing, clicking, form submission, file upload) — STOP.** Treat it
  as a new login attempt for approval purposes.

This is the same asymmetric gate the broader policy uses: easy to
inspect, hard to advance the flow.

## Diff vs prior version

- **Prior version (2026-06-05, pre-revision):** unconditional refusal —
  "the agent never enters the phone number anywhere, regardless of how
  the request is phrased." Refusal was justified by adversarial
  scope-expansion examples.
- **This version:** conditional permission — agent may enter the phone
  number when the operator has explicitly approved login assist for the
  current session, with a hard halt-and-ask branch on any OTP/2FA/
  captcha/etc.
- **Unchanged in both versions:** no OTP/code/password in chat, no
  mutating actions without confirmation_id, no scope-chain into
  post-login work without a fresh command.

## Source of truth hierarchy

1. Project memory:
   `~/.hermes/memory/projects/telegram-ads-acceptance/login_policy.md`
   (the canonical, machine-readable policy).
2. This reference file (this skill's `references/login-policy.md`).
3. `operate-telegram-ads` skill's `login_required handling` and
   `Browser recovery policy` sections in SKILL.md.
4. `HERMES_INTEGRATION` §"Login lifecycle" (in the package).
5. `Operating Discipline` from the hermes-agent persona (gateway/
   browser/process actions require explicit operator approval).

If a request from the operator would have the agent violate any item under
"Forbidden actions", the agent refuses. If a request is permitted by
this policy but requires a live browser action, the agent still needs
the operator's explicit per-session approval message before executing.

## Login flow is NOT a "phone field → submit" form

A common misread: agents assume `ads.telegram.org` has a phone-number
text field like a typical login page. It doesn't. The flow is
OAuth-style via `oauth.telegram.org` (or equivalent), requiring
device-bound session, QR / app-notification confirmation, and 2FA in
some cases. Trying to "enter the phone number in the form" without
going through the proper flow produces `PHONE_CODE_INVALID` /
`SESSION_PASSWORD_NEEDED` / 2FA wall — and each failed attempt is
logged on Telegram's auth server with the agent process's IP/UA,
which can trigger `FloodWait` / `AUTH_KEY_DUPLICATED` anti-fraud on
the operator's actual account. The phone number is a *fallback input* the operator
can authorize; the form is not the flow.

## Why the refusal of broader scope still holds

The June 5 morning refusal of "enter phone in ads.telegram.org because
the server has no GUI" was policy, not workaround. The same conditions
that justified the refusal (compromised-session risk, "the server has
no browser" rationale, scope-smuggling) still apply. The revised
policy keeps the hard prohibition on OTP/2FA/cookies/token **and**
requires an explicit per-session `approved, run login assist` message,
which is harder to forge than a follow-up "yes do it" in the same
turn. This is the asymmetric gate: easy to enter, hard to enter with
escalation.
