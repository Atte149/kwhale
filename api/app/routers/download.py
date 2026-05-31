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

_APKS = {
    "arm64": "kwhale-arm64.apk",
    "arm32": "kwhale-arm32.apk",
}


@router.get("/download", response_class=HTMLResponse)
async def download_page():
    """Simple landing page with install links."""
    rows = []
    for key, fname in _APKS.items():
        fpath = APK_DIR / fname
        size_mb = round(fpath.stat().st_size / 1024 / 1024, 1) if fpath.exists() else 0
        label = "ARM64 (большинство телефонов 2017+)" if key == "arm64" else "ARM32 (старые устройства)"
        rows.append(
            f'<li><a href="/download/{key}">{label}</a> — {size_mb} MB</li>'
        )
    items = "\n".join(rows)
    return f"""<!DOCTYPE html>
<html lang="ru"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>KWhale — установка</title>
<style>
body{{font-family:-apple-system,system-ui,sans-serif;max-width:520px;margin:40px auto;padding:0 20px;background:#111;color:#eee}}
h1{{font-size:28px}} a{{color:#4ea1ff;text-decoration:none;font-weight:600}}
li{{margin:14px 0;font-size:18px}} .hint{{color:#888;font-size:14px;margin-top:24px;line-height:1.5}}
</style></head><body>
<h1>🐋 KWhale</h1>
<p>Скачайте APK под ваш телефон:</p>
<ul>{items}</ul>
<p class="hint">Не знаете ARM64 или ARM32? Берите <b>ARM64</b> — подходит почти всем.<br>
После скачивания откройте файл из «Загрузок» и разрешите установку из этого источника.</p>
</body></html>"""


@router.get("/download/{arch}")
async def download_apk(arch: str):
    fname = _APKS.get(arch)
    if not fname:
        raise HTTPException(404, "Unknown architecture")
    fpath = APK_DIR / fname
    if not fpath.exists():
        raise HTTPException(404, "APK not found")
    return FileResponse(
        path=str(fpath),
        media_type="application/vnd.android.package-archive",
        filename=fname,
    )
