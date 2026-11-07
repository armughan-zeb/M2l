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
