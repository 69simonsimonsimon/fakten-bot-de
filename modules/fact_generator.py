import anthropic
import json
import os
import re
from pathlib import Path

# Größerer Pool — wird pro Video zufällig rotiert (kein fester Hashtag-Fingerabdruck)
_HASHTAG_POOL = [
    # Reach
    "#fyp", "#foryou", "#foryoupage", "#viral", "#trending", "#explore",
    # Deutsch
    "#tiktokdeutsch", "#deutsch", "#deutschtiktok", "#germantiktok", "#österreich", "#schweiz",
    # Wissen
    "#fakten", "#wusstest", "#lernen", "#bildung", "#wissen", "#interessant",
    "#überraschend", "#krass", "#unglaublich", "#faktendestages",
    "#wissenswert", "#lernenmittiktok", "#didyouknow", "#funfact",
]
_HASHTAG_CORE = ["#fyp", "#tiktokdeutsch", "#fakten"]   # immer dabei


def _get_base_hashtags() -> list[str]:
    """Wählt pro Video eine zufällige Kombination aus dem Pool — kein fester Fingerabdruck."""
    import random
    pool = [t for t in _HASHTAG_POOL if t not in _HASHTAG_CORE]
    random.shuffle(pool)
    return _HASHTAG_CORE + pool[:6]   # 3 Core + 6 zufällige = 9 Base-Tags

HISTORY_FILE = Path(__file__).parent.parent / "output" / "fact_history.json"


def _load_history() -> list[dict]:
    """
    Lädt alle bisherigen Fakten als Liste von {title, summary}.
    Liest aus der History-Datei UND aus allen vorhandenen JSON-Metadaten.
    """
    entries: dict[str, str] = {}  # title → summary

    # 1. History-Datei (neues Format: [{title, summary}] oder altes Format: ["title"])
    if HISTORY_FILE.exists():
        try:
            raw = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            for item in raw:
                if isinstance(item, str):
                    if item.strip():
                        entries.setdefault(item.strip(), "")
                elif isinstance(item, dict):
                    t = item.get("title", "").strip()
                    if t:
                        entries[t] = item.get("summary", "")
        except Exception:
            pass

    # 2. Alle JSON-Metadaten im Output-Ordner
    output_dir = HISTORY_FILE.parent
    for jf in output_dir.glob("*.json"):
        if jf.name == "fact_history.json":
            continue
        try:
            d = json.loads(jf.read_text(encoding="utf-8"))
            title = d.get("title", "").strip()
            # Dateinamen-artige Einträge überspringen (z.B. "video_20260415_095726")
            if title and not title.startswith("video_") and len(title) >= 8:
                entries.setdefault(title, "")
        except Exception:
            pass

    return [{"title": t, "summary": s} for t, s in entries.items() if t]


