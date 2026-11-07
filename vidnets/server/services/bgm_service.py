# =============================================================================
# BGM SERVICE — Content-ID Safe Channel Soundtrack & Audio Ducking Engine
# =============================================================================
# Provides curated, zero-latency background music presets for the 7 production
# channels, genre-based fallback resolution, audio ducking, and timeline mixing.
# =============================================================================

import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

SERVICE_DIR = Path(__file__).resolve().parent
SERVER_DIR = SERVICE_DIR.parent
VIDNETS_ROOT = SERVER_DIR.parent
PUBLIC_MUSIC_DIR = VIDNETS_ROOT / "public" / "music"
RESOURCES_MUSIC_DIR = VIDNETS_ROOT / "resources" / "audio" / "music"

# ─── CHANNEL PERSONA MAPPINGS ───
CHANNEL_PERSONA_MAP = {
    # Channel ID or name / slug -> track slug
    "classic wheels": "classic-wheels",
    "classic-wheels": "classic-wheels",
    "ix_1": "classic-wheels",
    "nostalgic brit": "nostalgic-brit",
    "nostalgic-brit": "nostalgic-brit",
    "nostalgic britian": "nostalgic-brit",
    "ix_2": "nostalgic-brit",
    "royal celeb": "royal-celeb",
    "royal-celeb": "royal-celeb",
    "ix_3": "royal-celeb",
    "crown royal": "crown-royal",
    "crown-royal": "crown-royal",
    "ix_4": "crown-royal",
    "crown zone": "crown-zone",
    "crown-zone": "crown-zone",
    "ix_5": "crown-zone",
    "after death": "after-death",
    "after-death": "after-death",
    "ix_6": "after-death",
    "train crew": "train-crew",
    "train-crew": "train-crew",
    "ix_7": "train-crew",
}

# ─── NICHE / KEYWORD MAPPINGS ───
NICHE_GENRE_MAP = {
    "classic cars": "classic-wheels",
    "vintage cars": "classic-wheels",
    "automotive": "classic-wheels",
    "rally": "classic-wheels",
    "british history": "nostalgic-brit",
    "heritage": "nostalgic-brit",
    "nostalgia": "nostalgic-brit",
    "royal celebrity": "royal-celeb",
    "celebrity": "royal-celeb",
    "palace drama": "royal-celeb",
    "monarchy": "crown-royal",
    "royal history": "crown-royal",
    "royalty": "crown-royal",
    "royal commentary": "crown-zone",
    "modern royal": "crown-zone",
    "commentary": "crown-zone",
    "crime": "after-death",
    "true crime": "after-death",
    "mystery": "after-death",
    "paranormal": "after-death",
    "death": "after-death",
    "ghost": "after-death",
    "railways": "train-crew",
    "trains": "train-crew",
    "locomotives": "train-crew",
    "engineering": "train-crew",
    "machinery": "train-crew",
}

GENRE_TRACK_MAP = {
    "retro_groove": "classic-wheels",
    "retro": "classic-wheels",
    "automotive": "classic-wheels",
    "british_orchestral": "nostalgic-brit",
    "heritage": "nostalgic-brit",
    "investigative_tension": "royal-celeb",
    "investigative": "royal-celeb",
    "suspense": "after-death",
    "royal_orchestral": "crown-royal",
    "regal": "crown-royal",
    "modern_cinematic": "crown-zone",
    "cinematic": "royal-celeb",
    "ambient_dark": "after-death",
    "dark": "after-death",
    "ambient": "after-death",
    "industrial_acoustic": "train-crew",
    "industrial": "train-crew",
    "documentary": "crown-zone",
}

TRACK_METADATA = {
    "classic-wheels": {
        "title": "Vintage Horizon",
        "genre": "retro_groove",
        "bpm": 112,
        "mood": "Driving, analog warmth, steady groove",
        "channel": "Classic Wheels",
    },
    "nostalgic-brit": {
        "title": "Heritage Reverie",
        "genre": "british_orchestral",
        "bpm": 75,
        "mood": "Warm, pastoral, reflective British strings",
        "channel": "Nostalgic Brit",
    },
    "royal-celeb": {
        "title": "Palace Secrets",
        "genre": "investigative_tension",
        "bpm": 90,
        "mood": "Minimalist piano ostinato, cinematic suspense pulse",
        "channel": "Royal Celeb",
    },
    "crown-royal": {
        "title": "Sovereign March",
        "genre": "royal_orchestral",
        "bpm": 80,
        "mood": "Majestic, ceremonial royal strings & horns",
        "channel": "Crown Royal",
    },
    "crown-zone": {
        "title": "Crown Analysis",
        "genre": "modern_cinematic",
        "bpm": 96,
        "mood": "Contemporary documentary bed, sleek electronic pulse",
        "channel": "Crown Zone",
    },
    "after-death": {
        "title": "Beyond the Threshold",
        "genre": "ambient_dark",
        "bpm": 60,
        "mood": "Deep sub drone, eerie harmonic suspense",
        "channel": "After Death",
    },
    "train-crew": {
        "title": "Iron Rails",
        "genre": "industrial_acoustic",
        "bpm": 100,
        "mood": "Rhythmic locomotive cadence, mechanical acoustic drive",
        "channel": "Train Crew",
    },
}


