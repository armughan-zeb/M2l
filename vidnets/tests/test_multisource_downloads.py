import asyncio
import http.server
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))


def test_limits_span_independent_scene_event_loops():
    from services.web_image.hybrid_search import DownloadLimiter
    limiter = DownloadLimiter(total=3, per_host=2)
    peaks = []

    async def scene():
        async def download(host):
            async with limiter.slot(host, time.monotonic() + 5):
                with limiter.lock:
                    peaks.append((limiter.active, max(limiter.hosts.values())))
                await asyncio.sleep(0.02)
        await asyncio.gather(*(download(str(i % 2)) for i in range(8)))

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(asyncio.run, scene()) for _ in range(4)]
        for future in futures:
            future.result()
    assert len(peaks) == 32
    assert max(p[0] for p in peaks) <= 3
    assert max(p[1] for p in peaks) <= 2
    assert limiter.active == 0


def test_host_cooldown_does_not_block_other_sources():
    from services.web_image.hybrid_search import DownloadLimiter
    limiter = DownloadLimiter()
    limiter.defer("blocked")

    async def probe():
        try:
            async with limiter.slot("blocked", time.monotonic() + 1):
                raise AssertionError("blocked host acquired a slot")
        except RuntimeError as exc:
            assert "429" in str(exc)
        async with limiter.slot("healthy", time.monotonic() + 1):
            assert limiter.active == 1
    asyncio.run(probe())
    assert limiter.active == 0


def test_shortlist_keeps_alternative_hosts():
    from services.web_image.resolver import _diverse_download_candidates
    rows = [(10 - i / 10, {"url": f"https://first.test/{i}"}) for i in range(20)]
    rows.append((5, {"url": "https://second.test/image"}))
    selected = _diverse_download_candidates(rows, 8)
    assert len(selected) == 8
    assert selected[0] == rows[0]
    assert rows[-1] in selected


def test_shortlist_prefers_already_approved_host_within_slack():
    # A near-equal candidate from a host the pipeline already fetches from
    # (live.staticflickr.com is in the default preferred set) must win over
    # a slightly better candidate from an unseen host: each unseen host
    # costs a per-host network approval card and stalls media search.
    from services.web_image.resolver import _diverse_download_candidates
    rows = [(10 - i / 10, {"url": f"https://new.test/{i}"}) for i in range(20)]
    preferred_row = (9.7, {"url": "https://live.staticflickr.com/5685/photo_b.jpg"})
    rows.append(preferred_row)
    selected = _diverse_download_candidates(rows, 4)
    assert len(selected) == 4
    assert selected[0] == preferred_row
    assert preferred_row in selected


def test_shortlist_preferred_host_does_not_override_relevance_gap():
    from services.web_image.resolver import _diverse_download_candidates
    rows = [(10 - i / 10, {"url": f"https://new.test/{i}"}) for i in range(20)]
    weak_preferred = (5, {"url": "https://live.staticflickr.com/5685/photo_b.jpg"})
    rows.append(weak_preferred)
    selected = _diverse_download_candidates(rows, 4)
    # It may only appear as the single alternate-host reserve in last place,
    # never ahead of the relevant rows.
    assert selected[0] == rows[0]
    assert weak_preferred not in selected[:3]


def test_preferred_hosts_env_override(monkeypatch):
    from services.web_image import resolver
    monkeypatch.setenv("VIDZEO_PREFERRED_IMAGE_HOSTS", "custom.test, other.test")
    assert resolver._preferred_image_hosts() == {"custom.test", "other.test"}
    rows = [(10, {"url": "https://new.test/1"}), (9.7, {"url": "https://custom.test/1"})]
    selected = resolver._diverse_download_candidates(rows, 1)
    assert selected[0] == rows[1]
    # An empty override disables the preference: best score wins again.
    monkeypatch.setenv("VIDZEO_PREFERRED_IMAGE_HOSTS", "")
    assert resolver._preferred_image_hosts() == set()
    selected = resolver._diverse_download_candidates(rows, 1)
    assert selected[0] == rows[0]


def test_yandex_robot_check_is_reported_and_other_sources_remain_enabled():
    from services.sources.yandex_source import YandexSource
    from services.web_image.resolver import _enabled_fast_sources
    source = YandexSource()
    source.session.get = Mock(return_value=Mock(status_code=200, text='<title>Are you not a robot?</title>'))
    assert source.search("Royal Family") == []
    assert not source.is_available()
    # A module-level print stub from another test file may be active during
    # collection, so assert the durable circuit state rather than stdout.
    assert source._circuit_open is True
    assert source._robot_failure_count >= 1
    assert {name for name, kind in _enabled_fast_sources()} >= {"bing_hybrid", "flickr_api", "wikimedia"}
    # Openverse is no longer a default lane (api.openverse.org 504s through
    # the egress proxy); the robot check must still leave the other lanes up.


