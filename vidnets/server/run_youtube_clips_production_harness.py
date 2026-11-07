import os
import sys
import time
import json
import traceback
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.youtube_pipeline_v2 import get_youtube_clips, select_youtube_scene_matches
import services.youtube_pipeline_v2 as yt_v2

TITANIC_HARNESS_SCENES = [
    "On April 10, 1912, the RMS Titanic embarked on its maiden voyage from Southampton, England.",
    "Constructed in Belfast by Harland and Wolff, Titanic was the largest passenger liner in the world.",
    "Captain Edward Smith commanded the massive ocean liner as it sped across the chilly North Atlantic.",
    "Inside, wealthy passengers enjoyed opulent dining saloons, a grand staircase, and luxurious suites.",
    "Hundreds of immigrants traveled in third class, dreaming of new opportunities in America.",
    "On the moonless night of April 14, lookouts spotted a giant iceberg directly ahead.",
    "First Officer Murdoch ordered hard-a-starboard and full reverse, but the ship scraped the iceberg.",
    "Freezing Atlantic water flooded five forward watertight compartments below the waterline.",
    "Chief designer Thomas Andrews calculated that Titanic would sink in less than two hours.",
    "Marconi wireless operators transmitted SOS distress signals into the midnight sky.",
    "Crew members launched wooden lifeboats into the freezing sea under women and children first.",
    "At 2:20 AM, the ocean liner broke in two and plunged into the abyss.",
    "RMS Carpathia rushed through dangerous ice fields at top speed to rescue survivors.",
    "At dawn, Carpathia rescued 705 shivering survivors from the lifeboats.",
    "In 1985, Dr. Robert Ballard discovered the shipwreck resting 12,500 feet under the sea."
]

def run_harness():
    print("=" * 80)
    print("YOUTUBE CLIPS PRODUCTION HARNESS — RESOLUTION, RANKING & CLEANING ANALYSIS")
    print("=" * 80, flush=True)

    title = "The Sinking of RMS Titanic"
    script = " ".join(TITANIC_HARNESS_SCENES)
    num_scenes = len(TITANIC_HARNESS_SCENES)
    scene_durations = [4.0] * num_scenes

    # Environment setup
    os.environ["VIDZEO_VIP_SAFETY_OVERRIDE"] = "1"
    os.environ["VIDZEO_YT_BLUR_SMALL_LOGOS"] = "1"
    
    t0 = time.time()

    # Step 1: Analyze Metadata Container Search & Candidate Ranking
    print("\n--- STEP 1: Metadata Container Search & Candidate Ranking Analysis ---")
    metadata_t0 = time.time()
    matches = select_youtube_scene_matches(
        scene_texts=TITANIC_HARNESS_SCENES,
        title=title,
        script=script,
        scene_durations=scene_durations
    )
    metadata_elapsed = time.time() - metadata_t0
    print(f"Metadata analysis elapsed: {metadata_elapsed:.2f}s")
    
    print(f"Container Query Plan Rows: {len(matches.get('query_plan', []))}")
    for idx, match in enumerate(matches.get("matches", [])):
        sel = match.get("selected_video")
        sel_title = sel.get("title") if sel else "NONE"
        sel_score = sel.get("score") if sel else 0.0
        candidates_count = len(match.get("ranked_candidates") or [])
        print(f"  Scene {idx+1:02d}: Pool Candidates={candidates_count} | Winner: '{sel_title}' (Score={sel_score:.2f})")

    # Step 2: Full Download, Visual Ranking & LAVIP Blur Cleaning Pass
    print("\n--- STEP 2: Downloading, Visual Ranking & Blur Cleaning Pass ---")
    output_dir = Path(__file__).parent.parent / "temp" / "yt_harness_output"
    output_dir.mkdir(parents=True, exist_ok=True)

    results = get_youtube_clips(
        script=script,
        title=title,
        num_scenes=num_scenes,
        scene_durations=scene_durations,
        scene_texts=TITANIC_HARNESS_SCENES,
        output_dir=output_dir
    )

    elapsed = time.time() - t0
    pipeline_elapsed = elapsed - metadata_elapsed
    resolved_count = sum(1 for r in results if r and os.path.exists(r))

    print("\n" + "=" * 80)
    print("HARNESS RESULTS SUMMARY:")
    print("=" * 80)
    print(f"Total Scenes: {num_scenes}")
    print(f"Resolved YouTube Clips: {resolved_count} / {num_scenes} ({resolved_count/num_scenes*100:.1f}%)")
    print(f"Total Harness Runtime: {elapsed:.2f}s")
    print(f"Full resolver elapsed: {pipeline_elapsed:.2f}s")
    print(
        f"Duplicate metadata-search overhead: {metadata_elapsed:.2f}s "
        "(analysis Step 1 is intentionally repeated by the full resolver in Step 2)"
    )

    audit = []
    for idx, path in enumerate(results):
        status = "RESOLVED" if (path and os.path.exists(path)) else "FAILED"
        file_size = os.path.getsize(path) / (1024 * 1024) if (path and os.path.exists(path)) else 0.0
        print(f"  Scene {idx+1:02d}: [{status}] Path={os.path.basename(path or 'NONE')} | Size={file_size:.2f}MB")
        audit.append({
            "scene_index": idx,
            "scene_text": TITANIC_HARNESS_SCENES[idx],
            "status": status,
            "path": path,
            "file_size_mb": file_size,
        })

    manifest_dir = Path(__file__).parent.parent / "temp" / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    audit_file = manifest_dir / "youtube_clip_audit.json"
    with open(audit_file, "w", encoding="utf-8") as f:
        json.dump({
            "total_scenes": num_scenes,
            "resolved_count": resolved_count,
            "success_rate": resolved_count / num_scenes,
            "elapsed_seconds": elapsed,
            "pipeline_audit": getattr(results, "audit", {}) or {},
            "scenes": audit,
        }, f, indent=2)
    print(f"\nAudit report written to: {audit_file}")

if __name__ == "__main__":
    run_harness()
