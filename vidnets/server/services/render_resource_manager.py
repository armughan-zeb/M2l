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
            libc = ctypes.CDLL("libc.so.6")
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
    frame_buffers = (2 if available_mb < 3072 else 8) if video_heavy else 1
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
            'admission_state': state, 'message': message}


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
        raise RenderMemoryLowError(
            "Available memory is below the admission floor for one Remotion worker. "
            f"Only {after_recovery.get('available_mb', 0)} MB is available; "
            f"at least {MIN_REMOTION_AVAILABLE_MB} MB is needed to attempt serial rendering. "
            "Free memory and retry.",
            snapshot,
        )

    return snapshot
