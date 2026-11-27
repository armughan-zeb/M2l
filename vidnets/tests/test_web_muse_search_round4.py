"""Muse-search web-image wave, round 4 (test 31 fix).

Test 31 (Panama Canal, job ff4dcea6) returned 0 muse rows live:
Wikimedia's 5 s adapter timeout died ~1 s before the proxy's ~6 s
response; Openverse ran direct-first on a dead route and swallowed
its transport failure without record_failure(); the 20 s wave wall
abandoned slow providers while logging a fake instant 0-row result;
and per-scene queries never reached the wave at all once the shared
pool was warm.

Every test here must FAIL on clean HEAD 5bf3ce7 and PASS with the
fix (verify with git stash). Run with ``VIDZEO_RENDER_GATE=off``.
The live-gated proof test (test_web_muse_search_round4_proof_live)
is skipped unless ``VIDZEO_LIVE_WEB_TEST=1`` — run it: mocked
tests alone are what got us here.
"""
import os
import time
from types import SimpleNamespace

import pytest
import requests

from services.sources.base_source import ImageCandidate
from services.sources.openverse_source import OpenverseSource
from services.sources.wikimedia_source import (
    WikimediaSource,
    wikimedia_timeout_seconds,
)
from services.web_image import muse_search as muse_mod
from services.web_image import resolver


# ---------------------------------------------------------------------------
# Fakes (same candidate shapes as test_web_muse_search.py)
# ---------------------------------------------------------------------------

def _wikimedia_candidate():
    return ImageCandidate(
        id="wikimedia_1", source="wikimedia", title="Moon crater Tycho",
        description="",
        download_url="https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Tycho.jpg/1280px-Tycho.jpg",
        preview_url="https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Tycho.jpg/1280px-Tycho.jpg",
        width=1280, height=800, license="",
        metadata={
            "page_id": "1",
            "foreign_url": "https://commons.wikimedia.org/wiki/File:Tycho.jpg",
            "provider": "wikimedia",
            "original_url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Tycho.jpg",
        },
    )


def _openverse_candidate():
    return ImageCandidate(
        id="openverse_1", source="openverse", title="Moon over the desert",
        description="A full moon rising",
        download_url="https://live.staticflickr.com/1/moon.jpg",
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
        self._failure_count = 0
        self._circuit_open = False

    def search(self, query, limit=20, direct=False):
        self.calls.append((query, direct))
        if self._sleep:
            time.sleep(self._sleep)
        return list(self._rows[:limit])

    def is_available(self):
        return True


class _FailingThenDirectSource:
    """Proxy attempt fails (records it); direct attempt returns rows.

    Proves whether the manager's alternate-route retry fires at all.
    """

    def __init__(self, rows):
        self._rows = list(rows)
        self.calls = []
        self._failure_count = 0
        self._circuit_open = False

    def search(self, query, limit=20, direct=False):
        self.calls.append((query, direct))
        if direct:
            return list(self._rows[:limit])
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
# R1 — Wikimedia timeout survives a slow proxy response (fix #1)
# ---------------------------------------------------------------------------

def test_wikimedia_timeout_survives_slow_proxy_response(monkeypatch):
    """A 6 s+ TTFB proxy response lands instead of timing out at 5 s.

    Fails on HEAD: adapter timeout is 5.0 s, so the fake raises
    ReadTimeout exactly the way the live proxy did in test 31.
    """
    monkeypatch.delenv("VIDZEO_WIKIMEDIA_TIMEOUT_SECONDS", raising=False)
    # Round 5: default raised 30 -> 55 s (test 32 died at 30 s twice
    # while the proxy was degraded past 30 s TTFB); fits the 60 s wall.
    assert wikimedia_timeout_seconds() == 55.0
    assert WikimediaSource().timeout == 55.0
    monkeypatch.setenv("VIDZEO_WIKIMEDIA_TIMEOUT_SECONDS", "45")
    assert WikimediaSource().timeout == 45.0
    monkeypatch.delenv("VIDZEO_WIKIMEDIA_TIMEOUT_SECONDS", raising=False)

    class FakeResp:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"query": {"pages": [{
                "pageid": 7,
                "title": "File:Tycho.jpg",
                "imageinfo": [{
                    "url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Tycho.jpg",
                    "thumburl": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Tycho.jpg/1280px-Tycho.jpg",
                    "thumbwidth": 1280, "thumbheight": 800,
                    "mime": "image/jpeg",
                    "descriptionurl": "https://commons.wikimedia.org/wiki/File:Tycho.jpg",
                }],
            }]}}

    class SlowProxySession:
        """6.2 s TTFB; dies instantly if the caller only allows < 6 s."""

        def get(self, url, **kwargs):
            if float(kwargs.get("timeout") or 0) < 6.0:
                raise requests.exceptions.ReadTimeout("proxy TTFB 6.2s > timeout")
            time.sleep(6.2)
            return FakeResp()

    source = WikimediaSource()
    source.session = SlowProxySession()
    started = time.monotonic()
    rows = source.search("panama canal", limit=5)
    elapsed = time.monotonic() - started
    assert len(rows) == 1, "slow proxy response must land with timeout 30"
    assert elapsed >= 6.0, f"response arrived after {elapsed:.1f}s, expected 6s+ TTFB"


