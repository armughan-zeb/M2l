"""Generate a full 2-minute documentary video about the 1961 Jaguar E-Type."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
OUTPUT_DIR = ROOT / "output" / "productions" / "jaguar_e_type_2min"
sys.path.insert(0, str(SERVER))

# Configure real-media pipeline flags
os.environ.setdefault("VIDZEO_REMOTION_RENDER", "0")
os.environ.setdefault("VIDZEO_SEMANTIC_RENDER_REQUIRED", "1")
os.environ.setdefault("VIDZEO_ALLOW_LEGACY_RENDER_FALLBACK", "0")
os.environ.setdefault("VIDZEO_YT_LENIENT_ACCEPTANCE", "1")
os.environ.setdefault("VIDZEO_YT_RELAXED_VISUAL_GATE", "1")
os.environ.setdefault("VIDZEO_YT_FAIL_OPEN_ON_VISUAL_UNAVAILABLE", "1")
os.environ.setdefault("VIDZEO_YT_PREFER_WHISPER_TIMING", "1")
os.environ.setdefault("VIDZEO_YT_PREFER_DISTINCT_SOURCES", "1")
os.environ.setdefault("VIDZEO_YT_NEAREST_YEAR_TOLERANCE", "10")
os.environ.setdefault("VIDZEO_YT_RESULTS_PER_QUERY", "20")
os.environ.setdefault("VIDZEO_STOCK_FINAL_RESERVE_SECONDS", "120")
os.environ.setdefault("VIDZEO_WEB_FAST_MODE", "1")
os.environ.setdefault("VIDZEO_WEB_SAME_QUERY_CANDIDATES", "8")
os.environ.setdefault("VIDZEO_WEB_VISION_SHORTLIST", "8")
os.environ.setdefault("VIDZEO_WEB_ALLOW_DETERMINISTIC_REUSE", "0")
os.environ.setdefault("VIDZEO_YT_REJECT_TALKING_HEADS", "1")
os.environ.setdefault("VIDZEO_YT_BLUR_SMALL_LOGOS", "1")

TITLE = "The 1961 Jaguar E-Type: The Icon That Defined Sports Cars"
ENTITY_TITLE = "1961 Jaguar E-Type"

SCRIPT = """In March 1961 at the Geneva Motor Show, Jaguar introduced a car that would redefine performance and automotive beauty forever: the Jaguar E-Type. Its aerodynamic curves and breathtaking proportions stunned the world, prompting Enzo Ferrari to famously call it the most beautiful car ever made.

Beneath its sculpted, forward-tilting bonnet lay a racing-bred 3.8-liter straight-six engine delivering two hundred and sixty-five horsepower. Capable of reaching a top speed of one hundred and fifty miles per hour, the E-Type offered supercar performance at a fraction of the cost of its Italian and German competitors.

The engineering was just as revolutionary as its styling. Developed from Jaguar's Le Mans-winning D-Type racers, it featured a lightweight monocoque chassis, four-wheel disc brakes, and an advanced independent rear suspension system that delivered unmatched balance and road handling.

Public demand was so overwhelming that Jaguar drove a second demonstration roadster overnight from Coventry to Geneva just to satisfy international journalists. Road tests praised not only its blistering acceleration, but also its daily usability and refined ride quality.

Over six decades later, the Series 1 Jaguar E-Type remains a high-water mark of industrial design and British motorsport pedigree. It proved that cutting-edge technology and timeless elegance could exist in perfect harmony, securing its place as an enduring legend of the open road."""


def progress(event):
    if isinstance(event, dict):
        label = event.get("message") or event.get("stage") or event.get("status")
        pct = event.get("progress") or event.get("percent")
        pct_str = f"[{float(pct)*100:5.1f}%] " if pct is not None else ""
        if label:
            print(f"  {pct_str}{label}", flush=True)
    elif event:
        print(f"  {event}", flush=True)


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    import app as vidzeo_app

    config = {
        "type": "long",
        "name": TITLE,
        "title": TITLE,
        "content_title": ENTITY_TITLE,
        "topic_anchor": "1961 Jaguar E-Type sports car, Geneva Motor Show, 3.8-litre engine, automotive design history, Le Mans heritage",
        "_topic_anchor": "1961 Jaguar E-Type sports car, Geneva Motor Show, 3.8-litre engine, automotive design history, Le Mans heritage",
        "script": SCRIPT,
        "image_source": "Mixed Media",
        "mixed_media_policy": "automatic",
        "pipeline_v2_mode": "on",
        "visual_understanding": False,
        "visual_required": False,
        "strict_retrieval_accuracy": True,
        "allow_emergency_media": False,
        "allow_relevant_stock": True,
        "allow_stock_last_resort": True,
        "cross_source_fallback": True,
        "youtube_accepted_coverage_target": 0.80,
        "yt_hard_timeout_seconds": 240,
        "yt_per_clip_timeout_seconds": 120,
        "yt_pool_source_download_deadline_seconds": 300,
        "yt_pool_local_cut_deadline_seconds": 180,
        "yt_max_download_attempts_per_scene": 2,
        "scene_mode": "Every 3 Beats",
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
        "visual_verification_frame_count": 3,
        "intro_media_duration_seconds": 15.0,
        "intro_media_min_shots": 6,
        "intro_media_max_shots": 8,
        "intro_max_shot_seconds": 2.5,
        "max_still_hold_seconds": 3.5,
        "max_visual_beat_seconds": 3.5,
        "media_deadline_seconds": 900,
        "visual_deadline_seconds": 900,
        "visual_shared_deadline_seconds": 900,
        "yt_branch_wall_timeout_seconds": 450,
        "auto_export": True,
    }

    started = time.monotonic()
    print("=" * 88)
    print("2-MINUTE JAGUAR E-TYPE PRODUCTION RUN")
    print(f"Title: {TITLE}")
    print(f"Output Directory: {OUTPUT_DIR}")
    print("=" * 88, flush=True)

    result = vidzeo_app._run_video_pipeline(config, progress_callback=progress)
    elapsed = round(time.monotonic() - started, 1)

    if not isinstance(result, dict) or not result.get("success"):
        print(f"\n[ERROR] Production run failed after {elapsed}s: {result}", flush=True)
        return 1

    video_path = result.get("video_path")
    manifest_path = result.get("manifest_path")
    print("\n" + "=" * 88)
    print(f"PRODUCTION SUCCESS after {elapsed}s!")
    print(f"Video Output: {video_path}")
    print(f"Manifest: {manifest_path}")
    print("=" * 88, flush=True)

    report = {
        "success": True,
        "elapsed_seconds": elapsed,
        "video_path": str(video_path or ""),
        "manifest_path": str(manifest_path or ""),
        "result": result,
    }
    report_path = OUTPUT_DIR / "production_run_report.json"
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
