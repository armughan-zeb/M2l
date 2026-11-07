import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module

SCENE_TEXTS = [
    "On April 10, 1912, the RMS Titanic, the largest ocean liner ever built, embarked on its maiden voyage from Southampton to New York, hailed as unsinkable with over two thousand passengers and crew.",
    "Constructed in Belfast by Harland and Wolff, Titanic measured 882 feet and featured opulent saloons, a grand staircase, and watertight compartments, commanded by Captain Edward Smith.",
    "On the moonless night of April 14, lookouts spotted a massive iceberg directly ahead; despite the order to turn hard-a-starboard, Titanic struck the iceberg along its starboard side.",
    "Water flooded the forward compartments, and chief designer Thomas Andrews calculated the vessel would sink within two hours; the crew began launching lifeboats under women and children first.",
    "At 2:20 AM on April 15, Titanic broke in two and sank with over 1,500 lives lost, while the liner Carpathia rushed through ice fields to rescue 705 survivors at dawn.",
    "The wreck of Titanic was discovered in 1985 resting two and a half miles deep, where it remains a protected memorial to the greatest maritime disaster in history.",
]

SCRIPT = "\n\n".join(SCENE_TEXTS)

config = {
    'script': SCRIPT,
    'title': 'The Sinking of RMS Titanic - YouTube Clips Accurate Final',
    'name': 'The Sinking of RMS Titanic - YouTube Clips Accurate Final',
    'image_source': 'YouTube Clips',
    'type': 'long',
    'vo_mode': 'Use Audiogram',
    'voice': 'en-US-ChristopherNeural',
    'tts_model': 'audiogram',
    'whisper_mode': 'local',
    'aspect_ratio': '16:9',
    'resolution': '1080p',
    'yt_pool_first': True,
    'yt_skip_llm_query_planner': False,
    'visual_understanding': True,
    'visual_required': False,
    'openrouter_api_key': os.environ.get('OPENROUTER_API_KEY') or '',
    'yt_lenient_acceptance': False,
    # Branch wall is derived in app.py from scene count (110s/scene, capped);
    # an explicit operator value here would pin or defeat that scaling.
    'yt_branch_wall_timeout_seconds': None,
    'fail_on_unresolved_media': False,
    'yt_min_source_duration': 20,
    'yt_max_download_attempts_per_scene': 4,
}

PHASES = {}


def on_progress(pct, msg):
    now = time.time()
    key = str(msg)[:60]
    if key not in PHASES:
        PHASES[key] = now
        print(f"  [progress {pct:5.1f}%] {msg}", flush=True)
    else:
        print(f"  [progress {pct:5.1f}%] {msg} (cont.)", flush=True)


if __name__ == '__main__':
    t0 = time.time()
    print("=" * 80)
    print("STARTING TITANIC 1-MIN VIDEO (YOUTUBE CLIPS PIPELINE + EDGE TTS + RENDER 1080p)")
    print("=" * 80, flush=True)

    try:
        result = app_module._run_video_pipeline(config, progress_callback=on_progress)
        print("\n" + "=" * 80)
        print("PIPELINE RESULT COMPLETED SUCCESSFULLY!")
        print("=" * 80)
        print(repr(result))
    except Exception as e:
        print("\n" + "=" * 80)
        print("PIPELINE FAILED WITH ERROR:")
        print("=" * 80)
        traceback.print_exc()
        print("ERROR:", repr(e))
    finally:
        total = time.time() - t0
        print(f"\nTotal Pipeline Elapsed Time: {total:.1f}s ({total/60:.1f} min)", flush=True)
        print("\n--- Phase timings (unique progress stages) ---")
        ordered = sorted(PHASES.items(), key=lambda kv: kv[1])
        prev = t0
        for label, stamp in ordered:
            print(f"  {stamp - t0:8.1f}s  (+{stamp - prev:6.1f}s)  {label}", flush=True)
            prev = stamp