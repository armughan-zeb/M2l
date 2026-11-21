"""Render-gate reconciliation for globe/country_spotlight maps (run 4, job 3006fd62).

Run 4 parked after a VM reboot during render: the checkpointed-render resume
failed 4x identically with ``unsupported_map_location`` on scene 0, a
country_spotlight globe map with ``location_names == ["Pacific", "Panama"]``.
The first render never ran that validation (non-strict render-start downgrades
a failing plan to ``pass_with_notes`` and renders it), while the resume gate
raised strictly -- so a plan that had already rendered 88% could never resume.

The director grounds "Pacific" (it is in the curated-region catalogue used by
both the director and the globe renderer, and it is narrated), but the
render-plan validator only knew countries/cities/admin1. These tests pin the
reconciliation:

* the exact Panama scene-0 shape passes BOTH gates (validation and resume);
* a region-only globe map ("North Atlantic") passes too;
* genuinely ungrounded names (unknown place, or a region the narration never
  mentions) are still rejected -- the validator is not weakened;
* the resume repair drops ungrounded names (or falls back to verified media
  when none remain) instead of parking the job;
* a plan whose render already started survives a resume even when validation
  still reports advisory failures (render-start/resume parity), while a plan
  that never rendered still raises.
"""

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

import app as server_app

PANAMA_NARRATION = (
    "Cross from the Pacific to the Caribbean in just two hours through the Panama Canal."
)


def _map_shot(location_names):
    return {
        "enabled": True,
        "location_names": list(location_names),
        "feature_ids": [],
        "route_id": "",
        "source_ids": [],
        "motion_profile": "wide_context",
        "render_style": "globe_3d",
    }


def _panama_scene(image, location_names=("Pacific", "Panama"), narration=PANAMA_NARRATION):
    """Scene 0 of job 3006fd62, exactly as saved (schema v4, globe_3d)."""
    shot = _map_shot(location_names)
    return {
        "start": 0.0,
        "duration": 4.772,
        "narration": narration,
        "text": {"frame_mode": "map", "overlay_policy": "none"},
        "map_shot": dict(shot),
        "media": {"type": "image", "src": str(image)},
        "primary_visual": {
            "type": "AnimatedMap",
            "variant": "country_spotlight",
            "props": {
                "variant": "country_spotlight",
                "country": "PAN",
                "country_code": "PAN",
                "location": "PANAMA",
                "title": "PANAMA",
                "map_shot": dict(shot),
                "render_style": "globe_3d",
                "motion_profile": "wide_context",
            },
            "asset": {"type": "image", "src": str(image), "path": str(image)},
        },
        "graphic": {"template_id": "map-route", "props": {}},
    }


def _plan(scenes):
    return {
        "edit_plan_schema_version": 4,
        "fps": 30,
        "width": 1920,
        "height": 1080,
        "director": {"overlay_budget": 0},
        "scenes": scenes,
    }


def _map_errors(result):
    return [error for error in result.get("errors") or [] if error.get("code") == "unsupported_map_location"]


@pytest.fixture
def image(tmp_path):
    path = tmp_path / "scene.jpg"
    path.write_bytes(b"image-bytes" * 32)
    return path


def test_panama_globe_map_passes_validation(tmp_path, image, monkeypatch):
    """Exact run-4 scene 0: narrated ocean + country on a globe_3d map."""
    monkeypatch.setattr(server_app, "_remotion_runtime_asset_errors", lambda: [])
    result = server_app._validate_remotion_edit_plan(_plan([_panama_scene(image)]), 1)
    assert _map_errors(result) == []
    assert result["status"] == "pass"


def test_region_only_globe_map_passes_validation(tmp_path, image, monkeypatch):
    """A director region-only globe scene (e.g. 'North Atlantic') is grounded.

    Narration carries two places plus a crossing relation, so the production
    timeline keeps the directed map (the same intent shape as the Panama
    intro); the map itself is anchored only by the region.
    """
    monkeypatch.setattr(server_app, "_remotion_runtime_asset_errors", lambda: [])
    scene = _panama_scene(
        image,
        location_names=["North Atlantic"],
        narration="The convoy crossed the North Atlantic to reach Iceland in winter.",
    )
    scene["primary_visual"]["props"].pop("country", None)
    scene["primary_visual"]["props"].pop("country_code", None)
    scene["primary_visual"]["props"]["location"] = "North Atlantic"
    result = server_app._validate_remotion_edit_plan(_plan([scene]), 1)
    assert _map_errors(result) == []
    assert result["status"] == "pass"


