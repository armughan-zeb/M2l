"""Produce a 1-minute crime documentary through the Remotion render pipeline."""
import json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

os.environ.setdefault("VIDZEO_REMOTION_RENDER", "0")
os.environ.setdefault("VIDZEO_SEMANTIC_RENDER_REQUIRED", "1")
os.environ.setdefault("VIDZEO_ALLOW_LEGACY_RENDER_FALLBACK", "0")

os.environ.setdefault("VIDZEO_QWEN_VISION_MODEL", "qwen/qwen3.5-flash-02-23")
os.environ.setdefault("VIDZEO_QWEN_VISION_FALLBACK_MODEL", "qwen/qwen3.5-flash-02-23")
os.environ.setdefault("VIDZEO_LLM_MODEL", "qwen/qwen-2.5-7b-instruct")
os.environ.setdefault("VIDZEO_QUERY_LLM_MODEL", "qwen/qwen-2.5-7b-instruct")

SCRIPT = """On a cold morning in February 1990, Boston police received an anonymous tip about a warehouse at the edge of the harbor. What they found inside would launch the largest art heist investigation in American history.

Thirteen paintings had been stolen from the Isabella Stewart Gardner Museum, including works by Rembrandt and Vermeer. The thieves had posed as police officers, convincing the night guards to let them inside. Within eighty-one minutes, they had cut the masterpieces from their frames, leaving behind nothing but empty walls and a trail of unanswered questions. The total value was staggering: five hundred million dollars, making it the single largest property theft ever recorded.

For three decades, the FBI chased leads across four continents. They traced the stolen art through Boston's criminal underworld, following whispers of mob connections and secret vaults. Informants pointed to a notorious crime family, but every lead went cold. The statute of limitations expired long ago, yet the empty frames still hang in the Gardner Museum, waiting for the return of the masterpieces that may never come home.

The case remains unsolved. The paintings are still missing. And somewhere out there, in a vault or a basement or behind a false wall, thirteen stolen masterpieces wait to be found."""


def main():
    import app as vidzeo_app
    words = len(SCRIPT.split())
    print(f"[crime-doc] words={words} (target ~60s)", flush=True)

    config = {
        "type": "long",
        "name": "gardner_heist_crime_doc",
        "title": "The Gardner Museum Heist: America's Biggest Art Theft",
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
        if fraction >= 1.0: print()

    print(f"[crime-doc] Starting at {time.strftime('%H:%M:%S')}", flush=True)
    started = time.monotonic()
    result = vidzeo_app._run_video_pipeline(config, progress_callback=progress)
    elapsed = time.monotonic() - started
    print(f"\n[crime-doc] Complete in {elapsed:.0f}s", flush=True)

    video_path = result.get("video_path") or result.get("output_path") or ""
    print(f"[crime-doc] Video: {video_path}", flush=True)
    if video_path and os.path.exists(video_path):
        print(f"[crime-doc] Size: {os.path.getsize(video_path)/1024/1024:.1f} MB", flush=True)

    # Show which profile was used
    profile = result.get("editing_profile") or {}
    palette = profile.get("palette", {})
    fonts = profile.get("typography", {})
    print(f"[crime-doc] Profile: niche={profile.get('niche')}, mood={profile.get('mood')}, pace={profile.get('pace')}")
    print(f"[crime-doc] Palette: bg={palette.get('background')} accent={palette.get('accent')} primary={palette.get('primary')}")
    print(f"[crime-doc] Fonts: display={fonts.get('display')} body={fonts.get('body')}")
    print(f"[crime-doc] Archetype: {profile.get('editorial_archetype')}")


if __name__ == "__main__":
    main()
