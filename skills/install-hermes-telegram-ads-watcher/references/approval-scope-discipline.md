# Approval-scope discipline — the closed-list rule

the operator's approval system treats every approved tool call as a separate side-effect surface, even if the additional calls are read-only. This reference captures the workflow pattern that prevents scope drift.

## The rule

When the operator says:

> "Approve AR-X. Allowed: one tool call T1 with parameters P. Not approved: T2, T3, T4."

The execution contract is:

1. ✅ Execute exactly `T1(P)`. Nothing else.
2. ✅ Use only the data returned by `T1` to write the report.
3. ❌ Do NOT execute `T2`, `T3`, `T4` even if they are read-only.
4. ❌ Do NOT execute `T1` with different parameters even if "close".
5. ❌ Do NOT augment the report with data from a different tool "while I'm at it".

## Why this matters

the operator explicitly flagged this in the 2026-06-17 session:

> "the approval was for exactly one telegram_ads_login_check(), but the report says two additional read-only tools were also called (telegram_ads_status, telegram_ads_get_browser_profile_info). This caused no harmful mutation, but treat it as scope drift."

The reasoning behind the strictness:

- **Side-effect surface.** Even a "read-only" `telegram_ads_*` tool call initiates Chromium IPC, navigates to ads.telegram.org, generates a server-side request log entry, and consumes a request slot. Each call is a separate action with its own risk profile.
- **Auditability.** If something goes wrong (cookie expires, login is challenged, account is flagged), the operator needs to know exactly which tool call could have caused it. Extra calls break the audit chain.
- **Trust.** Once you "helpfully cross-verify" with extra calls, the operator can no longer trust that the closed-list contract means what it says. Future approvals would carry that asterisk.

## When you want more data

The correct pattern is to **propose a separate approval request**:

```text
Report from approved T1: [results].
For verification, I would benefit from running T2 (read-only, X seconds, no side effects beyond T1's).
Proposed AR: AR-X-VERIFY — run exactly one T2 with parameters P.
Not approved: do not run T2 yet.
```

Then wait for the operator to issue AR-X-VERIFY (with its own scope), and execute that one separately.

## Pattern for self-check before executing

Before every tool call under an approved scope, run this checklist:

1. Is this exact command/tool in the approval's "Allowed" list?
2. Are my parameters exactly as approved?
3. Have I already executed the approved number of times?
4. Is there any temptation to "also do X" or "while I'm at it"?

If any answer is "no / yes / yes", stop. Either propose a separate AR or skip.

## Applies to all skill work, not just Telegram Ads

This is a general the operator workflow preference. It applies to:

- Telegram Ads tool calls (smoke, login-check, status, recovery)
- Browser/process signals (kill, pkill, SIGTERM)
- Lockfile operations (delete, mv, chmod)
- DB writes (any DB: watcher, ontology, sleep, project memory)
- Memory writes (MEMORY.md, USER.md, project memory, wiki, KC proposals)
- File edits (code, config, systemd units)
- Git operations (add, commit, push, pull, merge, rebase, checkout)
- External actions (ads launch/stop, broadcasts, deploys, payments, refunds)

In every case, "read-only" is not an excuse for an unapproved extra call.

## Notes for delegating agents

If you delegate a subagent (via `delegate_task`) under an approved scope, propagate the closed-list rule in the subagent's `context`. Subagents don't inherit scope discipline — they inherit tool availability. Always include the approval's exact "Allowed" / "Not approved" / "Expected output" verbatim in the subagent's prompt.
