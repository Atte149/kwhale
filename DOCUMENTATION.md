# KWhale — Документация сервиса

Самостоятельный музыкальный сервис: свой стриминг + ИИ-рекомендации, поиск по
смыслу текстов и по звучанию (спектрограммы), агент с инструментами, скачивание
из стримингов, мобильный клиент с виджетом и Bluetooth-тегом.

---

## 1. Архитектура (что из чего состоит)

```
┌─────────────────────────────────────────────────────────────┐
│  Caddy (reverse-proxy)   music.dueattendant149.org           │
│   /rest/*  → Navidrome (Subsonic: вход, библиотека, стрим)   │
│   всё прочее → kwhale-api (умные функции)                     │
└───────────────┬─────────────────────────────────────────────┘
                │
   ┌────────────┴───────────┬──────────────┬──────────────┐
   ▼                        ▼              ▼              ▼
 Navidrome              kwhale-api      embedding       MCP
 (медиа-движок,         (FastAPI:       (bge-m3,        (для внешних
  Subsonic API,          /recs,/library  1024-dim,       ИИ-агентов)
  стрим, обложки)        /events,/stream OpenAI-совм.)
                         /discover)
   │                        │
   ▼                        ▼
 PostgreSQL+pgvector ◄── worker (Celery)
 (track_features,         - Essentia: BPM/энергия/тональность + 20-мерный вектор
  playback_events,        - bge-m3: эмбеддинг текста (1024)
  recommendations,        - спектрограмма (mel) → mimo-v2-omni: описание звука
  taste_profile)          - vibe-теги (LLM)
   ▲                      - рекомендации (ALS + контент + персональный слой)
   │                      - скачивание из ICM/Yandex (плагины)
 Redis (очередь Celery)   tagger (авто-теги входящих файлов)
```

### Контейнеры (docker compose, каталог `/files/kwhale/server`)

| Сервис | Назначение | Порт (на хосте) |
|---|---|---|
| `navidrome` | медиа-движок, Subsonic API, стриминг, обложки | 4533 (внутр.) |
| `api` | kwhale-api (FastAPI) — все умные эндпоинты | 19000 |
| `worker` | Celery: индексация, рекомендации, скачивание | — |
| `worker-beat` | планировщик (ночное обновление профиля вкуса) | — |
| `embedding` | bge-m3 (sentence-transformers), эмбеддинги текста | 8095 |
| `mcp` | MCP-сервер (инструменты для внешних ИИ-агентов) | 8090 |
| `tagger` | авто-тегирование скачанных файлов | — |
| `postgres` | PostgreSQL 16 + pgvector | — |
| `redis` | брокер очереди Celery | — |

### Учётные данные
- Вход в приложение / Subsonic / API: **vladik / melorise**
- Домен: **https://music.dueattendant149.org**

---

## 2. Сборка APK (пошагово)

Клиент — форк Flutter-приложения **Musly** (OpenSubsonic-клиент), переименован в
**KWhale**. Каталог: `/files/kwhale/client` (он же канонический; копия в
`~/kwhale-client`).

### Окружение (один раз)
```bash
export PATH="$PATH:/home/dueattendant149/flutter/bin"
export ANDROID_HOME=/home/dueattendant149/android-sdk
export ANDROID_SDK_ROOT=/home/dueattendant149/android-sdk
```
Flutter лежит в `/home/dueattendant149/flutter`, Android SDK в
`/home/dueattendant149/android-sdk`.

### Сборка
```bash
cd /files/kwhale/client
flutter pub get                       # зависимости (один раз / после правок pubspec)
flutter build apk --release           # один APK (~62 МБ)
# либо по архитектурам (меньше размер):
flutter build apk --release --split-per-abi
```

Результат: `build/app/outputs/flutter-apk/app-release.apk`
(или `app-arm64-v8a-release.apk` и т.д.).

