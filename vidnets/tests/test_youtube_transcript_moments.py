import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from services.youtube_moment_matcher import match_pool_moments, match_scene_moments
from services.youtube_transcript_index import (
    TranscriptDocument,
    YouTubeTranscriptIndex,
    normalize_segments,
)


def test_source_id_normalizes_youtube_urls():
    from services.youtube_source_pool import normalize_source_id, source_id

    assert normalize_source_id("https://youtu.be/abc123XYZ_1?t=30") == "abc123XYZ_1"
    assert normalize_source_id("https://www.youtube.com/watch?v=abc123XYZ_1") == "abc123XYZ_1"
    assert normalize_source_id("https://youtube.com/shorts/abc123XYZ_1") == "abc123XYZ_1"
    assert source_id({"webpage_url": "https://www.youtube.com/embed/abc123XYZ_1"}) == "abc123XYZ_1"


def test_source_pool_deduplicates_same_video_across_pools():
    from services.youtube_source_pool import build_sources

    sources = build_sources({
        "pool-a": [{"id": "same-video", "title": "A", "score": 10}],
        "pool-b": [{"id": "same-video", "title": "A mirror", "score": 8}],
    })
    assert list(sources) == ["same-video"]
    assert sources["same-video"].pool_keys == ["pool-a", "pool-b"]


def test_source_pool_never_reintroduces_captioned_or_slideshow_candidates():
    from services.youtube_source_pool import build_sources

    sources = build_sources({
        "captions": [{
            "id": "captioned-video",
            "title": "Titanic archival footage with English subtitles",
            "duration": 300,
        }],
        "slides": [{
            "id": "slideshow-video",
            "title": "Titanic photo slideshow and text cards",
            "duration": 300,
        }],
    })
    assert sources == {}


def test_normalize_segments_sorts_and_merges_duplicate_overlap():
    segments = normalize_segments([
        {"start": 3, "duration": 1, "text": "Titanic sank"},
        {"start": 1, "duration": 1, "text": "Departure"},
        {"start": 3.2, "duration": 1, "text": "Titanic sank"},
    ])
    assert [segment.start for segment in segments] == [1.0, 3.0]
    assert segments[1].end == 4.2


def test_transcript_index_caches_provider_result(tmp_path):
    calls = []

    def provider(video_id):
        calls.append(video_id)
        return {"source": "test", "segments": [{"start": 2, "duration": 3, "text": "Titanic departure"}]}

    index = YouTubeTranscriptIndex(str(tmp_path / "transcripts.sqlite3"), provider=provider)
    first = index.fetch("video-1")
    second = index.fetch("video-1")
    assert first.available and second.available
    assert calls == ["video-1"]


