"""Resume the NFL regression job with grounded-graphic and niche-mix fixes."""

from __future__ import annotations

import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import run_nfl_test_video_001 as test_runner  # noqa: E402

BASE_URL = "http://127.0.0.1:5000"
JOB_ID = "4176417c"
RECORD_DIR = ROOT / "artifacts" / "nfl_test_001_20261002" / "attempts" / "attempt_014"
MAX_RUNTIME_SECONDS = 2100
POLL_SECONDS = 3


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append_event(value: dict) -> None:
    test_runner.append_event(
        RECORD_DIR / "logs" / "queue_events.jsonl",
        test_runner._redact_json(value),
    )


def main() -> int:
    if RECORD_DIR.exists() and any(RECORD_DIR.iterdir()):
        raise RuntimeError(f"Refusing to overwrite attempt record: {RECORD_DIR}")
    RECORD_DIR.mkdir(parents=True, exist_ok=True)
    (RECORD_DIR / "README.md").write_text(
        "# NFL Test 001 — Attempt 014\n\n"
        "This retry continues the saved media and narration package from attempt 013. "
        "It preserves prior attempts and records grounded-graphic provenance validation, "
        "the sports-specific 30% YouTube target, "
        "queue events, backend log delta, render output, and technical validation here.\n",
        encoding="utf-8",
    )
    session = requests.Session()
    queue_response = session.get(f"{BASE_URL}/api/queue/status?compact=1", timeout=20)
    queue_response.raise_for_status()
    queue = queue_response.json()
    active = queue.get("active_jobs") or []
    queued = [
        {"id": row.get("id"), "status": row.get("status")}
        for row in (queue.get("items") or [])
        if row.get("status") == "queued"
    ]
    if active or queued:
        raise RuntimeError(f"Queue is not exclusive: active={active!r}, queued={queued!r}")

    item_response = session.get(f"{BASE_URL}/api/queue/item/{JOB_ID}", timeout=20)
    item_response.raise_for_status()
    item = (item_response.json() or {}).get("item") or {}
    if item.get("status") != "paused" or not item.get("retryable"):
        raise RuntimeError(f"Expected retryable saved job; status={item.get('status')!r}.")
    config = item.get("config") or {}
    package_value = config.get("_output_package_dir") or config.get("output_package_dir") or ""
    package_dir = Path(str(package_value)).resolve() if package_value else None
    if not package_dir or not package_dir.is_dir():
        raise RuntimeError("The saved attempt 012 package is missing; preserving this retry without recreating its evidence.")
    previous_package_dir = package_dir
    package_dir = RECORD_DIR / "saved_package_snapshot"
    shutil.copytree(previous_package_dir, package_dir)
    new_output_name = "nfl_test_001_super_bowl_xlix_attempt_014"
    update_response = session.post(
        f"{BASE_URL}/api/queue/update",
        json={
            "id": JOB_ID,
            "updates": {
                "config": {
                    "output_dir": str(package_dir),
                    "output_package_dir": str(package_dir),
                    "_output_package_dir": str(package_dir),
                    "output_name": new_output_name,
                    "name": new_output_name,
                    # This is a sports documentary: reserve clips for the
                    # actual play/action beats and keep verified stills for
                    # context, research, and explanation.
                    "youtube_coverage_target": 0.30,
                    "mixed_media_weights": {
                        "Web Images": 0.70,
                        "AI Images": 0.0,
                        "Stock Media": 0.0,
                        "YouTube Clips": 0.30,
                    },
                    "yt_branch_wall_timeout_seconds": 180,
                },
            },
        },
        timeout=30,
    )
    update_response.raise_for_status()
    append_event({
        "event": "saved_package_snapshotted_and_retry_routed",
        "job_id": JOB_ID,
        "previous_package_dir": str(previous_package_dir),
        "retry_package_dir": str(package_dir),
        "output_name": new_output_name,
    })
    offsets = {
        str(path): path.stat().st_size if path.is_file() else 0
        for path in (test_runner.SERVER_LOG, test_runner.SERVER_ERR_LOG)
    }
    test_runner.write_json(RECORD_DIR / "preflight.json", {
        "recorded_at": now(),
        "job_id": JOB_ID,
        "resume_from_status": item.get("status"),
        "prior_error": item.get("error"),
        "previous_package_dir": str(previous_package_dir),
        "package_dir": str(package_dir),
        "queue_clear": True,
        "active_jobs": active,
        "queued_jobs": queued,
        "backend_worker_running": queue.get("worker_running"),
        "log_offsets_bytes": offsets,
        "fallback_policy": config.get("media_fallback_policy"),
        "grounded_graphic_fallback_enabled": config.get("allow_grounded_graphic_fallback"),
    })
    append_event({"event": "saved_job_retry_preflight_passed", "job_id": JOB_ID, "package_dir": str(package_dir or "")})

    resume_response = session.post(f"{BASE_URL}/api/queue/resume", json={"id": JOB_ID}, timeout=30)
    resume_response.raise_for_status()
    resumed = resume_response.json()
    append_event({"event": "saved_job_resume_requested", "job_id": JOB_ID, "response": resumed})
    if resumed.get("needs_start"):
        start_response = session.post(f"{BASE_URL}/api/queue/start", json={}, timeout=30)
        start_response.raise_for_status()
        append_event({"event": "queue_worker_started", "response": start_response.json()})

    started = time.monotonic()
    last_seq = int(item.get("progress_seq") or 0)
    previous = None
    terminal = None
    while time.monotonic() - started < MAX_RUNTIME_SECONDS:
        response = session.get(f"{BASE_URL}/api/queue/item/{JOB_ID}", timeout=25)
        response.raise_for_status()
        current = (response.json() or {}).get("item") or {}
        elapsed = round(time.monotonic() - started, 1)
        for event in current.get("progress_events") or []:
            seq = int(event.get("seq") or 0) if isinstance(event, dict) else 0
            if seq > last_seq:
                append_event({"event": "pipeline_progress", "job_id": JOB_ID, "elapsed_seconds": elapsed, "progress_event": event})
                last_seq = seq
        signature = (current.get("status"), current.get("progress"), current.get("progress_text"))
        if signature != previous:
            previous = signature
            print(f"[{elapsed:6.0f}s] status={signature[0]} progress={signature[1]} {signature[2] or ''}", flush=True)
        if str(current.get("status") or "").lower() in {"completed", "complete", "failed", "error", "cancelled", "paused", "shutdown_timeout"}:
            terminal = current
            break
        time.sleep(POLL_SECONDS)

    terminal = terminal or (session.get(f"{BASE_URL}/api/queue/item/{JOB_ID}", timeout=20).json() or {}).get("item") or {}
    clean_terminal = test_runner._redact_json(terminal)
    test_runner.write_json(RECORD_DIR / "final_queue_item.json", clean_terminal)
    result = terminal.get("result") if isinstance(terminal.get("result"), dict) else {}
    package_value = result.get("output_package_dir") or (terminal.get("config") or {}).get("output_package_dir") or (terminal.get("config") or {}).get("_output_package_dir")
    package_dir = Path(str(package_value)).resolve() if package_value else package_dir
    video_value = result.get("video_path") or result.get("video") or result.get("output_path")
    video_path = Path(str(video_value)).resolve() if video_value else None
    if not video_path or not video_path.is_file():
        candidates = list(package_dir.rglob("nfl_test_001_super_bowl_xlix*.mp4")) if package_dir and package_dir.is_dir() else []
        video_path = max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None

    for source, filename in (
        (test_runner.SERVER_LOG, "backend-runtime-attempt_014.log"),
        (test_runner.SERVER_ERR_LOG, "backend-stderr-attempt_014.log"),
    ):
        capture = test_runner.save_server_log_delta(source, offsets[str(source)], RECORD_DIR / "logs" / filename)
        append_event({"event": "server_log_captured", "source": str(source), "capture": capture})

    render_metadata = result.get("render_metadata") or {}
    quality = render_metadata.get("quality_checks") or {}
    validation = {
        "checked_at": now(),
        "job_id": JOB_ID,
        "status": terminal.get("status"),
        "progress": terminal.get("progress"),
        "error": terminal.get("error"),
        "paused_reason": terminal.get("paused_reason"),
        "video_path": str(video_path) if video_path else None,
        "video_exists": bool(video_path and video_path.is_file()),
        "package_dir": str(package_dir) if package_dir else None,
        "manifest_path": result.get("manifest_path"),
        "duration_seconds": result.get("duration"),
        "renderer": render_metadata.get("renderer"),
        "backend": render_metadata.get("selected_backend"),
        "quality_check_status": quality.get("status"),
        "sound_audit": (result.get("component_manifest") or {}).get("sound_audit"),
        "sequence_audit": (result.get("component_manifest") or {}).get("sequence_audit"),
        "elapsed_seconds": round(time.monotonic() - started, 1),
    }
    if video_path and video_path.is_file():
        validation["media_probe"] = test_runner.ffprobe_summary(video_path)
    else:
        validation["media_probe"] = {"status": "not_run", "reason": "No completed video returned."}
    test_runner.write_json(RECORD_DIR / "technical_validation.json", validation)
    success = bool(result.get("success")) and bool(video_path and video_path.is_file()) and str(terminal.get("status") or "").lower() == "completed"
    summary = {
        "test_id": "NFL Test 001",
        "attempt": 14,
        "job_id": JOB_ID,
        "queue_status": terminal.get("status"),
        "success": success,
        "elapsed_seconds": validation["elapsed_seconds"],
        "video_path": str(video_path) if video_path else None,
        "package_dir": str(package_dir) if package_dir else None,
        "quality_check_status": quality.get("status"),
        "archive_dir": str(RECORD_DIR),
    }
    test_runner.write_json(RECORD_DIR / "generation_summary.json", summary)
    print("[NFL Test 001 / Attempt 014]", json.dumps(summary, indent=2), flush=True)
    return 0 if success else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        RECORD_DIR.mkdir(parents=True, exist_ok=True)
        test_runner.write_json(RECORD_DIR / "runner_error.json", {"error_type": type(exc).__name__, "error": str(exc)})
        append_event({"event": "attempt_014_runner_error", "error_type": type(exc).__name__, "error": str(exc)[:3000]})
        raise
