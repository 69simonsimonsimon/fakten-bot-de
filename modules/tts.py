import asyncio
import os
import re

import edge_tts
import requests as _requests

# ElevenLabs: Domi — energetisch, gut für Fakten-Videos
_EL_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "AZnzlk1XvdvUeBnXmlld")
_EL_MODEL    = "eleven_multilingual_v2"

OPENAI_VOICE = "onyx"
OPENAI_MODEL = "tts-1"

_CREATOR_TOPICS = {
    "pop culture", "popkultur", "pop-kultur",
    "animals", "tiere",
    "food", "essen",
    "psychology", "psychologie",
}


# ── ElevenLabs TTS ────────────────────────────────────────────────────────────

def _tts_elevenlabs(text: str, audio_path: str, api_key: str) -> list[dict]:
    """ElevenLabs TTS → Whisper für Word-Timings."""
    url  = f"https://api.elevenlabs.io/v1/text-to-speech/{_EL_VOICE_ID}"
    resp = _requests.post(
        url,
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
        json={
            "text":     text,
            "model_id": _EL_MODEL,
            "voice_settings": {
                "stability":         0.45,
                "similarity_boost":  0.80,
                "style":             0.30,
                "use_speaker_boost": True,
            },
        },
        timeout=120,
    )
    resp.raise_for_status()
    with open(audio_path, "wb") as f:
        f.write(resp.content)

    # Whisper für Word-Timings
    from openai import OpenAI
    wc = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    with open(audio_path, "rb") as f:
        tr = wc.audio.transcriptions.create(
            model="whisper-1", file=f,
            response_format="verbose_json",
            timestamp_granularities=["word"],
        )
    return [{"word": w.word.strip(), "start": w.start, "end": w.end} for w in (tr.words or [])]


# ── OpenAI TTS + Whisper word timings ────────────────────────────────────────

def _tts_openai(text: str, audio_path: str, api_key: str) -> list[dict]:
    from openai import OpenAI
    client = OpenAI(api_key=api_key)

    response = client.audio.speech.create(
        model=OPENAI_MODEL,
        voice=OPENAI_VOICE,
        input=text,
    )
    with open(audio_path, "wb") as f:
        f.write(response.content)

    with open(audio_path, "rb") as f:
        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="verbose_json",
            timestamp_granularities=["word"],
        )

    return [{"word": w.word.strip(), "start": w.start, "end": w.end} for w in (transcript.words or [])]


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
    ElevenLabs (primär) → OpenAI TTS → Edge TTS Fallback.
    Gibt (audio_path, word_timings) zurück.
    """
    el_key     = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()

    if el_key and openai_key:
        try:
            print(f"   ElevenLabs TTS [{_EL_VOICE_ID}] ...")
            timings = _tts_elevenlabs(text, output_path, el_key)
            return output_path, timings
        except Exception as e:
            print(f"   ElevenLabs Fehler: {e} — OpenAI Fallback")

    if openai_key:
        try:
            print(f"   OpenAI TTS [{OPENAI_VOICE}] ...")
            timings = _tts_openai(text, output_path, openai_key)
            return output_path, timings
        except Exception as e:
            print(f"   OpenAI TTS Fehler: {e} — Edge TTS Fallback")

    # Edge TTS Fallback — Stimme je nach Thema
    t = (topic or "").lower().strip()
    voice_name = "de-DE-SeraphinaMultilingualNeural" if t in _CREATOR_TOPICS else "de-DE-FlorianMultilingualNeural"
    print(f"   Edge TTS Fallback: {voice_name}")
    timings = asyncio.run(_tts_edge_async(text, output_path, voice_name))
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