def test_unmentioned_ocean_name_is_still_rejected(tmp_path, image, monkeypatch):
    """A curated region the narration never mentions is still ungrounded.

    The timeline compiler demotes the ungrounded map before the location
    check runs, so the region never survives as a map anchor -- but the
    demotion now falls back to the scene's verified media with a valid
    media_only treatment (the test-15 edit-plan fix), so the plan passes
    as a media scene instead of failing on the demoted entry. The property
    that must not weaken is that the ungrounded region is never used as a
    map anchor, not that the plan fails.
    """
    monkeypatch.setattr(server_app, "_remotion_runtime_asset_errors", lambda: [])
    scene = _panama_scene(image, narration="The canal changed world trade forever.")
    plan = _plan([scene])
    result = server_app._validate_remotion_edit_plan(plan, 1)
    assert result["status"] == "pass"
    assert result.get("errors") == []
    demoted = plan["scenes"][0]
    assert demoted["primary_visual"]["type"] != "AnimatedMap"
    assert not demoted["map_shot"].get("enabled")
    assert demoted["text"]["frame_mode"] == "media_only"
    assert demoted["primary_visual"]["variant"]


def test_unknown_place_is_still_rejected(tmp_path, image, monkeypatch):
    """A place in no catalogue (even when narrated) is still rejected as a map.

    Atlantis never survives as a map location: the timeline compiler
    demotes the map and the scene falls back to its verified media with a
    valid media_only treatment (the test-15 edit-plan fix), so the plan
    passes as a media scene. The property that must not weaken is that the
    unknown place is never used as a map anchor, not that the plan fails.
    """
    monkeypatch.setattr(server_app, "_remotion_runtime_asset_errors", lambda: [])
    scene = _panama_scene(
        image,
        location_names=["Atlantis", "Panama"],
        narration="Sail from Atlantis to Panama on the old charts.",
    )
    plan = _plan([scene])
    result = server_app._validate_remotion_edit_plan(plan, 1)
    assert result["status"] == "pass"
    assert result.get("errors") == []
    demoted = plan["scenes"][0]
    assert demoted["primary_visual"]["type"] != "AnimatedMap"
    assert not demoted["map_shot"].get("enabled")
    assert demoted["text"]["frame_mode"] == "media_only"
    assert demoted["primary_visual"]["variant"]


LEONIDA_NARRATION = (
    "Leonida is a fictional Florida: Vice City, based on Miami, the Grassrivers, "
    "and the Leonida Keys, on a map reported to be three times the size of "
    "Red Dead Redemption 2's map."
)


def _leonida_map_scene(media, location_names=("Vice City", "Leonida Keys"),
                      narration=LEONIDA_NARRATION):
    """Test-15 scene 9 shape: the director gave a map treatment to fictional
    Leonida geography, and the scene's verified media is a web fallback
    rescue image (or a YT clip, or nothing). Neither location is in any
    geographic catalogue, so the production timeline demotes the map to the
    verified media -- and the demoted entry must be a valid v4 scene."""
    return {
        "start": 0.0,
        "duration": 7.0,
        "narration": narration,
        "text": {"frame_mode": "map", "overlay_policy": "none"},
        "map_shot": {
            "enabled": True,
            "location_names": list(location_names),
            "feature_ids": [], "route_id": "",
            "render_style": "vector_editorial",
        },
        "media": dict(media),
        "primary_visual": {
            "type": "AnimatedMap", "variant": "territory_zoom_highlight",
            "props": {"map_shot": {"enabled": True, "location_names": list(location_names)}},
            "asset": dict(media),
        },
        "graphic": {"template_id": "map-route", "props": {}},
    }


def _no_demoted_entry_errors(result):
    return [error for error in result.get("errors") or []
            if error.get("code") in {"invalid_frame_mode", "missing_scene_treatment", "frame_visual_mismatch"}]


