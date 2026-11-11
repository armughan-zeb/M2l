import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from services import youtube_pipeline_v2 as pipeline
from services.youtube_pool_renderer import resolve_pool_first
from services.youtube_moment_matcher import YouTubeMoment, heuristic_moment
from services.youtube_pool_renderer import (
    _best_probe_moment,
    _creator_cta_text_match,
    _detect_creator_cta_overlay,
    _pool_probe_plan,
    _prefetch_probe_transcript_moments,
    _probe_pool_candidates,
    _screen_moment_overlays,
    _screen_moment_quality,
    _native_overlay_lane_for_category,
    _bounded_native_overlay_box,
    _caption_blur_admissible,
    _select_verified_pool_sources,
    _source_shortlist,
)
from services.youtube_overlay_policy import preserve_source_overlays


def test_creator_cta_detector_requires_edge_overlay_and_explicit_promotion_text():
    subscribe_box = [[120, 620], [360, 620], [360, 680], [120, 680]]
    assert _creator_cta_text_match("SUBSCRIBE", subscribe_box, 1280, 720) is True
    assert _creator_cta_text_match("DSUBSCRIBED", subscribe_box, 1280, 720) is True
    assert _creator_cta_text_match("STAR & STORY", subscribe_box, 1280, 720) is False
    central_box = [[400, 300], [600, 300], [600, 350], [400, 350]]
    assert _creator_cta_text_match("Subscribe", central_box, 1280, 720) is False


def test_creator_cta_detector_checks_intro_middle_and_end_frames(tmp_path):
    from PIL import Image

    timestamps = []

    def write_frame(command, **_kwargs):
        timestamp = float(command[command.index("-ss") + 1])
        timestamps.append(timestamp)
        Image.effect_noise((320, 180), 80).convert("RGB").save(command[-1], format="JPEG")
        return SimpleNamespace(returncode=0)

    with mock.patch(
        "services.scene_understanding.frame_overlay_blur._get_rapidocr",
        return_value=lambda _pixels: ([], {}),
    ), mock.patch(
        "services.visual_intelligence.frame_extractor._ffmpeg_bin",
        return_value="ffmpeg",
    ), mock.patch("services.youtube_pool_renderer.subprocess.run", side_effect=write_frame):
        result = _detect_creator_cta_overlay(str(tmp_path / "sample.mp4"), 10.0)

    assert result["checked"] is True
    assert timestamps == [0.8, 5.0, 9.0]


def test_creator_cta_detector_rejects_intro_subscribe_card(tmp_path):
    from PIL import Image

    timestamps = []

    def write_frame(command, **_kwargs):
        timestamps.append(float(command[command.index("-ss") + 1]))
        Image.effect_noise((320, 180), 80).convert("RGB").save(command[-1], format="JPEG")
        return SimpleNamespace(returncode=0)

    def ocr(_pixels):
        return [([ [250, 5], [315, 5], [315, 25], [250, 25] ], "SUBSCRIBE", 0.98)], {}

    with mock.patch(
        "services.scene_understanding.frame_overlay_blur._get_rapidocr",
        return_value=ocr,
    ), mock.patch(
        "services.visual_intelligence.frame_extractor._ffmpeg_bin",
        return_value="ffmpeg",
    ), mock.patch("services.youtube_pool_renderer.subprocess.run", side_effect=write_frame):
        result = _detect_creator_cta_overlay(str(tmp_path / "sample.mp4"), 10.0)

    assert result["detected"] is True
    assert result["matched_term"] == "subscribe"
    assert timestamps == [0.8]


def test_creator_cta_rejection_survives_source_overlay_preservation():
    result = preserve_source_overlays({
        "reject": True,
        "clean": False,
        "rejection_reason": "creator_cta_overlay_rejected",
        "overlay_types": ["creator_cta"],
    })
    assert result["reject"] is True
    assert result["clean"] is False
    assert result["rejection_reason"] == "creator_cta_overlay_rejected"


def test_native_overlay_lane_is_explicit_and_geometry_bounded():
    assert _native_overlay_lane_for_category("gaming") is True
    assert _native_overlay_lane_for_category("technology") is True
    assert _native_overlay_lane_for_category("automotive") is True
    assert _native_overlay_lane_for_category("documentary") is False
    assert _bounded_native_overlay_box((0.02, 0.04, 0.28, 0.14)) is True
    assert _bounded_native_overlay_box((0.05, 0.04, 0.96, 0.22)) is False
from services.youtube_source_pool import _is_pool_visual_candidate


def test_caption_blur_policy_cannot_restore_a_confident_talking_head(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDZEO_YT_CAPTION_POLICY", "blur")
    monkeypatch.setenv("VIDZEO_YT_REJECT_TALKING_HEAD", "1")
    clip = tmp_path / "presenter-with-captions.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42" + (b"0" * 9000))

    talking = {
        "talking_head": True,
        "talking_head_confidence": 0.95,
        "talking_head_reason": "stable_centered_presenter",
    }
    facecam = {
        "facecam_inset": False,
        "facecam_confidence": 0.0,
        "facecam_reason": "no_persistent_corner_face",
        "facecam_corner": "",
    }
    with mock.patch(
        "services.visual_intelligence.frame_extractor.extract_clip_frames",
        return_value=(["frame-1.jpg", "frame-2.jpg"], 5.0),
    ), mock.patch(
        "services.visual_intelligence.frame_extractor.cleanup_frames",
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_watermark",
        return_value={},
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_watermark_box",
        return_value=None,
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_subtitles",
        return_value={"has_subtitle": True},
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_subtitle_box",
        return_value=(0.2, 0.72, 0.8, 0.84),
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_talking_head",
        return_value=talking,
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_facecam_inset",
        return_value=facecam,
    ):
        result = _screen_moment_overlays(str(clip))

    assert result["checked"] is True
    assert result["reject"] is True
    assert result["rejection_reason"] == "confident_talking_head_rejected"


def test_small_watermark_is_tolerated_even_when_caption_policy_is_blur(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDZEO_YT_CAPTION_POLICY", "blur")
    clip = tmp_path / "watermarked-driving-footage.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42" + (b"0" * 9000))
    with mock.patch(
        "services.visual_intelligence.frame_extractor.extract_clip_frames",
        return_value=(["frame-1.jpg", "frame-2.jpg"], 5.0),
    ), mock.patch(
        "services.visual_intelligence.frame_extractor.cleanup_frames",
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_watermark",
        return_value={"top_right": 0.95},
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_watermark_box",
        side_effect=[None, (0.82, 0.03, 0.96, 0.12), None, None],
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_subtitles",
        return_value={},
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_subtitle_box",
        return_value=None,
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_persistent_text_overlay_box",
        return_value=None,
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_talking_head",
        return_value={"talking_head": False},
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_facecam_inset",
        return_value={"facecam_inset": False},
    ):
        result = _screen_moment_overlays(str(clip))

    assert result["reject"] is False
    assert result["watermark_tolerated"] is True
    assert result["overlay_types"] == ["watermark"]


