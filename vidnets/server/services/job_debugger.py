"""Stuck-job debugger watchdog for the Vidnets server.

A job can be alive but stalled: the worker waits on an inbox vision review,
a hung download, or a scene whose candidates keep failing, while the
heartbeat keeps the progress bar looking busy. Nothing used to notice that.
This module runs a background thread inside the server process (started from
``main.py``) that watches every ``processing`` job for *no change* -- not
slowness -- and escalates:

1. stale pending inbox requests for the live job  -> force a hook re-wake
2. one scene stuck past the scene threshold      -> advance the scene from
   media already on disk (see :func:`pick_advance_candidate`)
3. hung yt-dlp/ffmpeg child of the server        -> SIGTERM the child
4. worker thread dead (no queue change at all)   -> restart the server so the
   job auto-resumes from its checkpoint (``VIDZEO_AUTO_REQUEUE_INTERRUPTED``)

Safety rules, all deliberate:

* Never act on a job whose progress or normalized progress text changed
  recently, even if it is slow. The heartbeat's ``still working (Ns)``
  suffix is stripped before comparing, so a ticking clock is not progress.
* Max 3 debugger actions per job per hour; a job debugger-restarted 3 times
  is flagged and left alone -- no infinite restart loop.
* Never duplicate a job, never restart mid-render (a live ffmpeg/remotion
  child blocks the restart path), never act when more than one job is
  processing (the scene-advance flag is process-wide and assumes a single
  active media resolution).
* A stale inbox request alone is NOT a stall signal: dead jobs leave pending
  requests behind for hours. Only requests belonging to a LIVE job count.

The module is stdlib-only so the coordinator and the queue manager can use
its registry/picker without import cycles. Queue writes go through
``queue_manager`` (lazy import) or the ``/api/queue/advance-scene`` route.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

LOG_PREFIX = "[job-debugger]"

# ---------------------------------------------------------------------------
# Configuration (env-tunable, defaults per the build brief)
# ---------------------------------------------------------------------------


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.environ.get(name, "") or "").strip() or default)
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.environ.get(name, "") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


class DebuggerConfig:
    """Thresholds. Times are wall-clock seconds/minutes, not CPU time."""

    def __init__(self, env: Optional[Mapping[str, str]] = None):
        env = env if env is not None else os.environ
        self.enabled = _env_bool("VIDZEO_JOB_DEBUGGER_ENABLED", True)
        self.interval_seconds = max(
            30, _env_int("VIDZEO_JOB_DEBUGGER_INTERVAL_SECONDS", 120)
        )
        self.stall_minutes = max(
            1, _env_int("VIDZEO_JOB_STALL_MINUTES", 10)
        )
        self.scene_stall_minutes = max(
            1, _env_int("VIDZEO_JOB_SCENE_STALL_MINUTES", 20)
        )
        self.dead_worker_minutes = max(
            5, _env_int("VIDZEO_JOB_DEBUGGER_DEAD_WORKER_MINUTES", 15)
        )
        self.max_actions_per_hour = max(
            1, _env_int("VIDZEO_JOB_DEBUGGER_MAX_ACTIONS_PER_HOUR", 3)
        )
        self.max_restarts = max(
            1, _env_int("VIDZEO_JOB_DEBUGGER_MAX_RESTARTS", 3)
        )
        self.rewake_seconds = max(
            60, _env_int("VIDZEO_JOB_DEBUGGER_REWAKE_SECONDS", 240)
        )
        self.rewake_seconds_scriptit = max(
            60, _env_int("VIDZEO_JOB_DEBUGGER_REWAKE_SECONDS_SCRIPTIT", 120)
        )
        self.hung_child_minutes = max(
            5, _env_int("VIDZEO_JOB_DEBUGGER_HUNG_CHILD_MINUTES", 15)
        )
        # An advance flag that was never consumed (coordinator call already
        # past the scene, job finished, ...) must not leak into a later job.
        self.advance_flag_ttl_seconds = max(
            60, _env_int("VIDZEO_JOB_DEBUGGER_ADVANCE_TTL_SECONDS", 1800)
        )

    @property
    def stall_seconds(self) -> float:
        return float(self.stall_minutes * 60)

    @property
    def scene_stall_seconds(self) -> float:
        return float(self.scene_stall_minutes * 60)

    @property
    def dead_worker_seconds(self) -> float:
        return float(self.dead_worker_minutes * 60)


# ---------------------------------------------------------------------------
# Paths (same resolution rules as the server itself)
# ---------------------------------------------------------------------------


def runtime_root(env: Optional[Mapping[str, str]] = None) -> Path:
    env = env if env is not None else os.environ
    configured = str(env.get("VIDZEO_RUNTIME_ROOT") or "").strip()
    if configured:
        return Path(configured).expanduser()
    home = Path.home()
    candidate = home / "workspace" / "vidnets-runtime"
    if candidate.is_dir():
        return candidate
    return candidate


def queue_file(env: Optional[Mapping[str, str]] = None) -> Path:
    env = env if env is not None else os.environ
    configured = str(env.get("VIDZEO_QUEUE_FILE") or "").strip()
    if configured:
        return Path(configured).expanduser()
    runtime = runtime_root(env)
    runtime_queue = runtime / "server" / "queue.json"
    if runtime_queue.exists():
        return runtime_queue
    # Fall back to the packaged server directory (fresh installs).
    return Path(__file__).resolve().parent.parent / "queue.json"


def bridge_dir(env: Optional[Mapping[str, str]] = None) -> Path:
    return runtime_root(env) / "temp" / "agent-bridge"


def server_log_file(env: Optional[Mapping[str, str]] = None) -> Path:
    return runtime_root(env) / "logs" / "server-runtime.log"


def hook_seen_file(env: Optional[Mapping[str, str]] = None) -> Path:
    return Path.home() / "hooks" / "state" / "vidnets-inbox-watch" / "seen.txt"


def state_file(env: Optional[Mapping[str, str]] = None) -> Path:
    return runtime_root(env) / "server" / "job-debugger-state.json"


def advance_flag_dir(env: Optional[Mapping[str, str]] = None) -> Path:
    return runtime_root(env) / "temp" / "debugger-advance"


# ---------------------------------------------------------------------------
# Progress normalization -- the heartbeat is not progress
# ---------------------------------------------------------------------------

_STILL_WORKING_RE = re.compile(r"\s*[—-]\s*still working.*$", re.IGNORECASE)
_SCENE_RE = re.compile(r"preparing media\s+(\d+)\s*/\s*(\d+)", re.IGNORECASE)


def normalize_progress_text(text: Any) -> str:
    """Strip the heartbeat's elapsed-time suffix so a ticking clock is not
    mistaken for work. ``Preparing media 14/15 — still working (3877s)``
    normalizes to ``Preparing media 14/15``."""
    base = str(text or "").strip()
    return _STILL_WORKING_RE.sub("", base).strip()


def parse_scene_number(text: Any) -> Optional[int]:
    """1-based scene number from a ``Preparing media N/M`` label, else None.

    The label is derived from the pipeline's raw count text, so N is the
    scene the counter is on (1-based). The coordinator converts it to a
    0-based index.
    """
    match = _SCENE_RE.search(normalize_progress_text(text))
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def progress_fingerprint(item: Mapping[str, Any]) -> Tuple[Any, str, int]:
    """What counts as 'the job moved': progress value, normalized text, and
    the latest progress-event seq. The heartbeat appends events, so seq alone
    is NOT movement -- it only proves the heartbeat thread is alive."""
    return (
        item.get("progress"),
        normalize_progress_text(item.get("progress_text")),
        int(item.get("progress_seq") or 0),
    )


# ---------------------------------------------------------------------------
# Scene-advance registry (in-process channel to the media coordinator)
# ---------------------------------------------------------------------------
# The media coordinator runs in this same process, so a plain dict is the
# live channel; a flag file under the runtime temp dir is the durable record.
# Only one media resolution runs at a time (single-worker queue), so the flag
# is keyed by scene number, not by job -- the watchdog refuses to set one
# while more than one job is processing.

_ADVANCE_LOCK = threading.Lock()
_ADVANCE_REQUESTS: Dict[str, Dict[str, Any]] = {}


def set_scene_advance(
    job_id: str, scene_number: int, reason: str = ""
) -> Dict[str, Any]:
    """Register a scene advance. Returns the stored record."""
    record = {
        "job_id": str(job_id or ""),
        "scene_number": int(scene_number),
        "reason": str(reason or ""),
        "at": time.time(),
    }
    with _ADVANCE_LOCK:
        _ADVANCE_REQUESTS[str(job_id or "")] = record
    return record


def is_scene_advance_requested(scene_index: int) -> bool:
    """True when a live advance flag covers this 0-based scene index.

    The flag names the 1-based scene the progress counter was on; the first
    scene at or after that number without an accepted resolution is the stuck
    one (this absorbs the off-by-one between 'N scenes done' and 'on scene
    N'). Expired flags are ignored.
    """
    now = time.time()
    ttl = DebuggerConfig().advance_flag_ttl_seconds
    with _ADVANCE_LOCK:
        for record in list(_ADVANCE_REQUESTS.values()):
            if now - float(record.get("at") or 0) > ttl:
                continue
            try:
                number = int(record.get("scene_number") or 0)
            except (TypeError, ValueError):
                continue
            if number > 0 and (int(scene_index) + 1) >= number:
                return True
    return False


def consume_scene_advance(scene_index: int) -> Optional[Dict[str, Any]]:
    """Pop the flag covering this scene index once it has been applied, so a
    consumed advance can never leak into a later scene or job."""
    now = time.time()
    ttl = DebuggerConfig().advance_flag_ttl_seconds
    with _ADVANCE_LOCK:
        for job_id, record in list(_ADVANCE_REQUESTS.items()):
            if now - float(record.get("at") or 0) > ttl:
                continue
            try:
                number = int(record.get("scene_number") or 0)
            except (TypeError, ValueError):
                continue
            if number > 0 and (int(scene_index) + 1) >= number:
                return _ADVANCE_REQUESTS.pop(job_id)
    return None


def pending_advance_requests() -> List[Dict[str, Any]]:
    with _ADVANCE_LOCK:
        return [dict(record) for record in _ADVANCE_REQUESTS.values()]


def clear_scene_advances() -> int:
    """Drop all pending advance flags (test isolation / watchdog startup).
    Returns how many were dropped."""
    with _ADVANCE_LOCK:
        count = len(_ADVANCE_REQUESTS)
        _ADVANCE_REQUESTS.clear()
        return count


# ---------------------------------------------------------------------------
# Tier-1 advance pick: reuse already-downloaded, vision-checked media
# ---------------------------------------------------------------------------
# The user's stall policy, in order: (1) already-downloaded media for this
# scene, closest first, scored by the same metadata/vision scores -- never
# just any file; (2) a general scene from the same job's already-approved
# media (the coordinator's existing nearest-verified-neighbor tier); (3) a
# graphic (the coordinator's existing graphic fallback). This picker is
# tier (1) only; tiers (2) and (3) already exist in the coordinator and are
# reached by falling through when this returns None.

_FATAL_VERIFY_REASONS = (
    "promotional_thumbnail",
    "entity_conflict",
    # A footage-only scene rejected a web still for contract reasons, not
    # quality: reusing the still would silently break the scene's footage
    # contract. Those scenes fall through to the same-job reuse tier.
    "footage_required",
    # NOTE: "explicit_scene_conflict" is deliberately NOT here. It is the
    # coordinator's derived veto label, and it also fires for a soft
    # partial-match verdict in a non-relaxed scene -- exactly the reusable
    # case. Genuine entity conflicts are caught below from the model's own
    # verdict fields (fatal_mismatch / entity_conflict / irrelevance text).
)

# A vision verdict that says the pixels are irrelevant to the scene -- not
# merely the wrong viewpoint -- is not "closest" media, whatever its score.
_IRRELEVANT_VERIFY_TOKENS = (
    "off_topic",
    "off-topic",
    "irrelevant",
    "wrong subject",
    "wrong_subject",
    "wrong entity",
    "wrong_entity",
    "entity mismatch",
    "entity_mismatch",
    "different entity",
    "different_entity",
    "wrong event",
    "wrong_event",
    "frame mismatch",
    "frame_mismatch",
    "confident_irrelevance",
    "low_relevance",
    "relevance_below",
    "rejected_entity_mismatch",
    "wrong operator",
    "wrong_operator",
    "different operator",
    "different_operator",
    "wrong incident",
    "wrong_incident",
)


def _is_fatal_verdict(outcome: Any) -> bool:
    """True when the vision verdict is unusable for reuse: a hard identity /
    content rejection (wrong operator, wrong incident, thumbnail), a
    footage-contract rejection, or an explicit irrelevance verdict
    (off-topic, wrong entity). A soft 'wrong viewpoint / partial match'
    verdict is NOT fatal -- that is exactly the closest-available media the
    advance is for."""
    if not isinstance(outcome, (list, tuple)) or len(outcome) < 6:
        return False
    reason = str(outcome[3] or "").lower()
    if any(token in reason for token in _FATAL_VERIFY_REASONS):
        return True
    verdict = outcome[5] or {}
    if isinstance(verdict, dict):
        if verdict.get("fatal_mismatch") is True:
            return True
        if verdict.get("entity_conflict"):
            return True
        verdict_text = " ".join(
            str(verdict.get(key) or "")
            for key in ("reason", "rejection_reason", "status")
        ).lower()
        if any(token in verdict_text for token in _IRRELEVANT_VERIFY_TOKENS):
            return True
    return False


def pick_advance_candidate(
    scene_index: int,
    candidates: Optional[Sequence[Mapping[str, Any]]],
    gated_outcomes: Optional[Mapping[Any, Any]],
) -> Optional[Tuple[Mapping[str, Any], float, Optional[Dict[str, Any]]]]:
    """Best already-downloaded, vision-checked candidate for a stuck scene.

    ``candidates`` is the scene's ``discovered`` list; ``gated_outcomes`` is
    the coordinator's ``(index, path) -> (metadata, tie, verified, reason,
    visual_score, verdict)`` map. Only candidates with a real file on disk
    AND a vision verdict (not a fatal one) qualify, ranked by vision score
    then metadata score. Returns ``(candidate, visual_score, verdict)`` or
    None when nothing usable is already on disk.
    """
    best: Optional[Tuple[Mapping[str, Any], float, Optional[Dict[str, Any]]]] = None
    best_key: Optional[Tuple[float, float]] = None
    for candidate in candidates or []:
        if not isinstance(candidate, Mapping):
            continue
        path = str(
            candidate.get("cleaned_path") or candidate.get("path") or ""
        ).strip()
        if not path:
            continue
        try:
            if not os.path.isfile(path) or os.path.getsize(path) <= 1024:
                continue
        except OSError:
            continue
        outcome = None
        if gated_outcomes:
            outcome = gated_outcomes.get(
                (int(scene_index), str(candidate.get("path") or ""))
            )
        if outcome is None:
            # Never vision-checked: the user wants checked media only.
            continue
        if _is_fatal_verdict(outcome):
            continue
        try:
            visual_score = float(outcome[4] or 0.0)
        except (TypeError, ValueError):
            visual_score = 0.0
        try:
            metadata_score = float(
                candidate.get("final_score")
                or candidate.get("score")
                or candidate.get("metadata_score")
                or 0.0
            )
        except (TypeError, ValueError):
            metadata_score = 0.0
        key = (visual_score, metadata_score)
        if best is None or key > (best_key or (0.0, 0.0)):
            verdict = outcome[5] if isinstance(outcome[5], dict) else None
            best = (candidate, visual_score, verdict)
            best_key = key
    return best


# ---------------------------------------------------------------------------
# Diagnosis (read-only)
# ---------------------------------------------------------------------------


def check_server_responsive(
    host: str = "127.0.0.1", port: int = 5010, timeout: float = 3.0
) -> bool:
    """Any HTTP response from :5010 means the Flask layer is alive. A 404 is
    fine -- the root has no handler; only refused/timeout means down."""
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        try:
            conn.request("GET", "/")
            response = conn.getresponse()
            response.read()
            return True
        finally:
            conn.close()
    except Exception:
        return False


def find_stale_inbox_requests(
    job_id: str,
    now: Optional[float] = None,
    config: Optional[DebuggerConfig] = None,
    bridge: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Pending inbox requests belonging to THIS live job that are older than
    the hook's re-wake threshold. Stale requests from dead jobs are ignored:
    only ``job_id`` matches count, and expired requests are skipped (the
    hook skips them too -- a reboot can leave a pending file whose waiter is
    gone)."""
    now = time.time() if now is None else float(now)
    config = config or DebuggerConfig()
    bridge = bridge or bridge_dir()
    stale: List[Dict[str, Any]] = []
    try:
        entries = list(bridge.iterdir())
    except OSError:
        return stale
    for entry in entries:
        request_path = entry / "request.json"
        if not request_path.is_file():
            continue
        if (entry / "response.json").exists():
            continue
        try:
            request = json.loads(request_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if str(request.get("job_id") or "") != str(job_id or ""):
            continue
        if str(request.get("state") or "") != "pending":
            continue
        try:
            expires_at = float(request.get("expires_at") or 0)
        except (TypeError, ValueError):
            expires_at = 0.0
        if expires_at and expires_at <= now:
            continue
        try:
            created_at = float(request.get("created_at") or 0)
        except (TypeError, ValueError):
            created_at = 0.0
        if not created_at:
            continue
        operation = str(request.get("operation") or "")
        threshold = (
            config.rewake_seconds_scriptit
            if operation.startswith("scriptit.")
            else config.rewake_seconds
        )
        age = now - created_at
        if age > threshold:
            stale.append(
                {
                    "id": str(request.get("id") or entry.name),
                    "operation": operation,
                    "kind": str(request.get("kind") or ""),
                    "age_seconds": round(age, 1),
                    "threshold_seconds": threshold,
                }
            )
    return stale


def tail_job_log(
    job_id: str,
    minutes: int = 10,
    max_lines: int = 20,
    log_path: Optional[Path] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Last lines mentioning this job in the server log. Classifies the
    likely wait: a per-host approval wait, or a hung download/probe."""
    now = time.time() if now is None else float(now)
    log_path = log_path or server_log_file()
    lines: List[str] = []
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            # Read the tail in bounded chunks; the log can be large.
            chunk = 65536
            data = b""
            while size > 0 and len(data) < 512 * 1024:
                read = min(chunk, size)
                size -= read
                handle.seek(size)
                data = handle.read(read).encode("utf-8", errors="replace") + data
                if data.count("\n") > 400:
                    break
        text = data.decode("utf-8", errors="replace")
        lines = [line for line in text.splitlines() if str(job_id) in line]
    except OSError:
        lines = []
    recent = lines[-max_lines:]
    lowered = " ".join(recent).lower()
    return {
        "lines": recent,
        "count": len(lines),
        "approval_wait": any(
            token in lowered
            for token in ("approval", "awaiting approval", "permission")
        ),
        "hung_download": any(
            token in lowered
            for token in ("yt-dlp", "ffmpeg")
        )
        and any(
            token in lowered
            for token in ("timeout", "timed out", "stalled", "retry")
        ),
    }


def _iter_process_tree() -> List[Tuple[int, int, float, str]]:
    """(pid, ppid, start_time_seconds_since_boot, cmdline) for all processes,
    from /proc. Empty on non-Linux hosts."""
    processes: List[Tuple[int, int, float, str]] = []
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return processes
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            pid = int(entry.name)
            stat = (entry / "stat").read_text(errors="replace").split()
            ppid = int(stat[3])
            start_ticks = float(stat[21])
            cmdline = (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode(
                "utf-8", errors="replace"
            ).strip()
            processes.append((pid, ppid, start_ticks, cmdline))
        except (OSError, ValueError, IndexError):
            continue
    return processes


def find_hung_children(
    min_age_seconds: float = 900.0,
    server_pid: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """yt-dlp/ffmpeg descendants of the server process that have been running
    longer than ``min_age_seconds``. A healthy download updates progress and
    finishes; a child older than this with a frozen queue is a hung
    download/probe, not a slow one."""
    server_pid = server_pid if server_pid is not None else os.getpid()
    try:
        ticks_per_second = float(os.sysconf("SC_CLK_TCK"))
        boot_time = time.time() - float(
            open("/proc/stat", encoding="utf-8").read().split("btime")[1].split()[0]
        )
    except Exception:
        return []
    processes = _iter_process_tree()
    by_pid = {pid: (ppid, start_ticks, cmdline) for pid, ppid, start_ticks, cmdline in processes}
    descendants = set()
    frontier = [server_pid]
    while frontier:
        current = frontier.pop()
        for pid, (ppid, _start, _cmd) in by_pid.items():
            if ppid == current and pid not in descendants:
                descendants.add(pid)
                frontier.append(pid)
    now = time.time()
    hung: List[Dict[str, Any]] = []
    for pid in descendants:
        ppid, start_ticks, cmdline = by_pid[pid]
        lowered = cmdline.lower()
        if "yt-dlp" not in lowered and "ffmpeg" not in lowered:
            continue
        started_at = boot_time + (start_ticks / ticks_per_second)
        age = now - started_at
        if age < min_age_seconds:
            continue
        hung.append(
            {
                "pid": pid,
                "age_seconds": round(age, 1),
                "cmdline": cmdline[:300],
                "kind": "yt-dlp" if "yt-dlp" in lowered else "ffmpeg",
            }
        )
    return hung


def has_live_render_child(server_pid: Optional[int] = None) -> bool:
    """True when a remotion/ffmpeg child of the server is alive -- a render
    is in progress and the server must NOT be restarted."""
    server_pid = server_pid if server_pid is not None else os.getpid()
    processes = _iter_process_tree()
    by_pid = {pid: (ppid, _s, cmd) for pid, ppid, _s, cmd in processes}
    descendants = set()
    frontier = [server_pid]
    while frontier:
        current = frontier.pop()
        for pid, (ppid, _s, _c) in by_pid.items():
            if ppid == current and pid not in descendants:
                descendants.add(pid)
                frontier.append(pid)
    for pid in descendants:
        cmdline = (by_pid[pid][2] or "").lower()
        if "ffmpeg" in cmdline or "remotion" in cmdline or "render" in cmdline:
            return True
    return False


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def force_rewake(
    stale_requests: Sequence[Mapping[str, Any]],
    seen_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Force the inbox hook to re-wake for stale requests: remove their ids
    from the hook's seen file, so the next 20s poll treats them as never
    woken. The hook itself is runtime-managed and is never restarted from
    here; if its state file is missing or unwritable that is logged, not
    worked around."""
    seen_path = seen_path or hook_seen_file()
    ids = {str(item.get("id") or "") for item in stale_requests if item.get("id")}
    ids.discard("")
    if not ids:
        return {"ok": False, "reason": "no request ids"}
    try:
        original = seen_path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "reason": f"hook seen file unreadable: {exc}"}
    kept = [
        line
        for line in original.splitlines()
        if not line.split(" ", 1) or line.split(" ", 1)[0] not in ids
    ]
    removed = len(original.splitlines()) - len(kept)
    if removed == 0:
        return {"ok": True, "removed": 0, "note": "ids not in seen file; hook will wake on next poll"}
    try:
        tmp_path = seen_path.with_suffix(".tmp")
        tmp_path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        os.replace(tmp_path, seen_path)
    except OSError as exc:
        return {"ok": False, "reason": f"hook seen file unwritable: {exc}"}
    return {"ok": True, "removed": removed}


def write_advance_flag_file(
    job_id: str, scene_number: int, reason: str = ""
) -> Path:
    """Durable record of a scene advance, next to the runtime temp dir.
    The in-process registry is the live channel; this file is the audit
    trail the coordinator never needs to read."""
    directory = advance_flag_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{job_id}.json"
    payload = {
        "job_id": str(job_id),
        "scene_number": int(scene_number),
        "reason": str(reason or ""),
        "at": time.time(),
    }
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)
    return path


