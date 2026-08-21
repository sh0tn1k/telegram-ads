---
name: operate-telegram-ads-growth-loop
description: "Autonomous Telegram Ads growth operator — campaign research, validation, watcher, decision, and reporting loop."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [telegram-ads, operator, campaign, growth, decision-engine, watcher]
    related_skills:
      - prepare-and-manage-tg-ads
      - operate-telegram-ads-cabinet
      - create-telegram-ads-campaign-workflow
      - handle-telegram-ads-review-and-declines
      - format-telegram-ads-report
      - telegram-ads-cost-modifiers
---

# Telegram Ads Autonomous Growth Operator

## Оператор полного цикла Telegram Ads

Этот skill превращает Hermes из помощника, обсуждающего каждое действие, в
специализированного Telegram Ads Campaign Operator. Оператор самостоятельно:

1. Изучает продукт, проект и Telegram assets
2. Исследует поисковые запросы и каналы/боты для таргетинга
3. Формирует изолированные рекламные гипотезы
4. Проверяет готовность назначения
5. Готовит рекламные объявления и approval requests
6. После approved action регистрирует наблюдение
7. Периодически читает статистику
8. Вычисляет показатели и определяет evidence checkpoints
9. Классифицирует результат и готовит рекомендации
10. Создаёт внутренние задачи и approval drafts
11. Уведомляет о материальных событиях

## Границы автономии

**Hermes НЕ МОЖЕТ без отдельного explicit approval:**
- Создавать или запускать live-рекламу
- Останавливать рекламу
- Менять CPM, бюджет, targeting
- Редактировать объявление
- Публиковать материалы
- Отправлять пользовательские сообщения
- Выполнять deploy
- Менять production DB или конфигурацию
- Тратить рекламный бюджет

## Маршрутизация запросов на наблюдение (CRITICAL)

Когда пользователь просит «поставить рекламу на наблюдение», «следить за
кампанией», «сообщить когда останется N Stars», или «сам решай когда
рекомендовать отключение» — используй **ТОЛЬКО** канонический путь через
Telegram Ads Targeting Operator.

### Канонический путь (единственно допустимый)

```
Найди ad_id через telegram_ads_list_ads / telegram_ads_get_ad
  → telegram_ads_register_campaign_watch(ad_id, project_id, budget_threshold_stars, ...)
  → operator зарегистрирован → подтверждение пользователю
  → operator disabled → запрос активации (НЕ скрипт!)
```

### ЖЁСТКИЙ ЗАПРЕТ — что НЕЛЬЗЯ делать

При запросе на наблюдение за рекламой **ЗАПРЕЩЕНО**:

- ❌ Писать Python-скрипт с polling loop
- ❌ Писать bash-скрипт
- ❌ Создавать cron job
- ❌ Создавать новую SQLite базу
- ❌ Запускать фоновый процесс через `terminal(background=True)`
- ❌ Использовать `delegate_task` для «создания системы мониторинга»
- ❌ Устанавливать legacy `hermes_telegram_ads.watcher`
- ❌ Запускать V1/V2 watcher параллельно с operator
- ❌ Генерировать любой код для мониторинга

### Если operator disabled

> «Operator выключен. Для наблюдения нужна однократная активация:
> `TELEGRAM_ADS_OPERATOR_ENABLED=1` + gateway restart.
> Напишите 'активируй operator', когда будете готовы.»

### Приоритет подсистем

Если существующая first-class подсистема Hermes (operator, watcher, scheduler)
может выполнить запрос — используй её. Никогда не создавай ad-hoc замену
(custom script, cron, daemon, SQLite store, polling loop).

Порядок выбора:
1. Существующий typed tool/subsystem (operator.register_campaign_watch)
2. Существующий skill/workflow (operate-telegram-ads мониторинг)
3. Configuration/activation request (если operator disabled)
4. Только затем новая разработка (если capability действительно отсутствует)

## Быстрый старт

```python
from agent.telegram_ads_operator import TelegramAdsOperatorService

operator = TelegramAdsOperatorService()

# 1. Validate campaign
is_valid, errors = operator.validate_campaign_brief(brief)

# 2. Check destination
readiness = operator.audit_destination("@mychannel", EntityType.CHANNEL)

# 3. Evaluate campaign metrics
decision = operator.evaluate_campaign(DecisionInput(
    impressions=1000, clicks=20,
    spend=5.0, cpm=2.5, budget=20.0,
    evidence_window=1000,
    attribution_quality="none",
))
```

## Структура

