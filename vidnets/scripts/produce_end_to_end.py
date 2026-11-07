"""End-to-end documentary: web images + Vox composer.
1. Runs production pipeline to get real images + TTS
2. Extracts manifest with scene data
3. Assigns Vox templates based on narration content
4. Renders through Vox composer
5. Measures performance
"""
import json, os, sys, time, glob, shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

SCRIPT = """On the night of March 18, 1990, two men dressed as Boston police officers walked into the Isabella Stewart Gardner Museum. They overpowered the guards and for 81 minutes, they cut thirteen masterpieces from their frames.

The stolen works included Rembrandt's only seascape and Vermeer's The Concert. The total value was 500 million dollars, making it the largest property theft in history.

For 35 years, the FBI chased leads across the globe. Informants pointed to Boston's criminal underworld, but every trail went cold. The statute of limitations expired long ago.

Today, the empty frames still hang in the museum. A 10 million dollar reward remains unclaimed. The Gardner heist remains the greatest unsolved art theft in history."""

def main():
    print("=" * 50)
    print("PHASE 1: Production Pipeline (Web Images + TTS)")
    print("=" * 50)

    started = time.monotonic()

    import app as vidzeo_app

    config = {
        "type": "long", "name": "gardner_vox_prod",
        "title": "The Gardner Heist: Greatest Art Theft",
        "script": SCRIPT, "image_source": "Web Images",
        "scene_mode": "Every 2 Beats", "vo_mode": "Use Audiogram",
        "tts_model": "audiogram", "voice": "Adam (Male US)",
        "whisper_mode": "local", "captions_enabled": True,
        "caption_mode": "phrase", "words_per_group": 4,
        "quality": "standard", "resolution": "1080p",
        "visual_understanding": False,
        "strict_mixed_percentages": False,
        "strict_retrieval_accuracy": True,
        "fail_on_unresolved_media": False,
        "auto_export": False,
        "semantic_edit_enable": True, "semantic_render_enabled": False,
    }

    def progress(fraction, msg):
        pct = int(fraction * 100)
        bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
        print(f"\r  [{bar}] {pct:3d}% {msg[:60]:60s}", end="", flush=True)
        if fraction >= 1.0: print()

    print(f"Script: {len(SCRIPT.split())} words")
    result = vidzeo_app._run_video_pipeline(config, progress_callback=progress)

    phase1_elapsed = time.monotonic() - started
    print(f"\nPhase 1 complete in {phase1_elapsed:.0f}s")

    # Find the manifest with scene data and images
    manifests = sorted(
        glob.glob(str(ROOT / "temp" / "manifests" / "*semantic_manifest*")),
        key=os.path.getmtime, reverse=True,
    )

    if not manifests:
        print("No manifest found!")
        return

    with open(manifests[0]) as f:
        data = json.load(f)
    m = data.get("semantic_edit_manifest", data.get("manifest", data))

    # Extract scenes with images
    raw_scenes = []
    for s in m.get("scenes", []):
        media = s.get("media", {})
        img = media.get("src", media.get("path", ""))
        dur = float(s.get("duration", 3.0))
        nar = s.get("narration", "")
        raw_scenes.append({"image": img, "duration": dur, "narration": nar})

    # Keep only scenes where image exists
    valid = [(i, s) for i, s in enumerate(raw_scenes) if s["image"] and Path(s["image"]).exists()]
    print(f"  {len(valid)}/{len(raw_scenes)} scenes have valid images")

    if not valid:
        print("No valid images — generating placeholders")
        from PIL import Image as PILImage
        tmp = str(ROOT / "temp")
        for i, s in enumerate(raw_scenes):
            fp = os.path.join(tmp, f"bg_{i:03d}.png")
            tints = [(8,10,15),(12,14,20),(10,8,14),(15,12,18),(6,8,12)]
            PILImage.new("RGB", (1920, 1080), tints[i % 5]).save(fp)
            s["image"] = fp

    # Template assignment based on narration keywords
    tpl = [
        "chapter_title", "keyword_emphasis", "stat_counter", "money_counter",
        "keyword_emphasis", "stat_counter", "quote_card", "stat_counter",
        "keyword_emphasis", "end_card",
    ]
    props_base = [
        {"kicker":"BOSTON - 1990", "title":"THE GARDNER HEIST"},
        {"text":"THIRTEEN MASTERPIECES", "detail":"Stolen in 81 minutes by thieves in police uniforms"},
        {"value":"500", "label":"MILLION DOLLAR THEFT"},
        {"value":"500", "unit":"M", "label":"LARGEST PROPERTY THEFT IN HISTORY"},
        {"text":"THE EMPTY FRAMES", "detail":"Still hanging in the museum as a silent memorial"},
        {"value":"35", "label":"YEARS WITHOUT ANSWERS"},
        {"quote":"Whoever has those paintings cannot sell them. But they also cannot let them go.", "attribution":"Anthony Amore, Security Director"},
        {"value":"10", "label":"MILLION DOLLAR REWARD"},
        {"text":"THE CASE REMAINS OPEN", "detail":"A $10M reward still stands for any information"},
        {"title":"THE CASE REMAINS OPEN", "subtitle":"CALL THE FBI TIPLINE"},
    ]

    palette = {"primary": "#f4f0e6", "accent": "#d62e3c", "danger": "#ef6f6c", "background": "#0a0a0f", "surface": "#16263b"}

    # ==========================================================
    print("\n" + "=" * 50)
    print("PHASE 2: Vox Composer Render")
    print("=" * 50)

    from services.native_engine.vox_composer import VoxComposer

    vox_scenes = []
    for i in range(min(len(valid), len(tpl))):
        idx = valid[i][0] if i < len(valid) else i
        s = raw_scenes[idx]
        vox_scenes.append({
            "duration": s["duration"],
            "background_asset": s["image"],
            "image_treatment": "static_hold",
            "template_id": tpl[i],
            "props": props_base[i],
            "palette": palette,
            "transition": {"id": "dissolve", "duration": 0.4},
        })

    total_dur = sum(s["duration"] for s in vox_scenes)
    print(f"{len(vox_scenes)} scenes, {total_dur:.0f}s")
    print(f"Rendering with Vox templates...")

    phase2_start = time.monotonic()
    composer = VoxComposer(1920, 1080, 30)
    output = str(ROOT / "output" / "gardner_vox_final.mp4")

    vox_result = composer.render(timeline={"scenes": vox_scenes}, output_path=output)
    phase2_elapsed = time.monotonic() - phase2_start

    print(f"\nPhase 2 complete in {phase2_elapsed:.0f}s")
    print(f"Video: {output}")
    print(f"Size: {vox_result['size_mb']} MB")

    # ==========================================================
    print("\n" + "=" * 50)
    print("PERFORMANCE SUMMARY")
    print("=" * 50)
    total = phase1_elapsed + phase2_elapsed
    print(f"Phase 1 (images + TTS): {phase1_elapsed:.0f}s")
    print(f"Phase 2 (Vox render):   {phase2_elapsed:.0f}s")
    print(f"Total:                  {total:.0f}s")
    print(f"Output:                 {output}")
    print(f"Scenes:                 {len(vox_scenes)}")
    print(f"Duration:               {total_dur:.0f}s")
    print(f"Resolution:             1920x1080")
    print(f"Size:                   {vox_result['size_mb']} MB")


if __name__ == "__main__":
    main()
