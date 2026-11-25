"""Round 10 (job e4d610b9 fix): the web lane must survive a transient
egress outage instead of burning its whole media budget on dead hosts.

Job e4d610b9 proved the failure mode, from its own audit
(web_image_audit_e4d610b9_*.json) and server log
(vidnets-server-restart-20261123-1456.log): during 15:01-15:21 PKT the
VM's egress was degraded and EVERY web provider failed on BOTH routes
at once (Yandex/Bing/Flickr ReadTimeouts at 30 s then 45 s, the muse
wave empty on Openverse/Wikimedia) from the very first wave. The repo
kept retrying those dead hosts for the whole 1103 s media budget and
resolved 0/13 scenes. The engines, the queries, and the route logic
were all healthy — the live round-10 probe (test_yandex_live_round10.py,
6 checks, all passing) gets 12 rows in ~4 s from the same code path.

The fix is a stage-scoped egress-outage circuit (WebOutageTracker in
resolver.py):
  - consecutive provider calls that end 0-row ON A TRANSPORT FAILURE
    (timeout / connection error, never a genuine empty answer) count
    toward an outage; at the threshold the stage declares an outage
    episode and every scene worker SLEEPS through a growing backoff
    (60 s, then 120 s) instead of hammering the dead egress;
  - backoff time does NOT count against the media deadline (the
    tracker grants a deadline extension equal to the backoff);
  - at most 3 episodes per stage (max 360 s extension); past the cap
    the stage degrades per-scene through its normal fallbacks.

Every mocked test below fails on clean b294205 and passes with the fix.
"""

import threading
import time
from types import SimpleNamespace

import pytest

from services.sources.base_source import ImageCandidate
from services.web_image import muse_search as muse_mod
from services.web_image.resolver import WebOutageTracker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeClock:
    """Controllable monotonic clock + sleep for the tracker tests.

    The tracker reads time.monotonic() and sleeps through its injected
    sleep callable; advancing the clock inside the fake sleep makes a
    60 s backoff elapse instantly without any real waiting.
    """

    def __init__(self):
        self.now = 1000.0
        self.slept = 0.0

    def monotonic(self):
        return float(self.now)

    def sleep(self, seconds):
        step = float(seconds)
        self.now += step
        self.slept += step


def _tracker(monkeypatch, clock, **kwargs):
    """Tracker wired to the fake clock; threshold 3 unless overridden."""
    monkeypatch.setattr(time, "monotonic", clock.monotonic)
    return WebOutageTracker(sleep=clock.sleep, **kwargs)


def _fail_streak(tracker, count=3):
    """Feed ``count`` consecutive 0-row transport failures; return the
    list of note_outcome() verdicts."""
    return [
        tracker.note_outcome(transport_failed=True, rows=0)
        for _ in range(count)
    ]


def _wikimedia_candidate():
    return ImageCandidate(
        id="wikimedia_1", source="wikimedia", title="Panama Canal locks",
        description="A photo of the Panama Canal",
        download_url="https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Locks.jpg/1280px-Locks.jpg",
        preview_url="https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Locks.jpg/1280px-Locks.jpg",
        width=1280, height=800, license="",
        metadata={
            "page_id": "1",
            "foreign_url": "https://commons.wikimedia.org/wiki/File:Locks.jpg",
            "provider": "wikimedia",
            "original_url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Locks.jpg",
        },
    )


def _openverse_candidate():
    return ImageCandidate(
        id="openverse_1", source="openverse", title="Panama Canal aerial",
        description="Aerial view of the Panama Canal",
        download_url="https://live.staticflickr.com/1/canal.jpg",
        preview_url="https://api.openverse.org/v1/images/1/thumb/",
        width=1200, height=800, license="by",
        metadata={
            "provider": "flickr",
            "foreign_url": "https://www.flickr.com/photos/x/1",
            "license_url": "https://creativecommons.org/licenses/by/2.0/",
            "attribution": "Photographer",
            "creator": "Photographer",
        },
    )


class _RaisingSource:
    """Every call raises a transport error (timeout / connection error)."""

    def __init__(self, exc=TimeoutError("read timed out")):
        self.exc = exc
        self.calls = []
        self._failure_count = 0
        self._circuit_open = False

    def search(self, query, limit=20, direct=False, timeout=None):
        self.calls.append((query, direct))
        raise self.exc

    def is_available(self):
        return True


class _EmptySource:
    """Answers every call with a genuine empty result (no failure)."""

    def __init__(self):
        self.calls = []
        self._failure_count = 0
        self._circuit_open = False

    def search(self, query, limit=20, direct=False, timeout=None):
        self.calls.append((query, direct))
        return []

    def is_available(self):
        return True


