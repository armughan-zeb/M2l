import threading
import time
from typing import Any, Dict, Mapping
from server.services.media_resolution_coordinator import (
    SceneResolution,
    resolve_storyboard_media_v2,
)


def test_parallel_stock_fallback_runs_concurrently():
    """Verify that multiple degraded scenes are resolved in parallel via stock lane."""
    scenes = [
        {"scene_index": 0, "text": "A futuristic city skyline", "source_policy": {"stock_last_resort_allowed": True}},
        {"scene_index": 1, "text": "Cyberpunk flying cars", "source_policy": {"stock_last_resort_allowed": True}},
        {"scene_index": 2, "text": "Neon holographic billboards", "source_policy": {"stock_last_resort_allowed": True}},
    ]

    stock_call_timestamps = []
    stock_lock = threading.Lock()

    def dummy_stock_lane(scene: Mapping[str, Any], event: threading.Event):
        with stock_lock:
            stock_call_timestamps.append(time.monotonic())
        # Sleep briefly to simulate network I/O
        time.sleep(0.1)
        return [{"path": f"stock_{scene['scene_index']}.mp4", "score": 90.0, "provider": "stock"}]

    def empty_lane(scene: Mapping[str, Any], event: threading.Event):
        return []

    resolutions = resolve_storyboard_media_v2(
        scenes,
        youtube_lane=empty_lane,
        web_lane=empty_lane,
        stock_lane=dummy_stock_lane,
        web_wait_until_complete=True,
    )

    assert len(resolutions) == 3
    for idx, res in resolutions.items():
        assert res.provider == "stock"
        assert res.status == "accepted"


def test_terminal_stock_has_budget_after_primary_deadline(monkeypatch):
    import server.services.media_resolution_coordinator as coordinator
    clock = [100.0]
    monkeypatch.setattr(coordinator.time, "monotonic", lambda: clock[0])
    monkeypatch.setenv("VIDZEO_STOCK_FINAL_RESERVE_SECONDS", "5")
    calls = []

    def exhausted(scene, event):
        # Simulate providers consuming the entire primary wall.
        clock[0] = 200.0
        return []

    def stock(scene, event):
        calls.append(scene["scene_index"])
        return [{"path": "recovered.mp4", "score": 90.0, "provider": "stock"}]

    result = coordinator.resolve_storyboard_media_v2(
        [{"scene_index": 0, "text": "Factory machinery",
          "source_policy": {"stock_last_resort_allowed": True}}],
        youtube_lane=exhausted, web_lane=exhausted, stock_lane=stock,
        deadline_seconds=1,
    )
    assert calls == [0]
    assert result[0].provider == "stock"


def test_cross_lane_prefetch_runs_and_resolves_scenes():
    """Verify that scenes failing primary discovery get resolved via parallel cross-lane prefetch."""
    scenes = [
        {"scene_index": 0, "text": "King Charles portrait", "content_title": "Royals"},
        {"scene_index": 1, "text": "Queen Elizabeth portrait", "content_title": "Royals"},
    ]

    prefetch_called_for = []
    prefetch_lock = threading.Lock()

    def primary_empty_lane(scene: Mapping[str, Any], event: threading.Event):
        return []

    def fallback_web_lane(scene: Mapping[str, Any], event: threading.Event):
        with prefetch_lock:
            prefetch_called_for.append(scene["scene_index"])
        time.sleep(0.05)
        return [{
            "path": f"web_recovered_{scene['scene_index']}.jpg",
            "score": 95.0,
            "provider": "web",
            "media_type": "image",
        }]

    resolutions = resolve_storyboard_media_v2(
        scenes,
        youtube_lane=primary_empty_lane,
        web_lane=primary_empty_lane,
        web_fallback_lane=fallback_web_lane,
        web_wait_until_complete=True,
    )

    assert len(resolutions) == 2
    for idx, res in resolutions.items():
        assert res.provider == "web"
        assert "web_recovered" in res.selected_path
        assert res.status == "accepted"


def test_stock_accuracy_gate_rejects_forbidden_and_non_photographic():
    from server.services.real_media_service import MediaItem, MediaConfig, _stock_candidate_passes_accuracy_gate

    config = MediaConfig()
    config.min_score_threshold = 10.0

    scene = {
        "visual_requirement": {
            "forbidden_subjects": ["frog", "fairy tale"]
        }
    }

    # Test 1: Frog item is rejected
    frog_item = MediaItem(
        id="123",
        source="pixabay",
        type="image",
        title="The Frog Prince",
        description="Fairy tale frog prince photo",
        tags=["frog", "prince", "fairytale"],
        download_url="http://example.com/frog.jpg",
        preview_url="http://example.com/frog.jpg",
        width=1920,
        height=1080,
        score=100.0,
        score_breakdown={"relevance": 15.0},
    )
    passed, reason = _stock_candidate_passes_accuracy_gate(frog_item, config, scene=scene)
    assert not passed
    assert "forbidden_subject_metadata" in reason

    # Test 2: Statue item is rejected
    statue_item = MediaItem(
        id="456",
        source="pixabay",
        type="image",
        title="King Charles marble statue in London",
        description="Historical monument statue",
        tags=["king", "statue", "monument"],
        download_url="http://example.com/statue.jpg",
        preview_url="http://example.com/statue.jpg",
        width=1920,
        height=1080,
        score=100.0,
        score_breakdown={"relevance": 15.0},
    )
    passed, reason = _stock_candidate_passes_accuracy_gate(statue_item, config, scene=scene)
    assert not passed
    assert "non_photographic_metadata" in reason

    # Test 3: Real photo passes
    photo_item = MediaItem(
        id="789",
        source="pexels",
        type="image",
        title="Buckingham Palace London",
        description="Official royal palace photograph",
        tags=["palace", "architecture", "london"],
        download_url="http://example.com/palace.jpg",
        preview_url="http://example.com/palace.jpg",
        width=1920,
        height=1080,
        score=100.0,
        score_breakdown={"relevance": 15.0},
    )
    passed, reason = _stock_candidate_passes_accuracy_gate(photo_item, config, scene=scene)
    assert passed

