# Telegram Ads Parser — Диагностика и архитектура

## Когда читать этот документ

Snapshot или `parse_ads_table` возвращает данные, не совпадающие с Telegram Ads UI:
- все кампании "Unknown" / "Stopped", хотя в UI они Active
- `clicks = 0` при значимых impressions (CTR = 0%)
- `spent_total` не совпадает с UI spend
- column shift warning в snapshot output

## Архитектура

```
pages/account.py:parse_ads_table(browser)
  Phase 1 — Header mapping
    querySelectorAll('table.pr-table thead th')
    → normalize header text (lower, strip, collapse spaces)
    → match against _HEADER_ALIASES
    → column_map = {"spent": 9, "status": 12, ...}

  Phase 2 — Row parsing (by column_map, not hardcoded indices)
    querySelectorAll('table.pr-table tbody tr')
    → for each row: cells[col_map["spent"]], cells[col_map["status"]], ...
    → status: try .pr-status-badge class first, then column text
    → if status not in KNOWN_STATUSES → "Unknown" (NOT "Stopped")
    → cell count check: warn on mismatch

  Phase 3 — Data quality
    → compute_data_quality(col_map, missing, ads)
    → warnings: MISSING_HEADER, COLUMN_COUNT_MISMATCH, STATUS_PARSE_FAILED
    → return dict with ads, data_quality, column_map, warnings, budget_column_label

workflows/_snapshot.py
  → adapter.parse_ads()  (was adapter.list_ads())
  → _classify_campaigns() — Unknown NOT counted as stopped
  → _detect_anomalies() — CTR/clicks, column shift, budget overlap
  → _compute_entry_data_quality() — combine parser quality + anomalies
```

## Header aliases

```python
_HEADER_ALIASES = {
    "ad_title":   ["ad title", "title"],
    "views":      ["views", "impressions"],
    "clicks":     ["clicks"],
    "actions":    ["actions"],
    "ctr":        ["ctr"],        # STRICT — exact match only
    "cvr":        ["cvr"],        # STRICT — exact match only
    "cpm":        ["cpm"],        # STRICT — exact match only
    "cpc":        ["cpc"],        # STRICT — exact match only
    "cpa":        ["cpa"],        # STRICT — exact match only
    "spent":      ["spent"],
    "budget":     ["budget"],
    "target":     ["target"],
    "status":     ["status"],
    "date_added": ["date added", "added", "created"],
}
_STRICT_HEADERS = frozenset({"ctr", "cvr", "cpm", "cpc", "cpa"})
```

**Короткие колонки (3-4 символа) — ТОЛЬКО exact match.** Никогда не используй `includes` для `ctr`, `cvr`, `cpm`, `cpc`, `cpa` — ложно срабатывает на любой колонке, содержащей эти буквы.

**Длинные колонки — exact + includes** с проверкой `len(a) > 4` (например "status" может быть "Campaign Status").

## Data Quality Model

```python
DataQuality = Literal["complete", "partial", "unreliable"]
```

| Quality | Условие | Что делать |
|---|---|---|
| `complete` | Все обязательные колонки найдены, статусы Known | Данным можно доверять |
| `partial` | Есть missing headers, или часть статусов Unknown | Интерпретировать с осторожностью, проверять anomalies |
| `unreliable` | Нет column_map, или все статусы Unknown | Данные НЕЛЬЗЯ использовать для выводов; нужен fix парсера |

Статус Unknown **всегда** = `partial` или `unreliable`. Никогда не считается "Stopped".

## Anomaly Detection

Функция `_detect_anomalies()` в `_snapshot.py`:

| Anomaly code | Условие | Вероятная причина |
|---|---|---|
| `PARSE_ANOMALY_CTR_CLICKS_MISMATCH` | CTR > 0% но clicks = 0 | Column shift: clicks читается из неверной колонки |
| `PARSE_ANOMALY_STATUS_UNKNOWN` | N из M статусов Unknown | Status column не найден или текст не в Known |
| `PARSE_ANOMALY_COLUMN_SHIFT` | spent_total > 0 и все статусы Unknown | Полный column shift |
| `PARSE_ANOMALY_SPENT_ZERO` | campaigns_total > 0 но spent_total = 0 | spent колонка не читается |
| `PARSE_ANOMALY_BUDGET_EXCEEDS_SPENT` | budget_column_total > spent_total | Нормально для daily budget; anomaly если разрыв велик |

## Output schema

### `parse_ads_table()` return

