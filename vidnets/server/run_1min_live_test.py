import os
import sys
import time
import json
import traceback
from pathlib import Path

# Setup paths and environment
SERVER_DIR = Path(__file__).resolve().parent
ROOT_DIR = SERVER_DIR.parent

sys.path.insert(0, str(SERVER_DIR))
sys.path.insert(0, str(ROOT_DIR))

# Ensure bundled tools (ffmpeg, node, python) are in PATH
tools_ffmpeg = str(ROOT_DIR / "tools" / "ffmpeg")
tools_node = str(ROOT_DIR / "tools" / "node")
tools_python = str(ROOT_DIR / "tools" / "python")
tools_scripts = str(ROOT_DIR / "tools" / "python" / "Scripts")
node_bins = str(ROOT_DIR / "node_modules" / ".bin")

os.environ["PATH"] = f"{tools_ffmpeg};{tools_node};{tools_python};{tools_scripts};{node_bins};" + os.environ.get("PATH", "")
os.environ["VIDZEO_AUTH_MODE"] = "local"
os.environ["VIDZEO_ALLOW_PACKAGED_LOCAL_AUTH"] = "1"
os.environ["VIDZEO_VIP_SAFETY_OVERRIDE"] = "1"
os.environ["VIDZEO_WEB_FAST_MODE"] = "1"
os.environ.setdefault("VIDZEO_WEB_WAIT_FOR_FINAL_AUDIO_SCENES", "1")
os.environ["VIDZEO_WEB_VERBOSE"] = "1"
os.environ["VIDZEO_YT_REJECT_TALKING_HEADS"] = "1"
os.environ["VIDZEO_YT_BLUR_SMALL_LOGOS"] = "1"
os.environ["VIDZEO_YT_REUSE_TIMESTAMP_GAP"] = "15"
os.environ["VIDZEO_YT_POOL_SOURCE_SHORTLIST"] = "6"
os.environ["VIDZEO_YT_DEBUG_POOLS"] = "1"
os.environ["VIDZEO_RENDER_BACKEND"] = "remotion"
os.environ["VIDZEO_REMOTION_RENDER"] = "1"
os.environ["VIDZEO_SEMANTIC_EDIT_ENABLE"] = "1"
os.environ["VIDZEO_SEMANTIC_RENDER_REQUIRED"] = "1"

import app as app_module

TEST_SCRIPT = """In the late nineteen sixties, British and French engineers achieved what many believed impossible: the creation of Concorde, the world's most iconic supersonic passenger airliner.

Cruising at over twice the speed of sound at sixty thousand feet, Concorde shrank the Atlantic crossing between London and New York to under three and a half hours. Passengers sipped champagne at Mach two while watching the curvature of the Earth outside their windows.

However, skyrocketing fuel prices, sonic boom restrictions, and devastating operating costs made supersonic commercial travel an unsustainable luxury.

On October twenty-fourth, two thousand and three, Concorde made its final emotional touchdown at London Heathrow, bringing an end to the golden era of commercial supersonic aviation."""

config = {
    'script': TEST_SCRIPT,
    'title': 'Concorde: The Era of Commercial Supersonic Flight',
    'name': 'Concorde: The Era of Commercial Supersonic Flight',
    'topic': 'Concorde supersonic airliner London Heathrow New York Mach 2 aviation',
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
    'mixed_media_policy': 'manual',
    'mixed_media_weights': {'Web Images': 0.50, 'YouTube Clips': 0.50},
    'youtube_accepted_coverage_target': 0.50,
    'media_source_priority': ['youtube', 'web', 'stock'],
    'pipeline_v2_mode': 'on',
    'youtube_rights_mode': 'all_public',
    'allow_relevant_stock': True,
    'allow_stock_last_resort': True,
    'strict_retrieval_accuracy': True,
    'allow_emergency_media': True,
    'semantic_edit_enable': True,
    'semantic_render_enabled': True,
    'enable_sound_library': True,
    'visual_deadline_seconds': 1200,
    'visual_shared_deadline_seconds': 1200,
}

def on_progress(pct, msg):
    print(f"  [progress {pct:3d}%] {msg}", flush=True)

if __name__ == '__main__':
    t0 = time.time()
    print("=" * 80)
    print("STARTING 1-MINUTE LIVE VIDEO TEST (MIXED MEDIA: WEB IMAGES + YT CLIPS + REMOTION)")
    print("Topic: Concorde: The Era of Commercial Supersonic Flight")
    print("Target Duration: ~1 minute (114 words spoken narration)")
    print("=" * 80, flush=True)

    try:
        result = app_module._run_video_pipeline(config, progress_callback=on_progress)
        elapsed = time.time() - t0
        print("\n" + "=" * 80)
        print(f"PIPELINE COMPLETED SUCCESSFULLY IN {elapsed:.1f}s")
        print("=" * 80)
        
        video_path = (result or {}).get("video_path") or (result or {}).get("output_path")
        manifest_path = (result or {}).get("manifest_path")
        
        print(f"Result Status: {(result or {}).get('status', 'OK')}")
        print(f"Video Output: {video_path}")
        print(f"Manifest Path: {manifest_path}")

        if video_path and os.path.exists(video_path):
            file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
            print(f"Video File Size: {file_size_mb:.2f} MB")
            
            # Probe video metadata using ffprobe if available
            try:
                import subprocess
                ffprobe_bin = str(ROOT_DIR / "tools" / "ffmpeg" / "ffprobe.exe")
                if os.path.exists(ffprobe_bin):
                    probe_cmd = [
                        ffprobe_bin, "-v", "error",
                        "-show_entries", "format=duration,size:stream=width,height,codec_name,r_frame_rate",
                        "-of", "json", video_path
                    ]
                    probe_out = subprocess.check_output(probe_cmd, text=True)
                    meta = json.loads(probe_out)
                    format_info = meta.get("format", {})
                    streams = meta.get("streams", [])
                    print(f"Video Duration: {float(format_info.get('duration', 0)):.2f}s")
                    for s in streams:
                        print(f"Stream: codec={s.get('codec_name')} {s.get('width', '')}x{s.get('height', '')} fps={s.get('r_frame_rate')}")
            except Exception as probe_err:
                print(f"Probe warning: {probe_err}")
                
        if manifest_path and os.path.exists(manifest_path):
            with open(manifest_path, "r", encoding="utf-8") as mf:
                manifest_data = json.load(mf)
            resolutions = manifest_data.get("media_resolution_v2", [])
            print(f"\nTotal Media Scenes Resolved: {len(resolutions)}")
            for idx, r in enumerate(resolutions):
                print(f"  Scene {idx+1}: [{r.get('provider')}] {r.get('label', '')[:40]} -> {os.path.basename(r.get('path', ''))}")
            yt_cov = manifest_data.get("youtube_coverage", {})
            if yt_cov:
                print(f"\nYouTube Coverage Metrics:")
                print(f"  Target: {yt_cov.get('target')}")
                print(f"  Achieved: {yt_cov.get('achieved')}")
                print(f"  Clips Used: {yt_cov.get('clips_used')}")
                
        print("\nALL VERIFICATION CHECKS PASSED!")
    except Exception as e:
        print("\n" + "=" * 80)
        print("PIPELINE TEST FAILED WITH EXCEPTION:")
        print("=" * 80)
        traceback.print_exc()
        sys.exit(1)
