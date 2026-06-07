"""APK download — serves the KWhale Android client directly with correct headers.

Bypasses OwnCloud's content:// URI issue where the Android installer reports
"size 0 / no AndroidManifest". Files are served with explicit Content-Length and
the official APK MIME type so the browser downloads them as installable packages.
"""
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

router = APIRouter(tags=["download"])

APK_DIR = Path(os.environ.get("APK_DIR", "/apk"))
LATEST_APK = "kwhale-latest.apk"


@router.get("/download", response_class=HTMLResponse)
async def download_page():
    """Simple landing page with install link."""
    fpath = APK_DIR / LATEST_APK
    size_mb = round(fpath.stat().st_size / 1024 / 1024, 1) if fpath.exists() else 0

    return f"""<!DOCTYPE html>
<html lang="ru"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>KWhale — установка</title>
<style>
body{{font-family:-apple-system,system-ui,sans-serif;max-width:520px;margin:40px auto;padding:0 20px;background:#111;color:#eee}}
h1{{font-size:28px}} a{{color:#4ea1ff;text-decoration:none;font-weight:600}}
.download-btn{{display:inline-block;background:#4ea1ff;color:#fff;padding:16px 32px;border-radius:12px;
font-size:18px;font-weight:600;text-decoration:none;margin:24px 0}}
.download-btn:hover{{background:#3d8fe0}}
.hint{{color:#888;font-size:14px;margin-top:24px;line-height:1.5}}
.version{{color:#666;font-size:13px;margin-top:8px}}
</style></head><body>
<h1>🐋 KWhale</h1>
<p>Музыкальный плеер с ИИ-рекомендациями</p>
<a href="/download/latest" class="download-btn">📥 Скачать APK ({size_mb} MB)</a>
<p class="version">Версия: 1.1.0 | Универсальный APK (все архитектуры)</p>
<div class="hint">
<p><b>Как установить:</b></p>
<ol>
<li>Скачайте APK</li>
<li>Откройте файл из «Загрузок»</li>
<li>Разрешите установку из этого источника</li>
<li>Установите приложение</li>
</ol>
<p><b>Новое в версии 1.1.0:</b></p>
<ul>
<li>🏠 Объединённый Главный экран: рекомендации, плейлисты, альбомы и «Для вас» (ИИ) в одном месте</li>
<li>📚 Новая Библиотека: карусель плейлистов и альбомов сверху, кнопка «Перемешать» и список любимых треков</li>
<li>🔍 Единый Поиск: переключатель между локальной библиотекой и поиском по стримингам (ICM, Yandex)</li>
<li>🎨 Брендинг: приложение называется KWhale, новая фирменная иконка и убраны белые углы</li>
<li>▶️ Стриминг по сети: исправлено воспроизведение треков из рекомендаций и сетевого поиска</li>
<li>📥 Скачивание: починён поток скачивания для сетевого поиска</li>
<li>✨ Glass-режим: исправлен, теперь все 3 вкладки отображаются корректно</li>
<li>📴 Офлайн-режим: сохранена возможность слушать скачанные треки без интернета</li>
</ul>
</div>
</body></html>"""


@router.get("/download/latest")
async def download_latest_apk():
    """Download the latest universal APK."""
    fpath = APK_DIR / LATEST_APK
    if not fpath.exists():
        raise HTTPException(404, "APK not found")
    return FileResponse(
        path=str(fpath),
        media_type="application/vnd.android.package-archive",
        filename=LATEST_APK,
    )
