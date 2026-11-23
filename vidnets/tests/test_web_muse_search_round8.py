"""Round 8 (test 35 fix): the web-image download wall, fixed properly.

Test 35 proved, from its own log (vidnets-server-restart-20261123-0932.log):

BUG 1 — the 60 s per-URL budget is below the slow-hour transfer time.
Median transfer 61.0 s — censored AT the 60 s line, so the true need is
above 60 s — and 25 of 26 chunks landed 0 (~150 URLs attempted, 2
landed). Slot-wait median 0.1 s: the queue is NOT the constraint any
more (round 7 fixed that). The slow-hour transfer bracket is ~60-120 s
(test 34's hour landed transfers inside 60 s; test 35's hour needs
more), so the default doubles to 120 s (fast mode 60 s). 120 s is the
largest budget that still leaves room for the second route inside the
180 s batch wall.

BUG 2 — the route fallback is dead in the job. VIDZEO_NET_RETRIES=1 is
live in the server env, so max_attempts = min(2, 1) = 1: each URL gets
ONE route. Direct-first CDN hosts never fall back to the proxy, so a
direct connect that blackholes kills the URL. The two attempts are the
two egress paths, not repeats of the same request — the fallback must
not be gated on the retries knob.

BUG 3 — a hung connect burns the whole total budget. The session sets
connect=5.0, but every per-request timeout overrides the session
timeout with a total-only ClientTimeout, so a hung connect has NO
connect limit. Test 35's 'Cannot connect to host
live.staticflickr.com:443' after ~60 s is aiohttp relabeling a
total-timeout-during-connect: a SYN-blackholed connect. A 10 s connect
timeout fails it fast and leaves the wall for the other route.

BUG 4 (same wall, single-candidate path) — _download_and_accept_single_candidate
hardcodes timeout_seconds=30 for the url / preview / original attempts,
so a slow hour kills the single-candidate rescue too. It now uses the
scene budget.

Universal lane: deliberately NOT changed. It already adapts (the
slow-network factor, capped at 1.5x by design after test 30 showed 2.0x
deepens slot saturation) and it DID return rows for 6 scenes in test
35's slow hour. The download wall — not universal rows — decided the
media mix (rows existed; downloads landed 2/~150).

Every mocked test below fails on clean 93a5f96 and passes with the fix.
The slow live repro is work/round8/slow_repro.py (local drip server +
blackhole + forward proxy, through the resolver's real download path).
"""

import asyncio as _asyncio
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests  # noqa: F401  (kept for parity with the round-7 file)


# ---------------------------------------------------------------------------
# BUG 1: the per-URL transfer budget must cover the slow-hour bracket
# ---------------------------------------------------------------------------

def test_download_timeout_default_is_120(monkeypatch):
    """Non-fast default 120 s, fast mode 60 s, env clamp max 300 s.

    Fails on 93a5f96: 60.0 / 30.0 / 120.0. Test 35's median transfer
    was censored at the 60 s line — the budget must sit above it.
    """
    from services.web_image import resolver

    monkeypatch.delenv("VIDZEO_WEB_IMAGE_DOWNLOAD_TIMEOUT_SECONDS", raising=False)
    assert resolver._web_image_download_timeout_seconds(False) == 120.0
    assert resolver._web_image_download_timeout_seconds(True) == 60.0
    monkeypatch.setenv("VIDZEO_WEB_IMAGE_DOWNLOAD_TIMEOUT_SECONDS", "300")
    assert resolver._web_image_download_timeout_seconds(False) == 300.0
    monkeypatch.setenv("VIDZEO_WEB_IMAGE_DOWNLOAD_TIMEOUT_SECONDS", "5")
    assert resolver._web_image_download_timeout_seconds(False) == 5.0


# --- slow-transport fakes (round-7 pattern): latency honored, timeout enforced

class _SlowContent:
    def __init__(self, data=b""):
        self._data = data

    async def iter_chunked(self, _n):
        if self._data:
            yield self._data


