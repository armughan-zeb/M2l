"""Round 13 (Nov 23, 2026): web + vision have NO timeouts at all.

User order (Nov 23, 22:19 PKT, refined 21:57 PKT): "we want the web to
work, without timeouts or cancel, same for the vision, fix this into
code deeply, remove things overlapping it." Rule: WEB has no timeouts
and no cancellation; VISION has no timeouts and no cancellation — a
web scene and a vision verdict keep going until they resolve. YT MAY
keep a timeout; when YT times out on a scene, that scene falls back
to WEB per-scene, in the go. A scene falls to a graphic only when
BOTH lanes have genuinely failed it, never because a budget fired.

Evidence (proof job 9ab2eb05, GTA VI 30-sec, completed 100%):
- Web Images 6, Grounded Graphic Fallback 2, YT 0.
- YT search FOUND 53 candidates, then sat in "Searching YouTube (53
  candidates found)" from 551s through 870s+ without resolving one
  clip: the transcript stage stalled (0/8 completed at the 120s
  deadline, every fetch still in flight — the two provider legs ran
  SEQUENTIALLY, ~120s per video on 6 workers), and the heartbeat
  kept claiming "Searching" because the poller recognised no
  transcript/moment phase.
- Web scenes 6-7 fell to graphics because the web lane vision budget
  expired with 2 scenes unverdicted.

Fix (this round, on top of round 12's uncommitted diff):
- W1: the coordinator's _bounded_lane_call silently clamped an
  infinite web timeout to 120s — removed.
- Y1: the transcript provider legs now RACE (first success wins)
  under one per-video wall (default 45s); a definitive no-captions
  answer ends the race at once; the third sequential yt-dlp re-fetch
  is the retry for transport failures and still runs when both legs
  failed on transport — but it shares the per-video wall (it gets no
  fresh budget), so the per-video total stays inside the wall.
- Y4: fetch_many prints transcript progress every 10s; the moment
  stage prints its accounting; the worker poller recognises
  transcript/moment phases and the callback names them; the app.py
  YT branch-wall handler switches the heartbeat to the rescue stage.
- W2 (REVERTED): the shared-batch warm-up KEEPS its batch deadline even
  in wait mode — it is a prefetch, not a gate (pinned by
  test_wait_mode_shared_provider_pool_returns_at_batch_deadline); a
  provider still working at the wall finishes in the background and
  its rows land in the cache. The per-scene wave is what runs to
  resolution.
- W3: the per-scene provider wave waits until every provider settles
  in wait mode (no 90s query cap, no cancelling pending providers).
- W6: the download batch wall and the slot-wait cap are removed in
  wait mode (per-attempt socket budget retained; per-host throttle
  untouched).
- W5: the muse-search wave waits for both providers in wait mode
  (no wave wall, no grace abandonment, no walled retry).
- W4: the universal rescue loop drops its time cap in wait mode
  (attempt cap stays as the genuine-failure bound).
- W8/W12: the web stage/query/batch deadlines are infinite (None at
  the app.py call site) in wait mode; the delivery budget remains
  the outer job backstop.

Every test here FAILS on the pre-round-13 tree and PASSES with the
fix. Slow sources are mocked; no test waits longer than ~5s real.
"""

import asyncio
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))


class _AcceptingVerifier:
    """Live vision that accepts every candidate; records every call."""

    def __init__(self):
        self.calls = []

    def __call__(self, _scene, _candidate):
        self.calls.append(str(_candidate.get("path")))
        return {"accepted": True, "status": "visual_pass", "final_score": 95.0,
                "visual_checked": True, "visual_available": True}


def _r13_scene():
    return {"scene_index": 0, "scene_id": "gta",
            "text": "Grand Theft Auto VI street scene"}


def _web_row(image_path):
    return {"path": str(image_path), "source": "web", "provider": "web",
            "media_type": "image", "candidate_id": "web:still",
            "title": "Grand Theft Auto VI official screenshot",
            "final_score": 80.0}


# ---------------------------------------------------------------------------
# W1 — the coordinator's unbounded web call is really unbounded
# ---------------------------------------------------------------------------

