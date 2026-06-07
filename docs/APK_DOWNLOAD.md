# Скачивание APK через веб-интерфейс

**Обновлено:** 2026-05-31

---

## Описание

Пользователи могут скачать последнюю версию Android-приложения kwhale напрямую с сервера через веб-интерфейс по адресу:

**https://music.dueattendant149.org/download**

---

## Архитектура

### Компоненты

1. **APK файл**: `/files/kwhale/apk/kwhale-latest.apk` (66 MB)
2. **Docker volume**: Монтируется в API контейнер как `/apk:ro` (read-only)
3. **FastAPI роутер**: `/files/kwhale/server/api/app/routers/download.py`
4. **Эндпоинты**:
   - `GET /download` — HTML страница с кнопкой скачивания
   - `GET /download/latest` — прямая ссылка на APK файл

### Поток данных

```
Пользователь
    ↓
https://music.dueattendant149.org/download
    ↓
Caddy (reverse proxy)
    ↓
kwhale-api (FastAPI контейнер)
    ↓
/apk/kwhale-latest.apk (Docker volume)
    ↓
Скачивание APK
```

---

## Использование

### Для пользователей

1. Открыть в браузере: **https://music.dueattendant149.org/download**
2. Нажать кнопку "📥 Скачать APK"
3. Открыть скачанный файл
4. Разрешить установку из этого источника
5. Установить приложение

### Прямая ссылка на APK

```
https://music.dueattendant149.org/download/latest
```

Можно использовать для:
- Прямого скачивания через curl/wget
- QR-кода для быстрой установки
- Ссылки в документации

---

## Обновление APK

### Автоматическое (после сборки)

После сборки APK автоматически копируется в `/files/kwhale/apk/kwhale-latest.apk`:

```bash
cd /files/kwhale/client
flutter build apk --release
cp build/app/outputs/flutter-apk/app-release.apk /files/kwhale/apk/kwhale-latest.apk
```

API контейнер сразу начнёт отдавать новую версию (volume монтируется read-only, но файл обновляется на хосте).

### Ручное обновление

```bash
# Скопировать новый APK
cp /path/to/new.apk /files/kwhale/apk/kwhale-latest.apk

# Проверить размер
ls -lh /files/kwhale/apk/kwhale-latest.apk

# Перезапустить API (опционально, обычно не требуется)
cd /files/kwhale/server
docker compose restart api
```

---

## Технические детали

### Docker Compose конфигурация

В `/files/kwhale/server/docker-compose.yml`:

```yaml
api:
  volumes:
    - /files/kwhale/apk:/apk:ro  # Read-only mount
```

### FastAPI роутер

**Файл:** `/files/kwhale/server/api/app/routers/download.py`

**Основные функции:**

1. **`download_page()`** — HTML страница с информацией и кнопкой скачивания
   - Показывает размер файла
   - Список изменений в версии
   - Инструкция по установке

2. **`download_latest_apk()`** — отдаёт APK файл
   - MIME type: `application/vnd.android.package-archive`
   - Правильные заголовки для Android Package Installer
   - Обходит проблему OwnCloud с `content://` URI

### MIME Type

Используется официальный MIME type для Android APK:
```
application/vnd.android.package-archive
```

Это гарантирует, что:
- Браузер правильно определяет тип файла
- Android Package Installer может открыть файл
- Нет проблем с "size 0 / no AndroidManifest"

---

## Преимущества перед OwnCloud

### Проблемы OwnCloud

1. **Content URI**: OwnCloud использует `content://` URI, которые иногда не работают с Package Installer
2. **Размер 0**: Installer может показывать "size 0 bytes"
3. **Нет AndroidManifest**: Ошибка "no AndroidManifest.xml found"
4. **Аутентификация**: Требуется логин для скачивания

### Решение через API

1. ✅ **Прямой HTTP**: Обычный HTTP URL без content:// схемы
2. ✅ **Правильный Content-Length**: Явно указан размер файла
3. ✅ **Правильный MIME type**: Android распознаёт как APK
4. ✅ **Без аутентификации**: Публичный доступ к странице скачивания
5. ✅ **Красивая страница**: HTML с инструкциями и changelog

---

## Мониторинг и отладка

### Проверить доступность APK

```bash
# Проверить файл на хосте
ls -lh /files/kwhale/apk/kwhale-latest.apk

# Проверить внутри контейнера
docker compose exec api ls -lh /apk/kwhale-latest.apk

# Проверить эндпоинт
curl -I https://music.dueattendant149.org/download/latest
```

### Ожидаемый ответ

```
HTTP/2 200
content-type: application/vnd.android.package-archive
content-length: 69206016
content-disposition: attachment; filename="kwhale-latest.apk"
```

### Логи

```bash
# Логи API контейнера
cd /files/kwhale/server
docker compose logs -f api | grep download
```

---

## QR-код для быстрой установки

Можно сгенерировать QR-код со ссылкой на APK:

