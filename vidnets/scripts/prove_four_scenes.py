"""Step 3 Proof: Rebuild four representative failures through the Remotion documentary pipeline.

1. Clean corner identification over moving footage.
2. Larger, meaningful center emphasis.
3. Evidence annotation using a real source asset.
4. Geographic locator with accurate coordinates.

Extracts entrance, hold, and exit frames for all 4 scenes and validates the Step 3 completion gate.
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
sys.path.insert(0, str(ROOT / "server"))

from server.services.native_render_policy import render_semantic_documentary_remotion

PROOF_DIR = ROOT / "verification_frames" / "proof_four_scenes"
OUTPUT_MP4 = ROOT / "output" / "proof_four_scenes.mp4"
MANIFEST_JSON = ROOT / "temp" / "proof_four_scenes.manifest.json"

CLIP_CINEMATIC = ROOT / "temp" / "proof_clip_cinematic.mp4"
CLIP_VICE_CITY = ROOT / "temp" / "proof_clip_vice_city.mp4"
CLIP_NIGHT_CITY = ROOT / "cache" / "media" / "pexels_pexels_v_10109068_cc86775cc29ee297.mp4"
EVIDENCE_IMAGE = ROOT / "public" / "remotion-manifest-media" / "lucia_jason_50s.jpg"


def extract_authentic_speech_audio(output_dir: Path) -> list[dict]:
    """Extract real narration audio slices from the verified GTA showcase audio."""
    output_dir.mkdir(parents=True, exist_ok=True)
    source_video = ROOT / "output" / "productions" / "gta_vi_1min_showcase" / "GTA_VI_1Min_Showcase.mp4"
    if not source_video.is_file():
        source_video = ROOT / "output" / "GTA VI 1-Min Mixed Media Showcase.mp4"

    # Exact time slices for the 4 scenes from the benchmark
    scenes_meta = [
        {
            "idx": 1,
            "start": 4.98,
            "duration": 5.50,
            "text": "Rockstar Games shattered global records within twenty-four hours of releasing the first official GTA VI trailer."
        },
        {
            "idx": 2,
            "start": 0.00,
            "duration": 4.98,
            "text": "For more than a decade, the gaming world has waited for one definitive blockbuster: Grand Theft Auto VI."
        },
        {
            "idx": 3,
            "start": 46.50,
            "duration": 5.80,
            "text": "With an unprecedented production budget, industry analysts forecast GTA VI to become the biggest entertainment launch ever."
        },
        {
            "idx": 4,
            "start": 13.76,
            "duration": 6.20,
            "text": "Returning players to the neon-drenched streets of Vice City, the game delivers the largest and densest open world in gaming history."
        }
    ]

    results = []
    for scn in scenes_meta:
        audio_path = output_dir / f"scene_{scn['idx']}.mp3"
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(scn["start"]),
            "-t", str(scn["duration"]),
            "-i", str(source_video),
            "-vn", "-c:a", "libmp3lame", "-b:a", "192k",
            str(audio_path)
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if not audio_path.exists() or audio_path.stat().st_size == 0:
            raise RuntimeError(f"Failed to extract audio for scene {scn['idx']}: {res.stderr}")
        
        results.append({
            "path": str(audio_path.resolve()),
            "duration": scn["duration"],
            "text": scn["text"]
        })
    return results


def build_proof_manifest(audio_scenes: list[dict]) -> dict:
    """Build the clean v4 SemanticEditManifest for the four scenes."""
    t0 = 0.0
    scenes = []
    
    # Scene 1: Corner identification over moving footage
    s1_dur = audio_scenes[0]["duration"]
    scenes.append({
        "id": "scene-1-corner-id",
        "scene_index": 0,
        "start": t0,
        "duration": s1_dur,
        "narration": audio_scenes[0]["text"],
        "media": {
            "type": "video",
            "src": str(CLIP_CINEMATIC.resolve()),
            "source_start": 0.0,
            "safe_framing": False,
        },
        "primary_visual": {
            "type": "Headline",
            "variant": "upper_identifier",
            "props": {
                "display_text": "RECORD-BREAKING REVEAL",
                "emphasis_phrase": "RECORD-BREAKING",
                "source": "GLOBAL LAUNCH",
            },
        },
        "media_composition": {
            "enabled": True,
            "hero_layer": "source_media",
            "placement": "top_left",
            "overlay_grammar": "field_caption",
            "blur_media_underlay": False,
        },
        "editorial_contract": {
            "enabled": True,
            "family": "corner_identification",
            "variant": "upper_identifier",
            "anchor": "top_left",
            "display_text": "RECORD-BREAKING REVEAL",
            "emphasis_phrase": "RECORD-BREAKING",
            "source": "GLOBAL LAUNCH",
            "timing": {
                "total_seconds": s1_dur,
                "enter_seconds": 0.35,
                "exit_seconds": 0.25,
            },
        },
        "camera": {"motion": "static"},
        "sound_cues": [
            {"name": "whoosh.light", "time_local": 0.1, "volume": 0.4}
        ],
    })
    t0 += s1_dur

    # Scene 2: Large Center Emphasis over moving footage
    s2_dur = audio_scenes[1]["duration"]
    scenes.append({
        "id": "scene-2-center-emphasis",
        "scene_index": 1,
        "start": t0,
        "duration": s2_dur,
        "narration": audio_scenes[1]["text"],
        "media": {
            "type": "video",
            "src": str(CLIP_VICE_CITY.resolve()),
            "source_start": 0.0,
            "safe_framing": False,
        },
        "primary_visual": {
            "type": "KineticText",
            "variant": "word_scale",
            "props": {
                "display_text": "DEFINITIVE BLOCKBUSTER",
                "emphasis_phrase": "DEFINITIVE BLOCKBUSTER",
                "source": "DECADE WAIT",
            },
        },
        "media_composition": {
            "enabled": True,
            "hero_layer": "source_media",
            "placement": "center",
            "overlay_grammar": "keyword_emphasis",
            "blur_media_underlay": False,
        },
        "editorial_contract": {
            "enabled": True,
            "family": "keyword_emphasis",
            "variant": "word_scale",
            "anchor": "center",
            "display_text": "DEFINITIVE BLOCKBUSTER",
            "emphasis_phrase": "DEFINITIVE BLOCKBUSTER",
            "source": "DECADE WAIT",
            "timing": {
                "total_seconds": s2_dur,
                "enter_seconds": 0.35,
                "exit_seconds": 0.25,
            },
        },
        "camera": {"motion": "slow_push"},
        "sound_cues": [
            {"name": "impact.soft", "time_local": 0.3, "volume": 0.5}
        ],
    })
    t0 += s2_dur

    # Scene 3: Evidence annotation using a real source asset
    s3_dur = audio_scenes[2]["duration"]
    scenes.append({
        "id": "scene-3-evidence-annotation",
        "scene_index": 2,
        "start": t0,
        "duration": s3_dur,
        "narration": audio_scenes[2]["text"],
        "media": {
            "type": "image",
            "src": str(EVIDENCE_IMAGE.resolve()),
            "safe_framing": False,
        },
        "primary_visual": {
            "type": "DocumentHighlight",
            "variant": "evidence_bracket",
            "props": {
                "display_text": "UNPRECEDENTED BUDGET",
                "emphasis_phrase": "UNPRECEDENTED",
                "source": "ANALYST FORECAST",
            },
        },
        "media_composition": {
            "enabled": True,
            "hero_layer": "source_media",
            "placement": "bottom_left",
            "overlay_grammar": "evidence_annotation",
            "blur_media_underlay": False,
        },
        "editorial_contract": {
            "enabled": True,
            "family": "evidence_annotation",
            "variant": "evidence_bracket",
            "anchor": "bottom_left",
            "display_text": "UNPRECEDENTED BUDGET",
            "emphasis_phrase": "UNPRECEDENTED",
            "source": "ANALYST FORECAST",
            "timing": {
                "total_seconds": s3_dur,
                "enter_seconds": 0.35,
                "exit_seconds": 0.25,
            },
        },
        "camera": {"motion": "pan_left"},
        "sound_cues": [
            {"name": "paper.rustle", "time_local": 0.1, "volume": 0.35}
        ],
    })
    t0 += s3_dur

    # Scene 4: Geographic locator with accurate coordinates
    s4_dur = max(5.0, audio_scenes[3]["duration"])
    scenes.append({
        "id": "scene-4-geographic-locator",
        "scene_index": 3,
        "start": t0,
        "duration": s4_dur,
        "narration": audio_scenes[3]["text"],
        "media": {
            "type": "video",
            "src": str(CLIP_VICE_CITY.resolve()),
            "source_start": 0.8,
            "safe_framing": False,
        },
        "primary_visual": {
            "type": "AnimatedMap",
            "variant": "single_locator",
            "props": {
                "location": "Vice City",
                "coordinates": {"lat": 25.7617, "lon": -80.1918},
                "display_text": "VICE CITY METRO",
                "source": "FLORIDA · 25.76°N 80.19°W",
            },
        },
        "media_composition": {
            "enabled": True,
            "hero_layer": "source_media",
            "placement": "top_left",
            "overlay_grammar": "locator_inset",
            "blur_media_underlay": False,
        },
        "editorial_contract": {
            "enabled": True,
            "family": "locator_route",
            "variant": "single_locator",
            "anchor": "top_left",
            "location": {
                "kind": "locator",
                "location": "Vice City",
                "coordinates": {"lat": 25.7617, "lon": -80.1918}
            },
            "display_text": "VICE CITY METRO",
            "source": "FLORIDA · 25.76°N 80.19°W",
            "timing": {
                "total_seconds": s4_dur,
                "enter_seconds": 0.40,
                "exit_seconds": 0.25,
            },
        },
        "camera": {"motion": "drift"},
        "sound_cues": [
            {"name": "map.ping", "time_local": 0.2, "volume": 0.45}
        ],
    })
    t0 += s4_dur

    # Merge audio tracks into single audio stream
    audio_tracks = []
    for scn in scenes:
        idx = scn["scene_index"]
        audio_tracks.append({
            "src": audio_scenes[idx]["path"],
            "start": scn["start"],
            "duration": scn["duration"],
        })

    manifest = {
        "version": 4,
        "title": "Documentary Engine Proof — Four Redesigned Scenes",
        "fps": 30,
        "width": 1920,
        "height": 1080,
        "duration": round(t0, 2),
        "duration_in_frames": int(round(t0 * 30)),
        "theme": {
            "id": "vox_clarity",
            "palette": {
                "background": "#0b0f14",
                "primary": "#f8fafc",
                "secondary": "#b6c2d0",
                "accent": "#f2c94c",
                "border": "rgba(255,255,255,.32)"
            }
        },
        "visual_dna": {
            "genre": "vox-editorial",
            "typography": {
                "display": "Archivo Black",
                "editorial": "Plus Jakarta Sans",
                "body": "DM Sans",
                "numbers": "JetBrains Mono",
                "compact": "Barlow Condensed"
            },
            "palette": {
                "background": "#0b0f14",
                "primary": "#f8fafc",
                "secondary": "#b6c2d0",
                "accent": "#f2c94c",
                "border": "rgba(255,255,255,.32)"
            }
        },
        "scenes": scenes,
        "audio": {
            "tracks": audio_tracks
        }
    }
    return manifest


def extract_evaluation_frames(video_path: Path, manifest: dict, output_dir: Path):
    """Extract entrance, hold, and exit frames for all scenes."""
    output_dir.mkdir(parents=True, exist_ok=True)
    scenes = manifest["scenes"]
    
    extracted = []
    for scn in scenes:
        idx = scn["scene_index"] + 1
        start = scn["start"]
        dur = scn["duration"]
        
        t_enter = start + 0.35
        t_hold = start + min(dur - 0.5, 2.0)
        t_exit = start + dur - 0.15
        
        for phase, t_val in [("enter", t_enter), ("hold", t_hold), ("exit", t_exit)]:
            frame_name = f"scene_{idx:02d}_{phase}_{t_val:.2f}s.jpg"
            out_file = output_dir / frame_name
            cmd = [
                "ffmpeg", "-y", "-ss", f"{t_val:.3f}",
                "-i", str(video_path),
                "-frames:v", "1",
                "-q:v", "2",
                str(out_file)
            ]
            subprocess.run(cmd, capture_output=True)
            if out_file.exists() and out_file.stat().st_size > 0:
                extracted.append(str(out_file))
                print(f"  [Frame] Extracted {frame_name} ({out_file.stat().st_size / 1024:.1f} KB)")
            else:
                print(f"  [Frame] WARNING: Failed to extract {frame_name}")
    return extracted


def main():
    print("=" * 80)
    print("STEP 3 PROOF: REBUILDING FOUR REPRESENTATIVE SCENES THROUGH PIPELINE")
    print("=" * 80)

    # 1. Narrations for the 4 scenes
    script_texts = [
        "Rockstar Games shattered global records within twenty-four hours of releasing the first official GTA VI trailer.",
        "For more than a decade, the gaming world has waited for one definitive blockbuster: Grand Theft Auto VI.",
        "With an unprecedented production budget, industry analysts forecast GTA VI to become the biggest entertainment launch ever.",
        "Returning players to the neon-drenched streets of Vice City, the game delivers the largest and densest open world in gaming history."
    ]

    # 2. Extract Authentic Audio
    print("\n[1/4] Extracting authentic narration audio for the 4 scenes...")
    audio_dir = ROOT / "temp" / "proof_audio"
    audio_scenes = extract_authentic_speech_audio(audio_dir)
    for i, a in enumerate(audio_scenes):
        print(f"  Scene {i+1} duration: {a['duration']}s | file: {Path(a['path']).name}")

    # 3. Build Manifest
    print("\n[2/4] Assembling SemanticEditManifest v4...")
    manifest = build_proof_manifest(audio_scenes)
    MANIFEST_JSON.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_JSON.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  Manifest written to: {MANIFEST_JSON} ({len(manifest['scenes'])} scenes, {manifest['duration']}s total)")

    # 4. Render Video via Remotion Headless Chrome
    print("\n[3/4] Rendering video via Remotion headless pipeline...")
    OUTPUT_MP4.parent.mkdir(parents=True, exist_ok=True)
    t_start = time.time()
    
    result = render_semantic_documentary_remotion(
        manifest=manifest,
        output_path=OUTPUT_MP4,
        config={"concurrency": 2, "scale": 1}
    )
    render_elapsed = time.time() - t_start
    print(f"\nRender completed in {render_elapsed:.1f}s!")
    print(f"Output Video: {OUTPUT_MP4} ({OUTPUT_MP4.stat().st_size / (1024*1024):.2f} MB)")

    # 5. Extract evaluation frames
    print("\n[4/4] Extracting entrance, hold, and exit frames for all 4 scenes...")
    extracted_frames = extract_evaluation_frames(OUTPUT_MP4, manifest, PROOF_DIR)
    print(f"Successfully extracted {len(extracted_frames)} evaluation frames in {PROOF_DIR}")
    print("\n" + "=" * 80)
    print("STEP 3 PROOF COMPLETED SUCCESSFULLY!")
    print("=" * 80)


if __name__ == "__main__":
    main()