def test_small_caption_remains_sharp_even_with_legacy_blur_setting(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDZEO_YT_CAPTION_POLICY", "blur")
    clip = tmp_path / "captioned-footage.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42" + (b"0" * 9000))
    with mock.patch(
        "services.visual_intelligence.frame_extractor.extract_clip_frames",
        return_value=(["frame-1.jpg", "frame-2.jpg"], 5.0),
    ), mock.patch(
        "services.visual_intelligence.frame_extractor.cleanup_frames",
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_watermark",
        return_value={},
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_watermark_box",
        return_value=None,
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_subtitles",
        return_value={"has_subtitle": True},
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_subtitle_box",
        return_value=(0.2, 0.72, 0.8, 0.84),
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_persistent_text_overlay_box",
        return_value=None,
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_talking_head",
        return_value={"talking_head": False},
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_facecam_inset",
        return_value={"facecam_inset": False},
    ):
        result = _screen_moment_overlays(str(clip))

    assert result["reject"] is False
    assert result["no_blur"] is True


def test_bounded_caption_probe_is_admitted_without_remediation(tmp_path, monkeypatch):
    """A small source caption must not force a blurred derivative."""
    monkeypatch.setenv("VIDZEO_YT_CAPTION_POLICY", "blur")
    assert _caption_blur_admissible({
        "has_subtitle": True,
        "reject": True,
        "rejection_reason": "caption_overlay_rejected",
        "regions": [{"type": "subtitle", "bbox": [0.12, 0.74, 0.88, 0.86]}],
        "facecam_inset": False,
        "talking_head": False,
        "outro_detected": False,
    }) is False
    assert _caption_blur_admissible({
        "has_subtitle": True,
        "reject": True,
        "regions": [{"type": "text_overlay", "bbox": [0.05, 0.08, 0.95, 0.28]}],
    }) is False

    monkeypatch.setenv("VIDZEO_YT_POOL_PROBE_VISION_CAP", "0")
    records = {
        "captioned": SimpleNamespace(
            video_id="captioned", url="https://youtube.test/captioned",
            title="Driving footage with subtitles", channel="Archive", duration=600,
            metadata_score=100, pool_keys=["pool-a"],
        ),
    }

    def fake_probe(_candidate, output_path, *_args, **_kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"probe" * 1000)
        return True

    def fake_overlay(_path, *_args, **_kwargs):
        return {
            "checked": True, "clean": False, "reject": True,
            "has_subtitle": True, "overlay_types": ["subtitle"],
            "regions": [{"type": "subtitle", "bbox": [0.12, 0.74, 0.88, 0.86]}],
            "rejection_reason": "caption_overlay_rejected",
            "facecam_inset": False, "talking_head": False,
            "outro_detected": False,
        }

    class ProbePipeline:
        _download_clip = staticmethod(fake_probe)

    with mock.patch("services.youtube_pool_renderer._screen_moment_quality", return_value={"checked": True, "reject": False}), \
         mock.patch("services.youtube_pool_renderer._screen_moment_overlays", side_effect=fake_overlay):
        audit = _probe_pool_candidates(
            ProbePipeline(), SimpleNamespace(), {"pool-a": ["captioned"]},
            records=records, output_root=tmp_path,
        )
    assert audit["captioned"]["status"] == "accepted"
    assert not audit["captioned"].get("caption_blur_pending")


def test_bounded_native_data_overlay_survives_without_presenter(tmp_path):
    clip = tmp_path / "sports-scoreboard.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42" + (b"0" * 9000))
    with mock.patch(
        "services.visual_intelligence.frame_extractor.extract_clip_frames",
        return_value=(["frame-1.jpg", "frame-2.jpg"], 5.0),
    ), mock.patch(
        "services.visual_intelligence.frame_extractor.cleanup_frames",
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_watermark",
        return_value={},
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_watermark_box",
        return_value=None,
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_subtitles",
        return_value={"has_subtitle": True},
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_subtitle_box",
        return_value=(0.02, 0.04, 0.28, 0.14),
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_persistent_text_overlay_box",
        return_value=None,
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_talking_head",
        return_value={"talking_head": False},
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_facecam_inset",
        return_value={"facecam_inset": False},
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_outro_endcard",
        return_value={"outro_detected": False},
    ):
        result = _screen_moment_overlays(
            str(clip), native_overlay_lane=True,
        )

    assert result["reject"] is False
    assert result["native_overlay_detected"] is True
    assert "native_data_overlay" in result["overlay_types"]


def test_persistent_top_heading_is_hard_rejected(tmp_path, monkeypatch):
    """Creator/archive/timeline title bands must never reach compositing."""
    clip = tmp_path / "benchmark-with-archive-heading.mp4"
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42" + (b"0" * 9000))
    with mock.patch(
        "services.visual_intelligence.frame_extractor.extract_clip_frames",
        return_value=(["frame-1.jpg", "frame-2.jpg", "frame-3.jpg"], 5.0),
    ), mock.patch(
        "services.visual_intelligence.frame_extractor.cleanup_frames",
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_watermark",
        return_value={},
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_watermark_box",
        return_value=None,
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_subtitles",
        return_value={},
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_subtitle_box",
        return_value=None,
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_persistent_text_overlay_box",
        side_effect=[(0.04, 0.50, 0.96, 0.95), None],
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_talking_head",
        return_value={"talking_head": False},
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_facecam_inset",
        return_value={"facecam_inset": False},
    ), mock.patch(
        "services.scene_understanding.analyzers.detect_outro_endcard",
        return_value={"outro_detected": False},
    ):
        result = _screen_moment_overlays(str(clip))

    assert result["checked"] is True
    assert result["reject"] is True
    assert result["rejection_reason"] == "burned_in_text_overlay_rejected"
    assert "text_overlay" in result["overlay_types"]


def test_benchmark_presenter_package_is_not_pool_source():
    assert not _is_pool_visual_candidate({
        "id": "benchmark01",
        "title": "Revolutionary new benchmark - GTA VI",
        "channel": "Gaming Review Lab",
        "description": "Performance review with webcam and commentary.",
        "duration": 240,
    })
    assert _is_pool_visual_candidate({
        "id": "raw01",
        "title": "GTA VI raw gameplay benchmark capture no commentary",
        "channel": "Gameplay Archive",
        "description": "Direct gameplay capture, no commentary.",
        "duration": 240,
    })


def test_pool_quality_gate_rejects_uniform_dark_cut():
    raw = bytes([10]) * (32 * 18 * 5)
    completed = SimpleNamespace(returncode=0, stdout=raw, stderr=b"")
    with mock.patch("services.youtube_pool_renderer.subprocess.run", return_value=completed):
        result = _screen_moment_quality("dark-cut.mp4")
    assert result["checked"] is True
    assert result["reject"] is True
    assert result["reason"] == "blank_or_low_information_frame"


def test_pool_quality_gate_keeps_moving_textured_frame():
    frames = [
        bytes((index * 37 + frame_index * 23) % 256 for index in range(32 * 18))
        for frame_index in range(5)
    ]
    completed = SimpleNamespace(returncode=0, stdout=b"".join(frames), stderr=b"")
    with mock.patch("services.youtube_pool_renderer.subprocess.run", return_value=completed):
        result = _screen_moment_quality("textured-cut.mp4")
    assert result["checked"] is True
    assert result["reject"] is False


def test_pool_quality_gate_rejects_static_textured_slide():
    frame = bytes((index * 37) % 256 for index in range(32 * 18))
    completed = SimpleNamespace(returncode=0, stdout=frame * 5, stderr=b"")
    with mock.patch("services.youtube_pool_renderer.subprocess.run", return_value=completed):
        result = _screen_moment_quality("static-slide.mp4")
    assert result["checked"] is True
    assert result["reject"] is True
    assert result["reason"] == "still_or_slideshow_frame"


