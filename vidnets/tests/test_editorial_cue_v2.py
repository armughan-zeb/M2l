from __future__ import annotations

from pathlib import Path
import hashlib

from server.services.editorial_cue_planner import plan_editorial_cues
from server.services.semantic_edit_engine import build_semantic_edit_manifest
from server.services.semantic_ffmpeg_renderer import (
    _gate_timeline_filter,
    _scene_graphic_interval,
)
from server.services.storyboard_v2 import (
    ContainerV1,
    GenerationRequestV2,
    SceneContractV1,
    ScriptPlanV1,
    StoryboardV2,
    WordTimingV1,
    normalize_script_with_map,
)
from server.services.storyboard_v2.text import tokenize_source
from server.services.native_engine.libraries.professional_templates import (
    PROFESSIONAL_TEMPLATES,
)


def _timed_scene(
    text: str,
    *,
    scene_id: str = "scene-1",
    container_id: str = "item-1",
    char_start: int = 0,
    token_start: int = 0,
    word_start: int = 0,
    start: float = 0.0,
    duration: float = 6.0,
):
    tokens = list(tokenize_source(text))
    words = []
    for offset, token in enumerate(tokens):
        spoken_start = start + 0.45 + offset * 0.26
        words.append(WordTimingV1(
            index=word_start + offset,
            text=token.text,
            start_seconds=spoken_start,
            end_seconds=spoken_start + 0.18,
            confidence=0.99,
        ))
    scene = {
        "scene_id": scene_id,
        "order": 0,
        "container_id": container_id,
        "text": text,
        "char_start": char_start,
        "char_end": char_start + len(text),
        "token_start": token_start,
        "token_end": token_start + len(tokens),
        "word_start_index": word_start,
        "word_end_index": word_start + len(words),
        "start_seconds": start,
        "end_seconds": start + duration,
        "timing_quality": "word_aligned",
        "alignment_confidence": 0.99,
    }
    return scene, words


def test_listicle_item_heading_is_exact_and_container_scoped():
    first_text = "Number 7: Snake Milker. The work requires collecting venom safely."
    second_text = "The technician keeps both hands behind a protective barrier."
    first, first_words = _timed_scene(first_text, duration=7.0)
    second, second_words = _timed_scene(
        second_text,
        scene_id="scene-2",
        char_start=len(first_text) + 1,
        token_start=len(tokenize_source(first_text)),
        word_start=len(first_words),
        start=7.0,
        duration=6.0,
    )
    cues = plan_editorial_cues(
        [first, second],
        [*first_words, *second_words],
        containers=[{
            "container_id": "item-1",
            "kind": "listicle_item",
            "ordinal": 7,
            "canonical_subject": "Snake Milker",
        }],
        editorial_beats=[{}, {}],
    )

    assert len(cues) == 1
    cue = cues[0]
    assert cue.scene_id == "scene-1"
    assert cue.container_id == "item-1"
    assert cue.cue_type == "item_heading"
    assert cue.text == "Number 7: Snake Milker"
    assert cue.props["title"] == cue.text
    assert list(cue.props["items"]) == [cue.text]
    assert "KEY POINT" not in str(cue.props)
    assert cue.word_start_index == 0
    assert cue.scene_local_start_seconds < 0.5
    assert cue.scene_local_end_seconds < first["end_seconds"]


def test_statistic_keeps_the_complete_2200_value_and_unit():
    text = "The tower employs 2,200 workers during the annual maintenance cycle."
    scene, words = _timed_scene(text)
    cues = plan_editorial_cues([scene], words, editorial_beats=[{}])

    assert len(cues) == 1
    cue = cues[0]
    assert cue.cue_type == "statistic"
    assert cue.text == "2,200 workers"
    assert cue.props["value"] == "2,200 workers"
    assert text[cue.char_start:cue.char_end] == cue.text
    assert cue.word_end_index - cue.word_start_index == 3


