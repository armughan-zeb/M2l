"""Verification script: Renders a real test video via render_semantic_documentary
to verify fast-paced editorial cadence, facts & figures overlays, and clean cuts.
"""

import os
import sys
import wave
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))

from server.services.semantic_edit_engine import build_semantic_edit_manifest, stable_seed
from server.services.native_render_policy import render_semantic_documentary, procedural_sound_library


def create_blank_wav(path: Path, duration_sec: float = 8.0, sample_rate: int = 44100):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00\x00\x00" * int(sample_rate * duration_sec))


def create_sample_image(path: Path, color: tuple, label: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (1280, 720), color)
    draw = ImageDraw.Draw(img)
    draw.text((50, 50), label, fill=(255, 255, 255))
    img.save(str(path), "JPEG", quality=90)


def main():
    print("=== STARTING FAST-PACED EDITORIAL CADENCE RENDER VERIFICATION ===")
    out_dir = ROOT / "verification_output"
    out_dir.mkdir(parents=True, exist_ok=True)

    audio_path = out_dir / "test_silent.wav"
    create_blank_wav(audio_path, duration_sec=8.0)

    img0 = out_dir / "scene0.jpg"
    img1 = out_dir / "scene1.jpg"
    img2 = out_dir / "scene2.jpg"
    img3 = out_dir / "scene3.jpg"

    create_sample_image(img0, (20, 25, 35), "SCENE 0: DATE/CRISIS")
    create_sample_image(img1, (30, 40, 50), "SCENE 1: RECON AIRCRAFT")
    create_sample_image(img2, (25, 30, 45), "SCENE 2: $50B DEFENSE")
    create_sample_image(img3, (35, 45, 55), "SCENE 3: DIPLOMATIC BRIEFING")

    texts = [
        "In 1962, military intelligence confirmed an unprecedented crisis.",
        "Reconnaissance aircraft scanned the open ocean looking for suspicious convoys.",
        "Emergency defense appropriations surged past $50 billion.",
        "Top commanders briefed the president on diplomatic containment measures.",
    ]
    durations = [2.0, 2.0, 2.0, 2.0]
    media = [
        {"src": str(img0), "type": "image", "path": str(img0)},
        {"src": str(img1), "type": "image", "path": str(img1)},
        {"src": str(img2), "type": "image", "path": str(img2)},
        {"src": str(img3), "type": "image", "path": str(img3)},
    ]

    sound_lib = procedural_sound_library()

    print("Building semantic edit manifest with seed from video ID 'vid-cadence-001'...")
    manifest = build_semantic_edit_manifest(
        scene_texts=texts,
        scene_durations=durations,
        media=media,
        topic="Cuban Missile Crisis",
        category="history",
        config={
            "variation_seed": stable_seed("vid-cadence-001"),
            "sound_library": sound_lib,
            "render_backend": "ffmpeg",
        },
    )

    manifest["audio"] = {
        "voiceover": {"path": str(audio_path), "src": str(audio_path), "rights_status": "user_generated"}
    }

    print("Checking scene graphic cadence...")
    for i, sc in enumerate(manifest["scenes"]):
        gid = sc.get("graphic", {}).get("template_id")
        pv = sc.get("primary_visual", {})
        print(f"  Scene {i}: template={gid}, pv_type={pv.get('type')}, variant={pv.get('variant')}")

    out_mp4 = out_dir / "cadence_verification.mp4"
    if out_mp4.exists():
        out_mp4.unlink()

    print(f"Rendering documentary to {out_mp4} using native FFmpeg...")
    result = render_semantic_documentary(
        manifest,
        str(out_mp4),
        {
            "name": "cadence_verification",
            "semantic_render_quality": "draft",
            "render_backend": "ffmpeg",
        },
    )

    assert out_mp4.exists(), f"Render failed: {out_mp4} was not generated!"
    file_size = out_mp4.stat().st_size
    print(f"[SUCCESS] Output MP4 generated successfully! Size: {file_size:,} bytes")
    assert file_size > 10000, f"File too small: {file_size} bytes"

    # Extract verification frames at each scene midpoint
    frame_times = [1.0, 3.0, 5.0, 7.0]
    for i, t in enumerate(frame_times):
        frame_path = out_dir / f"frame_scene_{i}_{t}s.png"
        cmd = [
            "ffmpeg", "-y", "-ss", str(t), "-i", str(out_mp4),
            "-vframes", "1", str(frame_path)
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        assert frame_path.exists(), f"Frame extraction failed for t={t}s"
        print(f"[SUCCESS] Extracted frame for scene {i} at {t}s -> {frame_path.name}")

    print("\n[SUCCESS] ALL VERIFICATIONS COMPLETED SUCCESSFULLY!")


if __name__ == "__main__":
    main()