def test_transcript_index_single_flight_fetches_once(tmp_path):
    calls = []
    started = threading.Event()

    def provider(video_id):
        calls.append(video_id)
        started.set()
        time.sleep(0.05)
        return {"segments": [{"start": 1, "duration": 3, "text": "Lifeboat rescue"}]}

    index = YouTubeTranscriptIndex(str(tmp_path / "transcripts.sqlite3"), provider=provider)
    threads = [threading.Thread(target=index.fetch, args=("video-2",)) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert started.is_set()
    assert calls == ["video-2"]


def test_transcript_batch_deadline_retains_fast_result_without_waiting_for_stall(tmp_path):
    release_slow = threading.Event()
    slow_finished = threading.Event()

    def provider(video_id):
        if video_id == "slow-video":
            release_slow.wait(timeout=5)
            slow_finished.set()
        return {
            "segments": [{
                "start": 1,
                "duration": 3,
                "text": f"RMS Titanic archive {video_id}",
            }],
        }

    index = YouTubeTranscriptIndex(
        str(tmp_path / "deadline-transcripts.sqlite3"),
        provider=provider,
    )
    index.put(TranscriptDocument(
        video_id="fast-video",
        status="ready",
        segments=normalize_segments([{
            "start": 1,
            "duration": 3,
            "text": "RMS Titanic fast cached transcript",
        }]),
        fetched_at=time.time(),
    ))
    started = time.monotonic()
    try:
        documents = index.fetch_many(
            ["fast-video", "slow-video"],
            workers=2,
            deadline_seconds=0.50,
        )
        elapsed = time.monotonic() - started
        assert elapsed < 1.50
        assert list(documents) == ["fast-video"]
        assert documents["fast-video"].available
    finally:
        release_slow.set()

    # The timed-out provider can complete its independent cache write safely;
    # fetch_many itself no longer blocks pool-first startup waiting for it.
    assert slow_finished.wait(timeout=2)


def test_transcript_batch_fetches_independent_sources_in_parallel(tmp_path):
    started = 0
    lock = threading.Lock()
    peak_parallel = 0
    active = 0

    def provider(video_id):
        nonlocal started, peak_parallel, active
        with lock:
            started += 1
            active += 1
            peak_parallel = max(peak_parallel, active)
        time.sleep(0.08)
        with lock:
            active -= 1
        return {"segments": [{"start": 0, "duration": 1, "text": video_id}]}

    index = YouTubeTranscriptIndex(str(tmp_path / "parallel-transcripts.sqlite3"), provider=provider)
    documents = index.fetch_many(
        [f"parallel-{index}" for index in range(4)],
        workers=4,
        deadline_seconds=1.0,
    )
    assert len(documents) == 4
    assert started == 4
    assert peak_parallel >= 2


def test_transcript_batch_completes_with_large_bounded_pool(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDZEO_YT_TRANSCRIPT_CONCURRENCY", "100")
    index = YouTubeTranscriptIndex(
        str(tmp_path / "bounded-parallel-transcripts.sqlite3"),
        provider=lambda video_id: {"segments": [{"start": 0, "duration": 1, "text": video_id}]},
    )
    documents = index.fetch_many(
        [f"bounded-{index}" for index in range(14)],
        deadline_seconds=2.0,
    )
    assert len(documents) == 14


def test_authenticated_session_retries_cached_transport_error(tmp_path):
    calls = []

    def provider(video_id):
        calls.append(video_id)
        return {"segments": [{"start": 0, "duration": 1, "text": "authenticated caption"}]}

    index = YouTubeTranscriptIndex(
        str(tmp_path / "auth-retry.sqlite3"),
        provider=provider,
        session_jar=object(),
    )
    index.put(TranscriptDocument(
        video_id="video-auth-retry",
        status="error",
        error="RuntimeError: HTTP Error 429: Too Many Requests",
        fetched_at=time.time(),
    ))
    result = index.fetch_many(["video-auth-retry"], deadline_seconds=1.0)
    assert result["video-auth-retry"].available
    assert calls == ["video-auth-retry"]


def test_matcher_combines_adjacent_caption_segments():
    document = TranscriptDocument(
        video_id="window-video",
        status="ready",
        segments=normalize_segments([
            {"start": 10, "duration": 2, "text": "RMS Titanic passengers"},
            {"start": 12, "duration": 2, "text": "entered the lifeboats after impact"},
        ]),
    )
    moments = match_scene_moments(
        document,
        scene_index=0,
        subject="RMS Titanic",
        scene_text="Passengers entered the lifeboats after impact.",
        duration_needed=4,
        video_id="window-video",
    )
    assert moments
    assert moments[0].start == 10
    assert "lifeboats" in moments[0].transcript_text.lower()


def test_matcher_prefers_subject_phrase_and_rejects_negative_term():
    document = TranscriptDocument(
        video_id="video-3",
        status="ready",
        segments=normalize_segments([
            {"start": 10, "duration": 4, "text": "A reaction explains the Titanic sinking"},
            {"start": 80, "duration": 5, "text": "RMS Titanic lifeboats launch after the iceberg collision"},
        ]),
    )
    moments = match_scene_moments(
        document,
        scene_index=0,
        subject="RMS Titanic",
        scene_text="The lifeboats launch after the collision.",
        visual_facet="lifeboat maritime footage",
        negative_terms=["reaction"],
        duration_needed=4,
        video_id="video-3",
    )
    assert moments
    assert moments[0].start == 80
    assert "subject" in moments[0].match_reason


def test_pool_matcher_assigns_shared_sources_with_distinct_ranges():
    document = TranscriptDocument(
        video_id="video-4",
        status="ready",
        segments=normalize_segments([
            {"start": 20, "duration": 5, "text": "RMS Titanic departs Southampton"},
            {"start": 90, "duration": 5, "text": "RMS Titanic lifeboats launch"},
        ]),
    )
    result = match_pool_moments(
        {"source-4": document},
        [
            {"scene_index": 0, "subject": "RMS Titanic", "scene_text": "The ship departs", "duration": 4},
            {"scene_index": 1, "subject": "RMS Titanic", "scene_text": "Lifeboats launch", "duration": 4},
        ],
        source_metadata={"source-4": {"video_id": "video-4"}},
    )
    first = result[0][0]
    second = result[1][0]
    assert first.video_id == second.video_id == "video-4"
    assert first.start != second.start


# --- run-2 transcript failure regressions ---------------------------------
# Job 55f091bd kept 1/12 transcripts: the ranged-moments caller passed a
# 60 s deadline that the callee silently clamped to 25 s, and every
# per-video provider chain burned ~15 s on yt-dlp legs that could never
# succeed (storyboard-only player response, android carries no captions)
# before the API leg ran — then re-ran the same extraction as a fallback
# even after the API had answered definitively.


def _test_jar():
    import http.cookiejar

    jar = http.cookiejar.CookieJar()
    for name in ("SAPISID", "LOGIN_INFO"):
        jar.set_cookie(http.cookiejar.Cookie(
            version=0, name=name, value="test-value", port=None, port_specified=False,
            domain=".youtube.com", domain_specified=True, domain_initial_dot=True,
            path="/", path_specified=True, secure=True,
            expires=int(time.time()) + 3600, discard=False,
            comment=None, comment_url=None, rest={"HttpOnly": None}, rfc2109=False,
        ))
    return jar


class _StoryboardYDL:
    """Fake yt-dlp that mimics the no-JS-solver player response.

    Real yt-dlp raises "Requested format is not available" during
    extract_info when the only formats are storyboards — unless
    ignore_no_formats_error is set, in which case the info dict (with its
    caption tracks) is returned.
    """

    instances = []

    def __init__(self, opts):
        self.opts = opts
        self.cookiejar = None
        _StoryboardYDL.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, video_id, download=False):
        if not self.opts.get("ignore_no_formats_error"):
            raise RuntimeError(
                "Requested format is not available. "
                "Use --list-formats for a list of available formats"
            )
        return {
            "subtitles": {},
            "automatic_captions": {
                "en": [{"ext": "json3", "url": "https://example.test/captions"}]
            },
        }


class _StoryboardYtDlpModule:
    def YoutubeDL(self, opts):
        return _StoryboardYDL(opts)


class _CaptionResponse:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        import json

        payload = {"events": [
            {"tStartMs": 1000, "dDurationMs": 2000,
             "segs": [{"utf8": "Panama Canal crossing "}]},
        ]}
        return json.dumps(payload).encode("utf-8")


class _CaptionOpener:
    def open(self, url, timeout=None):
        return _CaptionResponse()


def test_transcript_ytdlp_returns_captions_despite_storyboard_only_formats(tmp_path):
    from unittest import mock

    from services import youtube_transcript_index as transcript_index

    _StoryboardYDL.instances.clear()
    index = YouTubeTranscriptIndex(
        str(tmp_path / "storyboard-transcripts.sqlite3"), session_jar=_test_jar()
    )
    with mock.patch(
        "services.yt_dlp_runtime.load_yt_dlp", return_value=_StoryboardYtDlpModule()
    ), mock.patch(
        "youtube_transcript_api.YouTubeTranscriptApi",
        side_effect=RuntimeError("api down"),
    ), mock.patch.object(
        transcript_index, "_cookie_opener", return_value=_CaptionOpener()
    ):
        document = index.fetch("dQw4w9WgXcQ")
    assert document.available
    assert document.source == "yt-dlp-authenticated"
    assert document.segments and "Panama Canal" in document.segments[0].text
    assert _StoryboardYDL.instances
    assert _StoryboardYDL.instances[0].opts["ignore_no_formats_error"] is True
    # ``web`` is tried first (it is faster live and never takes yt-dlp's
    # embedded-ytcfg path); ``web_embedded`` remains the fallback client
    # for videos whose ``web`` response carries no caption tracks.
    assert _StoryboardYDL.instances[0].opts["extractor_args"]["youtube"]["player_client"] == ["web"]


class _FailingYDL:
    instances = []

    def __init__(self, opts):
        self.opts = opts
        self.cookiejar = None
        _FailingYDL.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, video_id, download=False):
        raise RuntimeError("primary provider down")