def test_web_lane_waits_past_old_120s_clamp(monkeypatch, tmp_path):
    """A slow web lane past a 2s media deadline still resolves the scene.

    The web lane takes 0.7s real time while a fake clock jumps +121s
    the moment the lane starts running — past the old 120s clamp the
    coordinator used to apply to an infinite timeout. On the
    pre-round-13 tree _bounded_lane_call returns (False, None, None)
    at the jump and the scene falls to a graphic; with the fix the
    infinite deadline waits and the lane's row resolves the scene.
    """
    from services.media_resolution_coordinator import resolve_storyboard_media_v2

    image = tmp_path / "web_still.jpg"
    image.write_bytes(b"\x00" * 1024)
    verifier = _AcceptingVerifier()
    jump = threading.Event()
    base = [1000.0]
    real_monotonic = time.monotonic

    def fake_monotonic():
        return base[0] + (121.0 if jump.is_set() else 0.0)

    monkeypatch.setattr(time, "monotonic", fake_monotonic)

    def web_lane(_scene, _cancel):
        time.sleep(0.3)
        jump.set()  # the clock jumps +121s while the lane is still working
        time.sleep(0.4)
        return [_web_row(image)]

    # Elapsed is measured on the REAL clock: the fake clock jumps +121s
    # by design, so time.monotonic() - started would read 121s.
    started = real_monotonic()
    result = resolve_storyboard_media_v2(
        [_r13_scene()],
        youtube_lane=lambda _s, _c: [],
        web_lane=web_lane,
        web_fallback_lane=lambda _s, _c: [],
        visual_verifier=verifier,
        neutral_factory=lambda _scene: None,
        deadline_seconds=2,
        web_fallback_grace_seconds=0,
        web_wait_until_complete=True,
    )
    assert result[0].provider == "web", (
        f"slow web lane must resolve the scene in wait mode, got {result[0].provider!r}"
    )
    assert result[0].selected_path == str(image)
    assert result[0].verification_status == "verified"
    assert real_monotonic() - started < 30.0


# ---------------------------------------------------------------------------
# Y1 — the transcript legs race; the per-video wall binds slow legs
# ---------------------------------------------------------------------------

class _FakeCookieJar:
    """Session jar with a cookie header; no cookies to iterate."""

    def get_cookie_header(self, *args, **kwargs):
        return ""

    def __iter__(self):
        return iter([])


class _SlowYtDlp:
    """Fake yt-dlp whose extract_info sleeps, then reports no captions."""

    def __init__(self, sleep_seconds=2.0):
        self._sleep = sleep_seconds

    class _YDL:
        def __init__(self, opts, sleep_seconds=2.0):
            self._sleep = sleep_seconds

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, video_id, download=False):
            time.sleep(self._sleep)
            return {"id": video_id, "formats": [],
                    "subtitles": {}, "automatic_captions": {}}

    def YoutubeDL(self, opts):
        return self._YDL(opts, self._sleep)


def _fake_transcript_api(sleep_seconds=0.0):
    class FakeTranscript:
        def to_raw_data(self):
            return [{"start": 1, "duration": 3,
                     "text": "Grand Theft Auto VI street scene"}]

    class FakeAPI:
        def __init__(self, http_client=None):
            pass

        def fetch(self, video_id, languages=None):
            if sleep_seconds:
                time.sleep(sleep_seconds)
            return FakeTranscript()

    return FakeAPI


def test_transcript_race_fast_api_beats_slow_auth_leg(tmp_path, monkeypatch):
    """The API leg wins at once; the slow auth leg does not block it.

    Auth yt-dlp leg sleeps 2s per client (4s sequential on the old
    code); the transcript-API leg answers instantly. With the race the
    fetch returns in well under 1.5s from the API; sequentially it
    took the full auth chain first (~4s) before the API ever ran.
    """
    from services.youtube_transcript_index import YouTubeTranscriptIndex
    import services.yt_dlp_runtime as yt_dlp_runtime
    import youtube_transcript_api as yta_mod

    monkeypatch.setattr(yt_dlp_runtime, "load_yt_dlp",
                        lambda: _SlowYtDlp(sleep_seconds=2.0))
    monkeypatch.setattr(yta_mod, "YouTubeTranscriptApi",
                        _fake_transcript_api(sleep_seconds=0.0))
    index = YouTubeTranscriptIndex(
        str(tmp_path / "race.sqlite3"), session_jar=_FakeCookieJar())
    started = time.monotonic()
    doc = index.fetch("race-video-1")
    elapsed = time.monotonic() - started
    assert doc.status == "ready" and doc.available
    assert doc.source == "youtube_transcript_api"
    assert elapsed < 1.5, (
        f"race must return the fast leg at once, took {elapsed:.1f}s"
    )


