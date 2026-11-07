"""Local PCM sound measurements for frame-aligned editorial events."""
from array import array
from functools import lru_cache
import math
from pathlib import Path
import sys
import wave


@lru_cache(maxsize=128)
def _measure(path: str, modified_ns: int, size: int) -> dict:
    try:
        if size > 8 * 1024 * 1024:
            return {}
        with wave.open(path, 'rb') as audio:
            if audio.getsampwidth() != 2 or audio.getcomptype() != 'NONE':
                return {}
            rate, channels = audio.getframerate(), audio.getnchannels()
            duration = audio.getnframes() / max(1, rate)
            samples = array('h', audio.readframes(min(audio.getnframes(), rate * 8)))
        if sys.byteorder != 'little':
            samples.byteswap()
        if not samples:
            return {}
        # A 5 ms envelope; align the first strong transient rather than the
        # start of an initial silence or a long low-level whoosh ramp.
        window = max(channels, round(rate * .005) * channels)
        envelopes = [max(abs(sample) for sample in samples[i:i + window]) for i in range(0, len(samples), window)]
        peak = max(envelopes)
        onset = next((i for i, value in enumerate(envelopes) if value >= peak * .7), 0) if peak else 0
        return {'duration_seconds': round(duration, 4), 'transient_offset_seconds': round(onset * window / channels / rate, 4),
                'source_peak_dbfs': round(20 * math.log10(max(1, peak) / 32768), 2), 'transient_measurement': 'pcm_5ms_first_70pct_peak'}
    except (OSError, EOFError, wave.Error, ValueError):
        return {}


def measure_sound(path: str | Path) -> dict:
    source = Path(path)
    try:
        stat = source.stat()
        return dict(_measure(str(source.resolve()), stat.st_mtime_ns, stat.st_size))
    except OSError:
        return {}
