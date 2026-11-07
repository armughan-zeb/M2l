"""Continue NFL Test 001 after archiving its prior media-gate attempt."""

from __future__ import annotations

import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from run_nfl_test_video_001 import BASE_URL, RECORD_DIR, SCRIPT, ffprobe_summary


ROOT = SCRIPT_DIR.parent
ATTEMPT_NO = 7
ATTEMPT_DIR = RECORD_DIR / "attempts" / "attempt_007"
SERVER_LOG = ATTEMPT_DIR / "logs" / "server-runtime.log"
SERVER_ERR_LOG = ATTEMPT_DIR / "logs" / "server-error.log"
POLL_SECONDS = 3
MAX_RUNTIME_SECONDS = 2400


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def append_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"recorded_at": utc_now(), **event}, ensure_ascii=False, default=str) + "\n")


def capture_log_delta(source: Path, offset: int, target: Path) -> dict[str, Any]:
    target.parent.mkdir(parents=True, exist_ok=True)
    if not source.is_file():
        target.write_text(f"Source log unavailable: {source}\n", encoding="utf-8")
        return {"source": str(source), "available": False, "start_offset_bytes": offset}
    with source.open("rb") as handle:
        end_size = source.stat().st_size
        if end_size < offset:
            offset = 0
        handle.seek(offset)
        data = handle.read()
    target.write_bytes(data)
    return {
        "source": str(source),
        "available": True,
        "start_offset_bytes": offset,
        "end_size_bytes": source.stat().st_size,
        "captured_bytes": len(data),
        "archive": str(target),
    }


def archive_first_attempt(package_dir: Path) -> None:
    attempt_one = RECORD_DIR / "attempts" / "attempt_001"
    if attempt_one.exists():
        return
    attempt_one.mkdir(parents=True, exist_ok=True)
    for name in (
        "request.json", "narration.txt", "sources.json", "sources.md", "preflight.json",
        "job_id.txt", "run_state.json", "final_queue_item.json", "generation_summary.json",
        "technical_validation.json", "log_capture.json",
    ):
        source = RECORD_DIR / name
        if source.is_file():
            shutil.copy2(source, attempt_one / source.name)
    source_logs = RECORD_DIR / "logs"
    if source_logs.is_dir():
        shutil.copytree(source_logs, attempt_one / "logs", dirs_exist_ok=True)
    if package_dir.is_dir():
        shutil.copytree(package_dir, attempt_one / "package", dirs_exist_ok=True)
    temp_jobs = ROOT / "temp" / "jobs" / (RECORD_DIR / "job_id.txt").read_text(encoding="utf-8").strip()
    if temp_jobs.is_dir():
        shutil.copytree(temp_jobs, attempt_one / "temp_job", dirs_exist_ok=True)
    audit_source = ROOT / "temp" / "manifests"
    audit_target = attempt_one / "temp_manifests"
    for audit in audit_source.glob(f"*{(RECORD_DIR / 'job_id.txt').read_text(encoding='utf-8').strip()}*"):
        if audit.is_file():
            audit_target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(audit, audit_target / audit.name)
    write_json(attempt_one / "attempt_summary.json", {
        "attempt": 1,
        "status": "paused",
        "reason": "media_contract",
        "root_cause": "Football use of the word 'drive' was misclassified as automotive domain evidence; the visual gate then rejected correct NFL imagery.",
        "preserved_at": utc_now(),
        "package_dir": str(package_dir),
        "archive_contains_package_and_temp_evidence": True,
    })


