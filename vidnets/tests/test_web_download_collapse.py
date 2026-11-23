"""Web download collapse: 190 mapped candidates, 0 downloaded (test 29 fix).

Test 29 (job 7dfd3114) proved the web-0 moved downstream: search returned
200 rows / 190 mapped candidates, but ZERO were ever downloaded — 85
download failures (36 timeout, 36 inflight_cached, 5 capacity-wait,
8 batch-budget-exhausted), 0 files in render_media/web_v2_*. Every test
here must FAIL on clean HEAD 88e6f82 and PASS with the fix. Run with
``VIDZEO_RENDER_GATE=off``.
"""
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))


# ---------------------------------------------------------------------------
# T9a — per-query empty streak (§3): one query's failures must not gate
# another query's retries.
# ---------------------------------------------------------------------------

def test_empty_streak_is_per_query(monkeypatch):
    """T9a: 3 stalled batches for query A reach the cap, so a 4th batch
    for A skips its retry waves — but a batch for a DIFFERENT query B
    still pays full retry waves (1 + 2). On HEAD the counter is global,
    so B's batch pays only 1 engine run. Control: the snapshot answers
    each query's own streak, and the property reports the max."""
    import requests
    from services.web_image.universal_scraper import (
        UniversalImageResult,
        UniversalScraperManager,
        _universal_empty_streak_cap,
    )

    monkeypatch.setenv("VIDZEO_UNIVERSAL_EMPTY_STREAK_CAP", "3")
    monkeypatch.setenv("VIDZEO_UNIVERSAL_EMPTY_RETRY_WAVES", "2")
    monkeypatch.setenv("VIDZEO_UNIVERSAL_EMPTY_RETRY_BACKOFF_SECONDS", "0")
    assert _universal_empty_streak_cap() == 3

    manager = UniversalScraperManager(max_workers=2, engine_timeout_seconds=5)
    runs = {"count": 0, "return_rows": False}

    class StallEngine:
        name = "stall"

        def search(self, query, limit, page, safe_search, direct=False):
            runs["count"] += 1
            if runs["return_rows"]:
                return [UniversalImageResult(
                    title="stub", image_url="https://images.example/stub.jpg",
                    source_engine="stall",
                )]
            raise requests.exceptions.ReadTimeout("stalling engine timed out")

    manager._engines = {"stall": StallEngine()}
    try:
        for _ in range(4):
            resp = manager.search("moon", engine_ids=["stall"])
            assert resp.total == 0
        # Query A is at the cap: its 4th batch paid 1 run (verified by
        # the property below); a fresh query must still retry fully.
        assert manager.progress_snapshot("moon")["consecutive_empty_stalled_batches"] == 4
        assert manager.progress_snapshot("mars")["consecutive_empty_stalled_batches"] == 0
        assert manager.consecutive_empty_stalled_batches == 4
        before = runs["count"]
        resp = manager.search("mars", engine_ids=["stall"])
        assert resp.total == 0
        assert runs["count"] - before == 3, (
            "a different query must still pay 1 + 2 retry waves, "
            f"got {runs['count'] - before} engine run(s)"
        )
        assert manager.progress_snapshot("mars")["consecutive_empty_stalled_batches"] == 1
        # And a rows-returning batch clears only its own query's streak.
        runs["return_rows"] = True
        resp = manager.search("moon", engine_ids=["stall"])
        assert resp.total == 1
        runs["return_rows"] = False
        assert manager.progress_snapshot("moon")["consecutive_empty_stalled_batches"] == 0
        assert manager.progress_snapshot("mars")["consecutive_empty_stalled_batches"] == 1
    finally:
        manager._executor.shutdown(wait=True)


# ---------------------------------------------------------------------------
# T9b — rescue gate reads the per-query streak (§3).
# ---------------------------------------------------------------------------