class _SlowResponse:
    """Headers arrive after ``latency``; a shorter timeout raises first."""

    def __init__(self, latency, timeout_total):
        self.status = 200
        self.headers = {"Content-Type": "image/jpeg"}
        self.content = _SlowContent(b"x" * 20000)
        self._latency = latency
        self._timeout_total = timeout_total

    async def __aenter__(self):
        if self._timeout_total is not None and self._timeout_total < self._latency:
            await _asyncio.sleep(self._timeout_total)
            raise _asyncio.TimeoutError()
        await _asyncio.sleep(self._latency)
        return self

    async def __aexit__(self, *args):
        return False


class _SlowSession:
    """Lands on both routes after ``latency`` seconds per transfer."""

    latency = 75.0

    def __init__(self, **kwargs):
        self.trust_env = kwargs.get("trust_env")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def get(self, url, **kwargs):
        timeout = kwargs.get("timeout")
        return _SlowResponse(self.latency, getattr(timeout, "total", None))


class _FakeTimeout:
    def __init__(self, total=None, connect=None):
        self.total = total
        self.connect = connect


class _FakeConnector:
    def __init__(self, *args, **kwargs):
        pass


def _install_fake_aiohttp(monkeypatch, session_cls):
    import aiohttp as _real_aiohttp

    from services.web_image import hybrid_search

    monkeypatch.setattr(hybrid_search, "aiohttp", SimpleNamespace(
        ClientSession=session_cls,
        TCPConnector=_FakeConnector,
        ClientTimeout=_FakeTimeout,
        ClientConnectionError=_real_aiohttp.ClientConnectionError,
    ))


def test_slow_transfer_lands_with_120_budget(monkeypatch, tmp_path):
    """A 75 s transfer lands under the code's own default budget.

    Through the resolver's real download path (_download_candidate_batch)
    with the fake transport. Fails on 93a5f96: the 60 s default kills
    the 75 s transfer (0 landed, ~60 s). With the fix (120 s) it lands
    at ~75 s — test 35's shape, where the median transfer was censored
    at the 60 s line.
    """
    from services.web_image import hybrid_search, resolver

    _install_fake_aiohttp(monkeypatch, _SlowSession)
    _SlowSession.latency = 75.0
    monkeypatch.delenv("VIDZEO_WEB_IMAGE_DOWNLOAD_TIMEOUT_SECONDS", raising=False)
    hybrid_search._DOWNLOAD_LIMITER = hybrid_search.DownloadLimiter(
        total=12, per_host=3, cdn_per_host=6,
    )
    timeout = resolver._web_image_download_timeout_seconds(False)
    t0 = time.monotonic()
    res = resolver._download_candidate_batch(
        [{"url": "https://thumb.wikimedia.org/wikipedia/commons/0/00/Test.jpg"}],
        tmp_path, max_concurrent=1, timeout_seconds=timeout,
        download_cache={}, download_cache_lock=threading.Lock(),
    )
    wall = time.monotonic() - t0
    landed = sum(1 for _u, p, _e in res if p and Path(p).is_file())
    assert landed == 1, f"75 s transfer did not land (wall {wall:.1f}s, res={res})"
    assert 70.0 <= wall <= 130.0, f"unexpected wall {wall:.1f}s"


# ---------------------------------------------------------------------------
# BUG 2 + 3: route fallback + connect timeout (route-aware fakes)
# ---------------------------------------------------------------------------

class _FastResponse:
    def __init__(self):
        self.status = 200
        self.headers = {"Content-Type": "image/jpeg"}
        self.content = _SlowContent(b"x" * 20000)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class _FailingResponse:
    """Direct route fails at once (connection refused)."""

    def __init__(self, timeout=None):
        self._timeout = timeout

    async def __aenter__(self):
        import aiohttp as _real_aiohttp

        raise _real_aiohttp.ClientConnectionError(
            "Cannot connect to host thumb.wikimedia.org:443"
        )

    async def __aexit__(self, *args):
        return False


class _HangResponse:
    """Direct route hangs. A connect timeout fails it at ``connect``;
    without one it hangs until ``total`` — aiohttp's real semantics
    for a SYN-blackholed connect (test 35's ~60 s 'Cannot connect')."""

    def __init__(self, timeout=None):
        self._timeout = timeout

    async def __aenter__(self):
        import aiohttp as _real_aiohttp

        timeout = self._timeout
        connect = getattr(timeout, "connect", None)
        total = getattr(timeout, "total", None)
        if connect is not None:
            await _asyncio.sleep(connect)
            raise _real_aiohttp.ClientConnectionError(
                "Cannot connect to host thumb.wikimedia.org:443"
            )
        await _asyncio.sleep(total if total is not None else 60.0)
        raise _asyncio.TimeoutError()

    async def __aexit__(self, *args):
        return False


