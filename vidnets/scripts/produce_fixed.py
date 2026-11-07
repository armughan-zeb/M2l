"""Fixed production: YouTube clips with proper format + semantic render enabled."""
import json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

os.environ.setdefault("VIDZEO_REMOTION_RENDER", "0")
os.environ.setdefault("VIDZEO_SEMANTIC_RENDER_REQUIRED", "1")
os.environ.setdefault("VIDZEO_QWEN_VISION_MODEL", "qwen/qwen3.5-flash-02-23")
os.environ.setdefault("VIDZEO_QWEN_VISION_FALLBACK_MODEL", "qwen/qwen3.5-flash-02-23")
os.environ.setdefault("VIDZEO_LLM_MODEL", "qwen/qwen-2.5-7b-instruct")
os.environ.setdefault("VIDZEO_EDITING_PROFILE_LLM", "1")

# Keep the diagnostic runner on the same adaptive, quality-first selector as
# the packaged app.  The old 720p cap made this helper silently produce a
# visibly softer source than the normal pipeline.
os.environ.setdefault(
    "VIDZEO_YT_FORMAT",
    "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
    "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
)

SCRIPT = """On the night of March 18, 1990, two men dressed as Boston police officers walked into the Isabella Stewart Gardner Museum. They overpowered the guards and for 81 minutes, cut thirteen masterpieces from their frames.

The stolen works included Rembrandt's only seascape and Vermeer's The Concert. The total value was 500 million dollars, making it the largest property theft in history.

For 35 years, the FBI chased leads across the globe. Informants pointed to Boston's criminal underworld, but every trail went cold. The statute of limitations expired long ago.

Today, the empty frames still hang in the museum. A 10 million dollar reward remains unclaimed. The Gardner heist remains the greatest unsolved art theft in history."""


def main():
    import app as vidzeo_app

    words = len(SCRIPT.split())
    print(f"[fixed] words={words}", flush=True)

    config = {
        "type": "long",
        "name": "gardner_fixed",
        "title": "The Gardner Heist: Greatest Art Theft",
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

    print(f"[fixed] Starting at {time.strftime('%H:%M:%S')}", flush=True)
    started = time.monotonic()

    result = vidzeo_app._run_video_pipeline(config, progress_callback=progress)

    elapsed = time.monotonic() - started
    print(f"\n[fixed] Complete in {elapsed:.0f}s", flush=True)

    video_path = result.get("video_path") or result.get("output_path") or ""
    print(f"[fixed] Video: {video_path}", flush=True)
    if video_path and os.path.exists(video_path):
        print(f"[fixed] Size: {os.path.getsize(video_path)/1024/1024:.1f} MB", flush=True)

    # Check manifest for template mapping
    import glob
    manifests = sorted(
        glob.glob(str(ROOT / "temp" / "manifests" / "*semantic_manifest*")),
        key=os.path.getmtime, reverse=True,
    )
    if manifests:
        with open(manifests[0]) as f:
            data = json.load(f)
        m = data.get("semantic_edit_manifest", data.get("manifest", data))
        scenes = m.get("scenes", [])
        counts = {}
        for s in scenes:
            tid = s.get("graphic", {}).get("template_id", "full-bleed")
            counts[tid] = counts.get(tid, 0) + 1
        overlays = sum(v for k, v in counts.items() if k != "full-bleed")
        print(f"\n[fixed] Templates: {overlays}/{len(scenes)} scenes have overlays ({overlays*100/max(1,len(scenes)):.0f}%)")


if __name__ == "__main__":
    main()