class _FailingYtDlpModule:
    def YoutubeDL(self, opts):
        return _FailingYDL(opts)


class _FallbackCountingYDL:
    instances = []

    def __init__(self, opts):
        self.opts = opts
        self.cookiejar = None
        _FallbackCountingYDL.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, video_id, download=False):
        return {
            "subtitles": {},
            "automatic_captions": {
                "en": [{"ext": "json3", "url": "https://example.test/captions"}]
            },
        }


class _FallbackCountingModule:
    def YoutubeDL(self, opts):
        return _FallbackCountingYDL(opts)


class TranscriptsDisabled(Exception):
    """Name-matched stand-in for youtube_transcript_api.TranscriptsDisabled.

    The provider classifies definitive answers by exception type name, so
    this exercises the exact production branch without importing the
    library's error surface in the test. The class name must match the
    library's exactly — that is the contract under test.
    """


def test_transcript_fallback_skipped_after_definitive_api_error(tmp_path):
    from unittest import mock

    from services import youtube_transcript_index as transcript_index

    _FailingYDL.instances.clear()
    _FallbackCountingYDL.instances.clear()
    index = YouTubeTranscriptIndex(
        str(tmp_path / "definitive-transcripts.sqlite3"), session_jar=_test_jar()
    )
    with mock.patch(
        "services.yt_dlp_runtime.load_yt_dlp", return_value=_FailingYtDlpModule()
    ), mock.patch(
        "youtube_transcript_api.YouTubeTranscriptApi",
        side_effect=TranscriptsDisabled("Subtitles are disabled for this video"),
    ), mock.patch.dict(
        sys.modules, {"yt_dlp": _FallbackCountingModule()}
    ), mock.patch.object(
        transcript_index, "_cookie_opener", return_value=_CaptionOpener()
    ):
        document = index.fetch("dQw4w9WgXcQ")
    assert not document.available
    assert document.status == "error"
    assert "TranscriptsDisabled" in document.error
    # Round 13: the auth and API legs now race, so a definitive API
    # answer can end the race before the auth leg's thread starts (the
    # pending auth future is cancelled) — the auth leg running is no
    # longer guaranteed, and it does not need to be: TranscriptsDisabled
    # is authoritative about the video itself, so no leg could succeed
    # after it. The contract that matters is the next assert: the
    # fallback must never re-run the same extraction after a definitive
    # no-transcript answer.
    assert not _FallbackCountingYDL.instances, (
        "fallback re-ran the same extraction after a definitive no-transcript answer"
    )


