import time
import json
import subprocess
from types import SimpleNamespace

from services.web_image.universal_scraper import (
    UniversalImageResult,
    UniversalScraperManager,
    universal_result_to_candidate,
)


def test_native_image_search_timeout_terminates_process(monkeypatch):
    from services.web_image import universal_scraper as scraper
    processes = []
    class Process:
        def __init__(self, *args, **kwargs):
            self.killed = False
            processes.append(self)
        def communicate(self, *args, **kwargs):
            if not self.killed:
                raise subprocess.TimeoutExpired('native-search', 4)
            return ('[]', '')
        def poll(self):
            return 1 if self.killed else None
        def kill(self):
            self.killed = True
    monkeypatch.setattr(scraper, 'DDGS', object())
    monkeypatch.setattr(scraper.subprocess, 'Popen', Process)
    assert scraper.DuckDuckGoEngine().search('NFL', 3, 1, True) == []
    assert processes and all(process.killed for process in processes)


def test_native_image_search_preserves_metadata_and_pagination(monkeypatch):
    from services.web_image import universal_scraper as scraper
    captured = []
    class Process:
        def __init__(self, *args, **kwargs):
            pass
        def communicate(self, request=None, **kwargs):
            if request:
                captured.append(json.loads(request))
            return (json.dumps([{'image': 'https://example.com/first.jpg'},
                {'image': 'https://example.com/second.jpg', 'title': 'NFL field',
                 'width': 1920, 'height': 1080, 'url': 'https://example.com/article'}]), '')
        def poll(self):
            return 0
    monkeypatch.setattr(scraper, 'DDGS', object())
    monkeypatch.setattr(scraper.subprocess, 'Popen', Process)
    rows = scraper.DuckDuckGoEngine().search('NFL', 1, 2, True)
    assert len(rows) == 1 and rows[0].image_url.endswith('second.jpg')
    assert rows[0].width == 1920 and rows[0].source_url.endswith('article')
    assert captured[0]['kind'] == 'images' and captured[0]['limit'] == 2


def test_native_tls_bing_yandex_live_in_child_processes(monkeypatch):
    from services.web_image import universal_scraper as scraper
    captured = []
    class Process:
        def __init__(self, *args, **kwargs):
            pass
        def communicate(self, request=None, **kwargs):
            if request:
                captured.append(json.loads(request))
            return ('{"status_code":200,"text":"search markup"}', '')
        def poll(self):
            return 0
    monkeypatch.setattr(scraper, 'primp', SimpleNamespace(Client=lambda: (_ for _ in ()).throw(AssertionError('Native TLS initialized in API'))))
    monkeypatch.setattr(scraper.subprocess, 'Popen', Process)
    for engine, url in ((scraper.BingEngine(), 'https://www.bing.com/images/async'), (scraper.YandexEngine(), 'https://yandex.com/images/search')):
        response = engine._get_client().get(url, headers={}, timeout=6)
        assert response.status_code == 200
    assert len(captured) == 2 and all(row['kind'] == 'http' for row in captured)


class _FakeEngine:
    def __init__(self, name, delay=0.0, rows=1, fail=False):
        self.name = name
        self.delay = delay
        self.rows = rows
        self.fail = fail

    def search(self, query, limit, page, safe_search):
        del limit, page, safe_search
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise RuntimeError(f"{self.name} unavailable")
        return [
            UniversalImageResult(
                title=f"{self.name} {query} {idx}",
                image_url=f"https://{self.name}.example/{idx}.jpg",
                source_engine=self.name,
            )
            for idx in range(self.rows)
        ]


def test_manager_fans_out_and_interleaves_results():
    manager = UniversalScraperManager(max_workers=3, engine_timeout_seconds=2)
    manager._engines = {
        "one": _FakeEngine("one", delay=0.12, rows=2),
        "two": _FakeEngine("two", delay=0.12, rows=2),
        "three": _FakeEngine("three", delay=0.12, rows=2),
    }
    started = time.perf_counter()
    response = manager.search("Apollo 11", engine_ids=["one", "two", "three"], limit_per_engine=2)
    elapsed = time.perf_counter() - started
    assert elapsed < 0.30
    assert response.total == 6
    assert [row.source_engine for row in response.results[:3]] == ["one", "two", "three"]
    assert all(stat.success for stat in response.stats)


