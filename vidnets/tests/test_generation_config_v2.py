from __future__ import annotations

import os
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from config import normalize_generation_config  # noqa: E402
from services.scene_splitter import build_expected_scenes, calculate_scene_mode  # noqa: E402
from services.storyboard_v2 import align_script_plan_to_words, build_script_plan  # noqa: E402


def test_generation_config_keeps_content_and_output_identity_separate():
    config = normalize_generation_config(
        {
            "title": "Top 10 Extreme Jobs",
            "name": "august-client-export",
        }
    )

    assert config["content_title"] == "Top 10 Extreme Jobs"
    assert config["output_name"] == "august-client-export"
    assert config["title"] == "Top 10 Extreme Jobs"
    assert config["name"] == "august-client-export"


def test_generation_config_parses_persisted_string_booleans_fail_closed():
    config = normalize_generation_config(
        {
            "allow_relevant_stock": "false",
            "visual_understanding": "false",
            "visual_required": "false",
            "yt_lenient_acceptance": "false",
            "strict_retrieval_accuracy": "false",
        }
    )

    assert config["allow_relevant_stock"] is False
    assert config["visual_understanding"] is False
    assert config["visual_required"] is False
    assert config["yt_lenient_acceptance"] is False
    assert config["strict_retrieval_accuracy"] is False


def test_generation_config_v2_defaults_are_accuracy_first(monkeypatch):
    monkeypatch.delenv("VIDZEO_PIPELINE_V2_MODE", raising=False)
    monkeypatch.delenv("VIDZEO_QWEN_VISION_MODEL", raising=False)
    monkeypatch.delenv("VIDZEO_QWEN_VISION_FALLBACK_MODEL", raising=False)
    config = normalize_generation_config({})

    assert config["pipeline_v2_mode"] == "on"
    assert config["scene_mode"] == "Every Beat"
    assert config["media_mode"] == "automatic"
    assert config["youtube_rights_mode"] == "all_public"
    assert config["youtube_fair_use_mode"] is False
    assert config["yt_lenient_acceptance"] is False
    assert config["strict_retrieval_accuracy"] is True
    assert config["qwen_vision_model"] == "qwen/qwen3.5-flash-02-23"
    assert config["qwen_vision_fallback_model"] == "qwen/qwen3.5-flash-02-23"


def test_missing_media_uses_grounded_terminal_repair_without_emergency_media(monkeypatch):
    for key in (
        "VIDZEO_MEDIA_FALLBACK_POLICY", "VIDZEO_ALLOW_GROUNDED_GRAPHIC_FALLBACK",
        "VIDZEO_ALLOW_GROUNDED_GRAPHIC_FALLBACK_STRICT",
        "VIDZEO_GLOBAL_MEDIA_GUARANTEE", "VIDZEO_ALLOW_EMERGENCY_MEDIA",
    ):
        monkeypatch.delenv(key, raising=False)
    config = normalize_generation_config({})
    assert config["media_fallback_policy"] == "grounded_graphic"
    assert config["allow_grounded_graphic_fallback"] is True
    assert config["allow_grounded_graphic_fallback_strict"] is True
    assert config.get("allow_emergency_media", False) is False


def test_scene_grouping_stays_one_beat_even_for_legacy_two_beat_request():
    requested = normalize_generation_config({"scene_mode": "Every 2 Beats"})
    scenes = build_expected_scenes(
        "First beat. Second beat. Third beat.",
        "Every 2 Beats",
        9.0,
    )

    assert requested["scene_mode"] == "Every Beat"
    assert calculate_scene_mode(3, 1, requested_mode="Every 2 Beats") == "Every Beat"
    assert len(scenes) == 3
    assert [scene.beat_count for scene in scenes] == [1, 1, 1]


def test_storyboard_keeps_each_narration_beat_whole_instead_of_splitting_at_commas():
    script = (
        "On April 16, 1949, the Easter Parade brought a plane into Berlin about every minute "
        "as crews moved thousands of tons in a single day. "
        "The blockade ended on May 12, but flights continued to build reserves."
    )
    plan = build_script_plan(
        script,
        content_title="Berlin Airlift",
        format_hint="narrative",
        scene_mode="Every Beat",
    )

    assert [scene.text.strip() for scene in plan.scenes] == [
        "On April 16, 1949, the Easter Parade brought a plane into Berlin about every minute as crews moved thousands of tons in a single day.",
        "The blockade ended on May 12, but flights continued to build reserves.",
    ]


def test_whisper_timing_attaches_to_whole_beats_without_scene_drift():
    script = (
        "On April 16, 1949, the Easter Parade brought a plane into Berlin about every minute "
        "as crews moved thousands of tons in a single day. "
        "The blockade ended on May 12, but flights continued to build reserves."
    )
    plan = build_script_plan(
        script,
        content_title="Berlin Airlift",
        format_hint="narrative",
        scene_mode="Every Beat",
    )
    words = []
    cursor = 0.0
    for index, word in enumerate(script.split()):
        end = cursor + 0.25
        words.append({"word": word, "start": cursor, "end": end, "index": index})
        cursor = end + (0.18 if word.endswith((".", ",", ";")) else 0.06)

    timed_plan, alignment = align_script_plan_to_words(plan, words, audio_duration=cursor)

    assert alignment.token_alignment.matched_ratio == 1.0
    assert [scene.text.strip() for scene in timed_plan.scenes] == [scene.text.strip() for scene in plan.scenes]
    assert abs(timed_plan.scenes[0].start_seconds) < 0.001
    assert abs(timed_plan.scenes[-1].end_seconds - cursor) < 0.001
    for left, right in zip(timed_plan.scenes, timed_plan.scenes[1:]):
        assert abs(left.end_seconds - right.start_seconds) < 0.001


def test_automatic_mode_selects_both_primary_lanes_but_explicit_mode_is_locked():
    import app as server_app

    automatic = server_app._coerce_media_source_from_weights(
        {"image_source": "Web Images", "media_mode": "automatic", "pipeline_v2_mode": "on"}
    )
    explicit = server_app._coerce_media_source_from_weights(
        {"image_source": "Web Images", "media_mode": "source_locked", "pipeline_v2_mode": "on"}
    )

    assert automatic["image_source"] == "Mixed Media"
    assert explicit["image_source"] == "Web Images"


def test_explicit_youtube_source_is_not_promoted_to_mixed_by_automatic_mode():
    import app as server_app

    locked = server_app._coerce_media_source_from_weights(
        {"image_source": "YouTube Clips", "media_mode": "automatic", "pipeline_v2_mode": "on"}
    )

    assert locked["image_source"] == "YouTube Clips"
