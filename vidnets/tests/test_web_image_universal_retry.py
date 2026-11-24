"""Bounded retry for the universal (four-engine) web image lane.

Nov 18 (Moon 20s test): every engine failed transiently through the proxy
(Flickr/Bing/Yandex ReadTimeout, DuckDuckGo bot-gated) and all 5 scenes died
with 0 candidates — the timed-out queries burned the stage deadline before
query 2 could run. The fix retries the same query once (2 attempts total):
attempt 1 via the egress proxy, attempt 2 direct (proxy bypass). A genuine
no-results (an engine answered with zero rows) is never retried, and a
persistent failure is bounded to the configured attempt count.

Nov 18 (web-resilience build): the original bypass was a thread-local flag
set on the caller thread — a NO-OP in production, because HTTP runs on
ThreadPoolExecutor worker threads where the flag is always False. The
retry now passes an explicit ``direct=`` parameter down to the worker
thread, and the fake scraper in these tests submits through a real
ThreadPoolExecutor (mirroring UniversalScraperManager.search) so a
regression of that shape fails here instead of passing silently.
"""
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
import requests

from services.web_image import resolver
from services.web_image.universal_scraper import (
    _Engine,
    _new_route_session,
    _note_route_result,
    _route_order,
    _throttled_get,
    _web_bypass_proxy_enabled,
    web_bypass_proxy,
    BaiduEngine,
    BingEngine,
    FlickrEngine,
    OpenverseEngine,
    UniversalImageResult,
    UniversalScraperManager,
    YandexEngine,
)


def _failed_stat(engine, error="ReadTimeout"):
    return SimpleNamespace(
        engine=engine, count=0, duration_ms=15000, success=False, error=error
    )


def _ok_stat(engine):
    return SimpleNamespace(
        engine=engine, count=0, duration_ms=1000, success=True, error=None
    )


