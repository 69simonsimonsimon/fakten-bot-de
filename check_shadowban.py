"""
TikTok Shadow-Ban Checker für @syncin2
Öffnet TikTok OHNE Login (wie ein zufälliger Besucher) und prüft ob der Account/Videos sichtbar sind.
Aufruf: python3 check_shadowban.py
"""

import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

ACCOUNT    = "syncin2"
PROFILE_URL = f"https://www.tiktok.com/@{ACCOUNT}"
DEBUG_DIR   = Path("/tmp/tiktok_shadowban")
DEBUG_DIR.mkdir(exist_ok=True)

# Letzten 2 Hashtags aus dem Analytics-Cache holen
def _get_recent_hashtags() -> list[str]:
    cache = Path(__file__).parent / "dashboard" / "analytics_cache.json"
    if not cache.exists():
        return []
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        tags = []
        for v in data[:2]:
            title = v.get("title", "")
            if title:
                tags.append(title)
        return tags
    except Exception:
        return []


async def check():
    recent = _get_recent_hashtags()
    print(f"\n{'='*55}")
    print(f"  Shadow-Ban Check für @{ACCOUNT}")
    print(f"{'='*55}")
    print(f"  Öffne Browser OHNE Login (Außenperspektive)...\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False, slow_mo=200,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        # Kein Cookie-Inject – frischer Browser wie ein normaler Besucher
        ctx  = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="de-DE",
        )
        page = await ctx.new_page()
        await page.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )

        results = {}

        # ── 1. Profil sichtbar? ───────────────────────────────────────────────
        print(f"[1] Öffne Profil: {PROFILE_URL}")
        await page.goto(PROFILE_URL, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(6000)
        await page.screenshot(path=str(DEBUG_DIR / "01_profil.png"), full_page=False)

        cur_url   = page.url.lower()
        page_text = (await page.evaluate("() => document.body.innerText")).lower()

        if "not found" in page_text or "couldn't find" in page_text or "404" in cur_url:
            results["profil"] = "❌ Profil nicht gefunden (Account gesperrt oder gelöscht?)"
        elif "login" in cur_url:
            results["profil"] = "⚠️  Weiterleitung zum Login (TikTok blockiert anonymen Zugriff)"
        else:
            # Videos auf dem Profil zählen
            vid_count = await page.evaluate("""() => {
                return document.querySelectorAll('a[href*="/video/"]').length;
            }""")
            # Follower/Views auslesen
            stats_text = await page.evaluate("""() => {
                const els = Array.from(document.querySelectorAll('[data-e2e*="followers"],[data-e2e*="views"],[class*="CountInfos"]'));
                return els.map(e => e.innerText.trim()).filter(t => t).join(' | ');
            }""")
            results["profil"] = f"✅ Profil sichtbar – {vid_count} Videos gefunden" + (f" | {stats_text}" if stats_text else "")

        print(f"    {results['profil']}")
        print(f"    → Screenshot: {DEBUG_DIR}/01_profil.png")

        # ── 2. Letztes Video sichtbar und abspielbar? ─────────────────────────
        print(f"\n[2] Prüfe ob neueste Videos sichtbar sind...")
        await page.wait_for_timeout(2000)

        videos_on_profile = await page.evaluate("""() => {
            return Array.from(document.querySelectorAll('a[href*="/video/"]'))
                .slice(0, 3)
                .map(a => ({
                    href: a.href,
                    text: a.innerText.trim().substring(0, 60),
                }));
        }""")

        if videos_on_profile:
            results["videos"] = f"✅ {len(videos_on_profile)} neueste Videos auf Profil sichtbar"
            for v in videos_on_profile:
                print(f"    → {v['href'][:70]}")
        else:
            results["videos"] = "❌ Keine Videos auf dem Profil sichtbar — möglicher Shadow Ban"
        print(f"    {results['videos']}")

        # ── 3. TikTok-Suche: Erscheint der Account? ───────────────────────────
        print(f"\n[3] Suche nach '@{ACCOUNT}' auf TikTok...")
        await page.goto(f"https://www.tiktok.com/search?q=%40{ACCOUNT}", wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(5000)
        await page.screenshot(path=str(DEBUG_DIR / "02_suche_account.png"), full_page=False)

        search_text = (await page.evaluate("() => document.body.innerText")).lower()
        account_found = ACCOUNT.lower() in search_text

        if account_found:
            results["suche"] = f"✅ Account '@{ACCOUNT}' in der Suche gefunden"
        else:
            results["suche"] = f"❌ Account '@{ACCOUNT}' taucht NICHT in der Suche auf — Shadow Ban wahrscheinlich"
        print(f"    {results['suche']}")
        print(f"    → Screenshot: {DEBUG_DIR}/02_suche_account.png")

        # ── 4. Hashtag-Check: Erscheinen Videos in Hashtag-Suchen? ───────────
        print(f"\n[4] Hashtag-Suche...")
        hashtags_to_check = ["wusstest", "fakten", "tiktokdeutsch"]
        hashtag_results = []

        for tag in hashtags_to_check[:2]:
            url = f"https://www.tiktok.com/tag/{tag}"
            print(f"    Öffne #{tag}...")
            await page.goto(url, wait_until="domcontentloaded", timeout=25_000)
            await page.wait_for_timeout(4000)

            tag_text = (await page.evaluate("() => document.body.innerText")).lower()
            tag_vids  = await page.evaluate("""() => document.querySelectorAll('a[href*="/video/"]').length""")

            if "not found" in tag_text or tag_vids == 0:
                hashtag_results.append(f"    #{tag}: ⚠️  Keine Videos gefunden")
            else:
                account_in_tag = ACCOUNT.lower() in tag_text
                if account_in_tag:
                    hashtag_results.append(f"    #{tag}: ✅ {tag_vids} Videos — @{ACCOUNT} sichtbar")
                else:
                    hashtag_results.append(f"    #{tag}: ⚠️  {tag_vids} Videos geladen — @{ACCOUNT} nicht darunter (kann normal sein)")

        for r in hashtag_results:
            print(r)
        results["hashtags"] = hashtag_results

        await page.screenshot(path=str(DEBUG_DIR / "03_hashtag.png"), full_page=False)

        # ── 5. Auswertung ─────────────────────────────────────────────────────
        await browser.close()

        print(f"\n{'='*55}")
        print("  ERGEBNIS")
        print(f"{'='*55}")

        ban_signals = 0
        if "❌" in results.get("profil", ""):    ban_signals += 2
        if "❌" in results.get("videos", ""):   ban_signals += 2
        if "❌" in results.get("suche", ""):    ban_signals += 2
        if "⚠️" in results.get("profil", ""):   ban_signals += 1
        if "⚠️" in results.get("suche", ""):    ban_signals += 1

        if ban_signals >= 4:
            verdict = "🚨 SHADOW BAN SEHR WAHRSCHEINLICH"
            advice  = ("TikTok zeigt deinen Account/Videos nicht für fremde Nutzer.\n"
                       "  Empfehlung: 3–7 Tage keine Videos posten, dann mit harmlosem\n"
                       "  Content ohne aggressive Hashtags neu starten.")
        elif ban_signals >= 2:
            verdict = "⚠️  EINGESCHRÄNKTE REICHWEITE möglich"
            advice  = ("Einige Signale deuten auf eingeschränkte Sichtbarkeit hin.\n"
                       "  Empfehlung: Hashtag-Nutzung reduzieren, 1–2 Tage Pause.")
        else:
            verdict = "✅ KEIN SHADOW BAN erkannt"
            advice  = ("Der Account ist von außen normal sichtbar.\n"
                       "  Die niedrigen Views könnten an Posting-Zeit, Hashtags oder\n"
                       "  dem TikTok-Algorithmus liegen (neue Videos brauchen 24–48h).")

        print(f"\n  {verdict}")
        print(f"\n  {advice}")
        print(f"\n  Screenshots gespeichert in: {DEBUG_DIR}")
        print(f"{'='*55}\n")


if __name__ == "__main__":
    asyncio.run(check())
