"""Server-speed fixes: parallel Linux TTS narration + non-blocking queue status.

Priority 1 — narration used to split a script into ~56 x 360-char chunks
and synthesize them 2-at-a-time (~280s for a 2,711-word script while the
job sat at 10%). It now uses a few large balanced chunks at 4 workers and
reports per-chunk progress.

Priority 2 — /api/queue/status built its payload (deepcopy + redact of
~38 MB of completed-job manifests, ~28s) while holding the queue lock,
so a curl timed out at 10s. It now serves a cached snapshot when the
lock is busy and builds payloads outside the lock.
"""

import json
import math
import os
import sys
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from services import edge_tts_service as tts
from services import queue_manager as queue_module
from services.audio_first_pipeline import _run_tts
from services.queue_manager import QueueManager
from services.speech_timing import load_speech_timing, save_speech_timing, timing_path


# =========================================================================
# Priority 1 — Linux TTS chunking / parallelism / progress
# =========================================================================

def _script(sentences=220, words_per_sentence=12):
    """A ~2,700-word script: 220 sentences of 12 words (~16k chars)."""
    parts = []
    for i in range(sentences):
        words = [f"word{i}_{j}" for j in range(words_per_sentence)]
        parts.append(" ".join(words) + ".")
    return " ".join(parts)


