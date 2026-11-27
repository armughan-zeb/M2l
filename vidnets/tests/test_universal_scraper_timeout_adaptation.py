"""Slow-network timeout adaptation for the universal web image lane.

Nov 22 2026 (job d1ece590): Yandex, Bing and Flickr ALL timed out together
during a slow egress hour — each engine failed with "all routes failed
(direct,proxy): ReadTimeout" after ~60s (per-route socket timeout 30s x 2
routes) against a ~60s batch deadline. The batch already retries an
all-empty timed-out batch ONCE at 1.5x timeout on the other route, but when
the egress stays slow longer than one retry, the batch still comes back
empty — the deadline never adapts to the shared-network signal.

The fix: the manager tracks consecutive all-timeout batches (zero rows AND
every non-gated engine timed out — a shared-network signal, not a per-engine
failure) and raises the effective engine timeout and per-route fetch timeout
by 1.5x per consecutive all-timeout batch, capped at 2x the base. Any batch
that returns rows resets the factor: the network recovered.

Retries (Nov 22 user order): a timed-out batch gets up to 2 retry waves
(3 waves total), alternating route with backoff, each at the adapted
deadline — a single 60s timeout must not kill the lane. Bounded: no more
than 2 retry waves, then the batch settles empty and the resolver's own
retry/rescue takes over. Concurrency is NOT raised; retries reuse the same
throttled path.

These tests use fake engines with route-dependent latency (a slow proxy
route, a faster direct route — the production shape the retry was built
for) and NO real network. They must FAIL on the pre-fix code: without the
adaptation the retry wave and the next batch are cut at the same wall the
engines just died on.
"""
import threading
import time

import pytest

from services.web_image.universal_scraper import (
    UniversalImageResult,
    UniversalScraperManager,
    _universal_fetch_timeout_seconds,
)


def _slow_env(monkeypatch):
    """Pin the timing knobs so deadlines are exact, not stagger-masked."""
    monkeypatch.setenv("VIDZEO_UNIVERSAL_ENGINE_STAGGER_SECONDS", "0")
    monkeypatch.setenv("VIDZEO_UNIVERSAL_EMPTY_RETRY_BACKOFF_SECONDS", "0")
    monkeypatch.setenv("VIDZEO_UNIVERSAL_FETCH_TIMEOUT_SECONDS", "30")


class _SlowEngine:
    """Fake engine with route-dependent latency.

    ``proxy_sleep`` is the latency on the proxy route (direct=False),
    ``direct_sleep`` on the direct route (direct=True). Each call records
    the per-route fetch timeout it observed — _run_engine sets that
    thread-locally around the call, so this is the observable proof the
    adapted fetch timeout reached the pool worker thread.
    """

    def __init__(self, name, seen, proxy_sleep=0.0, direct_sleep=0.0):
        self.name = name
        self._seen = seen
        self.proxy_sleep = proxy_sleep
        self.direct_sleep = direct_sleep
        self.calls = 0

    def search(self, query, limit, page, safe_search, direct=False):
        self.calls += 1
        sleep = self.direct_sleep if direct else self.proxy_sleep
        self._seen.append(
            {
                "engine": self.name,
                "direct": direct,
                "fetch_timeout": _universal_fetch_timeout_seconds(),
            }
        )
        if sleep:
            time.sleep(sleep)
        return [
            UniversalImageResult(
                title=f"{self.name} row",
                image_url=f"https://images.example/{self.name}.jpg",
                source_engine=self.name,
            )
        ]


def _manager(engine_timeout_seconds, engines):
    manager = UniversalScraperManager(
        max_workers=3, engine_timeout_seconds=engine_timeout_seconds
    )
    manager._engines = dict(engines)
    return manager


def _shutdown(manager):
    try:
        manager._executor.shutdown(wait=True)
    except Exception:
        pass


