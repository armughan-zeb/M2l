"""Round 6 (test 33 fix): muse-row downloads survive a degraded proxy.

Test 33 proved round 5 fixed the SEARCH half — the wave's transport
retry returned 40 rows live — but the final plan was still 3 YouTube /
0 web / 10 graphics. The next wall was DOWNLOADS, measured live:

* the shared-pool prefetch used a 5 s per-image budget in fast mode
  while a healthy-hour image transfer via the proxy takes ~16-18 s, so
  all 15 prefetch rows died and 0/15 local assets warmed the cache;
* the one retry went to the direct route, which returns HTTP 429
  (shared egress IP rate-limited) or black-holes on IPv4 for the image
  hosts, while the proxy returns 200 — so the retry burned a full
  budget plus a limiter slot and landed nothing;
* with 16-18 s transfers holding the 3/host slots, URLs 4-6 on a host
  waited out the 15 s slot-wait cap and died as "capacity wait timed
  out" — even with a 30 s budget, because the slot failure was terminal
  for that URL in that batch;
* the prefetch's transport failures were written into the shared
  download cache as 30 s failure markers, so scene workers reusing
  those URLs got 'cached_download_failure' without ever attempting them.

Every test below fails on clean af6c29e and passes with the fix.
Live-gated tests need VIDZEO_LIVE_WEB_TEST=1.
"""

import asyncio
import hashlib
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.web_image import hybrid_search
from services.web_image.hybrid_search import (
    DownloadLimiter,
    DownloadSlotTimeout,
    download_bulk,
)
from services.web_image import resolver


# ---------------------------------------------------------------------------
# Fakes (aiohttp shape: proxy session vs direct session by trust_env)
# ---------------------------------------------------------------------------

class _FakeContent:
    def __init__(self, data=b""):
        self._data = data

    async def iter_chunked(self, _n):
        if self._data:
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


class _ProxyFailsOnceSession:
    """Proxy times out on the first call, then lands; direct lands.

    Models the degraded hour: the proxy is slow, not dead.
    """

    def __init__(self, **kwargs):
        self.trust_env = kwargs.get("trust_env")
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def get(self, url, **kwargs):
        self.calls += 1
        if self.trust_env and self.calls == 1:
            return _TimeoutResponse()
        return _FakeResponse(status=200, data=b"x" * 20000)


class _ProxyAlwaysLandsSession:
    """Both routes land immediately."""

    def __init__(self, **kwargs):
        self.trust_env = kwargs.get("trust_env")
        self.calls = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def get(self, url, **kwargs):
        self.calls += 1
        return _FakeResponse(status=200, data=b"x" * 20000)


def _install_fake_aiohttp(monkeypatch, session_cls):
    import aiohttp
    monkeypatch.setattr(hybrid_search, "aiohttp", SimpleNamespace(
        ClientSession=session_cls,
        TCPConnector=_FakeConnector,
        ClientTimeout=_FakeTimeout,
        ClientConnectionError=aiohttp.ClientConnectionError,
    ))


# ---------------------------------------------------------------------------
# R1 — transport retry goes via the proxy by default (not dead direct)
# ---------------------------------------------------------------------------

def test_download_retries_via_direct_for_unknown_host(monkeypatch, tmp_path):
    """An unknown host tries proxy first, then the direct retry lands.

    Round-6 correction: the interim proxy-retry default was built on
    "direct never lands" — the live route table proved that wrong, so
    unknown hosts keep proxy-first with a direct fallback. The
    proxy-retry behaviour now applies only when direct is disabled.
    """
    _install_fake_aiohttp(monkeypatch, _ProxyFailsOnceSession)
    stats = []
    results = download_bulk(
        ["https://images.example/moon.jpg"], tmp_path,
        max_concurrent=1, timeout_seconds=30, stats=stats,
    )
    assert len(results) == 1
    url, path, error = results[0]
    assert error == "", f"download failed: {error}"
    assert path is not None and Path(path).is_file()
    assert len(stats) == 1
    assert stats[0]["route"] == "direct", (
        f"retry should go direct for an unknown host, got route={stats[0]['route']}"
    )


