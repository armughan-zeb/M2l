"""Measure narration itself, so a silent file cannot count as a voiceover."""
import math
import array
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


def _ffmpeg():
    executable = os.environ.get('VIDZEO_FFMPEG_PATH') or shutil.which('ffmpeg')
    bundled = Path(__file__).resolve().parents[2] / 'tools/ffmpeg/ffmpeg.exe'
    return executable or (str(bundled) if bundled.is_file() else 'ffmpeg')


def validate_narration_audio(audio_path):
    target = Path(audio_path)
    if not target.is_file() or target.stat().st_size < 128:
        raise RuntimeError('Narration audio is missing or empty.')
    ffmpeg = _ffmpeg()
    # FFmpeg measures the stream internally. Do not hold a multi-hour PCM
    # narration or one Python object per sample in the desktop process.
    decoded = subprocess.run(
        [ffmpeg, '-hide_banner', '-nostats', '-i', str(target), '-vn', '-ac', '1',
         '-ar', '8000', '-af', 'silencedetect=noise=-50dB:d=0.2,volumedetect',
         '-progress', 'pipe:1', '-f', 'null', '-'],
        capture_output=True, text=True, errors='replace', timeout=300,
    )
    duration_values = re.findall(r'out_time_us=(\d+)', decoded.stdout or '')
    duration = float(duration_values[-1]) / 1_000_000 if duration_values else 0.0
    peaks = re.findall(r'max_volume:\s*(-?[\d.]+|[-+]?inf)\s*dB', decoded.stderr or '')
    peak_dbfs = float(peaks[-1]) if peaks else -math.inf
    silent_seconds = sum(float(value) for value in re.findall(r'silence_duration:\s*([\d.]+)', decoded.stderr or ''))
    if decoded.returncode or duration < .1 or not peaks:
        raise RuntimeError('Narration audio cannot be decoded or is too short.')
    ratio = max(0.0, min(1.0, 1.0 - silent_seconds / duration))
    if peak_dbfs < -46 or ratio < .025:
        raise RuntimeError('Narration contains no usable speech signal; speech recovery is required.')
    return {'status': 'pass', 'duration_seconds': round(duration, 4),
            'peak_dbfs': round(peak_dbfs, 2),
            'active_window_ratio': round(ratio, 4)}


def validate_narration_mix(video_path, narration_path, duration):
    """Check that the exported mix contains the verified voice, not only SFX."""
    validate_narration_audio(narration_path)
    duration = max(.1, float(duration))
    window = min(3.0, duration / 4)
    def samples(path, start):
        decoded = subprocess.run(
            [_ffmpeg(), '-v', 'error', '-ss', str(start), '-i', str(path),
             '-t', str(window), '-vn', '-ac', '1', '-ar', '2000', '-f', 'f32le', '-'],
            capture_output=True, timeout=30)
        if decoded.returncode:
            raise RuntimeError('Could not compare exported voice with narration.')
        values = array.array('f')
        values.frombytes(decoded.stdout)
        return values
    checks = []
    for fraction in (.10, .40, .70):
        start = max(0, min(duration - window, duration * fraction))
        voice = samples(narration_path, start)
        mixed = samples(video_path, start)
        length = min(len(voice), len(mixed))
        if length < 200:
            continue
        energy = sum(value * value for value in voice[:length])
        if energy / length < 1e-6:
            continue
        best, gain = 0.0, 0.0
        # AAC priming/resampling can move samples slightly. Compare a bounded
        # 40 ms window instead of declaring the retained voice absent.
        for lag in range(-80, 81):
            begin, end = max(0, -lag), min(length, length - lag)
            reference = voice[begin:end]
            actual = mixed[begin + lag:end + lag]
            ref_energy = sum(value * value for value in reference)
            mix_energy = sum(value * value for value in actual)
            if ref_energy <= 0 or mix_energy <= 0:
                continue
            correlation = sum(a * b for a, b in zip(reference, actual)) / math.sqrt(ref_energy * mix_energy)
            if correlation > best:
                best, gain = correlation, math.sqrt(mix_energy / ref_energy)
        checks.append({'start': round(start, 3), 'correlation': round(best, 4), 'gain': round(gain, 4)})
    matched = sum(row['correlation'] >= .35 and row['gain'] >= .04 for row in checks)
    if not checks or matched < min(2, len(checks)):
        raise RuntimeError('Exported audio does not retain the verified narration; remux recovery is required.')
    return {'status': 'pass', 'matched_samples': matched, 'samples': checks}


def repair_narration_mix(video_path, narration_path, duration, audio_manifest=None):
    """Recover a missing/muted voice by remuxing the existing Remotion picture."""
    validate_narration_audio(narration_path)
    target = Path(video_path)
    audio = audio_manifest or {}
    music = audio.get('music') or {}
    music = music if isinstance(music, dict) else {}
    with tempfile.TemporaryDirectory(prefix='narration-recovery-', dir=target.parent) as folder:
        from .semantic_ffmpeg_renderer import _build_audio_concat
        music_gain = .12 if music.get('volume') is None else float(music['volume'])
        mix = _build_audio_concat(str(narration_path), str(music.get('path') or music.get('src') or ''),
                                  music_gain, list(audio.get('effects') or []),
                                  float(duration), folder)
        if not mix:
            raise RuntimeError('Verified narration could not be remixed for export.')
        repaired = Path(folder) / 'repaired.mp4'
        result = subprocess.run(
            [_ffmpeg(), '-y', '-v', 'error', '-i', str(target), '-i', str(mix),
             '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac',
             '-b:a', '192k', '-t', str(duration), '-movflags', '+faststart', str(repaired)],
            capture_output=True, timeout=300)
        if result.returncode or not repaired.is_file():
            raise RuntimeError('Narration recovery mux failed; original picture retained.')
        quality = validate_narration_mix(repaired, narration_path, duration)
        repaired.replace(target)
        return {**quality, 'recovery': 'verified_narration_remux'}
