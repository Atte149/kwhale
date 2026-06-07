# LLM-система рекомендаций Melorise

**Версия:** 1.0
**Дата:** 2026-05-30
**Статус:** Актуально после доработки и отладки

Этот документ полностью описывает LLM-часть системы рекомендаций Melorise:
архитектуру, все промпты, схемы инструментов, потоки данных, конфигурацию и
отладку. Он покрывает три независимых LLM-подсистемы:

1. **Tool-using агент** (`api/app/prompt_recs.py`) — free-text запрос → плейлист
2. **Генерация vibe-тегов** (`worker/app/indexer.py`) — текст песни → теги настроения
3. **Анализ спектрограммы** (`worker/app/indexer.py`) — мультимодальный разбор звучания

---

## 1. Общая картина

```
                        ┌─────────────────────────────────────────┐
                        │              ПОЛЬЗОВАТЕЛЬ                 │
                        └───────────────────┬─────────────────────┘
                                            │ POST /recs/prompt
                                            │ {"prompt": "грустный джаз для дождя"}
                                            ▼
        ┌───────────────────────────────────────────────────────────┐
        │                  API (FastAPI, async)                       │
        │  api/app/prompt_recs.py :: run_prompt_agent()               │
        │                                                             │
        │   ┌─────────────┐   tool_calls   ┌──────────────────────┐  │
        │   │  LLM (chat) │ ◄────────────► │  6 инструментов:     │  │
        │   │  llm_model  │   tool results │  search_library      │  │
        │   └─────────────┘                │  similar_by_audio    │  │
        │         │                        │  semantic_search ────┼──┼──► embedding (bge-m3)
        │         │ финальный JSON         │  filter_by_features  │  │
        │         ▼ {"track_ids":[...]}    │  filter_by_vibe_tags │  │
        │   ┌─────────────┐                │  get_taste_profile   │  │
        │   │  _finalize  │ ◄── валидация  └──────────┬───────────┘  │
        │   │ (anti-halluc)│                          │ pgvector/SQL  │
        │   └─────────────┘                          ▼               │
        └───────────────────────────────────────────┼───────────────┘
                                                     ▼
                                          ┌────────────────────┐
                                          │  PostgreSQL+pgvector│
                                          │  track_features     │
                                          └────────────────────┘

        ┌───────────────────────────────────────────────────────────┐
        │                 WORKER (Celery)                             │
        │  worker/app/indexer.py :: index_track()                     │
        │                                                             │
        │   generate_vibe_tags()  ──► LLM (text)   ──► vibe_tags      │
        │   analyze_spectrogram() ──► LLM (omni/vision) ──► spectro   │
        │   embed_text(lyrics)    ──► embedding (bge-m3) ──► vector   │
        │                                                             │
        │   Все вызовы LLM идут через llm_client (retry+backoff)      │
        └───────────────────────────────────────────────────────────┘
```

### Модели (конфигурируются через env)

| Назначение            | Переменная     | Дефолт                | Где используется            |
|-----------------------|----------------|-----------------------|-----------------------------|
| Текстовый LLM (агент) | `LLM_MODEL`    | `gpt-4o-mini`         | prompt_recs, vibe_tags      |
| Мультимодальный (omni)| `OMNI_MODEL`   | `mimo-v2-omni`        | analyze_spectrogram         |
| Эмбеддинги            | (bge-m3)       | `BAAI/bge-m3` 1024-dim| semantic_search, lyrics     |
| Базовый URL LLM       | `OPENAI_API_BASE` | `api.openai.com/v1` | все LLM-вызовы              |
| Ключ                  | `OPENAI_API_KEY`  | (пусто)             | все LLM-вызовы              |

Сервис эмбеддингов OpenAI-совместим (`/v1/embeddings`), модель `bge-m3`,
1024-dim, нормализованные векторы — совпадает со схемой `vector(1024)`.

---

## 2. Tool-using агент (prompt → плейлист)

**Файл:** `api/app/prompt_recs.py`
**Точка входа:** `run_prompt_agent(pool, prompt, limit) -> list[str]`
**Эндпоинт:** `POST /recs/prompt` (`api/app/routers/recommendations.py`)

