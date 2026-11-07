import json
import hashlib
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))


def test_auto_mixed_plan_exposes_both_primary_discovery_lanes():
    from services.mixed_media_planner import build_adaptive_mixed_media_plan

    scenes = [
        "Workers run through the factory as machinery starts",
        "A historical photograph identifies the original inventor",
    ]
    plan = build_adaptive_mixed_media_plan(
        scenes,
        title="Factory history",
        scene_media_mode="auto",
    )

    assert plan.source_strategy == "preference"
    assert plan.discovery_indices["YouTube Clips"] == [0, 1]
    assert plan.discovery_indices["Web Images"] == [0, 1]
    assert plan.decisions[0].preferred_sources[0] == "YouTube Clips"
    assert plan.decisions[1].preferred_sources[0] == "Web Images"
    assert all(set(decision.primary_discovery_sources) >= {"YouTube Clips", "Web Images"} for decision in plan.decisions)


def test_web_scene_order_is_container_round_robin():
    from services.web_image.source_broker import fair_round_robin_indices

    assert fair_round_robin_indices(["item-a", "item-a", "item-b", "item-b", "item-c"]) == [0, 2, 4, 1, 3]


def test_transcript_negative_cache_expires_but_ready_cache_survives(tmp_path, monkeypatch):
    from services.youtube_transcript_index import (
        TranscriptDocument,
        YouTubeTranscriptIndex,
        normalize_segments,
    )

    cache = YouTubeTranscriptIndex(str(tmp_path / "transcripts.sqlite3"), provider=lambda _video: [])
    monkeypatch.setenv("VIDZEO_YT_TRANSCRIPT_MISSING_TTL_SECONDS", "1")
    monkeypatch.setenv("VIDZEO_YT_TRANSCRIPT_SUCCESS_TTL_SECONDS", "1000")
    cache.put(TranscriptDocument(video_id="missing", status="missing", fetched_at=time.time() - 10))
    cache.put(TranscriptDocument(
        video_id="ready",
        status="ready",
        fetched_at=time.time() - 10,
        segments=normalize_segments([{"start": 1, "duration": 2, "text": "RMS Titanic"}]),
    ))

    assert cache.get("missing") is None
    assert cache.get("ready").available


def test_transcript_moments_never_cross_container_pool_keys():
    from services.youtube_moment_matcher import match_pool_moments
    from services.youtube_transcript_index import TranscriptDocument, normalize_segments

    documents = {
        "welder-video": TranscriptDocument(
            video_id="welder-video", status="ready",
            segments=normalize_segments([{"start": 10, "duration": 5, "text": "Deep sea welder repairs an underwater pipeline"}]),
        ),
        "cleaner-video": TranscriptDocument(
            video_id="cleaner-video", status="ready",
            segments=normalize_segments([{"start": 20, "duration": 5, "text": "High rise window cleaner works on a skyscraper"}]),
        ),
    }
    metadata = {
        "welder-video": {"video_id": "welder-video", "pool_keys": ["deep sea welder"]},
        "cleaner-video": {"video_id": "cleaner-video", "pool_keys": ["window cleaner"]},
    }
    result = match_pool_moments(
        documents,
        [{
            "scene_index": 0,
            "pool_key": "window cleaner",
            "container_id": "window cleaner",
            "subject": "High rise window cleaner",
            "scene_text": "The cleaner works on a skyscraper",
            "duration": 4,
        }],
        source_metadata=metadata,
    )

    assert result[0]
    assert {moment.source_id for moment in result[0]} == {"cleaner-video"}


def test_transcript_normalization_preserves_non_latin_words():
    from services.youtube_transcript_index import normalize_text
    from services.youtube_moment_matcher import _tokens

    assert normalize_text("یہ ایک تاریخی مقام ہے۔") == "یہ ایک تاریخی مقام ہے"
    assert "تاریخی" in _tokens("یہ ایک تاریخی مقام ہے")


