"""Sound Design Registry & Component Library for the Programmatic Documentary Editor.

Defines allowable, closed-vocabulary SFX IDs mapped to local audio files in
public/sound-effects/. Attaches precise audio stings, whooshes, paper rustles,
and map pings based on documentary semantic triggers.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .contract import SoundCue, VisualSceneType


ROOT = Path(__file__).resolve().parents[3]
SFX_DIR = ROOT / "public" / "sound-effects"

# Closed vocabulary of allowable SFX tokens
SFX_REGISTRY: Dict[str, Dict[str, str]] = {
    # Transitions / movements
    "whoosh.light": {"file": "edit/edit_whoosh_light.wav", "cat": "whoosh", "vol": 0.22},
    "whoosh.deep": {"file": "edit/edit_whoosh_deep.wav", "cat": "whoosh", "vol": 0.28},
    "whoosh.air": {"file": "edit/edit_whoosh_air.wav", "cat": "whoosh", "vol": 0.20},
    "whoosh.soft": {"file": "vidzeo/whoosh_soft.wav", "cat": "whoosh", "vol": 0.24},
    "whoosh.snap": {"file": "vidzeo/whoosh_snap.wav", "cat": "whoosh", "vol": 0.26},
    "transition.suck": {"file": "vidzeo/transition_suck.wav", "cat": "transition", "vol": 0.25},
    # Paper / archive / documents
    "paper.shuffle": {"file": "edit/edit_paper_shuffle.wav", "cat": "paper", "vol": 0.32},
    "paper.tear": {"file": "vidzeo/paper_tear.wav", "cat": "paper", "vol": 0.30},
    "paper.flip": {"file": "vidzeo/paper_flip.wav", "cat": "paper", "vol": 0.32},
    "paper.rustle": {"file": "vidzeo/paper_rustle.wav", "cat": "paper", "vol": 0.34},
    "marker.scratch": {"file": "vidzeo/marker_scratch.wav", "cat": "paper", "vol": 0.28},
    "stamp.hit": {"file": "vidzeo/stamp_hit.wav", "cat": "stamp", "vol": 0.38},
    "page.stack": {"file": "vidzeo/page_stack.wav", "cat": "paper", "vol": 0.30},
    # Impact / stings / drama
    "impact.soft": {"file": "vidzeo/impact_soft.wav", "cat": "impact", "vol": 0.35},
    "impact.heavy": {"file": "vidzeo/impact.wav", "cat": "impact", "vol": 0.40},
    "hit.dry": {"file": "edit/edit_hit_dry.wav", "cat": "hit", "vol": 0.32},
    "sting.minor": {"file": "edit/edit_sting_minor.wav", "cat": "sting", "vol": 0.30},
    "sub.boom": {"file": "vidzeo/sub_boom.wav", "cat": "impact", "vol": 0.38},
    "bass.drop": {"file": "vidzeo/bass_drop.wav", "cat": "impact", "vol": 0.40},
    "reverse.swell": {"file": "vidzeo/reverse_swell.wav", "cat": "riser", "vol": 0.30},
    "news.sting": {"file": "vidzeo/news_sting.wav", "cat": "sting", "vol": 0.32},
    # Typography & mechanical
    "type.click": {"file": "vidzeo/typewriter.wav", "cat": "click", "vol": 0.25},
    "type.burst": {"file": "vidzeo/typing_burst.wav", "cat": "typing", "vol": 0.28},
    "camera.shutter": {"file": "vidzeo/camera_shutter.wav", "cat": "camera", "vol": 0.34},
    "photo.slide": {"file": "vidzeo/photo_slide.wav", "cat": "photo", "vol": 0.30},
    # Geographic & data pings
    "map.ping": {"file": "vidzeo/map_pulse.wav", "cat": "map-ping", "vol": 0.32},
    "map.draw": {"file": "vidzeo/map_draw.wav", "cat": "map-ping", "vol": 0.30},
    "route.ping": {"file": "vidzeo/route_ping.wav", "cat": "map-ping", "vol": 0.26},
    "sonar.ping": {"file": "vidzeo/sonar.wav", "cat": "map-ping", "vol": 0.28},
    "data.tick": {"file": "vidzeo/data_tick.wav", "cat": "click", "vol": 0.28},
    "technical.tick": {"file": "vidzeo/technical_tick.wav", "cat": "click", "vol": 0.26},
    "coin.tick": {"file": "vidzeo/coin_tick.wav", "cat": "click", "vol": 0.28},
    "cash.counter": {"file": "vidzeo/cash_counter.wav", "cat": "counter", "vol": 0.32},
    # Narrative risers / reveals
    "riser.tension": {"file": "vidzeo/riser.wav", "cat": "riser", "vol": 0.30},
    "reveal.chime": {"file": "vidzeo/reveal_chime.wav", "cat": "reveal", "vol": 0.28},
    "reveal.soft": {"file": "edit/edit_reveal_soft.wav", "cat": "reveal", "vol": 0.25},
    # Diverse transitions & editorial actions
    "edit.slider.move": {"file": "edit/edit_slider_move.wav", "cat": "transition", "vol": 0.25},
    "edit.zoom.whirr": {"file": "edit/edit_zoom_whirr.wav", "cat": "transition", "vol": 0.24},
    "edit.whoosh.sucked": {"file": "edit/edit_whoosh_sucked.wav", "cat": "transition", "vol": 0.24},
    "edit.hit.tight": {"file": "edit/edit_hit_tight.wav", "cat": "hit", "vol": 0.32},
    "ui.tick": {"file": "vidzeo/ui_tick.wav", "cat": "click", "vol": 0.26},
    "decision.tick": {"file": "vidzeo/decision_tick.wav", "cat": "click", "vol": 0.28},
}

# Diverse transition sound effects pool (rotated across 1-in-3 transitions)
TRANSITION_SFX_POOL: List[Dict[str, Any]] = [
    {"sfx_id": "whoosh.air", "vol": 0.22, "dur": 0.35},
    {"sfx_id": "transition.suck", "vol": 0.25, "dur": 0.40},
    {"sfx_id": "edit.zoom.whirr", "vol": 0.24, "dur": 0.35},
    {"sfx_id": "whoosh.snap", "vol": 0.26, "dur": 0.30},
    {"sfx_id": "edit.slider.move", "vol": 0.25, "dur": 0.38},
    {"sfx_id": "whoosh.deep", "vol": 0.26, "dur": 0.42},
    {"sfx_id": "whoosh.soft", "vol": 0.24, "dur": 0.35},
]


def resolve_sfx_path(sfx_id: str) -> Optional[str]:
    """Resolve an SFX token to its public/sound-effects/ web path if available."""
    entry = SFX_REGISTRY.get(sfx_id)
    if not entry:
        return None
    rel_path = entry["file"]
    full_path = SFX_DIR / rel_path
    if full_path.is_file():
        return f"/sound-effects/{rel_path.replace(os.sep, '/')}"
    return None


def generate_sound_cues_for_scene(
    scene_type: str,
    scene_start: float = 0.0,
    scene_duration: float = 4.0,
    emphasis_time_local: float = 0.0,
    has_transition: bool = True,
    scene_index: int = 0,
) -> List[SoundCue]:
    """Select appropriate sound cues based on scene type, entrance timing, and scene index.
    
    Adheres strictly to documentary editorial restraint:
    1. 1-in-3 Transition Sound Rule: Transition whooshes/movements only trigger on every 3rd cut
       (scene_index % 3 == 0 for scenes after cold open), rotating through a diverse mixture of SFX.
       2 out of 3 cuts remain clean, natural cinematic edits without repetitive whoosh sounds.
    2. Motion Graphic Synchronous SFX: Every motion graphic is backed by high-impact semantic
       sound cues (data ticks, counters, route pings, attributions) timed precisely to the animation.
    """
    cues: List[SoundCue] = []

    # 1. Cold open hook vs. 1-in-3 Scene Transition Rule
    if scene_index == 0 or scene_start <= 0.1:
        # The 3-Second Hook Rule: Cold open must grab attention immediately with a deep cinematic punch
        cues.append(SoundCue(
            sfx_id="whoosh.deep",
            time_local=0.0,
            volume=0.30,
            duration=0.45,
            tag="cold_open_hook",
        ))
    elif has_transition and scene_index > 0 and (scene_index % 3 == 0):
        # Trigger transition SFX on 1 in 3 transitions using diverse mixture pool
        t_choice = TRANSITION_SFX_POOL[(scene_index // 3) % len(TRANSITION_SFX_POOL)]
        cues.append(SoundCue(
            sfx_id=t_choice["sfx_id"],
            time_local=0.0,
            volume=t_choice["vol"],
            duration=t_choice["dur"],
            tag=f"transition_in_{scene_index}",
        ))

    # 2. Scene-specific semantic motion graphic sound placement
    if scene_type == VisualSceneType.CHAPTER_TITLE.value or scene_type == "ChapterTitle":
        cues.append(SoundCue(sfx_id="riser.tension", time_local=0.0, volume=0.26, duration=0.8, tag="chapter_riser"))
        cues.append(SoundCue(sfx_id="impact.soft", time_local=0.18, volume=0.38, duration=0.6, tag="chapter_sting"))
    elif scene_type in {VisualSceneType.STAT_CARD.value, "StatCard"}:
        cues.append(SoundCue(sfx_id="data.tick", time_local=max(0.12, emphasis_time_local), volume=0.32, duration=0.35, tag="stat_hit"))
        if scene_duration >= 2.0:
            cues.append(SoundCue(sfx_id="cash.counter", time_local=min(scene_duration - 0.8, 0.75), volume=0.28, duration=0.6, tag="stat_counter_roll"))
        if scene_duration >= 3.0:
            cues.append(SoundCue(sfx_id="coin.tick", time_local=min(scene_duration - 0.5, 2.0), volume=0.26, duration=0.3, tag="stat_lock"))
    elif scene_type in {VisualSceneType.CHART.value, "Chart"}:
        cues.append(SoundCue(sfx_id="data.tick", time_local=max(0.12, emphasis_time_local), volume=0.32, duration=0.35, tag="chart_hit"))
        if scene_duration >= 2.0:
            cues.append(SoundCue(sfx_id="cash.counter", time_local=min(scene_duration - 0.8, 1.1), volume=0.28, duration=0.65, tag="chart_counter_roll"))
        if scene_duration >= 3.0:
            cues.append(SoundCue(sfx_id="coin.tick", time_local=min(scene_duration - 0.5, 2.3), volume=0.26, duration=0.3, tag="chart_milestone"))
    elif scene_type in {VisualSceneType.ANIMATED_MAP.value, "AnimatedMap"}:
        cues.append(SoundCue(sfx_id="map.ping", time_local=0.12, volume=0.34, duration=0.45, tag="map_pulse"))
        cues.append(SoundCue(sfx_id="map.draw", time_local=0.28, volume=0.30, duration=0.75, tag="map_trajectory"))
        if scene_duration >= 2.5:
            cues.append(SoundCue(sfx_id="route.ping", time_local=min(scene_duration - 0.5, 2.0), volume=0.28, duration=0.4, tag="map_dest_lock"))
    elif scene_type in {VisualSceneType.DOCUMENT_HIGHLIGHT.value, "DocumentHighlight"}:
        cues.append(SoundCue(sfx_id="paper.rustle", time_local=0.08, volume=0.34, duration=0.5, tag="paper_reveal"))
        cues.append(SoundCue(sfx_id="stamp.hit", time_local=0.5, volume=0.38, duration=0.4, tag="stamp_declassified"))
        if scene_duration >= 2.2:
            cues.append(SoundCue(sfx_id="marker.scratch", time_local=min(scene_duration - 0.5, 1.2), volume=0.28, duration=0.35, tag="marker_highlight"))
    elif scene_type in {VisualSceneType.COLLAGE.value, "Collage"}:
        cues.append(SoundCue(sfx_id="photo.slide", time_local=0.1, volume=0.32, duration=0.4, tag="polaroid_pin"))
        cues.append(SoundCue(sfx_id="paper.rustle", time_local=0.25, volume=0.28, duration=0.45, tag="paper_stack"))
    elif scene_type in {VisualSceneType.HEADLINE.value, "Headline"}:
        cues.append(SoundCue(sfx_id="paper.flip", time_local=0.1, volume=0.34, duration=0.5, tag="broadsheet_reveal"))
        cues.append(SoundCue(sfx_id="hit.dry", time_local=0.5, volume=0.30, duration=0.35, tag="headline_stamp"))
    elif scene_type in {VisualSceneType.QUOTE_CARD.value, "QuoteCard"}:
        cues.append(SoundCue(sfx_id="reveal.soft", time_local=0.15, volume=0.28, duration=0.45, tag="quote_in"))
        cues.append(SoundCue(sfx_id="stamp.hit", time_local=min(scene_duration - 0.5, 1.0), volume=0.32, duration=0.4, tag="quote_attribution"))
    elif scene_type in {VisualSceneType.PERSON_PROFILE.value, "PersonProfile"}:
        cues.append(SoundCue(sfx_id="camera.shutter", time_local=0.15, volume=0.36, duration=0.35, tag="shutter_snap"))
        cues.append(SoundCue(sfx_id="photo.slide", time_local=0.35, volume=0.26, duration=0.4, tag="dossier_slide"))
    elif scene_type in {"TextCard", "text-card", VisualSceneType.KINETIC_TEXT.value, "KineticText"}:
        cues.append(SoundCue(sfx_id="reveal.soft", time_local=0.08, volume=0.24, duration=0.35, tag="text_reveal"))
        cues.append(SoundCue(sfx_id="marker.scratch", time_local=max(0.2, emphasis_time_local), volume=0.26, duration=0.35, tag="highlight_sweep"))
    elif scene_type in {VisualSceneType.TIMELINE.value, "Timeline"}:
        cues.append(SoundCue(sfx_id="paper.flip", time_local=0.1, volume=0.28, duration=0.4, tag="date_flip"))
        cues.append(SoundCue(sfx_id="technical.tick", time_local=0.65, volume=0.26, duration=0.3, tag="milestone_tick"))
    elif scene_type in {VisualSceneType.DIAGRAM.value, "Diagram"}:
        cues.append(SoundCue(sfx_id="technical.tick", time_local=0.1, volume=0.26, duration=0.35, tag="diagram_tick"))
        cues.append(SoundCue(sfx_id="map.draw", time_local=0.45, volume=0.28, duration=0.5, tag="diagram_connect"))
    elif scene_type in {VisualSceneType.COMPARISON.value, "Comparison"}:
        cues.append(SoundCue(sfx_id="edit.slider.move", time_local=0.15, volume=0.26, duration=0.4, tag="slider_reveal"))

    return cues
