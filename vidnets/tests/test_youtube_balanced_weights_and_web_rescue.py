"""Tests for balanced YouTube scoring, relaxed conflict vetoes, and pre-stock web image rescue."""
import os
from pathlib import Path
from unittest.mock import MagicMock

from services.visual_intelligence.semantic_guard import (
    calculate_youtube_weighted_score,
    explicit_scene_conflict,
)
from services.media_resolution_coordinator import (
    _quota_gate_is_hard_rejection,
    resolve_storyboard_media_v2,
    SceneResolution,
)


def test_calculate_youtube_weighted_score_formula():
    # Both vision and transcript available: 0.40 vision + 0.35 transcript + 0.25 metadata
    # is_relaxed=True adds +5 if vision >= 35.0
    score = calculate_youtube_weighted_score(
        vision_score=80.0,
        vision_available=True,
        transcript_score=60.0,
        transcript_available=True,
        metadata_score=70.0,
        is_relaxed=False,
    )
    expected = 80.0 * 0.40 + 60.0 * 0.35 + 70.0 * 0.25
    assert abs(score - round(expected, 2)) < 0.01

    # In relaxed mode, gets +5 bonus if vision >= 35.0
    relaxed_score = calculate_youtube_weighted_score(
        vision_score=80.0,
        vision_available=True,
        transcript_score=60.0,
        transcript_available=True,
        metadata_score=70.0,
        is_relaxed=True,
    )
    assert abs(relaxed_score - round(expected + 5.0, 2)) < 0.01

    # Vision unavailable: rebalances to transcript 0.55, metadata 0.45
    no_vision = calculate_youtube_weighted_score(
        vision_score=0.0,
        vision_available=False,
        transcript_score=80.0,
        transcript_available=True,
        metadata_score=60.0,
        is_relaxed=True,
    )
    expected_no_vision = 80.0 * 0.55 + 60.0 * 0.45
    assert abs(no_vision - round(expected_no_vision, 2)) < 0.01


def test_explicit_scene_conflict_relaxed_vs_strict():
    # NFL clip describing different team / different matchup
    verdict_spurious = {
        "status": "rejected",
        "fatal_mismatch": True,
        "reason": "Different matchup: Kansas City Chiefs vs Miami Dolphins with quarterback Tua Tagovailoa",
    }
    # Strict mode must flag this as conflict
    assert explicit_scene_conflict(verdict_spurious, relaxed=False) is True
    # Relaxed mode must NOT flag this as conflict
    assert explicit_scene_conflict(verdict_spurious, relaxed=True) is False

    # In-domain wrong entity is strict conflict but relaxed advisory
    verdict_wrong_entity = {
        "status": "rejected",
        "fatal_mismatch": True,
        "reason": "wrong entity: Kansas City Chiefs instead of Baltimore Ravens",
    }
    assert explicit_scene_conflict(verdict_wrong_entity, relaxed=False) is True
    assert explicit_scene_conflict(verdict_wrong_entity, relaxed=True) is False

    # True fatal conflict (wrong domain / different sport)
    verdict_soccer = {
        "status": "rejected",
        "fatal_mismatch": True,
        "reason": "Wrong domain: different_sport soccer match in stadium",
    }
    assert explicit_scene_conflict(verdict_soccer, relaxed=False) is True
    assert explicit_scene_conflict(verdict_soccer, relaxed=True) is True

    # Unsafe content is always fatal
    verdict_unsafe = {
        "unsafe_content": True,
        "reason": "suggestive overlay",
    }
    assert explicit_scene_conflict(verdict_unsafe, relaxed=False) is True
    assert explicit_scene_conflict(verdict_unsafe, relaxed=True) is True