def test_yandex_challenge_trips_resolver_job_breaker(monkeypatch):
    from services.web_image import resolver
    from services.web_image.source_broker import SourceHealth

    class ChallengedYandex:
        _circuit_open = True

        def search(self, _query, limit=20):
            return []

    health = SourceHealth(cooldown_seconds=30)
    monkeypatch.setattr(resolver, "_YANDEX_SOURCE", ChallengedYandex())
    monkeypatch.setattr(resolver, "_ACTIVE_PROVIDER_HEALTH", health)

    assert resolver._discover_api_urls("yandex", "royal family") == []
    assert not health.healthy("yandex")


def test_failed_original_uses_provider_preview(monkeypatch, tmp_path):
    from services.web_image import host_allowlist, resolver
    path = tmp_path / "preview.jpg"
    path.write_bytes(b"preview fixture")
    calls = []
    def download(urls, *args, **kwargs):
        calls.append(urls)
        return [(u, path if "preview" in u else None, "" if "preview" in u else "HTTP 400") for u in urls]
    monkeypatch.setattr(resolver, "download_bulk", download)
    # Preview rescue is gated to already-approved hosts: the preview host
    # must be in the seen-host store, or the rescue would cost a fresh
    # per-host approval card for a low-res thumbnail.
    store_path = tmp_path / "seen_hosts.json"
    monkeypatch.setenv("VIDZEO_SEEN_HOSTS_PATH", str(store_path))
    host_allowlist.record_hosts(["provider.test"], source="test", path=store_path)
    items = [{"url": "https://publisher.test/original", "preview_url": "https://provider.test/preview"}]
    result = resolver._download_candidate_batch(items, tmp_path, 3, 10)
    assert result == [(items[0]["url"], path, "")]
    assert len(calls) == 2
    assert items[0]["download_url_used"] == items[0]["preview_url"]


def test_failed_original_skips_preview_on_unseen_host(monkeypatch, tmp_path):
    from services.web_image import resolver
    path = tmp_path / "preview.jpg"
    path.write_bytes(b"preview fixture")
    calls = []
    def download(urls, *args, **kwargs):
        calls.append(urls)
        return [(u, path if "preview" in u else None, "" if "preview" in u else "HTTP 400") for u in urls]
    monkeypatch.setattr(resolver, "download_bulk", download)
    # Empty seen-host store: the unseen preview host must NOT be fetched.
    monkeypatch.setenv("VIDZEO_SEEN_HOSTS_PATH", str(tmp_path / "seen_hosts.json"))
    items = [{"url": "https://publisher.test/original", "preview_url": "https://provider.test/preview"}]
    result = resolver._download_candidate_batch(items, tmp_path, 3, 10)
    assert result == [(items[0]["url"], None, "HTTP 400")]
    assert len(calls) == 1


def test_download_bulk_enforces_byte_cap_before_buffering(tmp_path):
    """Oversized image responses must fail without creating a local artifact."""
    from services.web_image.hybrid_search import download_bulk

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = b"x" * 4096
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/large.jpg"
        result = download_bulk([url], tmp_path, max_concurrent=1, timeout_seconds=5, max_bytes=1024)
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert result[0][1] is None
    assert "too large" in result[0][2]
    assert not list(tmp_path.iterdir())


def test_denied_origin_opens_circuit_without_blocking_healthy_download(tmp_path, monkeypatch):
    from services.web_image import hybrid_search

    hits = [0, 0]
    body = b"fixture image" * 1024

    class Denied(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits[0] += 1
            self.send_response(403)
            self.end_headers()

        def log_message(self, *_args):
            pass

    class Healthy(Denied):
        def do_GET(self):
            hits[1] += 1
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    limiter = hybrid_search.DownloadLimiter(total=2, per_host=1)
    monkeypatch.setattr(hybrid_search, "_DOWNLOAD_LIMITER", limiter)
    servers = [http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler) for handler in (Denied, Healthy)]
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in servers]
    for thread in threads:
        thread.start()
    blocked, healthy = [f"http://127.0.0.1:{server.server_port}" for server in servers]
    urls = [f"{blocked}/{index}.jpg" for index in range(5)] + [f"{healthy}/valid.jpg"]
    try:
        rows = hybrid_search.download_bulk(urls, tmp_path, max_concurrent=1, timeout_seconds=2)
        assert hits == [3, 1]
        assert [row[2] for row in rows[:3]] == ["HTTP 403"] * 3
        assert all("circuit open" in row[2] for row in rows[3:5])
        assert rows[5][1] is not None, rows[5][2]
        assert rows[5][1].read_bytes() == body
        host = blocked.split("://")[1]
        limiter.cooldowns[host] = time.monotonic() - 1
        recovered = hybrid_search.download_bulk(urls[:2], tmp_path, max_concurrent=2, timeout_seconds=2)
        assert hits == [4, 1]
        assert recovered[0][2] == "HTTP 403"
        assert "circuit open" in recovered[1][2]
    finally:
        for server, thread in zip(servers, threads):
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()
