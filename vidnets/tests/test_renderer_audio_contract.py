"""Regression tests for the FFmpeg renderer's sample-accurate audio contract."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from server.services import semantic_ffmpeg_renderer as renderer


def _ffprobe_json(path: str) -> dict:
    result = subprocess.run(
        [
            renderer._ffprobe(),
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,sample_rate,channels,duration",
            "-of",
            "json",
            path,
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    return json.loads(result.stdout or "{}")


def _make_audio(ffmpeg: str, path: Path, duration: float) -> None:
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=44100:duration={duration}",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        check=True,
        timeout=20,
    )


def test_audio_mix_is_fixed_48k_stereo_and_pads_without_speed_change(tmp_path):
    ffmpeg = renderer._ffmpeg()
    voice = tmp_path / "voice.wav"
    _make_audio(ffmpeg, voice, 1.0)

    mixed = renderer._build_audio_concat(
        str(voice), "", 0.2, [], 1.5, str(tmp_path)
    )
    assert mixed and Path(mixed).is_file()

    payload = _ffprobe_json(mixed)
    stream = next(row for row in payload["streams"] if row["codec_type"] == "audio")
    assert stream["sample_rate"] == "48000"
    assert stream["channels"] == 2
    assert float(stream["duration"]) == pytest.approx(1.5, abs=0.03)


def test_semantic_mux_keeps_picture_duration_when_audio_is_short(tmp_path):
    ffmpeg = renderer._ffmpeg()
    voice = tmp_path / "voice.wav"
    _make_audio(ffmpeg, voice, 1.0)
    still = tmp_path / "still.png"
    renderer.Image.new("RGB", (320, 180), (20, 30, 50)).save(still)
    output = tmp_path / "delivery.mp4"

    manifest = {
        "width": 320,
        "height": 180,
        "fps": 24,
        "duration": 2.0,
        "scenes": [
            {
                "duration": 1.0,
                "media": {"path": str(still), "type": "image"},
                "graphic": {"template_id": "full-bleed", "props": {}},
            },
            {
                "duration": 1.0,
                "media": {"path": str(still), "type": "image"},
                "graphic": {"template_id": "full-bleed", "props": {}},
            },
        ],
        "audio": {"voiceover": {"path": str(voice)}, "effects": []},
        "transitions": [],
    }

    renderer.render_semantic_documentary_ffmpeg(
        manifest,
        output,
        config={"semantic_render_concurrency": 1, "semantic_render_budget_seconds": 120},
    )
    payload = _ffprobe_json(str(output))
    assert float(payload["format"]["duration"]) == pytest.approx(2.0, abs=0.08)
    stream = next(row for row in payload["streams"] if row["codec_type"] == "audio")
    assert stream["sample_rate"] == "48000"
    assert stream["channels"] == 2
    assert float(stream["duration"]) == pytest.approx(2.0, abs=0.08)

def test_audio_mix_loops_short_background_music_to_target_duration(tmp_path):
    ffmpeg = renderer._ffmpeg()
    voice = tmp_path / "voice.wav"
    music = tmp_path / "music.wav"
    _make_audio(ffmpeg, voice, 1.0)
    _make_audio(ffmpeg, music, 0.5)

    mixed = renderer._build_audio_concat(
        str(voice), str(music), 0.25, [], 2.5, str(tmp_path)
    )
    assert mixed and Path(mixed).is_file()

    payload = _ffprobe_json(mixed)
    stream = next(row for row in payload["streams"] if row["codec_type"] == "audio")
    assert stream["sample_rate"] == "48000"
    assert stream["channels"] == 2
    assert float(stream["duration"]) == pytest.approx(2.5, abs=0.03)

