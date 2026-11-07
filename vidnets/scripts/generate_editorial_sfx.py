"""Generate the editorial SFX library used for text-card + transition cues.

Writes 24 short mono 44.1kHz WAVs into public/sound-effects/edit/.

All sounds are procedurally synthesized — no external assets required so this
stays hermetic, license-free, and reproducible. Sounds are intentionally
small/tight (mostly 0.28–0.85s) because they fire at edit moments under an
existing voiceover mix.
"""
from __future__ import annotations

import os
import wave
from pathlib import Path

import numpy as np
from scipy import signal as sig

SR = 44100
OUT_DIR = Path(__file__).resolve().parent.parent / "public" / "sound-effects" / "edit"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _t(dur: float) -> np.ndarray:
    return np.arange(int(SR * dur)) / SR


def _soft_env(t: np.ndarray, attack: float = 0.01, release: float = 0.18) -> np.ndarray:
    env = np.ones_like(t)
    a = max(1, int(attack * SR))
    r = max(1, int(release * SR))
    env[:a] = np.linspace(0.0, 1.0, a)
    env[-r:] = np.linspace(1.0, 0.0, r)
    return env


def _norm(x: np.ndarray, peak: float = 0.92) -> np.ndarray:
    m = float(np.max(np.abs(x))) or 1.0
    return (x / m) * peak


def _save(name: str, x: np.ndarray, peak: float = 0.92) -> None:
    pcm = np.clip(_norm(x, peak), -1.0, 1.0)
    pcm16 = (pcm * 32767).astype(np.int16)
    path = OUT_DIR / f"{name}.wav"
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(pcm16.tobytes())
    print(f"  wrote {path.name}: {len(x)/SR:.2f}s")


def whoosh(dur: float = 0.45, sweep_up: bool = True, low: int = 200, high: int = 3200, brightness: float = 0.7):
    t = _t(dur)
    n = np.random.randn(len(t))
    sos = sig.butter(2, low if sweep_up else high, btype="highpass", fs=SR, output="sos")
    x = sig.sosfilt(sos, n)
    # pitch envelope sweeps the band upward (or downward)
    sweep = np.logspace(np.log10(low), np.log10(high), len(t)) if sweep_up else np.logspace(np.log10(high), np.log10(low), len(t))
    # amplitude shaped like a passing whoosh
    amp = np.sin(np.pi * np.clip(np.linspace(0, 1, len(t)), 0, 1)) ** 1.4
    return _norm(x * amp * _soft_env(t, attack=0.02, release=dur * 0.35) * brightness)


def paper_shuffle(dur: float = 0.32):
    t = _t(dur)
    n = np.random.randn(len(t))
    sos = sig.butter(3, [900, 6500], btype="bandpass", fs=SR, output="sos")
    x = sig.sosfilt(sos, n)
    # crinkle pattern: amplitude jitter modulated at ~9Hz with random dips
    flutter = (1 + 0.6 * np.sin(2 * np.pi * 9 * t) + 0.4 * np.sin(2 * np.pi * 23 * t + 0.7))
    dips = 1.0 - 0.35 * (np.random.rand(len(t)) > 0.985)
    return _norm(x * flutter * dips * _soft_env(t, 0.005, dur * 0.30))


def paper_tear_subtle(dur: float = 0.28):
    t = _t(dur)
    n = np.random.randn(len(t))
    sos = sig.butter(2, [1400, 8000], btype="bandpass", fs=SR, output="sos")
    x = sig.sosfilt(sos, n)
    # progressive tear: amplitude ramps up with random micro-rips
    rip = np.cumsum(np.random.rand(len(t)) > 0.996).clip(0, 1)
    amp = np.minimum(1.0, 0.25 + rip * 0.85 + np.linspace(0, 0.4, len(t)))
    return _norm(x * amp * _soft_env(t, 0.003, dur * 0.24))


def staple(dur: float = 0.14):
    t = _t(dur)
    n = np.random.randn(len(t))
    sos = sig.butter(2, [2500, 9500], btype="bandpass", fs=SR, output="sos")
    x = sig.sosfilt(sos, n)
    thump = np.exp(-t * 90) * 1.0
    return _norm(x * thump * 1.4 + np.sin(2 * np.pi * 220) * np.exp(-t * 60) * 0.3)


def book_close(dur: float = 0.28):
    t = _t(dur)
    low = np.sin(2 * np.pi * 110 * t) * np.exp(-t * 22)
    mid = np.sin(2 * np.pi * 260 * t) * np.exp(-t * 30) * 0.55
    n = np.random.randn(len(t))
    sos = sig.butter(1, 900, "lowpass", fs=SR, output="sos")
    dust = sig.sosfilt(sos, n) * np.exp(-t * 28) * 0.4
    return _norm(low + mid + dust)


def type_click(dur: float = 0.06):
    t = _t(dur)
    return _norm(np.random.randn(len(t)) * np.exp(-t * 220))


