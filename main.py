#!/usr/bin/env python3
"""
TikTok Fakten-Bot
-----------------
Erstellt automatisch ein Fakten-Video und lädt es auf TikTok hoch.

Verwendung:
  python main.py                    # Einmalig ein Video erstellen + hochladen
  python main.py --topic science    # Bestimmtes Thema wählen
  python main.py --only-create      # Nur Video erstellen, nicht hochladen
  python main.py --schedule         # Täglich automatisch posten (cron-Modus)
"""

import argparse
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# Lade .env Datei
load_dotenv(Path(__file__).parent / ".env", override=True)

# Füge modules/ zum Pfad hinzu
sys.path.insert(0, str(Path(__file__).parent / "modules"))

from fact_generator import generate_fact
from tts import text_to_speech, get_sentence_timings  # noqa: F401
from video_creator import create_video
from tiktok_uploader import upload_video
from tiktok_uploader_browser import upload_video_browser


TOPICS = [
    "science",
    "history",
    "nature",
    "technology",
    "space",
    "animals",
    "psychology",
    "food",
    "geography",
    "human body",
    "pop culture",
]

OUTPUT_DIR = Path(__file__).parent / "output"


def run_once(topic: str = None, only_create: bool = False, privacy: str = "SELF_ONLY", long: bool = False) -> str:
    """
    Hauptfunktion: Erstellt ein Video und lädt es hoch.
    Gibt den Pfad zur erstellten Videodatei zurück.
    """
    topic = topic or random.choice(TOPICS)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    audio_path = OUTPUT_DIR / f"audio_{timestamp}.mp3"
    video_path = OUTPUT_DIR / f"video_{timestamp}.mp4"

    print(f"\n{'='*50}")
    print(f"TikTok Bot gestartet — {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    print(f"Thema: {topic}")
    print(f"{'='*50}\n")

    # 1. Fakt generieren
    print("1. Generiere Fakt...")
    fact_data = generate_fact(topic, long=long)
    print(f"   Titel: {fact_data['title']}")
    print(f"   Fakt:  {fact_data['fact'][:80]}...")

    # 2. Text-to-Speech
    print("\n2. Erstelle Voiceover...")
    tts_text = f"{fact_data['title']}. {fact_data['fact']}"
    _, word_timings = text_to_speech(tts_text, str(audio_path))
    print(f"   Audio: {audio_path.name} ({len(word_timings)} Wörter)")

    # 3. Video erstellen
    print("\n3. Erstelle Video...")
    gradient_index = random.randint(0, 4)
    create_video(
        title=fact_data["title"],
        fact=fact_data["fact"],
        audio_path=str(audio_path),
        output_path=str(video_path),
        word_timings=word_timings,
        gradient_index=gradient_index,
        topic=topic,
    )
    print(f"   Video: {video_path.name}")

    # Temporäre Audio-Datei löschen
    audio_path.unlink(missing_ok=True)

    description  = fact_data.get("description", fact_data["title"])
    full_caption = description + " " + " ".join(fact_data["hashtags"])

    # Metadaten speichern (für Dashboard + Upload)
    import json as _json
    meta = {
        "title":    fact_data["title"],
        "topic":    topic,
        "caption":  full_caption,
        "uploaded": False,
    }
    Path(str(video_path).replace(".mp4", ".json")).write_text(
        _json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if only_create:
        print(f"\nVideo gespeichert (kein Upload): {video_path}")
        print(f"\nBeschreibung für TikTok:\n{full_caption}")
        return str(video_path)

    # 4. TikTok Upload (Browser)
    print("\n4. Lade auf TikTok hoch (Browser)...")
    try:
        success = upload_video_browser(str(video_path), full_caption)
        if success:
            print(f"\nErfolgreich hochgeladen!")
        else:
            print(f"\nUpload nicht abgeschlossen — Video lokal: {video_path}")
    except Exception as e:
        print(f"\nUpload fehlgeschlagen: {e}")
        print(f"Video lokal gespeichert: {video_path}")

    return str(video_path)


def run_scheduler(topic: str = None, interval_hours: int = 24, privacy: str = "PUBLIC_TO_EVERYONE"):
    """Läuft dauerhaft und postet alle `interval_hours` Stunden ein neues Video."""
    print(f"Scheduler gestartet — postet alle {interval_hours} Stunden")
    print("Mit Ctrl+C beenden.\n")

    while True:
        try:
            run_once(topic=topic, privacy=privacy)
        except Exception as e:
            print(f"Fehler beim Erstellen/Hochladen: {e}")

        next_run = datetime.fromtimestamp(time.time() + interval_hours * 3600)
        print(f"\nNächster Post: {next_run.strftime('%d.%m.%Y %H:%M:%S')}")
        print(f"Warte {interval_hours} Stunden...\n")
        time.sleep(interval_hours * 3600)


def main():
    parser = argparse.ArgumentParser(description="TikTok Fakten-Bot")
    parser.add_argument("--topic", type=str, default=None, help=f"Thema auswählen: {', '.join(TOPICS)}")
    parser.add_argument("--only-create", action="store_true", help="Nur Video erstellen, nicht hochladen")
    parser.add_argument("--schedule", action="store_true", help="Dauerhaft alle 24h posten")
    parser.add_argument("--interval", type=int, default=24, help="Stunden zwischen Posts (Standard: 24)")
    parser.add_argument(
        "--long", action="store_true", help="Längeres Video (min. 1 Minute)",
    )
    parser.add_argument(
        "--privacy",
        type=str,
        default="SELF_ONLY",
        choices=["SELF_ONLY", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "PUBLIC_TO_EVERYONE"],
        help="TikTok Sichtbarkeit (Standard: SELF_ONLY zum Testen)",
    )
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(exist_ok=True)

    if args.schedule:
        run_scheduler(topic=args.topic, interval_hours=args.interval, privacy=args.privacy)
    else:
        run_once(topic=args.topic, only_create=args.only_create, privacy=args.privacy, long=args.long)


if __name__ == "__main__":
    main()
