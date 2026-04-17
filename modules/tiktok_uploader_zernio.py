"""
TikTok Uploader via Zernio API
------------------------------
Kein Browser, kein Playwright — einfache HTTP-Requests.
Video → catbox.moe (temporäres Hosting) → Zernio API → TikTok

Benötigt in .env:
    ZERNIO_API_KEY=...
    ZERNIO_TIKTOK_ACCOUNT_ID=...
"""

import json
import logging
import os
import time
from pathlib import Path

import requests

logger = logging.getLogger("syncin")

ZERNIO_BASE = "https://zernio.com/api/v1"


def _zernio_headers() -> dict:
    key = os.environ.get("ZERNIO_API_KEY", "").strip()
    if not key:
        raise ValueError("ZERNIO_API_KEY fehlt — bitte in Railway Variables eintragen")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type":  "application/json",
    }


def _account_id() -> str:
    aid = os.environ.get("ZERNIO_TIKTOK_ACCOUNT_ID", "").strip()
    if not aid:
        raise ValueError("ZERNIO_TIKTOK_ACCOUNT_ID fehlt — bitte in Railway Variables eintragen")
    return aid


def _upload_to_host(video_path: str) -> str:
    """
    Lädt das Video zu einem temporären Hoster hoch und gibt die öffentliche URL zurück.
    Probiert mehrere Dienste bis einer klappt (Fallback-Kette).
    """
    size_mb = Path(video_path).stat().st_size / 1_048_576
    logger.info(f"   Video-Upload: {size_mb:.1f} MB ...")

    errors = []

    # 1. 0x0.st — open source, funktioniert von Server-IPs, bis 512 MB
    try:
        with open(video_path, "rb") as f:
            resp = requests.post(
                "https://0x0.st",
                files={"file": ("video.mp4", f, "video/mp4")},
                timeout=180,
            )
        if resp.ok and resp.text.strip().startswith("https://"):
            url = resp.text.strip()
            logger.info(f"   0x0.st: {url}")
            return url
        errors.append(f"0x0.st HTTP {resp.status_code}: {resp.text[:100]}")
    except Exception as e:
        errors.append(f"0x0.st: {e}")
    logger.warning(f"   0x0.st fehlgeschlagen: {errors[-1]}")

    # 2. Litterbox (catbox temp, 72h) — anderer Endpoint als catbox.moe
    try:
        with open(video_path, "rb") as f:
            resp = requests.post(
                "https://litterbox.catbox.moe/resources/internals/api.php",
                data={"reqtype": "fileupload", "time": "72h"},
                files={"fileToUpload": ("video.mp4", f, "video/mp4")},
                timeout=180,
            )
        if resp.ok and resp.text.strip().startswith("https://"):
            url = resp.text.strip()
            logger.info(f"   Litterbox: {url}")
            return url
        errors.append(f"Litterbox HTTP {resp.status_code}: {resp.text[:100]}")
    except Exception as e:
        errors.append(f"Litterbox: {e}")
    logger.warning(f"   Litterbox fehlgeschlagen: {errors[-1]}")

    # 3. transfer.sh — per PUT-Request
    try:
        filename = Path(video_path).name
        with open(video_path, "rb") as f:
            resp = requests.put(
                f"https://transfer.sh/{filename}",
                data=f,
                headers={"Max-Downloads": "5", "Max-Days": "1"},
                timeout=180,
            )
        if resp.ok and resp.text.strip().startswith("https://"):
            url = resp.text.strip()
            logger.info(f"   transfer.sh: {url}")
            return url
        errors.append(f"transfer.sh HTTP {resp.status_code}: {resp.text[:100]}")
    except Exception as e:
        errors.append(f"transfer.sh: {e}")
    logger.warning(f"   transfer.sh fehlgeschlagen: {errors[-1]}")

    raise RuntimeError(
        f"Alle Hosting-Dienste fehlgeschlagen ({size_mb:.1f} MB): " + " | ".join(errors)
    )


