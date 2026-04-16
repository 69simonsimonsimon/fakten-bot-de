#!/usr/bin/env python3
"""
TikTok OAuth Helper (manueller Code-Flow)
------------------------------------------
TikTok erlaubt kein localhost als Redirect URI.
Daher öffnen wir den Browser, du autorisierst die App,
und kopierst die Redirect-URL aus der Adressleiste.

Verwendung:
  python auth_tiktok.py
"""

import base64
import hashlib
import os
import secrets
import urllib.parse
import webbrowser
from pathlib import Path

import requests
from dotenv import load_dotenv, set_key

load_dotenv()

CLIENT_KEY = os.environ.get("TIKTOK_CLIENT_KEY", "")
CLIENT_SECRET = os.environ.get("TIKTOK_CLIENT_SECRET", "")
REDIRECT_URI = "https://example.com/callback"
SCOPES = "user.info.basic,video.publish,video.upload"
ENV_FILE = Path(__file__).parent / ".env"


def generate_pkce() -> tuple[str, str]:
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return code_verifier, code_challenge


def get_access_token(auth_code: str, code_verifier: str) -> dict:
    url = "https://open.tiktokapis.com/v2/oauth/token/"
    data = {
        "client_key": CLIENT_KEY,
        "client_secret": CLIENT_SECRET,
        "code": auth_code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
        "code_verifier": code_verifier,
    }
    resp = requests.post(url, data=data)
    resp.raise_for_status()
    return resp.json()


def main():
    if not CLIENT_KEY or not CLIENT_SECRET:
        print("FEHLER: TIKTOK_CLIENT_KEY und TIKTOK_CLIENT_SECRET in .env fehlen!")
        return

    code_verifier, code_challenge = generate_pkce()

    params = urllib.parse.urlencode({
        "client_key": CLIENT_KEY,
        "scope": SCOPES,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    })
    auth_url = f"https://www.tiktok.com/v2/auth/authorize/?{params}"

    print("\nTikTok OAuth Flow")
    print("=" * 50)
    print("SCHRITT 1: Redirect URI im TikTok Developer Portal setzen:")
    print(f"  → https://example.com/callback")
    print("  (Unter Login Kit → Web → diese URL eintragen und speichern)\n")
    print("SCHRITT 2: Browser öffnet sich — bei TikTok anmelden und Zugriff erlauben.")
    print("SCHRITT 3: Du wirst zu example.com weitergeleitet (Seite kann Fehler zeigen).")
    print("SCHRITT 4: Kopiere die KOMPLETTE URL aus der Adressleiste und füge sie hier ein.\n")
    print("=" * 50)

    input("Drücke ENTER um den Browser zu öffnen...")
    webbrowser.open(auth_url)

    print("\nNach der Autorisierung wirst du zu example.com weitergeleitet.")
    print("Die URL sieht ungefähr so aus:")
    print("  https://example.com/callback?code=XXXX&scopes=...&state=...\n")

    redirect_url = input("Füge die vollständige URL hier ein: ").strip()

    # Code aus URL extrahieren
    parsed = urllib.parse.urlparse(redirect_url)
    params_dict = urllib.parse.parse_qs(parsed.query)
    auth_code = params_dict.get("code", [None])[0]

    if not auth_code:
        print("\nFEHLER: Kein 'code' in der URL gefunden.")
        print("Stelle sicher dass du die vollständige URL kopiert hast.")
        return

    print(f"\nAuth-Code gefunden. Hole Access Token...")
    token_data = get_access_token(auth_code, code_verifier)

    access_token = token_data.get("access_token")
    open_id = token_data.get("open_id")

    if not access_token:
        print(f"\nFEHLER beim Token-Abruf: {token_data}")
        return

    if not ENV_FILE.exists():
        ENV_FILE.write_text("")
    set_key(str(ENV_FILE), "TIKTOK_ACCESS_TOKEN", access_token)
    if open_id:
        set_key(str(ENV_FILE), "TIKTOK_OPEN_ID", open_id)

    print(f"\nToken erfolgreich gespeichert!")
    print(f"Du kannst jetzt 'python3.12 main.py' ausführen.")


if __name__ == "__main__":
    main()
