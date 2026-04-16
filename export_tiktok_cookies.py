#!/usr/bin/env python3
"""
Exportiert TikTok-Cookies für Railway.
Einmalig auf dem Mac ausführen:

    python3 export_tiktok_cookies.py

Danach den ausgegebenen Wert als Railway-Variable 'TIKTOK_COOKIES' eintragen.
"""

import asyncio
import json
from pathlib import Path

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("❌ Playwright fehlt — installiere mit: pip install playwright")
    raise


async def main():
    print("\n  syncin → TikTok Cookie Export\n")

    out_file = Path(__file__).parent / "tiktok_cookies.json"

    async with async_playwright() as p:
        # Sichtbarer Browser — damit du dich einloggen kannst falls nötig
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )

        # Vorhandene lokale Chrome-Cookies versuchen zu laden
        try:
            import browser_cookie3
            jar = browser_cookie3.chrome(domain_name=".tiktok.com")
            pre_cookies = [
                {
                    "name":   c.name,
                    "value":  c.value,
                    "domain": c.domain if c.domain.startswith(".") else "." + c.domain,
                    "path":   c.path or "/",
                }
                for c in jar
            ]
            if pre_cookies:
                await ctx.add_cookies(pre_cookies)
                print(f"   {len(pre_cookies)} lokale Chrome-Cookies vorgeladen…")
        except Exception:
            pass  # browser_cookie3 optional

        page = await ctx.new_page()
        await page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        print("   Öffne TikTok Studio…")
        await page.goto(
            "https://www.tiktok.com/tiktokstudio/content",
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        await page.wait_for_timeout(4000)

        # Falls Login-Seite → warten bis eingeloggt
        if "login" in page.url.lower():
            print("\n   ⚠️  Nicht eingeloggt — bitte im Browser-Fenster bei TikTok einloggen…")
            print("   (Das Fenster bleibt offen bis du eingeloggt bist)\n")
            for _ in range(72):  # max 6 Minuten warten
                await page.wait_for_timeout(5000)
                if "login" not in page.url.lower():
                    print("   ✓ Login erkannt!")
                    await page.wait_for_timeout(3000)
                    break
            else:
                print("❌ Timeout — kein Login in 6 Minuten erkannt")
                await browser.close()
                return

        # Cookies exportieren
        all_cookies = await ctx.cookies()
        tiktok_cookies = [
            c for c in all_cookies
            if "tiktok.com" in c.get("domain", "")
        ]

        await browser.close()

    if not tiktok_cookies:
        print("❌ Keine TikTok-Cookies gefunden — bist du eingeloggt?")
        return

    # Nur die nötigen Felder behalten
    clean = [
        {
            "name":   c["name"],
            "value":  c["value"],
            "domain": c["domain"],
            "path":   c.get("path", "/"),
        }
        for c in tiktok_cookies
    ]

    cookie_json = json.dumps(clean, ensure_ascii=False)
    out_file.write_text(cookie_json, encoding="utf-8")

    print(f"\n✓ {len(clean)} Cookies exportiert → {out_file}\n")
    print("=" * 60)
    print("Geh jetzt zu Railway → dein Service → Variables:")
    print()
    print("  Name:  TIKTOK_COOKIES")
    print(f"  Value: (Inhalt der Datei tiktok_cookies.json)")
    print()
    print("Den genauen Wert findest du in:")
    print(f"  {out_file}")
    print("=" * 60)
    print()
    print("Nach dem Setzen der Variable: Railway deployed neu,")
    print("danach laufen Analytics automatisch auf Railway! 🎉")


if __name__ == "__main__":
    asyncio.run(main())
