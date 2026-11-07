import os
import sys


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

import app
from services.semantic_query_agent import detect_list_sections
from services.youtube_pipeline_v2 import (
    _build_scene_rows,
    _group_rows,
    _row_retrieval_context,
    _score_candidate,
)
from services.visual_requirement import (
    build_visual_requirement,
    candidate_domain_context_pending,
    candidate_requirement_match,
)


SCENES = [
    "Here are the top five best footballers of the current era.",
    "With incredible strength and deadly finishing, Haaland is one of the most feared strikers.",
    "He combines vision, technical ability, and leadership beyond his years.",
    "Ronaldo continues to break records and score goals.",
    "Messi has completed football's greatest collection of achievements.",
]


def test_word_rank_marker_preserves_named_player_containers():
    sections = detect_list_sections(SCENES, "Cristiano Ronaldo FIFA World Cup")
    subjects = [str(row.get("container_subject") or "") for row in sections]
    assert any("Haaland" in subject for subject in subjects)
    assert any("Ronaldo" in subject for subject in subjects)
    assert any("Messi" in subject for subject in subjects)


def test_mechanical_fallback_does_not_flatten_listicle_into_global_pool():
    bundle = app._generate_mechanical_shared_scene_bundle(
        "", "Cristiano Ronaldo FIFA World Cup", SCENES
    )
    rows = _build_scene_rows(
        "Cristiano Ronaldo FIFA World Cup", SCENES, bundle["yt"]
    )
    groups = _group_rows(rows)
    assert any("haaland" in key for key in groups)
    assert any("messi" in key for key in groups)
    # The fallback must produce more than one retrieval pool for a ranking.
    assert len(groups) >= 3


def test_distinct_scene_subject_is_hard_rejected_before_download():
    bundle = app._generate_mechanical_shared_scene_bundle(
        "", "Cristiano Ronaldo FIFA World Cup", SCENES
    )
    rows = _build_scene_rows(
        "Cristiano Ronaldo FIFA World Cup", SCENES, bundle["yt"]
    )
    row = next(item for item in rows if "Haaland" in str(item.get("section_subject")))
    context = _row_retrieval_context(row)
    candidate = {
        "title": "Cristiano Ronaldo highlights",
        "channel": "Clean Sports Footage",
        "description": "real match footage",
        "duration": 300,
    }
    score = _score_candidate(
        candidate,
        "Erling Haaland",
        row["scene_text"],
        "Erling Haaland match footage",
        context,
    )
    assert score == -999.0
    assert candidate["v2_reject_reason"] == "scene_subject_mismatch"


def test_prince_charles_namesake_channel_is_rejected_before_ranking():
    """Do not let Pastor Prince Charles outrank royal archive footage."""
    candidate = {
        "title": "Powerful Fire Prayer | Pastor Prince Charles | RGCI Sialkot",
        "description": "Official church sermon and worship service.",
        "channel": "Elwish Tv",
        "duration": 600,
    }
    context = {
        "container_subject": "Prince Charles",
        "domain_category": "history",
    }
    score = _score_candidate(
        candidate,
        "Prince Charles",
        "Prince Charles public service and royal history",
        "Prince Charles archival footage",
        context,
    )
    assert score == -999.0
    assert candidate["v2_reject_reason"] == "canonical_entity_mismatch"


def test_train_crew_role_title_is_ranked_as_identity_evidence():
    scene = "Before departure, the driver and conductor complete a safety check."
    requirement = build_visual_requirement(
        title="Train Crew: Conductors and Locomotive Engineers",
        script=scene,
        scene_text=scene,
        visual_intent="briefing and safety check",
    )
    row = {
        "section_subject": "Train Crew",
        "scene_subject": "Train Crew",
        "candidate_subject": "Train Crew",
        "title_anchor": "Train Crew",
        "domain_anchor": "Train Crew",
        "container_subject": "Train Crew",
        "visual_requirement": requirement,
    }
    context = _row_retrieval_context(row)
    role_clip = {
        "title": "A Day in the Life of a Locomotive Engineer",
        "description": "Railroad cab and operating footage.",
        "channel": "Norfolk Southern",
        "duration": 198,
    }
    sensational_clip = {
        "title": "This Train Crew PAID the Price for Britain's Biggest Robbery",
        "description": "A sensational railway crime story.",
        "channel": "History Channel",
        "duration": 600,
    }
    role_score = _score_candidate(
        role_clip, "Train Crew", scene,
        "Train Crew conductor locomotive engineer railway operations real footage",
        context,
    )
    sensational_score = _score_candidate(
        sensational_clip, "Train Crew", scene,
        "Train Crew railway crew archival footage",
        context,
    )
    assert role_score > 0
    assert role_score > sensational_score


def test_train_crew_domain_adjacent_metadata_is_pending_not_identity():
    scene = "Before departure, the crew prepares the locomotive and checks the platform."
    requirement = build_visual_requirement(
        title="Train Crew: Conductors and Locomotive Engineers",
        script=scene,
        scene_text=scene,
        visual_intent="rail operations",
    )
    candidate = {
        "title": "This Rail Maintenance Crew Has Plenty on its Plate",
        "description": "Railway operations and maintenance footage.",
        "channel": "Railway Archive",
    }
    matched, reason, _ = candidate_requirement_match(requirement, candidate)
    pending, evidence = candidate_domain_context_pending(requirement, candidate)

    assert not matched
    assert reason == "missing_rail_crew_evidence"
    assert pending
    assert evidence["pending"] is True
    assert evidence["metadata_source"] == "asset"


def test_train_crew_animal_metadata_cannot_enter_pending_recall_lane():
    requirement = build_visual_requirement(
        title="Train Crew: Conductors and Locomotive Engineers",
        script="A documentary about train crew operations.",
        scene_text="A locomotive cab briefing.",
    )
    candidate = {
        "title": "Railway wildlife crew explores a safari train",
        "description": "Animals and wildlife beside a railway.",
    }
    pending, evidence = candidate_domain_context_pending(requirement, candidate)
    assert not pending
    assert evidence["animal_hits"]