### 2.1 Идея

Пользователь пишет свободный запрос («энергичное для пробежки», «грустный джаз
для дождливого вечера»). Запрос отдаётся LLM вместе с набором из 6 инструментов.
Модель сама решает, какие инструменты вызвать, мы выполняем их против
библиотеки/БД, отдаём результаты обратно и повторяем цикл, пока модель не вернёт
финальный плейлист.

### 2.2 Цикл агента (по шагам)

```
1. messages = [system, user_prompt]
2. Цикл до MAX_ROUNDS (6):
   a. POST /chat/completions с tools=TOOLS_SPEC, tool_choice="auto"
   b. Получаем choices[0].message
   c. Если НЕТ tool_calls → это финальный ответ:
        - парсим track_ids из JSON (_parse_ids)
        - _finalize: оставляем только id, которые реально вернули инструменты
        - возвращаем результат
   d. Если ЕСТЬ tool_calls:
        - добавляем "чистый" assistant-ход (только id/type/function)
        - для каждого вызова: выполняем инструмент (_run_tool)
        - записываем доказательства (_Evidence)
        - добавляем role="tool" с результатом
3. Если раунды кончились → возвращаем лучшие по рангу доказательства
4. Любая ошибка → _fallback (keyword-эвристика)
```

### 2.3 Защита от галлюцинаций (ключевое улучшение)

**Проблема:** LLM может выдумать `track_ids`, которых нет в библиотеке. Старый
код возвращал их как есть → клиент получал битые id, плеер падал.

**Решение:** класс `_Evidence` накапливает все id, которые инструменты реально
вернули. `_finalize()` оставляет из выбора модели только те id, что есть в
`evidence.known()`. Выдуманные id молча отбрасываются.

```python
def _finalize(chosen_ids, evidence, limit):
    known = evidence.known()           # id, которые видели инструменты
    out = [t for t in chosen_ids if t in known]   # фильтр галлюцинаций
    # добор из ранжированных доказательств до limit
    for tid in evidence.ranked():
        if tid not in out: out.append(tid)
    return out[:limit]
```

### 2.4 Ранжирование результатов (`_Evidence`)

Каждый id получает вес:
- **+1.0** за каждое появление в результатах инструмента
- **+0.5 × score** если инструмент вернул similarity-score (similar_by_audio,
  semantic_search)
- **+0.25 × (N−1)** за подтверждение разными инструментами (corroboration):
  трек, найденный и по звуку, и по смыслу текста — ранжируется выше

`ranked()` сортирует по убыванию веса, при равенстве — по порядку первого
появления. Это даёт осмысленный плейлист даже когда модель говорит просто
«используй что нашёл».

### 2.5 Системный промпт

```
Ты — музыкальный куратор сервиса Melorise. По запросу пользователя собери
лучший плейлист ИЗ ЕГО БИБЛИОТЕКИ, используя инструменты. У тебя есть поиск по
метаданным, по звучанию (аудио-вектор), по смыслу текстов, по аудио-фичам
(energy/valence/bpm) и по vibe/спектро-тегам, а также профиль вкуса. Комбинируй
НЕСКОЛЬКО инструментов для точности. Когда готов — верни ТОЛЬКО JSON:
{"track_ids": ["id1","id2", ...]} с 15-40 id из результатов инструментов.
Не выдумывай id — бери их строго из ответов инструментов.
```

> Примечание: даже если модель проигнорирует инструкцию «не выдумывай», слой
> `_finalize` гарантирует, что выдуманные id не попадут в ответ.

### 2.6 Инструменты агента (TOOLS_SPEC)

Все 6 инструментов описаны в `TOOLS_SPEC` (формат OpenAI function-calling) и
диспетчеризуются через `_DISPATCH`. Каждый возвращает `list[dict]` с полем `id`
(и опционально `score`), либо `dict` с `error`.

