"""Round 7 (test 34 fix): the round-6 regression and the job-time download wall.

Test 34 proved two things:

BUG 1 — round 6 renamed ``started`` to ``attempt_started`` in
``_throttled_get`` (universal_scraper.py) but left the except block
reading ``started``. Every engine fetch that hit a real transport
error after acquiring a slot raised NameError instead of surfacing
the real error — test 34's log shows all three universal engines
(bing, flickr, yandex) failing with "all routes failed (direct):
NameError: name 'started' is not defined", 0 rows from the whole
universal lane, 222 times. The NameError only fires when client.get
RAISES, so a healthy hour (no transport errors) never trips it —
which is why round 6's live proofs passed.

BUG 2 — test 34's muse-row downloads still landed ~0 in the early
chunks (0/6, 0/4, 0/6 in 60-122s, errors 'timeout' + 'web download
capacity wait timed out') even though the round-6 live proofs
downloaded 15/15 muse rows in ~5s. The proofs ran in a healthy hour
at low concurrency (one chunk, no contention); a job runs 8 scene
workers x 6-URL chunks at once against 12 total / 3-per-host slots
while egress is slow. The tests below reproduce job-time conditions
through the resolver's real download path and pin the bottleneck.

Every mocked test below fails on clean ccb3f2f and passes with the fix.
Live-gated tests need VIDZEO_LIVE_WEB_TEST=1.
"""

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests


# ---------------------------------------------------------------------------
# BUG 1: _throttled_get must surface the original transport error
# ---------------------------------------------------------------------------

def test_throttled_get_surfaces_original_error_after_slot_acquired(monkeypatch):
    """A fetch that raises after acquiring a slot surfaces ITS error.

    Fails on ccb3f2f: the except block reads the renamed-away ``started``
    and raises NameError, masking the real transport error — test 34's
    "all routes failed (direct): NameError: name 'started' is not
    defined" on every engine.
    """
    from services.web_image import universal_scraper as usc

    host = "started-regression.example"

    class RaisingClient:
        def get(self, url, headers=None, timeout=None, **kwargs):
            raise requests.exceptions.ReadTimeout("read timed out after 60s")

    with pytest.raises(requests.exceptions.ReadTimeout, match="read timed out"):
        usc._throttled_get(
            RaisingClient(), f"https://{host}/x.jpg", headers={}, timeout=10,
        )


def test_throttled_get_surfaces_connection_error_after_slot_acquired(monkeypatch):
    """Same shape, connection error: the original error must win.

    Fails on ccb3f2f with NameError for the same reason; guards the fix
    against a narrow patch that only rescues timeouts.
    """
    from services.web_image import universal_scraper as usc

    host = "started-regression-conn.example"

    class RaisingClient:
        def get(self, url, headers=None, timeout=None, **kwargs):
            raise requests.exceptions.ConnectionError(
                "Cannot connect to host example.com:443"
            )

    with pytest.raises(
        requests.exceptions.ConnectionError, match="Cannot connect"
    ):
        usc._throttled_get(
            RaisingClient(), f"https://{host}/x.jpg", headers={}, timeout=10,
        )


# ---------------------------------------------------------------------------
# BUG 2a: slot-wait cap + CDN per-host cap (downloads under job load)
# ---------------------------------------------------------------------------

def test_slot_wait_cap_default_is_sixty(monkeypatch):
    """The slot-wait cap defaults to 60 s, not 15 s.

    Fails on ccb3f2f (returns 15.0). The test-25 fresh-transfer-budget
    fix removed the 15 s cap's rationale — waiting no longer burns
    transfer budget — and test 34 proved the cap itself kills URLs
    while siblings finish a 45-60 s transfer.
    """
    from services.web_image import hybrid_search

    monkeypatch.delenv("VIDZEO_WEB_DOWNLOAD_SLOT_WAIT_SECONDS", raising=False)
    assert hybrid_search._download_slot_wait_seconds() == 60.0


def test_cdn_hosts_get_higher_per_host_cap():
    """The measured image CDNs get 6 slots/host; other hosts keep 3.

    Fails on ccb3f2f: no _host_limit / cdn_per_host exists and every
    host is capped at 3 — the binding constraint that made 40 muse
    rows undownloadable in a slow hour (3 slots x 60 s transfers).
    """
    from services.web_image.hybrid_search import DownloadLimiter

    limiter = DownloadLimiter(total=12, per_host=3, cdn_per_host=6)
    assert limiter._host_limit("thumb.wikimedia.org") == 6
    assert limiter._host_limit("upload.wikimedia.org") == 6
    assert limiter._host_limit("live.staticflickr.com") == 6
    assert limiter._host_limit("cdn.britannica.com") == 3
    assert limiter._host_limit("example.com") == 3
    limiter.open_circuit("thumb.wikimedia.org", seconds=60)
    assert limiter._host_limit("thumb.wikimedia.org") == 1


