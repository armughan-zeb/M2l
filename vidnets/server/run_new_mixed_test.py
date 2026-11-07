import os
import sys
import time
import json
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module

# Engaging topic: Secrets of Deep Ocean Trenches
NEW_MIXED_SCRIPT = """Deep beneath the ocean surface lies the Hadal Zone, Earth's final and most mysterious frontier.

Number three: The Mariana Trench. Plunging nearly eleven kilometers down into the Pacific Ocean, its crushing pressure is over one thousand times greater than at sea level.

Number two: Giant Hydrothermal Vents. Spewing mineral-rich water at boiling temperatures, these underwater chimneys sustain bizarre ecosystems that thrive completely without sunlight.

Number one: The Snailfish of the Deep. Thriving eight thousand meters underwater, this translucent creature has evolved flexible bones and special cellular enzymes to withstand unimaginable pressure.

What deep sea secret surprises you the most?"""

config = {
    'script': NEW_MIXED_SCRIPT,
    'title': 'Secrets of the Deepest Ocean Trenches',
    'name': 'Secrets of the Deepest Ocean Trenches',
    'image_source': 'Mixed Media',
    'type': 'long',
    'scene_mode': 'Every 2 Beats',
    'vo_mode': 'Use Audiogram',
    'voice': 'en-US-ChristopherNeural',
    'tts_model': 'audiogram',
    'whisper_mode': 'local',
    'aspect_ratio': '16:9',
    'resolution': '1080p',
    'render_resolution': (1920, 1080),
}

def on_progress(pct, msg):
    print(f"  [progress {pct:3d}%] {msg}", flush=True)

if __name__ == '__main__':
    t0 = time.time()
    print("=" * 80)
    print("STARTING NEW MIXED MEDIA GENERATION RUN: Secrets of the Deepest Ocean Trenches")
    print("=" * 80, flush=True)

    try:
        result = app_module._run_video_pipeline(config, progress_callback=on_progress)
        elapsed = time.time() - t0
        print("\n" + "=" * 80)
        print(f"TEST RUN COMPLETED IN {elapsed:.1f}s")
        print("=" * 80)
        print(f"Status: {result.get('status') if isinstance(result, dict) else 'OK'}")
        
        video_path = result.get('video_path') or result.get('output_path') if isinstance(result, dict) else None
        print(f"Video Output: {video_path}")
        
        if video_path and os.path.exists(video_path):
            size_mb = os.path.getsize(video_path) / (1024 * 1024)
            print(f"Video File Size: {size_mb:.2f} MB")
            print(f"Verification: SUCCESS - Output video generated at {video_path}")
        else:
            print("Verification: FAILED - Video output file not found")
    except Exception as exc:
        print(f"Generation failed with error: {exc}")
        import traceback
        traceback.print_exc()