| Инструмент | Аргументы | Что делает | SQL/источник |
|------------|-----------|------------|--------------|
| `search_library` | `query: str` | Поиск по метаданным | Navidrome search3 |
| `similar_by_audio` | `track_id: str` | Похожие по звучанию | pgvector `features_vector <=>` |
| `semantic_search` | `text: str` | Поиск по смыслу текстов | embedding → pgvector `lyrics_embedding <=>` |
| `filter_by_features` | `energy/valence/bpm_min/max` | Фильтр по аудио-фичам | SQL диапазоны + RANDOM() |
| `filter_by_vibe_tags` | `tags: list[str]` | Поиск по vibe/спектро-тегам | JSONB + Python scoring |
| `get_taste_profile` | — | Профиль вкуса пользователя | `taste_profile` |

**Важно про `score`:** только `similar_by_audio` и `semantic_search` возвращают
`score` (косинусная близость 0..1). Он используется в `_Evidence` для взвешивания.

### 2.7 Обработка ошибок инструментов (`_run_tool`)

Каждый вызов инструмента изолирован — он НИКОГДА не роняет весь цикл агента:

```python
async def _run_tool(pool, call):
    fn = call["function"]["name"]
    impl = _DISPATCH.get(fn)
    if impl is None:
        return {"error": f"unknown tool: {fn}"}   # неизвестный инструмент
    try:
        args = json.loads(arguments or "{}")
    except Exception:
        args = {}                                  # битые аргументы → {}
    try:
        return await impl(pool, **args)
    except TypeError as e:
        return {"error": f"bad arguments for {fn}: {e}"}  # лишние/неверные args
    except Exception as e:
        return {"error": f"{fn} failed: {e}"}      # любая ошибка → payload модели
```

Ошибка инструмента превращается в payload, который модель читает и реагирует
(например, пробует другой инструмент), а не в краш запроса.

### 2.8 Совместимость с tool_calls (`_clean_tool_calls`)

Некоторые LLM-шлюзы возвращают 500, если в assistant-ходе переотправить
провайдер-специфичные поля (`reasoning_content`, `tool_calls[].index`). Поэтому
перед переотправкой оставляем только `id`/`type`/`function`:

```python
{"id": c["id"], "type": "function",
 "function": {"name": ..., "arguments": ...}}
```

### 2.9 Fallback без LLM (`_fallback`)

Если `OPENAI_API_KEY` не задан или LLM/инструменты недоступны — работает
keyword-эвристика: слова запроса матчатся против vibe/спектро-тегов, добор
случайными треками до `limit`. Сервис всегда возвращает что-то осмысленное.

---

## 3. Генерация vibe-тегов (worker)

**Файл:** `worker/app/indexer.py :: generate_vibe_tags()`

Текстовый LLM получает исполнителя, название и фрагмент текста песни, возвращает
5–8 тегов настроения/атмосферы.

### Промпт

```
Song: {artist} — {title}
Lyrics excerpt:
{lyrics[:500]}

Generate 5–8 short descriptive tags for this song's mood, atmosphere, and feel.
Return ONLY a JSON array of strings, no explanation.
Example: ["melancholic","rainy","introspective"]
```

### Параметры вызова
- `model = LLM_MODEL`, `max_tokens = 160`, `timeout = 30s`
- Вызов через `llm_client.chat_completion()` (retry+backoff)
- Парсинг: `extract_string_list()` → `_clean_tags()` (см. §5)

---

## 4. Анализ спектрограммы (worker, мультимодальный)

**Файлы:** `worker/app/indexer.py :: build_spectrogram_png() + analyze_spectrogram()`

### Поток
1. `build_spectrogram_png()` — Essentia рендерит log-mel спектрограмму ~45с из
   центра трека (96 mel-полос, 22050 Гц) в PNG 512×256.
2. `analyze_spectrogram()` — PNG в base64 отправляется omni-модели как
   `image_url`, она описывает звучание и даёт 4–8 аудио-тегов.

### Промпт (RU)

```
Это mel-спектрограмма трека «{artist} — {title}» (горизонталь = время,
вертикаль = частота, ярче = громче). Опиши звучание по спектру одним
предложением (desc) и дай 4-8 коротких аудио-тегов (плотный бас / яркие верха /
тёплый / динамичный и т.п.). Ответь ТОЛЬКО компактным JSON без рассуждений:
{"desc":"...","tags":["...","..."]}
```