def test_transcript_fallback_still_runs_after_transport_failures(tmp_path):
    """The gating must not remove the retry the cookie-wiring test pins.

    Primary transport failure + API transport failure still reaches the
    yt-dlp fallback (with the session jar attached).
    """
    from unittest import mock

    from services import youtube_transcript_index as transcript_index

    _FailingYDL.instances.clear()
    _FallbackCountingYDL.instances.clear()
    index = YouTubeTranscriptIndex(
        str(tmp_path / "transport-transcripts.sqlite3"), session_jar=_test_jar()
    )
    with mock.patch(
        "services.yt_dlp_runtime.load_yt_dlp", return_value=_FailingYtDlpModule()
    ), mock.patch(
        "youtube_transcript_api.YouTubeTranscriptApi",
        side_effect=RuntimeError("api blocked"),
    ), mock.patch.dict(
        sys.modules, {"yt_dlp": _FallbackCountingModule()}
    ), mock.patch.object(
        transcript_index, "_cookie_opener", return_value=_CaptionOpener()
    ):
        document = index.fetch("dQw4w9WgXcQ")
    assert document.available
    assert _FallbackCountingYDL.instances, "fallback YoutubeDL was never built"
    assert _FallbackCountingYDL.instances[-1].cookiejar is not None


def test_transcript_api_client_bounds_requests_with_default_timeout():
    import requests
    from unittest import mock

    from services.youtube_transcript_index import _transcript_api_client

    client = _transcript_api_client(_test_jar())
    assert client is not None
    # Round 12 raised the transcript API timeout default 30s -> 60s
    # (YT per-call timeouts 15s -> 60s); the old 30.0 expectation is
    # stale. The bound itself is unchanged: one default timeout for
    # every request the client issues.
    assert client._vidzeo_default_timeout == 60.0
    with mock.patch.object(
        requests.Session, "request",
        return_value=mock.Mock(status_code=200, text=""),
    ) as fake_request:
        client.get("https://example.test/watch?v=dQw4w9WgXcQ")
    assert fake_request.call_args.kwargs.get("timeout") == 60.0


