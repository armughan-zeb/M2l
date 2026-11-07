"""
Sound Design Library — SFX mapping, timing, and audio compositing.

Maps each template type to its default SFX cues with timing.
Provides FFmpeg audio filter chains for multi-track composition.
"""

from pathlib import Path
import os

ROOT = Path(os.environ.get("VIDZEO_RESOURCE_ROOT") or Path(__file__).resolve().parents[4]).resolve()
SFX_DIR = ROOT / "public" / "sound-effects" / "vidzeo"


# ═══════════════════════════════════════════════════════════════════
# SFX Catalog (curated mappings)
# ═══════════════════════════════════════════════════════════════════

SFX_CATALOG = {
    # Transitions
    "whoosh_fast":   "whoosh_snap.wav",
    "whoosh_soft":   "whoosh_soft.wav",
    "whoosh_dark":   "whoosh_dark.wav",
    "whoosh_air":    "whoosh_air.wav",
    "transition_suck": "transition_suck.wav",
    "impact_soft":   "impact_soft.wav",
    "impact_hard":   "impact.wav",
    "impact_metal":  "impact_metal.wav",
    "impact_glass":  "impact_glass.wav",
    "impact_wood":   "impact_wood.wav",

    # Documentary
    "paper_flip":    "paper_flip.wav",
    "paper_rustle":  "paper_rustle.wav",
    "paper_tear":    "paper_tear.wav",
    "typewriter":    "typewriter.wav",
    "camera_shutter":"camera_shutter.wav",
    "photo_slide":   "photo_slide.wav",
    "pencil_mark":   "pencil_mark.wav",
    "marker_scratch":"marker_scratch.wav",
    "stamp_hit":     "stamp_hit.wav",
    "news_sting":    "news_sting.wav",
    "telegraph":     "telegraph.wav",

    # Data
    "data_tick":     "data_tick.wav",
    "cash_counter":  "cash_counter.wav",
    "coin_tick":     "coin_tick.wav",
    "clock_tick":    "clock_tick.wav",
    "decision_tick": "decision_tick.wav",
    "technical_tick":"technical_tick.wav",
    "ui_tick":       "ui_tick.wav",
    "notification_up":"notification_up.wav",

    # Technology
    "keyboard":      "keyboard_single.wav",
    "mouse_click":   "mouse_click.wav",
    "sonar":         "sonar.wav",
    "signal_ping":   "signal_ping.wav",
    "radio_static":  "radio_static.wav",

    # Suspense
    "riser":         "riser.wav",
    "sub_boom":      "sub_boom.wav",
    "low_rumble":    "low_rumble.wav",
    "tension_string":"tension_string.wav",
    "heartbeat":     "heartbeat.wav",
    "suspense_pulse":"suspense_pulse.wav",
    "reverse_swell": "reverse_swell.wav",
    "reveal_chime":  "reveal_chime.wav",

    # Maps
    "map_pulse":     "map_pulse.wav",
    "map_draw":      "map_draw.wav",
    "route_ping":    "route_ping.wav",
    "lens_click":    "lens_click.wav",

    # Other
    "calendar_flip": "calendar_flip.wav",
    "ambient_room":  "room_tone.wav",
    "wind_room":     "wind_room.wav",
    "archive_drone": "archive_drone.wav",
    "vinyl_crackle": "vinyl_crackle.wav",
    "water_pulse":   "water_pulse.wav",
    "crowd_murmur":  "crowd_murmur.wav",
}


# ═══════════════════════════════════════════════════════════════════
# Default SFX per template type
# ═══════════════════════════════════════════════════════════════════

