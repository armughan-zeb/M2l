"""Regressions from the silent Corolla export and its failed recovery paths."""
import sys
import math
import struct
import subprocess
import wave
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from services.narration_quality import validate_narration_audio
from services import edge_tts_service
from services.youtube_pipeline_v2 import _hard_block_reason, _ranged_download_worker_limit
from services.editorial_director.media_first import _display_text


def test_silent_but_valid_audio_container_is_rejected(tmp_path):
    target = tmp_path / 'silent.wav'
    with wave.open(str(target), 'wb') as audio:
        audio.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        audio.writeframes(b'\0\0' * 8000)
    with pytest.raises(RuntimeError, match='no usable speech'):
        validate_narration_audio(target)


def test_provider_authorization_failure_recovers_once_without_retry_sleep(tmp_path):
    class Denied(Exception):
        status = 403
    async def denied(*args, **kwargs):
        raise Denied('403 authorization rejected')
    with mock.patch.object(edge_tts_service, '_EDGE_TTS_AVAILABLE', True), \
         mock.patch.object(edge_tts_service, '_EDGE_TTS_FALLBACK_ENABLED', True), \
         mock.patch.object(edge_tts_service, '_generate_edge_tts', side_effect=denied), \
         mock.patch.object(edge_tts_service, '_generate_sapi_audio', return_value=('spoken.wav', 4)) as recovery, \
         mock.patch.object(edge_tts_service.time, 'sleep') as sleep:
        assert edge_tts_service.generate_audio('Real narration is required.') == ('spoken.wav', 4)
        recovery.assert_called_once()
        sleep.assert_not_called()


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows offline speech integration')
def test_offline_speech_has_audible_output_and_numeric_word_boundaries(tmp_path):
    from services.local_speech_service import generate_audio
    from services.speech_timing import aligned_provider_words
    text = 'Toyota launched the Corolla in Japan in 1966. Each generation changed its engineering while preserving a familiar name.'
    path, duration = generate_audio(text, str(tmp_path / 'spoken.wav'))
    quality = validate_narration_audio(path)
    words, alignment = aligned_provider_words(path, text, duration)
    assert quality['active_window_ratio'] > .25
    assert len(words) == len(text.split())
    assert alignment['exact_match_ratio'] == 1
    assert words[-1]['end'] <= duration


def test_explicitly_synthetic_publisher_does_not_consume_real_footage_slot():
    assert _hard_block_reason({'title': 'How the Toyota Corolla is made',
                               'channel': 'NextGen Auto AI', 'duration': 900}) == 'synthetic_publisher'
    assert _hard_block_reason({'title': 'Factory tour', 'channel': 'Toyota', 'duration': 900}) == ''


def test_range_download_memory_floor_and_parallel_headroom():
    assert _ranged_download_worker_limit(4, 384, 8 * 1024**3) == 1
    assert _ranged_download_worker_limit(4, 1024, 8 * 1024**3) == 2
    assert _ranged_download_worker_limit(4, 4096, 16 * 1024**3) == 4


def test_verified_story_publisher_is_preferred_without_trusting_fan_names():
    from services.youtube_pipeline_v2 import _verified_publisher_affinity
    assert _verified_publisher_affinity({'channel': 'Toyota USA', 'channel_is_verified': True}, 'Toyota Corolla') == 40
    assert _verified_publisher_affinity({'channel': 'Toyota Reviews', 'channel_is_verified': True}, 'Toyota Corolla') == 0
    assert _verified_publisher_affinity({'channel': 'Toyota', 'channel_is_verified': False}, 'Toyota Corolla') == 0


def test_overlay_replaces_dangling_intro_fragment_with_grounded_entity():
    display, _, _ = _display_text('corner_identification',
                                 {'display_text': 'The Toyota Corolla started'},
                                 'The Toyota Corolla started as a practical car with a big question.')
    assert display == 'Toyota Corolla'


def test_estimated_captions_are_exportable_but_not_marked_publish_ready():
    from services.delivery_quality import delivery_quality_summary
    report = delivery_quality_summary({'quality_checks': {'status': 'pass'}, 'captions_enabled': True,
                                       'scene_timing': {'timing_source': 'tts_script_timing_low_confidence_whisper'}})
    assert not report['ready_to_publish']
    assert any('Caption timing' in message for message in report['blockers'])
    measured = delivery_quality_summary({'quality_checks': {'status': 'pass'}, 'captions_enabled': True,
                                         'creative_review': {'status': 'pass', 'llm_review_certified': True},
                                         'scene_timing': {'timing_source': 'tts_word_boundary'}})
    assert measured['ready_to_publish']


def test_voice_reference_check_rejects_effects_only_and_accepts_same_voice(tmp_path):
    from services.narration_quality import validate_narration_mix, repair_narration_mix, _ffmpeg
    def make_audio(name, frequency):
        path = tmp_path / name
        with wave.open(str(path), 'wb') as output:
            output.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            output.writeframes(b''.join(struct.pack('<h', int(9000 * math.sin(2 * math.pi * frequency * i / 8000)))
                                       for i in range(24000)))
        return path
    voice = make_audio('voice.wav', 337)
    effects = make_audio('effects.wav', 791)
    assert validate_narration_mix(voice, voice, 3)['matched_samples'] == 3
    with pytest.raises(RuntimeError, match='does not retain'):
        validate_narration_mix(effects, voice, 3)
    movie = tmp_path / 'effects-only.mp4'
    subprocess.run([_ffmpeg(), '-y', '-v', 'error', '-f', 'lavfi', '-i',
                    'color=c=navy:s=160x90:r=15:d=3', '-i', str(effects),
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', '-t', '3', str(movie)],
                   capture_output=True, check=True, timeout=30)
    recovered = repair_narration_mix(movie, voice, 3)
    assert recovered['recovery'] == 'verified_narration_remux'
    assert recovered['matched_samples'] == 3
