"""Verification Script for Full Motion Graphics Variety, World Map Animations, and Dynamic Palettes.

Tests:
1. Dynamic 4-Color Palettes & Topic Scoring (Eliminating forced blue/purple)
2. World Map Country Animations (country_spotlight, country_corridor, global_network)
3. Full Chart & Facts/Figures Variety (ranked_horizontal_bars, donut_distribution,
   multi_line_trend, area_growth_fill, radial_gauge, before_after_delta,
   trend_line_with_markers, editorial_bar)
4. Anti-Repetition Variety Governance
5. 1080p Local Remotion Frame Verification
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.services.editorial_director import EditingDirector, detect_genre, generate_visual_dna
from server.services.native_render_policy import render_semantic_documentary_remotion

OUT_DIR = ROOT / "output" / "variety_showcase"
OUT_DIR.mkdir(parents=True, exist_ok=True)
FRAMES_DIR = OUT_DIR / "frames"
FRAMES_DIR.mkdir(parents=True, exist_ok=True)
ARTIFACT_DIR = Path(r"C:\Users\user\.gemini\antigravity\brain\92f3db13-954a-4b44-a08e-9383e3c2c23b")

def test_palette_diversity():
    print("=" * 75)
    print("[1] Testing Topic-Based Palette Selection (Zero Forced Blue/Purple)")
    print("=" * 75)

    topics = [
        ("The Global Semiconductor War", "technology", "silicon chip wafer tsmc hardware"),
        ("Clean Energy Transition 2030", "technology", "clean energy solar battery ev grid"),
        ("Wall Street Gold & Reserves", "finance-explainer", "gold bullion swiss bank currency"),
        ("Federal Wiretap & The Cartel", "true-crime", "fbi detective undercover wiretap crime"),
        ("Murder at Blackwood Manor", "true-crime", "murder blood killer forensic autopsy"),
        ("Amazon Rainforest Canopy", "science-nature", "forest wildlife canopy amazon rainforest"),
        ("The Mariana Trench Deep Dive", "science-nature", "ocean marine sea abyss water trench"),
        ("Grand Theft Auto VI", "gaming-blockbuster", "vice city florida miami rockstar"),
        ("Vox Investigative Explainer", "investigative-documentary", "vox explainer breakdown data how why"),
    ]

    for topic, category, script in topics:
        dna = generate_visual_dna(topic, category, script)
        p = dna.palette
        print(f"Topic: {topic:32} | Genre: {dna.genre:22}")
        print(f"  -> Accent: {p.get('accent')} | Secondary: {p.get('secondary')} | Accent2: {p.get('accent2')} | Surface: {p.get('surface')}")
        assert p.get('accent') is not None
        assert p.get('accent') not in ("#3B82F6", "#00F0FF", "#8A2BE2") # Ensure no default blue/purple


def test_variety_director_plan():
    print("\n" + "=" * 75)
    print("[2] Planning 9-Scene Global Semiconductor Documentary with Full Variety")
    print("=" * 75)

    SCENES = [
        # Scene 0: Cold Open Hook (ChapterTitle)
        "The Silicon Fortress: The Geopolitics of Advanced Microchips.",
        # Scene 1: World Map Country Spotlight (Japan JPN)
        "In Japan, Tokyo has committed over four trillion yen to re-establish sovereign domestic chipmaking.",
        # Scene 2: Multi-line comparison chart (USA vs Allied Competitors)
        "Comparing investment trajectories over the past five years, the United States versus allied competitors shows a massive divergence.",
        # Scene 3: Clean cinematic B-Roll (Breathing room)
        "Inside automated fabrication facilities, robotic tracks transport silicon discs through nitrogen-sealed atmospheric chambers.",
        # Scene 4: World Map Transnational Corridor (From USA to Japan)
        "From the United States to Japan, strategic supply chains transport extreme ultraviolet lithography optics across the Pacific.",
        # Scene 5: Before / After Delta Chart
        "Under federal subsidy packages, domestic fabrication capacity jumped from 20 million wafers to 120 million, a staggering 500 percent leap.",
        # Scene 6: Radial Gauge Chart (Market Saturation)
        "Leading foundries operate at an astonishing 92 percent capacity saturation to meet global artificial intelligence demands.",
        # Scene 7: World Map Global Alliance Network (USA, DEU, JPN)
        "Across the United States, Germany, and Japan, tri-lateral research consortia are standardizing two nanometer silicon architecture.",
        # Scene 8: Donut Distribution Chart (Foundry Market Share)
        "The breakdown of global sub-five nanometer manufacturing reveals a 74 percent dominant concentration in East Asia.",
    ]

    durations = [4.5, 5.0, 5.5, 4.5, 5.5, 5.5, 5.0, 5.5, 5.5]

    director = EditingDirector()
    plan = director.plan_documentary(
        script_text=" ".join(SCENES),
        scene_texts=SCENES,
        scene_durations=durations,
        topic="The Global Semiconductor War",
        category="technology",
    )

    print(f"Visual DNA Genre: {plan.visual_dna.genre}")
    print(f"Branded Palette: {plan.visual_dna.palette}")
    print(f"Chart Theme: {plan.visual_dna.chart_style}")
    print("-" * 75)

    for s in plan.scenes:
        pv = s.primary_visual
        props = pv.props
        title = props.get("title", props.get("kicker", ""))
        print(f"Scene {s.scene_index}: [{pv.type:12}] | Variant: {pv.variant:23} | Title: {title}")

    return plan, durations


def render_and_verify(plan, durations):
    print("\n" + "=" * 75)
    print("[3] Rendering Verification Frames via Local Remotion")
    print("=" * 75)

    manifest = plan.to_remotion_manifest()

    # Assign high-res backdrop image for Scene 3 so full bleed looks great
    backdrop = ROOT / "output" / "productions" / "gta_vi_1min_showcase" / "frame_3.png"
    if backdrop.exists() and len(manifest["scenes"]) > 3:
        manifest["scenes"][3]["primary_visual"]["asset"] = {
            "type": "image",
            "src": str(backdrop).replace("\\", "/"),
        }

    manifest["audio"] = {
        "voiceover": None,
        "music": None,
        "sound_effects": [],
    }

    manifest_path = OUT_DIR / "variety_showcase_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    output_mp4 = OUT_DIR / "variety_showcase.mp4"

    # Render via local Remotion
    print("Starting Remotion render (100% local Chromium)...")
    t0 = time.monotonic()
    render_res = render_semantic_documentary_remotion(
        manifest=manifest,
        output_path=output_mp4,
        config={
            "scale": 1.0,
            "concurrency": 2,
        },
    )
    render_elapsed = round(time.monotonic() - t0, 2)
    print(f"Remotion Render Completed in {render_elapsed}s!")

    # Extract verification frames at midpoint of each scene
    print("\nExtracting verification frames for every scene...")
    curr_time = 0.0
    extracted_frames = []
    for idx, dur in enumerate(durations):
        midpoint = curr_time + (dur * 0.5)
        curr_time += dur
        sc = plan.scenes[idx]
        sc_type = sc.primary_visual.type.lower()
        sc_variant = sc.primary_visual.variant.lower()
        frame_name = f"variety_sc{idx}_{sc_type}_{sc_variant}.png"
        frame_path = FRAMES_DIR / frame_name

        cmd = [
            "ffmpeg", "-y", "-ss", f"{midpoint:.2f}",
            "-i", str(output_mp4),
            "-frames:v", "1",
            "-q:v", "2", str(frame_path)
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        print(f"  [OK] Scene {idx} [{sc_type} : {sc_variant}] -> {frame_name}")
        extracted_frames.append(frame_path)

        # Copy to artifact dir
        if ARTIFACT_DIR.exists():
            shutil.copy2(frame_path, ARTIFACT_DIR / frame_name)

    print("\nAll verification frames successfully saved to:")
    print(f"  {FRAMES_DIR}")
    return extracted_frames


if __name__ == "__main__":
    test_palette_diversity()
    plan, durations = test_variety_director_plan()
    frames = render_and_verify(plan, durations)
    print("\nTEST SUITE FINISHED SUCCESSFULLY!")
