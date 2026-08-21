# Workflow Authoring — Adding a New `telegram_ads_workflow`

Playbook for adding a new typed workflow (e.g. `account_diagnosis`, future
`prepare_campaign`, `fix_rejection`) to the `telegram_ads_workflow` tool.

This is a **runtime + doc + test** change. Steps must be in order. Skipping
steps leads to a workflow that "looks implemented" but fails at runtime or
breaks existing users.

---

## 1. Pre-flight: design before code

Before writing the workflow, write a design doc to
`projects/<workflow_name>_design.md`. Use the `account_diagnosis` design
(`projects/account_diagnosis_design.md`) as template. Required sections:

- Purpose (one paragraph)
- Strict constraints (what the workflow must NOT do)
- Typed parameters (table)
- Step-by-step behavior
- Output structure (JSON example)
- Honest limitations (what the workflow cannot detect, and why)
- Error handling table
- Test plan (specific test cases)
- Files touched / not touched

**Show the design to the operator before any code changes.** Approval gate.

---

## 2. Files to create

| File | Purpose |
|---|---|
| `hermes_telegram_ads/workflows/_<workflow>.py` | The workflow module. `async def run_<workflow>(adapter, params) -> dict`. |
| `tests/test_telegram_ads_<workflow>.py` | Unit tests with `AsyncMock` adapter. |
| `projects/<workflow>_design.md` | Design doc. |

## 3. Files to patch (in this order)

1. `hermes_telegram_ads/workflows/__init__.py` — add `WORKFLOW_REGISTRY` entry:
   `"<workflow>": ("_<workflow>", "run_<workflow>")`. **No other change.**

2. `tools/telegram_ads_workflow_tool.py` — extend `WORKFLOW_SCHEMA`:
   - Add `"<workflow>"` to the `workflow.enum` list.
   - Add new parameters to `properties` (with type, description).
   - Do **not** change the schema structure (the `{"type": "function", "function": {...}}`
     wrapping is fragile; preserve it exactly).

3. `shared/TELEGRAM_ADS_TOOL_CONTRACT.md` — add a row to §3.6 "Workflow layer"
   table (or create new section if pattern diverges).

4. `skills/business-growth/prepare-and-manage-tg-ads/SKILL.md` — add a
   "Workflow: `<workflow>`" subsection to the "Telegram Ads Workflows" section.

5. `skills/business-growth/prepare-and-manage-tg-ads/references/workflow_design.md`
   — add a section with output spec, parameters, error handling.

6. `projects/telegram_ads_tool_contract.md` — optional: update detailed
   contract mirror.

## 4. Cross-profile guard — required approval

The `hermes_telegram_ads` package lives at
`~/.hermes/profiles/deepseek/plugins/packages/hermes_telegram_ads_pkg/`
(editable install shared between `default` and `deepseek` profiles).

Writes via `write_file` / `patch` will trigger the **cross-profile soft guard**.
You must:

- Show the operator the file paths that will be written.
- Get explicit approval.
- Then call with `cross_profile=True` (or use `terminal` to bypass — but
  `cross_profile=True` is the documented path).

**Never** write through `terminal cat > file` as a workaround — the guard exists
for a reason. Use `cross_profile=True` so the change is recorded as approved.

Files that always require `cross_profile=True`:
- `~/.hermes/profiles/deepseek/plugins/packages/hermes_telegram_ads_pkg/hermes_telegram_ads/workflows/_*.py`
- `~/.hermes/profiles/deepseek/plugins/packages/hermes_telegram_ads_pkg/hermes_telegram_ads/workflows/__init__.py`

Files that do **not** require cross-profile:
- `hermes-agent/tools/*.py`
- `hermes-agent/tests/*.py`
- `~/.hermes/shared/*.md`
- `~/.hermes/skills/**/*.md`
- `~/.hermes/SOUL.md`
- `~/.hermes/projects/*.md`

---

## 5. Schema patching — pitfalls

The `WORKFLOW_SCHEMA` in `telegram_ads_workflow_tool.py` is a deeply nested
OpenAI function-call dict. `patch` with even slightly wrong indentation can
produce a syntactically valid Python file with a **broken JSON schema**
(extra braces, missing closing brackets).

**Rules:**

- When extending an existing property, use a small, surgical `patch` (e.g. add
  one new property to `properties`, or add one new enum value).
- When restructuring the schema (e.g. moving a property, renaming, refactoring
  indent levels), **rewrite the whole `WORKFLOW_SCHEMA` block with `write_file`**.
  Read the whole file first to make sure you have a clean view.
- After any schema change, verify with:
  ```bash
  python -c "import ast; ast.parse(open('tools/telegram_ads_workflow_tool.py').read()); print('OK')"
  ```
  followed by importing the module and checking the registered schema:
  ```python
  import importlib.util
  spec = importlib.util.spec_from_file_location('tgwf', 'tools/telegram_ads_workflow_tool.py')
  mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
  ```
- If the schema breaks, **revert the last `patch` and use `write_file`** to
  rewrite the whole schema block from the previous known-good version.

---

## 6. Test design

- **Minimum 20 tests** for a non-trivial workflow.
- Cover: happy path, error paths, edge cases, conclusion safety (if applicable).
- Use `unittest.mock.AsyncMock` / `MagicMock` for adapter.
- Pattern: `_make_adapter(accounts=..., parse_result=..., balance=...)` helper
  that returns a fully-mocked adapter with `AsyncMock`-set methods.
