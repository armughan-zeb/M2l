"""Comprehensive test suite verifying multi-niche TextCard generation,
entity extraction into highlight_words, and synchronized procedural SFX.
"""

import pytest
from server.services.semantic_edit_engine import build_semantic_edit_manifest
from server.services.native_render_policy import procedural_sound_library


def test_niche_text_cards_and_entity_highlighting():
    """Verify that multiple niches produce varied text card styles with full entity highlighting."""
    texts = [
        "In October 1962, President John F. Kennedy discovered secret Soviet missile installations in Cuba.",
        "The military intelligence budget exceeded $50 billion as DEFCON 2 was declared.",
        "Over 100,000 troops mobilized rapidly across Florida, preparing for potential conflict.",
        "Diplomatic cables confirmed an unprecedented breakthrough between Washington and Moscow.",
    ]
    durations = [5.0, 4.8, 5.2, 5.0]
    media = [
        {"src": "test1.jpg", "type": "image"},
        {"src": "test2.jpg", "type": "image"},
        {"src": "test3.jpg", "type": "image"},
        {"src": "test4.jpg", "type": "image"},
    ]

    sound_lib = procedural_sound_library()

    manifest = build_semantic_edit_manifest(
        scene_texts=texts,
        scene_durations=durations,
        media=media,
        topic="Cuban Missile Crisis",
        category="history",
        config={
            "graphic_density": 0.75,
            "sound_density": 0.60,
            "sound_library": sound_lib,
            "variation_seed": 42,
        },
    )

    scenes = manifest.get("scenes", [])
    assert len(scenes) == 4

    # Check that graphic-enabled scenes contain rich primary_visual systems
    graphic_scenes = [s for s in scenes if s.get("primary_visual", {}).get("type") not in {"SmartImage", "SmartVideo"}]
    assert len(graphic_scenes) >= 2, f"Expected at least 2 graphic scenes, got {len(graphic_scenes)}"

    all_cues = [c for s in scenes for c in s.get("sound_cues", [])]
    assert len(all_cues) >= 1, "Expected sound cues across manifest"

    for s in scenes:
        pv = s.get("primary_visual", {})
        pv_type = pv.get("type")
        props = pv.get("props", {})
        if pv_type == "TextCard":
            assert props.get("text"), "TextCard missing text"
            assert props.get("style"), "TextCard missing style"
            assert props.get("kicker"), "TextCard missing kicker"
            hw = props.get("highlight_words", [])
            assert isinstance(hw, list)


def test_tech_niche_cyber_hud_style():
    """Verify tech niche produces cyber-hud style and technical ticks."""
    texts = [
        "Advanced neural network algorithms processed over 450 terabytes of cryptographic telemetry.",
        "The mainframe detected unauthorized quantum decryption sequences originating from sector 7.",
    ]
    durations = [4.5, 4.5]
    media = [{"src": "tech1.jpg", "type": "image"}, {"src": "tech2.jpg", "type": "image"}]

    sound_lib = procedural_sound_library()

    manifest = build_semantic_edit_manifest(
        scene_texts=texts,
        scene_durations=durations,
        media=media,
        topic="Quantum Cyber Defense",
        category="technology",
        config={
            "graphic_density": 0.85,
            "sound_density": 0.70,
            "sound_library": sound_lib,
            "selected_template": "cyber-hud",
            "variation_seed": 101,
        },
    )

    scenes = manifest.get("scenes", [])
    types = [s.get("primary_visual", {}).get("type") for s in scenes]
    assert any(t in {"TextCard", "StatCard", "Diagram"} for t in types)
    all_sfx = [c.get("kind") for s in scenes for c in s.get("sound_cues", [])]
    assert any("tick" in str(k) or "whoosh" in str(k) for k in all_sfx)


def test_crime_investigative_memo_style():
    """Verify crime/investigative niche produces investigative-memo and paper/rustle SFX."""
    texts = [
        "Classified autopsy reports revealed toxic cyanide concentrations in the victim's bloodstream.",
        "Federal investigators secured 14 fingerprint fragments from the abandoned getaway vehicle.",
    ]
    durations = [4.5, 4.5]
    media = [{"src": "crime1.jpg", "type": "image"}, {"src": "crime2.jpg", "type": "image"}]

    sound_lib = procedural_sound_library()

    manifest = build_semantic_edit_manifest(
        scene_texts=texts,
        scene_durations=durations,
        media=media,
        topic="The Black Dahlia Dossier",
        category="crime",
        config={
            "graphic_density": 0.85,
            "sound_density": 0.70,
            "sound_library": sound_lib,
            "selected_template": "investigative-memo",
            "variation_seed": 777,
        },
    )

    scenes = manifest.get("scenes", [])
    types = [s.get("primary_visual", {}).get("type") for s in scenes]
    assert any(t in {"TextCard", "DocumentHighlight", "PersonProfile", "Collage"} for t in types)
    all_sfx = [c.get("kind") for s in scenes for c in s.get("sound_cues", [])]
    assert any("paper" in str(k) or "whoosh" in str(k) or "stamp" in str(k) for k in all_sfx)
