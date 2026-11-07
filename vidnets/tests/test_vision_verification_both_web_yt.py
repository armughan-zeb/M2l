import os
import sys
from pathlib import Path
from unittest import mock
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services.visual_intelligence.context import VisualDecisionContext
from services.pipeline_v2_media_adapter import build_qwen_verifier, _validate_qwen_verdict_schema
from services.media_resolution_coordinator import _verification_result


def test_visual_decision_context_auto_enables_with_key():
    with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test-env-key"}, clear=True):
        ctx = VisualDecisionContext.from_config({}, scene_count=3)
        assert ctx.enabled is True
        assert ctx.api_key == "sk-or-test-env-key"

    # Explicit opt-out is respected
    ctx_opt_out = VisualDecisionContext.from_config(
        {"visual_understanding": False, "openrouter_api_key": "sk-key"},
        scene_count=3,
    )
    assert ctx_opt_out.enabled is False


def test_build_qwen_verifier_forces_live_verification_for_both_web_and_yt(tmp_path):
    class SpyVisionClient:
        model = "test-qwen"

        def __init__(self):
            self.image_calls = []
            self.clip_calls = []

        def analyze_image(self, path, prompt, system):
            self.image_calls.append((path, prompt))
            return {
                "candidate_id": str(path),
                "entity_conflict": False,
                "identity_score": 92.0,
                "action_score": 88.0,
                "context_score": 90.0,
                "quality": 85.0,
                "visible_overlays": [],
                "subject_box": {},
                "safe_text_regions": [],
                "fatal_mismatch": False,
                "reason": "Prince Harry and Meghan Markle clearly visible.",
            }

        def analyze_clip_frames(self, frames, prompt, system):
            self.clip_calls.append((frames, prompt))
            return {
                "candidate_id": "youtube:clip_0",
                "entity_conflict": False,
                "identity_score": 95.0,
                "action_score": 85.0,
                "context_score": 92.0,
                "quality": 88.0,
                "visible_overlays": [],
                "subject_box": {},
                "safe_text_regions": [],
                "fatal_mismatch": False,
                "reason": "Authentic royal wedding footage of Prince Harry.",
            }

    spy = SpyVisionClient()
    web_file = tmp_path / "harry.jpg"
    web_file.write_bytes(b"image-bytes")

    yt_file = tmp_path / "harry_clip.mp4"
    yt_file.write_bytes(b"fake-mp4-data")

    context = VisualDecisionContext.from_config(
        {"visual_understanding": True, "openrouter_api_key": "sk-test"},
        scene_count=2,
    )
    context._client = object()
    context.get_client = lambda: spy

    # Ordinary narrative scene (strict_identity is False)
    scene = {
        "text": "Prince Harry and Meghan Markle celebrated their royal wedding at Windsor.",
        "positive_subjects": ["Prince Harry", "Meghan Markle"],
        "visual_requirement": {
            "strict_identity": False,
            "canonical_entity": "Prince Harry",
        },
    }

    with mock.patch("services.pipeline_v2_media_adapter.VisualDecisionContext.from_config", return_value=context):
        verifier, _ = build_qwen_verifier(
            {"visual_understanding": True, "openrouter_api_key": "sk-test"},
            [scene],
        )

    # 1. Verify Web Image candidate
    web_candidate = {
        "candidate_id": str(web_file),
        "path": str(web_file),
        "source": "web",
        "media_type": "image",
        "qwen_result": {
            "source": "web_image",
            "verification_status": "deterministic_web_image",
            "accepted": True,
        },
    }
    web_res = verifier(scene, web_candidate)
    assert len(spy.image_calls) == 1
    assert web_res["accepted"] is True
    assert web_res["verification_attempt"] == "primary"
    assert web_res["visual_checked"] is True

    # 2. Verify YouTube Clip candidate
    dummy_frame = tmp_path / "frame_0.jpg"
    dummy_frame.write_bytes(b"frame-bytes")

    with mock.patch("services.visual_intelligence.frame_extractor.extract_clip_frames", return_value=([str(dummy_frame)], 5.0)):
        yt_candidate = {
            "candidate_id": "youtube:clip_0",
            "path": str(yt_file),
            "source": "youtube",
            "media_type": "video",
            "qwen_result": {
                "source": "youtube_pool_moment",
                "verification_status": "deterministic_pool_cut",
                "visual_available": False,
                "accepted": True,
            },
        }
        yt_res = verifier(scene, yt_candidate)
        assert len(spy.clip_calls) == 1
        assert yt_res["accepted"] is True
        assert yt_res["verification_attempt"] == "primary"
        assert yt_res["visual_checked"] is True


