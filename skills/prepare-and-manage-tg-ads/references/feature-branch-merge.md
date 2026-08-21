# Upstream feature-branch integration (conservative additive merge)

When integrating a new `feature/*` branch from `telegram-ads-upstream` into the **installed package** at `~/.hermes/profiles/deepseek/plugins/packages/hermes_telegram_ads_pkg/`:

## Why "Strategy A" matters

The installed package is **not a git repo** — it was copied, not cloned. The
`browser_manager.py` and `workflows/` directory are **local workflow-layer
additions** (from `~/.hermes/profiles/deepseek/plugins/...`) that do not
exist in upstream `master` or any `feature/*` branch. Applying `git pull`
or `cp -r feature-branch/*` would clobber these additions.

`advisory: run `git diff feature-branch..master -- <file>` (in a clean clone)
to classify each upstream file as:

- **New (A-side only)** — safe to copy: `hermes_tools.py`, `schemas.py`,
  `docs/*`, `examples/hermes_snapshot.py`, `tests/fake_adapter.py`,
  `tests/test_hermes_*.py`, `skills/operate-telegram-ads/SKILL.md`.
- **Modified upstream, no local edit** — review: `pyproject.toml` (lint
  rules, not breaking), `browser.py` (import-style refactor).
- **Modified upstream, local edit (workflow-layer)** — **DO NOT copy
  upstream version**. Files: `adapter.py` (has `parse_ads()` from workflow
  layer), `pages/account.py` (has `parse_ads_table()` from workflow layer),
  `__init__.py` (imports `workflows`), `parser.py` (callers expect
  `parse_percent` not `parse_pct`), `safety.py`, `api.py`, `audit.py`.

## Procedure

1. **Backup first**: `cp -a $PKG $BACKUPS/hermes_telegram_ads_pkg.<UTC>`.
   Always timestamped, never overwrite prior backups.
2. **Copy A-side files only** (`cp -v $FEAT/<file> $PKG/<file>`).
3. **Patch `__init__.py` manually** — keep installed `from hermes_telegram_ads import workflows` and `workflows.run_workflow` reference in docstring. Add the A-side `from hermes_telegram_ads.hermes_tools import (...)` block. Extend `__all__` with A-side names.
4. **DO NOT copy** upstream versions of workflow-supporting files. If upstream `adapter.py` removes `parse_ads()`, installed `_snapshot.py` breaks.
5. **Add conftest fixtures additively** — A-side adds `tool_config`, `fake_adapter`, `toolset` fixtures. If a test fails with `fixture 'toolset' not found`, you forgot this step.
6. **Inventory test may fail** on A-side because installed adapter has extras (`parse_ads` etc.). Add them to `DOCUMENTED_EXCLUSIONS` in `tests/test_hermes_inventory.py` with an honest comment explaining the back-compat reason — **do not delete the test entry**, document the gap.
7. **Skill placement**: A-side ships `skills/operate-telegram-ads/SKILL.md` (tool-layer). Install into `~/.hermes/skills/devops/operate-telegram-ads/` to keep it separate from `prepare-and-manage-tg-ads` in `~/.hermes/skills/business-growth/` (which is procedure-layer). Also copy into `<pkg>/skills/operate-telegram-ads/` because A-side `test_hermes_docs.py` asserts on `<pkg>/skills/...` path.
8. **Hermes wrapper** at `~/.hermes/hermes-agent/tools/telegram_ads_typed_tool.py` — separate file from legacy `telegram_ads_tool.py`. Registers each `ToolSpec` from `TELEGRAM_ADS_TOOLS` as a separate registry entry under toolset `telegram_ads_typed`. Uses `BrowserProfileManager.shared().acquire_adapter()` factory to avoid creating a second Playwright profile.

## Cross-profile guard reality

`patch` and `write_file` to `~/.hermes/profiles/deepseek/...` from a session
running under profile `default` triggers a soft guard. To proceed:

- Use `cross_profile=True` flag on each `patch` / `write_file` call.
- **Per-patch approval, not per-session** — if integrating 5 files, that
  may require 5 explicit user approvals. Don't try to bundle.
- The guard is a soft defense-in-depth layer, not a security boundary; the
  `terminal` tool can bypass it (don't — keep the guard active).

## Tests to run

```bash
# A-side hermes tests (78 tests)
cd $PKG && pytest tests/test_hermes_*.py -x

# Core installed tests (90 tests) — verifies workflow layer still works
cd $PKG && pytest tests/test_audit.py tests/test_config.py \
    tests/test_payloads.py tests/test_safety.py tests/test_types.py \
    tests/test_api_contracts.py
```

`tests/test_parser.py` is **known-broken in installed package** (imports
`parse_pct` but installed `parser.py` exposes `parse_percent`). Pre-existing
issue from May 28, unrelated to A-side integration. Fix separately.

## Toolset registration (separate approval)

Adding `telegram_ads_typed` to `~/.hermes/config.yaml` `toolsets.default`
is a **production config edit** requiring explicit approval. Wrapper file
itself can be created without config edit — tools are registered in
process memory at import time, just not exposed to the LLM until the
config list includes the toolset name.
