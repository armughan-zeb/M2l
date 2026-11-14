import asyncio
import math
import wave
from unittest import mock

from server.services import speech_timing, edge_tts_service
from server.services.editorial_director.media_director import plan_media_composition
from server.services.visual_requirement import build_visual_requirement, provider_query_variants


def test_negated_category_is_not_a_required_visual_attribute():
    req = build_visual_requirement(title='Tesla', script='Tesla was not a family sedan. It was an electric sports car.', scene_text='Tesla was not a family sedan.')
    assert 'vehicle_class' not in req['required_attributes']


def test_recent_vehicle_year_does_not_force_archive_queries():
    req = build_visual_requirement(title='Tesla', script='Model 3 deliveries began in 2017.', scene_text='Model 3 deliveries began in 2017.')
    assert 'archival footage' not in provider_query_variants(req, provider='youtube')[0]


def test_energy_storage_is_not_required_to_show_an_automobile():
    req = build_visual_requirement(title='Tesla', script='Tesla makes electric cars and energy storage.',
        scene_text='Stationary batteries store electricity for homes.', semantic_context={'domain_category': 'automotive'})
    assert req['subject_type'] == 'organization'
    assert 'automobile' not in provider_query_variants(req, provider='youtube')[0]


def test_clock_is_bound_to_exact_audio(tmp_path):
    audio = tmp_path / 'voice.mp3'
    audio.write_bytes(b'original-audio')
    words = [{'word': '486,532', 'start': .1, 'end': 2.712}]
    speech_timing.save_speech_timing(audio, words)
    aligned, audit = speech_timing.aligned_provider_words(audio, '486,532', 3.0)
    assert audit['exact_match_ratio'] == 1
    assert aligned[0]['start'] == .1
    assert aligned[0]['end'] == 2.712
    audio.write_bytes(b'replaced-audio')
    assert speech_timing.load_speech_timing(audio, 3.0) == []


def test_clock_rejects_invalid_boundaries(tmp_path):
    audio = tmp_path / 'voice.mp3'
    audio.write_bytes(b'audio')
    for start, end in [(-.01, .3), (.1, math.nan), (1, 1), (4, 5), (.2, 9)]:
        speech_timing.save_speech_timing(audio, [{'word': 'test', 'start': start, 'end': end}])
        assert speech_timing.load_speech_timing(audio, 3) == []


def test_grouped_year_remains_inside_its_measured_phrase(tmp_path):
    audio = tmp_path / 'voice.mp3'
    audio.write_bytes(b'audio')
    speech_timing.save_speech_timing(audio, [{'word': 'in 2008', 'start': 2, 'end': 3.2}])
    words, audit = speech_timing.aligned_provider_words(audio, 'in 2008.', 4)
    assert audit['exact_match_ratio'] == 1
    assert audit['phrase_split_word_count'] == 2
    assert words[0]['start'] == 2
    assert words[1]['end'] == 3.2
    assert words[0]['end'] == words[1]['start']
    assert words[1]['end'] - words[1]['start'] > words[0]['end'] - words[0]['start']


def test_tts_stream_captures_word_events(tmp_path):
    class Stream:
        async def stream(self):
            yield {'type': 'audio', 'data': b'audio'}
            yield {'type': 'WordBoundary', 'text': 'Hello', 'offset': 1000000, 'duration': 3000000}
    audio = tmp_path / 'voice.mp3'
    with mock.patch.object(edge_tts_service.edge_tts, 'Communicate', return_value=Stream()) as factory:
        asyncio.run(edge_tts_service._generate_edge_tts('Hello', 'en-US-GuyNeural', str(audio)))
    assert factory.call_args.kwargs['boundary'] == 'WordBoundary'
    assert speech_timing.load_speech_timing(audio, 1)[0]['start'] == .1


def test_chunk_concat_preserves_sample_clock(tmp_path):
    paths = []
    for index, frames in enumerate([24000, 36000]):
        path = tmp_path / f'part{index}.wav'
        with wave.open(str(path), 'wb') as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(24000)
            audio.writeframes(b'\0\0' * frames)
        speech_timing.save_speech_timing(path, [{'word': str(index), 'start': .1, 'end': .4}])
        paths.append(str(path))
    with mock.patch.object(edge_tts_service, 'TEMP_DIR', tmp_path):
        path, duration = edge_tts_service._concat_edge_chunks(paths, 0)
    words = speech_timing.load_speech_timing(path, duration)
    assert duration == 2.5
    assert [round(row['start'], 3) for row in words] == [.1, 1.1]
    assert [round(row['end'], 3) for row in words] == [.4, 1.4]


def test_enabled_captions_have_separate_reserved_region():
    scene = {'captions_enabled': True, 'media': {'src': 'image.jpg', 'type': 'image'},
             'primary_visual': {'type': 'Headline'},
             'design': {'safe_text_regions': [[.1, .80, .8, .96]]}}
    contract = plan_media_composition(scene)
    assert contract['safe_region'][3] <= .80
    assert contract['caption_region'][1] > contract['safe_region'][3]


# ── S8: script-derived caption timings (no Whisper for narration) ───────
def test_script_words_for_audio_covers_every_word_without_drift():
    script = " ".join(f"word{i}" for i in range(2754))
    rows = speech_timing.script_words_for_audio(script, 600.0)
    assert len(rows) == 2754
    assert rows[0]["start"] == 0.0
    assert rows[-1]["end"] == 600.0
    assert all(rows[i]["start"] < rows[i]["end"] for i in range(len(rows)))
    assert all(rows[i]["end"] <= rows[i + 1]["start"] + 0.001 for i in range(len(rows) - 1))
    assert all(row["timing_source"] == "tts_script_timing" for row in rows)


def test_script_words_for_audio_reanchors_each_chunk():
    # Each chunk's words are bounded by that chunk's own measured duration,
    # so timing errors cannot accumulate across chunks.
    first = speech_timing.script_words_for_audio("The quick brown fox jumps.", 2.0)
    second = speech_timing.script_words_for_audio("Over the lazy dog today.", 3.0)
    assert first[-1]["end"] == 2.0
    assert second[0]["start"] == 0.0
    assert second[-1]["end"] == 3.0


def test_script_chunk_concat_keeps_offsets_and_source_label(tmp_path):
    paths = []
    for index, (frames, text) in enumerate(
        [(24000, "The quick brown fox jumps."), (36000, "Over the lazy dog today.")]
    ):
        path = tmp_path / f"part{index}.wav"
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(24000)
            audio.writeframes(b"\0\0" * frames)
        speech_timing.save_speech_timing(
            path,
            speech_timing.script_words_for_audio(text, frames / 24000),
            source="tts_script_timing",
        )
        paths.append(str(path))
    with mock.patch.object(edge_tts_service, "TEMP_DIR", tmp_path):
        path, duration = edge_tts_service._concat_edge_chunks(paths, 0)
    assert duration == 2.5
    words = speech_timing.load_speech_timing(path, duration)
    assert [row["word"] for row in words] == [
        "The", "quick", "brown", "fox", "jumps.",
        "Over", "the", "lazy", "dog", "today.",
    ]
    assert words[5]["word"] == "Over" and words[5]["start"] == 1.0
    assert all(row["timing_source"] == "tts_script_timing" for row in words)
    aligned, audit = speech_timing.aligned_provider_words(
        path, "The quick brown fox jumps. Over the lazy dog today.", duration
    )
    assert audit["exact_match_ratio"] == 1.0
    assert len(aligned) == 10