```python
{
    "ads": [AdSummary, ...],
    "data_quality": "complete" | "partial" | "unreliable",
    "column_map": {"spent": 9, "status": 12, ...},
    "missing_headers": ["budget"],
    "expected_columns": 14,
    "warnings": [
        "MISSING_HEADER: budget not found in table headers",
        "COLUMN_COUNT_MISMATCH: ad_id=123 cells=13 expected=14",
        "STATUS_PARSE_FAILED: 3/3 statuses are Unknown",
    ],
    "budget_column_label": "Budget",  # actual Telegram Ads header text
}
```

### `_classify_campaigns()` return

```python
{
    "total": 3,
    "active": 0,
    "stopped": 0,
    "declined": 0,
    "limited": 0,
    "unknown": 3,   # Unknown НЕ counted as stopped
}
```

### `_compute_performance()` return

```python
{
    "impressions": 3904,
    "clicks": 88,
    "ctr": 2.25,
    "spent_total": 253.0,        # sum of spent column
    "budget_column_total": 47.0, # sum of budget column (label unknown)
}
```

**Budget naming conventions:**
- `spent_total` — сумма колонки "Spent" (фактические траты)
- `budget_column_total` — сумма колонки "Budget" (что бы ни означало)
- `budget_column_label` — actual header text из Telegram Ads (например "Budget", "Daily Budget")
- Никогда не выводить "spent X of Y" — мы не знаем, что означает Y
- Никогда не считать `budget_used_pct` — это подразумевает знание, которого у нас нет
- Example Guard Bot reference: spent_total=253, budget_column_total=47

## Budget naming — правила

**Абсолютно запрещено:**

```
❌ "потрачено 253 из 47"   — budget != total_allocated
❌ "spent 253 of 47"       — то же
❌ "использовано 537% бюджета" — budget_column может быть daily, не monthly
```

**Корректно:**

```
✅ spent_total = 253⭐, budget_column_total = 47⭐
   budget_column_label = "Budget"  (так называется колонка в Telegram Ads UI)
   Эти две суммы могут иметь разный смысл — не смешивай.
```

## Парсинг чисел — edge cases

`parser.py` functions:

**sanitize_number(text: str) → str:**
- Специальные пробелы (U+00A0, U+202F, U+2009, U+2002, U+2003, U+2007) → обычный пробел
- Запятые → удалены (thousand separators)
- Множественные пробелы → схлопнуты в один
- Trim leading/trailing

**parse_money(text: str) → float:**
- "⭐ 97" → 97.0
- "⭐️549" → 549.0
- "💎11.97" → 11.97
- "💎1,234.56" → 1234.56
- "–" → 0.0
- "+💎0.0035" → 0.0035

**parse_int(text: str) → int:**
- "5,391" → 5391
- "1 592" → 1592
- "1\u00a0592" → 1592
- "–" → 0
- "-5" → -5
- "4 Start bot" → 4

**parse_percent(text: str) → float | None:**
- "3.91%" → 3.91
- "45,28%" → 45.28 (EU decimal comma → dot)
- "–" → None

## Проверка: live browser

```bash
# 1. Открыть ads.telegram.org/account в headed режиме (xvfb-run)
# 2. Выполнить в DevTools:

# Проверить заголовки:
const h = [...document.querySelectorAll('table.pr-table thead th')]
  .map(t => t.innerText.trim());
console.log(`Headers (${h.length}):`, h);

# Проверить данные первой строки:
const row = document.querySelector('table.pr-table tbody tr');
const cells = row?.querySelectorAll('td') || [];
console.log(`Cells: ${cells.length}`);
const HEADER_ALIASES = {ad_title:['ad title','title'],views:['views','impressions'],clicks:['clicks'],actions:['actions'],ctr:['ctr'],cvr:['cvr'],cpm:['cpm'],cpc:['cpc'],cpa:['cpa'],spent:['spent'],budget:['budget'],target:['target'],status:['status'],date_added:['date added','added','created']};
const nh = h.map(x => x.toLowerCase().replace(/\s+/g, ' ').trim());
Object.entries(HEADER_ALIASES).forEach(([key,aliases]) => {
  const idx = nh.findIndex(x => aliases.some(a => a == x || (a.length>4 && x.includes(a))));
  if (idx >= 0) console.log(`${key}: [${idx}] "${cells[idx]?.innerText.trim()}"`);
  else console.log(`${key}: NOT FOUND`);
});
```

## Status parsing — порядок приоритета

