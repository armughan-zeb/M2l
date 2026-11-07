"""Live relevance harness for the identity-first Mixed Media pipeline.

This intentionally uses an ambiguity-heavy automobile topic.  The narration
mentions the Jaguar name's animal origin, while the actual subject is the
classic Jaguar E-Type.  The run exercises real TTS, web discovery, YouTube
discovery, semantic gates, shared visual verification, rendering, and final
manifest accounting.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

SERVER_DIR = Path(__file__).resolve().parent
ROOT = SERVER_DIR.parent
sys.path.insert(0, str(SERVER_DIR))

OUTPUT_DIR = ROOT / "output" / "harness_logs" / "jaguar_e_type_relevance"
REPORT_PATH = OUTPUT_DIR / "relevance_report.json"

TITLE = "The History of Jaguar Cars: Why the 1961 E-Type Still Matters"
SCRIPT = """In 1961, Jaguar unveiled the E-Type, a classic sports car that became one of the defining designs of the automobile age. The name Jaguar suggested speed and grace, but this story is about the machine, not the animal.

Its long bonnet, low nose, independent rear suspension, and straight-six engine gave the E-Type a distinctive profile. Period road tests praised its performance while noting that the car cost far less than many competing grand tourers.

The E-Type was revealed at the Geneva Motor Show, where its shape drew immediate attention from journalists and enthusiasts. The important visual record is the 1960s automobile itself: its body, cockpit, engineering, and period road presence.

