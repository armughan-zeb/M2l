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


def test_open_circuit_blocks_on_first_403():
    """A 403 is a refusal: the circuit opens on the first one, not the third.

    The failure-count path still needs 3 failures for timeouts, but a host
    that answers 403 has already refused — waiting for 2 more refusals
    burns candidate attempts against a dead host (Broken Rail: 18
    circuit-open failures on one host after its 403s).
    """
    from services.web_image.hybrid_search import DownloadLimiter
    limiter = DownloadLimiter(total=3, per_host=2)
    limiter.open_circuit("dead.test")

    async def probe():
        try:
            async with limiter.slot("dead.test", time.monotonic() + 1):
                raise AssertionError("circuit did not open on first 403")
        except RuntimeError as exc:
            assert "circuit open" in str(exc)
        async with limiter.slot("healthy.test", time.monotonic() + 1):
            assert limiter.active == 1
    asyncio.run(probe())
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
    # Muse-search (Nov 22, test-30 fix): wikimedia is folded into the
    # muse_search wave, so the lane that must stay up after a Yandex
    # robot check is muse_search, not the standalone wikimedia lane.
    assert {name for name, kind in _enabled_fast_sources()} >= {"bing_hybrid", "flickr_api", "muse_search"}
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


def test_download_bulk_retries_transient_failure_then_falls_back(tmp_path):
    """A network blip retries instead of failing once; a host that keeps
    failing returns an error row (the caller moves to the next candidate)
    instead of raising and blocking the job."""
    from services.web_image.hybrid_search import download_bulk

    body = b"fixture image" * 1024
    hits = {"flaky": 0, "dead": 0}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path.startswith("/flaky"):
                hits["flaky"] += 1
                if hits["flaky"] == 1:
                    self.send_response(500)
                    self.end_headers()
                    return
            else:
                hits["dead"] += 1
                self.send_response(500)
                self.end_headers()
                return
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
        rows = download_bulk(
            [
                f"http://127.0.0.1:{server.server_port}/flaky.jpg",
                f"http://127.0.0.1:{server.server_port}/dead.jpg",
            ],
            tmp_path, max_concurrent=1, timeout_seconds=5,
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert hits["flaky"] == 2, "the transient 500 must be retried once"
    assert rows[0][1] is not None
    assert rows[0][1].read_bytes() == body
    assert hits["dead"] == 2, "a dead host is retried, then gives up"
    assert rows[1][1] is None
    assert "HTTP 500" in rows[1][2]


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


def test_timeouts_do_not_open_host_circuit(tmp_path, monkeypatch):
    """Timeouts are shared slow egress, not a host refusal (test 23).

    Three slow hosts timing out must NOT open the circuit: in test 23
    50 timeouts across every host at once tripped the 3-failure circuit
    and 56 further attempts then died instantly on it, while the
    circuit's refusals (403 opens at once, 429 defers 30s) are untouched.
    A fast URL on the same host must still download afterwards.
    """
    from services.web_image import hybrid_search

    body = b"fixture image" * 1024

    class Slow(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/fast"):
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            # Sleep longer than the per-attempt timeout so every attempt
            # times out; the batch wall (3x the timeout) still fits three
            # parallel URLs x two attempts.
            time.sleep(8)

        def log_message(self, *_args):
            pass

    limiter = hybrid_search.DownloadLimiter(total=4, per_host=3)
    monkeypatch.setattr(hybrid_search, "_DOWNLOAD_LIMITER", limiter)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        host = f"127.0.0.1:{server.server_port}"
        rows = hybrid_search.download_bulk(
            [f"{base}/slow{i}.jpg" for i in range(3)],
            tmp_path, max_concurrent=3, timeout_seconds=3,
        )
        assert all(row[1] is None and row[2] == "timeout" for row in rows), rows
        assert limiter.cooldowns.get(host, 0) <= time.monotonic(), (
            "timeouts must not open the host circuit"
        )
        fast = hybrid_search.download_bulk(
            [f"{base}/fast.jpg"], tmp_path, max_concurrent=1, timeout_seconds=5,
        )
        assert fast[0][1] is not None, fast[0][2]
        assert fast[0][1].read_bytes() == body
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_slow_first_attempt_retries_with_fresh_budget(tmp_path, monkeypatch):
    """The retry must get a full budget, not the first attempt's remainder.

    Old code computed one deadline per URL, so a first attempt that timed
    out left ~0s for the retry (and the retry gate broke out before it
    even ran).  A host that answers on the second try must now succeed.
    """
    from services.web_image import hybrid_search

    body = b"fixture image" * 1024
    hits = {"n": 0}
    hits_lock = threading.Lock()

    class SlowFirst(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            with hits_lock:
                hits["n"] += 1
                first = hits["n"] == 1
            if first:
                time.sleep(2)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *_args):
            pass

    limiter = hybrid_search.DownloadLimiter(total=4, per_host=3)
    monkeypatch.setattr(hybrid_search, "_DOWNLOAD_LIMITER", limiter)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), SlowFirst)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/slow.jpg"
        rows = hybrid_search.download_bulk(
            [url], tmp_path, max_concurrent=1, timeout_seconds=1,
        )
        assert hits["n"] == 2, "the retry must actually run"
        assert rows[0][1] is not None, rows[0][2]
        assert rows[0][1].read_bytes() == body
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_web_image_download_timeout_default_is_120s(monkeypatch):
    """The per-image download default is 120s (round 8, test 35: the 60s
    budget was censored at the line — median transfer 61.0s, 25/26 chunks
    landed 0; the slow-hour bracket is ~60-120s); fast mode is 60s (the
    old non-fast default); the env override and the 3-300s clamp still
    hold. (Was 60s/30s, clamp 3-120s, before round 8.)"""
    from services.web_image import resolver

    monkeypatch.delenv("VIDZEO_WEB_IMAGE_DOWNLOAD_TIMEOUT_SECONDS", raising=False)
    assert resolver._web_image_download_timeout_seconds(False) == 120.0
    assert resolver._web_image_download_timeout_seconds(True) == 60.0
    monkeypatch.setenv("VIDZEO_WEB_IMAGE_DOWNLOAD_TIMEOUT_SECONDS", "12")
    assert resolver._web_image_download_timeout_seconds(False) == 12.0
    monkeypatch.setenv("VIDZEO_WEB_IMAGE_DOWNLOAD_TIMEOUT_SECONDS", "100")
    assert resolver._web_image_download_timeout_seconds(False) == 100.0
    monkeypatch.setenv("VIDZEO_WEB_IMAGE_DOWNLOAD_TIMEOUT_SECONDS", "200")
    assert resolver._web_image_download_timeout_seconds(False) == 200.0
    monkeypatch.setenv("VIDZEO_WEB_IMAGE_DOWNLOAD_TIMEOUT_SECONDS", "400")
    assert resolver._web_image_download_timeout_seconds(False) == 300.0
    monkeypatch.setenv("VIDZEO_WEB_IMAGE_DOWNLOAD_TIMEOUT_SECONDS", "1")
    assert resolver._web_image_download_timeout_seconds(False) == 3.0
    monkeypatch.setenv("VIDZEO_WEB_IMAGE_DOWNLOAD_TIMEOUT_SECONDS", "garbage")
    assert resolver._web_image_download_timeout_seconds(False) == 120.0