def test_ranged_moments_honors_caller_deadline():
    from services.youtube_ranged_moments import prepare_ranged_moments

    seen = {}

    class _FakeIndex:
        def fetch_many(self, video_ids, workers=None, deadline_seconds=None):
            seen["deadline_seconds"] = deadline_seconds
            seen["workers"] = workers
            return {}

    moments, audit = prepare_ranged_moments(
        [{"pool_key": "p1", "scene_text": "Cross the Panama Canal.",
          "candidate_subject": "Panama Canal", "section_subject": "Panama Canal",
          "scene_subject": "Panama Canal"}],
        [{"id": "dQw4w9WgXcQ"}],
        {},
        [4.0],
        deadline_seconds=60.0,
        index=_FakeIndex(),
    )
    assert moments == {}
    assert audit["sources_requested"] == 1
    assert seen["deadline_seconds"] == 60.0
    assert seen["workers"] == 6


# --- YT-lane speed fix (Nov 20, 2026): overlapped ranged transcripts ---
#
# Measured live (tests 12/14/15): the synchronous transcript phase is a hard
# barrier of ~130-175 s between "search complete" and the first download,
# most of it waiting on one slow/erroring transcript while scenes with
# verified timestamps waited too. The fixes below overlap that phase with
# the download wave and skip re-fetching transcripts for prep cuts whose
# moment is already verified.


def _prep_assignment(video_id, timestamp, scene):
    return {
        "id": video_id,
        "video_id": video_id,
        "prep_manifest_injected": True,
        "prep_best_timestamp": timestamp,
        "prep_live_scene": scene,
        "prep_cache_path": f"/tmp/prep/{video_id}.mp4",
        "best_timestamp": timestamp,
    }


def test_ranged_moments_skips_prep_injected_top_candidate():
    from services.youtube_ranged_moments import prepare_ranged_moments

    requested = []

    class _FakeIndex:
        def fetch_many(self, video_ids, workers=None, deadline_seconds=None):
            requested.extend(video_ids)
            return {}

    moments, audit = prepare_ranged_moments(
        [{"pool_key": "p1", "scene_text": "Vice City was back.",
          "candidate_subject": "Grand Theft Auto VI",
          "section_subject": "Grand Theft Auto VI",
          "scene_subject": "Grand Theft Auto VI"}],
        [_prep_assignment("c5qhgwwLY50", 167.99, 0)],
        {"p1": [{"id": "NrAZnO_QPhM", "video_id": "NrAZnO_QPhM"}]},
        [9.0],
        deadline_seconds=60.0,
        index=_FakeIndex(),
    )
    # The prep video's verified moment needs no fresh transcript; only the
    # reserve video (the fallback if the prep cut fails) is fetched.
    assert requested == ["NrAZnO_QPhM"]
    assert audit["sources_requested"] == 1
    assert audit["prep_moment_skipped"] == 1
    assert moments == {}


