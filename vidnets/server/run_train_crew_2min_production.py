import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module

SCENE_TEXTS = [
    "Before sunrise at the railroad depot, the freight train crew arrives to begin their twelve-hour shift.",
    "Reviewing dispatch track warrants and weather bulletins, the conductor and engineer plan every mile of the journey.",
    "The locomotive engineer climbs into the cab of the leading diesel-electric engine, testing the air brakes and electronics.",
    "Walking the length of the train, the conductor inspects railcars, knuckle couplers, and pneumatic brake lines.",
    "With clearance granted from rail traffic control, the engineer opens the throttle as twin diesel engines roar to life.",
    "Operating a heavy freight train demands constant vigilance, reading trackside signal aspects and monitoring wayside detectors.",
    "Inside the locomotive cab, the crew works in tight coordination, calling out signals and managing train momentum.",
    "Descending steep mountain passes requires precise dynamic braking to control thousands of trailing tons of freight.",
    "At busy railway junctions, switch crews and yardmasters guide the train through crossovers onto the mainline.",
    "Passing opposing trains on double-track territory, the crew exchanges headlight salutes across the iron rails.",
    "As night falls, powerful locomotive ditch lights illuminate continuous welded rail stretching into the distance.",
    "Pulling safely into the destination terminal, the crew secures the locomotives, keeping the supply chain moving."
]

SCRIPT = "\n\n".join(SCENE_TEXTS)

config = {
    'script': SCRIPT,
    'title': 'Train Crew: Life on the Rails',
    'name': 'Train Crew - Life on the Rails',
    'topic': 'Train Crew',
    'topic_anchor': 'Train Crew',
    'category': 'general',
    'image_source': 'Mixed Media',
    'type': 'long',
    'scene_mode': 'Every 2 Beats',
    'vo_mode': 'Use Audiogram',
    'voice': 'en-US-AndrewNeural',
    'tts_model': 'audiogram',
    'whisper_mode': 'local',
    'aspect_ratio': '16:9',
    'resolution': '1080p',
    'allow_emergency_media': True,
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
    print("STARTING 2-MIN TRAIN CREW PRODUCTION HARNESS (50% AT MOST YT FOOTAGE + 1080p)")
    print("=" * 80, flush=True)

    os.environ["VIDZEO_VIP_SAFETY_OVERRIDE"] = "1"
    os.environ["VIDZEO_WEB_FAST_MODE"] = "1"
    os.environ["VIDZEO_ALLOW_EMERGENCY_MEDIA"] = "1"
    os.environ.setdefault("VIDZEO_WEB_WAIT_FOR_FINAL_AUDIO_SCENES", "1")

    try:
        result = app_module._run_video_pipeline(config, progress_callback=on_progress)
        print("\n" + "=" * 80)
        print("PIPELINE RESULT COMPLETED SUCCESSFULLY!")
        print("=" * 80)
        print(repr(result))
        elapsed = time.time() - t0
        print(f"\nTotal Elapsed Time: {elapsed:.1f}s")
        if isinstance(result, dict) and result.get("output_path"):
            print(f"FINAL VIDEO OUTPUT: {result['output_path']}")
    except Exception as e:
        print("\n" + "=" * 80)
        print("PIPELINE FAILED WITH ERROR")
        print("=" * 80)
        traceback.print_exc()