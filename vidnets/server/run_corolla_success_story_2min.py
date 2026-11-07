"""Generate a mixed-media documentary video about the Toyota Corolla's success."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
OUTPUT_DIR = ROOT / "output" / "corolla_success_story_2min"
sys.path.insert(0, str(SERVER))

# Keep the job on the real-media path and make YouTube a first-class source.
os.environ.setdefault("VIDZEO_REMOTION_RENDER", "0")
os.environ.setdefault("VIDZEO_SEMANTIC_RENDER_REQUIRED", "1")
os.environ.setdefault("VIDZEO_ALLOW_LEGACY_RENDER_FALLBACK", "0")
os.environ.setdefault("VIDZEO_YT_RELAXED_VISUAL_GATE", "1")
os.environ.setdefault("VIDZEO_YT_FAIL_OPEN_ON_VISUAL_UNAVAILABLE", "1")
os.environ.setdefault("VIDZEO_YT_PREFER_WHISPER_TIMING", "1")
os.environ.setdefault("VIDZEO_YT_PREFER_DISTINCT_SOURCES", "1")
os.environ.setdefault("VIDZEO_YT_NEAREST_YEAR_TOLERANCE", "5")
os.environ.setdefault("VIDZEO_YT_V2_QUERIES_PER_CONTAINER", "10")
os.environ.setdefault("VIDZEO_YT_RESULTS_PER_QUERY", "20")
os.environ.setdefault("VIDZEO_YT_MAX_DOWNLOAD_ATTEMPTS_PER_SCENE", "2")
os.environ.setdefault("VIDZEO_STOCK_FINAL_RESERVE_SECONDS", "120")
os.environ.setdefault("VIDZEO_MIXED_YT_DOWNLOAD_CANDIDATES", "6")
os.environ.setdefault("VIDZEO_MIXED_YT_DOWNLOAD_ATTEMPTS", "6")
os.environ.setdefault("VIDZEO_YT_PROGRESSIVE_ATTEMPTS", "6")
os.environ.setdefault("VIDZEO_WEB_SAME_QUERY_CANDIDATES", "8")
os.environ.setdefault("VIDZEO_WEB_VISION_SHORTLIST", "8")
os.environ.setdefault("VIDZEO_WEB_ENABLE_GDL", "1")
os.environ.setdefault("VIDZEO_WEB_ALLOW_DETERMINISTIC_REUSE", "0")
os.environ.setdefault("VIDZEO_YT_REJECT_TALKING_HEADS", "1")
os.environ.setdefault("VIDZEO_YT_BLUR_SMALL_LOGOS", "1")

TITLE = "How the Toyota Corolla Became So Successful"
# The display title is narrative, but the retrieval title must be the exact
# canonical entity.  Keeping these separate prevents the planner from treating
# words such as "became" or "successful" as part of the searched subject.
ENTITY_TITLE = "Toyota Corolla"
SCRIPT = """The Toyota Corolla did not become one of the world’s best-known cars through a single breakthrough. Its success came from making ordinary driving feel dependable, affordable, and easy to live with.

Toyota introduced the Corolla in the 1960s, when compact cars were becoming increasingly important. It was small enough for crowded cities, economical enough for everyday budgets, and simple enough to maintain. That combination gave it a broad audience from the beginning.

The Corolla’s reputation grew through consistency. Toyota refined manufacturing, quality control, and supply systems so cars could be produced efficiently and repaired with familiar parts. Owners did not need a luxury experience. They needed a car that started, handled daily commutes, and kept working.

The model also adapted to different markets. Over the decades, Corolla versions were built and sold across Asia, Europe, Africa, the Americas, and Australia. Body styles, engines, safety equipment, and interiors changed for local expectations, but the central promise stayed recognizable: practical transportation with predictable running costs.

The timing helped too. During periods of fuel anxiety and economic uncertainty, efficient compact cars became more attractive. When buyers wanted something sportier or more modern, Toyota could evolve the Corolla without abandoning its basic identity.

Its scale created another advantage. A huge owner base meant abundant service knowledge, replacement parts, and resale demand. That made the car easier to own, which reinforced its popularity.

The Corolla became successful because it turned trust into a product strategy. It was not designed to impress everyone for one moment. It was designed to satisfy millions of drivers, year after year. That quiet consistency is what transformed a compact family car into a global automotive institution."""


def progress(event):
    if isinstance(event, dict):
        label = event.get("message") or event.get("stage") or event.get("status")
        if label:
            print(f"[corolla] {label}", flush=True)
    elif event:
        print(f"[corolla] {event}", flush=True)


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    import app as vidzeo_app

    config = {
        "type": "long",
        "name": TITLE,
        "title": ENTITY_TITLE,
        "content_title": ENTITY_TITLE,
        "topic_anchor": "Toyota Corolla automobile, 1966 launch, global success, reliability, affordability, manufacturing, and market adaptation",
        "_topic_anchor": "Toyota Corolla automobile, 1966 launch, global success, reliability, affordability, manufacturing, and market adaptation",
        "script": SCRIPT,
        "image_source": "Mixed Media",
        "mixed_media_policy": "automatic",
        "pipeline_v2_mode": "on",
        "visual_understanding": True,
        "visual_required": True,
        "strict_retrieval_accuracy": True,
        "allow_emergency_media": False,
        "allow_relevant_stock": True,
        "allow_stock_last_resort": True,
        "cross_source_fallback": True,
        "youtube_accepted_coverage_target": 0.60,
        "yt_hard_timeout_seconds": 180,
        "yt_per_clip_timeout_seconds": 120,
        "yt_pool_source_download_deadline_seconds": 240,
        "yt_pool_local_cut_deadline_seconds": 120,
        "yt_max_download_attempts_per_scene": 2,
        "scene_mode": "Every 5 Beats",
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
        "visual_max_calls": 96,
        "visual_shared_max_calls": 96,
        "visual_call_max_attempts": 1,
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

    print(f"[corolla] Starting: {TITLE}", flush=True)
    result = vidzeo_app._run_video_pipeline(config, progress_callback=progress)
    if not isinstance(result, dict):
        print(f"[corolla] Pipeline returned: {result!r}", flush=True)
        return 1

    report = {
        "title": TITLE,
        "output_dir": str(OUTPUT_DIR),
        "result": result,
        "environment": {
            key: os.environ.get(key)
            for key in (
                "VIDZEO_YT_V2_QUERIES_PER_CONTAINER",
                "VIDZEO_YT_RESULTS_PER_QUERY",
                "VIDZEO_YT_PREFER_DISTINCT_SOURCES",
                "VIDZEO_YT_PREFER_WHISPER_TIMING",
            )
        },
    }
    report_path = OUTPUT_DIR / "corolla_success_story_run_report.json"
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"[corolla] Run report: {report_path}", flush=True)
    print(f"[corolla] Result: {json.dumps(result, default=str)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
