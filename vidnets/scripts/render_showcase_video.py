"""Script to generate and render a high-end documentary showcase video
demonstrating all overhaul features: professional typography, spring animations,
kinetic keyword sweeps, exponential stat counters, sound effect pre-rolls,
and smooth exit transitions.
"""

import sys
import time
import wave
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))

from server.services.semantic_edit_engine import build_semantic_edit_manifest, stable_seed
from server.services.native_render_policy import render_semantic_documentary, procedural_sound_library


def create_blank_wav(path: Path, duration_sec: float = 12.0, sample_rate: int = 44100):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00\x00\x00" * int(sample_rate * duration_sec))


def create_showcase_image(path: Path, color: tuple, title: str, subtitle: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (1920, 1080), color)
    draw = ImageDraw.Draw(img)
    # Subtle gradient or background grid
    for y in range(0, 1080, 80):
        draw.line([(0, y), (1920, y)], fill=(color[0] + 10, color[1] + 10, color[2] + 15), width=1)
    for x in range(0, 1920, 120):
        draw.line([(x, 0), (x, 1080)], fill=(color[0] + 10, color[1] + 10, color[2] + 15), width=1)
    img.save(str(path), "JPEG", quality=92)


def main():
    print("=== RENDERING HIGH-END DOCUMENTARY SHOWCASE VIDEO ===")
    out_dir = ROOT / "output" / "documentary_showcase"
    out_dir.mkdir(parents=True, exist_ok=True)

    audio_path = out_dir / "voiceover.wav"
    create_blank_wav(audio_path, duration_sec=12.0)

    # 4 distinct scenes showing different styles & components
    img0 = out_dir / "scene0_vox.jpg"
    img1 = out_dir / "scene1_broll.jpg"
    img2 = out_dir / "scene2_stat.jpg"
    img3 = out_dir / "scene3_magnates.jpg"

    create_showcase_image(img0, (15, 23, 42), "VOX EXPLAINER", "High-contrast Canary Yellow & Archival Header")
    create_showcase_image(img1, (20, 30, 48), "B-ROLL RECON", "Cinematic Media Layer with Camera Motion")
    create_showcase_image(img2, (18, 20, 26), "STAT COUNTER", "Exponential Easing & Impact Sync")
    create_showcase_image(img3, (8, 8, 10), "MAGNATES NOIR", "Luxury Gold & Blur-To-Sharp Typography")

    texts = [
        "In October 1962, military intelligence declassified an unprecedented nuclear emergency.",
        "Reconnaissance aircraft scanned Soviet supply convoys steaming across the Atlantic Ocean.",
        "Emergency naval blockades mobilized defense resources valued at $50 Billion.",
        "Diplomatic negotiations averted total conflict, marking a defining moment in modern history.",
    ]
    durations = [3.0, 3.0, 3.0, 3.0]
    media = [
        {"src": str(img0), "type": "image", "path": str(img0)},
        {"src": str(img1), "type": "image", "path": str(img1)},
        {"src": str(img2), "type": "image", "path": str(img2)},
        {"src": str(img3), "type": "image", "path": str(img3)},
    ]

    sound_lib = procedural_sound_library()

    print("[1/3] Building semantic documentary manifest...")
    manifest = build_semantic_edit_manifest(
        scene_texts=texts,
        scene_durations=durations,
        media=media,
        topic="Cuban Missile Crisis: Declassified Nuclear Stand专门",
        category="investigative_documentary",
        config={
            "variation_seed": stable_seed("documentary-showcase-v1"),
            "sound_library": sound_lib,
            "render_backend": "remotion",
        },
    )

    manifest["audio"] = {
        "voiceover": {"path": str(audio_path), "src": str(audio_path), "rights_status": "user_generated"}
    }

    print("\n[2/3] Manifest Scenes & Editorial Direction:")
    for i, sc in enumerate(manifest["scenes"]):
        gid = sc.get("graphic", {}).get("template_id")
        pv = sc.get("primary_visual", {})
        cues = sc.get("sound_cues", [])
        trans = sc.get("transition", {}).get("template_id")
        print(f"  Scene {i}: template={gid}, pv_type={pv.get('type')}, variant={pv.get('variant')}, transition={trans}, sfx_count={len(cues)}")

    out_mp4 = out_dir / "documentary_showcase.mp4"
    if out_mp4.exists():
        out_mp4.unlink()

    print(f"\n[3/3] Rendering video to {out_mp4} (Remotion with FFmpeg fallback)...")
    start_t = time.time()
    result = render_semantic_documentary(
        manifest=manifest,
        output_path=out_mp4,
        config={"render_backend": "remotion"},
    )
    elapsed = time.time() - start_t

    print("\n=== RENDER COMPLETE ===")
    print(f"Output File: {result.get('output_path')}")
    print(f"Renderer: {result.get('renderer', 'unknown')}")
    print(f"File Size: {Path(result.get('output_path')).stat().st_size:,} bytes")
    print(f"Time Taken: {elapsed:.2f} seconds")


if __name__ == "__main__":
    main()
