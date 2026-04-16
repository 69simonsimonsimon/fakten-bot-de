"""
TikTok Uploader via Zernio API
------------------------------
Kein Browser, kein Playwright — einfache HTTP-Requests.
Video → catbox.moe (temporäres Hosting) → Zernio API → TikTok

Benötigt in .env:
    ZERNIO_API_KEY=...
    ZERNIO_TIKTOK_ACCOUNT_ID=...
"""

import os
import time
from pathlib import Path

import requests

ZERNIO_BASE  = "https://zernio.com/api/v1"
CATBOX_URL   = "https://catbox.moe/user/api.php"


def _zernio_headers() -> dict:
    key = os.environ.get("ZERNIO_API_KEY", "").strip()
    if not key:
        raise ValueError("ZERNIO_API_KEY fehlt in .env")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type":  "application/json",
    }


def _account_id() -> str:
    aid = os.environ.get("ZERNIO_TIKTOK_ACCOUNT_ID", "").strip()
    if not aid:
        raise ValueError("ZERNIO_TIKTOK_ACCOUNT_ID fehlt in .env")
    return aid


def _upload_to_catbox(video_path: str) -> str:
    """
    Lädt das Video zu catbox.moe hoch (kostenlos, kein Account nötig, max 200 MB).
    Gibt die öffentliche URL zurück, die Zernio dann abruft.
    """
    size_mb = Path(video_path).stat().st_size / 1_048_576
    print(f"   Lade Video hoch ({size_mb:.1f} MB) ...")
    with open(video_path, "rb") as f:
        resp = requests.post(
            CATBOX_URL,
            data={"reqtype": "fileupload"},
            files={"fileToUpload": ("video.mp4", f, "video/mp4")},
            timeout=180,
        )
    if not resp.ok or not resp.text.startswith("https://"):
        raise RuntimeError(f"Catbox-Upload fehlgeschlagen: {resp.status_code} {resp.text[:200]}")
    url = resp.text.strip()
    print(f"   Video verfügbar: {url}")
    return url


def _create_tiktok_post(video_url: str, caption: str) -> str:
    """
    Erstellt den TikTok-Post via Zernio API.
    Gibt die Post-ID zurück.
    """
    print("   Erstelle TikTok-Post via Zernio ...")
    resp = requests.post(
        f"{ZERNIO_BASE}/posts",
        headers=_zernio_headers(),
        json={
            "content":   caption[:4000],
            "platforms": [{"platform": "tiktok", "accountId": _account_id()}],
            "mediaItems": [{"url": video_url, "type": "video"}],
            "publishNow": True,
            "platformSettings": {
                "tiktok": {
                    "privacy":        "public",
                    "allowComments":  True,
                    "allowDuets":     True,
                    "allowStitches":  True,
                }
            },
        },
        timeout=300,
    )
    if not resp.ok:
        raise RuntimeError(f"Post-Erstellung fehlgeschlagen: {resp.status_code} {resp.text[:300]}")

    post_id = resp.json().get("post", {}).get("_id", "unknown")
    print(f"   Post erstellt (ID: {post_id})")
    return post_id


def _wait_for_publish(post_id: str, max_wait: int = 120) -> bool:
    """
    Wartet bis der Post von Zernio verarbeitet und auf TikTok veröffentlicht wurde.
    Gibt True zurück wenn status=published.
    """
    h = {k: v for k, v in _zernio_headers().items() if k != "Content-Type"}
    print(f"   Warte auf Veröffentlichung (max {max_wait}s) ...")
    for i in range(0, max_wait, 10):
        time.sleep(10)
        try:
            resp = requests.get(f"{ZERNIO_BASE}/posts/{post_id}", headers=h, timeout=15)
            if not resp.ok:
                continue
            post = resp.json().get("post", {})
            status = post.get("status", "")
            platforms = post.get("platforms", [])
            p_status = platforms[0].get("status", "") if platforms else ""
            print(f"   Status nach {i+10}s: post={status}, tiktok={p_status}")
            if status == "published" or p_status == "published":
                return True
            if p_status in ("failed", "error"):
                err = platforms[0].get("error", "?")
                print(f"   TikTok-Fehler: {err}")
                return False
        except Exception as e:
            print(f"   Status-Check Fehler: {e}")
    return False


def upload_video_zernio(video_path: str, caption: str) -> bool:
    """
    Hauptfunktion: Lädt Video via Zernio zu TikTok hoch.
    1. Video → catbox.moe (public URL)
    2. Zernio API → TikTok post
    3. Warte auf Bestätigung
    """
    try:
        video_url = _upload_to_catbox(video_path)
        post_id   = _create_tiktok_post(video_url, caption)
        published = _wait_for_publish(post_id)
        if published:
            print("   ✓ TikTok-Video erfolgreich veröffentlicht!")
        else:
            print("   ⚠️  Post wurde erstellt, Status unklar — prüfe TikTok manuell")
        return True  # Post wurde erstellt, auch wenn Status noch pending
    except Exception as e:
        print(f"   ✗ Zernio-Upload fehlgeschlagen: {e}")
        return False


# Kompatibilitäts-Alias (app.py bleibt unverändert)
def upload_video_browser(video_path: str, caption: str) -> bool:
    return upload_video_zernio(video_path, caption)
