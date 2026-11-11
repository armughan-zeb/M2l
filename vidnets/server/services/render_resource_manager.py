"""Release Vidnets-owned idle memory and measure safe render headroom."""

from __future__ import annotations

import ctypes
import gc
import os
import subprocess
import sys
import time
from typing import Any, Dict, List


MAX_REMOTION_WORKERS = 8
MIN_REMOTION_WORKERS = 1
MIN_REMOTION_AVAILABLE_MB = 384

# How often the render memory wait re-checks admission (seconds). The old
# loop slept 2s; a media worker that frees memory was therefore noticed up
# to 2s late, and — worse — the fixed 120s wait expired while the job's own
# media workers were still running, pausing the job instead of waiting for
# them. The wait below is worker-aware: it extends while media workers are
# alive and fails fast once none are left.
DEFAULT_MEMORY_POLL_SECONDS = 0.5
DEFAULT_MEMORY_WORKER_EXTEND_SECONDS = 30.0
DEFAULT_MEMORY_NO_WORKER_GRACE_SECONDS = 10.0

# Process names that are Vidnets media workers (children of this server, or
# members of its process group). Only these are ever counted as "workers
# that may free memory when they exit"; the Vidnets server, the daemon, the
# CMS worker and the user's apps are never in this set.
_MEDIA_WORKER_NAMES = {
    "ffmpeg", "ffprobe", "yt-dlp", "yt_dlp",
    "chrome", "chromium", "chrome-headless-shell", "headless_shell",
    "google-chrome", "chromium-browser",
}
# Command-line tokens that identify a media worker whose executable name is
# a generic interpreter (python/node). A bare `node` or `python` process is
# never a media worker on its name alone.
_MEDIA_WORKER_CMDLINE_TOKENS = (
    "headless_agent", "research_search_worker", "youtube_pipeline",
    "youtube_pool", "yt-dlp", "remotion", "render-vidnets",
    "chrome-headless-shell", "puppeteer",
)


def _is_media_worker(name: str, cmdline: str) -> bool:
    lowered_name = (name or "").strip().lower()
    lowered_cmd = (cmdline or "").lower()
    if lowered_name in _MEDIA_WORKER_NAMES:
        return True
    if lowered_name in {"python", "python3", "python.exe", "node", "node.exe"}:
        return any(token in lowered_cmd for token in _MEDIA_WORKER_CMDLINE_TOKENS)
    return False


def _proc_cmdline(proc) -> str:
    try:
        parts = proc.cmdline()
    except Exception:
        return ""
    return " ".join(str(part) for part in (parts or []))


