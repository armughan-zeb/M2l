"""Targeted YouTube-clips branch test (mirrors a real queue job).

Calls app._run_youtube_clips_branch directly with a 6-scene Titanic script so
the probe -> plan -> cut -> gate pipeline is exercised end to end without
running TTS/whisper/render. Uses the same env forcing and 300s branch wall a
real queue job gets, then verifies per-scene clip paths, the resolution
journal, and that no truncated .raw.* artifacts are left behind.
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module

SCENE_TEXTS = [
    "On April 10, 1912, the RMS Titanic, the largest ocean liner ever built, embarked on its maiden voyage from Southampton to New York, hailed as unsinkable with over two thousand passengers and crew.",
    "Constructed in Belfast by Harland and Wolff, Titanic measured 882 feet and featured opulent saloons, a grand staircase, and watertight compartments, commanded by Captain Edward Smith.",
    "On the moonless night of April 14, lookouts spotted a massive iceberg directly ahead; despite the order to turn hard-a-starboard, Titanic struck the iceberg along its starboard side.",
    "Water flooded the forward compartments, and chief designer Thomas Andrews calculated the vessel would sink within two hours; the crew began launching lifeboats under women and children first.",
    "At 2:20 AM on April 15, Titanic broke in two and sank with over 1,500 lives lost, while the liner Carpathia rushed through ice fields to rescue 705 survivors at dawn.",
    "The wreck of Titanic was discovered in 1985 resting two and a half miles deep, where it remains a protected memorial to the greatest maritime disaster in history.",
]

SCRIPT = "\n\n".join(SCENE_TEXTS)


def on_progress(pct, msg):
    print(f"  [progress {pct:5.1f}%] {msg}", flush=True)


def main():
    t0 = time.time()
    print("=" * 80)
    print(f"YT BRANCH DEBUG TEST ({len(SCENE_TEXTS)} scenes)")
    print("=" * 80, flush=True)

    config = {
        "script": SCRIPT,
        "title": "The Sinking of RMS Titanic - YouTube Clips Accurate Final",
        "name": "yt_branch_debug_titanic",
        "image_source": "YouTube Clips",
        "type": "long",
        "yt_pool_first": True,
        "yt_skip_llm_query_planner": False,
        "visual_understanding": False,
        "yt_lenient_acceptance": False,
        "fail_on_unresolved_media": False,
        "yt_min_source_duration": 20,
        "yt_max_download_attempts_per_scene": 4,
        "_run_temp_dir": str(Path(os.environ.get("VIDZEO_TEMP_DIR", "temp")) / "yt_branch_debug"),
        "_branch_start_event": None,
    }

    from services.cancellation import CancellationContext

    cancellation = CancellationContext(job_id="debug-yt-branch")
    try:
        yt_paths = app_module._run_youtube_clips_branch(
            SCENE_TEXTS,
            [10.0] * len(SCENE_TEXTS),
            config,
            config["title"],
            SCRIPT,
            progress_callback=on_progress,
            cancellation=cancellation,
        )
    except Exception as exc:
        print("\n" + "=" * 80)
        print("YT BRANCH FAILED")
        print("=" * 80)
        import traceback

        traceback.print_exc()
        print("ERROR:", repr(exc))
        return

    print("\n" + "=" * 80)
    print("YT BRANCH RESULTS")
    print("=" * 80)
    resolved = 0
    for index, path in enumerate(yt_paths or []):
        if path and os.path.exists(path):
            resolved += 1
            print(
                f"  scene {index + 1:02d}: RESOLVED {path} ({os.path.getsize(path) / 1024:.0f} KB)"
            )
        else:
            print(f"  scene {index + 1:02d}: UNRESOLVED")
    print(f"  resolved {resolved}/{len(SCENE_TEXTS)} scenes in {time.time() - t0:.1f}s")

    run_dir = Path(config["_run_temp_dir"])
    run_folders = sorted(run_dir.glob("*")) if run_dir.is_dir() else []
    latest = max(run_folders, key=lambda p: p.stat().st_mtime) if run_folders else None
    if latest is not None:
        journal = latest / "resolution_audit.json"
        print(f"\n  run folder: {latest}")
        print(f"  journal: {journal} ({'EXISTS' if journal.is_file() else 'MISSING'})")
        raw_leftovers = list((latest / "cuts").glob("*.raw.*")) if (latest / "cuts").is_dir() else []
        print(f"  leftover .raw.* artifacts: {len(raw_leftovers)}")
        probes = list((latest / "probes").glob("*.probe.mp4")) if (latest / "probes").is_dir() else []
        print(f"  probe files: {len(probes)}")
    else:
        print(f"\n  no run folder found under {run_dir}")


if __name__ == "__main__":
    main()