TEMPLATE_SFX = {
    "chapter_title": [
        {"key": "whoosh_dark", "start": 0.0, "volume": 0.3, "duration": 1.2},
        {"key": "sub_boom", "start": 0.1, "volume": 0.2, "duration": 1.5},
    ],
    "keyword_reveal": [
        {"key": "whoosh_fast", "start": 0.0, "volume": 0.35, "duration": 0.4},
        {"key": "impact_soft", "start": 0.15, "volume": 0.2, "duration": 0.3},
    ],
    "money_counter": [
        {"key": "cash_counter", "start": 0.0, "volume": 0.3, "duration": 2.0},
        {"key": "coin_tick", "start": 0.5, "volume": 0.15, "duration": 0.1},
        {"key": "coin_tick", "start": 1.0, "volume": 0.15, "duration": 0.1},
        {"key": "coin_tick", "start": 1.5, "volume": 0.15, "duration": 0.1},
    ],
    "stat_counter": [
        {"key": "data_tick", "start": 0.0, "volume": 0.25, "duration": 0.1},
        {"key": "data_tick", "start": 0.3, "volume": 0.2, "duration": 0.1},
        {"key": "data_tick", "start": 0.6, "volume": 0.2, "duration": 0.1},
        {"key": "impact_hard", "start": 0.9, "volume": 0.35, "duration": 0.4},
    ],
    "quote_card": [
        {"key": "paper_flip", "start": 0.0, "volume": 0.2, "duration": 0.5},
    ],
    "evidence_callout": [
        {"key": "stamp_hit", "start": 0.0, "volume": 0.35, "duration": 0.3},
        {"key": "paper_rustle", "start": 0.2, "volume": 0.15, "duration": 0.8},
        {"key": "pencil_mark", "start": 0.4, "volume": 0.12, "duration": 0.3},
    ],
    "date_reveal": [
        {"key": "calendar_flip", "start": 0.0, "volume": 0.3, "duration": 0.6},
    ],
    "redaction_reveal": [
        {"key": "marker_scratch", "start": 0.0, "volume": 0.3, "duration": 0.4},
        {"key": "tension_string", "start": 0.1, "volume": 0.15, "duration": 1.0},
    ],
    "lower_third": [
        {"key": "whoosh_soft", "start": 0.0, "volume": 0.3, "duration": 0.4},
    ],
    "end_card": [
        {"key": "reveal_chime", "start": 0.0, "volume": 0.25, "duration": 1.5},
        {"key": "riser", "start": 0.2, "volume": 0.12, "duration": 2.0},
    ],
    "source_citation": [
        {"key": "paper_flip", "start": 0.0, "volume": 0.2, "duration": 0.4},
    ],
    "countdown_clock": [
        {"key": "clock_tick", "start": 0.0, "volume": 0.2, "duration": 0.1},
        {"key": "clock_tick", "start": 0.5, "volume": 0.2, "duration": 0.1},
        {"key": "tension_string", "start": 0.0, "volume": 0.15, "duration": 1.5},
    ],
    "cold_open": [
        {"key": "sub_boom", "start": 0.0, "volume": 0.35, "duration": 1.5},
        {"key": "whoosh_dark", "start": 0.3, "volume": 0.25, "duration": 1.0},
    ],
}


# ═══════════════════════════════════════════════════════════════════
# Transition SFX
# ═══════════════════════════════════════════════════════════════════

TRANSITION_SFX = {
    "dissolve":     "whoosh_soft",
    "dip_black":    "sub_boom",
    "dip_white":    "reveal_chime",
    "wipe_right":   "whoosh_fast",
    "wipe_left":    "whoosh_fast",
    "slide_left":   "whoosh_soft",
    "slide_right":  "whoosh_soft",
    "flash_cut":    "impact_hard",
    "glitch_slice_h":"impact_glass",
    "pixelate":     "technical_tick",
    "zoom_burst":   "impact_hard",
    "light_leak":   "reveal_chime",
    "paper_tear":   "paper_tear",
    "circle_reveal":"riser",
    "radial_blur":  "whoosh_air",
}


# ═══════════════════════════════════════════════════════════════════
# Helper functions
# ═══════════════════════════════════════════════════════════════════

def sfx_path(key: str) -> str:
    """Get absolute path to SFX file."""
    if key not in SFX_CATALOG:
        return ""
    p = SFX_DIR / SFX_CATALOG[key]
    return str(p) if p.exists() else ""


def all_template_sfx(template_id: str) -> list:
    """Get default SFX cues for a template."""
    return TEMPLATE_SFX.get(template_id, [])


def transition_sfx(transition_id: str) -> str:
    """Get the SFX key for a transition."""
    return TRANSITION_SFX.get(transition_id, "whoosh_soft")


# ═══════════════════════════════════════════════════════════════════
# FFmpeg audio filter builders
# ═══════════════════════════════════════════════════════════════════

def adelay_filter(delay_ms: int) -> str:
    """Build an adelay filter for offsetting audio."""
    return f"adelay={delay_ms}|{delay_ms}"


def atrim_filter(start: float, end: float) -> str:
    """Trim audio to a specific segment."""
    return f"atrim=start={start}:end={end},asetpts=PTS-STARTPTS"


