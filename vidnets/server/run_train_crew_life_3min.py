"""Generate a three-minute documentary about the working life of train crews."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

# Use the real semantic/FFmpeg production path. Remotion is not a media
# fallback for this job; unresolved real media remains visible in the audit.
os.environ.setdefault("VIDZEO_REMOTION_RENDER", "0")
os.environ.setdefault("VIDZEO_SEMANTIC_RENDER_REQUIRED", "1")
os.environ.setdefault("VIDZEO_ALLOW_LEGACY_RENDER_FALLBACK", "0")
os.environ.setdefault("VIDZEO_WEB_SAME_QUERY_CANDIDATES", "8")
os.environ.setdefault("VIDZEO_WEB_VISION_SHORTLIST", "8")
os.environ.setdefault("VIDZEO_WEB_ENABLE_GDL", "1")
# Keep strict mixed-media jobs honest: if the web lane has only one downloaded
# still, leave the scene unresolved so the coordinator can try YouTube or an
# extracted frame instead of manufacturing coverage by copying that still.
os.environ.setdefault("VIDZEO_WEB_ALLOW_DETERMINISTIC_REUSE", "0")
os.environ.setdefault("VIDZEO_YT_REJECT_TALKING_HEADS", "1")
os.environ.setdefault("VIDZEO_YT_BLUR_SMALL_LOGOS", "1")
# The harness deliberately gives the shared verifier a real shortlist rather
# than one YouTube metadata winner. Six bounded attempts per eligible scene
# are enough to include occupational-role matches, alternate publishers, and
# timestamp variants without turning retrieval into an unbounded crawl.
os.environ.setdefault("VIDZEO_MIXED_YT_DOWNLOAD_CANDIDATES", "6")
os.environ.setdefault("VIDZEO_MIXED_YT_DOWNLOAD_ATTEMPTS", "6")
os.environ.setdefault("VIDZEO_YT_PROGRESSIVE_ATTEMPTS", "6")
os.environ.setdefault("VIDZEO_YT_V2_QUERIES_PER_CONTAINER", "9")
os.environ.setdefault("VIDZEO_YT_RESULTS_PER_QUERY", "24")

TITLE = "Train Crew: Conductors and Locomotive Engineers"
SCRIPT = """Before most passengers arrive at the station, the train crew is already at work. A journey begins with a briefing, a safety check, and a careful look at the route ahead. The driver, conductor, and operations team are not simply moving a train. They are preparing a living workplace that will carry hundreds of people through changing conditions.

For the driver, the cab is a place of constant attention. Speed limits, signals, gradients, weather, track conditions, and instructions from control all have to be read together. Every movement of the throttle and brake is deliberate. A quiet stretch of line still demands concentration, because a signal can change, an obstruction can appear, or a station may arrive sooner than expected.

The conductor works through the train. Before departure, they check doors, communicate with the cab, confirm that the platform is clear, and make sure passengers can travel safely. During the trip, the job combines routine with judgment: answering questions, assisting families, supporting older passengers, responding to disruptions, and keeping people informed when the timetable changes.

Train crew life is built around teamwork. The driver may be focused on the railway ahead while the conductor watches the passenger environment behind the cab. Dispatchers and station staff pass information between them. When a service is delayed, the crew must make decisions calmly and explain them clearly. The public sees a late train; the crew sees a chain of signals, platform availability, maintenance reports, and safety rules that must all line up before the doors can open.

The schedule can be demanding. Early starts, late finishes, weekends, holidays, and long periods away from home are part of the profession. Fatigue management is therefore not a slogan. Rest, handovers, and fitness for duty protect both the crew and the people on board. Professionalism often looks quiet: a checklist completed correctly, a door watched until it is secure, or a warning passed to the next team before it becomes a problem.

There are human moments too. A familiar commuter says good morning. A child watches the driver’s cab with wonder. A conductor helps someone find the right platform after a difficult connection. These moments do not appear on a timetable, but they are part of what makes the work meaningful.