def test_pool_quality_gate_rejects_short_hold_with_title_card_transition():
    width, height = 32, 18
    base = bytes((index * 37) % 256 for index in range(width * height))
    changed = bytearray(base)
    for row in range(3, height - 3):
        for column in range(3, width - 3):
            index = row * width + column
            changed[index] = min(255, changed[index] + 24)
    completed = SimpleNamespace(
        returncode=0,
        stdout=base * 4 + bytes(changed),
        stderr=b"",
    )
    with mock.patch("services.youtube_pool_renderer.subprocess.run", return_value=completed):
        result = _screen_moment_quality("short-title-card-transition.mp4")
    assert result["checked"] is True
    assert result["reject"] is True
    assert result["reason"] == "static_image_or_pan"


def test_pool_quality_gate_rejects_dark_low_detail_cut_above_legacy_luma_floor():
    frame = bytes([26]) * (32 * 18)
    frame = frame[: int(len(frame) * 0.9)] + bytes([60]) * (len(frame) - int(len(frame) * 0.9))
    completed = SimpleNamespace(returncode=0, stdout=frame * 5, stderr=b"")
    with mock.patch("services.youtube_pool_renderer.subprocess.run", return_value=completed):
        result = _screen_moment_quality("dark-low-detail-cut.mp4")
    assert result["checked"] is True
    assert result["reject"] is True
    assert result["reason"] == "blank_or_low_information_frame"


def test_small_pool_uses_fast_three_source_default(monkeypatch):
    monkeypatch.delenv("VIDZEO_YT_POOL_SOURCE_SHORTLIST", raising=False)
    sources = {
        f"source-{index}": SimpleNamespace(
            video_id=f"source-{index}", url=f"https://youtube.test/source-{index}",
            title="RMS Titanic archival footage", channel="Archive", duration=600,
            metadata_score=100 - index, pool_keys=[f"pool-{index}"],
        )
        for index in range(6)
    }
    pools = SimpleNamespace(source_records=sources, moment_assignments={}, rows=[{}] * 8)

    assert len(_source_shortlist(pools)) == 3


def test_pool_probe_plan_keeps_listicle_containers_fair(monkeypatch):
    monkeypatch.setenv("VIDZEO_YT_POOL_PROBE_CANDIDATES_PER_POOL", "4")
    records = {}
    raw_pools = {"item-1": [], "item-2": [], "item-3": []}
    for pool_index in range(3):
        for candidate_index in range(4):
            source_id = f"source-{pool_index}-{candidate_index}"
            records[source_id] = SimpleNamespace(
                video_id=source_id,
                url=f"https://youtube.test/{source_id}",
                title=f"Archive item {pool_index}",
                channel="Archive",
                duration=600,
                metadata_score=100 - candidate_index,
                pool_keys=[f"item-{pool_index + 1}"],
            )
    pools = SimpleNamespace(
        pools=raw_pools,
        source_records=records,
        rows=[
            {"pool_key": "item-1", "scene_text": "one"},
            {"pool_key": "item-2", "scene_text": "two"},
            {"pool_key": "item-3", "scene_text": "three"},
        ],
    )
    candidates, requirements = _pool_probe_plan(pools)
    assert set(candidates) == {"item-1", "item-2", "item-3"}
    assert all(len(values) == 4 for values in candidates.values())
    assert all(row["source_target"] == 2 for row in requirements.values())


def test_verified_pool_selection_prioritizes_one_source_per_listicle_pool():
    pools = SimpleNamespace(
        rows=[{"pool_key": "item-1"}, {"pool_key": "item-2"}, {"pool_key": "item-3"}],
        source_records={},
    )
    candidates = {
        "item-1": ["a1", "a2"],
        "item-2": ["b1", "b2"],
        "item-3": ["c1", "c2"],
    }
    audit = {
        source_id: {"status": "accepted"}
        for source_id in ("a1", "a2", "b1", "b2", "c1", "c2")
    }
    requirements = {
        key: {"source_target": 2}
        for key in candidates
    }
    selected, selected_by_pool = _select_verified_pool_sources(
        pools, candidates, audit, requirements
    )
    assert selected[:3] == ["a1", "b1", "c1"]
    assert all(row["selected_source_ids"] for row in selected_by_pool.values())
    assert len(selected) >= 3


def test_probe_candidates_preserve_caption_and_admit_clean_source(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDZEO_YT_POOL_PROBE_VISION_CAP", "0")
    monkeypatch.setenv("VIDZEO_YT_CAPTION_POLICY", "reject")
    records = {
        "captioned": SimpleNamespace(
            video_id="captioned", url="https://youtube.test/captioned",
            title="Archive captions", channel="Archive", duration=600,
            metadata_score=100, pool_keys=["pool-a"],
        ),
        "clean": SimpleNamespace(
            video_id="clean", url="https://youtube.test/clean",
            title="Archive footage", channel="Archive", duration=600,
            metadata_score=90, pool_keys=["pool-a"],
        ),
    }

    def fake_probe(_candidate, output_path, *_args, **_kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"probe" * 1000)
        return True

    def fake_overlay(path, *_args, **_kwargs):
        if "captioned" in path:
            return {
                "checked": True, "clean": False, "reject": True,
                "has_subtitle": True, "overlay_types": ["subtitle"],
                "rejection_reason": "caption_overlay_rejected",
                "archival_caption_allowed": False,
            }
        return {"checked": True, "clean": True, "reject": False, "overlay_types": [], "archival_caption_allowed": False}

    class ProbePipeline:
        _download_clip = staticmethod(fake_probe)

    with mock.patch("services.youtube_pool_renderer._screen_moment_quality", return_value={"checked": True, "reject": False}), \
         mock.patch("services.youtube_pool_renderer._screen_moment_overlays", side_effect=fake_overlay):
        audit = _probe_pool_candidates(
            ProbePipeline(),
            SimpleNamespace(),
            {"pool-a": ["captioned", "clean"]},
            records=records,
            output_root=tmp_path,
        )
    assert audit["captioned"]["status"] == "accepted"
    assert not audit["captioned"].get("caption_blur_pending")
    assert audit["clean"]["status"] == "accepted"


def test_probe_collection_enforces_wall_and_isolates_stalled_worker(tmp_path, monkeypatch):
    """A hung probe must not hold the pool admission stage indefinitely."""
    monkeypatch.setenv("VIDZEO_YT_POOL_PROBE_CONCURRENCY", "2")
    monkeypatch.setenv("VIDZEO_YT_POOL_PROBE_VISION_CAP", "0")
    monkeypatch.setattr(
        "services.youtube_pool_renderer.estimate_probe_wall_seconds",
        lambda *args, **kwargs: 0.10,
    )
    records = {
        "slow": SimpleNamespace(
            video_id="slow", url="https://youtube.test/slow",
            title="Slow archive", channel="Archive", duration=600,
            metadata_score=100, pool_keys=["pool-a"],
        ),
        "fast": SimpleNamespace(
            video_id="fast", url="https://youtube.test/fast",
            title="Fast archive", channel="Archive", duration=600,
            metadata_score=90, pool_keys=["pool-a"],
        ),
    }
    release_slow = threading.Event()

    def fake_probe(candidate, output_path, *_args, **_kwargs):
        if candidate.get("id") == "slow":
            release_slow.wait(timeout=5)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"probe" * 1000)
        return True

    class ProbePipeline:
        _download_clip = staticmethod(fake_probe)

    started = time.monotonic()
    try:
        with mock.patch(
            "services.youtube_pool_renderer._screen_moment_quality",
            return_value={"checked": True, "reject": False},
        ), mock.patch(
            "services.youtube_pool_renderer._screen_moment_overlays",
            return_value={"checked": True, "clean": True, "reject": False, "overlay_types": []},
        ):
            audit = _probe_pool_candidates(
                ProbePipeline(), SimpleNamespace(rows=[{}]),
                {"pool-a": ["slow", "fast"]},
                records=records,
                output_root=tmp_path,
            )
        elapsed = time.monotonic() - started
        assert elapsed < 1.0
        assert audit["slow"]["status"] == "rejected"
        assert audit["slow"]["rejection_reason"] == "probe_wall_exhausted"
        assert audit["fast"]["status"] == "accepted"
    finally:
        release_slow.set()