1. `.pr-status-badge` CSS class → определить статус по class name
2. `.pr-status-badge` innerText → текст статусного badge
3. Статус-колонка innerText (по column_map)
4. Если ничто не совпало → `"Unknown"`

**Статус НИКОГДА не сваливается в "Stopped" по умолчанию.** Статус "Stopped" — только если распознан explicit текст "Stopped" или badge class stopped.

## KNOWN_STATUSES vs ALL_STATUSES

```python
# constants.py
KNOWN_STATUSES = ("Active", "On Hold", "Stopped", "In Review", "Declined")
STATUS_UNKNOWN = "Unknown"
ALL_STATUSES = KNOWN_STATUSES + ("Unknown",)  # только для Literal validation
```

- `KNOWN_STATUSES` — используется для `data_quality` и `_classify_campaigns`
- `STATUS_UNKNOWN` — не Known, всегда parse warning
- `ALL_STATUSES` — для type validation, не для логики

## Real-world failure modes (read-only diagnosis playbook)

Эти 5 паттернов наблюдались в живом snapshot'е (2026-06-02 21:50, 4 кабинета: the operator, Example TON, Example Bot, Example Guard Bot). Если snapshot возвращает что-то похожее — диагноз ниже. **Все patches read-only proposal, не применять без the operator approval + cross-profile guard.**

### F1. `Selector not found: input[name='owner_id'][type='hidden']` → аккаунт skipped

**Симптом:** `account.scan_failed: SelectorNotFoundError` для конкретного кабинета, остальные OK.

**Где:** `hermes_telegram_ads/api.py:115` — `rebootstrap()` HTML fallback.

**Root cause:** Поле `<input name='owner_id'>` есть **только** на формах (`/account/ad/new`, `/account/budget/add_stars`, `/account/ad/<id>/budget`). На `/account` (homepage) и в account-switcher dropdown его **нет**. После `choose_account?token=...&to=account` редиректа `window.Aj.state.ownerId` часто ещё не инициализирован к моменту `rebootstrap()` — JS-скрипт `ajInit({...})` запускается асинхронно, а `rebootstrap()` ждёт 0 секунд.

**Status: APPLIED 2026-06-03 (Phase 1 — `Patch 1+2`).** См. `hermes_telegram_ads/api.py:bootstrap()`. Tiered extraction:
1. `await self._browser.wait_for_function("() => window.Aj && window.Aj.state && window.Aj.state.apiUrl", timeout_ms=2000)` — дать Aj-скрипту время (timeout swallowed).
2. Tier 1: `evaluate("() => window.Aj.state.ownerId")` — primary path.
3. Tier 2: `read_attr("input[name='owner_id'][type='hidden']", "value")` — fallback, **wrapped in try/except** so `SelectorNotFoundError` falls through to tier 3 instead of propagating up.
4. Tier 3: regex `_OWNER_ID_INLINE_RE = r'ownerId["\\\']?\s*[:=]\s*["\\\']?([A-Za-z0-9_-]{4,64})["\\\']?'` over `browser.html()`, bounded by `asyncio.wait_for(html, timeout=1.5s)`.
5. On failure: `raise TelegramAdsApiError(..., raw_response={"url", "tiers_tried": [...]})` (см. gotcha про `raw_response` vs `context` в SKILL.md failure modes).

**Test coverage:** `tests/test_telegram_ads_api_owner_id.py` — 20 тестов (Tier 1 primary, Tier 2 SelectorNotFoundError swallow, Tier 3 regex happy path, all-tiers-fail context, slow html() bounded, regex parametrize для 5 shapes, hash fallback, LoginRequired).

### F2. `campaigns parsed = 0` при наличии spend в `/account/budget`

**Симптом:** `account.campaigns.total = 0`, но `account.budget.balance > 0` + `last_transactions[]` не пуст.

**Где:** `hermes_telegram_ads/pages/account.py:223` — `parse_ads_table()` Phase 1 header query.

**Root cause:** `rebootstrap()` для таких аккаунтов **успешно** проходит (потому что `/account/budget` содержит owner_id-инпут), но `parse_ads_table()` на `/account` ищет только `table.pr-table thead tr`. STARS Bot accounts могут рендерить активные кампании в `table.pr-table`, а **архивные / On Hold / Declined** — в `.pr-card-list` или на отдельной вкладке. Текущий парсер не покрывает этот layout → таблица пустая, spend приходит только из `/account/budget`.

