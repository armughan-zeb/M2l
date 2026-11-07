"""Test suite for Conversational AI Director Tweak (Phase 5)."""

import pytest
from server.services.editorial_director import (
    EditingDirector,
    validate_edit_plan,
    tweak_edit_plan,
    VisualSceneType,
)


def test_ai_director_tweak_pacing_faster(offline_director):
    director = offline_director
    plan = director.plan_documentary(
        script_text="The market expanded. Then the collapse began.",
        scene_texts=["The market expanded.", "Then the collapse began."],
        scene_durations=[4.0, 4.0],
        topic="Market Crash",
        category="finance",
    )

    # Apply tweak: "Make it faster and punchier"
    tweaked = tweak_edit_plan(plan, "Make it faster and punchier with higher energy")

    assert tweaked.visual_dna.motion_style == "punchy"
    for s in tweaked.scenes:
        assert s.motion.speed >= 1.2
        assert s.pacing.energy >= 0.7


def test_ai_director_tweak_visual_dna_genre_switch(offline_director):
    director = offline_director
    plan = director.plan_documentary(
        script_text="The technology evolved rapidly.",
        scene_texts=["The technology evolved rapidly."],
        scene_durations=[4.0],
        topic="Tech Evolution",
        category="technology",
    )

    # Apply tweak: "Switch to dark archival historical look"
    tweaked = tweak_edit_plan(plan, "Switch to dark archival historical look with paper texture")

    assert tweaked.visual_dna.genre in {"historical-documentary", "royal-history"}
    assert tweaked.visual_dna.texture == "paper"
    assert tweaked.visual_dna.image_treatment == "warm_archival"
    assert tweaked.visual_dna.palette["accent"] in {"#C17A3A", "#D4AF37"}