More than six decades later, the 1961 Jaguar E-Type remains a landmark of classic car design. Modern sports cars may be faster, but they do not change what this historic automobile represented when it first appeared."""


def _progress(percent: float, message: str) -> None:
    print(f"  [{float(percent) * 100:5.1f}%] {message}", flush=True)


def _manifest_rows(result: dict) -> list[dict]:
    manifest = result.get("component_manifest") if isinstance(result, dict) else None
    persisted_path = result.get("manifest_path") if isinstance(result, dict) else None
    if persisted_path and Path(str(persisted_path)).is_file():
        try:
            persisted = json.loads(Path(str(persisted_path)).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            persisted = None
        if isinstance(persisted, dict):
            # The persisted delivery manifest is the final source of truth.
            # The in-memory component manifest intentionally omits the
            # compositor's normalized video-clips array, so auditing it alone
            # falsely reports every rendered scene as unresolved.
            manifest = persisted
    if not isinstance(manifest, dict):
        return []
    requirements = list(manifest.get("visual_requirements") or [])
    resolutions = list(manifest.get("media_resolution_v2") or [])
    shot_rows = list(manifest.get("media_shots") or [])
    labels = list(manifest.get("source_labels") or [])
    paths = list(manifest.get("media_paths") or [])
    if not labels or not paths:
        delivery_rows = list(manifest.get("video_clips") or [])
        labels = [
            str(row.get("source") or row.get("provider") or "") if isinstance(row, dict) else ""
            for row in delivery_rows
        ]
        paths = [
            str(row.get("path") or "") if isinstance(row, dict) else ""
            for row in delivery_rows
        ]
    rows = []
    for index in range(max(len(requirements), len(resolutions), len(paths))):
        requirement = requirements[index] if index < len(requirements) and isinstance(requirements[index], dict) else {}
        resolution = resolutions[index] if index < len(resolutions) and isinstance(resolutions[index], dict) else {}
        evidence = resolution.get("candidate_evidence") if isinstance(resolution.get("candidate_evidence"), dict) else {}
        rows.append({
            "scene_index": index,
            "entity": requirement.get("canonical_entity", ""),
            "subject_type": requirement.get("subject_type", ""),
            "requirement_hash": requirement.get("requirement_hash", ""),
            "provider": resolution.get("provider", ""),
            "label": labels[index] if index < len(labels) else "",
            "path": paths[index] if index < len(paths) else "",
            "resolution_status": resolution.get("status", ""),
            "verification_status": resolution.get("verification_status", ""),
            "resolution_requirement_hash": resolution.get("requirement_hash", ""),
            "identity_gate_reason": evidence.get("identity_gate_reason", resolution.get("identity_gate_reason", "")),
            "candidate_metadata": evidence.get("metadata", ""),
            "matched_terms": evidence.get("matched_terms", []),
            "visual_decision": resolution.get("visual_decision") or {},
            "fallback_reason": resolution.get("fallback_reason", ""),
            "fallback_layer": resolution.get("fallback_layer", resolution.get("resolution_kind", "")),
            "alternates": resolution.get("alternates") or [],
            "candidate_ledger": resolution.get("candidate_ledger") or [],
            "shots": (
                shot_rows[index].get("shots") or []
                if index < len(shot_rows) and isinstance(shot_rows[index], dict)
                else []
            ),
        })
    return rows


def _write_contact_sheet(rows: list[dict]) -> str:
    """Write a thumbnail audit of every selected shot, when Pillow is available."""
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageOps
    except Exception:
        return ""
    try:
        from tempfile import TemporaryDirectory
        tiles = []
        with TemporaryDirectory(prefix="jaguar_contact_") as temp_dir:
            for row in rows:
                shots = list(row.get("shots") or [])
                if not shots and row.get("path"):
                    shots = [{"path": row.get("path"), "candidate_id": row.get("scene_index")}]
                for shot_index, shot in enumerate(shots):
                    path = Path(str(shot.get("path") or shot.get("asset_path") or ""))
                    if not path.is_file():
                        continue
                    source = path
                    if path.suffix.casefold() in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}:
                        # PNG avoids the Windows FFmpeg MJPEG full-range
                        # encoder failure seen on some downloaded YouTube
                        # clips; the contact sheet converts it to RGB later.
                        thumb_path = Path(temp_dir) / f"scene_{row.get('scene_index', 0)}_{shot_index}.png"
                        try:
                            subprocess.run(
                                ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss",
                             "0", "-i", str(path), "-frames:v", "1",
                                 "-vf", "scale=640:-2", str(thumb_path)],
                                check=False, timeout=20,
                            )
                        except Exception:
                            continue
                        source = thumb_path
                    try:
                        with Image.open(source) as image:
                            tile = ImageOps.fit(image.convert("RGB"), (360, 220), method=Image.Resampling.LANCZOS)
                        label = f"scene {int(row.get('scene_index', 0)) + 1} / shot {shot_index + 1}"
                        draw = ImageDraw.Draw(tile)
                        draw.rectangle((0, 194, 360, 220), fill=(0, 0, 0))
                        draw.text((8, 199), label, fill=(255, 255, 255))
                        tiles.append(tile)
                    except Exception:
                        continue
        if not tiles:
            return ""
        columns = 4
        rows_count = (len(tiles) + columns - 1) // columns
        sheet = Image.new("RGB", (columns * 360, rows_count * 220), (24, 28, 34))
        for index, tile in enumerate(tiles):
            sheet.paste(tile, ((index % columns) * 360, (index // columns) * 220))
        output_path = OUTPUT_DIR / "selected_shots_contact_sheet.jpg"
        sheet.save(output_path, quality=92)
        return str(output_path)
    except Exception:
        return ""


def _audit(result: dict) -> dict:
    rows = _manifest_rows(result)
    external = [row for row in rows if str(row.get("provider", "")).casefold() in {"web", "youtube", "youtube clips", "yt"}]
    mismatches = [
        row for row in external
        if row.get("requirement_hash")
        and row.get("resolution_requirement_hash") != row.get("requirement_hash")
    ]
    unresolved = [row for row in rows if not row.get("path")]
    selected_ledger_rows = []
    rejected_candidates = []
    for row in rows:
        ledger = [item for item in row.get("candidate_ledger") or [] if isinstance(item, dict)]
        selected = [item for item in ledger if item.get("selected")]
        selected_ledger_rows.extend(selected)
        rejected_candidates.extend(
            {
                "scene_index": row.get("scene_index"),
                "candidate_id": item.get("candidate_id", ""),
                "provider": item.get("provider", ""),
                "status": item.get("verification_status") or item.get("status") or "",
                "reason": item.get("rejection_reason") or item.get("identity_gate_reason") or "not_selected",
                "visual_decision": item.get("visual_decision") or {},
            }
            for item in ledger
            if not item.get("selected")
        )
    wildlife_contamination = [
        row for row in rows
        if any(token in str(row.get("candidate_metadata") or "").casefold() for token in ("wildlife", "cheetah", "leopard", "lion", "tiger"))
    ]
    modern_car_contamination = [
        row for row in rows
        if "honda civic type r" in str(row.get("candidate_metadata") or "").casefold()
    ]
    selected_paths = [str(row.get("path") or "") for row in rows if row.get("path")]
    selected_shots = [
        shot
        for row in rows
        for shot in (row.get("shots") or [])
        if isinstance(shot, dict) and shot.get("path")
    ]
    shot_paths = [str(shot.get("path") or "") for shot in selected_shots]
    adjacent_duplicates = sum(
        1 for left, right in zip(selected_paths, selected_paths[1:])
        if left and left == right
    )
    adjacent_shot_duplicates = sum(
        1 for left, right in zip(shot_paths, shot_paths[1:])
        if left and left == right
    )
    duplicate_report = {
        "selected_asset_count": len(selected_paths),
        "unique_asset_count": len(set(selected_paths)),
        "adjacent_duplicate_count": adjacent_duplicates,
        "selected_shot_count": len(selected_shots),
        "unique_shot_path_count": len(set(shot_paths)),
        "adjacent_shot_duplicate_count": adjacent_shot_duplicates,
    }
    metadata = result.get("component_manifest") if isinstance(result, dict) else {}
    persisted_path = result.get("manifest_path") if isinstance(result, dict) else None
    if persisted_path and Path(str(persisted_path)).is_file():
        try:
            persisted = json.loads(Path(str(persisted_path)).read_text(encoding="utf-8"))
            if isinstance(persisted, dict):
                metadata = persisted
        except (OSError, ValueError):
            pass
    coverage = metadata.get("youtube_coverage") if isinstance(metadata, dict) else {}
    shot_summary = metadata.get("media_shot_plan") if isinstance(metadata, dict) else {}
    intro_count = int((shot_summary or {}).get("intro_shot_count") or 0)
    intro_shortfall = bool((shot_summary or {}).get("intro_shortfall"))
    intro_shots = [
        shot
        for row in rows
        for shot in (row.get("shots") or [])
        if isinstance(shot, dict) and shot.get("intro_shot")
    ]
    intro_max_duration = max(
        (float(shot.get("duration") or 0.0) for shot in intro_shots),
        default=0.0,
    )
    intro_reused_segments = sum(
        1 for shot in intro_shots
        if str(shot.get("reuse_kind") or "") != "unique_candidate"
    )
    reuse_count = sum(
        1 for row in rows
        if str(row.get("resolution_status") or "").casefold() == "accepted"
        and str(row.get("fallback_layer") or "").casefold().find("reuse") >= 0
    )
    contact_sheet_path = _write_contact_sheet(rows)
    return {
        "title": TITLE,
        "scene_count": len(rows),
        "external_asset_count": len(external),
        "provider_counts": {
            provider: sum(1 for row in rows if str(row.get("provider", "")).casefold() == provider)
            for provider in sorted({str(row.get("provider", "")).casefold() for row in rows if row.get("provider")})
        },
        "requirement_hash_mismatches": mismatches,
        "unresolved_scenes": unresolved,
        "wildlife_contamination": wildlife_contamination,
        "modern_car_contamination": modern_car_contamination,
        "youtube_coverage": coverage,
        "intro_report": {
            "shot_count": intro_count,
            "required_range": [6, 8],
            "shortfall": intro_shortfall,
            "max_shot_duration_seconds": round(intro_max_duration, 3),
            "max_allowed_shot_duration_seconds": 2.5,
            "reused_motion_segments": intro_reused_segments,
            "passed": 6 <= intro_count <= 8 and not intro_shortfall and intro_max_duration <= 2.5 + 0.03,
        },
        "reuse_count": reuse_count,
        "duplicate_report": duplicate_report,
        "selected_candidate_count": len(selected_ledger_rows),
        "rejected_candidate_count": len(rejected_candidates),
        "rejected_candidates": rejected_candidates,
        "per_scene_relevance": [
            {
                "scene_index": row.get("scene_index"),
                "entity": row.get("entity", ""),
                "subject_type": row.get("subject_type", ""),
                "verification_status": row.get("verification_status", ""),
                "identity_score": (row.get("visual_decision") or {}).get("identity_score"),
                "context_score": (row.get("visual_decision") or {}).get("context_score"),
                "final_score": (row.get("visual_decision") or {}).get("final_score"),
            }
            for row in rows
        ],
        "contact_sheet_path": contact_sheet_path,
        "rows": rows,
        "passed": bool(rows)
        and not mismatches
        and not unresolved
        and not wildlife_contamination
        and not modern_car_contamination
        and (
            not shot_summary
            or (
                6 <= intro_count <= 8
                and not intro_shortfall
                and intro_max_duration <= 2.5 + 0.03
            )
        ),
    }


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if "--audit-only" in sys.argv:
        try:
            previous = json.loads(REPORT_PATH.read_text(encoding="utf-8")) if REPORT_PATH.is_file() else {}
        except (OSError, ValueError):
            previous = {}
        manifest_path = str(
            previous.get("manifest_path")
            or (ROOT / "temp" / "manifests" / "Jaguar E-Type Relevance Harness.manifest.json")
        )
        audit = _audit({"manifest_path": manifest_path})
        audit["elapsed_seconds"] = previous.get("elapsed_seconds")
        audit["video_path"] = str(previous.get("video_path") or (OUTPUT_DIR / "Jaguar E-Type Relevance Harness.mp4"))
        audit["manifest_path"] = manifest_path
        audit["pipeline_success"] = bool(previous.get("pipeline_success"))
        REPORT_PATH.write_text(json.dumps(audit, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        print(json.dumps({
            "passed": bool(audit["passed"] and audit["pipeline_success"]),
            "provider_counts": audit["provider_counts"],
            "external_asset_count": audit["external_asset_count"],
            "requirement_hash_mismatches": len(audit["requirement_hash_mismatches"]),
            "unresolved_scenes": len(audit["unresolved_scenes"]),
            "video_path": audit["video_path"],
            "manifest_path": audit["manifest_path"],
            "report_path": str(REPORT_PATH),
        }, indent=2), flush=True)
        return 0 if audit["passed"] and audit["pipeline_success"] else 2
    # Keep this harness real, but bounded.  It uses the configured OpenRouter
    # key and YouTube browser cookies from the local app environment.
    os.environ.setdefault("VIDZEO_WEB_FAST_MODE", "1")
    os.environ.setdefault("VIDZEO_WEB_WAIT_FOR_FINAL_AUDIO_SCENES", "1")
    os.environ.setdefault("VIDZEO_WEB_VERBOSE", "1")
    os.environ.setdefault("VIDZEO_WEB_ENABLE_GDL", "1")
    os.environ.setdefault("VIDZEO_WEB_GDL_RANGE", "20")
    # The release gate must never report a pass after silently falling back to
    # the retired legacy motion renderer. Native FFmpeg recovery remains an
    # explicit, auditable path when a semantic filter edge case occurs.
    os.environ.setdefault("VIDZEO_SEMANTIC_RENDER_REQUIRED", "1")
    os.environ.setdefault("VIDZEO_ALLOW_LEGACY_RENDER_FALLBACK", "0")
    os.environ.setdefault("VIDZEO_YT_REJECT_TALKING_HEADS", "1")
    os.environ.setdefault("VIDZEO_YT_BLUR_SMALL_LOGOS", "1")
    os.environ.setdefault("VIDZEO_YT_POOL_SOURCE_SHORTLIST", "10")
    os.environ.setdefault("VIDZEO_YT_POOL_SOURCE_RESERVES", "3")
    os.environ.setdefault("VIDZEO_YT_POOL_TOTAL_SOURCES", "12")
    os.environ.setdefault("VIDZEO_VISUAL_FINALIST_LIMIT", "8")
    os.environ.setdefault("VIDZEO_MIXED_YT_TARGET_RATIO", "0.60")
    os.environ.setdefault("VIDZEO_YOUTUBE_ACCEPTED_COVERAGE_TARGET", "0.60")

    config = {
        "script": SCRIPT,
        "title": TITLE,
        "name": "Jaguar E-Type Relevance Harness",
        "image_source": "Mixed Media",
        "mixed_media_policy": "automatic",
        "pipeline_v2_mode": "on",
        "semantic_render_required": True,
        "allow_legacy_render_fallback": False,
        # Exercise the real semantic verifier when the local OpenRouter key
        # is available. If it is unavailable, strict scenes must still fail
        # closed into grounded graphics rather than unrelated media.
        "visual_understanding": True,
        "visual_required": True,
        "allow_emergency_media": False,
        "type": "long",
        "scene_mode": "Every 2 Beats",
        "vo_mode": "Use Audiogram",
        "voice": "en-US-ChristopherNeural",
        "tts_model": "audiogram",
        "whisper_mode": "local",
        "aspect_ratio": "16:9",
        "resolution": "720p",
        "render_resolution": (1280, 720),
        "output_dir": str(OUTPUT_DIR),
        "visual_deadline_seconds": 900,
        "visual_shared_deadline_seconds": 900,
        "media_deadline_seconds": 600,
        "yt_branch_wall_timeout_seconds": 420,
        "youtube_accepted_coverage_target": 0.60,
        "cross_source_fallback": True,
        "visual_verification_frame_count": 5,
        "intro_media_duration_seconds": 15.0,
        "intro_media_min_shots": 6,
        "intro_media_max_shots": 8,
        "max_still_hold_seconds": 3.5,
    }

    started = time.monotonic()
    print("=" * 88)
    print("JAGUAR E-TYPE IDENTITY / RELEVANCE LIVE HARNESS")
    print(f"Title: {TITLE}")
    print("Expected subject: classic Jaguar automobile, not wildlife and not a modern unrelated car")
    print("=" * 88, flush=True)

    try:
        from app import _run_video_pipeline

        result = _run_video_pipeline(config, progress_callback=_progress)
        audit = _audit(result if isinstance(result, dict) else {})
        audit["elapsed_seconds"] = round(time.monotonic() - started, 1)
        audit["video_path"] = str((result or {}).get("video_path") or "") if isinstance(result, dict) else ""
        audit["manifest_path"] = str((result or {}).get("manifest_path") or "") if isinstance(result, dict) else ""
        audit["pipeline_success"] = bool(isinstance(result, dict) and result.get("success"))
        REPORT_PATH.write_text(json.dumps(audit, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

        print("\n" + "=" * 88)
        print(f"Pipeline success: {audit['pipeline_success']}")
        print(f"Elapsed: {audit['elapsed_seconds']}s")
        print(f"Scenes: {audit['scene_count']}")
        print(f"Providers: {audit['provider_counts']}")
        print(f"YouTube accepted coverage: {audit['youtube_coverage']}")
        print(f"Intro shots: {audit['intro_report']}")
        print(f"Reuse count: {audit['reuse_count']}")
        print(f"Requirement hash mismatches: {len(audit['requirement_hash_mismatches'])}")
        print(f"Unresolved scenes: {len(audit['unresolved_scenes'])}")
        print(f"Video: {audit['video_path']}")
        print(f"Manifest: {audit['manifest_path']}")
        print(f"Report: {REPORT_PATH}")
        print(f"RELEVANCE HARNESS: {'PASS' if audit['passed'] and audit['pipeline_success'] else 'FAIL'}")
        print("=" * 88, flush=True)
        for row in audit["rows"]:
            print(
                f"scene={row['scene_index']} provider={row['provider']} "
                f"entity={row['entity']} gate={row['identity_gate_reason']} "
                f"verification={row['verification_status']} "
                f"metadata={str(row['candidate_metadata'])[:120]}",
                flush=True,
            )
        return 0 if audit["passed"] and audit["pipeline_success"] else 2
    except Exception as exc:
        failure = {
            "passed": False,
            "pipeline_success": False,
            "elapsed_seconds": round(time.monotonic() - started, 1),
            "error": repr(exc),
        }
        REPORT_PATH.write_text(json.dumps(failure, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nJAGUAR HARNESS FAILED after {failure['elapsed_seconds']}s: {exc}", flush=True)
        traceback.print_exc()
        print(f"Report: {REPORT_PATH}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