class _RouteSession:
    """Direct session (trust_env=False) fails per ``direct_mode``; the
    proxy session (trust_env=True) always lands fast."""

    direct_mode = "fail"

    def __init__(self, **kwargs):
        self.trust_env = kwargs.get("trust_env")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def get(self, url, **kwargs):
        timeout = kwargs.get("timeout")
        if self.trust_env is False:
            if self.direct_mode == "hang":
                return _HangResponse(timeout)
            return _FailingResponse(timeout)
        return _FastResponse()


class _CapturingSession(_RouteSession):
    """Records every per-request timeout; proxy lands fast."""

    seen_timeouts = []

    def get(self, url, **kwargs):
        self.seen_timeouts.append(kwargs.get("timeout"))
        if self.trust_env is False:
            return _FailingResponse(kwargs.get("timeout"))
        return _FastResponse()


def _fresh_limiter(monkeypatch):
    from services.web_image import hybrid_search

    hybrid_search._DOWNLOAD_LIMITER = hybrid_search.DownloadLimiter(
        total=12, per_host=3, cdn_per_host=6,
    )


def test_route_fallback_runs_despite_net_retries_1(monkeypatch, tmp_path):
    """With NET_RETRIES=1 the proxy fallback still runs.

    Direct fails at once; the proxy lands. Fails on 93a5f96:
    max_attempts = min(2, 1) = 1, so the URL dies on the direct
    failure and the proxy is never tried — test 35's connect-error
    URLs, where the direct-first CDN host never fell back.
    """
    from services.web_image import hybrid_search, resolver

    monkeypatch.setenv("VIDZEO_NET_RETRIES", "1")
    monkeypatch.setenv("VIDZEO_WEB_DOWNLOAD_DIRECT_RETRY", "1")
    _RouteSession.direct_mode = "fail"
    _install_fake_aiohttp(monkeypatch, _RouteSession)
    _fresh_limiter(monkeypatch)
    res = resolver._download_candidate_batch(
        [{"url": "https://thumb.wikimedia.org/wikipedia/commons/0/00/Test.jpg"}],
        tmp_path, max_concurrent=1, timeout_seconds=120.0,
        download_cache={}, download_cache_lock=threading.Lock(),
    )
    landed = sum(1 for _u, p, _e in res if p and Path(p).is_file())
    assert landed == 1, f"proxy fallback did not run under NET_RETRIES=1: {res}"


def test_per_request_timeout_includes_connect_timeout(monkeypatch, tmp_path):
    """The per-request timeout carries connect=60.0.

    Round 9 (test 36): the 10s connect cap killed every attempt in
    the connect phase, on both routes, before a byte flowed -- the
    connect phase itself takes 3.6-5s healthy and 10-40s+ in a slow
    hour. The default is 60s (half the 120s total). Fails on b91060e:
    the per-request ClientTimeout carries connect=10.0.
    """
    from services.web_image import hybrid_search, resolver

    monkeypatch.delenv("VIDZEO_WEB_DOWNLOAD_CONNECT_TIMEOUT_SECONDS", raising=False)
    _CapturingSession.seen_timeouts = []
    _install_fake_aiohttp(monkeypatch, _CapturingSession)
    _fresh_limiter(monkeypatch)
    resolver._download_candidate_batch(
        [{"url": "https://thumb.wikimedia.org/wikipedia/commons/0/00/Test.jpg"}],
        tmp_path, max_concurrent=1, timeout_seconds=120.0,
        download_cache={}, download_cache_lock=threading.Lock(),
    )
    assert _CapturingSession.seen_timeouts, "no per-request timeout captured"
    for timeout in _CapturingSession.seen_timeouts:
        assert timeout is not None and timeout.connect == 60.0, (
            f"per-request timeout missing connect=60.0: total="
            f"{getattr(timeout, 'total', None)} connect="
            f"{getattr(timeout, 'connect', None)}"
        )


