# Feature-branch integration — `telegram-ads-upstream`

When the operator asks to integrate a `feature/*` branch from
`https://github.com/example/telegram-ads-upstream` into the
**installed** package (which lives at
`~/.hermes/profiles/deepseek/plugins/packages/hermes_telegram_ads_pkg/`),
use this procedure. The installed package has local additions (notably
`browser_manager.py` and `workflows/`) that do **not** exist in upstream
branches, so a clean replace is **not** safe.

Recognize the situation by these signals:

- the operator gives a branch name (e.g. `feature/hermes-tool-wrappers`) plus a
  commit SHA, and explicitly says "не делать clean replace" / "сохранить
  локальные доработки" / "Strategy A conservative merge".
- The installed package is an editable install (`*.pth` file under
  `~/.hermes/hermes-agent/venv/lib/python3.11/site-packages/`).
- `git status` in the installed package is "not a git repo" — installed
  package is a checked-out directory, **not** a git working tree.

## Procedure (8 steps, additive merge)

### 1. Backup (mandatory, before any edit)

```bash
PKG=/home/hermes/.hermes/profiles/deepseek/plugins/packages/hermes_telegram_ads_pkg
TS=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP=/home/hermes/.hermes/backups/hermes_telegram_ads_pkg.${TS}
mkdir -p /home/hermes/.hermes/backups
cp -a "$PKG" "$BACKUP"
```

Always create a timestamped backup. If a backup already exists for the
same session, that's fine — keep them all.

### 2. Classify branch changes

Clone the branch shallowly into `/tmp` (never into the installed path):

```bash
cd /tmp && git clone --depth 1 --branch <branch> \
  https://github.com/example/telegram-ads-upstream.git \
  tg-ads-feature
cd /tmp/tg-ads-feature
git fetch origin master
git diff --stat FETCH_HEAD..HEAD
git diff --name-only FETCH_HEAD..HEAD
```

Sort the changed files into three buckets:

| Bucket | Action |
|---|---|
| **New files** (in `git diff --name-only` only as additions) | Copy to installed package as-is |
| **Modified files in branch** that you also have local edits for | Compare both, port only the **non-conflicting additive** parts |
| **Modified files in branch** that are **unchanged locally** | Safe to copy verbatim (no merge conflict) |

If the branch modified a file that the installed package also has but
with **divergent semantics** (e.g. feature-branch moved a function from
`pages/account.py` to `parser.py` while installed kept the old
location), **do not** copy the modified file — instead port individual
additive changes with `patch`. Reason: the local divergent semantics
are what the installed `workflows/` layer relies on.

### 3. Copy new files first

For each new file in the branch, copy to the installed package:

```bash
PKG=/home/hermes/.hermes/profiles/deepseek/plugins/packages/hermes_telegram_ads_pkg
FEAT=/tmp/tg-ads-feature
cp -v "$FEAT/hermes_telegram_ads/<new_module>.py" \
      "$PKG/hermes_telegram_ads/<new_module>.py"
cp -v "$FEAT/docs/<doc>.md" "$PKG/docs/<doc>.md"
cp -v "$FEAT/skills/<skill>/SKILL.md" "$PKG/skills/<skill>/SKILL.md"
cp -v "$FEAT/tests/<test>.py" "$PKG/tests/<test>.py"
```

Cross-profile guard will fire on the `cp`. The path lives under
`~/.hermes/profiles/deepseek/plugins/` and the agent is running under
`default` profile. Apply `cross_profile=True` on the
`write_file`/`patch` calls (NOT on `terminal cp` — terminal bypasses
the guard but produces no audit log; the explicit `cross_profile=True`
on `patch` is the audited path).

### 4. Patch `__init__.py` last (additive merge only)

The branch's `__init__.py` typically **removes** the local `workflows`
import. To preserve both:

```python
# Keep installed's `from . import workflows` AND add branch's hermes_tools
from hermes_telegram_ads.hermes_tools import (  # branch additions
    TELEGRAM_ADS_TOOLS, TOOLS_BY_NAME, TelegramAdsToolset,
    SafetyClass, ToolSpec, tool_names,
)
# Keep installed:
from hermes_telegram_ads import workflows  # noqa: F401  (local)
```

Extend `__all__` to include the new exports while keeping the existing
ones. Verify with import test:

```bash
$venv/bin/python3.11 -c "
import hermes_telegram_ads
print('TelegramAdsToolset:', hermes_telegram_ads.TelegramAdsToolset)
print('len(TELEGRAM_ADS_TOOLS):', len(hermes_telegram_ads.TELEGRAM_ADS_TOOLS))
print('run_workflow:', hermes_telegram_ads.run_workflow)
"
```

If the workflow export is missing → workflows broke → **revert and
investigate**, do not push forward.

### 5. Patch `conftest.py` (additive)

Branch tests reference fixtures (`toolset`, `fake_adapter`,
`tool_config`) that the installed `conftest.py` does not have. Append
additively:

```python
# ─── Hermes tool-layer fixtures (added YYYY-MM-DD: feature/X) ──

@pytest.fixture
def tool_config(tmp_path):
    from tests.fake_adapter import make_config
    return make_config(tmp_path)


@pytest.fixture
def fake_adapter(tool_config):
    from tests.fake_adapter import FakeAdapter
    return FakeAdapter(tool_config)


@pytest.fixture
def toolset(fake_adapter):
    from hermes_telegram_ads.hermes_tools import TelegramAdsToolset
    return TelegramAdsToolset(adapter=fake_adapter)
```

Do **not** delete the existing `load_json`, `load_text`, `fixtures_dir`
fixtures.