def test_rescue_gate_reads_per_query_streak(monkeypatch):
    """T9b: the resolver's rescue pass for a query is skipped only when
    THAT query's streak is at the cap. A query whose own streak is 0
    still gets its rescue attempts even while another query sits at
    the cap. On HEAD the gate reads one global counter, so the
    capped-query half runs its rescue (5 calls, not 2)."""
    from services.web_image import resolver

    def _failed_stat(engine, error="ReadTimeout"):
        return SimpleNamespace(
            engine=engine, count=0, duration_ms=15000, success=False, error=error
        )

    monkeypatch.delenv("VIDZEO_UNIVERSAL_SCRAPER_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("VIDZEO_WEB_UNIVERSAL_RETRY_BACKOFF_SECONDS", "0")
    monkeypatch.setenv("VIDZEO_WEB_UNIVERSAL_RESCUE_ATTEMPTS", "3")
    monkeypatch.setenv("VIDZEO_WEB_UNIVERSAL_RESCUE_BACKOFF_SECONDS", "0")
    monkeypatch.setenv("VIDZEO_WEB_UNIVERSAL_RESCUE_DEADLINE_SECONDS", "60")
    monkeypatch.setenv("VIDZEO_UNIVERSAL_EMPTY_STREAK_CAP", "3")

    calls = []

    def search(query, **options):
        calls.append(options.get("direct"))
        stats = [_failed_stat("bing"), _failed_stat("yandex")]
        return resolver.UniversalSearchResponse(query, 0, [], stats)

    def search_via_pool(query, **options):
        return search(query, **options)

    # Streak 3 for "Moon phases" only; every other query is at 0.
    fake = SimpleNamespace(
        search=search_via_pool,
        collect_late_results=lambda _q, **_: [],
        progress_snapshot=lambda query=None: {
            "consecutive_empty_stalled_batches": 3 if query == "Moon phases" else 0
        },
    )
    monkeypatch.setattr(resolver, "_UNIVERSAL_SCRAPER", fake)
    monkeypatch.setattr(
        resolver, "universal_result_to_candidate",
        lambda item, **_: {"url": item.image_url},
    )
    monkeypatch.setattr(resolver, "candidate_is_safe", lambda _c: (True, "fixture"))
    monkeypatch.setattr(resolver, "_report_provider_success", lambda _s: None)
    monkeypatch.setattr(resolver, "_report_provider_failure", lambda *_a: None)

    # Capped query: 2 main attempts, zero rescue attempts.
    calls.clear()
    rows = resolver._discover_universal_urls("Moon phases")
    assert rows == []
    assert calls == [False, True], f"rescue must be skipped at cap, got {calls}"
    assert resolver._universal_audit_snapshot()["rescue_attempts"] == 0

    # Fresh query: rescue attempts run (2 main + 3 rescue).
    calls.clear()
    rows = resolver._discover_universal_urls("Mars phases")
    assert rows == []
    assert calls == [False, True, True, False, True], \
        f"rescue must run below the cap, got {calls}"
    assert resolver._universal_audit_snapshot()["rescue_attempts"] == 3


# ---------------------------------------------------------------------------
# T9c — fast-mode download timeout (§1): 8s killed every slow-hour image.
# ---------------------------------------------------------------------------

def test_fast_mode_download_timeout_default_is_60s(monkeypatch):
    """T9c: the live V2 path runs fast mode, so its per-image download
    timeout is the fast-mode default — 8s on HEAD, inside a 24s batch
    wall. Test 29 logged 36 download timeouts and 0/190 candidates
    downloaded at that budget. The fast-mode default was 30s (batch
    wall 90s); round 8 (test 35) raises it to 60s — the old non-fast
    default — and the non-fast default to 120s; the env override wins."""
    from services.web_image.resolver import _web_image_download_timeout_seconds

    monkeypatch.delenv("VIDZEO_WEB_IMAGE_DOWNLOAD_TIMEOUT_SECONDS", raising=False)
    assert _web_image_download_timeout_seconds(True) == 60
    assert _web_image_download_timeout_seconds(False) == 120
    monkeypatch.setenv("VIDZEO_WEB_IMAGE_DOWNLOAD_TIMEOUT_SECONDS", "60")
    assert _web_image_download_timeout_seconds(True) == 60


# ---------------------------------------------------------------------------
# T9d — inflight leftovers are re-downloaded, not dropped (§2).
# ---------------------------------------------------------------------------

def test_inflight_leftover_is_redownloaded_not_dropped(monkeypatch, tmp_path):
    """T9d: a URL owned by another in-flight download whose owner never
    delivers must not die as 'inflight_cached' — the batch re-queues
    it for a direct download and records the real outcome. On HEAD the
    same setup returns (url, None, 'inflight_cached') and the
    candidate is lost (test 29: 36 such losses, scene 8 lost all 18)."""
    from services.web_image import resolver

    out_dir = tmp_path / "scene"
    out_dir.mkdir()
    url = "https://images.example.com/a.jpg"
    cache = {url: "__vidzeo_inflight__some-other-owner"}
    lock = threading.Lock()

    downloaded = {"calls": 0}

    def fake_bulk(urls, output_dir, max_concurrent=None, timeout_seconds=None,
                  headers_by_url=None, max_bytes=None):
        downloaded["calls"] += 1
        path = Path(output_dir) / "img_stub.jpg"
        path.write_bytes(b"x" * 9000)
        return [(u, path, "") for u in urls]

    monkeypatch.setattr(resolver, "download_bulk", fake_bulk)

    results = resolver._download_candidate_batch(
        [{"url": url}], out_dir, max_concurrent=2, timeout_seconds=0.5,
        download_cache=cache, download_cache_lock=lock,
    )
    assert downloaded["calls"] == 1, "leftover must be fetched directly once"
    assert len(results) == 1
    got_url, got_path, got_err = results[0]
    assert got_url == url
    assert got_err == "", f"re-download must succeed, got {got_err!r}"
    assert got_path is not None and Path(got_path).is_file()
    assert "inflight_cached" not in str(got_err)


def test_inflight_leftover_failure_records_real_error(monkeypatch, tmp_path):
    """T9d (negative): if the re-download itself fails, the failure is
    recorded with the real error — never 'inflight_cached' — so the
    scene's failure summary stays honest and the URL gets its 30s
    job-local cooldown instead of a phantom dedup state."""
    from services.web_image import resolver

    out_dir = tmp_path / "scene"
    out_dir.mkdir()
    url = "https://images.example.com/b.jpg"
    cache = {url: "__vidzeo_inflight__some-other-owner"}
    lock = threading.Lock()

    def failing_bulk(urls, output_dir, max_concurrent=None, timeout_seconds=None,
                     headers_by_url=None, max_bytes=None):
        return [(u, None, "timeout") for u in urls]

    monkeypatch.setattr(resolver, "download_bulk", failing_bulk)

    results = resolver._download_candidate_batch(
        [{"url": url}], out_dir, max_concurrent=2, timeout_seconds=0.5,
        download_cache=cache, download_cache_lock=lock,
    )
    assert len(results) == 1
    got_url, got_path, got_err = results[0]
    assert got_url == url
    assert got_path is None
    assert got_err == "timeout", f"real error must be recorded, got {got_err!r}"
    assert cache[url].startswith("__vidzeo_failed__")