### Распространение
```bash
cp build/app/outputs/flutter-apk/app-release.apk /files/kwhale/apk/kwhale-latest.apk
# загрузка в OwnCloud:
curl -u "vladik:melorise" -T /files/kwhale/apk/kwhale-latest.apk \
  "https://cloud.dueattendant149.org/remote.php/dav/files/vladik/KWhale/kwhale-latest.apk"
```
Скачать на телефон: облако → папка `KWhale` → `kwhale-latest.apk`, установить
(разрешить установку из неизвестных источников).

### Типичные проблемы сборки
- **Дубли функций в `MusicService.kt`** (две `updateMetadata` / `updatePlaybackState`)
  — признак повреждения файла прерванным редактированием. Лечится копией из
  чистого источника (`~/kwhale-client`) или `git checkout HEAD -- <файл>`.
- **Не находит модель/символ** — `flutter clean && flutter pub get`.
- Виджет не появляется в списке — проверить, что в `AndroidManifest.xml` есть
  `<receiver android:name=".KWhaleWidgetProvider">` с `appwidget-provider`.

---

## 3. Что изменено относительно оригинального Musly

KWhale = Musly + серверная «умная» часть + три фичи в клиенте.

### Переименование
- Пакет приложения, идентификаторы, строки бренда: `Musly` → `KWhale`.

### Новый сетевой слой (клиент)
- `lib/services/kwhale_api_service.dart` — клиент к kwhale-api (рекомендации,
  жанры, промпт-агент, discover, телеметрия). Musly умел только Subsonic; KWhale
  говорит и с Subsonic (медиа), и с kwhale-api (умные функции) — маршрутизация по
  пути в Caddy.

### Экран «Для вас» (`lib/screens/for_you_screen.dart`)
- 4 типа рекомендаций: **Персональные** (ALS+контент+персональный слой),
  **Открытия**, **По жанру**, **Промпт к ИИ** (поле ввода → агент).

### Виджет домашнего экрана (НОВОЕ)
- `KWhaleWidgetProvider.kt` + `res/layout/kwhale_widget.xml` +
  `res/xml/kwhale_widget_info.xml`.
- Показывает обложку, **название и исполнителя над кнопками** управления
  (play/pause, вперёд, назад). Масштабируется до 2×1 (мин. 110×60 dp), ресайз по
  обеим осям. Обновляется из `MusicService.refreshWidgetState()`.

### Bluetooth-тег (НОВОЕ) — решение проблемы «в машине виден только альбом»
- Настройки → Воспроизведение → «Тег для Bluetooth», 4 режима:
  - **Стандартно** — поля как в теге.
  - **Исполнитель — Название в поле «Альбом»** — для магнитол, что показывают
    только альбом.
  - **Исполнитель — Название в поле «Название»**.
  - **Название везде** — дублировать во все поля.
- Реализация: Flutter пишет настройку `kwhale_bt_tag_mode` в SharedPreferences;
  `MusicService.updateMetadata()` читает `flutter.kwhale_bt_tag_mode` и
  переформирует `MediaMetadataCompat` (TITLE/ARTIST/ALBUM) перед отправкой в
  MediaSession/AVRCP.

### Серверная часть (целиком новая, каталог `server/`)
- `embedding/` — bge-m3 сервис (1024-мерные эмбеддинги текста).
- `worker/app/indexer.py` — Essentia + bge-m3 + спектрограммы (mel → mimo-v2-omni)
  + vibe-теги.
- `worker/app/recommender.py` + `personal_score.py` — гибрид + персональный слой.
- `api/app/prompt_recs.py` — агент с инструментами (tool-calling).
- `api/app/routers/recommendations.py` — `/recs`, `/recs/genres`, `/recs/prompt`.

---

## 4. Рекомендательный алгоритм (полная схема и формулы)

Рекомендации строятся в три слоя: **кандидаты** → **персональный коэффициент** →
**ранжирование**. Плюс отдельно — **промпт-агент** (по запросу пользователя).

### 4.1. Слой кандидатов

Два независимых генератора, результаты объединяются (дедуп):

**(A) ALS — коллаборативная фильтрация** (`_als_recommendations`)
Матрица «пользователь × трек» из `playback_events`, вес взаимодействия:
```
confidence(u, t) = plays(u,t) * (1 + avg_completion(u,t))
```
Обучается `implicit.AlternatingLeastSquares`, берутся топ-N кандидатов.