### 6. Inventory exclusion for branch-vs-installed drift

`tests/test_hermes_inventory.py` enforces "every adapter public
coroutine is wrapped in a `telegram_ads_*` tool **or** documented in
`DOCUMENTED_EXCLUSIONS`". If the installed adapter has methods the
branch did not wrap (typical: workflow-layer additions like
`adapter.parse_ads()`), add an exclusion entry with a **specific
reason**:

```python
DOCUMENTED_EXCLUSIONS: dict[str, str] = {
    "get_profile": "Returns PII (phone/email). Never exposed to agents.",
    "get_account_stats": "Thin stub returning only a URL; superseded by snapshot + screenshots.",
    "parse_ads": (
        "Installed workflow-layer addition; superseded by "
        "telegram_ads_snapshot_* tools that return the same "
        "data_quality / column_map / warnings structure. Kept for "
        "back-compat with installed _snapshot.py / _account_diagnosis.py "
        "workflows."
    ),
}
```

Include a date stamp and a back-compat note. Do not add empty
exclusions.

### 7. Skill in two places (skill's package + Hermes skill library)

The branch ships `skills/operate-telegram-ads/SKILL.md`. Install in
**two** locations:

1. `<pkg>/skills/operate-telegram-ads/SKILL.md` — for
   `test_hermes_docs.py` to find it.
2. `~/.hermes/skills/devops/operate-telegram-ads/SKILL.md` — for
   Hermes CLI's `~/.hermes/skills/` scanner to load it as a runtime
   skill.

Both copies must have the same SHA. After installation, verify:

```bash
sha256sum /tmp/tg-ads-feature/skills/operate-telegram-ads/SKILL.md \
          ~/.hermes/skills/devops/operate-telegram-ads/SKILL.md \
          ~/.hermes/profiles/deepseek/plugins/packages/hermes_telegram_ads_pkg/skills/operate-telegram-ads/SKILL.md
```

All three should match. If they don't, re-copy.

### 8. Verification

```bash
# 1. Python syntax
$venv/bin/python3.11 -c "import ast; ast.parse(open('<file>').read())"

# 2. Module import (catches Python-level errors)
$venv/bin/python3.11 -c "import hermes_telegram_ads; print(len(hermes_telegram_ads.TELEGRAM_ADS_TOOLS))"

# 3. New hermes tests
cd <pkg> && $venv/bin/python3.11 -m pytest tests/test_hermes_*.py -v

# 4. Pre-existing core tests (catch regression)
cd <pkg> && $venv/bin/python3.11 -m pytest tests/test_audit.py tests/test_config.py \
     tests/test_payloads.py tests/test_safety.py tests/test_types.py \
     tests/test_api_contracts.py -v
```

Pre-existing test failures unrelated to the integration (e.g.
`test_parser.py` importing `parse_pct` from a package that has
`parse_percent`) are **out of scope** for the integration. Capture
them in a separate task; do not block the integration on them.

## Pitfalls

### Cross-profile write guard

`write_file` and `patch` block on
`~/.hermes/profiles/deepseek/plugins/...` when running under profile
`default`. The guard message says: "To bypass this guard after explicit
user direction, retry the call with `cross_profile=True`." This is the
audited path. `terminal cp` bypasses the guard silently but produces
no audit log.

**Ask once per patch series, not once per session.** If the session
involves 5 cross-profile edits (browser_manager + workflow tool +
tests + 2 contract changes), each **series** gets its own `clarify` +
approval. Do not bundle them into one approval even if the user
already approved "the cross-profile write" earlier. A "restart"
approval is also separate from a "patch" approval even if both come up
in the same report.

### Pre-existing test failures

Always run the installed core tests **before** declaring the
integration done. If they were already failing before the integration,
note the failure with the file/test name and continue. Do not "fix" by
modifying installed test code — that's a separate task.

### `__init__.py` collision

The branch's `__init__.py` typically **removes** the `workflows`
import to avoid circular dependencies in their reduced surface. If
you copy the branch's `__init__.py` verbatim, **installed workflows
break** (`run_workflow` becomes `ImportError`). Always merge
`__init__.py` manually: keep installed's `workflows` import, add
branch's `hermes_tools` imports, extend `__all__`.

### Frozen working tree

`git status` in the installed package will say "not a git repo" — the
installed package is **not** a git working tree. To check what
"installed" actually is, use:

```bash
# 1. .pth file points to installed
ls -la ~/.hermes/hermes-agent/venv/lib/python3.11/site-packages/*.pth

# 2. Find the actual package directory
$venv/bin/python3.11 -c "import hermes_telegram_ads; print(hermes_telegram_ads.__file__)"

# 3. Compare with backup
diff -q <backup> <installed>
```

To get "current state of installed" you diff against the backup. To
get "what branch changed" you diff the branch against its parent
commit (master). These are **separate diffs** — do not confuse them.

### `hermes_telegram_ads/<module>.py` modified in branch

If the branch modified a module that the installed package also has
**with a divergent local edit**, do not overwrite. Read both diffs,
port the additive changes (new methods, new exports) with `patch`,
and leave the local divergent logic alone. Examples of divergent
local edits in this package:

- `browser_manager.py` — installed only, branch does not have it
- `workflows/__init__.py` + `_snapshot.py` + `_account_diagnosis.py`
  + `_inspect_ad.py` — installed only, branch does not have them
- `adapter.py::parse_ads` — installed only, branch's adapter doesn't
  have this method (and removing it breaks installed workflows)

When in doubt, ask the operator which side wins before patching.
