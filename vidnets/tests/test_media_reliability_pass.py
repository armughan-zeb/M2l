from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def test_verified_youtube_cut_cache_round_trip(tmp_path, monkeypatch):
    from services import youtube_media_cache as cache

    monkeypatch.setenv("VIDZEO_YT_CUT_CACHE_DIR", str(tmp_path / "cut-cache"))
    cache._INITIALIZED.clear()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"xxxxftypisom" + (b"x" * 12_000))

    stored = cache.put_verified_cut(
        "video123", 12.345, 4.0, str(source), metadata={"overlay_clean": True}
    )
    assert stored
    restored = cache.get_verified_cut("video123", 12.345, 4.0)
    assert restored
    assert Path(restored["path"]).is_file()
    assert restored["metadata"]["overlay_clean"] is True


def test_youtube_search_coalesces_duplicate_inflight_requests(monkeypatch):
    import services.youtube_pipeline_v2 as pipeline

    with pipeline._SEARCH_FLIGHT_LOCK:
        pipeline._SEARCH_FLIGHTS.clear()
        pipeline._SEARCH_MEMORY.clear()
    calls = []
    owner_started = threading.Event()

    def fake_search(query, max_results=12, session_jar=None, cancellation=None):
        calls.append(query)
        owner_started.set()
        time.sleep(0.05)
        return [{"id": "abc123", "title": "Relevant footage"}]

    monkeypatch.setattr(pipeline, "_search_youtube_uncached", fake_search)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(pipeline._search_youtube, "same query")
        assert owner_started.wait(timeout=2)
        second = executor.submit(pipeline._search_youtube, "same query")
        futures = [first, second]
        values = [future.result(timeout=3) for future in futures]
    assert len(calls) == 1
    assert all(value and value[0]["id"] == "abc123" for value in values)


def test_rights_classification_surfaces_review_risk():
    from services.media_provenance import classify_rights

    youtube = classify_rights({"provider": "youtube", "url": "https://youtube.com/watch?v=x"})
    assert youtube["rights_status"] == "platform_terms_review_required"
    assert youtube["rights_review_required"] is True
    open_media = classify_rights({"provider": "wikimedia", "license": "CC BY-SA 4.0"})
    assert open_media["rights_status"] == "reported_open_license"
    stock = classify_rights({"provider": "yandex", "source_url": "https://www.bigstockphoto.com/x"})
    assert stock["rights_status"] == "commercial_license_required"


def test_persistent_text_overlay_detector_catches_higher_lower_third(tmp_path):
    import cv2
    import numpy as np
    from services.scene_understanding.analyzers import detect_persistent_text_overlay_box

    frames = []
    for index in range(4):
        image = np.zeros((180, 320, 3), dtype="uint8") + 40
        cv2.putText(
            image, "CHANNEL TEXT", (50, 115), cv2.FONT_HERSHEY_SIMPLEX,
            0.75, (255, 255, 255), 2, cv2.LINE_AA,
        )
        path = tmp_path / f"frame_{index}.jpg"
        cv2.imwrite(str(path), image)
        frames.append(str(path))
    box = detect_persistent_text_overlay_box(frames)
    assert box is not None
    assert box[1] >= 0.45


def test_circular_facecam_is_hard_rejected_for_inherited_frame(tmp_path):
    import cv2
    import numpy as np
    from services.scene_understanding.analyzers import screen_extracted_frame

    image = np.zeros((406, 720, 3), dtype="uint8") + 48
    cv2.circle(image, (645, 332), 70, (245, 245, 245), 5)
    cv2.rectangle(image, (585, 272), (705, 392), (95, 95, 95), -1)
    cv2.circle(image, (645, 332), 70, (245, 245, 245), 5)
    # A high-contrast circle anchored in the lower-right corner represents
    # the common round creator-camera layout. The inherited-frame gate must
    # reject it without relying on a semantic query or parent clip verdict.
    path = tmp_path / "circular_facecam.jpg"
    cv2.imwrite(str(path), image)
    decision = screen_extracted_frame(str(path))
    assert decision["accepted"] is False
    assert decision["rejection_reason"] == "frame_facecam_inset_rejected"


def test_persistent_stationary_facecam_is_rejected_while_moving_actors_are_preserved(tmp_path):
    from unittest.mock import patch
    from services.scene_understanding.analyzers import detect_facecam_inset
    from services.pipeline_v2_media_adapter import _youtube_alternate_attempt_allowed

    frames = [str(tmp_path / f"frame_{i}.jpg") for i in range(4)]
    for f in frames:
        Path(f).write_bytes(b"dummy")

    # 1. Stationary facecam in mid_left (streamer webcam)
    stationary_detections = [
        {"frame_width": 640, "frame_height": 360, "faces": [(64, 144, 27, 27)]},
        {"frame_width": 640, "frame_height": 360, "faces": [(63, 144, 28, 28)]},
        {"frame_width": 640, "frame_height": 360, "faces": [(64, 145, 26, 26)]},
        {"frame_width": 640, "frame_height": 360, "faces": [(67, 141, 26, 26)]},
    ]
    with patch("services.scene_understanding.analyzers.detect_faces", side_effect=stationary_detections):
        res = detect_facecam_inset(frames)
        assert res["facecam_inset"] is True
        assert res["facecam_corner"] == "mid_left"
        assert res["facecam_confidence"] >= 0.60

    # 2. Moving actors across the top-right quadrant (clean trailer footage)
    moving_detections = [
        {"frame_width": 640, "frame_height": 360, "faces": [(424, 10, 27, 27)]},
        {"frame_width": 640, "frame_height": 360, "faces": [(478, 74, 71, 71)]},
        {"frame_width": 640, "frame_height": 360, "faces": []},
        {"frame_width": 640, "frame_height": 360, "faces": [(288, 224, 56, 56)]},
    ]
    with patch("services.scene_understanding.analyzers.detect_faces", side_effect=moving_detections):
        res = detect_facecam_inset(frames)
        assert res["facecam_inset"] is False
        assert res["facecam_corner"] == ""

    # 3. Alternate attempt allowed guardrail
    bad_attempt = {
        "status": "downloaded",
        "facecam_inset": True,
        "rejection_reason": "facecam_inset_rejected",
    }
    assert _youtube_alternate_attempt_allowed(bad_attempt) is False

    clean_attempt = {
        "status": "downloaded",
        "facecam_inset": False,
        "overlay_types": [],
    }
    assert _youtube_alternate_attempt_allowed(clean_attempt) is True