def test_ranged_moments_skips_own_prep_reserve_but_not_other_scene_prep():
    from services.youtube_ranged_moments import prepare_ranged_moments

    requested = []

    class _FakeIndex:
        def fetch_many(self, video_ids, workers=None, deadline_seconds=None):
            requested.extend(video_ids)
            return {}

    rows = [
        {"pool_key": "p0", "scene_text": "Vice City was back.",
         "candidate_subject": "Grand Theft Auto VI", "section_subject": "Grand Theft Auto VI",
         "scene_subject": "Grand Theft Auto VI"},
        {"pool_key": "p1", "scene_text": "Lucia and Jason run from the law.",
         "candidate_subject": "Grand Theft Auto VI", "section_subject": "Grand Theft Auto VI",
         "scene_subject": "Grand Theft Auto VI"},
    ]
    assignments = [
        _prep_assignment("c5qhgwwLY50", 167.99, 0),
        {"id": "Bhwj-_9Xz6M", "video_id": "Bhwj-_9Xz6M"},
    ]
    pools = {
        # Scene 0's only pool entry is its own prep cut: nothing to fetch.
        "p0": [_prep_assignment("c5qhgwwLY50", 167.99, 0)],
        # Scene 1's reserve is a prep cut mapped to scene 0: scene 1 has no
        # verified moment for it, so it stays eligible as scene 1's fallback.
        "p1": [_prep_assignment("TgtfearX668", 42.0, 0)],
    }
    moments, audit = prepare_ranged_moments(
        rows, assignments, pools, [9.0, 9.0],
        deadline_seconds=60.0, index=_FakeIndex(),
    )
    assert requested == ["Bhwj-_9Xz6M", "TgtfearX668"]
    assert audit["sources_requested"] == 2
    assert audit["prep_moment_skipped"] == 2


def test_ranged_moments_still_fetches_prep_candidate_without_timestamp():
    from services.youtube_ranged_moments import prepare_ranged_moments

    requested = []

    class _FakeIndex:
        def fetch_many(self, video_ids, workers=None, deadline_seconds=None):
            requested.extend(video_ids)
            return {}

    # prep_manifest_injected but no verified moment: the transcript is the
    # only way to place the cut, so it must still be fetched.
    moments, audit = prepare_ranged_moments(
        [{"pool_key": "p1", "scene_text": "Vice City was back.",
          "candidate_subject": "Grand Theft Auto VI",
          "section_subject": "Grand Theft Auto VI",
          "scene_subject": "Grand Theft Auto VI"}],
        [{"id": "c5qhgwwLY50", "video_id": "c5qhgwwLY50",
          "prep_manifest_injected": True}],
        {},
        [9.0],
        deadline_seconds=60.0,
        index=_FakeIndex(),
    )
    assert requested == ["c5qhgwwLY50"]
    assert audit["prep_moment_skipped"] == 0


def _fake_prepare_factory(release, moments, audit=None):
    def _fake_prepare(rows, assignments, pools, durations, *, session_jar=None,
                      deadline_seconds=None, cancellation=None):
        release.wait(timeout=30.0)
        return moments, dict(audit or {"status": "completed",
                                       "sources_requested": len(moments),
                                       "scenes_with_moments": len(moments)})
    return _fake_prepare


