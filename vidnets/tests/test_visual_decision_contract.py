import os
import sys
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))


def test_overlay_regions_are_normalized_and_bounded():
    from services.visual_intelligence.decision import normalize_overlay_regions

    regions = normalize_overlay_regions([{
        "type": "creator_logo",
        "bbox": [-0.2, 0.1, 0.25, 0.4],
        "frame_indices": [0, 2, 99],
        "confidence": "88",
        "moving": "false",
    }], frame_count=3)

    assert len(regions) == 1
    assert regions[0].bbox == [0.0, 0.1, 0.25, 0.4]
    assert regions[0].frame_indices == [0, 2]
    assert regions[0].area_ratio == 0.075
    assert regions[0].confidence == 88.0


def test_clip_overlay_decision_requires_cleanup_even_at_high_score():
    from services.visual_intelligence.decision import normalize_scene_decision

    decision = normalize_scene_decision({
        "story_relevance": 92,
        "visual_quality": 92,
        "confidence": 90,
        "watermark": True,
        "overlay_regions": [{
            "type": "creator_logo",
            "bbox": [0.8, 0.02, 0.98, 0.08],
            "confidence": 90,
        }],
    }, media_type="video")

    assert decision.status == "needs_cleanup"
    assert "watermark" in decision.rejection_reasons


def test_overlay_blur_builds_one_encode_for_two_regions(tmp_path):
    from services.scene_understanding import analyzers

    source = tmp_path / "source.mp4"
    output = tmp_path / "cleaned.mp4"
    source.write_bytes(b"source")
    calls = []

    def fake_ffmpeg(args, timeout=30):
        calls.append(args)
        output.write_bytes(b"cleaned")
        return True, ""

    with mock.patch.object(analyzers, "_video_meta", return_value=(1280, 720, 30.0)), \
         mock.patch.object(analyzers, "_run_ffmpeg", side_effect=fake_ffmpeg):
        assert analyzers.apply_overlay_blur(str(source), str(output), [
            {"type": "subtitle", "bbox": [0.2, 0.78, 0.8, 0.9], "confidence": 90},
            {"type": "creator_logo", "bbox": [0.82, 0.02, 0.96, 0.08], "confidence": 90},
        ])

    assert len(calls) == 1
    assert "-filter_complex" in calls[0]
    graph = next(str(arg) for arg in calls[0] if "boxblur" in str(arg))
    # The cleanup graph uses one full-frame blur plus one feathered mask blur
    # and merges it without an opaque crop rectangle. This keeps watermark and
    # caption edges natural instead of leaving hard dark corners.
    assert graph.count("boxblur") == 2
    assert graph.count("drawbox=") == 2
    assert "maskedmerge" in graph
    assert "overlay=" not in graph


def test_overlay_blur_rejects_the_old_full_width_subtitle_band(tmp_path):
    from services.scene_understanding import analyzers

    source = tmp_path / "source.mp4"
    output = tmp_path / "cleaned.mp4"
    source.write_bytes(b"source")

    with mock.patch.object(analyzers, "_video_meta", return_value=(1280, 720, 30.0)), \
         mock.patch.object(analyzers, "_run_ffmpeg") as run_ffmpeg:
        assert not analyzers.apply_overlay_blur(str(source), str(output), [
            {"type": "subtitle", "bbox": [0.0, 0.62, 1.0, 0.98], "confidence": 80},
        ])

    run_ffmpeg.assert_not_called()


def test_overlay_detectors_return_compact_boxes(tmp_path):
    import cv2
    import numpy as np
    from services.scene_understanding import analyzers

    frames = []
    for index in range(3):
        image = np.zeros((180, 320, 3), dtype=np.uint8)
        cv2.putText(image, "PATHE", (266, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (235, 235, 235), 1)
        cv2.putText(image, "HISTORICAL FOOTAGE", (78, 166), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (235, 235, 235), 1)
        path = tmp_path / f"frame_{index}.png"
        assert cv2.imwrite(str(path), image)
        frames.append(str(path))

    logo = analyzers.detect_watermark_box(frames, "top_right")
    subtitle = analyzers.detect_subtitle_box(frames)

    assert logo is not None
    assert logo[0] > 0.75 and logo[1] < 0.2
    assert (logo[2] - logo[0]) * (logo[3] - logo[1]) < 0.03
    assert subtitle[1] > 0.65
    assert subtitle[3] - subtitle[1] < 0.14
    assert (subtitle[2] - subtitle[0]) * (subtitle[3] - subtitle[1]) < 0.12


def test_extracted_frame_overlay_cleanup_blurs_captions_without_full_frame(tmp_path):
    import cv2
    import numpy as np
    from services.scene_understanding.frame_overlay_blur import (
        apply_gaussian_overlay_blur,
        detect_overlay_regions,
    )

    frames = []
    for index in range(3):
        image = np.full((240, 420, 3), 48, dtype=np.uint8)
        cv2.rectangle(image, (20 + index * 2, 45), (185 + index * 2, 165), (90, 130, 180), -1)
        cv2.putText(image, "DOCUMENTARY CAPTION", (55, 216), cv2.FONT_HERSHEY_SIMPLEX,
                    0.65, (245, 245, 245), 2, cv2.LINE_AA)
        cv2.putText(image, "TV", (378, 30), cv2.FONT_HERSHEY_SIMPLEX,
                    0.42, (235, 235, 235), 1, cv2.LINE_AA)
        path = tmp_path / f"source_{index}.jpg"
        assert cv2.imwrite(str(path), image, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        frames.append(str(path))

    regions = detect_overlay_regions(frames[1], context_frames=frames)
    caption_regions = [r for r in regions if r.get("type") == "caption"]
    assert caption_regions, regions
    assert all((r["bbox"][2] - r["bbox"][0]) * (r["bbox"][3] - r["bbox"][1]) < 0.34
               for r in caption_regions)

    output = tmp_path / "cleaned.jpg"
    cleaned = apply_gaussian_overlay_blur(frames[1], regions, output_path=str(output))
    assert cleaned == str(output)
    assert output.is_file() and output.stat().st_size > 512
    original = cv2.imread(frames[1], cv2.IMREAD_GRAYSCALE)
    result = cv2.imread(str(output), cv2.IMREAD_GRAYSCALE)
    assert original is not None and result is not None
    # The cleanup is masked: the center remains sharp while the caption's
    # local high-frequency energy drops.
    assert float(result[205:232, 40:380].std()) < float(original[205:232, 40:380].std())
    assert float(result[45:165, 20:185].std()) >= float(original[45:165, 20:185].std()) * 0.75


def test_low_relevance_web_image_is_decisively_rejected():
    from services.visual_intelligence.decision import normalize_scene_decision

    # Irrelevant web image scored 20 relevance with 55 confidence
    decision = normalize_scene_decision({
        "story_relevance": 20.0,
        "confidence": 55.0,
        "visual_quality": 85.0,
    }, media_type="image")

    assert decision.status == "rejected"
    assert "low_relevance" in decision.rejection_reasons