def test_stock_policy_caps_and_accuracy_gate(monkeypatch):
    from services import real_media_service as stock

    assert 1 <= stock._STOCK_SCENE_WORKERS <= 6
    assert 1 <= stock._MAX_PROVIDER_CONCURRENT_REQUESTS <= 4
    assert 1 <= stock.MediaConfig().max_concurrent_downloads <= 4
    assert stock.stock_scene_allowed({"source_policy": {"stock_allowed": False}}) == (
        False, "contract_forbids_stock"
    )

    candidate = stock.MediaItem(
        id="1", source="pexels", type="video", title="Generic office",
        description="", tags=[], download_url="https://example.test/1.mp4",
        preview_url="", width=1920, height=1080, score=80,
        score_breakdown={"relevance": 0},
    )
    passed, reason = stock._stock_candidate_passes_accuracy_gate(candidate, stock.MediaConfig())
    assert not passed
    assert reason.startswith("relevance_below_floor")

    first_scope = stock._stock_resolution_cache_scope(
        0, {"scene_id": "same", "contract_hash": "same", "_stock_run_scope": "job-a"}, "office", "pexels",
    )
    second_scope = stock._stock_resolution_cache_scope(
        0, {"scene_id": "same", "contract_hash": "same", "_stock_run_scope": "job-b"}, "office", "pexels",
    )
    assert first_scope != second_scope


def test_youtube_default_run_directories_do_not_collide_between_jobs():
    from services.youtube_pipeline_v2 import _new_job_run_id

    assert _new_job_run_id("v2", "same title") != _new_job_run_id("v2", "same title")


def test_qwen_v2_defaults_and_per_scene_budget():
    from services.visual_intelligence.context import VisualDecisionContext, default_visual_call_cap
    from services.visual_intelligence.vision_client import DEFAULT_QWEN_VISION_MODEL

    assert default_visual_call_cap(1) == 8
    assert default_visual_call_cap(20) == 25
    context = VisualDecisionContext.from_config(
        {
            "pipeline_v2_mode": "on",
            "visual_understanding": True,
            "openrouter_api_key": "test",
            "qwen_vision_model": DEFAULT_QWEN_VISION_MODEL,
        },
        20,
    )
    assert context.max_calls == 25
    assert context.model == DEFAULT_QWEN_VISION_MODEL
    assert context.fail_closed


def test_atomic_media_promotion_requires_ffprobe_and_same_job_directory(tmp_path):
    from services import media_integrity

    staged = tmp_path / "scene.part.mp4"
    final = tmp_path / "scene.mp4"
    staged.write_bytes(b"video" * 2048)
    payload = {
        "format": {"duration": "4.0"},
        "streams": [{"index": 0, "codec_type": "video", "width": 1280, "height": 720, "avg_frame_rate": "30/1"}],
    }
    completed = SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
    with mock.patch.object(media_integrity.subprocess, "run", return_value=completed):
        result = media_integrity.promote_verified_media(staged, final)
    assert result.valid
    assert result.path == str(final.resolve())
    assert final.exists() and not staged.exists()


def test_atomic_media_promotion_rejects_duration_outside_one_frame(tmp_path):
    from services import media_integrity

    staged = tmp_path / "scene.part.mp4"
    final = tmp_path / "scene.mp4"
    staged.write_bytes(b"video" * 2048)
    payload = {
        "format": {"duration": "3.8"},
        "streams": [{"index": 0, "codec_type": "video", "width": 1280, "height": 720, "avg_frame_rate": "30/1"}],
    }
    completed = SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
    with mock.patch.object(media_integrity.subprocess, "run", return_value=completed):
        result = media_integrity.promote_verified_media(
            staged,
            final,
            expected_duration=4.0,
            duration_tolerance=1 / 30,
            expected_width=1280,
            expected_height=720,
        )
    assert not result.valid
    assert result.reason == "duration_mismatch"
    assert staged.exists() and not final.exists()


