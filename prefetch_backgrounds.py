"""
Hintergrundvideo-Cache aufbauen
Lädt für alle Themen mehrere Videos herunter damit sie bei der Erstellung sofort verfügbar sind.

Verwendung:
  python prefetch_backgrounds.py            # 8 Videos pro Thema
  python prefetch_backgrounds.py --count 15 # mehr Videos
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)
sys.path.insert(0, str(Path(__file__).parent / "modules"))

from video_creator import _fetch_pexels_video, CACHE_DIR

TOPICS = [
    "science", "history", "nature", "technology", "space",
    "animals", "psychology", "food", "geography", "human body",
]


def prefetch(count: int = 8):
    api_key = os.environ.get("PEXELS_API_KEY", "")
    if not api_key:
        print("PEXELS_API_KEY fehlt in .env")
        return

    print(f"Cache-Verzeichnis: {CACHE_DIR}")
    print(f"Lade bis zu {count} Videos pro Thema ({len(TOPICS)} Themen)...\n")

    total_downloaded = 0
    total_size_mb = 0.0

    for topic in TOPICS:
        slug = topic.replace(" ", "_")
        existing = sorted(CACHE_DIR.glob(f"{slug}_*.mp4"))
        print(f"  {topic:20s} — {len(existing)} vorhanden", end="", flush=True)

        if len(existing) >= count:
            print(f" (vollständig, überspringe)")
            continue

        _fetch_pexels_video(topic, api_key, max_videos=count)

        after = sorted(CACHE_DIR.glob(f"{slug}_*.mp4"))
        new = len(after) - len(existing)
        size = sum(f.stat().st_size for f in after) / 1_048_576
        total_downloaded += new
        total_size_mb += size
        print(f" → {new} neu heruntergeladen ({size:.0f} MB gesamt)")

    print(f"\nFertig. {total_downloaded} neue Videos, Cache-Größe: {total_size_mb:.0f} MB")
    all_files = list(CACHE_DIR.glob("*.mp4"))
    print(f"Gesamt im Cache: {len(all_files)} Videos")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=8, help="Videos pro Thema")
    args = parser.parse_args()
    prefetch(args.count)