def test_transcript_race_per_video_wall_binds_slow_legs(tmp_path, monkeypatch):
    """Both legs slow: the per-video wall ends the fetch at ~1s, not ~10s.

    Auth leg sleeps 5s per client and the API leg sleeps 5s; with the
    per-video wall patched to 1s the fetch errors at ~1s instead of
    chaining ~10s+ of sequential provider timeouts (the job-9ab2eb05
    shape: 8 videos x ~120s chained on 6 workers against a 120s batch
    deadline, 0 retained).
    """
    from services.youtube_transcript_index import YouTubeTranscriptIndex
    import services.yt_dlp_runtime as yt_dlp_runtime
    import youtube_transcript_api as yta_mod
    import services.youtube_transcript_index as transcript_mod

    monkeypatch.setattr(yt_dlp_runtime, "load_yt_dlp",
                        lambda: _SlowYtDlp(sleep_seconds=5.0))
    monkeypatch.setattr(yta_mod, "YouTubeTranscriptApi",
                        _fake_transcript_api(sleep_seconds=5.0))
    monkeypatch.setattr(transcript_mod, "_transcript_per_video_wall_seconds",
                        lambda: 1.0)
    index = YouTubeTranscriptIndex(
        str(tmp_path / "race-wall.sqlite3"), session_jar=_FakeCookieJar())
    started = time.monotonic()
    doc = index.fetch("race-video-2")
    elapsed = time.monotonic() - started
    assert doc.status == "error", f"walled race must error, got {doc.status!r}"
    assert 0.8 <= elapsed < 3.0, (
        f"per-video wall must bind the fetch at ~1s, took {elapsed:.1f}s"
    )


# ---------------------------------------------------------------------------
# Y4 — the heartbeat states the true YT stage
# ---------------------------------------------------------------------------

def _poller(tmp_path, lines):
    from services.youtube_worker_progress import YouTubeWorkerProgress

    log = tmp_path / "worker.log"
    log.write_bytes(b"\n".join(lines) + b"\n")
    emitted = []
    callbacks = []
    poller = YouTubeWorkerProgress(
        log, "worker-test",
        emit=lambda msg, **kw: emitted.append(msg),
        progress_callback=lambda frac, msg: callbacks.append((frac, msg)),
    )
    poller.poll(final=True)
    return emitted, callbacks


def test_worker_progress_names_transcript_stage(tmp_path):
    """A transcript progress line reports fetching transcripts, not searching."""
    emitted, callbacks = _poller(tmp_path, [
        b"search complete: 53 unique candidate videos found",
        b"   [yt-transcript] fetching 3/8 completed transcript(s); 2 ready",
    ])
    assert any("phase=transcripts completed=3 total=8 ready=2" in line
               for line in emitted), emitted
    assert any(msg == "Fetching YouTube transcripts (3/8, 2 ready)"
               for _frac, msg in callbacks), callbacks
    # The transcript line must OVERWRITE the search text: the heartbeat
    # must not keep repeating "Searching YouTube" once transcripts start.
    assert callbacks[-1][1] == "Fetching YouTube transcripts (3/8, 2 ready)"


def test_worker_progress_names_moments_stage(tmp_path):
    """A moments line reports matching moments, not searching."""
    emitted, callbacks = _poller(tmp_path, [
        b"   [yt-moments] matched 12 moments across 5/8 scenes (2 transcripts ready)",
    ])
    assert any("phase=moments matched=12 scenes=5 total=8" in line
               for line in emitted), emitted
    assert any(msg == "Matching YouTube moments (12 across 5/8 scenes)"
               for _frac, msg in callbacks), callbacks


def test_worker_progress_names_transcript_deadline_line(tmp_path):
    """The fetch_many deadline accounting line also maps to transcripts."""
    emitted, callbacks = _poller(tmp_path, [
        b"   [yt-transcript] deadline 120s: retaining 5/8 completed transcript(s); 3 unfinished; 4 ready",
    ])
    assert any("phase=transcripts completed=5 total=8 ready=4" in line
               for line in emitted), emitted
    assert any(msg == "Fetching YouTube transcripts (5/8, 4 ready)"
               for _frac, msg in callbacks), callbacks


# ---------------------------------------------------------------------------
# W5 — the muse-search wave waits for slow providers in wait mode
# ---------------------------------------------------------------------------