### Параметры вызова
- `model = OMNI_MODEL`, `max_tokens = 1200`, `timeout = 120s`
- `response_format = {"type": "json_object"}`
- **`max_tokens` поднят с 800 до 1200** — на 800 JSON обрезался посреди массива
  тегов (это и порождало баг с мусорными тегами).
- Парсинг: `parse_spectro()` из `tagging.py` (см. §5).


---

## 5. Устойчивый парсинг JSON (ключевое исправление)

**Файлы:** `worker/app/json_utils.py`, `api/app/json_utils.py` (зеркальные копии),
`worker/app/tagging.py`

LLM часто возвращают JSON в markdown-блоке, с префиксом-прозой или обрезанным на
полуслове (когда упёрлись в `max_tokens`). Раньше парсинг был ad-hoc и порождал
баг загрязнения тегов. Теперь вся логика централизована.

### 5.1 Публичный API

| Функция | Назначение |
|---------|------------|
| `extract_json(content)` | dict/list/None — лучший парс любого JSON |
| `extract_json_object(content)` | только dict или None |
| `extract_json_array(content)` | только list или None |
| `extract_string_list(content, key)` | list[str], терпит обрезанный JSON |

### 5.2 Как работает (стратегия)

1. **Прямой парс** очищенного от markdown-блоков текста.
2. **Сбалансированный спан** — поиск первого `{...}`/`[...]` с учётом строк и
   экранирования (фигурные скобки внутри строк не сбивают счётчик глубины).
3. Для `extract_string_list` при обрезанном JSON — читаем **только строковые
   литералы внутри спана массива**, начиная с `"key":[`. Останавливаемся на
   первом неполном литерале.

### 5.3 Почему это чинит баг с мусорными тегами

**Старый код** искал все строки в кавычках после `"tags"` через regex и
захватывал сам ключ `"tags"`, а также ключи следующих полей (`"confidence"`) и
числа (`"0.9"`):

```python
# БЫЛО (баг):
tags = re.findall(r'"((?:[^"\\]|\\.)+)"', blob[blob.find('"tags"'):])
# {"tags":["rock"],"confidence":0.9}  →  ["tags","rock","confidence"]  ❌
```

**Новый код** читает только значения внутри границ массива и прогоняет через
`clean_tags()`:

```python
# СТАЛО:
extract_string_list('{"tags":["rock","ener', key="tags")  →  ["rock"]  ✅
extract_string_list('{"tags":["rock"],"confidence":0.9}', key="tags")  →  ["rock"]  ✅
```

### 5.4 `clean_tags()` — финальный фильтр (`tagging.py`)

Даже после парсинга теги нормализуются и чистятся:
- нижний регистр, обрезка кавычек/пробелов
- выброс структурных стоп-слов: `tags`, `desc`, `mood`, `null`, …
- выброс чисто числовых/пунктуационных фрагментов (`0.9`, `:`)
- выброс слишком длинных (>40 символов)
- дедупликация с сохранением порядка, максимум 8 тегов

Это **страховочный слой**: даже если парсер что-то пропустит, мусор не дойдёт до БД.

---

## 6. Общий LLM-клиент (worker)

**Файл:** `worker/app/llm_client.py`
**Точка входа:** `chat_completion(messages, model, ...) -> dict`

Централизует все chat-вызовы воркера (vibe-теги, спектрограмма):

- **Переиспользует** один module-level `httpx.Client` (нет churn сокетов и
  утечки соединений — это была одна из критических проблем).
- **Retry** транзиентных ошибок (429, 5xx, таймауты, обрывы) с экспоненциальным
  backoff + jitter. Уважает заголовок `Retry-After`.
- **Fail-fast** на не-retryable 4xx (кроме 429) — баги всплывают, а не маскируются.
- **Raises `LLMError`** при отсутствии ключа, неожиданной форме ответа или
  исчерпании ретраев.

### Параметры retry (env)