# ---------------------------------------------------------------------------
# R2 — Openverse transport failure is recorded (fix #3) + retry gate
# ---------------------------------------------------------------------------

def test_openverse_request_exception_records_failure():
    """A ConnectionError on both internal attempts records a failure.

    Fails on HEAD: _search_response swallows RequestException without
    record_failure(), so _failure_count stays 0 and the manager reads
    a dead transport as a genuine empty answer.
    """

    class DeadSession:
        headers = {}

        def get(self, *args, **kwargs):
            raise requests.exceptions.ConnectionError("direct route dead")

        def post(self, *args, **kwargs):
            raise AssertionError("no token fetch for anonymous Openverse")

    source = OpenverseSource(session=DeadSession())
    rows = source.search("panama canal", limit=5)
    assert rows == []
    assert source._failure_count >= 1, (
        f"_failure_count={source._failure_count}, transport failure unrecorded"
    )
    # One failure must not trip the circuit (needs 3).
    assert source.is_available() is True


def test_openverse_failure_retries_direct_when_enabled():
    """With direct retry enabled, a recorded failure retries once direct.

    Nov 25 direct-only: the retry re-attempts direct — there is no
    proxy leg to switch to. (Previously: first attempt direct, retry
    on proxy.) The manager still retries on a recorded failure
    (2 attempts, 4 calls).
    """

    class DeadSession:
        headers = {}
        calls = []

        def get(self, *args, **kwargs):
            DeadSession.calls.append(kwargs.get("proxies"))
            raise requests.exceptions.ConnectionError("transport dead")

        def post(self, *args, **kwargs):
            raise AssertionError("no token fetch for anonymous Openverse")

    DeadSession.calls = []
    source = OpenverseSource(session=DeadSession())
    # Round 5: the transport retry is disabled here to isolate the
    # direct-retry gate — with it on, the wave would retry the dead
    # transport once more under the extended wall (see round-5 tests).
    manager = muse_mod.MuseSearchManager(
        openverse_source=source,
        wikimedia_source=_CountingSource("wikimedia", []),
        openverse_direct_first=True,
        direct_retry=True,
        wave_seconds=10,
        retry_wall_seconds=0,
    )
    rows = manager.search("panama canal", q_idx=0)
    assert rows == []
    assert len(DeadSession.calls) == 4, (
        f"expected 2 manager attempts x 2 internal attempts = 4 calls, "
        f"got {len(DeadSession.calls)}"
    )
    assert source._failure_count == 2
    # Both attempts direct: no proxy leg exists any more.
    assert all(
        call == {"http": None, "https": None} for call in DeadSession.calls
    ), f"expected every call direct, got {DeadSession.calls}"


def test_wikimedia_first_call_is_direct_and_lands():
    """Nov 25 direct-only: the first call goes direct and lands.

    The fake fails on proxy and returns rows on direct; under
    direct-only the manager's first attempt is direct, so the row
    lands at once with no retry — the old proxy-first shape (empty
    rows, proxy calls only) no longer exists.
    """
    source = _FailingThenDirectSource([_wikimedia_candidate()])
    manager = muse_mod.MuseSearchManager(
        wikimedia_source=source,
        openverse_source=_CountingSource("openverse", []),
        wave_seconds=10,
    )
    rows = manager.search("panama canal", q_idx=0)
    assert len(rows) == 1, "direct first call must land the row"
    assert [direct for _q, direct in source.calls] == [True], (
        f"expected a single direct call, got {source.calls}"
    )