def test_download_stays_direct_when_direct_disabled(monkeypatch, tmp_path):
    """Nov 25 direct-only: direct_retry=False no longer selects the proxy.

    The flag is ignored for routing now; the download goes direct and
    lands. The proxy session fails once in the fake, but it is never
    used, so the download lands on the first direct attempt.
    """
    _install_fake_aiohttp(monkeypatch, _ProxyFailsOnceSession)
    stats = []
    results = download_bulk(
        ["https://images.example/moon.jpg"], tmp_path,
        max_concurrent=1, timeout_seconds=30, stats=stats, direct_retry=False,
    )
    assert len(results) == 1
    url, path, error = results[0]
    assert error == "", f"download failed: {error}"
    assert path is not None and Path(path).is_file()
    assert len(stats) == 1
    assert stats[0]["route"] == "direct", (
        f"download must stay direct when direct is off, got route={stats[0]['route']}"
    )


def test_download_direct_retry_opt_in_uses_direct(monkeypatch, tmp_path):
    """direct_retry=True still routes the retry direct (positive control)."""
    _install_fake_aiohttp(monkeypatch, _ProxyFailsOnceSession)
    stats = []
    results = download_bulk(
        ["https://images.example/moon.jpg"], tmp_path,
        max_concurrent=1, timeout_seconds=30, stats=stats, direct_retry=True,
    )
    assert results[0][1] is not None and Path(results[0][1]).is_file()
    assert stats[0]["route"] == "direct"


class _OrderRecordingSession:
    """Records (route, url) per call in a shared list; lands on both."""

    calls = []

    def __init__(self, **kwargs):
        self.trust_env = kwargs.get("trust_env")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def get(self, url, **kwargs):
        route = "direct" if not self.trust_env else "proxy"
        _OrderRecordingSession.calls.append((route, url))
        return _FakeResponse(status=200, data=b"x" * 20000)


def test_download_direct_first_host_tries_direct_first(monkeypatch, tmp_path):
    """A thumb.wikimedia.org URL is attempted direct FIRST.

    Fails on HEAD: the proxy session gets the first call for every host.
    """
    _OrderRecordingSession.calls = []
    _install_fake_aiohttp(monkeypatch, _OrderRecordingSession)
    results = download_bulk(
        ["https://thumb.wikimedia.org/x/moon.jpg"], tmp_path,
        max_concurrent=1, timeout_seconds=30,
    )
    assert results[0][1] is not None and Path(results[0][1]).is_file()
    assert _OrderRecordingSession.calls, "no download attempt recorded"
    assert _OrderRecordingSession.calls[0][0] == "direct", (
        f"first attempt went via {_OrderRecordingSession.calls[0][0]}"
    )


class _Direct429Session:
    """Direct returns 429, proxy returns 200."""

    calls = []

    def __init__(self, **kwargs):
        self.trust_env = kwargs.get("trust_env")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def get(self, url, **kwargs):
        route = "direct" if not self.trust_env else "proxy"
        _Direct429Session.calls.append((route, url))
        if not self.trust_env:
            return _FakeResponse(status=429, data=b"")
        return _FakeResponse(status=200, data=b"x" * 20000)


def test_download_429_on_direct_has_no_proxy_fallback(monkeypatch, tmp_path):
    """Nov 25 direct-only: a 429 on direct retries direct; there is no
    proxy leg to fall through to.

    The fake 429s every direct call, so the download fails with a 429
    instead of landing via the proxy — and no proxy call is recorded.
    """
    _Direct429Session.calls = []
    _install_fake_aiohttp(monkeypatch, _Direct429Session)
    stats = []
    results = download_bulk(
        ["https://thumb.wikimedia.org/x/moon.jpg"], tmp_path,
        max_concurrent=1, timeout_seconds=30, stats=stats,
    )
    assert results[0][1] is None
    assert "429" in (results[0][2] or "")
    assert stats[0]["route"] == "direct"
    assert [route for route, _url in _Direct429Session.calls] == [
        "direct", "direct",
    ]


