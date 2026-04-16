"""
Debug-Script für TikTok Upload
Macht Screenshots an jedem Schritt und loggt alle Buttons/Elemente.
Aufruf: python3 debug_upload.py
"""

import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload"
DEBUG_DIR  = Path("/tmp/tiktok_debug")
DEBUG_DIR.mkdir(exist_ok=True)


def _get_chrome_cookies():
    try:
        import browser_cookie3
        jar = browser_cookie3.chrome(domain_name=".tiktok.com")
        return [{"name": c.name, "value": c.value,
                 "domain": c.domain if c.domain.startswith(".") else "." + c.domain,
                 "path": c.path or "/"} for c in jar]
    except Exception as e:
        print(f"   Cookies: {e}")
        return []


async def run(video_path: str):
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False, slow_mo=200,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx  = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()
        await page.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )

        cookies = _get_chrome_cookies()
        if cookies:
            await ctx.add_cookies(cookies)
            print(f"   {len(cookies)} Cookies geladen")

        # ── 1. Seite laden ────────────────────────────────────────────────────
        print("\n[1] Öffne TikTok Studio...")
        await page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(10_000)
        await page.screenshot(path=str(DEBUG_DIR / "01_geladen.png"), full_page=False)
        print(f"    URL: {page.url}")
        print(f"    → Screenshot: {DEBUG_DIR}/01_geladen.png")

        if "login" in page.url.lower():
            print("    Bitte einloggen – warte bis zu 3 Minuten...")
            for _ in range(36):
                await page.wait_for_timeout(5000)
                if "login" not in page.url.lower():
                    print("    Login erkannt.")
                    await page.wait_for_timeout(5000)
                    break

        # ── 2. Video hochladen ─────────────────────────────────────────────────
        print(f"\n[2] Lade Video: {video_path}")
        fi = page.locator("input[type=file]")
        if await fi.count() == 0:
            print("    FEHLER: kein <input type=file> gefunden!")
            # Prüfe ob iframe
            frames = page.frames
            print(f"    Frames auf der Seite: {[f.url for f in frames]}")
        else:
            await fi.set_input_files(video_path)
            print("    Datei übergeben.")

        await page.wait_for_timeout(5000)
        await page.screenshot(path=str(DEBUG_DIR / "02_nach_upload.png"), full_page=False)
        print(f"    → Screenshot: {DEBUG_DIR}/02_nach_upload.png")

        # ── 3. Buttons loggen ─────────────────────────────────────────────────
        def log_buttons(label):
            return page.evaluate("""() => {
                return Array.from(document.querySelectorAll('button'))
                    .map(b => ({
                        text:         b.innerText.trim().substring(0,60),
                        dataE2e:      b.getAttribute('data-e2e'),
                        ariaDisabled: b.getAttribute('aria-disabled'),
                        disabled:     b.disabled,
                        visible:      b.offsetWidth > 0 && b.offsetHeight > 0,
                    }))
                    .filter(b => b.visible && b.text);
            }""")

        btns = await log_buttons("initial")
        print(f"\n    Sichtbare Buttons direkt nach Upload:")
        for b in btns:
            print(f"      [{b.get('dataE2e') or '-':30}] '{b['text'][:40]}'"
                  f"  aria-disabled={b['ariaDisabled']}  disabled={b['disabled']}")

        # ── 4. Auf Verarbeitung warten ────────────────────────────────────────
        print("\n[3] Warte auf Post-Button (max 4 Minuten)...")
        post_btn = None
        for sel in [
            "[data-e2e=post_video_button]",
            "button:has-text('Veröffentlichen')",
            "button:has-text('Post')",
            "button:has-text('Posten')",
            "button:has-text('Publish')",
        ]:
            if await page.locator(sel).count() > 0:
                post_btn = page.locator(sel).first
                print(f"    Post-Button Selektor: {sel}")
                break

        if post_btn is None:
            print("    KEIN POST-BUTTON GEFUNDEN!")
            await page.screenshot(path=str(DEBUG_DIR / "03_kein_button.png"), full_page=True)
            await browser.close()
            return

        for i in range(120):
            aria     = await post_btn.get_attribute("aria-disabled")
            disabled = await post_btn.get_attribute("disabled")
            if i % 10 == 0:
                print(f"    {i*2}s: aria-disabled={aria!r}  disabled={disabled!r}")
            if aria != "true" and disabled is None:
                print(f"    ✓ Button klickbar nach {i*2}s")
                break
            await page.wait_for_timeout(2000)

        await page.screenshot(path=str(DEBUG_DIR / "03_vor_klick.png"), full_page=False)
        print(f"    → Screenshot: {DEBUG_DIR}/03_vor_klick.png")

        # ── 5. Beschreibung einfügen ──────────────────────────────────────────
        print("\n[4] Trage Test-Beschreibung ein...")
        for sel in [
            "[data-e2e=caption_container] .public-DraftEditor-content",
            ".public-DraftEditor-content",
            "[contenteditable=true]",
        ]:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                await loc.click()
                await page.wait_for_timeout(300)
                await page.keyboard.press("Control+a")
                await page.keyboard.press("Delete")
                await page.keyboard.type("Debug Test #debug", delay=30)
                filled = await loc.inner_text()
                print(f"    Feld '{sel}' → eingetragen: '{filled.strip()[:50]}'")
                break
        else:
            print("    Kein Beschreibungsfeld gefunden.")

        await page.wait_for_timeout(1000)
        await page.screenshot(path=str(DEBUG_DIR / "04_nach_caption.png"), full_page=False)
        print(f"    → Screenshot: {DEBUG_DIR}/04_nach_caption.png")

        # ── 6. Buttons nach Caption nochmal prüfen ─────────────────────────────
        btns2 = await log_buttons("nach caption")
        print(f"\n    Buttons nach Caption-Eingabe:")
        for b in btns2:
            print(f"      [{b.get('dataE2e') or '-':30}] '{b['text'][:40]}'"
                  f"  aria-disabled={b['ariaDisabled']}  disabled={b['disabled']}")

        aria_now = await post_btn.get_attribute("aria-disabled")
        print(f"\n    Post-Button aria-disabled JETZT: {aria_now!r}")

        # ── 7. Klicken ────────────────────────────────────────────────────────
        print("\n[5] Klicke Post-Button...")
        url_before = page.url
        await post_btn.scroll_into_view_if_needed()
        await page.wait_for_timeout(500)
        await post_btn.click()
        print(f"    Geklickt. Warte 8s...")

        await page.wait_for_timeout(8000)
        await page.screenshot(path=str(DEBUG_DIR / "05_nach_klick.png"), full_page=False)
        print(f"    → Screenshot: {DEBUG_DIR}/05_nach_klick.png")
        print(f"    URL vorher:  {url_before}")
        print(f"    URL nachher: {page.url}")

        # Dialoge/Overlays?
        dialogs = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll(
                '[role=dialog],[role=alertdialog],[class*=modal],[class*=Modal],[class*=overlay]'
            )).map(e => e.innerText.trim().substring(0,200)).filter(t => t.length > 3);
        }""")
        if dialogs:
            print(f"\n    Dialoge/Overlays nach dem Klick:")
            for d in dialogs:
                print(f"      → {d[:120]}")

        print(f"\nAlle Screenshots in: {DEBUG_DIR}")
        print("Browser bleibt 60s offen für manuelle Inspektion...")
        await page.wait_for_timeout(60_000)
        await browser.close()


if __name__ == "__main__":
    output_dir = Path(__file__).parent / "output"
    if len(sys.argv) > 1:
        video = sys.argv[1]
    else:
        videos = sorted(output_dir.glob("*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True)
        if not videos:
            print("Kein Video im output/ Ordner gefunden.")
            sys.exit(1)
        video = str(videos[0])
        print(f"Nehme neuestes Video: {Path(video).name}")

    asyncio.run(run(video))