def test_manager_keeps_partial_success_when_one_engine_fails():
    manager = UniversalScraperManager(max_workers=2, engine_timeout_seconds=2)
    manager._engines = {
        "good": _FakeEngine("good", rows=2),
        "bad": _FakeEngine("bad", fail=True),
    }
    response = manager.search("royal family", engine_ids=["good", "bad"], limit_per_engine=2)
    assert response.total == 2
    assert response.stats[0].success is True
    assert response.stats[1].success is False
    assert "unavailable" in response.stats[1].error


def test_default_engine_order_is_yandex_first():
    # Yandex bot-walls least, so it is always first; Bing, Flickr and
    # DuckDuckGo are fallbacks in that order (WEB_ENGINE_PRIORITY).
    assert UniversalScraperManager.DEFAULT_ENGINES == (
        "yandex", "bing", "flickr", "duckduckgo",
    )
    assert UniversalScraperManager.DEFAULT_ENGINES[0] == "yandex"


def test_is_bot_gate_error_matches_known_challenge_messages():
    from services.web_image.universal_scraper import (
        is_bot_gate_error,
        is_suspected_bot_gate_error,
    )

    # The DDG "likely bot-gate interstitial" message is a guess on two
    # clean empty results, not a definitive refusal: it is suspected, not
    # a bot gate, and takes the short refusal skip (GTA fix, Nov 16).
    assert not is_bot_gate_error(RuntimeError(
        "DuckDuckGo returned no results after 2 attempts (likely bot-gate interstitial)"
    ))
    assert is_suspected_bot_gate_error(RuntimeError(
        "DuckDuckGo returned no results after 2 attempts (likely bot-gate interstitial)"
    ))
    assert is_bot_gate_error(RuntimeError(
        "Yandex bot challenge (SmartCaptcha; egress IP gated)"
    ))
    assert not is_bot_gate_error(RuntimeError("connection timed out"))
    assert is_bot_gate_error(RuntimeError("Bing returned HTTP status 403"))


def test_bot_gated_engine_is_skipped_until_its_reprobe_window():
    # DuckDuckGo bot-gates once (its 202 anomaly interstitial). The manager
    # must not pay its retry/timeout budget again on the next scene — but
    # the skip is now 300s with a halfway re-probe, not a whole-run ban
    # (GTA fix, Nov 16). An immediate second search still skips it.
    calls = {"duckduckgo": 0, "yandex": 0}

    class BotGatedEngine(_FakeEngine):
        def search(self, query, limit, page, safe_search):
            calls[self.name] += 1
            raise RuntimeError(
                "DuckDuckGo returned no results after 2 attempts "
                "(likely bot-gate interstitial)"
            )

    class CountingEngine(_FakeEngine):
        def search(self, query, limit, page, safe_search):
            calls[self.name] += 1
            return super().search(query, limit, page, safe_search)

    manager = UniversalScraperManager(max_workers=2, engine_timeout_seconds=2)
    manager._engines = {
        "yandex": CountingEngine("yandex", rows=1),
        "duckduckgo": BotGatedEngine("duckduckgo"),
    }
    first = manager.search("train wheel", engine_ids=["yandex", "duckduckgo"], limit_per_engine=1)
    assert first.total == 1
    assert calls == {"duckduckgo": 1, "yandex": 1}
    second = manager.search(
        "train wheel flange", engine_ids=["yandex", "duckduckgo"], limit_per_engine=1
    )
    assert second.total == 1
    # DuckDuckGo was not called again; Yandex still runs.
    assert calls == {"duckduckgo": 1, "yandex": 2}
    assert all(stat.engine != "duckduckgo" for stat in second.stats)


def test_http_403_refusal_is_cooled_down_without_disabling_provider_for_15_minutes():
    calls = {"flickr": 0}

    class RefusedEngine(_FakeEngine):
        def search(self, query, limit, page, safe_search):
            calls[self.name] += 1
            raise RuntimeError("Flickr returned HTTP status 403")

    manager = UniversalScraperManager(max_workers=1, engine_timeout_seconds=1)
    manager._refusal_skip_seconds = 30.0
    manager._bot_gate_skip_seconds = 900.0
    manager._engines = {"flickr": RefusedEngine("flickr")}

    first = manager.search("railway wheel", engine_ids=["flickr"], limit_per_engine=1)
    second = manager.search("railway wheel flange", engine_ids=["flickr"], limit_per_engine=1)

    assert calls["flickr"] == 1
    assert not first.stats[0].success
    assert second.stats == []


