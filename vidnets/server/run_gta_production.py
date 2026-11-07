import sys, os, time, traceback
from pathlib import Path

# Apply production tuning BEFORE importing the app so the YouTube pipeline
# picks it up (the standalone runner does not auto-load .env).
os.environ.setdefault('VIDZEO_YT_MIN_SOURCE_DURATION', '25')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module  # noqa: E402

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
    'script': SCRIPT,
    'title': 'GTA VI - The Most Anticipated Game',
    'name': 'GTA VI - The Most Anticipated Game',
    'topic': 'Grand Theft Auto VI',
    'topic_anchor': 'Grand Theft Auto VI',
    'category': 'gaming',
    'image_source': 'YouTube Clips',
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


def on_progress(pct, msg):
    print(f"  [progress {pct:5.1f}%] {msg}", flush=True)


if __name__ == '__main__':
    t0 = time.time()
    print("=" * 80)
    print("STARTING GTA VI PRODUCTION (pure YouTube Clips + 1080p + Edge TTS)")
    print("=" * 80, flush=True)

    os.environ["VIDZEO_VIP_SAFETY_OVERRIDE"] = "1"
    os.environ["VIDZEO_WEB_FAST_MODE"] = "1"
    os.environ["VIDZEO_ALLOW_EMERGENCY_MEDIA"] = "1"
    os.environ["VIDZEO_YT_ALLOW_PARTIAL_COVERAGE"] = "1"
    os.environ.setdefault("VIDZEO_WEB_WAIT_FOR_FINAL_AUDIO_SCENES", "1")
    os.environ["VIDZEO_SEMANTIC_GRAPHIC_COVERAGE"] = "1.0"
    os.environ["VIDZEO_RESTRAINED_TEMPLATES"] = "1"

    try:
        result = app_module._run_video_pipeline(config, progress_callback=on_progress)
        print("\n" + "=" * 80)
        print("PIPELINE RESULT COMPLETED SUCCESSFULLY!")
        print("=" * 80)
        print(repr(result))

        raw_video_path = result.get("video_path") or result.get("output_path") or ""
        if raw_video_path and os.path.isfile(raw_video_path):
            prod_dir = Path("output/productions/gta_vi_production")
            prod_dir.mkdir(parents=True, exist_ok=True)
            final_mp4 = prod_dir / "GTA VI - The Most Anticipated Game.mp4"
            import shutil
            shutil.copy2(raw_video_path, final_mp4)
            print(f"\nFINAL VIDEO OUTPUT: {final_mp4.resolve()}")

            # Save run report
            report_path = prod_dir / "production_run_report.json"
            import json
            report_path.write_text(json.dumps({
                "success": True,
                "elapsed_seconds": round(time.time() - t0, 1),
                "video_path": str(final_mp4.resolve()),
                "manifest_path": result.get("manifest_path"),
                "result": result,
            }, indent=2, default=str), encoding="utf-8")
            print(f"Run report saved to: {report_path.resolve()}")
    except Exception as e:
        print("\n" + "=" * 80)
        print("PIPELINE FAILED WITH ERROR")
        print("=" * 80)
        traceback.print_exc()
        print("ERROR:", repr(e))
    finally:
        print(f"\nTotal Elapsed Time: {time.time() - t0:.1f}s", flush=True)
