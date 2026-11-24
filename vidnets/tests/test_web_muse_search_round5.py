"""Round-5 muse-search tests (test 32 fix): the wave survives a degrading proxy.

Test 32 proved the round-4 wave still returns 0 rows live when the
egress proxy degrades past the Wikimedia adapter timeout: both waves
logged ``wikimedia=0 rows (30.0s, proxy) openverse=abandoned at wall
(60s)`` while the proxy was degraded for the whole ~17 min media
window (all engines 45 s timeouts on both routes; downloads 61 s
timeouts). An hour earlier the same query returned 40 rows in 7.5 s
— the code works when egress is healthy; the wave must also survive
a slow-but-alive proxy.

Fixes under test (see muse_search.py, wikimedia_source.py,
openverse_source.py, resolver.py):
  R1  Wikimedia first-attempt timeout 30 -> 55 s (fits the 60 s wall).
  R2  One transport retry per wave under an extended wall (default
      +60 s, total capped 180 s) with a longer adapter timeout —
      fires only on a recorded transport failure with 0 rows, never
      on a genuine empty answer, never when rows already landed.
  R3  The shared-batch warm-up floors its batch wall at the wave's
      max wall when the muse wave is in the batch — otherwise a
      slow-but-successful wave's rows are cancelled at the 45 s batch
      wall and orphaned.
  R4  Live proof: the real APIs return rows for the test-32 query.

Every test here fails on clean cf714f3 (stash-verified) and passes
with the fix, except the live-gated proof (skipped without
VIDZEO_LIVE_WEB_TEST=1).
"""

import os
import time

import pytest
import requests

from services.sources.base_source import ImageCandidate
from services.sources.wikimedia_source import (
    WikimediaSource,
    wikimedia_timeout_seconds,
)
from services.web_image import muse_search as muse_mod
from services.web_image import resolver


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

