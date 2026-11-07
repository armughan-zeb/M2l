"""YouTube-ONLY full video production test.

Runs the whole pipeline (TTS -> whisper -> YouTube clip retrieval -> visual
gate -> final render) with ``image_source='YouTube Clips'`` so every visual is
drawn exclusively from YouTube footage. Web/stock fallbacks stay disabled.
"""

import os
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module

SCRIPT = """On April 10, 1912, the RMS Titanic, the largest ocean liner ever built, embarked on its maiden voyage from Southampton to New York. Hailed as unsinkable, the vessel carried over two thousand passengers and crew.

Constructed in Belfast by Harland and Wolff, Titanic measured 882 feet and featured opulent saloons, a grand staircase, and watertight compartments. Captain Edward Smith commanded the ship across the North Atlantic.

On the moonless night of April 14, lookouts spotted a massive iceberg directly ahead. Despite the order to turn hard-a-starboard, Titanic struck the iceberg along its starboard side, opening six compartments to the freezing ocean.

Water flooded the forward compartments, and chief designer Thomas Andrews calculated the ship would sink within two hours. The crew launched lifeboats under women and children first.

At 2:20 AM on April 15, Titanic broke in two and sank, with over 1,500 lives lost. The liner Carpathia rushed through ice fields and rescued 705 survivors at dawn."""


def on_progress(pct, msg):
    print(f"  [progress {pct:5.1f}%] {msg}", flush=True)


def main():
    t0 = time.time()
    words = len(SCRIPT.split())
    print("=" * 80)
    print(f"YOUTUBE-ONLY FULL PRODUCTION TEST ({words} words)")
    print("=" * 80, flush=True)

    # Deterministic YouTube-only tuning.
    os.environ["VIDZEO_VIP_SAFETY_OVERRIDE"] = "1"
    os.environ["VIDZEO_YT_REJECT_TALKING_HEADS"] = "1"
    os.environ["VIDZEO_YT_REJECT_TALKING_HEAD"] = "1"
    os.environ["VIDZEO_YT_LENIENT_ACCEPTANCE"] = "1"
    os.environ["VIDZEO_YT_MIN_SOURCE_DURATION"] = "20"
    os.environ["VIDZEO_YT_POOL_FIRST"] = "1"
    # Keep enough reserve sources for the frame/motion gate to find real
    # moving footage after slideshow, AI-restored, and captioned packages are
    # rejected. The resolver still downloads in parallel and caps the pool.
    os.environ["VIDZEO_YT_POOL_SOURCE_SHORTLIST"] = "10"
    os.environ["VIDZEO_YT_SOURCE_RECORDS_PER_POOL"] = "12"
    os.environ["VIDZEO_YT_POOL_SOURCE_DOWNLOAD_DEADLINE_SECONDS"] = "100"
    os.environ["VIDZEO_YT_SOURCE_RETRY_SEQUENTIAL"] = "1"
    os.environ["VIDZEO_YT_MOMENT_SHORTLIST"] = "8"
    os.environ["VIDZEO_YT_POOL_TIMESTAMP_GAP"] = "10"
    os.environ["VIDZEO_YT_TRANSCRIPT_CONCURRENCY"] = "12"
    os.environ["VIDZEO_YT_TRANSCRIPT_DEADLINE_SECONDS"] = "30"
    os.environ["VIDZEO_YT_V2_QUERIES_PER_CONTAINER"] = "7"
    os.environ["VIDZEO_YT_RESULTS_PER_QUERY"] = "16"
    os.environ["VIDZEO_YT_SOURCE_DOWNLOAD_CONCURRENCY"] = "4"
    # Keep the source quality-first.  Size/time limits are enforced by the
    # downloader and transport budgets; a 360p selector here used to create
    # unusable test artifacts that were mistaken for production output.
    os.environ["VIDZEO_YT_SOURCE_FORMAT"] = (
        "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
        "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
    )
    os.environ["VIDZEO_YANDEX_SOURCES_ENABLED"] = "0"
    os.environ["VIDZEO_WEB_SOURCES_ENABLED"] = "0"
    os.environ["VIDZEO_STOCK_ENABLED"] = "0"
    os.environ["VIDZEO_REMOTION_RENDER"] = "0"
    os.environ["VIDZEO_SEMANTIC_RENDER_REQUIRED"] = "1"
    os.environ["VIDZEO_YT_ALLOW_WEB_FALLBACK"] = "0"
    # Keep the search budget scaled (the production fix).
    os.environ.pop("VIDZEO_YT_V2_SEARCH_TIMEOUT", None)

    config = {
        "script": SCRIPT,
        "title": "The Sinking of RMS Titanic - YouTube Clips Accurate Final",
        "name": "titanic_yt_only_accurate_final",
        "image_source": "YouTube Clips",
        "type": "long",
        "scene_mode": "Every 2 Beats",
        "vo_mode": "Use Audiogram",
        "tts_model": "audiogram",
        "voice": "en-US-ChristopherNeural",
        "whisper_mode": "local",
        "captions_enabled": True,
        "caption_mode": "phrase",
        "words_per_group": 4,
        "quality": "standard",
        "resolution": "1080p",
        "visual_understanding": False,
        "fail_on_unresolved_media": False,
        "allow_web_fallback_after_youtube_exhausted": False,
        "yt_lenient_acceptance": True,
        "yt_pool_first": True,
        "yt_pool_source_download_deadline_seconds": 150,
        "yt_download_candidate_limit": 4,
        "yt_max_download_attempts_per_scene": 4,
        "yt_stop_on_soft_timeout": False,
        "yt_min_resolved_ratio_before_soft_stop": 0.90,
        "yt_min_source_duration": 20,
    }

    try:
        result = app_module._run_video_pipeline(config, progress_callback=on_progress)
        print("\n" + "=" * 80)
        print("YOUTUBE-ONLY PIPELINE COMPLETED")
        print("=" * 80, flush=True)
        if isinstance(result, dict):
            for key in ("video_path", "output_path", "audio_path", "media_paths", "scene_count", "error"):
                if key in result:
                    value = result[key]
                    if isinstance(value, list):
                        value = [p for p in value if p]
                        print(f"  {key}: {len(value)}")
                    else:
                        print(f"  {key}: {value}")
            final = result.get("video_path") or result.get("output_path")
            if final and os.path.exists(final):
                print(f"  FINAL VIDEO EXISTS: {final} ({os.path.getsize(final) / 1024 / 1024:.1f} MB)")
        else:
            print(f"  result: {result!r}")
    except Exception as exc:
        print("\n" + "=" * 80)
        print("PIPELINE FAILED")
        print("=" * 80)
        traceback.print_exc()
        print("ERROR:", repr(exc))
    finally:
        print(f"\nTotal Elapsed Time: {time.time() - t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
