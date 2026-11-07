"""Runtime state management and gallery-dl binary resolution."""
from __future__ import annotations

import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Dict, List, Optional

try:
    from config import TEMP_DIR
except ImportError:
    from server.config import TEMP_DIR

logger = logging.getLogger(__name__)


def _web_log(message: str) -> None:
    print(f"   [web] {message}", flush=True)


def _web_verbose() -> bool:
    return str(os.environ.get("VIDZEO_WEB_VERBOSE", "0")).strip().lower() in {"1", "true", "yes", "on"}


# ── Gallery-dl binary resolution ──────────────────────────────────────

_GALLERY_DL_CACHE: Optional[List[str]] = None
_GALLERY_DL_CACHE_LOCK = threading.Lock()


def _candidate_commands() -> List[List[str]]:
    import sys
    candidates = []

    # 1. Direct python module invocation (guaranteed in all packaged and virtual environments)
    if sys.executable and os.path.isfile(sys.executable):
        candidates.append([sys.executable, "-m", "gallery_dl"])

    # 2. Check candidate root folders for tools/python and venvs
    root_dirs = [
        Path(os.environ.get("VIDZEO_ROOT", "")).resolve() if os.environ.get("VIDZEO_ROOT") else None,
        Path(__file__).parent.parent.parent.parent,
        Path(sys.executable).parent.parent if getattr(sys, "frozen", False) else None,
        Path.cwd(),
        TEMP_DIR.parent,
    ]
    for root in filter(None, root_dirs):
        gdl_tool = root / "tools" / "python" / "Scripts" / "gallery-dl.exe"
        if gdl_tool.is_file():
            candidates.append([str(gdl_tool.resolve())])
        py_tool = root / "tools" / "python" / "python.exe"
        if py_tool.is_file():
            candidates.append([str(py_tool.resolve()), "-m", "gallery_dl"])
        v310 = root / "venv310" / "Scripts" / "gallery-dl.exe"
        if v310.is_file():
            candidates.append([str(v310.resolve())])
        v = root / "venv" / "Scripts" / "gallery-dl.exe"
        if v.is_file():
            candidates.append([str(v.resolve())])

    candidates.append(["gallery-dl"])
    return candidates



def resolve_gallery_dl(required: bool = True, force_rescan: bool = False) -> Optional[List[str]]:
    global _GALLERY_DL_CACHE
    timeout_s = 2.0
    try:
        timeout_s = max(0.5, min(5.0, float(os.environ.get("VIDZEO_GALLERY_DL_PROBE_TIMEOUT", "2"))))
    except Exception:
        timeout_s = 2.0
    with _GALLERY_DL_CACHE_LOCK:
        if not force_rescan and _GALLERY_DL_CACHE is not None:
            return _GALLERY_DL_CACHE if _GALLERY_DL_CACHE else None
        for cmd in _candidate_commands():
            try:
                r = subprocess.run(cmd + ["--version"], capture_output=True, text=True, timeout=timeout_s,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                if r.returncode == 0:
                    _GALLERY_DL_CACHE = cmd
                    version = r.stdout.strip()
                    if _web_verbose():
                        _web_log(f"gallery-dl found: {' '.join(cmd)} (v{version})")
                    return cmd
                else:
                    if _web_verbose():
                        _web_log(f"gallery-dl candidate '{' '.join(cmd)}' failed: exit={r.returncode}")
            except subprocess.TimeoutExpired:
                if _web_verbose():
                    _web_log(f"gallery-dl candidate '{' '.join(cmd)}' timed out")
            except Exception as e:
                if _web_verbose():
                    _web_log(f"gallery-dl candidate '{' '.join(cmd)}' error: {type(e).__name__}")
                continue
        _GALLERY_DL_CACHE = []
        if required:
            raise RuntimeError(
                "gallery-dl is required but not found. "
                "Install: pip install gallery-dl, or place gallery-dl.exe in venv310/Scripts/"
            )
        if _web_verbose():
            _web_log("gallery-dl not found; pipeline will use Bing Images fallback")
        return None


# Backward compat
_resolve_gallery_dl_command = resolve_gallery_dl


# ── Runtime state reset ───────────────────────────────────────────────

def reset_web_runtime_state(reason: str = "job boundary") -> None:
    # If other jobs are currently active in the queue, do NOT destroy shared web search state or temp images!
    try:
        from services.queue_manager import get_queue_manager
        qm = get_queue_manager()
        with qm._queue_lock:
            processing_jobs = [j for j in qm.queue if isinstance(j, dict) and j.get('status') == 'processing']
            if len(processing_jobs) > 1 or len(qm.active_jobs) > 1:
                _web_log(f"skipping runtime state reset ({reason}): multiple jobs active in queue ({len(processing_jobs)} processing, {len(qm.active_jobs)} active)")
                return
    except Exception:
        pass

    global _GALLERY_DL_CACHE
    with _GALLERY_DL_CACHE_LOCK:
        _GALLERY_DL_CACHE = None
    try:
        from web_pipeline.gallery_search import _gs
        _gs.clear_cache()
        try:
            _gs._stop_workers()
        except Exception:
            pass
    except (ImportError, AttributeError):
        pass
    try:
        img_dir = TEMP_DIR / "web_images"
        if img_dir.exists():
            for f in img_dir.iterdir():
                if f.is_file():
                    try:
                        f.unlink()
                    except Exception:
                        pass
    except Exception:
        pass
    _web_log(f"runtime state reset ({reason})")