def test_pool_renderer_heuristic_fallback_covers_all_scenes_without_transcripts(tmp_path):
    sources = {
        f"source-{index}": SimpleNamespace(
            video_id=f"source-{index}", url=f"https://youtube.test/source-{index}",
            title="RMS Titanic archival footage", channel="Archive", duration=600,
            metadata_score=100 - index,
        )
        for index in range(3)
    }
    pools = SimpleNamespace(
        source_records=sources,
        moment_assignments={},
        assignments=[{"id": "source-0", "duration": 600, "best_timestamp": 20} for _ in range(11)],
        rows=[{"scene_text": f"Scene {index}", "candidate_subject": "RMS Titanic", "title_anchor": "RMS Titanic"} for index in range(11)],
        scene_durations=[4.0] * 11,
    )
    downloaded = []
    cuts = []
    def fake_source(candidate, output_path, **_kwargs):
        downloaded.append(candidate["id"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"source")
        return True
    def fake_cut(source_path, output_path, start, duration):
        cuts.append((str(source_path), start, duration))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"cut")
        return True
    with mock.patch.object(pipeline, "_download_source_once", side_effect=fake_source), \
         mock.patch.object(pipeline, "_cut_local_segment", side_effect=fake_cut):
        result = resolve_pool_first(pipeline, pools, output_dir=tmp_path)
    assert len(downloaded) == 3
    assert len(cuts) == 11
    assert sum(bool(path) for path in result) == 11


def test_pool_renderer_keeps_source_failures_in_audit(tmp_path):
    first = SimpleNamespace(video_id="source-1", url="https://youtube.test/source-1", title="Archive one", channel="Archive", duration=600, metadata_score=100, pool_keys=["pool-a"])
    second = SimpleNamespace(video_id="source-2", url="https://youtube.test/source-2", title="Archive two", channel="Archive", duration=600, metadata_score=90, pool_keys=["pool-b"])
    pools = SimpleNamespace(
        source_records={"source-1": first, "source-2": second},
        moment_assignments={},
        assignments=[{"id": "source-1", "duration": 600, "best_timestamp": 20}],
        rows=[{"scene_text": "Scene", "candidate_subject": "Titanic", "title_anchor": "Titanic"}],
        scene_durations=[4.0],
    )

    def fake_source(candidate, output_path, audit=None, **_kwargs):
        if candidate["id"] == "source-1":
            audit.update({"status": "http_403", "reason": "forbidden"})
            return False
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"source")
        audit.update({"status": "downloaded"})
        return True

    with mock.patch.object(pipeline, "_download_source_once", side_effect=fake_source), \
         mock.patch.object(pipeline, "_cut_local_segment", return_value=False):
        result = resolve_pool_first(pipeline, pools, output_dir=tmp_path)

    assert result.audit["source_downloads_attempted"] == 2
    assert result.audit["source_downloads_failed"] == 1
    assert result.audit["source_audit"]["source-1"]["status"] == "http_403"


def test_pool_renderer_source_deadline_retains_fast_download_without_waiting_for_stall(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("VIDZEO_YT_POOL_SOURCE_SHORTLIST", "2")
    monkeypatch.setenv("VIDZEO_YT_SOURCE_DOWNLOAD_CONCURRENCY", "2")
    monkeypatch.setenv("VIDZEO_YT_POOL_SOURCE_DOWNLOAD_DEADLINE_SECONDS", "0.10")
    fast = SimpleNamespace(
        video_id="fast-source", url="https://youtube.test/fast-source",
        title="RMS Titanic archive fast", channel="Archive", duration=600,
        metadata_score=100, pool_keys=["titanic"],
    )
    slow = SimpleNamespace(
        video_id="slow-source", url="https://youtube.test/slow-source",
        title="RMS Titanic archive slow", channel="Archive", duration=600,
        metadata_score=90, pool_keys=["titanic"],
    )
    pools = SimpleNamespace(
        source_records={"fast-source": fast, "slow-source": slow},
        moment_assignments={
            0: [heuristic_moment(
                source_id="fast-source", video_id="fast-source", scene_index=0,
                start=20, duration=4,
            )],
            1: [heuristic_moment(
                source_id="slow-source", video_id="slow-source", scene_index=1,
                start=80, duration=4,
            )],
        },
        assignments=[
            {"id": "fast-source", "duration": 600, "best_timestamp": 20},
            {"id": "slow-source", "duration": 600, "best_timestamp": 80},
        ],
        rows=[
            {"scene_text": "Titanic departs", "candidate_subject": "RMS Titanic", "title_anchor": "RMS Titanic"},
            {"scene_text": "Titanic lifeboats", "candidate_subject": "RMS Titanic", "title_anchor": "RMS Titanic"},
        ],
        scene_durations=[4.0, 4.0],
    )
    release_slow = threading.Event()
    slow_finished = threading.Event()
    slow_path = tmp_path / "sources" / "slow-source.source.mp4"

    def fake_source(candidate, output_path, **_kwargs):
        if candidate["id"] == "slow-source":
            release_slow.wait(timeout=5)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"source" * 1000)
        if candidate["id"] == "slow-source":
            slow_finished.set()
        return True

    def fake_cut(_source_path, output_path, _start, _duration, **_kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"cut" * 2000)
        return True

    started = time.monotonic()
    try:
        with mock.patch.object(pipeline, "_download_source_once", side_effect=fake_source), \
             mock.patch.object(pipeline, "_cut_local_segment", side_effect=fake_cut):
            result = resolve_pool_first(pipeline, pools, output_dir=tmp_path)
        elapsed = time.monotonic() - started
        # Windows thread teardown and the source-deadline drain can vary by a
        # few hundred milliseconds under the full suite; the important
        # invariant is that the stalled source does not hold the resolver for
        # its five-second worker wait.
        assert elapsed < 2.25
        assert result.audit["source_download_deadline_reached"] is True
        assert result.audit["source_downloads_pending_at_deadline"] == 1
        assert result.audit["source_downloads_succeeded"] == 1
        assert sum(bool(path) for path in result) == 2
    finally:
        release_slow.set()

    # A running source owns its unique path. Crossing the batch deadline must
    # not delete or corrupt it while the cooperative worker winds down.
    assert slow_finished.wait(timeout=2)
    assert slow_path.exists()


