# Direct Playwright Recovery — Telegram Ads Snapshot

## Когда это нужно

`telegram_ads` и `telegram_ads_workflow` инструменты недоступны:
- числятся в tool-листе как parameterless (`properties: {}`)
- Gateway держит browser profile locked
- Нужен срочный read-only snapshot без ожидания фикса

## Обходной путь

Прямой запуск Playwright `headless=True` + парсинг dashboard `body.inner_text()`.

## Шаг 1: Освободить browser profile

```bash
# Найти и убить Playwright driver'ы, держащие лок
ps aux | grep 'playwright/driver' | grep -v grep
kill -9 <PID1> <PID2> ...
# Проверить
ps aux | grep 'telegram_ads/browser_profile' | grep -v grep | wc -l
# → 0
```

Gateway (`hermes gateway`) автоматически перезапустит свой Playwright,
но на время snapshot'а profile свободен.

## Шаг 2: Playwright контекст

```python
from playwright.async_api import async_playwright

PROFILE_DIR = "/home/hermes/.hermes/data/telegram_ads/browser_profile"
BASE_URL = "https://ads.telegram.org"

async with async_playwright() as p:
    ctx = await p.chromium.launch_persistent_context(
        user_data_dir=PROFILE_DIR,
        headless=True,  # Xvfb не нужен
        viewport={"width": 1440, "height": 1000},
        args=["--no-sandbox"],
    )
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
```

## Шаг 3: Получить список кабинетов

```python
await page.goto(f"{BASE_URL}/account", wait_until="networkidle", timeout=30000)
await page.wait_for_timeout(2000)

acct_name = page.locator("span.pr-header-account-name")
if await acct_name.count() == 0:
    print("NOT_LOGGED_IN")

await acct_name.first.click()
await page.wait_for_timeout(1000)

items = await page.locator("a[href*='/choose_account/']").all()
accounts = []
for item in items:
    href = await item.get_attribute("href") or ""
    text = (await item.inner_text()).strip()
    token = ""
    if "/choose_account/" in href:
        token = href.split("/choose_account/")[1].split("?")[0]
    if text and "Create a new" not in text:
        accounts.append({"title": text, "token": token})

await page.keyboard.press("Escape")
```

## Шаг 4: Переключить кабинет и получить баланс

```python
await page.goto(f"{BASE_URL}/choose_account/{token}?to=account",
                wait_until="networkidle", timeout=30000)
await page.wait_for_timeout(2000)

body = await page.locator("body").inner_text()

import re
bal_m = re.search(r'Budget:\s*\n(⭐️|💎)?\s*([\d,.]+)', body[:2000])
if bal_m:
    bal = float(bal_m.group(2).replace(',', ''))
    sym = (bal_m.group(1) or "")
    cur = "TON" if '💎' in sym else "STARS"
```

## Шаг 5: Парсинг таблицы кампаний

Формат dashboard table в `body.inner_text()` — разделитель `\n\t\n`.

Колонки (14 шт, без "Opened"):
1. title → 6. cvr → 11. budget_remaining
2. views → 7. cpm → 12. target
3. clicks → 8. cpc → 13. status
4. actions → 9. cpa → 14. date_added
5. ctr → 10. spent

```python
COL_SEP = '\n\t\n'

def parse_row(raw):
    parts = raw.split(COL_SEP)
    if len(parts) < 12:
        return None

    def parse_num(s):
        s = s.strip().replace(',','').replace('⭐️','').replace('💎','').replace('–','0')
        try: return float(s) if '.' in s else int(s)
        except: return 0

    return {
        "title": parts[0].strip(),
        "views": parse_num(parts[1]),
        "clicks": parse_num(parts[2]),
        "actions": parse_num(parts[3].split('\n')[0]),
        "ctr_pct": parse_num(parts[4].replace('%','')),
        "cvr_pct": parse_num(parts[5].replace('%','')),
        "cpm": parse_num(parts[6]),
        "cpc": parse_num(parts[7]),
        "cpa": parse_num(parts[8]),
        "spent": parse_num(parts[9]),
        "budget_remaining": parse_num(parts[10]),
        "target": parts[11].strip(),
        "status": parts[12].strip(),
    }
```

## Полный скрипт сбора snapshot

Лучше собирать в `execute_code` (Python, asyncio).

```python
async def get_snapshot():
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR, headless=True,
            viewport={"width": 1440, "height": 1000},
            args=["--no-sandbox"],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        await page.goto(f"{BASE_URL}/account", ...)
        accounts = await get_accounts(page)
        result = []

        for acct in accounts:
            await page.goto(f"{BASE_URL}/choose_account/{acct['token']}?to=account", ...)
            body = await page.locator("body").inner_text()
            # extract balance
            # find "AD TITLE" in body → split by \n\n\n → parse rows
            # classify statuses, compute totals
            result.append({...})

        await ctx.close()
        return {"accounts": result, "total": {...}}
```

## Известные проблемы

1. **Ad IDs** — href'ы ссылок `/account/ad/{id}` могут не совпадать с порядком строк в таблице. Для точной привязки — парсить href каждой строки.
2. **Budget column** — отображаемый остаток, не лимит. Не выводи "spent X of Y".
3. **Status "In Review"** — считается в `active` для мониторинга.
4. **После snapshot** — Gateway может не сразу перезапустить Playwright. Следующий вызов `telegram_ads` может пересоздать контекст — нормально.