def test_storyboard_media_coordinator_is_container_fair_and_stock_is_last():
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    calls = []
    stock_calls = []
    scenes = [
        {"scene_id": "a1", "order": 0, "container_id": "a", "text": "Action starts", "source_policy": {"stock_allowed": False, "visual_verification_required": True}},
        {"scene_id": "a2", "order": 1, "container_id": "a", "text": "Action continues", "source_policy": {"stock_allowed": False, "visual_verification_required": True}},
        {"scene_id": "b1", "order": 2, "container_id": "b", "text": "A document appears", "source_policy": {"stock_allowed": True, "visual_verification_required": True}},
    ]

    def youtube(scene, _cancel):
        calls.append(("youtube", scene["scene_id"]))
        if scene["scene_id"] == "b1":
            return []
        return {"path": f"{scene['scene_id']}.mp4", "source": "youtube", "container_id": scene["container_id"], "score": 80}

    def web(scene, _cancel):
        calls.append(("web", scene["scene_id"]))
        if scene["scene_id"] == "b1":
            return []
        return {"path": f"{scene['scene_id']}.jpg", "source": "web", "container_id": scene["container_id"], "score": 70}

    def stock(scene, _cancel):
        stock_calls.append(scene["scene_id"])
        return {"path": "b1-stock.mp4", "source": "stock", "container_id": "b", "score": 75}

    def verify(_scene, candidate):
        return {"accepted": True, "relevance": candidate["score"], "entity_conflict": False}

    result = resolve_storyboard_media_v2(
        scenes,
        youtube_lane=youtube,
        web_lane=web,
        stock_lane=stock,
        visual_verifier=verify,
    )

    first_a2 = min(index for index, (_source, scene_id) in enumerate(calls) if scene_id == "a2")
    assert all(
        min(index for index, (_source, seen_id) in enumerate(calls) if seen_id == scene_id) < first_a2
        for scene_id in ("a1", "b1")
    )
    assert result[0].status == "accepted"
    assert result[0].provider == "youtube"
    assert result[2].provider == "stock"
    assert stock_calls == ["b1"]


def test_storyboard_media_coordinator_rejects_cross_container_candidate():
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    scene = {
        "scene_id": "item-2-scene", "order": 0, "container_id": "item-2",
        "text": "Item two", "source_policy": {"stock_allowed": False, "visual_verification_required": True},
    }
    result = resolve_storyboard_media_v2(
        [scene],
        youtube_lane=lambda _scene, _cancel: {"path": "wrong.mp4", "source": "youtube", "container_id": "item-1"},
        web_lane=lambda _scene, _cancel: [],
        visual_verifier=lambda _scene, _candidate: {"accepted": True, "relevance": 99},
    )
    assert result[0].provider == "neutral"
    assert result[0].fallback_reason == "degraded:no_verified_media"
    assert result[0].status == "degraded"
    assert any(row.get("status") == "container_mismatch" for row in result[0].attempts)


def test_stock_is_invoked_only_as_the_final_real_media_layer():
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    calls = []
    scenes = [
        {
            "scene_id": "subject-0",
            "order": 0,
            "container_id": "subject",
            "text": "The named subject appears.",
            "positive_subjects": ["named subject"],
            "source_policy": {"stock_last_resort_allowed": True, "visual_verification_required": True},
        },
        {
            "scene_id": "subject-1",
            "order": 1,
            "container_id": "subject-next",
            "text": "The subject's next detail.",
            "positive_subjects": ["named subject"],
            "source_policy": {"stock_last_resort_allowed": True, "visual_verification_required": True},
        },
    ]

    def youtube(scene, _cancel):
        if scene["order"] == 0:
            return {"path": "verified-source.mp4", "source": "youtube", "container_id": "subject", "score": 90}
        return []

    def stock(scene, _cancel):
        calls.append(scene["scene_id"])
        return {"path": "final-stock.mp4", "source": "stock", "container_id": scene["container_id"], "score": 85}

    result = resolve_storyboard_media_v2(
        scenes,
        youtube_lane=youtube,
        web_lane=lambda _scene, _cancel: [],
        stock_lane=stock,
        visual_verifier=lambda _scene, _candidate: {"accepted": True, "relevance": 95},
        neutral_factory=lambda _scene: None,
    )

    assert result[1].provider == "stock"
    assert result[1].resolution_kind == "stock_last_resort"
    assert result[1].fallback_reason == "stock_last_resort_after_all_real_media_layers"
    assert calls == ["subject-1"]
    stages = [row.get("stage") for row in result[1].attempts]
    assert stages.index("stock_last_resort") > stages.index("discovery_result")


def test_stock_terminal_fallback_can_skip_vision_after_all_factual_lanes_fail():
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    scene = {
        "scene_id": "terminal-stock",
        "order": 0,
        "container_id": "terminal-stock",
        "text": "A broad documentary scene",
        "source_policy": {
            "stock_last_resort_allowed": True,
            "stock_last_resort_no_vision": True,
            "visual_verification_required": True,
        },
    }

    def should_not_run(_scene, _candidate):
        raise AssertionError("terminal stock fallback must not call vision")

    result = resolve_storyboard_media_v2(
        [scene],
        youtube_lane=lambda _scene, _cancel: [],
        web_lane=lambda _scene, _cancel: [],
        stock_lane=lambda _scene, _cancel: {
            "path": "terminal-stock.jpg",
            "source": "stock",
            "container_id": "terminal-stock",
            "score": 80,
        },
        visual_verifier=should_not_run,
        neutral_factory=lambda _scene: None,
    )

    assert result[0].provider == "stock"
    assert result[0].verification_status == "deterministic_stock_last_resort"
    assert result[0].visual_decision["visual_verification_skipped"] is True
    assert any(
        row.get("stage") == "stock_last_resort_visual_gate"
        and row.get("status") == "stock_last_resort_visual_skipped"
        for row in result[0].attempts
    )


