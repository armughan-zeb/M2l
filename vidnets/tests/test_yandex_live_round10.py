"""Live Yandex re-probe — round 10 clue hunt.

Job e4d610b9 (GTA VI, on the user's branch) got 0 web images: its audit
shows every Yandex/Bing/Flickr search dying as ReadTimeout on the DIRECT
route after 47-90s, with the proxy tried only 1 of 84 times. But the
round-6 probe (Nov 23 ~06:26) got Yandex direct search back in 3.9s.

This test separates the four suspects so the clue lands in one run:

1. Transport: raw requests GET to yandex.com/images/search, direct vs
   proxy, short query — is direct hanging NOW, or is it the engine?
2. Query: the job's exact long GTA VI query vs a short query through
   YandexEngine — do long queries hang where short ones answer?
3. Route: engine.search with direct=True (direct-first) vs direct=False
   (proxy-first) — does the proxy still answer, and which routes does
   _fetch actually try (engine._last_routes_tried)?
4. Throttle: elapsed time vs socket timeout — a 47-90s engine call with
   a 30s socket timeout means most of the time was queue-wait, not
   socket. Each test prints elapsed, rows, error, and routes tried.

Live-gated: skipped unless VIDZEO_LIVE_WEB_TEST=1.
"""

import os
import time

import pytest

from server.services.web_image.universal_scraper import YandexEngine

LIVE = pytest.mark.skipif(
    os.environ.get("VIDZEO_LIVE_WEB_TEST") != "1",
    reason="live-gated: set VIDZEO_LIVE_WEB_TEST=1 to run against real Yandex",
)

SHORT_QUERY = "Panama Canal"
# The job's exact first query for scene 1 (from the web audit).
JOB_QUERY = (
    "Grand Theft Auto VI 2026 xbox series documentary gameplay city "
    "after screenshot wallpaper"
)
URL = "https://yandex.com/images/search?text={query}&p=0"


def _raw_get(query, direct, timeout=30):
    """Plain requests GET, no engine, no throttle — pure transport."""
    import urllib.parse

    import requests

    url = URL.format(query=urllib.parse.quote(query))
    kwargs = {"headers": {"User-Agent": "Mozilla/5.0"}, "timeout": timeout}
    if direct:
        kwargs["proxies"] = {"http": None, "https": None}
    t0 = time.time()
    try:
        r = requests.get(url, **kwargs)
        return time.time() - t0, r.status_code, len(r.text), None
    except Exception as exc:  # noqa: BLE001 - report, don't raise
        return time.time() - t0, None, 0, f"{type(exc).__name__}: {str(exc)[:120]}"


@LIVE
def test_raw_direct_short_query():
    elapsed, status, nbytes, err = _raw_get(SHORT_QUERY, direct=True)
    print(f"\nRAW direct short: {elapsed:.1f}s status={status} "
          f"bytes={nbytes} err={err}")
    assert err is None, f"raw direct short query failed in {elapsed:.1f}s: {err}"
    assert status == 200 and nbytes > 1000


@LIVE
def test_raw_proxy_short_query():
    elapsed, status, nbytes, err = _raw_get(SHORT_QUERY, direct=False)
    print(f"\nRAW proxy short: {elapsed:.1f}s status={status} "
          f"bytes={nbytes} err={err}")
    assert err is None, f"raw proxy short query failed in {elapsed:.1f}s: {err}"
    assert status == 200 and nbytes > 1000


@LIVE
def test_engine_direct_job_query():
    engine = YandexEngine()
    t0 = time.time()
    try:
        rows = engine.search(JOB_QUERY, limit=12, page=1, safe_search=True)
        elapsed = time.time() - t0
        print(f"\nENGINE direct job-query: {elapsed:.1f}s rows={len(rows)} "
              f"routes_tried={getattr(engine, '_last_routes_tried', None)}")
        assert len(rows) >= 1, (
            f"YandexEngine direct returned 0 rows for job query "
            f"in {elapsed:.1f}s routes={engine._last_routes_tried}"
        )
    except Exception as exc:  # noqa: BLE001 - print the clue, then fail
        elapsed = time.time() - t0
        print(f"\nENGINE direct job-query FAILED: {elapsed:.1f}s "
              f"err={type(exc).__name__}: {str(exc)[:200]} "
              f"routes_tried={getattr(engine, '_last_routes_tried', None)}")
        raise


