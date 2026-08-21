# Approved copy examples (2026-08-12, проверены валидатором)

Все тексты ниже прошли `telegram_ads_prepare_copy_variants` → `valid: true, violations: []`.

## @example_news (AI & Tech news, EN)

Итоговый текст (одобрен оператором, вариант 2):
> 🤖 AI & tech news, updated daily: latest breakthroughs and emerging trends in artificial intelligence.

Отвергнутые по пути:
- «The #1 AI news source! …» — «не номер один» (непроверяемое превосходство).
- «Stay ahead with daily AI news…» — «без призыва» (императив/CTA).
- Многострочная версия — `Text contains line breaks (forbidden)`.

## Портфель: 6 каналов (разная структура на каждом)

- **@example_cn_ai**: `China's AI and technology landscape, covered daily in Chinese: models, robotics, semiconductors, startups and the country's tech giants.` (перечисление через двоеточие)
- **@example_kr_ai**: `Korean-language updates on AI and technology, with a focus on China's models, robotics and semiconductor industry.` (language-first framing)
- **@example_builders**: `AI news for people who build and manage with AI — practical takes on prompting, projects and how the field is changing professional work.` (audience-first, тире)
- **@example_football**: `⚽ From every corner of the globe — daily football news and transfer talk. Matchdays, lineups, injuries and the biggest stories in the game 🌍` (эмоциональный, две части)
- **@example_league**: `🔴 One league. One passion. Premier League news, results and analysis for fans everywhere — matchdays, transfers and the stories behind English football ⚽` (короткие сегменты)
- **@example_oss**: `A curated look at interesting open-source projects on GitHub — new tools, AI agents and developer utilities.` (curated-look framing)

## Футбол: фанатский стиль (как в примере «Red Devils», но без «#1»)

Оба прошли валидацию (одна строка, ≤160):

- **@example_league**: `🇬🇧 Premier League fans, this is your home! Full coverage of England's biggest league: matchdays, transfers, results and analysis. 🔥`
- **@example_football**: `🌍 Football never stops: transfers, matchdays and the biggest stories in the game — daily news from leagues around the world ⚽`

## Выводы валидатора (точные violation-строки)

```
Text length 189 exceeds limit 160
Text contains line breaks (forbidden)
```

Оба violations появляются ВМЕСТЕ для многострочного длинного текста; чинится
флэттен-в-одну-строку + триммингом, после чего re-validate.