def _find_track_file(slug: str) -> Optional[Path]:
    """Search for the track in public/music and resources/audio/music."""
    clean_slug = re.sub(r"[^a-z0-9_-]", "", slug.lower().strip())
    candidates = [
        PUBLIC_MUSIC_DIR / f"{clean_slug}.mp3",
        RESOURCES_MUSIC_DIR / f"{clean_slug}.mp3",
        PUBLIC_MUSIC_DIR / f"{clean_slug}.wav",
        RESOURCES_MUSIC_DIR / f"{clean_slug}.wav",
    ]
    for c in candidates:
        if c.is_file() and c.stat().st_size > 0:
            return c
    return None


def get_bgm_catalog() -> List[Dict[str, Any]]:
    """Return catalog of available built-in background music tracks."""
    catalog = []
    for slug, meta in TRACK_METADATA.items():
        file_path = _find_track_file(slug)
        catalog.append({
            "slug": slug,
            "title": meta.get("title", slug),
            "genre": meta.get("genre", "documentary"),
            "bpm": meta.get("bpm", 100),
            "mood": meta.get("mood", ""),
            "channel": meta.get("channel", ""),
            "available": file_path is not None,
            "file_path": str(file_path.resolve()) if file_path else "",
        })
    return catalog


def resolve_channel_music(
    channel_name: str = "",
    niche: str = "",
    genre: str = "",
    explicit_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Resolve the background music track for a production job.
    Precedence:
    1. Explicit valid file path
    2. Exact channel name / slug match in the 7 production channels
    3. Niche / category keyword match
    4. Genre preset match
    5. Default documentary theme fallback ('crown-zone' or 'documentary')
    """
    # 1. Explicit path
    if explicit_path:
        exp_p = Path(explicit_path)
        if exp_p.is_file() and exp_p.stat().st_size > 0:
            return {
                "path": str(exp_p.resolve()),
                "src": str(exp_p.resolve()),
                "slug": exp_p.stem,
                "title": exp_p.stem.replace("-", " ").title(),
                "genre": genre or "custom",
                "source": "explicit_path",
            }

    target_slug = None

    # 2. Match channel name or slug
    clean_ch = str(channel_name or "").lower().strip()
    if clean_ch in CHANNEL_PERSONA_MAP:
        target_slug = CHANNEL_PERSONA_MAP[clean_ch]
    else:
        # Partial match on channel name
        for key, slug in CHANNEL_PERSONA_MAP.items():
            if key in clean_ch:
                target_slug = slug
                break

    # 3. Match niche / category
    if not target_slug and niche:
        clean_niche = str(niche).lower().strip()
        for kw, slug in NICHE_GENRE_MAP.items():
            if kw in clean_niche:
                target_slug = slug
                break

    # 4. Match genre preset
    if not target_slug and genre:
        clean_genre = str(genre).lower().strip().replace(" ", "_")
        target_slug = GENRE_TRACK_MAP.get(clean_genre)

    # 5. Fallback: try documentary.mp3 or crown-zone (modern documentary)
    if not target_slug:
        target_slug = "documentary"

    file_path = _find_track_file(target_slug)
    if not file_path:
        # Fallback to any available track in public/music
        if PUBLIC_MUSIC_DIR.is_dir():
            mp3s = list(PUBLIC_MUSIC_DIR.glob("*.mp3"))
            if mp3s:
                file_path = mp3s[0]
                target_slug = file_path.stem

    if not file_path:
        return None

    meta = TRACK_METADATA.get(target_slug, {})
    return {
        "path": str(file_path.resolve()),
        "src": str(file_path.resolve()),
        "slug": target_slug,
        "title": meta.get("title", target_slug.replace("-", " ").title()),
        "genre": meta.get("genre", "documentary"),
        "channel": meta.get("channel", channel_name),
        "bpm": meta.get("bpm", 100),
        "source": "curated_catalog",
    }


def calculate_ducked_volume(
    current_time_sec: float,
    narration_intervals: List[Tuple[float, float]],
    base_volume: float = 0.20,
    ducked_ratio: float = 0.45,
    attack_sec: float = 0.35,
    release_sec: float = 0.50,
) -> float:
    """
    Calculate instantaneous ducked music volume at current_time_sec.
    - During narration, volume drops to base_volume * ducked_ratio.
    - Outside narration, volume returns smoothly to base_volume.
    """
    base_volume = max(0.0, min(1.0, float(base_volume)))
    ducked_vol = base_volume * ducked_ratio
    
    # Check distance to active speech
    is_speaking = False
    for start, end in narration_intervals:
        if start <= current_time_sec <= end:
            is_speaking = True
            break
        # Check transition windows
        if (start - attack_sec) <= current_time_sec < start:
            # Ramping down into speech
            progress = (current_time_sec - (start - attack_sec)) / max(0.01, attack_sec)
            return base_volume - progress * (base_volume - ducked_vol)
        if end < current_time_sec <= (end + release_sec):
            # Ramping up after speech
            progress = (current_time_sec - end) / max(0.01, release_sec)
            return ducked_vol + progress * (base_volume - ducked_vol)

    return ducked_vol if is_speaking else base_volume
