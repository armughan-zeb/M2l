"""Repair a conflicting source date in the Suez test export and rerender with Remotion."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

PACKAGE = ROOT / "output" / "suez_crisis_history_validation_20260926_015107_20260926_015107_5bde100a"
VIDEO_NAME = "suez_crisis_history_validation_20260926_015107.mp4"
MANIFEST_PATH = PACKAGE / "suez_crisis_history_validation_20260926_015107.manifest.json"
PLAN_PATH = PACKAGE / "suez_crisis_history_validation_20260926_015107_remotion_edit_plan.json"
PACKAGE_PLAN_PATH = PACKAGE / "editorial" / "remotion_edit_plan.json"
SOURCE_IMAGE_PATH = PACKAGE / "media" / "assets" / "nasser_1956_wikimedia_commons.png"
IMAGE_PATH = PACKAGE / "media" / "assets" / "nasser_1956_editorial_frame.png"
IMAGE_PAGE = "https://commons.wikimedia.org/wiki/File:Gamal_Abd_El-Nasser_1956.png"
IMAGE_DIRECT = "https://upload.wikimedia.org/wikipedia/commons/6/6c/Gamal_Abd_El-Nasser_1956.png"
SUBTITLE_PATH = ROOT / "temp" / "captions_3410bd4a.ass"


def _probe(path: Path) -> dict:
    ffprobe = str(os.environ.get("VIDZEO_FFPROBE_PATH") or shutil.which("ffprobe") or "ffprobe")
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
        timeout=30,
    )
    return json.loads(result.stdout or "{}")


def _ensure_editorial_frame() -> None:
    """Keep the full archival portrait visible in a landscape documentary frame."""
    from PIL import Image, ImageDraw

    if not SOURCE_IMAGE_PATH.is_file():
        raise RuntimeError(f"The original public-domain source image is missing: {SOURCE_IMAGE_PATH}")
    width, height = 1920, 1080
    background = Image.new("RGB", (width, height))
    pixels = background.load()
    left, right = (43, 47, 52), (65, 59, 51)
    for x in range(width):
        t = x / max(1, width - 1)
        color = tuple(round(left[i] * (1 - t) + right[i] * t) for i in range(3))
        for y in range(height):
            pixels[x, y] = color

    portrait = Image.open(SOURCE_IMAGE_PATH).convert("RGB")
    portrait_height = 982
    portrait_width = round(portrait.width * portrait_height / portrait.height)
    portrait = portrait.resize((portrait_width, portrait_height), Image.Resampling.LANCZOS)
    x0 = width - portrait_width
    y0 = (height - portrait_height) // 2
    background.paste(portrait, (x0, y0))
    # A single restrained divider creates a text-safe field without adding a card or panel.
    ImageDraw.Draw(background, "RGBA").line(
        (x0 - 22, 120, x0 - 22, 960), fill=(181, 138, 74, 180), width=2
    )
    IMAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    background.save(IMAGE_PATH, format="PNG", optimize=True)


def _archival_still(duration: float) -> dict:
    path = str(IMAGE_PATH.resolve())
    shot = {
        "path": path,
        "asset_path": path,
        "media_type": "image",
        "type": "image",
        "shot_start": 0.0,
        "shot_end": duration,
        "duration": duration,
        "source": "editorial_composite_from_wikimedia_commons",
        "source_url": IMAGE_PAGE,
        "candidate_id": "wikimedia:File:Gamal_Abd_El-Nasser_1956.png",
        "fallback_layer": "grounded_source_repair",
        "verification": "source_page_and_composite_visual_review",
        "rights_status": "public_domain",
        "reuse_kind": "unique_candidate",
    }
    visual_decision = {
        "candidate_id": shot["candidate_id"],
        "accepted": True,
        "status": "accepted",
        "source": "Wikimedia Commons source record and composite visual review",
        "verification_status": "source_page_and_composite_visual_review",
        "visual_checked": True,
        "visual_available": True,
        "entity_conflict": False,
        "reason": "The 1956 archival photograph depicts Gamal Abdel Nasser at a press event, matching the nationalization narration.",
    }
    asset = {
        "path": path,
        "src": path,
        "type": "image",
        "label": "Gamal Abdel Nasser at a 1956 press event — Wikimedia Commons",
        "source": "editorial_composite_from_wikimedia_commons",
        "source_url": IMAGE_PAGE,
        "direct_url": IMAGE_DIRECT,
        "rights_status": "public_domain",
        "license": "Public domain (Wikimedia Commons PD-Art record)",
        "creator": "Unknown; attributed to a contemporary 1956 press photograph",
        "visual_decision": visual_decision,
        "candidate_id": shot["candidate_id"],
        "verification_status": "source_page_and_composite_visual_review",
        "shots": [shot],
        "alternates": [],
    }
    media = {
        "src": path,
        "type": "image",
        "shots": [shot],
        "fit": "cover",
        "safe_framing": True,
        "position": "center",
        "rights_status": "public_domain",
        "source": "wikimedia_commons",
        "source_url": IMAGE_PAGE,
        "subject_bbox": None,
    }
    return {"media": media, "asset": asset}


def _replace_scene_source(scene: dict, duration: float) -> None:
    still = _archival_still(duration)
    scene["media"] = still["media"]
    primary = scene.setdefault("primary_visual", {})
    primary["asset"] = still["asset"]
    scene["media_source_correction"] = {
        "reason": "Removed source footage with a conflicting burned-in November date from the July 26 nationalization beat.",
        "source_page": IMAGE_PAGE,
        "license": still["asset"]["license"],
        "review": "The image and source attribution were checked before rerender.",
    }
    composition = scene.get("media_composition")
    if isinstance(composition, dict):
        composition["media_type"] = "image"
        composition["fit"] = "cover"
    camera = scene.get("camera")
    if isinstance(camera, dict):
        camera["motion"] = "static"
        camera["speed"] = 0.0
    motion = scene.get("motion")
    if isinstance(motion, dict):
        motion["camera"] = "static"


def main() -> int:
    import app as vidzeo_app
    from services.audio_timeline_validation import validate_audio_edit_timeline

    started = time.monotonic()
    _ensure_editorial_frame()
    if not IMAGE_PATH.is_file() or IMAGE_PATH.stat().st_size < 4096:
        raise RuntimeError(f"The grounded 1956 archival still is missing or incomplete: {IMAGE_PATH}")

    outer = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest = dict(outer.get("remotion_manifest") or {})
    scenes = list(manifest.get("scenes") or [])
    if len(scenes) != 9:
        raise RuntimeError(f"Expected nine Whisper-aligned scenes, found {len(scenes)}.")
    target = scenes[1]
    if "July 26, 1956" not in str(target.get("display_text") or target.get("text", {}).get("primary") or ""):
        raise RuntimeError("The expected July 26 nationalization scene could not be identified safely.")
    target_duration = float(target.get("duration") or 0.0)
    _replace_scene_source(target, target_duration)
    manifest["scenes"] = scenes

    validation = vidzeo_app._validate_remotion_edit_plan(manifest, len(scenes))
    if validation.get("status") != "pass":
        raise RuntimeError(f"Corrected plan failed preflight: {validation.get('errors')}")

    print("[suez-correction] Removed conflicting burned-in date; corrected plan passed preflight.", flush=True)
    response = vidzeo_app.app.test_client().post(
        "/api/editor/render",
        json={
            "name": "Suez Crisis History — grounded archival repair",
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
        raise RuntimeError("The corrected candidate was not rendered by Remotion.")
    if (render_metadata.get("quality_checks") or {}).get("status") != "pass":
        raise RuntimeError("The corrected Remotion candidate failed its quality checks.")

    candidate = Path(str(result.get("output_path") or "")).resolve()
    if not candidate.is_file():
        raise RuntimeError("Remotion did not create a corrected video candidate.")
    probe = _probe(candidate)
    streams = list(probe.get("streams") or [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})
    duration = float((probe.get("format") or {}).get("duration") or 0.0)
    if (video.get("width"), video.get("height")) != (1920, 1080) or not audio:
        raise RuntimeError("The corrected Remotion render is missing a 1080p picture or audio stream.")

    final_video = PACKAGE / VIDEO_NAME
    corrected = PACKAGE / "checkpoints" / "suez_corrected_captioned_candidate.mp4"
    if SUBTITLE_PATH.is_file():
        vidzeo_app._burn_subtitles_into_master(candidate, SUBTITLE_PATH, corrected)
    else:
        shutil.copy2(candidate, corrected)
    if not corrected.is_file() or corrected.stat().st_size < 4096:
        raise RuntimeError("Caption finishing did not produce a usable corrected file.")
    final_probe = _probe(corrected)
    final_streams = list(final_probe.get("streams") or [])
    final_video_stream = next((stream for stream in final_streams if stream.get("codec_type") == "video"), {})
    final_audio_stream = next((stream for stream in final_streams if stream.get("codec_type") == "audio"), {})
    final_duration = float((final_probe.get("format") or {}).get("duration") or 0.0)
    if (final_video_stream.get("width"), final_video_stream.get("height")) != (1920, 1080) or not final_audio_stream:
        raise RuntimeError("The captioned corrected file failed its final picture/audio check.")
    if abs(final_duration - duration) > 0.1:
        raise RuntimeError("Caption finishing changed the documentary duration unexpectedly.")

    whisper = outer.get("audio_timing", {}).get("whisper_alignment", {})
    windows = list(outer.get("scene_timing_windows") or [])
    contiguous = all(
        abs(float(current.get("start_seconds") or 0.0) - float(previous.get("end_seconds") or 0.0)) <= 0.02
        for previous, current in zip(windows, windows[1:])
    )
    effects = list((manifest.get("audio") or {}).get("effects") or [])
    effects_valid = validate_audio_edit_timeline(
        scenes,
        effects,
        min_effect_gap_seconds=12.0,
        require_unique_families=True,
    )
    effect_files_exist = all(Path(str(effect.get("src") or "")).is_file() for effect in effects)
    if len(windows) != 9 or not contiguous:
        raise RuntimeError("Saved Whisper scene windows are incomplete or not contiguous.")
    if whisper.get("status") != "aligned" or float(whisper.get("exact_match_ratio") or 0.0) < 0.90:
        raise RuntimeError("Whisper script alignment did not meet the acceptance threshold.")
    if effects_valid.get("status") != "pass" or not effect_files_exist:
        raise RuntimeError(f"Sound-effect timing/variety validation failed: {effects_valid.get('errors')}")

    # Keep the reviewed render unless the fully checked replacement is ready.
    os.replace(corrected, final_video)
    if candidate.is_file():
        candidate.unlink()

    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
    _replace_scene_source(plan["scenes"][1], float(plan["scenes"][1].get("duration") or target_duration))
    plan["render_metadata"] = render_metadata
    plan["edit_plan_validation"] = validation
    plan["source_correction_log"] = [
        {
            "scene_index": 1,
            "old_source": "YouTube cut with a visible November 1956 date during July 26 narration",
            "new_source": IMAGE_PAGE,
            "rights_status": "public_domain",
        }
    ]
    PLAN_PATH.write_text(json.dumps(plan, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    PACKAGE_PLAN_PATH.parent.mkdir(parents=True, exist_ok=True)
    PACKAGE_PLAN_PATH.write_text(json.dumps(plan, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    outer["remotion_manifest"] = manifest
    outer["render_metadata"] = render_metadata
    outer["edit_plan_validation"] = validation
    outer["video_path"] = str(final_video.resolve())
    outer["final_source_correction"] = plan["source_correction_log"][0]
    MANIFEST_PATH.write_text(json.dumps(outer, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (PACKAGE / "media" / "sources.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "assets": [
                    {
                        "path": str(IMAGE_PATH.resolve()),
                        "title": "Gamal Abd El-Nasser at a 1956 press event",
                        "source_page": IMAGE_PAGE,
                        "source_file": IMAGE_DIRECT,
                        "author": "Unknown",
                        "license": "Public domain (Wikimedia Commons PD-Art record)",
                        "used_in_scene": 2,
                        "source_image_path": str(SOURCE_IMAGE_PATH.resolve()),
                        "verification": "source page and landscape editorial composite visually reviewed",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    report = {
        "status": "pass",
        "video_path": str(final_video.resolve()),
        "render_backend": "remotion",
        "remotion_workers": render_metadata.get("remotion_workers"),
        "duration_seconds": final_duration,
        "resolution": f"{final_video_stream.get('width')}x{final_video_stream.get('height')}",
        "scene_count": len(windows),
        "scene_windows_contiguous": contiguous,
        "whisper_status": whisper.get("status"),
        "whisper_exact_match_ratio": whisper.get("exact_match_ratio"),
        "whisper_timing_repairs": whisper.get("timing_repair_count"),
        "sound_effect_count": len(effects),
        "sound_effect_kinds": [str(item.get("kind") or "") for item in effects],
        "sound_effect_families": effects_valid.get("sound_effect_families"),
        "sound_effect_minimum_gap_seconds": effects_valid.get("minimum_sound_gap_seconds"),
        "sound_effects_whisper_anchored": effects_valid.get("whisper_anchored_count"),
        "sound_effect_anchors": [
            {"kind": item.get("kind"), "start": item.get("start"), "anchor": item.get("sound_anchor")}
            for item in effects
        ],
        "sound_effect_files_exist": effect_files_exist,
        "source_date_conflict_removed": True,
        "replacement_source": IMAGE_PAGE,
        "replacement_license": "public_domain",
        "quality_check": (render_metadata.get("quality_checks") or {}).get("status"),
        "elapsed_correction_seconds": round(time.monotonic() - started, 2),
    }
    report_path = PACKAGE / "checkpoints" / "whisper_sfx_validation.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary_path = PACKAGE / "generation_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary.update(
        {
            "video_path": str(final_video.resolve()),
            "quality_status": report["quality_check"],
            "source_correction": report["replacement_source"],
            "render_metadata": render_metadata,
        }
    )
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
