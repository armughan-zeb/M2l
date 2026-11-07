"""1-Minute Production Video Generator for Grand Theft Auto VI (Mixed Media + YouTube Cookies + Full Editing Path).

Monitors execution logs, records stage-by-stage latency, and outputs complete production metrics.
"""
import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

# Ensure root and server are in path
ROOT_DIR = Path(__file__).resolve().parents[1]
SERVER_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SERVER_DIR))
sys.path.insert(0, str(ROOT_DIR))

# Ensure cookies are explicitly configured
COOKIES_FILE = ROOT_DIR / "test_cookies.txt"
if COOKIES_FILE.is_file():
    os.environ["VIDZEO_YT_COOKIES_FILE"] = str(COOKIES_FILE.resolve())
    print(f"[setup] Using cookies file: {COOKIES_FILE.resolve()} ({COOKIES_FILE.stat().st_size} bytes)")
else:
    print(f"[setup] WARNING: Cookies file not found at {COOKIES_FILE.resolve()}")

# Production tuning and overrides
os.environ.setdefault("VIDZEO_YT_MIN_SOURCE_DURATION", "20")
os.environ["VIDZEO_VIP_SAFETY_OVERRIDE"] = "1"
os.environ["VIDZEO_WEB_FAST_MODE"] = "1"
os.environ.setdefault("VIDZEO_WEB_WAIT_FOR_FINAL_AUDIO_SCENES", "1")
os.environ["VIDZEO_YT_REJECT_TALKING_HEADS"] = "1"
os.environ["VIDZEO_YT_BLUR_SMALL_LOGOS"] = "1"
os.environ["VIDZEO_ALLOW_EMERGENCY_MEDIA"] = "1"
os.environ["VIDZEO_MIXED_YT_BRANCH_WALL_TIMEOUT_SECONDS"] = "480"
os.environ["VIDZEO_SEMANTIC_GRAPHIC_COVERAGE"] = "1.0"
os.environ["VIDZEO_RESTRAINED_TEMPLATES"] = "0"  # Enable full editing templates

import app as app_module  # noqa: E402
from services.youtube_session import ensure_verified_session, _auth_cookie_count

# Verify cookies are loaded in youtube session broker
session_jar, gen = ensure_verified_session()
auth_count = _auth_cookie_count(session_jar) if session_jar else 0
print(f"[setup] YouTube Session Status: verified={session_jar is not None}, auth_cookies={auth_count}, gen={gen}")

SCENE_TEXTS = [
    "For more than a decade, the gaming world has waited for one definitive blockbuster: Grand Theft Auto VI.",
    "Rockstar Games shattered global records within twenty-four hours of releasing the first official GTA VI trailer.",
    "Returning players to the neon-drenched streets of Vice City, the game delivers the largest and densest open world in gaming history.",
    "Inspired by Bonnie and Clyde, protagonists Jason and Lucia lead a high-stakes crime saga across sun-soaked Florida.",
    "Next-generation physics bring bustling beaches, wild Florida swamplands, and hyper-realistic vehicle handling to life.",
    "From high-speed highway getaways to dynamic weather storms, the living sandbox reacts to every criminal move.",
    "With an unprecedented production budget, industry analysts forecast GTA VI to become the biggest entertainment launch ever.",
    "Setting a revolutionary new benchmark for gaming, Grand Theft Auto VI is ready to redefine open-world entertainment."
]

SCRIPT = "\n\n".join(SCENE_TEXTS)

config = {
    "script": SCRIPT,
    "title": "Grand Theft Auto VI - The Next Generation",
    "name": "GTA VI 1-Min Mixed Media Showcase",
    "topic": "Grand Theft Auto VI",
    "topic_anchor": "Grand Theft Auto VI",
    "category": "gaming",
    "image_source": "Mixed Media",
    "mixed_media_weights": {"Web Images": 0.50, "YouTube Clips": 0.50},
    "type": "long",
    "scene_mode": "Every 2 Beats",
    "vo_mode": "Use Audiogram",
    "voice": "en-US-AndrewNeural",
    "tts_model": "audiogram",
    "whisper_mode": "local",
    "aspect_ratio": "16:9",
    "resolution": "1080p",
    "mixed_yt_wall_cap_seconds": 480,
    "allow_emergency_media": True,
    "yt_lenient_acceptance": True,
    "visual_understanding": True,
    "enable_sound_library": True,
    "enable_editorial_cues": True,
    "enable_motion_templates": True,
}