def _wikimedia_candidate(**overrides):
    from services.sources.base_source import ImageCandidate

    row = {
        "id": "wikimedia_1", "source": "wikimedia",
        "title": "Moon crater Tycho", "description": "",
        "download_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Tycho.jpg/1280px-Tycho.jpg",
        "preview_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Tycho.jpg/1280px-Tycho.jpg",
        "width": 1280, "height": 800, "license": "",
        "metadata": {"page_id": "1",
                     "foreign_url": "https://commons.wikimedia.org/wiki/File:Tycho.jpg",
                     "provider": "wikimedia",
                     "original_url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Tycho.jpg"},
    }
    row.update(overrides)
    return ImageCandidate(
        id=row["id"], source=row["source"], title=row["title"],
        description=row["description"], download_url=row["download_url"],
        preview_url=row["preview_url"], width=row["width"],
        height=row["height"], license=row["license"], metadata=row["metadata"],
    )


def _openverse_candidate(**overrides):
    from services.sources.base_source import ImageCandidate

    row = {
        "id": "openverse_1", "source": "openverse",
        "title": "Moon over the desert", "description": "A full moon rising",
        "download_url": "https://live.staticflickr.com/1/moon.jpg",
        "preview_url": "https://api.openverse.org/v1/images/1/thumb/",
        "width": 1200, "height": 800, "license": "by",
        "metadata": {"provider": "flickr",
                     "foreign_url": "https://www.flickr.com/photos/x/1",
                     "license_url": "https://creativecommons.org/licenses/by/2.0/",
                     "attribution": "Photographer", "creator": "Photographer"},
    }
    row.update(overrides)
    return ImageCandidate(
        id=row["id"], source=row["source"], title=row["title"],
        description=row["description"], download_url=row["download_url"],
        preview_url=row["preview_url"], width=row["width"],
        height=row["height"], license=row["license"], metadata=row["metadata"],
    )


class _SlowCountingSource:
    """Fake adapter returning canned rows after a fixed delay."""

    def __init__(self, rows, sleep_seconds=0.0):
        self._rows = list(rows)
        self._sleep = sleep_seconds
        self.calls = []

    def search(self, query, limit=20, direct=False):
        self.calls.append((query, direct))
        if self._sleep:
            time.sleep(self._sleep)
        return list(self._rows[:limit])

    def is_available(self):
        return True


def _slow_wave_manager(sleep_seconds=1.5):
    from services.web_image import muse_search as muse_mod

    return muse_mod.MuseSearchManager(
        wikimedia_source=_SlowCountingSource(
            [_wikimedia_candidate()], sleep_seconds=sleep_seconds),
        openverse_source=_SlowCountingSource(
            [_openverse_candidate()], sleep_seconds=sleep_seconds),
        wave_seconds=0.5,
        retry_wall_seconds=0,
    )


def test_muse_wave_wait_mode_waits_for_slow_providers():
    """Both providers sleep 1.5s past a 0.5s wall; wait mode returns both rows.

    On the pre-round-13 tree the wave abandoned both providers at the
    0.5s wall and returned 0 rows; with the fix the wave has no wall
    in wait mode and both providers settle.
    """
    manager = _slow_wave_manager(sleep_seconds=1.5)
    started = time.monotonic()
    rows = manager.search("Grand Theft Auto VI street", q_idx=0,
                          wait_until_complete=True)
    elapsed = time.monotonic() - started
    assert len(rows) == 2, f"wait mode must return both providers' rows, got {len(rows)}"
    # candidate_to_row stamps source="muse_search" on every wave row;
    # the providers are distinguished by their download URLs.
    assert any("upload.wikimedia.org" in str(row.get("url") or "") for row in rows)
    assert any("live.staticflickr.com" in str(row.get("url") or "") for row in rows)
    assert not manager.last_wave_transport_failed
    assert elapsed < 10.0


def test_muse_wave_without_wait_still_abandons_at_wall():
    """Control: without wait mode the same slow providers are abandoned at 0.5s."""
    manager = _slow_wave_manager(sleep_seconds=1.5)
    started = time.monotonic()
    rows = manager.search("Grand Theft Auto VI street", q_idx=0,
                          wait_until_complete=False)
    elapsed = time.monotonic() - started
    assert rows == [], f"non-wait wave must abandon slow providers, got {len(rows)} rows"
    assert manager.last_wave_transport_failed
    assert elapsed < 5.0, f"non-wait wave must stay bounded, took {elapsed:.1f}s"


# ---------------------------------------------------------------------------
# W2 (REVERTED) — the shared-batch warm-up keeps its batch deadline
# ---------------------------------------------------------------------------

