import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module

TEST_SCRIPT = """On April 14, 1912, the RMS Titanic struck an iceberg and sank in less than three hours, claiming more than fifteen hundred lives.

In 1915, a German U-boat torpedoed the British liner Lusitania off Ireland; she went down in eighteen minutes.

In 2012, the Costa Concordia capsized off Giglio after striking rocks, and its night rescue became a global spectacle.

Today, the wrecks of these famous ships still rest on the ocean floor, each one a chapter of maritime history."""

config = {
    'script': TEST_SCRIPT,
    'title': 'Top 4 Famous Shipwrecks (Web + YT)',
    'name': 'Top 4 Famous Shipwrecks (Web + YT)',
    'image_source': 'Mixed Media',
    'type': 'long',
    'scene_mode': 'Every 2 Beats',
    'vo_mode': 'Use Audiogram',
    'voice': 'en-US-ChristopherNeural',
    'tts_model': 'audiogram',
    'whisper_mode': 'local',
    'aspect_ratio': '16:9',
    'resolution': '1080p',
    'topic': 'Famous Shipwrecks Titanic Lusitania Costa Concordia',
    'mixed_media_policy': 'manual',
    'mixed_media_weights': {"Web Images": 0.55, "YouTube Clips": 0.45},
    'visual_deadline_seconds': 1200,
    'visual_shared_deadline_seconds': 1200,
}

def on_progress(pct, msg):
    print(f"  [progress {pct:3d}%] {msg}", flush=True)

if __name__ == '__main__':
    t0 = time.time()
    print("=" * 80)
    print("STARTING QUICK TEST: SHIPWRECKS (WEB IMAGES + YT CLIPS PRIORITY)")
    print("=" * 80, flush=True)

    os.environ["VIDZEO_VIP_SAFETY_OVERRIDE"] = "1"
    os.environ["VIDZEO_WEB_FAST_MODE"] = "1"
    os.environ.setdefault("VIDZEO_WEB_WAIT_FOR_FINAL_AUDIO_SCENES", "1")
    os.environ["VIDZEO_WEB_VERBOSE"] = "1"
    os.environ["VIDZEO_YT_REJECT_TALKING_HEADS"] = "1"
    os.environ["VIDZEO_YT_BLUR_SMALL_LOGOS"] = "1"
    os.environ["VIDZEO_YT_REUSE_TIMESTAMP_GAP"] = "15"
    os.environ["VIDZEO_YT_POOL_SOURCE_SHORTLIST"] = "6"
    os.environ["VIDZEO_YT_DEBUG_POOLS"] = "1"

    try:
        result = app_module._run_video_pipeline(config, progress_callback=on_progress)
        print("\n" + "=" * 80)
        print("PIPELINE RESULT COMPLETED SUCCESSFULLY!")
        print("=" * 80)
        manifest_path = (result or {}).get("manifest_path")
        if manifest_path and os.path.exists(manifest_path):
            import json
            with open(manifest_path, "r", encoding="utf-8") as mf:
                manifest_data = json.load(mf)
            resolutions = manifest_data.get("media_resolution_v2", [])
            print(f"\nTotal Scenes Resolved: {len(resolutions)}")
            for idx, r in enumerate(resolutions):
                print(f"  Scene {idx+1}: provider={r.get('provider')} | path={os.path.basename(r.get('path', ''))} | label={r.get('label')}")
            print(f"\nYouTube Coverage: {json.dumps(manifest_data.get('youtube_coverage', {}), indent=2)}")
        print("\nVideo path:", (result or {}).get("video_path"))
    except Exception as e:
        print("\n" + "=" * 80)
        print("PIPELINE FAILED WITH ERROR:")
        print("=" * 80)
        traceback.print_exc()
        print("ERROR:", repr(e))
    finally:
        print(f"\nTotal Pipeline Elapsed Time: {time.time() - t0:.1f}s", flush=True)