# --- slow-transport fakes: a 20 s transfer, honoring the timeout -------

import asyncio as _asyncio


class _SlowContent:
    def __init__(self, data=b""):
        self._data = data

    async def iter_chunked(self, _n):
        if self._data:
            yield self._data


class _SlowResponse:
    """Headers arrive after ``latency``; a shorter timeout raises first."""

    def __init__(self, latency, timeout_total):
        self.status = 200
        self.headers = {"Content-Type": "image/jpeg"}
        self.content = _SlowContent(b"x" * 20000)
        self._latency = latency
        self._timeout_total = timeout_total

    async def __aenter__(self):
        if self._timeout_total is not None and self._timeout_total < self._latency:
            await _asyncio.sleep(self._timeout_total)
            raise _asyncio.TimeoutError()
        await _asyncio.sleep(self._latency)
        return self

    async def __aexit__(self, *args):
        return False


class _SlowSession:
    """Lands on both routes after ``latency`` seconds per transfer."""

    latency = 20.0

    def __init__(self, **kwargs):
        self.trust_env = kwargs.get("trust_env")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def get(self, url, **kwargs):
        timeout = kwargs.get("timeout")
        return _SlowResponse(self.latency, getattr(timeout, "total", None))


class _SlowFakeTimeout:
    def __init__(self, total=None, connect=None):
        self.total = total
        self.connect = connect


class _SlowFakeConnector:
    def __init__(self, *args, **kwargs):
        pass


def _install_slow_aiohttp(monkeypatch, latency=20.0):
    import aiohttp as _real_aiohttp
    from types import SimpleNamespace

    from services.web_image import hybrid_search

    _SlowSession.latency = latency
    monkeypatch.setattr(hybrid_search, "aiohttp", SimpleNamespace(
        ClientSession=_SlowSession,
        TCPConnector=_SlowFakeConnector,
        ClientTimeout=_SlowFakeTimeout,
        ClientConnectionError=_real_aiohttp.ClientConnectionError,
    ))


def _slow_job_urls():
    """48 unique URLs, 16 per CDN host — the muse wave's host mix."""
    urls = []
    for i in range(16):
        urls.append(f"https://thumb.wikimedia.org/wikipedia/commons/thumb/0/00/Test{i}.jpg/1280px-Test{i}.jpg")
    for i in range(16):
        urls.append(f"https://upload.wikimedia.org/wikipedia/commons/0/00/Test{i}.jpg")
    for i in range(16):
        urls.append(f"https://live.staticflickr.com/65535/Test{i}.jpg")
    return urls


def test_job_time_downloads_land_under_slow_transfers(monkeypatch, tmp_path):
    """8 scene threads x 6 URLs at 20 s/transfer land ~all of them.

    The job-time shape through the resolver's real download path
    (_download_candidate_batch, shared cache, chunk size 6, 60 s
    per-URL timeout). Fails on ccb3f2f: 3/host slots + the 15 s
    slot-wait cap kill every URL past the first 3 per host — test 34's
    early chunks landing 0-1/6. With the fix (6/host CDN cap, 60 s
    slot-wait cap) the queue drains inside the 180 s batch wall.
    """
    import threading as _threading

    from services.web_image import hybrid_search, resolver

    _install_slow_aiohttp(monkeypatch, latency=20.0)
    monkeypatch.delenv("VIDZEO_WEB_DOWNLOAD_SLOT_WAIT_SECONDS", raising=False)
    # Fresh limiter from the module defaults (what the job gets).
    hybrid_search._DOWNLOAD_LIMITER = hybrid_search.DownloadLimiter(
        total=12, per_host=3, cdn_per_host=6,
    )
    urls = _slow_job_urls()
    slices = [urls[i * 6:(i + 1) * 6] for i in range(8)]
    cache: dict = {}
    cache_lock = _threading.Lock()
    results: dict = {}

    def worker(i, chunk):
        results[i] = resolver._download_candidate_batch(
            [{"url": u} for u in chunk], tmp_path / f"scene{i}",
            max_concurrent=6, timeout_seconds=60,
            download_cache=cache, download_cache_lock=cache_lock,
        )

    threads = [_threading.Thread(target=worker, args=(i, s), daemon=True)
               for i, s in enumerate(slices)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=300)
    landed = sum(
        1 for res in results.values() for _u, p, _e in res
        if p and Path(p).is_file()
    )
    assert landed >= 40, f"only {landed}/48 landed under 20 s transfers"


