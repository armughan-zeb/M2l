"""Grand Theft Auto VI 1-minute full video production test (YouTube-only).

Tests the complete end-to-end pipeline:
1. Script & TTS (Edge TTS neural voice)
2. Local Whisper word alignment & caption timing
3. YouTube clip search, ranking, stream download, and precise moment cutting
4. Bounded YouTube retries and verification of moving footage for each scene
5. 1080p documentary rendering and final MP4 delivery
"""

import os
import sys
import time
import json
import traceback
from pathlib import Path

# Ensure vidnets/server is in python path
SERVER_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SERVER_DIR))

import app as app_module

# 1-minute script: 8 tightly-focused documentary scenes (~145 words, ~55-60s speech)
SCENE_TEXTS = [
    "Grand Theft Auto VI is the most anticipated video game of the decade, following more than ten years of waiting since GTA Five.",
    "Set in the sun-soaked state of Leonida, the story takes players back to the neon-lit streets, crowded beaches, and swamps of Vice City.",
    "Trailers highlight a world far larger and more dynamic than any previous Rockstar title, featuring unprecedented visual detail.",
    "At the heart of the campaign are Lucia and Jason, a criminal duo tackling high-stakes robberies across modern Florida.",
    "The first official trailer shattered world records with over ninety million views in just twenty-four hours.",
    "Rockstar Games has promised a cinematic revolution, with living ecosystems, advanced crowds, and realistic wildlife throughout Leonida.",
    "The game is scheduled to launch on PlayStation 5 and Xbox Series X, pushing current-generation consoles to their absolute limits.",
    "Grand Theft Auto VI represents the next defining era of interactive entertainment, redefining what open-world gaming can achieve."
]

SCRIPT = "\n\n".join(SCENE_TEXTS)

PHASES = {}

def on_progress(pct, msg):
    now = time.time()
    key = str(msg)[:60]
    if key not in PHASES:
        PHASES[key] = now
        print(f"  [progress {pct:5.1f}%] {msg}", flush=True)
    else:
        print(f"  [progress {pct:5.1f}%] {msg} (cont.)", flush=True)