def test_ordinary_engine_failure_is_not_sticky():
    # A transport failure (not a bot challenge) must not disable the engine
    # for the rest of the run; it may recover on the next scene.
    manager = UniversalScraperManager(max_workers=2, engine_timeout_seconds=2)
    manager._engines = {
        "good": _FakeEngine("good", rows=1),
        "bad": _FakeEngine("bad", fail=True),
    }
    manager.search("royal family", engine_ids=["good", "bad"], limit_per_engine=1)
    second = manager.search("royal family", engine_ids=["good", "bad"], limit_per_engine=1)
    assert any(stat.engine == "bad" and not stat.success for stat in second.stats)


def test_manager_converts_future_timeout_into_partial_response(monkeypatch):
    # Staggered deadlines (Area 6, Nov 16): the second engine's deadline
    # is the base timeout plus one stagger step, so a 1.4s engine still
    # times out against a 1.0s base + 0.2s stagger = 1.2s deadline — but
    # an engine that merely needed longer than the FIRST engine's wall
    # no longer dies with it.
    monkeypatch.setenv("VIDZEO_UNIVERSAL_ENGINE_STAGGER_SECONDS", "0.2")
    manager = UniversalScraperManager(max_workers=2, engine_timeout_seconds=1)
    manager._engines = {
        "good": _FakeEngine("good", rows=1),
        "slow": _FakeEngine("slow", delay=1.4, rows=1),
    }
    response = manager.search(
        "royal family", engine_ids=["good", "slow"], limit_per_engine=1, timeout_seconds=1
    )
    assert response.total == 1
    assert any(stat.engine == "slow" and not stat.success and "timeout" in stat.error for stat in response.stats)


def test_result_adapter_preserves_provenance_and_pending_visual_gate():
    row = UniversalImageResult(
        title="Moon landing",
        image_url="https://images.example/moon.jpg",
        thumbnail_url="https://images.example/moon-small.jpg",
        source_url="https://archive.example/apollo",
        source_engine="Yandex",
        description="Yandex visual search index",
    )
    candidate = universal_result_to_candidate(row, query="Apollo 11")
    assert candidate["source"] == "universal_scraper"
    assert candidate["provider"] == "universal_yandex"
    assert candidate["metadata_pending"] is True
    assert candidate["source_url"] == "https://archive.example/apollo"