def _save_to_history(title: str, summary: str = ""):
    """Fügt einen Fakt (Titel + kurze Zusammenfassung) zur History hinzu."""
    try:
        existing = {e["title"]: e["summary"] for e in _load_history()}
        existing[title] = summary[:150] if summary else ""
        history = [{"title": t, "summary": s} for t, s in existing.items()]
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        HISTORY_FILE.write_text(
            json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        print(f"   Warnung: History konnte nicht gespeichert werden: {e}")


_STOPWORDS = {
    "haben","sind","wurde","wird","kann","kein","nicht","aber","auch","mehr",
    "sehr","noch","dass","dies","eine","einem","einen","einer","über","unter",
    "beim","oder","wenn","dann","damit","dabei","nach","schon","immer","ohne",
    "alle","viele","durch","welche","dieser","dieses","diese","ihre","ihrer",
    "sein","seine","seinen","seiner","jeder","jeden","jede","doch","erst","fast",
    "etwa","rund","lang","kurz","groß","klein","weit","tief","hoch","voll",
    "jetzt","hier","dort","heute","immer","meist","schon","noch","weil","denn",
    "beim","nach","seit","mehr","muss","soll","sogar","genau","ganze","dabei",
}


def _keywords(text: str) -> set[str]:
    """Extrahiert bedeutungstragende Wörter (>4 Zeichen, keine Stoppwörter)."""
    words = re.findall(r'\b[a-zäöüß]{4,}\b', text.lower())
    return {w for w in words if w not in _STOPWORDS}


def _is_too_similar(title: str, fact: str, history: list[dict]) -> tuple[bool, str]:
    """
    Prüft ob ein neu generierter Fakt inhaltlich zu ähnlich zu einem vorhandenen ist.
    Gibt (True, ähnlicher_Titel) zurück wenn Ähnlichkeit >= 45%.
    """
    new_kw = _keywords(title + " " + fact[:300])
    if not new_kw:
        return False, ""

    for entry in history:
        hist_text = entry["title"] + " " + entry.get("summary", "")
        hist_kw   = _keywords(hist_text)
        if not hist_kw:
            continue
        overlap = len(new_kw & hist_kw) / min(len(new_kw), len(hist_kw))
        if overlap >= 0.45:
            return True, entry["title"]

    return False, ""


def generate_fact(topic: str = "general", long: bool = False) -> dict:
    """
    Generiert einen Fakt mit Titel, Text, Beschreibung und Hashtags.
    Vermeidet dabei alle bereits generierten Fakten — auch inhaltlich ähnliche.
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"].strip())

    fact_length = (
        "Den Fakt ausführlich in 8-10 spannenden Sätzen erklären (Deutsch). "
        "Erkläre den Hintergrund, gib Beispiele, nenne Zahlen und überrasche mit einem Abschluss-Gedanken. "
        "Ziel: mindestens 180 Wörter."
        if long else
        "Den Fakt in 2-3 spannenden Sätzen erklärt (Deutsch). Überraschend und lehrreich."
    )

    # Bisherige Fakten laden
    used = _load_history()
    avoid_block = ""
    if used:
        avoid_lines = []
        for e in used:
            line = f"- {e['title']}"
            if e.get("summary"):
                line += f"  →  {e['summary'][:100]}"
            avoid_lines.append(line)
        avoid_list = "\n".join(avoid_lines)
        avoid_block = f"""
WICHTIG – Diese Fakten wurden bereits verwendet. Weder der exakte Titel noch ein inhaltlich ähnlicher Fakt darf nochmal vorkommen:
{avoid_list}

Auch diese inhaltlichen Bereiche sind damit GESPERRT (kein Fakt über dieselbe Kernaussage, egal mit welchem Tier/Objekt formuliert).
Wähle ein komplett anderes, überraschendes Thema!
"""

    # Spezieller Prompt für Popkultur
    if topic.lower() in ("pop culture", "popkultur", "pop-kultur"):
        from datetime import date
        today = date.today().strftime("%B %Y")  # z.B. "April 2026"
        prompt = f"""Erstelle einen überraschenden Fakt über ein aktuelles Popkultur-Thema für ein deutsches TikTok-Video (@syncin2).
Heute ist: {today}
{avoid_block}
Fokussiere dich auf AKTUELLE Themen wie:
- Aktuelle Filme, Serien oder Streaming-Hits (Netflix, Disney+, etc.)
- Aktuelle Musik, Künstler, Alben oder Rekorde
- Aktuelle Social-Media-Trends oder viral gegangene Momente
- Aktuelle Videospiele oder Gaming-Phänomene
- Aktuelle Promis, ihre Rekorde oder überraschende Fakten
- Aktuelle Meme-Kulturen oder Internet-Phänomene

Wähle etwas das junge Deutsche (16–30 Jahre) kennen und interessant finden.
Vermeide alte oder veraltete Themen – der Fakt soll sich HEUTE relevant anfühlen.

Gib NUR valides JSON zurück (kein Markdown, kein extra Text):
{{
  "title": "Kurzer, packender Titel (max 6 Wörter, Deutsch)",
  "fact": "{fact_length}",
  "description": "Eine kurze, neugierig machende TikTok-Beschreibung (1-2 Sätze, Deutsch, mit 1-2 passenden Emojis). Nicht mehr als 100 Zeichen.",
  "hashtags": ["#popkultur1", "#popkultur2", "#popkultur3", "#popkultur4", "#popkultur5"],
  "visual_query": "2-3 englische Suchbegriffe für ein passendes Stockvideo (z.B. 'concert crowd lights' oder 'phone social media scroll'). Nur filmisch umsetzbare Motive — keine abstrakten Begriffe."
}}

Regeln:
- Alles auf Deutsch (außer visual_query)
- Fakt muss 100% wahr und verifizierbar sein
- Titel muss einen HOOK enthalten: entweder den Namen einer bekannten Person/Show + überraschende Zahl ODER eine kontraintuitive Aussage ("Dieser Netflix-Hit wurde fast gecancelt")
- Eröffne den Fakt mit einem Curiosity-Gap — was kaum jemand über dieses Thema weiß
- Nutze konkrete Zahlen: Streams, Follower, Einnahmen, Rekorde, Datum
- Struktur: "Die meisten Fans wissen nicht, dass X" oder "Bevor Y Weltstar wurde, Z"
- Beschreibung soll Neugier wecken ohne den Fakt zu verraten (Teaser-Stil)
- Hashtags: 5 themenspezifische Popkultur-Hashtags (z.B. #netflix #taylorswift)"""
    else:
        prompt = f"""Erstelle einen faszinierenden Fakt für ein deutsches TikTok-Video (@syncin2).
Thema: {topic}
{avoid_block}
Gib NUR valides JSON zurück (kein Markdown, kein extra Text):
{{
  "title": "Kurzer, packender Titel (max 6 Wörter, Deutsch)",
  "fact": "{fact_length}",
  "description": "Eine kurze, neugierig machende TikTok-Beschreibung (1-2 Sätze, Deutsch, mit 1-2 passenden Emojis). Nicht mehr als 100 Zeichen.",
  "hashtags": ["#themenspezifisch1", "#themenspezifisch2", "#themenspezifisch3", "#themenspezifisch4", "#themenspezifisch5"],
  "visual_query": "2-3 englische Suchbegriffe für ein passendes Stockvideo (z.B. 'honey bees golden' oder 'deep ocean bioluminescence'). Nur filmisch umsetzbare Motive — keine abstrakten Begriffe."
}}

Regeln:
- Alles auf Deutsch (außer visual_query)
- Fakt muss 100% wahr und verifizierbar sein
- Titel muss einen HOOK enthalten: entweder eine konkrete Zahl/Größenangabe ODER eine paradoxe Aussage ("Das Gehirn fühlt keinen Schmerz") ODER ein Vergleich der Dimensionen sprengt ("Größer als Deutschland")
- Eröffne den Fakt mit einem Curiosity-Gap: Was fast niemand weiß / Was Wissenschaftler überrascht hat / Was komplett gegen die Intuition läuft
- Struktur: "Die meisten denken X — aber tatsächlich ist Y" oder "X klingt unmöglich, ist aber wahr: Y"
- Nutze konkrete Zahlen, Jahreszahlen oder Vergleiche statt vager Aussagen ("97 % der Menschen" statt "viele Menschen")
- Beschreibung soll Neugier wecken ohne den Fakt zu verraten (Teaser-Stil)
- Hashtags: 5 themenspezifische (z.B. #weltall #venus für Space-Themen)"""

    MAX_ATTEMPTS = 5
    data = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        # Bei Wiederholungsversuch: gesperrte Themen in Prompt ergänzen
        attempt_prompt = prompt
        if attempt > 1 and data:
            attempt_prompt += (
                f"\n\nZUSATZ: Dein letzter Vorschlag '{data.get('title','')}' war "
                f"inhaltlich zu ähnlich zu einem vorhandenen Fakt. "
                f"Wähle diesmal ein KOMPLETT anderes Kernthema!"
            )

        message = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1400 if long else 800,
            messages=[{"role": "user", "content": attempt_prompt}],
        )

        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())

        # Lokaler Ähnlichkeitscheck
        too_similar, similar_to = _is_too_similar(
            data.get("title", ""), data.get("fact", ""), used
        )
        if too_similar:
            print(f"   ⚠️  Versuch {attempt}/{MAX_ATTEMPTS}: '{data['title']}' zu ähnlich zu '{similar_to}' — neuer Versuch…")
            if attempt == MAX_ATTEMPTS:
                print("   ⚠️  Max. Versuche erreicht, nehme letzten Vorschlag trotzdem.")
            continue

        print(f"   ✓ Ähnlichkeitscheck OK (Versuch {attempt}/{MAX_ATTEMPTS})")
        break

    # Rotierende Base-Hashtags hinzufügen (keine Duplikate)
    existing_tags = {h.lower() for h in data.get("hashtags", [])}
    for tag in _get_base_hashtags():
        if tag.lower() not in existing_tags:
            data["hashtags"].append(tag)

    # Titel + Kern des Fakts in History speichern
    summary = data.get("fact", "")[:150].strip()
    _save_to_history(data["title"], summary)
    print(f"   History aktualisiert: '{data['title']}' ({len(used)+1} Fakten gespeichert)")

    return data


if __name__ == "__main__":
    fact = generate_fact("space")
    print(json.dumps(fact, ensure_ascii=False, indent=2))