def test_hung_connect_fails_fast_then_proxy_lands(monkeypatch, tmp_path):
    """A SYN-blackholed direct connect fails at ~60 s, proxy lands.

    Direct hangs; the proxy lands fast. Round 9 (test 36): the 10s
    connect cap that used to fail this fast killed every slow-but-alive
    connect in the job, so the default is now 60s (half the 120s total)
    -- a hung connect costs up to 60s, then the proxy lands inside the
    180s batch wall. This test pins the NEW behavior (it passes on the
    old 10s cap too, faster -- the round-9 tests carry the
    fail-on-clean proof that 10s kills slow connects).
    """
    from services.web_image import hybrid_search, resolver

    monkeypatch.setenv("VIDZEO_NET_RETRIES", "1")
    monkeypatch.setenv("VIDZEO_WEB_DOWNLOAD_DIRECT_RETRY", "1")
    monkeypatch.delenv("VIDZEO_WEB_DOWNLOAD_CONNECT_TIMEOUT_SECONDS", raising=False)
    _RouteSession.direct_mode = "hang"
    _install_fake_aiohttp(monkeypatch, _RouteSession)
    _fresh_limiter(monkeypatch)
    t0 = time.monotonic()
    res = resolver._download_candidate_batch(
        [{"url": "https://thumb.wikimedia.org/wikipedia/commons/0/00/Test.jpg"}],
        tmp_path, max_concurrent=1, timeout_seconds=120.0,
        download_cache={}, download_cache_lock=threading.Lock(),
    )
    wall = time.monotonic() - t0
    landed = sum(1 for _u, p, _e in res if p and Path(p).is_file())
    assert landed == 1, f"hung direct + proxy fallback did not land: {res}"
    assert wall < 90.0, f"hung connect did not fail within the 60s cap (wall {wall:.1f}s)"


def test_connect_timeout_default_is_sixty(monkeypatch):
    """The connect timeout defaults to 60 s (env-overridable).

    Round 9 (test 36): 10s killed every slow-but-alive connect.
    """
    from services.web_image import hybrid_search

    monkeypatch.delenv("VIDZEO_WEB_DOWNLOAD_CONNECT_TIMEOUT_SECONDS", raising=False)
    assert hybrid_search._download_connect_timeout_seconds() == 60.0
    monkeypatch.setenv("VIDZEO_WEB_DOWNLOAD_CONNECT_TIMEOUT_SECONDS", "25")
    assert hybrid_search._download_connect_timeout_seconds() == 25.0


# ---------------------------------------------------------------------------
# BUG 4: the single-candidate path must use the scene budget, not 30 s
# ---------------------------------------------------------------------------

def test_single_candidate_uses_scene_budget(monkeypatch, tmp_path):
    """_download_and_accept_single_candidate passes the scene budget.

    Fails on 93a5f96: the url / preview / original attempts hardcode
    timeout_seconds=30, so a slow hour kills the single-candidate
    rescue exactly like the batch path. With the fix they use
    _web_image_download_timeout_seconds() (120 s default).
    """
    import threading as _threading

    from services.web_image import resolver

    captured = []

    def fake_bulk(urls, output_dir, max_concurrent=1, timeout_seconds=None,
                  headers_by_url=None, **kw):
        captured.append(timeout_seconds)
        return []

    monkeypatch.setattr(resolver, "download_bulk", fake_bulk)
    monkeypatch.setattr(resolver, "candidate_is_safe", lambda item: (True, ""))
    monkeypatch.setattr(resolver, "_web_junk_metadata", lambda *a: False)
    monkeypatch.delenv("VIDZEO_WEB_IMAGE_DOWNLOAD_TIMEOUT_SECONDS", raising=False)
    item = {
        "url": "https://thumb.wikimedia.org/wikipedia/commons/0/00/Test.jpg",
        "title": "Panama Canal",
        "source_url": "https://en.wikipedia.org/wiki/Panama_Canal",
        "provider": "wikimedia",
    }
    out = resolver._download_and_accept_single_candidate(
        item, tmp_path, 0, "Panama Canal scene", set(), "Panama Canal",
        {}, {}, set(), _threading.Lock(), _threading.Lock(), _threading.Lock(),
    )
    assert out is None  # no download landed (fake returns []), so no accept
    assert captured == [120.0], f"single-candidate budget not the scene budget: {captured}"