def test_direct_first_lands_without_retry():
    """Nov 25 direct-only: direct lands on the first attempt.

    The fake returns rows on direct; the manager's first call is
    direct, so the row lands at once and no retry fires — there is
    no alternate route left to rescue via.
    """
    source = _FailingThenDirectSource([_wikimedia_candidate()])
    manager = muse_mod.MuseSearchManager(
        wikimedia_source=source,
        openverse_source=_CountingSource("openverse", []),
        direct_retry=True,
        wave_seconds=10,
    )
    rows = manager.search("panama canal", q_idx=0)
    assert len(rows) == 1
    assert [direct for _q, direct in source.calls] == [True]


# ---------------------------------------------------------------------------
# R3 — Openverse is direct-only (Nov 25 direct-only order)
# ---------------------------------------------------------------------------

def test_direct_web_mode_respects_explicit_source_overrides(monkeypatch):
    monkeypatch.setenv("VIDZEO_WEB_DIRECT_FIRST", "1")
    monkeypatch.delenv("VIDZEO_MUSE_SEARCH_OPENVERSE_ROUTE", raising=False)
    monkeypatch.delenv("VIDZEO_MUSE_SEARCH_DIRECT_RETRY", raising=False)
    assert muse_mod.muse_search_openverse_direct_first()
    assert muse_mod.muse_search_direct_retry_enabled()
    monkeypatch.setenv("VIDZEO_MUSE_SEARCH_OPENVERSE_ROUTE", "proxy")
    monkeypatch.setenv("VIDZEO_MUSE_SEARCH_DIRECT_RETRY", "0")
    assert not muse_mod.muse_search_openverse_direct_first()
    assert not muse_mod.muse_search_direct_retry_enabled()


def test_openverse_direct_first_by_default(monkeypatch):
    """Nov 25 direct-only: the default route is direct.

    The env knob still overrides it: "direct" stays direct,
    "proxy" opts back into the proxy route.
    """
    monkeypatch.delenv("VIDZEO_MUSE_SEARCH_OPENVERSE_ROUTE", raising=False)
    assert muse_mod.muse_search_openverse_direct_first() is True
    monkeypatch.setenv("VIDZEO_MUSE_SEARCH_OPENVERSE_ROUTE", "direct")
    assert muse_mod.muse_search_openverse_direct_first() is True
    monkeypatch.setenv("VIDZEO_MUSE_SEARCH_OPENVERSE_ROUTE", "proxy")
    assert muse_mod.muse_search_openverse_direct_first() is False


def test_openverse_first_call_uses_direct_by_default(monkeypatch):
    """Nov 25 direct-only: the first live Openverse call goes direct.

    The first call carries the direct bypass proxies.
    """
    monkeypatch.delenv("VIDZEO_MUSE_SEARCH_OPENVERSE_ROUTE", raising=False)
    calls = []

    class FakeResp:
        status_code = 200
        headers = {}

        def json(self):
            return {"results": [{
                "id": "abc", "title": "Moon", "url": "https://live.staticflickr.com/1/moon.jpg",
                "thumbnail": "https://api.openverse.org/v1/images/abc/thumb/",
                "foreign_landing_url": "https://www.flickr.com/photos/x/1",
                "license": "by", "license_version": "2.0",
                "license_url": "https://creativecommons.org/licenses/by/2.0/",
                "creator": "Photographer", "provider": "flickr",
                "width": 1200, "height": 800, "filetype": "jpg",
                "filesize": 12345, "tags": [{"name": "moon"}],
            }]}

    class FakeSession:
        headers = {}

        def get(self, url, **kwargs):
            calls.append(kwargs.get("proxies"))
            return FakeResp()

        def post(self, *args, **kwargs):
            raise AssertionError("no token fetch for anonymous Openverse")

    source = OpenverseSource(session=FakeSession())
    manager = muse_mod.MuseSearchManager(
        openverse_source=source,
        wikimedia_source=_CountingSource("wikimedia", []),
        wave_seconds=10,
    )
    rows = manager.search("moon crater", q_idx=0)
    assert len(rows) == 1
    assert calls and calls[0] == {"http": None, "https": None}, (
        f"first Openverse call must use direct (proxies={{'http': None, "
        f"'https': None}}), got {calls[0]!r}"
    )


