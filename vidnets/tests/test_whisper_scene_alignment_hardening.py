import json
import re
from unittest import mock

from server.services import scene_splitter


def _norm_words(value):
    return [re.sub(r"[^a-z0-9]+", "", token.casefold()) for token in str(value or "").split() if re.sub(r"[^a-z0-9]+", "", token)]


def test_long_scene_split_never_duplicates_short_script_tail():
    source = "First fact. Second fact."
    parts = scene_splitter._split_text_for_scene_parts(source, 8)

    assert len(parts) == 4
    assert [word for part in parts for word in _norm_words(part)] == _norm_words(source)


def test_long_scene_splits_snap_back_to_whisper_script_word_boundaries():
    script = (
        "Berlin was cut off from road and rail supplies in June 1948. "
        "Allied pilots began flying food and fuel into the divided city. "
        "The airlift continued through the winter and changed the course of the crisis."
    )
    tokens = script.split()
    word_times = []
    cursor = 0.0
    for index, token in enumerate(tokens):
        duration = 0.25
        word_times.append({"word": token, "start": round(cursor, 3), "end": round(cursor + duration, 3), "index": index})
        cursor += duration + (0.18 if token.endswith((".", ",", ";", ":")) else 0.06)
    audio_duration = cursor + 0.4

    scenes = scene_splitter.align_expected_scenes_to_transcript(
        script,
        "Every Beat",
        word_times,
        audio_duration,
    )

    assert scenes
    assert [word for scene in scenes for word in _norm_words(scene.text)] == _norm_words(script)
    assert abs(scenes[0].start_time) < 0.01
    assert abs(scenes[-1].end_time - audio_duration) < 0.01
    for left, right in zip(scenes, scenes[1:]):
        assert abs(left.end_time - right.start_time) < 0.01
        next_scene_first_word = sum(len(_norm_words(item.text)) for item in scenes[: scenes.index(right)])
        assert abs(right.start_time - word_times[next_scene_first_word]["start"]) < 0.03


def test_dotted_initialisms_stay_together_and_tiny_beats_are_merged():
    script = (
        "At 11:40 p.m. on April 14, 1912, Titanic struck an iceberg. "
        "The sinking killed 1,517 people. U.S. and British investigations followed. "
        "The wreck was found in 1985."
    )
    units = scene_splitter._split_script_visual_units(script)
    assert any("p.m." in unit for unit in units)
    assert any("U.S." in unit for unit in units)
    assert not any(unit.strip() in {"p.", "m.", "U.", "S."} for unit in units)

    transcript_words = []
    cursor = 0.0
    for token in script.split():
        parts = {"p.m.": ["p.", "m."], "U.S.": ["U.", "S."]}.get(token, [token])
        for part in parts:
            duration = 0.24
            transcript_words.append({
                "word": part,
                "start": round(cursor, 3),
                "end": round(cursor + duration, 3),
            })
            cursor += duration + (0.38 if part.endswith((".", ",")) else 0.05)
    audio_duration = cursor + 0.2

    scenes = scene_splitter.align_expected_scenes_to_transcript(
        script, "Every Beat", transcript_words, audio_duration
    )
    combined_text = " ".join(scene.text for scene in scenes)
    assert "p.m." in combined_text
    assert "U.S." in combined_text
    assert not any(scene.text.strip() in {"p.", "m.", "U.", "S."} for scene in scenes)
    assert all(scene.duration >= 1.2 for scene in scenes)
    assert abs(scenes[-1].end_time - audio_duration) < 0.01


def test_llm_split_must_preserve_every_script_word_exactly():
    script = "The moon landing changed history. The crew returned safely."
    response = {
        "scenes": [
            {"scene_id": 1, "narration_text": "The moon landing changed history."},
            {"scene_id": 2, "narration_text": "The crew returned safely."},
        ]
    }
    with mock.patch("services.openrouter_llm.OpenRouterLLM") as llm_type:
        llm_type.return_value.chat.return_value = json.dumps(response)
        scenes = scene_splitter.split_scenes_llm(script, 12.0, "key", "test/model")
    assert scenes is not None
    assert [word for scene in scenes for word in _norm_words(scene.text)] == _norm_words(script)

    response["scenes"][1]["narration_text"] = "The crew returned safely and celebrated."
    with mock.patch("services.openrouter_llm.OpenRouterLLM") as llm_type:
        llm_type.return_value.chat.return_value = json.dumps(response)
        rejected = scene_splitter.split_scenes_llm(script, 12.0, "key", "test/model")
    assert rejected is None