def _wikimedia_candidate():
    return ImageCandidate(
        id="wikimedia_1", source="wikimedia", title="Panama Canal locks",
        description="",
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
        description="Aerial view",
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


class _CountingSource:
    """Fake adapter returning canned rows, counting calls, optionally slow."""

    def __init__(self, name, rows, sleep_seconds=0.0):
        self.name = name
        self._rows = list(rows)
        self._sleep = sleep_seconds
        self.calls = []
        self.timeouts = []
        self._failure_count = 0
        self._circuit_open = False

    def search(self, query, limit=20, direct=False, timeout=None):
        self.calls.append((query, direct))
        self.timeouts.append(timeout)
        if self._sleep:
            time.sleep(self._sleep)
        return list(self._rows[:limit])

    def is_available(self):
        return True


class _FailOnceThenRowsSource:
    """First call records a transport failure and returns []; later calls return rows.

    Models the test-32 failure: the first attempt dies on a slow
    proxy (timeout recorded as a failure), the retry lands.
    """

    def __init__(self, rows):
        self._rows = list(rows)
        self.calls = []
        self.timeouts = []
        self._failure_count = 0
        self._circuit_open = False

    def search(self, query, limit=20, direct=False, timeout=None):
        self.calls.append((query, direct))
        self.timeouts.append(timeout)
        if len(self.calls) == 1:
            self._failure_count += 1
            return []
        return list(self._rows[:limit])

    def is_available(self):
        return True


class _FailForeverSource:
    """Every call records a transport failure and returns []."""

    def __init__(self):
        self.calls = []
        self._failure_count = 0
        self._circuit_open = False

    def search(self, query, limit=20, direct=False, timeout=None):
        self.calls.append((query, direct))
        self._failure_count += 1
        return []

    def is_available(self):
        return True


def _manager(wikimedia_rows=None, openverse_rows=None, **kwargs):
    return muse_mod.MuseSearchManager(
        wikimedia_source=_CountingSource(
            "wikimedia", wikimedia_rows if wikimedia_rows is not None else [_wikimedia_candidate()]
        ),
        openverse_source=_CountingSource(
            "openverse", openverse_rows if openverse_rows is not None else [_openverse_candidate()]
        ),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# R1 — Wikimedia first-attempt timeout 55 s
# ---------------------------------------------------------------------------

def test_wikimedia_default_timeout_is_55_seconds(monkeypatch):
    """Default adapter timeout is 55 s (was 30 s); env override still wins.

    Fails on HEAD: default is 30.0 — test 32's wave died at 30 s twice
    while the proxy was degraded past 30 s TTFB.
    """
    monkeypatch.delenv("VIDZEO_WIKIMEDIA_TIMEOUT_SECONDS", raising=False)
    assert wikimedia_timeout_seconds() == 55.0
    assert WikimediaSource().timeout == 55.0
    monkeypatch.setenv("VIDZEO_WIKIMEDIA_TIMEOUT_SECONDS", "30")
    assert wikimedia_timeout_seconds() == 30.0
    assert WikimediaSource().timeout == 30.0
    monkeypatch.setenv("VIDZEO_WIKIMEDIA_TIMEOUT_SECONDS", "90")
    assert wikimedia_timeout_seconds() == 90.0


def test_wikimedia_search_accepts_timeout_override(monkeypatch):
    """A longer per-call timeout lets a slow proxy response land.

    Fails on HEAD: search() has no timeout parameter — the wave's
    transport retry cannot ask for a longer call.
    """
    monkeypatch.delenv("VIDZEO_WIKIMEDIA_TIMEOUT_SECONDS", raising=False)

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"query": {"pages": [{
                "pageid": 7,
                "title": "File:Locks.jpg",
                "imageinfo": [{
                    "url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Locks.jpg",
                    "thumburl": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Locks.jpg/1280px-Locks.jpg",
                    "thumbwidth": 1280, "thumbheight": 800,
                    "mime": "image/jpeg",
                    "descriptionurl": "https://commons.wikimedia.org/wiki/File:Locks.jpg",
                }],
            }]}}

    class SlowSession:
        """40 s TTFB; dies if the caller only allows < 40 s."""

        def get(self, url, **kwargs):
            if float(kwargs.get("timeout") or 0) < 40.0:
                raise requests.exceptions.ReadTimeout("proxy TTFB 40s > timeout")
            return FakeResp()

    source = WikimediaSource()
    source.session = SlowSession()
    # Default 55 s survives a 40 s TTFB.
    assert len(source.search("panama canal", limit=5)) == 1
    # An explicit short timeout still dies (override is honoured).
    assert source.search("panama canal", limit=5, timeout=10) == []


# ---------------------------------------------------------------------------
# R2 — one transport retry under an extended wall
# ---------------------------------------------------------------------------

def test_transport_retry_rescues_rows_after_first_attempt_failure(monkeypatch):
    """A first-attempt transport failure retries once and lands rows.

    Fails on HEAD: the wave returns [] after the first failure — the
    test-32 failure mode (wikimedia=0 rows at the adapter timeout).
    """
    logs = []
    monkeypatch.setattr(muse_mod, "_web_log", logs.append)
    wiki = _FailOnceThenRowsSource([_wikimedia_candidate()])
    manager = muse_mod.MuseSearchManager(
        wikimedia_source=wiki,
        openverse_source=_CountingSource("openverse", []),
        wave_seconds=10,
        retry_wall_seconds=10,
    )
    rows = manager.search("panama canal", q_idx=0)
    assert len(rows) == 1
    assert len(wiki.calls) == 2, f"expected first + one retry, got {wiki.calls}"
    assert "transport retry" in " ".join(logs)
    # The retry asked for a longer adapter timeout than the default call.
    assert wiki.timeouts[0] is None
    assert wiki.timeouts[1] is not None and wiki.timeouts[1] > 10


def test_transport_retry_does_not_fire_on_genuine_empty():
    """A genuine empty answer (no failure recorded) is never retried.

    Fails on HEAD only if a retry is added without the failure gate
    (regression guard for the round-4 rule).
    """
    wiki = _CountingSource("wikimedia", [])
    manager = muse_mod.MuseSearchManager(
        wikimedia_source=wiki,
        openverse_source=_CountingSource("openverse", []),
        wave_seconds=10,
        retry_wall_seconds=10,
    )
    assert manager.search("panama canal", q_idx=0) == []
    assert len(wiki.calls) == 1, f"genuine empty must not retry: {wiki.calls}"


def test_transport_retry_does_not_fire_when_rows_already_landed():
    """A wave that already holds rows never spends a retry."""
    wiki = _CountingSource("wikimedia", [_wikimedia_candidate()])
    manager = muse_mod.MuseSearchManager(
        wikimedia_source=wiki,
        openverse_source=_CountingSource("openverse", []),
        wave_seconds=10,
        retry_wall_seconds=10,
    )
    assert len(manager.search("panama canal", q_idx=0)) == 1
    assert len(wiki.calls) == 1


def test_transport_retry_is_bounded_to_one_attempt():
    """A permanently dead transport gets exactly one retry, then [].

    Fails on HEAD only if the retry loops (regression guard: the retry
    must not turn a 60 s wave into an unbounded one).
    """
    wiki = _FailForeverSource()
    manager = muse_mod.MuseSearchManager(
        wikimedia_source=wiki,
        openverse_source=_CountingSource("openverse", []),
        wave_seconds=2,
        retry_wall_seconds=2,
    )
    started = time.monotonic()
    assert manager.search("panama canal", q_idx=0) == []
    elapsed = time.monotonic() - started
    assert len(wiki.calls) == 2, f"expected first + exactly one retry, got {len(wiki.calls)}"
    assert elapsed < 6.0, f"bounded retry took {elapsed:.1f}s"


def test_transport_retry_extends_wall_for_slow_provider(monkeypatch):
    """A provider slower than the first wall lands on the retry.

    Models TTFB past the wave wall: the first phase abandons the
    provider at the wall, the retry phase (extended wall) lets it
    answer. Fails on HEAD: the wave returns [] at the first wall.
    """
    logs = []
    monkeypatch.setattr(muse_mod, "_web_log", logs.append)
    wiki = _CountingSource("wikimedia", [_wikimedia_candidate()], sleep_seconds=1.0)
    manager = muse_mod.MuseSearchManager(
        wikimedia_source=wiki,
        openverse_source=_CountingSource("openverse", []),
        wave_seconds=0.5,
        retry_wall_seconds=10,
    )
    rows = manager.search("panama canal", q_idx=0)
    assert len(rows) == 1, "slow provider must land on the retry"
    assert len(wiki.calls) == 2
    assert "transport retry" in " ".join(logs)


def test_transport_retry_disabled_by_zero_retry_wall():
    """VIDZEO_MUSE_SEARCH_RETRY_WALL_SECONDS=0 restores round-4 behaviour."""
    wiki = _FailOnceThenRowsSource([_wikimedia_candidate()])
    manager = muse_mod.MuseSearchManager(
        wikimedia_source=wiki,
        openverse_source=_CountingSource("openverse", []),
        wave_seconds=10,
        retry_wall_seconds=0,
    )
    assert manager.search("panama canal", q_idx=0) == []
    assert len(wiki.calls) == 1


def test_retry_wall_env_defaults_and_caps(monkeypatch):
    """Retry wall defaults 60 s, max wall defaults 180 s (wave + retry)."""
    monkeypatch.delenv("VIDZEO_MUSE_SEARCH_RETRY_WALL_SECONDS", raising=False)
    monkeypatch.delenv("VIDZEO_MUSE_SEARCH_MAX_WALL_SECONDS", raising=False)
    monkeypatch.delenv("VIDZEO_MUSE_SEARCH_WAVE_SECONDS", raising=False)
    assert muse_mod.muse_search_retry_wall_seconds() == 60.0
    assert muse_mod.muse_search_max_wave_wall_seconds() == 180.0
    assert muse_mod.muse_search_max_wave_wall() == 120.0
    monkeypatch.setenv("VIDZEO_MUSE_SEARCH_RETRY_WALL_SECONDS", "0")
    assert muse_mod.muse_search_retry_wall_seconds() == 0.0
    assert muse_mod.muse_search_max_wave_wall() == 60.0
    monkeypatch.setenv("VIDZEO_MUSE_SEARCH_RETRY_WALL_SECONDS", "60")
    monkeypatch.setenv("VIDZEO_MUSE_SEARCH_MAX_WALL_SECONDS", "90")
    assert muse_mod.muse_search_max_wave_wall() == 90.0


# ---------------------------------------------------------------------------
# R3 — shared-batch warm-up floors its wall at the wave's max wall
# ---------------------------------------------------------------------------

def test_shared_batch_wall_covers_slow_wave(monkeypatch):
    """A wave slower than the 45 s batch wall still lands rows in the pool.

    Fails on HEAD: the warm-up pool cancels the wave's future at the
    batch wall (45 s default) and its rows are orphaned even though
    the wave itself completed — the slow-wave rows never reach the
    shared pool. Here the batch wall is forced to 1 s and the wave
    wall to 2 s, so only the round-5 floor lets the 1.5 s wave land.
    """
    monkeypatch.setenv("VIDZEO_WEB_BATCH_DISCOVERY_SECONDS", "1")
    monkeypatch.setenv("VIDZEO_MUSE_SEARCH_WAVE_SECONDS", "2")
    monkeypatch.setenv("VIDZEO_MUSE_SEARCH_RETRY_WALL_SECONDS", "0")
    monkeypatch.setenv("VIDZEO_MUSE_SEARCH_OPENVERSE_JOB_BUDGET", "150")
    default_manager = muse_mod._DEFAULT_MANAGER
    wiki = _CountingSource("wikimedia", [_wikimedia_candidate()], sleep_seconds=1.5)
    ov = _CountingSource("openverse", [], sleep_seconds=0.0)
    monkeypatch.setattr(default_manager, "_wikimedia_source", wiki)
    monkeypatch.setattr(default_manager, "_openverse_source", ov)
    monkeypatch.setattr(default_manager, "_openverse_calls", 0)
    monkeypatch.setattr(default_manager, "_query_cache", {})
    monkeypatch.setattr(
        resolver, "_enabled_fast_sources",
        lambda: [("muse_search", "muse")],
    )
    result = resolver._warm_shared_discovery_pool(
        query="Panama Canal",
        scene_count=13,
        gallery_cmd=None,
        discovery_cache=None,
        source_telemetry=None,
        stage_deadline_at=time.monotonic() + 30,
        source_health=None,
    )
    assert result["candidates"] >= 1, (
        f"slow wave rows orphaned by the batch wall: {result}"
    )


# ---------------------------------------------------------------------------
# R4 — live proof (test-32 acceptance, retrieval layer)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    os.environ.get("VIDZEO_LIVE_WEB_TEST") != "1",
    reason="live-gated: set VIDZEO_LIVE_WEB_TEST=1 to run against the real APIs",
)
def test_web_muse_search_round5_proof_live(monkeypatch):
    """Real APIs return muse-search rows for the test-32 query.

    Uses production defaults (60 s wall, Wikimedia 55 s proxy-first,
    one transport retry under +60 s). This is the retrieval-layer
    half of the test-32 proof: a re-run must show muse wave rows > 0
    for a Panama query, web verifier calls N/30, and web > 0 in the
    final plan.
    """
    logs = []
    monkeypatch.setattr(muse_mod, "_web_log", logs.append)
    manager = muse_mod.MuseSearchManager()
    rows = manager.search("Panama Canal", q_idx=0)
    assert len(rows) >= 1, (
        f"muse-search wave returned 0 rows live for 'Panama Canal': {logs}"
    )
    assert all(row.get("source") == "muse_search" for row in rows)
    assert all(str(row.get("url") or "").startswith("http") for row in rows)
    providers = {row.get("provider") for row in rows}
    assert "wikimedia" in providers, (
        f"no Wikimedia rows live (providers: {providers}): {logs}"
    )
    line = " ".join(logs)
    assert "wikimedia=abandoned" not in line, (
        f"primary Wikimedia was abandoned live: {logs}"
    )