def test_all_supported_cue_types_require_grounded_text():
    cases = [
        ("On April 15, 1912 the ship changed course.", {}, "date", "April 15, 1912"),
        ('The witness said "the sea was calm" before dawn.', {}, "quote", "the sea was calm"),
        (
            "Dr. Maya Chen led the rescue team.",
            {"role": "person", "treatment": "person", "emphasis": "Dr. Maya Chen", "_emphasis_explicit": True},
            "person", "Dr. Maya Chen",
        ),
        (
            "The convoy entered the North Atlantic before midnight.",
            {"role": "location", "treatment": "location", "emphasis": "North Atlantic", "_emphasis_explicit": True},
            "location", "North Atlantic",
        ),
        (
            "The final warning changed the outcome.",
            {"role": "evidence", "treatment": "keyword", "importance": 0.9, "emphasis": "final warning", "_emphasis_explicit": True},
            "highlight", "final warning",
        ),
    ]
    for text, beat, expected_type, expected_text in cases:
        scene, words = _timed_scene(text)
        cues = plan_editorial_cues([scene], words, editorial_beats=[beat])
        assert len(cues) == 1
        assert cues[0].cue_type == expected_type
        assert cues[0].text == expected_text
        assert text[cues[0].char_start:cues[0].char_end] == expected_text


def test_word_timing_uses_spoken_padding_and_minimum_readability():
    text = "The tower employs 2,200 workers during maintenance."
    scene, words = _timed_scene(text)
    cue = plan_editorial_cues([scene], words, editorial_beats=[{}])[0]
    matched = [word for word in words if cue.word_start_index <= word.index < cue.word_end_index]
    minimum = min(2.8, max(0.9, 0.9 + 0.28 * (3 - 1)))
    assert cue.scene_local_start_seconds <= matched[0].start_seconds - 0.08 + 0.001
    assert cue.scene_local_end_seconds >= matched[-1].end_seconds + 0.55 - 0.001
    assert cue.scene_local_end_seconds - cue.scene_local_start_seconds >= minimum - 0.001


def test_three_reasons_is_grounded_without_inventing_list_rows():
    text = "There were three reasons the warning failed: timing, distance, and silence."
    scene, words = _timed_scene(text)
    cues = plan_editorial_cues(
        [scene],
        words,
        containers=[{"container_id": "item-1", "kind": "listicle_item", "canonical_subject": "warning failure"}],
        editorial_beats=[{}],
    )

    assert len(cues) == 1
    cue = cues[0]
    assert cue.text == "three reasons"
    assert list(cue.props["items"]) == ["three reasons"]
    assert not cue.props["line2"]
    assert not cue.props["line3"]


def test_repeated_phrase_uses_explicit_source_span_not_first_text_match():
    text = "The signal faded, but the signal returned."
    scene, words = _timed_scene(text)
    second_start = text.rindex("the signal")
    second_end = second_start + len("the signal")
    cues = plan_editorial_cues(
        [scene],
        words,
        editorial_beats=[{
            "role": "evidence",
            "treatment": "keyword",
            "importance": 0.94,
            "emphasis": "the signal",
            "emphasis_char_start": second_start,
            "emphasis_char_end": second_end,
            "_emphasis_explicit": True,
        }],
    )

    assert len(cues) == 1
    cue = cues[0]
    assert cue.char_start == second_start
    assert cue.char_end == second_end
    # The second occurrence maps to the later transcript indexes.
    assert cue.word_start_index == 4


def test_negative_and_unmatched_cues_are_suppressed_instead_of_estimated():
    text = "An ordinary connective sentence keeps the visual breathing."
    scene, words = _timed_scene(text)
    negative = plan_editorial_cues(
        [scene],
        words,
        editorial_beats=[{
            "treatment": "none",
            "importance": 0.99,
            "emphasis": "ordinary connective",
            "_emphasis_explicit": True,
            "selected_for_text": False,
        }],
    )
    no_words = plan_editorial_cues(
        [scene],
        [],
        editorial_beats=[{
            "treatment": "keyword",
            "importance": 0.99,
            "emphasis": "ordinary connective",
            "_emphasis_explicit": True,
        }],
    )
    assert negative == ()
    assert no_words == ()