def test_download_batch_wall_seconds_scales_with_timeout(monkeypatch):
    """Test 25: the 90s batch wall was the binding killer, not the 60s
    per-URL timeout. A scene batch holds up to 12 URLs through 3/host
    slots at 60s/attempt: the old min(90, timeout*3) wall let ~4 URLs
    attempt and truncated every retry to ~30s. The new 180s cap lets
    ~9 attempt with a full retry budget. Fast mode (8s) still gets its
    24s wall and a 30s timeout still gets 90s — only timeouts above
    30s see a longer wall."""
    from services.web_image import hybrid_search

    monkeypatch.delenv("VIDZEO_WEB_DOWNLOAD_BATCH_WALL_SECONDS", raising=False)
    assert hybrid_search._download_batch_wall_seconds(60.0) == 180.0
    assert hybrid_search._download_batch_wall_seconds(30.0) == 90.0
    assert hybrid_search._download_batch_wall_seconds(8.0) == 24.0
    monkeypatch.setenv("VIDZEO_WEB_DOWNLOAD_BATCH_WALL_SECONDS", "120")
    assert hybrid_search._download_batch_wall_seconds(60.0) == 120.0
    monkeypatch.setenv("VIDZEO_WEB_DOWNLOAD_BATCH_WALL_SECONDS", "9999")
    assert hybrid_search._download_batch_wall_seconds(60.0) == 180.0
    monkeypatch.setenv("VIDZEO_WEB_DOWNLOAD_BATCH_WALL_SECONDS", "10")
    assert hybrid_search._download_batch_wall_seconds(60.0) == 30.0
    monkeypatch.setenv("VIDZEO_WEB_DOWNLOAD_BATCH_WALL_SECONDS", "garbage")
    assert hybrid_search._download_batch_wall_seconds(60.0) == 180.0


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
        # First 403 opens the host circuit immediately: exactly one request
        # reaches the denying host, the rest fail fast on the open circuit.
        assert hits == [1, 1]
        assert rows[0][2] == "HTTP 403"
        assert all("circuit open" in row[2] for row in rows[1:5])
        assert rows[5][1] is not None, rows[5][2]
        assert rows[5][1].read_bytes() == body
        host = blocked.split("://")[1]
        limiter.cooldowns[host] = time.monotonic() - 1
        recovered = hybrid_search.download_bulk(urls[:2], tmp_path, max_concurrent=2, timeout_seconds=2)
        assert hits == [2, 1]
        assert recovered[0][2] == "HTTP 403"
        assert "circuit open" in recovered[1][2]
    finally:
        for server, thread in zip(servers, threads):
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()