def _create_tiktok_post(video_url: str, caption: str) -> str:
    """
    Erstellt den TikTok-Post via Zernio API.
    Gibt die Post-ID zurück.
    """
    logger.info("   Erstelle TikTok-Post via Zernio ...")
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
        raise RuntimeError(
            f"Zernio API Fehler: HTTP {resp.status_code} — {resp.text[:300]}"
        )

    post_id = resp.json().get("post", {}).get("_id", "unknown")
    logger.info(f"   Post erstellt (ID: {post_id})")
    return post_id


def _wait_for_publish(post_id: str, max_wait: int = 120) -> bool:
    """
    Wartet bis der Post von Zernio verarbeitet und auf TikTok veröffentlicht wurde.
    Gibt True zurück wenn status=published.
    """
    h = {k: v for k, v in _zernio_headers().items() if k != "Content-Type"}
    logger.info(f"   Warte auf TikTok-Veröffentlichung (max {max_wait}s) ...")
    for i in range(0, max_wait, 10):
        time.sleep(10)
        try:
            resp = requests.get(f"{ZERNIO_BASE}/posts/{post_id}", headers=h, timeout=15)
            if not resp.ok:
                continue
            post      = resp.json().get("post", {})
            status    = post.get("status", "")
            platforms = post.get("platforms", [])
            p_status  = platforms[0].get("status", "") if platforms else ""
            logger.info(f"   Status nach {i+10}s: post={status}, tiktok={p_status}")
            if status == "published" or p_status == "published":
                return True
            if p_status in ("failed", "error"):
                err = platforms[0].get("error", "?")
                logger.error(f"   TikTok-Plattform-Fehler: {err}")
                return False
        except Exception as e:
            logger.warning(f"   Status-Check Fehler: {e}")
    logger.warning("   Timeout beim Warten auf Veröffentlichung — prüfe TikTok manuell")
    return False


def _mark_uploaded(video_path: str):
    """Schreibt uploaded=True sofort in die Metadaten — verhindert Doppelpost bei Retry."""
    meta = Path(video_path).with_suffix(".json")
    try:
        if meta.exists():
            d = json.loads(meta.read_text(encoding="utf-8"))
            d["uploaded"] = True
            meta.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info("   ✓ Metadata: uploaded=True gesetzt")
    except Exception as e:
        logger.warning(f"   Metadata-Update fehlgeschlagen: {e}")


def upload_video_zernio(video_path: str, caption: str) -> bool:
    """
    Hauptfunktion: Lädt Video via Zernio zu TikTok hoch.
    1. Video → temporärer Hoster (public URL)
    2. Zernio API → TikTok post erstellen
    3. Sofort uploaded=True setzen (Doppelpost-Schutz)
    4. Warte auf Bestätigung (Fehler hier verhindern keinen Erfolg mehr)
    """
    # Phase 1: Video hochladen — darf fehlschlagen, kein Post erstellt
    try:
        video_url = _upload_to_host(video_path)
    except Exception as e:
        logger.error(f"   ✗ Video-Upload fehlgeschlagen: {e}")
        return False

    # Phase 2: Post erstellen — darf fehlschlagen, noch kein Post erstellt
    try:
        post_id = _create_tiktok_post(video_url, caption)
    except Exception as e:
        logger.error(f"   ✗ Post-Erstellung fehlgeschlagen: {e}")
        return False

    # Phase 3: Post ist erstellt → SOFORT als hochgeladen markieren
    # Jeder Retry-Versuch danach wird durch den Doppelpost-Schutz in _run_upload geblockt
    _mark_uploaded(video_path)

    # Phase 4: Auf Veröffentlichung warten — Fehler hier ändern nichts mehr am Ergebnis
    try:
        published = _wait_for_publish(post_id)
        if published:
            logger.info("   ✓ TikTok-Video erfolgreich veröffentlicht!")
        else:
            logger.warning("   ⚠️  Post erstellt, Status unklar — prüfe TikTok manuell")
    except Exception as e:
        logger.warning(f"   Status-Check übersprungen (Post wurde erstellt): {e}")

    return True


# Kompatibilitäts-Alias (app.py bleibt unverändert)
def upload_video_browser(video_path: str, caption: str) -> bool:
    return upload_video_zernio(video_path, caption)
