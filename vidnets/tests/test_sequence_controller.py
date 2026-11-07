"""Unit tests for SequenceController and documentary sequence continuity audit.

Verifies:
1. Rolling 60s cooldown per composition family.
2. Prevention of adjacent identical compositions (even with different variants).
3. Intentional continuity exception allowing multi-step charts.
4. Quadrant alternation across corner compositions.
5. Hero/center graphic limit of max 2 consecutive beats.
6. Max 2 font families per video rule in visual DNA.
7. Dynamic pacing curve classification (tension vs exhale).
"""

from __future__ import annotations

import pytest
from server.services.editorial_director.sequence_controller import (
    CORNER_QUADRANTS,
    DEFAULT_FAMILY_COOLDOWN_SECONDS,
    MAX_CONSECUTIVE_HERO_BEATS,
    SequenceController,
    audit_sequence_continuity,
)
from server.services.editorial_director.media_director import plan_media_composition
from server.services.editorial_director.media_first import apply_media_first_direction


def test_rolling_60s_cooldown_blocks_early_reuse():
    controller = SequenceController(cooldown_seconds=60.0)

    # Scene 0 at t=0s, duration=5s: uses locator_route
    scene0 = {"scene_index": 0, "duration": 5.0}
    can_use0, reason0 = controller.can_use_family("locator_route")
    assert can_use0 is True
    controller.register_scene_contract(scene0, {"family": "locator_route", "enabled": True})

    # Scene 1 at t=5s, duration=5s: uses numeric_emphasis
    scene1 = {"scene_index": 1, "duration": 5.0}
    can_use1, _ = controller.can_use_family("numeric_emphasis")
    assert can_use1 is True
    controller.register_scene_contract(scene1, {"family": "numeric_emphasis", "enabled": True})

    # Scene 2 at t=10s: tries to reuse locator_route (only 5s elapsed since scene 0 ended at 5s)
    can_use2, reason2 = controller.can_use_family("locator_route")
    assert can_use2 is False
    assert "cooldown_locator_route" in reason2

    # Fast-forward time to t=66s:
    controller.current_time = 66.0
    can_use_later, _ = controller.can_use_family("locator_route")
    assert can_use_later is True


def test_adjacent_identical_composition_forbidden():
    controller = SequenceController()

    scene0 = {"scene_index": 0, "duration": 4.0}
    controller.register_scene_contract(scene0, {"family": "corner_identification", "enabled": True})

    # Scene 1 immediately attempts corner_identification
    can_use, reason = controller.can_use_family("corner_identification")
    assert can_use is False
    assert reason == "adjacent_repeat_corner_identification"


def test_intentional_continuity_bypasses_adjacent_block():
    controller = SequenceController()

    scene0 = {"scene_index": 0, "duration": 4.0}
    controller.register_scene_contract(
        scene0,
        {"family": "data_chart", "enabled": True, "continuity_exception": "quarterly_breakdown"},
    )

    # Scene 1 continues the intentional chart sequence
    can_use, reason = controller.can_use_family("data_chart", continuity_group="quarterly_breakdown")
    assert can_use is True
    assert reason == "eligible"


def test_center_hero_limit_max_two_consecutive_beats():
    controller = SequenceController()

    # Beat 1: Center
    scene0 = {"scene_index": 0, "duration": 4.0}
    c0 = controller.register_scene_contract(scene0, {"family": "keyword_emphasis", "enabled": True, "preferred_placement": "center"})
    assert c0["anchor"] == "center"

    # Beat 2: Center
    scene1 = {"scene_index": 1, "duration": 4.0}
    c1 = controller.register_scene_contract(scene1, {"family": "editorial_statement", "enabled": True, "preferred_placement": "center"})
    assert c1["anchor"] == "center"

    # Beat 3: Requests Center, but limit is 2 -> must degrade to corner quadrant
    scene2 = {"scene_index": 2, "duration": 4.0}
    anchor2 = controller.select_anchor("center", scene2, is_center_eligible=True)
    assert anchor2 in CORNER_QUADRANTS
    assert anchor2 != "center"


def test_corner_quadrants_alternate():
    controller = SequenceController()

    anchors = []
    for i in range(4):
        scene = {"scene_index": i, "duration": 3.0}
        anchor = controller.select_anchor("corner", scene, is_center_eligible=False)
        anchors.append(anchor)
        controller.recent_anchors.append(anchor)

    # Verify no two consecutive corner anchors are identical
    for i in range(len(anchors) - 1):
        assert anchors[i] != anchors[i + 1]


def test_audit_flags_font_family_excess():
    # Compliant: 2 fonts (Plus Jakarta Sans, Inter)
    compliant_manifest = {
        "visual_dna": {
            "typography": {
                "display": "Plus Jakarta Sans",
                "body": "Inter",
                "numbers": "Inter",
            }
        },
        "scenes": [
            {"scene_index": 0, "duration": 4.0, "editorial_contract": {"family": "corner_identification", "enabled": True}},
        ],
    }
    report1 = audit_sequence_continuity(compliant_manifest)
    assert report1["status"] == "pass"
    assert len(report1["font_families"]) == 2

    # Non-compliant: 3 fonts (Archivo Black, Inter, JetBrains Mono)
    non_compliant_manifest = {
        "visual_dna": {
            "typography": {
                "display": "Archivo Black",
                "body": "Inter",
                "numbers": "JetBrains Mono",
            }
        },
        "scenes": [
            {"scene_index": 0, "duration": 4.0, "editorial_contract": {"family": "corner_identification", "enabled": True}},
        ],
    }
    report2 = audit_sequence_continuity(non_compliant_manifest)
    assert report2["status"] == "fail"
    assert any("maximum allowed is 2" in err for err in report2["errors"])


def test_dynamic_pacing_classification():
    from server.services.editorial_director.sequence_controller import _scene_pacing_mode

    tension_scene = {"narration": "Suddenly chaos erupted as the crisis deepened under intense pressure."}
    assert _scene_pacing_mode(tension_scene) == "tension"

    exhale_scene = {"narration": "Meanwhile, the vast calm landscape remains silent years later."}
    assert _scene_pacing_mode(exhale_scene) == "exhale"

    neutral_scene = {"narration": "The board met at ten o'clock to review the quarterly revenue figures."}
    assert _scene_pacing_mode(neutral_scene) == "neutral"
