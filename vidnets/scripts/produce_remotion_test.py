"""Produce a 30-second documentary through the Remotion render pipeline."""
import json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

os.environ["VIDZEO_REMOTION_RENDER"] = "1"
os.environ["VIDZEO_SEMANTIC_RENDER_REQUIRED"] = "1"
os.environ["VIDZEO_ALLOW_LEGACY_RENDER_FALLBACK"] = "0"

os.environ.setdefault("VIDZEO_QWEN_VISION_MODEL", "qwen/qwen3.5-flash-02-23")
os.environ.setdefault("VIDZEO_QWEN_VISION_FALLBACK_MODEL", "qwen/qwen3.5-flash-02-23")
os.environ.setdefault("VIDZEO_LLM_MODEL", "qwen/qwen-2.5-7b-instruct")
os.environ.setdefault("VIDZEO_QUERY_LLM_MODEL", "qwen/qwen-2.5-7b-instruct")

SCRIPT = """In July 1969, Apollo 11 carried three astronauts toward the Moon. Neil Armstrong and Buzz Aldrin descended in the lunar module Eagle while Michael Collins orbited above.

On July 20, with seconds of fuel remaining, Armstrong landed safely. He became the first human to step onto another world, declaring one small step for a man, one giant leap for mankind.

The mission cost twenty five billion dollars in today's money. It proved that humanity could reach beyond Earth."""

def main():
    import app as vidzeo_app
    # .env overrides with override=True — re-apply our settings AFTER import
    os.environ["VIDZEO_REMOTION_RENDER"] = "1"
    os.environ["VIDZEO_REMOTION_RENDER_QUALITY"] = "cinematic"
    os.environ["VIDZEO_REMOTION_RENDER_CONCURRENCY"] = "3"
    words = len(SCRIPT.split())
    print(f"[remotion-test] words={words} (target ~30s)", flush=True)

    config = {
        "type": "long", "name": "apollo_remotion_test",
        "title": "Apollo 11: Remotion Test",
        "script": SCRIPT, "image_source": "Web Images",
        "scene_mode": "Every 3 Beats", "vo_mode": "Use Audiogram",
        "tts_model": "audiogram", "voice": "Adam (Male US)",
        "whisper_mode": "local", "captions_enabled": True,
        "caption_mode": "phrase", "words_per_group": 4,
        "quality": "standard", "resolution": "1080p",
        "visual_understanding": False,
        "strict_mixed_percentages": False,
        "strict_retrieval_accuracy": True,
        "fail_on_unresolved_media": False,
        "auto_export": True,
        "semantic_edit_enable": True, "semantic_render_enabled": True,
        "semantic_theme": "history_archive",
    }

    def progress(fraction, msg):
        pct = int(fraction * 100)
        bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
        print(f"\r  [{bar}] {pct:3d}% {msg[:60]:60s}", end="", flush=True)
        if fraction >= 1.0: print()

    print(f"[remotion-test] Starting at {time.strftime('%H:%M:%S')}", flush=True)
    started = time.monotonic()
    result = vidzeo_app._run_video_pipeline(config, progress_callback=progress)
    elapsed = time.monotonic() - started
    print(f"\n[remotion-test] Complete in {elapsed:.0f}s", flush=True)
    
    video_path = result.get("video_path") or result.get("output_path") or ""
    print(f"[remotion-test] Video: {video_path}", flush=True)
    if video_path and os.path.exists(video_path):
        print(f"[remotion-test] Size: {os.path.getsize(video_path)/1024/1024:.1f} MB", flush=True)

if __name__ == "__main__":
    main()