**(B) Контентная — по аудио-вектору** (`_content_recommendations`)
20-мерный вектор `features_vector` (Essentia). Для «любимых» треков (высокое
завершение, ≥2 прослушиваний) берётся средний вектор, ищутся ближайшие по
косинусу в pgvector:
```
similarity(t) = 1 − (features_vector(t) <=> avg_vector_loved)
```

Кандидаты: `merged = dedup(ALS ∪ Content)`.

### 4.2. Персональный коэффициент (ядро — `personal_score.py`)

Для каждого трека-кандидата `t` при текущем часе `H`:

```
score(t) = W_fav    · fav(t)
         + W_freq   · freq(t)
         + W_compl  · completion(t)
         + W_time   · time_affinity(t, H)
         + W_recent · recency(t)
         − W_skip   · skip_penalty(t)
```

**Веса** (по умолчанию):
| Коэффициент | Вес | Что отражает |
|---|---|---|
| `W_fav`    | 0.30 | трек в избранном (starred) |
| `W_freq`   | 0.20 | как часто слушаешь |
| `W_compl`  | 0.20 | как полно дослушиваешь (анти-скип) |
| `W_time`   | 0.20 | совпадение со временем суток |
| `W_recent` | 0.10 | недавно играл |
| `W_skip`   | 0.25 | штраф за скипы |

**Компоненты** (каждый нормирован ~0..1):

- **fav(t)** = 1, если трек в избранном Navidrome, иначе 0.
  → «треки, которые мне больше нравятся, имеют коэффициент выше».

- **freq(t)** = `ln(1 + plays(t)) / ln(1 + max_plays)` — лог-нормировка частоты
  относительно самого слушаемого трека (защита от перекоса в пользу 1-2 хитов).

- **completion(t)** = средняя доля дослушивания `avg(completion_pct)`, 0..1.

- **time_affinity(t, H)** = `bucket_plays(t, H) / plays(t)` — доля прослушиваний
  трека, попавших в **текущий пояс времени суток**.
  Пояса: утро 5–11, день 11–17, вечер 17–23, ночь 23–5 (часовой пояс
  Europe/Moscow, `LOCAL_TZ_OFFSET_HOURS=3`).
  → реализует «если утром я слушаю джаз — у джаза утром коэффициент выше».

- **recency(t)** = `0.5 ^ (days_since_last_play / 30)` — экспоненциальный спад с
  периодом полураспада 30 дней (играл сегодня → ~1.0, месяц назад → ~0.5).

- **skip_penalty(t)** = `skips(t) / (plays(t) + skips(t))` — доля скипов среди
  всех взаимодействий; вычитается с весом `W_skip`.

Все агрегаты берутся за **последние 90 дней**. Трек без истории получает
нейтральный 0.0 (сохраняет позицию из слоя кандидатов).

### 4.3. Ранжирование

```
merged.sort(key = (−score(t), исходная_позиция_в_кандидатах))
final = merged[:20]
```
Топ-20 с их коэффициентами сохраняется в `recommendations(track_ids, scores)`.
Ночью `worker-beat` пересчитывает профиль вкуса и рекомендации.

### 4.4. Холодный старт
Если истории/кандидатов нет — `/recs` отдаёт случайные треки из библиотеки
(«Свежие треки…»), чтобы лента никогда не была пустой, и запускает фоновую
генерацию.

### 4.5. Промпт-агент (`/recs/prompt`, `prompt_recs.py`)

Настоящий агент с **tool-calling** (модель `deepseek-v4-flash`). Цикл: модель
получает запрос + набор инструментов, сама решает какие вызвать, мы выполняем их
по БД, отдаём результат обратно, до 6 раундов, в конце модель возвращает
`{"track_ids":[...]}`.