def main():
    t0 = time.time()
    word_count = len(SCRIPT.split())
    print("=" * 80)
    print(f"GTA VI 1-MINUTE VIDEO PRODUCTION TEST (YOUTUBE CLIPS ONLY)")
    print(f"Scenes: {len(SCENE_TEXTS)} | Words: {word_count} | Mode: YouTube Clips Exclusive")
    print("=" * 80, flush=True)

    # YouTube-only environment tuning
    os.environ["VIDZEO_VIP_SAFETY_OVERRIDE"] = "1"
    os.environ["VIDZEO_YT_REJECT_TALKING_HEADS"] = "1"
    os.environ["VIDZEO_YT_LENIENT_ACCEPTANCE"] = "1"
    os.environ["VIDZEO_YT_MIN_SOURCE_DURATION"] = "10"
    os.environ["VIDZEO_YT_POOL_FIRST"] = "0"
    os.environ["VIDZEO_YT_ALLOW_WEB_FALLBACK"] = "0"
    os.environ["VIDZEO_WEB_SOURCES_ENABLED"] = "0"
    os.environ["VIDZEO_STOCK_ENABLED"] = "0"
    os.environ["VIDZEO_ALLOW_LEGACY_RENDER_FALLBACK"] = "1"
    os.environ["VIDZEO_YT_V2_RESULTS_PER_QUERY"] = "15"
    os.environ["VIDZEO_YT_SOURCE_DOWNLOAD_CONCURRENCY"] = "3"
    os.environ["VIDZEO_VIDEO_DELIVERY_BUDGET_SECONDS"] = "1800"
    os.environ["VIDZEO_MEDIA_DEADLINE_SECONDS"] = "1200"
    os.environ["VIDZEO_MEDIA_COORDINATOR_DEADLINE_SECONDS"] = "1200"
    os.environ["VIDZEO_YT_BRANCH_WALL_TIMEOUT_SECONDS"] = "1200"
    os.environ["VIDZEO_MIXED_YT_BRANCH_WALL_TIMEOUT_SECONDS"] = "1200"
    os.environ["VIDZEO_YT_V2_OUTER_WALL_MAX_SECONDS"] = "1200"
    os.environ["VIDZEO_YT_SOURCE_ALLOW_UNBOUNDED_FORMAT"] = "1"
    os.environ["VIDZEO_YT_OVERLAY_SCREEN"] = "1"

    config = {
        "script": SCRIPT,
        "title": "Grand Theft Auto VI - The Next Generation of Open World",
        "name": "gta_vi_1min_youtube_production",
        "image_source": "YouTube Clips",
        "type": "long",
        "scene_mode": "Every Beat",
        "vo_mode": "Use Audiogram",
        "tts_model": "audiogram",
        "voice": "en-US-ChristopherNeural",
        "whisper_mode": "local",
        "aspect_ratio": "16:9",
        "resolution": "1080p",
        "captions_enabled": True,
        "caption_mode": "word",
        "caption_style": "documentary",
        "caption_font": "DM Sans",
        "yt_pool_first": False,
        "yt_lenient_acceptance": True,
        "llm_provider": "external_agent",
        "visual_understanding": True,
        "visual_required": True,
        "fail_on_unresolved_media": True,
        "allow_web_fallback_after_youtube_exhausted": False,
        "web_sources_enabled": False,
        "allow_stock_last_resort": False,
        "allow_grounded_graphic_fallback": False,
        "allow_emergency_media": False,
        "global_media_guarantee": False,
        "media_fallback_policy": "real_media_only",
        "yt_overlay_screen": True,
        "yt_use_authenticated_session": True,
        "yt_download_candidate_limit": 6,
        "yt_max_download_attempts_per_scene": 6,
        "yt_min_source_duration": 10,
        "delivery_budget_seconds": 1800,
        "_render_reserve_seconds": 120,
        "media_deadline_seconds": 1200,
        "yt_branch_wall_timeout_seconds": 1200,
        "allow_legacy_render_fallback": True,
    }

    log_file = SERVER_DIR.parent / "temp" / f"gta_vi_1min_run_{int(t0)}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    print(f"Log will be saved to: {log_file}")

    result = None
    try:
        result = app_module._run_video_pipeline(config, progress_callback=on_progress)
        print("\n" + "=" * 80)
        print("PIPELINE EXECUTION COMPLETED")
        print("=" * 80, flush=True)

        if isinstance(result, dict):
            final_mp4 = result.get("video_path") or result.get("output_path")
            print(f"Result Status: {'SUCCESS' if not result.get('error') else 'WARNING/ERROR'}")
            print(f"Final .mp4: {final_mp4}")
            if final_mp4 and os.path.exists(final_mp4):
                file_size_mb = os.path.getsize(final_mp4) / (1024 * 1024)
                print(f"Final MP4 Size: {file_size_mb:.2f} MB")
            print(f"Audio Path: {result.get('audio_path')}")
            print(f"Scene Count: {result.get('scene_count')}")
            media_paths = result.get("media_paths") or []
            print(f"Media Resolved Count: {len(media_paths)}")
            for idx, p in enumerate(media_paths):
                print(f"  Scene {idx+1}: {p}")
            if result.get("error"):
                print(f"Reported Notice/Error: {result.get('error')}")
        else:
            print(f"Result: {result!r}")

    except Exception as exc:
        print("\n" + "=" * 80)
        print("PIPELINE EXCEPTION OCCURRED:")
        print("=" * 80)
        traceback.print_exc()
        print(f"Error: {exc!r}")

    finally:
        total_time = time.time() - t0
        print(f"\nTotal Pipeline Elapsed: {total_time:.1f}s ({total_time/60:.2f} min)", flush=True)
        print("\n--- Phase breakdown ---")
        ordered = sorted(PHASES.items(), key=lambda kv: kv[1])
        prev = t0
        for label, stamp in ordered:
            print(f"  +{stamp - prev:6.1f}s (at {stamp - t0:6.1f}s) : {label}", flush=True)
            prev = stamp

if __name__ == "__main__":
    main()