def test_brain_timeout_still_recovers_one_full_named_person_with_whisper_bounds():
    text = "Prince Harry challenged the decision while King Charles III remained silent."
    scene, words = _timed_scene(text)
    cues = plan_editorial_cues(
        [scene],
        words,
        editorial_beats=[{
            "treatment": "none",
            "selected_for_text": False,
        }],
    )

    assert len(cues) == 1
    cue = cues[0]
    assert cue.cue_type == "person"
    assert cue.text == "Prince Harry"
    assert cue.word_start_index == 0
    assert cue.word_end_index == 2
    assert cue.props["_word_highlight_mode"] == "whisper"
    assert cue.template == "lower-third"


def test_placement_avoids_subject_and_caption_and_fails_closed_without_a_slot():
    text = "The tower employs 2,200 workers during maintenance."
    scene, words = _timed_scene(text)
    safe = plan_editorial_cues(
        [scene],
        words,
        editorial_beats=[{}],
        captions_enabled=True,
        media=[{"visual_decision": {"subject_bbox": [0.05, 0.42, 0.55, 0.73]}}],
    )
    blocked = plan_editorial_cues(
        [scene],
        words,
        editorial_beats=[{}],
        captions_enabled=True,
        media=[{"visual_decision": {"subject_bbox": [0.01, 0.01, 0.99, 0.77]}}],
    )
    assert safe[0].placement["position"] in {"top_left", "top_right"}
    assert blocked == ()


def test_manifest_v2_has_no_forced_coverage_and_exposes_exact_cue_bounds():
    factual = "The tower employs 2,200 workers during maintenance."
    ordinary = "A quiet wide shot lets the sequence breathe."
    first, first_words = _timed_scene(factual, duration=6.0)
    second, second_words = _timed_scene(
        ordinary,
        scene_id="scene-2",
        container_id="item-2",
        char_start=len(factual) + 1,
        token_start=len(tokenize_source(factual)),
        word_start=len(first_words),
        start=6.0,
        duration=5.0,
    )
    manifest = build_semantic_edit_manifest(
        [factual, ordinary],
        [6.0, 5.0],
        topic="Extreme jobs",
        scene_contracts=[first, second],
        word_timings=[*first_words, *second_words],
        containers=[
            {"container_id": "item-1", "kind": "listicle_item", "canonical_subject": "tower workers"},
            {"container_id": "item-2", "kind": "narrative", "canonical_subject": "wide shot"},
        ],
        config={"editorial_cue_v2_enabled": True},
    )

    assert manifest["metrics"]["editorial_cue_v2_count"] == 1
    assert manifest["scenes"][1]["graphic"]["template_id"] == "full-bleed"
    graphic = manifest["scenes"][0]["graphic"]
    cue = graphic["cue"]
    assert graphic["scene_local_start"] == cue["scene_local_start_seconds"]
    assert graphic["scene_local_end"] == cue["scene_local_end_seconds"]
    assert graphic["duration"] < manifest["scenes"][0]["duration"]


def test_consecutive_dates_rotate_compatible_templates_without_changing_text():
    first_text = "The official showcase begins on August 27, 2026."
    second_text = "The game launches on November 19, 2026."
    first, first_words = _timed_scene(first_text, duration=6.0)
    second, second_words = _timed_scene(
        second_text,
        scene_id="scene-2",
        container_id="item-2",
        char_start=len(first_text) + 1,
        token_start=len(tokenize_source(first_text)),
        word_start=len(first_words),
        start=6.0,
        duration=6.0,
    )

    cues = plan_editorial_cues(
        [first, second],
        [*first_words, *second_words],
        variation_seed=17,
    )

    assert [cue.text for cue in cues] == ["August 27, 2026", "November 19, 2026"]
    assert cues[0].template != cues[1].template


def test_shadow_mode_records_cues_without_changing_legacy_graphics():
    text = "The tower employs 2,200 workers during maintenance."
    scene, words = _timed_scene(text)
    manifest = build_semantic_edit_manifest(
        [text],
        [6.0],
        topic="Extreme jobs",
        scene_contracts=[scene],
        word_timings=words,
        config={"pipeline_v2_mode": "shadow"},
    )
    assert manifest["metrics"]["editorial_cue_v2_enabled"] is False
    assert manifest["metrics"]["editorial_cue_v2_shadow"] is True
    assert manifest["editorial_cues"] == []
    assert manifest["editorial_cues_shadow"][0]["text"] == "2,200 workers"