def test_irrelevant_stock_is_not_used_as_a_last_resort():
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    scene = {
        "scene_id": "jaguar",
        "order": 0,
        "container_id": "jaguar",
        "text": "The Jaguar E-Type automobile enters the frame.",
        "positive_subjects": ["Jaguar E-Type automobile"],
        "source_policy": {"stock_last_resort_allowed": True, "visual_verification_required": True},
    }

    result = resolve_storyboard_media_v2(
        [scene],
        youtube_lane=lambda _scene, _cancel: [],
        web_lane=lambda _scene, _cancel: [],
        stock_lane=lambda _scene, _cancel: {
            "path": "wild-jaguar.mp4",
            "source": "stock",
            "container_id": "jaguar",
            "title": "Jaguar animal in the wild",
        },
        visual_verifier=lambda _scene, _candidate: {
            "accepted": False,
            "entity_conflict": True,
            "reason": "wildlife is not the automobile",
        },
        neutral_factory=lambda _scene: None,
    )

    assert result[0].provider == "neutral"
    assert result[0].resolution_kind == "neutral_no_claim"
    assert any(
        row.get("stage") == "stock_last_resort_hard_gate"
        and row.get("status") == "forbidden_confusable_entity"
        for row in result[0].attempts
    )


def test_youtube_high_identity_does_not_override_severe_overlay():
    from services.media_resolution_coordinator import _verification_result

    scene = {
        "text": "The Eiffel Tower was built from riveted iron.",
        "source_policy": {"youtube_relaxed_visual_gate": True, "visual_verification_required": True},
        "visual_requirement": {"canonical_entity": "Eiffel Tower", "strict_identity": True},
    }
    candidate = {
        "source": "youtube", "provider": "youtube", "path": "tower.mp4",
        "overlay_clean": True,
    }

    def verifier(_scene, _candidate):
        return {
            "accepted": False, "status": "rejected", "visual_checked": True,
            "visual_available": True, "identity_score": 95,
            "context_score": 90, "final_score": 90,
            "severe_overlay": True,
            "rejection_reason": "severe_overlay_or_subtitle_detected",
            "reason": "The tower is visible behind a large graphic overlay.",
        }

    accepted, _reason, _score, decision = _verification_result(scene, candidate, verifier)
    assert accepted is False
    assert decision["accepted"] is False


def test_storyboard_media_coordinator_sends_bounded_top_k_finalists_to_qwen(monkeypatch):
    monkeypatch.setenv('VIDZEO_VISUAL_FINALIST_LIMIT', '6')
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    scene = {
        "scene_id": "scene-1", "order": 0, "container_id": "item-1",
        "text": "The spacecraft launches", "source_policy": {"visual_verification_required": True},
    }
    verified = []

    def candidates(source, offset):
        return [
            {
                "candidate_id": f"{source}-{score}",
                "path": f"{source}-{score}.mp4",
                "source": source,
                "container_id": "item-1",
                "score": score + offset,
            }
            for score in (10, 20, 30)
        ]

    def verify(_scene, candidate):
        verified.append(candidate["candidate_id"])
        return {"accepted": True, "relevance": candidate["score"]}

    result = resolve_storyboard_media_v2(
        [scene],
        youtube_lane=lambda _scene, _cancel: candidates("youtube", 1),
        web_lane=lambda _scene, _cancel: candidates("web", 0),
        visual_verifier=verify,
    )

    # The coordinator verifies a bounded top-K set so a high-scoring generic
    # result cannot crowd out a lower-ranked but semantically correct asset.
    assert len(verified) == 6
    assert set(verified) == {
        "youtube-30", "youtube-20", "youtube-10",
        "web-30", "web-20", "web-10",
    }
    assert result[0].candidate_id == "youtube-30"


