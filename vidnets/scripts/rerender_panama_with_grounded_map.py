"""Correct and re-render the Panama Canal plan through Flask's Remotion editor route."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

PACKAGE = ROOT / "output" / "panama_canal_history_whisper_sfx_20260926_001647_20260926_001647_2308036f"
VIDEO_NAME = "panama_canal_history_whisper_sfx_20260926_001647.mp4"
MANIFEST_PATH = PACKAGE / "panama_canal_history_whisper_sfx_20260926_001647.manifest.json"
PLAN_PATH = PACKAGE / "panama_canal_history_whisper_sfx_20260926_001647_remotion_edit_plan.json"
SUBTITLE_PATH = ROOT / "temp" / "captions_822dc072.ass"


def _probe(path: Path) -> dict:
    ffprobe = str(os.environ.get("VIDZEO_FFPROBE_PATH") or "ffprobe")
    result = subprocess.run(
        [
            ffprobe,
            "-v", "error",
            "-show_entries", "format=duration,size:stream=codec_type,codec_name,width,height,sample_rate,channels",
            "-of", "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=25,
    )
    return json.loads(result.stdout or "{}")


def _correct_map(scene: dict, apply_grounded_map_country_props) -> bool:
    shot = scene.get("map_shot") if isinstance(scene.get("map_shot"), dict) else {}
    primary = scene.get("primary_visual") if isinstance(scene.get("primary_visual"), dict) else {}
    if primary.get("type") != "AnimatedMap" or shot.get("enabled") is not True:
        return False
    primary["props"] = apply_grounded_map_country_props(primary.get("props") or {}, shot)
    scene["primary_visual"] = primary
    graphic = scene.get("graphic") if isinstance(scene.get("graphic"), dict) else {}
    if graphic:
        graphic_props = apply_grounded_map_country_props(graphic.get("props") or {}, shot)
        graphic_props["map_shot"] = dict(shot)
        graphic["props"] = graphic_props
        scene["graphic"] = graphic
    return True


def _make_sound_variety(manifest: dict) -> bool:
    """Use a Whisper-word water cue plus one stronger, distinct cut sound."""
    from services.audio_timeline_validation import whisper_anchor_time

    scenes = list(manifest.get("scenes") or [])
    target = next((
        scene for scene in scenes
        if "water does the lifting" in str(scene.get("narration") or "").casefold()
    ), None)
    if not isinstance(target, dict):
        raise RuntimeError("No water-lift narration scene was available for sound synchronization.")

    anchor = whisper_anchor_time(target, "water")
    if anchor is None:
        raise RuntimeError("Whisper did not provide a valid word-time anchor for the water cue.")
    scene_start = float(target.get("start") or 0.0)
    local_start = round(anchor - scene_start, 3)
    source = ROOT / "public" / "sound-effects" / "vidzeo" / "water_pulse.wav"
    if not source.is_file():
        raise RuntimeError(f"The water movement sound asset is missing: {source}")

    cue = {
        "kind": "water_pulse",
        "src": str(source.resolve()),
        "start": round(anchor, 3),
        "time_local": local_start,
        "scene_local_start": local_start,
        "duration": 0.48,
        "volume": 0.18,
        "fade_in": 0.02,
        "fade_out": 0.14,
        "duck_voice": 0.025,
        "sound_intent": "water_motion",
        "sound_anchor": "water",
        "timing_quality": "whisper_word_aligned",
        "reason": "Whisper-aligned water lift in the lock explanation",
        "rights_status": "generated",
        "publisher": "Vidzeo local procedural library",
        "license": "Vidzeo procedural / generated",
    }
    old_cues = list(target.get("sound_cues") or [])
    replaced = next((
        item for item in old_cues
        if isinstance(item, dict) and (
            (str(item.get("kind") or "").casefold() == "water_pulse" and str(item.get("sound_anchor") or "").casefold() == "water")
            or str(item.get("kind") or "").casefold() in {"edit_whoosh_sucked", "whoosh_soft"}
        )
    ), None)
    if replaced is None:
        raise RuntimeError("The expected repetitive transition cue was not found in the water-lift scene.")
    target["sound_cues"] = [cue if item is replaced else item for item in old_cues]

    audio = manifest.get("audio") if isinstance(manifest.get("audio"), dict) else {}
    effects = list(audio.get("effects") or [])
    old_start = float(replaced.get("start") or scene_start)
    effect_index = next((
        index for index, item in enumerate(effects)
        if isinstance(item, dict)
        and str(item.get("kind") or "").casefold() == str(replaced.get("kind") or "").casefold()
        and abs(float(item.get("start") or 0.0) - old_start) <= 0.05
    ), None)
    if effect_index is None:
        raise RuntimeError("The repeated transition cue was missing from the export audio track.")
    effects[effect_index] = dict(cue)

    transition_scene = next((
        scene for scene in scenes
        if "official opening transit" in str(scene.get("narration") or "").casefold()
    ), None)
    if not isinstance(transition_scene, dict):
        raise RuntimeError("No official opening transit scene was available for a varied transition cue.")
    old_transition = next((
        item for item in transition_scene.get("sound_cues") or []
        if isinstance(item, dict) and str(item.get("kind") or "").casefold() == "whoosh_soft"
    ), None)
    transition_source = ROOT / "public" / "sound-effects" / "edit" / "edit_whoosh_air.wav"
    if old_transition is None or not transition_source.is_file():
        raise RuntimeError("The distinct scene-transition effect or its audio file is missing.")
    transition_cue = dict(old_transition)
    transition_cue.update({
        "kind": "edit_whoosh_air",
        "src": str(transition_source.resolve()),
        "volume": 0.20,
        "reason": "Narrative transition into the Ancon's official opening transit",
        "publisher": "Vidzeo editorial SFX",
        "license": "Vidzeo procedural / generated (editorial)",
    })
    transition_scene["sound_cues"] = [
        transition_cue if item is old_transition else item
        for item in transition_scene.get("sound_cues") or []
    ]
    transition_start = float(old_transition.get("start") or transition_scene.get("start") or 0.0)
    transition_effect_index = next((
        index for index, item in enumerate(effects)
        if isinstance(item, dict)
        and str(item.get("kind") or "").casefold() == "whoosh_soft"
        and abs(float(item.get("start") or 0.0) - transition_start) <= 0.05
    ), None)
    if transition_effect_index is None:
        raise RuntimeError("The transition cue was missing from the export audio track.")
    effects[transition_effect_index] = dict(transition_cue)
    audio["effects"] = effects
    manifest["audio"] = audio
    return True


def main() -> int:
    import app as vidzeo_app
    from services.editorial_director.director import apply_grounded_map_country_props
    from services.audio_timeline_validation import validate_audio_edit_timeline

    outer = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest = dict(outer.get("remotion_manifest") or {})
    scenes = list(manifest.get("scenes") or [])
    fixed_scenes = sum(_correct_map(scene, apply_grounded_map_country_props) for scene in scenes)
    if fixed_scenes < 1:
        raise RuntimeError("No enabled map scene with verified places was available to correct.")
    manifest["scenes"] = scenes
    _make_sound_variety(manifest)

    validation = vidzeo_app._validate_remotion_edit_plan(manifest, len(scenes))
    if validation.get("status") != "pass":
        raise RuntimeError(f"Corrected edit plan did not pass preflight: {validation.get('errors')}")

    print(f"[corrected-render] Grounded {fixed_scenes} map scene(s); edit-plan preflight passed.", flush=True)
    response = vidzeo_app.app.test_client().post(
        "/api/editor/render",
        json={
            "name": "Panama Canal History Grounded Map",
            "resolution": "1080p",
            "fast_render": True,
            "manifest": manifest,
        },
    )
    result = response.get_json(silent=True) or {}
    if response.status_code != 200 or not result.get("success"):
        raise RuntimeError(
            f"Remotion correction render failed ({response.status_code}): "
            f"{result.get('error') or 'no error details returned'}"
        )
    render_metadata = result.get("render_metadata") or {}
    if render_metadata.get("selected_backend") != "remotion":
        raise RuntimeError("Corrected render was not produced by Remotion.")
    if (render_metadata.get("quality_checks") or {}).get("status") != "pass":
        raise RuntimeError("Corrected Remotion master did not pass its frame/audio checks.")

    candidate = Path(str(result.get("output_path") or "")).resolve()
    if not candidate.is_file():
        raise RuntimeError("Remotion did not create the corrected candidate video.")
    candidate_probe = _probe(candidate)
    stream_types = {stream.get("codec_type") for stream in candidate_probe.get("streams") or []}
    if not {"video", "audio"}.issubset(stream_types):
        raise RuntimeError("Corrected Remotion candidate is missing video or narration audio.")

    final_candidate = PACKAGE / "corrected_captioned_candidate.mp4"
    if SUBTITLE_PATH.is_file():
        vidzeo_app._burn_subtitles_into_master(candidate, SUBTITLE_PATH, final_candidate)
        if not final_candidate.is_file() or final_candidate.stat().st_size < 4096:
            raise RuntimeError("Caption finishing pass did not produce a valid file.")
    else:
        shutil.copy2(candidate, final_candidate)

    final_probe = _probe(final_candidate)
    streams = list(final_probe.get("streams") or [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})
    duration = float((final_probe.get("format") or {}).get("duration") or 0.0)
    if (video.get("width"), video.get("height")) != (1920, 1080) or not audio:
        raise RuntimeError("Corrected final failed the 1080p video/audio stream check.")

    whisper = outer.get("audio_timing", {}).get("whisper_alignment", {})
    windows = list(outer.get("scene_timing_windows") or [])
    boundaries = [float(item.get("start_seconds") or 0.0) for item in windows]
    effects = list((manifest.get("audio") or {}).get("effects") or [])
    effect_starts = [float(item.get("start") or 0.0) for item in effects]
    effect_kinds = [str(item.get("kind") or "") for item in effects]
    windows_contiguous = all(
        abs(float(current.get("start_seconds") or 0.0) - float(previous.get("end_seconds") or 0.0)) <= 0.02
        for previous, current in zip(windows, windows[1:])
    )
    effect_files_exist = all(Path(str(item.get("src") or "")).is_file() for item in effects)
    audio_timeline = validate_audio_edit_timeline(
        scenes,
        effects,
        min_effect_gap_seconds=12.0,
        require_unique_families=True,
    )
    if not windows_contiguous:
        raise RuntimeError("Scene windows are not contiguous in the saved Whisper timeline.")
    if audio_timeline.get("status") != "pass":
        raise RuntimeError(f"Sound-effect timing or variety failed validation: {audio_timeline.get('errors')}")
    if not effect_files_exist:
        raise RuntimeError("A referenced sound effect file is missing.")
    if float(whisper.get("exact_match_ratio") or 0.0) < 0.90:
        raise RuntimeError("Whisper exact-word alignment fell below the 90% acceptance threshold.")

    final_video = PACKAGE / VIDEO_NAME
    os.replace(final_candidate, final_video)

    temporary_edit_plan = Path(str(result.get("edit_plan_path") or ""))
    if temporary_edit_plan.is_file():
        archived_plan = PACKAGE / "editorial" / "remotion_render_validation_plan.json"
        archived_plan.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(temporary_edit_plan), str(archived_plan))
    if candidate.is_file():
        candidate.unlink()

    manifest["edit_plan_validation"] = validation
    manifest["render_metadata"] = render_metadata
    plan_payload = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    plan_payload["scenes"] = scenes
    plan_payload["audio"] = manifest.get("audio") or {}
    plan_payload["edit_plan_validation"] = validation
    plan_payload["render_metadata"] = render_metadata
    PLAN_PATH.write_text(json.dumps(plan_payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    outer["remotion_manifest"] = manifest
    outer["render_metadata"] = render_metadata
    outer["edit_plan_validation"] = validation
    outer["video_path"] = str(final_video.resolve())
    MANIFEST_PATH.write_text(json.dumps(outer, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    report = {
        "status": "pass",
        "video_path": str(final_video.resolve()),
        "render_backend": "remotion",
        "render_workers": render_metadata.get("remotion_workers"),
        "duration_seconds": duration,
        "resolution": f"{video.get('width')}x{video.get('height')}",
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name"),
        "whisper_status": whisper.get("status"),
        "whisper_exact_match_ratio": whisper.get("exact_match_ratio"),
        "whisper_timing_repairs": whisper.get("timing_repair_count"),
        "scene_count": len(windows),
        "scene_windows_contiguous": windows_contiguous,
        "sound_effect_count": len(effects),
        "sound_effect_kinds": effect_kinds,
        "sound_effects_distinct": len(set(effect_kinds)) == len(effect_kinds),
        "sound_effect_families": audio_timeline.get("sound_effect_families"),
        "sound_effects_whisper_anchored": audio_timeline.get("whisper_anchored_count"),
        "sound_effects_transition_boundary_synced": audio_timeline.get("transition_boundary_count"),
        "sound_effect_minimum_gap_seconds": audio_timeline.get("minimum_sound_gap_seconds"),
        "sound_effect_timeline_validation": audio_timeline,
        "sound_effect_files_exist": effect_files_exist,
        "map_correction": {
            "verified_location": "Panama",
            "country_code": scenes[0]["primary_visual"]["props"].get("country_code"),
            "rendered_label": scenes[0]["primary_visual"]["props"].get("title"),
        },
        "quality_check": (render_metadata.get("quality_checks") or {}).get("status"),
    }
    (PACKAGE / "checkpoints" / "whisper_sfx_validation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary_path = PACKAGE / "generation_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary.update({
        "video_path": str(final_video.resolve()),
        "quality_status": report["quality_check"],
        "corrected_map": True,
        "render_metadata": render_metadata,
    })
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
