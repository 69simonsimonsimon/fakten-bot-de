#!/usr/bin/env python3
"""
Synchronisiert lokale Analytics-Daten zu Railway.
Führe dieses Skript auf deinem Mac aus:

    python sync_to_railway.py

Es liest den lokalen Analytics-Cache und pusht ihn zur Railway-App.
"""

import json
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    print("❌ 'requests' fehlt — installiere mit: pip install requests")
    sys.exit(1)

CACHE_FILE = Path(__file__).parent / "dashboard" / "analytics_cache.json"
CONFIG_FILE = Path(__file__).parent / ".railway_url"


def get_railway_url() -> str:
    if CONFIG_FILE.exists():
        saved = CONFIG_FILE.read_text().strip()
        if saved:
            print(f"   Gespeicherte URL: {saved}")
            use = input("   Diese verwenden? [Enter = Ja, neue URL eingeben = Nein]: ").strip()
            if not use:
                return saved

    url = input("Railway-URL (z.B. https://syncin-bot.up.railway.app): ").strip().rstrip("/")
    if not url.startswith("http"):
        url = "https://" + url
    CONFIG_FILE.write_text(url)
    return url


def main():
    print("\n  syncin → Railway Analytics-Sync\n")

    if not CACHE_FILE.exists():
        print("❌ Kein lokaler Analytics-Cache gefunden.")
        print("   Öffne das lokale Dashboard und klicke einmal auf 'Stats abrufen'.")
        sys.exit(1)

    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ Cache konnte nicht gelesen werden: {e}")
        sys.exit(1)

    if not data:
        print("❌ Cache ist leer — bitte erst lokal Stats abrufen.")
        sys.exit(1)

    print(f"✓ {len(data)} Videos im lokalen Cache gefunden")

    railway_url = get_railway_url()

    print(f"   Sende zu {railway_url}/api/analytics/sync-cache ...")
    try:
        res = requests.post(
            f"{railway_url}/api/analytics/sync-cache",
            json=data,
            timeout=20,
        )
        res.raise_for_status()
        result = res.json()
        print(f"✓ Sync erfolgreich! {result.get('count', '?')} Videos auf Railway aktualisiert.\n")
    except requests.exceptions.ConnectionError:
        print(f"❌ Verbindung fehlgeschlagen — ist die URL richtig? ({railway_url})")
        sys.exit(1)
    except requests.exceptions.HTTPError as e:
        print(f"❌ Server-Fehler: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
