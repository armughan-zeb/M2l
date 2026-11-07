"""Test suite for Editorial Director Contract, Visual DNA, and Similarity Detector (Phase 1)."""

import pytest
from server.services.editorial_director import (
    EditPlan,
    VisualDNA,
    SceneDecision,
    PrimaryVisual,
    MotionParameters,
    AudioPlan,
    PacingBeat,
    VisualSceneType,
    UNIVERSAL_SCENE_TYPES,
    validate_edit_plan,
    generate_visual_dna,
    compute_video_seed,
    GENRE_PRESETS,
    FONT_PERSONALITIES,
    EditFingerprint,
    compute_edit_fingerprint,
    check_similarity,
    reroll_variations_if_similar,
)


def test_universal_scene_types_contains_15_systems():
    expected = {
        "SmartImage", "SmartVideo", "KineticText", "StatCard", "QuoteCard",
        "Headline", "DocumentHighlight", "AnimatedMap", "Timeline", "Chart",
        "Comparison", "Collage", "PersonProfile", "Diagram", "ChapterTitle",
    }
    assert set(UNIVERSAL_SCENE_TYPES) == expected
    assert len(UNIVERSAL_SCENE_TYPES) == 15


def test_edit_plan_validation_and_roundtrip():
    raw = {
        "title": "The Collapse of Enron",
        "topic": "Enron accounting scandal",
        "category": "finance",
        "seed": 42109,
        "visual_dna": {
            "genre": "finance-explainer",
            "palette": {"primary": "#ffffff", "accent": "#10B981"},
            "typography": {"display": "Montserrat", "body": "Inter"},
            "texture": "clean",
        },
        "audio_plan": {
            "duration": 18.5,
            "voiceover": {"src": "audio/narration.mp3"},
        },
        "scenes": [
            {
                "scene_id": "scene_1",
                "scene_index": 0,
                "start": 0.0,
                "end": 4.5,
                "duration": 4.5,
                "narration": "In late 2001, an energy titan vanished into thin air.",
                "visual_intent": "corporate_mystery",
                "primary_visual": {
                    "type": "ChapterTitle",
                    "variant": "bold_editorial_kicker",
                    "props": {"kicker": "OCTOBER 2001", "headline": "THE TITAN FALLS"},
                },
                "motion": {"camera": "slow_push", "speed": 1.0},
                "pacing": {"importance": 0.9, "emotion": 0.7, "energy": 0.6, "complexity": 0.4},
                "transition_out": "hard_cut",
            },
            {
                "scene_id": "scene_2",
                "scene_index": 1,
                "start": 4.5,
                "end": 10.0,
                "duration": 5.5,
                "narration": "Over sixty billion dollars in market valuation was wiped out in weeks.",
                "visual_intent": "financial_collapse",
                "primary_visual": {
                    "type": "Chart",
                    "variant": "editorial_bar",
                    "props": {
                        "visualizationType": "line",
                        "values": [{"label": "2000", "value": 90}, {"label": "2001", "value": 0.26}],
                        "emphasis": "$60 BILLION",
                    },
                },
                "motion": {"camera": "static", "speed": 1.0},
                "transition_out": "directional_slide_left",
            },
        ],
    }

    plan = validate_edit_plan(raw)
    assert plan.title == "The Collapse of Enron"
    assert len(plan.scenes) == 2
    assert plan.scenes[0].primary_visual.type == VisualSceneType.CHAPTER_TITLE.value
    assert plan.scenes[1].primary_visual.type == VisualSceneType.CHART.value

    serialized = plan.to_dict()
    assert serialized["version"] == "2.0"
    assert serialized["scenes"][1]["primary_visual"]["props"]["emphasis"] == "$60 BILLION"


def test_visual_dna_genre_detection_and_determinism():
    script_royal = "King Charles III and Prince William held a council at Buckingham Palace."
    dna_royal = generate_visual_dna("The Royal Succession", "history", script_royal)
    assert dna_royal.genre == "royal-history"
    assert dna_royal.texture == "clean"
    assert dna_royal.palette["accent"] == "#D4AF37"  # Gold

    script_tech = "Artificial intelligence models rely on advanced semiconductor chips."
    dna_tech = generate_visual_dna("The AI Chip War", "tech", script_tech)
    assert dna_tech.genre == "technology"
    assert dna_tech.texture == "clean"
    assert dna_tech.palette["accent"] == "#F59E0B"

    # Test deterministic seed reproducibility
    seed1 = compute_video_seed("user_123", "proj_abc", "Some fixed script content")
    seed2 = compute_video_seed("user_123", "proj_abc", "Some fixed script content")
    assert seed1 == seed2