def test_download_direct_retry_default_is_on(monkeypatch):
    """Direct stays ENABLED by default (round-6 correction).

    The interim fix disabled direct globally on the premise that direct
    never lands images — the live route table proved that wrong
    (thumb.wikimedia.org direct 200 in 2.1 s, live.staticflickr.com
    direct 200 in 2.0 s). Direct is an independent route that can
    survive a proxy brownout; the per-host order decides first route.
    =0 forces proxy-only.
    """
    monkeypatch.delenv("VIDZEO_WEB_DOWNLOAD_DIRECT_RETRY", raising=False)
    assert hybrid_search._download_direct_retry_enabled() is True
    monkeypatch.setenv("VIDZEO_WEB_DOWNLOAD_DIRECT_RETRY", "0")
    assert hybrid_search._download_direct_retry_enabled() is False
    monkeypatch.setenv("VIDZEO_WEB_DOWNLOAD_DIRECT_RETRY", "1")
    assert hybrid_search._download_direct_retry_enabled() is True


def test_download_route_order_is_direct_only(monkeypatch):
    """Nov 25 direct-only: every host downloads direct; there is no
    proxy leg, per-host or otherwise.
    """
    monkeypatch.delenv("VIDZEO_WEB_DOWNLOAD_DIRECT_FIRST_HOSTS", raising=False)
    assert hybrid_search._download_route_order("thumb.wikimedia.org") == [
        "direct",
    ]
    assert hybrid_search._download_route_order("upload.wikimedia.org") == [
        "direct",
    ]
    assert hybrid_search._download_route_order("live.staticflickr.com") == [
        "direct",
    ]
    assert hybrid_search._download_route_order("images.example") == [
        "direct",
    ]
    assert hybrid_search._download_route_order("commons.wikimedia.org") == [
        "direct",
    ]
    # The direct flag and the env override no longer change the route.
    assert hybrid_search._download_route_order(
        "thumb.wikimedia.org", direct_allowed=False
    ) == ["direct"]
    # Env override replaces the set — still direct-only.
    monkeypatch.setenv(
        "VIDZEO_WEB_DOWNLOAD_DIRECT_FIRST_HOSTS", "images.example"
    )
    assert hybrid_search._download_route_order("images.example") == [
        "direct",
    ]
    assert hybrid_search._download_route_order("thumb.wikimedia.org") == [
        "direct",
    ]


# ---------------------------------------------------------------------------
# R2 — a slot-wait failure retries the acquisition (not terminal)
# ---------------------------------------------------------------------------

def test_slot_wait_timeout_retries_acquisition(monkeypatch, tmp_path):
    """A URL that loses the slot lottery on attempt 1 lands on attempt 2.

    Fails on HEAD: DownloadSlotTimeout returns the URL dead at once,
    even though the slot frees ~1 s later and budget remains.

    The holder keeps the only slot for 2 s; the wait cap is 1 s, so
    attempt 1 must fail on the wait and attempt 2 must acquire the
    freed slot and land.
    """
    _install_fake_aiohttp(monkeypatch, _ProxyAlwaysLandsSession)
    monkeypatch.setenv("VIDZEO_WEB_DOWNLOAD_SLOT_WAIT_SECONDS", "1")
    limiter = DownloadLimiter(total=1, per_host=1)
    monkeypatch.setattr(hybrid_search, "_DOWNLOAD_LIMITER", limiter)

    async def hold_slot():
        async with limiter.slot("images.example", time.monotonic() + 30):
            await asyncio.sleep(2)

    # Hold the slot in a background thread with its own loop so the
    # download call below contends for it.
    holder_done = threading.Event()

    def _holder_thread():
        asyncio.run(hold_slot())
        holder_done.set()

    thread = threading.Thread(target=_holder_thread, daemon=True)
    thread.start()
    # Wait until the holder actually owns the slot; otherwise the
    # download could take it first and the retry is never exercised.
    deadline = time.monotonic() + 5
    while limiter.active < 1 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert limiter.active == 1, "holder never acquired the slot"
    try:
        results = download_bulk(
            ["https://images.example/moon.jpg"], tmp_path,
            max_concurrent=1, timeout_seconds=30,
        )
    finally:
        holder_done.wait(timeout=5)
    assert len(results) == 1
    url, path, error = results[0]
    assert error == "", f"download failed after slot retry: {error}"
    assert path is not None and Path(path).is_file()


# ---------------------------------------------------------------------------
# R3 — prefetch uses the scene download budget (not a hardcoded 5 s)
# ---------------------------------------------------------------------------

