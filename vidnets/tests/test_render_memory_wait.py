"""Worker-aware render memory wait (Nov 10, 2026, RAM hygiene).

The old render wait slept a fixed 2s and expired after a flat 120s even
while this job's own media workers were still running, so the job paused
and burned wrapper retries until a retry landed after a worker exit.
The wait now re-checks every ~0.5s, extends while media workers are
alive (bounded by the delivery budget), and fails fast once none are
left to free memory.
"""

import shutil
import subprocess
import time

import pytest

from services import render_resource_manager as resources


class FakeClock:
    """monotonic() that advances 1s per sleep() call."""

    def __init__(self, start=1000.0):
        self.now = start
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += 1.0


def _low_error(available_mb):
    return resources.RenderMemoryLowError(
        f"only {available_mb} MB available",
        {"available_mb": available_mb, "total_mb": 8192},
    )


def test_wait_releases_promptly_when_workers_exit(monkeypatch):
    clock = FakeClock()
    calls = {"prepare": 0}
    workers = [[{"pid": 111, "name": "ffmpeg", "rss_mb": 64}], []]

    def prepare():
        calls["prepare"] += 1
        if calls["prepare"] < 3:
            raise _low_error(221)
        return {"selected_workers": 1, "available_mb": 900}

    seen = []

    snapshot = resources.wait_for_render_memory(
        prepare,
        wait_deadline=clock.now + 900,
        base_wait_seconds=120,
        poll_seconds=0.5,
        live_workers=lambda: workers.pop(0) if workers else [],
        on_wait=seen.append,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert snapshot["selected_workers"] == 1
    assert calls["prepare"] == 3
    # Admission was re-checked on the short poll, not the old 2s sleep.
    assert clock.sleeps and all(s == 0.5 for s in clock.sleeps)
    assert seen and seen[0]["available_mb"] == 221
    assert seen[0]["live_workers"] == [{"pid": 111, "name": "ffmpeg", "rss_mb": 64}]


def test_wait_extends_while_media_workers_are_alive():
    clock = FakeClock()
    prepare_calls = {"n": 0}

    def prepare():
        prepare_calls["n"] += 1
        raise _low_error(221)

    # Base wait 2s, but a worker stays alive the whole time: the deadline
    # must keep extending until the hard wait_deadline (100s), not expire
    # at the base 2s.
    with pytest.raises(resources.RenderMemoryLowError):
        resources.wait_for_render_memory(
            prepare,
            wait_deadline=clock.now + 100,
            base_wait_seconds=2,
            poll_seconds=0.5,
            worker_extend_seconds=30,
            live_workers=lambda: [{"pid": 111, "name": "ffmpeg", "rss_mb": 64}],
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )
    assert 95 <= clock.now - 1000.0 <= 101
    assert prepare_calls["n"] > 50  # polled throughout, not just once


def test_wait_fails_fast_once_no_worker_can_free_memory():
    clock = FakeClock()

    def prepare():
        raise _low_error(221)

    with pytest.raises(resources.RenderMemoryLowError):
        resources.wait_for_render_memory(
            prepare,
            wait_deadline=clock.now + 900,
            base_wait_seconds=120,  # must NOT burn the whole base wait
            poll_seconds=0.5,
            no_worker_grace_seconds=10,
            live_workers=lambda: [],
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )
    elapsed = clock.now - 1000.0
    assert 10 <= elapsed <= 13, f"failed fast after {elapsed}s, not 120s"


def test_wait_never_passes_the_delivery_budget_deadline():
    clock = FakeClock()

    def prepare():
        raise _low_error(221)

    with pytest.raises(resources.RenderMemoryLowError):
        resources.wait_for_render_memory(
            prepare,
            wait_deadline=clock.now + 7,  # hard cap wins over extensions
            base_wait_seconds=120,
            poll_seconds=0.5,
            worker_extend_seconds=30,
            live_workers=lambda: [{"pid": 111, "name": "ffmpeg", "rss_mb": 64}],
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )
    assert 7 <= clock.now - 1000.0 <= 9


def test_wait_reclaims_own_garbage_when_a_worker_exits(monkeypatch):
    clock = FakeClock()
    collected = {"n": 0}
    monkeypatch.setattr(resources.gc, "collect", lambda *args: collected.__setitem__("n", collected["n"] + 1))
    workers = [[{"pid": 111, "name": "ffmpeg", "rss_mb": 64}], []]
    calls = {"n": 0}

    def prepare():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _low_error(221)
        return {"selected_workers": 1}

    resources.wait_for_render_memory(
        prepare,
        wait_deadline=clock.now + 900,
        base_wait_seconds=120,
        live_workers=lambda: workers.pop(0) if workers else [],
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert collected["n"] >= 1, "gc must run when the worker set shrinks"


def test_held_render_reports_the_freshest_available_memory(monkeypatch):
    # _memory_snapshot calls in prepare_render_resources order: before,
    # after_trim, after, after_recovery. The raised error used to carry
    # the stale `after` reading; it must carry after_recovery (221).
    readings = iter([
        {"total_mb": 8192, "available_mb": 200, "free_mb": 100, "process_rss_mb": 800},
        {"total_mb": 8192, "available_mb": 200, "free_mb": 100, "process_rss_mb": 800},
        {"total_mb": 8192, "available_mb": 200, "free_mb": 100, "process_rss_mb": 800},
        {"total_mb": 8192, "available_mb": 221, "free_mb": 120, "process_rss_mb": 790},
    ])
    monkeypatch.setattr(resources, "_memory_snapshot", lambda: next(readings))
    monkeypatch.setattr(resources, "_trim_current_process_working_set", lambda: False)
    with pytest.raises(resources.RenderMemoryLowError) as excinfo:
        resources.prepare_render_resources(release_idle=True, video_heavy=True)
    assert excinfo.value.memory_snapshot["available_mb"] == 221
    assert excinfo.value.memory_snapshot["available_at_raise_mb"] == 221


def test_live_media_workers_finds_a_real_ffmpeg_child_and_ignores_plain_children():
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg not installed")
    own_pid = resources.os.getpid()
    worker = subprocess.Popen(
        [ffmpeg, "-v", "error", "-f", "lavfi", "-i", "nullsrc", "-t", "60", "-f", "null", "-"]
    )
    plain = subprocess.Popen(["sleep", "60"])
    try:
        deadline = time.time() + 10
        found = []
        while time.time() < deadline:
            found = resources.live_media_workers()
            if any(entry["pid"] == worker.pid for entry in found):
                break
            time.sleep(0.2)
        pids = {entry["pid"] for entry in found}
        assert worker.pid in pids, f"ffmpeg child {worker.pid} not in {found}"
        assert plain.pid not in pids, "a plain sleep child must not count as a media worker"
        assert own_pid not in pids, "the server itself must never be listed"
        entry = next(item for item in found if item["pid"] == worker.pid)
        assert entry["name"].lower() == "ffmpeg"
        assert entry["rss_mb"] >= 0
    finally:
        worker.kill()
        plain.kill()
        worker.wait()
        plain.wait()