def test_production_manifest_consumes_timed_storyboard_and_preserves_safe_metadata():
    script = "Number 1: Tower Climber employs 2,200 workers during maintenance."
    normalized, normalization_map = normalize_script_with_map(script)
    source_tokens = list(tokenize_source(script))
    words = tuple(
        WordTimingV1(
            index=index,
            text=token.text,
            start_seconds=0.42 + index * 0.28,
            end_seconds=0.61 + index * 0.28,
            confidence=0.99,
        )
        for index, token in enumerate(source_tokens)
    )
    container = ContainerV1(
        container_id="item_tower_climber",
        kind="listicle_item",
        ordinal=1,
        canonical_subject="Tower Climber",
        char_start=0,
        char_end=len(script),
        text=script,
        marker_start=0,
        marker_end=len("Number 1:"),
        explicit_marker=True,
    )
    scene = SceneContractV1(
        scene_id="scene_tower_climber",
        order=0,
        container_id=container.container_id,
        text=script,
        char_start=0,
        char_end=len(script),
        token_start=0,
        token_end=len(source_tokens),
        visual_intent="named_subject_action",
        positive_subjects=("Tower Climber",),
        forbidden_subjects=("Snake Milker",),
    ).with_timing(
        word_start_index=0,
        word_end_index=len(words),
        start_seconds=0.0,
        end_seconds=6.0,
        timing_quality="exact",
        alignment_confidence=0.99,
    )
    plan = ScriptPlanV1(
        script_hash=hashlib.sha256(script.encode("utf-8")).hexdigest(),
        source_script=script,
        normalized_script=normalized,
        normalization_to_original=normalization_map,
        detected_format="listicle",
        format_confidence=1.0,
        language="en",
        containers=(container,),
        scenes=(scene,),
    )
    storyboard = StoryboardV2.new(
        job_id="job_editorial_wiring",
        run_id="run_editorial_wiring",
        request=GenerationRequestV2(
            script=script,
            content_title="Extreme Jobs",
            output_name="extreme-jobs",
            pipeline_v2_mode="on",
        ),
        script_plan=plan,
    ).with_script_plan(plan, word_timings=words)
    subject_bbox = [0.04, 0.38, 0.54, 0.72]
    safe_regions = [[0.62, 0.06, 0.92, 0.31]]

    manifest = build_semantic_edit_manifest(
        [script],
        [6.0],
        media=[{
            "path": "C:/verified-tower-climber.jpg",
            "visual_decision": {
                "status": "accepted",
                "confidence": 97,
                "subject_bbox": subject_bbox,
                "safe_text_regions": safe_regions,
            },
        }],
        editorial_beats=[{
            "role": "evidence",
            "treatment": "listicle",
            "importance": 0.98,
            "emphasis": "KEY POINT",  # deliberately ungrounded; must never render
        }],
        topic="Extreme Jobs",
        config={
            "storyboard_v2": storyboard,
            "editorial_cue_v2_enabled": True,
            "captions_enabled": True,
        },
    )

    assert manifest["metrics"]["editorial_cue_v2_count"] == 1
    assert len(manifest["editorial_cues"]) == 1
    cue = manifest["editorial_cues"][0]
    rendered_scene = manifest["scenes"][0]
    assert rendered_scene["scene_id"] == scene.scene_id
    assert rendered_scene["container_id"] == container.container_id
    assert cue["scene_id"] == scene.scene_id
    assert cue["container_id"] == container.container_id
    assert script[cue["char_start"]:cue["char_end"]] == cue["text"]
    assert cue["text"] == "Number 1: Tower Climber"
    assert cue["word_start_index"] is not None
    assert cue["word_end_index"] > cue["word_start_index"]
    assert 0 <= cue["scene_local_start_seconds"] < cue["scene_local_end_seconds"] <= 6.0
    assert "KEY POINT" not in str(rendered_scene["graphic"]["props"])
    assert cue["placement"]["subject_bbox"] == subject_bbox
    assert cue["placement"]["caption_band"] == [0.04, 0.76, 0.96, 0.98]
    assert rendered_scene["design"]["safe_text_regions"] == safe_regions
    assert rendered_scene["design"]["focal_bbox"] == subject_bbox


