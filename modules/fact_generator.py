import anthropic
import json
import os
import re
import threading
from pathlib import Path

# Lock verhindert dass zwei gleichzeitige Generierungen dieselbe History lesen
# und denselben Fakt zweimal erstellen (Race Condition bei Batch-Generierung)
_generation_lock = threading.Lock()

# ── Modell ────────────────────────────────────────────────────────────────────
# Über Railway-Variable ANTHROPIC_MODEL überschreibbar (z.B. claude-opus-4-6
# für maximale Qualität). Standard: Sonnet — schneller, günstiger, gleiche
# Qualität für kurze Fakten-Texte.
_CLAUDE_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")

# ── Hashtag-Pools ─────────────────────────────────────────────────────────────
# Core-Tags kommen bei jedem Video dazu.
# Topic-Tags: 3 themenspezifische pro Video (rotierend, kein fester Fingerabdruck).
# Reach-Tags: 2 zufällige aus dem allgemeinen Pool.

_HASHTAG_CORE = ["#fyp", "#tiktokdeutsch", "#fakten"]

_TOPIC_HASHTAGS: dict[str, list[str]] = {
    "science":     ["#wissenschaft", "#forscher", "#experiment", "#entdeckung",
                    "#biologie", "#chemie", "#physik", "#naturwissenschaft"],
    "history":     ["#geschichte", "#historisch", "#altertum", "#mittelalter",
                    "#archäologie", "#geschichtsfakten", "#antike"],
    "space":       ["#weltall", "#nasa", "#universum", "#planet", "#astronaut",
                    "#astronomie", "#kosmos", "#galaxie"],
    "technology":  ["#technologie", "#innovation", "#zukunft", "#ki", "#digital",
                    "#tech", "#künstlicheintelligenz", "#gadgets"],
    "animals":     ["#tiere", "#wildlife", "#tierreich", "#tierfakten",
                    "#tierliebe", "#natur", "#tierwelt"],
    "psychology":  ["#psychologie", "#mentalhealth", "#gehirn", "#gedanken",
                    "#bewusstsein", "#verhalten", "#psychofakten"],
    "food":        ["#essen", "#foodfacts", "#küche", "#kochen",
                    "#ernährung", "#food", "#lebensmittel"],
    "geography":   ["#geographie", "#welt", "#länder", "#reisen",
                    "#erdkunde", "#kontinente", "#länderfakten"],
    "human body":  ["#körper", "#gesundheit", "#medizin", "#anatomie",
                    "#biologie", "#körperfakten", "#wissenschaft"],
    "pop culture": ["#popkultur", "#trending", "#entertainment", "#kultur",
                    "#musik", "#film", "#serie"],
    "nature":      ["#natur", "#erde", "#umwelt", "#naturwunder",
                    "#naturkunde", "#ökologie", "#naturfakten"],
}

_HASHTAG_REACH = [
    "#viral", "#explore", "#foryou", "#foryoupage",
    "#deutsch", "#deutschtiktok", "#wissen", "#interessant",
    "#überraschend", "#krass", "#unglaublich", "#faktendestages",
    "#wissenswert", "#lernenmittiktok", "#didyouknow", "#funfact",
    "#lernen", "#wusstest", "#mustwatch",
]


def _get_base_hashtags(topic: str = "") -> list[str]:
    """
    Wählt pro Video: 3 Core-Tags + 3 themenspezifische + 2 zufällige Reach-Tags.
    Kein fester Fingerabdruck — rotiert innerhalb jedes Pools.
    """
    import random
    t = topic.lower().strip()

    # Topic-spezifische Tags (rotierend)
    topic_pool = _TOPIC_HASHTAGS.get(t, [])
    if not topic_pool:
        # Fallback: allgemeine Wissens-Tags
        topic_pool = ["#wissen", "#bildung", "#lernen", "#lernenmittiktok",
                      "#wissenswert", "#faktendestages"]
    random.shuffle(topic_pool)
    topic_tags = topic_pool[:3]

    # 2 zufällige Reach-Tags
    reach_pool = [r for r in _HASHTAG_REACH if r not in _HASHTAG_CORE and r not in topic_tags]
    random.shuffle(reach_pool)
    reach_tags = reach_pool[:2]

    return ["#fyp", "#fakten", "#wissen"] + topic_tags + reach_tags

