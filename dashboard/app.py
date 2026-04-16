"""
syncin Dashboard – FastAPI Backend
Starten mit: python dashboard/app.py
Dann im Browser: http://localhost:8000
"""

import json
import logging
import os
import random
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Projekt-Root und Module einbinden
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "modules"))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=True)

IS_RAILWAY = bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PROJECT_ID"))

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_DIR  = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "bot.log"

_handler = RotatingFileHandler(str(LOG_FILE), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"))
logger = logging.getLogger("syncin")
logger.setLevel(logging.INFO)
logger.addHandler(_handler)
logger.addHandler(logging.StreamHandler())  # auch auf stdout


# ── macOS-Benachrichtigungen ──────────────────────────────────────────────────
def notify(title: str, message: str):
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{message}" with title "{title}" sound name "Glass"'],
            timeout=5, capture_output=True,
        )
    except Exception:
        pass

try:
    from fact_generator import generate_fact
    from tts import text_to_speech
    from video_creator import create_video
    from tiktok_uploader_zernio import upload_video_browser
    from analytics_scraper import fetch_analytics, load_cached
except Exception as _import_err:
    logger.error(f"Import-Fehler beim Start: {_import_err}")
    raise

OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", str(ROOT / "output")))
OUTPUT_DIR.mkdir(exist_ok=True, parents=True)

TOPICS = [
    "science", "history", "nature", "technology", "space",
    "animals", "psychology", "food", "geography", "human body",
    "pop culture",
]

# Rotierende Call-to-Actions — täglich anderer CTA für mehr Abwechslung im Feed
_CTAS = [
    "Folge @syncin2 für täglich neue Fakten! 🧠",
    "Mehr Fakten? Folge @syncin2! 🔥",
    "Täglich Neues auf @syncin2! ✨",
    "Folge @syncin2 für mehr Wissen! 💡",
    "Mehr überraschende Fakten auf @syncin2! 🚀",
    "Wusstest du das? Folge @syncin2! 😮",
    "Jeden Tag ein neuer Fakt — @syncin2! 📚",
    "Bleib neugierig! Folge @syncin2! 🌍",
]

app  = FastAPI()
jobs: dict[str, dict] = {}        # job_id → status-dict
uploads: dict[str, str] = {}      # filename → "running" | "done" | "error"
batch_jobs: dict[str, dict] = {}  # batch_id → batch-status


# ── Healthcheck (Railway) ─────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


# ── Video-Liste ───────────────────────────────────────────────────────────────

