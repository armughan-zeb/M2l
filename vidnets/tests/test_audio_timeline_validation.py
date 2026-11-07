from server.services.audio_timeline_validation import (
    sound_effect_family,
    validate_audio_edit_timeline,
    validate_whisper_scene_plan,
    whisper_anchor_time,
)


def _scene(start=0.0, narration="Here water lifts the ship.", duration=4.0):
    return {
        "start": start,
        "duration": duration,
        "narration": narration,
        "word_cues": [{
            "timing_quality": "whisper_word_aligned",
            "timed_words": [
                {"index": 0, "text": "Here", "start": 0.0, "absolute_start": start},
                {"index": 1, "text": "water", "start": 0.8, "absolute_start": start + 0.8},
                {"index": 2, "text": "lifts", "start": 1.1, "absolute_start": start + 1.1},
                {"index": 3, "text": "the", "start": 1.4, "absolute_start": start + 1.4},
                {"index": 4, "text": "ship.", "start": 1.6, "absolute_start": start + 1.6},
            ],
        }],
    }


def test_whisper_anchor_requires_exact_contiguous_word_sequence():
    scene = _scene(10.0)
    assert whisper_anchor_time(scene, "water lifts") == 10.8
    assert whisper_anchor_time(scene, "water raises") is None


def test_sound_timeline_accepts_word_anchored_and_boundary_cues(tmp_path):
    asset = tmp_path / "sfx.wav"
    asset.write_bytes(b"fake-audio-for-path-validation")
    scenes = [_scene(0.0, duration=16.0), _scene(16.0, "The canal opens.")]
    scenes[1]["word_cues"] = []
    effects = [
        {"kind": "water_pulse", "src": str(asset), "start": 0.8, "sound_anchor": "water", "sound_intent": "impact"},
        {"kind": "whoosh_soft", "src": str(asset), "start": 16.0, "sound_intent": "transition"},
    ]
    result = validate_audio_edit_timeline(scenes, effects, require_unique_families=True)
    assert result["status"] == "pass"
    assert result["whisper_anchored_count"] == 1
    assert result["transition_boundary_count"] == 1


def test_validator_rejects_whoosh_variants_as_same_family(tmp_path):
    asset = tmp_path / "sfx.wav"
    asset.write_bytes(b"fake-audio-for-path-validation")
    scenes = [_scene(0.0), _scene(20.0)]
    effects = [
        {"kind": "whoosh_soft", "src": str(asset), "start": 0.0, "sound_intent": "transition"},
        {"kind": "edit_whoosh_sucked", "src": str(asset), "start": 20.0, "sound_intent": "transition"},
    ]
    result = validate_audio_edit_timeline(scenes, effects, require_unique_families=True)
    assert result["status"] == "fail"
    assert any(error["code"] == "similar_sound_effect_families" for error in result["errors"])
    assert sound_effect_family("whoosh_soft") == sound_effect_family("edit_whoosh_sucked")


def test_validator_rejects_drift_from_whisper_word_anchor(tmp_path):
    asset = tmp_path / "sfx.wav"
    asset.write_bytes(b"fake-audio-for-path-validation")
    result = validate_audio_edit_timeline(
        [_scene(0.0)],
        [{"kind": "water_pulse", "src": str(asset), "start": 1.0, "sound_anchor": "water"}],
    )
    assert result["status"] == "fail"
    assert any(error["code"] == "whisper_sound_anchor_mismatch" for error in result["errors"])


def test_whisper_scene_plan_requires_exact_script_coverage_and_word_aligned_cuts():
    script = "The moon rose. Apollo returned home."
    words = [
        {"index": 0, "word": "The", "start": 0.0},
        {"index": 1, "word": "moon", "start": 0.3},
        {"index": 2, "word": "rose.", "start": 0.7},
        {"index": 3, "word": "Apollo", "start": 1.2},
        {"index": 4, "word": "returned", "start": 1.6},
        {"index": 5, "word": "home.", "start": 2.1},
    ]
    windows = [
        {"scene_index": 0, "start_seconds": 0.0, "end_seconds": 1.2, "word_start_index": 0, "word_end_index": 3},
        {"scene_index": 1, "start_seconds": 1.2, "end_seconds": 2.8, "word_start_index": 3, "word_end_index": 6},
    ]
    result = validate_whisper_scene_plan(
        script,
        ["The moon rose.", "Apollo returned home."],
        windows,
        words,
        2.8,
    )
    assert result["status"] == "pass"

    windows[1]["start_seconds"] = 1.55
    result = validate_whisper_scene_plan(
        script,
        ["The moon rose.", "Apollo returned home."],
        windows,
        words,
        2.8,
    )
    assert result["status"] == "fail"
    assert any(error["code"] == "scene_cut_not_on_whisper_word" for error in result["errors"])


def test_whisper_scene_plan_rejects_missing_or_repeated_narration():
    result = validate_whisper_scene_plan(
        "The ship reached port safely.",
        ["The ship reached port.", "port safely."],
        [],
        [],
        5.0,
        timing_source="tts_script_timing_fallback",
    )
    assert result["status"] == "fail"
    assert any(error["code"] == "scene_script_coverage_mismatch" for error in result["errors"])


def test_whisper_anchor_can_sync_to_the_end_of_a_spoken_word():
    scene = _scene(10.0)
    scene["word_cues"][0]["timed_words"][1]["absolute_end"] = 11.1
    assert whisper_anchor_time(scene, "water", edge="end") == 11.1
