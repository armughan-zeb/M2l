import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module

SCENE_TEXTS = [
    "Cristiano Ronaldo, born on the island of Madeira, began his journey to world football greatness with unmatched determination.",
    "At Manchester United under Sir Alex Ferguson, Cristiano Ronaldo won his first Ballon d'Or trophy and the UEFA Champions League title.",
    "Moving to Real Madrid, Cristiano Ronaldo scored 450 goals and led the club to four Champions League titles in an unforgettable era.",
    "Famous for his extraordinary athleticism, powerful headers, and signature free kicks, Ronaldo dominated the world's biggest matches.",
    "Captaining Portugal, Cristiano Ronaldo lifted the UEFA European Championship trophy in 2016 in a historic triumph.",
    "With over 850 career goals, Cristiano Ronaldo's discipline and relentless hunger cement his legacy as one of the greatest of all time."
]

SCRIPT = "\n\n".join(SCENE_TEXTS)

config = {
    'script': SCRIPT,
    'title': 'Cristiano Ronaldo - The Relentless Legend',
    'name': 'Cristiano Ronaldo - The Relentless Legend',
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
    print("STARTING 1-MIN CRISTIANO RONALDO PRODUCTION HARNESS (FULL FEATURES + 1080p)")
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