**Status: PROPOSED, NOT YET APPLIED (2026-06-03 Phase 2 deferred).** the operator's explicit gate: "need real sanitized DOM fixture or corrected implementation". **Do not implement** without one of:
- Sanitized DOM fixture (real Telegram Ads HTML with `.pr-card-list` layout, sanitized of any account_token / csrf hash / ad_id values > 0 / real spend numbers).
- OR: corrected implementation plan reviewed against current production DOM by an agent with live browser access (NOT auto-approved — explicit operator approval per Operating Discipline rule 6).

**Why blocked:** Without a fixture, the heuristic in fix proposal could be wrong (e.g. real `.pr-card-list` could mean "On Hold campaigns", not "active campaigns missing from table"). Implementing against a guess risks regressing the table-based parser for TON accounts.

**Fix proposal (held, NOT applied):**
- Multi-selector header query: `document.querySelectorAll('table.pr-table, .pr-card-list')`.
- Если найден `.pr-card-list` (нет `thead`), map его элементы на AdSummary + emit warning `STARS_BOT_LAYOUT_USED: card-list fallback`.
- Heuristic: если `balance > 0` + `transactions` не пуст + `ads = []` → `data_quality == "partial"` (не `complete`) + `CAMPAIGNS_MISSING_DESPITE_SPEND`.

**Test (held, NOT applied):** fixture STARS-кабинета только с `.pr-card-list` → expect `data_quality == "partial"`, `ads` не пуст, warning `STARS_BOT_LAYOUT_USED`.

### F3. Fields misaligned в Bot Account (Example Guard Bot-style)

**Симптом:** `ad.title` содержит `⭐️` + название + `\n💎17.00` (balance вшит в title-cell). Колонки `views/clicks` показывают значения, не совпадающие с UI.

**Где:** `hermes_telegram_ads/pages/account.py:262-280` (row_js в `parse_ads_table`).

**Root cause:** У STARS Bot accounts title-cell имеет layout `<span class="ad-account-glyph">⭐️</span> + <a>name</a> + <span class="ad-account-balance">💎X.XX</span>`. `innerText` склеивает всё в одну строку с `\n`-переносами. Парсер читает `cells[colMap['ad_title']]` и записывает всю конструкцию в `ad_title`; `ad_id_href` всё равно корректен (берётся из `a[href^='/account/ad/']`), но `views/clicks` колонки сдвигаются если `<a>` занимает не первую ячейку.

**Status: PROPOSED, NOT YET APPLIED (2026-06-03 Phase 2 deferred).** the operator's explicit gate: "do not call Python `_extract_clean_title` from browser row_js. Either clean title after JS result in Python or implement equivalent JS cleanup." **Do not implement** without a real sanitized fixture showing the actual title-cell HTML.

**Why blocked:** Calling Python helper from browser row_js breaks the JS-string execution model in `parse_ads_table`. The fix needs to either (a) extract clean title in JS via `first_line.splitlines()[0].strip()` and `replace(/^⭐️?💎?\s*/, '')`, or (b) post-process the title in Python after `parse_ads_table` returns. Without a fixture we don't know which regex is correct, and a wrong regex would silently corrupt TON account titles.

**Fix proposal (held, NOT applied):**
- Extract clean title: `first_line.splitlines()[0].strip()` + strip leading `⭐️⭐💎\s+`.
- Использовать `titleA?.innerText` (только anchor innerText), а не `cells[titleCellIdx]?.innerText`.
- DevTools verification: `document.querySelector('a[href^="/account/ad/"]').innerText` должно давать "Example Guard", не "⭐️Example Guard\n💎17.00".

**Test (held, NOT applied):** fixture HTML с title-cell содержащим ⭐️ + balance → expect `AdSummary.title == "Example Guard"`, `ad_id` корректен.

### F4. `Failed to release adapter: object NoneType can't be used in 'await' expression`

**Симптом:** warning в логе `tools.telegram_ads_workflow_tool`, **не** блокирует snapshot, но маскирует реальные ошибки + оставляет lock held для следующего workflow.

**Где:** `tools/telegram_ads_workflow_tool.py:219`.

**Root cause:** `await manager.release_adapter()`, но `hermes_telegram_ads/browser_manager.py:196` объявляет `def release_adapter` (sync, не async). `await None` → TypeError. Параллельно `workflows/__init__.py:101` вызывает **без** `await` → lock не снимается → следующий workflow получает `BrowserProfileBusyError`.

