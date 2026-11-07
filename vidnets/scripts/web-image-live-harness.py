"""Opt-in live web-image uniqueness and accuracy smoke harness.

This script makes real provider requests.  It is intentionally excluded from
CI and writes only to Vidzeo's temp retrieval directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))


SCENES = [
    "RMS Titanic left Southampton on her maiden voyage across the Atlantic Ocean.",
    "The ocean liner carried first class passengers, families, crew, and emigrants.",
    "Titanic lookouts watched the cold North Atlantic for icebergs at night.",
    "The ship struck an iceberg along her starboard side before midnight.",
    "Water flooded Titanic boiler rooms and forward compartments.",
    "Captain Edward Smith and officers assessed the damage on the bridge.",
    "Wireless operators Jack Phillips and Harold Bride sent distress calls.",
    "Titanic passengers assembled on the boat deck wearing life jackets.",
    "Crew lowered RMS Titanic lifeboats into the dark Atlantic.",
    "The orchestra musicians played while the evacuation continued.",
    "RMS Carpathia arrived and rescued survivors from Titanic lifeboats.",
    "Titanic wreckage was discovered on the North Atlantic seabed decades later.",
]

QUERY_VARIANTS = [
    ["RMS Titanic Southampton departure", "Titanic maiden voyage Atlantic"],
    ["RMS Titanic passengers deck", "Titanic emigrants crew"],
    ["RMS Titanic lookouts iceberg", "Titanic crow nest lookouts"],
    ["RMS Titanic iceberg collision", "Titanic starboard iceberg"],
    ["RMS Titanic boiler rooms flooding", "Titanic flooded compartments"],
    ["RMS Titanic Captain Edward Smith bridge", "Titanic officers bridge"],
    ["RMS Titanic wireless operators distress", "Titanic radio room"],
    ["RMS Titanic passengers life jackets", "Titanic evacuation passengers"],
    ["RMS Titanic lifeboats launched", "Titanic lifeboat evacuation"],
    ["RMS Titanic orchestra musicians", "Titanic band musicians"],
    ["RMS Carpathia Titanic survivors rescue", "Carpathia Titanic lifeboats"],
    ["RMS Titanic wreck seabed", "Titanic wreck discovery"],
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deadline", type=int, default=90)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--min-unique-ratio", type=float, default=0.85)
    parser.add_argument("--max-scenes", type=int, default=len(SCENES))
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=ROOT / ".env")
    except Exception:
        pass

    os.environ["VIDZEO_WEB_STAGE_DEADLINE_SECONDS"] = str(args.deadline)
    os.environ.setdefault("VIDZEO_WEB_FIRST_WAVE_DEADLINE_SECONDS", "6")
    os.environ.setdefault("VIDZEO_WEB_SECOND_WAVE_DEADLINE_SECONDS", "6")
    # The harness measures the critical fast lane; gallery-dl remains an
    # opt-in runtime fallback and must not make this bounded smoke flaky.
    os.environ["VIDZEO_WEB_ENABLE_GDL"] = "0"
    os.environ.setdefault("VIDZEO_WEB_VERBOSE", "0")

    from services.web_image.resolver import resolve_web_images_for_video_fast

    started = time.monotonic()
    scene_count = max(1, min(len(SCENES), int(args.max_scenes)))
    scenes = SCENES[:scene_count]
    query_variants = QUERY_VARIANTS[:scene_count]
    result = resolve_web_images_for_video_fast(
        script=" ".join(scenes),
        title="RMS Titanic documentary",
        scene_texts=scenes,
        scene_durations=[4.0] * len(scenes),
        scene_queries_override=[
            {"query_variants": variants} for variants in query_variants
        ],
        subject_override="RMS Titanic",
        output_namespace=f"live_harness_{int(time.time())}",
        fast_mode=True,
        max_candidate_downloads=6,
        max_scene_workers=args.workers,
        api_worker_limit=8,
        visual_context=None,
    )

    paths = [path for path in result.get("image_paths", []) if path]
    # The production resolver returns the authoritative per-scene rows in
    # ``results``.  Older experimental coordinators called this field a
    # resolution ledger; keeping the live harness on the real contract avoids
    # a false zero-result report after successful downloads.
    ledger = result.get("results") or ((result.get("audit") or {}).get("scenes") or [])
    providers = Counter(
        str(row.get("provider") or row.get("source") or row.get("domain") or "unknown")
        for row in ledger
        if isinstance(row, dict) and row.get("path")
    )
    fingerprints = []
    for path in paths:
        try:
            fingerprints.append(hashlib.sha256(Path(path).read_bytes()).hexdigest())
        except Exception:
            fingerprints.append(str(path))
    unique_assets = len(set(fingerprints))
    unique_ratio = unique_assets / max(1, len(scenes))
    summary = {
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "scenes": len(scenes),
        "resolved": len(paths),
        "unique_assets": unique_assets,
        "unique_ratio": round(unique_ratio, 3),
        "fallback_reuse": sum(
            1 for row in ledger
            if isinstance(row, dict) and row.get("reused_from_scene")
        ),
        "providers": dict(providers),
        "queries": result.get("scene_queries") or [],
        "audit_path": result.get("audit_path"),
    }
    print(json.dumps(summary, indent=2))
    return 0 if (
        len(paths) == len(scenes)
        and unique_ratio >= args.min_unique_ratio
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