def request_scene_advance(
    job_id: str, scene_number: int, reason: str = ""
) -> Dict[str, Any]:
    """Advance a stuck scene: register the flag the media coordinator
    checks between waves / at resolution time, write the durable flag file,
    and record it on the queue item (best effort -- the in-process registry
    is what the coordinator reads)."""
    record = set_scene_advance(job_id, scene_number, reason)
    flag_path = write_advance_flag_file(job_id, scene_number, reason)
    try:
        from services.queue_manager import get_queue_manager

        manager = get_queue_manager()
        manager.request_scene_advance(job_id, scene_number, reason)
    except Exception as exc:  # queue write is best-effort, never fatal
        print(f"{LOG_PREFIX} queue record for advance failed: {exc}", flush=True)
    return {**record, "flag_file": str(flag_path)}


def kill_hung_child(pid: int) -> Dict[str, Any]:
    """SIGTERM a hung download/probe child. The bounded lane call returns on
    its timeout/error and the stage moves on; nothing else is touched."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return {"ok": False, "reason": "process already gone"}
    except PermissionError as exc:
        return {"ok": False, "reason": f"cannot signal pid {pid}: {exc}"}
    return {"ok": True, "pid": pid}


def restart_server(reason: str = "") -> Dict[str, Any]:
    """Restart this server process so the job auto-resumes from its
    checkpoint. A detached restarter shell (own session, survives this
    process) SIGTERMs the server PID, waits, SIGKILLs if needed, then runs
    the documented start command. Callers must have verified no render is
    in progress and the restart cap is not hit."""
    vidnets_dir = Path(__file__).resolve().parent.parent
    runtime = runtime_root()
    start_log = runtime / "logs" / "server-start.log"
    pid = os.getpid()
    script = (
        "sleep 3\n"
        f"kill -TERM {pid} 2>/dev/null || true\n"
        "for i in $(seq 1 10); do\n"
        f"  kill -0 {pid} 2>/dev/null || break\n"
        "  sleep 1\n"
        "done\n"
        f"kill -KILL {pid} 2>/dev/null || true\n"
        "sleep 1\n"
        f"cd {vidnets_dir} && setsid nohup bash VIDNETS_AGENT.sh "
        f"--runtime-root {runtime} start "
        f">> {start_log} 2>&1 < /dev/null &\n"
    )
    restarter_log = runtime / "logs" / "job-debugger-restart.log"
    restarter_log.parent.mkdir(parents=True, exist_ok=True)
    with open(restarter_log, "a", encoding="utf-8") as log_handle:
        log_handle.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] restart requested: {reason}\n")
        log_handle.write(script)
    subprocess.Popen(
        ["bash", "-c", script],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    print(f"{LOG_PREFIX} restart requested ({reason}); restarter spawned", flush=True)
    return {"ok": True, "pid": pid, "reason": reason}


# ---------------------------------------------------------------------------
# Watchdog state + decision engine
# ---------------------------------------------------------------------------


def _new_state() -> Dict[str, Any]:
    return {"observations": {}, "actions": {}, "restarts": {}, "flagged": {}}


def load_state(path: Optional[Path] = None) -> Dict[str, Any]:
    path = path or state_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            merged = _new_state()
            merged.update({key: data.get(key) or {} for key in merged})
            return merged
    except (OSError, ValueError):
        pass
    return _new_state()


def save_state(state: Mapping[str, Any], path: Optional[Path] = None) -> None:
    path = path or state_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp_path, path)
    except OSError as exc:
        print(f"{LOG_PREFIX} state save failed: {exc}", flush=True)


class JobDebugger:
    """Tick-based watchdog. ``tick()`` is pure decision + action against the
    live queue file; the thread loop in :func:`start_job_debugger` drives it."""

    def __init__(
        self,
        config: Optional[DebuggerConfig] = None,
        *,
        queue_path: Optional[Path] = None,
        state_path: Optional[Path] = None,
        bridge: Optional[Path] = None,
        log_path: Optional[Path] = None,
        seen_path: Optional[Path] = None,
        state: Optional[Dict[str, Any]] = None,
        actions: Optional[Any] = None,
    ):
        self.config = config or DebuggerConfig()
        self.queue_path = queue_path or queue_file()
        self.state_path = state_path or state_file()
        self.bridge = bridge or bridge_dir()
        self.log_path = log_path or server_log_file()
        self.seen_path = seen_path or hook_seen_file()
        self.state = state if state is not None else load_state(self.state_path)
        # ``actions`` is a test seam: an object with force_rewake /
        # request_scene_advance / kill_hung_child / restart_server callables.
        self.actions = actions
        self._lock = threading.Lock()

    # -- queue reading ----------------------------------------------------
    def read_processing_jobs(self) -> List[Dict[str, Any]]:
        try:
            data = json.loads(self.queue_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        return [
            item
            for item in items
            if isinstance(item, dict)
            and str(item.get("status") or "").strip().lower() == "processing"
        ]

    # -- observation ------------------------------------------------------
    def _observe(self, job_id: str, item: Mapping[str, Any], now: float) -> Dict[str, Any]:
        observations = self.state.setdefault("observations", {})
        record = observations.setdefault(job_id, {})
        progress = item.get("progress")
        text = normalize_progress_text(item.get("progress_text"))
        seq = int(item.get("progress_seq") or 0)
        scene_number = parse_scene_number(item.get("progress_text"))
        changed = (
            record.get("progress") != progress
            or record.get("text") != text
        )
        if not record or changed:
            record.update(
                {
                    "progress": progress,
                    "text": text,
                    "seq": seq,
                    "scene_number": scene_number,
                    "scene_since": now,
                    "last_change": now,
                    "first_seen": record.get("first_seen") or now,
                }
            )
        else:
            record["seq"] = seq
            if record.get("scene_number") != scene_number:
                record["scene_number"] = scene_number
                record["scene_since"] = now
        return record

    # -- rate limits ------------------------------------------------------
    def _actions_in_last_hour(self, job_id: str, now: float) -> int:
        actions = (self.state.get("actions") or {}).get(job_id) or []
        return sum(1 for entry in actions if now - float(entry.get("at") or 0) < 3600)

    def _record_action(self, job_id: str, action: str, reason: str, now: float) -> None:
        actions = self.state.setdefault("actions", {}).setdefault(job_id, [])
        actions.append({"action": action, "reason": reason, "at": now})
        self.state["actions"][job_id] = actions[-50:]

    def _log(self, message: str) -> None:
        print(f"{LOG_PREFIX} {message}", flush=True)

    # -- diagnosis --------------------------------------------------------
    def diagnose(self, job_id: str) -> Dict[str, Any]:
        config = self.config
        stale = find_stale_inbox_requests(job_id, config=config, bridge=self.bridge)
        log_info = tail_job_log(job_id, log_path=self.log_path)
        hung = find_hung_children(
            min_age_seconds=config.hung_child_minutes * 60.0
        )
        return {
            "server_responsive": check_server_responsive(),
            "stale_inbox": stale,
            "log": log_info,
            "hung_children": hung,
            "live_render": has_live_render_child(),
        }

    # -- one tick ---------------------------------------------------------
    def tick(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
        """Inspect every processing job once. Returns the actions taken."""
        now = time.time() if now is None else float(now)
        config = self.config
        jobs = self.read_processing_jobs()
        taken: List[Dict[str, Any]] = []
        if not jobs:
            return taken
        multiple_processing = len(jobs) > 1
        for item in jobs:
            job_id = str(item.get("id") or "")
            if not job_id:
                continue
            record = self._observe(job_id, item, now)
            quiet_for = now - float(record.get("last_change") or now)
            scene_number = record.get("scene_number")
            scene_quiet_for = (
                now - float(record.get("scene_since") or now)
                if scene_number is not None
                else 0.0
            )
            seq = int(item.get("progress_seq") or 0)
            # Track seq movement separately: the heartbeat appends events, so
            # a frozen seq means the heartbeat thread itself is dead.
            if seq != int(record.get("last_seq") or 0):
                record["last_seq"] = seq
                record["last_seq_change"] = now
                seq_frozen_for = 0.0
            elif "last_seq_change" not in record:
                record["last_seq_change"] = now
                seq_frozen_for = 0.0
            else:
                seq_frozen_for = now - float(record["last_seq_change"])

            if quiet_for < config.stall_seconds:
                continue
            # Stalled: progress + normalized text unchanged past the threshold.
            diagnosis = self.diagnose(job_id)
            self._log(
                f"job {job_id} stalled: quiet {quiet_for:.0f}s "
                f"progress={record.get('progress')} text={record.get('text')!r} "
                f"scene={scene_number} scene_quiet={scene_quiet_for:.0f}s "
                f"server_up={diagnosis['server_responsive']} "
                f"stale_inbox={len(diagnosis['stale_inbox'])} "
                f"hung_children={len(diagnosis['hung_children'])} "
                f"log_lines={diagnosis['log']['count']} "
                f"approval_wait={diagnosis['log']['approval_wait']}"
            )
            self._record_diagnosis(job_id, diagnosis, now)
            if (self.state.get("flagged") or {}).get(job_id):
                self._log(f"job {job_id} flagged (restart cap hit); left alone")
                continue
            if self._actions_in_last_hour(job_id, now) >= config.max_actions_per_hour:
                self._log(f"job {job_id} at action rate limit; waiting")
                continue

            action = self._choose_action(
                job_id, record, diagnosis, quiet_for, scene_quiet_for,
                seq_frozen_for, multiple_processing,
            )
            if action is None:
                continue
            result = self._perform(job_id, action, now)
            result["job_id"] = job_id
            taken.append(result)
            self._record_action(job_id, action["kind"], action["reason"], now)
            self._log(
                f"job {job_id} action {action['kind']}: {action['reason']} -> {result}"
            )
        with self._lock:
            save_state(self.state, self.state_path)
        return taken

    def _record_diagnosis(self, job_id: str, diagnosis: Mapping[str, Any], now: float) -> None:
        try:
            from services.queue_manager import get_queue_manager

            manager = get_queue_manager()
            stale = len(diagnosis.get("stale_inbox") or [])
            hung = len(diagnosis.get("hung_children") or [])
            manager.record_debugger_event(
                job_id,
                f"Debugger: no progress change; server_up="
                f"{bool(diagnosis.get('server_responsive'))} stale_inbox={stale} "
                f"hung_children={hung} approval_wait="
                f"{bool((diagnosis.get('log') or {}).get('approval_wait'))}",
            )
        except Exception as exc:  # diagnosis recording is best-effort
            print(f"{LOG_PREFIX} diagnosis record failed: {exc}", flush=True)

    def _choose_action(
        self,
        job_id: str,
        record: Mapping[str, Any],
        diagnosis: Mapping[str, Any],
        quiet_for: float,
        scene_quiet_for: float,
        seq_frozen_for: float,
        multiple_processing: bool,
    ) -> Optional[Dict[str, Any]]:
        config = self.config
        restarts = int((self.state.get("restarts") or {}).get(job_id) or 0)
        # 1. Stale inbox requests for the live job: the hook can re-wake.
        if diagnosis.get("stale_inbox"):
            oldest = max(
                item.get("age_seconds", 0) for item in diagnosis["stale_inbox"]
            )
            return {
                "kind": "rewake",
                "reason": (
                    f"{len(diagnosis['stale_inbox'])} stale inbox request(s) "
                    f"for live job, oldest {oldest:.0f}s"
                ),
                "stale": list(diagnosis["stale_inbox"]),
            }
        # 2. One scene stuck: advance it from on-disk media.
        scene_number = record.get("scene_number")
        if (
            scene_number is not None
            and scene_quiet_for >= config.scene_stall_seconds
            and not multiple_processing
        ):
            return {
                "kind": "advance_scene",
                "reason": (
                    f"scene {scene_number} unchanged for {scene_quiet_for:.0f}s "
                    f"(>{config.scene_stall_minutes} min)"
                ),
                "scene_number": int(scene_number),
            }
        # 3. Hung download/probe child: kill it, the bounded lane call returns.
        if diagnosis.get("hung_children"):
            child = diagnosis["hung_children"][0]
            return {
                "kind": "kill_hung_child",
                "reason": (
                    f"{child.get('kind')} child pid {child.get('pid')} hung "
                    f"for {child.get('age_seconds', 0):.0f}s"
                ),
                "pid": int(child.get("pid") or 0),
            }
        # 4. Worker dead: no queue change at all (heartbeat seq frozen too).
        if seq_frozen_for >= config.dead_worker_seconds:
            if restarts >= config.max_restarts:
                flagged = self.state.setdefault("flagged", {})
                flagged[job_id] = {
                    "reason": f"debugger restarted {restarts} times; left alone",
                    "at": time.time(),
                }
                self._log(
                    f"job {job_id} reached restart cap ({restarts}); flagged"
                )
                return None
            if diagnosis.get("live_render"):
                self._log(
                    f"job {job_id} looks dead but a render child is alive; "
                    "not restarting"
                )
                return None
            return {
                "kind": "restart_server",
                "reason": (
                    f"no queue change for {seq_frozen_for:.0f}s "
                    f"(worker+heartbeat dead), server_up="
                    f"{bool(diagnosis.get('server_responsive'))}"
                ),
            }
        return None

    def _perform(
        self, job_id: str, action: Mapping[str, Any], now: float
    ) -> Dict[str, Any]:
        kind = action.get("kind")
        actions = self.actions
        if kind == "rewake":
            if actions is not None and hasattr(actions, "force_rewake"):
                return dict(actions.force_rewake(action.get("stale") or []))
            return force_rewake(action.get("stale") or [], seen_path=self.seen_path)
        if kind == "advance_scene":
            scene_number = int(action.get("scene_number") or 0)
            if actions is not None and hasattr(actions, "request_scene_advance"):
                return dict(
                    actions.request_scene_advance(
                        job_id, scene_number, action.get("reason") or ""
                    )
                )
            return request_scene_advance(
                job_id, scene_number, action.get("reason") or ""
            )
        if kind == "kill_hung_child":
            pid = int(action.get("pid") or 0)
            if actions is not None and hasattr(actions, "kill_hung_child"):
                return dict(actions.kill_hung_child(pid))
            return kill_hung_child(pid)
        if kind == "restart_server":
            restarts = self.state.setdefault("restarts", {})
            restarts[job_id] = int(restarts.get(job_id) or 0) + 1
            if actions is not None and hasattr(actions, "restart_server"):
                return dict(actions.restart_server(action.get("reason") or ""))
            return restart_server(action.get("reason") or "")
        return {"ok": False, "reason": f"unknown action {kind!r}"}

    # -- status -----------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "interval_seconds": self.config.interval_seconds,
            "stall_minutes": self.config.stall_minutes,
            "scene_stall_minutes": self.config.scene_stall_minutes,
            "dead_worker_minutes": self.config.dead_worker_minutes,
            "max_actions_per_hour": self.config.max_actions_per_hour,
            "max_restarts": self.config.max_restarts,
            "observations": dict(self.state.get("observations") or {}),
            "actions": dict(self.state.get("actions") or {}),
            "restarts": dict(self.state.get("restarts") or {}),
            "flagged": dict(self.state.get("flagged") or {}),
            "pending_advance": pending_advance_requests(),
        }


# ---------------------------------------------------------------------------
# Thread lifecycle (started from main.py)
# ---------------------------------------------------------------------------

_THREAD: Optional[threading.Thread] = None
_DEBUGGER: Optional[JobDebugger] = None


def get_debugger() -> JobDebugger:
    global _DEBUGGER
    if _DEBUGGER is None:
        _DEBUGGER = JobDebugger()
    return _DEBUGGER


def start_job_debugger() -> Optional[threading.Thread]:
    """Start the watchdog thread. Safe to call twice; never raises."""
    global _THREAD
    try:
        debugger = get_debugger()
        if not debugger.config.enabled:
            print(f"{LOG_PREFIX} disabled by VIDZEO_JOB_DEBUGGER_ENABLED", flush=True)
            return None
        if _THREAD is not None and _THREAD.is_alive():
            return _THREAD

        def _run() -> None:
            while True:
                try:
                    time.sleep(debugger.config.interval_seconds)
                except Exception:
                    break
                try:
                    debugger.tick()
                except Exception as exc:  # a tick must never kill the server
                    print(f"{LOG_PREFIX} tick failed: {exc}", flush=True)

        thread = threading.Thread(
            target=_run, name="job-debugger", daemon=True
        )
        thread.start()
        _THREAD = thread
        print(
            f"{LOG_PREFIX} watching (interval={debugger.config.interval_seconds}s, "
            f"stall={debugger.config.stall_minutes}min, "
            f"scene_stall={debugger.config.scene_stall_minutes}min)",
            flush=True,
        )
        return thread
    except Exception as exc:
        print(f"{LOG_PREFIX} failed to start: {exc}", flush=True)
        return None