def test_validate_qwen_verdict_schema_handles_windows_slashes():
    candidate = {"path": "C:\\temp\\images\\photo.jpg", "candidate_id": "C:\\temp\\images\\photo.jpg"}
    # Model returns forward slashes
    verdict = {
        "candidate_id": "C:/temp/images/photo.jpg",
        "entity_conflict": False,
        "identity_score": 90.0,
        "action_score": 80.0,
        "context_score": 85.0,
        "quality": 80.0,
        "visible_overlays": [],
        "subject_box": {},
        "safe_text_regions": [],
    }
    valid, res, errors = _validate_qwen_verdict_schema(candidate, verdict)
    assert valid is True
    assert "candidate_id_mismatch" not in errors

    # Model returns basename
    verdict_base = dict(verdict, candidate_id="photo.jpg")
    valid_b, res_b, errors_b = _validate_qwen_verdict_schema(candidate, verdict_base)
    assert valid_b is True
    assert "candidate_id_mismatch" not in errors_b


def test_coordinator_enforces_vision_veto_for_fatal_mismatches():
    scene = {
        "text": "Prince Harry visits the Invictus Games competitors.",
        "positive_subjects": ["Prince Harry"],
        "visual_requirement": {"canonical_entity": "Prince Harry"},
    }

    mismatched_verdict = {
        "candidate_id": "yt:1",
        "entity_conflict": True,
        "fatal_mismatch": True,
        "identity_score": 10.0,
        "action_score": 75.0,
        "context_score": 30.0,
        "final_score": 25.0,
        "accepted": False,
        "reason": "This is video game footage of Harry Potter, not Prince Harry.",
    }

    def verifier(_sc, _cand):
        return mismatched_verdict

    candidate = {
        "candidate_id": "yt:1",
        "path": "C:\\temp\\potter_gameplay.mp4",
        "source": "youtube",
        "media_type": "video",
        "overlay_clean": True,
    }

    verified, reason, score, outcome = _verification_result(scene, candidate, verifier)
    assert verified is False
    assert outcome["fatal_mismatch"] is True


def test_coordinator_rejects_outro_detected_and_blur_failure():
    scene = {
        "text": "Prince Harry and his family live in California.",
        "positive_subjects": ["Prince Harry"],
        "visual_requirement": {"canonical_entity": "Prince Harry"},
        "source_policy": {"youtube_quota": True, "youtube_relaxed": True},
    }

    # 1. Candidate with outro_detected = True
    outro_cand = {
        "candidate_id": "yt:outro_1",
        "path": "C:\\temp\\outro.mp4",
        "source": "youtube",
        "media_type": "video",
        "overlay_clean": True,
        "outro_detected": True,
        "rejection_reason": "outro_endcard_rejected:high_white_outro_slate_ratio_0.49",
    }
    def dummy_verifier(_sc, _cand):
        return {"accepted": False, "reason": "outro_endcard_rejected"}

    verified, reason, score, outcome = _verification_result(scene, outro_cand, dummy_verifier)
    assert verified is False

    # 2. Candidate with blur_failed = True
    blur_failed_cand = {
        "candidate_id": "yt:blur_fail",
        "path": "C:\\temp\\clip.mp4",
        "source": "youtube",
        "media_type": "video",
        "overlay_clean": True,
        "blur_failed": True,
        "rejection_reason": "overlay_blur_failed",
    }
    verified_bf, _, _, _ = _verification_result(scene, blur_failed_cand, dummy_verifier)
    assert verified_bf is False


def test_detect_outro_endcard_identifies_high_contrast_cards(tmp_path):
    import numpy as np
    import cv2
    from services.scene_understanding.analyzers import detect_outro_endcard

    # Create a frame that is predominantly white (studio outro card)
    white_frame = np.ones((100, 100, 3), dtype=np.uint8) * 245
    white_path = str(tmp_path / "white.jpg")
    cv2.imwrite(white_path, white_frame)

    res = detect_outro_endcard([white_path])
    assert res["outro_detected"] is True
    assert "white_outro_slate" in res["outro_reason"]

    # Create a normal frame (mid tones)
    normal_frame = np.ones((100, 100, 3), dtype=np.uint8) * 120
    normal_path = str(tmp_path / "normal.jpg")
    cv2.imwrite(normal_path, normal_frame)

    res_norm = detect_outro_endcard([normal_path])
    assert res_norm["outro_detected"] is False