def test_demoted_map_with_web_rescue_image_passes_validation(tmp_path, monkeypatch):
    """Test 15 regression: scene 9's media was a web fallback rescue image,
    its map was demoted, and the demoted entry carried frame_mode 'media'
    with no treatment variant -- advisory at media time, then a hard
    RemotionEditPlanError at the render resume after the VM reboot. The
    demoted entry must now be a valid media_only scene and the plan pass."""
    monkeypatch.setattr(server_app, "_remotion_runtime_asset_errors", lambda: [])
    image = tmp_path / "rescue_scene9.jpg"
    image.write_bytes(b"image-bytes" * 32)
    plan = _plan([_leonida_map_scene({"type": "image", "src": str(image)})])
    result = server_app._validate_remotion_edit_plan(plan, 1)
    assert _no_demoted_entry_errors(result) == []
    assert result["status"] == "pass"
    scene = plan["scenes"][0]
    assert scene["map_decision"] == "no_grounded_geography_needed_for_scene"
    assert scene["text"]["frame_mode"] == "media_only"
    assert scene["primary_visual"]["type"] == "SmartImage"
    assert scene["primary_visual"]["variant"] == "full_bleed_cinematic"


def test_demoted_map_with_video_clip_passes_validation(tmp_path, monkeypatch):
    """Same demotion with a YT video clip as the scene media: SmartVideo
    with the video treatment variant, media_only frame mode, plan passes."""
    monkeypatch.setattr(server_app, "_remotion_runtime_asset_errors", lambda: [])
    clip = tmp_path / "scene_0008.mp4"
    clip.write_bytes(b"video-bytes" * 32)
    plan = _plan([_leonida_map_scene({"type": "video", "src": str(clip)})])
    result = server_app._validate_remotion_edit_plan(plan, 1)
    assert _no_demoted_entry_errors(result) == []
    assert result["status"] == "pass"
    scene = plan["scenes"][0]
    assert scene["text"]["frame_mode"] == "media_only"
    assert scene["primary_visual"]["type"] == "SmartVideo"
    assert scene["primary_visual"]["variant"] == "full_bleed"


def test_demoted_map_without_media_becomes_kinetic(tmp_path, monkeypatch):
    """A demoted map scene with no verified media falls back to kinetic
    text -- with the kinetic frame mode and a real KineticText variant,
    not a bare 'media' mode with no treatment."""
    monkeypatch.setattr(server_app, "_remotion_runtime_asset_errors", lambda: [])
    plan = _plan([_leonida_map_scene({})])
    result = server_app._validate_remotion_edit_plan(plan, 1)
    assert _no_demoted_entry_errors(result) == []
    assert result["status"] == "pass"
    scene = plan["scenes"][0]
    assert scene["text"]["frame_mode"] == "kinetic"
    assert scene["primary_visual"]["type"] == "KineticText"
    assert scene["primary_visual"]["variant"] == "bold_minimal_center"


def test_resume_replays_demoted_map_plan_after_reboot(tmp_path, monkeypatch):
    """Test-15 failure mode, end to end: a reboot mid-render with NO finished
    section must still resume, because the saved plan is now valid -- the
    render-start/resume parity clause cannot save a plan whose render never
    finished a section, so the plan itself must pass the resume gate."""
    audio = tmp_path / "voiceover.mp3"
    audio.write_bytes(b"audio-bytes" * 32)
    image = tmp_path / "rescue_scene9.jpg"
    image.write_bytes(b"image-bytes" * 32)
    package = tmp_path / "paused-demoted-map"
    plan = _plan([_leonida_map_scene({"type": "image", "src": str(image)})])
    plan["audio"] = {"voiceover": {"src": str(audio)}}
    plan["config"] = {}
    _write_resume_package(package, image, audio, plan)
    render_calls = _mock_render(monkeypatch)

    result = server_app._resume_checkpointed_remotion_render(
        {"_output_package_dir": str(package), "output_name": "saved-edit",
         "content_title": "Saved Edit", "type": "long"},
        lambda *_args: None,
    )

    assert render_calls == [(1, "remotion")]
    assert result["render_metadata"]["resumed_from_checkpoint"] is True
    assert result["ready_to_publish"] is True


def test_non_globe_map_does_not_accept_region_name(tmp_path, image, monkeypatch):
    """A region name is only a globe_3d camera centre, not a local-map place."""
    monkeypatch.setattr(server_app, "_remotion_runtime_asset_errors", lambda: [])
    scene = _panama_scene(image)
    scene["map_shot"]["render_style"] = "vector_editorial"
    scene["primary_visual"]["props"]["map_shot"]["render_style"] = "vector_editorial"
    scene["primary_visual"]["props"]["render_style"] = "vector_editorial"
    result = server_app._validate_remotion_edit_plan(_plan([scene]), 1)
    assert _map_errors(result) != []