def test_retry_wave_uses_adapted_deadline_after_all_timeout(monkeypatch):
    """The retry wave inside the SAME search gets the longer deadline.

    Base 2.0s: wave 1 dies at 2.0s, the pre-fix retry dies at 3.0s (the
    direct-route engines need 3.6s), and the batch ends empty — the late
    spool cannot rescue it because both waves' engines are still asleep
    when the batch ends. With the fix the retry deadline is
    2.0 * 1.5 (retry) * 1.5 (slow-network factor) = 4.5s, so the direct
    engines complete and the batch returns rows.
    """
    _slow_env(monkeypatch)
    seen = []
    engines = {
        name: _SlowEngine(name, seen, proxy_sleep=7.0, direct_sleep=3.6)
        for name in ("yandex", "bing", "flickr")
    }
    manager = _manager(2.0, engines)
    try:
        resp = manager.search(
            "slow network retry", engine_ids=["yandex", "bing", "flickr"]
        )
    finally:
        _shutdown(manager)
    # Wave 1 saw the base fetch timeout; the retry wave saw it adapted.
    wave1 = [s for s in seen if not s["direct"]]
    retry = [s for s in seen if s["direct"]]
    assert wave1 and all(s["fetch_timeout"] == pytest.approx(30.0) for s in wave1)
    assert retry and all(s["fetch_timeout"] == pytest.approx(45.0) for s in retry)
    assert resp.total == 3


def test_next_batch_uses_adapted_deadline_after_all_timeout(monkeypatch):
    """A batch after an all-timeout batch starts with the longer deadline.

    Batch 1 ends empty on both old and new code (engines need longer than
    even the adapted retries — and stay asleep past the batch end, so the
    late spool cannot rescue it either; the waterfall wall is ~7.5s, so
    the 12s sleeps stay safely past it). The fix records the streak, so
    batch 2's wave 1 runs at 1.0 * 1.5 = 1.5s and its engines (1.0s)
    complete in wave 1 — and see the adapted 45s fetch timeout. On the
    pre-fix code batch 2's wave 1 is still 1.0s and the factor stays 1.0x.
    """
    _slow_env(monkeypatch)
    seen = []
    engines = {
        name: _SlowEngine(name, seen, proxy_sleep=12.0, direct_sleep=12.0)
        for name in ("yandex", "bing", "flickr")
    }
    manager = _manager(1.0, engines)
    try:
        resp1 = manager.search(
            "slow network batch one", engine_ids=["yandex", "bing", "flickr"]
        )
        assert resp1.total == 0
        assert manager.slow_network_streak == 1
        assert manager.slow_network_factor == pytest.approx(1.5)

        for engine in engines.values():
            engine.proxy_sleep = 1.0
            engine.direct_sleep = 1.0
        seen.clear()
        resp2 = manager.search(
            "slow network batch two", engine_ids=["yandex", "bing", "flickr"]
        )
    finally:
        _shutdown(manager)
    assert manager.last_batch_max_deadline_seconds == pytest.approx(1.5)
    wave1 = [s for s in seen if not s["direct"]]
    assert wave1 and all(s["fetch_timeout"] == pytest.approx(45.0) for s in wave1)
    assert resp2.total == 3
    # Rows came back: the network recovered, so the streak resets.
    assert manager.slow_network_streak == 0
    assert manager.slow_network_factor == pytest.approx(1.0)


def test_bot_gate_failure_does_not_arm_slow_network(monkeypatch):
    """A bot gate is a refusal, not a slow network: no adaptation."""

    class _GatedEngine:
        name = "gated"

        def search(self, query, limit, page, safe_search, direct=False):
            raise RuntimeError("yandex: bot challenge captcha detected")

    _slow_env(monkeypatch)
    manager = _manager(2.0, {"yandex": _GatedEngine()})
    try:
        resp = manager.search("gated query", engine_ids=["yandex"])
    finally:
        _shutdown(manager)
    assert resp.total == 0
    assert manager.slow_network_streak == 0
    assert manager.slow_network_factor == pytest.approx(1.0)
    assert manager.last_batch_max_deadline_seconds == pytest.approx(2.0)


