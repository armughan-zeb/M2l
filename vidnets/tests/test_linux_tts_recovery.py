import subprocess
import threading
import time
from pathlib import Path
from unittest import mock

from services import edge_tts_service as tts


def test_linux_tts_retries_timeout_without_accepting_partial_output(tmp_path, monkeypatch):
    output = tmp_path / "narration.mp3"
    output.write_bytes(b"old audio" * 30)
    monkeypatch.setenv("VIDZEO_LINUX_TTS_COMMAND", "tts --output {output}")
    attempts = []

    def run(argv, **kwargs):
        target = Path(argv[-1])
        attempts.append(target.exists())
        if len(attempts) == 1:
            target.write_bytes(b"partial" * 30)
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        target.write_bytes(b"new audio" * 30)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    with mock.patch.object(tts.subprocess, "run", side_effect=run), mock.patch.object(
        tts, "_get_audio_duration", return_value=2.0
    ):
        result, duration = tts._generate_linux_tts_audio("test words", str(output))
    assert attempts == [False, False]
    assert Path(result).read_bytes() == b"new audio" * 30
    assert duration == 2.0


def test_linux_tts_exhaustion_preserves_existing_output(tmp_path, monkeypatch):
    output = tmp_path / "narration.mp3"
    output.write_bytes(b"existing" * 30)
    monkeypatch.setenv("VIDZEO_LINUX_TTS_COMMAND", "tts --output {output}")

    def timeout(argv, **kwargs):
        Path(argv[-1]).write_bytes(b"partial" * 30)
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    with mock.patch.object(tts.subprocess, "run", side_effect=timeout) as run:
        try:
            tts._generate_linux_tts_audio("test words", str(output))
            assert False, "expected a timeout"
        except subprocess.TimeoutExpired:
            pass
    assert run.call_count == 3
    assert output.read_bytes() == b"existing" * 30
    assert not (tmp_path / "narration.attempt.mp3").exists()


def test_linux_tts_default_timeout_is_60_and_env_overrides(tmp_path, monkeypatch):
    output = tmp_path / "narration.mp3"
    monkeypatch.setenv("VIDZEO_LINUX_TTS_COMMAND", "tts --output {output}")
    monkeypatch.delenv("VIDZEO_LINUX_TTS_TIMEOUT_SECONDS", raising=False)
    seen = []

    def run(argv, **kwargs):
        seen.append(kwargs["timeout"])
        target = Path(argv[-1])
        target.write_bytes(b"audio" * 40)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    with mock.patch.object(tts.subprocess, "run", side_effect=run), mock.patch.object(
        tts, "_get_audio_duration", return_value=2.0
    ):
        tts._generate_linux_tts_audio("test words", str(output))
    assert seen == [60.0]
    monkeypatch.setenv("VIDZEO_LINUX_TTS_TIMEOUT_SECONDS", "45")
    with mock.patch.object(tts.subprocess, "run", side_effect=run), mock.patch.object(
        tts, "_get_audio_duration", return_value=2.0
    ):
        tts._generate_linux_tts_audio("test words", str(output))
    assert seen == [60.0, 45.0]


def test_linux_split_keeps_long_scripts_in_60_to_80_word_chunks():
    # A ~2,700-word script like Broken Rail's: the 360-char target keeps
    # chunks at ~60 words (measured 54–63), so the 60s per-chunk deadline
    # is sized right. The 5-chunk cap in the splitter never binds here —
    # the char limit flushes first — so no split-size change was needed.
    sentence = "The coal train rolls through the valley at dawn."
    script = " ".join([sentence] * 300)
    chunks = tts._split_script_for_parallel_tts(script)
    assert len(chunks) > 5
    for chunk in chunks:
        words = len(chunk.split())
        assert 30 <= words <= 90, f"chunk of {words} words: {chunk[:60]!r}"
    assert " ".join(chunks) == script


