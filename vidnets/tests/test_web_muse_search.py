"""Muse-search web-image replacement (test 30 fix, round 3).

Every test here must FAIL on clean HEAD 9fbaa4d and PASS with the fix.
Run with ``VIDZEO_RENDER_GATE=off``. The live-gated proof test
(test_web_muse_search_proof_live) is skipped unless
``VIDZEO_LIVE_WEB_TEST=1``.
"""
import asyncio
import hashlib
import json
import os
import threading
import time
from html import escape as html_escape
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.sources.base_source import ImageCandidate
from services.web_image import muse_search as muse_mod
from services.web_image import resolver
from services.web_image.hybrid_search import (
    DownloadLimiter,
    DownloadSlotTimeout,
    download_bulk,
)
from services.web_image.source_broker import (
    web_image_fallback_layers,
    web_image_source_catalog,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

def _wikimedia_candidate(**overrides):
    row = {
        "id": "wikimedia_1",
        "source": "wikimedia",
        "title": "Moon crater Tycho",
        "description": "",
        "download_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Tycho.jpg/1280px-Tycho.jpg",
        "preview_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Tycho.jpg/1280px-Tycho.jpg",
        "width": 1280,
        "height": 800,
        "license": "",
        "metadata": {
            "page_id": "1",
            "foreign_url": "https://commons.wikimedia.org/wiki/File:Tycho.jpg",
            "provider": "wikimedia",
            "original_url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Tycho.jpg",
        },
    }
    row.update(overrides)
    return ImageCandidate(
        id=row["id"], source=row["source"], title=row["title"],
        description=row["description"], download_url=row["download_url"],
        preview_url=row["preview_url"], width=row["width"],
        height=row["height"], license=row["license"], metadata=row["metadata"],
    )


def _openverse_candidate(**overrides):
    row = {
        "id": "openverse_1",
        "source": "openverse",
        "title": "Moon over the desert",
        "description": "A full moon rising",
        "download_url": "https://live.staticflickr.com/1/moon.jpg",
        "preview_url": "https://api.openverse.org/v1/images/1/thumb/",
        "width": 1200,
        "height": 800,
        "license": "by",
        "metadata": {
            "provider": "flickr",
            "foreign_url": "https://www.flickr.com/photos/x/1",
            "license_url": "https://creativecommons.org/licenses/by/2.0/",
            "attribution": "Photographer",
            "creator": "Photographer",
        },
    }
    row.update(overrides)
    return ImageCandidate(
        id=row["id"], source=row["source"], title=row["title"],
        description=row["description"], download_url=row["download_url"],
        preview_url=row["preview_url"], width=row["width"],
        height=row["height"], license=row["license"], metadata=row["metadata"],
    )


class _CountingSource:
    """Fake adapter returning canned rows and counting calls."""

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


def _manager(wikimedia_rows=None, openverse_rows=None, **kwargs):
    return muse_mod.MuseSearchManager(
        wikimedia_source=_CountingSource("wikimedia", wikimedia_rows or [_wikimedia_candidate()]),
        openverse_source=_CountingSource("openverse", openverse_rows or [_openverse_candidate()]),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# T1 — muse wave is first; standalone API lanes leave the default wave
# ---------------------------------------------------------------------------

def test_muse_search_wave_prefers_api_rows_over_universal(monkeypatch):
    """Tier-one starts with muse_search; the wave returns API rows.

    Fails on HEAD: tier-one starts with brave_images and there is no
    muse branch in _discover_source_query.
    """
    monkeypatch.delenv("VIDZEO_WEB_ONLY_PROVIDER", raising=False)
    assert resolver._TIER_ONE_SOURCES[0] == "muse_search"
    assert resolver._FAST_SOURCES[0] == ("muse_search", "muse")
    assert resolver._SOURCE_PRIORITY["muse_search"] == 0.45
    enabled = [name for name, _kind in resolver._enabled_fast_sources()]
    assert "muse_search" in enabled
    # Standalone lanes folded into the wave: no double-calling.
    assert "openverse" not in enabled
    assert "wikimedia" not in enabled
    # ... but still dispatchable for diagnostics via only-provider.
    monkeypatch.setenv("VIDZEO_WEB_ONLY_PROVIDER", "openverse")
    only = [name for name, _kind in resolver._enabled_fast_sources()]
    assert "openverse" in only
    assert "universal_scraper" in only

    rows = [{
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Tycho.jpg/1280px-Tycho.jpg",
        "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Tycho.jpg/1280px-Tycho.jpg",
        "title": "Moon crater Tycho", "source": "muse_search",
        "provider": "wikimedia", "query": "moon crater",
    }]
    monkeypatch.setattr(resolver, "search_muse_sources", lambda q, **kw: rows)
    got = resolver._discover_source_query("muse_search", "muse", "moon crater")
    assert got == rows
    assert got[0]["source"] == "muse_search"


def test_muse_search_in_source_catalog():
    """H9: the broker names muse_search as a first-class tier-0 lane."""
    catalog = web_image_source_catalog()
    assert catalog[0]["name"] == "muse_search"
    assert catalog[0]["tier"] == 0
    layers = web_image_fallback_layers()
    assert "muse_search" in layers[0]["providers"]


# ---------------------------------------------------------------------------
# T2 — wave fails fast on API timeout
# ---------------------------------------------------------------------------

def test_muse_search_wave_fails_fast_on_api_timeout():
    """Both API fakes sleep past the wave wall; the wave stays bounded.

    Fails on HEAD: no muse wave exists; the universal path waits minutes.

    Round 5: "fail fast" is now "fail bounded" — a transport failure
    earns ONE retry under the extended wall, so the wave takes the
    0.5 s wall plus the 3 s retry sleep (~3.5 s), still far under the
    60 s max wall and never the minutes the universal path burns.
    """
    manager = muse_mod.MuseSearchManager(
        wikimedia_source=_CountingSource("wikimedia", [], sleep_seconds=3.0),
        openverse_source=_CountingSource("openverse", [], sleep_seconds=3.0),
        wave_seconds=0.5,
    )
    started = time.monotonic()
    rows = manager.search("moon crater", q_idx=0)
    elapsed = time.monotonic() - started
    assert rows == []
    assert elapsed < 6.0, f"wave took {elapsed:.1f}s, expected one bounded retry"


# ---------------------------------------------------------------------------
# T3 — Openverse job budget caps calls
# ---------------------------------------------------------------------------

def test_muse_search_openverse_job_budget_caps_calls():
    """With budget 2, the 3rd scene's Openverse call is skipped.

    Fails on HEAD: no budget gate exists.
    Round 4 note: the three calls use DISTINCT queries on purpose —
    an identical query would return the manager's per-query cache
    (one wave per distinct query per job) instead of re-running the
    wave, which is the intended budget saving, not a budget bypass.
    """
    manager = _manager(openverse_budget=2, wave_seconds=10)
    for call_no in range(3):
        rows = manager.search(f"moon crater {call_no}", q_idx=0)
        # First two calls get both lanes; the third is Wikimedia-only.
        assert len(rows) == (2 if call_no < 2 else 1)
    assert len(manager.openverse_source.calls) == 2
    assert len(manager.wikimedia_source.calls) == 3
    assert manager.openverse_calls == 2


# ---------------------------------------------------------------------------
# T4 — thumbnail download URL preference (Wikimedia 1280px)
# ---------------------------------------------------------------------------

def test_muse_search_row_prefers_thumbnail_download_url():
    """Wikimedia row downloads the 1280px thumburl; full-res retained.

    Fails on HEAD: no muse module maps rows at all.
    """
    manager = _manager(wave_seconds=10)
    rows = manager.search("moon crater", q_idx=0)
    by_provider = {row["provider"]: row for row in rows}
    wiki = by_provider["wikimedia"]
    assert wiki["download_url"].endswith("/1280px-Tycho.jpg")
    assert wiki["url"] == wiki["download_url"]
    assert wiki["original_url"] == "https://upload.wikimedia.org/wikipedia/commons/a/ab/Tycho.jpg"
    assert wiki["source"] == "muse_search"
    # Openverse keeps the original URL: its thumbnail is ~600px and
    # would fail the resolver's 640px validation floor.
    ov = by_provider["openverse"]
    assert ov["download_url"] == "https://live.staticflickr.com/1/moon.jpg"
    assert ov["preview_url"] == "https://api.openverse.org/v1/images/1/thumb/"


def test_wikimedia_adapter_retains_original_url(monkeypatch):
    """The adapter itself must carry the full-res URL in metadata."""

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

    class FakeSession:
        def get(self, *args, **kwargs):
            return FakeResp()

    from services.sources.wikimedia_source import WikimediaSource
    source = WikimediaSource()
    source.session = FakeSession()
    rows = source.search("moon crater", limit=5)
    assert len(rows) == 1
    assert rows[0].download_url.endswith("/1280px-Tycho.jpg")
    assert rows[0].metadata["original_url"] == "https://upload.wikimedia.org/wikipedia/commons/a/ab/Tycho.jpg"


# ---------------------------------------------------------------------------
# T5 — Openverse direct-first route
# ---------------------------------------------------------------------------

def test_muse_search_openverse_direct_first_route():
    """Openverse is attempted direct before proxy.

    Fails on HEAD: no route-order concept for Openverse.
    """
    from services.sources import OpenverseSource

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

        def post(self, *args, **kwargs):  # pragma: no cover - anonymous path
            raise AssertionError("no token fetch for anonymous Openverse")

    source = OpenverseSource(session=FakeSession())
    manager = muse_mod.MuseSearchManager(
        openverse_source=source,
        wikimedia_source=_CountingSource("wikimedia", []),
        openverse_direct_first=True,
        wave_seconds=10,
    )
    rows = manager.search("moon crater", q_idx=0)
    assert len(rows) == 1
    assert rows[0]["provider"] == "openverse"
    assert calls, "Openverse session.get was never called"
    # Direct first: proxies={http: None, https: None} (env proxy bypass).
    assert calls[0] == {"http": None, "https": None}


# ---------------------------------------------------------------------------
# T6/T9 — download direct retry + per-URL timing stats
# ---------------------------------------------------------------------------

class _FakeContent:
    def __init__(self, data):
        self._data = data

    async def iter_chunked(self, _n):
        yield self._data


class _FakeResponse:
    def __init__(self, status=200, data=b""):
        self.status = status
        self.headers = {"Content-Type": "image/jpeg"}
        self.content = _FakeContent(data)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _TimeoutResponse:
    async def __aenter__(self):
        raise asyncio.TimeoutError()

    async def __aexit__(self, *args):
        return False


class _FakeConnector:
    def __init__(self, *args, **kwargs):
        pass


class _FakeTimeout:
    def __init__(self, total=None, connect=None):
        self.total = total
        self.connect = connect


class _FakeAiohttpSession:
    """Proxy session times out; direct session lands (trust_env=False)."""

    def __init__(self, **kwargs):
        self.trust_env = kwargs.get("trust_env")
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def get(self, url, **kwargs):
        self.calls.append(url)
        if self.trust_env:
            return _TimeoutResponse()
        return _FakeResponse(status=200, data=b"x" * 20000)


def _install_fake_aiohttp(monkeypatch):
    import aiohttp
    from services.web_image import hybrid_search
    monkeypatch.setattr(hybrid_search, "aiohttp", SimpleNamespace(
        ClientSession=_FakeAiohttpSession,
        TCPConnector=_FakeConnector,
        ClientTimeout=_FakeTimeout,
        ClientConnectionError=aiohttp.ClientConnectionError,
    ))


def test_download_bulk_direct_retry_on_timeout(monkeypatch, tmp_path):
    """Proxy attempt times out, direct attempt lands the URL.

    Fails on HEAD: download_bulk has no direct retry at all.

    Round 6: the direct retry is now opt-in (default retries via the
    proxy — direct returns 429/black-holes for the image hosts), so this
    positive control passes direct_retry=True explicitly.
    """
    _install_fake_aiohttp(monkeypatch)
    results = download_bulk(
        ["https://images.example/moon.jpg"], tmp_path,
        max_concurrent=1, timeout_seconds=30, direct_retry=True,
    )
    assert len(results) == 1
    url, path, error = results[0]
    assert error == "", f"download failed: {error}"
    assert path is not None and Path(path).is_file()
    assert Path(path).stat().st_size == 20000


def test_download_logs_slot_vs_transfer_time(monkeypatch, tmp_path):
    """Per-URL records carry slot_wait_ms / transfer_ms / route.

    Fails on HEAD: no such fields exist anywhere in the download path.

    Round 6: the fake proxy always times out, so this test opts into
    the direct retry (direct_retry=True) to keep its route=="direct"
    assertion; the default now retries via the proxy instead.
    """
    _install_fake_aiohttp(monkeypatch)
    stats = []
    results = download_bulk(
        ["https://images.example/moon.jpg"], tmp_path,
        max_concurrent=1, timeout_seconds=30, stats=stats, direct_retry=True,
    )
    assert results[0][1] is not None
    assert len(stats) == 1
    record = stats[0]
    assert record["url"] == "https://images.example/moon.jpg"
    assert record["route"] == "direct"
    assert "slot_wait_ms" in record and record["slot_wait_ms"] >= 0
    assert "transfer_ms" in record and record["transfer_ms"] >= 0
    assert record["bytes"] == 20000
    assert record["landed"] is True


# ---------------------------------------------------------------------------
# T7 — chunked download batches bound the shared wall
# ---------------------------------------------------------------------------

def test_download_batch_chunking_bounds_wall(monkeypatch, tmp_path):
    """12 URLs go out in sub-batches of <=6, each with its own wall.

    Fails on HEAD: one shared 90s wall covers all 12 URLs.
    """
    calls = []

    def fake_bulk(urls, output_dir, max_concurrent=50, timeout_seconds=15.0,
                  headers_by_url=None, stats=None, **kwargs):
        calls.append(list(urls))
        out = []
        for url in urls:
            name = "fake_" + hashlib.sha256(url.encode()).hexdigest()[:8] + ".jpg"
            dest = Path(output_dir) / name
            dest.write_bytes(b"x" * 100)
            out.append((url, dest, ""))
        return out

    monkeypatch.setattr(resolver, "download_bulk", fake_bulk)
    items = [
        {"url": f"https://images.example/{i}.jpg", "preview_url": "", "thumbnail_url": ""}
        for i in range(12)
    ]
    results = resolver._download_candidate_batch(
        items, tmp_path, max_concurrent=4, timeout_seconds=30,
        download_cache={}, download_cache_lock=threading.Lock(),
    )
    assert all(len(call) <= 6 for call in calls), f"chunk sizes: {[len(c) for c in calls]}"
    assert len(calls) >= 2, "expected at least 2 sub-batches for 12 URLs"
    assert sum(len(call) for call in calls) == 12
    assert len(results) == 12
    assert all(error == "" for _url, _path, error in results)


def test_download_candidate_batch_passes_stats_through(monkeypatch, tmp_path):
    """download_stats records survive the chunked batch call."""

    def fake_bulk(urls, output_dir, max_concurrent=50, timeout_seconds=15.0,
                  headers_by_url=None, stats=None, **kwargs):
        for url in urls:
            if stats is not None:
                stats.append({
                    "url": url, "host": "images.example", "route": "proxy",
                    "slot_wait_ms": 100.0, "transfer_ms": 2000.0,
                    "bytes": 12345, "landed": True, "error": "",
                })
        out = []
        for url in urls:
            dest = Path(output_dir) / ("fake_" + hashlib.sha256(url.encode()).hexdigest()[:8] + ".jpg")
            dest.write_bytes(b"x" * 100)
            out.append((url, dest, ""))
        return out

    monkeypatch.setattr(resolver, "download_bulk", fake_bulk)
    stats = []
    items = [{"url": "https://images.example/1.jpg", "preview_url": "", "thumbnail_url": ""}]
    resolver._download_candidate_batch(
        items, tmp_path, max_concurrent=1, timeout_seconds=30,
        download_cache={}, download_cache_lock=threading.Lock(),
        download_stats=stats,
    )
    assert len(stats) == 1
    assert stats[0]["slot_wait_ms"] == 100.0
    assert stats[0]["transfer_ms"] == 2000.0


# ---------------------------------------------------------------------------
# T8 — FIFO slot queue + slot-wait cap
# ---------------------------------------------------------------------------

def test_download_slot_wait_cap_fails_fast(monkeypatch):
    """A waiter fails fast at the 15s cap instead of burning its budget.

    Fails on HEAD: polling loop, no cap — the waiter acquires at 5s.
    """
    monkeypatch.setenv("VIDZEO_WEB_DOWNLOAD_SLOT_WAIT_SECONDS", "1")
    limiter = DownloadLimiter(total=1, per_host=1)

    async def run():
        async def holder():
            async with limiter.slot("h.example", time.monotonic() + 30):
                await asyncio.sleep(5)

        async def waiter():
            started = time.monotonic()
            try:
                async with limiter.slot("h.example", time.monotonic() + 30):
                    return ("acquired", time.monotonic() - started)
            except DownloadSlotTimeout:
                return ("timeout", time.monotonic() - started)

        task = asyncio.create_task(holder())
        await asyncio.sleep(0.1)
        outcome = await waiter()
        await task
        return outcome

    status, elapsed = asyncio.run(run())
    assert status == "timeout", f"waiter should fail fast, got {status}"
    assert elapsed < 3.0, f"waiter waited {elapsed:.1f}s, expected ~1s cap"


def test_download_slot_fifo_serves_waiters_in_order(monkeypatch):
    """Waiters take the next freed slot in arrival order.

    Fails on HEAD (probabilistically): the polling loop grants the slot
    to whichever coroutine polls first, so order is a race.
    """
    monkeypatch.setenv("VIDZEO_WEB_DOWNLOAD_SLOT_WAIT_SECONDS", "15")
    limiter = DownloadLimiter(total=1, per_host=1)
    order = []

    async def run_round():
        async def holder():
            async with limiter.slot("h.example", time.monotonic() + 30):
                await asyncio.sleep(0.3)

        async def waiter(name, delay):
            await asyncio.sleep(delay)
            async with limiter.slot("h.example", time.monotonic() + 30):
                order.append(name)
                await asyncio.sleep(0.05)

        task = asyncio.create_task(holder())
        first = asyncio.create_task(waiter("first", 0.05))
        second = asyncio.create_task(waiter("second", 0.15))
        await asyncio.gather(task, first, second)

    for _ in range(5):
        asyncio.run(run_round())
    assert order == ["first", "second"] * 5, f"acquisition order: {order}"


# ---------------------------------------------------------------------------
# T10 — universal fallback is a single bounded attempt after muse zero
# ---------------------------------------------------------------------------

def _install_fake_scraper(monkeypatch, search_fn, rescue_attempts="0"):
    monkeypatch.delenv("VIDZEO_UNIVERSAL_SCRAPER_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("VIDZEO_UNIVERSAL_LATE_WAIT_SECONDS", "0")
    monkeypatch.setenv("VIDZEO_WEB_UNIVERSAL_RETRY_BACKOFF_SECONDS", "0")
    monkeypatch.setenv("VIDZEO_WEB_UNIVERSAL_RESCUE_ATTEMPTS", rescue_attempts)
    monkeypatch.setenv("VIDZEO_WEB_UNIVERSAL_RESCUE_BACKOFF_SECONDS", "0")
    monkeypatch.setenv("VIDZEO_WEB_UNIVERSAL_RESCUE_DEADLINE_SECONDS", "60")

    def search_via_pool(query, **options):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as ex:
            return ex.submit(search_fn, query, **options).result()

    monkeypatch.setattr(
        resolver, "_UNIVERSAL_SCRAPER",
        SimpleNamespace(search=search_via_pool, collect_late_results=lambda _q, **_: []),
    )
    monkeypatch.setattr(
        resolver, "universal_result_to_candidate", lambda item, **_: {"url": item.image_url}
    )
    monkeypatch.setattr(resolver, "candidate_is_safe", lambda _candidate: (True, "fixture"))
    monkeypatch.setattr(resolver, "_report_provider_success", lambda _source: None)
    monkeypatch.setattr(resolver, "_report_provider_failure", lambda *_args: None)


def _failed_stat(engine, error="ReadTimeout"):
    return SimpleNamespace(
        engine=engine, count=0, duration_ms=15000, success=False, error=error
    )


def test_universal_fallback_single_attempt_after_muse_zero(monkeypatch):
    """Muse returns 0; universal is called exactly once, not 5x + rescue.

    Fails on HEAD: no bounded mode exists (2 attempts + rescue pass).
    """
    calls = []

    def search(query, **options):
        calls.append(options.get("direct"))
        stats = [_failed_stat("bing"), _failed_stat("yandex"), _failed_stat("flickr")]
        return resolver.UniversalSearchResponse(query, 0, [], stats)

    _install_fake_scraper(monkeypatch, search)
    rows = resolver._discover_universal_urls("Moon phases", bounded=True)
    assert rows == []
    assert len(calls) == 1, f"bounded fallback must call once, got {len(calls)}"
    calls.clear()
    rows = resolver._discover_source_query(
        "universal_scraper", "universal", "Moon phases", universal_bounded=True,
    )
    assert rows == []
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# T11 — Bing decoy rows discarded
# ---------------------------------------------------------------------------

def _decoy_bing_html():
    rows = [
        {"murl": "https://images.example/puppy1.jpg", "turl": "https://images.example/t1.jpg",
         "t": "Cute puppies playing", "desc": "puppies"},
        {"murl": "https://images.example/kitten.jpg", "turl": "https://images.example/t2.jpg",
         "t": "Kittens sleeping", "desc": "kittens"},
    ]
    parts = "".join(
        f'<div class="iusc" m="{html_escape(json.dumps(row))}"></div>'
        for row in rows
    )
    return f"<html><body>{parts}</body></html>"


def test_muse_search_bing_decoy_rows_discarded(monkeypatch):
    """Bing rows with no query token are a decoy: lane fails, not banks.

    Fails on HEAD: no decoy check on the hybrid lane.
    """
    from services.web_image import hybrid_search

    class FakeBingSession:
        def get(self, url, **kwargs):
            return SimpleNamespace(status_code=200, text=_decoy_bing_html())

    monkeypatch.setenv("VIDZEO_MUSE_SEARCH_BING_GAP_SECONDS", "0")
    monkeypatch.setattr(
        hybrid_search, "_get_bing_session", lambda: FakeBingSession()
    )
    with pytest.raises(RuntimeError, match="decoy"):
        hybrid_search.search_bing("moon landing", limit=10)

    # Lane level: decoy rows returned by the (faked) search fn are
    # discarded and recorded as a provider failure.
    decoy_rows = [
        {"image_url": "https://images.example/puppy1.jpg", "title": "Cute puppies playing",
         "source_url": "https://images.example/p1", "page_url": "https://images.example/p1",
         "preview_url": "", "thumbnail_url": "", "domain": "images.example",
         "tags": "", "description": "puppies"},
    ]
    failures = []
    monkeypatch.setattr(resolver, "search_bing", lambda *args, **kwargs: list(decoy_rows))
    monkeypatch.setattr(
        resolver, "_report_provider_failure",
        lambda source, exc: failures.append((source, str(exc))),
    )
    got = resolver._discover_hybrid_urls("bing_hybrid", "moon landing")
    assert got == []
    assert failures and "decoy" in failures[0][1]


# ---------------------------------------------------------------------------
# T12 — live-gated proof (test 31 acceptance, retrieval layer)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    os.environ.get("VIDZEO_LIVE_WEB_TEST") != "1",
    reason="live-gated: set VIDZEO_LIVE_WEB_TEST=1 to run against the real APIs",
)
def test_web_muse_search_proof_live():
    """Real APIs return muse-search rows for a documentary query.

    This is the retrieval-layer half of the test-31 proof run: the
    full acceptance (web > 0 in a final plan, §4.2 log lines) needs a
    real job run, not a unit test.
    """
    manager = muse_mod.MuseSearchManager(wave_seconds=20)
    rows = manager.search("RMS Titanic ocean liner", q_idx=0)
    assert len(rows) >= 1, "muse-search wave returned 0 rows live"
    assert all(row.get("source") == "muse_search" for row in rows)
    assert all(str(row.get("url") or "").startswith("http") for row in rows)


# ---------------------------------------------------------------------------
# Extras: slow-network cap default + catalog (design §2.4, H9)
# ---------------------------------------------------------------------------

def test_enrich_validated_rows_fills_wikimedia_rights(monkeypatch):
    """Deferred enrichment fills license/attribution for validated rows."""

    class FakeWikimedia:
        def __init__(self):
            self.calls = []

        def enrich_rights(self, candidates, limit=6, direct=False):
            self.calls.append([c.metadata.get("page_id") for c in candidates])
            for candidate in candidates:
                candidate.license = "CC BY-SA 4.0"
                candidate.metadata["attribution"] = "Test Artist"
                candidate.metadata["license_url"] = "https://creativecommons.org/licenses/by-sa/4.0/"
                candidate.metadata["creator"] = "Test Artist"
            return list(candidates)

    fake = FakeWikimedia()
    monkeypatch.setattr(muse_mod._DEFAULT_MANAGER, "_wikimedia_source", fake)
    rows = [{
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Tycho.jpg/1280px-Tycho.jpg",
        "provider": "wikimedia", "title": "Moon crater Tycho",
        "license": "", "page_id": "1",
    }]
    enriched = muse_mod.enrich_validated_rows(rows, limit=6)
    assert enriched[0]["license"] == "CC BY-SA 4.0"
    assert enriched[0]["attribution"] == "Test Artist"
    assert enriched[0]["license_url"] == "https://creativecommons.org/licenses/by-sa/4.0/"
    assert fake.calls == [["1"]]


def test_slow_network_max_factor_defaults_to_1_5x(monkeypatch):
    """Universal slow-network multiplier caps at 1.5x by default now."""
    from services.web_image import universal_scraper
    monkeypatch.delenv("VIDZEO_UNIVERSAL_SLOW_NETWORK_MAX_FACTOR", raising=False)
    assert universal_scraper._universal_slow_network_max_factor() == 1.5
    monkeypatch.setenv("VIDZEO_UNIVERSAL_SLOW_NETWORK_MAX_FACTOR", "2")
    assert universal_scraper._universal_slow_network_max_factor() == 2.0

# ---------------------------------------------------------------------------
# Tolerant wrappers: old test doubles predate the download_stats kwarg
# ---------------------------------------------------------------------------

def test_batch_tolerant_retries_without_download_stats(monkeypatch, tmp_path):
    """A _download_candidate_batch double without download_stats still works."""
    from services.web_image import resolver

    calls = {"n": 0}

    def fake_batch(items, output_dir, max_concurrent, timeout_seconds,
                   download_cache=None, download_cache_lock=None):
        calls["n"] += 1
        return [(item["url"], None, "") for item in items]

    monkeypatch.setattr(resolver, "_download_candidate_batch", fake_batch)
    stats: list = []
    results = resolver._download_candidate_batch_tolerant(
        [{"url": "https://example.com/a.jpg"}],
        tmp_path, max_concurrent=2, timeout_seconds=5,
        download_stats=stats,
    )
    assert calls["n"] == 1
    assert len(results) == 1


def test_chunked_tolerant_retries_without_stats_kwarg(monkeypatch, tmp_path):
    """A download_bulk double without stats still works via the chunked path."""
    from services.web_image import resolver

    def fake_bulk(urls, output_dir, max_concurrent=None, timeout_seconds=None,
                  headers_by_url=None, max_bytes=None):
        return [(u, None, "") for u in urls]

    monkeypatch.setattr(resolver, "download_bulk", fake_bulk)
    stats: list = []
    results = resolver._download_bulk_chunked_tolerant(
        ["https://example.com/a.jpg", "https://example.com/b.jpg"],
        tmp_path, max_concurrent=2, timeout_seconds=5,
        download_stats=stats,
    )
    assert len(results) == 2
