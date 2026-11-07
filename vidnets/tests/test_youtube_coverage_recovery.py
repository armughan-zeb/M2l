"""Coverage recovery must reach real provider/coordinator behavior."""
import os
import sys
import threading
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services import youtube_pipeline_v2 as youtube
from services.pipeline_v2_media_adapter import BatchFallbackLane, _youtube_recovery_excluded_ids
from services.media_resolution_coordinator import resolve_storyboard_media_v2


def test_recovery_excludes_only_sources_attempted_for_rejected_scene():
    audit = {"attempts": {
        "0": [{"video_id": "wrong-era"}, {"candidate_id": "static-guide"}],
        "1": [{"video_id": "keep-good-source"}],
        "2": [{"video_id": "another-scene"}],
        "-1": [{"video_id": "invalid-offset"}],
    }}
    assert _youtube_recovery_excluded_ids(audit, [0, 2, 3], [0]) == {"wrong-era", "static-guide"}


def test_dashboard_guide_does_not_outrank_actual_interior_footage():
    def score(title, scene="The Toyota Corolla cabin and dashboard changed across generations."):
        return youtube._score_candidate(
            {"title": title, "channel": "Independent Auto", "duration": 300},
            "Toyota Corolla", scene, "Toyota Corolla interior dashboard raw footage",
            {"domain_category": "automotive", "container_subject": "Toyota Corolla"},
        )
    footage = score("Toyota Corolla Interior and Dashboard Walkaround Raw Footage")
    guide = score("Toyota Corolla Interior Dashboard Warning Lights User Guide")
    assert footage > guide
    requested_guide = score("Toyota Corolla Interior Dashboard Warning Lights User Guide",
                            "How to identify Toyota Corolla dashboard warning lights.")
    assert requested_guide > guide


def test_clickbait_title_is_demoted_without_forbidding_its_footage():
    def score(title):
        return youtube._score_candidate(
            {"title": title, "channel": "Auto Archive", "duration": 300},
            "Toyota Corolla", "Toyota Corolla driving footage.", "Toyota Corolla footage",
            {"domain_category": "automotive", "container_subject": "Toyota Corolla"},
        )
    assert score("Toyota Corolla driving footage") > score("Shocking Secrets Toyota Corolla You Never Knew") > -999


def test_primary_worker_default_does_not_reintroduce_two_candidate_cap():
    import app
    payload = app._youtube_worker_payload(script="A car", title="Toyota Corolla",
        scene_texts=["A car"], scene_durations=[4], config={"image_source": "Mixed Media"},
        scene_query_overrides=[], openrouter_key="")
    assert payload["download_candidate_limit"] == 6
    assert payload["max_download_attempts_per_scene"] == 6


def _resolve_rejected_footage(*, image_passes=True, vision_unavailable=False, recovery_passes=True):
    scene = {"scene_id": "era", "scene_index": 0, "container_id": "car",
             "text": "Toyota Corolla archival driving footage", "positive_subjects": ["Toyota Corolla"],
             "source_policy": {"mixed_media": True, "youtube_quota": True}}
    def candidate(path, source):
        return {"path": path, "source": source, "provider": source,
                "media_type": "video" if source == "youtube" else "image", "score": 90}
    class Primary:
        allowed_indices = {0}
        primary_attempted_indices = {0}
        def __call__(self, *_):
            return candidate("wrong-era.mp4", "youtube")
    prepared, loads = [], []
    def loader():
        loads.append(True)
        return {0: candidate("new-source.mp4", "youtube")}
    fallback = BatchFallbackLane(loader, lambda indices: prepared.extend(indices))
    fallback.retry_rejected_primary = True
    def verify(_scene, item):
        path = item["path"]
        if path == "wrong-era.mp4":
            return {"accepted": False, "status": "unavailable" if vision_unavailable else "visual_rejected",
                    "reason": "vision_unavailable" if vision_unavailable else "wrong era",
                    "visual_available": not vision_unavailable}
        return {"accepted": image_passes if path.endswith("jpg") else recovery_passes,
                "status": "accepted" if (path.endswith("jpg") or recovery_passes) else "visual_rejected", "score": 95}
    resolved = resolve_storyboard_media_v2([scene], youtube_lane=Primary(),
        web_lane=lambda *_: candidate("correct-still.jpg", "web") if image_passes else [],
        youtube_fallback_lane=fallback, web_fallback_lane=None, visual_verifier=verify,
        neutral_factory=lambda *_: None, deadline_seconds=3)
    return resolved[0], prepared, loads


def test_rejected_quota_clip_recovers_even_when_web_image_passed():
    resolved, prepared, loads = _resolve_rejected_footage()
    assert prepared == [0]
    assert loads == [True]
    assert resolved.provider == "youtube"
    assert resolved.selected_path == "new-source.mp4"


def test_rejected_quota_clip_recovers_without_a_primary_image():
    resolved, prepared, loads = _resolve_rejected_footage(image_passes=False)
    assert prepared == [0] and loads == [True]
    assert resolved.provider == "youtube"


def test_vision_outage_does_not_launch_duplicate_download_batch():
    resolved, prepared, loads = _resolve_rejected_footage(vision_unavailable=True)
    assert prepared == [] and loads == []
    assert resolved.provider == "web"


def test_bad_recovery_does_not_replace_verified_image():
    resolved, _, loads = _resolve_rejected_footage(recovery_passes=False)
    assert loads == [True]
    assert resolved.provider == "web"


def test_adapter_prepares_rejected_primary_and_forwards_exclusions(monkeypatch, tmp_path):
    from services import pipeline_v2_media_adapter as adapter
    calls = []
    def runner(texts, durations, config, *args, **kwargs):
        calls.append(dict(config))
        return youtube.YouTubeClipResults([None], audit={
            "resolved": 0, "failure_reason": "content_rejected", "attempts": {
                "0": [{"candidate_id": "rejected-master", "status": "motion_or_quality_rejected"}],
            },
        })
    def coordinator(rows, **kwargs):
        cancel = threading.Event()
        kwargs["youtube_lane"](rows[0], cancel)
        recovery = kwargs["youtube_fallback_lane"]
        assert recovery.retry_rejected_primary
        recovery.prepare_recovery([0])
        recovery(rows[0], cancel)
        recovery(rows[0], cancel)  # One coalesced wave, never per-scene loops.
        return {"media_paths": [], "source_labels": [], "metadata": {}}, {}
    monkeypatch.setattr(adapter, "resolve_and_build_media_plan", coordinator)
    adapter.resolve_existing_provider_branches_v2(
        {"image_source": "Mixed Media", "visual_understanding": False,
         "global_media_guarantee": False, "_run_temp_dir": str(tmp_path),
         "youtube_accepted_coverage_target": 1},
        {"scene_contracts": [{"scene_id": "car", "order": 0, "duration": 4,
                              "text": "Toyota Corolla driving footage."}]},
        title="Toyota Corolla", script="Toyota Corolla driving footage.",
        render_resolution=(960, 540), youtube_runner=runner, web_runner=lambda *a, **k: {},
        stock_runner=lambda *a, **k: {}, snapshotter=lambda paths, **k: list(paths),
        neutral_asset_factory=lambda *a, **k: None, report_progress=lambda *a: None)
    assert len(calls) == 2
    assert calls[1]["youtube_excluded_video_ids"] == ["rejected-master"]
    assert calls[1]["_run_temp_dir"].endswith("youtube_content_recovery")