def test_ffmpeg_gate_shifts_animation_time_and_gates_every_layer():
    drawtext = "drawtext=text='TEST':fontsize=64:x=min(200\\,t*40):y=100:enable=gte(t\\,0.16)"
    drawbox = "drawbox=x=20:y=20:w='max(6\\,t*100)':h=4:color=white:t=fill"
    gated_text = _gate_timeline_filter(drawtext, 1.25, 2.75)
    gated_box = _gate_timeline_filter(drawbox, 1.25, 2.75)

    assert "between(t\\,1.250\\,2.750)" in gated_text
    assert "gte((t-1.250)\\,0.16)" in gated_text
    assert "(t-1.250)*40" in gated_text
    assert "between(t\\,1.250\\,2.750)" in gated_box
    assert "(t-1.250)*100" in gated_box

    scene = {
        "start": 10.0,
        "duration": 5.0,
        "graphic": {"start": 11.25, "duration": 1.5},
    }
    assert _scene_graphic_interval(scene) == (1.25, 2.75)


def test_v2_specialized_date_and_listicle_templates_are_not_replaced_by_generic_card():
    palette = {"primary": "#ffffff", "accent": "#d8a84e", "surface": "#11151d"}
    date = PROFESSIONAL_TEMPLATES["date_numeral"](
        palette,
        {
            "_editorial_contract": True,
            "_cue_schema_version": 2,
            "_use_generic_contract": False,
            "_contract_text": "APRIL 15, 1912",
            "date": "APRIL 15, 1912",
            "label": "APRIL 15, 1912",
            "kicker": "",
        },
        scene_duration=2.4,
    )["chains"]
    listicle = PROFESSIONAL_TEMPLATES["listicle_steps"](
        palette,
        {
            "_editorial_contract": True,
            "_cue_schema_version": 2,
            "_use_generic_contract": False,
            "title": "NUMBER 7: SNAKE MILKER",
            "line1": "NUMBER 7: SNAKE MILKER",
        },
        scene_duration=2.4,
    )["chains"]
    date_texts = {
        chain.split("text='", 1)[1].split("'", 1)[0]
        for chain in date if chain.startswith("drawtext=") and "text='" in chain
    }
    assert date_texts == {"APRIL 15, 1912"}
    assert all("A TURNING POINT" not in chain for chain in date)
    listicle_texts = {
        chain.split("text='", 1)[1].split("'", 1)[0]
        for chain in listicle if chain.startswith("drawtext=") and "text='" in chain
    }
    assert listicle_texts == {r"NUMBER 7\: SNAKE MILKER"}
    assert all("01" not in chain and "THE THREE THINGS" not in chain for chain in listicle)


def test_remotion_graphics_use_nested_sequences_for_cue_bounds():
    source = (
        Path(__file__).resolve().parents[1]
        / "src" / "remotion" / "DocumentaryComposition.jsx"
    ).read_text(encoding="utf-8")
    assert "from={graphicFrom}" in source
    assert "durationInFrames={graphicDuration}" in source
    assert "graphic.scene_local_start" in source
    graphics_end = source.index("{/* 3. Subtle Cinematic Atmosphere Overlays */")
    graphics_source = source[:graphics_end]
    assert "startFrom=" not in graphics_source
    # Audio source trimming is intentional and independent of graphic-local
    # Sequence timing; preserve the exact sound-effect source offset.
    assert "soundCue.trim_start_seconds" in source


def test_proper_name_rejects_conjunction_and_possessive():
    from services.editorial_cue_planner import plan_editorial_cues

    text = "And Rockstar's most anticipated game set records across the globe."
    scene, words = _timed_scene(text)
    cues = list(plan_editorial_cues([scene], words, editorial_beats=[{}]))

    # Must never emit a lower-third person cue titled "And Rockstar's"
    person_cues = [c for c in cues if c.cue_type == "person"]
    assert len(person_cues) == 0