- Test parser warnings and `data_quality` propagation explicitly.
- For workflows that produce a `conclusion` field, include tests that assert
  the conclusion **does not** contain forbidden phrases (e.g. "never had
  campaigns", "were deleted").
- Run **both** the new test file and the full existing suite:
  ```bash
  python -m pytest tests/test_telegram_ads_<workflow>.py tests/test_telegram_ads_workflows.py tests/test_telegram_ads_parser.py tests/test_telegram_ads_browser_lock.py -v
  ```
  All must pass.

### 6.1 Mock adapter template (canonical pattern)

This is the pattern used by `test_telegram_ads_snapshot_scan_failed.py`
and `test_telegram_ads_workflows.py`. Use it as a starting point for
new workflow tests:

```python
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock
import asyncio


@dataclass
class FakeAccount:
    """Stand-in for hermes_telegram_ads.adapter.Account."""
    title: str
    account_token: str
    account_type: str = "TON"
    currency: str = "TON"


def _make_adapter(
    list_accounts_result: list[FakeAccount] | None = None,
    choose_account_raises: Exception | None = None,
    parse_ads_result: dict[str, Any] | None = None,
    budget_result: MagicMock | None = None,
) -> MagicMock:
    """Build a MagicMock adapter that satisfies the workflow API."""
    adapter = MagicMock()
    adapter.list_accounts = AsyncMock(
        return_value=list_accounts_result if list_accounts_result is not None else []
    )

    async def _choose_account(token):
        if choose_account_raises is not None:
            raise choose_account_raises
        return None

    adapter.choose_account = _choose_account
    adapter.parse_ads = AsyncMock(
        return_value=parse_ads_result or {
            "ads": [],
            "data_quality": "complete",
            "warnings": [],
            "budget_column_label": "",
        }
    )
    adapter.get_account_budget = AsyncMock(
        return_value=budget_result or MagicMock(balance=0.0, currency="TON", transactions=[])
    )
    return adapter


def test_example():
    from hermes_telegram_ads.workflows._snapshot import run_snapshot

    adapter = _make_adapter(
        list_accounts_result=[FakeAccount(title="X", account_token="t1")],
        choose_account_raises=RuntimeError("boom"),
    )
    result = asyncio.run(run_snapshot(adapter, {}))
    assert result["ok"] is False
    assert result["error"] == "ACCOUNT_SCAN_FAILED"
```

Key points:

- Use `AsyncMock` for `list_accounts`, `parse_ads`, `get_account_budget`
  (these are awaited in workflow code).
- Use a plain `async def` for `choose_account` when you need
  conditional raise behavior (more readable than side-effect chains
  on `AsyncMock`).
- The adapter **must** expose every method the workflow calls. If you
  forget one, the test may pass through to MagicMock's auto-attr and
  return a MagicMock — which may or may not work. Be explicit.
- Call `run_<workflow>(adapter, params)` directly in tests for
  multi-account workflows (so you can read the inner `ok` / `error` /
  `total` shape). Wrap with `run_workflow(...)` only when testing the
  dispatcher, not the workflow semantics.
- For each `accounts_analyzed == 0` style failure semantic, add
  **four** tests at minimum: all-skipped, partial, real-zero success,
  end-to-end error does not become workflow-specific failure (e.g.
  `LOGIN_REQUIRED` must not become `ACCOUNT_SCAN_FAILED`). See
  `references/snapshot_failure_semantics.md`.

## 7. Honest-limitation design pattern

## 7. Honest-limitation design pattern

When the workflow cannot observe something (DOM state, filter UI, archive
view, etc.), do **not**:

- ❌ Silently fake the observation.
- ❌ Attempt to clear / reset UI state (that's write-adjacent).
- ❌ Return an empty / "ok" default.

Instead, return an explicit "I cannot tell" signal:

```json
{
  "filters": {
    "checked": true,
    "present": null,
    "cleared": null,
    "reason_if_unchecked": "DOM probe not implemented in adapter"
  }
}
```

And include the limitation as a fragment in the `conclusion`:

> "Filter state could not be observed — counts may exclude campaigns
> hidden by current filters."

This is the Operating-Discipline-compliant pattern. Honest > useful-fake.

---

## 8. Epistemic honesty in `conclusion`

Any workflow that produces a `conclusion` (or any natural-language summary)
field **must** be tested for these forbidden phrases:

- "never had", "were deleted", "was deleted", "doesn't exist", "do not exist"

The conclusion should describe what the **UI shows as visible**, not infer
causation. The test:

```python
@pytest.mark.asyncio
async def test_conclusion_never_claims_deleted():
    # ... setup ...
    result = await run_<workflow>(adapter, params)
    conclusion = result["conclusion"].lower()
    for phrase in FORBIDDEN_PHRASES:
        assert phrase not in conclusion
```

Capture `FORBIDDEN_PHRASES` as a module-level constant for reuse.

---

## 9. Registry order

The `WORKFLOW_REGISTRY` in `workflows/__init__.py` is read-only at runtime.
Order of insertion determines `sorted(WORKFLOW_REGISTRY)` output in error
messages. New workflows go at the end. Do **not** reorder existing entries —
that breaks error messages and tests that snapshot registry order.

---

## 10. References

- Design template: `projects/account_diagnosis_design.md`
- Existing workflow modules: `hermes_telegram_ads/workflows/_snapshot.py`,
  `_inspect_ad.py`, `_account_diagnosis.py`
- Existing test patterns: `tests/test_telegram_ads_workflows.py`,
  `test_telegram_ads_account_diagnosis.py`
- Tool schema: `tools/telegram_ads_workflow_tool.py`
- Shared contract: `~/.hermes/shared/TELEGRAM_ADS_TOOL_CONTRACT.md`
- Operating Discipline (mandatory): `skills/business-growth/prepare-and-manage-tg-ads/SKILL.md`
  § "Operating Discipline"
