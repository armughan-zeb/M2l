"""Test suite for Script Analyzer, Scene Grammar, and Editing Director (Phases 2 & 3)."""

import pytest
from server.services.editorial_director import (
    EditingDirector,
    analyze_sentence_triggers,
    compute_beat_pacing,
    enforce_scene_grammar,
    enforce_scene_dict_grammar,
    VisualSceneType,
    SceneDecision,
    PrimaryVisual,
    MotionParameters,
    PacingBeat,
)


def test_script_analyzer_triggers():
    # Number triggers StatCard or Chart
    t_num = analyze_sentence_triggers("Apple's revenue grew to $394 billion in 2022.")
    assert len(t_num.numbers) >= 1
    assert t_num.recommended_scene_type in {VisualSceneType.STAT_CARD.value, VisualSceneType.CHART.value}

    # Quote triggers QuoteCard
    t_quote = analyze_sentence_triggers('When asked about the crash, the engineer replied "we had no choice."')
    assert len(t_quote.quotes) == 1
    assert t_quote.recommended_scene_type == VisualSceneType.QUOTE_CARD.value

    # Document triggers DocumentHighlight
    t_doc = analyze_sentence_triggers("An internal audit memo revealed the concealed liabilities.")
    assert t_doc.has_document is True
    assert t_doc.recommended_scene_type == VisualSceneType.DOCUMENT_HIGHLIGHT.value

    # Headline triggers Headline
    t_head = analyze_sentence_triggers("The Times reported the unexpected insolvency on the front page.")
    assert t_head.has_headline is True
    assert t_head.recommended_scene_type == VisualSceneType.HEADLINE.value

    # Locations trigger AnimatedMap
    t_map = analyze_sentence_triggers("Napoleon marched his grand army from Paris to Moscow.")
    assert len(t_map.locations) >= 2
    assert t_map.recommended_scene_type == VisualSceneType.ANIMATED_MAP.value


def test_beat_pacing_computation():
    beat_calm = compute_beat_pacing("The village was quiet as the evening approached.", 1, 10)
    beat_intense = compute_beat_pacing("Suddenly the emergency sirens wailed as panic exploded in the streets.", 2, 10)

    assert beat_intense.energy > beat_calm.energy
    # Scene 0 has high importance
    beat_intro = compute_beat_pacing("In 1912, a ship set sail.", 0, 5)
    assert beat_intro.importance >= 0.9


def test_scene_grammar_enforcement():
    # 4 consecutive full bleed images should be broken up
    scenes = [
        SceneDecision(
            scene_id=f"s_{i}", scene_index=i, start=i*4.0, end=(i+1)*4.0, duration=4.0,
            narration=f"Scene {i}", visual_intent="beat",
            primary_visual=PrimaryVisual(type="SmartImage", variant="full_bleed_cinematic"),
            motion=MotionParameters(), pacing=PacingBeat(),
        )
        for i in range(5)
    ]

    validated = enforce_scene_grammar(scenes)
    assert len(validated) == 5

    # Check that variant was diversified so we don't have >2 identical full bleed images
    variants = [s.primary_visual.variant for s in validated]
    assert len(set(variants)) > 1


def test_editing_director_full_plan(offline_director):
    director = offline_director
    script = [
        "In 1912, the greatest vessel ever constructed embarked on its maiden voyage.",
        "Captain Smith received a critical ice warning memo from neighboring ships.",
        'Passengers reported hearing a sound "like tearing canvas along the hull."',
        "Over 1,500 lives were lost in the freezing Atlantic waters.",
        "The tragedy fundamentally reshaped maritime safety regulations worldwide.",
    ]
    durations = [4.5, 4.0, 5.0, 4.0, 5.5]

    plan = director.plan_documentary(
        script_text=" ".join(script),
        scene_texts=script,
        scene_durations=durations,
        topic="The Sinking of Titanic",
        category="history",
    )

    assert plan.version == "2.0"
    assert plan.visual_dna.genre in {"historical-documentary", "investigative-documentary", "royal-history"}
    assert len(plan.scenes) == 5

    # Provider failure stays text-free and uses the grounded local opening.
    assert plan.director_metadata["status"] == "deterministic_fallback"
    assert plan.scenes[0].primary_visual.type == VisualSceneType.SMART_IMAGE.value
    # Scene 2 quotes passengers -> QuoteCard
    assert plan.scenes[2].primary_visual.type == VisualSceneType.QUOTE_CARD.value
    # Scene 3 has statistic -> StatCard
    assert plan.scenes[3].primary_visual.type == VisualSceneType.STAT_CARD.value


def test_three_second_rule_cold_open(offline_director):
    director = offline_director
    script = [
        "In 1997, Netflix was born as a small DVD mail-order experiment.",
        "Blockbuster famously rejected an offer to buy Netflix for $50 million.",
    ]
    plan = director.plan_documentary(
        script_text=" ".join(script),
        scene_texts=script,
        scene_durations=[4.0, 4.0],
        topic="The Netflix Story",
        category="technology",
    )

    scene0 = plan.scenes[0]
    # The 3-Second Rule: Scene 0 must have high tension & energy
    assert scene0.pacing.energy >= 0.85
    assert scene0.pacing.importance >= 0.90
    assert scene0.motion.camera != "static"
    assert scene0.motion.speed >= 1.2
    # Sound cue at 0.0s for immediate hook
    assert any(c.time_local == 0.0 for c in scene0.sound_cues)


def test_adjacent_non_media_anti_repetition_in_grammar():
    scenes = [
        SceneDecision(
            scene_id="s_0", scene_index=0, start=0.0, end=4.0, duration=4.0,
            narration="First bold statement.", visual_intent="statement",
            primary_visual=PrimaryVisual(type="KineticText", variant="staggered_lines"),
            template_id="kinetic-statement",
            motion=MotionParameters(), pacing=PacingBeat(),
        ),
        SceneDecision(
            scene_id="s_1", scene_index=1, start=4.0, end=8.0, duration=4.0,
            narration="Second bold statement.", visual_intent="statement",
            primary_visual=PrimaryVisual(type="KineticText", variant="staggered_lines"),
            template_id="kinetic-statement",
            motion=MotionParameters(), pacing=PacingBeat(),
        ),
    ]

    validated = enforce_scene_grammar(scenes)
    assert len(validated) == 2
    assert validated[0].primary_visual.variant != validated[1].primary_visual.variant
    assert validated[0].template_id != validated[1].template_id


def test_adjacent_non_media_anti_repetition_in_dict_grammar():
    scenes = [
        {
            "id": "s_0",
            "duration": 4.0,
            "primary_visual": {"type": "KineticText", "variant": "staggered_lines"},
            "template_id": "kinetic-statement",
            "pacing": {"continuation": False},
        },
        {
            "id": "s_1",
            "duration": 4.0,
            "primary_visual": {"type": "KineticText", "variant": "staggered_lines"},
            "template_id": "kinetic-statement",
            "pacing": {"continuation": False},
        },
    ]

    validated = enforce_scene_dict_grammar(scenes)
    assert len(validated) == 2
    assert validated[0]["primary_visual"]["variant"] != validated[1]["primary_visual"]["variant"]
    assert validated[0]["template_id"] != validated[1]["template_id"]