def type_burst_short(dur: float = 0.36):
    t = _t(dur)
    x = np.zeros_like(t)
    rng = np.random.default_rng(7)
    n_keys = max(4, int(dur / 0.07))
    starts = rng.uniform(0.005, dur - 0.06, n_keys)
    starts.sort()
    for s in starts:
        i = int(s * SR)
        k = type_click(0.05)
        end = min(len(x), i + len(k))
        x[i:end] += k[: end - i] * 0.85
    return _norm(x)


def cursor_blink(dur: float = 0.10):
    t = _t(dur)
    return _norm(np.sin(2 * np.pi * 1450 * t) * np.exp(-t * 65) * 0.6)


def hit_dry(dur: float = 0.24):
    t = _t(dur)
    n = np.random.randn(len(t))
    sos = sig.butter(2, 320, "lowpass", fs=SR, output="sos")
    low = np.sin(2 * np.pi * 92 * t) * np.exp(-t * 26)
    thump = sig.sosfilt(sos, n) * np.exp(-t * 32) * 0.7
    return _norm(low + thump)


def hit_tight(dur: float = 0.18):
    t = _t(dur)
    n = np.random.randn(len(t))
    sos = sig.butter(2, [700, 5200], btype="bandpass", fs=SR, output="sos")
    crack = sig.sosfilt(sos, n) * np.exp(-t * 60)
    low = np.sin(2 * np.pi * 140 * t) * np.exp(-t * 38) * 0.55
    return _norm(crack * 0.9 + low)


def hit_boomed(dur: float = 0.62):
    t = _t(dur)
    low = np.sin(2 * np.pi * 55 * t) * np.exp(-t * 9)
    sub = np.sin(2 * np.pi * 38 * t) * np.exp(-t * 6.5) * 0.65
    n = np.random.randn(len(t))
    sos = sig.butter(2, 240, "lowpass", fs=SR, output="sos")
    tail = sig.sosfilt(sos, n) * np.exp(-t * 11) * 0.4
    return _norm(low + sub + tail)


def sting_minor(dur: float = 0.50):
    t = _t(dur)
    f1, f2, f3 = 233.08, 277.18, 349.23  # Bb, C#, F (dim triad feel)
    chord = np.sin(2 * np.pi * f1 * t) * 0.5 + np.sin(2 * np.pi * f2 * t) * 0.42 + np.sin(2 * np.pi * f3 * t) * 0.34
    return _norm(chord * np.exp(-t * 5.5) * _soft_env(t, 0.01, 0.30))


def sting_major(dur: float = 0.60):
    t = _t(dur)
    f1, f2, f3 = 261.63, 329.63, 392.0  # C E G
    chord = np.sin(2 * np.pi * f1 * t) * 0.55 + np.sin(2 * np.pi * f2 * t) * 0.42 + np.sin(2 * np.pi * f3 * t) * 0.36
    shimmer = np.sin(2 * np.pi * 1046 * t) * np.exp(-t * 16) * 0.15
    return _norm((chord + shimmer) * np.exp(-t * 5.2) * _soft_env(t, 0.012, 0.34))


def reveal_soft(dur: float = 0.55):
    t = _t(dur)
    whooshy = np.random.randn(len(t))
    sos = sig.butter(1, 900, "lowpass", fs=SR, output="sos")
    x = sig.sosfilt(sos, whooshy)
    amp = np.sin(np.pi * np.clip(np.linspace(0, 1, len(t)), 0, 1)) ** 1.6
    chime = np.sin(2 * np.pi * 1046 * t) * np.exp(-t * 8) * 0.22 + np.sin(2 * np.pi * 1568 * t) * np.exp(-t * 12) * 0.14
    return _norm(x * amp * 0.6 + chime)


def reveal_chime(dur: float = 0.62):
    t = _t(dur)
    x = np.sin(2 * np.pi * 1318.51 * t) * np.exp(-t * 9)
    x += np.sin(2 * np.pi * 1567.98 * t) * np.exp(-t * 11) * 0.7
    x += np.sin(2 * np.pi * 2093.0 * t) * np.exp(-t * 14) * 0.4
    return _norm(x * _soft_env(t, 0.01, 0.4))


def shutter_snap(dur: float = 0.16):
    t = _t(dur)
    n = np.random.randn(len(t))
    sos = sig.butter(3, [1200, 8500], btype="bandpass", fs=SR, output="sos")
    x = sig.sosfilt(sos, n) * np.exp(-t * 85)
    click = np.sin(2 * np.pi * 2300 * t) * np.exp(-t * 180) * 0.4
    return _norm(x + click)


def flash_bulb(dur: float = 0.34):
    t = _t(dur)
    n = np.random.randn(len(t))
    sos = sig.butter(3, [2800, 9800], btype="bandpass", fs=SR, output="sos")
    hiss = sig.sosfilt(sos, n)
    riseenv = np.minimum(1.0, t / 0.04)
    sizzlenv = np.exp(-t * 14)
    popenv = np.exp(-((t - 0.045) ** 2) / 0.0004) * 0.6
    return _norm(hiss * riseenv * sizzlenv * 0.7 + popenv)