@LIVE
def test_engine_proxy_job_query():
    engine = YandexEngine()
    t0 = time.time()
    try:
        rows = engine.search(
            JOB_QUERY, limit=12, page=1, safe_search=True, direct=False
        )
        elapsed = time.time() - t0
        print(f"\nENGINE proxy job-query: {elapsed:.1f}s rows={len(rows)} "
              f"routes_tried={getattr(engine, '_last_routes_tried', None)}")
        assert len(rows) >= 1, (
            f"YandexEngine proxy returned 0 rows for job query "
            f"in {elapsed:.1f}s routes={engine._last_routes_tried}"
        )
    except Exception as exc:  # noqa: BLE001 - print the clue, then fail
        elapsed = time.time() - t0
        print(f"\nENGINE proxy job-query FAILED: {elapsed:.1f}s "
              f"err={type(exc).__name__}: {str(exc)[:200]} "
              f"routes_tried={getattr(engine, '_last_routes_tried', None)}")
        raise


@LIVE
def test_engine_parallel_fanout_job_query():
    """Same job query, 8 threads at once — the job's actual conditions.

    The sequential tests above pass in ~4s, so if this fan-out reproduces
    the job's 47-90s ReadTimeouts, the failure is load-dependent
    (throttle queue + concurrent connections from one egress IP), not
    Yandex, the query, or the route.
    """
    import threading

    results = {}

    def one(i):
        engine = YandexEngine()
        t0 = time.time()
        try:
            rows = engine.search(
                JOB_QUERY, limit=12, page=1, safe_search=True
            )
            results[i] = (time.time() - t0, len(rows), None,
                          getattr(engine, "_last_routes_tried", None))
        except Exception as exc:  # noqa: BLE001 - record, don't raise
            results[i] = (time.time() - t0, 0,
                          f"{type(exc).__name__}: {str(exc)[:100]}",
                          getattr(engine, "_last_routes_tried", None))

    threads = [threading.Thread(target=one, args=(i,)) for i in range(8)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    total = time.time() - t0
    ok = sum(1 for v in results.values() if v[1] >= 1)
    print(f"\nFANOUT 8 threads in {total:.1f}s: {ok}/8 returned rows")
    for i in sorted(results):
        el, n, err, routes = results[i]
        print(f"  thread {i}: {el:.1f}s rows={n} err={err} routes={routes}")
    assert ok == 8, f"parallel fan-out: only {ok}/8 threads got rows"


@LIVE
def test_engine_full_job_burst():
    """The job's real burst: 8 scenes x 3 engines at once (24 threads).

    The 8-thread single-engine fan-out passed. The job fires 8 scene
    workers across Yandex + Bing + Flickr simultaneously, plus the YT
    lane's own workers — if THIS reproduces the 30-45s ReadTimeouts on
    both routes, the failure is burst-driven (egress/IP tarpit under
    concurrent load), not any single engine, query, or route.
    """
    import threading

    from server.services.web_image.universal_scraper import (
        BingEngine,
        FlickrEngine,
    )

    engines = {"yandex": YandexEngine, "bing": BingEngine, "flickr": FlickrEngine}
    results = {}

    def one(i, name, cls):
        engine = cls()
        t0 = time.time()
        try:
            rows = engine.search(
                JOB_QUERY, limit=12, page=1, safe_search=True
            )
            results[(i, name)] = (time.time() - t0, len(rows), None)
        except Exception as exc:  # noqa: BLE001 - record, don't raise
            results[(i, name)] = (
                time.time() - t0, 0, f"{type(exc).__name__}: {str(exc)[:100]}"
            )

    threads = []
    for i in range(8):
        for name, cls in engines.items():
            threads.append(threading.Thread(target=one, args=(i, name, cls)))
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    total = time.time() - t0
    ok = sum(1 for v in results.values() if v[1] >= 1)
    print(f"\nBURST 24 threads in {total:.1f}s: {ok}/24 returned rows")
    for name in engines:
        got = sum(1 for (i, n), v in results.items() if n == name and v[1] >= 1)
        times = sorted(v[0] for (i, n), v in results.items() if n == name)
        errs = {v[2] for (i, n), v in results.items() if n == name and v[2]}
        print(f"  {name}: {got}/8 rows, times={[f'{x:.0f}s' for x in times]}, "
              f"errors={errs or '-'}")
    assert ok == 24, f"job burst: only {ok}/24 threads got rows"
