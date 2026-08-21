# Telegram Ads Watcher Integration (Hermes)

Долговременная заметка про подключение `hermes_telegram_ads.watcher`
(полит в `telegram-ads-upstream`) к Hermes: какой канонический
путь вызова `telegram_ads_*` операций, как обернуть в read-only adapter,
как не запустить scheduler против живого кабинета случайно.

Pinned на момент интеграции:
`telegram-ads-upstream @ d6f7cdb66fff08e6210a117c5a099bcb8cdce883`
(пакет `hermes_telegram_ads 0.1.0`).

## Зачем watcher

- Периодический (по `interval_sec`) polling Telegram Ads без активного участия агента.
- 62 capabilities описаны в `list_tool_coverage()`. Categorization:
  - `direct_watch`: 13 (snapshot + event emit)
  - `snapshot_only`: 10 (single read, no event)
  - `not_applicable`: 19 (no read surface)
  - `forbidden_in_watcher`: 11 (все 11 mutation-тулов — watcher **никогда** их не вызывает)
  - `post_action_verification`: 9 (read после одобренной мутации)
- Watcher не делает мутаций. Это структурная гарантия — `TelegramAdsWatcherService`
  ловит `forbidden_in_watcher` capabilities и не создаёт для них watches.

## Канонический путь к Telegram Ads из Python

**НЕ пытайся** импортировать `hermes_tools.telegram_ads_*` — это runtime stub,
который инжектится только в gateway context (`agent/transports/hermes_tools_mcp_server.py`).
В обычном `python3` (CLI, cron, venv) `import hermes_tools` падает с
`ModuleNotFoundError`.

**Правильный путь** — `TelegramAdsAdapter` из `hermes_telegram_ads.hermes_tools`:
он async, разделяет `BrowserProfileManager` с типизированными tools, и
создаётся через singleton:

```python
from hermes_telegram_ads.hermes_tools import (
    BrowserProfileManager,
    TelegramAdsAdapter,
    TelegramAdsConfig,
)

manager = BrowserProfileManager.shared()  # singleton
config = TelegramAdsConfig.default()       # или from shared config
adapter = await manager.acquire_adapter(config=config)
# теперь adapter.get_ad / list_ads / ... доступны
```

Это **тот же** adapter, который `tools/telegram_ads_typed_tool.py`
оборачивает в 57 typed tools (`telegram_ads_list_accounts`,
`telegram_ads_get_ad`, ...). Browser profile — shared, persistent,
никаких дубликатов Chromium.

### Watcher → Adapter contract

`TelegramAdsWatcherService` вызывает **ровно 10** методов на adapter'е
(verified из исходников `watcher/service.py`):

| Метод | Async? | Назначение |
|---|---|---|
| `get_ad(ad_id)` | async | AdDetail по одному объявлению |
| `get_ad_stats(ad_id)` | async | статистика одного объявления |
| `get_account_budget()` | async | баланс кабинета |
| `get_account_stats()` | async | URL публичной статистики кабинета (см. пitiada 1) |
| `list_ads()` | async | список объявлений |
| `list_accounts()` | async | список кабинетов |
| `get_share_stats_url(ad_id)` | async | share URL для объявления |
| `validate_ad(draft)` | async | локальная policy-проверка черновика |
| `detect_login_state(navigate=True)` | async | login state detect |
| `browser_healthy()` | **sync** | quick health check (НЕ async) |

Pitiada 1: `get_account_stats()` **не экспортирован** в `TelegramAdsAdapter`;
service вызывает его только для `kind == "account_stats"` watches. Адаптер
должен вернуть dict с полем `url` (или `None`). Реализация: синтез из
`get_account_budget()` + `url=None` — допустимо.

### Mutation guard (защита от write)