def archive_previous_attempt(attempt_no: int, item: dict[str, Any], package_dir: Path, job_id: str) -> None:
    """Snapshot the most recent failed attempt before the retry reuses its job."""
    prior = RECORD_DIR / "attempts" / f"attempt_{attempt_no:03d}"
    prior.mkdir(parents=True, exist_ok=True)
    snapshot = prior / "retry_snapshot"
    for name in (
        "request.json", "narration.txt", "preflight.json", "queue_events.jsonl",
        "final_queue_item.json", "generation_summary.json", "technical_validation.json",
    ):
        source = prior / name
        if source.is_file():
            target = snapshot / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    if package_dir.is_dir():
        shutil.copytree(package_dir, prior / "package", dirs_exist_ok=True)

    job_root = ROOT / "temp" / "jobs" / job_id
    run_id = str(item.get("last_run_id") or item.get("active_run_id") or "")
    run_root = job_root / run_id if run_id else None
    if run_root is None or not run_root.is_dir():
        candidates = sorted(
            (path for path in job_root.glob(f"{job_id}-run-*") if path.is_dir()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        ) if job_root.is_dir() else []
        run_root = candidates[0] if candidates else None
    if run_root and run_root.is_dir():
        shutil.copytree(run_root, prior / "temp_job", dirs_exist_ok=True)
        audit_target = prior / "temp_manifests"
        audit_target.mkdir(parents=True, exist_ok=True)
        for audit in (ROOT / "temp" / "manifests").glob(f"*{run_root.name}*"):
            if audit.is_file():
                shutil.copy2(audit, audit_target / audit.name)
    write_json(prior / "archive_before_retry.json", {
        "recorded_at": utc_now(),
        "attempt": attempt_no,
        "run_id": run_id or (run_root.name if run_root else None),
        "run_root": str(run_root) if run_root else None,
        "package_dir": str(package_dir),
        "archived_before_retry": True,
    })


def main() -> int:
    session = requests.Session()
    job_id_path = RECORD_DIR / "job_id.txt"
    if not job_id_path.is_file():
        raise FileNotFoundError(f"Missing first-attempt job id: {job_id_path}")
    job_id = job_id_path.read_text(encoding="utf-8").strip()
    item_response = session.get(f"{BASE_URL}/api/queue/item/{job_id}", timeout=20)
    item_response.raise_for_status()
    item = (item_response.json() or {}).get("item") or {}
    if item.get("status") not in {"paused", "error"}:
        raise RuntimeError(f"Retry expects the same paused/error job; found status={item.get('status')!r}")
    package_dir = Path(str((item.get("config") or {}).get("output_package_dir") or "")).resolve()
    archive_first_attempt(package_dir)
    archive_previous_attempt(ATTEMPT_NO - 1, item, package_dir, job_id)
    ATTEMPT_DIR.mkdir(parents=True, exist_ok=True)

    payload = json.loads((RECORD_DIR / "request.json").read_text(encoding="utf-8"))
    payload["script"] = SCRIPT
    payload["topic_anchor"] = "Super Bowl XLIX final play Malcolm Butler Russell Wilson Seahawks Patriots"
    payload["_topic_anchor"] = payload["topic_anchor"]
    payload["delivery_budget_seconds"] = 1800
    payload["render_reserve_seconds"] = 300
    # Missing archival shots may use an explicitly labelled, fact-grounded
    # graphic. This does not allow blank emergency plates or unverified footage.
    payload["media_fallback_policy"] = "grounded_graphic"
    payload["allow_grounded_graphic_fallback"] = True
    payload["allow_grounded_graphic_fallback_strict"] = True
    payload["allow_emergency_media"] = False
    write_json(RECORD_DIR / "request.json", payload)
    (RECORD_DIR / "narration.txt").write_text(SCRIPT.strip() + "\n", encoding="utf-8")
    (ATTEMPT_DIR / "narration.txt").write_text(SCRIPT.strip() + "\n", encoding="utf-8")
    write_json(ATTEMPT_DIR / "request.json", payload)

    status_response = session.get(f"{BASE_URL}/api/queue/status?compact=1", timeout=20)
    status_response.raise_for_status()
    queue_status = status_response.json()
    active_jobs = queue_status.get("active_jobs") or []
    queued_jobs = [row.get("id") for row in queue_status.get("items", []) if row.get("status") == "queued"]
    if active_jobs or any(value != job_id for value in queued_jobs):
        raise RuntimeError(f"Queue must be exclusive before retry; active={active_jobs}, other_queued={queued_jobs}")
    offsets = {
        str(path): path.stat().st_size if path.is_file() else 0
        for path in (SERVER_LOG, SERVER_ERR_LOG)
    }
    write_json(ATTEMPT_DIR / "preflight.json", {
        "recorded_at": utc_now(),
        "job_id": job_id,
        "queue_exclusive": True,
        "worker_running": queue_status.get("worker_running"),
        "active_jobs": active_jobs,
        "queued_jobs": queued_jobs,
        "memory": queue_status.get("memory"),
        "server_log_offsets_bytes": offsets,
        "change_from_attempt_006": "Keep repeated-pixel release blocking, but pass exhausted duplicate-shot findings into the scene-local creative review so it can compose a grounded Remotion graphic; saved AV1 inputs remain H.264-normalized with source-hash provenance.",
    })
    append_event(ATTEMPT_DIR / "queue_events.jsonl", {
        "event": "retry_preflight_passed",
        "job_id": job_id,
        "queue_status": item.get("status"),
        "active_jobs": active_jobs,
    })

    update_response = session.post(
        f"{BASE_URL}/api/queue/update",
        json={"id": job_id, "updates": {"config": {
            "script": SCRIPT,
            "topic_anchor": payload["topic_anchor"],
            "_topic_anchor": payload["_topic_anchor"],
            "delivery_budget_seconds": 1800,
            "render_reserve_seconds": 300,
            "media_fallback_policy": "grounded_graphic",
            "allow_grounded_graphic_fallback": True,
            "allow_grounded_graphic_fallback_strict": True,
            "allow_emergency_media": False,
        }}},
        timeout=30,
    )
    update_response.raise_for_status()
    append_event(ATTEMPT_DIR / "queue_events.jsonl", {"event": "paused_job_config_updated", "http_status": update_response.status_code})
    resume_response = session.post(f"{BASE_URL}/api/queue/resume", json={"id": job_id}, timeout=30)
    resume_response.raise_for_status()
    resume_result = resume_response.json()
    if resume_result.get("needs_start"):
        start_response = session.post(f"{BASE_URL}/api/queue/start", json={}, timeout=30)
        start_response.raise_for_status()
    append_event(ATTEMPT_DIR / "queue_events.jsonl", {
        "event": "job_resumed",
        "job_id": job_id,
        "resume_response": resume_result,
    })
    print(f"[NFL Test 001 / attempt {ATTEMPT_NO}] Resumed job {job_id}; previous attempts are archived and grounded graphics are enabled for missing evidence-only beats.", flush=True)

    started = time.monotonic()
    last_seq = 0
    final_item: dict[str, Any] | None = None
    last_signature = None
    while time.monotonic() - started <= MAX_RUNTIME_SECONDS:
        try:
            response = session.get(f"{BASE_URL}/api/queue/item/{job_id}", timeout=30)
            response.raise_for_status()
            item = (response.json() or {}).get("item") or {}
        except requests.RequestException as exc:
            append_event(ATTEMPT_DIR / "queue_events.jsonl", {"event": "monitor_retry", "error": str(exc)[:1200]})
            time.sleep(POLL_SECONDS)
            continue
        elapsed = round(time.monotonic() - started, 2)
        for event in item.get("progress_events") or []:
            seq = int(event.get("seq") or 0)
            if seq > last_seq:
                append_event(ATTEMPT_DIR / "queue_events.jsonl", {
                    "event": "pipeline_progress",
                    "elapsed_seconds": elapsed,
                    "progress_event": event,
                })
                print(f"[{elapsed:6.0f}s] {int(event.get('progress') or 0):3d}% {event.get('text') or ''}", flush=True)
                last_seq = seq
        signature = tuple(item.get(key) for key in ("status", "progress", "progress_text", "error"))
        if signature != last_signature or int(elapsed) % 15 == 0:
            write_json(RECORD_DIR / "run_state.json", {
                "recorded_at": utc_now(),
                "attempt": ATTEMPT_NO,
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
            })
            last_signature = signature
        if str(item.get("status") or "").lower() in {"completed", "failed", "error", "cancelled", "shutdown_timeout", "paused"}:
            final_item = item
            break
        time.sleep(POLL_SECONDS)
    if final_item is None:
        raise TimeoutError(f"Retry attempt did not reach a terminal state within {MAX_RUNTIME_SECONDS} seconds")

    write_json(RECORD_DIR / "final_queue_item.json", final_item)
    write_json(ATTEMPT_DIR / "final_queue_item.json", final_item)
    result = final_item.get("result") if isinstance(final_item.get("result"), dict) else {}
    video_value = result.get("video_path") or result.get("video") or result.get("output_path")
    video_path = Path(str(video_value)).resolve() if video_value else None
    package_value = result.get("output_package_dir") or (final_item.get("config") or {}).get("output_package_dir")
    package_dir = Path(str(package_value)).resolve() if package_value else None
    render = result.get("render_metadata") or {}
    quality = render.get("quality_checks") or {}
    media_probe = ffprobe_summary(video_path) if video_path and video_path.is_file() else {"status": "not_run"}
    terminal_status = str(final_item.get("status") or "unknown").lower()
    summary = {
        "test_id": "NFL Test 001",
        "attempt": ATTEMPT_NO,
        "title": payload.get("title"),
        "job_id": job_id,
        "queue_status": terminal_status,
        "success": bool(result.get("success")) and bool(video_path and video_path.is_file()) and terminal_status == "completed",
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "video_path": str(video_path) if video_path else None,
        "package_dir": str(package_dir) if package_dir else None,
        "manifest_path": result.get("manifest_path"),
        "duration_seconds": result.get("duration"),
        "backend": render.get("selected_backend"),
        "quality_check_status": quality.get("status"),
        "media_probe": media_probe,
        "error": final_item.get("error"),
        "archive_dir": str(RECORD_DIR),
    }
    write_json(ATTEMPT_DIR / "generation_summary.json", summary)
    write_json(RECORD_DIR / "generation_summary.json", summary)
    write_json(ATTEMPT_DIR / "technical_validation.json", {
        "recorded_at": utc_now(),
        "queue_status": terminal_status,
        "success": summary["success"],
        "video_exists": bool(video_path and video_path.is_file()),
        "manifest_path": result.get("manifest_path"),
        "quality_checks": quality,
        "media_probe": media_probe,
    })
    capture_log_delta(SERVER_LOG, offsets[str(SERVER_LOG)], ATTEMPT_DIR / "logs" / "server-runtime-job.log")
    capture_log_delta(SERVER_ERR_LOG, offsets[str(SERVER_ERR_LOG)], ATTEMPT_DIR / "logs" / "server-error-job.log")
    append_event(ATTEMPT_DIR / "queue_events.jsonl", {"event": "terminal_state", "summary": summary})
    index_path = RECORD_DIR / "attempts" / "index.json"
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        index = {"test_id": "NFL Test 001", "attempts": []}
    attempts = [
        row for row in (index.get("attempts") or [])
        if int(row.get("attempt") or 0) != ATTEMPT_NO
    ]
    attempts.append({
        "attempt": ATTEMPT_NO,
        "status": terminal_status,
        "reason": final_item.get("error"),
        "folder": ATTEMPT_DIR.name,
    })
    write_json(index_path, {
        "test_id": "NFL Test 001",
        "attempts": sorted(attempts, key=lambda row: int(row.get("attempt") or 0)),
    })
    print(f"[NFL Test 001 / attempt {ATTEMPT_NO}] Final record:", json.dumps(summary, indent=2, ensure_ascii=False), flush=True)
    return 0 if summary["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
