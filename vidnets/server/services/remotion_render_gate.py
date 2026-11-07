"""One process-wide slot for memory-intensive Remotion renders."""

from __future__ import annotations

import threading
import time
import os
from pathlib import Path
from typing import Any, Callable


REMOTION_RENDER_GATE = threading.Semaphore(1)


class _ProcessRenderSlot:
    """One render across desktop and headless processes for this OS user."""
    def __init__(self):
        default = Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'Vidnets Studio' / 'render.lock'
        path = Path(os.environ.get('VIDNETS_RENDER_LOCK_FILE') or default)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = path.open('a+b')
        self.stream.seek(0, 2)
        if self.stream.tell() == 0:
            self.stream.write(b'0')
            self.stream.flush()
        self.locked = False

    def acquire(self):
        try:
            self.stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.locked = True
            return True
        except OSError:
            return False

    def close(self):
        try:
            if self.locked:
                self.stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        finally:
            self.stream.close()


def run_with_remotion_render_slot(
    render_call: Callable[[], Any],
    *,
    cancellation: Any = None,
    progress_callback: Callable[[float, str], Any] | None = None,
    gate: Any = None,
    timeout_seconds: float | None = None,
    continue_while_active: bool | None = None,
) -> Any:
    """Serialize Remotion work and keep cancellation responsive while waiting."""
    active_gate = gate if gate is not None else REMOTION_RENDER_GATE
    waiting_reported = False
    last_report = 0.0
    import math
    from .job_context import check_job_active, remaining_seconds, current_job
    continuous = continue_while_active if continue_while_active is not None else os.environ.get('VIDZEO_RENDER_CONTINUE_WHILE_ACTIVE', '1').lower() in {'1', 'true', 'yes', 'on'}
    if continuous and timeout_seconds is None:
        budget = current_job().get('delivery_budget')
        if budget is not None:
            budget.begin_render_phase()
        # Waiting in queue for the single render slot must not fail after 20 minutes:
        # a long documentary video render can take 30-60 minutes. Allow up to 4 hours in queue.
        requested = float(os.environ.get('VIDNETS_RENDER_SLOT_WAIT_SECONDS', '14400.0'))
    else:
        requested = float(timeout_seconds if timeout_seconds is not None else os.environ.get('VIDNETS_RENDER_SLOT_WAIT_SECONDS', '14400.0'))
    if math.isnan(requested) or requested <= 0:
        raise TimeoutError('Video render slot wait budget is exhausted.')
    deadline = time.monotonic() + requested
    while not active_gate.acquire(blocking=True, timeout=0.5):
        check_job_active()
        if time.monotonic() >= deadline:
            error = TimeoutError('Waiting for the previous render exceeded this job budget. Retry the saved job.')
            error.stage = 'remotion_render'
            error.retryable = True
            raise error
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        now = time.monotonic()
        if not waiting_reported or now - last_report >= 4.0:
            waiting_reported = True
            last_report = now
            message = "Waiting for previous video render to finish in queue..."
            print(f"   [render-gate] {message}", flush=True)
            print(f'__VIDZEO_ULTRA_PROGRESS__{{"progress": 70, "message": "{message}", "waitingForRenderSlot": true, "timestamp": {now}}}', flush=True)
            if progress_callback:
                try:
                    progress_callback(70.0, message)
                except Exception:
                    pass
    process_slot = None
    try:
        if gate is None or gate is REMOTION_RENDER_GATE:
            process_slot = _ProcessRenderSlot()
            while not process_slot.acquire():
                check_job_active()
                if cancellation is not None:
                    cancellation.raise_if_cancelled()
                if time.monotonic() >= deadline:
                    error = TimeoutError('Another Vidnets process is rendering. Retry the saved job.')
                    error.stage = 'remotion_render'
                    error.retryable = True
                    raise error
                now = time.monotonic()
                if not waiting_reported or now - last_report >= 4.0:
                    waiting_reported, last_report = True, now
                    message = "Waiting for the other Vidnets render to finish in queue..."
                    print(f"   [render-gate] {message}", flush=True)
                    print(f'__VIDZEO_ULTRA_PROGRESS__{{"progress": 70, "message": "{message}", "waitingForRenderSlot": true, "timestamp": {now}}}', flush=True)
                    if progress_callback:
                        try:
                            progress_callback(70.0, message)
                        except Exception:
                            pass
                time.sleep(0.5)
        check_job_active()
        if time.monotonic() >= deadline:
            error = TimeoutError('The render slot became available after this job wait budget. Retry the saved job.')
            error.stage = 'remotion_render'
            error.retryable = True
            raise error
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        return render_call()
    finally:
        if process_slot is not None:
            process_slot.close()
        active_gate.release()