| Переменная | Дефолт | Назначение |
|------------|--------|------------|
| `LLM_MAX_ATTEMPTS` | 4 | макс. число попыток |
| `LLM_RETRY_BASE_DELAY` | 1.0 | базовая задержка backoff (сек) |
| `LLM_RETRY_MAX_DELAY` | 20.0 | потолок задержки (сек) |

> На стороне API (`prompt_recs.py`) используется собственный async-`httpx`-цикл,
> т.к. агент работает в event loop. Логика чистки tool_calls и парсинга та же.

---

## 7. Отладка

### 7.1 Где смотреть логи

| Симптом | Где искать | Сообщение |
|---------|------------|-----------|
| Агент молча отдаёт fallback | логи api (`server-api-1`) | `[agent] HTTP ...` / `[agent] run failed` |
| Пустые vibe-теги | логи worker (`server-worker-1`) | `generate_vibe_tags failed: ...` |
| Пустой спектро-разбор | логи worker | `analyze_spectrogram failed: ...` |
| Эмбеддинги не считаются | логи worker/api | `embed_text failed` / `[agent] embed_query failed` |

```bash
# Логи контейнеров (нужен sudo на этом хосте)
sudo docker logs -f --tail=100 server-api-1
sudo docker logs -f --tail=100 server-worker-1
```

### 7.2 Ручная проверка эмбеддингов

```bash
curl -s -X POST http://localhost:8095/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model":"bge-m3","input":"меланхоличный джаз"}' | python3 -m json.tool
# Ожидаем: data[0].embedding длиной 1024, L2-norm ≈ 1.0
```

### 7.3 Ручная проверка агента

```bash
# Нужен валидный JWT в $TOKEN
curl -s -X POST http://localhost:19000/recs/prompt \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"спокойное пианино для работы","limit":20}' | python3 -m json.tool
```

### 7.4 Типичные проблемы

| Проблема | Причина | Решение |
|----------|---------|---------|
| Все запросы → fallback | `OPENAI_API_KEY` пуст | задать ключ в `worker/.env` и `api/.env` |
| 500 от LLM-шлюза | переотправка `reasoning_content`/`index` | уже чинится `_clean_tool_calls` |
| Обрезанные теги | малый `max_tokens` | поднят до 1200 для omni; `clean_tags` страхует |
| Битые id у клиента | галлюцинации модели | `_finalize` фильтрует по `evidence.known()` |
| Пустой semantic_search | embedding недоступен | проверить `EMBEDDING_API_URL` (§ топология) |

### 7.5 Запуск тестов

```bash
# Воркер (json_utils, tagging, llm_client): 43 теста
python3 -m pytest worker/tests/ -v

# API (агент: evidence, finalize, parse_ids, clean_tool_calls): 20 тестов
python3 -m pytest api/tests/ -v
```

> Тесты двух сервисов запускаются **раздельно** — оба содержат пакет `app`,
> при совместном сборе pytest возникает коллизия имён.

---

## 8. Журнал исправлений (что изменено при доработке)

| # | Что | Файл | Эффект |
|---|-----|------|--------|
| 1 | Устойчивый парсинг JSON | `json_utils.py` (×2) | конец багу мусорных тегов |
| 2 | `clean_tags()` — фильтр-страховка | `tagging.py` | мусор не доходит до БД |
| 3 | Общий LLM-клиент с retry/backoff | `llm_client.py` | нет утечки соединений, устойчивость |
| 4 | Anti-hallucination в агенте | `prompt_recs.py` | выдуманные id отбрасываются |
| 5 | Ранжирование `_Evidence` | `prompt_recs.py` | осмысленный порядок плейлиста |
| 6 | Изоляция ошибок инструментов | `prompt_recs.py` | один битый tool не роняет запрос |
| 7 | `max_tokens` 800→1200 (omni) | `indexer.py` | JSON не обрезается |
| 8 | `peak > 0` → `peak > 1e-9` | `indexer.py` | нет NaN при нормализации тихого аудио |
| 9 | Маркер `index_status='failed'` | `indexer.py`, `tasks.py`, `init.sql` | конец бесконечному циклу индексации |
| 10 | `extra="ignore"` в Settings | `config.py` | API не падает от лишних env-переменных |

