"""Compatibility facade for the Audiogram voice service.

Older integrations imported this module directly. Keeping the small facade
prevents those callers from loading an optional local model while routing all
audio generation through the supported Audiogram implementation.
"""

from . import edge_tts_service
from .silence_remover import shorten_audio_silence, shorten_sound_sequence


def init_tts():
    """Keep the legacy initialization hook successful and side-effect free."""
    return True


def is_available():
    """Report a real online or offline speech capability."""
    import sys
    return edge_tts_service.is_available() or sys.platform == 'win32'


def get_voices():
    return edge_tts_service.get_voice_labels()


def generate_audio(script, voice='Brian (Male US)', speed=1.0, shorten_silence=True, **kwargs):
    path, dur = edge_tts_service.generate_audio(
        script,
        voice,
        speed=speed,
        edge_options=kwargs.get('edge_options'),
    )
    if shorten_silence and path and dur > 0:
        try:
            path, dur, _, _ = shorten_audio_silence(path)
        except Exception:
            pass
    return path, dur


def generate_audio_chunked(script, voice='Brian (Male US)', speed=1.0,
                           max_workers=2, shorten_silence=True, **kwargs):
    path, dur = edge_tts_service.generate_audio_chunked(
        script,
        voice,
        speed=speed,
        max_workers=max_workers,
        edge_options=kwargs.get('edge_options'),
    )
    if shorten_silence and path and dur > 0:
        try:
            path, dur, _, _ = shorten_audio_silence(path)
        except Exception:
            pass
    return path, dur


def preview_voice(voice, text='Hello! This is a preview of my voice.'):
    return edge_tts_service.preview_voice(voice, text)
