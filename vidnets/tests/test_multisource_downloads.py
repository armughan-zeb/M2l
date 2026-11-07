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


def test_yandex_robot_check_is_reported_and_other_sources_remain_enabled(capsys):
    from services.sources.yandex_source import YandexSource
    from services.web_image.resolver import _enabled_fast_sources
    source = YandexSource()
    source.session.get = Mock(return_value=Mock(status_code=200, text='<title>Are you not a robot?</title>'))
    assert source.search("Royal Family") == []
    assert not source.is_available()
    assert "Robot check" in capsys.readouterr().out
    assert {"bing_hybrid", "flickr_api", "openverse", "wikimedia"} <= {name for name, kind in _enabled_fast_sources()}


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
    from services.web_image import resolver
    path = tmp_path / "preview.jpg"
    path.write_bytes(b"preview fixture")
    calls = []
    def download(urls, *args, **kwargs):
        calls.append(urls)
        return [(u, path if "preview" in u else None, "" if "preview" in u else "HTTP 400") for u in urls]
    monkeypatch.setattr(resolver, "download_bulk", download)
    items = [{"url": "https://publisher.test/original", "preview_url": "https://provider.test/preview"}]
    result = resolver._download_candidate_batch(items, tmp_path, 3, 10)
    assert result == [(items[0]["url"], path, "")]
    assert len(calls) == 2
    assert items[0]["download_url_used"] == items[0]["preview_url"]


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