def _install_fake_scraper(monkeypatch, search_fn, rescue_attempts="0"):
    """Install a fake _UNIVERSAL_SCRAPER + the small resolver collaborators.

    The fake search runs on a real ThreadPoolExecutor, mirroring
    UniversalScraperManager.search: the resolver's ``direct=`` flag must
    survive the executor hop to reach the worker thread. Rescue is off
    by default (fast tests); rescue tests opt in explicitly.
    """
    monkeypatch.delenv("VIDZEO_UNIVERSAL_SCRAPER_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("VIDZEO_UNIVERSAL_LATE_WAIT_SECONDS", "0")
    monkeypatch.setenv("VIDZEO_WEB_UNIVERSAL_RETRY_BACKOFF_SECONDS", "0")
    monkeypatch.setenv("VIDZEO_WEB_UNIVERSAL_RESCUE_ATTEMPTS", rescue_attempts)
    monkeypatch.setenv("VIDZEO_WEB_UNIVERSAL_RESCUE_BACKOFF_SECONDS", "0")
    monkeypatch.setenv("VIDZEO_WEB_UNIVERSAL_RESCUE_DEADLINE_SECONDS", "60")

    def search_via_pool(query, **options):
        with ThreadPoolExecutor(max_workers=2) as ex:
            return ex.submit(search_fn, query, **options).result()

    monkeypatch.setattr(
        resolver,
        "_UNIVERSAL_SCRAPER",
        SimpleNamespace(search=search_via_pool, collect_late_results=lambda _q, **_: []),
    )
    monkeypatch.setattr(
        resolver, "universal_result_to_candidate", lambda item, **_: {"url": item.image_url}
    )
    monkeypatch.setattr(resolver, "candidate_is_safe", lambda _candidate: (True, "fixture"))
    monkeypatch.setattr(resolver, "_report_provider_success", lambda _source: None)
    monkeypatch.setattr(resolver, "_report_provider_failure", lambda *_args: None)


def test_retry_fires_on_transient_failure_then_returns_rows(monkeypatch):
    calls = []

    def search(query, **options):
        # The direct flag must arrive on the POOL thread, not just the caller.
        assert threading.current_thread().name != "MainThread"
        calls.append(options.get("direct"))
        if len(calls) == 1:
            stats = [_failed_stat("bing"), _failed_stat("yandex"), _failed_stat("flickr")]
            return resolver.UniversalSearchResponse(query, 0, [], stats)
        first = SimpleNamespace(image_url="https://images.example/moon.jpg")
        stats = [_ok_stat("bing"), _failed_stat("yandex"), _failed_stat("flickr")]
        return resolver.UniversalSearchResponse(query, 1, [first], stats)

    _install_fake_scraper(monkeypatch, search)
    rows = resolver._discover_universal_urls("Moon phases")
    assert [row["url"] for row in rows] == ["https://images.example/moon.jpg"]
    # Two attempts: first via proxy, second direct (explicit direct=).
    assert calls == [False, True]


def test_retry_is_bounded_on_persistent_failure(monkeypatch):
    calls = []

    def search(query, **options):
        calls.append(options.get("direct"))
        stats = [_failed_stat("bing"), _failed_stat("yandex"), _failed_stat("flickr")]
        return resolver.UniversalSearchResponse(query, 0, [], stats)

    _install_fake_scraper(monkeypatch, search)
    rows = resolver._discover_universal_urls("Moon phases")
    assert rows == []
    # Bounded: exactly the configured attempt count (2), then it gives up.
    assert calls == [False, True]
    assert resolver._universal_audit_snapshot()["settled_zero"] is True
    assert resolver._universal_audit_snapshot()["rescue_attempts"] == 0


def test_no_retry_on_genuine_no_results(monkeypatch):
    calls = []

    def search(query, **options):
        calls.append(options.get("direct"))
        stats = [_ok_stat("bing"), _ok_stat("yandex")]
        return resolver.UniversalSearchResponse(query, 0, [], stats)

    _install_fake_scraper(monkeypatch, search)
    rows = resolver._discover_universal_urls("Moon phases")
    assert rows == []
    # An engine answered (zero rows is a real answer): no retry, no rescue.
    assert calls == [False]
    assert resolver._universal_audit_snapshot()["settled_zero"] is False


def test_transient_detector_only_counts_all_failed(monkeypatch):
    all_failed = SimpleNamespace(stats=[_failed_stat("bing"), _failed_stat("yandex")])
    one_ok = SimpleNamespace(stats=[_ok_stat("bing"), _failed_stat("yandex")])
    no_stats = SimpleNamespace(stats=[])
    assert resolver._universal_result_is_transient_failure(all_failed) is True
    assert resolver._universal_result_is_transient_failure(one_ok) is False
    assert resolver._universal_result_is_transient_failure(no_stats) is False


def test_retry_bounds_are_env_tunable(monkeypatch):
    monkeypatch.setenv("VIDZEO_WEB_UNIVERSAL_RETRY_ATTEMPTS", "3")
    monkeypatch.setenv("VIDZEO_WEB_UNIVERSAL_RETRY_BACKOFF_SECONDS", "6")
    assert resolver._universal_retry_max_attempts() == 3
    assert resolver._universal_retry_backoff_seconds() == 6.0
    monkeypatch.setenv("VIDZEO_WEB_UNIVERSAL_RETRY_ATTEMPTS", "99")
    assert resolver._universal_retry_max_attempts() == 3
    monkeypatch.setenv("VIDZEO_WEB_UNIVERSAL_RETRY_ATTEMPTS", "0")
    assert resolver._universal_retry_max_attempts() == 1


def test_rescue_pass_retries_persistent_transient_failure(monkeypatch):
    calls = []

    def search(query, **options):
        calls.append(options.get("direct"))
        stats = [_failed_stat("bing"), _failed_stat("yandex"), _failed_stat("flickr")]
        return resolver.UniversalSearchResponse(query, 0, [], stats)

    _install_fake_scraper(monkeypatch, search, rescue_attempts="2")
    rows = resolver._discover_universal_urls("Moon phases")
    assert rows == []
    # 2 main attempts (proxy, direct) + 2 rescue attempts alternating
    # route (direct, proxy).
    assert calls == [False, True, True, False]
    audit = resolver._universal_audit_snapshot()
    assert audit["rescue_attempts"] == 2
    assert audit["settled_zero"] is True


def test_rescue_pass_returns_rows_when_route_recovers(monkeypatch):
    calls = []

    def search(query, **options):
        calls.append(options.get("direct"))
        if len(calls) < 4:
            stats = [_failed_stat("bing"), _failed_stat("yandex")]
            return resolver.UniversalSearchResponse(query, 0, [], stats)
        first = SimpleNamespace(image_url="https://images.example/moon.jpg")
        stats = [_ok_stat("bing"), _failed_stat("yandex")]
        return resolver.UniversalSearchResponse(query, 1, [first], stats)

    _install_fake_scraper(monkeypatch, search, rescue_attempts="3")
    rows = resolver._discover_universal_urls("Moon phases")
    assert [row["url"] for row in rows] == ["https://images.example/moon.jpg"]
    assert calls == [False, True, True, False]
    assert resolver._universal_audit_snapshot()["settled_zero"] is False


def test_rescue_never_fires_on_genuine_no_results(monkeypatch):
    calls = []

    def search(query, **options):
        calls.append(options.get("direct"))
        stats = [_ok_stat("bing"), _ok_stat("yandex")]
        return resolver.UniversalSearchResponse(query, 0, [], stats)

    _install_fake_scraper(monkeypatch, search, rescue_attempts="3")
    rows = resolver._discover_universal_urls("Moon phases")
    assert rows == []
    assert calls == [False]
    assert resolver._universal_audit_snapshot()["rescue_attempts"] == 0


class _RecordingClient:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(status_code=200, text="<html></html>")


def test_throttled_get_bypasses_proxy_inside_bypass_context(monkeypatch):
    monkeypatch.setattr(
        "services.web_image.universal_scraper._universal_retry_count", lambda: 0
    )
    client = _RecordingClient()
    _throttled_get(client, "https://www.bing.com/images/search?text=moon",
                   headers={}, timeout=5)
    assert client.calls[0].get("proxies") is None
    client.calls.clear()
    with web_bypass_proxy():
        _throttled_get(client, "https://www.bing.com/images/search?text=moon",
                       headers={}, timeout=5)
    assert client.calls[0].get("proxies") == {"http": None, "https": None}
    # The shim flag restores after the context.
    assert _web_bypass_proxy_enabled() is False


def test_throttled_get_direct_param_reaches_pool_thread(monkeypatch):
    """Regression test for the 4445f63 no-op: the explicit direct= flag
    must arrive at _throttled_get as proxies={http: None, https: None}
    on the worker thread that issues the request."""
    monkeypatch.setattr(
        "services.web_image.universal_scraper._universal_retry_count", lambda: 0
    )
    client = _RecordingClient()

    def work(direct):
        _throttled_get(client, "https://www.bing.com/images/search?text=moon",
                       headers={}, timeout=5, direct=direct)
        return client.calls[-1].get("proxies")

    with ThreadPoolExecutor(max_workers=2) as ex:
        proxy_kwargs = ex.submit(work, False).result()
        direct_kwargs = ex.submit(work, True).result()
    assert proxy_kwargs is None
    assert direct_kwargs == {"http": None, "https": None}


class _RouteProbeClient:
    """Fails proxied calls, answers direct ones — proves per-engine route fallback."""

    def __init__(self):
        self.routes = []

    def get(self, url, **kwargs):
        proxies = kwargs.get("proxies")
        self.routes.append("direct" if proxies else "proxy")
        if not proxies:
            raise requests.exceptions.ReadTimeout("proxied route timed out")
        return SimpleNamespace(status_code=200, text="<html></html>")


class _ProbeEngine(_Engine):
    name = "probe"

    def __init__(self, client):
        self._client = client
        self._local = threading.local()

    def _get_client(self):
        return self._client


def test_engine_fetch_falls_back_to_direct_on_proxy_timeout(monkeypatch):
    monkeypatch.setattr(
        "services.web_image.universal_scraper._universal_retry_count", lambda: 0
    )
    client = _RouteProbeClient()
    engine = _ProbeEngine(client)
    response, route = engine._fetch(
        "https://www.bing.com/images/search?text=moon",
        headers={}, timeout=5, direct=False,
    )
    assert response.status_code == 200
    assert route == "direct"
    assert client.routes == ["proxy", "direct"]


def test_dead_route_is_skipped_fast_then_reprobed():
    engine = "probe-skip"
    for _ in range(3):
        _note_route_result(engine, "proxy", False)
    assert _route_order(engine, prefer_direct=False) == ["direct"]
    assert _route_order(engine, prefer_direct=True) == ["direct"]
    _note_route_result(engine, "proxy", True)
    assert _route_order(engine, prefer_direct=False) == ["proxy", "direct"]


def test_manager_search_passes_direct_to_pool_threads():
    """Regression test: a real UniversalScraperManager with stubbed
    engines must deliver direct=True to engine.search() on its pool
    threads (not the caller thread)."""
    manager = UniversalScraperManager(max_workers=2, engine_timeout_seconds=5)
    seen = []

    class StubEngine:
        name = "stub"

        def search(self, query, limit, page, safe_search, direct=False):
            seen.append((direct, threading.current_thread().name))
            return [UniversalImageResult(
                title="stub", image_url="https://images.example/stub.jpg",
                source_engine="stub",
            )]

    manager._engines = {"stub": StubEngine()}
    try:
        resp = manager.search("moon", engine_ids=["stub"], direct=True)
    finally:
        manager._executor.shutdown(wait=True)
    assert resp.total == 1
    assert len(seen) == 1
    assert seen[0][0] is True
    assert "universal-scraper" in seen[0][1]


# ── Worker F (Nov 18): trust_env=False direct session ─────────────────
# The proxies={http: None, https: None} kwarg is NOT the bypass for a real
# Session — on requests 2.34.2 it only works by a setdefault-before-strip
# ordering accident in merge_environment_settings. The direct route must
# therefore go through a Session with trust_env=False, which never reads
# the proxy env vars at all. These tests pin the session wiring, not the
# kwarg: if a future change hands the direct route a trust_env=True
# session again, the wire path silently goes through the (dead) proxy.


@pytest.mark.parametrize(
    "engine_cls",
    [YandexEngine, BingEngine, BaiduEngine, OpenverseEngine, FlickrEngine],
)
def test_direct_route_session_is_trust_env_false(engine_cls):
    """Each HTTP engine keeps separate per-route sessions: proxy honours
    the env proxy (trust_env=True), direct never reads it (trust_env=False)."""
    engine = engine_cls()
    proxy = engine._get_client()
    direct = engine._get_client(direct=True)
    assert isinstance(proxy, requests.Session)
    assert isinstance(direct, requests.Session)
    assert proxy is not direct
    assert proxy.trust_env is True
    assert direct.trust_env is False
    assert direct.proxies == {}
    # Cached per route: repeat calls return the same session object.
    assert engine._get_client() is proxy
    assert engine._get_client(direct=True) is direct


def test_new_route_session_direct_never_reads_env_proxy():
    session = _new_route_session(direct=True)
    assert session.trust_env is False
    assert session.proxies == {}
    assert _new_route_session(direct=False).trust_env is True


def test_fetch_issues_each_route_on_its_own_session(monkeypatch):
    """_fetch must hand the proxy attempt a trust_env=True session and the
    direct fallback attempt a trust_env=False session — the wire path must
    match the route label, not just the proxies kwarg."""
    from services.web_image import universal_scraper as scraper

    seen = []

    def _fake_throttled_get(client, url, *, headers, timeout, direct=False):
        seen.append((client.trust_env, direct))
        if not direct:
            raise requests.exceptions.ReadTimeout("proxied route timed out")
        return SimpleNamespace(status_code=200, text="<html></html>")

    monkeypatch.setattr(scraper, "_throttled_get", _fake_throttled_get)

    class _Probe(_Engine):
        name = "workerf-route-probe"

    engine = _Probe()
    response, route = engine._fetch(
        "https://www.bing.com/images/search?text=moon",
        headers={}, timeout=5, direct=False,
    )
    assert response.status_code == 200
    assert route == "direct"
    assert seen == [(True, False), (False, True)]