def test_shared_pool_prefetch_timeout_uses_scene_budget(monkeypatch):
    """Prefetch budget is the scene budget: 60 s fast, 120 s slow
    (round 8 doubled the scene budget — was 30 s fast, 60 s slow).

    Fails on HEAD: no such helper exists (the prefetch hardcoded
    5 s fast / 8 s, below the ~16-18 s a healthy-hour transfer takes).
    """
    assert resolver._shared_pool_prefetch_timeout_seconds(True) == 60.0
    assert resolver._shared_pool_prefetch_timeout_seconds(False) == 120.0
    monkeypatch.setenv("VIDZEO_WEB_IMAGE_DOWNLOAD_TIMEOUT_SECONDS", "45")
    assert resolver._shared_pool_prefetch_timeout_seconds(True) == 45.0
    # A nearly-spent stage deadline still bounds the budget (min 3 s).
    deadline = time.monotonic() + 4.0
    assert resolver._shared_pool_prefetch_timeout_seconds(
        True, deadline
    ) == pytest.approx(4.0, abs=0.05)


# ---------------------------------------------------------------------------
# R4 — transport failures do not poison the shared download cache
# ---------------------------------------------------------------------------

def _fake_bulk_factory(outcomes):
    """download_bulk double: outcomes maps url -> (landed, error)."""
    calls = []

    def fake_bulk(urls, output_dir, max_concurrent=50, timeout_seconds=15.0,
                  headers_by_url=None, stats=None, **kwargs):
        calls.append(list(urls))
        out = []
        for url in urls:
            landed, error = outcomes.get(url, (False, "timeout"))
            if landed:
                dest = Path(output_dir) / (
                    "fake_" + hashlib.sha256(url.encode()).hexdigest()[:8] + ".jpg"
                )
                dest.write_bytes(b"x" * 100)
                out.append((url, dest, ""))
            else:
                out.append((url, None, error))
        return out

    fake_bulk.calls = calls
    return fake_bulk


def _batch_items(urls):
    return [
        {"url": url, "preview_url": "", "thumbnail_url": "", "original_url": ""}
        for url in urls
    ]


def test_transport_failure_does_not_poison_cache(monkeypatch, tmp_path):
    """A timeout in call 1 is re-attempted in call 2; a 403 stays cached.

    Fails on HEAD: both failures are written as 30 s cache markers and
    the read side short-circuits on any marker, so call 2 returns
    'cached_download_failure' for the timeout URL too. The fix records
    every failure with the real error but only a definitive failure
    cools the URL down — a transport-failure marker is retryable.
    """
    timeout_url = "https://images.example/slow.jpg"
    refused_url = "https://images.example/refused.jpg"
    cache = {}
    lock = threading.Lock()
    first = _fake_bulk_factory({
        timeout_url: (False, "timeout"),
        refused_url: (False, "HTTP 403"),
    })
    monkeypatch.setattr(resolver, "download_bulk", first)
    results1 = resolver._download_candidate_batch(
        _batch_items([timeout_url, refused_url]), tmp_path,
        max_concurrent=2, timeout_seconds=30,
        download_cache=cache, download_cache_lock=lock,
    )
    assert all(path is None for _u, path, _e in results1)

    second = _fake_bulk_factory({
        timeout_url: (True, ""),
        refused_url: (True, ""),
    })
    monkeypatch.setattr(resolver, "download_bulk", second)
    results2 = resolver._download_candidate_batch(
        _batch_items([timeout_url, refused_url]), tmp_path,
        max_concurrent=2, timeout_seconds=30,
        download_cache=cache, download_cache_lock=lock,
    )
    by_url = {url: (path, err) for url, path, err in results2}
    assert by_url[timeout_url][0] is not None, (
        "transport failure must be re-attempted, not served from cache"
    )
    assert by_url[refused_url][0] is None, (
        "a 403 must stay cached for the cooldown"
    )
    assert "cached_download_failure" in (by_url[refused_url][1] or ""), (
        f"expected cached_download_failure, got {by_url[refused_url][1]!r}"
    )
    assert timeout_url in [u for call in second.calls for u in call]
    assert refused_url not in [u for call in second.calls for u in call]