def test_openverse_grace_bounds_slow_secondary(monkeypatch):
    """Once Wikimedia delivers, a hanging Openverse gets only the grace.

    Fails on HEAD: the wave waits for every lane up to the wall, so a
    3 s Openverse holds a wave whose primary answered instantly.
    """
    logs = []
    monkeypatch.setattr(muse_mod, "_web_log", logs.append)
    manager = muse_mod.MuseSearchManager(
        wikimedia_source=_CountingSource("wikimedia", [_wikimedia_candidate()]),
        openverse_source=_CountingSource(
            "openverse", [_openverse_candidate()], sleep_seconds=3.0,
        ),
        wave_seconds=10,
        openverse_grace_seconds=0.5,
    )
    started = time.monotonic()
    rows = manager.search("moon crater", q_idx=0)
    elapsed = time.monotonic() - started
    assert len(rows) == 1, "only the primary's row should land"
    assert rows[0]["provider"] == "wikimedia"
    assert elapsed < 2.0, f"wave took {elapsed:.1f}s, grace should cap it ~0.5s"
    assert any("abandoned after primary grace" in line for line in logs), logs


# ---------------------------------------------------------------------------
# R4 — wave wall 60 s + honest abandoned telemetry (fix #4)
# ---------------------------------------------------------------------------

def test_wave_wall_default_is_60_seconds(monkeypatch):
    """The fail-fast wall is 60 s now, not 20 s.

    Fails on HEAD: muse_search_wave_seconds() defaults to 20.0.
    """
    monkeypatch.delenv("VIDZEO_MUSE_SEARCH_WAVE_SECONDS", raising=False)
    assert muse_mod.muse_search_wave_seconds() == 60.0
    monkeypatch.setenv("VIDZEO_MUSE_SEARCH_WAVE_SECONDS", "25")
    assert muse_mod.muse_search_wave_seconds() == 25.0


def test_wave_wall_is_honoured_functionally():
    """A provider slower than the wall is abandoned; faster lands."""
    fast = _manager(
        wikimedia_rows=[_wikimedia_candidate()],
        openverse_rows=[],
        wave_seconds=2,
    )
    # _CountingSource with no sleep returns instantly; slow the wiki fake.
    fast._wikimedia_source._sleep = 1.0
    rows = fast.search("moon crater", q_idx=0)
    assert len(rows) == 1, "1.0s provider inside a 2s wall must land"

    slow = _manager(
        wikimedia_rows=[_wikimedia_candidate()],
        openverse_rows=[],
        wave_seconds=0.5,
        # Round 5: disable the transport retry to pin the bare wall —
        # with it on, the abandoned provider is retried under the
        # extended wall and lands (see round-5 tests).
        retry_wall_seconds=0,
    )
    slow._wikimedia_source._sleep = 1.0
    rows = slow.search("moon crater", q_idx=0)
    assert rows == [], "1.0s provider past a 0.5s wall must be abandoned"


def test_pending_wave_futures_logged_abandoned_not_fake_empty(monkeypatch):
    """Pending futures log 'abandoned at wall', not instant 0 rows.

    Fails on HEAD: the log line reads 'wikimedia=0 rows (0.0s,
    proxy)' for a provider that never answered — the fake telemetry
    that misled the test 31 forensics.
    """
    logs = []
    monkeypatch.setattr(muse_mod, "_web_log", logs.append)
    # Round 5: the transport retry is disabled here to pin the
    # abandonment telemetry in isolation — with it on, the slow
    # providers answer (empty) on the retry and the log honestly
    # reports their measured 0 rows instead (see round-5 tests).
    manager = muse_mod.MuseSearchManager(
        wikimedia_source=_CountingSource("wikimedia", [], sleep_seconds=3.0),
        openverse_source=_CountingSource("openverse", [], sleep_seconds=3.0),
        wave_seconds=1,
        retry_wall_seconds=0,
    )
    rows = manager.search("moon crater", q_idx=0)
    assert rows == []
    line = " ".join(logs)
    assert "abandoned at wall" in line, f"no abandonment logged: {logs}"
    assert "0 rows (0.0s, proxy)" not in line, f"fake instant result: {logs}"