def test_partial_success_does_not_arm_slow_network(monkeypatch):
    """One engine answering is a result, not a slow network: no adaptation
    and no retry wave."""
    _slow_env(monkeypatch)
    seen = []
    engines = {
        "yandex": _SlowEngine("yandex", seen, proxy_sleep=0.0, direct_sleep=0.0),
        "bing": _SlowEngine("bing", seen, proxy_sleep=5.0, direct_sleep=5.0),
        "flickr": _SlowEngine("flickr", seen, proxy_sleep=5.0, direct_sleep=5.0),
    }
    manager = _manager(2.0, engines)
    try:
        resp = manager.search(
            "partial success", engine_ids=["yandex", "bing", "flickr"]
        )
    finally:
        _shutdown(manager)
    assert resp.total == 1
    assert manager.slow_network_streak == 0
    assert manager.slow_network_factor == pytest.approx(1.0)
    assert engines["bing"].calls == 1
    assert engines["flickr"].calls == 1


class _StagedEngine:
    """Fake engine whose latency depends on the attempt number.

    ``sleeps[i]`` is the latency of the i-th call (wave 1, retry 1,
    retry 2, ...). Models an egress that stays slow for the first
    attempts and then recovers — the retry waves must keep the lane
    alive until it does.
    """

    def __init__(self, name, seen, sleeps):
        self.name = name
        self._seen = seen
        self.sleeps = list(sleeps)
        self.calls = 0

    def search(self, query, limit, page, safe_search, direct=False):
        self.calls += 1
        index = min(self.calls - 1, len(self.sleeps) - 1)
        self._seen.append(
            {
                "engine": self.name,
                "direct": direct,
                "fetch_timeout": _universal_fetch_timeout_seconds(),
                "attempt": self.calls,
            }
        )
        if self.sleeps[index]:
            time.sleep(self.sleeps[index])
        return [
            UniversalImageResult(
                title=f"{self.name} row",
                image_url=f"https://images.example/{self.name}.jpg",
                source_engine=self.name,
            )
        ]


def test_second_retry_wave_recovers_when_first_retry_also_times_out(monkeypatch):
    """User order (Nov 22): one slow hour must not zero the lane.

    Base 2.0s: wave 1 dies at 2.0s (engines need 7.0s), retry 1 dies at
    4.5s (engines need 5.0s), retry 2 — same 4.5s adapted deadline, route
    flipped back — succeeds (engines need 2.5s) and the batch returns
    rows. With only the old single retry the batch would end empty.
    """
    _slow_env(monkeypatch)
    seen = []
    engines = {
        name: _StagedEngine(name, seen, sleeps=[7.0, 5.0, 2.5])
        for name in ("yandex", "bing", "flickr")
    }
    manager = _manager(2.0, engines)
    try:
        resp = manager.search(
            "second retry recovers", engine_ids=["yandex", "bing", "flickr"]
        )
    finally:
        _shutdown(manager)
    assert resp.total == 3
    assert all(engine.calls == 3 for engine in engines.values())
    # All three waves ran at the adapted fetch timeout after wave 1.
    retry2 = [s for s in seen if s["attempt"] == 3]
    assert retry2 and all(s["fetch_timeout"] == pytest.approx(45.0) for s in retry2)


def test_retries_are_bounded_at_two_waves(monkeypatch):
    """No infinite loops: a persistently dead egress gets wave 1 + 2
    retries (3 calls per engine), then the batch settles empty and the
    resolver's own retry/rescue takes over."""
    _slow_env(monkeypatch)
    seen = []
    engines = {
        name: _SlowEngine(name, seen, proxy_sleep=4.0, direct_sleep=4.0)
        for name in ("yandex", "bing", "flickr")
    }
    manager = _manager(0.5, engines)
    try:
        resp = manager.search(
            "bounded retries", engine_ids=["yandex", "bing", "flickr"],
            timeout_seconds=0.5,
        )
    finally:
        _shutdown(manager)
    assert resp.total == 0
    assert all(engine.calls == 3 for engine in engines.values())
    # One streak bump per batch, even across two dead retry waves.
    assert manager.slow_network_streak == 1
    assert manager.slow_network_factor == pytest.approx(1.5)


