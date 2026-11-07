"""Run a real two-minute Train Crew documentary production.

This operator harness uses the same production pipeline as the desktop queue,
with live YouTube and web-image retrieval enabled.  It writes a self-contained
report beside the finished MP4 and never prints provider credentials.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app as app_module  # noqa: E402


TITLE = "Train Crew: The Hidden Team Behind Every Journey"
SCRIPT = (
    "Before a passenger ever sees a platform, a train crew has already turned a timetable into a safe plan. "
    "The locomotive engineer reads the route, checks the controls, and manages power and braking through every change in grade. "
    "The conductor coordinates the people inside the train, confirms doors and signals, and keeps the journey moving when plans change. "
    "Behind them, dispatchers protect the path through the network, balancing other trains, maintenance windows, and emergency decisions. "
    "At a busy station, a few seconds can involve a chain of precise calls: platform staff report that boarding is complete, the conductor verifies the signal, and the engineer moves only when the route is clear. "
    "On long-distance services, the crew also manages fatigue, weather, track restrictions, and the small problems passengers never notice. "
    "Before departure, they walk through a routine that looks ordinary but carries a serious purpose: equipment is checked, doors are tested, the radio is confirmed, and the route information is read back instead of assumed. "
    "During the run, the engineer watches speed and signals while the conductor watches the platform, the passengers, and the condition of the train. "
    "If a signal changes, a door reports a fault, or a section of track is restricted, the crew pauses the rhythm and follows the rulebook step by step. "
    "Their work is part technical discipline and part communication. A hand signal, a radio confirmation, or a checklist can prevent a delay from becoming a dangerous mistake. "
    "When a train reaches its destination on time, the result may look effortless. That quiet arrival is the product of people watching the rails, the timetable, the machines, and one another. "
    "Train crews are the human control system of the railway: trained to move thousands of people through a complex network, one verified decision at a time."
)


def progress(pct: float, message: str) -> None:
    print(f"[progress {float(pct):5.1f}%] {message}", flush=True)


def _manifest_summary(manifest: dict) -> dict:
    items = list(manifest.get("media_items") or manifest.get("items") or manifest.get("video_clips") or [])
    counts: dict[str, int] = {}
    fallback_reasons: list[str] = []
    for item in items:
        source = str(item.get("source") or item.get("provider") or "unknown")
        counts[source] = counts.get(source, 0) + 1
        reason = str(item.get("fallback_reason") or "").strip()
        if reason:
            fallback_reasons.append(reason)
    accounting = manifest.get("source_accounting")
    if isinstance(accounting, dict):
        counts = {str(key): int(value or 0) for key, value in accounting.items()}
    return {
        "item_count": len(items),
        "source_counts": counts,
        "fallback_count": len(fallback_reasons),
        "fallback_reasons": fallback_reasons[:30],
        "duration": manifest.get("duration"),
        "fps": manifest.get("fps"),
        "scene_count": len(manifest.get("scenes") or []),
    }


def main() -> int:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    run_dir = ROOT / "output" / "productions" / f"train_crew_2min_real_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    config = {
        "script": SCRIPT,
        "title": TITLE,
        "content_title": TITLE,
        "name": TITLE,
        "output_name": f"Train Crew - The Hidden Team {stamp}",
        "output_dir": str(run_dir),
        "topic": "train crew railway operations",
        "topic_anchor": "train crew railway operations",
        "category": "documentary",
        "type": "long",
        "image_source": "Mixed Media",
        "media_weights": {"youtube_clips": 65, "web_images": 35, "stock_media": 0},
        "scene_mode": "Every 2 Beats",
        "captions_enabled": True,
        "caption_style": "hormozi",
        "caption_mode": "smart_emphasis",
        "words_per_group": 3,
        "bold": True,
        "uppercase": True,
        "font": "Montserrat-Bold",
        "highlight_color": "#FFE600",
        "semantic_theme": "documentary",
        "semantic_fast_render": False,
        "aspect_ratio": "16:9",
        "resolution": "1080p",
        "render_resolution": (1920, 1080),
        "quality": "standard",
        "whisper_mode": "local",
        "voice": "en-US-ChristopherNeural",
        "tts_model": "audiogram",
        "allow_emergency_media": True,
        "yt_lenient_acceptance": True,
        "yt_stop_on_soft_timeout": True,
        "yt_stream_downloads": True,
        "yt_download_candidate_limit": 6,
        "yt_allow_same_source_reuse": True,
        "yt_max_download_scenes": 0,
        "yt_min_resolved_ratio_before_soft_stop": 0.60,
        "intro_media_duration_seconds": 15.0,
        "intro_media_min_shots": 6,
        "intro_media_max_shots": 8,
        "intro_max_shot_seconds": 2.5,
        "max_still_hold_seconds": 3.5,
        "max_visual_beat_seconds": 3.5,
        "animate_all_media": True,
        "variation_seed": int(time.time()),
    }

    print("=" * 78)
    print("REAL TRAIN CREW TWO-MINUTE MIXED-MEDIA PRODUCTION")
    print(f"Title: {TITLE}")
    print(f"Run directory: {run_dir}")
    print("Live endpoints: OpenRouter scene brain, YouTube retrieval, web-image retrieval")
    print("Still-image motion: centered contain + non-repeating bounded camera beats")
    print("=" * 78, flush=True)

    result: dict = {}
    error: str | None = None
    try:
        result = app_module._run_video_pipeline(config, progress_callback=progress)
    except Exception as exc:
        import traceback

        error = f"{type(exc).__name__}: {exc}"
        (run_dir / "pipeline_error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        print(f"[error] {error}", flush=True)

    elapsed = time.monotonic() - started
    video_path = Path(str(result.get("video_path") or "")) if isinstance(result, dict) else Path()
    manifest_path = Path(str(result.get("manifest_path") or "")) if isinstance(result, dict) else Path()
    manifest = {}
    if manifest_path and manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
    report = {
        "success": bool(not error and video_path.is_file() and video_path.stat().st_size > 65536),
        "elapsed_seconds": round(elapsed, 2),
        "title": TITLE,
        "script_word_count": len(SCRIPT.split()),
        "video_path": str(video_path.resolve()) if video_path else "",
        "manifest_path": str(manifest_path.resolve()) if manifest_path else "",
        "media_summary": _manifest_summary(manifest),
        "result_keys": sorted(result.keys()) if isinstance(result, dict) else [],
        "error": error,
    }
    report_path = run_dir / "run_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str), flush=True)
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