def test_pool_renderer_heuristic_fallback_stays_inside_source_bounds(tmp_path):
    source = SimpleNamespace(video_id="source-1", url="https://youtube.test/source-1", title="Archive", channel="Archive", duration=20, metadata_score=100)
    pools = SimpleNamespace(
        source_records={"source-1": source},
        moment_assignments={},
        assignments=[{"id": "source-1", "duration": 20, "best_timestamp": None} for _ in range(4)],
        rows=[{"scene_text": "Scene", "candidate_subject": "Titanic", "title_anchor": "Titanic"} for _ in range(4)],
        scene_durations=[6.0] * 4,
    )
    starts = []
    def fake_source(candidate, output_path, **_kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"source")
        return True
    def fake_cut(_source_path, output_path, start, duration):
        starts.append((start, duration))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"cut")
        return True
    with mock.patch.object(pipeline, "_download_source_once", side_effect=fake_source), \
         mock.patch.object(pipeline, "_cut_local_segment", side_effect=fake_cut):
        result = resolve_pool_first(pipeline, pools, output_dir=tmp_path)
    assert len(result) == 4
    assert all(0 <= start <= 13 for start, _duration in starts)


def test_pool_renderer_downloads_each_source_once_and_cuts_each_scene(tmp_path):
    source = SimpleNamespace(video_id="source-1", url="https://youtube.test/source-1", title="Archive", channel="Archive", duration=600)
    pools = SimpleNamespace(
        source_records={"source-1": source},
        moment_assignments={
            0: [heuristic_moment(source_id="source-1", video_id="source-1", scene_index=0, start=20, duration=4)],
            1: [heuristic_moment(source_id="source-1", video_id="source-1", scene_index=1, start=80, duration=4)],
        },
        assignments=[],
        rows=[
            {"scene_text": "Departure", "candidate_subject": "Titanic", "title_anchor": "Titanic"},
            {"scene_text": "Lifeboats", "candidate_subject": "Titanic", "title_anchor": "Titanic"},
        ],
        scene_durations=[4.0, 4.0],
    )
    downloaded = []
    cuts = []

    def fake_source(candidate, output_path, **_kwargs):
        downloaded.append(candidate["id"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"source")
        return True

    def fake_cut(source_path, output_path, start, duration):
        cuts.append((str(source_path), start, duration))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"cut")
        return True

    with mock.patch.object(pipeline, "_download_source_once", side_effect=fake_source), \
         mock.patch.object(pipeline, "_cut_local_segment", side_effect=fake_cut):
        result = resolve_pool_first(pipeline, pools, output_dir=tmp_path)

    assert downloaded == ["source-1"]
    assert [row[1] for row in cuts] == [20.0, 80.0]
    assert all(path and Path(path).exists() for path in result)


def test_pool_renderer_validates_moments_progressively_per_scene(tmp_path):
    source = SimpleNamespace(
        video_id="source-1", url="https://youtube.test/source-1",
        title="RMS Titanic archival footage", channel="Archive", duration=600,
        metadata_score=100, pool_keys=["titanic"],
    )
    pools = SimpleNamespace(
        source_records={"source-1": source},
        moment_assignments={
            0: [
                heuristic_moment(source_id="source-1", video_id="source-1", scene_index=0, start=20, duration=4),
                heuristic_moment(source_id="source-1", video_id="source-1", scene_index=0, start=80, duration=4),
                heuristic_moment(source_id="source-1", video_id="source-1", scene_index=0, start=140, duration=4),
            ],
        },
        assignments=[],
        rows=[{"scene_text": "Titanic lifeboats", "candidate_subject": "RMS Titanic", "title_anchor": "RMS Titanic"}],
        scene_durations=[4.0],
    )
    vision_calls = []

    def fake_source(_candidate, output_path, **_kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"source")
        return True

    def fake_cut(_source_path, output_path, _start, _duration):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"cut")
        return True

    def fake_vision(path, *_args, **_kwargs):
        vision_calls.append(path)
        if ".moment_00." in path:
            return False, 30.0, True, "replace"
        return True, 92.0, True, "keep"

    class StrictContext:
        visual_required = True
        model = "test-model"

    with mock.patch.object(pipeline, "_download_source_once", side_effect=fake_source), \
         mock.patch.object(pipeline, "_cut_local_segment", side_effect=fake_cut), \
         mock.patch.object(pipeline, "_visual_verdict_scored", side_effect=fake_vision):
        result = resolve_pool_first(
            pipeline,
            pools,
            output_dir=tmp_path,
            visual_context=StrictContext(),
        )

    assert len(vision_calls) == 2
    assert result[0] and ".moment_01." in result[0]
    assert result.audit["visual_moments_verified"] == 2


def test_pool_renderer_redownloads_source_if_removed_before_local_cut(tmp_path):
    source = SimpleNamespace(
        video_id="source-1", url="https://youtube.test/source-1",
        title="RMS Titanic archival footage", channel="Archive", duration=600,
        metadata_score=100, pool_keys=["titanic"],
    )
    pools = SimpleNamespace(
        source_records={"source-1": source},
        moment_assignments={},
        assignments=[{"id": "source-1", "duration": 600, "best_timestamp": None}],
        rows=[{"scene_text": "Titanic lifeboats", "candidate_subject": "RMS Titanic", "title_anchor": "RMS Titanic"}],
        scene_durations=[4.0],
    )
    source_downloads = []

    def fake_source(_candidate, output_path, **_kwargs):
        source_downloads.append(str(output_path))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"source" * 1000)
        return True

    def remove_source_during_plan(candidate, _scene_index, _used, _duration):
        source_path = tmp_path / "sources" / f"{candidate['id']}.source.mp4"
        source_path.unlink(missing_ok=True)
        return 20.0

    def fake_cut(_source_path, output_path, _start, _duration):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"cut" * 2000)
        return True

    with mock.patch.object(pipeline, "_download_source_once", side_effect=fake_source), \
         mock.patch.object(pipeline, "_pick_timestamp", side_effect=remove_source_during_plan), \
         mock.patch.object(pipeline, "_cut_local_segment", side_effect=fake_cut):
        result = resolve_pool_first(pipeline, pools, output_dir=tmp_path)

    assert len(source_downloads) == 2
    assert result[0] and Path(result[0]).exists()