def volume_filter(volume: float) -> str:
    """Adjust audio volume."""
    return f"volume={volume}"


def amix_filter(num_inputs: int) -> str:
    """Mix multiple audio streams."""
    return f"amix=inputs={num_inputs}:duration=first:dropout_transition=0"


def build_sfx_audio_chain(
    cues: list,
    scene_start_s: float,
    input_index: int,
    total_duration: float,
) -> tuple:
    """Build FFmpeg audio filter chain for SFX cues in a scene.

    Returns (filter_chain, audio_inputs_to_add) where:
    - filter_chain: list of filter strings
    - audio_inputs_to_add: list of [-i, /path/to/sfx.wav] args
    """
    if not cues:
        return [], []

    filter_parts = []
    audio_inputs = []
    sfx_labels = []

    for i, cue in enumerate(cues):
        path = sfx_path(cue["key"])
        if not path:
            continue

        # Add audio input
        audio_inputs.extend(["-i", path])
        # Audio stream label uses next input index
        label = f"{input_index + i}:a"

        # Trim to cue duration (if specified)
        # Apply volume
        # Apply delay
        delay_ms = int((scene_start_s + cue.get("start", 0)) * 1000)
        vol = cue.get("volume", 0.2)
        dur = cue.get("duration", 1.0)

        cue_filter = (
            f"[{label}]"
            f"atrim=start=0:end={dur},"
            f"asetpts=PTS-STARTPTS,"
            f"volume={vol},"
            f"adelay={delay_ms}|{delay_ms},"
            f"apad=whole_dur={total_duration + 1}[sfx{i}]"
        )
        filter_parts.append(cue_filter)
        sfx_labels.append(f"[sfx{i}]")

    if not sfx_labels:
        return [], []

    # Mix all SFX together
    amix_input_count = len(sfx_labels)
    amix_input_str = "".join(sfx_labels)
    mix_filter = f"{amix_input_str}amix=inputs={amix_input_count}:duration=longest:dropout_transition=0[sfxmix]"
    filter_parts.append(mix_filter)

    return filter_parts, audio_inputs


def build_transition_sfx(
    transition_id: str,
    scene_start_s: float,
    duration: float = 0.5,
    volume: float = 0.25,
    input_index: int = 0,
) -> tuple:
    """Build audio for a transition between scenes."""
    sfx_key = transition_sfx(transition_id)
    path = sfx_path(sfx_key)
    if not path:
        return [], []

    delay_ms = int((scene_start_s - duration) * 1000)
    if delay_ms < 0:
        delay_ms = 0

    filter_str = (
        f"[{input_index}:a]"
        f"atrim=start=0:end={duration + 0.1},"
        f"asetpts=PTS-STARTPTS,"
        f"volume={volume},"
        f"adelay={delay_ms}|{delay_ms},"
        f"apad=whole_dur=999[transfx]"
    )
    return [filter_str], [["-i", path]]


# ═══════════════════════════════════════════════════════════════════
# Style-based SFX recommendation (LLM helper)
# ═══════════════════════════════════════════════════════════════════

STYLE_SFX_BUDGETS = {
    "serious": {
        "max_overlapping_sfx": 1,
        "preferred_sfx": ["sub_boom", "whoosh_dark", "impact_soft", "tension_string"],
        "music_volume": 0.12,
        "voice_volume": 1.0,
    },
    "energetic": {
        "max_overlapping_sfx": 2,
        "preferred_sfx": ["whoosh_fast", "impact_hard", "data_tick", "reveal_chime"],
        "music_volume": 0.2,
        "voice_volume": 1.0,
    },
    "calm": {
        "max_overlapping_sfx": 1,
        "preferred_sfx": ["whoosh_soft", "paper_rustle", "whoosh_air"],
        "music_volume": 0.18,
        "voice_volume": 1.0,
    },
    "suspense": {
        "max_overlapping_sfx": 2,
        "preferred_sfx": ["sub_boom", "tension_string", "heartbeat", "low_rumble", "reverse_swell"],
        "music_volume": 0.1,
        "voice_volume": 1.0,
    },
    "archive": {
        "max_overlapping_sfx": 1,
        "preferred_sfx": ["paper_flip", "paper_tear", "vinyl_crackle", "archive_drone"],
        "music_volume": 0.15,
        "voice_volume": 1.0,
    },
}
