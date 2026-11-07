"""Regression tests for the read-only pre-V2 storyboard adapter."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from services.storyboard_v2 import (  # noqa: E402
    ContractValidationError,
    GenerationRequestV2,
    StoryboardStore,
    StoryboardV2,
    build_script_plan,
    load_storyboard_compatible,
    upgrade_storyboard_v1,
)


def test_parallel_v1_arrays_become_container_pure_degraded_v2_contracts():
    legacy = {
        "schema_version": "1.0",
        "title": "Two inventions",
        "name": "old-export",
        "scene_texts": [
            "Number 1: Alpha was introduced in 1991.",
            "Number 2: Beta followed in 1993.",
        ],
        "scene_durations": [4.0, 5.0],
        "media_paths": [r"C:\legacy\alpha.jpg", r"C:\legacy\beta.mp4"],
        "source_labels": ["Web Images", "YouTube Clips"],
        "text_cards": [
            {
                "scene_index": 0,
                "text": "Alpha",
                "scene_local_start_seconds": 0.4,
                "scene_local_end_seconds": 1.4,
                "absolute_start_seconds": 0.4,
                "absolute_end_seconds": 1.4,
            },
            {
                "scene_index": 1,
                "text": "KEY POINT",
                "scene_local_start_seconds": 0.3,
                "scene_local_end_seconds": 1.3,
                "absolute_start_seconds": 4.3,
                "absolute_end_seconds": 5.3,
            },
        ],
        "api_key": "must-not-migrate",
    }

    storyboard = upgrade_storyboard_v1(legacy, job_id="job-old", run_id="run-read")

    assert storyboard.schema_version == "2.0"
    assert storyboard.request.content_title == "Two inventions"
    assert storyboard.request.output_name == "old-export"
    assert storyboard.request.pipeline_v2_mode == "compat_read_only"
    assert storyboard.script_plan.detected_format == "listicle"
    assert len(storyboard.script_plan.scenes) == 2
    assert len({scene.container_id for scene in storyboard.script_plan.scenes}) == 2
    assert "".join(scene.text for scene in storyboard.script_plan.scenes) == storyboard.request.script
    assert [scene.start_seconds for scene in storyboard.script_plan.scenes] == [0.0, 4.0]
    assert [scene.end_seconds for scene in storyboard.script_plan.scenes] == [4.0, 9.0]

    assert len(storyboard.media_candidates) == 2
    for scene, candidate in zip(storyboard.script_plan.scenes, storyboard.media_candidates):
        assert candidate.scene_id == scene.scene_id
        assert candidate.container_id == scene.container_id
        assert candidate.status == "migrated_unverified"
        assert candidate.rights_status == "unknown"
        assert candidate.qwen_result["status"] == "not_run"
        assert candidate.gate_outcomes["relevance_verified"] is False
        assert candidate.gate_outcomes["eligible_for_automatic_render"] is False
        assert storyboard.scene_states[scene.scene_id]["terminal_state"] == "degraded"
        assert storyboard.scene_states[scene.scene_id]["automatic_render_eligible"] is False

    assert len(storyboard.editorial_cues) == 1
    cue = storyboard.editorial_cues[0]
    assert cue.text == "Alpha"
    assert storyboard.request.script[cue.char_start:cue.char_end] == cue.text
    assert cue.timing_quality == "legacy_explicit_unverified"
    assert "KEY POINT" not in json.dumps(storyboard.to_dict())
    assert "must-not-migrate" not in json.dumps(storyboard.to_dict())


def test_component_manifest_v1_load_is_read_only_and_keeps_paths_unverified(tmp_path):
    legacy_path = tmp_path / "saved-project.storyboard.json"
    legacy = {
        "schema_version": "1.0",
        "name": "Saved editor project",
        "duration": 6.0,
        "video_clips": [
            {
                "path": r"C:\old\one.jpg",
                "start": 0,
                "end": 3,
                "track_offset": 0,
                "source": "Web Images",
                "media_type": "image",
            },
            {
                "path": r"C:\old\two.mp4",
                "start": 0,
                "end": 3,
                "track_offset": 3,
                "source": "YouTube Clips",
                "media_type": "video",
            },
        ],
        "metadata": {
            "source_scene_texts": [
                "The opening establishes the subject clearly.",
                "The conclusion explains the outcome clearly.",
            ]
        },
    }
    original = json.dumps(legacy, sort_keys=True, indent=2)
    legacy_path.write_text(original, encoding="utf-8")

    store = StoryboardStore(legacy_path, job_id="queue-17", run_id="compat-view")
    storyboard = store.load_compatible()

    assert legacy_path.read_text(encoding="utf-8") == original
    assert storyboard.job_id == "queue-17"
    assert storyboard.run_id == "compat-view"
    assert storyboard.schema_version == "2.0"
    assert len(storyboard.media_candidates) == 2
    assert storyboard.media_candidates[0].local_path == r"C:\old\one.jpg"
    assert storyboard.media_candidates[1].media_type == "video"
    assert all(candidate.status != "accepted" for candidate in storyboard.media_candidates)
    event = storyboard.attempt_ledger[0]
    assert event.event_type == "compat.v1_loaded"
    assert event.payload["automatic_render_eligible"] is False


def test_cross_item_v1_scene_is_reparsed_and_its_media_is_not_assigned():
    script = "Number 1: Alpha is first.\nNumber 2: Beta is second."
    legacy = {
        "schema_version": "1.0",
        "script": script,
        "scene_texts": [script],
        "scene_durations": [6.0],
        "media_paths": [r"C:\legacy\ambiguous.jpg"],
        "source_labels": ["Web Images"],
    }

    storyboard = upgrade_storyboard_v1(legacy)

    assert len(storyboard.script_plan.leaf_containers) == 2
    assert len(storyboard.script_plan.scenes) == 2
    assert storyboard.media_candidates == ()
    assert all(
        state["media_status"] == "unresolved"
        for state in storyboard.scene_states.values()
    )
    assert storyboard.attempt_ledger[0].payload["scene_alignment"] == (
        "canonical_v2_reparse_legacy_scene_crossed_container"
    )
    assert storyboard.attempt_ledger[0].payload["mapped_scene_count"] == 0


def test_ambiguous_or_untimed_legacy_cards_are_dropped():
    legacy = {
        "scene_texts": ["the signal faded, then the signal returned."],
        "scene_durations": [5.0],
        "editorial_cues": [
            {
                "scene_index": 0,
                "text": "the signal",
                "scene_local_start_seconds": 0.5,
                "scene_local_end_seconds": 1.5,
                "absolute_start_seconds": 0.5,
                "absolute_end_seconds": 1.5,
            },
            {"scene_index": 0, "text": "returned"},
        ],
    }

    storyboard = upgrade_storyboard_v1(legacy)

    assert storyboard.editorial_cues == ()
    scene = storyboard.script_plan.scenes[0]
    assert storyboard.scene_states[scene.scene_id]["legacy_cue_status"] == (
        "dropped_unverified_legacy_cue"
    )


def test_loader_returns_native_v2_without_migration():
    request = GenerationRequestV2.from_mapping(
        {"script": "A complete native storyboard narration.", "title": "Native"}
    )
    plan = build_script_plan(request.script, content_title=request.content_title)
    native = StoryboardV2.new(
        job_id="native-job",
        run_id="native-run",
        request=request,
        script_plan=plan,
    )

    loaded = load_storyboard_compatible(native.to_dict())

    assert loaded.to_dict() == native.to_dict()
    assert loaded.attempt_ledger == ()


def test_media_only_v1_manifest_fails_instead_of_inventing_narration():
    with pytest.raises(ContractValidationError, match="no script or scene narration"):
        upgrade_storyboard_v1(
            {
                "schema_version": "1.0",
                "video_clips": [{"path": r"C:\old\only.jpg", "start": 0, "end": 3}],
            }
        )


def test_store_compat_loader_refuses_another_job_scope(tmp_path):
    path = tmp_path / "wrong-job.storyboard.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "job_id": "job-a",
                "run_id": "run-a",
                "scene_texts": ["A complete legacy narration scene."],
            }
        ),
        encoding="utf-8",
    )
    store = StoryboardStore(path, job_id="job-b", run_id="run-a")

    with pytest.raises(ContractValidationError, match="job_id"):
        store.load_compatible()
