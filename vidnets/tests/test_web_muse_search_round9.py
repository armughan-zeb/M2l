"""Round 9 (test 36 fix): the connect timeout -- not the transfer budget --
is the layer killing web downloads in the live job.

Test 36 proved, from its own log (vidnets-server-restart-20261123-1120.log):
  - 35 chunks, ALL landed 0 (~200 URLs attempted, 0 landed).
  - Median transfer ~21.3s, errors ALL 'timeout'. Chunk walls cluster at
    multiples of ~11s (21.9, 43.2, 54.3, 79.0, 88.0, 99.1, 110.0, ...).
  - The live per-URL total is 120s (fast_mode=False: no web_fast_mode in
    the job config), batch wall 180s, slot cap 60s. None of those is ~21s.
  - The only ~10s timer in the download path is round 8's connect
    timeout (10s). The live-path repro (work/round9/slow_connect_repro.py,
    HTTPS origin + blackholed direct route + 15s-delayed proxy CONNECT,
    real resolver entry point, real env) proves it with phase timings:
    EVERY attempt dies in the CONNECT phase at ~10s
    (ConnectionTimeoutError, conn_create_start with no conn_create_end),
    on BOTH routes, before a byte flows -- 0/6 landed, per-URL ~21s,
    chunk wall 43.8s, the exact test-36 signature. With the connect
    timeout raised to 60s the same setup lands 6/6 in 151.6s; with 30s
    it lands 6/6 in 77.3s.
  - Why 10s is wrong: the connect phase is the slow phase on this egress
    even when healthy -- direct connects to thumb/upload.wikimedia.org
    take 3.6-5.0s of a 4-6s transfer (measured live, Nov 23, healthy
    hour: 12/12 landed in 14.7s, wave 7.1s). Test 36's hour ran the muse
    wave in 80.7s vs 7.1s healthy (~11x slower); test 35's hour 98.9s
    (wikimedia 38.9s, openverse 36.8s). A 10s cap kills every
    slow-but-alive connect. The fix raises the default to 60s -- half
    the 120s per-URL total, so a truly blackholed route can burn at most
    half the attempt budget and the other route still gets its turn
    inside the 180s batch wall.

Every mocked test below fails on clean b91060e and passes with the fix.
The live-path repro is work/round9/slow_connect_repro.py.
"""

import asyncio as _asyncio
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_connect_timeout_default_is_sixty(monkeypatch):
    """The connect timeout defaults to 60 s (env-overridable).

    Fails on b91060e: 10.0. Test 36's median transfer was 21.3s --
    two ~10s connect-timeout deaths per URL -- because the 10s cap
    kills slow-but-alive connects on both routes.
    """
    from services.web_image import hybrid_search

    monkeypatch.delenv("VIDZEO_WEB_DOWNLOAD_CONNECT_TIMEOUT_SECONDS", raising=False)
    assert hybrid_search._download_connect_timeout_seconds() == 60.0
    monkeypatch.setenv("VIDZEO_WEB_DOWNLOAD_CONNECT_TIMEOUT_SECONDS", "25")
    assert hybrid_search._download_connect_timeout_seconds() == 25.0
    monkeypatch.setenv("VIDZEO_WEB_DOWNLOAD_CONNECT_TIMEOUT_SECONDS", "300")
    assert hybrid_search._download_connect_timeout_seconds() == 120.0


# --- slow-connect fakes: the connect phase takes ``delay`` seconds -------
# --- (round-8 pattern: latency honored, timeout enforced) ----------------

class _SlowContent:
    def __init__(self, data=b""):
        self._data = data

    async def iter_chunked(self, _n):
        if self._data:
            yield self._data


class _SlowConnectResponse:
    """Headers arrive after a ``delay``-second connect phase.

    If the passed timeout's connect (or total) cap is shorter than the
    delay, the connect dies at the cap with aiohttp's real
    ConnectionTimeoutError -- the exact error the live trace shows for
    test 36's attempts (conn_create_start, no conn_create_end).
    ``delay=None`` means the connect never completes (blackhole).
    """

    def __init__(self, delay, timeout=None):
        self.status = 200
        self.headers = {"Content-Type": "image/jpeg"}
        self.content = _SlowContent(b"x" * 20000)
        self._delay = delay
        self._timeout = timeout

    async def __aenter__(self):
        import aiohttp as _real_aiohttp

        timeout = self._timeout
        connect = getattr(timeout, "connect", None)
        total = getattr(timeout, "total", None)
        cap = connect
        if total is not None:
            cap = total if cap is None else min(cap, total)
        if self._delay is None:
            await _asyncio.sleep(cap if cap is not None else 60.0)
            raise _real_aiohttp.ConnectionTimeoutError(
                "Connection timeout to host thumb.wikimedia.org:443"
            )
        if cap is not None and cap < self._delay:
            await _asyncio.sleep(cap)
            raise _real_aiohttp.ConnectionTimeoutError(
                "Connection timeout to host thumb.wikimedia.org:443"
            )
        await _asyncio.sleep(self._delay)
        return self

    async def __aexit__(self, *args):
        return False


class _SlowConnectSession:
    """Direct session (trust_env=False) and proxy session (trust_env=True)
    each take ``delay`` seconds to connect. ``None`` = blackhole."""

    direct_delay = 25.0
    proxy_delay = 25.0

    def __init__(self, **kwargs):
        self.trust_env = kwargs.get("trust_env")
        self.session_timeout = kwargs.get("timeout")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def get(self, url, **kwargs):
        delay = self.proxy_delay if self.trust_env else self.direct_delay
        return _SlowConnectResponse(delay, kwargs.get("timeout"))


