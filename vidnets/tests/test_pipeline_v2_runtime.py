"""Production-boundary tests for the Storyboard V2 runtime bridge."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from services.pipeline_v2_runtime import (  # noqa: E402
    V2PipelineRuntime,
    build_aligned_audio_plan_v2,
    pipeline_v2_active,
    pipeline_v2_mode,
    pipeline_v2_visible,
)
from services.storyboard_v2 import EditorialCueV2, tokenize_source  # noqa: E402


def _config(script: str, *, name: str = "client-export") -> dict:
    return {
        "script": script,
        "title": "Extreme Work",
        "name": name,
        "format_hint": "auto",
        "language": "en",
        "scene_mode": "adaptive",
        "media_mode": "automatic",
        "pipeline_v2_mode": "on",
        "job_id": "job-runtime-test",
    }


def _listicle_script(item_count: int = 2) -> str:
    rows = [
        "Number 1: Cave Diver. Divers inspect submerged tunnels and repair safety lines.",
        "Number 2: Tower Climber. Climbers maintain antennas above crowded cities.",
        "Number 3: Volcano Guide. Guides monitor gas and lead visitors along marked paths.",
    ]
    return "\n".join(rows[:item_count])


def _whisper_rows(script: str, *, step: float = 0.24):
    rows = []
    for index, token in enumerate(tokenize_source(script)):
        rows.append(
            {
                "word": token.text,
                "start": round(index * step, 6),
                "end": round(index * step + step * 0.72, 6),
                "probability": 0.99,
            }
        )
    duration = round(len(rows) * step + 0.4, 6)
    return rows, duration


def test_pipeline_mode_helpers_fail_safe_to_on():
    assert pipeline_v2_mode({}) == "on"
    assert pipeline_v2_mode({"pipeline_v2_mode": " SHADOW "}) == "shadow"
    assert pipeline_v2_mode({"pipeline_v2_mode": "unexpected"}) == "on"
    assert pipeline_v2_active({"pipeline_v2_mode": "on"})
    assert not pipeline_v2_active({"pipeline_v2_mode": "shadow"})
    assert pipeline_v2_visible({"pipeline_v2_mode": "shadow"})
    assert not pipeline_v2_visible({"pipeline_v2_mode": "off"})


def test_runtime_honors_queue_owned_job_and_run_scope(tmp_path):
    config = _config(_listicle_script(2), name="queue-scoped")
    config.update({
        "job_id": "stale-imported-job",
        "run_id": "stale-imported-run",
        "_queue_job_id": "queue-job-42",
        "_queue_run_id": "queue-job-42-run-0003-abcdef123456",
    })

    runtime = V2PipelineRuntime.create(
        config,
        output_path=tmp_path / "queue-scoped.mp4",
    )

    assert runtime.storyboard.job_id == "queue-job-42"
    assert runtime.storyboard.run_id == "queue-job-42-run-0003-abcdef123456"
    assert runtime.store.job_id == runtime.storyboard.job_id
    assert runtime.store.run_id == runtime.storyboard.run_id
    assert config["_ffmpeg_smoke"]["ok"] is True
    assert Path(config["_ffmpeg_path"]).is_file()
    assert Path(config["_ffprobe_path"]).is_file()


def test_runtime_sanitizes_unsafe_scope_ids_without_alias_collisions(tmp_path):
    first_config = _config(_listicle_script(1), name="unsafe-one")
    first_config.update({"_queue_job_id": "../job shared", "_queue_run_id": "../run?shared"})
    second_config = _config(_listicle_script(1), name="unsafe-two")
    second_config.update({"_queue_job_id": "..\\job/shared", "_queue_run_id": "../run*shared"})

    first = V2PipelineRuntime.create(first_config, output_path=tmp_path / "unsafe-one.mp4")
    second = V2PipelineRuntime.create(second_config, output_path=tmp_path / "unsafe-two.mp4")

    for scope_id in (
        first.storyboard.job_id,
        first.storyboard.run_id,
        second.storyboard.job_id,
        second.storyboard.run_id,
    ):
        assert scope_id
        assert all(character.isalnum() or character in "._-" for character in scope_id)
        assert "/" not in scope_id
        assert "\\" not in scope_id
    assert first.storyboard.job_id != second.storyboard.job_id
    assert first.storyboard.run_id != second.storyboard.run_id


def test_runtime_create_expected_and_audio_alignment_are_one_stable_storyboard(tmp_path):
    script = _listicle_script(2)
    output_path = tmp_path / "client-export.mp4"
    runtime = V2PipelineRuntime.create(_config(script), output_path=output_path)
    original_scene_ids = [scene.scene_id for scene in runtime.script_plan.scenes]
    original_container_ids = [scene.container_id for scene in runtime.script_plan.scenes]

    assert runtime.request.content_title == "Extreme Work"
    assert runtime.request.output_name == "client-export"
    assert runtime.storyboard.job_id == "job-runtime-test"
    assert Path(runtime.snapshot_path).name == "client-export.storyboard.json"
    assert Path(runtime.snapshot_path).is_file()
    assert runtime.storyboard.attempt_ledger[0].event_type == "script_plan_created"
    assert runtime.script_plan.detected_format == "listicle"

    expected = runtime.expected_plan(estimated_duration=12.0)
    assert expected["script_plan_v2"] is runtime.script_plan
    assert [scene.scene_id for scene in expected["scene_contracts"]] == original_scene_ids
    assert sum(expected["scene_durations"]) == pytest.approx(12.0, abs=1e-5)
    assert expected["storyboard_path"] == runtime.snapshot_path
    assert all(
        scene.container_id in {container.container_id for container in expected["containers"]}
        for scene in expected["scene_contracts"]
    )

    words, duration = _whisper_rows(script)
    aligned = runtime.align_audio(words, audio_duration=duration)
    scenes = aligned["scene_contracts"]

    assert [scene.scene_id for scene in scenes] == original_scene_ids
    assert [scene.container_id for scene in scenes] == original_container_ids
    assert scenes[0].start_seconds == 0.0
    assert scenes[-1].end_seconds == pytest.approx(duration)
    assert all(
        left.end_seconds == pytest.approx(right.start_seconds)
        for left, right in zip(scenes, scenes[1:])
    )
    assert all(scene.timing_quality == "exact" for scene in scenes)
    assert sum(aligned["scene_durations"]) == pytest.approx(duration, abs=1e-5)
    assert len(runtime.storyboard.word_timings) == len(words)
    assert [entry.event_type for entry in runtime.storyboard.attempt_ledger] == [
        "script_plan_created",
        "audio_alignment_completed",
    ]

    restored = runtime.store.load()
    assert restored.to_dict() == runtime.storyboard.to_dict()
    assert [scene.scene_id for scene in restored.script_plan.scenes] == original_scene_ids


def test_build_aligned_audio_plan_uses_runtime_and_estimates_when_words_are_missing(tmp_path):
    script = _listicle_script(2)
    runtime = V2PipelineRuntime.create(
        _config(script, name="estimated"), output_path=tmp_path / "estimated.mp4"
    )
    expected = runtime.expected_plan(8.0)

    result = build_aligned_audio_plan_v2(
        expected,
        [],
        audio_path=str(tmp_path / "voice.wav"),
        audio_duration=8.0,
        scene_mode="adaptive",
        runtime=runtime,
    )

    assert result["audio_path"].endswith("voice.wav")
    assert result["duration"] == 8.0
    assert result["whisper_words"] == []
    assert all(scene.timing_quality == "estimated" for scene in result["scene_contracts"])
    assert sum(result["scene_durations"]) == pytest.approx(8.0, abs=1e-5)
    assert runtime.storyboard.attempt_ledger[-1].payload["reason"] == "whisper_words_unavailable"


def test_media_resolution_persistence_keeps_scene_container_and_evidence(tmp_path):
    script = _listicle_script(3)
    runtime = V2PipelineRuntime.create(
        _config(script, name="media"), output_path=tmp_path / "media.mp4"
    )
    image_path = tmp_path / "verified-image.jpg"
    image_bytes = b"verified-image-content" * 64
    image_path.write_bytes(image_bytes)

    runtime.persist_media_resolutions(
        {
            0: {
                "status": "accepted",
                "selected_path": str(image_path),
                "provider": "wikimedia",
                "source_url": "https://commons.example.test/file",
                "media_type": "image",
                "query_used": "Cave Diver underwater tunnel",
                "rights_status": "licensed",
                "license": "CC BY 4.0",
                "license_url": "https://creativecommons.org/licenses/by/4.0/",
                "creator": "Example Creator",
                "metadata_score": 8.5,
                "verification_status": "accepted",
                "resolution_kind": "unique",
                "candidate_id": "broker-candidate-1",
                "asset_fingerprint": "provider-fingerprint",
                "visual_decision": {
                    "passed": True,
                    "identity_score": 0.96,
                    "subject_bbox": [0.1, 0.2, 0.5, 0.8],
                },
                "gate_outcomes": {"container_gate": "pass"},
                "attempts": [{"provider": "wikimedia", "status": "accepted"}],
            },
            1: {
                "status": "accepted",
                "selected_path": "",
                "provider": "neutral",
                "verification_status": "not_applicable",
                "fallback_reason": "degraded:no_verified_media",
                "resolution_kind": "neutral",
            },
            # Scene 2 is deliberately absent: it must remain explicitly unresolved.
        }
    )

    scenes = runtime.script_plan.scenes
    candidate = runtime.storyboard.media_candidates[0]
    assert len(runtime.storyboard.media_candidates) == 1
    assert candidate.scene_id == scenes[0].scene_id
    assert candidate.container_id == scenes[0].container_id
    assert candidate.fingerprint == hashlib.sha256(image_bytes).hexdigest()
    assert candidate.rights_status == "licensed"
    assert candidate.license == "CC BY 4.0"
    assert candidate.attribution == "Example Creator"
    assert candidate.qwen_result["identity_score"] == 0.96
    assert candidate.gate_outcomes["container_gate"] == "pass"
    assert candidate.gate_outcomes["license_url"].startswith("https://creativecommons.org")

    states = runtime.storyboard.scene_states
    assert states[scenes[0].scene_id]["status"] == "accepted"
    assert states[scenes[1].scene_id]["status"] == "degraded"
    assert states[scenes[2].scene_id] == {
        "status": "unresolved",
        "degraded_reason": "resolver_missing_terminal_state",
    }
    summary = runtime.storyboard.attempt_ledger[-1]
    assert summary.event_type == "media_resolution_completed"
    assert dict(summary.payload) == {"accepted": 1, "degraded": 1, "unresolved": 1}

    restored = runtime.store.load()
    assert restored.media_candidates[0].to_dict() == candidate.to_dict()
    assert restored.scene_states == states


def test_rejected_candidates_survive_reload_and_final_manifest_without_credentials(tmp_path):
    runtime = V2PipelineRuntime.create(
        _config(_listicle_script(1), name="evidence"), output_path=tmp_path / "evidence.mp4"
    )
    runtime.persist_media_resolutions({0: {
        "status": "degraded", "provider": "graphic",
        "fallback_reason": "degraded:no_verified_media",
        "attempts": [{"stage": "visual_gate", "status": "vision_unavailable"}],
        "candidate_ledger": [{"id": "downloaded-alternative", "status": "pending",
                              "authorization": "private-test-secret"}],
        "alternates": [{"id": "second-source", "query": "locomotive cab"}],
    }})
    scene_id = runtime.script_plan.scenes[0].scene_id
    restored = runtime.store.load()
    state = restored.scene_states[scene_id]
    assert state["candidate_ledger"][0]["id"] == "downloaded-alternative"
    assert state["candidate_ledger"][0]["authorization"] == "[REDACTED]"
    manifest = runtime.finalize(video_path=str(tmp_path / "evidence.mp4"), component_manifest={})
    text = Path(manifest).read_text(encoding="utf-8")
    exported = json.loads(text)["media_evidence"]
    assert exported["scene_states"][scene_id]["attempts"][0]["status"] == "vision_unavailable"
    assert exported["scene_states"][scene_id]["alternates"][0]["id"] == "second-source"
    assert exported["run_id"] == restored.run_id
    assert "private-test-secret" not in text


def test_media_resolution_persistence_drops_malformed_video_range(tmp_path):
    runtime = V2PipelineRuntime.create(
        _config(_listicle_script(1), name="malformed-range"),
        output_path=tmp_path / "malformed-range.mp4",
    )
    media_path = tmp_path / "clip.mp4"
    media_path.write_bytes(b"clip" * 64)

    runtime.persist_media_resolutions({
        0: {
            "status": "accepted",
            "selected_path": str(media_path),
            "provider": "youtube",
            "media_type": "video",
            # A recovered provider row can contain a zero-length range.  It
            # should be persisted as a full-asset video, not fail the job.
            "video_start": 18.0,
            "video_end": 18.0,
        }
    })

    candidate = runtime.storyboard.media_candidates[0]
    assert candidate.media_type == "video"
    assert candidate.video_start_seconds is None
    assert candidate.video_end_seconds is None


def test_manifest_cue_aliases_are_normalized_and_persisted_as_canonical_cues(tmp_path):
    script = _listicle_script(2)
    runtime = V2PipelineRuntime.create(
        _config(script, name="cues"), output_path=tmp_path / "cues.mp4"
    )
    words, duration = _whisper_rows(script)
    runtime.align_audio(words, audio_duration=duration)
    scene = runtime.script_plan.scenes[0]
    cue = EditorialCueV2(
        cue_id="cue-runtime-1",
        scene_id=scene.scene_id,
        container_id=scene.container_id,
        cue_type="item_heading",
        text="Number 1: Cave Diver",
        char_start=scene.char_start,
        char_end=scene.char_start + len("Number 1: Cave Diver"),
        token_start=scene.token_start,
        token_end=scene.token_start + 4,
        word_start_index=scene.word_start_index,
        word_end_index=(scene.word_start_index or 0) + 4,
        scene_local_start_seconds=0.08,
        scene_local_end_seconds=1.55,
        absolute_start_seconds=float(scene.start_seconds or 0.0) + 0.08,
        absolute_end_seconds=float(scene.start_seconds or 0.0) + 1.55,
        timing_quality="exact",
        template="listicle-build",
        props={"title": "Number 1: Cave Diver"},
        placement={"position": "top_left"},
        audit_reason="explicit_container_heading",
    )
    manifest_row = cue.to_dict()
    manifest_row["scene_local_start"] = manifest_row.pop("scene_local_start_seconds")
    manifest_row["scene_local_end"] = manifest_row.pop("scene_local_end_seconds")
    manifest_row["absolute_start"] = manifest_row.pop("absolute_start_seconds")
    manifest_row["absolute_end"] = manifest_row.pop("absolute_end_seconds")
    manifest_row["schema_version"] = 2
    manifest_row["renderer_only_field"] = "ignored"

    runtime.persist_editorial_cues([manifest_row])

    assert len(runtime.storyboard.editorial_cues) == 1
    persisted = runtime.storyboard.editorial_cues[0]
    assert persisted.cue_id == cue.cue_id
    assert persisted.scene_local_start_seconds == cue.scene_local_start_seconds
    assert persisted.scene_local_end_seconds == cue.scene_local_end_seconds
    assert persisted.absolute_start_seconds == cue.absolute_start_seconds
    assert persisted.absolute_end_seconds == cue.absolute_end_seconds
    assert persisted.props["title"] == "Number 1: Cave Diver"
    assert runtime.storyboard.attempt_ledger[-1].event_type == "editorial_cues_planned"

    snapshot = json.loads(Path(runtime.snapshot_path).read_text(encoding="utf-8"))
    saved_cue = snapshot["editorial_cues"][0]
    assert saved_cue["scene_local_start_seconds"] == 0.08
    assert "scene_local_start" not in saved_cue
    assert "renderer_only_field" not in saved_cue


def test_finalize_atomically_writes_redacted_manifest_and_final_storyboard(tmp_path):
    script = _listicle_script(2)
    video_path = tmp_path / "finished.mp4"
    video_bytes = b"final-video-bytes" * 128
    video_path.write_bytes(video_bytes)
    runtime = V2PipelineRuntime.create(
        _config(script, name="finished"), output_path=video_path
    )
    component_manifest = {
        "duration": 14.25,
        "source_accounting": {"youtube": 1, "web_image": 1, "stock": 0},
        "clips": [{"scene_id": runtime.script_plan.scenes[0].scene_id}],
        "openrouter_api_key": "must-not-reach-disk",
        "private_url": "https://example.test/file?token=also-secret",
    }

    manifest_path = Path(
        runtime.finalize(video_path=str(video_path), component_manifest=component_manifest)
    )

    assert manifest_path == video_path.with_suffix(".manifest.json")
    assert manifest_path.is_file()
    assert not [item for item in tmp_path.iterdir() if item.name.endswith(".part")]
    manifest_text = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    assert "must-not-reach-disk" not in manifest_text
    assert "also-secret" not in manifest_text
    assert manifest["openrouter_api_key"] == "[REDACTED]"
    assert manifest["source_accounting"]["stock"] == 0

    final_event = runtime.storyboard.attempt_ledger[-1]
    assert final_event.event_type == "render_completed"
    assert final_event.payload["video_fingerprint"] == hashlib.sha256(video_bytes).hexdigest()
    assert final_event.payload["manifest_path"] == str(manifest_path)
    assert final_event.payload["source_accounting"]["youtube"] == 1

    restored = runtime.store.load()
    assert restored.to_dict() == runtime.storyboard.to_dict()
    assert restored.attempt_ledger[-1].event_type == "render_completed"
    assert runtime.store.read_events()[-1].to_dict() == final_event.to_dict()