def test_long_form_pool_caps_relevance_vision_for_relevant_footage(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDZEO_YT_POOL_VISION_LONGFORM_MIN_SCENES", "8")
    monkeypatch.setenv("VIDZEO_YT_POOL_VISION_VETO_BUDGET", "6")
    monkeypatch.delenv("VIDZEO_YT_POOL_VISION_MAX_CALLS", raising=False)
    source = SimpleNamespace(
        video_id="source-1", url="https://youtube.test/source-1",
        title="RMS Titanic archival footage", channel="Archive", duration=600,
        metadata_score=105, pool_keys=["titanic"],
    )
    scene_count = 20
    pools = SimpleNamespace(
        source_records={"source-1": source},
        moment_assignments={
            scene_index: [
                heuristic_moment(
                    source_id="source-1", video_id="source-1", scene_index=scene_index,
                    start=20 + scene_index * 8, duration=4,
                ),
                heuristic_moment(
                    source_id="source-1", video_id="source-1", scene_index=scene_index,
                    start=220 + scene_index * 8, duration=4,
                ),
            ]
            for scene_index in range(scene_count)
        },
        assignments=[],
        rows=[
            {
                "scene_text": f"Titanic documentary scene {scene_index}",
                "candidate_subject": "RMS Titanic",
                "title_anchor": "RMS Titanic",
            }
            for scene_index in range(scene_count)
        ],
        scene_durations=[4.0] * scene_count,
    )
    vision_calls = []
    progress = []
    vision_concurrency_lock = threading.Lock()
    vision_active = 0
    vision_max_active = 0

    def fake_source(_candidate, output_path, **_kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"source" * 1000)
        return True

    def fake_cut(_source_path, output_path, _start, _duration):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"cut" * 2000)
        return True

    def fake_vision(path, *_args, **kwargs):
        nonlocal vision_active, vision_max_active
        assert kwargs["relevance_only"] is True
        with vision_concurrency_lock:
            vision_calls.append(path)
            vision_active += 1
            vision_max_active = max(vision_max_active, vision_active)
        # Keep calls in flight long enough to verify that the production pool
        # workers overlap visual requests instead of serializing them.
        time.sleep(0.02)
        with vision_concurrency_lock:
            vision_active -= 1
        return True, 88.0, True, 'verified_relevant_footage'

    class VisualContext:
        enabled = True
        visual_required = True
        max_concurrent = 4
        degraded_reason = ""
        model = "test-model"

    with mock.patch.object(pipeline, "_download_source_once", side_effect=fake_source), \
         mock.patch.object(pipeline, "_cut_local_segment", side_effect=fake_cut), \
         mock.patch.object(pipeline, "_visual_verdict_scored", side_effect=fake_vision):
        result = resolve_pool_first(
            pipeline,
            pools,
            output_dir=tmp_path,
            visual_context=VisualContext(),
            progress_callback=lambda value, message: progress.append((value, message)),
        )

    assert len(vision_calls) == scene_count
    assert sum(bool(path) for path in result) == scene_count
    assert result.audit["visual_veto_call_cap"] == scene_count
    assert result.audit["visual_veto_calls_completed"] == scene_count
    assert result.audit["visual_veto_hard_rejections"] == 0
    assert vision_max_active > 1
    assert progress[-1][0] == 0.97
    assert "20/20 clips resolved" in progress[-1][1]


def test_long_form_pool_bypasses_metadata_and_transcript_strong_scenes(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDZEO_YT_POOL_VISION_LONGFORM_MIN_SCENES", "8")
    monkeypatch.setenv("VIDZEO_YT_POOL_VISION_VETO_BUDGET", "6")
    monkeypatch.delenv("VIDZEO_YT_POOL_VISION_MAX_CALLS", raising=False)
    source = SimpleNamespace(
        video_id="source-1", url="https://youtube.test/source-1",
        title="RMS Titanic archival footage", channel="Archive", duration=600,
        metadata_score=110, pool_keys=["titanic"],
    )
    scene_count = 12
    pools = SimpleNamespace(
        source_records={"source-1": source},
        moment_assignments={
            scene_index: [YouTubeMoment(
                source_id="source-1",
                video_id="source-1",
                start=20 + scene_index * 12,
                end=24 + scene_index * 12,
                transcript_text="RMS Titanic archival narration",
                match_score=90,
                scene_index=scene_index,
                match_reason="exact_subject+scene_overlap",
            )]
            for scene_index in range(scene_count)
        },
        assignments=[],
        rows=[
            {
                "scene_text": f"RMS Titanic archival scene {scene_index}",
                "candidate_subject": "RMS Titanic",
                "title_anchor": "RMS Titanic",
            }
            for scene_index in range(scene_count)
        ],
        scene_durations=[4.0] * scene_count,
    )
    vision_calls = []

    def fake_source(_candidate, output_path, **_kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"source" * 1000)
        return True

    def fake_cut(_source_path, output_path, _start, _duration):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"cut" * 2000)
        return True

    def fake_vision(path, *_args, **_kwargs):
        vision_calls.append(path)
        return True, 92.0, True, "relevance_keep"

    class VisualContext:
        enabled = True
        visual_required = True
        degraded_reason = ""
        model = "test-model"

    with mock.patch.object(pipeline, "_download_source_once", side_effect=fake_source), \
         mock.patch.object(pipeline, "_cut_local_segment", side_effect=fake_cut), \
         mock.patch.object(pipeline, "_visual_verdict_scored", side_effect=fake_vision):
        result = resolve_pool_first(
            pipeline,
            pools,
            output_dir=tmp_path,
            visual_context=VisualContext(),
        )

    # One representative sanity-checks the shared source; the other exact
    # transcript matches do not waste paid calls.
    assert len(vision_calls) == 1
    assert sum(bool(path) for path in result) == scene_count
    assert result.audit["metadata_trusted_bypasses"] == scene_count - 1


def test_pool_relevance_only_verdict_ignores_quality_and_rejects_semantic_miss(monkeypatch):
    monkeypatch.setenv("VIDZEO_VISUAL_HARD_REJECT_RELEVANCE", "25")
    monkeypatch.setenv("VIDZEO_VISUAL_HARD_REJECT_CONFIDENCE", "70")

    class VisualContext:
        enabled = True
        visual_required = True

        @staticmethod
        def get_client():
            return object()

    analysis = SimpleNamespace(
        vision_available=True,
        story_relevance=65,
        confidence=95,
        fatal_mismatch=False,
        final_score=5,
        watermark=True,
    )
    fake_clip_pipeline = mock.Mock()
    fake_clip_pipeline.analyze_clip.return_value = analysis
    fake_clip_pipeline.verdict_for.return_value = "replace"

    with mock.patch(
        "services.visual_intelligence.clip_pipeline.ClipIntelligencePipeline",
        return_value=fake_clip_pipeline,
    ):
        accepted, score, available, reason = pipeline._visual_verdict_scored(
            "clip.mp4", 0, "Titanic", "Titanic at sea", "RMS Titanic",
            VisualContext(), relevance_only=True,
        )
        assert (accepted, score, available, reason) == (True, 65.0, True, "relevance_keep")

        analysis.story_relevance = 10
        accepted, score, available, reason = pipeline._visual_verdict_scored(
            "clip.mp4", 0, "Titanic", "Titanic at sea", "RMS Titanic",
            VisualContext(), relevance_only=True,
        )
        assert (accepted, score, available, reason) == (False, 10.0, True, "confident_irrelevance")


def _overlay_pool(tmp_path):
    source = SimpleNamespace(
        video_id="source-1", url="https://youtube.test/source-1",
        title="RMS Titanic archival footage", channel="Archive", duration=600,
        metadata_score=100, pool_keys=["titanic"],
    )
    return SimpleNamespace(
        source_records={"source-1": source},
        moment_assignments={
            0: [heuristic_moment(source_id="source-1", video_id="source-1", scene_index=0, start=20, duration=4)],
        },
        assignments=[{"id": "source-1", "duration": 600, "best_timestamp": 20}],
        rows=[{"scene_text": "Titanic lifeboats", "candidate_subject": "RMS Titanic", "title_anchor": "RMS Titanic"}],
        scene_durations=[4.0],
    )


def fake_overlay_source(_candidate, output_path, **_kwargs):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"source")
    return True


def fake_overlay_cut(_source_path, output_path, _start, _duration):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"cut")
    return True