def test_retry_wave_count_is_env_tunable(monkeypatch):
    """Operators can dial the lane back to the old single retry."""
    monkeypatch.setenv("VIDZEO_UNIVERSAL_EMPTY_RETRY_WAVES", "1")
    from services.web_image import universal_scraper as scraper

    assert scraper._universal_empty_retry_waves() == 1
    monkeypatch.setenv("VIDZEO_UNIVERSAL_EMPTY_RETRY_WAVES", "99")
    assert scraper._universal_empty_retry_waves() == 2
    monkeypatch.setenv("VIDZEO_UNIVERSAL_EMPTY_RETRY_WAVES", "0")
    assert scraper._universal_empty_retry_waves() == 1


def test_slow_network_factor_caps_at_configured_max(monkeypatch):
    """1.5x per consecutive all-timeout batch, capped at the configured max.

    The default cap is 1.5x (muse-search design §2.4 — the old 2.0x cap
    grew timeouts to 120–180 s in test 30); this test pins the cap at
    2x via env to prove the cap itself is honoured. The 10s sleeps stay
    asleep past the waterfall batch wall (~6s at most), so the late spool
    cannot rescue a batch that must end empty.
    """
    _slow_env(monkeypatch)
    monkeypatch.setenv("VIDZEO_UNIVERSAL_SLOW_NETWORK_MAX_FACTOR", "2")
    seen = []
    engines = {
        name: _SlowEngine(name, seen, proxy_sleep=10.0, direct_sleep=10.0)
        for name in ("yandex", "bing", "flickr")
    }
    # Base 0.5s via timeout_seconds: the constructor floors at 1.0s.
    manager = _manager(0.5, engines)
    try:
        assert manager.search(
            "cap batch one", engine_ids=["yandex", "bing", "flickr"],
            timeout_seconds=0.5,
        ).total == 0
        assert manager.slow_network_factor == pytest.approx(1.5)
        assert manager.search(
            "cap batch two", engine_ids=["yandex", "bing", "flickr"],
            timeout_seconds=0.5,
        ).total == 0
        assert manager.slow_network_streak == 2
        assert manager.slow_network_factor == pytest.approx(2.0)
        assert manager.last_batch_max_deadline_seconds == pytest.approx(0.75)
        assert manager.search(
            "cap batch three", engine_ids=["yandex", "bing", "flickr"],
            timeout_seconds=0.5,
        ).total == 0
        assert manager.slow_network_streak == 3
        assert manager.slow_network_factor == pytest.approx(2.0)
        assert manager.last_batch_max_deadline_seconds == pytest.approx(1.0)
    finally:
        _shutdown(manager)


# --- Test 25 (job 655e40d8): fast transport failures must arm it too ---


class _FailingEngine:
    """Fake engine that fails FAST with a transport error (test 25 shape).

    The failure completes BEFORE the batch deadline — exactly how
    Bing/Flickr died in test 25 (ReadTimeout at ~60s vs a 65/70s
    staggered deadline), so the old timed_out-only trigger never saw it.
    ``error`` is the full exception text the production engine raises.
    """

    def __init__(self, name, error):
        self.name = name
        self.error = error
        self.calls = 0

    def search(self, query, limit, page, safe_search, direct=False):
        self.calls += 1
        raise RuntimeError(self.error)


