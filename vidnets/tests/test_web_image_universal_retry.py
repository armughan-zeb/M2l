"""Bounded retry for the universal (four-engine) web image lane.

Nov 18 (Moon 20s test): every engine failed transiently through the proxy
(Flickr/Bing/Yandex ReadTimeout, DuckDuckGo bot-gated) and all 5 scenes died
with 0 candidates — the timed-out queries burned the stage deadline before
query 2 could run. The fix retries the same query once (2 attempts total):
attempt 1 via the egress proxy, attempt 2 direct (proxy bypass). A genuine
no-results (an engine answered with zero rows) is never retried, and a
persistent failure is bounded to the configured attempt count.
"""
from types import SimpleNamespace

import pytest

from services.web_image import resolver
from services.web_image.universal_scraper import (
    _throttled_get,
    _web_bypass_proxy_enabled,
    web_bypass_proxy,
)


def _failed_stat(engine, error="ReadTimeout"):
    return SimpleNamespace(
        engine=engine, count=0, duration_ms=15000, success=False, error=error
    )


def _ok_stat(engine):
    return SimpleNamespace(
        engine=engine, count=0, duration_ms=1000, success=True, error=None
    )


def _install_fake_scraper(monkeypatch, search_fn):
    """Install a fake _UNIVERSAL_SCRAPER + the small resolver collaborators."""
    monkeypatch.delenv("VIDZEO_UNIVERSAL_SCRAPER_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("VIDZEO_UNIVERSAL_LATE_WAIT_SECONDS", "0")
    monkeypatch.setenv("VIDZEO_WEB_UNIVERSAL_RETRY_BACKOFF_SECONDS", "0")
    monkeypatch.setattr(
        resolver,
        "_UNIVERSAL_SCRAPER",
        SimpleNamespace(search=search_fn, collect_late_results=lambda _q, **_: []),
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
        calls.append(_web_bypass_proxy_enabled())
        if len(calls) == 1:
            stats = [_failed_stat("bing"), _failed_stat("yandex"), _failed_stat("flickr")]
            return resolver.UniversalSearchResponse(query, 0, [], stats)
        first = SimpleNamespace(image_url="https://images.example/moon.jpg")
        stats = [_ok_stat("bing"), _failed_stat("yandex"), _failed_stat("flickr")]
        return resolver.UniversalSearchResponse(query, 1, [first], stats)

    _install_fake_scraper(monkeypatch, search)
    rows = resolver._discover_universal_urls("Moon phases")
    assert [row["url"] for row in rows] == ["https://images.example/moon.jpg"]
    # Two attempts: first via proxy, second direct (bypass).
    assert calls == [False, True]


def test_retry_is_bounded_on_persistent_failure(monkeypatch):
    calls = []

    def search(query, **options):
        calls.append(_web_bypass_proxy_enabled())
        stats = [_failed_stat("bing"), _failed_stat("yandex"), _failed_stat("flickr")]
        return resolver.UniversalSearchResponse(query, 0, [], stats)

    _install_fake_scraper(monkeypatch, search)
    rows = resolver._discover_universal_urls("Moon phases")
    assert rows == []
    # Bounded: exactly the configured attempt count (2), then it gives up.
    assert calls == [False, True]


def test_no_retry_on_genuine_no_results(monkeypatch):
    calls = []

    def search(query, **options):
        calls.append(_web_bypass_proxy_enabled())
        stats = [_ok_stat("bing"), _ok_stat("yandex")]
        return resolver.UniversalSearchResponse(query, 0, [], stats)

    _install_fake_scraper(monkeypatch, search)
    rows = resolver._discover_universal_urls("Moon phases")
    assert rows == []
    # An engine answered (zero rows is a real answer): no retry.
    assert calls == [False]


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
    # The flag is thread-local and restores after the context.
    assert _web_bypass_proxy_enabled() is False
