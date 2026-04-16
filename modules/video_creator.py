import os
import random
import textwrap
from pathlib import Path

import certifi
import numpy as np
import requests
from moviepy import (
    AudioFileClip,
    ColorClip,
    CompositeAudioClip,
    CompositeVideoClip,
    ImageClip,
    VideoClip,
    VideoFileClip,
    afx,
    concatenate_videoclips,
    vfx,
)
from PIL import Image, ImageDraw, ImageFont

WIDTH  = 1080
HEIGHT = 1920

def _resolve_font(mac_path: str, linux_candidates: list) -> str:
    """Gibt den korrekten Font-Pfad zurück — Mac oder Linux (Railway)."""
    if Path(mac_path).exists():
        return mac_path
    for candidate in linux_candidates:
        if Path(candidate).exists():
            return candidate
    # Letzter Ausweg: fc-list durchsuchen
    try:
        import subprocess
        out = subprocess.check_output(["fc-list", "--format=%{file}\n"], text=True, timeout=5)
        for line in out.splitlines():
            line = line.strip()
            if line and any(n in line for n in ["Liberation", "DejaVu", "Arial", "Helvetica"]):
                return line
    except Exception:
        pass
    return mac_path  # Fallback (wirft später einen klaren Fehler)

BOLD = _resolve_font(
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    [
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    ],
)
REGULAR = _resolve_font(
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    [
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    ],
)

_backgrounds_env = os.environ.get("BACKGROUNDS_DIR", "")
CACHE_DIR = Path(_backgrounds_env) if _backgrounds_env else Path(__file__).parent.parent / "assets" / "backgrounds"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

MUSIC_DIR = Path(__file__).parent.parent / "assets" / "music"
MUSIC_DIR.mkdir(parents=True, exist_ok=True)

# Mehrere Suchbegriffe pro Thema → mehr visuelle Vielfalt im Cache
TOPIC_QUERIES: dict[str, list[str]] = {
    "space":       ["galaxy nebula", "planet earth", "stars cosmos", "milky way", "aurora borealis"],
    "science":     ["laboratory experiment", "microscope cells", "chemistry science", "dna molecule", "physics"],
    "nature":      ["waterfall forest", "ocean waves", "mountains sunrise", "tropical jungle", "desert landscape"],
    "animals":     ["wildlife safari", "underwater fish", "birds flying", "wolves forest", "dolphins ocean"],
    "technology":  ["circuit board", "city neon lights", "data server", "robot technology", "drone aerial"],
    "psychology":  ["human mind", "meditation calm", "crowd people", "brain neurons", "emotion faces"],
    "history":     ["ancient ruins", "medieval castle", "old city", "museum art", "historical architecture"],
    "food":        ["cooking kitchen", "fresh vegetables", "street food", "restaurant meal", "baking bread"],
    "geography":   ["aerial city", "mountain aerial", "ocean aerial", "river landscape", "world map"],
    "human body":  ["heart pulse", "running athlete", "yoga stretching", "medical hospital", "fitness workout"],
    "pop culture": ["concert crowd", "social media phone", "gaming setup", "movie cinema", "festival lights"],
}

GRADIENTS = [
    ((10, 10, 35), (30, 20, 80)),
    ((5, 30, 60), (10, 80, 120)),
    ((20, 5, 40), (70, 15, 90)),
]


# ── Hintergrund ───────────────────────────────────────────────────────────────