def test_ranged_overlap_scene_with_timestamp_does_not_wait():
    from services.youtube_pipeline_v2 import _RangedMomentOverlap

    release = threading.Event()
    overlap = _RangedMomentOverlap(
        _fake_prepare_factory(release, {1: [{"video_id": "Bhwj-_9Xz6M", "start": 10.0}]}),
        [{}], [{"best_timestamp": 167.99}, {"id": "Bhwj-_9Xz6M"}], {}, [9.0, 9.0],
        deadline_seconds=30.0)
    try:
        overlap.start()
        started = time.monotonic()
        # Scene 0 already has a provider timestamp: it must not wait for the
        # transcript phase that is still blocked on `release`.
        assert overlap.moments_for_scene(0) == []
        assert time.monotonic() - started < 2.0
        release.set()
        # Once transcripts land, the scene without a timestamp sees them.
        assert overlap.moments_for_scene(1) == [{"video_id": "Bhwj-_9Xz6M", "start": 10.0}]
        # The timestamped scene still has no moments of its own.
        assert overlap.moments_for_scene(0) == []
    finally:
        release.set()
        overlap.resolve()


def test_ranged_overlap_scene_without_timestamp_waits_for_moments():
    from services.youtube_pipeline_v2 import _RangedMomentOverlap

    release = threading.Event()
    overlap = _RangedMomentOverlap(
        _fake_prepare_factory(release, {0: [{"video_id": "Bhwj-_9Xz6M", "start": 10.0}]}),
        [{}], [{"id": "Bhwj-_9Xz6M"}], {}, [9.0],
        deadline_seconds=30.0)
    try:
        overlap.start()
        threading.Timer(0.5, release.set).start()
        started = time.monotonic()
        # No usable timestamp: the scene waits for transcripts, then gets
        # the moment instead of a heuristic timestamp.
        assert overlap.moments_for_scene(0) == [{"video_id": "Bhwj-_9Xz6M", "start": 10.0}]
        assert 0.4 < time.monotonic() - started < 10.0
    finally:
        release.set()
        overlap.resolve()


def test_ranged_overlap_resolve_never_blocks_on_slow_transcripts():
    from services.youtube_pipeline_v2 import _RangedMomentOverlap

    release = threading.Event()
    overlap = _RangedMomentOverlap(
        _fake_prepare_factory(release, {}),
        [{}], [{"best_timestamp": 5.0}], {}, [9.0],
        deadline_seconds=120.0)
    try:
        overlap.start()
        started = time.monotonic()
        moments, audit = overlap.resolve()
        assert time.monotonic() - started < 2.0
        assert moments == {}
        assert audit.get("partial") is True
    finally:
        release.set()


def test_ranged_overlap_failed_prepare_never_stalls_downloads():
    from services.youtube_pipeline_v2 import _RangedMomentOverlap

    def _boom(rows, assignments, pools, durations, **kwargs):
        raise RuntimeError("transcript provider down")

    overlap = _RangedMomentOverlap(
        _boom, [{}], [{"best_timestamp": 5.0}], {}, [9.0],
        deadline_seconds=30.0)
    overlap.start()
    assert overlap.moments_for_scene(0) == []
    moments, audit = overlap.resolve()
    assert audit["status"] == "unavailable"
    assert audit["error_type"] == "RuntimeError"


def test_assignment_usable_timestamp_predicate():
    from services.youtube_pipeline_v2 import _yt_assignment_has_usable_timestamp

    assert _yt_assignment_has_usable_timestamp({"best_timestamp": 167.99}) is True
    assert _yt_assignment_has_usable_timestamp({"prep_best_timestamp": 0.0}) is True
    assert _yt_assignment_has_usable_timestamp({"best_timestamp": "12.5"}) is True
    assert _yt_assignment_has_usable_timestamp({"best_timestamp": None}) is False
    assert _yt_assignment_has_usable_timestamp({"best_timestamp": "soon"}) is False
    assert _yt_assignment_has_usable_timestamp({"best_timestamp": -1}) is False
    assert _yt_assignment_has_usable_timestamp({}) is False
    assert _yt_assignment_has_usable_timestamp(None) is False
    assert _yt_assignment_has_usable_timestamp("c5qhgwwLY50") is False
