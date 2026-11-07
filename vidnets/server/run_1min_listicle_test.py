import os
import sys
import time
import json
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module

# Exactly ~1 minute (120-140 words spoken at standard ~130-150 WPM)
LISTICLE_1MIN_SCRIPT = """Here are the top three most mind-blowing space discoveries that changed how we view the universe.

Number three: The Trappist One System. Located forty light years away, this ultra-cool dwarf star hosts seven Earth-sized rocky planets, with three located right in the habitable zone where liquid water could exist.

Number two: The Cosmic Web. Galaxies are not scattered randomly. They are linked across billions of light years along colossal filaments of dark matter and gas, resembling a gigantic neural network.

Number one: The First Black Hole Image. In 2019, the Event Horizon Telescope combined observatories around the globe to capture the glowing shadow of the supermassive black hole at the center of galaxy Messier 87.

Which cosmic mystery do you find most fascinating?"""

config = {
    'script': LISTICLE_1MIN_SCRIPT,
    'title': 'Top 3 Mind Blowing Space Discoveries',
    'name': 'Top 3 Mind Blowing Space Discoveries',
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
    print("STARTING 1-MINUTE LISTICLE VIDEO GENERATION TEST")
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
            print(f"Verification Check: Result returned: {result}")
            
    except Exception as e:
        print(f"\nPipeline failed with exception: {e}")
        import traceback
        traceback.print_exc()