# ---------------------------------------------------------------------------
# BUG 2b (survival): the streamer drain must wait for the vision pack
# ---------------------------------------------------------------------------

def _slow_passing_row(candidate_id):
    return {
        "candidate_id": candidate_id,
        "scene_description": "a street scene with cars",
        "identity_score": 85.0, "action_score": 85.0, "context_score": 85.0,
        "quality": 85.0, "final_score": 85.0, "relevance": 85.0,
        "confidence": 85.0, "domain_match": True, "scene_match": True,
        "fatal_mismatch": False, "entity_conflict": False,
        "reason": "shows the scene subject", "visible_overlays": [],
        "subject_box": {}, "safe_text_regions": [], "has_text": False,
    }


class _SlowWebClient:
    """Vision answers arrive after ``delay`` seconds — the slow hour."""

    def __init__(self, delay=70.0):
        self.delay = delay
        self.calls = []
        self.lock = threading.Lock()
        self.model = "qwen/test"

    def analyze_images_batch(self, paths, prompt, system, ids, scene_tags=None):
        with self.lock:
            self.calls.append({"paths": list(paths), "ids": list(ids)})
        time.sleep(self.delay)
        return {cid: _slow_passing_row(cid) for cid in ids}


def _make_slow_streamer(tmp_path, n, client, budget):
    from types import SimpleNamespace

    from services.web_image.resolver import _WebVisionStreamer
    from PIL import Image

    results = [None] * n
    applied = []

    def apply_result(idx, result):
        applied.append(idx)
        results[idx] = result

    ctx = SimpleNamespace(
        model="qwen/test", visual_required=False, fail_closed=False,
        lane_scheduler=None, _parent=None,
    )
    ctx._lane_budget = budget
    ctx.get_client = lambda: client
    streamer = _WebVisionStreamer(
        visual_context=ctx,
        lane_budget=budget,
        scene_texts=[f"Panama Canal scene {i}" for i in range(n)],
        scene_active_subjects=["Panama Canal"] * n,
        title="Panama Canal",
        results=results,
        results_lock=threading.RLock(),
        apply_result=apply_result,
    )
    return streamer, results, applied


def _slow_scene_result(idx, tmp_path):
    from PIL import Image

    path = tmp_path / f"web_{idx}.jpg"
    Image.new("RGB", (64, 48), (10 + idx, 40, 80)).save(path, "JPEG", quality=70)
    shortlist = [{
        "candidate_id": f"web:{idx}:c0",
        "path": str(path),
        "title": "Panama Canal locks",
        "description": "a canal scene",
        "domain": "upload.wikimedia.org",
        "provider": "web",
        "source": "web",
        "url": "https://upload.wikimedia.org/wikipedia/commons/0/00/Test.jpg",
        "query_used": "Panama Canal",
    }]
    winner = dict(shortlist[0])
    winner["_vision_shortlist"] = shortlist
    winner["candidate_pool_size"] = 1
    winner["downloaded_candidate_count"] = 1
    winner["query_used"] = "Panama Canal"
    winner["final_score"] = 80.0
    return winner


def test_streamer_close_waits_for_slow_vision_pack(tmp_path):
    """close() waits for a vision pack answering after 70 s.

    Test 34's shape: the pack filed ~03:06:40, answers landed
    03:08:23/53 (~103-133 s), but the drain's 60 s join expired
    first and the plan locked with 0 web decided. Fails on ccb3f2f
    (join cap 60 s < 70 s delay: scene undecided after close).
    With the fix (join cap 180 s, bounded by the lane budget) the
    scene is decided when close() returns.
    """
    from services.visual_intelligence.lane_budget import LaneVisionBudget

    client = _SlowWebClient(delay=70.0)
    budget = LaneVisionBudget("web", scene_count=13)
    streamer, results, applied = _make_slow_streamer(tmp_path, 13, client, budget)
    try:
        assert streamer.offer(4, _slow_scene_result(4, tmp_path))
        t0 = time.monotonic()
        streamer.close()
        waited = time.monotonic() - t0
    finally:
        # The worker thread is a daemon; if the join timed out it keeps
        # running, but close() already returned and the assertion below
        # is what distinguishes the fix from HEAD.
        pass
    assert 4 in streamer.decided_snapshot(), (
        f"scene 4 undecided after close() (waited {waited:.1f}s): "
        "the drain proceeded while the vision pack was still in flight"
    )
    assert results[4] is not None and results[4].get("visual_outcome") == "pass"
