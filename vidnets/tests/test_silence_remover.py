import os
import tempfile
import numpy as np
import pytest
import soundfile as sf
from server.services.silence_remover import (
    detect_silence_intervals,
    shorten_audio_silence,
    shorten_sound_sequence,
    adjust_word_timings,
)
from server.services.speech_timing import load_speech_timing, save_speech_timing


def test_detect_silence_intervals():
    sr = 24000
    # 0.5s tone, 0.8s silence, 0.5s tone, 0.6s silence
    t = np.linspace(0, 0.5, int(sr * 0.5), endpoint=False)
    tone = 0.5 * np.sin(2 * np.pi * 440 * t)
    silence1 = np.zeros(int(sr * 0.8), dtype=np.float32)
    silence2 = np.zeros(int(sr * 0.6), dtype=np.float32)

    audio = np.concatenate([tone, silence1, tone, silence2])
    intervals = detect_silence_intervals(audio, sr, min_silence_seconds=0.35)

    assert len(intervals) == 2
    # First interval should be around 0.5 to 1.3
    assert 0.45 <= intervals[0][0] <= 0.55
    assert 1.25 <= intervals[0][1] <= 1.35
    # Second interval should be around 1.8 to 2.4
    assert 1.75 <= intervals[1][0] <= 1.85
    assert 2.35 <= intervals[1][1] <= 2.45


def test_shorten_audio_silence_with_words(tmp_path):
    sr = 24000
    t1 = np.linspace(0, 0.4, int(sr * 0.4), endpoint=False)
    burst1 = 0.5 * np.sin(2 * np.pi * 440 * t1)
    silence = np.zeros(int(sr * 1.0), dtype=np.float32)  # 1.0s silence
    t2 = np.linspace(0, 0.4, int(sr * 0.4), endpoint=False)
    burst2 = 0.5 * np.sin(2 * np.pi * 880 * t2)

    audio = np.concatenate([burst1, silence, burst2])
    target_wav = tmp_path / "speech.wav"
    sf.write(str(target_wav), audio, sr)

    words = [
        {"word": "Hello", "start": 0.0, "end": 0.4},
        {"word": "World", "start": 1.4, "end": 1.8},
    ]
    save_speech_timing(str(target_wav), words)

    out_path, new_dur, updated_words, report = shorten_audio_silence(
        str(target_wav),
        min_silence_seconds=0.38,
        target_cadence_seconds=0.22,
    )

    # Original was 1.8s, gap was 1.0s, shortened by ~0.78s down to ~0.22s
    assert report["cuts_applied"] >= 1
    assert report["seconds_removed"] > 0.65
    assert new_dur < 1.25
    assert len(updated_words) == 2
    assert updated_words[0]["word"] == "Hello"
    assert updated_words[1]["word"] == "World"
    assert updated_words[0]["start"] == 0.0
    # The gap between word 0 and word 1 should now be ~0.22s
    gap = updated_words[1]["start"] - updated_words[0]["end"]
    assert 0.18 <= gap <= 0.28
    assert updated_words[1]["end"] <= new_dur + 0.05

    # Check timing file was updated and matches new audio sha256
    reloaded = load_speech_timing(out_path, new_dur)
    assert len(reloaded) == 2
    assert reloaded[1]["word"] == "World"


def test_shorten_audio_silence_acoustic_only(tmp_path):
    sr = 24000
    t1 = np.linspace(0, 0.3, int(sr * 0.3), endpoint=False)
    burst1 = 0.5 * np.sin(2 * np.pi * 440 * t1)
    silence = np.zeros(int(sr * 0.9), dtype=np.float32)  # 900ms dead air
    t2 = np.linspace(0, 0.3, int(sr * 0.3), endpoint=False)
    burst2 = 0.5 * np.sin(2 * np.pi * 440 * t2)

    audio = np.concatenate([burst1, silence, burst2])
    target_wav = tmp_path / "acoustic.wav"
    sf.write(str(target_wav), audio, sr)

    out_path, new_dur, _, report = shorten_audio_silence(
        str(target_wav),
        min_silence_seconds=0.35,
        target_cadence_seconds=0.20,
    )
    assert report["cuts_applied"] >= 1
    assert new_dur < 1.1


def test_shorten_audio_silence_no_silence(tmp_path):
    sr = 24000
    t = np.linspace(0, 1.0, sr, endpoint=False)
    audio = 0.4 * np.sin(2 * np.pi * 440 * t)
    target_wav = tmp_path / "continuous.wav"
    sf.write(str(target_wav), audio, sr)

    out_path, new_dur, _, report = shorten_audio_silence(str(target_wav))
    assert report["cuts_applied"] == 0
    assert abs(new_dur - 1.0) < 0.02


def test_shorten_sound_sequence(tmp_path):
    sr = 24000
    sfx1 = 0.3 * np.ones(int(sr * 0.2), dtype=np.float32)
    gap = np.zeros(int(sr * 0.8), dtype=np.float32)
    sfx2 = 0.3 * np.ones(int(sr * 0.2), dtype=np.float32)
    seq = np.concatenate([sfx1, gap, sfx2])

    target_wav = tmp_path / "sfx.wav"
    sf.write(str(target_wav), seq, sr)

    out_path, new_dur = shorten_sound_sequence(str(target_wav))
    assert new_dur < 0.8
