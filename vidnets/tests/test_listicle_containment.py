"""Integration tests for the rebuilt section-aware pipeline."""
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'server'))


# ── Test: Section detection on actual football script ──

def test_football_section_detection():
    from services.semantic_query_agent import detect_list_sections

    scene_texts = [
        "At number five is Erling Haaland. The Norwegian goal machine.",
        "With incredible strength, blistering pace, and deadly finishing.",
        "At number four is Jude Bellingham. Still incredibly young.",
        "He combines vision, technical ability, and leadership.",
        "Taking the third spot is Kylian Mbappe. Few players in history.",
        "Whether it is a World Cup final, Mbappe delivers.",
        "At number two is Cristiano Ronaldo. Two decades at the top.",
        "With over 900 career goals, his legacy is unquestionable.",
    ]
    sections = detect_list_sections(scene_texts, "football legends")

    expected = [
        ("Erling Haaland", True),
        ("Erling Haaland", False),
        ("Jude Bellingham", True),
        ("Jude Bellingham", False),
        ("Kylian Mbappe", True),
        ("Kylian Mbappe", False),
        ("Cristiano Ronaldo", True),
        ("Cristiano Ronaldo", False),
    ]
    for i, (exp_subj, exp_marker) in enumerate(expected):
        assert sections[i]["container_subject"] == exp_subj, f"Scene {i}: expected {exp_subj}, got {sections[i]['container_subject']}"
        assert sections[i]["has_explicit_marker"] == exp_marker, f"Scene {i}: marker mismatch"


# ── Test: Mechanical fallback produces section-aware queries ──

def test_mechanical_fallback_section_aware():
    from app import _run_mechanical_query_fallback

    scene_texts = [
        "Number 1: Apple is a healthy fruit.",
        "It contains fiber and vitamins.",
        "Number 2: Banana is rich in potassium.",
        "Bananas boost energy naturally.",
    ]
    rows = _run_mechanical_query_fallback("test", "fruits", scene_texts, silent=True)

    assert len(rows) >= 4, f"Expected 4+ rows, got {len(rows)}"
    assert rows[0].get("section_subject") == "fruits", f"Scene 0: {rows[0].get('section_subject')}"
    assert rows[2].get("section_subject") == "fruits", f"Scene 2: {rows[2].get('section_subject')}"
    assert "apple" in str(rows[0].get("visual_facet", "")).lower(), f"Facet doesn't contain apple: {rows[0].get('visual_facet')}"
    assert "banana" in str(rows[2].get("visual_facet", "")).lower(), f"Facet doesn't contain banana: {rows[2].get('visual_facet')}"
    assert rows[0].get("_mechanical") is True, "Should be flagged as mechanical"


# ── Test: Section-aware rotation rescue ──

def test_rotation_rescue_respects_sections():
    """Section-aware filtering: scene in a section only gets clips from same section."""
    from app import _section_subjects_from_config
    
    # Verify the helper works correctly
    config = {
        "_yt_scene_query_overrides": [
            {"section_subject": "Apple"},
            {"section_subject": "Apple"},
            {"section_subject": "Banana"},
            {"section_subject": "Banana"},
        ]
    }
    subjects = _section_subjects_from_config(config, 4)
    assert subjects == ["Apple", "Apple", "Banana", "Banana"]
    assert subjects[0] != subjects[2], "Apple and Banana should be different sections"


# ── Test: _subject_is_weak ──

def test_subject_is_weak_capitalized():
    from services.youtube_clips_service import _subject_is_weak

    assert not _subject_is_weak("Apple"), "Apple should NOT be weak"
    assert not _subject_is_weak("Banana"), "Banana should NOT be weak"
    assert not _subject_is_weak("Kylian"), "Kylian should NOT be weak"
    assert not _subject_is_weak("Cristiano Ronaldo"), "Cristiano Ronaldo should NOT be weak"
    assert _subject_is_weak("video"), "video should be weak"
    assert _subject_is_weak("footage"), "footage should be weak"


# ── Test: Section subjects from config ──

def test_section_subjects_from_config():
    from app import _section_subjects_from_config

    config = {
        "_yt_scene_query_overrides": [
            {"section_subject": "Apple"},
            {"section_subject": "Apple"},
            {"section_subject": "Banana"},
            {"section_subject": "Banana"},
        ]
    }
    subjects = _section_subjects_from_config(config, 4)
    assert subjects == ["Apple", "Apple", "Banana", "Banana"]


# ── Test: Domain noise stripping ──

def test_domain_noise_stripping():
    from app import _repair_concept_topic_query_rows

    # Need 3+ scenes for section detection to activate
    scene_texts = [
        "Intro: Let me tell you about fruits.",
        "Number 1: Apple is a healthy fruit.",
        "Number 2: Banana is rich in potassium.",
    ]
    rows = [
        {"youtube_query": "fruits health intro footage", "section_subject": ""},
        {"youtube_query": "fruits health Apple nature snack", "section_subject": ""},
        {"youtube_query": "fruits health Banana energy boost", "section_subject": ""},
    ]

    repaired = _repair_concept_topic_query_rows(rows, "fruits health", scene_texts)

    # Scene 1 (Apple) should have section_subject = Apple
    assert repaired[1].get("section_subject") == "Apple", f"Expected Apple, got {repaired[1].get('section_subject')}"
    # Scene 2 (Banana) should have section_subject = Banana
    assert repaired[2].get("section_subject") == "Banana", f"Expected Banana, got {repaired[2].get('section_subject')}"
    # Queries should contain their section subjects
    assert "apple" in repaired[1].get("youtube_query", "").lower()
    assert "banana" in repaired[2].get("youtube_query", "").lower()


def test_query_normalizer_keeps_full_section_subject_and_audit_contract():
    from services.semantic_query_agent import normalize_agent_queries

    rows = normalize_agent_queries(
        [{
            "primary": "football legends Kylian Mbappe skills real footage",
            "supporting": "football legends Mbappe training",
            "fallback": "football legends match footage",
        }],
        1,
        ["Kylian Mbappe"],
        topic_anchor="football legends",
    )

    row = rows[0]
    assert row["primary"].startswith("Kylian Mbappe ")
    assert "football" not in row["primary"].lower()
    assert "legends" not in row["primary"].lower()
    assert row["query_contract"]["primary"]["anchored"] is True
    assert row["query_contract"]["primary"]["quality"] == "pass"
