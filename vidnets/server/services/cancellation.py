"""Job-scoped cooperative cancellation and child-process cleanup."""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


class JobCancelled(InterruptedError):
    """Raised when a queue job is cancelled by the operator."""


@dataclass
class CancellationContext:
    job_id: str = ""
    event: threading.Event = field(default_factory=threading.Event)
    reason: str = ""
    parent: Optional["CancellationContext"] = field(default=None, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _processes: Dict[int, subprocess.Popen] = field(default_factory=dict, repr=False)
    _futures: Dict[int, Future] = field(default_factory=dict, repr=False)
    # Child contexts are used by nested pipeline stages (search -> download ->
    # ffmpeg).  Cancellation used to set only the parent event; a child could
    # therefore keep its own registered yt-dlp/ffmpeg processes alive until
    # their individual timeouts elapsed.  Keep a small in-memory registry so a
    # stage wall is a real stop signal all the way down the call tree.
    _children: Dict[int, "CancellationContext"] = field(default_factory=dict, repr=False)

    def cancelled(self) -> bool:
        return self.event.is_set() or bool(self.parent and self.parent.cancelled())

    def raise_if_cancelled(self) -> None:
        if self.cancelled():
            inherited_reason = self.parent.reason if self.parent and self.parent.cancelled() else ""
            raise JobCancelled(self.reason or inherited_reason or "Job cancelled by user")

    def child(self, job_id: str = "") -> "CancellationContext":
        """Create a stage-scoped context linked to this job.

        Cancelling the child stops only that stage. Cancelling the parent is
        still observed by every child, so a queue cancellation propagates
        without forcing a stage timeout to cancel the whole job.
        """
        child_id = job_id or (f"{self.job_id}:child" if self.job_id else "")
        child = CancellationContext(job_id=child_id, parent=self)
        with self._lock:
            self._children[id(child)] = child
        if self.cancelled():
            # A child created just as its parent is being cancelled must not
            # open a race where it misses the parent stop signal.
            child.cancel(self.reason or "Parent cancellation")
        return child

    def register_process(self, process: subprocess.Popen) -> subprocess.Popen:
        with self._lock:
            self._processes[process.pid] = process
        if self.cancelled():
            self._terminate_process(process)
        return process

    def unregister_process(self, process: Optional[subprocess.Popen]) -> None:
        if process is None:
            return
        with self._lock:
            self._processes.pop(getattr(process, "pid", None), None)

    def register_future(self, future: Future) -> Future:
        with self._lock:
            self._futures[id(future)] = future
        future.add_done_callback(self.unregister_future)
        return future

    def _prune_completed(self) -> None:
        with self._lock:
            completed = [key for key, future in self._futures.items() if future.done()]
            for key in completed:
                self._futures.pop(key, None)
            finished = [pid for pid, process in self._processes.items() if process.poll() is not None]
            for pid in finished:
                self._processes.pop(pid, None)

    def wait_for_quiescence(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout or 0.0))
        while True:
            self._prune_completed()
            if self.active_process_count() == 0 and self.active_future_count() == 0:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    def unregister_future(self, future: Optional[Future]) -> None:
        if future is None:
            return
        with self._lock:
            self._futures.pop(id(future), None)

    def cancel(self, reason: str = "Job cancelled by user") -> None:
        self.reason = reason
        self.event.set()
        with self._lock:
            futures = list(self._futures.values())
            processes = list(self._processes.values())
            children = list(self._children.values())
        for future in futures:
            try:
                future.cancel()
            except Exception:
                pass
        for process in processes:
            self._terminate_process(process)
        # Propagate actively, rather than relying on a later cooperative
        # poll.  This terminates grandchildren registered on nested contexts
        # and makes aggregate stage walls meaningful on slow/stalled hosts.
        for child in children:
            try:
                child.cancel(reason)
            except Exception:
                pass

    def active_process_count(self) -> int:
        with self._lock:
            return len(self._processes)

    def active_future_count(self) -> int:
        with self._lock:
            return len(self._futures)

    def clear_registries(self) -> None:
        with self._lock:
            self._futures.clear()
            self._processes.clear()
            self._children.clear()

    @staticmethod
    def _terminate_process(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=5,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            else:
                os.killpg(process.pid, signal.SIGTERM)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
        # Windows can report the parent as terminated while a child ffmpeg
        # process still owns an output file.  Always reap the Popen handle
        # after termination; otherwise TemporaryDirectory cleanup races the
        # still-open ``.ts`` file and masks the original timeout as WinError 32
        # / WinError 267.
        try:
            process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
                process.wait(timeout=2)
            except Exception:
                pass


def run_cancellable_process(
    command,
    *,
    cancellation: Optional[CancellationContext] = None,
    timeout: Optional[float] = None,
    cwd=None,
    **kwargs: Any,
) -> subprocess.CompletedProcess:
    """Run a child process while observing queue cancellation."""
    if cancellation is not None:
        cancellation.raise_if_cancelled()
    if kwargs.pop("capture_output", False):
        kwargs.setdefault("stdout", subprocess.PIPE)
        kwargs.setdefault("stderr", subprocess.PIPE)
    process = subprocess.Popen(command, cwd=cwd, **kwargs)
    if cancellation is not None:
        cancellation.register_process(process)
    started = time.monotonic()
    try:
        while True:
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            remaining = None
            if timeout is not None:
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
            slice_timeout = 0.2 if remaining is None else min(0.2, max(0.01, remaining))
            try:
                stdout, stderr = process.communicate(timeout=slice_timeout)
                return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                if remaining is not None and (time.monotonic() - started) >= timeout:
                    raise
    except JobCancelled:
        if cancellation is not None:
            cancellation._terminate_process(process)
        raise
    except BaseException:
        if process.poll() is None:
            CancellationContext._terminate_process(process)
        else:
            # A timeout/kill may have completed between the poll and this
            # handler.  Reap it anyway so pipes and file handles are closed
            # before the caller tears down its temporary render directory.
            try:
                process.wait(timeout=2)
            except Exception:
                pass
        raise
    finally:
        if cancellation is not None:
            cancellation.unregister_process(process)
