"""Live Yandex check — is Yandex actually answering from this VM?

Test 33's log showed every universal engine (Bing/Yandex/Flickr) hitting
45 s ReadTimeouts on the proxy for the whole media window, so a job-side
failure does not prove Yandex itself is dead. These tests hit Yandex
directly, live, through both code paths the pipeline uses:

1. ``YandexSource`` — the dedicated adapter (data-state JSON parsing).
2. ``YandexEngine`` — the universal-lane engine (SERP parsing), which is
   the path the coordinator actually calls during a job.

Live-gated: skipped unless VIDZEO_LIVE_WEB_TEST=1, same as the muse
live-proof tests. A failure here means Yandex is genuinely unreachable
right now (proxy down, egress IP gated by SmartCaptcha, or Yandex
blocking us) — not a code bug.
"""

import os
import time

import pytest

from server.services.sources.yandex_source import YandexSource
from server.services.web_image.universal_scraper import YandexEngine

LIVE = pytest.mark.skipif(
    os.environ.get("VIDZEO_LIVE_WEB_TEST") != "1",
    reason="live-gated: set VIDZEO_LIVE_WEB_TEST=1 to run against real Yandex",
)

QUERY = "Panama Canal"


@LIVE
def test_yandex_source_returns_rows_live():
    """Dedicated Yandex adapter returns real image rows for a real query."""
    source = YandexSource()
    assert source.is_available(), "YandexSource reports unavailable (env/circuit)"
    t0 = time.time()
    rows = source.search(QUERY, limit=12)
    elapsed = time.time() - t0
    assert len(rows) >= 1, (
        f"YandexSource returned 0 rows live for {QUERY!r} in {elapsed:.1f}s"
    )
    urls = [getattr(r, "download_url", None) or getattr(r, "url", None) for r in rows]
    assert all(u and str(u).startswith("http") for u in urls), (
        f"YandexSource rows missing http URLs: {urls[:3]}"
    )
    print(f"\nYandexSource live: {len(rows)} rows in {elapsed:.1f}s "
          f"(first: {urls[0][:80]})")


@LIVE
def test_yandex_universal_engine_returns_rows_live():
    """Universal-lane Yandex engine returns real image rows for a real query."""
    engine = YandexEngine()
    t0 = time.time()
    rows = engine.search(QUERY, limit=12, page=1, safe_search=True)
    elapsed = time.time() - t0
    assert len(rows) >= 1, (
        f"YandexEngine returned 0 rows live for {QUERY!r} in {elapsed:.1f}s"
    )
    urls = [getattr(r, "url", None) or getattr(r, "image_url", None) for r in rows]
    assert all(u and str(u).startswith("http") for u in urls), (
        f"YandexEngine rows missing http URLs: {urls[:3]}"
    )
    print(f"\nYandexEngine live: {len(rows)} rows in {elapsed:.1f}s "
          f"(first: {urls[0][:80]})")