def test_repair_drops_ungrounded_location_and_keeps_grounded(tmp_path, image):
    manifest = _plan([_panama_scene(
        image,
        location_names=["Atlantis", "Panama"],
        narration="Sail from Atlantis to Panama on the old charts.",
    )])
    repaired = server_app._repair_unsupported_map_locations(manifest)
    assert repaired == [0]
    assert manifest["scenes"][0]["map_shot"]["location_names"] == ["Panama"]
    assert manifest["scenes"][0]["primary_visual"]["props"]["map_shot"]["location_names"] == ["Panama"]
    assert manifest["scenes"][0]["primary_visual"]["type"] == "AnimatedMap"


def test_repair_keeps_valid_globe_map_untouched(tmp_path, image):
    manifest = _plan([_panama_scene(image)])
    assert server_app._repair_unsupported_map_locations(manifest) == []
    assert manifest["scenes"][0]["map_shot"]["location_names"] == ["Pacific", "Panama"]


def test_repair_falls_back_to_media_when_no_grounded_location(tmp_path, image, monkeypatch):
    monkeypatch.setattr(server_app, "_remotion_runtime_asset_errors", lambda: [])
    manifest = _plan([_panama_scene(
        image,
        location_names=["Atlantis"],
        narration="Sail from Atlantis on the old charts.",
    )])
    repaired = server_app._repair_unsupported_map_locations(manifest)
    assert repaired == [0]
    scene = manifest["scenes"][0]
    assert scene["primary_visual"]["type"] == "SmartImage"
    assert scene["text"]["frame_mode"] == "media_only"
    assert scene["map_shot"] == {}
    result = server_app._validate_remotion_edit_plan(manifest, 1)
    assert _map_errors(result) == []
    assert result["status"] == "pass"


def test_repair_leaves_scene_without_resolved_asset_untouched(tmp_path, monkeypatch):
    """No verified media to fall back to: the defect stays visible, not hidden."""
    monkeypatch.setattr(server_app, "_remotion_runtime_asset_errors", lambda: [])
    missing = tmp_path / "missing.jpg"
    manifest = _plan([_panama_scene(
        missing,
        location_names=["Atlantis"],
        narration="Sail from Atlantis on the old charts.",
    )])
    assert server_app._repair_unsupported_map_locations(manifest) == []
    result = server_app._validate_remotion_edit_plan(manifest, 1)
    assert result["status"] == "fail"


def _write_resume_package(package, image, audio, plan, stage="ready_for_render", status="saved"):
    checkpoints = package / "checkpoints"
    editorial = package / "editorial"
    for directory in (checkpoints, editorial, package / "render"):
        directory.mkdir(parents=True, exist_ok=True)
    plan_path = editorial / "remotion_edit_plan.json"
    plan_path.write_text(json.dumps(plan, default=str), encoding="utf-8")
    (checkpoints / "state.json").write_text(json.dumps({"stage": stage, "status": status}), encoding="utf-8")
    # The resume gate requires the saved plan's narration and timing to match
    # the audio plan beat for beat, so derive them from the plan's scenes
    # instead of hardcoding the Panama values.
    scene_texts = [str(scene.get("narration") or "") for scene in plan.get("scenes") or []]
    scene_durations = [float(scene.get("duration") or 0.0) for scene in plan.get("scenes") or []]
    total_duration = sum(scene_durations)
    (checkpoints / "pre_render.json").write_text(json.dumps({
        "backend": "remotion", "edit_plan_path": str(plan_path),
        "media_paths": [str(image)], "audio_path": str(audio), "duration": total_duration,
    }), encoding="utf-8")
    (checkpoints / "audio_plan.json").write_text(json.dumps({
        "audio_path": str(audio), "duration": total_duration,
        "scene_texts": scene_texts, "scene_durations": scene_durations,
    }), encoding="utf-8")
    return plan_path


