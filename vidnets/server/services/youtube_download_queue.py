"""Background download queue for verified YouTube clips.

Discovery (search, rank, transcript, probe, vision) is bounded by the
discovery budget. Downloads are NOT: once a clip is vision-verified, its
ranged download is submitted here and runs in parallel in the background.
Downloads may finish after discovery ends; the media stage completes when
discovery ends AND every resolved clip's download has finished.

The queue is transport-agnostic: callers submit a zero-argument callable
that performs one ranged download and returns its local path (or raises).
The queue only owns concurrency, bookkeeping, and the drain.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Any, Callable, Dict, List, Optional

DEFAULT_MAX_WORKERS = 4


class YouTubeDownloadQueue:
    """Run verified-clip downloads in parallel, in the background."""

    def __init__(self, *, max_workers: int = DEFAULT_MAX_WORKERS) -> None:
        self.max_workers = max(1, int(max_workers or DEFAULT_MAX_WORKERS))
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers, thread_name_prefix="yt-download"
        )
        self._lock = threading.Lock()
        self._futures: Dict[Future, str] = {}
        self._results: Dict[str, Optional[str]] = {}
        self._errors: Dict[str, str] = {}
        self._shutdown = False

    def submit(self, key: str, download: Callable[[], Optional[str]]) -> Future:
        """Queue one download. ``key`` identifies the clip (video id or
        scene-scoped key); ``download`` returns the local path or raises."""
        if self._shutdown:
            raise RuntimeError("download queue is shut down")

        def _run() -> Optional[str]:
            return download()

        future = self._executor.submit(_run)
        with self._lock:
            self._futures[future] = str(key)

        def _record(done: Future) -> None:
            try:
                path = done.result()
            except Exception as exc:  # noqa: BLE001 - record, do not raise
                with self._lock:
                    self._errors[str(key)] = f"{type(exc).__name__}: {exc}"
                    self._results[str(key)] = None
            else:
                with self._lock:
                    self._results[str(key)] = path

        future.add_done_callback(_record)
        return future

    def pending_count(self) -> int:
        with self._lock:
            return sum(1 for future in self._futures if not future.done())

    def submitted_count(self) -> int:
        with self._lock:
            return len(self._futures)

    def wait_all(self, timeout: Optional[float] = None) -> Dict[str, Optional[str]]:
        """Block until every submitted download finishes (or timeout).

        Returns the key -> local-path map (None for failed downloads).
        A timeout leaves unfinished downloads running in the background;
        call again to drain further. Never raises for download failures:
        a failed download is a None path the caller falls back from.
        """
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        while True:
            with self._lock:
                pending = [f for f in self._futures if not f.done()]
            if not pending:
                break
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            if remaining is not None and remaining <= 0:
                break
            done, _ = wait(pending, timeout=min(0.25, remaining) if remaining is not None else 0.25,
                           return_when=FIRST_COMPLETED)
            if not done and remaining is not None and remaining <= 0:
                break
        with self._lock:
            return dict(self._results)

    def errors(self) -> Dict[str, str]:
        with self._lock:
            return dict(self._errors)

    def shutdown(self, *, wait: bool = True) -> None:
        with self._lock:
            self._shutdown = True
        self._executor.shutdown(wait=wait, cancel_futures=False)

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "max_workers": self.max_workers,
                "submitted": len(self._futures),
                "pending": sum(1 for f in self._futures if not f.done()),
                "finished": sum(1 for f in self._futures if f.done()),
                "failed": len(self._errors),
            }
