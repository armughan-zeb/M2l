"""Ground-truth activity for long-running pipeline lanes.

The preparation-stall detector used to read only display progress: a
``(progress, text)`` change in the queue's ``update_progress``. Display
progress passes through sanitising, de-duplication and a stale-count
filter, so a working lane whose scene counter restarts (a web recovery
wave) or whose label never changes (vision rounds) looked idle for
300s and the job was paused. Lanes record raw activity here instead;
the detector reads it directly, so liveness never depends on what the
UI chose to display.

This module is a leaf: it imports only the standard library, so any
pipeline stage can record activity without import cycles. One job
runs at a time on this install; a new queue attempt resets the clock
so activity from a previous attempt cannot mask a stall in this one.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

_lock = threading.Lock()
_last_by_source: dict[str, float] = {}


def record_activity(source: str) -> None:
    """Note that ``source`` did real work right now. Never raises."""
    try:
        with _lock:
            _last_by_source[str(source or "lane")] = time.monotonic()
    except Exception:
        pass


def reset_activity(reason: str = "job boundary") -> None:
    """Forget activity from a previous attempt.

    A new queue attempt starts from a clean clock: activity recorded
    by the previous attempt must not mask a stall in this one.
    """
    try:
        with _lock:
            _last_by_source.clear()
    except Exception:
        pass


def last_activity() -> Optional[float]:
    """Monotonic timestamp of the newest recorded activity, if any."""
    try:
        with _lock:
            if not _last_by_source:
                return None
            return max(_last_by_source.values())
    except Exception:
        return None
