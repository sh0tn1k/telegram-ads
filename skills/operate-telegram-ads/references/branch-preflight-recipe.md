# Branch pre-flight & orphan-artifact recovery — recipe book

Two read-only techniques that turn "branch is missing" / "file vanished"
into structured questions with evidence, instead of blind retries.

## 1. Branch existence pre-flight

When the operator says "branch X is pushed, pull it", the right first move is
**never** `git pull`. Run this 6-step pre-flight first. It is read-only,
takes ~10 seconds, and gives the operator a concrete A/B/C/D/E menu to pick from
instead of a vague "branch not found".

```bash
# Step 1 — protocol-level ref query (not the index)
git ls-remote origin 'refs/heads/<branch>'        # exact match
git ls-remote origin 'refs/heads/<prefix>*'      # prefix wildcard
git ls-remote upstream 'refs/heads/<branch>'     # also check upstream
git ls-remote origin                              # full ref list

# Step 2 — local-only sources
git branch -a | grep -E "<branch>|<fuzzy-substring>"
git worktree list
git stash list
git tag | grep -iE "<fuzzy-substring>"
git reflog -10 | grep -iE "<fuzzy-substring>"

# Step 3 — sibling branches (when exact match is empty)
git branch -a | grep -F "<branch>"                # exact
git branch -a | grep -iE "<substring>"            # case-insensitive
git branch -a | grep -E "fix/<keyword>"           # siblings in same namespace
```

**Decision matrix:**

| Step 1 result | Step 2 result | Likely cause | Menu option |
|---|---|---|---|
| matches on origin | (any) | Branch is on `origin`, normal fetch path | A: typo, B: wrong remote |
| matches on upstream only | (any) | Branch is on upstream but not your fork | B: add upstream, or A: typo |
| empty everywhere | matches in step 2 | Branch was local, deleted/merged | E: not a branch, look at git status |
| empty everywhere | empty | Branch was never pushed, or lives elsewhere | B/C/D |
| matches both | (any) | Both forks have it | A: typo, B: pick which remote |

**When to use `web_search` for cross-check:** only if `origin` is a
private repo you'd need to confirm isn't public. Private repos won't
appear in web search, so an empty result is not a final negative — it's
just "ask the operator for the right remote URL".

**Public-ref cross-check queries** (for forks you suspect are public):

```text
site:github.com <org>/<repo> <branch>
https://github.com/<org>/<repo>/branches
https://github.com/<org>/<repo>/pulls?q=is%3Apr+branch%3A<branch>
```

**A/B/C/D/E menu template** (use this structure when reporting back to
the operator — he engaged with the menu on 2026-06-05, picked option B, and the
next round was productive instead of round-tripping on a missing ref):

- **A.** Wrong branch name. Provide the closest sibling branches from
  step 3.
- **B.** Branch lives on a remote not configured here. Provide the exact
  `git remote add <name> <url>` command.
- **C.** Branch lives on a PR that's not in the fork's refs. Provide the
  recipe: `git fetch origin refs/pull/<n>/head:fix/<branch>`.
- **D.** Branch needs to be pushed first from the source side.
- **E.** Not a branch — refers to local working-copy edits. Run
  `git status` and `git diff` to reveal uncommitted changes.

## 2. Orphan `.pyc` introspection

When `__pycache__/<module>.cpython-311.pyc` exists but the `.py` source
is gone (the file was deleted in this commit, the source was force-pushed
away, or the file is branch-only and not on your checkout), the bytecode
still encodes:

- the module's docstring
- the top-level `co_names` (imports + top-level call names)
- the names and arg lists of inner `def` blocks

Decoding it tells you what the deleted/branch-only file did, which is a
strong hint about what the branch is meant to introduce. This is a
legitimate read-only operation — no browser, no adapter, no I/O.

```python
import os, marshal
os.chdir("<repo>")  # or absolute paths in the f.open() below

pyc_path = "<repo>/__pycache__/<module>.cpython-311.pyc"
with open(pyc_path, "rb") as f:
    f.read(16)  # skip 16-byte CPython 3.7+ header (magic + flags + timestamp + size)
    code = marshal.load(f)

# Module docstring
if code.co_consts and isinstance(code.co_consts[0], str):
    print("=== docstring ===")
    print(code.co_consts[0])

# Top-level names (imports + top-level call targets)
print("\n=== co_names ===")
print(code.co_names)

# Inner code objects (functions / lambdas)
print("\n=== inner code objects ===")
for c in code.co_consts:
    if hasattr(c, 'co_code'):
        print(f"  {c.co_name}(args={c.co_varnames[:c.co_argcount]})")
```

**When `marshal.load` raises `EOFError` or `ValueError`:** the header
size is wrong (CPython 3.7+ uses 16 bytes; 3.6 uses 12 bytes; 3.0–3.5
uses 8 bytes). Adjust `f.read(N)` accordingly.

**When the bytecode was compiled under a different Python version than
your interpreter:** `marshal.load` will still work, but the `co_*`
attributes may differ. CPython marshalled bytecode is forward-compatible
within the major version (3.x → 3.y), so a 3.11 .pyc loads fine under
3.11 and 3.12 interpreters.

**Real example from 2026-06-05:** orphan
`tools/__pycache__/telegram_ads_workflow_tool.cpython-311.pyc` revealed
the module was a high-level workflow dispatcher for `snapshot`,
`inspect_ad`, `account_diagnosis` — all read-only — with a renamed
`TelegramAdsBrowserProfileManager` (canonical) and
`BrowserProfileManager` (deprecated alias). That single decode answered
the question "what did this branch add?" without ever needing the source
or the branch fetchable.

## 3. Reflog archaeology

When a branch is "gone" but a commit SHA is suspected (rebase, force-push,
local-only workflow), the reflog is your friend:

```bash
git reflog -20 | grep -iE "<fuzzy-substring>"  # find suspect SHA
git show <sha> --stat                          # resurrect the diff
```

The reflog is local-only (not pushed), survives rebases, and keeps the
last 30–90 days of HEAD motion by default. It's the cheapest "what did
this branch look like" check that doesn't require any remote to be
reachable.

**Combine with `git fsck --lost-found` for unreachable commits:**

```bash
git fsck --lost-found 2>&1 | grep "dangling commit"
# → "dangling commit <sha>"
git show <sha> --stat
```
