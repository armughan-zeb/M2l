"""Full 1-minute crime documentary — YouTube clips + native Vox engine."""
import json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

os.environ.setdefault("VIDZEO_REMOTION_RENDER", "0")
os.environ.setdefault("VIDZEO_SEMANTIC_RENDER_REQUIRED", "1")
os.environ.setdefault("VIDZEO_QWEN_VISION_MODEL", "qwen/qwen3.5-flash-02-23")
os.environ.setdefault("VIDZEO_QWEN_VISION_FALLBACK_MODEL", "qwen/qwen3.5-flash-02-23")
os.environ.setdefault("VIDZEO_LLM_MODEL", "qwen/qwen-2.5-7b-instruct")
os.environ.setdefault("VIDZEO_QUERY_LLM_MODEL", "qwen/qwen-2.5-7b-instruct")
os.environ.setdefault("VIDZEO_EDITING_PROFILE_LLM", "1")

SCRIPT = """On the night of March 18, 1990, two men dressed as Boston police officers walked into the Isabella Stewart Gardner Museum. They told the night guards they were responding to a disturbance call. Once inside, they overpowered the guards and handcuffed them in the basement. For the next 81 minutes, they moved through the museum's galleries, cutting thirteen priceless masterpieces from their frames.

The stolen works included Rembrandt's only known seascape, Vermeer's The Concert, and paintings by Degas and Manet. The total value was staggering: over 500 million dollars, making it the single largest property theft in world history.

The FBI launched an investigation that spanned the globe. They traced leads through Boston's criminal underworld, following whispers of mob connections and secret vaults. But every lead went cold. The statute of limitations expired in 1995, meaning no one could ever be prosecuted for the theft.

Today, the empty frames still hang in the Gardner Museum, a silent memorial to what was lost. A 10 million dollar reward remains unclaimed. The paintings are out there somewhere — possibly in a vault, a basement, or behind a false wall. The Gardner heist remains the greatest unsolved art theft in history."""


def main():
    import app as vidzeo_app

    words = len(SCRIPT.split())
    print(f"[yt-doc] words={words} (target ~60s)", flush=True)

    config = {
        "type": "long",
        "name": "gardner_yt_doc",
        "title": "The Gardner Heist: Greatest Art Theft in History",
        "script": SCRIPT,
        "image_source": "Web Images",
        "scene_mode": "Every 2 Beats",
        "vo_mode": "Use Audiogram",
        "tts_model": "audiogram",
        "voice": "Adam (Male US)",
        "whisper_mode": "local",
        "captions_enabled": True,
        "caption_mode": "phrase",
        "words_per_group": 4,
        "quality": "standard",
        "resolution": "1080p",
        "visual_understanding": False,
        "strict_mixed_percentages": False,
        "strict_retrieval_accuracy": True,
        "fail_on_unresolved_media": False,
        "auto_export": True,
        "semantic_edit_enable": True,
        "semantic_render_enabled": True,
    }

    def progress(fraction, msg):
        pct = int(fraction * 100)
        bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
        print(f"\r  [{bar}] {pct:3d}% {msg[:60]:60s}", end="", flush=True)
        if fraction >= 1.0:
            print()

    print(f"[yt-doc] Starting pipeline at {time.strftime('%H:%M:%S')}", flush=True)
    started = time.monotonic()

    result = vidzeo_app._run_video_pipeline(config, progress_callback=progress)

    elapsed = time.monotonic() - started
    print(f"\n[yt-doc] Pipeline complete in {elapsed:.0f}s", flush=True)

    video_path = result.get("video_path") or result.get("output_path") or ""
    print(f"[yt-doc] Video: {video_path}", flush=True)
    if video_path and os.path.exists(video_path):
        print(f"[yt-doc] Size: {os.path.getsize(video_path)/1024/1024:.1f} MB", flush=True)


if __name__ == "__main__":
    main()