def test_pool_renderer_preserves_small_logo_and_original_asset(tmp_path):
    pools = _overlay_pool(tmp_path)
    dirty = {
        "checked": True,
        "watermark_prob": 0.88,
        "has_subtitle": False,
        "clean": False,
        "regions": [{"type": "watermark", "bbox": [0.75, 0.0, 1.0, 0.25], "confidence": 88.0}],
    }
    blurred_inputs = []

    def fake_blur(input_path, output_path, _regions, **_kwargs):
        blurred_inputs.append(str(input_path))
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"blurred")
        return True

    with mock.patch.object(pipeline, "_download_source_once", side_effect=fake_overlay_source), \
         mock.patch.object(pipeline, "_cut_local_segment", side_effect=fake_overlay_cut), \
         mock.patch("services.youtube_pool_renderer._screen_moment_overlays", return_value=dirty), \
         mock.patch("services.scene_understanding.analyzers.apply_overlay_blur", side_effect=fake_blur):
        result = resolve_pool_first(pipeline, pools, output_dir=tmp_path)

    assert len(blurred_inputs) == 0
    assert result[0] and not result[0].endswith(".clean.mp4")
    assert Path(result[0]).exists()
    assert result.audit["overlay_cuts_screened"] == 1
    assert result.audit["overlay_cuts_dirty"] == 1
    assert result.audit["overlay_cuts_blurred"] == 0
    assert result.audit["overlay_cuts_blur_failed"] == 0
    cut_record = result.audit["cut_audit"][0]
    assert cut_record["overlay_checked"] is True
    assert cut_record["overlay_clean"] is True
    assert not cut_record.get("overlay_blurred")
    assert cut_record["source_overlays_preserved"] is True
    assert cut_record["overlay_watermark_prob"] == 0.88


def test_pool_renderer_rejects_captioned_cut_and_records_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDZEO_YT_CAPTION_POLICY", "reject")
    pools = _overlay_pool(tmp_path)
    dirty = {
        "checked": True,
        "watermark_prob": 0.9,
        "has_subtitle": True,
        "clean": False,
        "regions": [
            {"type": "watermark", "bbox": [0.75, 0.0, 1.0, 0.25], "confidence": 90.0},
            {"type": "subtitle", "bbox": [0.0, 0.62, 1.0, 0.98], "confidence": 80.0},
        ],
    }

    with mock.patch.object(pipeline, "_download_source_once", side_effect=fake_overlay_source), \
         mock.patch.object(pipeline, "_cut_local_segment", side_effect=fake_overlay_cut), \
         mock.patch("services.youtube_pool_renderer._screen_moment_overlays", return_value=dirty), \
         mock.patch("services.scene_understanding.analyzers.apply_overlay_blur", return_value=False) as blur:
        result = resolve_pool_first(pipeline, pools, output_dir=tmp_path)

    assert result[0] is None
    assert result.audit["overlay_cuts_dirty"] == 1
    assert result.audit["overlay_cuts_blurred"] == 0
    assert result.audit["overlay_cuts_blur_failed"] == 0
    blur.assert_not_called()
    cut_record = result.audit["cut_audit"][0]
    assert cut_record["status"] == "overlay_rejected"
    assert cut_record["rejection_reason"] == "obstructive_source_overlay"
    assert "subtitle" in cut_record["overlay_types"]
    assert cut_record["overlay_has_subtitle"] is True


def test_pool_renderer_does_not_require_logo_blur_service(tmp_path):
    pools = _overlay_pool(tmp_path)
    dirty = {
        "checked": True,
        "watermark_prob": 0.9,
        "has_subtitle": False,
        "clean": False,
        "regions": [{"type": "watermark", "bbox": [0.75, 0.0, 1.0, 0.25], "confidence": 90.0}],
    }

    with mock.patch.object(pipeline, "_download_source_once", side_effect=fake_overlay_source), \
         mock.patch.object(pipeline, "_cut_local_segment", side_effect=fake_overlay_cut), \
         mock.patch("services.youtube_pool_renderer._screen_moment_overlays", return_value=dirty), \
         mock.patch("services.scene_understanding.analyzers.apply_overlay_blur", return_value=False):
        result = resolve_pool_first(pipeline, pools, output_dir=tmp_path)

    assert result[0] and not result[0].endswith(".clean.mp4")
    assert result.audit["overlay_cuts_dirty"] == 1
    assert result.audit["overlay_cuts_blurred"] == 0
    assert result.audit["overlay_cuts_blur_failed"] == 0
    cut_record = result.audit["cut_audit"][0]
    assert cut_record["status"] == "accepted"
    assert cut_record["source_overlays_preserved"] is True


def test_pool_renderer_overlay_screen_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDZEO_YT_OVERLAY_SCREEN", "0")
    pools = _overlay_pool(tmp_path)

    with mock.patch.object(pipeline, "_download_source_once", side_effect=fake_overlay_source), \
         mock.patch.object(pipeline, "_cut_local_segment", side_effect=fake_overlay_cut), \
         mock.patch("services.youtube_pool_renderer._screen_moment_overlays") as screen:
        result = resolve_pool_first(pipeline, pools, output_dir=tmp_path)

    screen.assert_not_called()
    assert result.audit["overlay_cuts_screened"] == 0
    assert result[0] and not result[0].endswith(".clean.mp4")


def test_pool_renderer_avoids_repeating_exact_same_timestamps_for_pooled_sources(tmp_path):
    source = SimpleNamespace(
        video_id="shared-pool-source",
        url="https://youtube.test/shared-pool-source",
        title="Titanic Documentary",
        channel="Documentary",
        duration=600,
        metadata_score=100,
    )
    # Both scene 0 and scene 1 have candidate moments with the EXACT SAME start timestamp (start=42.0).
    # Scene 1 also has an alternate candidate with a distinct timestamp (start=120.0).
    pools = SimpleNamespace(
        source_records={"shared-pool-source": source},
        moment_assignments={
            0: [
                heuristic_moment(source_id="shared-pool-source", video_id="shared-pool-source", scene_index=0, start=42.0, duration=4.0),
            ],
            1: [
                heuristic_moment(source_id="shared-pool-source", video_id="shared-pool-source", scene_index=1, start=42.0, duration=4.0),
                heuristic_moment(source_id="shared-pool-source", video_id="shared-pool-source", scene_index=1, start=120.0, duration=4.0),
            ],
        },
        assignments=[],
        rows=[
            {"scene_text": "Scene 1 Titanic", "candidate_subject": "Titanic", "title_anchor": "Titanic"},
            {"scene_text": "Scene 2 Titanic", "candidate_subject": "Titanic", "title_anchor": "Titanic"},
        ],
        scene_durations=[4.0, 4.0],
    )
    cuts = []

    def fake_source(candidate, output_path, **_kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"source" * 100)
        return True

    def fake_cut(source_path, output_path, start, duration):
        cuts.append((output_path.name, start, duration))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"cut" * 100)
        return True

    with mock.patch.object(pipeline, "_download_source_once", side_effect=fake_source), \
         mock.patch.object(pipeline, "_cut_local_segment", side_effect=fake_cut):
        result = resolve_pool_first(pipeline, pools, output_dir=tmp_path)

    assert result[0] is not None
    assert result[1] is not None
    # Inspect retrieval decisions
    decision_0 = result.retrieval_decisions["0"]["source_moment"]
    decision_1 = result.retrieval_decisions["1"]["source_moment"]
    start_0 = decision_0["start"]
    start_1 = decision_1["start"]
    # The two scenes MUST NOT have the exact same start timestamp!
    assert abs(start_0 - start_1) >= 4.0



