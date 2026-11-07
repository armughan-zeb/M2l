import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module

GTA_SCRIPT = """For over two decades, the Grand Theft Auto series has revolutionized open-world gaming, pushing the boundaries of interactive entertainment and pop culture. From its humble two-dimensional beginnings in 1997 to the blockbuster cultural phenomenon of GTA 5, Rockstar Games created a living, breathing virtual sandbox where players experienced unprecedented freedom and cinematic storytelling.

The franchise achieved its monumental breakthrough in 2001 with the release of Grand Theft Auto 3 on PlayStation 2. Moving to a fully three-dimensional rendering engine, Liberty City became an immersive metropolis filled with pedestrians, dynamic weather, radio stations, and chaotic emergent gameplay. It redefined what video games could accomplish, inspiring an entire generation of developers.

Building upon this revolutionary foundation, Rockstar unleashed Vice City in 2002, immersing players in a neon-soaked 1980s Miami aesthetic backed by a legendary synth-pop soundtrack. Two years later, Grand Theft Auto San Andreas expanded the scope exponentially, introducing three massive cities across a sprawling fictional state, deep character customization, and an unforgettable story following CJ Johnson.

With Grand Theft Auto 4 in 2008, Rockstar introduced the RAGE engine and Euphoria physics, setting new standards for realistic driving mechanics, artificial intelligence, and gritty narrative depth. Then in 2013, Grand Theft Auto 5 shattered commercial records, generating over one billion dollars in its first three days and selling over one hundred ninety million copies worldwide.

Now, as the gaming world eagerly awaits Grand Theft Auto 6, Vice City returns in stunning ultra-high-definition detail. Featuring dual protagonists Lucia and Jason, cutting-edge graphics, and unprecedented AI density, GTA 6 promises to usher in a new era of open-world gaming excellence."""

config = {
    'script': GTA_SCRIPT,
    'title': 'Grand Theft Auto: The Evolution of Open World Gaming',
    'name': 'Grand Theft Auto: The Evolution of Open World Gaming',
    'image_source': 'YouTube Clips',
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
    print("STARTING YOUTUBE CLIPS FULL PRODUCTION HARNESS (400+ WORDS)")
    print("=" * 80, flush=True)
    
    os.environ["VIDZEO_VIP_SAFETY_OVERRIDE"] = "1"
    os.environ["VIDZEO_YT_REJECT_TALKING_HEADS"] = "1"
    os.environ["VIDZEO_YT_BLUR_SMALL_LOGOS"] = "1"
    os.environ["VIDZEO_YT_MAX_LOGO_AREA"] = "0.30"
    os.environ["VIDZEO_YT_REUSE_TIMESTAMP_GAP"] = "15"
    os.environ["VIDZEO_QWEN_VISION_MODEL"] = "qwen/qwen3.5-flash-02-23"
    os.environ["VIDZEO_QWEN_VISION_DEEP_MODEL"] = "qwen/qwen3.5-flash-02-23"
    os.environ["VIDZEO_YT_V2_DOWNLOAD_ATTEMPTS_PER_SCENE"] = "8"
    os.environ["VIDZEO_YT_VISUAL_WINDOWS_PER_SCENE"] = "4"
    os.environ["VIDZEO_YT_RESOLUTION_DEADLINE_SECONDS"] = "600"
    os.environ["VIDZEO_YT_STOP_ON_SOFT_TIMEOUT"] = "0"
    
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
