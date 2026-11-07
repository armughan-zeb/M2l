"""Killable child-process entry point for the YouTube retrieval branch.

The Flask process owns orchestration and the UI log. This worker owns only the
provider calls and media cuts, so an aggregate branch wall can terminate the
entire process tree even when a third-party HTTP/yt-dlp call ignores a Python
thread cancellation.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Running a file inside ``server/services`` puts that directory on
# ``sys.path``. Add the server package root explicitly so ``services.*``
# imports match the Flask process.
SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: youtube_branch_worker.py REQUEST RESULT", flush=True)
        return 2
    request_path = Path(sys.argv[1])
    result_path = Path(sys.argv[2])
    try:
        payload = json.loads(request_path.read_text(encoding="utf-8"))
        if payload.get("pipeline_v2_mode"):
            os.environ["VIDZEO_PIPELINE_V2_MODE"] = "1"
            os.environ["VIDZEO_YT_VISUAL_GATE"] = "0"
        from services.youtube_pipeline_v2 import get_youtube_clips

        scene_texts = list(payload.get("scene_texts") or [])
        print(
            f"   [yt-worker] child started: {len(scene_texts)} scene(s); "
            "all provider subprocesses are killable",
            flush=True,
        )
        result = get_youtube_clips(
            script=str(payload.get("script") or ""),
            title=str(payload.get("title") or ""),
            num_scenes=len(scene_texts),
            scene_durations=list(payload.get("scene_durations") or []),
            scene_timing_windows=list(payload.get("scene_timing_windows") or []),
            openrouter_api_key=str(payload.get("openrouter_api_key") or ""),
            scene_texts=scene_texts,
            scene_query_overrides=list(payload.get("scene_query_overrides") or []),
            video_format=payload.get("video_format", ""),
            output_dir=payload.get("output_dir") or None,
            lenient_acceptance=bool(payload.get("lenient_acceptance", False)),
            download_candidate_limit=int(payload.get("download_candidate_limit") or 1),
            download_concurrency=payload.get("download_concurrency"),
            allow_same_source_reuse=bool(payload.get("allow_same_source_reuse", True)),
            stop_on_soft_timeout=bool(payload.get("stop_on_soft_timeout", False)),
            stream_downloads=bool(payload.get("stream_downloads", False)),
            max_download_scenes=int(payload.get("max_download_scenes") or 0),
            min_resolved_ratio_before_soft_stop=float(payload.get("min_resolved_ratio_before_soft_stop") or 0.0),
            stop_when_coverage_target_met=bool(payload.get("stop_when_coverage_target_met", False)),
            max_download_attempts_per_scene=int(payload.get("max_download_attempts_per_scene") or 0),
            search_timeout_seconds=payload.get("search_timeout_seconds"),
            soft_timeout_seconds=payload.get("soft_timeout_seconds"),
            soft_ceiling_seconds=payload.get("soft_ceiling_seconds"),
            hard_timeout_seconds=payload.get("hard_timeout_seconds"),
            session_jar=payload.get("session_jar"),
            youtube_session=payload.get("youtube_session"),
            use_authenticated_session=bool(payload.get("use_authenticated_session", True)),
            excluded_video_ids=payload.get('excluded_video_ids'),
            excluded_channel_ids=payload.get('excluded_channel_ids'),
            visual_context=None,
            prefer_whisper_timing=bool(payload.get("prefer_whisper_timing", True)),
            pool_first=bool(payload.get("pool_first", False)),
            cancellation=None,
        )
        output = {
            "ok": True,
            "paths": [str(path) if path else None for path in list(result or [])],
            "audit": getattr(result, "audit", {}) or {},
        }
        result_path.parent.mkdir(parents=True, exist_ok=True)
        temp_result = result_path.with_suffix(result_path.suffix + ".part")
        temp_result.write_text(
            json.dumps(output, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        temp_result.replace(result_path)
        print(
            f"   [yt-worker] child complete: {sum(1 for path in output['paths'] if path)}/{len(scene_texts)}",
            flush=True,
        )
        return 0
    except BaseException as exc:
        try:
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(
                json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, default=str),
                encoding="utf-8",
            )
        except Exception:
            pass
        print(f"   [yt-worker] failed: {type(exc).__name__}: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    # Provider libraries may leave non-daemon executor threads alive after the
    # result has been atomically written. A normal interpreter shutdown would
    # wait on those threads and make the parent appear stalled for another
    # minute. The parent owns the output file and process tree, so terminate
    # immediately once the result contract is durable.
    os._exit(main())