def test_quota_gate_is_hard_rejection_soundness():
    # Spurious matchup difference is not a hard rejection
    assert not _quota_gate_is_hard_rejection(
        "visual_rejected",
        {"fatal_mismatch": True, "reason": "Different matchup: Chiefs vs Dolphins"},
    )
    # Fatal difference is a hard rejection
    assert _quota_gate_is_hard_rejection(
        "visual_rejected",
        {"fatal_mismatch": True, "reason": "different_sport: soccer penalty kick"},
    )
    # Unsafe is always hard rejection
    assert _quota_gate_is_hard_rejection(
        "visual_pass",
        {"unsafe_content": True, "reason": "explicit"},
    )


def test_pre_stock_web_image_rescue(tmp_path):
    web_file = tmp_path / "nfl_web_image.jpg"
    web_file.write_bytes(b"\xFF\xD8\xFF\xE0" + b"\x00" * 2000)

    rows = [{
        "scene_index": 0,
        "scene_id": "scene-0",
        "container_id": "c-0",
        "text": "Kansas City Chiefs offense on the field",
        "duration": 4.0,
    }]

    # Simulate discovery returning a valid web candidate on disk
    web_candidate = {
        "candidate_id": "web-cand-1",
        "path": str(web_file),
        "media_type": "image",
        "source": "web",
        "provider": "web",
        "final_score": 85.0,
        "visual_decision": {
            "accepted": True,
            "status": "accepted",
            "score": 85.0,
        },
    }

    # stock lane that should NOT be called if web rescue works
    stock_called = []
    def mock_stock_lane(scene, event):
        stock_called.append(scene)
        return []

    # web lane returns nothing in initial discovery so scene falls to neutral before stock
    def mock_web_lane(scene, event):
        return []

    def mock_yt_lane(scene, event):
        return []

    # We mock discover or inject discovered candidate
    from unittest.mock import patch

    def mock_discover(index, source, lane):
        if source == "web":
            return index, source, [web_candidate], "ok"
        return index, source, [], "empty"

    with patch("services.media_resolution_coordinator.ThreadPoolExecutor") as mock_tpe:
        # Run resolution with web image available
        pass

    # Direct test on resolve_storyboard_media_v2:
    # If initial discovery yields web candidate, coordinator resolves it to web
    res = resolve_storyboard_media_v2(
        scenes=rows,
        youtube_lane=lambda s, e: [],
        web_lane=lambda s, e: [web_candidate],
        stock_lane=mock_stock_lane,
        max_workers=1,
        deadline_seconds=10.0,
    )
    # Must be resolved to web, NOT stock or neutral
    assert len(res) == 1
    assert res[0].provider == "web"
    assert res[0].status == "accepted"
    assert len(stock_called) == 0


def test_pre_stock_web_image_rescue_when_initial_empty(tmp_path):
    web_file = tmp_path / "nfl_rescue_image.jpg"
    web_file.write_bytes(b"\xFF\xD8\xFF\xE0" + b"\x00" * 2000)

    rows = [{
        "scene_index": 0,
        "scene_id": "scene-0",
        "container_id": "c-0",
        "text": "Kansas City Chiefs offense on the field",
        "duration": 4.0,
    }]

    web_candidate = {
        "candidate_id": "web-cand-rescue",
        "path": str(web_file),
        "media_type": "image",
        "source": "web",
        "provider": "web",
        "final_score": 90.0,
        "visual_decision": {
            "accepted": True,
            "status": "accepted",
            "score": 90.0,
        },
    }

    stock_called = []
    def mock_stock_lane(scene, event):
        stock_called.append(scene)
        return []

    class MockBatchWebLane:
        def __init__(self):
            self.shared_batch = False
            self.call_count = 0

        def __call__(self, scene, event):
            # Discovery returns empty so scene falls through to neutral
            return []

        def get_row(self, index, timeout=0.0):
            self.call_count += 1
            if self.call_count < 4:
                return []
            return [web_candidate]

    lane = MockBatchWebLane()
    res = resolve_storyboard_media_v2(
        scenes=rows,
        youtube_lane=lambda s, e: [],
        web_lane=lane,
        stock_lane=mock_stock_lane,
        max_workers=1,
        deadline_seconds=10.0,
    )
    # The pre-stock rescue pass must retrieve web_candidate via get_row and rescue scene to web!
    assert len(res) == 1
    assert res[0].provider == "web"
    assert res[0].status == "accepted"
    assert res[0].resolution_kind == "pre_stock_web_rescue"
    assert len(stock_called) == 0


