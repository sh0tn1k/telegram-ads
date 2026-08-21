---
name: prepare-and-manage-tg-ads
description: "Подготовка, анализ и оптимизация Telegram Ads кампаний для Telegram-ботов/каналов под строгим approval-контролем. Используй для подготовки креативов и таргетинга, оценки CPM/bid/бюджета, диагностики отклонений, ревью CTR/CPC/CPA/ROAS, approval-запросов на запуск/остановку/изменение. Триггеры: «Telegram Ads», «подготовь рекламу», «почему отклонили», «перепиши креатив», «CPM/бюджет», «проанализируй кампанию», «срез кабинета» (snapshot), «проверить объявление» (inspect_ad). Не используй для постов в канал или запуска без approval."
version: 1.0.0
---

# Prepare and Manage Telegram Ads

## Purpose

Готовить, анализировать и давать операционные рекомендации по Telegram Ads: креативы, таргетинг, CPM/bid, бюджет, причины отклонения, оптимизация (stop/scale/adjust). Связывать рекламную статистику с воронкой и выручкой бота через UTM/campaign_id. Любое действие с внешними последствиями выполняется только после явного approval от the operator.

## When to use

**Hermes Goal pre-routing.** Если запрос на рекламу сформулирован как цель в естественном языке (например, «запусти рекламу чтобы было 5 платящих»), сначала должна пройти Hermes Goal validation / routing layer. Этот skill получает только валидированный goal context или draft/plan request. Ad launch/change остаётся approval-gated: не вызывать live Telegram Ads actions без отдельного явного approval. Если project не imported / нет context intake — вернуть `NEEDS_PROJECT_CONTEXT`, а не готовить project-bound ad diagnosis.

- Подготовка новой Telegram Ads кампании (черновики креативов, таргетинг, диапазон CPM, бюджет).
- Ревью результатов уже идущей кампании (CTR/CPC/CPA/ROAS, downstream-конверсия).
- Диагностика отклонения объявления и переписывание креатива под политику Telegram.
- Рассмотрение изменения CPM/bid/бюджета.
- Создание новых вариантов объявлений (A/B под сегмент и метрику).
- Подготовка approval-запроса перед запуском/остановкой/изменением живой кампании.

## When not to use

- До того как определены аудитория/сегмент/эксперимент — кроме случая, когда явно нужен только черновик.
- Для запуска, изменения или остановки живых кампаний **без** явного approval.
- Для написания постов в канал, онбординга, paywall-текстов (это другие skills).
- Для не-Telegram рекламных каналов, кроме концептуальной адаптации идеи.
- Для диагностики воронки/активации без рекламного контекста (используй analyze-funnel-and-metrics).

## Required inputs

Минимум для осмысленной работы:

- цель кампании и целевая метрика (task_created, payment_completed, paid_conversion и т.п.);
- сегмент/аудитория и оффер (или явный запрос на черновик до их определения).

Желательно (best-effort, если нет — отметить как missing data):

- analytics JSON проекта (см. `_shared/ANALYTICS_JSON_SCHEMA.md`, блоки `ads`, `attribution`, `funnel`, `revenue`);
- project context (см. `_shared/PROJECT_CONTEXT_SCHEMA.md`): pricing, value proposition, segments, markets, ads_accounts, approval_constraints;
- текущие креативы/таргетинг/CPM/бюджет кампании;
- текст и причина отклонения (если есть);
- история прошлых экспериментов (experiment memory).

## Related tools/data

Skill сам не имеет прямого доступа к инструментам — он описывает, какие данные/инструменты использовать, когда их предоставит Hermes runtime:

