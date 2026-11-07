"""Test suite for Word-Level Audio Sync and Sound Director (Phase 4)."""

import pytest
from server.services.editorial_director.sound_director import (
    SFX_REGISTRY,
    resolve_sfx_path,
    generate_sound_cues_for_scene,
)
from server.services.editorial_director.audio_sync import align_scene_to_words
from server.services.editorial_director.contract import (
    SceneDecision,
    PrimaryVisual,
    MotionParameters,
    PacingBeat,
    VisualSceneType,
)


def test_sfx_registry_tokens():
    assert "impact.soft" in SFX_REGISTRY
    assert "whoosh.light" in SFX_REGISTRY
    assert "map.ping" in SFX_REGISTRY
    assert "paper.shuffle" in SFX_REGISTRY


def test_sound_cue_generation_by_scene_type():
    # Chapter title gets impact & riser
    cues_chapter = generate_sound_cues_for_scene(VisualSceneType.CHAPTER_TITLE.value, 0.0, 4.0)
    sfx_ids = [c.sfx_id for c in cues_chapter]
    assert "impact.soft" in sfx_ids

    # Stat card gets data tick
    cues_stat = generate_sound_cues_for_scene(VisualSceneType.STAT_CARD.value, 4.0, 4.0, emphasis_time_local=1.2)
    sfx_ids = [c.sfx_id for c in cues_stat]
    assert "data.tick" in sfx_ids
    stat_cue = next(c for c in cues_stat if c.sfx_id == "data.tick")
    assert stat_cue.time_local == 1.2


def test_word_timestamp_alignment():
    scene = SceneDecision(
        scene_id="s_test",
        scene_index=1,
        start=10.0,
        end=15.0,
        duration=5.0,
        narration="Revenue reached $50 million by year end.",
        visual_intent="statistic",
        primary_visual=PrimaryVisual(type="StatCard", variant="hero_counter_large"),
        text={"headline": "REVENUE", "emphasis": "$50 million"},
        motion=MotionParameters(),
        pacing=PacingBeat(),
    )

    word_timings = [
        {"word": "Revenue", "start": 10.1, "end": 10.6},
        {"word": "reached", "start": 10.7, "end": 11.0},
        {"word": "$50", "start": 11.2, "end": 11.6},
        {"word": "million", "start": 11.7, "end": 12.2},
        {"word": "by", "start": 12.3, "end": 12.5},
        {"word": "year", "start": 12.6, "end": 12.9},
        {"word": "end", "start": 13.0, "end": 13.3},
    ]

    aligned = align_scene_to_words(scene, word_timings)
    # $50 starts at 11.2, local scene start is 10.0 -> emphasis_local ~ 1.2s
    assert "rollDurationSeconds" in aligned.primary_visual.props
    assert aligned.primary_visual.props["rollDurationSeconds"] >= 1.2
    assert len(aligned.sound_cues) >= 1


def test_one_in_three_transition_rule_and_mixture():
    """Verify that transition sound effects trigger only once every 3 scene transitions with varied SFX."""
    # Scene 0 is cold open hook
    cues_0 = generate_sound_cues_for_scene("SmartVideo", scene_start=0.0, scene_duration=5.0, scene_index=0)
    assert any(c.tag == "cold_open_hook" for c in cues_0)

    # Scene 1: cut from scene 0 (scene_index=1, 1 % 3 != 0) -> NO transition SFX
    cues_1 = generate_sound_cues_for_scene("SmartVideo", scene_start=5.0, scene_duration=5.0, scene_index=1)
    assert not any("transition_in" in c.tag for c in cues_1)

    # Scene 2: cut from scene 1 (scene_index=2, 2 % 3 != 0) -> NO transition SFX
    cues_2 = generate_sound_cues_for_scene("SmartVideo", scene_start=10.0, scene_duration=5.0, scene_index=2)
    assert not any("transition_in" in c.tag for c in cues_2)

    # Scene 3: cut from scene 2 (scene_index=3, 3 % 3 == 0) -> TRANSITION SFX TRIGGERS!
    cues_3 = generate_sound_cues_for_scene("SmartVideo", scene_start=15.0, scene_duration=5.0, scene_index=3)
    t_cue_3 = next((c for c in cues_3 if "transition_in" in c.tag), None)
    assert t_cue_3 is not None
    assert t_cue_3.sfx_id in SFX_REGISTRY

    # Scene 6: cut from scene 5 (scene_index=6, 6 % 3 == 0) -> TRANSITION SFX TRIGGERS with a DIFFERENT SFX!
    cues_6 = generate_sound_cues_for_scene("SmartVideo", scene_start=30.0, scene_duration=5.0, scene_index=6)
    t_cue_6 = next((c for c in cues_6 if "transition_in" in c.tag), None)
    assert t_cue_6 is not None
    assert t_cue_6.sfx_id in SFX_REGISTRY
    assert t_cue_6.sfx_id != t_cue_3.sfx_id  # Varied mixture!


def test_motion_graphic_sound_synchronization():
    """Verify that every motion graphic produces synchronized sound cues."""
    # Chart: gets data tick, counter roll, and milestone
    chart_cues = generate_sound_cues_for_scene("Chart", scene_start=5.0, scene_duration=4.5, scene_index=1)
    chart_ids = [c.sfx_id for c in chart_cues]
    assert "data.tick" in chart_ids
    assert "cash.counter" in chart_ids
    assert "coin.tick" in chart_ids

    # AnimatedMap: gets map ping, map draw, and route ping
    map_cues = generate_sound_cues_for_scene("AnimatedMap", scene_start=10.0, scene_duration=4.5, scene_index=2)
    map_ids = [c.sfx_id for c in map_cues]
    assert "map.ping" in map_ids
    assert "map.draw" in map_ids
    assert "route.ping" in map_ids

    # QuoteCard: gets reveal soft and stamp hit
    quote_cues = generate_sound_cues_for_scene("QuoteCard", scene_start=15.0, scene_duration=4.5, scene_index=3)
    quote_ids = [c.sfx_id for c in quote_cues]
    assert "reveal.soft" in quote_ids
    assert "stamp.hit" in quote_ids