**Инструменты агента:**
| Инструмент | Что делает | Источник |
|---|---|---|
| `search_library(query)` | поиск по метаданным | Navidrome |
| `similar_by_audio(track_id)` | похожие по звучанию | `features_vector <=>` |
| `semantic_search(text)` | по смыслу текстов | `lyrics_embedding <=>` (bge-m3) |
| `filter_by_features(energy/valence/bpm)` | по аудио-фичам | `track_features` |
| `filter_by_vibe_tags(tags)` | по vibe + спектро-тегам | `vibe_tags`,`spectro_tags` |
| `get_taste_profile()` | профиль вкуса | `taste_profile` |

При недоступности LLM — деградация в эвристику по тегам + случайные треки.

### 4.6. Какие аспекты трека учитываются (сводка)

| Аспект | Откуда | Где используется |
|---|---|---|
| Аудио-характер (BPM, энергия, тональность, MFCC) | Essentia → `features_vector` | контентный слой, `similar_by_audio`, `filter_by_features` |
| Звучание по спектрограмме (плотность, бас, верхи) | mel-спектрограмма → mimo-v2-omni → `spectro_desc/tags` | `filter_by_vibe_tags`, агент |
| Смысл текста песни | bge-m3 → `lyrics_embedding` | `semantic_search`, агент |
| Настроение/вайб | LLM → `vibe_tags` | теги, агент |
| Поведение (частота, завершение, скипы, время) | `playback_events` | персональный слой |
| Избранное | Navidrome starred | персональный слой (`W_fav`) |
| Совместная фильтрация | `playback_events` (ALS) | слой кандидатов |

---

## 5. Сбор статистики прослушиваний

Клиент шлёт события в `POST /api/events` (`playback_events`):
`navidrome_id, event_type (play/complete/skip/seek), ts, hour_of_day,
day_of_week, position_sec, completion_pct`. Поля времени вычисляются в БД в поясе
Europe/Moscow. На этих данных строятся персональный слой (4.2), профиль вкуса
(`taste_profile`) и ALS (4.1).

---

## 6. Эксплуатация

```bash
cd /files/kwhale/server
docker compose ps                       # статус
docker compose up -d --build api        # пересборка сервиса
docker compose logs -f worker           # логи индексации

# запустить индексацию заново (для треков без эмбеддинга/спектрограммы):
docker compose exec worker python3 -c "
import os, psycopg2
from app.tasks import index_track
pg=psycopg2.connect(os.environ['DATABASE_URL']); cur=pg.cursor()
cur.execute(\"SELECT navidrome_id FROM track_features WHERE lyrics_embedding IS NULL OR spectro_desc IS NULL\")
for (tid,) in cur.fetchall(): index_track.delay(tid)
"
# проверить прогресс индексации:
docker compose exec postgres psql -U kwhale -d kwhale -c \
 "SELECT count(*) FILTER (WHERE features_vector IS NOT NULL) feats, \
         count(*) FILTER (WHERE lyrics_embedding IS NOT NULL) emb, \
         count(*) FILTER (WHERE spectro_desc IS NOT NULL) spectro FROM track_features;"
```

### Ключевые ENV (`server/api/.env`, `server/worker/.env`)
```
OPENAI_API_BASE=https://opencode.ai/zen/go/v1   # OpenCode Go
OPENAI_API_KEY=sk-...                            # ключ подписки
LLM_MODEL=deepseek-v4-flash                      # агент (tool-calling)
OMNI_MODEL=mimo-v2-omni                          # анализ спектрограмм
EMBEDDING_API_URL=http://embedding:8000/v1/embeddings  # bge-m3
LOCAL_TZ_OFFSET_HOURS=3                          # для времени суток
```
Все вызовы к OpenCode идут с заголовком `User-Agent: kwhale/1.0` (без него
Cloudflare отдаёт 403/1010).

---

## 7. Известные ограничения
- **OpenCode Go нестабилен**: периодически 500/403. Агент, vibe-теги и
  спектрограммы при сбое деградируют (fallback), индексация продолжится при
  следующем прогоне.
- **Индексация спектрограмм медленная** (LLM+omni на каждый трек) — идёт часами в
  фоне; рекомендации по аудио/смыслу работают пропорционально готовности.
- bge-m3 даёт **1024** измерения — колонка `lyrics_embedding vector(1024)` (не
  1536, как у OpenAI-эмбеддингов).
