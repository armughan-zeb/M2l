import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module

TITANIC_SCRIPT = """On April 10, 1912, the RMS Titanic, the largest and most luxurious ocean liner ever built, embarked on its maiden voyage from Southampton, England, bound for New York City. Hailed as an unsinkable marvel of modern industrial engineering, the massive vessel carried over 2,200 passengers and crew members, including wealthy aristocrats, prominent businessmen, and hundreds of hopeful immigrants seeking new lives in America.

Constructed in Belfast by the Harland and Wolff shipyard for the White Star Line, Titanic measured 882 feet in length and featured state-of-the-art watertight bulkheads, opulent dining saloons, a grand staircase, and a heated swimming pool. Captain Edward John Smith commanded the magnificent ship across the chilly waters of the North Atlantic.

However, four days into the voyage, on the quiet, moonless night of April 14, 1912, at 11:40 PM, lookouts Frederick Fleet and Reginald Lee spotted a massive iceberg looming directly in the ship's path. Despite First Officer William Murdoch's desperate order to turn hard-a-starboard and reverse engines, Titanic struck the iceberg along its starboard side, buckling hull plates below the waterline and opening six compartments to the freezing ocean.

As water flooded the forward compartments, chief naval architect Thomas Andrews inspected the damage and calculated that the ship had less than two hours before sinking completely into the Atlantic. Distress signals, including the newly adopted SOS, were transmitted via Marconi wireless telegraphy to nearby ships, while distress rockets were launched into the night sky.

The ship carried only 20 lifeboats, enough for barely half of the people onboard. Under the rule of women and children first, officers loaded lifeboats into the freezing sea. The ocean liner Carpathia, commanded by Captain Arthur Rostron, heard the distress calls and altered course at full speed through dangerous ice fields to reach the scene.

At 2:20 AM on April 15, 1912, Titanic broke in two and sank to the ocean floor, resulting in the tragic loss of over 1,500 lives. Carpathia arrived at dawn, rescuing 705 shivering survivors from the lifeboats. The disaster stunned the global public, sparking sweeping international maritime safety regulations, mandating 24-hour radio watches and lifeboats for all passengers. Decades later, in 1985, Dr. Robert Ballard discovered the shipwreck resting 12,500 feet below the surface, preserving its tragic legacy forever."""

config = {
    'script': TITANIC_SCRIPT,
    'title': 'The Sinking of RMS Titanic - Unsinkable Legend',
    'name': 'The Sinking of RMS Titanic - Unsinkable Legend',
    'image_source': 'Web Images',
    'type': 'long',
    'scene_mode': 'Every 2 Beats',
    'vo_mode': 'Use Audiogram',
    'voice': 'en-US-ChristopherNeural',
    'tts_model': 'audiogram',
    'whisper_mode': 'local',
    'aspect_ratio': '16:9',
    'resolution': '1080p',
}

def on_progress(pct, msg):
    print(f"  [progress {pct:3d}%] {msg}", flush=True)

if __name__ == '__main__':
    t0 = time.time()
    print("=" * 80)
    print("STARTING TITANIC FULL VIDEO PRODUCTION (WEB IMAGES PIPELINE + EDGE TTS)")
    print("=" * 80, flush=True)
    
    # Enable Qwen Vision safety override and fast mode environment tuning
    os.environ["VIDZEO_VIP_SAFETY_OVERRIDE"] = "1"
    os.environ["VIDZEO_WEB_FAST_MODE"] = "1"
    
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
        print(f"\nTotal Pipeline Elapsed Time: {time.time() - t0:.1f}s", flush=True)