def test_score_verdict_relaxed_mode_normalizes_entity_difference():
    import sys
    if "server" not in sys.path:
        sys.path.insert(0, "server")
    from services.pipeline_v2_media_adapter import _score_verdict

    scene = {
        "text": "The Baltimore Ravens take the field for kickoff",
        "source_policy": {"youtube_relaxed_visual_gate": True},
    }
    verdict = {
        "accepted": True,
        "fatal_mismatch": True,
        "reason": "wrong entity: Kansas City Chiefs instead of Baltimore Ravens",
        "domain_match": True,
        "identity_score": 75.0,
        "context_score": 85.0,
        "final_score": 80.0,
    }
    res = _score_verdict(scene, verdict)
    assert res.get("spurious_mismatch") is True
    assert res.get("fatal_mismatch") is False
    assert res.get("status") != "rejected"
    assert res.get("rejection_reason") != "explicit_scene_conflict"


def test_score_verdict_strict_mode_rejects_domain_conflict():
    import sys
    if "server" not in sys.path:
        sys.path.insert(0, "server")
    from services.pipeline_v2_media_adapter import _score_verdict

    scene = {
        "text": "The Baltimore Ravens score a touchdown in the NFL",
        "source_policy": {"youtube_relaxed_visual_gate": True},
    }
    verdict = {
        "accepted": True,
        "fatal_mismatch": True,
        "reason": "wrong domain: different_sport soccer match with ball in net",
        "domain_match": False,
        "identity_score": 20.0,
        "context_score": 20.0,
        "final_score": 20.0,
    }
    res = _score_verdict(scene, verdict)
    assert res.get("accepted") is False
    assert res.get("status") == "rejected"
    assert res.get("rejection_reason") == "explicit_scene_conflict"


def test_pre_stock_web_image_rescue_cross_scene(tmp_path):
    web_file = tmp_path / "nfl_scene1_image.jpg"
    web_file.write_bytes(b"\xFF\xD8\xFF\xE0" + b"\x00" * 2000)

    rows = [
        {
            "scene_index": 0,
            "scene_id": "scene-0",
            "container_id": "c-0",
            "text": "The NFL kickoff begins",
            "duration": 4.0,
        },
        {
            "scene_index": 1,
            "scene_id": "scene-1",
            "container_id": "c-0",
            "text": "Kansas City Chiefs offense on the field",
            "duration": 4.0,
        },
    ]

    web_candidate = {
        "candidate_id": "web-cand-cross",
        "path": str(web_file),
        "media_type": "image",
        "source": "web",
        "provider": "web",
        "final_score": 90.0,
        "visual_decision": {
            "accepted": True,
            "status": "accepted",
            "score": 90.0,
        },
    }

    stock_called = []
    def mock_stock_lane(scene, event):
        stock_called.append(scene)
        return []

    class MockBatchWebLane:
        def __init__(self):
            self.shared_batch = False

        def __call__(self, scene, event):
            idx = int(scene.get("scene_index", 0))
            if idx == 1:
                return [web_candidate]
            return []

        def get_row(self, index, timeout=0.0):
            if index == 1:
                return [web_candidate]
            return []

    lane = MockBatchWebLane()
    res = resolve_storyboard_media_v2(
        scenes=rows,
        youtube_lane=lambda s, e: [],
        web_lane=lane,
        stock_lane=mock_stock_lane,
        max_workers=1,
        deadline_seconds=10.0,
    )
    assert len(res) == 2
    assert res[0].provider == "web"
    assert res[1].provider == "web"
    assert len(stock_called) == 0