Watcher не вызывает mutation tools. Чтобы при дальнейшем расширении
(wire'е в другие части Hermes) случайно не дать ему путь к мутации,
**adapter должен явно запрещать** все 17 имён из
`FORBIDDEN_MUTATION_TOOLS`:

```
create_ad, edit_ad, change_cpm, add_to_budget, withdraw_from_budget,
start_ad, stop_ad, delete_ad, set_budget, archive_ad, set_schedule,
set_targeting, set_conversion_event, set_pixel, apply_approved_action,
login_start, login_submit_phone
```

Реализация: `__getattr__` + frozenset + `MutationForbiddenError`.
Без `__getattr__` — `AttributeError` тоже допустим, но явный
`MutationForbiddenError` лучше для диагностики (говорит "это не
забытый метод, это запрещённый mutation path").

### Consumer (event routing)

`WatcherScheduler.tick()` возвращает `list[WatcherEvent]`. Consumer
получает events и роутит по `event_type`. Канонический route table:

| event_type | Действие (read-only) |
|---|---|
| `ad_approved` | internal notification (the operator / dashboard) |
| `ad_declined` | review task в the agent queue, **НЕ** auto-resubmit/recreate |
| `budget_low` | draft approval request (вид "increase_budget") — без mutation |
| `account_balance_low` | draft approval request (вид "top_up_account") |
| `post_action_verified` | mark verified в project state |
| `post_action_not_verified` | diagnostic task |
| `login_required` | manual login alert (никогда auto-login) |
| `watch_error` | operational alert |

**Не вызывай mutation tools** ни в одном handler'е. Consumer — это
только планирование + репортинг, не действие.

## Hermes integration wiring (готовый паттерн)

Файл `ads_watcher_integration.py` в корне `hermes-agent/`:

```
build_wiring(adapter=None, project_id, store_path, poll_interval_sec)
  → AdsWatcherWiring(store, service, scheduler, adapter, consumer)

run_once(wiring)            # async one-shot: tick() + route
run(wiring)                 # DEPRECATED, raises RuntimeError

smoke_checks()              # 8 read-only проверок
```

### Idle mode (default)

`adapter=None` → adapter в idle mode. `get_account_budget()` etc. падают
с `RuntimeError` → `TelegramAdsWatcherService._run_account` ловит это
внутренне, возвращает `events=[]`. Tick чистый no-op, **0 network calls**.

Это позволяет иметь `build_wiring()` в прод-коде без риска реального
polling'а. Production wire требует:
1. Явное одобрение оператора на запуск против реального кабинета.
2. Реальный `TelegramAdsAdapter`, полученный через
   `BrowserProfileManager.shared().acquire_adapter()`.
3. `WatcherScheduler.run_forever()` в gateway event loop (НЕ в `run()` —
   `run()` явно disabled).

### DB

`SQLiteWatcherStore` создаёт файл лениво при первом обращении. По
умолчанию: `~/.hermes/data/ads_watcher.sqlite3` (overridable через
`HERMES_ADS_WATCHER_DB`). Schema — internal to package, не редактируй
напрямую; используй `service.consume_event()` / `service.list_events()`
для работы с events.

## Smoke checks (8 пунктов, все read-only)

```bash
cd /home/hermes/.hermes/hermes-agent && python3 ads_watcher_integration.py
```

Ожидаемый output:

```
smoke_checks:
  build_wiring_creates_all_pieces: PASS
  adapter_exposes_readonly_methods: PASS
  mutation_guard_hard_fails: PASS
  scheduler_tick_idle: PASS (events=0)
  run_once_idle: PASS (events=0)
  consumer_routes_ad_declined: PASS
  consumer_routes_budget_low: PASS
  coverage_count_is_62: PASS
```

Все 8 PASS означает: wiring собран, adapter read-only, scheduler не
дёргает Telegram Ads в idle, consumer роутит synthetic events без
мутации, coverage не сломан.

### Pitfalls при сборке smoke-теста

- `WatcherEvent.source` — это `Literal['telegram_ads_watcher']`, не
  произвольная строка. Pydantic упадёт на `"test"`.
- `WatcherEvent.created_at` — `datetime`, не `None` (default factory есть,
  но explicit `None` режет validation).
- `WatcherEvent.dedupe_key` — required `str`, не `None`.
- `WatcherEvent.id` — required, придумай UUID-подобную строку.
- `WatcherEvent.consumed_at` — опциональный `datetime | None`, default
  `None` — это OK.

## Типичные сценарии использования

### "Хочу алертить когда объявление declined"

1. `service.create_watch(kind="ad_status", ad_id=N, interval_sec=900)` —
   watch за статусом конкретного объявления каждые 15 минут.
2. На `ad_declined` event consumer'а → `create_review_task` (в the agent queue
   через Task Board или local file).
3. **Не** вызывай `edit_ad` / `create_ad` из consumer'а — это требует
   approval и отдельного step'а.

### "Хочу мониторить баланс кабинета"

1. `service.create_watch(kind="account_budget", account_id=..., interval_sec=600)`.
2. На `account_balance_low` event → draft approval request на top-up
   (оператор одобрит → ручной перевод → `telegram_ads_get_account_budget()`
   подтвердит новое значение).

### "Хочу verify что одобренная мутация реально применилась"

1. После одобренного `change_cpm` / `add_to_budget` / etc. →
   `service.create_post_action_watches(action="change_cpm", ad_id=N, expected={...})`.
2. Watcher создаст 9 `post_action_verification` watches (read-only checks).
3. На `post_action_verified` → mark verified. На `post_action_not_verified`
   → diagnostic task (НЕ rollback — rollback требует approval).

## Pitiada: что НЕ делать

- **Не** вызывай `WatcherScheduler.run_forever()` без явного одобрения
  оператора. Это polling loop, который зацикливается на `interval_sec`.
- **Не** создавай watches для реальных `ad_id` / `account_id` пока
  оператор не подтвердил что хочет мониторить именно их.
- **Не** вызывай mutation tools из consumer'а (route handlers).
- **Не** редактируй SQLite файл напрямую — используй `service` API.
- **Не** импортируй `hermes_tools.telegram_ads_*` — это runtime stub,
  падает с `ModuleNotFoundError` вне gateway.
- **Не** полагайся на `validate_ad` как полный coverage Telegram Ads policy —
  есть server-side правила (см. umbrella skill: "Server-side search query
  can't contain less than 4 characters" — local checker не покрывает).
- **Не** путай `WatcherScheduler.run()` (НЕ существует — это `run_forever`
  или `tick`) и `telegram_ads_create_ad` (есть, но mutation).
- **Не** храни raw `account_token` в events/watches — service хеширует
  через `hash_account_token()` и пишет `account_token_hash` в store.
  `account_token` raw — только в параметрах `create_watch`, не в
  `WatchSpec` (нет такого поля).

## Связанные skills / tools

- `install-hermes-telegram-ads-watcher` — install procedure (pinned commit).
- `operate-telegram-ads` (devops) — low-level telegram_ads toolset ops.
- `plugin-package-stability-audit` — если watcher сломался upstream,
  audit против `telegram-ads-upstream`.
- `telegram-ads-workflow` typed tool — для snapshot/inspect_ad это всё ещё
  primary read path. Watcher — для continuous monitoring, не для
  on-demand анализа.
