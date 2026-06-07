# KWhale — План стабилизации

> Цель: вывести проект из архитектурного тупика и привести к **стабильному само-хостинг-стримингу с LLM-рекомендациями и кэшированием треков**.
> Главный инвариант: **код должен поддерживаться слабыми моделями.** Принцип — *удалять, а не добавлять*.
> Статус: single-user (владелец). Multi-user — отложено (см. §Будущее).
> Обновлено: 2026-06-08.

## Инвариант проекта (правила, которые нельзя нарушать)
- **Один ID** везде — `navidrome_id` (он же Subsonic id). Никаких параллельных пространств ID.
- **Один формат URL** — API отдаёт только `navidrome_id`, ссылки на стрим/обложку клиент строит через Subsonic.
- **Одна LLM-модель** на всё (теги + рекомендации + чат).
- **Один интерфейс провайдера** источников музыки.
- **Один путь рекомендаций** (без дублей).
- Каждый файл читается изолированно; никакого «чёрного ящика».

## Целевая архитектура (один стек)
```
 music.dueattendant149.org ─ Caddy(HTTPS)
   /rest/* → Navidrome (один, multi-user-ready)
   /api/*  → kwhale-api (JWT поверх Navidrome)
 Flutter (Musly fork): browse/stream/cover — Subsonic → Navidrome (id = navidrome_id)
                        /api/recs /api/chat /api/discover /api/events → kwhale-api
   Postgres+pgvector: track_features, playback_events, taste_profile, recommendations, provider_track_map
   Celery worker: index_track(авто) · recommend(RAG) · acquire(ICM>Yandex>VK) · beat(taste_profile)
   MCP: только инструменты для чат-режима
   incoming → watcher(идемпотентно) → tagger → library → Navidrome scan → index(авто)
   LLM: opencode go / MiniMax M2.7 (подписка)   ·   Maloja: необязательна (статистика/импорт старой истории)
```

## Решения (зафиксировано)
| Тема | Решение |
|---|---|
| Старый стек musicbrain (v1) | Погасить целиком (с бэкапом). Maloja — необязательна. |
| Канон | kwhale = единственный проект. Привести live `/files/kwhale/server` и git-репо к одному виду. `base_url=music.dueattendant149.org`. |
| Рекомендации | RAG: pgvector-отбор → прозрачный scoring по слоям → 1 LLM-вызов. **ALS/`implicit` удалить.** |
| LLM | opencode go (подписка), модель **MiniMax M2.7**, одна на всё. Спектро/omni — удалить. |
| Источники | Модульный `BaseProvider`: **ICM (главный) → Yandex → VK (fallback)**. |
| История прослушивания | Своя таблица `playback_events` (per-user, авто). Богаче maloja. |
| Multi-user | Отложено. Схему с `user_id` сохранить (задел). |

## Рекомендательный алгоритм
Три шага (как у Spotify/YouTube: candidate-gen → ranking → re-rank):
1. **Отбор кандидатов** — pgvector kNN по `features_vector` (звук) и `lyrics_embedding` (смысл) от «центроида вкуса» юзера или от эмбеддинга текстового запроса. Фильтры: blacklist, дедуп, уже-услышанное.
2. **Оценка баллами** — прозрачная взвешенная сумма (веса = настраиваемые константы, без обучения ML):
   - аудио-сходство; смысл/текст;
   - implicit-affinity: дослушал(+), скип-рано(−), повтор(++), лайк(++);
   - время суток + день недели (данные уже есть);
   - новизна/exploration (подмешать незнакомое);
   - анти-повтор/fatigue (минус недавно услышанному/рекомендованному);
   - минус скипам и blacklist.
3. **Финальная расстановка** — 1 вызов M2.7: вход = профиль вкуса + режим (taste/preset/custom) + топ-кандидаты (≤120); выход = упорядоченные `navidrome_id` + короткое «почему». Запись в `recommendations`.

Tools/MCP — отдельно, только для интерактивного чата (Lana-style). Авто-recs без agent-loop.

## Roadmap (каждый этап отдельно проверяем и обратим)
- **Этап 0 — Канон.** Привести git-репо и live к одному виду; убрать orphan `agent.py`; зафиксировать `.env`. *Проверка:* `git status` чист, что редактируешь = что работает. *Откат:* git reset.
- **Этап 1 — Погасить v1.** Бэкап musicbrain → `compose -p musicbrain down` (без `-v`, данные сохраняются). Освободить порты. *Проверка:* music-домен жив только на v2; v1-контейнеров нет. *Откат:* `compose -p musicbrain up -d`.
- **Этап 2 — Стрим/обложки.** 302 → публичный HTTPS; API отдаёт только `navidrome_id`, клиент резолвит через Subsonic. *Проверка:* обложки+стрим с телефона по мобильному.
- **Этап 3 — Ingest.** Авто-индексация (beat/после скана); идемпотентный watcher; backfill `navidrome_id`; решить AcoustID. *Проверка:* трек в incoming → library+Navidrome+`track_features` без ручного шага.
- **Этап 4 — Рекомендации v2.** Удалить ALS/omni; реализовать RAG со слоями (§выше); per-user beat; `taste_profile`. *Проверка:* `recommendations` наполняется, `/api/recs` отдаёт играбельные id.
- **Этап 5 — Провайдеры.** `BaseProvider` = ICM+Yandex+VK(fallback); один путь acquire; убрать дубли. *Проверка:* discover→acquire по каждому.
- **Этап 6 — Multi-user.** ОТЛОЖЕНО. (Navidrome мультибиблиотеки доступны с v0.58.0; у нас 0.61.2.)
- **Этап 7 — Чистка + тесты.** Удалить мёртвый код (§ниже); smoke-тесты ключевых путей; `ARCHITECTURE.md` = реальность.

## Что удалить
`implicit`/ALS · спектро/omni/`mimo-v2-omni` · orphan `agent.py` · дубль acquire (discover↔internal) · 3 формата URL · мёртвые клиентские вызовы (vibe/similar/taste-profile/generate — либо подключить в чат, либо вырезать) · тройное дублирование recs · весь musicbrain v1 (koel/slskd/tor — заменены провайдерами).

## Будущее (отложено)
- **Multi-user (семья/друзья):** вариант А (общий каталог, личные recs/лайки/плейлисты) — база уже готова (`user_id`). Вариант Б (раздельные библиотеки) — Navidrome multi-library (admin-управляемые папки, доступ по юзеру). Рекомендуемый будущий вариант — гибрид: общая «Common» + личная библиотека у желающих.
- **Maloja:** поставить, если нужна last.fm-статистика и импорт старой истории.

## Заметки по LLM
- opencode go = подписка (фикс. цена, не per-token). Модели: GLM-5.1 / Kimi K2.6 / **MiniMax M2.7**.
- Все go-модели текстовые → мультимодаль (спектрограммы) не поддерживается → удаляем.
- Экономия лимитов: vibe-теги считать один раз при индексации (кэш в `vibe_tags`); recs = 1 вызов/юзер/день.