# Output-Verzeichnis respektiert OUTPUT_DIR env-Variable (Railway Volume = /data/output)
_OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", str(Path(__file__).parent.parent / "output")))
HISTORY_FILE = _OUTPUT_DIR / "fact_history.json"


def _load_history() -> list[dict]:
    """
    Lädt alle bisherigen Fakten als Liste von {title, summary}.
    Liest aus der History-Datei UND aus allen hochgeladenen JSON-Metadaten.
    Nur Videos mit "uploaded": true werden als gesperrt betrachtet.
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

    # 2. Nur hochgeladene JSON-Metadaten (uploaded: true) — damit nur TikTok-Posts gesperrt sind
    for jf in _OUTPUT_DIR.glob("*.json"):
        if jf.name == "fact_history.json":
            continue
        try:
            d = json.loads(jf.read_text(encoding="utf-8"))
            if not d.get("uploaded", False):
                continue  # Nur hochgeladene Videos berücksichtigen
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
    Thread-sicher: nur eine Generierung gleichzeitig (verhindert doppelte Fakten).
    """
    with _generation_lock:
        return _generate_fact_locked(topic=topic, long=long)


def _generate_fact_locked(topic: str = "general", long: bool = False) -> dict:
    """Interne Implementierung — wird nur innerhalb des _generation_lock aufgerufen."""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"].strip())

    fact_length = (
        "Den Fakt in 7-9 spannenden Sätzen erklären (Deutsch). "
        "Erkläre den Hintergrund, gib Beispiele, nenne konkrete Zahlen und ende mit einem überraschenden Abschluss-Gedanken. "
        "WICHTIG: Mindestens 150 Wörter, maximal 165 Wörter. Zähle deine Wörter bevor du antwortest. Der letzte Satz muss vollständig sein. Zu kurze Antworten werden abgelehnt!"
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
  "hashtags": ["#popkultur1", "#popkultur2", "#popkultur3", "#popkultur4"],
  "visual_query": "2-3 englische Suchbegriffe für ein passendes Stockvideo (z.B. 'concert crowd lights' oder 'phone social media scroll'). Nur filmisch umsetzbare Motive — keine abstrakten Begriffe."
}}