def _mock_render(monkeypatch):
    # The real validator also checks the repo's runtime assets (Remotion
    # packages, sound effects). Those live in the checkout, not in the
    # fixture; stub that one probe so the test exercises the plan gates,
    # not the machine's node_modules. (Same stub the validator tests use.)
    monkeypatch.setattr(server_app, "_remotion_runtime_asset_errors", lambda: [])
    render_calls = []

    def fake_render(*, manifest, output_path, config, progress_callback, cancellation):
        render_calls.append((len(manifest["scenes"]), config["render_backend"]))
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"rendered-remotion-video")
        reviewed = {**manifest, "creative_review": {"status": "pass_with_advisories"},
                    "preview_review": {"status": "pass"},
                    "scenes": [{**scene, "reviewed_revision": True} for scene in manifest["scenes"]]}
        reviewed_path = output.parent / "reviewed.manifest.json"
        reviewed_path.write_text(json.dumps(reviewed), encoding="utf-8")
        return {
            "output_path": str(output),
            "renderer": "remotion-react",
            "render_resources": {"recommended_workers": 2},
            "rendered_manifest_path": str(reviewed_path),
            "creative_review": reviewed["creative_review"],
            "preview_review": reviewed["preview_review"],
        }

    monkeypatch.setattr(server_app, "_validate_remotion_master", lambda *args, **kwargs: {
        "status": "pass", "backend": "remotion",
    })
    monkeypatch.setattr(server_app, "_memory_status_snapshot", lambda: {"available_mb": 2048})
    monkeypatch.setattr(
        "services.native_render_policy.render_semantic_documentary",
        fake_render,
    )
    return render_calls


def test_resume_replays_panama_plan_after_reboot(tmp_path, image, monkeypatch):
    """Run-4 replay: a reboot mid-render must resume, not raise edit-plan errors."""
    audio = tmp_path / "voiceover.mp3"
    audio.write_bytes(b"audio-bytes" * 32)
    package = tmp_path / "paused-panama"
    plan = _plan([_panama_scene(image)])
    plan["audio"] = {"voiceover": {"src": str(audio)}}
    plan["config"] = {}
    _write_resume_package(package, image, audio, plan)
    render_calls = _mock_render(monkeypatch)

    result = server_app._resume_checkpointed_remotion_render(
        {"_output_package_dir": str(package), "output_name": "saved-edit",
         "content_title": "Saved Edit", "type": "long"},
        lambda *_args: None,
    )

    assert render_calls == [(1, "remotion")]
    assert result["render_metadata"]["resumed_from_checkpoint"] is True
    assert result["ready_to_publish"] is True


def test_resume_parity_already_rendering_plan_survives_advisory_failure(tmp_path, image, monkeypatch):
    """Render-start downgrades non-strict failures to pass_with_notes; a resume
    of a render that already produced a finished section must do the same."""
    audio = tmp_path / "voiceover.mp3"
    audio.write_bytes(b"audio-bytes" * 32)
    package = tmp_path / "paused-advisory"
    plan = _plan([_panama_scene(image)])
    plan["audio"] = {"voiceover": {"src": str(audio)}}
    plan["config"] = {}
    _write_resume_package(package, image, audio, plan)
    sections = package / "render" / ".saved-edit_remotion.mp4.render-sections" / "abc123"
    sections.mkdir(parents=True, exist_ok=True)
    (sections / "section-0.mp4").write_bytes(b"finished-section" * 32)
    render_calls = _mock_render(monkeypatch)
    monkeypatch.setattr(server_app, "_validate_remotion_edit_plan", lambda _m, _n: {
        "status": "fail", "asset_status": [],
        "errors": [{"code": "fixture_advisory_failure", "scene_index": 0}],
    })

    result = server_app._resume_checkpointed_remotion_render(
        {"_output_package_dir": str(package), "output_name": "saved-edit",
         "content_title": "Saved Edit", "type": "long"},
        lambda *_args: None,
    )

    assert render_calls == [(1, "remotion")]
    assert result["render_metadata"]["resumed_from_checkpoint"] is True


def test_resume_still_raises_when_plan_never_rendered(tmp_path, image, monkeypatch):
    """Parity is conditioned on render progress: no finished section, still raise."""
    audio = tmp_path / "voiceover.mp3"
    audio.write_bytes(b"audio-bytes" * 32)
    package = tmp_path / "paused-never-rendered"
    plan = _plan([_panama_scene(image)])
    plan["audio"] = {"voiceover": {"src": str(audio)}}
    plan["config"] = {}
    _write_resume_package(package, image, audio, plan)
    _mock_render(monkeypatch)
    monkeypatch.setattr(server_app, "_validate_remotion_edit_plan", lambda _m, _n: {
        "status": "fail", "asset_status": [],
        "errors": [{"code": "fixture_advisory_failure", "scene_index": 0}],
    })

    with pytest.raises(server_app.RemotionEditPlanError, match="fixture_advisory_failure"):
        server_app._resume_checkpointed_remotion_render(
            {"_output_package_dir": str(package), "output_name": "saved-edit",
             "content_title": "Saved Edit", "type": "long"},
            lambda *_args: None,
        )
