"""Run a real YouTube V2 search/rank/download transport audit.

This diagnostic reads an existing script from queue.json and deliberately skips
TTS/rendering so production media delivery can be measured in isolation.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
sys.path[:0] = [str(SERVER), str(ROOT)]


def _select_script(contains: str) -> tuple[str, str]:
    payload = json.loads((SERVER / "queue.json").read_text(encoding="utf-8"))
    matches = []
    needle = contains.casefold()
    for item in payload.get("items", []):
        config = item.get("config") or {}
        script = str(config.get("script") or "").strip()
        if script and needle in script.casefold():
            matches.append((len(script), script, str(config.get("title") or config.get("name") or contains)))
    if not matches:
        raise RuntimeError(f"No queue script contains {contains!r}")
    _, script, title = max(matches)
    return script, title


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contains", required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--category", default="")
    parser.add_argument("--scenes", type=int, default=28)
    parser.add_argument("--provider-url", default="http://127.0.0.1:4416")
    parser.add_argument("--temp-dir", default="", help="Isolated temp directory for a cold transport audit")
    args = parser.parse_args()

    os.environ["VIDZEO_YT_POT_PROVIDER_URL"] = args.provider_url
    os.environ["VIDZEO_YT_POT_PROVIDER_READY"] = "1"
    os.environ["VIDZEO_YT_FAST_DOWNLOAD_PLAYER_CLIENTS"] = "mweb"
    os.environ["VIDZEO_YT_RESCUE_PLAYER_CLIENTS"] = "android_vr"
    os.environ["VIDZEO_YT_SEARCH_CONCURRENCY"] = "8"
    os.environ["VIDZEO_YT_DOWNLOAD_CONCURRENCY"] = "8"
    os.environ["VIDZEO_YT_V2_DOWNLOAD_ATTEMPTS_PER_SCENE"] = "2"
    os.environ["VIDZEO_YT_V2_DOWNLOAD_STAGE_TIMEOUT"] = "240"

    from services import youtube_pipeline_v2 as pipeline

    if args.temp_dir:
        pipeline.TEMP_DIR = Path(args.temp_dir).resolve()
        pipeline.TEMP_DIR.mkdir(parents=True, exist_ok=True)

    script, title = _select_script(args.contains)
    parsed = pipeline.parse_script_to_scenes(script, title, args.subject, args.scenes)
    scene_texts = [str(row.get("description") or "").strip() for row in parsed][: args.scenes]
    if len(scene_texts) < args.scenes:
        scene_texts.extend([args.subject] * (args.scenes - len(scene_texts)))
    overrides = [
        {
            "container_id": "transport-audit",
            "container_type": "topic",
            "section_subject": args.subject,
            "scene_subject": args.subject,
            "subject_query": args.subject,
            "domain_anchor": args.subject,
            "topic_anchor": args.subject,
            "domain_category": args.category,
        }
        for _ in scene_texts
    ]

    started = time.time()
    results = pipeline.get_youtube_clips(
        script=script,
        title=title,
        num_scenes=len(scene_texts),
        scene_durations=[4.0] * len(scene_texts),
        scene_texts=scene_texts,
        scene_query_overrides=overrides,
        topic_anchor=args.subject,
        video_format="single subject",
        max_download_attempts_per_scene=2,
    )
    elapsed = time.time() - started

    timestamps = defaultdict(list)
    source_ids = set()
    files = []
    for result in results:
        if not result:
            continue
        path = Path(result)
        files.append(str(path))
        meta_path = path.with_suffix(".meta.json")
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        video_id = str(meta.get("video_id") or "")
        if video_id:
            source_ids.add(video_id)
            timestamps[video_id].append(float(meta.get("start") or 0.0))

    near_duplicates = []
    for video_id, starts in timestamps.items():
        ordered = sorted(starts)
        for left, right in zip(ordered, ordered[1:]):
            if right - left < 8.0:
                near_duplicates.append({"video_id": video_id, "left": left, "right": right})

    summary = {
        "script_chars": len(script),
        "scenes": len(scene_texts),
        "resolved": len(files),
        "coverage_percent": round(100.0 * len(files) / max(1, len(scene_texts)), 1),
        "unique_sources": len(source_ids),
        "near_duplicate_timestamps": near_duplicates,
        "elapsed_seconds": round(elapsed, 2),
        "provider_ready": pipeline._pot_provider_ready(force=True),
    }
    print("YT_TRANSPORT_AUDIT " + json.dumps(summary, sort_keys=True))
    return 0 if files else 2


if __name__ == "__main__":
    raise SystemExit(main())