def test_visual_dna_channel_branding_override():
    brand = {
        "brand_color": "#FF007A",
        "background_color": "#000000",
        "font_family": "Bebas Neue",
    }
    dna = generate_visual_dna("History of Aviation", "general", "Airplanes flew", channel_brand=brand)
    assert dna.palette["accent"] == "#FF007A"
    assert dna.palette["background"] == "#000000"
    assert dna.typography["display"] == "Bebas Neue"


def test_similarity_detector_and_anti_repetition():
    plan_a = validate_edit_plan({
        "title": "Project Alpha",
        "scenes": [
            {"primary_visual": {"type": "SmartImage", "variant": "full_bleed_cinematic"}, "motion": {"camera": "slow_push"}, "transition_out": "hard_cut"},
            {"primary_visual": {"type": "Headline", "variant": "newspaper_front_page"}, "motion": {"camera": "pan_left"}, "transition_out": "hard_cut"},
            {"primary_visual": {"type": "AnimatedMap", "variant": "route_flight_arc"}, "motion": {"camera": "drift"}, "transition_out": "cross_dissolve"},
        ],
    })

    fp_a = compute_edit_fingerprint(plan_a)
    assert fp_a.scene_sequence == ["SmartImage", "Headline", "AnimatedMap"]

    # Identical structure should fail similarity check
    check = check_similarity(fp_a, [fp_a], threshold=0.70)
    assert check["passed"] is False
    assert check["max_similarity"] >= 0.9

    # Reroll should adjust variations to reduce similarity
    adjusted = reroll_variations_if_similar(plan_a, [fp_a], threshold=0.70)
    assert adjusted is not None


def test_to_remotion_manifest():
    plan = validate_edit_plan({
        "title": "Documentary Test",
        "topic": "Space Exploration",
        "fps": 30,
        "width": 1920,
        "height": 1080,
        "scenes": [
            {
                "scene_id": "scene_1",
                "scene_index": 0,
                "start": 0.0,
                "duration": 3.0,
                "narration": "In July 1969, humanity touched the moon.",
                "primary_visual": {
                    "type": "Timeline",
                    "variant": "date_flip_calendar",
                    "props": {"year": "1969"},
                    "asset": {"src": "/images/moon.jpg"},
                },
                "motion": {"camera": "slow_push", "entrance": "mask_reveal"},
                "transition_out": "hard_cut",
            },
        ],
    })

    manifest = plan.to_remotion_manifest()
    assert manifest["version"] == 2
    assert manifest["fps"] == 30
    assert manifest["duration"] == 3.0
    assert manifest["duration_in_frames"] == 90
    assert len(manifest["scenes"]) == 1

    scene0 = manifest["scenes"][0]
    assert scene0["id"] == "scene_1"
    assert scene0["primary"]["type"] == "Timeline"
    assert scene0["background"]["src"] == "/images/moon.jpg"
    assert scene0["media"]["src"] == "/images/moon.jpg"
    assert scene0["camera"]["motion"] == "slow_push"
    assert scene0["transition"]["template_id"] == "hard_cut"
    assert manifest["metrics"]["variation_audit"]["status"] == "pass"


def test_scene_graphic_themes_change_palette_without_lightening_maps():
    plan = validate_edit_plan({
        "title": "Theme grammar",
        "visual_dna": {"palette": {
            "primary": "#F7F2E8", "secondary": "#D8CEC1", "accent": "#F2C14E",
            "accent2": "#28B8A8", "background": "#101B25", "surface": "#1D2B35",
        }},
        "scenes": [
            {"primary_visual": {"type": "Chart", "props": {"data": [{"label": "A", "value": 2}, {"label": "B", "value": 3}]}}, "graphic_theme": "editorial_light"},
            {"primary_visual": {"type": "Diagram"}, "graphic_theme": "accent_focus"},
            {"primary_visual": {"type": "AnimatedMap"}, "graphic_theme": "editorial_light"},
        ],
    })
    scenes = plan.to_remotion_manifest()["scenes"]
    light = scenes[0]["visual_dna"]["palette"]
    focus = scenes[1]["visual_dna"]["palette"]
    mapped = scenes[2]["visual_dna"]["palette"]
    assert light["background"] == "#F7F2E8"
    assert light["primary"] == "#101B25"
    assert scenes[0]["graphic"]["props"]["card_theme"] == "white_editorial"
    assert focus["surface"] != "#1D2B35"
    assert focus["accent"] == "#28B8A8"
    assert mapped["background"] == "#101B25"
    assert scenes[2]["design"]["graphic_theme"] == "cinematic_dark"