def test_is_definitive_download_error_classifies(monkeypatch):
    """Only refusals/rejections count as definitive; transport does not."""
    definitive = [
        "HTTP 403", "HTTP 404", "HTTP 410", "HTTP 429",
        "not image: text/html", "too small (12B)", "too large (1B > 2B)",
        "invalid URL",
    ]
    for err in definitive:
        assert resolver._is_definitive_download_error(err), err
    transient = [
        "timeout", "web download capacity wait timed out",
        "web download batch budget exhausted", "download failed",
        "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504",
        "Cannot connect to host", "",
    ]
    for err in transient:
        assert not resolver._is_definitive_download_error(err), err


# ---------------------------------------------------------------------------
# R5 — chunk log reports the error breakdown
# ---------------------------------------------------------------------------

def test_chunk_log_reports_error_breakdown(monkeypatch, tmp_path):
    """'download chunk' lines name the failure kinds, not just landed 0.

    Fails on HEAD: the line carries no error breakdown, so test 33's
    0/6 chunks needed a live probe to explain.
    """
    logged = []
    monkeypatch.setattr(resolver, "_web_log", logged.append)
    outcomes = _fake_bulk_factory({
        "https://images.example/a.jpg": (False, "timeout"),
        "https://images.example/b.jpg": (False, "timeout"),
        "https://images.example/c.jpg": (
            False, "web download capacity wait timed out",
        ),
    })
    monkeypatch.setattr(resolver, "download_bulk", outcomes)
    resolver._download_bulk_chunked(
        [
            "https://images.example/a.jpg",
            "https://images.example/b.jpg",
            "https://images.example/c.jpg",
        ],
        tmp_path, max_concurrent=3, timeout_seconds=30,
    )
    chunk_lines = [line for line in logged if "download chunk:" in line]
    assert len(chunk_lines) == 1, f"expected one chunk line, got {logged}"
    line = chunk_lines[0]
    assert "landed 0" in line
    assert "timeout" in line
    assert "capacity wait" in line


# ---------------------------------------------------------------------------
# Scraper (universal_scraper): queue budget vs socket timeout, route health
# ---------------------------------------------------------------------------

def test_throttled_get_socket_gets_full_timeout_after_long_queue(monkeypatch):
    """A call that queued 5 s still hands the socket the full 10 s.

    Fails on HEAD: the socket gets max(0.5, 10 - 5) = 5 s of crumbs —
    the test-33 failure shape ("ReadTimeout read timeout=0.707s"
    after a 44 s queue wait).
    """
    from services.web_image import universal_scraper as usc

    monkeypatch.setattr(usc, "_universal_per_host", lambda: 1)
    host = "queue-test.example"
    state = usc._universal_host_slot(host, "proxy")
    semaphore = state["semaphore"]
    acquired = threading.Event()

    def holder():
        semaphore.acquire()
        acquired.set()
        time.sleep(5)
        semaphore.release()

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    assert acquired.wait(timeout=5), "holder never acquired the slot"
    recorded = {}

    class FakeClient:
        def get(self, url, headers=None, timeout=None, **kwargs):
            recorded["timeout"] = timeout
            return SimpleNamespace(status_code=200, text="")

    try:
        response = usc._throttled_get(
            FakeClient(), f"https://{host}/x.jpg", headers={}, timeout=10,
        )
    finally:
        thread.join(timeout=10)
    assert response.status_code == 200
    assert recorded.get("timeout", 0) >= 9.5, (
        f"socket got {recorded.get('timeout')}s, expected the full 10s"
    )


def test_queue_timeout_does_not_strike_route(monkeypatch):
    """A queue-timeout must not increment route failures (round 6).

    Fails on HEAD: _fetch strikes the route for ANY transport error, so
    3 queued calls skip both routes for 120 s — test 33's
    routes_tried=proxy only.
    """
    from services.web_image import universal_scraper as usc

    for route in ("proxy", "direct"):
        usc._route_state.pop(("yandex", route), None)

    def fake_throttled_get(client, url, *, headers, timeout, direct=False):
        raise TimeoutError(
            "throttled request to yandex.com exceeded its 30s budget "
            "waiting for a host slot"
        )

    monkeypatch.setattr(usc, "_throttled_get", fake_throttled_get)
    engine = usc.YandexEngine()
    with pytest.raises(RuntimeError, match="all routes failed"):
        engine._fetch(
            "https://yandex.com/images/search?text=x", headers={}, timeout=30,
        )
    for route in ("proxy", "direct"):
        state = usc._route_state.get(("yandex", route)) or {}
        assert float(state.get("failures", 0) or 0) == 0, (
            f"route {route} was struck by a queue-timeout"
        )


