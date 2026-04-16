import asyncio
import base64
import os
import re

import edge_tts
from elevenlabs import ElevenLabs

DEFAULT_EDGE_VOICE    = "de-DE-SeraphinaMultilingualNeural"
ELEVENLABS_VOICE_ID   = "TX3LPaxmHKxFdv7VOQHJ"   # Liam – Energetic, Social Media Creator
ELEVENLABS_MODEL      = "eleven_multilingual_v2"


# ── ElevenLabs ────────────────────────────────────────────────────────────────

def _chars_to_words(alignment: dict) -> list[dict]:
    """
    Konvertiert ElevenLabs Zeichen-Timings zu Wort-Timings.
    alignment: {"characters": [...], "character_start_times_seconds": [...], "character_end_times_seconds": [...]}
    """
    chars      = alignment["characters"]
    starts     = alignment["character_start_times_seconds"]
    ends       = alignment["character_end_times_seconds"]

    word_timings = []
    current_word  = ""
    word_start    = None

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


def _tts_elevenlabs(text: str, audio_path: str, api_key: str) -> list[dict]:
    client   = ElevenLabs(api_key=api_key)
    response = client.text_to_speech.convert_with_timestamps(
        voice_id=ELEVENLABS_VOICE_ID,
        text=text,
        model_id=ELEVENLABS_MODEL,
        output_format="mp3_44100_192",
    )
    # Audio speichern
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

async def _tts_edge_async(text: str, audio_path: str, voice: str) -> list[dict]:
    communicate  = edge_tts.Communicate(text, voice, boundary="WordBoundary")
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

def text_to_speech(text: str, output_path: str) -> tuple[str, list[dict]]:
    """
    Erstellt Audio mit Word-Timings.
    Nutzt ElevenLabs wenn ELEVENLABS_API_KEY gesetzt, sonst Edge TTS.
    Gibt (audio_path, word_timings) zurück.
    """
    el_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if el_key:
        try:
            timings = _tts_elevenlabs(text, output_path, el_key)
            return output_path, timings
        except Exception as e:
            print(f"   ElevenLabs Fehler: {e} — nutze Edge TTS als Fallback")

    timings = asyncio.run(_tts_edge_async(text, output_path, DEFAULT_EDGE_VOICE))
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
