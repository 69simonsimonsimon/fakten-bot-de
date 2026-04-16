#!/usr/bin/env python3
"""
Synchronisiert lokale Analytics-Daten zu Railway.

Manuell:
    python3 sync_to_railway.py

Automatisch (kein Prompt, scrapt frische Daten):
    python3 sync_to_railway.py --auto
"""

import json
import sys
import argparse
from pathlib import Path

try:
    import requests
except ImportError:
    print("❌ 'requests' fehlt — installiere mit: pip install requests")
    sys.exit(1)

PROJECT_ROOT = Path(__file__).parent
CACHE_FILE   = PROJECT_ROOT / "dashboard" / "analytics_cache.json"
CONFIG_FILE  = PROJECT_ROOT / ".railway_url"

sys.path.insert(0, str(PROJECT_ROOT / "dashboard"))


def get_railway_url(auto: bool) -> str:
    if CONFIG_FILE.exists():
        saved = CONFIG_FILE.read_text().strip()
        if saved and (auto or not input(f"   Gespeicherte URL: {saved}\n   Diese verwenden? [Enter = Ja]: ").strip()):
            return saved

    if auto:
        print("❌ Keine Railway-URL gespeichert. Einmal manuell ausführen um sie zu speichern.")
        sys.exit(1)

    url = input("Railway-URL (z.B. https://syncin-bot.up.railway.app): ").strip().rstrip("/")
    if not url.startswith("http"):
        url = "https://" + url
    CONFIG_FILE.write_text(url)
    return url


def scrape_fresh(auto: bool) -> list | None:
    """Versucht frische Analytics via Playwright zu laden."""
    try:
        from analytics_scraper import fetch_analytics
        print("   Scrape frische TikTok-Analytics …")
        data = fetch_analytics()
        if data:
            print(f"   ✓ {len(data)} Videos gescrapt")
            return data
    except Exception as e:
        if not auto:
            print(f"   ⚠️  Scraping fehlgeschlagen: {e}")
    return None


def load_cached() -> list:
    if not CACHE_FILE.exists():
        return []
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def push_to_railway(data: list, railway_url: str) -> bool:
    print(f"   Sende {len(data)} Videos zu {railway_url} …")
    try:
        res = requests.post(
            f"{railway_url}/api/analytics/sync-cache",
            json=data,
            timeout=20,
        )
        res.raise_for_status()
        result = res.json()
        print(f"✓ Sync erfolgreich! {result.get('count', '?')} Videos auf Railway aktualisiert.")
        return True
    except requests.exceptions.ConnectionError:
        print(f"❌ Verbindung fehlgeschlagen — URL: {railway_url}")
        return False
    except requests.exceptions.HTTPError as e:
        print(f"❌ Server-Fehler: {e}")
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--auto", action="store_true",
                        help="Nicht-interaktiv: scrapt frische Daten und synct ohne Prompts")
    args = parser.parse_args()

    if not args.auto:
        print("\n  syncin → Railway Analytics-Sync\n")

    # 1. Frische Daten scrapen (im Auto-Modus immer, sonst auch)
    data = scrape_fresh(args.auto)

    # 2. Fallback: gecachte Daten
    if not data:
        data = load_cached()
        if not data:
            print("❌ Keine Analytics-Daten vorhanden. Bitte einmal manuell 'Stats abrufen' klicken.")
            sys.exit(1)
        print(f"   Nutze gecachte Daten ({len(data)} Videos)")

    # 3. Zu Railway pushen
    railway_url = get_railway_url(args.auto)
    ok = push_to_railway(data, railway_url)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
