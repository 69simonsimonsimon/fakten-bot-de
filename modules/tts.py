import asyncio
import base64
import os
import re

import edge_tts
from elevenlabs import ElevenLabs

# ── Zwei Stimmen je nach Thema ────────────────────────────────────────────────
#
#  STIMME 1 – NARRATOR  (Wissenschaft, Geschichte, Weltall, Technik …)
#    Ruhig, autoritär, Doku-Stil
#    ElevenLabs: Marcus  (lNDVWnlRYtLKcBKNFtRM)
#    Edge TTS:   de-DE-FlorianMultilingualNeural
#
#  STIMME 2 – CREATOR   (Pop-Kultur, Tiere, Essen, Psychologie …)
#    Energetisch, jung, Social-Media-Stil
#    ElevenLabs: Liam    (TX3LPaxmHKxFdv7VOQHJ)
#    Edge TTS:   de-DE-SeraphinaMultilingualNeural
#
# ─────────────────────────────────────────────────────────────────────────────

_VOICE_NARRATOR = {
    "elevenlabs_id": os.environ.get("ELEVENLABS_VOICE_NARRATOR", "lNDVWnlRYtLKcBKNFtRM"),  # Marcus
    "edge_tts":      "de-DE-FlorianMultilingualNeural",
    "label":         "Narrator",
}

_VOICE_CREATOR = {
    "elevenlabs_id": os.environ.get("ELEVENLABS_VOICE_CREATOR", "TX3LPaxmHKxFdv7VOQHJ"),   # Liam
    "edge_tts":      "de-DE-SeraphinaMultilingualNeural",
    "label":         "Creator",
}

# Themen → Creator-Stimme (alle anderen → Narrator)
_CREATOR_TOPICS = {
    "pop culture", "popkultur", "pop-kultur",
    "animals", "tiere",
    "food", "essen",
    "psychology", "psychologie",
}

ELEVENLABS_MODEL = "eleven_multilingual_v2"


def _pick_voice(topic: str) -> dict:
    """Wählt Stimme basierend auf Thema."""
    t = (topic or "").lower().strip()
    return _VOICE_CREATOR if t in _CREATOR_TOPICS else _VOICE_NARRATOR


# ── ElevenLabs ────────────────────────────────────────────────────────────────

def _chars_to_words(alignment: dict) -> list[dict]:
    chars  = alignment["characters"]
    starts = alignment["character_start_times_seconds"]
    ends   = alignment["character_end_times_seconds"]

    word_timings = []
    current_word = ""
    word_start   = None

    for char, start, end in zip(chars, starts, ends):
        if char in (" ", "\n", "\t"):
            if current_word:
                word_timings.append({"word": current_word, "start": word_start, "end": end})
                current_word = ""
                word_start   = None
        else:
            if not current_word:
                word_start = start
            current_word += char

    if current_word:
        word_timings.append({"word": current_word, "start": word_start, "end": ends[-1]})

    return word_timings


def _tts_elevenlabs(text: str, audio_path: str, api_key: str, voice: dict) -> list[dict]:
    client   = ElevenLabs(api_key=api_key)
    response = client.text_to_speech.convert_with_timestamps(
        voice_id=voice["elevenlabs_id"],
        text=text,
        model_id=ELEVENLABS_MODEL,
        output_format="mp3_44100_192",
    )
    audio_bytes = base64.b64decode(response.audio_base_64)
    with open(audio_path, "wb") as f:
        f.write(audio_bytes)

    alignment = response.alignment
    return _chars_to_words({
        "characters":                    alignment.characters,
        "character_start_times_seconds": alignment.character_start_times_seconds,
        "character_end_times_seconds":   alignment.character_end_times_seconds,
    })


# ── Edge TTS Fallback ─────────────────────────────────────────────────────────

async def _tts_edge_async(text: str, audio_path: str, voice_name: str) -> list[dict]:
    communicate  = edge_tts.Communicate(text, voice_name, boundary="WordBoundary")
    word_timings = []
    with open(audio_path, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 1e7
                dur   = chunk["duration"] / 1e7
                word_timings.append({"word": chunk["text"], "start": start, "end": start + dur})
    return word_timings


# ── Öffentliche API ───────────────────────────────────────────────────────────

def text_to_speech(text: str, output_path: str, topic: str = "") -> tuple[str, list[dict]]:
    """
    Erstellt Audio mit Word-Timings.
    Wählt Stimme je nach Thema:
      - Wissenschaft/Geschichte/Weltall/… → Narrator (ruhig, autoritär)
      - Pop-Kultur/Tiere/Essen/Psychologie → Creator (energetisch, jung)
    Nutzt ElevenLabs wenn ELEVENLABS_API_KEY gesetzt, sonst Edge TTS.
    """
    voice  = _pick_voice(topic)
    el_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()

    if el_key:
        try:
            print(f"   ElevenLabs TTS [{voice['label']}]: {voice['elevenlabs_id']}")
            timings = _tts_elevenlabs(text, output_path, el_key, voice)
            return output_path, timings
        except Exception as e:
            print(f"   ElevenLabs Fehler: {e} — nutze Edge TTS als Fallback")

    print(f"   Edge TTS [{voice['label']}]: {voice['edge_tts']}")
    timings = asyncio.run(_tts_edge_async(text, output_path, voice["edge_tts"]))
    return output_path, timings


def get_sentence_timings(fact_text: str, word_timings: list[dict]) -> list[tuple]:
    sentences = re.split(r'(?<=[.!?])\s+', fact_text.strip())
    if not word_timings:
        return [(s, i * 3.0, (i + 1) * 3.0) for i, s in enumerate(sentences)]
    result, word_idx = [], 0
    for sentence in sentences:
        n = len(re.findall(r'\w+', sentence))
        si = min(word_idx, len(word_timings) - 1)
        ei = min(word_idx + n - 1, len(word_timings) - 1)
        result.append((sentence, max(0, word_timings[si]["start"] - 0.1), word_timings[ei]["end"] + 0.2))
        word_idx += n
    return result
