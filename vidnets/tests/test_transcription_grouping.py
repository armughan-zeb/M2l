import pytest
from server.services.transcription_service import _compute_next_word_group_end, align_whisper_words_to_script
from server.services.audio_first_pipeline import _build_script_timing_fallback

def test_transcription_grouping_avoids_dangling_preposition():
    words = [
        {"word": "According", "start": 0.0, "end": 0.5},
        {"word": "to", "start": 0.5, "end": 0.8},
        {"word": "accounts", "start": 0.8, "end": 1.2},
        {"word": "involved", "start": 1.2, "end": 1.6},
        {"word": "in", "start": 1.6, "end": 1.8},
        {"word": "the", "start": 1.8, "end": 2.0},
        {"word": "incident.", "start": 2.0, "end": 2.5},
    ]

    end_idx = _compute_next_word_group_end(words, 0, 3)
    assert end_idx == 3
    first_chunk = [w["word"] for w in words[0:end_idx]]
    assert first_chunk == ["According", "to", "accounts"]

    end_idx2 = _compute_next_word_group_end(words, 3, 3)
    chunk2 = [w["word"] for w in words[3:end_idx2]]
    assert chunk2[-1].lower() not in {"the", "a", "an", "to", "of", "in"}

def test_transcription_grouping_respects_sentence_boundary():
    words = [
        {"word": "GTA", "start": 0.0, "end": 0.5},
        {"word": "VI.", "start": 0.5, "end": 1.0},
        {"word": "It", "start": 1.0, "end": 1.2},
        {"word": "is", "start": 1.2, "end": 1.4},
        {"word": "coming.", "start": 1.4, "end": 1.8},
    ]
    end_idx = _compute_next_word_group_end(words, 0, 4)
    assert end_idx == 2
    assert [w["word"] for w in words[0:end_idx]] == ["GTA", "VI."]


def test_final_word_ends_on_exact_audio_clock():
    duration = 43.566984
    words, _audit = align_whisper_words_to_script(
        "The tower opened.",
        [
            {"word": "The", "start": 0.0, "end": 0.2},
            {"word": "tower", "start": 0.2, "end": 0.7},
            {"word": "opened", "start": 0.7, "end": 43.6},
        ],
        duration,
    )
    assert words[-1]["end"] == duration
    assert _build_script_timing_fallback("The tower opened.", duration)[-1]["end"] == duration