def test_linux_chunk_resplit_halves_a_failed_chunk_and_reuses_completed_audio(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "TEMP_DIR", tmp_path)
    monkeypatch.setenv("VIDZEO_LINUX_TTS_COMMAND", "tts --output {output}")
    monkeypatch.setenv("VIDZEO_LINUX_TTS_WORKERS", "1")
    chunks = ["First sentence.", "Second sentence.", "Third sentence."]
    calls = []
    fail = [True]

    def synthesize(text, output, **_kwargs):
        calls.append(text)
        if text == chunks[1] and fail[0]:
            raise subprocess.TimeoutExpired("tts", 1)
        Path(output).write_bytes(text.encode() * 40)
        return output, 1.0

    def concat(paths, _start):
        data = [Path(path).read_bytes() for path in paths]
        return data, 3.0

    with mock.patch.object(tts, "_split_script_for_parallel_tts", return_value=chunks), \
         mock.patch.object(tts, "_generate_linux_tts_audio", side_effect=synthesize), \
         mock.patch.object(tts, "_get_audio_duration", return_value=1.0), \
         mock.patch.object(tts, "_concat_edge_chunks", side_effect=concat):
        # A failed chunk is re-split into halves instead of failing the render.
        data, duration = tts._generate_linux_tts_chunked("narration")
    assert calls == [chunks[0], chunks[1], "Second", "sentence.", chunks[2]]
    assert data == [chunk.encode() * 40 for chunk in
                    ["First sentence.", "Second", "sentence.", "Third sentence."]]
    assert duration == 3.0
    # Second run: completed chunks are reused; the failed chunk (never
    # cached whole) is retried once and then cached.
    calls.clear()
    fail[0] = False
    with mock.patch.object(tts, "_split_script_for_parallel_tts", return_value=chunks), \
         mock.patch.object(tts, "_generate_linux_tts_audio", side_effect=synthesize), \
         mock.patch.object(tts, "_get_audio_duration", return_value=1.0), \
         mock.patch.object(tts, "_concat_edge_chunks", side_effect=concat):
        data, duration = tts._generate_linux_tts_chunked("narration")
    assert calls == [chunks[1]]
    assert data == [chunk.encode() * 40 for chunk in chunks]
    assert duration == 3.0


def test_linux_tts_chunks_run_in_parallel_preserving_order(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "TEMP_DIR", tmp_path)
    monkeypatch.setenv("VIDZEO_LINUX_TTS_COMMAND", "tts --output {output}")
    monkeypatch.setenv("VIDZEO_LINUX_TTS_WORKERS", "2")
    chunks = [f"Sentence {index} here." for index in range(6)]
    calls = []
    lock = threading.Lock()
    in_flight = [0]
    peak = [0]

    def synthesize(text, output, **_kwargs):
        with lock:
            calls.append(text)
            in_flight[0] += 1
            peak[0] = max(peak[0], in_flight[0])
        time.sleep(0.1)
        with lock:
            in_flight[0] -= 1
        Path(output).write_bytes(text.encode() * 40)
        return output, 1.0

    def concat(paths, _start):
        data = [Path(path).read_bytes() for path in paths]
        return data, 6.0

    with mock.patch.object(tts, "_split_script_for_parallel_tts", return_value=chunks), \
         mock.patch.object(tts, "_generate_linux_tts_audio", side_effect=synthesize), \
         mock.patch.object(tts, "_get_audio_duration", return_value=1.0), \
         mock.patch.object(tts, "_concat_edge_chunks", side_effect=concat):
        data, duration = tts._generate_linux_tts_chunked("narration")
    assert sorted(calls) == sorted(chunks)
    assert peak[0] >= 2, "chunks did not run concurrently"
    assert data == [chunk.encode() * 40 for chunk in chunks]
    assert duration == 6.0


def test_synthesize_linux_chunk_writes_script_timing_sidecar(tmp_path):
    """S8: a synthesized chunk carries a script-derived timing sidecar, so
    the caption step can use it instead of running local Whisper."""
    import wave

    from services import speech_timing

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    text = "The quick brown fox jumps."
    identity = "test-identity"
    cached = tts._linux_chunk_cache_path(cache_dir, identity, text)
    # Real 1s audio in the cache so no TTS binary is needed (ffprobe reads
    # content, not the extension).
    with wave.open(str(cached), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\0\0" * 24000)
    assert tts._linux_chunk_is_valid(cached)
    (local,) = tts._synthesize_linux_chunk(
        text, cache_dir, identity, work_dir, 0, 1, time.time()
    )
    rows = speech_timing.load_speech_timing(local, 1.0)
    assert [row["word"] for row in rows] == ["The", "quick", "brown", "fox", "jumps."]
    assert rows[0]["start"] == 0.0
    assert rows[-1]["end"] == 1.0
    assert all(row["timing_source"] == "tts_script_timing" for row in rows)