def _gradient_bg(c1, c2) -> np.ndarray:
    img = Image.new("RGB", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(img)
    for y in range(HEIGHT):
        t = y / HEIGHT
        r = int(c1[0]*(1-t) + c2[0]*t)
        g = int(c1[1]*(1-t) + c2[1]*t)
        b = int(c1[2]*(1-t) + c2[2]*t)
        draw.line([(0, y), (WIDTH, y)], fill=(r, g, b))
    return np.array(img)


def _fetch_pexels_video(query: str, api_key: str, max_videos: int = 5) -> str | None:
    """
    Lädt bis zu max_videos verschiedene Videos für das Thema herunter
    und gibt zufällig eines zurück.
    """
    import random
    slug = query.replace(" ", "_")

    # Bereits gecachte Videos für dieses Thema finden
    cached = sorted(CACHE_DIR.glob(f"{slug}_*.mp4"))
    if len(cached) >= max_videos:
        chosen = random.choice(cached)
        return str(chosen)

    try:
        headers = {"Authorization": api_key}
        verify  = certifi.where()
        videos  = []
        for orientation in ["portrait", None]:
            params = {"query": query, "per_page": 20, "size": "large"}
            if orientation:
                params["orientation"] = orientation
            r = requests.get("https://api.pexels.com/videos/search",
                             headers=headers, params=params, timeout=15, verify=verify)
            videos = r.json().get("videos", [])
            if videos:
                break
        if not videos:
            return None

        # Zufällige Auswahl aus verfügbaren Videos (noch nicht gecachte bevorzugen)
        random.shuffle(videos)
        downloaded = []

        for video in videos:
            if len(downloaded) + len(cached) >= max_videos:
                break
            idx = len(cached) + len(downloaded) + 1
            cache_file = CACHE_DIR / f"{slug}_{idx:02d}.mp4"
            if cache_file.exists():
                continue

            files = sorted(video["video_files"], key=lambda f: f.get("width", 0), reverse=True)
            url   = next((f["link"] for f in files if f.get("width", 0) >= 1080), files[0]["link"])

            try:
                print(f"   Lade Hintergrundvideo {idx} herunter...")
                dl = requests.get(url,
                                  headers={"User-Agent": "Mozilla/5.0",
                                           "Referer": "https://www.pexels.com/"},
                                  verify=verify, timeout=60, stream=True)
                dl.raise_for_status()
                with open(str(cache_file), "wb") as f:
                    for chunk in dl.iter_content(1024 * 1024):
                        f.write(chunk)
                downloaded.append(cache_file)
            except Exception:
                continue

        all_videos = sorted(CACHE_DIR.glob(f"{slug}_*.mp4"))
        if not all_videos:
            return None
        return str(random.choice(all_videos))

    except Exception as e:
        print(f"   Pexels fehlgeschlagen: {e}")
        return None


PER_QUERY = 5   # Videos pro Sub-Query → 5 Sub-Queries × 5 = ~25 Videos/Thema


def _fetch_multiple_pexels_videos(query: str, api_key: str, count: int = 2) -> list[str]:
    """
    Gibt `count` zufällig ausgewählte Video-Pfade zurück.
    Nutzt themenspezifische Sub-Queries für mehr visuelle Vielfalt.
    Jeder Sub-Query bekommt einen eigenen Cache-Slug (keine Überschreibungen).
    """
    import random

    queries   = TOPIC_QUERIES.get(query.lower(), [query])
    all_paths: list[str] = []

    for sub_q in queries:
        slug   = sub_q.replace(" ", "_")
        cached = sorted(CACHE_DIR.glob(f"{slug}_*.mp4"))
        if len(cached) < PER_QUERY:
            _fetch_pexels_video(sub_q, api_key, max_videos=PER_QUERY)
            cached = sorted(CACHE_DIR.glob(f"{slug}_*.mp4"))
        all_paths.extend(str(p) for p in cached)

    # Alte einfache Caches (z.B. space_01.mp4) ebenfalls als Pool nutzen
    slug_old = query.replace(" ", "_")
    for p in sorted(CACHE_DIR.glob(f"{slug_old}_*.mp4")):
        if str(p) not in all_paths:
            all_paths.append(str(p))

    if not all_paths:
        return []

    random.shuffle(all_paths)
    return all_paths[:count]


def _make_background(video_path: str | None, duration: float, gradient_index: int):
    if video_path:
        try:
            clip = VideoFileClip(video_path)
            ratio = WIDTH / HEIGHT
            if clip.w / clip.h > ratio:
                nw = int(clip.h * ratio)
                clip = clip.cropped(x1=(clip.w-nw)//2, x2=(clip.w+nw)//2)
            else:
                nh = int(clip.w / ratio)
                clip = clip.cropped(y1=(clip.h-nh)//2, y2=(clip.h+nh)//2)
            clip = clip.resized((WIDTH, HEIGHT))
            if clip.duration < duration:
                clip = concatenate_videoclips([clip] * (int(duration / clip.duration) + 2))
            clip = clip.subclipped(0, duration)
            overlay = ColorClip((WIDTH, HEIGHT), color=(0,0,0)).with_opacity(0.48).with_duration(duration)
            return CompositeVideoClip([clip, overlay])
        except Exception as e:
            print(f"   Video-Fehler: {e}, nutze Farbverlauf")
    idx = gradient_index % len(GRADIENTS)
    return ImageClip(_gradient_bg(*GRADIENTS[idx])).with_duration(duration)


def _make_multi_background(video_paths: list[str], duration: float, gradient_index: int):
    """
    Teilt die Gesamtdauer gleichmäßig auf mehrere Videos auf.
    Jedes Segment bekommt ein anderes Hintergrundvideo.
    """
    if not video_paths:
        idx = gradient_index % len(GRADIENTS)
        return ImageClip(_gradient_bg(*GRADIENTS[idx])).with_duration(duration)

    n       = len(video_paths)
    seg_dur = duration / n
    segments = [_make_background(path, seg_dur, gradient_index) for path in video_paths]
    return concatenate_videoclips(segments)


# ── Header-Design ────────────────────────────────────────────────────────────

def _render_header(title: str) -> np.ndarray:
    """
    Rendert den Header:
    - 'WUSSTEST DU?' als grelles Badge mit Farbverlauf
    - Darunter der Titel in weiß/fett, automatisch auf Bildbreite angepasst
    """
    MAX_TITLE_W = WIDTH - 100   # 980px — Titel muss hier reinpassen
    font_badge  = ImageFont.truetype(BOLD, 54)
    badge_text  = "WUSSTEST DU?"

    # ── Schriftgröße automatisch verkleinern bis Titel auf eine Zeile passt ──
    font_size = 66
    MIN_SIZE  = 36
    font_title = ImageFont.truetype(BOLD, font_size)
    while font_size > MIN_SIZE and font_title.getlength(title) > MAX_TITLE_W:
        font_size -= 3
        font_title = ImageFont.truetype(BOLD, font_size)

    # ── Falls immer noch zu lang: auf 2 Zeilen umbrechen ─────────────────────
    def _wrap(text: str, font, max_w: int) -> list[str]:
        words, lines, cur = text.split(), [], ""
        for w in words:
            probe = (cur + " " + w).strip()
            if font.getlength(probe) <= max_w:
                cur = probe
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines or [text]

    if font_title.getlength(title) > MAX_TITLE_W:
        title_lines = _wrap(title, font_title, MAX_TITLE_W)
    else:
        title_lines = [title]

    # ── Maße berechnen ────────────────────────────────────────────────────────
    badge_w  = int(font_badge.getlength(badge_text)) + 60
    badge_h  = 84
    line_h   = font_size + 12
    title_h  = len(title_lines) * line_h + 4

    max_line_w = max(int(font_title.getlength(l)) for l in title_lines)
    total_w  = min(max(badge_w, max_line_w + 60, 700), WIDTH - 20)
    total_h  = badge_h + 20 + title_h

    img  = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # ── Gradient-Badge (orange → gelb) ───────────────────────────────────────
    badge_img = Image.new("RGBA", (badge_w, badge_h), (0, 0, 0, 0))
    b_draw    = ImageDraw.Draw(badge_img)
    for x in range(badge_w):
        t = x / badge_w
        b_draw.line([(x, 0), (x, badge_h)], fill=(255, int(100 + 115 * t), 0, 255))
    mask = Image.new("L", (badge_w, badge_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([(0, 0), (badge_w - 1, badge_h - 1)], radius=18, fill=255)
    badge_img.putalpha(mask)
    bx = (total_w - badge_w) // 2
    img.paste(badge_img, (bx, 0), badge_img)

    # Badge-Text
    tx = (total_w - int(font_badge.getlength(badge_text))) // 2
    draw.text((tx + 2, 16 + 2), badge_text, font=font_badge, fill=(0, 0, 0, 120))
    draw.text((tx, 16),          badge_text, font=font_badge, fill=(20, 20, 20, 255))

    # ── Titel-Zeilen (weiß mit Schatten) ─────────────────────────────────────
    ty = badge_h + 20
    for line in title_lines:
        tx2 = (total_w - int(font_title.getlength(line))) // 2
        draw.text((tx2 + 2, ty + 2), line, font=font_title, fill=(0, 0, 0, 180))
        draw.text((tx2, ty),          line, font=font_title, fill=(255, 255, 255, 255))
        ty += line_h

    return np.array(img)


# ── PIL-basiertes Karaoke-Rendering ──────────────────────────────────────────

def _render_karaoke_frame(
    words: list[str],
    highlight_indices: set[int],
    font_size: int = 96,
    max_width: int = 940,
) -> np.ndarray:
    """
    Rendert eine Zeile Wörter. Hervorgehobene Wörter erscheinen in Gelb,
    andere in Weiß. Gibt ein RGBA numpy-Array zurück.
    """
    font_bold   = ImageFont.truetype(BOLD, font_size)
    space_w     = font_bold.getlength(" ")

    # Wörter umbrechen
    lines:   list[list[tuple[int, str]]] = []   # [(word_idx, word), ...]
    cur_line: list[tuple[int, str]]      = []
    cur_w = 0.0

    for idx, word in enumerate(words):
        w = font_bold.getlength(word)
        if cur_line and cur_w + space_w + w > max_width:
            lines.append(cur_line)
            cur_line = [(idx, word)]
            cur_w = w
        else:
            cur_line.append((idx, word))
            cur_w += (space_w if cur_line else 0) + w
    if cur_line:
        lines.append(cur_line)

    line_h   = font_size + 16
    total_h  = len(lines) * line_h
    total_w  = max_width + 80
    pad      = 28

    # Transparentes Bild
    img  = Image.new("RGBA", (total_w, total_h + pad * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Halbtransparenter Hintergrund
    draw.rounded_rectangle(
        [(0, 0), (total_w - 1, total_h + pad * 2 - 1)],
        radius=24, fill=(0, 0, 0, 165)
    )

    for li, line_words in enumerate(lines):
        # Zeile zentrieren
        line_text_w = sum(font_bold.getlength(w) for _, w in line_words) + space_w * (len(line_words) - 1)
        x = (total_w - line_text_w) / 2
        y = pad + li * line_h

        for idx, word in line_words:
            color = "#FFE600" if idx in highlight_indices else "white"
            # Schatten
            draw.text((x + 2, y + 2), word, font=font_bold, fill=(0, 0, 0, 200))
            draw.text((x, y), word, font=font_bold, fill=color)
            x += font_bold.getlength(word) + space_w

    return np.array(img)


def _make_karaoke_clips(
    word_timings: list[dict],
    total_duration: float,
    group_size: int = 4,
) -> list:
    """
    Teilt word_timings in Gruppen und erstellt für jedes Wort einen Frame
    wo genau dieses Wort gelb hervorgehoben ist.
    """
    clips = []
    n = len(word_timings)

    for i, wt in enumerate(word_timings):
        # Gruppe bestimmen
        group_start_idx = (i // group_size) * group_size
        group_end_idx   = min(group_start_idx + group_size, n)
        group           = word_timings[group_start_idx:group_end_idx]
        group_words     = [w["word"] for w in group]
        highlight_idx   = i - group_start_idx   # Index innerhalb der Gruppe

        # Timing dieses Worts
        t_start = wt["start"]
        t_end   = word_timings[i + 1]["start"] if i + 1 < n else min(wt["end"] + 0.3, total_duration)
        t_end   = min(t_end, total_duration)

        if t_end <= t_start:
            continue

        frame  = _render_karaoke_frame(group_words, {highlight_idx})
        clip_h = frame.shape[0]
        clip_w = frame.shape[1]

        img_clip = (
            ImageClip(frame)
            .with_start(t_start)
            .with_end(t_end)
            .with_position(((WIDTH - clip_w) // 2, int(HEIGHT * 0.62) - clip_h // 2))
        )
        clips.append(img_clip)

    return clips


# ── Branding & UI-Overlays ────────────────────────────────────────────────────

def _render_watermark() -> np.ndarray:
    """Rendert '@syncin2' als kleines, halbtransparentes Badge."""
    text = "@syncin2"
    font = ImageFont.truetype(BOLD, 26)
    tw   = int(font.getlength(text)) + 22
    th   = 40
    img  = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([(0, 0), (tw - 1, th - 1)], radius=8, fill=(0, 0, 0, 110))
    # Schatten
    draw.text((11, 9), text, font=font, fill=(0, 0, 0, 130))
    draw.text((10, 8), text, font=font, fill=(255, 255, 255, 185))
    return np.array(img)


def _make_progress_bar(total_dur: float):
    """
    Dünne orange Fortschrittsleiste die von links nach rechts wächst.
    Erhöht die Watch-Time — Zuschauer sehen wie viel noch kommt.
    """
    BAR_H = 5
    color = np.array([255, 107, 53], dtype=np.uint8)   # #ff6b35

    def make_frame(t: float) -> np.ndarray:
        progress = min(t / max(total_dur, 0.001), 1.0)
        bar_w    = max(1, int(WIDTH * progress))
        frame    = np.zeros((BAR_H, WIDTH, 3), dtype=np.uint8)
        frame[:, :bar_w] = color
        return frame

    return VideoClip(make_frame, duration=total_dur).with_position((0, HEIGHT - BAR_H - 2))


# ── Hintergrundmusik ──────────────────────────────────────────────────────────

def _mix_background_music(speech: AudioFileClip, duration: float) -> AudioFileClip:
    """
    Mischt leise Hintergrundmusik unter das Voiceover.
    Wählt zufällig einen Track aus assets/music/. Fällt zurück auf reines
    Voiceover wenn der Ordner leer ist oder ein Fehler auftritt.
    Volume: 12% — deutlich hörbar aber nicht ablenken.
    """
    tracks = (
        list(MUSIC_DIR.glob("*.mp3"))
        + list(MUSIC_DIR.glob("*.wav"))
        + list(MUSIC_DIR.glob("*.m4a"))
        + list(MUSIC_DIR.glob("*.ogg"))
    )
    if not tracks:
        return speech

    try:
        track_path = random.choice(tracks)
        print(f"   Musik: {track_path.name}")
        music = AudioFileClip(str(track_path))

        # Loop bis Video-Länge erreicht, dann auf exakte Dauer kürzen
        music = music.with_effects([afx.AudioLoop(duration=duration)])

        # Lautstärke auf 12% senken + Fade in/out
        music = music.with_effects([
            afx.MultiplyVolume(0.12),
            afx.AudioFadeIn(1.0),
            afx.AudioFadeOut(1.5),
        ])

        return CompositeAudioClip([speech, music])

    except Exception as e:
        print(f"   Musik-Fehler (übersprungen): {e}")
        return speech


# ── Haupt-Funktion ────────────────────────────────────────────────────────────

def create_video(
    title: str,
    fact: str,
    audio_path: str,
    output_path: str,
    word_timings: list[dict] | None = None,
    sentence_timings: list[tuple] | None = None,
    gradient_index: int = 0,
    topic: str = "nature",
    visual_query: str = "",
) -> str:
    pexels_key   = os.environ.get("PEXELS_API_KEY", "")
    audio        = AudioFileClip(audio_path)
    total_dur    = audio.duration + 0.5

    # Hintergrund: spezifische Query vom Fakt bevorzugen, Thema als Fallback
    bg_query    = visual_query if visual_query else topic
    video_paths = _fetch_multiple_pexels_videos(bg_query, pexels_key, count=3) if pexels_key else []
    # Falls visual_query nichts liefert: Topic als Fallback
    if not video_paths and visual_query:
        video_paths = _fetch_multiple_pexels_videos(topic, pexels_key, count=3) if pexels_key else []
    n_bg = len(video_paths)
    if n_bg > 1:
        print(f"   Nutze {n_bg} verschiedene Hintergrundvideos")
    background = _make_multi_background(video_paths, total_dur, gradient_index)
    clips      = [background]

    # Header Badge — Fade in über 0.4 s
    header_img = _render_header(title)
    header_h   = header_img.shape[0]
    clips.append(
        ImageClip(header_img)
        .with_duration(total_dur)
        .with_position(("center", 80))
        .with_effects([vfx.FadeIn(0.4)])
    )

    # Karaoke-Text (nur Fakt-Wörter)
    if word_timings:
        # Nur Wörter des Fakts benutzen (Titel-Wörter überspringen)
        title_word_count = len(title.split())
        fact_timings = word_timings[title_word_count + 1:]  # +1 für den Punkt nach Titel
        if fact_timings:
            clips.extend(_make_karaoke_clips(fact_timings, total_dur, group_size=4))

    # @syncin2 Wasserzeichen (unten rechts)
    wm_img = _render_watermark()
    wm_h, wm_w = wm_img.shape[:2]
    clips.append(
        ImageClip(wm_img)
        .with_duration(total_dur)
        .with_position((WIDTH - wm_w - 24, HEIGHT - wm_h - 110))
        .with_effects([vfx.FadeIn(0.6)])
    )

    # Fortschrittsleiste (unten, wächst von links nach rechts)
    clips.append(_make_progress_bar(total_dur))


    # Hintergrundmusik unter Voiceover mischen
    mixed_audio = _mix_background_music(audio, total_dur)

    # Rendern
    video = CompositeVideoClip(clips, size=(WIDTH, HEIGHT)).with_audio(mixed_audio)
    video.write_videofile(
        output_path, fps=30, codec="libx264", audio_codec="aac", logger=None,
        ffmpeg_params=[
            "-preset", "veryfast",   # Schnell kodieren — TikTok komprimiert sowieso nach
            "-crf", "18",            # Qualität: 0=perfekt, 23=Standard → 18=hohe Qualität
            "-profile:v", "high",    # H.264 High Profile
            "-level", "4.0",         # Kompatibel mit 1080p30
            "-pix_fmt", "yuv420p",   # TikTok-Anforderung
            "-af", "loudnorm=I=-14:TP=-2:LRA=11",  # Lautstärke auf TikTok-Standard normalisieren
            "-b:a", "192k",          # Audio 192kbps
            "-threads", "0",
        ],
    )
    audio.close()
    if mixed_audio is not audio:
        try:
            mixed_audio.close()
        except Exception:
            pass
    video.close()
    return output_path