# ── S7: per-host search throttling ───────────────────────────────────────
def test_throttled_get_caps_per_host_concurrency_and_enforces_gap(monkeypatch):
    """One host sees at most VIDZEO_UNIVERSAL_PER_HOST concurrent requests,
    and request starts to the same host are spaced by the minimum delay."""
    import threading

    from services.web_image import universal_scraper as scraper

    monkeypatch.setenv("VIDZEO_UNIVERSAL_PER_HOST", "2")
    monkeypatch.setenv("VIDZEO_UNIVERSAL_MIN_DELAY_SECONDS", "0.5")
    host = "https://throttle-test-unique.example/search"

    class FakeResp:
        status_code = 200
        text = "ok"

    class FakeClient:
        def __init__(self):
            self.starts = []
            self.inflight = 0
            self.peak = 0
            self.lock = threading.Lock()

        def get(self, url, headers=None, timeout=None):
            with self.lock:
                self.starts.append(time.monotonic())
                self.inflight += 1
                self.peak = max(self.peak, self.inflight)
            time.sleep(0.2)
            with self.lock:
                self.inflight -= 1
            return FakeResp()

    client = FakeClient()
    threads = [
        threading.Thread(
            target=lambda: scraper._throttled_get(client, host, headers={}, timeout=5)
        )
        for _ in range(3)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert client.peak <= 2, "per-host concurrency cap exceeded"
    gaps = [client.starts[i + 1] - client.starts[i] for i in range(len(client.starts) - 1)]
    assert gaps and all(gap >= 0.45 for gap in gaps), f"min delay not enforced: {gaps}"


def test_throttled_get_retries_429_with_backoff(monkeypatch):
    """A 429 is retried with backoff; a 200 on the retry is returned."""
    from services.web_image import universal_scraper as scraper

    monkeypatch.setenv("VIDZEO_UNIVERSAL_MIN_DELAY_SECONDS", "0")
    monkeypatch.setenv("VIDZEO_UNIVERSAL_RETRY_BASE", "0.05")

    class FakeResp:
        def __init__(self, status):
            self.status_code = status
            self.text = ""

    class FlakyClient:
        def __init__(self):
            self.calls = 0

        def get(self, url, headers=None, timeout=None):
            self.calls += 1
            return FakeResp(429 if self.calls == 1 else 200)

    client = FlakyClient()
    response = scraper._throttled_get(
        client, "https://throttle-retry-unique.example/x", headers={}, timeout=5
    )
    assert client.calls == 2
    assert response.status_code == 200

# ── GTA web-lane fix (Nov 16): gate, late spool, liveness ───────────────

def test_suspected_interstitial_gets_short_refusal_skip_not_bot_gate_skip():
    """A guessed DDG interstitial takes the 60s refusal skip, not 900s/300s."""
    manager = UniversalScraperManager(max_workers=1, engine_timeout_seconds=1)

    class InterstitialEngine(_FakeEngine):
        def search(self, query, limit, page, safe_search):
            raise RuntimeError(
                "DuckDuckGo returned no results after 2 attempts "
                "(likely bot-gate interstitial)"
            )

    manager._engines = {"duckduckgo": InterstitialEngine("duckduckgo")}
    manager.search("railway wheel", engine_ids=["duckduckgo"], limit_per_engine=1)
    assert manager._bot_gate_kind["duckduckgo"] == "suspected_interstitial"
    remaining = manager._bot_gated["duckduckgo"] - time.monotonic()
    # Refusal skip is 60s by default — far short of the old 900s bot-gate ban.
    assert 30.0 < remaining <= 60.0
    # And the re-probe opens halfway through it.
    reprobe_in = manager._bot_gate_reprobe_due["duckduckgo"] - time.monotonic()
    assert 20.0 < reprobe_in <= 30.0


def test_gated_engine_is_reprobed_once_per_gate_epoch():
    """One probe per gate epoch, halfway through the skip; a failed probe re-gates."""
    calls = {"yandex": 0}

    class GatedEngine(_FakeEngine):
        def search(self, query, limit, page, safe_search):
            calls[self.name] += 1
            raise RuntimeError("Yandex bot challenge (SmartCaptcha; egress IP gated)")

    manager = UniversalScraperManager(max_workers=1, engine_timeout_seconds=1)
    manager._bot_gate_skip_seconds = 4.0  # re-probe due at 2s
    manager._engines = {"yandex": GatedEngine("yandex")}

    manager.search("railway", engine_ids=["yandex"], limit_per_engine=1)
    assert calls == {"yandex": 1}
    # Immediate retry: still gated, no probe yet.
    manager.search("railway wheel", engine_ids=["yandex"], limit_per_engine=1)
    assert calls == {"yandex": 1}
    # Halfway through the skip the single probe opens.
    time.sleep(2.1)
    manager.search("railway flange", engine_ids=["yandex"], limit_per_engine=1)
    assert calls == {"yandex": 2}
    # The failed probe re-gated for a fresh skip; no second probe this epoch.
    manager.search("railway axle", engine_ids=["yandex"], limit_per_engine=1)
    assert calls == {"yandex": 2}


def test_successful_probe_clears_the_gate():
    """A gated engine that answers again is usable immediately, not after the skip."""
    calls = {"yandex": 0}

    class FlakyEngine(_FakeEngine):
        def search(self, query, limit, page, safe_search):
            calls[self.name] += 1
            if calls[self.name] == 1:
                raise RuntimeError("Yandex bot challenge (SmartCaptcha; egress IP gated)")
            return super().search(query, limit, page, safe_search)

    manager = UniversalScraperManager(max_workers=1, engine_timeout_seconds=1)
    manager._bot_gate_skip_seconds = 300.0
    manager._refusal_skip_seconds = 60.0
    manager._engines = {"yandex": FlakyEngine("yandex", rows=1)}

    manager.search("railway", engine_ids=["yandex"], limit_per_engine=1)
    assert calls == {"yandex": 1}
    assert "yandex" in manager._bot_gated
    # Force the re-probe window open without waiting 150s.
    manager._bot_gate_reprobe_due["yandex"] = time.monotonic() - 1.0
    response = manager.search("railway wheel", engine_ids=["yandex"], limit_per_engine=1)
    assert calls == {"yandex": 2}
    assert response.total == 1
    assert "yandex" not in manager._bot_gated
    # And the next search runs it normally.
    manager.search("railway flange", engine_ids=["yandex"], limit_per_engine=1)
    assert calls == {"yandex": 3}


def test_late_engine_results_recovered_by_spool_recheck(monkeypatch):
    """An engine finishing after its batch deadline is salvaged, not dropped."""
    monkeypatch.setenv("VIDZEO_UNIVERSAL_ENGINE_STAGGER_SECONDS", "0.2")
    manager = UniversalScraperManager(max_workers=2, engine_timeout_seconds=1)
    manager._engines = {
        "good": _FakeEngine("good", rows=1),
        "slow": _FakeEngine("slow", delay=1.4, rows=2),
    }
    response = manager.search(
        "royal family", engine_ids=["good", "slow"], limit_per_engine=2, timeout_seconds=1
    )
    # The slow engine (deadline 1.2s) timed out; the batch still returned.
    assert response.total == 1
    assert any(stat.engine == "slow" and not stat.success for stat in response.stats)
    # Same-query re-check recovers the late rows once the engine lands.
    recovered = manager.collect_late_results("royal family", wait_seconds=2.0)
    assert len(recovered) == 2
    assert all(row.source_engine == "slow" for row in recovered)


def test_late_spool_harvest_covers_every_scene_query(monkeypatch):
    """collect_late_results_for_queries recovers late rows for all queries at once."""

    class QueryAwareEngine(_FakeEngine):
        def search(self, query, limit, page, safe_search):
            if self.delay:
                time.sleep(self.delay)
            return [
                UniversalImageResult(
                    title=f"{self.name} {query} {idx}",
                    image_url=f"https://{self.name}.example/{query.replace(' ', '-')}-{idx}.jpg",
                    source_engine=self.name,
                )
                for idx in range(self.rows)
            ]

    monkeypatch.setenv("VIDZEO_UNIVERSAL_ENGINE_STAGGER_SECONDS", "0.2")
    manager = UniversalScraperManager(max_workers=2, engine_timeout_seconds=1)
    manager._engines = {
        "good": QueryAwareEngine("good", rows=1),
        "slow": QueryAwareEngine("slow", delay=1.4, rows=2),
    }
    first = manager.search(
        "royal family", engine_ids=["good", "slow"], limit_per_engine=2, timeout_seconds=1
    )
    second = manager.search(
        "royal family crest", engine_ids=["good", "slow"], limit_per_engine=2, timeout_seconds=1
    )
    assert first.total == 1 and second.total == 1
    # One harvest call recovers both queries' late engines (4 rows), even
    # though the second query's engine lands after the first query's.
    harvested = manager.collect_late_results_for_queries(
        ["royal family", "royal family crest"], wait_seconds=2.0
    )
    assert len(harvested) == 4
    assert {row.source_engine for row in harvested} == {"slow"}


def test_manager_progress_snapshot_tracks_completion_and_gates():
    """last_progress_at / progress_snapshot expose the wave liveness signal."""
    manager = UniversalScraperManager(max_workers=2, engine_timeout_seconds=2)
    before = manager.last_progress_at
    manager._engines = {"good": _FakeEngine("good", delay=0.05, rows=1)}
    manager.search("apollo", engine_ids=["good"], limit_per_engine=1)
    assert manager.last_progress_at >= before
    snapshot = manager.progress_snapshot()
    assert snapshot["last_batch_max_deadline_seconds"] > 0
    assert snapshot["gated_engines"] == {}
    assert snapshot["last_progress_at"] == manager.last_progress_at


def test_throttled_get_caps_eight_way_stampede_to_per_host_limit(monkeypatch):
    """Eight parallel calls to one host still see at most 2 in flight."""
    import threading

    from services.web_image import universal_scraper as scraper

    monkeypatch.setenv("VIDZEO_UNIVERSAL_PER_HOST", "2")
    monkeypatch.setenv("VIDZEO_UNIVERSAL_MIN_DELAY_SECONDS", "0.05")
    host = "https://stampede-test-unique.example/search"

    class FakeResp:
        status_code = 200
        text = "ok"

    class FakeClient:
        def __init__(self):
            self.inflight = 0
            self.peak = 0
            self.lock = threading.Lock()

        def get(self, url, headers=None, timeout=None):
            with self.lock:
                self.inflight += 1
                self.peak = max(self.peak, self.inflight)
            time.sleep(0.15)
            with self.lock:
                self.inflight -= 1
            return FakeResp()

    client = FakeClient()
    threads = [
        threading.Thread(
            target=lambda: scraper._throttled_get(client, host, headers={}, timeout=5)
        )
        for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert client.peak <= 2, f"per-host concurrency cap exceeded: peak={client.peak}"


def test_isolated_client_rejects_list_payload_as_transport_error(monkeypatch):
    """Regression (Nov 16 verification): the search worker writes '[]' for
    ANY failure, and _IsolatedNativeClient.get indexed it as data['text'] —
    a bare TypeError. That TypeError used to be masked by the engines'
    plain-requests fallback; with the fallback removed it zeroed the whole
    engine live (primp DNS dead, plain requests fine). A non-dict payload
    must raise a clean RuntimeError so the caller can fall back."""
    from services.web_image import universal_scraper as scraper

    class Process:
        def __init__(self, *args, **kwargs):
            pass

        def communicate(self, *args, **kwargs):
            return ("[]", "")

        def poll(self):
            return 0

    monkeypatch.setattr(scraper.subprocess, "Popen", Process)
    client = scraper._IsolatedNativeClient()
    try:
        client.get("https://yandex.com/images/search?text=x", headers={}, timeout=15)
    except RuntimeError as exc:
        assert "unexpected payload" in str(exc)
    else:
        raise AssertionError("expected RuntimeError for a list worker payload")


def test_engine_falls_back_to_plain_requests_when_isolated_client_fails(monkeypatch):
    """Regression (Nov 16 verification): with the isolated transport dead
    (primp DNS failure), an engine must still return rows via the bounded
    plain-requests fallback — not zero rows. The fallback stays bounded
    because queue time counts inside each 15s budget."""
    from services.web_image import universal_scraper as scraper

    class DeadClient:
        def get(self, url, headers=None, timeout=None):
            raise RuntimeError("search worker returned an unexpected payload")

    feed = {"items": [{"media": {"m": "https://example.test/gta-vi_m.jpg"},
                       "title": "GTA VI key art", "link": "https://example.test/page",
                       "author": "nobody@example.test", "tags": "gta"}]}

    class FakeResp:
        status_code = 200

        def json(self):
            return dict(feed)

    monkeypatch.setattr(scraper.FlickrEngine, "_get_client", lambda self: DeadClient())
    monkeypatch.setattr(scraper.requests, "get",
                        lambda url, headers=None, timeout=None: FakeResp())
    rows = scraper.FlickrEngine().search("Grand Theft Auto VI key art", 5, 1, True)
    assert len(rows) == 1
    assert rows[0].image_url == "https://example.test/gta-vi_b.jpg"


def test_throttled_get_queue_wait_counts_inside_timeout(monkeypatch):
    """A call that cannot start inside its budget fails fast, not after 200s."""
    import threading

    from services.web_image import universal_scraper as scraper

    monkeypatch.setenv("VIDZEO_UNIVERSAL_PER_HOST", "1")
    monkeypatch.setenv("VIDZEO_UNIVERSAL_MIN_DELAY_SECONDS", "0")
    host = "https://queue-budget-test-unique.example/search"

    class FakeResp:
        status_code = 200
        text = "ok"

    blocker_started = threading.Event()
    release_blocker = threading.Event()

    class BlockingClient:
        def get(self, url, headers=None, timeout=None):
            blocker_started.set()
            release_blocker.wait(timeout=5)
            return FakeResp()

    errors = []

    def queued_call():
        try:
            scraper._throttled_get(
                BlockingClient(), host, headers={}, timeout=0.6
            )
        except Exception as exc:  # noqa: BLE001 - recorded for the assertion
            errors.append(exc)

    first = threading.Thread(
        target=lambda: scraper._throttled_get(
            BlockingClient(), host, headers={}, timeout=5
        )
    )
    first.start()
    assert blocker_started.wait(2.0)
    queued = threading.Thread(target=queued_call)
    started = time.monotonic()
    queued.start()
    queued.join(timeout=3.0)
    elapsed = time.monotonic() - started
    release_blocker.set()
    first.join(timeout=3.0)
    assert not queued.is_alive(), "queued call hung past its budget"
    assert elapsed < 2.0, f"queued call took {elapsed:.2f}s, budget was 0.6s"
    assert errors and isinstance(errors[0], TimeoutError)