```
agent/telegram_ads_operator/
├── constants.py           # Платформенные контракты, лимиты, enums
├── models.py              # Pydantic модели (shared)
├── search_queries.py      # Валидатор поисковых запросов
├── target_selection.py    # Валидатор таргетинга каналов/ботов
├── destination_readiness.py  # Аудит готовности назначения
├── campaign_policy.py     # Валидатор кампании
├── decision_engine.py     # Детерминированный decision engine
├── reporting_policy.py    # Политика уведомлений
├── attribution.py         # Атрибуционный контракт
└── service.py             # Интеграционный слой
```

## Ключевые принципы

### Search Ads
- Максимум 10 запросов на объявление
- Максимум 32 символа в запросе
- Browser-style вопросы отвергаются
- Transport encoding (+, Base64) — не семантический запрос
- Язык запроса должен совпадать с языком кампании
- Один ad = одна query hypothesis (или tight cluster)

### Таргетинг
- Language mismatch и destination mismatch — HARD GATES
- Нельзя смешивать channels и bots
- Нельзя смешивать developer и marketing аудитории
- MAXIMUM isolation: 1 target = 1 ad
- TIGHT_CLUSTER: до 10 targets с одной аудиторией и интентом

### Decision Engine
- Детерминированный — никакого free LLM reasoning
- **Multi-window persistence (v2.0):** engine отслеживает consecutive breach counters
  через `MultiWindowState`. Один слабый час ≠ рекомендация остановки.
- **Threshold breach — это сигнал для анализа, а не команда остановить рекламу.**
- 250 показов → health/anomaly only
- 1000 показов → первая классификация (НЕ автостоп!)
- Недостаточно данных → silently continue
- Budget 80% → warning
- Budget 95% → urgent warning
- Spend > max_approved_loss → propose_stop
- Classification tiers:
  - `insufficient_data` — продолжаем молча
  - `early_warning` — одиночное нарушение, наблюдаем
  - `sustained_degradation` — устойчивое нарушение одной метрики (≥ warning_consecutive_windows)
  - `recommend_pause` — 2+ метрик нарушены устойчиво ИЛИ одна severe метрика устойчиво
- **Recovery сбрасывает consecutive breach counters.**
- **Автоматическая остановка всегда запрещена** — только recommendation/approval request.

### Уведомления
- Только материальные события
- Routine poll — подавляется
- Неизменённая классификация — подавляется
- Уведомления требуют bounded notification authorization

## References

- `references/platform-contract-and-authority.md` — Источники и порядок авторитетности
- `references/search-ads-query-policy.md` — Политика поисковых запросов
- `references/channel-targeting-policy.md` — Политика таргетинга каналов
- `references/bot-targeting-policy.md` — Политика таргетинга ботов
- `references/destination-readiness.md` — Проверка готовности назначения
- `references/experiment-design.md` — Дизайн изолированных экспериментов
- `references/watcher-decision-policy.md` — Политика наблюдения и решений
- `references/metrics-and-attribution.md` — Метрики и атрибуция
- `references/english-market-playbook.md` — Англоязычный рынок
- `references/approval-and-notification-policy.md` — Approval и уведомления

## Templates

- `templates/campaign-brief.yaml` — Шаблон кампании
- `templates/search-query-research.yaml` — Исследование запросов
- `templates/target-research.yaml` — Исследование таргетов
- `templates/target-cluster.yaml` — Кластер таргетов
- `templates/watch-policy.yaml` — Политика наблюдения
- `templates/campaign-decision.yaml` — Решение по кампании
- `templates/notification-authorization.yaml` — Авторизация уведомлений

## Scripts

- `scripts/validate_search_queries.py` — Валидация запросов
- `scripts/validate_target_cluster.py` — Валидация кластера
- `scripts/validate_campaign_brief.py` — Валидация кампании

## Связь с другими skills

- `prepare-and-manage-tg-ads` — оператор переиспользует для анализа метрик
- `operate-telegram-ads-cabinet` — оператор переиспользует для навигации
- `create-telegram-ads-campaign-workflow` — вызывает campaign brief validation
- `handle-telegram-ads-review-and-declines` — для ре-сабмита после rejection
- `format-telegram-ads-report` — для форматирования отчёта оператора
- `telegram-ads-cost-modifiers` — для оценки эффективного CPM

## English-Market Playbook

Для англоязычных кампаний оператор использует таксономию интентов:
```
direct_problem → creator_profession → workflow_tools → agency → distribution → broad_adjacent
```

Поддерживаемые GEO: US, UK, CA, AU. Язык ≠ GEO — проверять отдельно.

## Тесты

```bash
cd /home/hermes/.hermes/hermes-agent
python -m pytest tests/agent/telegram_ads_operator/ -v
```