**Status: APPLIED 2026-06-03 (Phase 1 — `Patch 5`).** В `telegram_ads_workflow_tool.py:219`: `await manager.release_adapter()` → `manager.release_adapter()`. Опционально: сделать `release_adapter` async для консистентности — **deferred** до отдельного одобрения.

**Test coverage:** `tests/test_telegram_ads_workflow_tool_release.py` — 5 тестов:
- Static source check: `assert "await manager.release_adapter" not in _call_workflow source` (regression guard).
- `assert "manager.release_adapter()" in _call_workflow source` (fix presence).
- Mock `_make_browser` — после успешного workflow `fake_mgr.release_adapter.call_count == 1`.
- `test_release_adapter_await_does_not_raise_typeerror` — TypeError regression guard.
- `test_release_adapter_called_even_on_workflow_failure` — finally-гарантия на workflow crash.

### F5. Timezone discrepancy в `snapshot_date`

**Симптом:** пользователь видит `snapshot_date: 2026-06-02`, хотя тест был после полуночи UTC 2026-06-03.

**Где:** `hermes_telegram_ads/workflows/_snapshot.py:58,180` — `datetime.now(tz=UTC)` + `strftime("%Y-%m-%d")`.

**Root cause:** **не баг парсера** — живой код использует UTC правильно. Расхождение возникает из-за:
- Кэшированного snapshot output (старые fixture screenshots в `data/telegram_ads/screenshots/`).
- Test-fixture, записанной с фиксированной датой.
- В test-fixture `datetime.now()` без `tz=UTC` — локальный TZ даст сдвиг.

**Status: APPLIED 2026-06-03 (Phase 1 — `Patch 6`).** В `_snapshot.py`:
- Добавлена константа `SNAPSHOT_TIMEZONE = "UTC"`.
- Output envelope (success + `ACCOUNT_SCAN_FAILED` failure paths) теперь содержит `snapshot_timezone: "UTC"` явно.
- `snapshot_timestamp` остаётся ISO с `tz=UTC`.

**Test coverage:** `tests/test_telegram_ads_snapshot_timezone.py` — 6 тестов:
- Константа: `SNAPSHOT_TIMEZONE == "UTC"`.
- Success envelope: `result["snapshot_timezone"] == "UTC"`.
- ISO timestamp: `datetime.fromisoformat(result["snapshot_timestamp"]).utcoffset() == 0` + внутри `before <= ts <= after`.
- `snapshot_date` matches `datetime.now(tz=UTC).strftime("%Y-%m-%d")`.
- `ACCOUNT_SCAN_FAILED` envelope тоже содержит `snapshot_timezone == "UTC"`.
- `SNAPSHOT_TIMEZONE` re-export из `workflows/_snapshot.py`.

**Reminders (held, NOT applied):**
- Test-fixture builder: всегда `datetime.now(tz=UTC)`, никогда `datetime.now()`.
- Validation: `snapshot_timestamp` и `snapshot_date` должны консистентны относительно UTC.

## Artifacts convention for read-only parser diagnosis

Когда делаешь snapshot diagnosis, **всегда** сохраняй sanitized artifacts в `~/.hermes/projects/tg_ads_parser_diag_<YYYY-MM-DD>/`:

- `parser_log.json` — извлечённые WARNING/ERROR строки из `agent.log` с timestamp + session_id.
- `html_snapshot_inferred.json` — гипотетическая DOM-структура на основе кода (если live HTML недоступен).
- `selectors_health.json` — матрица selectors.yaml vs реальное использование, статус OK/BROKEN/MAYBE-BROKEN.
- `account_layout_notes.json` — per-account наблюдения (title, type, spend, campaigns_parsed, failure_point).
- `timezone_check.json` — где timestamp генерируется, что в fixtures.

**Никогда** не включай в artifacts: account_tokens, CSRF hash, ad_id values > 0, реальные balance/spend values. Заменяй на `<token>`, `<csrf>`, `<spend>`.

## Cross-profile patch protocol (напоминание)

`hermes_telegram_ads_pkg` живёт в `~/.hermes/profiles/deepseek/plugins/packages/hermes_telegram_ads_pkg/` (editable install shared между default + deepseek). `patch` / `write_file` на этот путь требует:
- `cross_profile=True` в skill_manage params
- Явное одобрение the operator на **эту серию** (per skill §"Cross-profile guard")
- **Один approval на patch series, не на каждый файл** — но **отдельно** на каждый restart/cleanup action

Не обходи guard через `terminal cat > file` — это anti-pattern.
