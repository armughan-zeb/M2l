"""End-to-End Director Showcase Video Generator.

Demonstrates the Programmatic Documentary Editing Architecture:
1. Director analyzes script and executes closed-vocabulary scene decisions.
2. Emits structured EditPlan.json (multi-layer Lego architecture).
3. Enforces the 3-Second Rule (high tension cold open hook & rhythmic modulation).
4. Converts EditPlan to Remotion manifest.
5. Renders 100% locally via Remotion (0 cloud cost, local Chromium).
6. Extracts verification frames for visual review.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import edge_tts

from server.services.editorial_director import (
    EditingDirector,
    EditPlan,
    VisualSceneType,
)
from server.services.native_render_policy import render_semantic_documentary


OUT_DIR = ROOT / "output" / "director_showcase"
OUT_DIR.mkdir(parents=True, exist_ok=True)
FRAMES_DIR = OUT_DIR / "frames"
FRAMES_DIR.mkdir(parents=True, exist_ok=True)


SCRIPT_SCENES = [
    {
        "text": "In 1993, three engineers met in a Silicon Valley diner with a wild ambition.",
        "image": ROOT / "public" / "remotion-manifest-media" / "scene_001.jpg",
    },
    {
        "text": "For twenty years, critics dismissed their graphics processors as mere video game toys.",
        "image": ROOT / "public" / "remotion-manifest-media" / "scene_002.jpg",
    },
    {
        "text": "Then AI arrived, driving their market valuation from 500 billion to over 3 trillion dollars.",
        "image": ROOT / "public" / "remotion-manifest-media" / "scene_003.jpg",
    },
    {
        "text": 'Jensen Huang declared "we bet the entire company on accelerated computing."',
        "image": ROOT / "public" / "remotion-manifest-media" / "scene_004.jpg",
    },
    {
        "text": "Today, their silicon powers data centers across the globe from California to Tokyo.",
        "image": ROOT / "public" / "remotion-manifest-media" / "scene_005.jpg",
    },
]


def get_audio_duration(file_path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json", str(file_path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(res.stdout)
    return float(data["format"]["duration"])


async def generate_voiceovers() -> tuple[list[float], Path]:
    print("[1/5] Generating natural voiceover narration via edge_tts...")
    voice = "en-US-ChristopherNeural"
    clip_paths = []
    durations = []

    for idx, sc in enumerate(SCRIPT_SCENES):
        clip_path = OUT_DIR / f"voice_scene_{idx}.mp3"
        comm = edge_tts.Communicate(sc["text"], voice)
        await comm.save(str(clip_path))
        dur = get_audio_duration(clip_path)
        # Pad duration slightly by 0.3s for editorial breathing room
        padded_dur = round(dur + 0.3, 2)
        durations.append(padded_dur)
        clip_paths.append(clip_path)
        print(f"  Scene {idx}: {padded_dur:.2f}s - \"{sc['text'][:45]}...\"")

    # Concatenate all clips into a master voiceover file
    concat_list_file = OUT_DIR / "concat_list.txt"
    with open(concat_list_file, "w", encoding="utf-8") as f:
        for p in clip_paths:
            escaped = str(p).replace("\\", "/")
            f.write(f"file '{escaped}'\n")

    master_audio = OUT_DIR / "master_voiceover.wav"
    concat_cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_list_file),
        "-c:a", "pcm_s16le", str(master_audio)
    ]
    subprocess.run(concat_cmd, capture_output=True, check=True)
    total_audio_dur = get_audio_duration(master_audio)
    print(f"Master voiceover generated: {master_audio} ({total_audio_dur:.2f}s total)")
    return durations, master_audio


def main():
    start_time = time.monotonic()
    print("=" * 60)
    print("VIDZEO DIRECTOR ENGINE: DOCUMENTARY GENERATION PIPELINE")
    print("=" * 60)

    # 1. Voiceover Generation
    durations, master_voice = asyncio.run(generate_voiceovers())

    # 2. Editorial Planning
    print("\n[2/5] Running LLM Director to produce EditPlan.json...")
    director = EditingDirector()
    scene_texts = [sc["text"] for sc in SCRIPT_SCENES]
    media_items = [
        {"type": "image", "src": str(sc["image"]).replace("\\", "/")}
        for sc in SCRIPT_SCENES
    ]

    edit_plan: EditPlan = director.plan_documentary(
        script_text=" ".join(scene_texts),
        scene_texts=scene_texts,
        scene_durations=durations,
        media_items=media_items,
        topic="The Nvidia Empire: Silicon of the Gods",
        category="technology",
    )

    # Save EditPlan.json to disk
    edit_plan_path = OUT_DIR / "EditPlan.json"
    with open(edit_plan_path, "w", encoding="utf-8") as f:
        json.dump(edit_plan.to_dict(), f, indent=2)
    print(f"EditPlan.json written to: {edit_plan_path}")
    print(f"  Genre: {edit_plan.visual_dna.genre}")
    print(f"  Total Scenes: {len(edit_plan.scenes)}")
    for s in edit_plan.scenes:
        print(f"  [Scene {s.scene_index}] {s.primary_visual.type} ({s.primary_visual.variant}) | Camera: {s.motion.camera} | Entrance: {s.motion.entrance} | SFX: {[c.sfx_id for c in s.sound_cues]}")

    # 3. Remotion Manifest Conversion
    print("\n[3/5] Converting EditPlan to Remotion composition manifest...")
    manifest = edit_plan.to_remotion_manifest()
    # Attach master voiceover
    manifest["audio"]["voiceover"] = {
        "src": str(master_voice).replace("\\", "/"),
        "volume": 1.0,
    }
    # Attach ambient room tone
    manifest["audio"]["music"] = {
        "src": "/sound-effects/vidzeo/archive_drone.wav",
        "volume": 0.08,
    }

    manifest_path = OUT_DIR / "remotion_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"Remotion manifest written to: {manifest_path}")

    # 4. 100% Local Remotion Rendering
    print("\n[4/5] Invoking Local Remotion Renderer (0 cloud cost, Node.js + Chromium)...")
    output_mp4 = OUT_DIR / "director_documentary.mp4"
    render_res = render_semantic_documentary(
        manifest=manifest,
        output_path=output_mp4,
        config={
            "render_backend": "remotion",
            "scale": 1.0,
            "concurrency": 2,
        },
    )
    print(f"Render completed in {render_res['render_seconds']}s")
    print(f"Final MP4: {output_mp4} (Size: {output_mp4.stat().st_size / (1024*1024):.2f} MB)")

    # 5. Extract Verification Frames
    print("\n[5/5] Extracting visual verification frames...")
    curr_time = 0.0
    for idx, dur in enumerate(durations):
        midpoint = curr_time + (dur * 0.5)
        curr_time += dur
        frame_path = FRAMES_DIR / f"scene_{idx}_{edit_plan.scenes[idx].primary_visual.type.lower()}.png"
        cmd = [
            "ffmpeg", "-y", "-ss", f"{midpoint:.2f}",
            "-i", str(output_mp4),
            "-frames:v", "1",
            "-q:v", "2", str(frame_path)
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        print(f"  Extracted Scene {idx} frame ({midpoint:.2f}s): {frame_path.name}")

    total_time = round(time.monotonic() - start_time, 2)
    print("\n" + "=" * 60)
    print(f"ALL STEPS COMPLETED IN {total_time}s!")
    print(f"Final Documentary Video: {output_mp4}")
    print(f"EditPlan JSON: {edit_plan_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