def test_cross_lane_youtube_fallback_runs_before_graphic(tmp_path):
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    clip = tmp_path / "jaguar-e-type.mp4"
    clip.write_bytes(b"verified clip")
    fallback_calls = []

    scene = {
        "scene_id": "scene-1",
        "order": 0,
        "container_id": "item-1",
        "text": "The 1961 Jaguar E-Type became a landmark sports car.",
        "source_policy": {"visual_verification_required": True},
        "positive_subjects": ["Jaguar E-Type"],
    }

    def youtube_fallback(_scene, _cancel):
        fallback_calls.append(True)
        return {
            "path": str(clip),
            "source": "youtube",
            "provider": "youtube",
            "media_type": "video",
            "title": "1961 Jaguar E-Type road test",
        }

    result = resolve_storyboard_media_v2(
        [scene],
        youtube_lane=lambda _scene, _cancel: None,
        web_lane=lambda _scene, _cancel: [],
        youtube_fallback_lane=youtube_fallback,
        visual_verifier=lambda _scene, _candidate: {
            "accepted": True,
            "status": "accepted",
            "final_score": 94,
        },
        neutral_factory=lambda _scene: pytest.fail("graphic fallback ran before cross-lane recovery"),
    )

    assert fallback_calls == [True]
    assert result[0].provider == "youtube"
    assert result[0].resolution_kind == "cross_lane_youtube_fallback"
    assert result[0].fallback_reason == "cross_lane_youtube"


def test_rejected_youtube_clip_tries_same_video_timestamp_frames(tmp_path, monkeypatch):
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    clip = tmp_path / "jaguar-e-type.mp4"
    clip.write_bytes(b"verified clip")
    frame_one = tmp_path / "frame-1.jpg"
    frame_two = tmp_path / "frame-2.jpg"
    frame_one.write_bytes(b"frame one")
    frame_two.write_bytes(b"frame two")
    monkeypatch.setattr(
        "services.visual_intelligence.frame_extractor.extract_clip_frames",
        lambda *_args, **_kwargs: ([str(frame_one), str(frame_two)], 8.0),
    )
    verified_ids = []

    scene = {
        "scene_id": "scene-1",
        "order": 0,
        "container_id": "item-1",
        "text": "The 1961 Jaguar E-Type became a landmark sports car.",
        "source_policy": {"visual_verification_required": True},
        "positive_subjects": ["Jaguar E-Type"],
    }
    candidate = {
        "path": str(clip),
        "source": "youtube",
        "provider": "youtube",
        "media_type": "video",
        "title": "1961 Jaguar E-Type road test",
    }

    def verify(_scene, row):
        candidate_id = str(row.get("candidate_id") or "")
        verified_ids.append(candidate_id)
        if ":frame:" in candidate_id:
            return {"accepted": True, "status": "accepted", "final_score": 92}
        return {"accepted": False, "status": "rejected", "reason": "opening frame mismatch"}

    result = resolve_storyboard_media_v2(
        [scene],
        youtube_lane=lambda _scene, _cancel: candidate,
        web_lane=lambda _scene, _cancel: [],
        visual_verifier=verify,
        neutral_factory=lambda _scene: None,
    )

    assert result[0].provider == "youtube"
    assert result[0].resolution_kind == "youtube_extracted_frame"
    assert result[0].fallback_reason == "same_video_timestamp_frame"
    assert any(":frame:" in candidate_id for candidate_id in verified_ids)


