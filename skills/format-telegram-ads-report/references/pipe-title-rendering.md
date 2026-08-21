# Pipe-Title Rendering Reference

Session-condensed reference for the markdown rendering rules around pipe
(`|`) characters in account / ad / campaign titles. Captures the demonstrated
failure mode and the deterministic safe render templates.

## Demonstrated failure mode (2026-06-06)

When a markdown table cell contains a `|`, the auto-rewriter splits the row
even when the `|` is escaped as `\|`. With the source row:

```text
| title | account_ref | currency | account_type | balance |
|---|---|---|---|---|
| Example Bot \ | Short Clips | acc_3 | STARS | Bot Account | 549 |
```

…the rewritten output produces a 5-column-shift bug:

| Rendered field | Intended value | Actual rendered value |
|---|---|---|
| `title` | `Example Bot \| Short Clips` | `Example Bot \` |
| `account_ref` | `acc_3` | `AI Shorts Maker` |
| `currency` | `STARS` | `acc_3` |
| `account_type` | `Bot Account` | `STARS` |
| `balance` | `549` | `Bot Account` |

The `title_display` field (with pipe-escaped variant) is necessary but not
sufficient — in some renderers the escape is also reinterpreted.

## Safe render templates (deterministic)

For any account / ad / campaign whose `title` contains a `|`, use one of these
two templates. Do **not** use a markdown table.

### Template A — code block (preferred)

```text
title:           Example Bot | Short Clips
account_ref:     acc_3
currency:        STARS
account_type:    Bot Account
current balance: 549 STARS
list_accounts balance: 0 STARS
mismatch:        yes
```

Use this for:
- Per-cabinet / per-ad detail blocks
- Audit entries
- Anywhere the row needs to be copy-pasteable or diff-able

### Template B — bullet list

- title: `Example Bot | Short Clips`
- account_ref: `acc_3`
- currency: STARS
- account_type: Bot Account
- current balance: 549 STARS
- list_accounts balance: 0 STARS
- mismatch: yes

Use this when the surrounding report is itself in bullet form and a code block
would look out of place.

For titles **without** `|`, markdown tables are acceptable. The code-block
format is used in this skill for visual consistency across the audit shape.

## Per-cabinet audit shape (canonical)

A multi-cabinet balance audit looks like:

```text
### 1. `Example Personal` (Personal Account, TON 💎)

title:           Example Personal
account_ref:     acc_2
currency:        TON
account_type:    Personal Account
current balance: 16.75 TON
list_accounts balance: 0.00 TON
mismatch:        yes

### 2. `Example Bot | Short Clips` (Bot Account, STARS ⭐️)

title:           Example Bot | Short Clips
account_ref:     acc_3
currency:        STARS
account_type:    Bot Account
current balance: 549 STARS
list_accounts balance: 0 STARS
mismatch:        yes
```

Each cabinet gets its own h3 header + code block, never a row in a shared
table. The header itself uses backticks (not a table cell) so the pipe in the
title is rendered verbatim without splitting.

## Cross-reference to other rules

- The canonical balance source order is in
  `operate-telegram-ads-cabinet/SKILL.md` §"Pre-Mutation Re-Confirmation
  Checklist" and the parent skill's `references/canonical-source-and-pipe-title-handling.md`.
- Transaction semantics (`transfer_to_ad` = budget allocation;
  `returned_from_ad` = budget release; `payment_for_views` = only real spend)
  are in the parent `SKILL.md` §"Canonical Balance and Transaction Semantics".
- The "Never Say" rules #1 ("spent X of Y"), #2 (no TON+STARS aggregation), and
  the new implicit #17 ("returned = spent" forbidden) apply to pipe-titled
  cabinets exactly as to non-pipe-titled ones.

## Pointer into the parent skill

The authoritative rules are in `SKILL.md`:
- §"Account / Ad / Campaign Title Safety (mandatory)"
- §"Common Pitfalls" items #15–#18
- §"Verification Checklist" items for pipe titles, balance source, and
  transaction semantics
