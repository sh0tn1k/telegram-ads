# Staged Patch Workflow — Protocol

## Когда the operator использует staged approval

Staged patch — это когда the operator разбивает patch на **фазы** (Phase 1, Phase 2, Phase 3, ...) и одобряет каждую отдельным сообщением с явными границами. Recognition patterns:

- "Apply Phase 1 + Phase 2" + "Do NOT apply yet: Patch 3/4" + "Reason: need real sanitized DOM fixture or corrected implementation"
- "I explicitly approve cross-profile patching for this Phase X + Phase Y patch series in /path/. Scope: file1, file2, tests. Do not modify X, Y, Z."
- "Apply immediately. Approved." после перечисления scope
- "Do not apply Patch N: [reason with gating condition]"

## Разбор типового staged approval message

```
Accept. Apply:
1. Patch N — title
2. Patch M — title

Do NOT apply yet:
- Patch K — reason (gate condition)

Cross-profile approval:
I explicitly approve cross-profile patching for this Phase 1 + Phase 2 patch series in:
/home/hermes/.hermes/profiles/deepseek/plugins/packages/hermes_telegram_ads_pkg/

Scope of approval:
- api.py
- _snapshot.py
- tests related to this patch

Do not modify campaigns, budgets, CPM, ads, accounts, or live Telegram Ads state.
Do not run live actions.
Do not restart gateway unless tests show it is required.
```

Извлеки:

| Поле | Где взять | Зачем |
|---|---|---|
| Phase IDs to apply | "Apply:" список | Применять только это |
| Phase IDs to defer | "Do NOT apply yet:" список | Не применять; запомнить gating condition |
| Cross-profile paths | "approve cross-profile patching in:" | Использовать `cross_profile=True` в `skill_manage` |
| Scope файлов | "Scope of approval:" | Whitelist файлов для модификации |
| Negative scope | "Do not modify X, Y, Z" | Hard guard — никаких live mutations |
| Other constraints | "Do not run live actions / restart gateway" | Hard guard — никаких side effects |

## Protocol

### Step 1: Parse approval message в структуру

Создай mental list:

```
ALLOWED: Patch 1, Patch 2, Patch 5, Patch 6
DEFERRED: Patch 3, Patch 4 (gate: real sanitized DOM fixture)
CROSS_PROFILE_PATHS: hermes_telegram_ads_pkg/ (api.py, _snapshot.py, tests)
NEGATIVE_SCOPE: campaigns, budgets, CPM, ads, accounts, live state
```

### Step 2: Создай todo список с явными boundaries

```python
todo([
    {"id": "1", "content": "Patch 1+2: api.py — robust owner_id extraction", "status": "in_progress"},
    {"id": "2", "content": "Patch 5: workflow_tool.py — remove await from sync release_adapter()", "status": "pending"},
    {"id": "3", "content": "Patch 6: _snapshot.py — add snapshot_timezone='UTC'", "status": "pending"},
    {"id": "4", "content": "Add tests: owner_id tiers, release_adapter sync, snapshot_timezone UTC", "status": "pending"},
    {"id": "5", "content": "Run pytest; ensure no regressions", "status": "pending"},
    # НЕ включать Patch 3, Patch 4 — они deferred
])
```

### Step 3: Apply patches в правильном порядке

1. **Сначала Patches 1+2 (api.py + cross-profile)**, потом Patch 5 (tool, default profile), потом Patch 6 (cross-profile), потом tests.
2. Каждый `skill_manage` для cross-profile пути → `cross_profile=True`.
3. Sanity check после каждого patch: `python -c "import ast; ast.parse(...)"` + import-test.

### Step 4: Tests

Минимум 5 тестов на каждую фазу:

- Static source check (regression guard на regression).
- Behavioral test (mock-based).
- Edge case.
- Error path / timeout.
- Integration check через `_call_workflow` или `run_snapshot` напрямую.

### Step 5: Run full suite

```bash
cd /home/hermes/.hermes/hermes-agent && . venv/bin/activate
python -m pytest tests/test_telegram_ads_<phase>.py tests/test_telegram_ads_<existing>.py ...
```

**Все тесты должны пройти.** Если регрессия — НЕ откатывай другие patches; сначала разберись, какой patch вызвал regression.

### Step 6: Report

В финальном отчёте:

1. Что применено (file by file, кратко).
2. Что НЕ применено и почему (gate condition).
3. Тесты: `152 passed, 0 failed`.
4. Explicit "no live actions performed" / "no gateway restart".
5. "Ready to Phase 3" — если the operator одобрил следующие фазы или если требуется fixture.

## Common pitfalls при staged patches

- **Применять Patch 3 "потому что the operator намекнул"** — НЕЛЬЗЯ. Staged approval = explicit gate. Если непонятно — переспроси.
- **Думать "Phase 1+2 = всё что касается parser"** — нет, Phase IDs должны точно матчиться с тем, что the operator перечислил в "Apply:". Не додумывай.
- **Группировать Phase 1+2 в одно todo** — нет, лучше отдельные todo для traceability.
- **Игнорировать "Do not modify campaigns, budgets, CPM, ads, accounts"** — это negative scope, не описание текущего состояния. Даже если текущая фаза не трогает live state, фиксируй в report что "negative scope соблюдён".
- **Группировать cross-profile approval с другим approval** — cross-profile это **отдельный** scope. Один patch series = один approval message. Следующая серия (даже через 5 минут) = новый approval.

## Когда the operator НЕ использует staged, а просто говорит "сделай X"

- Один patch, без фазирования → apply immediately после показа diff.
- "Сделай X" без explicit gate condition → interpret as one-shot, не staged.

## Artifacts convention для staged patch

Сохраняй в `~/.hermes/projects/tg_ads_patch_<phase>_<YYYY-MM-DD>/` (если сессия использует projects dir, иначе inline в report):

- `applied_patches.json` — список patches с file path, line range, commit hash (если есть).
- `deferred_patches.json` — список patches с gate condition.
- `test_results.txt` — pytest output.
- `sanity_checks.json` — ast.parse + import-test results.

## Ссылки

- Cross-profile guard: SKILL.md §"Cross-profile guard" (added 2026-06-02)
- Operating Discipline: SKILL.md §"Operating Discipline"
- Failure modes (TypeError `await None`, _CSRF_RE hex, mock pitfalls): SKILL.md §"Failure modes / common mistakes"
- Parser F1-F5 status: `references/parser_diagnostics.md`
