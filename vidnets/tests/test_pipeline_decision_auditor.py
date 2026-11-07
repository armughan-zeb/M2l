import os
from pathlib import Path
import pytest
from services.pipeline_decision_auditor import (
    audit_youtube_quota,
    audit_distant_timestamp_reuse,
    audit_llm_decisions,
    audit_pipeline_health,
    audit_historical_run,
)


def test_audit_youtube_quota_accurate_allocation():
    # 8 scenes with 50% target = 4 YouTube scenes
    media_plan_50 = {
        "source_labels": [
            "YouTube Clips", "Web Images", "YouTube Clips", "Web Images",
            "YouTube Clips", "Web Images", "YouTube Clips", "Web Images",
        ],
        "media_paths": [f"clip_{i}.mp4" for i in range(8)],
    }
    result = audit_youtube_quota(media_plan_50, requested_percentage=50, total_scenes=8)
    assert result["status"] == "pass"
    assert result["actual_count"] == 4
    assert result["expected_count"] == 4
    assert result["meets_target"] is True

    # 8 scenes with 25% target = 2 YouTube scenes
    result_25 = audit_youtube_quota(media_plan_50, requested_percentage="25%", total_scenes=8)
    assert result_25["actual_count"] == 4  # Provides even more than 25%
    assert result_25["meets_target"] is True


def test_audit_distant_timestamp_reuse_detects_clean_reuse():
    # Single YouTube video URL reused across 3 scenes at distant timestamps
    media_plan = {
        "media_paths": ["/tmp/yt1_cut1.mp4", "/tmp/web1.jpg", "/tmp/yt1_cut2.mp4", "/tmp/yt1_cut3.mp4"],
        "source_labels": ["YouTube Clips", "Web Images", "YouTube Clips", "YouTube Clips"],
        "metadata": {
            "youtube_pipeline_v2": {
                "assigned_cuts": [
                    {"source_url": "https://youtube.com/watch?v=abc12345", "scene_index": 0, "start": 10.0, "end": 15.0},
                    {"source_url": "https://youtube.com/watch?v=abc12345", "scene_index": 2, "start": 45.0, "end": 50.0},
                    {"source_url": "https://youtube.com/watch?v=abc12345", "scene_index": 3, "start": 90.0, "end": 95.0},
                ]
            }
        },
    }
    result = audit_distant_timestamp_reuse(media_plan, min_gap_seconds=10.0)
    assert result["status"] == "pass"
    assert result["reused_sources_count"] == 1
    assert result["total_youtube_cuts_tracked"] == 3
    assert result["zero_collision_guarantee"] is True
    assert len(result["overlaps_found"]) == 0
    assert result["valid_distant_reuses_count"] == 2


def test_audit_distant_timestamp_reuse_flags_overlap():
    # Overlapping cuts from the same YouTube URL
    media_plan = {
        "metadata": {
            "youtube_pipeline_v2": {
                "assigned_cuts": [
                    {"source_url": "https://youtube.com/watch?v=collision", "scene_index": 0, "start": 10.0, "end": 20.0},
                    {"source_url": "https://youtube.com/watch?v=collision", "scene_index": 1, "start": 15.0, "end": 25.0},
                ]
            }
        }
    }
    result = audit_distant_timestamp_reuse(media_plan, min_gap_seconds=5.0)
    assert result["status"] == "fail"
    assert result["zero_collision_guarantee"] is False
    assert len(result["overlaps_found"]) == 1
    assert result["overlaps_found"][0]["overlap_seconds"] == 5.0


def test_audit_llm_decisions_tracks_reliability():
    meta = {
        "jev_decision_history": [
            {"service": "jev_alpha", "confidence": 0.95, "selected_candidate_id": "cand_1"},
            {"service": "openrouter_chat_fallback", "used_fallback": True, "confidence": 0.88, "selected_candidate_id": "cand_2"},
        ]
    }
    result = audit_llm_decisions(meta)
    assert result["status"] == "pass"
    assert result["total_decisions"] == 2
    assert result["typed_valid_decisions"] == 2
    assert result["fallback_decisions"] == 1
    assert result["decision_reliability_score"] == 100.0


def test_audit_pipeline_health_strong_base_vs_fallback():
    # Strong base: 7 primary scenes, 1 fallback scene (12.5% fallback < 25% threshold)
    healthy_plan = {
        "media_paths": [f"/tmp/media_{i}.mp4" for i in range(8)],
        "source_labels": [
            "YouTube Clips", "Web Images", "YouTube Clips", "Web Images",
            "YouTube Clips", "Web Images", "YouTube Clips", "Stock Media Fallback",
        ],
    }
    health_result = audit_pipeline_health(healthy_plan, max_fallback_ratio=0.25)
    assert health_result["status"] == "pass"
    assert health_result["healthy_strong_baseline"] is True
    assert health_result["missing_slots_count"] == 0
    assert health_result["primary_ratio"] == 0.875
    assert health_result["fallback_ratio"] == 0.125
    assert health_result["fallbacks_are_emergency_only"] is True

    # Flawed base: missing media slots
    broken_plan = {
        "media_paths": ["/tmp/media_0.mp4", None, "/tmp/media_2.mp4"],
        "source_labels": ["YouTube Clips", "", "Web Images"],
    }
    broken_result = audit_pipeline_health(broken_plan)
    assert broken_result["status"] == "fail"
    assert broken_result["missing_slots_count"] == 1
    assert broken_result["missing_scene_indices"] == [1]


def test_audit_historical_past_run():
    # Audit real historical run from output/
    sample_dir = Path("output/02_football_the_goalkeeper_s_split_second_problem_20261001_084054_30168a8e")
    if sample_dir.exists():
        result = audit_historical_run(sample_dir)
        assert "run_directory" in result
        assert "render_failure" in result
        assert "Creative frame review requires scene redesign" in str(result["render_failure"])