def test_engine_prefers_direct_for_measured_hosts():
    """Per-engine direct-first preference follows the live route table.

    Fails on HEAD: no such helper exists (proxy-first for every engine).
    """
    from services.web_image import universal_scraper as usc

    for name in ("yandex", "bing", "flickr", "openverse"):
        assert usc._engine_prefers_direct(name), name
    for name in ("baidu", "duckduckgo", "unknown", ""):
        assert not usc._engine_prefers_direct(name), name


def test_yandex_fetch_tries_direct_first_by_default(monkeypatch):
    """YandexEngine._fetch tries direct first with no explicit direct=.

    Fails on HEAD: proxy-first by default.
    """
    from services.web_image import universal_scraper as usc

    tried = []

    def fake_throttled_get(client, url, *, headers, timeout, direct=False):
        tried.append("direct" if direct else "proxy")
        return SimpleNamespace(status_code=200, text="")

    monkeypatch.setattr(usc, "_throttled_get", fake_throttled_get)
    for route in ("proxy", "direct"):
        usc._route_state.pop(("yandex", route), None)
    engine = usc.YandexEngine()
    response, route = engine._fetch(
        "https://yandex.com/images/search?text=x", headers={}, timeout=30,
    )
    assert tried == ["direct"], f"first route tried: {tried}"
    assert route == "direct"


def test_universal_per_host_default_is_four(monkeypatch):
    """Per-host search cap defaults to 4 (was 2 — the 13-scene queue).

    Fails on HEAD: the default is 2, so 13 concurrent scene queries
    stair-step through 2 slots (repro wall 41.3 s, tail 39.6 s).
    """
    from services.web_image import universal_scraper as usc

    monkeypatch.delenv("VIDZEO_UNIVERSAL_PER_HOST", raising=False)
    assert usc._universal_per_host() == 4
    monkeypatch.setenv("VIDZEO_UNIVERSAL_PER_HOST", "2")
    assert usc._universal_per_host() == 2


# ---------------------------------------------------------------------------
# Live-gated: real wave rows download to real files via the fixed path
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    os.environ.get("VIDZEO_LIVE_WEB_TEST") != "1",
    reason="live-gated: set VIDZEO_LIVE_WEB_TEST=1 to run against the real APIs",
)
def test_live_muse_rows_download_to_files(tmp_path):
    """End to end: wave rows -> download_bulk -> real files on disk.

    The 30 s scene budget with proxy retry must land most of a 6-URL
    same-host chunk. On HEAD the same chunk lands 4/6: two URLs die on
    the slot wait while 16-18 s transfers hold the 3/host slots.
    """
    from services.web_image import muse_search as muse_mod

    manager = muse_mod.MuseSearchManager()
    rows = manager.search("Panama Canal", q_idx=0)
    assert len(rows) >= 6, f"wave returned only {len(rows)} rows"
    urls = [row["url"] for row in rows[:6] if row.get("url")]
    assert len(urls) == 6
    stats = []
    results = download_bulk(
        urls, tmp_path, max_concurrent=6, timeout_seconds=30, stats=stats,
    )
    landed = sum(1 for _u, path, _e in results if path and Path(path).is_file())
    assert landed >= 5, (
        f"only {landed}/6 muse rows landed; "
        f"errors={[err for _u, _p, err in results]}"
    )
    # Per-host route policy: thumb/upload.wikimedia.org and
    # live.staticflickr.com try direct first, so at least one of these
    # six rows must land via the direct route. On HEAD every landing is
    # via the proxy (direct is only the retry).
    direct_landings = sum(
        1 for record in stats
        if record.get("route") == "direct" and record.get("landed")
    )
    assert direct_landings >= 1, (
        "no muse row landed via direct; "
        f"routes={[record.get('route') for record in stats]}"
    )