def _warm_pool(monkeypatch, wait_until_complete, finished=None):
    import services.web_image.resolver as resolver

    def slow_provider(name, kind, query, gallery_cmd=None, **kwargs):
        try:
            time.sleep(1.5)
            return [{"url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Tycho.jpg/1280px-Tycho.jpg",
                     "image_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Tycho.jpg/1280px-Tycho.jpg",
                     "title": "Moon crater Tycho", "source": "muse_search",
                     "provider": "wikimedia", "query": query}]
        finally:
            if finished is not None:
                finished.set()

    monkeypatch.setattr(resolver, "_enabled_fast_sources",
                        lambda: [("slow_source", "api")])
    monkeypatch.setattr(resolver, "_discover_source_query", slow_provider)
    return resolver._warm_shared_discovery_pool(
        query="Grand Theft Auto VI street",
        scene_count=8,
        gallery_cmd=None,
        discovery_cache=None,
        source_telemetry=None,
        stage_deadline_at=time.monotonic() + 60,
        batch_seconds_override=0.5,
        wait_until_complete=wait_until_complete,
    )


def test_warm_pool_wait_mode_returns_at_batch_wall(monkeypatch):
    """REVERTED W2: the warm-up keeps its batch deadline even in wait mode.

    The warm-up is a prefetch, not a gate: it returns at the batch wall
    with the slow provider pending, and the provider finishes in the
    background (its rows land in the cache, where the per-scene wave —
    unbounded in wait mode, W3 — harvests them). Round 13 first made
    the warm-up block until providers settled; that broke the pinned
    contract in
    test_wait_mode_shared_provider_pool_returns_at_batch_deadline and
    only delayed the wave, so it was reverted. The scene — not the
    warm-up — is what must never be cut off.
    """
    finished = threading.Event()
    started = time.monotonic()
    result = _warm_pool(monkeypatch, wait_until_complete=True, finished=finished)
    elapsed = time.monotonic() - started
    assert elapsed < 1.0, f"warm-up must return at the batch wall, took {elapsed:.2f}s"
    assert result["providers_pending"] == 1
    assert finished.wait(2.0), "slow provider must finish in the background after the warm-up returns"


def test_warm_pool_without_wait_cancels_at_batch_wall(monkeypatch):
    """Control: without wait mode the same slow provider is cancelled at 0.5s."""
    started = time.monotonic()
    result = _warm_pool(monkeypatch, wait_until_complete=False)
    elapsed = time.monotonic() - started
    assert result["providers_completed"] == 0
    assert result["candidates"] == 0
    assert elapsed < 5.0, f"non-wait warm-up must stay bounded, took {elapsed:.1f}s"


# ---------------------------------------------------------------------------
# W6 — downloads: no batch wall, no slot-wait cap in wait mode
# ---------------------------------------------------------------------------

def test_download_slot_inf_deadline_waits_past_cap(monkeypatch):
    """A slot request with an infinite deadline waits past the 0.5s cap.

    One slot total; the holder keeps it 1.5s. The slot-wait cap is
    patched to 0.5s. On the pre-round-13 tree every waiter was capped
    at 0.5s and died as DownloadSlotTimeout; with the fix an infinite
    deadline (wait mode) waits until the slot frees.
    """
    from services.web_image import hybrid_search
    from services.web_image.hybrid_search import DownloadLimiter

    monkeypatch.setattr(hybrid_search, "_download_slot_wait_seconds",
                        lambda: 0.5)
    limiter = DownloadLimiter(total=1, per_host=1)
    acquired = threading.Event()

    async def holder():
        async with limiter.slot("example.com", time.monotonic() + 30):
            acquired.set()
            await asyncio.sleep(1.5)

    async def waiter():
        async with limiter.slot("example.com", float("inf")):
            return True

    async def main():
        task = asyncio.create_task(holder())
        await asyncio.to_thread(acquired.wait, 5)
        got = await asyncio.wait_for(waiter(), timeout=5)
        await task
        return got

    assert asyncio.run(main()) is True


