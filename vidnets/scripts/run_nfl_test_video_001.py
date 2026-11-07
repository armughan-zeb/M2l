"""Run and archive NFL Test 001 through Vidzeo's live queue and Remotion pipeline."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://127.0.0.1:5000"
RECORD_DIR = Path(os.environ.get(
    "VIDZEO_NFL_TEST_RECORD_DIR",
    str(ROOT / "artifacts" / "nfl_test_001_20261002"),
)).resolve()
SERVER_LOG = Path(os.environ.get(
    "VIDZEO_NFL_TEST_SERVER_LOG",
    str(ROOT / "logs" / "server-runtime.log"),
)).resolve()
SERVER_ERR_LOG = Path(os.environ.get(
    "VIDZEO_NFL_TEST_SERVER_ERR_LOG",
    str(ROOT / "logs" / "server-restart-stderr.log"),
)).resolve()
MAX_RUNTIME_SECONDS = 2400
POLL_SECONDS = 3
ATTEMPT_NUMBER = int(os.environ.get("VIDZEO_NFL_TEST_ATTEMPT_NUMBER", "1") or 1)


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


YT_COVERAGE_TARGET = max(0.0, min(1.0, _env_float("VIDZEO_NFL_YT_COVERAGE_TARGET", 0.30)))
YT_WALL_TIMEOUT_SECONDS = max(45, int(_env_float("VIDZEO_NFL_YT_WALL_TIMEOUT_SECONDS", 300)))
YT_SOURCE_DEADLINE_SECONDS = max(30, min(
    YT_WALL_TIMEOUT_SECONDS,
    int(_env_float("VIDZEO_NFL_YT_SOURCE_DEADLINE_SECONDS", 180)),
))
TTS_MODE = os.environ.get("VIDZEO_NFL_TTS_MODE", "Use OpenRouter TTS")
TTS_MODEL = os.environ.get("VIDZEO_NFL_TTS_MODEL", "deepgram/flux-tts:free")
VOICE = os.environ.get("VIDZEO_NFL_VOICE", "flux-bruce-en")

TITLE = "One Yard, One Read: The Play That Ended Super Bowl XLIX"
OUTPUT_NAME = os.environ.get("VIDZEO_NFL_TEST_OUTPUT_NAME") or (
    "nfl_test_001_super_bowl_xlix"
    if ATTEMPT_NUMBER <= 1
    else f"nfl_test_001_super_bowl_xlix_attempt_{ATTEMPT_NUMBER:03d}"
)
SCRIPT = """One yard. Twenty-six seconds. A championship balanced on one snap.

Super Bowl XLIX: Seattle trails New England, 28–24. A four-bobble catch by Jermaine Kearse keeps Seattle’s final push alive. Now the Seahawks face second-and-goal at the Patriots’ one. Marshawn Lynch waits in the backfield.

Seattle spreads out. Russell Wilson takes the snap and fires a quick slant toward Ricardo Lockette. Cornerback Malcolm Butler reads the route, cuts across Lockette’s path, and secures the interception at the goal line. The game is over.

Butler was an undrafted rookie. In practice, he had been beaten by the same play. This time, he recognized the route and attacked the passing lane.

Seattle’s choice remains one of football’s most debated calls. Coach Pete Carroll later said an incompletion would have left two more downs to run.

On February 1, 2015, one read ended Seattle’s bid for back-to-back titles and gave New England its fourth Super Bowl championship.

