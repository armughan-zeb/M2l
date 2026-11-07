import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module

SCENE_TEXTS = [
    "Football has crowned kings on every continent, but a few names rise above the rest, players whose talent changed how the game is played. Here is the countdown of the five greatest footballers of all time.",
    "Number five: Pelé. The Brazilian who won three World Cups and scored more than a thousand career goals made football magical in the 1960s, dazzling crowds from Santos to the Maracana with grace and explosive finishing.",
    "Number four: Zinedine Zidane. The French maestro won the World Cup and the Champions League, orchestrating matches with elegance, vision, and that unforgettable volley in the 2002 final that remains one of the greatest goals ever scored.",
    "Number three: Cristiano Ronaldo. A relentless goal machine from Madeira, he won five Ballon d'Or trophies, conquered England, Spain, and Italy, and rewrote the record books with his athleticism, discipline, and clutch finals performances.",
    "Number two: Lionel Messi. The Argentine magician dribbled past entire defenses, scored over eight hundred career goals, and finally lifted the World Cup in 2022, cementing his place as the most gifted player football has ever seen.",
    "Number one: Diego Maradona. In 1986 he carried Argentina almost single-handedly to World Cup glory, scoring the Hand of God and the Goal of the Century in the same quarterfinal, and became the most complete footballer who ever lived.",
]

SCRIPT = "\n\n".join(SCENE_TEXTS)

config = {
    'script': SCRIPT,
    'title': 'Top 5 Greatest Footballers of All Time',
    'name': 'Top 5 Greatest Footballers of All Time',
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
    print("STARTING TOP 5 FOOTBALLERS VIDEO (YOUTUBE CLIPS POOL-FIRST + EDGE TTS + RENDER 1080p)")
    print("=" * 80, flush=True)

    # Broadcast/fan footage carries burned-in score bugs and subtitles; blur
    # the caption bands instead of hard-rejecting every cut, and give the
    # probe screen a wider wall so more candidates reach the vetos.
    os.environ["VIDZEO_YT_FORMAT"] = (
        "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
        "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
    )
    os.environ["VIDZEO_YT_POOL_DIRECT_CLIPS_ONLY"] = "1"
    os.environ["VIDZEO_YT_ALLOW_PARTIAL_COVERAGE"] = "1"
    os.environ["VIDZEO_YT_POOL_PROBE_CONCURRENCY"] = "3"
    os.environ["VIDZEO_YT_SOURCE_RETRY_SEQUENTIAL"] = "0"
    os.environ["VIDZEO_YT_FRAGMENT_CONCURRENCY"] = "1"
    os.environ["VIDZEO_YT_CAPTION_POLICY"] = "blur"
    os.environ["VIDZEO_YT_POOL_PROBE_WALL_SECONDS"] = "1500"
    os.environ["VIDZEO_YT_POOL_PROBE_WALL_HARD_CAP"] = "1800"
    os.environ["VIDZEO_YT_POOL_PROBE_CANDIDATES_PER_POOL"] = "12"
    os.environ["VIDZEO_YT_POOL_SOURCE_SHORTLIST"] = "8"

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
