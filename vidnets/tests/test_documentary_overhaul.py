"""Tests for documentary overhaul features: fonts, genre presets, and sound cue synchronization."""

import pytest
from pathlib import Path
from server.services.local_scene_systems import _resolve_font_file
from server.services.semantic_edit_engine import plan_sound_cues


def test_documentary_fonts_exist_and_resolve():
    """Verify that all newly downloaded professional fonts resolve to physical files."""
    test_fonts = [
        "inter",
        "interbold",
        "oswald",
        "archivoblack",
        "barlowcondensed",
        "dmsans",
        "plusjakartasans",
        "lora",
        "jetbrainsmono",
    ]
    for font_name in test_fonts:
        path = _resolve_font_file(font_name)
        assert path.exists(), f"Font {font_name} resolved to {path} which does not exist"
        assert path.stat().st_size > 1000, f"Font file {path} is empty or corrupted"


def test_sound_cue_pre_roll_and_local_timing():
    """Verify sound cues include time_local and pre-roll whoosh lead for graphics."""
    scene = {
        "start": 10.0,
        "duration": 4.5,
        "graphic": {
            "template_id": "vox-highlighter",
            "scene_local_start": 0.8,
            "duration": 3.0,
            "props": {"style": "vox-highlighter"},
        },
        "semantic_beat": {"energy": "normal", "importance": 0.8},
        "transition": {"template_id": "cut"},
    }

    cues = plan_sound_cues(scene, 0, {})
    assert len(cues) > 0, "Expected sound cues to be planned"
    for cue in cues:
        assert "time_local" in cue, "Every cue must have time_local for Remotion/renderer alignment"
        assert "scene_local_start" in cue, "Every cue must have scene_local_start"
        assert cue["time_local"] >= 0.0, "time_local must be non-negative"
        assert cue["time_local"] <= scene["duration"], "time_local cannot exceed scene duration"

    # Verify whoosh lead: whoosh cue should lead the 0.8s graphic entrance by ~0.08s
    entrance_cues = [c for c in cues if "text_card_entrance" in str(c.get("reason", ""))]
    if entrance_cues:
        ec = entrance_cues[0]
        # Should be approximately 0.72s (0.8 - 0.08)
        assert ec["time_local"] <= 0.8, "Whoosh cue must lead the visual graphic entrance"
