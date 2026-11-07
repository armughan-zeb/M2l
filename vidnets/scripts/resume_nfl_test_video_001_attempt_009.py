"""Resume the checkpointed first NFL documentary test and archive all evidence."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
RECORD_DIR = ROOT / "artifacts" / "nfl_test_001_20261002"
ATTEMPT_DIR = RECORD_DIR / "attempts" / "attempt_009"
BOOT_LOG_DIR = RECORD_DIR / "attempts" / "attempt_009_boot" / "logs"
BASE_URL = "http://127.0.0.1:5000"
MAX_RUNTIME_SECONDS = 2700
POLL_SECONDS = 4


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"recorded_at": now(), **(value if isinstance(value, dict) else {"value": value})}, ensure_ascii=False, default=str) + "\n")


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            folded = str(key).casefold()
            if any(secret in folded for secret in ("api_key", "apikey", "password", "secret", "access_token", "cookie")):
                clean[key] = "[redacted]" if item else item
            else:
                clean[key] = redact(item)
        return clean
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def append_log_delta(source: Path, target: Path, offsets: dict[str, int]) -> None:
    key = str(source)
    if not source.is_file():
        return
    start = offsets.get(key, 0)
    size = source.stat().st_size
    if size < start:
        start = 0
    with source.open("rb") as stream:
        stream.seek(start)
        data = stream.read()
    if data:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("ab") as stream:
            stream.write(data)
        offsets[key] = start + len(data)


def probe_video(path: Path) -> dict[str, Any]:
    sys.path.insert(0, str(ROOT / "server"))
    from services.media_integrity import resolve_ffmpeg_pair

    _ffmpeg, ffprobe = resolve_ffmpeg_pair(ROOT)
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"ffprobe failed ({result.returncode}): {result.stderr[-1600:]}")
    payload = json.loads(result.stdout or "{}")
    streams = payload.get("streams") or []
    video = next((row for row in streams if row.get("codec_type") == "video"), {})
    audio = next((row for row in streams if row.get("codec_type") == "audio"), {})
    return {
        "path": str(path),
        "duration_seconds": float((payload.get("format") or {}).get("duration") or 0.0),
        "video_codec": video.get("codec_name"),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "audio_codec": audio.get("codec_name"),
        "has_audio": bool(audio),
        "video_stream_count": sum(1 for row in streams if row.get("codec_type") == "video"),
        "audio_stream_count": sum(1 for row in streams if row.get("codec_type") == "audio"),
        "size_bytes": path.stat().st_size,
    }


def extract_review_frames(video: Path, duration: float) -> list[str]:
    sys.path.insert(0, str(ROOT / "server"))
    from services.media_integrity import resolve_ffmpeg_pair

    ffmpeg, _ffprobe = resolve_ffmpeg_pair(ROOT)
    qa_dir = ATTEMPT_DIR / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    stamps = [min(1.0, duration / 10.0), duration / 2.0, max(0.0, duration - 1.0)]
    outputs = []
    for label, stamp in zip(("opening", "midpoint", "ending"), stamps):
        target = qa_dir / f"{label}.jpg"
        result = subprocess.run(
            [ffmpeg, "-y", "-ss", f"{stamp:.3f}", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(target)],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        if result.returncode or not target.is_file():
            raise RuntimeError(f"Could not extract {label} review frame: {result.stderr[-1000:]}")
        outputs.append(str(target))
    return outputs


def main() -> int:
    session = requests.Session()
    job_id_file = ATTEMPT_DIR / "job_id.txt"
    if not job_id_file.is_file():
        job_id_file.write_text("9a4fc5df\n", encoding="utf-8")
    job_id = job_id_file.read_text(encoding="utf-8").strip()
    if job_id != "9a4fc5df":
        raise RuntimeError(f"This preserved checkpoint expects 9a4fc5df, found {job_id!r}.")

    item_response = session.get(f"{BASE_URL}/api/queue/item/{job_id}", timeout=20)
    item_response.raise_for_status()
    item = (item_response.json() or {}).get("item") or {}
    if item.get("status") != "paused" or not item.get("retryable"):
        raise RuntimeError(f"Expected the paused retryable NFL checkpoint; status={item.get('status')!r}.")

    queue = session.get(f"{BASE_URL}/api/queue/status?compact=1", timeout=20).json()
    active = queue.get("active_jobs") or []
    other_queued = [row.get("id") for row in queue.get("items") or [] if row.get("status") == "queued" and row.get("id") != job_id]
    if active or other_queued:
        raise RuntimeError(f"Queue is not exclusive: active={active!r}, other queued={other_queued!r}.")
    append_jsonl(ATTEMPT_DIR / "queue_events.jsonl", {
        "event": "checkpoint_retry_preflight_passed",
        "job_id": job_id,
        "status": item.get("status"),
        "active_jobs": active,
        "other_queued_jobs": other_queued,
        "worker_running": queue.get("worker_running"),
    })
    write_json(ATTEMPT_DIR / "preflight.json", {
        "recorded_at": now(),
        "job_id": job_id,
        "queue_exclusive": True,
        "backend_worker_running": queue.get("worker_running"),
        "worker_count": queue.get("worker_count"),
        "resume_mode": "saved_remotion_plan_and_media_checkpoint",
        "prior_attempt_snapshot": str(ATTEMPT_DIR / "snapshot_attempt_008"),
    })

    offsets: dict[str, int] = {}
    log_sources = {
        "runtime": BOOT_LOG_DIR / "server-runtime.log",
        "stdout": BOOT_LOG_DIR / "server-process-stdout.log",
        "stderr": BOOT_LOG_DIR / "server-process-stderr.log",
    }
    log_targets = {key: ATTEMPT_DIR / "logs" / f"backend-{key}-attempt_009.log" for key in log_sources}
    for key, source in log_sources.items():
        offsets[str(source)] = source.stat().st_size if source.is_file() else 0

    resume = session.post(f"{BASE_URL}/api/queue/resume", json={"id": job_id}, timeout=30)
    resume.raise_for_status()
    resume_result = resume.json()
    append_jsonl(ATTEMPT_DIR / "queue_events.jsonl", {"event": "job_resume_requested", "job_id": job_id, "response": resume_result})
    if resume_result.get("needs_start"):
        start = session.post(f"{BASE_URL}/api/queue/start", json={}, timeout=30)
        start.raise_for_status()
        append_jsonl(ATTEMPT_DIR / "queue_events.jsonl", {"event": "queue_worker_started", "response": start.json()})

    started = time.monotonic()
    seen_seq = 0
    previous_progress = None
    terminal: dict[str, Any] | None = None
    while time.monotonic() - started < MAX_RUNTIME_SECONDS:
        for key, source in log_sources.items():
            append_log_delta(source, log_targets[key], offsets)
        try:
            current_response = session.get(f"{BASE_URL}/api/queue/item/{job_id}", timeout=25)
            current_response.raise_for_status()
            current = (current_response.json() or {}).get("item") or {}
        except requests.RequestException as exc:
            append_jsonl(ATTEMPT_DIR / "queue_events.jsonl", {"event": "monitor_retry", "error": f"{type(exc).__name__}: {exc}"})
            time.sleep(POLL_SECONDS)
            continue
        for event in current.get("progress_events") or []:
            seq = int(event.get("seq") or 0) if isinstance(event, dict) else 0
            if seq > seen_seq:
                append_jsonl(ATTEMPT_DIR / "queue_events.jsonl", {"event": "pipeline_progress", **event})
                seen_seq = seq
        signature = (current.get("status"), current.get("progress"), current.get("progress_text"))
        if signature != previous_progress:
            previous_progress = signature
            print(f"[{now()}] status={signature[0]} progress={signature[1]} {signature[2] or ''}", flush=True)
        if current.get("status") in {"completed", "complete", "error", "failed", "paused"}:
            terminal = current
            break
        time.sleep(POLL_SECONDS)

    for key, source in log_sources.items():
        append_log_delta(source, log_targets[key], offsets)
    if terminal is None:
        terminal = (session.get(f"{BASE_URL}/api/queue/item/{job_id}", timeout=20).json() or {}).get("item") or {}
        append_jsonl(ATTEMPT_DIR / "queue_events.jsonl", {"event": "monitor_deadline_reached", "status": terminal.get("status")})

    clean_terminal = redact(terminal)
    write_json(ATTEMPT_DIR / "final_queue_item.json", clean_terminal)
    package_dir = Path(str((terminal.get("config") or {}).get("output_package_dir") or "")).expanduser()
    plan_path = package_dir / "editorial" / "remotion_edit_plan.json"
    if not plan_path.is_file():
        candidates = list(package_dir.glob("*_remotion_edit_plan.json")) if package_dir.is_dir() else []
        plan_path = candidates[0] if candidates else plan_path
    video_candidates = list(package_dir.glob("nfl_test_001_super_bowl_xlix*.mp4")) if package_dir.is_dir() else []
    if package_dir.is_dir() and not video_candidates:
        video_candidates = [path for path in package_dir.rglob("*.mp4") if "render" not in path.parts]
    video_path = max(video_candidates, key=lambda path: path.stat().st_mtime) if video_candidates else None
    summary: dict[str, Any] = {
        "recorded_at": now(),
        "attempt": 9,
        "job_id": job_id,
        "status": terminal.get("status"),
        "progress": terminal.get("progress"),
        "error": terminal.get("error"),
        "progress_event_count": seen_seq,
        "package_dir": str(package_dir),
        "edit_plan_path": str(plan_path) if plan_path.is_file() else None,
        "video_path": str(video_path) if video_path else None,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "server_logs": {key: str(value) for key, value in log_targets.items()},
    }
    if video_path:
        technical = probe_video(video_path)
        audio_plan_path = package_dir / "checkpoints" / "audio_plan.json"
        expected_duration = None
        if audio_plan_path.is_file():
            try:
                expected_duration = float(json.loads(audio_plan_path.read_text(encoding="utf-8")).get("duration") or 0.0)
            except (OSError, ValueError, TypeError):
                expected_duration = None
        technical["expected_narration_duration_seconds"] = expected_duration
        technical["duration_matches_narration"] = bool(expected_duration and abs(technical["duration_seconds"] - expected_duration) <= 1.5)
        technical["hd_video"] = technical["width"] >= 1280 and technical["height"] >= 720
        technical["checks_pass"] = bool(
            technical["has_audio"]
            and technical["hd_video"]
            and technical["duration_seconds"] >= 45
            and technical["duration_matches_narration"]
        )
        technical["review_frames"] = extract_review_frames(video_path, technical["duration_seconds"])
        write_json(ATTEMPT_DIR / "technical_validation.json", technical)
        summary["technical_validation"] = technical
    write_json(ATTEMPT_DIR / "generation_summary.json", summary)
    write_json(ATTEMPT_DIR / "log_capture.json", {
        "recorded_at": now(),
        "attempt": 9,
        "log_files": {key: {"source": str(source), "archive": str(log_targets[key]), "size_bytes": log_targets[key].stat().st_size if log_targets[key].is_file() else 0} for key, source in log_sources.items()},
        "queue_events": str(ATTEMPT_DIR / "queue_events.jsonl"),
        "edit_plan": str(plan_path) if plan_path.is_file() else None,
    })
    print(json.dumps(summary, ensure_ascii=False, default=str), flush=True)
    return 0 if terminal.get("status") in {"completed", "complete"} and video_path else 1


if __name__ == "__main__":
    raise SystemExit(main())