def live_media_workers() -> List[Dict[str, Any]]:
    """List this server's live media workers (ffmpeg/Chrome/search workers).

    Union of two ownership proofs, so a worker is found even after its
    intermediate parent died (Vidnets ffmpeg downloads are reparented to
    PID 1 but keep the server's process group): (a) descendants of this
    process, (b) members of this process's group. Only processes whose
    name/cmdline matches a media worker are returned; the server itself,
    the daemon and unrelated processes are never listed. Returns [] when
    process enumeration is unavailable, so callers treat "unknown" as "no
    workers" and fail fast instead of waiting forever.
    """
    own_pid = os.getpid()
    found: Dict[int, Dict[str, Any]] = {}
    try:
        import psutil

        try:
            me = psutil.Process(own_pid)
            children = me.children(recursive=True)
        except Exception:
            children = []
        try:
            group = os.getpgid(own_pid)
        except Exception:
            group = None
        group_members: List[Any] = []
        if group is not None and os.name != "nt":
            try:
                for proc in psutil.process_iter(["pid"]):
                    try:
                        if os.getpgid(proc.pid) == group:
                            group_members.append(proc)
                    except Exception:
                        continue
            except Exception:
                group_members = []
        for proc in list(children) + group_members:
            try:
                pid = int(proc.pid)
            except Exception:
                continue
            if pid == own_pid or pid in found:
                continue
            try:
                name = str(proc.name() or "")
                cmdline = _proc_cmdline(proc)
                rss = int(proc.memory_info().rss // (1024 * 1024))
            except Exception:
                continue
            if _is_media_worker(name, cmdline):
                found[pid] = {"pid": pid, "name": name, "rss_mb": rss, "cmdline": cmdline[:300]}
        return [found[pid] for pid in sorted(found)]
    except Exception:
        pass
    # Fallback without psutil: scan /proc (Linux only) for group members
    # and direct children, matching on the same name/cmdline rules.
    if os.name == "nt":
        return []
    try:
        own_group = os.getpgid(own_pid)
    except Exception:
        return []
    try:
        proc_ids = [entry for entry in os.listdir("/proc") if entry.isdigit()]
    except Exception:
        return []
    for pid_text in proc_ids:
        pid = int(pid_text)
        if pid == own_pid or pid in found:
            continue
        try:
            with open(f"/proc/{pid}/stat", "r", encoding="utf-8", errors="replace") as handle:
                stat_parts = handle.read().rsplit(")", 1)[-1].split()
            ppid = int(stat_parts[1])
            pgrp = int(stat_parts[2])
            if ppid != own_pid and pgrp != own_group:
                continue
            with open(f"/proc/{pid}/cmdline", "rb") as handle:
                raw = handle.read().replace(b"\x00", b" ").decode("utf-8", "replace").strip()
            with open(f"/proc/{pid}/status", "r", encoding="utf-8", errors="replace") as handle:
                status_text = handle.read()
            name = ""
            rss_kb = 0
            for line in status_text.splitlines():
                if line.startswith("Name:"):
                    name = line.split(":", 1)[1].strip()
                elif line.startswith("VmRSS:"):
                    rss_kb = int(line.split()[1])
            if _is_media_worker(name, raw):
                found[pid] = {"pid": pid, "name": name, "rss_mb": rss_kb // 1024, "cmdline": raw[:300]}
        except Exception:
            continue
    return [found[pid] for pid in sorted(found)]


def wait_for_render_memory(
    prepare,
    *,
    wait_deadline,
    base_wait_seconds=120.0,
    poll_seconds=DEFAULT_MEMORY_POLL_SECONDS,
    worker_extend_seconds=DEFAULT_MEMORY_WORKER_EXTEND_SECONDS,
    no_worker_grace_seconds=DEFAULT_MEMORY_NO_WORKER_GRACE_SECONDS,
    live_workers=None,
    on_wait=None,
    check_active=None,
    sleep=None,
    monotonic=None,
):
    """Wait for render admission, releasing promptly when workers exit.

    ``prepare`` is a zero-argument callable returning the render snapshot
    or raising :class:`RenderMemoryLowError`. The wait:

    * re-checks admission every ``poll_seconds`` (default 0.5s) instead of
      the old fixed 2s sleep, so a render starts within ~0.5s of the
      worker exit that freed the memory;
    * while any of this job's media workers (see :func:`live_media_workers`)
      are still alive, extends the deadline in ``worker_extend_seconds``
      steps — those workers will free memory when they finish, so expiring
      the wait (and pausing the job) while they run is a false stall.
      Extensions never pass ``wait_deadline`` (the delivery budget);
    * once no media worker is alive, fails fast after
      ``no_worker_grace_seconds`` instead of burning the whole base wait:
      with nothing left to free memory, waiting longer cannot help;
    * reclaims this process's own garbage (``gc`` + ``malloc_trim``) when
      the worker set shrinks, i.e. exactly at the moment memory freed.

    Raises the last :class:`RenderMemoryLowError` when the (possibly
    extended) deadline passes or the no-worker grace expires.
    """
    live_workers = live_workers or live_media_workers
    # Resolve late (not as default args) so a patched time.sleep /
    # time.monotonic — e.g. in tests — is honoured by the wait loop.
    sleep = sleep or time.sleep
    monotonic = monotonic or time.monotonic
    poll = max(0.05, float(poll_seconds))
    extend = max(1.0, float(worker_extend_seconds))
    grace = max(0.0, float(no_worker_grace_seconds))
    memory_deadline = min(float(wait_deadline), monotonic() + max(1.0, float(base_wait_seconds)))
    no_worker_since = None
    previous_worker_pids: set[int] = set()
    last_error = None
    last_report_at = 0.0
    first_report = True
    while True:
        if check_active is not None:
            check_active()
        try:
            return prepare()
        except RenderMemoryLowError as exc:
            last_error = exc
        now = monotonic()
        if now >= memory_deadline:
            raise last_error
        try:
            workers = list(live_workers() or [])
        except Exception:
            workers = []
        worker_pids = {int(worker.get("pid") or 0) for worker in workers} - {0}
        extended = False
        if workers:
            no_worker_since = None
            new_deadline = min(float(wait_deadline), now + extend)
            if new_deadline > memory_deadline:
                memory_deadline = new_deadline
                extended = True
        else:
            if no_worker_since is None:
                no_worker_since = now
            elif now - no_worker_since >= grace:
                raise last_error
        if worker_pids < previous_worker_pids:
            # A worker exited since the last check: memory just freed, so
            # reclaim our own idle garbage now instead of on the next
            # prepare() call, which may be a full admission attempt.
            try:
                gc.collect(2)
            except Exception:
                pass
            _trim_current_process_working_set()
        previous_worker_pids = worker_pids
        if on_wait is not None and (first_report or extended or now - last_report_at >= 30.0):
            first_report = False
            last_report_at = now
            try:
                on_wait({
                    "available_mb": int((last_error.memory_snapshot or {}).get("available_mb") or 0),
                    "minimum_available_mb": MIN_REMOTION_AVAILABLE_MB,
                    "live_workers": workers,
                    "live_worker_rss_mb": sum(int(worker.get("rss_mb") or 0) for worker in workers),
                    "deadline_in_seconds": max(0.0, memory_deadline - now),
                    "extended": extended,
                })
            except Exception:
                pass
        sleep(poll)


class RenderMemoryLowError(RuntimeError):
    """A retryable render preflight failure with the measured RAM snapshot."""

    def __init__(self, message: str, snapshot: Dict[str, Any]):
        super().__init__(message)
        self.memory_snapshot = dict(snapshot or {})
        self.retryable = True
_PROTECTED_PROCESS_NAMES = {
    # Windows Core & System Services
    "system", "system idle process", "smss.exe", "csrss.exe", "wininit.exe",
    "winlogon.exe", "services.exe", "lsass.exe", "svchost.exe", "dwm.exe",
    "fontdrvhost.exe", "explorer.exe", "sihost.exe", "taskhostw.exe",
    "runtimebroker.exe", "startmenuexperiencehost.exe", "shellexperiencehost.exe",
    "searchhost.exe", "searchapp.exe", "taskmgr.exe", "msmpeng.exe",
    "spoolsv.exe", "ctfmon.exe", "securityhealthservice.exe",
    "memcompression", "memcompression.exe", "memory compression",

    # Vidzeo & CMS Ecosystem (Must stay active!)
    "vidnets.exe", "vidzeo.exe", "vidzeo app.exe", "node.exe", "python.exe",
    "pythonw.exe", "electron.exe", "ffmpeg.exe", "ffprobe.exe",

    # IX Browser (Explicitly mandated by AGENTS.md: Never terminate shared IX Browser)
    "ixbrowser.exe", "ix_browser.exe", "ixbrowser_proxy.exe",

    # Dev & Agent Environment
    "antigravity.exe", "antigravity", "language_server.exe", "language_server",
    "code.exe", "cursor.exe", "windsurf.exe", "codex.exe", "codex",
    "windowsterminal.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "conhost.exe",
    "git.exe", "ssh.exe"
}


def _enabled(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _memory_snapshot() -> Dict[str, int]:
    try:
        import psutil

        memory = psutil.virtual_memory()
        process_rss = psutil.Process(os.getpid()).memory_info().rss
        return {
            "total_mb": int(memory.total // (1024 * 1024)),
            "available_mb": int(memory.available // (1024 * 1024)),
            "free_mb": int(memory.free // (1024 * 1024)),
            "process_rss_mb": int(process_rss // (1024 * 1024)),
        }
    except Exception:
        return {"total_mb": 0, "available_mb": 0, "free_mb": 0, "process_rss_mb": 0}


def _trim_current_process_working_set() -> bool:
    """Ask the OS to page out this idle Flask process's resident pages only."""
    try:
        if os.name == "nt":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            psapi.EmptyWorkingSet.argtypes = [ctypes.c_void_p]
            psapi.EmptyWorkingSet.restype = ctypes.c_int
            return bool(psapi.EmptyWorkingSet(kernel32.GetCurrentProcess()))
        if sys.platform.startswith("linux"):
            import ctypes.util
            libc_name = ctypes.util.find_library("c") or "libc.so.6"
            try:
                libc = ctypes.CDLL(libc_name)
            except OSError:
                libc = ctypes.CDLL(None)
            malloc_trim = getattr(libc, "malloc_trim", None)
            if malloc_trim is not None:
                malloc_trim.argtypes = [ctypes.c_size_t]
                malloc_trim.restype = ctypes.c_int
                return bool(malloc_trim(0))
    except Exception:
        return False
    return False


def _release_cached_gpu_blocks() -> bool:
    """Release only unused CUDA allocator blocks if Torch is already loaded."""
    torch = sys.modules.get("torch")
    if torch is None:
        return False
    try:
        if torch.cuda.is_available() and torch.cuda.is_initialized():
            torch.cuda.empty_cache()
            return True
    except Exception:
        pass
    return False


def _current_user_session_id() -> int:
    if os.name != "nt":
        return -1
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    session_id = ctypes.c_ulong()
    kernel32.ProcessIdToSessionId.argtypes = [ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
    kernel32.ProcessIdToSessionId.restype = ctypes.c_int
    if not kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session_id)):
        return -1
    return int(session_id.value)


def _visible_windows_by_process() -> Dict[int, List[int]]:
    """Return visible top-level Windows owned by each process ID."""
    if os.name != "nt":
        return {}
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    windows: Dict[int, List[int]] = {}
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD

    def collect(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value:
            windows.setdefault(int(pid.value), []).append(int(hwnd))
        return True

    callback = callback_type(collect)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.EnumWindows(callback, 0)
    return windows


def _process_ancestors(pid: int) -> set[int]:
    try:
        import psutil

        current = psutil.Process(pid)
        ancestors = {pid}
        for parent in current.parents():
            ancestors.add(parent.pid)
        return ancestors
    except Exception:
        return {pid}


def _close_visible_user_apps_until_headroom(target_mb: int) -> Dict[str, Any]:
    """Preserve interactive apps; render admission adapts workers to free RAM.

    A visible window does not establish ownership. Closing an agent host can
    terminate its entire CMS child tree and lose an otherwise healthy render.
    Only this engine's own caches/working set may be trimmed automatically.
    """
    return {
        "apps_requested_to_close": [],
        "apps_force_terminated": [],
        "apps_close_failures": [],
        "skipped_reason": "user_apps_are_not_owned_render_resources",
    }


def select_remotion_workers(
    available_mb: int,
    total_mb: int,
    width: int = 1920,
    height: int = 1080,
    cpu_count: int | None = None,
    video_heavy: bool = False,
) -> Dict[str, int]:
    """Scale workers by headroom, admitting one worker above the paging floor."""
    total_bytes = max(1, int(total_mb)) * 1024 * 1024
    available_bytes = max(0, int(available_mb)) * 1024 * 1024
    cpu_total = max(1, int(cpu_count or os.cpu_count() or 1))
    cpu_limit = max(1, min(MAX_REMOTION_WORKERS, cpu_total - 1 if cpu_total > 2 else cpu_total))
    reserve_bytes = min(2 * 1024**3, max(384 * 1024**2, int(total_bytes * 0.07)))
    frame_bytes = max(1, int(width)) * max(1, int(height)) * 4
    configured_worker_mb = 256
    # Frame buffers are pipelining, not output quality: in extreme
    # low-memory mode (< 1024 MB available, the regime where renders get
    # held) drop to a single 1080p RGBA buffer (~8 MB saved per worker).
    # Above that keep 2 buffers so throughput does not regress.
    if video_heavy:
        frame_buffers = 1 if available_mb < 1024 else (2 if available_mb < 3072 else 8)
    else:
        frame_buffers = 1
    worker_overhead_mb = 128 if video_heavy else 64
    worker_bytes = configured_worker_mb * 1024**2 + frame_bytes * frame_buffers + worker_overhead_mb * 1024**2
    shared_bytes = ((128 if available_mb < 3072 else 256) + 96) * 1024**2
    raw_memory_limit = max(0, (available_bytes - reserve_bytes - shared_bytes) // max(1, worker_bytes + 64 * 1024**2))
    # The reserve is a parallelism budget, not a mandatory allocation. Under
    # pressure it can exceed all available RAM and incorrectly prevent even a
    # single worker from starting forever. Permit serial rendering above the
    # admission floor; its actual working set still depends on scene contents.
    single_worker_fallback = available_mb >= MIN_REMOTION_AVAILABLE_MB and raw_memory_limit < 1
    memory_limit = max(MIN_REMOTION_WORKERS, raw_memory_limit) if available_mb >= MIN_REMOTION_AVAILABLE_MB else 0
    selected = min(MAX_REMOTION_WORKERS, cpu_limit, int(memory_limit))
    # The memory estimate is a hard ceiling. Do not force a second worker just
    # because the machine has multiple cores; low-RAM renders stay serial.
    return {
        "recommended_workers": int(selected),
        "max_workers": MAX_REMOTION_WORKERS,
        "cpu_limit": int(cpu_limit),
        "memory_limit": int(memory_limit),
        "raw_memory_limit": int(raw_memory_limit),
        "single_worker_fallback": int(single_worker_fallback),
        "available_mb": int(available_mb),
        "reserve_mb": int(reserve_bytes // (1024 * 1024)),
        "estimated_worker_mb": int(round(worker_bytes / (1024 * 1024))),
        "frame_buffers": frame_buffers,
        "worker_overhead_mb": worker_overhead_mb,
        "video_heavy": int(bool(video_heavy)),
        "minimum_available_mb": MIN_REMOTION_AVAILABLE_MB,
        "preferred_available_mb": int((reserve_bytes + shared_bytes + worker_bytes + 64 * 1024**2) // (1024**2) + 1),
    }


def render_memory_status() -> Dict[str, Any]:
    """Expose the same admission decision to the UI as the render preflight."""
    snapshot = dict(_memory_snapshot())
    known = int(snapshot.get('total_mb') or 0) > 0
    available = max(0, int(snapshot.get('available_mb') or 0))
    plan = select_remotion_workers(available, int(snapshot.get('total_mb') or 0), video_heavy=True)
    blocked = known and plan['recommended_workers'] < MIN_REMOTION_WORKERS
    warning = known and (available < 1024 or bool(plan.get('single_worker_fallback')))
    state = 'unknown' if not known else ('waiting' if blocked else ('low_memory' if warning else 'ready'))
    message = (
        'Memory measurement is unavailable; the renderer will check before starting.' if not known else
        f'Waiting for memory: {available} MB available; {MIN_REMOTION_AVAILABLE_MB} MB is needed before starting a render.' if blocked else
        f'Low-memory mode: {available} MB available. Rendering can continue with one worker.' if warning else
        'Memory headroom is sufficient for rendering.'
    )
    return {**snapshot, **plan, 'warning': warning, 'warning_threshold_mb': 1024,
            'render_blocked': blocked, 'can_start_render': None if not known else not blocked,
            'admission_state': state, 'message': message,
            # Machine-readable hold reason for the wrapper/API: empty unless
            # a render is actually held, so a held render is unambiguous.
            'held_reason': message if blocked else ''}


def prepare_render_resources(
    require_remotion: bool = True,
    width: int = 1920,
    height: int = 1080,
    video_heavy: bool = False,
    release_idle: bool = True,
) -> Dict[str, Any]:
    """Collect idle Vidnets memory and choose a safe adaptive render count.

    This routine never closes other desktop apps. It trims only Vidnets' own
    idle working set, then selects up to eight workers from the memory and CPU
    headroom available at render start.
    """
    before = _memory_snapshot()
    collected = gc.collect(2) if release_idle else 0
    gpu_cache_released = _release_cached_gpu_blocks() if release_idle else False

    # Trim only when memory is tight. The threshold follows both a practical
    # 2 GiB floor and 20% of installed RAM, avoiding pointless page faults on
    # machines that already have comfortable headroom.
    threshold_mb = max(2048, int(before.get("total_mb", 0) * 0.20))
    should_trim = (
        release_idle and _enabled(os.environ.get("VIDZEO_RENDER_TRIM_WORKING_SET"), True)
        and before.get("available_mb", 0) > 0
        and before["available_mb"] < threshold_mb
    )
    trimmed = _trim_current_process_working_set() if should_trim else False
    after_trim = _memory_snapshot()

    # Close non-essential user apps only if memory is constrained (< 2560 MB or worker count < 2)
    # strictly keeping CMS, Vidnets, IX Browser, Antigravity, and Windows system processes active.
    # A render owns its process tree, not the user's other applications. A
    # stale environment flag must never turn memory admission into app closure.
    closed_apps: Dict[str, Any] = {}

    after = _memory_snapshot()
    worker_plan = select_remotion_workers(
        available_mb=after.get("available_mb", 0),
        total_mb=after.get("total_mb", 0),
        width=width,
        height=height,
        video_heavy=video_heavy,
    ) if require_remotion else {
        "recommended_workers": 0,
        "max_workers": MAX_REMOTION_WORKERS,
        "cpu_limit": 0,
        "memory_limit": 0,
        "available_mb": int(after.get("available_mb", 0)),
        "reserve_mb": 0,
        "estimated_worker_mb": 0,
        "frame_buffers": 0,
        "worker_overhead_mb": 0,
        "video_heavy": int(bool(video_heavy)),
    }
    snapshot = {
        **after,
        'process_rss_mb': int(after.get('process_rss_mb') or 0),
        "available_before_mb": int(before.get("available_mb", 0)),
        "process_rss_before_mb": int(before.get("process_rss_mb", 0)),
        "collected_objects": int(collected),
        "working_set_trimmed": int(trimmed),
        "gpu_cache_released": int(gpu_cache_released),
        "closed_apps": closed_apps.get("apps_requested_to_close", []),
        "terminated_apps": closed_apps.get("apps_force_terminated", []),
        **worker_plan,
        "selected_workers": int(worker_plan.get("recommended_workers", 0)),
        "low_memory_warning": bool(int(after.get("available_mb", 0) or 0) < 1024 or worker_plan.get('single_worker_fallback')),
        "warning_threshold_mb": 1024,
    }
    print(
        "   [render-memory] prepared: "
        f"available={snapshot['available_before_mb']}->{snapshot['available_mb']}MB, "
        f"Flask RSS={snapshot['process_rss_before_mb']}->{snapshot['process_rss_mb']}MB, "
        f"gc={snapshot['collected_objects']}, "
        f"workingSetTrim={'yes' if trimmed else 'no'}, "
        f"unusedGpuCache={'released' if gpu_cache_released else 'none'}, "
        f"closedApps={len(snapshot.get('closed_apps', []))}, "
        f"workers={snapshot['selected_workers']}/{snapshot['max_workers']} "
        f"(memory-fit={snapshot['memory_limit']}, cpu-limit={snapshot['cpu_limit']}, "
        f"serial-admission={'yes' if snapshot.get('single_worker_fallback') else 'no'})"
    )
    if require_remotion and not snapshot.get('total_mb'):
        # Node performs its own OS memory admission before loading Chromium.
        # Missing Python telemetry must not create an endless wait for 0 MB.
        snapshot.update(selected_workers=1, recommended_workers=1, memory_measurement_unavailable=True)
    elif require_remotion and snapshot["selected_workers"] < MIN_REMOTION_WORKERS:
        # Recheck only our released working set before a resumable pause.
        emergency_cleanup = {}
        after_recovery = _memory_snapshot()
        recovery_plan = select_remotion_workers(
            available_mb=after_recovery.get("available_mb", 0),
            total_mb=after_recovery.get("total_mb", 0),
            width=width,
            height=height,
            video_heavy=video_heavy,
        )
        if recovery_plan.get("recommended_workers", 0) >= MIN_REMOTION_WORKERS:
            snapshot.update(
                **after_recovery,
                **recovery_plan,
                selected_workers=int(recovery_plan["recommended_workers"]),
                emergency_cleanup=emergency_cleanup
            )
            return snapshot
        # A held render must be unmistakable in the server log: one line
        # naming the measured headroom and the floor, so the wrapper/job
        # error is never mistaken for unanswered inference requests.
        print(
            "   [render-memory] HELD: render refused: only "
            f"{after_recovery.get('available_mb', 0)} MB available; at least "
            f"{MIN_REMOTION_AVAILABLE_MB} MB is needed for one Remotion worker. "
            "Free memory and retry.",
            flush=True,
        )
        # Report the freshest measurement, not the stale one: the snapshot
        # was built from the earlier `after` reading, and the paused job
        # (and the wrapper's memory note) would otherwise quote an
        # available_mb that is already out of date.
        for key in ("total_mb", "available_mb", "free_mb", "process_rss_mb"):
            if after_recovery.get(key) is not None:
                snapshot[key] = int(after_recovery[key])
        snapshot["available_at_raise_mb"] = int(after_recovery.get("available_mb") or 0)
        raise RenderMemoryLowError(
            "Available memory is below the admission floor for one Remotion worker. "
            f"Only {after_recovery.get('available_mb', 0)} MB is available; "
            f"at least {MIN_REMOTION_AVAILABLE_MB} MB is needed to attempt serial rendering. "
            "Free memory and retry.",
            snapshot,
        )

    return snapshot
