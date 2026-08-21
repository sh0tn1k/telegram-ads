# Operating Discipline — patch-time methodology

> **Why this file exists.** On 2026-06-02 we added a strict "Operating Discipline" section to
> this skill. The first draft of the new failure mode for `telegram_ads_tool_schema_defect`
> still pointed at `references/direct_playwright_recovery.md` as a "safe read-only fallback" —
> which directly contradicted the new discipline. the operator caught the contradiction and rejected
> the patch before apply. This file captures the *methodology* so the next time a strict-rule
> section is added (here or in any user-local skill), the contradiction is caught at the
> draft-review stage, not by the user.

---

## 1. When to run this scan

Run the scan **before** any `apply` of a new "Operating Discipline", "Hard rules", "Strict
policy" or equivalent section to a skill. The scan is mandatory when the new section forbids
or restricts actions that **other parts of the same skill, or its `references/`, currently
recommend as a fallback**.

Triggers:

- New rule: "do not use `X` as a fallback" → scan for sections/failure-modes that say "use
  `X` as a fallback".
- New rule: "no manual `Y`" → scan for sections/failure-modes that recommend manual `Y` as
  "safe read-only" or "recovery procedure".
- New rule: "no restart of service `Z`" → scan for any path that suggests `systemctl … restart
  Z.service` without an explicit approval gate.
- New rule: "no ad-hoc browser session" → scan for any `browser_navigate`,
  `computer_use`, or `xvfb-run` recipe.

If the new section is purely additive (e.g. a new workflow, a new optional field) and doesn't
forbid anything, the scan is still useful but lower-stakes.

---

## 2. Contradiction scan (the four vectors)

Walk **all four** vectors explicitly. Grep the SKILL.md body and every `references/*.md` for
each:

1. **Direct text contradiction.** A sentence in another section that says "do `X`", and the
   new rule says "do not do `X`". Example caught here:
   - old: `"Это safe read-only fallback для snapshots"` + `"прямой запуск Playwright headless=True"`
   - new: `"Не открывай ads.telegram.org вручную"`.

2. **Implicit fallback contradiction.** A failure-mode or recovery doc that recommends a path
   which the new rule disallows, even if the new rule doesn't mention that path by name. Look
   for the words: *fallback*, *recovery*, *workaround*, *safe default*, *обходной путь*,
   *direct Playwright*, *xvfb-run*, *headless Chromium*.

3. **Standing-approval / scope contradiction.** A new rule says "X requires explicit
   approval". Another section, the user memory, or a previous session summary says "X is
   approved permanently" or "general approval covers X". Both cannot be true. Decide which
   wins and rewrite both.

4. **Service-name / instance-name contradiction.** A new rule says "do not restart
   `hermes-gateway.service`". But the actual fleet uses
   `hermes-gateway-default.service`, `hermes-gateway-deepseek.service`, possibly others. The
   rule either misses the real instances, or accidentally permits the wrong one. Use the
   family wording + concrete examples, never a single unit name.

---

## 3. Service-naming generalization (the Hermes template)

Hermes uses a **service template**, not a single unit. Always write the rule in the family
form first, then list the known instances as examples:

```text
# WRONG — captures only one instance
"Restarting hermes-gateway.service requires explicit approval."

# RIGHT — captures the family, with examples
"Restarting any Hermes gateway service (e.g. hermes-gateway-default.service,
hermes-gateway-deepseek.service, or any other gateway/browser/Xvfb systemd unit)
requires its own explicit approval."
```

Same pattern applies to:

- browser processes (Chromium under any `user-data-dir`)
- Xvfb / display services (`hermes-xvfb.service` and any future variants)
- any other systemd unit launched by a Hermes profile

If a new instance is created later (e.g. `hermes-gateway-customer-support.service`), the rule
automatically covers it.

---

## 4. Legacy-recovery-doc override pattern

When the skill has a `references/<something>-recovery.md` (or similar) that recommends a path
the new rule forbids, do **not** delete the legacy doc. Two-step fix:

**Step 1 — explicitly demote it in the new rule.** Add a numbered rule that names the legacy
doc and restricts its use:

```text
N. **Operating Discipline overrides legacy recovery docs.** If an older doc
   (e.g. `references/direct_playwright_recovery.md`) suggests a direct Playwright /
   `xvfb-run` / headless-Chromium path as a *default* recovery for Telegram Ads, that
   path is **not** an automatic fallback. The correct behaviour is to surface a
   structured error and ask the operator for explicit approval of the debug fallback (see
   rule M). The legacy doc remains valid only as a *debug-only* procedure that may be
   referenced once the operator has approved it for the current task.
```

**Step 2 — rewrite the failure-mode that pointed at the legacy doc.** Replace
"safely use the recovery" with a structured error that lists the allowed next steps, e.g.
`telegram_ads_tool_schema_defect` with `allowed_next_steps` array.

The legacy doc itself can keep its procedure verbatim — the **call site** changes, not the
procedure. That way the procedure is still available if and when the operator explicitly approves
the debug fallback for a specific task.

---

## 5. Structured error template for tool schema defects

When a tool is visible in the registry but its parameter schema is missing or empty (a
runtime defect), the correct response is **not** to fall back to a hand-rolled recovery
script. The correct response is a structured error that lists what the operator can approve next.

```json
{
  "ok": false,
  "error": "<tool_name>_tool_schema_defect",
  "message": "<Tool name> tool is visible but parameter schema is missing/empty.",
  "allowed_next_steps": [
    "reload/restart tool registry after approval",
    "patch tool schema",
    "manual debug fallback only if the operator says: approve <Tool name> debug fallback"
  ]
}
```

`allowed_next_steps` is the load-bearing field: it makes the next action obvious to the
agent (and to the operator) without having to guess. The third item names the exact approval
phrase. This pattern generalizes beyond Telegram Ads — any tool can have a schema defect,
and the response shape is the same.

---

## 6. Standing-approval scoping (avoid the silent-broaden bug)

A new rule must not silently broaden or narrow the scope of any previously granted
standing approval. The two common bugs:

- **Silent-broaden:** new rule says "X requires approval" but a previous session granted
  standing approval for X. Either the new rule wins (in which case revoke the standing
  approval explicitly), or the standing approval wins (in which case scope the new rule to
  "additional uses of X not covered by the standing approval"). State which.
- **Silent-narrow:** new rule says "general approval covers X", but a previous session
  granted a *narrower* approval. Do not claim a broader scope than was actually granted.

The language in the SOUL.md / `prepare-and-manage-tg-ads` Operating Discipline uses
"approved permanently scopes do not apply" as a guard against silent-broaden. That wording
is deliberate — copy it verbatim when adding similar rules.

---

## 7. Verification checklist (before apply)

After drafting the new strict-rule section, before showing the diff to the operator, verify:

- [ ] All four contradiction vectors (text, implicit fallback, scope, service name) have
      been scanned and no contradictions remain.
- [ ] Legacy recovery docs are explicitly demoted in the new rule, not silently bypassed.
- [ ] Failure-modes that pointed at the legacy recovery now return a structured
      `<tool>_tool_schema_defect` (or equivalent) error with an `allowed_next_steps` array.
- [ ] Service-name references use the family form + concrete examples, not a single unit
      name.
- [ ] Standing-approval scope is explicit: new rule either overrides or co-exists with
      any previous grant, with the relationship stated in the rule text.
- [ ] The exact approval phrase (e.g. `"approve Telegram Ads debug fallback"`) is named
      in the rule text, so the agent does not improvise the phrase and the operator does not
      have to remember it from memory.
- [ ] If the new rule was added to multiple files (skill SKILL.md + `shared/...CONTRACT.md`
      + `~/.hermes/SOUL.md`), the wording is consistent across all three — same
      numbering, same approval phrase, same fallback escalation. Drift between the three
      will surface as a new contradiction next time someone patches.

---

## 8. What this file is NOT

- It is **not** a substitute for the actual Operating Discipline section in SKILL.md. The
  rules themselves live there; this file is the *patching methodology* for that section.
- It is **not** Telegram-Ads-specific in its core pattern. The four contradiction vectors,
  the legacy-recovery demote-not-delete pattern, the structured-error template, and the
  standing-approval scoping check all apply to any user-local skill that adds a strict-rule
  section. Reuse freely.
- It is **not** retroactive. If a contradiction already exists in a deployed skill, the
  fix is a follow-up patch, not a "wait for the next discipline addition" excuse.