@app.get("/api/videos")
def list_videos():
    videos = []
    for mp4 in sorted(OUTPUT_DIR.glob("*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True):
        meta_file = mp4.with_suffix(".json")
        meta: dict = {}
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        videos.append({
            "filename": mp4.name,
            "size_mb": round(mp4.stat().st_size / 1_048_576, 1),
            "created": datetime.fromtimestamp(mp4.stat().st_mtime).strftime("%d.%m.%Y %H:%M"),
            "title":    meta.get("title", mp4.stem),
            "topic":    meta.get("topic", ""),
            "caption":  meta.get("caption", ""),
            "uploaded": meta.get("uploaded", False),
        })
    return videos


# ── Video generieren ──────────────────────────────────────────────────────────

@app.post("/api/generate")
def start_generate(topic: str = "", long: bool = False):
    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {"status": "running", "progress": 0, "message": "Startet...", "video": None}
    t = threading.Thread(target=_run_generation, args=(job_id, topic or None, long), daemon=True)
    t.start()
    return {"job_id": job_id}


def _run_generation(job_id: str, topic: str | None, long: bool):
    def upd(msg: str, pct: int):
        jobs[job_id]["message"]  = msg
        jobs[job_id]["progress"] = pct

    try:
        topic     = topic or random.choice(TOPICS)
        stamp     = datetime.now().strftime("%Y%m%d_%H%M%S")
        audio_path = OUTPUT_DIR / f"audio_{stamp}.mp3"
        video_path = OUTPUT_DIR / f"video_{stamp}.mp4"

        upd("Generiere Fakt …", 10)
        fact_data = generate_fact(topic, long=long)

        upd("Erstelle Voiceover …", 30)
        tts_text = f"{fact_data['title']}. {fact_data['fact']}"
        _, word_timings = text_to_speech(tts_text, str(audio_path), topic=topic)

        upd("Erstelle Video …", 55)
        visual_query = fact_data.get("visual_query", "").strip()
        if visual_query:
            print(f"   Hintergrund-Query: '{visual_query}'")
        create_video(
            title=fact_data["title"],
            fact=fact_data["fact"],
            audio_path=str(audio_path),
            output_path=str(video_path),
            word_timings=word_timings,
            gradient_index=random.randint(0, 4),
            topic=topic,
            visual_query=visual_query,
        )
        audio_path.unlink(missing_ok=True)

        # Metadaten speichern
        description  = fact_data.get("description", fact_data["title"])
        cta          = random.choice(_CTAS)
        full_caption = description + "\n" + cta + "\n" + " ".join(fact_data["hashtags"])
        meta = {
            "title":    fact_data["title"],
            "topic":    topic,
            "caption":  full_caption,
            "uploaded": False,
        }
        video_path.with_suffix(".json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        upd(f"Fertig: {video_path.name}", 100)
        jobs[job_id]["status"] = "done"
        jobs[job_id]["video"]  = video_path.name
        logger.info(f"Video erstellt: {video_path.name} (Thema: {topic})")
        notify("syncin Bot", f"Video fertig: {fact_data['title'][:50]}")

    except Exception as e:
        jobs[job_id]["status"]  = "error"
        jobs[job_id]["message"] = str(e)
        logger.error(f"Video-Generierung fehlgeschlagen: {e}")


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return jobs.get(job_id, {"status": "not_found"})


# ── Batch-Generierung ─────────────────────────────────────────────────────────

@app.post("/api/generate-batch")
def start_batch(count: int = 3, topic: str = "", long: bool = False):
    batch_id = str(uuid.uuid4())[:8]
    batch_jobs[batch_id] = {
        "status":      "running",
        "total":       count,
        "done":        0,
        "current":     0,
        "current_job": None,
        "videos":      [],
        "message":     "Startet...",
    }
    t = threading.Thread(
        target=_run_batch, args=(batch_id, count, topic or None, long), daemon=True
    )
    t.start()
    return {"batch_id": batch_id}


def _run_batch(batch_id: str, count: int, topic: str | None, long: bool):
    for i in range(count):
        job_id = str(uuid.uuid4())[:8]
        jobs[job_id] = {"status": "running", "progress": 0, "message": "Startet...", "video": None}
        batch_jobs[batch_id]["current_job"] = job_id
        batch_jobs[batch_id]["current"]     = i + 1
        batch_jobs[batch_id]["message"]     = f"Video {i+1} von {count}…"

        _run_generation(job_id, topic, long)

        job = jobs[job_id]
        if job.get("video"):
            batch_jobs[batch_id]["videos"].append(job["video"])
        batch_jobs[batch_id]["done"] = i + 1

    total = len(batch_jobs[batch_id]["videos"])
    batch_jobs[batch_id]["status"]  = "done"
    batch_jobs[batch_id]["message"] = f"Fertig! {total} Video{'s' if total != 1 else ''} erstellt."


@app.get("/api/batch/{batch_id}")
def get_batch(batch_id: str):
    b = batch_jobs.get(batch_id)
    if not b:
        return {"status": "not_found"}
    result = dict(b)
    # Aktuellen Job-Fortschritt mitliefern
    if b.get("current_job"):
        j = jobs.get(b["current_job"], {})
        result["job_progress"] = j.get("progress", 0)
        result["job_message"]  = j.get("message", "")
    return result


# ── TikTok Upload ─────────────────────────────────────────────────────────────

@app.post("/api/upload/{filename}")
def start_upload(filename: str, custom_caption: str = ""):
    video_path = OUTPUT_DIR / filename
    if not video_path.exists():
        return {"error": "Datei nicht gefunden"}

    meta_file = video_path.with_suffix(".json")
    caption   = custom_caption  # manuell eingetragen hat Vorrang
    if not caption and meta_file.exists():
        try:
            caption = json.loads(meta_file.read_text(encoding="utf-8")).get("caption", "")
        except Exception:
            pass

    # Manuelle Beschreibung im JSON speichern für spätere Uploads
    if custom_caption and meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            meta["caption"] = custom_caption
            meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    uploads[filename] = "running"
    t = threading.Thread(
        target=_run_upload, args=(filename, str(video_path), caption), daemon=True
    )
    t.start()
    return {"status": "started"}


def _run_upload(filename: str, video_path: str, caption: str, max_attempts: int = 3):
    meta_file = Path(video_path).with_suffix(".json")

    for attempt in range(1, max_attempts + 1):
        # ── Doppelpost-Schutz: bereits hochgeladen? → sofort abbrechen ──────
        try:
            if meta_file.exists():
                _m = json.loads(meta_file.read_text(encoding="utf-8"))
                if _m.get("uploaded"):
                    logger.info(f"Upload übersprungen: {filename} bereits hochgeladen (Doppelpost verhindert)")
                    uploads[filename] = "done"
                    return
        except Exception:
            pass

        try:
            logger.info(f"Upload Versuch {attempt}/{max_attempts}: {filename}")
            uploads[filename] = f"running (Versuch {attempt}/{max_attempts})"
            ok = upload_video_browser(video_path, caption)
            if ok:
                # Sofort als hochgeladen markieren — verhindert Doppelpost bei späterem Fehler
                try:
                    if meta_file.exists():
                        meta = json.loads(meta_file.read_text(encoding="utf-8"))
                        meta["uploaded"] = True
                        meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception as meta_e:
                    logger.warning(f"Metadata-Update fehlgeschlagen (Upload war erfolgreich): {meta_e}")
                uploads[filename] = "done"
                logger.info(f"Upload erfolgreich: {filename}")
                notify("syncin Bot", f"✓ Video hochgeladen: {Path(video_path).stem[:40]}")
                # Queue-Status aktualisieren
                for item in upload_queue:
                    if item["filename"] == filename and item["status"] == "uploading":
                        item["status"] = "done"
                return
            else:
                logger.warning(f"Upload fehlgeschlagen (Versuch {attempt}/{max_attempts}): {filename}")
        except Exception as e:
            logger.error(f"Upload-Fehler (Versuch {attempt}/{max_attempts}): {e}")

        if attempt < max_attempts:
            uploads[filename] = f"wartet auf Retry ({attempt}/{max_attempts})…"
            time.sleep(60)

    uploads[filename] = "error"
    for item in upload_queue:
        if item["filename"] == filename and item["status"] == "uploading":
            item["status"] = "error"
    logger.error(f"Upload endgültig fehlgeschlagen nach {max_attempts} Versuchen: {filename}")
    notify("syncin Bot", f"❌ Upload fehlgeschlagen: {Path(video_path).stem[:40]}")


@app.get("/api/upload-status/{filename}")
def upload_status(filename: str):
    return {"status": uploads.get(filename, "idle")}


@app.delete("/api/videos/{filename}")
def delete_video(filename: str):
    video_file = OUTPUT_DIR / filename
    meta_file  = video_file.with_suffix(".json")
    if not video_file.exists():
        return {"error": "Datei nicht gefunden"}
    video_file.unlink()
    if meta_file.exists():
        meta_file.unlink()
    uploads.pop(filename, None)
    return {"status": "deleted"}


@app.post("/api/mark-uploaded/{filename}")
def mark_uploaded(filename: str):
    meta_file = (OUTPUT_DIR / filename).with_suffix(".json")
    if meta_file.exists():
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    else:
        meta = {"title": filename.replace(".mp4",""), "topic": "", "caption": "", "uploaded": False}
    meta["uploaded"] = True
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    uploads[filename] = "done"
    return {"status": "ok"}


# ── Analytics ─────────────────────────────────────────────────────────────────

analytics_job: dict = {"status": "idle", "message": ""}
_analytics_last_refresh: datetime | None = None
_ANALYTICS_AUTO_INTERVAL = 15 * 60  # 15 Minuten

@app.get("/api/analytics")
def get_analytics(refresh: bool = False):
    global _analytics_last_refresh
    if refresh:
        if IS_RAILWAY:
            return {"status": "cloud_mode", "message": "Führe sync_to_railway.py auf deinem Mac aus"}
        analytics_job["status"] = "running"
        analytics_job["message"] = "Öffne Creator Center..."
        def _refresh_and_update():
            global _analytics_last_refresh
            _run_analytics()
            if analytics_job["status"] == "done":
                _analytics_last_refresh = datetime.now()
        t = threading.Thread(target=_refresh_and_update, daemon=True)
        t.start()
        return {"status": "started"}
    data = load_cached()
    return {
        "status": "ok",
        "data": data,
        "count": len(data),
        "last_updated": _analytics_last_refresh.isoformat() if _analytics_last_refresh else None,
        "is_railway": IS_RAILWAY,
    }

@app.get("/api/analytics/status")
def analytics_status():
    return analytics_job


@app.get("/api/config")
def get_config():
    """Gibt Umgebungs-Konfiguration zurück (Cloud vs. Lokal)."""
    return {"is_railway": IS_RAILWAY, "output_dir": str(OUTPUT_DIR)}


@app.post("/api/analytics/sync-cache")
def sync_analytics_cache(data: list):
    """Empfängt Analytics-Cache von lokalem Mac und speichert ihn."""
    from analytics_scraper import CACHE_FILE as _CACHE_FILE
    global _analytics_last_refresh
    _CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    _analytics_last_refresh = datetime.now()
    _append_analytics_history(data)
    logger.info(f"Analytics-Cache synchronisiert: {len(data)} Videos vom lokalen Mac")
    return {"status": "ok", "count": len(data)}

def _run_analytics():
    try:
        analytics_job["message"] = "Lese TikTok Stats..."
        data = fetch_analytics()
        analytics_job["status"]  = "done"
        cached = load_cached()
        if data and data is not cached:
            analytics_job["message"] = f"{len(data)} Videos geladen"
            _append_analytics_history(data)
            logger.info(f"Analytics-Snapshot gespeichert: {len(data)} Videos")
        else:
            analytics_job["message"] = f"Gecachte Daten ({len(data)} Videos) — kein Internet"
            logger.warning("Analytics: kein Internet, gecachte Daten gezeigt")
    except Exception as e:
        err = str(e)
        if "ERR_INTERNET_DISCONNECTED" in err or "kein Internet" in err:
            analytics_job["status"]  = "error"
            analytics_job["message"] = "Kein Internet — bitte Verbindung prüfen"
        else:
            analytics_job["status"]  = "error"
            analytics_job["message"] = err
        logger.error(f"Analytics-Fehler: {e}")


def _analytics_auto_refresh_loop():
    """Aktualisiert Analytics automatisch alle 15 Minuten im Hintergrund."""
    global _analytics_last_refresh
    if IS_RAILWAY:
        logger.info("Cloud-Modus: Analytics-Auto-Refresh deaktiviert (kein TikTok-Login verfügbar)")
        return
    time.sleep(90)  # Kurz warten bis Dashboard bereit ist
    while True:
        try:
            logger.info("Auto-Analytics: starte Hintergrund-Refresh...")
            data = fetch_analytics()
            if data:
                _analytics_last_refresh = datetime.now()
                _append_analytics_history(data)
                logger.info(f"Auto-Analytics: {len(data)} Videos aktualisiert ({_analytics_last_refresh.strftime('%H:%M:%S')})")
        except Exception as e:
            logger.warning(f"Auto-Analytics Fehler: {e}")
        time.sleep(_ANALYTICS_AUTO_INTERVAL)


# ── Upload-Warteschlange ──────────────────────────────────────────────────────

QUEUE_FILE    = OUTPUT_DIR / "upload_queue.json"
SCHEDULE_FILE = OUTPUT_DIR / "schedule.json"
ANALYTICS_HISTORY_FILE = OUTPUT_DIR / "analytics_history.json"

upload_queue: list[dict] = []   # [{filename, caption, scheduled_time, status}]
_queue_lock = threading.Lock()


def _load_queue():
    global upload_queue
    if QUEUE_FILE.exists():
        try:
            upload_queue = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        except Exception:
            upload_queue = []


def _save_queue():
    QUEUE_FILE.write_text(json.dumps(upload_queue, ensure_ascii=False, indent=2), encoding="utf-8")


def _queue_processor():
    """Hintergrund-Thread: prüft minütlich ob ein geplanter Upload fällig ist."""
    while True:
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            with _queue_lock:
                for item in upload_queue:
                    if item["status"] == "waiting" and item["scheduled_time"] <= now:
                        item["status"] = "uploading"
                        vp = str(OUTPUT_DIR / item["filename"])
                        t  = threading.Thread(
                            target=_run_upload,
                            args=(item["filename"], vp, item.get("caption", "")),
                            daemon=True,
                        )
                        t.start()
                        logger.info(f"Queue: starte Upload für {item['filename']}")
                _save_queue()
        except Exception as e:
            logger.error(f"Queue-Processor-Fehler: {e}")
        time.sleep(30)


@app.get("/api/queue")
def get_queue():
    return upload_queue


@app.post("/api/queue/add")
def add_to_queue(filename: str, scheduled_time: str, custom_caption: str = ""):
    """Fügt ein Video zur Upload-Warteschlange hinzu. scheduled_time: 'YYYY-MM-DD HH:MM'"""
    video_path = OUTPUT_DIR / filename
    if not video_path.exists():
        return {"error": "Datei nicht gefunden"}
    caption = custom_caption
    if not caption:
        meta_file = video_path.with_suffix(".json")
        if meta_file.exists():
            try:
                caption = json.loads(meta_file.read_text(encoding="utf-8")).get("caption", "")
            except Exception:
                pass
    with _queue_lock:
        # Duplikate vermeiden
        upload_queue[:] = [q for q in upload_queue if q["filename"] != filename]
        upload_queue.append({
            "filename":       filename,
            "caption":        caption,
            "scheduled_time": scheduled_time,
            "status":         "waiting",
        })
        upload_queue.sort(key=lambda x: x["scheduled_time"])
        _save_queue()
    logger.info(f"Queue: {filename} geplant für {scheduled_time}")
    return {"status": "queued"}


@app.delete("/api/queue/{filename}")
def remove_from_queue(filename: str):
    with _queue_lock:
        upload_queue[:] = [q for q in upload_queue if q["filename"] != filename]
        _save_queue()
    return {"status": "removed"}


# ── Auto-Zeitplan ─────────────────────────────────────────────────────────────

class ScheduleSlot(BaseModel):
    time:     str  = "18:00"
    mode:     str  = "new"       # "new" = generieren, "existing" = vorhandenes Video
    topic:    str  = ""
    filename: str  = ""          # nur relevant wenn mode == "existing"
    long:     bool = False

class ScheduleConfig(BaseModel):
    enabled:          bool               = False
    recovery_until:   str | None         = None
    recovery_reason:  str                = ""
    slots:            list[ScheduleSlot] = [ScheduleSlot()]


DEFAULT_SCHEDULE = {
    "enabled":          False,
    "recovery_until":   None,   # ISO-Datum "YYYY-MM-DD" oder None
    "recovery_reason":  "",
    "slots": [{"time": "18:00", "mode": "new", "topic": "", "filename": "", "long": False}],
}


def _load_schedule_cfg() -> dict:
    if SCHEDULE_FILE.exists():
        try:
            raw = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
            # Altes Format (time + count) automatisch migrieren
            if "time" in raw and "slots" not in raw:
                slots = [{"time": raw["time"], "topic": raw.get("topic", ""), "long": raw.get("long", False)}]
                raw = {"enabled": raw.get("enabled", False), "slots": slots}
            return {**DEFAULT_SCHEDULE, **raw}
        except Exception:
            pass
    return dict(DEFAULT_SCHEDULE)


def _save_schedule_cfg(cfg: dict):
    SCHEDULE_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def _check_views_drop() -> tuple[bool, str]:
    """
    Prüft ob ein Shadow Ban wahrscheinlich ist.
    Gibt (True, Grund) zurück wenn ein Einbruch erkannt wird.
    """
    try:
        from analytics_scraper import load_cached
        data = load_cached()
        if len(data) < 3:
            return False, ""

        recent = data[:3]
        # Signal 1: letzte 3 Videos alle mit 0 Views
        if all(v.get("views", 0) == 0 for v in recent):
            return True, "Letzte 3 Videos haben 0 Views"

        # Signal 2: starker Views-Einbruch (>85% Rückgang) gegenüber älteren Videos
        if len(data) >= 6:
            recent_avg = sum(v.get("views", 0) for v in data[:3]) / 3
            older_avg  = sum(v.get("views", 0) for v in data[3:6]) / 3
            if older_avg > 50 and recent_avg < older_avg * 0.15:
                return True, f"Views-Einbruch: Ø {recent_avg:.0f} vs. Ø {older_avg:.0f} (>{85}% Rückgang)"

        return False, ""
    except Exception:
        return False, ""


def _scheduler_loop():
    """Hintergrund-Thread: prüft alle 30s ob ein Slot feuern soll."""
    fired_keys: set[str] = set()
    while True:
        try:
            cfg = _load_schedule_cfg()
            if cfg.get("enabled"):
                now      = datetime.now()
                today    = now.strftime("%Y-%m-%d")
                cur_time = now.strftime("%H:%M")

                # ── Recovery-Modus prüfen ──────────────────────────────────
                recovery_until = cfg.get("recovery_until")
                if recovery_until and today <= recovery_until:
                    # Einmal pro Tag loggen
                    day_key = f"recovery_logged_{today}"
                    if day_key not in fired_keys:
                        fired_keys.add(day_key)
                        logger.info(f"Recovery-Modus aktiv bis {recovery_until} — kein Upload heute")
                    time.sleep(30)
                    continue

                # Recovery abgelaufen → automatisch beenden
                if recovery_until and today > recovery_until:
                    cfg["recovery_until"]  = None
                    cfg["recovery_reason"] = ""
                    _save_schedule_cfg(cfg)
                    logger.info("Recovery-Modus beendet — Zeitplan wieder aktiv")
                    notify("syncin Bot", "Recovery abgeschlossen — Zeitplan wieder aktiv!")

                # ── Auto-Pause: Views-Drop prüfen ─────────────────────────
                pause_check_key = f"pause_checked_{today}"
                if pause_check_key not in fired_keys:
                    fired_keys.add(pause_check_key)
                    should_pause, reason = _check_views_drop()
                    if should_pause:
                        recovery_days = 7
                        until = (now + __import__('datetime').timedelta(days=recovery_days)).strftime("%Y-%m-%d")
                        cfg["recovery_until"]  = until
                        cfg["recovery_reason"] = f"Auto-Pause: {reason}"
                        _save_schedule_cfg(cfg)
                        logger.warning(f"Auto-Pause aktiviert bis {until}: {reason}")
                        notify("syncin Bot", f"⚠️ Auto-Pause: {reason[:60]}")
                        time.sleep(30)
                        continue

                # ── Slots feuern ───────────────────────────────────────────
                fired_keys = {k for k in fired_keys if k.startswith(today) or k.startswith("recovery") or k.startswith("pause")}

                # Slot-Abstands-Warnung (nur einmal pro Tag)
                warn_key = f"gap_warned_{today}"
                if warn_key not in fired_keys:
                    fired_keys.add(warn_key)
                    slot_times = sorted([
                        int(s["time"].split(":")[0]) * 60 + int(s["time"].split(":")[1])
                        for s in cfg.get("slots", [])
                    ])
                    for i in range(1, len(slot_times)):
                        if slot_times[i] - slot_times[i-1] < 6 * 60:
                            logger.warning(f"Zeitplan: Slots liegen weniger als 6h auseinander — Shadow-Ban-Risiko!")
                            break

                for slot in cfg.get("slots", []):
                    target = slot.get("time", "18:00")
                    key    = f"{today}_{target}"
                    if cur_time == target and key not in fired_keys:
                        fired_keys.add(key)
                        mode  = slot.get("mode", "new")
                        label = slot.get("filename") if mode == "existing" else (slot.get("topic") or "zufällig")
                        logger.info(f"Zeitplan: Slot um {target} feuert (mode={mode}, {label})")
                        notify("syncin Bot", f"Zeitplan: Video um {target}…")
                        job_id = str(uuid.uuid4())[:8]
                        jobs[job_id] = {"status": "running", "progress": 0, "message": "Startet…", "video": None}
                        threading.Thread(
                            target=_run_scheduled_single,
                            args=(job_id, slot),
                            daemon=True,
                        ).start()
        except Exception as e:
            logger.error(f"Scheduler-Fehler: {e}")
        time.sleep(30)


def _run_scheduled_single(job_id: str, slot: dict):
    """Führt einen Zeitplan-Slot aus: entweder neues Video generieren+hochladen
    oder ein vorhandenes Video direkt hochladen."""
    mode     = slot.get("mode", "new")
    filename = slot.get("filename", "")

    if mode == "auto":
        # ── Auto: ältestes vorhandenes Video nehmen, sonst neu generieren ──
        candidates = sorted(
            [f for f in OUTPUT_DIR.glob("*.mp4")],
            key=lambda f: f.stat().st_mtime
        )
        picked = None
        for f in candidates:
            meta_f = f.with_suffix(".json")
            if meta_f.exists():
                try:
                    m = json.loads(meta_f.read_text(encoding="utf-8"))
                    if not m.get("uploaded", False):
                        picked = f
                        break
                except Exception:
                    pass
        if picked:
            filename = picked.name
            mode     = "existing"   # weiter als "existing" behandeln
            logger.info(f"Zeitplan (auto): nehme vorhandenes Video {filename}")
        else:
            mode = "new"            # kein Video vorhanden → neu generieren
            logger.info("Zeitplan (auto): kein vorhandenes Video → generiere neu")

    if mode == "existing" and filename:
        # ── Vorhandenes Video hochladen ────────────────────────────────────
        vp = OUTPUT_DIR / filename
        if not vp.exists():
            logger.error(f"Zeitplan: Datei nicht gefunden: {filename}")
            jobs[job_id]["status"]  = "error"
            jobs[job_id]["message"] = f"Datei nicht gefunden: {filename}"
            return
        meta_f  = vp.with_suffix(".json")
        caption = ""
        title   = ""
        if meta_f.exists():
            try:
                meta    = json.loads(meta_f.read_text(encoding="utf-8"))
                caption = meta.get("caption", "")
                title   = meta.get("title", "")
            except Exception:
                pass
        # Fallback: Caption aus Titel + Standard-Tags generieren wenn leer
        if not caption:
            fallback_cta = random.choice(_CTAS)
            caption = (
                f"{title + ' ' if title else ''}Wusstest du das? 🤯\n"
                f"{fallback_cta}\n"
                f"#fyp #tiktokdeutsch #fakten #wissen #viral"
            )
            logger.warning(f"Zeitplan: Caption war leer für {filename} — Fallback genutzt")
        jobs[job_id]["status"]  = "done"
        jobs[job_id]["message"] = f"Uploade {filename}…"
        jobs[job_id]["video"]   = filename
        logger.info(f"Zeitplan: uploade vorhandenes Video {filename} | Caption: {caption[:60]}…")
        _run_upload(filename, str(vp), caption)
        notify("syncin Bot", f"Zeitplan: Video hochgeladen!")
    else:
        # ── Neues Video generieren + hochladen ─────────────────────────────
        topic = slot.get("topic") or None
        long  = slot.get("long", False)
        _run_generation(job_id, topic, long)
        job = jobs.get(job_id, {})
        if job.get("video"):
            vp     = str(OUTPUT_DIR / job["video"])
            meta_f = Path(vp).with_suffix(".json")
            caption = ""
            title   = ""
            if meta_f.exists():
                try:
                    meta    = json.loads(meta_f.read_text(encoding="utf-8"))
                    caption = meta.get("caption", "")
                    title   = meta.get("title", "")
                except Exception:
                    pass
            if not caption:
                fallback_cta = random.choice(_CTAS)
                caption = (
                    f"{title + ' ' if title else ''}Wusstest du das? 🤯\n"
                    f"{fallback_cta}\n"
                    f"#fyp #tiktokdeutsch #fakten #wissen #viral"
                )
                logger.warning(f"Zeitplan: Caption war leer für {job['video']} — Fallback genutzt")
            logger.info(f"Zeitplan: starte Upload für {job['video']} | Caption: {caption[:60]}…")
            _run_upload(job["video"], vp, caption)
            notify("syncin Bot", f"Zeitplan: Video hochgeladen!")
        else:
            logger.error(f"Zeitplan: Video-Generierung fehlgeschlagen (job {job_id})")


@app.get("/api/videos/unuploaded")
def list_unuploaded():
    """Gibt alle lokal vorhandenen Videos zurück die noch nicht hochgeladen wurden."""
    result = []
    for mp4 in sorted(OUTPUT_DIR.glob("*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True):
        meta_file = mp4.with_suffix(".json")
        meta: dict = {}
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        if not meta.get("uploaded", False):
            result.append({
                "filename": mp4.name,
                "title":    meta.get("title", mp4.stem),
                "topic":    meta.get("topic", ""),
                "created":  datetime.fromtimestamp(mp4.stat().st_mtime).strftime("%d.%m. %H:%M"),
            })
    return result


@app.get("/api/schedule")
def get_schedule():
    return _load_schedule_cfg()


@app.post("/api/schedule")
def save_schedule(cfg: ScheduleConfig):
    data = cfg.model_dump()
    _save_schedule_cfg(data)
    n     = len(data["slots"])
    times = ", ".join(s["time"] for s in data["slots"])
    status = "aktiviert" if data["enabled"] else "deaktiviert"
    logger.info(f"Zeitplan {status}: {n} Slot(s) um {times}")
    return {"status": "ok", **data}


@app.post("/api/schedule/pause")
def manual_pause(days: int = 7, reason: str = "Manuell pausiert"):
    cfg = _load_schedule_cfg()
    until = (datetime.now() + __import__('datetime').timedelta(days=days)).strftime("%Y-%m-%d")
    cfg["recovery_until"]  = until
    cfg["recovery_reason"] = reason
    _save_schedule_cfg(cfg)
    logger.info(f"Manueller Recovery-Modus: {days} Tage bis {until} — {reason}")
    return {"status": "paused", "recovery_until": until}


@app.post("/api/schedule/resume")
def manual_resume():
    cfg = _load_schedule_cfg()
    cfg["recovery_until"]  = None
    cfg["recovery_reason"] = ""
    _save_schedule_cfg(cfg)
    logger.info("Recovery-Modus manuell beendet")
    return {"status": "resumed"}


# ── Beste Posting-Zeiten ─────────────────────────────────────────────────────

@app.get("/api/analytics/best-times")
def get_best_times():
    """
    Analysiert welche Tageszeiten die höchsten Views bringen.
    Matcht lokale JSON-Dateien (Erstellungszeitpunkt als Upload-Proxy)
    mit den Analytics-Daten (Views) und gruppiert nach Stunde.
    """
    from collections import defaultdict

    analytics = load_cached()
    if not analytics:
        return {"error": "Keine Analytics-Daten vorhanden. Bitte zuerst Stats abrufen.", "data": []}

    # Analytics nach bereinigtem Titel indexieren
    analytics_by_title: dict[str, int] = {}
    for video in analytics:
        key = video.get("title", "")[:50].lower().strip()
        if key:
            analytics_by_title[key] = video.get("views", 0)

    hour_views: dict[int, list[int]] = defaultdict(list)

    for jf in OUTPUT_DIR.glob("*.json"):
        try:
            d = json.loads(jf.read_text(encoding="utf-8"))
            title = d.get("title", "").strip()
            if not title or title.startswith("video_"):
                continue

            # Zeitstempel aus Dateiname ableiten (video_YYYYMMDD_HHMMSS.json)
            hour = None
            stem = jf.stem  # z.B. "video_20260415_183045"
            parts = stem.split("_")
            if len(parts) >= 3 and len(parts[2]) == 6:
                try:
                    hour = int(parts[2][:2])
                except Exception:
                    pass
            if hour is None:
                hour = datetime.fromtimestamp(jf.stat().st_mtime).hour

            # Views suchen — fuzzy match über Titel-Anfang
            views = 0
            title_key = title[:40].lower()
            for key, v in analytics_by_title.items():
                # Überschneidung von mindestens 3 Wörtern reicht
                t_words = set(title_key.split())
                k_words = set(key.split())
                if len(t_words & k_words) >= 2:
                    views = v
                    break

            hour_views[hour].append(views)
        except Exception:
            pass

    if not hour_views:
        return {"error": "Nicht genug Daten für Analyse (mind. 1 Video mit Analytics benötigt).", "data": []}

    result = []
    for hour in sorted(hour_views.keys()):
        vlist = hour_views[hour]
        avg   = sum(vlist) / len(vlist)
        result.append({
            "hour":        hour,
            "label":       f"{hour:02d}:00",
            "avg_views":   int(avg),
            "video_count": len(vlist),
        })

    result.sort(key=lambda x: x["avg_views"], reverse=True)
    return {"data": result, "top3": result[:3]}


# ── Analytics-Verlauf ─────────────────────────────────────────────────────────

def _append_analytics_history(data: list[dict]):
    history = []
    if ANALYTICS_HISTORY_FILE.exists():
        try:
            history = json.loads(ANALYTICS_HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    history.append({
        "timestamp":    datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total_views":  sum(v.get("views", 0)    for v in data),
        "total_likes":  sum(v.get("likes", 0)    for v in data),
        "total_comments": sum(v.get("comments", 0) for v in data),
        "total_videos": len(data),
    })
    history = history[-90:]   # max 90 Snapshots behalten
    ANALYTICS_HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


@app.get("/api/analytics/history")
def get_analytics_history():
    if ANALYTICS_HISTORY_FILE.exists():
        try:
            return json.loads(ANALYTICS_HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


# ── Logs ──────────────────────────────────────────────────────────────────────

@app.get("/api/logs")
def get_logs(lines: int = 80):
    if not LOG_FILE.exists():
        return {"logs": []}
    try:
        all_lines = LOG_FILE.read_text(encoding="utf-8").splitlines()
        return {"logs": all_lines[-lines:]}
    except Exception:
        return {"logs": []}


# ── Background Cache ──────────────────────────────────────────────────────────

cache_job: dict = {"status": "idle", "message": "", "progress": ""}

@app.post("/api/prefetch-cache")
def start_prefetch(count: int = 8):
    if cache_job["status"] == "running":
        return {"status": "already_running"}
    cache_job["status"]   = "running"
    cache_job["message"]  = "Starte Download..."
    cache_job["progress"] = ""
    t = threading.Thread(target=_run_prefetch, args=(count,), daemon=True)
    t.start()
    return {"status": "started"}

@app.get("/api/prefetch-status")
def prefetch_status():
    from video_creator import CACHE_DIR
    total = len(list(CACHE_DIR.glob("*.mp4")))
    return {**cache_job, "total_cached": total}

def _run_prefetch(count: int):
    import os
    from video_creator import _fetch_pexels_video, CACHE_DIR, TOPIC_QUERIES, PER_QUERY

    ALL_TOPICS = ["science","history","nature","technology","space",
                  "animals","psychology","food","geography","human body","pop culture"]
    api_key = os.environ.get("PEXELS_API_KEY", "")

    # Alle Sub-Queries aller Themen einsammeln
    all_queries: list[tuple[str, str]] = []  # (topic_label, sub_query)
    for topic in ALL_TOPICS:
        for sub_q in TOPIC_QUERIES.get(topic, [topic]):
            all_queries.append((topic, sub_q))

    total_q = len(all_queries)
    downloaded = 0

    for i, (topic, sub_q) in enumerate(all_queries):
        slug     = sub_q.replace(" ", "_")
        existing = len(list(CACHE_DIR.glob(f"{slug}_*.mp4")))
        cache_job["message"]  = f"{sub_q} ({i+1}/{total_q})"
        cache_job["progress"] = f"{i}/{total_q}"

        target = max(count, PER_QUERY)
        if existing < target:
            _fetch_pexels_video(sub_q, api_key, max_videos=target)
            new_count = len(list(CACHE_DIR.glob(f"{slug}_*.mp4")))
            downloaded += max(0, new_count - existing)

    total = len(list(CACHE_DIR.glob("*.mp4")))
    cache_job["status"]  = "done"
    cache_job["message"] = f"Fertig — {total} Videos im Cache (+{downloaded} neu)"
    logger.info(f"Prefetch abgeschlossen: {total} Videos total, {downloaded} neu heruntergeladen")


# ── Video-Dateien ausliefern ──────────────────────────────────────────────────

@app.get("/videos/{filename}")
def serve_video(filename: str):
    path = OUTPUT_DIR / filename
    if not path.exists():
        return {"error": "not found"}
    return FileResponse(str(path), media_type="video/mp4")


# ── Static Frontend (muss als letztes gemountet werden) ──────────────────────

app.mount("/", StaticFiles(directory=str(Path(__file__).parent / "static"), html=True))


def _auto_fill_cache():
    """
    Wird beim Start einmalig im Hintergrund ausgeführt.
    Füllt nur fehlende Cache-Einträge auf — bereits vorhandene Videos werden
    NICHT neu heruntergeladen. Kein manueller Eingriff nötig.
    Auf Railway: komplett deaktiviert — Videos werden on-demand beim Generieren geladen
    und bleiben dank Volume persistent gespeichert.
    """
    import os

    # Auf Railway: kein Vorabladen — verhindert Download-Sturm beim Start
    if IS_RAILWAY:
        logger.info("Cache-Startup: Railway-Modus — kein Vorabladen (on-demand bei Generierung)")
        return

    try:
        from video_creator import _fetch_pexels_video, CACHE_DIR, TOPIC_QUERIES, PER_QUERY
        api_key = os.environ.get("PEXELS_API_KEY", "")
        if not api_key:
            return

        ALL_TOPICS = ["science","history","nature","technology","space",
                      "animals","psychology","food","geography","human body","pop culture"]

        missing_queries = []
        for topic in ALL_TOPICS:
            for sub_q in TOPIC_QUERIES.get(topic, [topic]):
                slug = sub_q.replace(" ", "_")
                existing = len(list(CACHE_DIR.glob(f"{slug}_*.mp4")))
                if existing < PER_QUERY:
                    missing_queries.append((sub_q, existing))

        if not missing_queries:
            total = len(list(CACHE_DIR.glob("*.mp4")))
            logger.info(f"Cache vollständig ({total} Videos) — kein Nachfüllen nötig")
            return

        total_before = len(list(CACHE_DIR.glob("*.mp4")))
        logger.info(f"Cache-Startup: {len(missing_queries)} Sub-Queries unvollständig — lade nach…")

        for sub_q, existing in missing_queries:
            # Auf Railway: zwischen Downloads kurz pausieren damit Video-Generierung
            # nicht mit dem Cache-Download um Ressourcen konkurriert
            if IS_RAILWAY:
                time.sleep(2)
            try:
                _fetch_pexels_video(sub_q, api_key, max_videos=PER_QUERY)
            except Exception as e:
                logger.warning(f"Cache-Startup: Fehler bei '{sub_q}': {e}")

        total_after = len(list(CACHE_DIR.glob("*.mp4")))
        logger.info(f"Cache-Startup abgeschlossen: {total_after} Videos (+{total_after - total_before} neu)")

    except Exception as e:
        logger.warning(f"Cache-Startup fehlgeschlagen: {e}")


if __name__ == "__main__":
    # Persistente Queue laden
    _load_queue()

    # Hintergrund-Threads starten
    threading.Thread(target=_scheduler_loop,             daemon=True).start()
    threading.Thread(target=_queue_processor,            daemon=True).start()
    threading.Thread(target=_auto_fill_cache,            daemon=True).start()
    threading.Thread(target=_analytics_auto_refresh_loop, daemon=True).start()

    port = int(os.environ.get("PORT", 8000))
    logger.info(f"syncin Dashboard gestartet → http://0.0.0.0:{port}")
    print(f"\n  syncin Dashboard  →  http://0.0.0.0:{port}\n")
    # Railway braucht host=0.0.0.0 damit der Healthcheck durchkommt
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
