"""Job identity and wall budget propagated through nested preparation workers."""
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from concurrent.futures import ThreadPoolExecutor
import threading
import time

_CURRENT = ContextVar('vidzeo_job_context', default=None)


def current_job():
    return dict(_CURRENT.get() or {})


def remaining_seconds(default=None):
    """Seconds left on the current job's deadline, or ``default``.

    When a delivery budget is attached, its *live* deadline is the source of
    truth — the budget's deadline moves forward on every genuine progress
    report (``DeliveryBudget.note_progress``), so a job that keeps moving
    must not be killed by a stale snapshot. The context's ``deadline_at`` is
    only the snapshot taken when the job started; when a budget exists it is
    ignored in favour of the live deadline. A tighter phase deadline set by
    ``phase_scope`` (``phase_deadline_at``) still binds as a minimum, so
    short per-stage walls keep working. With no budget, the context deadline
    is used as before.
    """
    context = current_job()
    budget = context.get('delivery_budget')
    if budget is not None and getattr(budget, 'render_started', False):
        deadline = context.get('phase_deadline_at')
        return max(0.0, deadline - time.monotonic()) if deadline is not None else float('inf')
    candidates = []
    if budget is not None:
        live = getattr(budget, 'deadline_at', None)
        if live is not None:
            clock = getattr(budget, 'clock', None)
            now = float(clock()) if callable(clock) else time.monotonic()
            candidates.append(float(live) - now)
    phase_deadline = context.get('phase_deadline_at')
    if phase_deadline is not None:
        candidates.append(float(phase_deadline) - time.monotonic())
    if budget is None:
        deadline = context.get('deadline_at')
        if deadline is not None:
            candidates.append(float(deadline) - time.monotonic())
    if candidates:
        return max(0.0, min(candidates))
    return default


def check_job_active():
    context = current_job()
    finished = context.get('finished_event')
    if finished is not None and finished.is_set():
        raise InterruptedError('This video attempt has finished; late work cannot be published.')
    cancellation = context.get('cancellation')
    if cancellation is not None:
        cancellation.raise_if_cancelled()
    if remaining_seconds(1) <= 0:
        raise TimeoutError('The video processing deadline has been reached; retry from its checkpoint.')


@contextmanager
def job_scope(context):
    token = _CURRENT.set(dict(context))
    try:
        yield
    finally:
        _CURRENT.reset(token)


@contextmanager
def phase_scope(seconds):
    context = current_job()
    deadline = time.monotonic() + max(0, float(seconds))
    context['deadline_at'] = min(deadline, context.get('deadline_at', deadline))
    context['phase_deadline_at'] = min(deadline, context.get('phase_deadline_at', deadline))
    with job_scope(context):
        yield


class JobThreadPoolExecutor(ThreadPoolExecutor):
    """Copy context per task; one job cannot inherit a previous pool task's state."""
    def submit(self, fn, /, *args, **kwargs):
        context = copy_context()
        def scoped_task():
            check_job_active()
            result = fn(*args, **kwargs)
            check_job_active()
            return result
        return super().submit(context.run, scoped_task)

    def __exit__(self, exc_type, exc_value, traceback):
        if current_job():
            # Context-managed executor shutdown must not join stalled sockets
            # beyond the caller's deadline. Late results are rejected above.
            self.shutdown(wait=False, cancel_futures=exc_type is not None)
            return False
        return super().__exit__(exc_type, exc_value, traceback)


class JobThread(threading.Thread):
    """Propagate only job state to direct background workers, not Flask sessions."""
    def __init__(self, *args, **kwargs):
        self._job = current_job()
        super().__init__(*args, **kwargs)

    def run(self):
        with job_scope(self._job):
            super().run()
