import subprocess
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
