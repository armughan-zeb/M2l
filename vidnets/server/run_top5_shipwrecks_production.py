import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module

TOP5_SCRIPT = """Some shipwrecks are more than rusting hulks, they are chapters of history written in steel and saltwater. From cursed warships to luxury liners lost in minutes, here are the five most famous shipwrecks ever claimed by the deep.

Number five: the Edmund Fitzgerald. On November 10, 1975, this massive American freighter vanished during a brutal Lake Superior storm, taking all twenty nine crew members with her. Gordon Lightfoot's haunting ballad turned her into a legend.

Number four: the RMS Lusitania. In May 1915, a German U-boat torpedoed this British luxury liner off the coast of Ireland. She sank in just eighteen minutes, killing nearly twelve hundred people and helping pull the United States into the First World War.

Number three: the Costa Concordia. In 2012, this Italian cruise ship struck rocks near the island of Giglio and capsized, killing thirty two passengers. The dramatic night rescue and the record breaking salvage became a global spectacle.

Number two: the German battleship Bismarck. Hunted across the Atlantic in 1941, the pride of the German fleet was crippled by Royal Navy torpedoes and scuttled with more than two thousand men aboard. Her wreck still lies upright in the abyss.

Number one: the RMS Titanic. The unsinkable legend struck an iceberg on her maiden voyage in 1912 and went down in less than three hours, claiming more than fifteen hundred lives. Her rediscovery in 1985 turned a tragedy into the most famous shipwreck on Earth.

From the Great Lakes to the floor of the North Atlantic, these wrecks remind us that the ocean always keeps what it takes. Which of these legends would you dare to explore?"""

config = {
    'script': TOP5_SCRIPT,
    'title': 'Top 5 Most Famous Shipwrecks in History',
    'name': 'Top 5 Most Famous Shipwrecks in History',
    'image_source': 'Mixed Media',
    'type': 'long',
    'scene_mode': 'Every 2 Beats',
    'vo_mode': 'Use Audiogram',
    'voice': 'en-US-ChristopherNeural',
    'tts_model': 'audiogram',
    'whisper_mode': 'local',
    'aspect_ratio': '16:9',
    'resolution': '1080p',
    'visual_deadline_seconds': 1800,
    'visual_shared_deadline_seconds': 1800,
    'media_deadline_seconds': 420,
}

def on_progress(pct, msg):
    print(f"  [progress {pct:3d}%] {msg}", flush=True)

if __name__ == '__main__':
    t0 = time.time()
    print("=" * 80)
    print("STARTING TOP 5 SHIPWRECKS FULL VIDEO PRODUCTION (LISTICLE + MIXED MEDIA)")
    print("=" * 80, flush=True)

    os.environ["VIDZEO_VIP_SAFETY_OVERRIDE"] = "1"
    os.environ["VIDZEO_WEB_FAST_MODE"] = "1"
    os.environ.setdefault("VIDZEO_WEB_WAIT_FOR_FINAL_AUDIO_SCENES", "1")
    os.environ["VIDZEO_YT_REJECT_TALKING_HEADS"] = "1"
    os.environ["VIDZEO_YT_BLUR_SMALL_LOGOS"] = "1"
    os.environ["VIDZEO_YT_REUSE_TIMESTAMP_GAP"] = "15"

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
