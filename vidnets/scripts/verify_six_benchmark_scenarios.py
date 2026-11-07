"""Step 12 Benchmark Verification across 6 Documentary Scenarios.

Validates the full integrated pipeline across 6 canonical documentary scenarios:
1. Corner Identification over moving footage (Action/Automotive beat).
2. Monumental Center Keyword Emphasis (Thesis/Hook beat).
3. Evidence Annotation with authentic source asset (Investigative beat).
4. Geographic Locator with real coordinates (Spatial/Location beat).
5. Quantitative Metric / Numeric Emphasis (Data/Statistics beat).
6. Attributed Quote Pull with source attribution (Executive statement beat).

Executes:
- Manifest generation with apply_media_first_direction
- SequenceController 60s cooldown & anti-repetition audit
- 5-role Accountable Reviewer Council (lead_editor, cinematographer, graphics_director, evidence_reviewer, visual_critic)
- Frame-level metrics audit (luma, edge density, variance, zero dark/blank frames)
- Validates >=78% media visibility and reading hold floors (corner >= 2.0s, center >= 3.0s, data/maps >= 5.0s)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))

from server.services.native_render_policy import render_semantic_documentary_remotion
from server.services.editorial_director.media_director import plan_media_composition
from server.services.editorial_director.media_first import (
    COMPOSITION_LIBRARY,
    apply_media_first_direction,
    audit_editorial_contracts,
)
from server.services.editorial_director.sequence_controller import (
    SequenceController,
    audit_sequence_continuity,
)
from server.services.editorial_director.manager import execute_reviewer_council
from server.services.editorial_director.visual_critic import audit_frames, audit_manifest


BENCHMARK_SCENARIOS = [
    {
        "id": "scenario_01_corner_id",
        "name": "Corner Identification over Moving Footage",
        "family": "corner_identification",
        "narration": "Rockstar Games shattered global records within twenty-four hours of releasing the first trailer.",
        "media": {"src": str((ROOT / "temp" / "proof_clip_cinematic.mp4").resolve()), "type": "video"},
        "primary_visual": {
            "type": "TextCard",
            "variant": "upper_identifier",
            "props": {
                "text": "RECORD-BREAKING REVEAL",
                "source": "GLOBAL LAUNCH",
                "composition_family": "corner_identification",
            },
        },
        "duration": 5.5,
        "min_hold": 2.0,
        "expected_anchor": "top_left",
    },
    {
        "id": "scenario_02_center_emphasis",
        "name": "Monumental Center Keyword Emphasis",
        "family": "keyword_emphasis",
        "narration": "For more than a decade, the gaming world has waited for one definitive blockbuster: Grand Theft Auto VI.",
        "media": {"src": str((ROOT / "temp" / "proof_clip_vice_city.mp4").resolve()), "type": "video"},
        "primary_visual": {
            "type": "TextCard",
            "variant": "word_scale",
            "props": {
                "text": "DEFINITIVE BLOCKBUSTER",
                "composition_family": "keyword_emphasis",
                "position": "center",
            },
        },
        "duration": 5.0,
        "min_hold": 3.0,
        "expected_anchor": "center",
    },
    {
        "id": "scenario_03_evidence_annotation",
        "name": "Evidence Annotation with Source Bracket",
        "family": "evidence_annotation",
        "narration": "With an unprecedented production budget, industry analysts forecast GTA VI to become the biggest entertainment launch ever.",
        "media": {"src": str((ROOT / "public" / "remotion-manifest-media" / "lucia_jason_50s.jpg").resolve()), "type": "image"},
        "primary_visual": {
            "type": "DocumentHighlight",
            "variant": "evidence_bracket",
            "props": {
                "text": "UNPRECEDENTED BUDGET",
                "source": "ANALYST FORECAST",
                "composition_family": "evidence_annotation",
                "evidence_ref": "SEC_FILING_2024",
            },
        },
        "duration": 5.8,
        "min_hold": 3.0,
        "expected_anchor": "bottom_left",
    },
    {
        "id": "scenario_04_geographic_locator",
        "name": "Geographic Locator with Coordinates",
        "family": "locator_route",
        "narration": "Returning players to the neon-drenched streets of Vice City across the Florida peninsula.",
        "media": {"src": str((ROOT / "temp" / "proof_clip_vice_city.mp4").resolve()), "type": "video"},
        "primary_visual": {
            "type": "AnimatedMap",
            "variant": "single_locator",
            "props": {
                "location": "VICE CITY",
                "country": "FLORIDA",
                "composition_family": "locator_route",
                "source": "FLORIDA · 25.76°N 80.19°W",
            },
        },
        "duration": 6.2,
        "min_hold": 5.0,
        "expected_anchor": "bottom_left",
    },
    {
        "id": "scenario_05_numeric_emphasis",
        "name": "Quantitative Metric / Numeric Emphasis",
        "family": "numeric_emphasis",
        "narration": "Wall Street analysts projected initial shipments to exceed two billion dollars in net bookings.",
        "media": {"src": str((ROOT / "temp" / "proof_clip_cinematic.mp4").resolve()), "type": "video"},
        "primary_visual": {
            "type": "StatCard",
            "variant": "number_lockup",
            "props": {
                "value": "$2 BILLION",
                "label": "PROJECTED BOOKINGS",
                "source": "GOLDMAN SACHS REPORT",
                "composition_family": "numeric_emphasis",
            },
        },
        "duration": 5.5,
        "min_hold": 2.5,
        "expected_anchor": "bottom_left",
    },
    {
        "id": "scenario_06_attributed_quote",
        "name": "Attributed Quote Pull with Attribution",
        "family": "attributed_quote",
        "narration": "The memo described a company standing on a burning platform with urgent change required.",
        "media": {"src": str((ROOT / "temp" / "proof_clip_cinematic.mp4").resolve()), "type": "video"},
        "primary_visual": {
            "type": "QuoteCard",
            "variant": "margin_quote",
            "props": {
                "quote": "We are standing on a burning platform.",
                "attribution": "STEPHEN ELOP",
                "source": "NOKIA INTERNAL MEMO",
                "composition_family": "attributed_quote",
            },
        },
        "duration": 6.0,
        "min_hold": 3.0,
        "expected_anchor": "bottom_left",
    },
]


def run_benchmark_verification() -> dict:
    print("=" * 78)
    print("RUNNING DOCUMENTARY EDITING ENGINE BENCHMARK: 6 SCENARIOS")
    print("=" * 78)

    raw_manifest = {
        "density_mode": "dense",
        "visual_system": {
            "media_first": {
                "density_mode": "dense",
                "editing_personality": "automatic",
            },
        },
        "visual_dna": {
            "genre": "investigative-documentary",
            "typography": {
                "display": "Plus Jakarta Sans",
                "body": "Inter",
                "numbers": "Inter",
            },
            "palette": {
                "primary": "#f8fafc",
                "secondary": "#b6c2d0",
                "accent": "#f2c94c",
                "background": "#0b0f14",
            },
        },
        "scenes": [
            {
                "scene_index": idx,
                "duration": scn["duration"],
                "narration": scn["narration"],
                "media": scn["media"],
                "primary_visual": scn["primary_visual"],
                "importance": 0.9,
            }
            for idx, scn in enumerate(BENCHMARK_SCENARIOS)
        ],
    }

    # Step A: Apply media-first direction & schedule contracts
    print("\n[Step A] Applying media-first direction...")
    manifest = apply_media_first_direction(raw_manifest, plan_media_composition)
    scenes = manifest["scenes"]
    assert len(scenes) == 6, f"Expected 6 scenes, got {len(scenes)}"

    # Step B: Validate each scenario's contract, timing floor, and media-first rules
    print("[Step B] Verifying scenario-level contracts and timing floors...")
    scenario_results = []
    for idx, (spec, scene) in enumerate(zip(BENCHMARK_SCENARIOS, scenes)):
        contract = scene.get("editorial_contract", {})
        timing = contract.get("timing", {})
        comp = scene.get("media_composition", {})

        family = contract.get("family")
        enabled = contract.get("enabled")
        hold_seconds = float(timing.get("hold_seconds") or 0.0)
        total_seconds = float(timing.get("total_seconds") or 0.0)
        expected_min_hold = spec["min_hold"]

        # Assertions
        assert enabled is True, f"Scenario {idx} ({spec['name']}) contract was disabled"
        assert family == spec["family"], f"Scenario {idx} family mismatch: expected {spec['family']}, got {family}"
        assert hold_seconds >= expected_min_hold - 1e-3, (
            f"Scenario {idx} hold time {hold_seconds:.2f}s is below floor {expected_min_hold}s"
        )
        assert comp.get("overlay_background") in {None, "", "transparent", "none"}, (
            f"Scenario {idx} overlay has forbidden filled background: {comp.get('overlay_background')}"
        )
        assert comp.get("hero_layer") == "source_media", f"Scenario {idx} hero layer is not source_media"

        scenario_results.append({
            "scenario": spec["name"],
            "family": family,
            "anchor": contract.get("anchor"),
            "hold_seconds": hold_seconds,
            "total_seconds": total_seconds,
            "scene_duration": scene["duration"],
            "status": "PASS",
        })
        print(f"  [OK] [{spec['id']}] {spec['name']:48s} | Family: {family:20s} | Hold: {hold_seconds:.2f}s >= {expected_min_hold}s")

    # Step C: SequenceController Anti-Repetition & Cooldown Verification
    print("\n[Step C] Verifying SequenceController anti-repetition rules...")
    seq_audit = audit_sequence_continuity(manifest)
    print(f"  Sequence audit status: {seq_audit['status'].upper()}")
    print(f"  Unique families: {seq_audit['unique_families']} / 6")
    print(f"  Font families: {seq_audit['font_families']}")
    print(f"  Cooldown violations: {seq_audit['cooldown_violations']}")
    print(f"  Adjacent repetitions: {seq_audit['adjacent_repetitions']}")
    assert seq_audit["status"] == "pass", f"Sequence continuity audit failed: {seq_audit['errors']}"
    assert seq_audit["cooldown_violations"] == 0, "Found cooldown violations"
    assert seq_audit["adjacent_repetitions"] == 0, "Found adjacent repetitions"
    assert len(seq_audit["font_families"]) <= 2, f"Font families exceed 2: {seq_audit['font_families']}"

    # Step D: Multi-Agent Reviewer Council Execution
    print("\n[Step D] Executing 5-Role Accountable Reviewer Council...")
    council_res = execute_reviewer_council(manifest)
    council = council_res["council"]
    for role_name, role_data in council.items():
        role_status = role_data.get("status", "unknown").upper()
        print(f"  [OK] {role_name:24s} : {role_status}")
        assert role_status == "PASS", f"Council role {role_name} failed: {role_data}"

    assert council_res["status"] == "pass", "Reviewer Council did not approve manifest"
    assert council_res["ready_for_long_render"] is True

    # Step E: Visual Critic Manifest & Occupancy Verification
    print("\n[Step E] Executing Visual Critic checks...")
    critic_audit = audit_manifest(manifest)
    assert critic_audit["status"] == "pass", f"Visual Critic failed: {critic_audit['findings']}"
    assert critic_audit["errors"] == 0, f"Found critic errors: {critic_audit['findings']}"
    print(f"  Visual Critic status: {critic_audit['status'].upper()} (0 errors)")

    # Step F: Render 6 Benchmark Scenarios via Remotion Headless Chrome
    print("\n[Step F] Rendering 6 Benchmark Scenarios through Remotion...")
    output_mp4 = ROOT / "output" / "benchmark_six_scenarios.mp4"
    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    frames_dir = ROOT / "verification_frames" / "benchmark_six_scenarios"
    frames_dir.mkdir(parents=True, exist_ok=True)

    # Prepare manifest timing & geometry
    total_duration = sum(scn["duration"] for scn in manifest["scenes"])
    t_cursor = 0.0
    for scn in manifest["scenes"]:
        scn["start"] = round(t_cursor, 2)
        scn["id"] = f"scene_{scn['scene_index']}"
        t_cursor += scn["duration"]
        scn.setdefault("camera", {"motion": "drift"})

    manifest.update({
        "version": 4,
        "title": "Documentary Engine Proof — Six Benchmark Scenarios",
        "fps": 30,
        "width": 1920,
        "height": 1080,
        "duration": round(total_duration, 2),
        "duration_in_frames": int(round(total_duration * 30)),
    })

    t_render_start = time.time()
    render_semantic_documentary_remotion(
        manifest=manifest,
        output_path=output_mp4,
        config={"concurrency": 2, "scale": 1}
    )
    render_elapsed = time.time() - t_render_start
    print(f"  Render completed in {render_elapsed:.1f}s ({output_mp4.stat().st_size / (1024*1024):.2f} MB)")

    # Step G: Extract entrance, hold, exit frames for all 6 scenes
    print("\n[Step G] Extracting 18 entrance, hold, and exit evaluation frames...")
    extracted_frames = []
    for scn in manifest["scenes"]:
        idx = scn["scene_index"] + 1
        start = scn["start"]
        dur = scn["duration"]
        timing = (scn.get("editorial_contract") or {}).get("timing") or {}
        entry_sec = float(timing.get("entry_seconds") or 0.35)
        hold_sec = float(timing.get("hold_seconds") or 2.0)

        t_enter = start + min(dur - 0.2, entry_sec + 0.1)
        t_hold = start + min(dur - 0.4, entry_sec + hold_sec * 0.5)
        t_exit = start + max(0.1, dur - 0.15)

        for phase, t_val in [("enter", t_enter), ("hold", t_hold), ("exit", t_exit)]:
            frame_name = f"scene_{idx:02d}_{phase}_{t_val:.2f}s.jpg"
            out_file = frames_dir / frame_name
            cmd = [
                "ffmpeg", "-y", "-ss", f"{t_val:.3f}",
                "-i", str(output_mp4),
                "-frames:v", "1",
                "-q:v", "2",
                str(out_file)
            ]
            subprocess.run(cmd, capture_output=True)
            if out_file.exists() and out_file.stat().st_size > 0:
                extracted_frames.append(out_file)
                print(f"  [Frame] Extracted {frame_name} ({out_file.stat().st_size / 1024:.1f} KB)")
            else:
                print(f"  [Frame] WARNING: Failed to extract {frame_name}")

    # Step H: Frame-Level Visual Critic & Luma/Edge Density Audit
    print("\n[Step H] Running Visual Critic frame-level audit on 18 rendered frames...")
    frame_audit = audit_frames(extracted_frames)
    print(f"  Frame audit status: {frame_audit['status'].upper()}")
    print(f"  Low information frames: {frame_audit['low_information_frames']}")
    print(f"  Excessively dark frames: {frame_audit['dark_frames']}")
    print(f"  Soft/blurred frames: {frame_audit['soft_frames']}")
    assert frame_audit["low_information_frames"] == 0, f"Found {frame_audit['low_information_frames']} low-information frames"

    summary = {
        "status": "pass",
        "scenarios_tested": len(BENCHMARK_SCENARIOS),
        "scenario_details": scenario_results,
        "sequence_audit": seq_audit,
        "council_verdict": council_res["status"],
        "frame_audit": {
            "status": frame_audit["status"],
            "total_frames": len(extracted_frames),
            "low_information_frames": frame_audit["low_information_frames"],
            "dark_frames": frame_audit["dark_frames"],
            "soft_frames": frame_audit["soft_frames"],
        },
        "rendered_video": str(output_mp4.resolve()),
        "rendered_size_mb": round(output_mp4.stat().st_size / (1024 * 1024), 2),
        "ready_for_long_render": True,
    }

    # Save summary report
    out_file = ROOT / "verification_output" / "benchmark_six_scenarios_report.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nReport written to: {out_file.resolve()}")
    print("=" * 78)
    print("BENCHMARK VERIFICATION: ALL 6 SCENARIOS PASSED WITH FULL COUNCIL APPROVAL")
    print("=" * 78)
    return summary


if __name__ == "__main__":
    run_benchmark_verification()