def test_download_transfer_gets_fresh_budget_after_slot_wait(tmp_path, monkeypatch):
    """A download that waits most of its budget for a limiter slot must
    still get a full transfer budget once the slot is held (test 25).

    With 8 scene workers sharing 12 download slots, most attempts spent
    ~55s of their 60s budget waiting for a slot and then got ~5s to
    transfer an image that needed ~8s: 40/86 attempts died as "timeout"
    with zero images landed, although the bytes were available. The wait
    is bounded by the attempt deadline; the transfer now gets a fresh
    timeout_seconds of its own, capped only by the batch wall.
    """
    from services.web_image.hybrid_search import _DOWNLOAD_LIMITER, download_bulk

    monkeypatch.setenv("VIDZEO_NET_RETRIES", "1")  # one attempt: no retry mask
    body = b"fixture image" * 2048
    chunks, delay = 10, 0.2  # ~2s transfer

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            step = len(body) // chunks
            for i in range(chunks):
                chunk = body[i * step:(i + 1) * step] if i < chunks - 1 else body[i * step:]
                self.wfile.write(chunk)
                self.wfile.flush()
                time.sleep(delay)

        def log_message(self, *_args):
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host = f"127.0.0.1:{server.server_port}"
    acquired = threading.Event()
    held = []
    held_lock = threading.Lock()

    def hold_slots():
        async def _hold():
            async with _DOWNLOAD_LIMITER.slot(host, time.monotonic() + 30):
                with held_lock:
                    held.append(1)
                    if len(held) == 3:
                        acquired.set()
                await asyncio.sleep(9.0)

        async def _hold_all():
            await asyncio.gather(*[_hold() for _ in range(3)])

        asyncio.run(_hold_all())

    holder = threading.Thread(target=hold_slots, daemon=True)
    holder.start()
    try:
        assert acquired.wait(timeout=10), "slot holders never acquired"
        rows = download_bulk(
            [f"http://{host}/slow.jpg"], tmp_path,
            max_concurrent=1, timeout_seconds=10,
        )
    finally:
        holder.join(timeout=15)
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert rows[0][1] is not None, f"download failed: {rows[0][2]}"
    assert rows[0][1].read_bytes() == body


def test_download_transfer_still_bounded_by_batch_wall(tmp_path, monkeypatch):
    """The fresh transfer budget must not let a slow host hold the batch
    past its wall: with a 30s batch wall (the clamp floor), a 40s drip
    still fails fast instead of running to completion."""
    from services.web_image.hybrid_search import download_bulk

    monkeypatch.setenv("VIDZEO_NET_RETRIES", "1")
    monkeypatch.setenv("VIDZEO_WEB_DOWNLOAD_BATCH_WALL_SECONDS", "30")
    body = b"fixture image" * 2048
    chunks, delay = 10, 4.0  # ~40s transfer, past the 30s wall

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            step = len(body) // chunks
            for i in range(chunks):
                chunk = body[i * step:(i + 1) * step] if i < chunks - 1 else body[i * step:]
                self.wfile.write(chunk)
                self.wfile.flush()
                time.sleep(delay)

        def log_message(self, *_args):
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        started = time.monotonic()
        rows = download_bulk(
            [f"http://127.0.0.1:{server.server_port}/slow.jpg"], tmp_path,
            max_concurrent=1, timeout_seconds=60,
        )
        elapsed = time.monotonic() - started
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert rows[0][1] is None
    assert elapsed < 40, f"transfer ran {elapsed:.1f}s, past the 30s batch wall"