class _RowsSource:
    """Answers every call with rows."""

    def __init__(self, rows):
        self._rows = list(rows)
        self.calls = []
        self._failure_count = 0
        self._circuit_open = False

    def search(self, query, limit=20, direct=False, timeout=None):
        self.calls.append((query, direct))
        return list(self._rows[:limit])

    def is_available(self):
        return True


class _BlockingSource:
    """Blocks past the wave wall so the wave abandons the provider."""

    def __init__(self, seconds=30.0):
        self.seconds = seconds
        self.calls = []
        self._failure_count = 0
        self._circuit_open = False

    def search(self, query, limit=20, direct=False, timeout=None):
        self.calls.append((query, direct))
        time.sleep(self.seconds)
        return []

    def is_available(self):
        return True


def _muse_manager(wikimedia, openverse, **kwargs):
    kwargs.setdefault("retry_wall_seconds", 0.0)
    return muse_mod.MuseSearchManager(
        wikimedia_source=wikimedia,
        openverse_source=openverse,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Tracker: no outage on partial success or genuine empty
# ---------------------------------------------------------------------------

def test_no_outage_on_partial_success(monkeypatch):
    """Rows prove the egress is alive: the streak resets, no episode,
    no deadline extension.

    Fails on b294205: no tracker exists at all.
    """
    clock = _FakeClock()
    tracker = _tracker(monkeypatch, clock)
    assert _fail_streak(tracker, 2) == [False, False]
    assert tracker.note_outcome(transport_failed=False, rows=4) is False
    assert _fail_streak(tracker, 2) == [False, False]
    assert tracker.episodes_used == 0
    assert tracker.extension_seconds() == 0.0
    assert tracker.snapshot()["episodes_declared"] == 0


def test_no_outage_on_genuine_empty(monkeypatch):
    """A 0-row answer with NO transport failure is a bad/empty query,
    not a dead network: it resets the streak and never declares.

    Fails on b294205: no tracker exists at all.
    """
    clock = _FakeClock()
    tracker = _tracker(monkeypatch, clock)
    assert _fail_streak(tracker, 2) == [False, False]
    assert tracker.note_outcome(transport_failed=False, rows=0) is False
    assert _fail_streak(tracker, 2) == [False, False]
    assert tracker.episodes_used == 0
    assert tracker.extension_seconds() == 0.0


def test_rows_reset_streak_after_threshold_near_miss(monkeypatch):
    """Two failures, then rows, then two failures: the streak never
    reaches 3, so no outage is declared even though 4 failures
    happened in total."""
    clock = _FakeClock()
    tracker = _tracker(monkeypatch, clock)
    assert _fail_streak(tracker, 2) == [False, False]
    tracker.note_outcome(transport_failed=False, rows=1)
    assert _fail_streak(tracker, 2) == [False, False]
    assert tracker.episodes_used == 0


# ---------------------------------------------------------------------------
# Tracker: declaration, growing backoff, deadline extension
# ---------------------------------------------------------------------------

def test_outage_declared_at_threshold_with_backoff_and_extension(monkeypatch):
    """3 consecutive transport failures declare episode 1: a 60 s
    backoff starts and the stage deadline grows by exactly 60 s.

    Fails on b294205: no tracker exists at all.
    """
    clock = _FakeClock()
    tracker = _tracker(monkeypatch, clock)
    verdicts = _fail_streak(tracker, 3)
    assert verdicts == [False, False, True]
    assert tracker.episodes_used == 1
    assert tracker.declared_total == 1
    assert tracker.extension_seconds() == 60.0
    assert tracker.backoff_remaining() == pytest.approx(60.0)
    snap = tracker.snapshot()
    assert snap["episodes_used"] == 1
    assert snap["extension_seconds"] == 60.0
    assert snap["backoff_remaining_seconds"] == pytest.approx(60.0)


def test_backoff_grows_60_then_120_then_120(monkeypatch):
    """Backoff doubles per episode but is capped at the 120 s max:
    60 s, 120 s, 120 s. The deadline extension is the sum (300 s).

    Fails on b294205: no tracker exists at all.
    """
    clock = _FakeClock()
    tracker = _tracker(monkeypatch, clock)
    assert _fail_streak(tracker, 3) == [False, False, True]
    assert tracker.extension_seconds() == 60.0
    clock.now += 61.0  # first backoff elapses
    assert _fail_streak(tracker, 3) == [False, False, True]
    assert tracker.extension_seconds() == 180.0
    assert tracker.backoff_remaining() == pytest.approx(120.0)
    clock.now += 121.0
    assert _fail_streak(tracker, 3) == [False, False, True]
    assert tracker.extension_seconds() == 300.0
    assert tracker.backoff_remaining() == pytest.approx(120.0)


def test_episode_cap_stops_declaring(monkeypatch):
    """After 3 episodes the circuit stops declaring: further failures
    neither back off nor extend the deadline, so the stage degrades
    per-scene through its normal fallbacks instead of looping forever.

    Fails on b294205: no tracker exists at all.
    """
    clock = _FakeClock()
    tracker = _tracker(monkeypatch, clock)
    for _ in range(3):
        assert _fail_streak(tracker, 3) == [False, False, True]
        clock.now += 130.0
    assert tracker.episodes_used == 3
    assert tracker.extension_seconds() == 300.0
    assert _fail_streak(tracker, 3) == [False, False, False]
    assert tracker.episodes_used == 3
    assert tracker.extension_seconds() == 300.0
    assert tracker.declared_total == 3


def test_extension_cap_clamps_last_backoff(monkeypatch):
    """The deadline extension is capped: with a 100 s cap the first
    episode grants 60 s and the second only the remaining 40 s.

    Fails on b294205: no tracker exists at all.
    """
    clock = _FakeClock()
    tracker = _tracker(
        monkeypatch, clock, max_extension_seconds=100.0
    )
    assert _fail_streak(tracker, 3) == [False, False, True]
    assert tracker.extension_seconds() == 60.0
    clock.now += 61.0
    assert _fail_streak(tracker, 3) == [False, False, True]
    assert tracker.extension_seconds() == 100.0
    assert tracker.backoff_remaining() == pytest.approx(40.0)
    clock.now += 41.0
    assert _fail_streak(tracker, 3) == [False, False, False]
    assert tracker.extension_seconds() == 100.0


def test_concurrent_notes_declare_once(monkeypatch):
    """Eight scene workers failing at once declare exactly ONE episode
    for the same outage — a straggler's failure must not stack a
    second backoff on top of the one already running.

    Fails on b294205: no tracker exists at all.
    """
    tracker = WebOutageTracker()
    barrier = threading.Barrier(8)
    verdicts = []

    def _note():
        barrier.wait(timeout=10.0)
        verdicts.append(
            tracker.note_outcome(transport_failed=True, rows=0)
        )

    threads = [threading.Thread(target=_note) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10.0)
    assert sum(1 for verdict in verdicts if verdict) == 1
    assert tracker.declared_total == 1
    assert tracker.episodes_used == 1
    assert tracker.extension_seconds() == 60.0


# ---------------------------------------------------------------------------
# Tracker: wait_if_outage behaviour
# ---------------------------------------------------------------------------

def test_wait_if_outage_sleeps_through_backoff(monkeypatch):
    """After a declaration, wait_if_outage sleeps the whole backoff
    (in small steps, advancing the fake clock) and reports True; once
    the window elapses it returns False at once.

    Fails on b294205: no tracker exists at all.
    """
    clock = _FakeClock()
    tracker = _tracker(monkeypatch, clock)
    assert tracker.wait_if_outage() is False
    assert clock.slept == 0.0
    _fail_streak(tracker, 3)
    assert tracker.wait_if_outage() is True
    assert clock.slept == pytest.approx(60.0)
    assert tracker.backoff_remaining() == 0.0
    assert tracker.wait_if_outage() is False


def test_wait_if_outage_respects_cancel(monkeypatch):
    """A cancelled job ends the backoff wait promptly instead of
    sleeping the whole window."""
    clock = _FakeClock()
    tracker = _tracker(monkeypatch, clock)
    _fail_streak(tracker, 3)
    cancel = threading.Event()
    cancel.set()
    assert tracker.wait_if_outage(cancel_event=cancel) is False
    assert clock.slept == 0.0
    assert tracker.backoff_remaining() == pytest.approx(60.0)


def test_disabled_tracker_is_noop(monkeypatch):
    """VIDZEO_WEB_OUTAGE_ENABLED=0 (or enabled=False) disables the
    circuit entirely: no declaration, no extension, no waiting."""
    clock = _FakeClock()
    tracker = _tracker(monkeypatch, clock, enabled=False)
    assert _fail_streak(tracker, 10) == [False] * 10
    assert tracker.episodes_used == 0
    assert tracker.extension_seconds() == 0.0
    assert tracker.wait_if_outage() is False
    assert clock.slept == 0.0


def test_env_knobs_override_defaults(monkeypatch):
    """The env knobs tune the circuit: a threshold of 2 declares on
    the second failure, and a 10 s backoff grants a 10 s extension."""
    monkeypatch.setenv("VIDZEO_WEB_OUTAGE_FAIL_THRESHOLD", "2")
    monkeypatch.setenv("VIDZEO_WEB_OUTAGE_BACKOFF_SECONDS", "10")
    monkeypatch.setenv("VIDZEO_WEB_OUTAGE_BACKOFF_MAX_SECONDS", "10")
    clock = _FakeClock()
    tracker = _tracker(monkeypatch, clock)
    assert _fail_streak(tracker, 2) == [False, True]
    assert tracker.extension_seconds() == 10.0
    assert tracker.backoff_remaining() == pytest.approx(10.0)


def test_disabled_by_env(monkeypatch):
    monkeypatch.setenv("VIDZEO_WEB_OUTAGE_ENABLED", "0")
    clock = _FakeClock()
    tracker = _tracker(monkeypatch, clock)
    assert tracker.enabled is False
    assert _fail_streak(tracker, 5) == [False] * 5
    assert tracker.extension_seconds() == 0.0


# ---------------------------------------------------------------------------
# Muse wave: the transport-failure flag
# ---------------------------------------------------------------------------

def test_muse_flag_set_when_both_providers_raise(monkeypatch):
    """Both adapters raising TimeoutError/ConnectionError ends the
    wave 0-row with the transport flag set — the stage must read this
    as an egress failure, not an empty query.

    Fails on b294205: the flag does not exist.
    """
    manager = _muse_manager(
        _RaisingSource(TimeoutError("read timed out")),
        _RaisingSource(ConnectionError("connection reset")),
    )
    rows = manager.search("Panama Canal locks aerial view")
    assert rows == []
    assert manager.last_wave_transport_failed is True
    assert muse_mod.muse_search_last_wave_transport_failed(
        manager=manager
    ) is True


def test_muse_flag_clear_on_genuine_empty(monkeypatch):
    """Both adapters answering [] with no failure leaves the flag
    clear — a genuine empty answer must never look like an outage."""
    manager = _muse_manager(_EmptySource(), _EmptySource())
    rows = manager.search("Panama Canal locks aerial view")
    assert rows == []
    assert manager.last_wave_transport_failed is False


def test_muse_flag_clear_when_rows_returned(monkeypatch):
    """A wave that returns rows leaves the flag clear, even when the
    other provider raised."""
    manager = _muse_manager(
        _RowsSource([_wikimedia_candidate()]),
        _RaisingSource(TimeoutError("read timed out")),
    )
    rows = manager.search("Panama Canal locks aerial view")
    assert len(rows) == 1
    assert manager.last_wave_transport_failed is False


def test_muse_flag_set_when_provider_abandoned_at_wall(monkeypatch):
    """A provider that never answers inside the wave wall is abandoned,
    and the 0-row wave counts as a transport failure — a hung egress
    is a failed egress, even without an exception.

    Fails on b294205: the flag does not exist.
    """
    manager = _muse_manager(
        _BlockingSource(seconds=5.0),
        _EmptySource(),
        wave_seconds=1.0,
    )
    rows = manager.search("Panama Canal locks aerial view")
    assert rows == []
    assert manager.last_wave_transport_failed is True


def test_muse_flag_cleared_by_next_wave(monkeypatch):
    """A failed wave followed by a successful one clears the flag, so
    a stale failure never condemns a later, healthy wave."""
    manager = _muse_manager(
        _RaisingSource(TimeoutError("read timed out")),
        _RaisingSource(ConnectionError("connection reset")),
    )
    assert manager.search("Panama Canal locks aerial view") == []
    assert manager.last_wave_transport_failed is True
    manager._wikimedia_source = _RowsSource([_wikimedia_candidate()])
    manager._openverse_source = _RowsSource([_openverse_candidate()])
    rows = manager.search("Panama Canal locks aerial view")
    assert len(rows) == 2
    assert manager.last_wave_transport_failed is False


def test_image_transfer_outage_retries_same_candidates_after_backoff(tmp_path, monkeypatch):
    from services.web_image import resolver

    clock = _FakeClock()
    tracker = _tracker(monkeypatch, clock, backoff_seconds=5)
    urls = ["https://images.example/a.jpg", "https://archive.example/b.jpg", "https://denied.example/c.jpg"]
    calls = []

    def download(items, output_dir, **kwargs):
        requested = [item["url"] for item in items]
        calls.append(requested)
        if len(calls) == 1:
            return [(urls[0], None, "timeout"), (urls[1], None, "no route to host"), (urls[2], None, "HTTP 403")]
        # Search can answer while the image CDN route is still recovering.
        tracker.note_outcome(transport_failed=False, rows=12)
        return [(url, tmp_path / f"{index}.jpg", "") for index, url in enumerate(requested)]

    monkeypatch.setattr(resolver, "_download_candidate_batch_tolerant", download)
    results = resolver._download_batch_with_outage_recovery(
        [{"url": url} for url in urls], tmp_path,
        outage_tracker=tracker, cancel_event=threading.Event(),
        deadline=lambda: 1002.0 + tracker.extension_seconds(), timeout_seconds=10,
    )

    assert calls == [urls, urls[:2]]
    assert clock.slept == 5.0
    assert [bool(path) for _, path, _ in results] == [True, True, False]
    assert results[2][2] == "HTTP 403"