_YANDEX_SLOT_ERROR = (
    "yandex: all routes failed (proxy,direct): TimeoutError: throttled "
    "request to yandex.com exceeded its 30s budget waiting for a host slot"
)
_BING_READ_TIMEOUT = (
    "bing: all routes failed (proxy,direct): ReadTimeout: "
    "HTTPSConnectionPool(host='www.bing.com', port=443): Read timed out. "
    "(read timeout=30.0)"
)
_FLICKR_READ_TIMEOUT = (
    "flickr: all routes failed (proxy,direct): ReadTimeout: "
    "HTTPSConnectionPool(host='www.flickr.com', port=443): Read timed out. "
    "(read timeout=30.0)"
)


def test_fast_transport_failures_arm_slow_network(monkeypatch):
    """Test 25: engines failing fast with transport errors arm the streak.

    All three engines fail in milliseconds (no sleeps) with the exact
    error texts test 25 logged — ReadTimeouts for Bing/Flickr, a host-slot
    wait for Yandex — against a 2.0s batch deadline. On the pre-fix code
    the streak stays 0: the failures complete before their staggered
    deadlines, so no engine lands in ``timed_out`` and the old trigger
    (every engine still running past its deadline) never fires. With the
    fix the failure TEXT counts, the streak goes to 1, and the next batch
    runs at 1.5x.
    """
    _slow_env(monkeypatch)
    engines = {
        "yandex": _FailingEngine("yandex", _YANDEX_SLOT_ERROR),
        "bing": _FailingEngine("bing", _BING_READ_TIMEOUT),
        "flickr": _FailingEngine("flickr", _FLICKR_READ_TIMEOUT),
    }
    manager = _manager(2.0, engines)
    try:
        resp = manager.search(
            "test25 fast failures", engine_ids=["yandex", "bing", "flickr"]
        )
    finally:
        _shutdown(manager)
    assert resp.total == 0
    assert manager.slow_network_streak == 1
    assert manager.slow_network_factor == pytest.approx(1.5)
    # The retry waves retried ALL stalled engines, not just yandex:
    # wave 1 + 2 retries = 3 calls per engine (pre-fix: 1 call each,
    # because timed_out was empty and the retry loop broke immediately).
    assert all(engine.calls == 3 for engine in engines.values())


def test_refusal_among_transport_failures_does_not_arm(monkeypatch):
    """A 403 refusal among transport failures is a refusal, not a stall."""
    _slow_env(monkeypatch)
    engines = {
        "yandex": _FailingEngine("yandex", _YANDEX_SLOT_ERROR),
        "bing": _FailingEngine("bing", "bing: HTTP 403 Forbidden"),
        "flickr": _FailingEngine("flickr", _FLICKR_READ_TIMEOUT),
    }
    manager = _manager(2.0, engines)
    try:
        resp = manager.search(
            "refusal among stalls", engine_ids=["yandex", "bing", "flickr"]
        )
    finally:
        _shutdown(manager)
    assert resp.total == 0
    assert manager.slow_network_streak == 0
    assert manager.slow_network_factor == pytest.approx(1.0)


def test_transport_stall_classifier():
    """Unit: the failure-text classifier used by the streak trigger."""
    from services.web_image.universal_scraper import (
        _is_transport_stall_error_text,
    )

    assert _is_transport_stall_error_text(_BING_READ_TIMEOUT) is True
    assert _is_transport_stall_error_text(_YANDEX_SLOT_ERROR) is True
    assert _is_transport_stall_error_text(
        "flickr: ConnectTimeout: HTTPSConnectionPool timed out"
    ) is True
    assert _is_transport_stall_error_text(
        "yandex: ConnectionError: Failed to establish a new connection"
    ) is True
    assert _is_transport_stall_error_text("engine timeout") is True
    assert _is_transport_stall_error_text("bing: HTTP 403 Forbidden") is False
    assert _is_transport_stall_error_text("bing: HTTP 429 Too Many Requests") is False
    assert _is_transport_stall_error_text(
        "yandex: bot challenge captcha detected"
    ) is False
    assert _is_transport_stall_error_text("") is False
    assert _is_transport_stall_error_text(None) is False
