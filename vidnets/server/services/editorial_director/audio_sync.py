"""Word-Level Audio Synchronization Engine.

Aligns Whisper speech timestamps to visual keyframes:
- Entity spoken -> visual punch / badge appearance
- Number spoken -> counter rolls up to that exact word
- Location spoken -> map route starts drawing
- Turning point -> sound effect and transition trigger
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .contract import SceneDecision, VisualSceneType
from .sound_director import generate_sound_cues_for_scene, resolve_sfx_path


def align_scene_to_words(
    scene: SceneDecision,
    word_timings: Optional[Sequence[Mapping[str, Any]]] = None,
) -> SceneDecision:
    """Align scene animations, text reveals, and sound effects to Whisper word timestamps."""
    has_trans = (scene.transition_out != "none")
    if not word_timings:
        # Default fallback sound cues when word timings are absent
        cues = generate_sound_cues_for_scene(
            scene_type=scene.primary_visual.type,
            scene_start=scene.start,
            scene_duration=scene.duration,
            emphasis_time_local=0.2,
            has_transition=has_trans,
            scene_index=scene.scene_index,
        )
        for c in cues:
            resolved_src = resolve_sfx_path(c.sfx_id)
            if resolved_src:
                c.src = resolved_src
        scene.sound_cues = cues
        return scene

    words = []
    for item in (word_timings or []):
        if isinstance(item, dict):
            words.append(item)
        elif hasattr(item, "word"):
            words.append({
                "word": getattr(item, "word", ""),
                "start": float(getattr(item, "start", 0.0) or 0.0),
                "end": float(getattr(item, "end", 0.0) or 0.0),
            })
        elif hasattr(item, "to_dict"):
            words.append(item.to_dict())
        elif hasattr(item, "__dict__"):
            words.append(vars(item))
    scene_start = scene.start
    scene_end = scene.end

    # Filter words belonging to this scene
    scene_words = [
        w for w in words
        if float(w.get("start", 0.0)) >= (scene_start - 0.2) and float(w.get("end", 0.0)) <= (scene_end + 0.3)
    ]

    emphasis_local = 0.2
    target_text = (scene.text.get("emphasis") or scene.text.get("headline") or "").lower()

    if target_text and scene_words:
        for w in scene_words:
            word_str = str(w.get("word") or "").lower().strip()
            if word_str and word_str in target_text:
                w_start = float(w.get("start", scene_start))
                emphasis_local = max(0.0, min(scene.duration - 0.2, w_start - scene_start))
                break

    # If this is a numeric counter or map, adjust props to sync with the spoken word
    if scene.primary_visual.type in {VisualSceneType.STAT_CARD.value, VisualSceneType.CHART.value}:
        scene.primary_visual.props["rollDurationSeconds"] = round(emphasis_local + 0.3, 2)
    elif scene.primary_visual.type == VisualSceneType.ANIMATED_MAP.value:
        scene.primary_visual.props["startDelaySeconds"] = round(max(0.1, emphasis_local - 0.1), 2)

    # Attach sound cues synced to the calculated local emphasis timestamp
    cues = generate_sound_cues_for_scene(
        scene_type=scene.primary_visual.type,
        scene_start=scene.start,
        scene_duration=scene.duration,
        emphasis_time_local=emphasis_local,
        has_transition=has_trans,
        scene_index=scene.scene_index,
    )
    for c in cues:
        resolved_src = resolve_sfx_path(c.sfx_id)
        if resolved_src:
            c.src = resolved_src
    scene.sound_cues = cues

    return scene