def room_shift(dur: float = 0.65):
    t = _t(dur)
    n = np.random.randn(len(t))
    sos = sig.butter(1, 320, "lowpass", fs=SR, output="sos")
    x = sig.sosfilt(sos, n)
    drift = np.sin(2 * np.pi * 0.6 * t) * 0.35 + 1.0
    return _norm(x * drift * _soft_env(t, 0.18, 0.42) * 0.5)


def tape_stop(dur: float = 0.42):
    t = _t(dur)
    # pitch slides down: simulate by modulating a sine with a falling freq
    f0, f1 = 660, 60
    phase = 2 * np.pi * np.cumsum(np.logspace(np.log10(f0), np.log10(f1), len(t))) / SR
    x = np.sin(phase) * np.exp(-t * 6)
    n = np.random.randn(len(t))
    sos = sig.butter(2, 900, "lowpass", fs=SR, output="sos")
    dust = sig.sosfilt(sos, n) * np.exp(-t * 10) * 0.3
    return _norm(x * 0.6 + dust)


def vinyl_pop(dur: float = 0.05):
    t = _t(dur)
    n = np.random.randn(len(t))
    sos = sig.butter(3, [1500, 9500], btype="bandpass", fs=SR, output="sos")
    return _norm(sig.sosfilt(sos, n) * np.exp(-t * 240))


def zoom_whirr(dur: float = 0.32):
    t = _t(dur)
    f0, f1 = 380, 1100
    phase = 2 * np.pi * np.cumsum(np.logspace(np.log10(f0), np.log10(f1), len(t))) / SR
    x = np.sin(phase) * 0.6
    harmonic = np.sin(phase * 2.01) * 0.22
    env = np.sin(np.pi * np.clip(np.linspace(0, 1, len(t)), 0, 1)) ** 1.1
    return _norm((x + harmonic) * env)


def marker_underline(dur: float = 0.26):
    t = _t(dur)
    n = np.random.randn(len(t))
    sos = sig.butter(2, [1100, 5400], btype="bandpass", fs=SR, output="sos")
    x = sig.sosfilt(sos, n)
    # slight ramp of pressure
    amp = np.minimum(1.0, 0.25 + np.linspace(0, 0.8, len(t)))
    return _norm(x * amp * _soft_env(t, 0.008, dur * 0.34))


def check_tick(dur: float = 0.10):
    t = _t(dur)
    return _norm(np.sin(2 * np.pi * 1750 * t) * np.exp(-t * 95))


def slider_move(dur: float = 0.24):
    t = _t(dur)
    n = np.random.randn(len(t))
    sos = sig.butter(2, [800, 4600], btype="bandpass", fs=SR, output="sos")
    x = sig.sosfilt(sos, n)
    clicks = (np.sin(2 * np.pi * 1400 * t) * np.exp(-t * 140) * 0.35) * (np.sin(2 * np.pi * 9 * t) > 0.4)
    return _norm(x * 0.7 + clicks * 0.5)


def main() -> None:
    print(f"writing editorial SFX to {OUT_DIR}")
    _save("edit_whoosh_light", whoosh(0.32, True, 400, 4200, 0.62))
    _save("edit_whoosh_deep", whoosh(0.50, True, 120, 2600, 0.8))
    _save("edit_whoosh_sucked", whoosh(0.42, False, 220, 3800, 0.78))
    _save("edit_whoosh_air", whoosh(0.46, True, 300, 5200, 0.55))

    _save("edit_paper_shuffle", paper_shuffle(0.34))
    _save("edit_paper_tear_subtle", paper_tear_subtle(0.30))
    _save("edit_staple", staple(0.16))
    _save("edit_book_close", book_close(0.30))

    _save("edit_type_click", type_click(0.06))
    _save("edit_type_burst_short", type_burst_short(0.36))
    _save("edit_cursor_blink", cursor_blink(0.10))

    _save("edit_hit_dry", hit_dry(0.24))
    _save("edit_hit_tight", hit_tight(0.18))
    _save("edit_hit_boomed", hit_boomed(0.62))
    _save("edit_sting_minor", sting_minor(0.50))
    _save("edit_sting_major", sting_major(0.60))

    _save("edit_reveal_soft", reveal_soft(0.55))
    _save("edit_reveal_chime", reveal_chime(0.62))
    _save("edit_shutter_snap", shutter_snap(0.16))
    _save("edit_flash_bulb", flash_bulb(0.34))

    _save("edit_room_shift", room_shift(0.65))
    _save("edit_tape_stop", tape_stop(0.42))
    _save("edit_vinyl_pop", vinyl_pop(0.05))
    _save("edit_zoom_whirr", zoom_whirr(0.32))
    _save("edit_marker_underline", marker_underline(0.26))
    _save("edit_check_tick", check_tick(0.10))
    _save("edit_slider_move", slider_move(0.24))


if __name__ == "__main__":
    main()