Regeln:
- Alles auf Deutsch (außer visual_query)
- Fakt muss 100% wahr und verifizierbar sein
- Titel muss einen HOOK enthalten: entweder den Namen einer bekannten Person/Show + überraschende Zahl ODER eine kontraintuitive Aussage ("Dieser Netflix-Hit wurde fast gecancelt")
- Der Fakt muss mit dem überraschendsten/schockierendsten Satz beginnen (der Hook kommt zuerst!)
- Eröffne den Fakt mit einem Curiosity-Gap — was kaum jemand über dieses Thema weiß
- Nutze konkrete Zahlen: Streams, Follower, Einnahmen, Rekorde, Datum
- Struktur: "Die meisten Fans wissen nicht, dass X" oder "Bevor Y Weltstar wurde, Z"
- Beschreibung soll Neugier wecken ohne den Fakt zu verraten (Teaser-Stil)
- Hashtags: 4 themenspezifische Popkultur-Hashtags (z.B. #netflix #taylorswift)"""
    else:
        # Spezielle Kategorien mit provokanten Prompt-Anpassungen
        _provocative_topics = {
            "dark history":    "dunkle Geschichte (z.B. staatlich sanktionierte Verbrechen, vergessene Massaker, Experimente an Menschen, schockierende historische Praktiken)",
            "crime":           "wahre Verbrechen (spektakuläre Fälle, ungelöste Morde, serielle Täter, Justizirrtümer — echte Fakten, keine Fiktion)",
            "conspiracy truth":"echte Verschwörungen die sich als wahr erwiesen haben (z.B. MK-Ultra, Watergate, COINTELPRO, Tuskegee-Studie — nur verifizierte Fakten)",
            "money":           "Geld & Ungleichheit (schockierende Zahlen zu Reichtum, Armut, wie Konzerne Steuern umgehen, Superreiche-Fakten)",
            "war":             "Kriege & Militärgeschichte (schockierende oder kaum bekannte Fakten über Kriege, Waffen, Propaganda)",
            "medicine":        "Medizingeschichte (bizarre frühere Behandlungen, schockierende Fehler, Pharmakonzern-Skandale — nur belegte Fakten)",
            "survival":        "extreme Überlebensgeschichten (wahre Geschichten über unglaubliches Überleben unter unmöglichen Bedingungen)",
        }
        topic_desc = _provocative_topics.get(topic.lower(), topic)

        prompt = f"""Erstelle einen faszinierenden, provokanten Fakt für ein deutsches TikTok-Video (@syncin2).
Thema: {topic_desc}
{avoid_block}
Ziel: Der Fakt soll Zuschauer so schockieren, wütend machen oder ungläubig zurücklassen, dass sie KOMMENTIEREN müssen ("Das kann nicht sein!", "Krass!", "Wusste ich nicht!"). Meinungsteilende oder empörende Fakten gehen viral.

Gib NUR valides JSON zurück (kein Markdown, kein extra Text):
{{
  "title": "Kurzer, packender Titel (max 6 Wörter, Deutsch)",
  "fact": "{fact_length}",
  "description": "Eine kurze, provozierende TikTok-Beschreibung (1-2 Sätze, Deutsch, mit 1-2 passenden Emojis). Soll Empörung oder Ungläubigkeit auslösen. Max 100 Zeichen.",
  "hashtags": ["#themenspezifisch1", "#themenspezifisch2", "#themenspezifisch3", "#themenspezifisch4"],
  "visual_query": "2-3 englische Suchbegriffe für ein passendes Stockvideo (z.B. 'dark archive documents' oder 'courtroom drama gavel'). Nur filmisch umsetzbare Motive — keine abstrakten Begriffe."
}}

Regeln:
- Alles auf Deutsch (außer visual_query)
- Fakt muss 100% wahr und verifizierbar sein — keine Spekulationen
- Titel muss SOFORT schockieren: konkrete Zahl + schockierende Aussage, oder eine Aussage die Empörung auslöst
- Der Fakt MUSS mit dem schockierendsten Satz beginnen (Hook zuerst!)
- Wähle Fakten die eine emotionale Reaktion auslösen: Empörung, Ungläubigkeit, Schock, Fassungslosigkeit
- Nutze konkrete Zahlen, Namen, Jahreszahlen — nichts vages
- Bei Themen wie Verbrechen/Geschichte: zeige die menschliche Dimension (Opfer, Täter, Konsequenzen)
- Beschreibung soll provozieren ohne Clickbait zu sein — der Fakt rechtfertigt die Reaktion
- Hashtags: 4 themenspezifische (z.B. #kriminalfall #wahregeschichte für Crime-Themen)"""

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
            model=_CLAUDE_MODEL,
            max_tokens=2000 if long else 800,
            messages=[{"role": "user", "content": attempt_prompt}],
        )

        raw = message.content[0].text.strip()
        # Robustes JSON-Extraktieren — toleriert Markdown-Blöcke und Extra-Text
        if "```" in raw:
            import re as _re
            raw = _re.sub(r'```json\s*', '', raw)
            raw = _re.sub(r'```\s*', '', raw)
        # JSON-Objekt aus dem Text extrahieren (falls extra Text vorhanden)
        import re as _re
        match = _re.search(r'\{.*\}', raw, _re.DOTALL)
        if match:
            raw = match.group(0)
        try:
            data = json.loads(raw.strip())
        except json.JSONDecodeError as je:
            print(f"   ⚠️  Versuch {attempt}/{MAX_ATTEMPTS}: Ungültiges JSON — {je} — neuer Versuch…")
            data = None
            if attempt < MAX_ATTEMPTS:
                continue
            raise

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

    # Rotierende Base-Hashtags hinzufügen — themenspezifisch, keine Duplikate
    existing_tags = {h.lower() for h in data.get("hashtags", [])}
    for tag in _get_base_hashtags(topic):
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