def test_probe_samples_transcript_moment_when_present(tmp_path, monkeypatch):
    """Transcript + frame selection: a source with a transcript moment is
    probed AT the moment timestamp; a source without one (or with only a
    heuristic moment) falls back to deterministic quarter/center sampling."""
    monkeypatch.setenv("VIDZEO_YT_POOL_PROBE_VISION_CAP", "0")
    moment = YouTubeMoment(
        source_id="with-moment", video_id="with-moment",
        start=123.0, end=129.0,
        transcript_text="the driver sees whiteout from the cab",
        match_score=88.0, scene_index=0,
        match_reason="scene_overlap", heuristic=False,
    )
    heuristic_only = heuristic_moment(
        source_id="heur-source", video_id="heur-source",
        scene_index=0, start=300.0, duration=4.0,
    )
    records = {
        "with-moment": SimpleNamespace(
            video_id="with-moment", url="https://youtube.test/with-moment",
            title="Cab ride in a snowstorm", channel="Rail Archive", duration=600,
            metadata_score=100, pool_keys=["pool-a"],
        ),
        "plain-source": SimpleNamespace(
            video_id="plain-source", url="https://youtube.test/plain-source",
            title="Freight train in snow", channel="Rail Archive", duration=600,
            metadata_score=90, pool_keys=["pool-a"],
        ),
        "heur-source": SimpleNamespace(
            video_id="heur-source", url="https://youtube.test/heur-source",
            title="Winter railroad", channel="Rail Archive", duration=600,
            metadata_score=80, pool_keys=["pool-a"],
        ),
    }
    pools = SimpleNamespace(
        rows=[{}],
        moment_assignments={0: [moment, heuristic_only]},
    )
    downloaded_starts = {}

    def fake_probe(candidate, output_path, start=0.0, *args, **kwargs):
        downloaded_starts[str(candidate.get("video_id") or "")] = float(start)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"probe" * 1000)
        return True

    class ProbePipeline:
        _download_clip = staticmethod(fake_probe)

    with mock.patch("services.youtube_pool_renderer._screen_moment_quality", return_value={"checked": True, "reject": False}), \
         mock.patch("services.youtube_pool_renderer._screen_moment_overlays", return_value={"checked": True, "clean": True, "reject": False, "overlay_types": []}):
        audit = _probe_pool_candidates(
            ProbePipeline(), pools,
            {"pool-a": ["with-moment", "plain-source", "heur-source"]},
            records=records, output_root=tmp_path,
        )

    # The moment source is probed at the moment, and the downloader really
    # received that start (not just the audit row).
    assert audit["with-moment"]["probe_start"] == 123.0
    assert downloaded_starts["with-moment"] == 123.0
    assert audit["with-moment"]["probe_window_source"] == "transcript_moment"
    assert audit["with-moment"]["probe_moment_start"] == 123.0
    assert audit["with-moment"]["probe_moment_end"] == 129.0
    assert "cab" in audit["with-moment"]["probe_moment_text"]
    assert audit["with-moment"]["probe_moment_score"] == 88.0
    assert audit["with-moment"]["probe_moment_scene"] == 0
    assert audit["with-moment"]["status"] == "accepted"

    # Sources without a real transcript match fall back to the old sampling.
    safe_max_start = 600 - 3.0 - 0.25
    for source_id in ("plain-source", "heur-source"):
        fraction = ((sum(ord(char) for char in source_id) % 3) + 1) / 4.0
        expected = round(min(safe_max_start, safe_max_start * fraction), 3)
        assert audit[source_id]["probe_start"] == expected
        assert downloaded_starts[source_id] == expected
        assert audit[source_id]["probe_window_source"] == "quarter_center"
        assert "probe_moment_start" not in audit[source_id]


def test_best_probe_moment_ignores_heuristic_and_zero_score():
    pools = SimpleNamespace(moment_assignments={
        0: [
            heuristic_moment(source_id="vid-a", video_id="vid-a", scene_index=0, start=50.0, duration=4.0),
            YouTubeMoment(source_id="vid-a", video_id="vid-a", start=10.0, end=14.0,
                          transcript_text="", match_score=0.0, scene_index=0,
                          match_reason="weak", heuristic=False),
        ],
        1: [
            YouTubeMoment(source_id="vid-b", video_id="vid-b", start=200.0, end=206.0,
                          transcript_text="cab view", match_score=40.0, scene_index=1,
                          match_reason="scene_overlap", heuristic=False),
            YouTubeMoment(source_id="vid-b", video_id="vid-b", start=90.0, end=96.0,
                          transcript_text="cab view again", match_score=72.0, scene_index=1,
                          match_reason="scene_overlap", heuristic=False),
        ],
    })
    assert _best_probe_moment(pools, "vid-a") is None
    best = _best_probe_moment(pools, "vid-b")
    assert best is not None and best.start == 90.0 and best.match_score == 72.0
    assert _best_probe_moment(pools, "missing") is None
    assert _best_probe_moment(SimpleNamespace(), "vid-b") is None


def test_prefetch_probe_transcript_moments_populates_assignments(tmp_path):
    """The pre-probe fetch hands the probe candidates to the transcript
    matcher and stores the moments on the pools object before probing."""
    moment = YouTubeMoment(
        source_id="with-moment", video_id="with-moment",
        start=123.0, end=129.0, transcript_text="whiteout from the cab",
        match_score=88.0, scene_index=0, match_reason="scene_overlap",
        heuristic=False,
    )
    calls = {}

    class PrefetchPipeline:
        def _prepare_pool_transcript_moments(self, rows, pools, *, scene_durations,
                                             force, session_jar=None, deadline_seconds=None):
            calls["pools"] = pools
            calls["deadline_seconds"] = deadline_seconds
            calls["force"] = force
            return (
                {"with-moment": SimpleNamespace(available=True)},
                {0: [moment]},
                {"with-moment": {"video_id": "with-moment"}},
            )

    pools = SimpleNamespace(
        rows=[{"scene_subject": "BNSF cab whiteout"}],
        scene_durations=[4.0],
        moment_assignments={},
    )
    records = {
        "with-moment": SimpleNamespace(
            video_id="with-moment", url="https://youtube.test/with-moment",
            title="Cab ride in a snowstorm", channel="Rail Archive", duration=600,
            metadata_score=100, pool_keys=["pool-a"],
        ),
    }
    audit = _prefetch_probe_transcript_moments(
        PrefetchPipeline(), pools, {"pool-a": ["with-moment"]}, records,
    )
    assert audit["status"] == "completed"
    assert audit["available"] == 1
    assert audit["moments"] == 1
    # Only the probe candidates were handed to the matcher, and the tight
    # pre-probe deadline was used instead of the shared 300s wall.
    assert [c.get("video_id") for c in calls["pools"]["pool-a"]] == ["with-moment"]
    assert calls["deadline_seconds"] == 60.0
    assert calls["force"] is True
    assert pools.moment_assignments == {0: [moment]}


def test_prefetch_probe_transcript_moments_never_blocks_probe():
    """A transcript failure is fail-soft: the probe still runs with empty
    moments (quarter/center fallback) instead of raising."""

    class FailingPipeline:
        def _prepare_pool_transcript_moments(self, *args, **kwargs):
            raise RuntimeError("transcript broker down")

    pools = SimpleNamespace(rows=[{}], scene_durations=[4.0], moment_assignments={})
    records = {
        "vid": SimpleNamespace(
            video_id="vid", url="https://youtube.test/vid", title="t",
            channel="c", duration=60, metadata_score=1, pool_keys=["pool-a"],
        ),
    }
    audit = _prefetch_probe_transcript_moments(
        FailingPipeline(), pools, {"pool-a": ["vid"]}, records,
    )
    assert audit["status"] == "failed"
    assert pools.moment_assignments == {}