def test_download_slot_finite_deadline_still_times_out(monkeypatch):
    """Control: a finite-deadline slot request still raises past the cap."""
    from services.web_image import hybrid_search
    from services.web_image.hybrid_search import DownloadLimiter, DownloadSlotTimeout

    monkeypatch.setattr(hybrid_search, "_download_slot_wait_seconds",
                        lambda: 0.5)
    limiter = DownloadLimiter(total=1, per_host=1)
    acquired = threading.Event()

    async def holder():
        async with limiter.slot("example.com", time.monotonic() + 30):
            acquired.set()
            await asyncio.sleep(1.5)

    async def waiter():
        async with limiter.slot("example.com", time.monotonic() + 30):
            return True

    async def main():
        task = asyncio.create_task(holder())
        await asyncio.to_thread(acquired.wait, 5)
        try:
            await waiter()
            return None
        except DownloadSlotTimeout:
            return "timeout"
        finally:
            await task

    assert asyncio.run(main()) == "timeout"


def test_download_chunked_threads_wait_flag(tmp_path, monkeypatch):
    """_download_bulk_chunked passes wait_until_complete through to download_bulk.

    With the flag on, download_bulk receives wait_until_complete=True;
    with it off (the default) the keyword is not passed at all, so
    older download_bulk doubles that take no such keyword keep working.
    """
    import services.web_image.resolver as resolver

    seen = []

    def fake_download_bulk(urls, output_dir, max_concurrent=None,
                           timeout_seconds=None, headers_by_url=None, **kwargs):
        seen.append(dict(kwargs))
        return [(url, None, "fake") for url in urls]

    monkeypatch.setattr(resolver, "download_bulk", fake_download_bulk)
    resolver._download_bulk_chunked(
        ["https://example.com/a.jpg"], tmp_path, 2, 15.0,
        wait_until_complete=True)
    assert seen and seen[0].get("wait_until_complete") is True
    seen.clear()
    resolver._download_bulk_chunked(
        ["https://example.com/a.jpg"], tmp_path, 2, 15.0,
        wait_until_complete=False)
    assert seen and "wait_until_complete" not in seen[0]


# ---------------------------------------------------------------------------
# W4 — the universal rescue loop drops its time cap in wait mode
# ---------------------------------------------------------------------------

class _FailingScraper:
    """Universal scraper whose engines all fail transiently (timeout)."""

    def __init__(self):
        self.searches = 0

    def search(self, query, **kwargs):
        self.searches += 1
        return SimpleNamespace(
            query=query, total=0, results=[],
            stats=[SimpleNamespace(engine="yandex", count=0, duration_ms=10,
                                   success=False, error="timeout")],
        )

    def collect_late_results(self, query, wait_seconds=0):
        return []

    def progress_snapshot(self, query=None):
        return {"consecutive_empty_stalled_batches": 0}


def _universal_rescue(monkeypatch, wait_until_complete):
    import services.web_image.resolver as resolver

    scraper = _FailingScraper()
    monkeypatch.setattr(resolver, "_UNIVERSAL_SCRAPER", scraper)
    monkeypatch.setattr(resolver, "_universal_retry_max_attempts", lambda: 1)
    monkeypatch.setattr(resolver, "_universal_rescue_max_attempts", lambda: 2)
    monkeypatch.setattr(resolver, "_universal_rescue_deadline_seconds", lambda: 0.0)
    monkeypatch.setattr(resolver, "_universal_rescue_backoff_seconds", lambda n: 0.0)
    monkeypatch.setattr(resolver, "_universal_retry_backoff_seconds", lambda: 0.0)
    monkeypatch.setenv("VIDZEO_UNIVERSAL_LATE_WAIT_SECONDS", "0")
    rows = resolver._discover_universal_urls(
        "Grand Theft Auto VI street scene",
        wait_until_complete=wait_until_complete)
    return rows, resolver._UNIVERSAL_LAST_AUDIT["rescue_attempts"]


def test_universal_rescue_ignores_deadline_in_wait_mode(monkeypatch):
    """Rescue runs its full attempt cap even with a 0s rescue deadline.

    All engines fail transiently; the rescue deadline is 0s. On the
    pre-round-13 tree the rescue loop never ran (0 attempts) because
    the clock was already past the deadline; with the fix the attempt
    cap — not the clock — is the bound in wait mode.
    """
    rows, rescue_attempts = _universal_rescue(monkeypatch, wait_until_complete=True)
    assert rows == []
    assert rescue_attempts == 2, (
        f"wait-mode rescue must run its 2-attempt cap, ran {rescue_attempts}")


def test_universal_rescue_without_wait_respects_deadline(monkeypatch):
    """Control: without wait mode a 0s rescue deadline means 0 attempts."""
    rows, rescue_attempts = _universal_rescue(monkeypatch, wait_until_complete=False)
    assert rows == []
    assert rescue_attempts == 0