stage_timings = []
last_time = time.time()
last_pct = 0.0

def on_progress(pct, msg):
    global last_time, last_pct
    now = time.time()
    elapsed_since_last = now - last_time
    total_elapsed = now - t0
    stage_timings.append({
        "progress": pct,
        "delta_seconds": round(elapsed_since_last, 2),
        "total_seconds": round(total_elapsed, 2),
        "message": msg,
    })
    print(f"  [{total_elapsed:6.1f}s | +{elapsed_since_last:5.1f}s] [progress {pct:5.1f}%] {msg}", flush=True)
    last_time = now
    last_pct = pct

if __name__ == "__main__":
    t0 = time.time()
    last_time = t0
    print("=" * 80)
    print("STARTING 1-MINUTE GTA VI MIXED MEDIA PRODUCTION PIPELINE")
    print(f"Scenes: {len(SCENE_TEXTS)} | Mode: Mixed Media (50% Web / 50% YouTube)")
    print(f"Resolution: 1080p | Voice: en-US-AndrewNeural | Editing: Full Semantic + Templates")
    print("=" * 80, flush=True)

    try:
        result = app_module._run_video_pipeline(config, progress_callback=on_progress)
        print("\n" + "=" * 80)
        print("PIPELINE COMPLETED SUCCESSFULLY!")
        print("=" * 80)

        raw_video_path = result.get("video_path") or result.get("output_path") or ""
        prod_dir = ROOT_DIR / "output" / "productions" / "gta_vi_1min_showcase"
        prod_dir.mkdir(parents=True, exist_ok=True)

        final_mp4 = None
        if raw_video_path and os.path.isfile(raw_video_path):
            final_mp4 = prod_dir / "GTA_VI_1Min_Showcase.mp4"
            shutil.copy2(raw_video_path, final_mp4)
            print(f"\nFINAL VIDEO EXPORTED TO: {final_mp4.resolve()}")
            print(f"File Size: {final_mp4.stat().st_size / (1024 * 1024):.2f} MB")

            # Probe video with ffprobe
            try:
                import subprocess
                probe_cmd = [
                    "ffprobe", "-v", "error", "-show_entries",
                    "format=duration,size,bit_rate:stream=codec_name,width,height,r_frame_rate",
                    "-of", "json", str(final_mp4)
                ]
                proc = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=15)
                if proc.returncode == 0:
                    probe_data = json.loads(proc.stdout)
                    fmt = probe_data.get("format", {})
                    streams = probe_data.get("streams", [])
                    print(f"Duration: {float(fmt.get('duration', 0)):.1f}s")
                    print(f"Streams: {[s.get('codec_name') for s in streams]}")
                    if streams:
                        print(f"Resolution: {streams[0].get('width')}x{streams[0].get('height')} @ {streams[0].get('r_frame_rate')} fps")
            except Exception as probe_err:
                print(f"Note: Probe info unavailable: {probe_err}")

        # Save comprehensive performance report
        report_path = prod_dir / "production_metrics.json"
        total_pipeline_time = time.time() - t0
        report = {
            "success": True,
            "total_elapsed_seconds": round(total_pipeline_time, 2),
            "video_path": str(final_mp4.resolve()) if final_mp4 else "",
            "manifest_path": result.get("manifest_path"),
            "stage_timings": stage_timings,
            "result_summary": {
                "scene_count": len(SCENE_TEXTS),
                "media_paths": result.get("media_paths") or [],
                "source_labels": result.get("source_labels") or [],
            },
        }
        report_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"\nPerformance metrics saved to: {report_path.resolve()}")
        print(f"Total Pipeline Runtime: {total_pipeline_time:.1f}s")

    except Exception as exc:
        print("\n" + "=" * 80)
        print("PIPELINE ENCOUNTERED AN ERROR:")
        print("=" * 80)
        traceback.print_exc()
        print("ERROR:", repr(exc))
    finally:
        total_time = time.time() - t0
        print(f"\n[Done] Total Elapsed Time: {total_time:.1f}s", flush=True)
