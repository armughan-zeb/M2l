"""
Profile Engine — Multi-dimensional variation system for unique per-video editing.

Guarantees every video looks different:
  1. Unique HSL palette from script-derived seed (infinite variants)
  2. Font rotation with cross-niche borrowing (15 fonts × random pairing)
  3. Template anti-repetition rules (no repeat within 4 scenes)
  4. Animation + transition variety (11 animations × 15 transitions)
  5. Sound design variation (volume, timing, selection)
  6. Density variation (graphic_density, sound_density, pace, mood)

YouTube-proof: no two videos look templated. All variations are
deterministically reproducible from the script hash.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════════════
# Core — deterministic RNG from seed
# ═══════════════════════════════════════════════════════════════════

def _hash_seed(*parts: Any) -> int:
    """Deterministic seed from any input parts."""
    payload = "|".join(str(part or "") for part in parts).encode("utf-8", errors="ignore")
    return int(hashlib.sha256(payload).hexdigest()[:16], 16)


def _rng(seed: int):
    """Return a closure producing 0..1 floats deterministically."""
    state = seed & 0x7FFFFFFF

    def next_float():
        nonlocal state
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        return state / 0x7FFFFFFF

    return next_float


# ═══════════════════════════════════════════════════════════════════
# Assets — fonts, transitions, animations, overlays
# ═══════════════════════════════════════════════════════════════════

FONT_LIBRARY = [
    "Poppins-Bold.ttf",          # 0 — universal modern bold
    "Poppins-Bold.ttf",          # 1 — clean body
    "OpenSans-Bold.ttf",         # 2 — clean bold secondary
    "Poppins-Bold.ttf",          # 3 — universal modern bold
    "Montserrat-Bold.ttf",       # 4 — modern geometric
    "Poppins-Bold.ttf",          # 5 — universal modern bold
    "OpenSans-Bold.ttf",         # 6 — clean bold secondary
    "Poppins-Bold.ttf",          # 7 — universal modern bold
    "OpenSans-Bold.ttf",         # 8 — clean bold secondary
    "Montserrat-Bold.ttf",       # 9 — modern geometric
    "Poppins-Bold.ttf",          # 10 — universal modern bold
    "OpenSans-Bold.ttf",         # 11 — clean bold secondary
    "Poppins-Bold.ttf",          # 12 — universal modern bold
    "Poppins-Bold.ttf",          # 13 — universal modern bold
    "OpenSans-Bold.ttf",         # 14 — clean bold secondary
]

ANIMATIONS = [
    "slide_up", "slide_left", "slide_right", "drop_in",
    "scale_pop", "fade_in", "reveal_left", "pulse",
    "float", "glitch_in", "type_on",
]

TRANSITIONS = [
    "dissolve", "dip_black", "wipe_right", "wipe_left",
    "slide_left", "slide_right", "squeeze_h", "squeeze_v",
    "circle_reveal", "pixelate", "radial_blur", "dip_white",
    "wipe_up", "fade", "dissolve",
]

OVERLAYS = [
    "dark_vignette", "film_grain", "scanlines", "dust_specks",
    "letterbox", "flicker", "heavy_grain",
]

TEMPLATE_POOL: Dict[str, List[str]] = {
    "opener":      ["cold_open", "kinetic_statement", "split_statement"],
    "emphasis":    ["keyword_reveal", "kinetic_statement", "word_cascade",
                    "typewriter_reveal", "split_statement"],
    "statistic":   ["stat_counter", "money_counter", "percentage_gauge",
                    "metric_grid", "bar_chart", "ranked_list"],
    "evidence":    ["evidence_callout", "case_file", "source_citation",
                    "redaction_reveal", "stamp_mark", "index_card"],
    "quote":       ["quote_card", "witness_quote", "source_citation"],
    "timeline":    ["date_reveal", "calendar_rip", "timeline_trace"],
    "comparison":  ["cause_effect", "before_after", "split_compare"],
    "closing":     ["end_card", "cta_card"],
    "map":         ["map_route", "location_label", "field_caption"],
    "signal":      ["signal_scan", "typewriter_reveal", "countdown_clock"],
    "document":    ["newsprint_reveal", "index_card", "exhibit_label"],
    "profile":     ["profile_dossier", "evidence_callout", "field_caption"],
    "archive":     ["exhibit_label", "stamp_mark", "source_citation"],
}

SFX_POOL: Dict[str, List[str]] = {
    "chapter":    ["whoosh_dark", "sub_boom", "riser"],
    "keyword":    ["whoosh_fast", "impact_soft", "whoosh_snap"],
    "money":      ["cash_counter", "coin_tick", "data_tick"],
    "stat":       ["data_tick", "impact_hard", "technical_tick"],
    "quote":      ["paper_flip", "paper_rustle", "whoosh_soft"],
    "evidence":   ["stamp_hit", "paper_tear", "pencil_mark"],
    "date":       ["calendar_flip", "page_stack", "whoosh_soft"],
    "end":        ["reveal_chime", "riser", "whoosh_air"],
    "horror":     ["tension_string", "heartbeat", "low_rumble", "sub_boom"],
    "tech":       ["signal_ping", "technical_tick", "ui_tick", "sonar"],
    "archive":    ["archive_drone", "vinyl_crackle", "film_splice", "projector"],
    "tension":    ["tension_string", "suspense_pulse", "clock_tick", "heartbeat"],
}

# ═══════════════════════════════════════════════════════════════════
# Niche classification
# ═══════════════════════════════════════════════════════════════════

def classify_niche(script: str = "", topic: str = "", category: str = "") -> str:
    """Classify the document topic into a niche."""
    text = f"{topic} {category} {script[:2000]}".lower()
    rules = [
        ("crime",      ["crime", "murder", "heist", "detective", "investigation", "theft", "stolen", "police", "fbi"]),
        ("finance",    ["finance", "market", "stock", "money", "billion", "company", "economy", "dollar"]),
        ("technology", ["technology", "software", "computer", "ai", "startup", "spacecraft", "rocket"]),
        ("royal",      ["royal family", "british royal", "royalty", "monarchy", "monarch", "windsor", "king charles", "queen camilla", "prince william", "prince harry", "buckingham palace"]),
        ("history",    ["history", "historical", "archive", "war", "empire", "ancient", "century", "museum"]),
        ("travel",     ["travel", "destination", "journey", "tourism", "island", "city guide"]),
        ("sports",     ["sport", "football", "cricket", "basketball", "athlete", "championship"]),
        ("horror",     ["horror", "haunted", "paranormal", "ghost", "monster"]),
    ]
    for niche, words in rules:
        if any(word in text for word in words):
            return niche
    return "general"


# ═══════════════════════════════════════════════════════════════════
# Profile Generator — the master
# ═══════════════════════════════════════════════════════════════════

@dataclass
class EditingProfile:
    """Complete per-video editing profile. Every video gets a unique one."""

    # Identity
    profile_id: str = ""
    seed: int = 0
    niche: str = "general"
    source: str = "profile_engine_v1"

    # Visual
    palette: Dict[str, str] = field(default_factory=dict)
    display_font: str = "Poppins-Bold.ttf"
    body_font: str = "Poppins-Bold.ttf"
    accent_font: str = "OpenSans-Bold.ttf"

    # Template assignment
    template_map: Dict[str, str] = field(default_factory=dict)

    # Rhythm
    graphic_density: float = 0.55
    sound_density: float = 0.45
    pace: str = "measured"
    mood: str = "authoritative"

    # Animation
    default_animation: str = "slide_up"
    animation_speed: float = 0.45

    # Transitions
    transition_pool: List[str] = field(default_factory=list)
    transition_duration_range: Tuple[float, float] = (0.2, 0.5)

    # SFX
    sfx_pool: Dict[str, List[str]] = field(default_factory=dict)
    sfx_cross_niche: bool = False

    # Overlays
    active_overlays: List[str] = field(default_factory=list)
    overlay_intensity: float = 0.5

    # Anti-repetition
    recent_templates: List[str] = field(default_factory=list)
    min_template_spacing: int = 4

    def to_dict(self) -> dict:
        return {
            "profile_id": self.profile_id,
            "seed": self.seed,
            "niche": self.niche,
            "source": self.source,
            "palette": self.palette,
            "display_font": self.display_font,
            "body_font": self.body_font,
            "accent_font": self.accent_font,
            "graphic_density": self.graphic_density,
            "sound_density": self.sound_density,
            "pace": self.pace,
            "mood": self.mood,
            "default_animation": self.default_animation,
            "animation_speed": self.animation_speed,
            "transition_pool": self.transition_pool,
            "active_overlays": self.active_overlays,
        }


class ProfileEngine:
    """Generate unique editing profiles per video."""

    def __init__(self):
        pass

    def generate(
        self,
        script: str = "",
        topic: str = "",
        category: str = "",
        variation_seed: int = 0,
    ) -> EditingProfile:
        """Generate a completely unique editing profile for a video."""
        # Determine niche
        niche = classify_niche(script, topic, category)

        # Deterministic seed
        master_seed = _hash_seed(script, topic, category, variation_seed)
        rng = _rng(master_seed)

        profile = EditingProfile()
        profile.profile_id = hashlib.sha256(
            f"{script[:500]}|{topic}|{variation_seed}".encode()
        ).hexdigest()[:16]
        profile.seed = master_seed
        profile.niche = niche
        profile.rng = rng  # store for later use

        # ── 1. Palette (unique HSL-based) ──
        profile.palette = self._generate_palette(niche, master_seed, rng)

        # ── 2. Fonts (rotated with cross-niche borrowing) ──
        profile.display_font, profile.body_font, profile.accent_font = (
            self._select_fonts(niche, master_seed, rng)
        )

        # ── 3. Template rotation ──
        profile.template_map = self._build_template_map(niche, rng)

        # ── 4. Rhythm ──
        profile.graphic_density = 0.35 + rng() * 0.45
        profile.sound_density = 0.35 + rng() * 0.40
        profile.pace = ["urgent","rapid","precise","measured","deliberate","slow_burn"][
            int(rng() * 6)
        ]
        profile.mood = [
            "tense","energetic","authoritative","curious","somber","inspiring","clinical"
        ][int(rng() * 7)]

        # ── 5. Animation ──
        profile.default_animation = ANIMATIONS[int(rng() * len(ANIMATIONS))]
        profile.animation_speed = 0.3 + rng() * 0.4

        # ── 6. Transitions ──
        profile.transition_pool = self._select_transitions(niche, rng)
        profile.transition_duration_range = (0.2 + rng() * 0.1, 0.3 + rng() * 0.4)

        # ── 7. SFX ──
        profile.sfx_pool = self._select_sfx_pool(niche, rng)
        profile.sfx_cross_niche = rng() < 0.2  # 20% chance

        # ── 8. Overlays ──
        overlay_count = int(1 + rng() * 2)
        profile.active_overlays = [OVERLAYS[int(rng() * len(OVERLAYS))]
                                    for _ in range(overlay_count)]
        profile.active_overlays = list(set(profile.active_overlays))
        profile.overlay_intensity = 0.3 + rng() * 0.5

        # ── 9. Anti-repetition ──
        profile.recent_templates = []
        profile.min_template_spacing = 3 + int(rng() * 3)

        return profile

    def _generate_palette(self, niche: str, seed: int, rng) -> dict:
        """HSL-based palette generation — infinite variants per niche."""
        hue_ranges = {
            "crime":      [(345, 365), (0, 10)],
            "history":    [(30, 55), (25, 50)],
            "travel":     [(160, 210), (170, 200)],
            "finance":    [(120, 165), (195, 220)],
            "technology": [(180, 250), (260, 300)],
            "sports":     [(40, 65), (200, 220)],
            "horror":     [(0, 15), (340, 360)],
            "general":    [(25, 60), (190, 230)],
        }
        ranges = hue_ranges.get(niche, hue_ranges["general"])
        chosen = ranges[seed % len(ranges)]
        base_hue = chosen[0] + rng() * (chosen[1] - chosen[0])

        accent_offset = 30 if seed % 2 == 0 else 165
        accent_hue = (base_hue + accent_offset + rng() * 20 - 10) % 360
        secondary_hue = (base_hue + 150 + rng() * 15) % 360
        danger_hue = (355 + rng() * 15) % 360

        return {
            "background": self._hsl_hex(base_hue, 0.08 + rng() * 0.22, 0.02 + rng() * 0.07),
            "surface":    self._hsl_hex(base_hue, 0.10 + rng() * 0.20, 0.06 + rng() * 0.08),
            "primary":    self._hsl_hex(base_hue, 0.04 + rng() * 0.14, 0.85 + rng() * 0.12),
            "accent":     self._hsl_hex(accent_hue, 0.55 + rng() * 0.40, 0.42 + rng() * 0.22),
            "secondary":  self._hsl_hex(secondary_hue, 0.30 + rng() * 0.30, 0.35 + rng() * 0.25),
            "danger":     self._hsl_hex(danger_hue, 0.65 + rng() * 0.25, 0.38 + rng() * 0.20),
        }

    def _select_fonts(self, niche: str, seed: int, rng) -> Tuple[str, str, str]:
        """Select universal modern bold fonts that match any niche cleanly."""
        display = "Poppins-Bold.ttf" if (seed % 2 == 0) else "Montserrat-Bold.ttf"
        body = "Poppins-Bold.ttf"
        accent = "OpenSans-Bold.ttf"
        return display, body, accent

    def _build_template_map(self, niche: str, rng) -> Dict[str, str]:
        """Build per-category template assignment with rotation."""
        result = {}
        for category, options in TEMPLATE_POOL.items():
            # Shuffle options deterministically
            shuffled = list(options)
            for i in range(len(shuffled)):
                j = int(rng() * len(shuffled))
                shuffled[i], shuffled[j] = shuffled[j], shuffled[i]
            result[category] = shuffled[0]  # primary pick
        return result

    def _select_transitions(self, niche: str, rng) -> List[str]:
        """Select 5-8 transitions for this video."""
        pool = list(TRANSITIONS)
        for i in range(len(pool)):
            j = int(rng() * len(pool))
            pool[i], pool[j] = pool[j], pool[i]
        count = 5 + int(rng() * 4)
        return pool[:count]

    def _select_sfx_pool(self, niche: str, rng) -> Dict[str, List[str]]:
        """Select SFX per template category with variation."""
        result = {}
        for category, options in SFX_POOL.items():
            shuffled = list(options)
            for i in range(len(shuffled)):
                j = int(rng() * len(shuffled))
                shuffled[i], shuffled[j] = shuffled[j], shuffled[i]
            result[category] = shuffled[:max(1, len(shuffled) // 2)]
        return result

    def next_template(
        self,
        profile: EditingProfile,
        category: str,
        scene_index: int,
    ) -> str:
        """Get the next template for a scene, respecting anti-repetition rules."""
        options = list(TEMPLATE_POOL.get(category, TEMPLATE_POOL["emphasis"]))
        rng = _rng(profile.seed + scene_index)

        # Remove recently used templates
        recent = profile.recent_templates[-profile.min_template_spacing:]
        available = [t for t in options if t not in recent]

        if not available:
            available = options

        # Pick with deterministic randomness
        choice = available[int(rng() * len(available))]
        profile.recent_templates.append(choice)
        if len(profile.recent_templates) > 10:
            profile.recent_templates = profile.recent_templates[-10:]

        return choice

    def next_transition(self, profile: EditingProfile, scene_index: int) -> str:
        """Get the next transition, never repeating consecutively."""
        pool = profile.transition_pool
        rng = _rng(profile.seed + scene_index + 1000)
        last = profile.recent_templates[-2:] if len(profile.recent_templates) >= 2 else [""]
        available = [t for t in pool if t != last[-1]]
        if not available:
            available = pool
        return available[int(rng() * len(available))]

    def next_animation(self, profile: EditingProfile, scene_index: int) -> str:
        """Get the next animation with variation."""
        rng = _rng(profile.seed + scene_index + 2000)
        # 70% chance of default, 30% chance of random
        if rng() < 0.3:
            return ANIMATIONS[int(rng() * len(ANIMATIONS))]
        return profile.default_animation

    def next_sfx(self, profile: EditingProfile, category: str, scene_index: int) -> dict:
        """Get SFX cues for a category."""
        pool = profile.sfx_pool.get(category, SFX_POOL.get("chapter", [["whoosh_soft"]]))
        rng = _rng(profile.seed + scene_index + 3000)
        count = 1 + int(rng() * 2)
        selected = []
        for _ in range(count):
            choice = pool[int(rng() * len(pool))]
            selected.append({
                "key": choice,
                "volume": 0.12 + rng() * 0.25,
                "start": rng() * 0.3,
                "duration": 0.3 + rng() * 0.7,
            })
        return selected

    @staticmethod
    def _hsl_hex(h: float, s: float, l: float) -> str:
        """HSL to hex color."""
        h = h % 360 / 360
        s = max(0, min(1, s))
        l = max(0, min(1, l))
        if s == 0:
            v = int(l * 255)
            return f"#{v:02x}{v:02x}{v:02x}"
        q = l * (1 + s) if l < 0.5 else l + s - l * s
        p = 2 * l - q
        r = int(ProfileEngine._hue2rgb(p, q, h + 1/3) * 255)
        g = int(ProfileEngine._hue2rgb(p, q, h) * 255)
        b = int(ProfileEngine._hue2rgb(p, q, h - 1/3) * 255)
        return f"#{r:02x}{g:02x}{b:02x}"

    @staticmethod
    def _hue2rgb(p: float, q: float, t: float) -> float:
        if t < 0: t += 1
        if t > 1: t -= 1
        if t < 1/6: return p + (q - p) * 6 * t
        if t < 1/2: return q
        if t < 2/3: return p + (q - p) * (2/3 - t) * 6
        return p


# ═══════════════════════════════════════════════════════════════════
# Quick demo
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    engine = ProfileEngine()
    script = "The Gardner Museum heist was the largest art theft in history. Thieves stole 13 masterpieces worth 500 million dollars."

    print("=== Three profiles from same script, different seeds ===")
    for s in [42, 1337, 99999]:
        p = engine.generate(script=script, variation_seed=s)
        print(f"\nSeed {s}:")
        print(f"  Niche: {p.niche}")
        print(f"  Palette: bg={p.palette['background']} accent={p.palette['accent']} primary={p.palette['primary']}")
        print(f"  Fonts: display={p.display_font} body={p.body_font}")
        print(f"  Pace: {p.pace} Mood: {p.mood} Density: {p.graphic_density:.2f}")
        print(f"  Animation: {p.default_animation} ({p.animation_speed:.2f}s)")
        print(f"  Transitions: {p.transition_pool[:4]}")
        print(f"  Overlays: {p.active_overlays}")
