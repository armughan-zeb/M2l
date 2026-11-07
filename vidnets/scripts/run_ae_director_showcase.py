"""End-to-End After Effects Director Showcase Generator.

Demonstrates:
1. Parallel Director Pipeline: Upfront script analysis & scene partitioning.
2. Parallel media resolution: Media-independent scenes proceed immediately,
   media-attached scenes resolve concurrently in worker threads.
3. After Effects-Grade Cartography: 3D camera flythrough, laser arc, live odometer,
   topographic isobars, Johnny Harris territory highlight polygon.
4. Broadcast Typography: Oswald, Archivo Black, Inter, Playfair Display, JetBrains Mono.
5. 100% Local Remotion Execution: $0.00 cloud cost, running on local Chromium.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
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
from server.services.parallel_director_pipeline import ParallelDirectorPipeline
from server.services.native_render_policy import render_semantic_documentary_remotion


OUT_DIR = ROOT / "output" / "director_showcase"
OUT_DIR.mkdir(parents=True, exist_ok=True)
FRAMES_DIR = OUT_DIR / "frames"
FRAMES_DIR.mkdir(parents=True, exist_ok=True)

ARTIFACT_DIR = Path(r"C:\Users\user\.gemini\antigravity\brain\92f3db13-954a-4b44-a08e-9383e3c2c23b")

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


async def ensure_voiceovers() -> tuple[list[float], Path]:
    print("[1/5] Checking voiceover narration tracks...")
    voice = "en-US-ChristopherNeural"
    clip_paths = []
    durations = []

    for idx, sc in enumerate(SCRIPT_SCENES):
        clip_path = OUT_DIR / f"voice_scene_{idx}.mp3"
        if not clip_path.exists() or clip_path.stat().st_size < 1000:
            print(f"  Generating voiceover for Scene {idx} via edge_tts...")
            comm = edge_tts.Communicate(sc["text"], voice)
            await comm.save(str(clip_path))
        dur = get_audio_duration(clip_path)
        padded_dur = round(dur + 0.3, 2)
        durations.append(padded_dur)
        clip_paths.append(clip_path)
        print(f"  Scene {idx}: {padded_dur:.2f}s - \"{sc['text'][:45]}...\"")

    master_audio = OUT_DIR / "master_voiceover.wav"
    if not master_audio.exists() or master_audio.stat().st_size < 50000:
        concat_list_file = OUT_DIR / "concat_list.txt"
        with open(concat_list_file, "w", encoding="utf-8") as f:
            for p in clip_paths:
                escaped = str(p).replace("\\", "/")
                f.write(f"file '{escaped}'\n")

        concat_cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_list_file),
            "-c:a", "pcm_s16le", str(master_audio)
        ]
        subprocess.run(concat_cmd, capture_output=True, check=True)

    total_audio_dur = get_audio_duration(master_audio)
    print(f"Master voiceover ready: {master_audio} ({total_audio_dur:.2f}s total)")
    return durations, master_audio


def main():
    start_time = time.monotonic()
    print("=" * 65)
    print("VIDZEO PARALLEL DIRECTOR: AFTER EFFECTS GRADE SHOWCASE")
    print("=" * 65)

    # 1. Voiceover Prep
    durations, master_voice = asyncio.run(ensure_voiceovers())

    # 2. Parallel Director Planning
    print("\n[2/5] Initializing Parallel Director Pipeline...")
    pipeline = ParallelDirectorPipeline(max_media_workers=4)

    scene_texts = [sc["text"] for sc in SCRIPT_SCENES]

    # Plan the entire timeline upfront into an EditPlan
    plan, tasks = pipeline.plan_timeline(
        script_text=" ".join(scene_texts),
        scene_texts=scene_texts,
        scene_durations=durations,
        topic="The Nvidia Empire: Silicon of the Gods",
        category="technology",
    )

    # Attach initial local media if present to simulate resolved media
    def media_fetcher(idx: int, scene) -> str:
        # Simulate parallel retrieval worker
        time.sleep(0.05)
        local_img = SCRIPT_SCENES[idx]["image"]
        return str(local_img).replace("\\", "/")

    print("\n[3/5] Executing Parallel Media Retrieval Zone...")
    resolved_plan = pipeline.resolve_media_parallel(plan, tasks, media_fetcher=media_fetcher)

    # Save finalized EditPlan JSON
    edit_plan_path = OUT_DIR / "ae_EditPlan.json"
    with open(edit_plan_path, "w", encoding="utf-8") as f:
        json.dump(resolved_plan.to_dict(), f, indent=2)
    print(f"EditPlan JSON written to: {edit_plan_path}")
    print(f"  Genre: {resolved_plan.visual_dna.genre}")
    typo = resolved_plan.visual_dna.typography
    disp = typo.get("display") if isinstance(typo, dict) else getattr(typo, "display", "")
    num = typo.get("numbers") if isinstance(typo, dict) else getattr(typo, "numbers", "")
    print(f"  Typography: Display={disp}, Numbers={num}")
    for s in resolved_plan.scenes:
        print(f"  [Scene {s.scene_index}] {s.primary_visual.type} ({s.primary_visual.variant}) | Camera: {s.motion.camera} | SFX: {[c.sfx_id for c in s.sound_cues]}")

    # 3. Export Manifest
    print("\n[4/5] Preparing Remotion manifest with master audio & sound effects...")
    manifest = resolved_plan.to_remotion_manifest()
    manifest["audio"]["voiceover"] = {
        "src": str(master_voice).replace("\\", "/"),
        "volume": 1.0,
    }
    manifest["audio"]["music"] = {
        "src": "/sound-effects/vidzeo/archive_drone.wav",
        "volume": 0.08,
    }

    manifest_path = OUT_DIR / "ae_remotion_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"Remotion manifest written to: {manifest_path}")

    # 4. Render directly via Remotion (100% local Chromium)
    output_mp4 = OUT_DIR / "ae_documentary_showcase.mp4"
    print(f"\n[5/5] Invoking 100% Local Remotion Renderer (Output: {output_mp4})...")
    render_res = render_semantic_documentary_remotion(
        manifest=manifest,
        output_path=output_mp4,
        config={
            "scale": 1.0,
            "concurrency": 2,
        },
    )
    print(f"Remotion render finished in {render_res['render_seconds']}s")
    print(f"Generated Video: {output_mp4} ({output_mp4.stat().st_size / (1024*1024):.2f} MB)")

    # 5. Extract Verification Frames
    print("\n[Verification] Extracting high-res verification frames for each scene...")
    curr_time = 0.0
    extracted_frames = []
    for idx, dur in enumerate(durations):
        midpoint = curr_time + (dur * 0.5)
        curr_time += dur
        sc_type = resolved_plan.scenes[idx].primary_visual.type.lower()
        frame_name = f"ae_scene_{idx}_{sc_type}.png"
        frame_path = FRAMES_DIR / frame_name
        cmd = [
            "ffmpeg", "-y", "-ss", f"{midpoint:.2f}",
            "-i", str(output_mp4),
            "-frames:v", "1",
            "-q:v", "2", str(frame_path)
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        print(f"  [OK] Frame extracted: Scene {idx} [{sc_type}] at {midpoint:.2f}s -> {frame_name}")
        extracted_frames.append(frame_path)

    # 6. Copy Video and Frames to Artifact Directory for User Presentation
    if ARTIFACT_DIR.exists():
        print(f"\n[Artifacts] Copying assets to brain artifact directory: {ARTIFACT_DIR}")
        dest_video = ARTIFACT_DIR / "ae_documentary_showcase.mp4"
        shutil.copy2(output_mp4, dest_video)
        print(f"  [OK] Copied video to: {dest_video}")
        for fp in extracted_frames:
            dest_fp = ARTIFACT_DIR / fp.name
            shutil.copy2(fp, dest_fp)
            print(f"  [OK] Copied frame to: {dest_fp}")

    total_time = round(time.monotonic() - start_time, 2)
    print("\n" + "=" * 65)
    print(f"PIPELINE COMPLETE IN {total_time}s!")
    print(f"Documentary Video: {output_mp4}")
    print(f"EditPlan JSON: {edit_plan_path}")
    print("=" * 65)


if __name__ == "__main__":
    main()