Train crew life is a balance of precision and service. Behind every arrival is a team reading the railway, caring for passengers, and making hundreds of small decisions safely. The next time a train pulls into the station, remember that the journey depends on people who began working long before the passengers stepped aboard."""

OUTPUT_DIR = ROOT / "output" / "train_crew_life_3min"
REPORT_PATH = OUTPUT_DIR / "relevance_report.json"


def _progress(fraction: float, message: str) -> None:
    print(f"  [{float(fraction) * 100:5.1f}%] {message}", flush=True)


def _manifest_rows(result: dict) -> list[dict]:
    manifest = result.get("component_manifest") if isinstance(result, dict) else None
    persisted_path = result.get("manifest_path") if isinstance(result, dict) else None
    if persisted_path and Path(str(persisted_path)).is_file():
        try:
            persisted = json.loads(Path(str(persisted_path)).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            persisted = None
        if isinstance(persisted, dict):
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
        paths = [str(row.get("path") or "") if isinstance(row, dict) else "" for row in delivery_rows]
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
            "visual_decision": resolution.get("visual_decision") or {},
            "fallback_reason": resolution.get("fallback_reason", ""),
            "fallback_layer": resolution.get("fallback_layer", resolution.get("resolution_kind", "")),
            "candidate_ledger": resolution.get("candidate_ledger") or [],
            "shots": (
                shot_rows[index].get("shots") or []
                if index < len(shot_rows) and isinstance(shot_rows[index], dict)
                else []
            ),
        })
    return rows


def _write_contact_sheet(rows: list[dict]) -> str:
    try:
        from PIL import Image, ImageDraw, ImageOps
        from tempfile import TemporaryDirectory
    except Exception:
        return ""
    try:
        tiles = []
        with TemporaryDirectory(prefix="train_contact_") as temp_dir:
            for row in rows:
                shots = list(row.get("shots") or [])
                if not shots and row.get("path"):
                    shots = [{"path": row.get("path"), "clip_start": 0}]
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
                        subprocess.run(
                            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss",
                             "0", "-i", str(path), "-frames:v", "1",
                             "-vf", "scale=640:-2", str(thumb_path)],
                            check=False,
                            timeout=20,
                        )
                        source = thumb_path
                    try:
                        with Image.open(source) as image:
                            tile = ImageOps.fit(image.convert("RGB"), (360, 220), method=Image.Resampling.LANCZOS)
                        draw = ImageDraw.Draw(tile)
                        draw.rectangle((0, 194, 360, 220), fill=(0, 0, 0))
                        draw.text((8, 199), f"scene {int(row.get('scene_index', 0)) + 1} / shot {shot_index + 1}", fill=(255, 255, 255))
                        tiles.append(tile)
                    except Exception:
                        continue
        if not tiles:
            return ""
        columns = 4
        sheet = Image.new("RGB", (columns * 360, ((len(tiles) + columns - 1) // columns) * 220), (24, 28, 34))
        for index, tile in enumerate(tiles):
            sheet.paste(tile, ((index % columns) * 360, (index // columns) * 220))
        output_path = OUTPUT_DIR / "selected_shots_contact_sheet.jpg"
        sheet.save(output_path, quality=92)
        return str(output_path)
    except Exception:
        return ""


def _same_shot_visual(left: dict, right: dict) -> bool:
    """Detect path or underlying-fingerprint duplication in adjacent shots."""
    left_path = os.path.normcase(os.path.normpath(str(
        left.get("path") or left.get("asset_path") or ""
    ).strip()))
    right_path = os.path.normcase(os.path.normpath(str(
        right.get("path") or right.get("asset_path") or ""
    ).strip()))
    if left_path and right_path and left_path == right_path:
        return True
    left_fingerprint = str(left.get("asset_fingerprint") or left.get("fingerprint") or "").strip().casefold()
    right_fingerprint = str(right.get("asset_fingerprint") or right.get("fingerprint") or "").strip().casefold()
    return bool(left_fingerprint and right_fingerprint and left_fingerprint == right_fingerprint)


def _audit(result: dict) -> dict:
    rows = _manifest_rows(result)
    external = [row for row in rows if str(row.get("provider", "")).casefold() in {"web", "youtube", "youtube clips", "yt"}]
    mismatches = [
        row for row in external
        if row.get("requirement_hash") and row.get("resolution_requirement_hash") != row.get("requirement_hash")
    ]
    unresolved = [
        {"scene_index": row.get("scene_index"), "reason": row.get("fallback_reason") or row.get("identity_gate_reason") or "no_selected_media"}
        for row in rows if not row.get("path")
    ]
    selected_paths = [str(row.get("path") or "") for row in rows if row.get("path")]
    selected_shots = [shot for row in rows for shot in (row.get("shots") or []) if isinstance(shot, dict) and shot.get("path")]
    shot_paths = [str(shot.get("path") or "") for shot in selected_shots]
    ledger = [item for row in rows for item in (row.get("candidate_ledger") or []) if isinstance(item, dict)]
    rejected = [
        {
            "scene_index": row.get("scene_index"),
            "candidate_id": item.get("candidate_id", ""),
            "provider": item.get("provider", ""),
            "status": item.get("verification_status") or item.get("status") or "",
            "reason": item.get("rejection_reason") or item.get("identity_gate_reason") or "not_selected",
        }
        for row in rows for item in (row.get("candidate_ledger") or [])
        if isinstance(item, dict) and not item.get("selected")
    ]
    manifest = result.get("component_manifest") if isinstance(result, dict) else {}
    persisted_path = result.get("manifest_path") if isinstance(result, dict) else None
    if persisted_path and Path(str(persisted_path)).is_file():
        try:
            loaded = json.loads(Path(str(persisted_path)).read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                manifest = loaded
        except (OSError, ValueError):
            pass
    shot_summary = manifest.get("media_shot_plan") if isinstance(manifest, dict) else {}
    intro_count = int((shot_summary or {}).get("intro_shot_count") or 0)
    intro_shortfall = bool((shot_summary or {}).get("intro_shortfall"))
    intro_shots = [shot for row in rows for shot in (row.get("shots") or []) if isinstance(shot, dict) and shot.get("intro_shot")]
    intro_max = max((float(shot.get("duration") or 0) for shot in intro_shots), default=0.0)
    contact_sheet = _write_contact_sheet(rows)
    duplicate_report = {
        "selected_asset_count": len(selected_paths),
        "unique_asset_count": len(set(selected_paths)),
        "adjacent_duplicate_count": sum(1 for left, right in zip(selected_paths, selected_paths[1:]) if left and left == right),
        "selected_shot_count": len(selected_shots),
        "unique_shot_path_count": len(set(shot_paths)),
        "adjacent_shot_duplicate_count": sum(
            1 for left, right in zip(selected_shots, selected_shots[1:])
            if _same_shot_visual(left, right)
        ),
    }
    coverage = manifest.get("youtube_coverage") if isinstance(manifest, dict) else {}
    return {
        "title": TITLE,
        "scene_count": len(rows),
        "external_asset_count": len(external),
        "provider_counts": {provider: sum(1 for row in rows if str(row.get("provider", "")).casefold() == provider) for provider in sorted({str(row.get("provider", "")).casefold() for row in rows if row.get("provider")})},
        "youtube_coverage": coverage,
        "requirement_hash_mismatches": mismatches,
        "unresolved_scenes": unresolved,
        "intro_report": {
            "shot_count": intro_count,
            "required_range": [6, 8],
            "shortfall": intro_shortfall,
            "max_shot_duration_seconds": round(intro_max, 3),
            "passed": 6 <= intro_count <= 8 and not intro_shortfall and intro_max <= 2.53,
        },
        "duplicate_report": duplicate_report,
        "selected_candidate_count": len([item for item in ledger if item.get("selected")]),
        "rejected_candidate_count": len(rejected),
        "rejected_candidates": rejected,
        "fallback_layer_counts": {layer: sum(1 for row in rows if str(row.get("fallback_layer") or "") == layer) for layer in sorted({str(row.get("fallback_layer") or "") for row in rows if row.get("fallback_layer")})},
        "per_scene_relevance": [
            {
                "scene_index": row.get("scene_index"),
                "entity": row.get("entity", ""),
                "subject_type": row.get("subject_type", ""),
                "provider": row.get("provider", ""),
                "verification_status": row.get("verification_status", ""),
                "identity_gate_reason": row.get("identity_gate_reason", ""),
                "fallback_layer": row.get("fallback_layer", ""),
                "identity_score": (row.get("visual_decision") or {}).get("identity_score"),
                "context_score": (row.get("visual_decision") or {}).get("context_score"),
                "final_score": (row.get("visual_decision") or {}).get("final_score"),
            }
            for row in rows
        ],
        "contact_sheet_path": contact_sheet,
        "rows": rows,
        "passed": bool(rows) and not mismatches and not unresolved and duplicate_report["adjacent_duplicate_count"] == 0 and duplicate_report["adjacent_shot_duplicate_count"] == 0 and (not shot_summary or (6 <= intro_count <= 8 and not intro_shortfall and intro_max <= 2.53)),
    }


def main() -> int:
    import app as vidzeo_app

    config = {
        "type": "long",
        "name": "Train Crew Life 3 Minute",
        "title": TITLE,
        "content_title": TITLE,
        # Keep the searchable visual identity concrete. "Train Crew Life" is
        # the subject/theme, while this anchor names the people and job class
        # that providers can actually return and vision can verify.
        "topic_anchor": "Train Crew",
        "_topic_anchor": "Train Crew",
        "script": SCRIPT,
        "image_source": "Mixed Media",
        "mixed_media_policy": "automatic",
        "pipeline_v2_mode": "on",
        "visual_understanding": True,
        "visual_required": True,
        "strict_retrieval_accuracy": True,
        "allow_emergency_media": False,
        # This release harness validates the requested web/YouTube resolver
        # and cross-source fallback layers. Keep stock out of the experiment
        # so a planner's transient niche label cannot launch repeated stock
        # downloads for unresolved scenes or mask a web/YouTube shortfall.
        "allow_relevant_stock": False,
        "cross_source_fallback": True,
        "youtube_accepted_coverage_target": 0.60,
        "scene_mode": "Every 2 Beats",
        "vo_mode": "Use Audiogram",
        "voice": "en-US-ChristopherNeural",
        "tts_model": "audiogram",
        "whisper_mode": "local",
        "captions_enabled": True,
        "caption_mode": "phrase",
        "words_per_group": 4,
        "aspect_ratio": "16:9",
        "resolution": "720p",
        "render_resolution": (1280, 720),
        "quality": "standard",
        "semantic_edit_enable": True,
        "semantic_render_enabled": True,
        "semantic_render_concurrency": 2,
        "output_dir": str(OUTPUT_DIR),
        "visual_verification_frame_count": 5,
        "intro_media_duration_seconds": 15.0,
        "intro_media_min_shots": 6,
        "intro_media_max_shots": 8,
        "intro_max_shot_seconds": 2.5,
        "max_still_hold_seconds": 3.5,
        "max_visual_beat_seconds": 3.5,
        "media_deadline_seconds": 900,
        "visual_deadline_seconds": 900,
        "visual_shared_deadline_seconds": 900,
        "yt_branch_wall_timeout_seconds": 420,
        "auto_export": True,
    }

    print("=" * 88)
    print("TRAIN CREW LIFE — THREE-MINUTE PRODUCTION")
    print(f"Title: {TITLE}")
    print(f"Script words: {len(SCRIPT.split())}")
    print("Resolver: unified strict Mixed Media with web/YouTube cross-fallback")
    print("=" * 88, flush=True)

    started = time.monotonic()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        result = vidzeo_app._run_video_pipeline(config, progress_callback=_progress)
        elapsed = time.monotonic() - started
        video_path = str((result or {}).get("video_path") or (result or {}).get("output_path") or "")
        manifest_path = str((result or {}).get("manifest_path") or "")
        audit = _audit(result if isinstance(result, dict) else {})
        audit.update({
            "elapsed_seconds": round(elapsed, 1),
            "video_path": video_path,
            "manifest_path": manifest_path,
            "pipeline_success": bool(isinstance(result, dict) and result.get("success")),
        })
        REPORT_PATH.write_text(json.dumps(audit, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        summary = {
            "success": bool(audit["pipeline_success"] and audit["passed"] and Path(video_path).is_file()),
            "pipeline_success": audit["pipeline_success"],
            "audit_passed": audit["passed"],
            "elapsed_seconds": audit["elapsed_seconds"],
            "video_path": video_path,
            "manifest_path": manifest_path,
            "report_path": str(REPORT_PATH),
            "contact_sheet_path": audit.get("contact_sheet_path", ""),
            "script_words": len(SCRIPT.split()),
            "duration_seconds": (result or {}).get("duration") if isinstance(result, dict) else None,
            "scene_count": audit["scene_count"],
            "unresolved_scene_count": len(audit["unresolved_scenes"]),
            "youtube_coverage": audit["youtube_coverage"],
        }
        summary_path = OUTPUT_DIR / "generation_summary.json"
        summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        print(json.dumps(summary, indent=2), flush=True)
        print(f"Summary: {summary_path}", flush=True)
        print(f"Report: {REPORT_PATH}", flush=True)
        return 0 if summary["success"] else 1
    except Exception as exc:
        failure = {
            "success": False,
            "pipeline_success": False,
            "audit_passed": False,
            "elapsed_seconds": round(time.monotonic() - started, 1),
            "script_words": len(SCRIPT.split()),
            "error": repr(exc),
            "error_detail": getattr(exc, "detail", {}) or {},
        }
        REPORT_PATH.write_text(json.dumps(failure, indent=2, ensure_ascii=False), encoding="utf-8")
        (OUTPUT_DIR / "generation_summary.json").write_text(json.dumps(failure, indent=2), encoding="utf-8")
        print(f"\nTRAIN CREW HARNESS FAILED after {failure['elapsed_seconds']}s: {exc}", flush=True)
        traceback.print_exc()
        print(f"Report: {REPORT_PATH}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