def test_same_container_reuse_requires_a_separately_verified_render_variant(tmp_path):
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    scenes = [
        {
            "scene_id": f"item-1-scene-{index}",
            "order": index,
            "container_id": "item-1",
            "text": f"Item one detail {index}",
            "duration": 4.0,
            "visual_requirement": {"requirement_hash": f"item-one-{index}"},
            "source_policy": {"stock_allowed": False, "visual_verification_required": True},
        }
        for index in range(3)
    ]

    def without_variant(scene, _cancel):
        if scene["order"] == 0:
            return {
                "candidate_id": "source-a",
                "path": "cut-a.mp4",
                "source": "youtube",
                "container_id": "item-1",
                "score": 90,
            }
        return []

    no_variant = resolve_storyboard_media_v2(
        scenes,
        youtube_lane=without_variant,
        web_lane=lambda _scene, _cancel: [],
        visual_verifier=lambda _scene, _candidate: {"accepted": True, "final_score": 90},
    )
    assert no_variant[2].provider == "neutral"
    assert no_variant[2].fallback_reason == "degraded:no_verified_media"

    source_path = tmp_path / "cut-a.mp4"
    variant_path = tmp_path / "cut-b.mp4"
    source_path.write_bytes(b"source-cut")
    variant_bytes = b"separately-downloaded-distant-cut"
    variant_path.write_bytes(variant_bytes)
    variant_hash = hashlib.sha256(variant_bytes).hexdigest()
    source_url = "https://www.youtube.com/watch?v=abcdefghijk"

    def with_variant(scene, _cancel):
        if scene["order"] == 0:
            return {
                "candidate_id": "source-a",
                "path": str(source_path),
                "source": "youtube",
                "container_id": "item-1",
                "score": 90,
                "source_url": source_url,
                "video_start": 2.0,
                "video_end": 6.0,
                "verified_reuse_variants": [
                    {
                        "verified": True,
                        "candidate_id": "source-a-range-b",
                        "path": str(variant_path),
                        "source_url": source_url,
                        "video_start": 48.0,
                        "video_end": 52.0,
                        "visual_decision": {
                            "accepted": True,
                            "visual_checked": True,
                            "verified_requirement_hash": "item-one-2",
                            "verified_asset_sha256": variant_hash,
                        },
                    }
                ],
            }
        return []

    verified_variant = resolve_storyboard_media_v2(
        scenes,
        youtube_lane=with_variant,
        web_lane=lambda _scene, _cancel: [],
        visual_verifier=lambda _scene, _candidate: {"accepted": True, "final_score": 90},
    )
    assert verified_variant[2].status == "accepted"
    assert verified_variant[2].resolution_kind == "same_container_reuse"
    assert verified_variant[2].selected_path == str(variant_path)
    assert verified_variant[2].video_start == 48.0


def test_same_identity_does_not_replay_one_physical_youtube_cut():
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    def requirement(scene_hash, scene_text):
        return {
            "requirement_hash": scene_hash,
            "canonical_entity": "Jaguar E-Type",
            "subject_type": "automobile",
            "strict_identity": True,
            "required_attributes": {
                "model_or_make": "jaguar e type",
                "model": "e type",
                "year": "1961",
                "decade": "1960s",
                "era": "classic_historical",
            },
            "positive_evidence": [scene_text],
            "action_terms": scene_text.lower().split(),
        }

    scenes = [
        {
            "scene_id": "e-type-scene-0",
            "order": 0,
            "container_id": "jaguar-e-type",
            "text": "The 1961 Jaguar E-Type arrives at Geneva.",
            "positive_subjects": ["Jaguar E-Type"],
            "visual_requirement": requirement("scene-hash-0", "arrives at Geneva"),
            "source_policy": {"stock_allowed": False, "visual_verification_required": True},
        },
        {
            "scene_id": "e-type-scene-1",
            "order": 1,
            "container_id": "jaguar-e-type",
            "text": "Its straight-six defines the driving experience.",
            "positive_subjects": ["Jaguar E-Type"],
            "visual_requirement": requirement("scene-hash-1", "straight-six defines driving"),
            "source_policy": {"stock_allowed": False, "visual_verification_required": True},
        },
    ]

    def only_first(scene, _cancel):
        if scene["order"] == 0:
            return {
                "candidate_id": "e-type-cut",
                "path": "e-type-cut.mp4",
                "source": "youtube",
                "container_id": "jaguar-e-type",
                "score": 90,
                "title": "1961 Jaguar E-Type road footage",
            }
        return []

    result = resolve_storyboard_media_v2(
        scenes,
        youtube_lane=only_first,
        web_lane=lambda _scene, _cancel: [],
        visual_verifier=lambda _scene, _candidate: {"accepted": True, "final_score": 90},
    )

    assert result[1].provider == "neutral"
    assert result[1].fallback_reason == "degraded:no_verified_media"


def test_final_handoff_rejects_resolution_from_a_different_visual_requirement():
    from services.pipeline_v2_media_adapter import _visual_requirement_issue

    scene = {
        "visual_requirement": {
            "requirement_hash": "required-scene-hash",
        },
    }
    assert _visual_requirement_issue(
        scene,
        {"provider": "web", "requirement_hash": "other-scene-hash"},
        None,
        "Web Images",
    ) == "visual_requirement_mismatch"
    assert _visual_requirement_issue(
        scene,
        {"provider": "web"},
        None,
        "Web Images",
    ) == "missing_visual_requirement_hash"
    assert _visual_requirement_issue(
        scene,
        {"provider": "graphic", "requirement_hash": ""},
        None,
        "Remotion Graphic Fallback",
    ) == ""