- project memory;
- команда analytics JSON Telegram-бота;
- Telegram user-account tool в safe/read/test режиме;
- **`telegram_ads_workflow` (typed workflow tool)** — **обязательный первый интерфейс** для задач `snapshot` (срез кабинета, поддержка multi-account, data_quality, anomaly detection) и `inspect_ad` (детальный разбор объявления). Используй его вместо ручной компоновки `telegram_ads` вызовов, когда задача покрывается workflow. Оба workflow read-only, approval не требуется. Output spec snapshot — см. раздел Workflow:snapshot. Диагностика сбоев парсера — `references/parser_diagnostics.md`. Дизайн всех workflow — `references/workflow_design.md`.
- **`telegram_ads` (низкоуровневый tool) — для действий, не покрытых workflow. Полный контракт см. в `/home/hermes/.hermes/shared/TELEGRAM_ADS_TOOL_CONTRACT.md` или `references/telegram_ads_tool_contract.md`; **адаптер управляется через `BrowserProfileManager`** (`references/browser_profile_manager.md`) — модульный singleton для shared lifecycle, lock detection, structured error (browser_profile_locked, browser_profile_busy), **graceful `close_all(timeout=5.0)` API** (added 2026-06-02: 9-key per-step result, `use_adapter()` async context manager, dispatcher `try/finally release_adapter()`); **lifecycle teardown / restart safety** — `references/restart_safety_protocol.md` (added 2026-06-02: 3-level teardown pattern, SIGTERM-only fallback, restart-vs-kill decision tree, env-var rollback path, dispatcher try/finally as additional layer);
- **Stability audit of the underlying package** — если Telegram Ads tool flaky / misbehaving и есть подозрение на регрессию vs upstream `telegram-ads-upstream`, **не** отлаживай runtime вслепую. Сначала read-only audit против upstream: `plugin-package-stability-audit` skill (class-level: 7-шаговая процедура + KFP-01..KFP-10 каталог хрупких паттернов). Артефакты аудита в `/tmp/audit-<pkg>/`, патчи показываются, не применяются. Покрывает Playwright `env=` replace, signal-0 misuse, broken-symlink `Path.exists()`, asyncio.Lock ownership, status literal drift, hardcoded magic constants.
- **`telegram_ads_typed` (typed toolset, added 2026-06-03)** — 57 individual `telegram_ads_*` tools registered as separate registry entries via `tools/telegram_ads_typed_tool.py`. Wraps `TelegramAdsToolset` from `hermes_telegram_ads.hermes_tools` (the 57 ToolSpec entries из `TELEGRAM_ADS_TOOLS`). Acquires adapter via `BrowserProfileManager.shared().acquire_adapter()` (no second Playwright instance, no race with legacy `telegram_ads` tool profile lock). Use these when you want direct typed tool calls (e.g. `telegram_ads_list_accounts`, `telegram_ads_change_cpm`, `telegram_ads_validate_ad`, `telegram_ads_snapshot_accounts`). **Companion skill:** `operate-telegram-ads` in `~/.hermes/skills/devops/` (tools-layer; complements this skill's procedure-layer). Legacy `telegram_ads` single-tool dispatcher остаётся для back-compat.
- **Upstream feature-branch integration procedure** — when integrating a new `feature/*` branch from `telegram-ads-upstream` into the installed package (which has local `browser_manager.py` + `workflows/` additions that don't exist in upstream), use the conservative additive merge procedure. See `references/feature-branch-merge.md` for the full procedure (file classification, cross-profile guard handling, fixture additions, inventory exclusion pattern).
- **Watcher (continuous monitoring) — `hermes_telegram_ads.watcher`** (added 2026-06-09). Polling-based read-only monitor: 62 capabilities in `list_tool_coverage()`, of which 11 mutation tools are explicitly `forbidden_in_watcher`. Use cases: alert on ad_declined, monitor account_balance, verify that an approved mutation actually applied (post_action_verification). Canonical Python path to `telegram_ads_*` is `TelegramAdsAdapter` via `BrowserProfileManager.shared().acquire_adapter()` (NOT `hermes_tools.telegram_ads_*` — that is a runtime stub and not importable outside the gateway). Integration wiring pattern (read-only adapter + mutation guard + consumer route table + 8 smoke checks) — `references/ads_watcher_integration.md`. Install procedure (pinned commit) — `install-hermes-telegram-ads-watcher` skill. `WatcherScheduler.run_forever()` requires explicit operator approval; default integration stays in idle mode (tick = no-op, 0 network calls).
- **См. также § "Operating Discipline"** — обязательные правила взаимодействия с Telegram Ads (никаких ручных браузеров, процессов, debug-fallback без явного одобрения).
- **Staged patch workflow** (added 2026-06-03) — если the operator разбивает patch на Phase 1/2/3+ с явным "Do NOT apply yet" gate (например "need real sanitized DOM fixture"), строго соблюдай границы фаз. Recognition pattern + protocol: `references/staged-patch-workflow.md`. Никогда не применяй deferred phases по умолчанию — жди явного одобрения на каждую серию.
- UTM/campaign attribution data;
- payment/revenue data; pricing config; cost data;
- память прошлых экспериментов; заметки владельца.

**Hermes Ontology integration (v0.1).** Для подготовки / анализа /
оптимизации кампаний используй
``build_campaign_preparation(project_id=..., registry=..., slice_reader=...,
campaign_payload=..., creative_payloads=..., target_segment=..., cpm=..., budget=...,
action="create"|"change"|"submit", linked_experiment=...)`` из
``agent.ontology.growth_integration``. Билдер возвращает
``CampaignPreparationOutput`` с флагами
``external_action_flag=True``, ``approval_required=True``,
``no_live_action_without_approval=True`` — без них этот skill
никогда не выпускает реальный launch / change / submit.
Любые ``ontology_writes_proposed`` (campaign + creatives) идут
через явный approval оператора; ``tools/telegram_ads_*`` calls
запускаются **только** после подтверждения и только
через ``telegram_ads_workflow`` (см. § "Operating Discipline"
ниже — никаких ручных браузеров, никаких process actions,
никаких ``playwright_evaluate`` обходов).

Для read-only операций (snapshot, inspect_ad, разбор статистики)
вызывай ``build_growth_diagnosis(project_id=..., registry=...)``
— он вернёт срез проекта, facts / risks / approval_requirements
и ``missing_data``, на которые этот skill опирается при
диагностике.

Не утверждай наличие доступа к инструменту, если он не предоставлен runtime.

**DeepSeek и доступ к telegram_ads tool.** На 2026-06-03 профиль `deepseek`
(DeepSeek Companion) подключён к тем же `telegram_ads` и `telegram_ads_typed`
toolset'ам в `platform_toolsets.telegram`, что и `default` (the agent). the operator
сознательно переопределил предыдущее правило "DeepSeek by design без
telegram_ads". Это значит: при делегации Telegram Ads задач DeepSeek через
AGI Team Task Board **можно** передавать read-only операции
(`telegram_ads_status`, `telegram_ads_snapshot_accounts`, `telegram_ads_get_ad`,
`telegram_ads_get_ad_stats`, etc.) — DeepSeek увидит их в своём toolset и
сможет сделать честный review. **Mutating** операции (`telegram_ads_change_cpm`,
`telegram_ads_create_ad`, `telegram_ads_add_to_budget`, и т.д.) DeepSeek
по-прежнему **не может** выполнить сам — они вернут `status: "approval_required"`
+ `confirmation_id`, который DeepSeek должен surfaced the operator через Task Board,
а не исполнить автономно. Никогда не говори от лица DeepSeek "я изменю CPM
через telegram_ads" — DeepSeek не тратит деньги, только review'ит.

## Operating Discipline (mandatory)

Эти правила обязательны для **всех** задач, связанных с Telegram Ads. Нарушение → structured error, а не попытка обхода. Перекрывают любые legacy recovery docs (включая `references/direct_playwright_recovery.md`).

1. **Всегда начинай с `telegram_ads_workflow`.** Если для задачи есть typed workflow (`snapshot`, `inspect_ad`, ...) — используй его. Не собирай ручную последовательность `telegram_ads` calls, когда workflow покрывает задачу.
2. **Не открывай ads.telegram.org вручную.** Никаких `browser_navigate`, `browser_click`, `browser_snapshot`, `browser_console`, `browser_vision`, `computer_use`, прямых Playwright/Chromium скриптов для взаимодействия с Telegram Ads dashboard.
3. **Не инспектируй и не убивай процессы.** Запрещено: `ps`, `pgrep`, `pkill`, `kill`, рестарт gateway/Xvfb/Chromium/Playwright, чтение `~/.xsession-errors`, `cat /tmp/.X*-lock` в ходе Telegram Ads задачи. Если что-то выглядит неправильно — верни structured error, не "чини" инфраструктуру.
4. **Не запускай alternate browser session.** Никаких вторых Chromium, новых Playwright contexts, ручного `xvfb-run` для обхода locked profile.
5. **`browser_profile_locked` / `browser_profile_busy` — terminal для задачи.** Если `telegram_ads_workflow` возвращает такой error — останови задачу и верни structured error the operator. Не retry, не workaround.
6. **Manual browser/debug fallback — только с явного одобрения.** Разрешено только если the operator ввёл точную фразу: **"approve Telegram Ads debug fallback"**. Без этой фразы — никаких browser/Playwright/DevTools/Xvfb/process действий. Legacy recovery docs могут упоминаться только как debug-only procedure, на которую ссылаются **после** явного одобрения для текущей задачи.
7. **Restart любого Hermes gateway/browser/Xvfb сервиса — отдельное явное одобрение.** `hermes-gateway-default.service`, `hermes-gateway-deepseek.service`, или любой другой gateway/browser/Xvfb systemd unit для разблокировки Telegram Ads задачи требует собственного explicit approval — не покрывается general "approved permanently" grants.
8. **Killing Chromium/Playwright — никогда не автоматически.** `SIGTERM` / `SIGKILL` browser или Playwright процесса в ходе Telegram Ads задачи всегда требует task-scoped explicit approval. Не делается по инициативе агента.
9. **"Approved permanently" scope не применяется** к Telegram Ads browser/process/debug actions. Постоянное approval одной категории (например "approve spend on Telegram Ads CPM up to 💎2") **не** даёт права на browser/process/debug действия.
10. **Operating Discipline перекрывает legacy recovery docs.** Если старый doc (например `references/direct_playwright_recovery.md`) предлагает direct Playwright / `xvfb-run` / headless-Chromium как *default* recovery для Telegram Ads — это **не** автоматический fallback. Правильное поведение: structured error + запросить у the operator explicit approval на debug fallback (см. правило 6). Legacy doc валиден только как *debug-only* procedure.

Если для "починки" тула нужно нарушить любое из правил выше — правильный ответ: **structured error + запросить у the operator нужное specific approval**, а не обход правила.

## Telegram Ads Tool Actions

### Правила использования

1. **Tool — первичный интерфейс.** Если для задачи существует `telegram_ads` action — используй его. Не открывай ads.telegram.org вручную (через browser tool, computer_use, Playwright). Если tool не имеет нужного action — сообщи как gap, не делай browser fallback без явного разрешения the operator.

2. **Перед любым live action — approval-запрос.** Прежде чем вызывать CONFIRM_REQUIRED или DOUBLE_CONFIRM action, подготовь approval-запрос по формату из раздела Approval policy. Покажи the operator точный `telegram_ads(action=..., params=...)` вызов, который будет исполнен после подтверждения.

3. **Сначала diff, потом apply.** Прежде чем вносить любые изменения в код, конфиги, скиллы или tool definitions — покажи точный diff. Не применяй изменения без явного approval. Это относится к schema патчам, новым tool-файлам, изменениям в adapter и toolset definitions.

4. **Сначала валидация, потом запуск.** Перед `create_ad` всегда вызывай `validate_ad(draft)` — отловить ошибки полей до траты денег.

5. **DeepSeek и доступ к telegram_ads.** DeepSeek Companion (профиль
`deepseek`) с 2026-06-03 имеет прямой доступ к read-only telegram_ads
tools через свой `telegram_ads_typed` toolset. Можно делегировать ему
review/analysis задачи с этими tools. DeepSeek **не** может выполнить
mutating actions (CPM, create, budget, delete) самостоятельно — они
требуют `confirmation_id` от the operator. При делегации: укажи в Task Board
что mutating actions не выполнять, а только report'ить что нужно сделать.

### Task → Action mapping

| Задача | Tool actions | Category |
|---|---|---|
| Проверить логин | `telegram_ads(status)` / `telegram_ads(ensure_logged_in)` | SAFE_READ |
| Выбрать кабинет | `telegram_ads(list_accounts)` → `telegram_ads(choose_account, account_token=...)` | SAFE_READ |
| Список кампаний | `telegram_ads(list_ads)` | SAFE_READ |
| Детали кампании | `telegram_ads(get_ad, ad_id=...)` + `telegram_ads(get_ad_stats, ad_id=...)` | SAFE_READ |
| Бюджет кабинета | `telegram_ads(get_account_budget)` | SAFE_READ |
| Валидация креатива | `telegram_ads(validate_ad, draft=...)` | SAFE_READ |
| Подготовка черновика | `telegram_ads(prepare_draft, draft=...)` → screenshot | DRAFT |
| Сохранить черновик | `telegram_ads(save_draft, draft=...)` | DRAFT |
| Превью/скриншот | `telegram_ads(screenshot)` | SAFE_READ |
| Диагностика отклонения | `telegram_ads(get_ad, ad_id=...)` → `telegram_ads(validate_ad, draft=...)` → переписать креатив | SAFE_READ |
| Рекомендация CPM/bid | `telegram_ads(get_ad_stats, ad_id=...)` + `telegram_ads(get_account_budget)` — анализ без изменения | SAFE_READ |
| Изменить CPM ⛔ | `telegram_ads(change_cpm, ad_id=..., new_cpm=...)` — только после approval | CONFIRM |
| Изменить бюджет ⛔ | `telegram_ads(add_to_budget / withdraw_from_budget, ...)` — только после approval | CONFIRM |
| Запуск кампании ⛔ | `telegram_ads(create_ad, draft=...)` — только после approval | CONFIRM |
| Пауза/возобновление ⛔ | `telegram_ads(pause_ad / resume_ad, ad_id=...)` — только после approval | CONFIRM |
| Удалить кампанию ⛔ | `telegram_ads(delete_ad, ad_id=...)` — двойной confirm, только после approval | DOUBLE_CONFIRM |

> ⛔ = требуется явный approval от the operator перед вызовом

Полный список всех actions с inputs/outputs — см. таблицы ниже.

### Safe Read (без confirmation)

| Шаг | action | params | Output type |
|---|---|---|---|
| Открыть дашборд / проверить состояние | `status` / `open_dashboard` | — | URL |
| Проверить логин | `ensure_logged_in` | — | bool |
| Список кабинетов | `list_accounts` | — | list[Account] |
| Выбрать кабинет | `choose_account` | `account_token` | Account |
| Текущий кабинет | `current_account` | — | Account |
| Список объявлений | `list_ads` | — | list[AdSummary] |
| Детали объявления | `get_ad` | `ad_id` | AdDetail |
| Статистика объявления | `get_ad_stats` | `ad_id` | AdStats |
| Бюджет кабинета | `get_account_budget` | — | AccountBudget |
| Скачать CSV по объявлению | `download_report` | `ad_id`, `month` (YYYYMM) | file_path |
| Скачать CSV по кабинету | `download_account_report` | `month` (YYYYMM) | file_path |
| Валидация черновика | `validate_ad` | `draft: CreateAdDraft` | dict |
| Скриншот страницы | `screenshot` | `name`, `full_page` | file_path |
| Список пиксельных событий | `list_events` | — | list[Event] |
| Получить пиксельный код | `get_pixel_snippet` | — | PixelSnippet |
| Лог пиксельного события | `get_event_log` | `event_id` | EventLog |
| Публичная ссылка статистики | `get_share_stats_url` | `ad_id` | str |

### Draft (без confirmation, но логируется)

| Шаг | action | params | Output |
|---|---|---|---|
| Сохранить черновик + скриншот | `prepare_draft` | `draft: CreateAdDraft`, `screenshot_name` | dict |
| Сохранить черновик на сервере | `save_draft` | `draft: CreateAdDraft` | dict |
| Загрузить медиа | `upload_media` | `file_path` | media_token |
| Клонировать объявление как черновик | `create_similar_draft` | `source_ad_id` | dict |

### Confirm-Required (нужен approval + confirmation_id)

| Шаг | action | params | Risk |
|---|---|---|---|
| Создать объявление | `create_ad` | `draft: CreateAdDraft`, `confirmation_id` | тратит деньги |
| Редактировать объявление | `edit_ad` | `draft: EditAdDraft`, `confirmation_id` | перезапускает модерацию |
| Изменить CPM | `change_cpm` | `ad_id`, `new_cpm`, `confirmation_id` | влияет на spend |
| Добавить в бюджет | `add_to_budget` | `ad_id`, `amount`, `confirmation_id` | тратит деньги |
| Вывести из бюджета | `withdraw_from_budget` | `ad_id`, `amount`, `confirmation_id` | деньги |
| Поставить на паузу | `pause_ad` | `ad_id`, `confirmation_id` | живая кампания |
| Возобновить | `resume_ad` | `ad_id`, `confirmation_id` | живая кампания |
| Создать пиксельное событие | `create_event` | `title`, `event_type`, `confirmation_id` | сбор данных |

### Double-Confirm (нужен approval + 2x confirmation_id)

| Шаг | action | params | Risk |
|---|---|---|---|
| Удалить объявление | `delete_ad` | `ad_id`, `confirmation_id`, `second_confirmation_id` | необратимо |
| Удалить событие | `delete_event` | `event_id`, `confirmation_id`, `second_confirmation_id` | необратимо |
| Отозвать публичную ссылку | `revoke_stats_url` | `ad_id`, `confirmation_id`, `second_confirmation_id` | ломает внешний доступ |

### Forbidden (заблокированы safety layer)

| action | Причина |
|---|---|
| `transfer_stars` | `forbid_transfer_stars_until_verified` |
| `external_payment` | `forbid_external_payment_until_verified` |
| `change_status` | Зарезервировано |

## Telegram Ads Workflows (typed layer)

Для типовых read-only задач используй `telegram_ads_workflow` — он принимает typed параметры и возвращает структурированный JSON с готовыми метриками.

### Workflow: `snapshot`

Полный срез Telegram Ads: все кабинеты, балансы, кампании по статусам, события, суммарные метрики.

```
# Все кабинеты (default)
telegram_ads_workflow(workflow="snapshot")

# Только текущий кабинет
telegram_ads_workflow(workflow="snapshot", account_scope="current")

# Конкретный кабинет
telegram_ads_workflow(workflow="snapshot", account_scope="selected", account_token="...")
```

#### Output

Per-account:

```python
{
    "account": {"title": "...", "currency": "TON", "balance": 50.0},
    "campaigns": {"total": N, "active": N, "stopped": N,
                  "declined": N, "limited": N, "unknown": N},
    "performance": {"impressions": N, "clicks": N, "ctr": N.NN,
                    "spent_total": N.N, "budget_column_total": N.N},
    "data_quality": "complete" | "partial" | "unreliable",
    "budget_column_label": "Budget",   # actual TG Ads header text
    "events_count": N,
    "warnings": [...],
}
```

Total:

```python
{
    "accounts_analyzed": N,
    "campaigns_total": N, "campaigns_active": N, "campaigns_stopped": N,
    "campaigns_declined": N, "campaigns_limited": N, "campaigns_unknown": N,
    "impressions": N, "clicks": N, "ctr": N.NN,
    "spent_total": N.N, "budget_column_total": N.N,
}
```

#### Правила интерпретации

- **Unknown status** — не counted as stopped. Всегда parse warning. Если все статусы Unknown → `data_quality = "unreliable"`.
- **`spent_total` / `budget_column_total`** — две независимые колонки. **Не выводить "spent X of Y"** — мы не знаем, означает ли budget_column общий лимит, daily budget, или что-то ещё.
- **`data_quality`** — если `"partial"` или `"unreliable"`, данные НЕЛЬЗЯ использовать для глобальных выводов. Диагностика парсера — `references/parser_diagnostics.md`.
- **Scope** — если scope ≠ all, warning включает "Snapshot is scoped to N account(s), not all M". Total — только по просканированным.
- **Anomalies** в warnings: `PARSE_ANOMALY_CTR_CLICKS_MISMATCH`, `PARSE_ANOMALY_STATUS_UNKNOWN`, `PARSE_ANOMALY_COLUMN_SHIFT`, `PARSE_ANOMALY_SPENT_ZERO`.

Подробная диагностика сбоев парсера — `references/parser_diagnostics.md`.

### Workflow: `inspect_ad`

Детальный разбор одного объявления: полные данные, статистика, computed metrics.

```
telegram_ads_workflow(workflow="inspect_ad", ad_id=42)
```

Считает: CTR, CPC, CPA, daily_spend_rate, budget_remaining, budget_used_pct.
Output: ad, stats, share_stats_url, metrics{}, decline_reason (если есть).

### Workflow: `account_diagnosis`

Per-account deep-dive: "почему кабинет выглядит пустым / почему в нём неожиданные данные?". Read-only, approval не требуется.

```
telegram_ads_workflow(workflow="account_diagnosis", account_name="the operator")
telegram_ads_workflow(workflow="account_diagnosis", account_token="...", include_archive=True, compare_ui_type=True, expected_account_type="TON")
```

**Параметры:**

| Поле | Тип | Default | Описание |
|---|---|---|---|
| `account_name` | `str` | None | Имя кабинета (case-insensitive substring). Если задано вместе с `account_token`, приоритет у token. |
| `account_token` | `str` | None | Точный токен кабинета. |
| `include_archive` | `bool` | True | Считать ли stopped/declined/limited/on_hold кампании. |
| `check_filters` | `bool` | True | Зарезервировано: попытка прочитать filter state (DOM probe не реализован → всегда null). |
| `compare_ui_type` | `bool` | False | Сравнить наблюдаемый тип с `expected_account_type`. |
| `expected_account_type` | `str` | None | `"TON"` \| `"STARS"` \| `"Bot"`. |

**Что умеет:**
- Резолвит кабинет по token (приоритет) или name (substring, case-insensitive). При неоднозначном name — берёт первый + warning.
- Переключается на кабинет, вызывает `parse_ads()` (rich parser с `data_quality` + warnings), `get_account_budget()`.
- Классифицирует кампании: total/active/stopped/on_hold/declined/limited/unknown.
- Archive = stopped+declined+limited+on_hold counts (TG Ads не имеет отдельного archive view; "archive" — это семантическая сумма).
- UI type — эвристика: `currency` + `account_type` поле.
- Генерирует `conclusion` — human-readable summary с честными формулировками.

**Чего НЕ умеет (honest limitations):**
- ❌ Не очищает search/filters/sort — это было бы write-adjacent. Вместо этого возвращает `filters.present=null, reason="DOM probe not implemented in adapter"`.
- ❌ Не выводит "кампаний никогда не было" / "кампании были удалены". Только "as visible in the current dashboard".
- ❌ Не открывает `ads.telegram.org` вручную, не инспектирует/убивает процессы. Подчиняется Operating Discipline (см. §"Operating Discipline" выше).
- ❌ Не вводит confirmation_id / не делает mutating actions.

**Output structure:** см. `projects/account_diagnosis_design.md` §5.

**Правило:** если задача "почему кабинет выглядит пустым / неожиданным" — используй `account_diagnosis`, не собирай ручную последовательность `telegram_ads` calls.

**Детальный дизайн всех фаз workflow см. в `references/workflow_design.md`.**

**Правило:** если задача покрывается workflow — используй `telegram_ads_workflow`, а не ручную компоновку `telegram_ads` вызовов. Workflow — предпочтительный интерфейс, низкоуровневый tool — fallback.

**Failure semantic:** если `snapshot` (или любой multi-account workflow) собрал 0 успешных результатов при ≥ 1 найденных аккаунтах, он возвращает `ACCOUNT_SCAN_FAILED` (а не `ok=true` с zero-aggregated metrics). Полная спецификация — `references/snapshot_failure_semantics.md`.

Детальный дизайн всех фаз workflow см. в `references/workflow_design.md`.

### Adding a new workflow (authoring playbook)

Если нужно добавить новый workflow (`account_diagnosis`, `prepare_campaign`,
`fix_rejection`, ...) в `telegram_ads_workflow` tool, **сначала** прочитай
`references/workflow_authoring.md`. Там 10-шаговый чеклист:

1. Design doc (`projects/<workflow>_design.md`) → the operator approval
2. Создай `hermes_telegram_ads/workflows/_<workflow>.py` + tests
3. Patch `workflows/__init__.py` (registry) → `tools/telegram_ads_workflow_tool.py` (schema) → contract → skill → workflow_design
4. **Cross-profile guard**: пакет живёт в `profiles/deepseek/plugins/...` — нужен `cross_profile=True` + явное одобрение the operator
5. Schema pitfalls: при больших изменениях — `write_file` целиком, не `patch` (иначе JSON schema ломается)
6. Test design: минимум 20 unit tests с `AsyncMock` adapter (см. canonical pattern в `workflow_authoring.md` §6.1)
7. Honest-limitation pattern: если нельзя прочитать UI state — вернуть `present=null, reason="..."`, не фейк
8. Epistemic honesty: `conclusion` **никогда** не должен содержать "never had", "were deleted", "was deleted"
9. Registry: новые workflows — в конец `WORKFLOW_REGISTRY`, не переставлять
10. References: `account_diagnosis_design.md` как template

### Failure semantics for multi-account workflows

Если новый workflow сканирует **несколько** аккаунтов / кабинетов / сущностей
(snapshot pattern, future multi-account review), **обязательно** определи
explicit failure semantic на случай "просканировал ≥ 1, но ни один не
проанализировался":

- Возвращай `ok: false, error: "<DOMAIN>_COLLECTED_NOTHING"`, **не** `ok: true` с
  zero-aggregated metrics. Иначе consumer (LLM, dashboard) не отличит
  "успешно просканировал пустой кабинет" от "не смог ничего просканировать".
- `total: null`, `metrics: null` — **null, не zero**. Это ключевое различие
  между "real zero" и "no data".
- `data_quality: "unavailable"` (константа `DQ_UNAVAILABLE`) — новый
  top-level label, отличный от `"unreliable"`. `"unreliable"` = "получили
  данные, но парсер флагнул проблемы". `"unavailable"` = "данных нет совсем".
- Сохрани per-item skip reasons в `warnings[]` и per-item entries — это
  критично для диагностики.
- **НЕ маскируй end-to-end ошибки** (login, list_X errors) в свой
  collected-nothing error. Они должны пробрасываться через dispatcher как
  `LOGIN_REQUIRED` / `API_ERROR` / `WORKFLOW_ERROR` / `INFRA_MISSING`.
- Добавь минимум 4 теста: all-skipped, partial, real-zero success,
  end-to-end error does not become workflow-specific failure.

Полная спецификация для `snapshot` — `references/snapshot_failure_semantics.md`.
Reuse этот pattern для других multi-account workflows.

## Confirmation Flow

Любое mutating action (create, edit, change_cpm, pause/resume, budget ops, delete) требует confirmation_id.

```
Шаг 1: Вызвать action БЕЗ confirmation_id
Шаг 2: Получить ответ:
  {
    "requires_confirmation": true,
    "action": "createAd",
    "risk_level": "CONFIRM_REQUIRED",
    "confirmation_id": "uuid",
    "params_summary": { "title": "...", "cpm": 2.0, ... }
  }
Шаг 3: Показать params_summary пользователю + запросить approval
Шаг 4: После approval -- повторный вызов с confirmation_id
Шаг 5: Если double_confirm -- нужны два разных confirmation_id
```

**Правила:**
- Confirmation одноразовый -- повторное использование rejected
- Confirmation привязан к fingerprint params -- изменённые params rejected
- TTL: 300 секунд (конфигурируется, см. `telegram_ads.yaml`)
- Delete/revoke: требуют DOUBLE_CONFIRM -- два независимых confirmation_id

## Draft Schemas

### CreateAdDraft -- обязательные поля

```
title: str          -- заголовок объявления
text: str           -- текст (макс 160 символов)
promote_url: str    -- t.me/... или @username или https://...
cpm: float          -- ставка за 1000 показов
budget: float       -- общий бюджет
target_type: str    -- "channels" | "bots" | "search"
targets: list[str]  -- @usernames, t.me URLs, или поисковые запросы
```

### CreateAdDraft -- опциональные поля

```
website_name: str | None
website_photo_url: str | None
media_path: str | None           -- загружен через upload_media
ad_info: str | None
show_picture: bool               -- default: true
daily_budget: float | None
views_per_user: int              -- 1..4, default: 1
initial_active: bool             -- default: false
activate_at: str | None          -- ISO datetime
deactivate_at: str | None
weekly_schedule: dict | None     -- {day_idx: {hour, ...}}
schedule_tz: str | None          -- IANA TZ
conversion_event_id: str | None  -- Stars website ads only
placement: str                   -- reserved
```

### EditAdDraft

```
ad_id: int           -- ID редактируемого объявления
title: str
text: str
promote_url: str
cpm: float
active: bool         -- default: true
website_name: str | None
website_photo_url: str | None
media_token: str | None
ad_info: str | None
daily_budget: float | None
views_per_user: int
```

## Placement × Field Matrix (added 2026-06-18)

Этот раздел — **placement-aware gate** для Telegram Ads креативов. Матрица
определяет, какие поля CreateAdDraft / EditAdDraft допустимы для каждого
`target_type` (= placement в Telegram Ads), и что блокируется placement'ом, а
не контентом.

| `target_type` (placement) | `text` / creative_text_160 applicable? | `media_path` applicable? | `ad_info` applicable? | Allowed copy-bearing fields | Forbidden fields | Error token on violation |
|---|---|---|---|---|---|---|
| `channels` | ✅ yes | ✅ yes (photo +50%, video +80%) | ✅ yes | `title`, `text`, `promote_url`, `ad_info` | — | — |
| `bots` | ✅ yes | ❌ no | ✅ yes | `title`, `text`, `promote_url`, `ad_info` | `media_path` | `unsupported_media_for_target_type` |
| `search` | ❌ **NO** | ❌ no | ❌ no | `promote_url`, `targets` (search queries) | **`text`, `media_path`, `ad_info`** | `not_applicable_for_search_placement` |

**Hard rules:**

1. **Search placement** — это placement-specific формат продвижения в
   результатах поиска, не sponsored-message copy. Никакого 160-char
   спонсорского текста, никакого медиа, никакого `ad_info`. Только
   `promote_url` + список search queries (`targets`).
2. **Если placement неизвестен** — placement gate возвращает structured
   error `placement_unknown` и требует от the operator явного разрешения
   (`approve placement <channels | bots | search>`) **до** генерации
   copy. Не угадывать placement.
3. **Только для форматов, реально поддерживающих sponsored-message copy**
   (channels, bots) — генерировать 160-char `text`. Для search — copy
   generation пропускается полностью.
4. **Если рекомендация по ad creative сгенерирована с `text` для search
   placement** — это structured error `text_not_applicable_for_search_placement`,
   не warning. Не "исправить позже".

**Recommendation output spec (ad recommendations).** Любая рекомендация по
ad creative из этого skill **обязана** включать в себя:

- `placement`: `channels` | `bots` | `search` | `unknown`
- `allowed_fields`: список полей CreateAdDraft, разрешённых placement'ом
- `forbidden_fields`: список полей CreateAdDraft, заблокированных placement'ом
- `creative_text_applicable`: boolean

Если хотя бы одно из этих четырёх полей отсутствует — recommendation
incomplete, не выпускать оператору, добавить в `Missing data` секцию.

См. также `references/placement_field_matrix.md` для подробной матрицы и
worked examples.

## Procedure

1. **Определи режим.** Подготовка нового / ревью идущей / диагностика отклонения / изменение параметров / создание вариантов. От режима зависит набор шагов. Выбери нужные tool actions из таблицы выше.
1a. **Placement gate (added 2026-06-18).** Перед любой генерацией ad creative
   copy / recommendations — определи placement из `target_type` draft'а
   (`channels` | `bots` | `search` | `unknown`). Применить **Placement × Field
   Matrix** выше:

   - `channels`: copy generation proceeds normally (`text` 160 chars allowed,
     `media_path` optional).
   - `bots`: copy generation proceeds normally (`text` 160 chars allowed,
     `media_path` MUST be stripped → `unsupported_media_for_target_type`).
   - `search`: **DO NOT generate `text` (160 chars), `media_path`, `ad_info`**.
     Output: `not_applicable_for_search_placement`. Copy generation is
     skipped entirely. Draft retains `promote_url` + `targets` (search
     queries) + `cpm` + `budget` only.
   - `unknown` (placement не определён): STOP, спросить the operator `approve
     placement <channels | bots | search>` перед продолжением. Не угадывать.

   Любая recommendation, выходящая из этого skill, обязана включать
   `placement`, `allowed_fields`, `forbidden_fields`,
   `creative_text_applicable` (см. Recommendation output spec).
1. **Собери данные.** Прочитай project context и analytics JSON. Зафиксируй, какие поля доступны, а какие отсутствуют. Отсутствующие поля помечай явно как **missing data** -- значения не выдумывай.
1. **Привяжи рекламу к воронке.** Сопоставь кампанию с downstream-событиями через UTM/campaign_id: impressions → clicks → bot_starts → task_created → payment_completed. Если связь невозможна -- это **attribution problem**, фиксируй отдельно.
1. **Локализуй проблему.** Классифицируй явно:
- ad-level: низкий CTR, слабый креатив, неточная аудитория;
- funnel: клики есть, но нет starts/tasks/payments;
- product/pricing: задачи есть, оплат нет;
- attribution: невозможно связать spend с событиями.
1. **Для подготовки кампании:** предложи 2-3 варианта креатива под сегмент и оффер (с учётом политики Telegram Ads -- без запрещённых обещаний, без агрессивных формулировок, корректные claims), таргетинг, диапазон CPM, тестовый бюджет, UTM-разметку и stop condition. **Но:** если placement = `search` — copy не генерируется (см. step 1a), выдаётся placement-mandated исключение `not_applicable_for_search_placement`. Используй `validate_ad` → `prepare_draft` для проверки перед submit.
1. **Для диагностики отклонения:** определи вероятную причину по тексту/правилам Telegram, перепиши креатив в политику-комплаентный вид, сохранив оффер и метрику.
1. **Для ревью/оптимизации:** используй `list_ads` + `get_ad` + `get_ad_stats` + `get_account_budget` для сбора данных. Посчитай доступные метрики (CTR, CPC, CPA по task_created и по paid, ROAS). Сравни с целью. Дай решение stop/scale/adjust с обоснованием.
1. **Сформируй рекомендации.** Каждая рекомендация обязана включать: ожидаемый эффект, риск, stop condition, требуется ли approval.
1. **Подготовь approval-запрос**, если действие имеет внешние последствия (см. Approval policy). Используй confirmation flow для mutating actions. Не выполняй и не «подразумевай» выполнение.
1. **Зафиксируй memory updates** по политике памяти (facts / hypotheses / validated rules / не сохранять).

При нехватке данных -- не останавливайся: проводи частичный анализ на доступном, явно перечисляй missing data и рекомендуй улучшения инструментирования.

## Output format

Структурированный, операционный, на русском. Применяй разделы по релевантности:

```md
## Summary
## Facts            (только наблюдаемое из данных; missing data отмечать)
## Analysis         (привязка к воронке, локализация проблемы)
## Hypotheses       (помечены как гипотезы, не как факты)
## Risks
## Recommendations  (каждая: эффект + риск + stop condition + approval?
                     + placement, allowed_fields, forbidden_fields,
                     creative_text_applicable — обязательно для ad creatives)
## Next actions
## Approval required (если есть)
## Memory updates
```

Разделяй facts / hypotheses / risks / recommendations / next actions. Гипотезу никогда не подавай как факт.

## Privacy/compliance-sensitive copy policy

For privacy/compliance-sensitive projects, safe copy rewriting must not change the product.

If the original angle is risky, the agent must:
1. explain the risk;
2. preserve the product's real capability;
3. remove unsafe framing;
4. not invent a new feature;
5. not position the product as chat analytics if the product does not do chat analytics;
6. not promise unauthorized access.

For Dialog Spy Bot, safe copy must stay within:
- Telegram Business-connected chats;
- user-controlled setup;
- deleted/edited message recovery/context;
- privacy limitations.

Safer example:
`Сохраняйте контекст переписки: бот помогает увидеть удалённые и изменённые сообщения в чатах, подключённых через Telegram Business.`

## Approval policy

См. `_shared/APPROVAL_POLICY.md`. **Без approval** skill может:
- писать черновики креативов, предлагать таргетинг и диапазон CPM;
- анализировать CTR/CPC/CPA/ROAS (через safe read actions: `list_ads`, `get_ad`, `get_ad_stats`, `get_account_budget`, `download_report`);
- диагностировать отклонения, переписывать креативы;
- рекомендовать stop/scale/adjust, готовить approval-запрос;
- использовать draft actions: `validate_ad`, `prepare_draft`, `save_draft`, `upload_media`, `create_similar_draft`.

**Только с явным approval от the operator:**
- `create_ad` -- создание и запуск объявления (трата денег);
- `edit_ad` -- редактирование живого объявления (перезапуск модерации);
- `change_cpm` -- изменение CPM (влияет на spend);
- `add_to_budget` / `withdraw_from_budget` -- изменение бюджета (деньги);
- `pause_ad` / `resume_ad` -- пауза/возобновление (живая кампания);
- `delete_ad` / `delete_event` -- удаление (необратимо);
- `create_event` -- создание пиксельного события (сбор данных);
- `revoke_stats_url` -- отзыв публичной ссылки (ломает внешний доступ);
- любая трата денег, публикация, рассылка, отправка пользовательских сообщений, изменение production/БД/настроек.

Approval-запрос оформляй по формату из `_shared/APPROVAL_POLICY.md`: action, project, reason, expected effect, risks, budget/cost impact, rollback/stop condition, точная команда/действие, вопрос на approval.

## Memory updates

См. `_shared/MEMORY_POLICY.md`. Сохраняй только то, что повлияет на будущие решения:

- **Facts** -- стабильные наблюдения из статистики кампаний (например, фактический CPA сегмента за период).
- **Hypotheses** -- правдоподобные, но непроверенные идеи (например, «креатив с примером highlight'а может поднять CTR у геймеров») -- помечать как гипотезу.
- **Validated rules** -- подтверждённые результатом или явным подтверждением владельца; с указанием evidence, даты/источника, scope.
- **Не сохранять:** тривиальное, единичный результат как универсальную истину, чужие фреймворки как стратегию, гипотезу как факт. Сохранять по запросу the operator.

## Failure modes / common mistakes

- Выдумывание метрик при missing data вместо явной отметки отсутствия.
- Смешение ad-level и funnel/product-проблемы (рекомендация «менять креатив», когда теряется воронка после клика).
- Рекомендация изменить CPM/бюджет/статус без approval-запроса.
- Подача гипотезы как подтверждённого факта; сохранение одного результата как правила.
- Игнорирование attribution-проблемы (нельзя связать spend с событиями) -- анализ ROAS при этом недостоверен.
- Креатив, нарушающий политику Telegram Ads (запрещённые обещания, агрессия, неподтверждаемые claims).
- Рекомендация масштабировать кампанию без валидного объёма данных (мало кликов/конверсий для вывода).
- **Placement copy mismatch** — генерация 160-char `text` для `target_type=search` (per the operator's 2026-06-18 spec). Search placement не поддерживает sponsored-message copy. Correct behavior: structured error `not_applicable_for_search_placement`, не "исправлять позже".
- **Placement undefined** — `target_type` неизвестен и the operator не одобрил placement перед генерацией copy. Correct behavior: structured error `placement_unknown`, запросить у the operator `approve placement <channels | bots | search>`.
- **Recommendation missing placement fields** — рекомендация выдана без `placement` / `allowed_fields` / `forbidden_fields` / `creative_text_applicable`. Correct behavior: surface to the operator as incomplete recommendation, добавить в `Missing data` секцию.
- **Bot-targeting ad with uploaded media** — `target_type=bots` + `media_path` (per existing matrix). Correct behavior: `unsupported_media_for_target_type`.
- **Формирование draft JSON руками без валидации** -- всегда используй `validate_ad` перед `prepare_draft`/`create_ad`, чтобы отловить ошибки полей до submit.
- **Ручная компоновка `telegram_ads` вместо `telegram_ads_workflow`** -- для задач `snapshot` и `inspect_ad` используй workflow tool, не собирай их из сырых action-вызовов. Workflow считает метрики, перехватывает ошибки и возвращает структурированный JSON.
- **Глобальные выводы из частичных данных** — если snapshot сканирует не все кабинеты (scope=current/selected), не пиши "все кампании stopped" или "0 кликов" по всем кабинетам. Per-account метрики — это per-account. Total — только по успешно просканированным кабинетам. Если данных недостаточно — явно укажи scope и accounts_analyzed.
- **"Spent X of Y" — ЗАПРЕЩЕНО** — никогда не объединяй `spent_total` и `budget_column_total` в одну фразу "потрачено 253 из 47". `budget_column` может означать daily budget, monthly cap, remaining, или что-то другое. Используй только раздельные поля: `spent_total=253`, `budget_column_total=47`, `budget_column_label="Budget"`.
|- **Инструмент Telegram Ads не принимает параметры (runtime defect)** — если `telegram_ads` и `telegram_ads_workflow` числятся в tool-листе как parameterless (properties: {}), **не пытайся** автоматически вызывать direct Playwright / `xvfb-run` / headless-Chromium как "safe default fallback" — это нарушает Operating Discipline (правила 2, 4, 6). Правильное поведение: вернуть structured error:
  ```json
  {
    "ok": false,
    "error": "telegram_ads_tool_schema_defect",
    "message": "Telegram Ads tool is visible but parameter schema is missing/empty.",
    "allowed_next_steps": [
      "reload/restart tool registry after approval",
      "patch tool schema",
      "manual debug fallback only if the operator says: approve Telegram Ads debug fallback"
    ]
  }
  ```
  `references/direct_playwright_recovery.md` остаётся валидным только как **debug-only procedure**, на которую можно сослаться после явного одобрения the operator для текущей задачи. До одобрения — не использовать.
- **Browser profile locked** — Gateway держит Playwright контекст, второй процесс не может открыть тот же profile. **Никаких kill/pkill.** Инфраструктура: `BrowserProfileManager` (Singleton, `hermes_telegram_ads/browser_manager.py`) управляет единым `TelegramAdsAdapter` через оба tool-файла (`telegram_ads_tool.py` и `telegram_ads_workflow_tool.py`). Если adapter уже занят другим процессом → structured error:
  ```json
  {"ok": false, "error": "browser_profile_locked",
   "profile_path": "...", "owner_pid": 12345,
   "recommended_action": "retry_later_or_restart_gateway"}
  ```
  Если таймаут asyncio.Lock в том же процессе → `browser_profile_busy`. Не создавай второй Chromium — используй `BrowserProfileManager.acquire_adapter()`. Если locked: дождись завершения текущего workflow или `systemctl --user restart hermes-gateway.service`. Live diagnostics (кто держит профиль, Xvfb screenshot, Chromium cmdline) — `references/live_diagnostics.md`.

- **Browser profile recovery: post-fix canary pattern (added 2026-06-03)** — после любой fix-операции против `browser_profile_locked` / `browser_profile_busy` (SIGTERM Playwright MainThread, restart gateway, очистка lock-файлов) **всегда** завершай проверкой `telegram_ads_workflow(workflow="snapshot")`. Это одновременно:
  1. **Smoke test** — workflow поднимет fresh chromium и пойдёт в ads.telegram.org. Если snapshot вернул structured data с `ok=true` (или хотя бы partial с accounts_analyzed ≥ 1) — recovery succeeded.
  2. **Production canary** — не «что-то поднялось», а «ads workflow реально работает end-to-end против живого dashboard». В 2026-06-03 инциденте snapshot за 06:52:34 вернул 4 accounts, 2 analyzed — что доказало не только то, что lock-файлы убраны, но и то, что Playwright driver + new chromium + login session всё ещё живы и способны к ads UI навигации.
  3. **Locks for future recovery** — если snapshot сразу возвращает `browser_profile_locked`/`browser_profile_busy`, значит есть второй gateway, который держит профиль, и поверхностный fix недостаточен. **Canary failure → investigate deeper**, не повторяй тот же SIGTERM.
  Не пропускай canary. «Lock-файлы убраны + `lsof` чистый» ≠ «ads workflow работает».
- **Колонка "Opened" отсутствует в body.inner_text()** — при значении 0 Telegram Ads рендерит пустую ячейку, не попадающую в body text. Колонки (14 шт, без Opened): title, views, clicks, actions, ctr, cvr, cpm, cpc, cpa, spent, budget, target, status, date_added.
- **Разделитель колонок: `\n\t\n` (newline + tab + newline)** — не `\n`, не `\t`. Используй `re.split(r'\n\t\n|\n\t', row_text)`.
- **Извлечение баланса:** паттерн `Budget:\s*\n(⭐️|💎)?\s*([\d,.]+)` по `body[:2000]`. TON = `💎`, STARS = `⭐️`.
- **Захардвоженная дата** -- никогда не используй фиксированные даты (2025, "1 Jan", и т.п.). Всегда `datetime.now(UTC)`.
- **Internal fallback в user-facing output** -- если tool делает внутреннюю ошибку или fallback (например, Xvfb не запущен), ответ пользователю должен быть чистым: final summary или structured error. Никаких "попробую через browser", "запущу Xvfb", "упал" в рабочем топике. Internal fallback → скрытый log. User-facing → чистый результат или `{"error": "INFRA_MISSING", "hint": "..."}`.
- **Snapshots с неверными данными (all stopped/unknown, clicks=0, wrong spend)** — если snapshot возвращает данные, не совпадающие с Telegram Ads UI, проблема в DOM-парсере `pages/account.py`. Диагностика и fix — в `references/parser_diagnostics.md`. Не делай глобальных выводов из невалидного парсинга. Перед интерпретацией snapshot данных проверь:
  * `campaigns.active > 0` — если все stopped/unknown при Active статусе в UI, ошибка парсера
  * `data_quality` — если "partial" или "unreliable", данные не использовать для выводов
  * `clicks > 0` если `impressions > 0` — clicks=0 при значимых impressions = column shift (PARSE_ANOMALY_CTR_CLICKS_MISMATCH)
  * `warnings` — проверь `PARSE_ANOMALY_COLUMN_SHIFT`, `PARSE_ANOMALY_STATUS_UNKNOWN`
  * Никогда не выводи "spent X of Y" — `budget_column_total` и `spent_total` независимые колонки
- **Unknown status treated as Stopped** — статус "Unknown" НЕЛЬЗЯ сваливать в stopped. Unknown — parse warning, не нормальный статус. `_classify_campaigns` считает Unknown отдельно в `campaigns["unknown"]`, не в `stopped`. Если старый код использует `else: stopped += 1` — это ошибка.
- **Пропуск confirmation_id при mutating action** -- агент должен сначала получить confirmation_id (вызов без него), показать пользователю, потом вызвать с ним. Не пытайся подставить confirmation_id самостоятельно.
- **Доступ DeepSeek к telegram_ads (updated 2026-06-03).** Ранее этот skill
  утверждал, что DeepSeek не имеет telegram_ads tool — это правило
  переопределено оператором 2026-06-03. Текущая реальность: DeepSeek
  Companion (`deepseek` profile) wired to SAME `telegram_ads` +
  `telegram_ads_typed` toolsets in `platform_toolsets.telegram` as
  default profile. **Read-only операции** (`status`, `snapshot_accounts`,
  `get_ad`, `get_ad_stats`, `list_ads`, `get_rejection_info`, и т.д.)
  DeepSeek **может** выполнять автономно при делегации. **Mutating**
  операции (CPM, create, edit, budget, delete, revoke) по-прежнему
  требуют per-call `confirmation_id` от the operator — DeepSeek может их
  **подготовить** через `telegram_ads_prepare_approval_request`, но
  **не может** исполнить через `telegram_ads_apply_approved_action`
  без оператора. Не ссылайся на старую формулировку "DeepSeek не имеет
  telegram_ads" — она устарела.
- **Cross-profile guard при апдейте workflow package** — `hermes_telegram_ads` живёт в `~/.hermes/profiles/deepseek/plugins/packages/hermes_telegram_ads_pkg/` (editable install shared между default + deepseek). `write_file` / `patch` на этот путь требует `cross_profile=True` + явное одобрение the operator. Не обходи guard через `terminal cat > file` — это anti-pattern. Полная процедура — `references/workflow_authoring.md` §4.
  - **Operational pattern (added 2026-06-02):** ask once **per patch series**, not once per session. If the session involves 5 cross-profile edits (browser_manager + workflow tool + tests + 2 contract changes), each series gets its own `clarify` + approval. Do not bundle them into one approval even if the user already approved "the cross-profile write" earlier in the session. A "restart" approval is also separate from a "patch" approval even if both come up in the same report.
- **Tool вернул `UNKNOWN_WORKFLOW` / `workflow: ""` / `MISSING_REQUIRED_ARG` / `browser_profile_locked` / `telegram_ads_tool_schema_defect`** — не делай restart gateway, не открывай `ads.telegram.org`, не инспектируй процессы. Используй 3-шаговую методологию в `references/tool_error_diagnostics.md`: (1) raw registry schema, (2) `get_tool_definitions` с cache clear, (3) `handle_function_call` end-to-end с mocked deps. В 80% случаев окажется, что LLM-провайдер (особенно DeepSeek / Moonshot / Xiaomi) не передал `required` параметр — это не баг tool, и фиксить нужно на уровне `model_tools.handle_function_call` hardening, не через live browser actions.
  - **Reset for restart (added 2026-06-02):** if the diagnosis says "restart the gateway" — confirm with the user. Gateway restart is a separate action from any patch approval, and the user has standing approval to spend on CPM but **not** to restart infrastructure (per Operating Discipline rule 9). See `references/restart_safety_protocol.md` for the 3-level restart-vs-kill decision tree.
- **Schema patch на `WORKFLOW_SCHEMA` ломает JSON при больших правках** — если меняешь `tools/telegram_ads_workflow_tool.py` schema block и `patch` оставляет файл в невалидном JSON-виде (extra braces, missing brackets), **откати patch и `write_file` целиком**. Всегда после schema change: `python -c "import ast; ast.parse(...)"` + import-test. Pitfalls: `references/workflow_authoring.md` §5.
- **`ACCOUNT_SCAN_FAILED` semantic для `snapshot` (added 2026-06-02)** — если `list_accounts` вернул ≥ 1 аккаунт, но **ни один** не удалось проанализировать (login/parse/budget упали на всех), workflow возвращает **не** `ok=true` с zero-aggregated metrics, а `ok=false, error="ACCOUNT_SCAN_FAILED", total=null, metrics=null, data_quality="unavailable"`. Это различает "просканировал пустой кабинет" (ok=true, нули — реальные) от "не смог ничего просканировать" (ok=false, нули — null). **Не маскируй `LOGIN_REQUIRED` / `API_ERROR` / `WORKFLOW_ERROR` в `ACCOUNT_SCAN_FAILED`** — они остаются отдельными structured errors. Полная спецификация: `references/snapshot_failure_semantics.md`.
- **`run_workflow` wrapper не пробрасывает `ok: False` в top-level envelope (PITFALL)** — `hermes_telegram_ads/workflows/__init__.py:run_workflow()` оборачивает result of `func(adapter, params)` как `{"ok": True, "workflow": ..., "data": result}` **независимо** от `result["ok"]`. Это значит что snapshot, вернувший `{"ok": False, "error": "ACCOUNT_SCAN_FAILED"}`, дойдёт до LLM как `{"ok": True, "data": {"ok": False, "error": "ACCOUNT_SCAN_FAILED", ...}}`. LLM должен проверять `data.ok` (snapshot-level success), **не** outer `ok` (workflow-didn't-crash). Tests для ACCOUNT_SCAN_FAILED вызывают `run_snapshot` напрямую, **не** через `run_workflow`, чтобы избежать путаницы. Это дизайн-wart; refactor out of scope для ACCOUNT_SCAN_FAILED patch.
- **Staged patch approval pattern (added 2026-06-03)** — the operator часто разбивает patch на Phase 1/2/3+ и одобряет каждую фазу отдельным сообщением с явным scope: файлы, **Do NOT apply** list, **cross-profile approval** one-shot per phase. Recognition pattern: "Применяй Phase 1+2", "Do NOT apply Patch X/Y", "Scope: api.py / _snapshot.py / tests related". Если the operator говорит staged — **строго** соблюдай границы фаз. Не применяй Phase 3+ даже если считаешь, что они готовы. Не додумывай "он наверное одобрил бы" — жди явное. Full protocol: `references/staged-patch-workflow.md`.
- **`TelegramAdsApiError.__init__` signature gotcha (added 2026-06-03)** — базовый класс `HermesTelegramAdsError.__init__` принимает `context: dict`, но `TelegramAdsApiError.__init__` (subclass) **shadowing** базовый — принимает `raw_response: dict` (не `context`). Если используешь `context=...` в `raise TelegramAdsApiError(...)` — `TypeError: unexpected keyword argument 'context'` пробросится выше. Всегда `raw_response={...}` для `TelegramAdsApiError`. Аналогично проверяй signature перед добавлением `context=` / `details=` в любой error subclass в этом пакете.
- **`_CSRF_RE` regex требует hex-only hash (gotcha для тестов, added 2026-06-03)** — regex `r'apiUrl":"\\?/api\?hash=([a-f0-9]+)"'` совпадает только с hex-цифрами (`a-f0-9`). Тестовые fixtures с `hash=hello` или `hash=foo123` **не** пройдут. Используй `hash=abc12345` или `hash=deadbeef`. То же относится к inline-script regex при желании проверить hash в одном тесте.
- **Mock testing pitfalls для owner_id (added 2026-06-03)** — при тестировании `bootstrap()` (3-tier owner_id extraction) мок `browser.html` вызывается **дважды**: первый для hash extraction, второй для owner_id tier 3. Если мок возвращает static html — `bootstrap()` упадёт на hash tier раньше, чем дойдёт до owner_id. Решения: (a) счётчик вызовов + разные ответы; (b) html содержит валидный hash И нужный ownerId. `read_attr` с `AsyncMock(side_effect=SelectorNotFoundError, return_value=None)` ведёт себя иначе, чем с одним `side_effect=SelectorNotFoundError` — первый сработает на вызов, второй тоже, но более явно. Не путай: `read_attr` await'ится, **оберни** в `try/except` в production коде и в тестах тоже.
- **`wait_for_function` default mock MUST handle timeout (added 2026-06-03)** — `browser.wait_for_function` в `bootstrap()` оборачивается в `try/except Exception`. AsyncMock с `side_effect=None` молча возвращает `None` (не падает), что симулирует "Aj loaded immediately". Для симуляции "Aj never loaded" нужен `side_effect=asyncio.TimeoutError()` или `asyncio.TimeoutError()`. Если хочешь протестировать "Aj loaded but apiUrl is None" — мок `wait_for_function` без side_effect, а `evaluate` возвращает None для `"state.apiUrl"`.
- **CPM minimum varies by account, not by docs (added 2026-06-05)** — `validate_ad` НЕ знает server-side минимум для конкретного кабинета и пропустит draft с CPM ниже порога. Сервер режет на `create_ad` apply: `CPM can't be less than ⭐65 (field: cpm)`. Discovered: Example Bot | Short Clips (STARS) требует CPM ≥ 65 для новых ads, при том что старые ads в этом кабинете имеют CPM=50 (вероятно, минимум подняли после их создания, или минимум per-account-state). **Правило для prepare:** если старые ads в кабинете имеют CPM=50, всё равно стартуй с 65+ для новых. Не доверяй CPM существующих ads как нижней границе. Если the operator сказал "минимально допустимый" — план на 65 для STARS-bot-кабинетов, не 50.
- **Server-side "search query can't contain less than 4 characters" при list >1 (added 2026-06-05)** — `validate_ad` локально пропускает draft с 9 search queries (все слова 4+ символа, длина каждого запроса 11+), но `create_ad` apply возвращает `api_error: Search query can't contain less than 4 characters (field: search_queries)`. Локальный чекер НЕ воспроизводит это правило. **Diagnostic pattern:** если длина всех queries и всех слов в них — 4+ символа, и validate_ad passes, а сервер всё равно режет на search_queries, минимизируй до 1 query как probe. Если с 1 query проходит — баг в server-side checker'е для multi-query inputs (возможно невидимый Unicode, возможно word-level min >4, не разбирались в этой сессии). **Не уходи в retry-loop** с теми же 9 queries — после 2-3 одинаковых failure switch на 1-query fallback и зафиксируй в отчёте.
- **Confirmation_id: одноразовый, fingerprint-bound, TTL 300s (added 2026-06-05, reinforced)** — `confirmation_id` issued в `create_ad` prepare **сгорает** на первом apply, и привязан к fingerprint params (action + serialized draft). Если между prepare и apply ты **менял хоть один байт** draft (даже text, target, query list) — apply вернёт `invalid_confirmation: Confirmation does not match action+params / fingerprint_mismatch: true`. Если ты **не успел** apply за 300s — `confirmation_id` истекает и apply вернёт `invalid_confirmation: Unknown confirmation_id`. **Operational pattern:** получи confirmation_id и сразу же apply в **буквально следующем tool call** без изменений params. Если apply упал с api_error (а не invalid_confirmation) — confirmation_id **уже сгорел** на этом apply; нужно получить **новый** confirmation_id с исправленными params и apply снова. Не пытайся retry с тем же confirmation_id.
- **`save_screenshot` typed wrapper bug (added 2026-06-05)** — `telegram_ads_save_screenshot(name=..., full_page=...)` возвращает `INTERNAL_ERROR: TelegramAdsToolset.call() got multiple values for argument 'name'` (TypeError). Связано с конфликтом `name` keyword между `TelegramAdsToolset.call()` signature и `ToolSpec.execute()` kwargs. **Workaround:** скриншот из `prepare_ad_draft` (screenshot_name param) обычно работает, т.к. идёт через другой call path. Если только `save_screenshot` нужен — это cosmetic gap, не блокер для теста flow (verification по `get_ad` / `get_ad_creative` / `get_ad_budget_status` / `list_ads` достаточно).
- **`hermes_tools.telegram_ads_*` runtime stub (added 2026-06-09)** — `hermes_tools` модуль существует только внутри Hermes gateway runtime (`agent/transports/hermes_tools_mcp_server.py`). В обычном `python3` (cron, CLI, venv) `import hermes_tools` падает с `ModuleNotFoundError`. **Не** пытайся импортировать `telegram_ads_list_accounts` / `telegram_ads_get_ad` etc. напрямую — это не работает вне gateway. Для Python-side интеграций (watcher adapter, scripts, tests) используй `hermes_telegram_ads.hermes_tools.TelegramAdsAdapter` через `BrowserProfileManager.shared().acquire_adapter()`. Это **тот же** adapter, что под капотом у типизированных tools, shared browser profile, no second Chromium. Полная integration procedure: `references/ads_watcher_integration.md`.
- **Watcher tick path: 10 async + 1 sync (added 2026-06-09)** — `TelegramAdsWatcherService` вызывает на adapter'е ровно 10 методов: 9 async (`get_ad`, `get_ad_stats`, `get_account_budget`, `get_account_stats`, `list_ads`, `list_accounts`, `get_share_stats_url`, `validate_ad`, `detect_login_state`) + 1 sync (`browser_healthy`). Если пишешь read-only adapter — все 10 должны быть реализованы. `get_account_stats` не экспортирован в `TelegramAdsAdapter`; service вызывает его только для `kind == "account_stats"` watches; допустимо вернуть dict с `url=None` + `balance/currency` из `get_account_budget()`. Service internal: `await self.adapter.<method>()` для async, `self.adapter.<method>()` для sync. Sync `browser_healthy()` — единственный sync call path, не оборачивай в `asyncio.run`.
- **WatcherScheduler.run() не существует (added 2026-06-09)** — Scheduler API: `tick()` (async, returns list[WatcherEvent]) и `run_forever()` (async, polling loop). **Нет** метода `run()`. Если видишь код который вызывает `scheduler.run()` — это legacy, используй `tick()` для one-shot и `run_forever()` для long-running. `run_forever()` требует explicit operator approval (per Operating Discipline rule 7 — infrastructure loop).
- **Watch `account_stats` kind не работает out-of-the-box (added 2026-06-09)** — `TelegramAdsAdapter` не имеет `get_account_stats()` метода. Если создать `service.create_watch(kind="account_stats", ...)` против стандартного adapter'а — tick упадёт `AttributeError`. Патч: либо реализовать `get_account_stats()` в custom adapter (через `get_share_stats_url` другого ad + `get_account_budget`), либо вообще не использовать `account_stats` kind. `references/ads_watcher_integration.md` секция "Watcher → Adapter contract" с пометкой pitiada 1.

## Examples

**Пример 1 -- диагностика отклонения.**
Вход: текст объявления отклонён. → Facts: статус `rejected`, текст содержит гарантию результата. Analysis: вероятная причина -- обещание гарантированного дохода. Recommendations: переписанный политику-комплаентный вариант (эффект: прохождение модерации; риск: ниже эмоциональный отклик; stop condition: повторное отклонение → ручной разбор; approval: не требуется для черновика). Memory: сохранить как factual ограничение политики.

**Пример 2 -- ревью + предложение изменить CPM.**
Вход: analytics JSON, кампания active, CTR ок, impressions низкие, есть task_created. → Facts: CTR 1.2%, impressions 800, CPA task_created $1.8. Analysis: ad-level в норме, объём недостаточен для теста. Recommendation: поднять CPM (эффект: больше показов; риск: рост CPA если downstream не улучшится; stop: пауза при CPA > $N или 0 task_created после M кликов; approval: **требуется**). → Approval required: оформить запрос на изменение bid. Memory: hypothesis о достаточном объёме теста.

**Пример 3 -- attribution problem.**
Вход: spend есть, но bot_starts по campaign_id = 0. → Facts: отсутствует UTM-связка. Analysis: attribution problem, ROAS недостоверен. Recommendations: внедрить UTM/campaign_id в ссылку перед масштабированием; до этого -- не наращивать бюджет. Approval: не требуется (только инструментирование). Memory: factual gap в инструментировании.

**Пример 4 -- подготовка кампании с tool actions.**
Вход: нужно создать объявление для ExampleBot. → Шаг 1: `validate_ad(draft)` для проверки текста и полей. Шаг 2: `prepare_draft(draft, screenshot_name="example_v1")` для сохранения черновика и скриншота. Шаг 3: Показать скриншот и параметры пользователю. Шаг 4: Получить approval. Шаг 5: `create_ad(draft, confirmation_id=...)` для отправки в модерацию.

## Uploaded media placement matrix

- `target_type="search"`: text/query workflow only; uploaded photo/video creatives are unsupported and must return `unsupported_media_for_target_type` before upload/checkAdPost/approval/create.
- `target_type="bots"`: bot/logo or `show_picture` workflow only; uploaded photo/video creatives are unsupported and must return `unsupported_media_for_target_type` before upload/checkAdPost/approval/create.
- `target_type="channels"`: uploaded photo/video creative placement. Use this placement for uploaded-media live-flow tests.

CPM media modifiers (`media_photo`, `media_video`) apply only when uploaded media is supported by placement (`target_type="channels"`). For search/bot targeting, estimates must set `media_supported_by_target_type=false`, `media_ignored_by_placement=true`, and recovery hint: `use target_type="channels" for uploaded media`.