```bash
# Установить qrencode
sudo apt install qrencode

# Сгенерировать QR-код
qrencode -t PNG -o /tmp/kwhale-download-qr.png "https://music.dueattendant149.org/download/latest"
```

Пользователи могут отсканировать QR-код телефоном и сразу скачать APK.

---

## Безопасность

### Публичный доступ

- ✅ Страница `/download` доступна без аутентификации
- ✅ APK файл доступен без аутентификации
- ⚠️ Убедитесь, что APK подписан правильным ключом

### HTTPS

- ✅ Весь трафик идёт через HTTPS (Caddy)
- ✅ Защита от MITM атак
- ✅ Безопасная передача APK

### Проверка целостности

Пользователи могут проверить SHA256 хеш:

```bash
# На сервере
sha256sum /files/kwhale/apk/kwhale-latest.apk

# На телефоне (через Termux)
sha256sum /sdcard/Download/kwhale-latest.apk
```

---

## Changelog страницы

Обновите список изменений в `/files/kwhale/server/api/app/routers/download.py` при выпуске новой версии:

```python
<p><b>Новое в версии 1.0.13:</b></p>
<ul>
<li>✨ Объединены разделы Home и ForYou в «Рекомендации»</li>
<li>✨ Унифицированный поиск (локально + сеть)</li>
<li>✨ Редизайн виджета (Apple-like, 3 кнопки)</li>
<li>✨ Настройка Bluetooth-метаданных для магнитол</li>
<li>🎨 Обновлена иконка приложения</li>
<li>🎨 Упрощена навигация до 3 вкладок</li>
</ul>
```

---

## Альтернативные методы распространения

### 1. OwnCloud (резервный)

```bash
curl -u "vladik:melorise" -T /files/kwhale/apk/kwhale-latest.apk \
  "https://cloud.dueattendant149.org/remote.php/dav/files/vladik/KWhale/kwhale-latest.apk"
```

### 2. GitHub Releases

Можно автоматизировать публикацию через GitHub Actions:

```yaml
- name: Upload APK to Release
  uses: actions/upload-release-asset@v1
  with:
    upload_url: ${{ steps.create_release.outputs.upload_url }}
    asset_path: ./build/app/outputs/flutter-apk/app-release.apk
    asset_name: kwhale-${{ github.ref_name }}.apk
    asset_content_type: application/vnd.android.package-archive
```

### 3. F-Droid

Для публикации в F-Droid нужно:
1. Создать metadata файл
2. Убедиться, что все зависимости open-source
3. Отправить PR в fdroiddata репозиторий

---

## Troubleshooting

### Проблема: 404 Not Found

**Причина**: APK файл не существует

**Решение**:
```bash
ls -la /files/kwhale/apk/kwhale-latest.apk
# Если файла нет, пересобрать APK
cd /files/kwhale/client
flutter build apk --release
cp build/app/outputs/flutter-apk/app-release.apk /files/kwhale/apk/kwhale-latest.apk
```

### Проблема: Permission denied в контейнере

**Причина**: Неправильные права на файл

**Решение**:
```bash
chmod 644 /files/kwhale/apk/kwhale-latest.apk
chown dueattendant149:dueattendant149 /files/kwhale/apk/kwhale-latest.apk
```

### Проблема: Старая версия APK

**Причина**: Файл не обновлён после сборки

**Решение**:
```bash
# Проверить дату модификации
ls -lh /files/kwhale/apk/kwhale-latest.apk

# Скопировать новую версию
cp /files/kwhale/client/build/app/outputs/flutter-apk/app-release.apk \
   /files/kwhale/apk/kwhale-latest.apk
```

### Проблема: Android не может установить APK

**Причина**: Неправильный MIME type или повреждённый файл

**Решение**:
```bash
# Проверить целостность APK
unzip -t /files/kwhale/apk/kwhale-latest.apk

# Проверить MIME type в ответе
curl -I https://music.dueattendant149.org/download/latest | grep content-type
```

---

## Метрики

### Отслеживание скачиваний

Можно добавить логирование в роутер:

```python
import logging

logger = logging.getLogger(__name__)

@router.get("/download/latest")
async def download_latest_apk(request: Request):
    logger.info(f"APK download from {request.client.host}")
    # ... rest of the code
```

### Анализ логов

```bash
# Количество скачиваний за сегодня
docker compose logs api | grep "APK download" | grep "$(date +%Y-%m-%d)" | wc -l

# Уникальные IP адреса
docker compose logs api | grep "APK download" | awk '{print $NF}' | sort -u | wc -l
```

---

## Итоги

✅ **Реализовано:**
- Публичная страница скачивания на `/download`
- Прямая ссылка на APK `/download/latest`
- Правильные HTTP заголовки для Android
- Красивый HTML с инструкциями и changelog
- Автоматическое обновление при замене файла

✅ **Преимущества:**
- Работает без аутентификации
- Нет проблем с content:// URI
- Правильный MIME type
- Информативная страница для пользователей
- Простое обновление (просто заменить файл)

📱 **Ссылка для пользователей:**
**https://music.dueattendant149.org/download**