def test_linux_splitter_makes_few_balanced_chunks():
    script = _script()
    assert len(script.split()) == 220 * 12
    chunks = tts._split_script_for_parallel_tts(
        script,
        target_chunk_chars=tts._LINUX_TTS_TARGET_CHUNK_CHARS,
        max_chunks=tts._LINUX_TTS_MAX_CHUNKS,
    )
    # Chunk count scales with length: ceil(total / target), not a fixed 8.
    # The last chunk absorbs a small remainder, so the count can land one
    # below the target; it must never exceed it or the 32 cap.
    expected = -(-len(script) // tts._LINUX_TTS_TARGET_CHUNK_CHARS)
    assert expected - 1 <= len(chunks) <= expected, (
        f"{len(script)} chars -> {len(chunks)} chunks, "
        f"expected {expected} or one fewer")
    assert 1 <= len(chunks) <= tts._LINUX_TTS_MAX_CHUNKS
    sizes = [len(chunk) for chunk in chunks]
    mean = sum(sizes) / len(sizes)
    assert max(sizes) <= 1.6 * mean, f"unbalanced chunks: {sizes}"
    # Sentences preserved, in order, nothing dropped.
    assert " ".join(chunks) == script


def test_linux_splitter_scales_chunk_count_with_script_length():
    # Long jobs must not hit a hidden ceiling: a script that used to be
    # 70+ x 360-char chunks becomes ~2,000-char chunks, each bounded
    # in size so no single chunk can blow the per-attempt TTS timeout.
    counts = []
    for sentences in (60, 120, 220, 440, 540):
        script = _script(sentences=sentences)
        chunks = tts._split_script_for_parallel_tts(
            script,
            target_chunk_chars=tts._LINUX_TTS_TARGET_CHUNK_CHARS,
            max_chunks=tts._LINUX_TTS_MAX_CHUNKS,
        )
        # Balanced splitting targets ceil(total/1600) chunks but the last
        # chunk absorbs a small remainder, so the count can land one
        # below the target; it must never exceed it or the 32 cap.
        expected = min(
            tts._LINUX_TTS_MAX_CHUNKS,
            -(-len(script) // tts._LINUX_TTS_TARGET_CHUNK_CHARS),
        )
        assert expected - 1 <= len(chunks) <= expected, (
            f"{sentences} sentences ({len(script)} chars) -> "
            f"{len(chunks)} chunks, expected {expected} or one fewer")
        assert max(len(chunk) for chunk in chunks) <= 2200, (
            f"chunk too large for {sentences} sentences: "
            f"{[len(c) for c in chunks]}")
        assert " ".join(chunks) == script
        counts.append(len(chunks))
    assert counts == sorted(counts) and len(set(counts)) == len(counts), (
        f"chunk count must grow with script length: {counts}")
    # The longest script saturates the cap (or lands one below it when
    # the last chunk absorbs the remainder).
    assert counts[-1] >= tts._LINUX_TTS_MAX_CHUNKS - 1


def test_linux_splitter_short_script_is_one_chunk():
    chunks = tts._split_script_for_parallel_tts(
        "One short sentence here.",
        target_chunk_chars=tts._LINUX_TTS_TARGET_CHUNK_CHARS,
        max_chunks=tts._LINUX_TTS_MAX_CHUNKS,
    )
    assert chunks == ["One short sentence here."]


def test_default_splitter_still_small_chunks_for_edge():
    # The Edge (fallback) lane keeps its old ~360-char chunks: the Linux
    # balanced split must not change fallback behaviour.
    chunks = tts._split_script_for_parallel_tts(_script())
    assert len(chunks) > tts._LINUX_TTS_MAX_CHUNKS
    assert max(len(chunk) for chunk in chunks) <= 400


def test_linux_tts_workers_default_is_five(monkeypatch):
    # Spec §4.1: 5 parallel TTS workers (was 4).
    monkeypatch.delenv("VIDZEO_LINUX_TTS_WORKERS", raising=False)
    assert tts._linux_tts_workers() == 5
    monkeypatch.setenv("VIDZEO_LINUX_TTS_WORKERS", "2")
    assert tts._linux_tts_workers() == 2
    monkeypatch.setenv("VIDZEO_LINUX_TTS_WORKERS", "99")
    assert tts._linux_tts_workers() == 5
    monkeypatch.setenv("VIDZEO_LINUX_TTS_WORKERS", "0")
    assert tts._linux_tts_workers() == 1


def test_linux_chunked_reports_progress_and_preserves_order(tmp_path, monkeypatch):
    # Fake synthesis: later chunks finish first (sleep inversely to index).
    monkeypatch.setattr(tts, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(tts, "linux_tts_config",
                        lambda: (tts._MUSE_TTS_COMMAND_TEMPLATE, "voice", "100"))
    monkeypatch.setenv("VIDZEO_LINUX_TTS_WORKERS", "4")

    def fake_synthesize(text, cache_dir, identity, work_dir, index, total,
                        chunk_start, *, sub=None, allow_split=True):
        time.sleep(0.05 * (total - index))
        return [f"part-{index}.mp3"]

    captured = {}
    monkeypatch.setattr(tts, "_synthesize_linux_chunk", fake_synthesize)

    def fake_concat(paths, start):
        captured["paths"] = list(paths)
        return "final.mp3", 3.0
    monkeypatch.setattr(tts, "_concat_edge_chunks", fake_concat)

    progress = []
    script = _script(sentences=60, words_per_sentence=12)
    expected_chunks = len(tts._split_script_for_parallel_tts(
        script,
        target_chunk_chars=tts._LINUX_TTS_TARGET_CHUNK_CHARS,
        max_chunks=tts._LINUX_TTS_MAX_CHUNKS,
    ))
    assert expected_chunks > 1
    path, duration = tts._generate_linux_tts_chunked(
        script,
        progress_callback=lambda frac, msg: progress.append((frac, msg)),
    )
    assert path == "final.mp3" and duration == 3.0
    assert captured["paths"] == [f"part-{i}.mp3" for i in range(expected_chunks)]
    fractions = [frac for frac, _ in progress]
    assert fractions == sorted(fractions), f"progress not monotonic: {fractions}"
    assert fractions[-1] == pytest.approx(1.0)
    assert len(fractions) == expected_chunks
    assert all("chunk" in msg for _, msg in progress)


def _write_tone(path, seconds, rate=24000):
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        samples = bytearray()
        for i in range(frames):
            value = int(12000 * math.sin(2 * math.pi * 440 * i / rate))
            samples += value.to_bytes(2, "little", signed=True)
        wav.writeframes(bytes(samples))


def test_concat_parallel_decode_keeps_chunk_order_and_timings(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "TEMP_DIR", tmp_path)
    durations = [0.3, 0.6, 0.9]
    chunk_paths = []
    for index, seconds in enumerate(durations):
        chunk = tmp_path / f"chunk{index}.wav"
        _write_tone(chunk, seconds)
        rows = [
            {"word": f"w{index}a", "start": 0.0, "end": seconds / 2},
            {"word": f"w{index}b", "start": seconds / 2, "end": seconds},
        ]
        save_speech_timing(str(chunk), rows, source="tts_script_timing")
        chunk_paths.append(str(chunk))
    output, duration = tts._concat_edge_chunks(chunk_paths, time.time())
    assert Path(output).is_file()
    assert duration == pytest.approx(sum(durations), abs=0.15)
    merged = load_speech_timing(output, duration)
    assert [row["word"] for row in merged] == ["w0a", "w0b", "w1a", "w1b", "w2a", "w2b"]
    starts = [row["start"] for row in merged]
    assert starts == sorted(starts)
    assert starts[2] == pytest.approx(0.3, abs=0.05)
    assert starts[4] == pytest.approx(0.9, abs=0.05)


def test_concat_fails_clearly_when_disk_full(tmp_path, monkeypatch):
    # ffmpeg exits 228 on ENOSPC with no useful message in the old code;
    # the preflight must name the cause (disk space) instead.
    import types
    monkeypatch.setattr(tts, "TEMP_DIR", tmp_path)
    chunk_paths = []
    for index in range(2):
        chunk = tmp_path / f"chunk{index}.wav"
        _write_tone(chunk, 0.3)
        save_speech_timing(str(chunk), [
            {"word": f"w{index}", "start": 0.0, "end": 0.3},
        ], source="tts_script_timing")
        chunk_paths.append(str(chunk))
    monkeypatch.setattr(
        tts.shutil, "disk_usage",
        lambda path: types.SimpleNamespace(total=10 ** 9, used=10 ** 9, free=1024),
    )
    with pytest.raises(RuntimeError, match="not enough disk space"):
        tts._concat_edge_chunks(chunk_paths, time.time())


def test_run_tts_reports_chunk_progress(monkeypatch):
    script = "word " * 200  # long enough for the chunked path
    seen = []

    def fake_chunked(script_text, voice, max_workers=5, edge_options=None,
                     progress_callback=None):
        assert progress_callback is not None
        progress_callback(0.5, "Generating voice over... chunk 1/2")
        progress_callback(1.0, "Generating voice over... chunk 2/2")
        return "narration.mp3", 12.0

    monkeypatch.setattr(tts, "generate_audio_chunked", fake_chunked)
    path, duration = _run_tts(
        script, "Brian (Male US)", tts_model="edge",
        progress_callback=lambda frac, msg: seen.append((frac, msg)),
    )
    assert (path, duration) == ("narration.mp3", 12.0)
    assert [frac for frac, _ in seen] == [0.5, 1.0]


# =========================================================================
# Priority 2 — queue status never blocks on the lock
# =========================================================================

def _manager(tmp_path):
    QueueManager._instance = None
    original = queue_module.QUEUE_FILE
    queue_module.QUEUE_FILE = tmp_path / "queue.json"
    return QueueManager(), original


def _restore(original):
    QueueManager._instance = None
    queue_module.QUEUE_FILE = original


def _big_item(item_id, manifest_kb=1024):
    return {
        "id": item_id,
        "status": "completed",
        "progress": 100,
        "progress_text": "Completed",
        "config": {"title": f"Video {item_id}", "auth_user_id": None,
                   "openrouter_api_key": "sk-secret-should-not-leak"},
        "result": {"component_manifest": {
            "media_shot_plan": "y" * (manifest_kb * 1024),
            "untrimmed_blob": "z" * (manifest_kb * 1024),
        }},
    }


def test_get_status_serves_cached_snapshot_when_lock_busy(tmp_path):
    manager, original = _manager(tmp_path)
    try:
        manager.queue = [_big_item("job-a", 64), _big_item("job-b", 64)]
        first = manager.get_status()
        assert [item["id"] for item in first["items"]] == ["job-a", "job-b"]
        # Secrets are redacted in the served payload, not in storage.
        assert first["items"][0]["config"]["openrouter_api_key"] == queue_module._REDACTED_SECRET
        assert manager.queue[0]["config"]["openrouter_api_key"].startswith("sk-secret")

        acquired = manager._queue_lock.acquire(timeout=2.0)
        assert acquired
        try:
            # Age the cache past its TTL so the endpoint must go to the
            # lock (which this thread holds) and fall back to the stale
            # snapshot instead of blocking.
            manager._STATUS_CACHE_TTL_FULL_SECONDS = 0.0
            with ThreadPoolExecutor(max_workers=1) as pool:
                started = time.monotonic()
                future = pool.submit(manager.get_status)
                payload = future.result(timeout=5.0)
                elapsed = time.monotonic() - started
            assert elapsed < 1.0, f"status blocked {elapsed:.2f}s on the lock"
            assert payload.get("snapshot_stale") is True
            assert [item["id"] for item in payload["items"]] == ["job-a", "job-b"]
        finally:
            manager._queue_lock.release()
    finally:
        _restore(original)


def test_get_status_returns_quickly_without_cache_when_lock_busy(tmp_path):
    manager, original = _manager(tmp_path)
    try:
        manager.queue = [_big_item("job-a", 64)]
        assert manager._status_cache == {}
        acquired = manager._queue_lock.acquire(timeout=2.0)
        assert acquired
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                started = time.monotonic()
                payload = pool.submit(manager.get_status).result(timeout=5.0)
                elapsed = time.monotonic() - started
            assert elapsed < 1.0, f"status blocked {elapsed:.2f}s on the lock"
            assert payload.get("snapshot_stale") is True
            assert payload.get("snapshot_empty") is True
            assert payload["items"] == []
        finally:
            manager._queue_lock.release()
    finally:
        _restore(original)


def test_get_status_full_payload_shape_and_freshness(tmp_path):
    manager, original = _manager(tmp_path)
    try:
        manager.queue = [_big_item("job-a", 16)]
        payload = manager.get_status()
        assert set(payload) >= {"items", "worker_running", "worker_count",
                                "active_jobs", "active_job_state"}
        assert payload["worker_count"] >= 1
        assert payload["items"][0]["id"] == "job-a"
        # Second call inside the TTL is served from cache.
        again = manager.get_status()
        assert again["items"][0]["id"] == "job-a"
        # Compact mode is a different cache entry and trims the manifest.
        compact = manager.get_status(compact=True)
        assert compact["items"][0]["id"] == "job-a"
        compact_result = json.dumps(compact["items"][0].get("result") or {})
        assert "untrimmed_blob" not in compact_result
        full_result = json.dumps(payload["items"][0].get("result") or {})
        assert "untrimmed_blob" in full_result
    finally:
        _restore(original)


def test_get_status_rebuilds_when_queue_structure_changes(tmp_path):
    # Progress may lag up to the cache TTL, but an item being removed or
    # changing status must be visible on the next poll, not hidden behind
    # a fresh cache (regression: clear_all left a cancelled job visible).
    manager, original = _manager(tmp_path)
    try:
        manager.queue = [_big_item("job-a", 16)]
        first = manager.get_status()
        assert [item["id"] for item in first["items"]] == ["job-a"]
        manager.queue = []
        second = manager.get_status()
        assert second["items"] == []
        assert second.get("snapshot_stale") is not True
        manager.queue = [_big_item("job-b", 16)]
        third = manager.get_status()
        assert [item["id"] for item in third["items"]] == ["job-b"]
        manager.queue[0]["status"] = "processing"
        fourth = manager.get_status()
        assert fourth["items"][0]["status"] == "processing"
    finally:
        _restore(original)


def test_get_status_stays_fast_during_slow_persistence(tmp_path, monkeypatch):
    # The reported symptom: a progress save (deepcopy + redact + dump of
    # ~38 MB) held the queue lock, so /api/queue/status timed out. With
    # the fix the save only holds the lock for a shallow snapshot.
    manager, original = _manager(tmp_path)
    writing = threading.Event()
    release = threading.Event()
    real_dump = queue_module.json.dump
    writer = None
    try:
        manager.queue = [_big_item(f"job-{i}", 128) for i in range(6)]
        # Warm the status cache so the read during the save is served
        # from the snapshot (the endpoint's fast path under load).
        assert len(manager.get_status()["items"]) == 6

        def slow_dump(*args, **kwargs):
            writing.set()
            assert release.wait(10.0)
            return real_dump(*args, **kwargs)

        monkeypatch.setattr(queue_module.json, "dump", slow_dump)
        writer = threading.Thread(target=manager._save_queue)
        writer.start()
        assert writing.wait(10.0)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                started = time.monotonic()
                payload = pool.submit(manager.get_status).result(timeout=5.0)
                elapsed = time.monotonic() - started
            assert elapsed < 1.0, f"status blocked {elapsed:.2f}s during save"
            assert len(payload["items"]) == 6
        finally:
            release.set()
        writer.join(10.0)
        assert not writer.is_alive()
    finally:
        _restore(original)


def test_save_queue_releases_lock_quickly_for_big_history(tmp_path, monkeypatch):
    # A save of multi-MB history must not hold _queue_lock for the whole
    # deepcopy: the status endpoint must be able to grab it mid-save.
    # The deepcopy is hooked so the test only probes the lock once the
    # save is provably inside it.
    manager, original = _manager(tmp_path)
    try:
        manager.queue = [_big_item(f"job-{i}", 1024) for i in range(12)]
        real_deepcopy = queue_module.copy.deepcopy
        deepcopy_started = threading.Event()

        def slow_deepcopy(value, *args, **kwargs):
            deepcopy_started.set()
            return real_deepcopy(value, *args, **kwargs)

        monkeypatch.setattr(queue_module.copy, "deepcopy", slow_deepcopy)
        done = threading.Event()
        writer = threading.Thread(
            target=lambda: (manager._save_queue(), done.set()))
        writer.start()
        try:
            assert deepcopy_started.wait(10.0), "save never reached deepcopy"
            # The deepcopy of ~12 MB takes seconds; the lock must be free
            # while it runs (old code held it for the whole copy).
            acquired = manager._queue_lock.acquire(timeout=2.0)
            assert acquired, "queue lock still held during deepcopy"
            manager._queue_lock.release()
        finally:
            writer.join(20.0)
            assert not writer.is_alive()
        assert (tmp_path / "queue.json").is_file()
        saved = json.loads((tmp_path / "queue.json").read_text(encoding="utf-8"))
        assert len(saved["items"]) == 12
    finally:
        _restore(original)