class _CapturingSession(_SlowConnectSession):
    """Records every per-request timeout and session timeout."""

    seen_timeouts = []
    seen_session_timeouts = []

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        _CapturingSession.seen_session_timeouts.append(
            kwargs.get("timeout")
        )

    def get(self, url, **kwargs):
        _CapturingSession.seen_timeouts.append(kwargs.get("timeout"))
        return super().get(url, **kwargs)


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


def _fresh_limiter(monkeypatch):
    from services.web_image import hybrid_search

    hybrid_search._DOWNLOAD_LIMITER = hybrid_search.DownloadLimiter(
        total=12, per_host=3, cdn_per_host=6,
    )


def _run_one(monkeypatch, tmp_path, timeout_seconds=120.0):
    from services.web_image import resolver

    _fresh_limiter(monkeypatch)
    t0 = time.monotonic()
    res = resolver._download_candidate_batch(
        [{"url": "https://thumb.wikimedia.org/wikipedia/commons/0/00/Test.jpg"}],
        tmp_path, max_concurrent=1, timeout_seconds=timeout_seconds,
        download_cache={}, download_cache_lock=threading.Lock(),
    )
    wall = time.monotonic() - t0
    landed = sum(1 for _u, p, _e in res if p and Path(p).is_file())
    return landed, wall, res


def test_slow_connect_lands_through_live_path(monkeypatch, tmp_path):
    """A 25 s connect on BOTH routes lands through the live download path.

    Through the resolver's real download entry point
    (_download_candidate_batch) with the fake transport. Fails on
    clean b91060e: the 10s connect cap kills attempt 1 (direct) at 10s
    and attempt 2 (proxy) at 10s -- 0 landed in ~21s, test 36's exact
    median (21.3s). With the fix (60s) the direct attempt connects at
    25s and lands at ~25s.
    """
    from services.web_image import hybrid_search  # noqa: F401  (env parity)

    monkeypatch.setenv("VIDZEO_NET_RETRIES", "1")
    monkeypatch.setenv("VIDZEO_WEB_DOWNLOAD_DIRECT_RETRY", "1")
    monkeypatch.delenv("VIDZEO_WEB_DOWNLOAD_CONNECT_TIMEOUT_SECONDS", raising=False)
    _SlowConnectSession.direct_delay = 25.0
    _SlowConnectSession.proxy_delay = 25.0
    _install_fake_aiohttp(monkeypatch, _SlowConnectSession)
    landed, wall, res = _run_one(monkeypatch, tmp_path)
    assert landed == 1, f"25s connect did not land (wall {wall:.1f}s, res={res})"
    assert 20.0 <= wall <= 40.0, f"unexpected wall {wall:.1f}s"


def test_blackholed_direct_has_no_proxy_fallback(monkeypatch, tmp_path):
    """Nov 25 direct-only: a blackholed direct route fails, it does not land.

    The direct route never connects and there is no proxy leg to fall
    back to, so nothing lands — but the failure is bounded by the two
    60 s connect caps inside the batch wall, never an infinite hang.
    """
    monkeypatch.setenv("VIDZEO_NET_RETRIES", "1")
    monkeypatch.setenv("VIDZEO_WEB_DOWNLOAD_DIRECT_RETRY", "1")
    monkeypatch.delenv("VIDZEO_WEB_DOWNLOAD_CONNECT_TIMEOUT_SECONDS", raising=False)
    _SlowConnectSession.direct_delay = None
    _SlowConnectSession.proxy_delay = 25.0
    _install_fake_aiohttp(monkeypatch, _SlowConnectSession)
    landed, wall, res = _run_one(monkeypatch, tmp_path)
    assert landed == 0, f"blackholed direct should not land: {res}"
    assert wall < 150.0, f"blackholed direct did not fail within the caps (wall {wall:.1f}s)"


def test_per_request_timeout_carries_connect_sixty(monkeypatch, tmp_path):
    """The live download path's per-request AND session timeouts carry
    connect=60.0 -- the timeout the job actually uses.

    Fails on clean b91060e: connect=10.0 on both. The session timeout
    used to hardcode connect=5.0 (round 9 aligns it with the helper;
    it never fires because every GET passes a per-request timeout,
    but a 5s cap is below even the healthy connect time).
    """
    monkeypatch.delenv("VIDZEO_WEB_DOWNLOAD_CONNECT_TIMEOUT_SECONDS", raising=False)
    _CapturingSession.seen_timeouts = []
    _CapturingSession.seen_session_timeouts = []
    _SlowConnectSession.direct_delay = 25.0
    _SlowConnectSession.proxy_delay = 25.0
    _install_fake_aiohttp(monkeypatch, _CapturingSession)
    landed, _wall, _res = _run_one(monkeypatch, tmp_path)
    assert landed == 1
    assert _CapturingSession.seen_timeouts, "no per-request timeout captured"
    for timeout in _CapturingSession.seen_timeouts:
        assert timeout is not None and timeout.connect == 60.0, (
            f"per-request timeout missing connect=60.0: total="
            f"{getattr(timeout, 'total', None)} connect="
            f"{getattr(timeout, 'connect', None)}"
        )
    assert _CapturingSession.seen_session_timeouts, "no session timeout captured"
    for timeout in _CapturingSession.seen_session_timeouts:
        assert timeout is not None and timeout.connect == 60.0, (
            f"session timeout missing connect=60.0: total="
            f"{getattr(timeout, 'total', None)} connect="
            f"{getattr(timeout, 'connect', None)}"
        )