One yard. One read. One split second that changed the story."""

SOURCES = [
    {
        "title": "Malcolm Butler Interception in SB XLIX",
        "publisher": "NFL.com",
        "type": "league editorial reference",
        "url": "https://www.nfl.com/100/originals/100-greatest/detail.html?slug=plays-5",
        "supports": ["score, time, down and distance, route, intended receiver, interception"],
    },
    {
        "title": "Malcolm Butler: 'I knew what was going to happen'",
        "publisher": "NFL.com",
        "type": "league editorial reference",
        "url": "https://www.nfl.com/news/malcolm-butler-i-knew-what-was-going-to-happen-0ap3000000467664",
        "supports": ["Butler's preparation and prior practice rep against the same play"],
    },
    {
        "title": "Seahawks' Pete Carroll explains ill-fated call in Super Bowl XLIX",
        "publisher": "NFL.com",
        "type": "league editorial reference",
        "url": "https://www.nfl.com/news/seahawks-pete-carroll-explains-ill-fated-call-in-super-bowl-xli-0ap3000000467707",
        "supports": ["Carroll's explanation of the pass call and what an incompletion would leave"],
    },
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_redact_json(value), indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def _redact_json(value: Any) -> Any:
    """Keep auth material out of durable test artifacts while retaining all other evidence."""
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            folded = str(key).casefold()
            if any(marker in folded for marker in (
                "api_key", "apikey", "password", "secret", "access_token",
                "refresh_token", "authorization", "cookie", "auth_user_id",
            )):
                clean[key] = "[redacted]" if item else item
            else:
                clean[key] = _redact_json(item)
        return clean
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~-]{16,}", "Bearer [redacted]", value)
        value = re.sub(r"\b(?:sk-or-v1-|sk-|gsk_|AIza)[A-Za-z0-9_-]{20,}\b", "[redacted-key]", value)
    return value


def append_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"recorded_at": utc_now(), **event}, ensure_ascii=False, default=str) + "\n")


def save_server_log_delta(source: Path, offset: int, target: Path) -> dict[str, Any]:
    target.parent.mkdir(parents=True, exist_ok=True)
    if not source.is_file():
        target.write_text(f"Source log unavailable at capture: {source}\n", encoding="utf-8")
        return {"source": str(source), "available": False, "start_offset_bytes": offset}
    with source.open("rb") as handle:
        current_size = source.stat().st_size
        if current_size < offset:
            offset = 0
        handle.seek(offset)
        payload = handle.read()
    # Runtime logs are useful for diagnosis, but provider keys must never be
    # copied into the permanent artifact directory.
    safe_payload = _redact_json(payload.decode("utf-8", errors="replace")).encode("utf-8")
    target.write_bytes(safe_payload)
    return {
        "source": str(source),
        "available": True,
        "start_offset_bytes": offset,
        "end_size_bytes": source.stat().st_size,
        "captured_bytes": len(safe_payload),
        "archive": str(target),
    }


def ffprobe_summary(path: Path) -> dict[str, Any]:
    binary = shutil.which("ffprobe")
    if not binary:
        return {"status": "unavailable", "reason": "ffprobe is not on PATH"}
    completed = subprocess.run(
        [binary, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        return {"status": "failed", "returncode": completed.returncode, "stderr": completed.stderr[-4000:]}
    payload = json.loads(completed.stdout or "{}")
    streams = payload.get("streams") or []
    video = next((row for row in streams if row.get("codec_type") == "video"), {})
    audio = next((row for row in streams if row.get("codec_type") == "audio"), {})
    duration = (payload.get("format") or {}).get("duration")
    return {
        "status": "pass",
        "duration_seconds": float(duration) if duration else None,
        "format_name": (payload.get("format") or {}).get("format_name"),
        "video_codec": video.get("codec_name"),
        "width": video.get("width"),
        "height": video.get("height"),
        "frame_rate": video.get("avg_frame_rate"),
        "audio_codec": audio.get("codec_name"),
        "audio_sample_rate": audio.get("sample_rate"),
        "file_size_bytes": path.stat().st_size,
    }


def main() -> int:
    if RECORD_DIR.exists() and any(RECORD_DIR.iterdir()):
        raise RuntimeError(f"Refusing to overwrite existing test record: {RECORD_DIR}")
    RECORD_DIR.mkdir(parents=True, exist_ok=True)
    log_dir = RECORD_DIR / "logs"
    session = requests.Session()

    initial_log_offsets = {
        str(path): path.stat().st_size if path.is_file() else 0
        for path in (SERVER_LOG, SERVER_ERR_LOG)
    }
    (RECORD_DIR / "narration.txt").write_text(SCRIPT.strip() + "\n", encoding="utf-8")
    (RECORD_DIR / "sources.json").write_text(json.dumps(SOURCES, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (RECORD_DIR / "sources.md").write_text(
        "# Research references\n\n" + "\n".join(
            f"- [{item['title']}]({item['url']}) — {item['supports'][0]}." for item in SOURCES
        ) + "\n",
        encoding="utf-8",
    )
    (RECORD_DIR / "README.md").write_text(
        "# NFL Test 001 — Super Bowl XLIX\n\n"
        "This folder preserves one attempt of the NFL pipeline regression test. It is separate from `output/`, "
        "so normal output cleanup will not remove this record. The job is submitted to the running Vidzeo "
        "queue and uses the app's full research, media, LLM direction, narration, Remotion render, and QA path.\n\n"
        "## Files\n\n"
        "- `narration.txt`, `sources.md`, `sources.json`: script and factual references.\n"
        "- `request.json`: sanitized generation settings sent to the app.\n"
        "- `preflight.json`, `queue_events.jsonl`, `run_state.json`: queue check and timestamped progress.\n"
        "- `server-runtime-job.log`, `server-error-job.log`: backend log bytes appended during this run.\n"
        "- `generation_summary.json`, `final_queue_item.json`, `technical_validation.json`: final result and checks.\n"
        "- `nfl_test_001_super_bowl_xlix_<timestamp>_<id>/`: generated video, checkpoints, media, edit plan, and QA artifacts.\n\n"
        "The source notes are for factual grounding; the retrieved visual assets remain subject to their source terms.\n",
        encoding="utf-8",
    )

    script_words = len(SCRIPT.split())
    payload: dict[str, Any] = {
        "title": TITLE,
        "content_title": TITLE,
        "name": OUTPUT_NAME,
        "output_name": OUTPUT_NAME,
        "topic_anchor": "Super Bowl XLIX final play Malcolm Butler Russell Wilson Seahawks Patriots",
        "_topic_anchor": "Super Bowl XLIX final play Malcolm Butler Russell Wilson Seahawks Patriots",
        "script": SCRIPT,
        "type": "long",
        "image_source": "Mixed Media",
        "media_mode": "mixed",
        "mixed_media_policy": "automatic",
        "youtube_coverage_target": YT_COVERAGE_TARGET,
        "mixed_media_weights": {
            "Web Images": round(1.0 - YT_COVERAGE_TARGET, 4),
            "AI Images": 0.0,
            "Stock Media": 0.0,
            "YouTube Clips": YT_COVERAGE_TARGET,
        },
        "scene_mode": "Every 2 Beats",
        "aspect_ratio": "16:9",
        "resolution": "1080p",
        "quality": "high",
        "vo_mode": TTS_MODE,
        "tts_model": TTS_MODEL,
        "voice": VOICE,
        "whisper_mode": "local",
        "captions_enabled": True,
        "caption_mode": "phrase",
        "words_per_group": 4,
        "visual_understanding": True,
        "visual_required": True,
        "strict_retrieval_accuracy": _env_bool("VIDZEO_NFL_STRICT_RETRIEVAL", True),
        "fail_on_unresolved_media": _env_bool("VIDZEO_NFL_FAIL_ON_UNRESOLVED", True),
        "allow_emergency_media": _env_bool("VIDZEO_NFL_ALLOW_EMERGENCY_MEDIA", False),
        "media_fallback_policy": "grounded_graphic",
        "allow_grounded_graphic_fallback": True,
        "allow_grounded_graphic_fallback_strict": True,
        "render_backend": "remotion",
        "semantic_edit_enable": True,
        "semantic_render_enabled": True,
        "semantic_render_scale": 1.0,
        "semantic_render_crf": 18,
        "semantic_render_concurrency": 2,
        "enable_sound_library": True,
        "enable_editorial_cues": True,
        "editorial_cue_v2_enabled": True,
        "enable_motion_templates": True,
        "director_mode": True,
        "editing_personality": "automatic",
        "documentary_density": "dynamic",
        "youtube_rights_mode": "all_public",
        "allow_relevant_stock": True,
        "allow_stock_last_resort": True,
        "stock_last_resort_no_vision": False,
        "media_provider_retries": 2,
        "cross_source_fallback": True,
        "global_media_guarantee": True,
        "unique_web_assets_required": True,
        "web_collect_all_sources": True,
        "max_final_video_reuse_per_clip": 1,
        "yt_allow_same_source_reuse": True,
        "yt_download_candidate_limit": max(1, int(_env_float("VIDZEO_NFL_YT_CANDIDATE_LIMIT", 5))),
        "yt_max_download_attempts_per_scene": max(1, int(_env_float("VIDZEO_NFL_YT_ATTEMPTS_PER_SCENE", 4))),
        # Action scenes can share a verified game source at distinct moments.
        # Keep this bounded so a slow YouTube transport cannot hold rendering.
        "yt_pool_first": True,
        "yt_branch_wall_timeout_seconds": YT_WALL_TIMEOUT_SECONDS,
        "yt_hard_stop_wall_seconds": YT_WALL_TIMEOUT_SECONDS,
        "yt_pool_source_download_deadline_seconds": YT_SOURCE_DEADLINE_SECONDS,
        "yt_hard_stop_process": True,
        "research_sources": SOURCES,
        "research_fetch_sources": True,
        "research_llm_review": True,
        "research_budget_seconds": 45,
        "output_dir": str(RECORD_DIR),
    }
    write_json(RECORD_DIR / "request.json", payload)

    preflight_response = session.get(f"{BASE_URL}/api/queue/status?compact=1", timeout=20)
    preflight_response.raise_for_status()
    queue_status = preflight_response.json()
    active_jobs = queue_status.get("active_jobs") or []
    queued_items = [
        {"id": item.get("id"), "status": item.get("status"), "title": (item.get("config") or {}).get("content_title")}
        for item in (queue_status.get("items") or [])
        if item.get("status") == "queued"
    ]
    if active_jobs or queued_items:
        raise RuntimeError(f"Queue is not clear; refusing overlapping render. active={active_jobs}, queued={queued_items}")
    preflight = {
        "recorded_at": utc_now(),
        "base_url": BASE_URL,
        "server_reachable": True,
        "worker_running": queue_status.get("worker_running"),
        "worker_count": queue_status.get("worker_count"),
        "active_jobs": active_jobs,
        "queued_items": queued_items,
        "paused_historical_item_count": sum(1 for item in queue_status.get("items", []) if item.get("status") == "paused"),
        "completed_historical_item_count": sum(1 for item in queue_status.get("items", []) if item.get("status") == "completed"),
        "memory": queue_status.get("memory"),
        "server_log_offsets_bytes": initial_log_offsets,
        "script_word_count": script_words,
        "estimated_duration_seconds_at_2_6_wps": round(script_words / 2.6, 1),
    }
    write_json(RECORD_DIR / "preflight.json", preflight)
    append_event(log_dir / "queue_events.jsonl", {"event": "preflight_passed", "details": preflight})
    print(f"[NFL Test 001] Queue clear; submitting {script_words} words via {BASE_URL}/api/queue/add", flush=True)

    submitted_response = session.post(f"{BASE_URL}/api/queue/add", json=payload, timeout=60)
    submitted_response.raise_for_status()
    submitted = submitted_response.json()
    if not submitted.get("success") or not (submitted.get("item") or {}).get("id"):
        raise RuntimeError(f"Queue rejected generation: {submitted}")
    job_id = str(submitted["item"]["id"])
    append_event(log_dir / "queue_events.jsonl", {
        "event": "job_submitted",
        "job_id": job_id,
        "worker_running": submitted.get("worker_running"),
        "worker_started": submitted.get("worker_started"),
        "queue_status": submitted.get("item", {}).get("status"),
    })
    (RECORD_DIR / "job_id.txt").write_text(job_id + "\n", encoding="utf-8")
    started = time.monotonic()
    last_event_seq = 0
    last_state_signature = None
    final_item: dict[str, Any] | None = None
    print(f"[NFL Test 001] Job {job_id} submitted; monitoring app queue", flush=True)

    while time.monotonic() - started <= MAX_RUNTIME_SECONDS:
        try:
            response = session.get(f"{BASE_URL}/api/queue/item/{job_id}", timeout=30)
            response.raise_for_status()
            item = (response.json() or {}).get("item") or {}
        except requests.RequestException as exc:
            append_event(log_dir / "queue_events.jsonl", {
                "event": "monitor_request_retry",
                "error_type": type(exc).__name__,
                "error": str(exc)[:1200],
                "elapsed_seconds": round(time.monotonic() - started, 2),
            })
            time.sleep(POLL_SECONDS)
            continue

        elapsed = round(time.monotonic() - started, 2)
        for event in item.get("progress_events") or []:
            seq = int(event.get("seq") or 0)
            if seq > last_event_seq:
                append_event(log_dir / "queue_events.jsonl", {
                    "event": "pipeline_progress",
                    "job_id": job_id,
                    "elapsed_seconds": elapsed,
                    "progress_event": event,
                })
                print(f"[{elapsed:6.0f}s] {int(event.get('progress') or 0):3d}% {event.get('text') or ''}", flush=True)
                last_event_seq = seq

        state = {
            "recorded_at": utc_now(),
            "job_id": job_id,
            "elapsed_seconds": elapsed,
            "status": item.get("status"),
            "progress": item.get("progress"),
            "progress_text": item.get("progress_text"),
            "paused_reason": item.get("paused_reason"),
            "retryable": item.get("retryable"),
            "error": item.get("error"),
            "error_detail": item.get("error_detail"),
            "checkpoint_dir": item.get("checkpoint_dir"),
        }
        signature = tuple(state.get(key) for key in ("status", "progress", "progress_text", "error"))
        if signature != last_state_signature or int(elapsed) % 15 == 0:
            write_json(RECORD_DIR / "run_state.json", state)
            last_state_signature = signature

        status = str(item.get("status") or "").lower()
        if status in {"completed", "failed", "error", "cancelled", "shutdown_timeout", "paused"}:
            final_item = item
            break
        time.sleep(POLL_SECONDS)

    if final_item is None:
        raise TimeoutError(f"Job {job_id} did not reach a terminal state within {MAX_RUNTIME_SECONDS} seconds")

    write_json(RECORD_DIR / "final_queue_item.json", final_item)
    result = final_item.get("result") if isinstance(final_item.get("result"), dict) else {}
    package_value = result.get("output_package_dir") or (final_item.get("config") or {}).get("output_package_dir") or (final_item.get("config") or {}).get("_output_package_dir")
    package_dir = Path(str(package_value)).resolve() if package_value else None
    video_value = result.get("video_path") or result.get("video") or result.get("output_path")
    video_path = Path(str(video_value)).resolve() if video_value else None
    component_manifest = result.get("component_manifest") if isinstance(result.get("component_manifest"), dict) else {}
    # Production stores render metadata inside component_manifest. Older and
    # some test adapters expose it at the result root, so accept both shapes.
    render_metadata = result.get("render_metadata") or component_manifest.get("render_metadata") or {}
    if not isinstance(render_metadata, dict):
        render_metadata = {}
    quality = render_metadata.get("quality_checks") or render_metadata.get("quality") or {}
    if not isinstance(quality, dict):
        quality = {}
    record_status = str(final_item.get("status") or "unknown").lower()
    append_event(log_dir / "queue_events.jsonl", {
        "event": "terminal_state",
        "job_id": job_id,
        "status": record_status,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "video_path": str(video_path) if video_path else None,
        "package_dir": str(package_dir) if package_dir else None,
        "error": final_item.get("error"),
    })

    runtime_capture = save_server_log_delta(SERVER_LOG, initial_log_offsets[str(SERVER_LOG)], RECORD_DIR / "logs" / "server-runtime-job.log")
    stderr_capture = save_server_log_delta(SERVER_ERR_LOG, initial_log_offsets[str(SERVER_ERR_LOG)], RECORD_DIR / "logs" / "server-error-job.log")
    write_json(RECORD_DIR / "log_capture.json", {"server_runtime": runtime_capture, "server_stderr": stderr_capture})

    validation: dict[str, Any] = {
        "checked_at": utc_now(),
        "job_id": job_id,
        "queue_status": record_status,
        "pipeline_success": bool(result.get("success")),
        "backend": render_metadata.get("selected_backend"),
        "renderer": (render_metadata.get("renderer") or render_metadata.get("selected_backend") or ""),
        "render_status": render_metadata.get("status"),
        "quality_check_status": quality.get("status"),
        "quality_checks": quality,
        "video_path": str(video_path) if video_path else None,
        "video_exists": bool(video_path and video_path.is_file()),
        "manifest_path": result.get("manifest_path"),
        "package_dir": str(package_dir) if package_dir else None,
        "package_exists": bool(package_dir and package_dir.is_dir()),
        "duration_seconds": result.get("duration"),
        "research_ledger": component_manifest.get("research_ledger"),
        "director": render_metadata.get("director") or component_manifest.get("director"),
        "edit_plan_validation": render_metadata.get("edit_plan_validation"),
        "creative_review": render_metadata.get("creative_review"),
        "release_review": render_metadata.get("release_review"),
        "sound_audit": component_manifest.get("sound_audit") or (component_manifest.get("remotion_manifest") or {}).get("sound_audit"),
        "sequence_audit": component_manifest.get("sequence_audit") or (component_manifest.get("remotion_manifest") or {}).get("sequence_audit"),
        "source_accounting": component_manifest.get("source_accounting"),
        "terminal_accounting": component_manifest.get("terminal_accounting"),
        "integrity_degraded_scene_indices": component_manifest.get("integrity_degraded_scene_indices"),
        "integrity_degraded_warning": component_manifest.get("integrity_degraded_warning"),
        "youtube_coverage": component_manifest.get("youtube_coverage"),
        "youtube_coverage_warning": component_manifest.get("youtube_coverage_warning"),
    }
    if video_path and video_path.is_file():
        validation["media_probe"] = ffprobe_summary(video_path)
    else:
        validation["media_probe"] = {"status": "not_run", "reason": "No completed video file was returned"}
    write_json(RECORD_DIR / "technical_validation.json", validation)

    summary = {
        "test_id": "NFL Test 001",
        "attempt": ATTEMPT_NUMBER,
        "title": TITLE,
        "topic": "The final play of Super Bowl XLIX",
        "script_word_count": script_words,
        "job_id": job_id,
        "queue_status": record_status,
        "success": bool(result.get("success")) and bool(video_path and video_path.is_file()),
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "video_path": str(video_path) if video_path else None,
        "package_dir": str(package_dir) if package_dir else None,
        "manifest_path": result.get("manifest_path"),
        "duration_seconds": result.get("duration"),
        "backend": render_metadata.get("selected_backend"),
        "quality_check_status": quality.get("status"),
        "director_label": render_metadata.get("director_label") or (render_metadata.get("director") or {}).get("model"),
        "archive_dir": str(RECORD_DIR),
    }
    write_json(RECORD_DIR / "generation_summary.json", summary)
    print("[NFL Test 001] Terminal record saved:", json.dumps(summary, indent=2, ensure_ascii=False), flush=True)
    return 0 if summary["success"] and record_status == "completed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        RECORD_DIR.mkdir(parents=True, exist_ok=True)
        append_event(RECORD_DIR / "logs" / "queue_events.jsonl", {
            "event": "runner_error",
            "error_type": type(exc).__name__,
            "error": str(exc)[:3000],
        })
        write_json(RECORD_DIR / "runner_error.json", {
            "recorded_at": utc_now(),
            "error_type": type(exc).__name__,
            "error": str(exc),
        })
        raise