# ---------------------------------------------------------------------------
# R5 — one wave per distinct query per job; resolver gate (fix #5)
# ---------------------------------------------------------------------------

def test_wave_caches_per_distinct_query():
    """A repeated query returns cached rows with no new provider call.

    Fails on HEAD: no per-query cache exists; every scene re-runs
    the wave for the same query.
    """
    manager = _manager(wave_seconds=10)
    first = manager.search("panama canal", q_idx=0)
    assert len(first) == 2
    second = manager.search("panama canal", q_idx=0)
    assert len(second) == 2
    assert len(manager.wikimedia_source.calls) == 1
    assert len(manager.openverse_source.calls) == 1
    # Callers cannot poison the cache by mutating returned rows.
    assert second[0] is not first[0]
    second[0]["title"] = "MUTATED"
    third = manager.search("panama canal", q_idx=0)
    assert third[0]["title"] == first[0]["title"]
    assert len(manager.wikimedia_source.calls) == 1
    # A different query is a new wave; an empty result is never cached.
    manager.search("suez canal", q_idx=0)
    assert len(manager.wikimedia_source.calls) == 2


def test_wave_does_not_cache_empty_results():
    """Empty results are re-run: a transient outage must not poison."""
    manager = _manager(
        wikimedia_rows=[], openverse_rows=[], wave_seconds=10,
    )
    assert manager.search("panama canal", q_idx=0) == []
    assert manager.search("panama canal", q_idx=0) == []
    assert len(manager.wikimedia_source.calls) == 2
    assert len(manager.openverse_source.calls) == 2


def test_job_budget_reset_clears_query_cache():
    """A new job must not inherit the previous job's cached rows."""
    manager = _manager(wave_seconds=10)
    assert len(manager.search("panama canal", q_idx=0)) == 2
    manager.reset_job_budget()
    assert len(manager.search("panama canal", q_idx=0)) == 2
    assert len(manager.wikimedia_source.calls) == 2


def test_resolver_warm_pool_keeps_muse_search_per_scene():
    """Per-scene queries may use muse_search once the pool is warm.

    Fails on HEAD: the gate's surviving set is only
    {universal_scraper, yandex, flickr_api}, so the wave never runs
    for a per-scene query (test 31 ran it twice per job: warm-up +
    pre-stock recovery only).
    """
    assert "muse_search" in resolver._WARM_POOL_PER_SCENE_SOURCES
    assert set(resolver._WARM_POOL_PER_SCENE_SOURCES) == {
        "muse_search", "universal_scraper", "yandex", "flickr_api",
    }


# ---------------------------------------------------------------------------
# R6 — circuit-breaker reset is usable immediately (fix #6)
# ---------------------------------------------------------------------------

def test_is_available_true_after_expired_circuit_reset():
    """A reset circuit is available on the very call that heals it.

    Fails on HEAD: is_available() resets the circuit and still
    returns False, wasting one call per reset.
    """
    source = WikimediaSource()
    source._circuit_open = True
    source._failure_count = 3
    source._last_failure_time = time.time() - 61.0
    assert source.is_available() is True
    assert source._circuit_open is False
    assert source._failure_count == 0

    # A circuit inside its 60 s window is still closed to callers.
    source._circuit_open = True
    source._failure_count = 3
    source._last_failure_time = time.time() - 10.0
    assert source.is_available() is False


# ---------------------------------------------------------------------------
# R7 — live-gated proof (test 31 acceptance, retrieval layer)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    os.environ.get("VIDZEO_LIVE_WEB_TEST") != "1",
    reason="live-gated: set VIDZEO_LIVE_WEB_TEST=1 to run against the real APIs",
)
def test_web_muse_search_round4_proof_live(monkeypatch):
    """Real APIs return muse-search rows for the test-31 query.

    Uses production defaults (60 s wall, Wikimedia 30 s proxy-first,
    Openverse 45 s proxy-first secondary) — no overrides. This is the
    retrieval-layer half of the test-31 proof: a re-run must show
    muse wave rows > 0 for a Panama query, web verifier calls N/30,
    and web > 0 in the final plan.
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
