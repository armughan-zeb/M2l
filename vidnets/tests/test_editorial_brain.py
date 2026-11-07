import pytest

from server.services import editorial_brain
from server.services.documentary_editing import build_documentary_edit_plan


def test_editorial_payload_accepts_fenced_or_preambled_json():
    assert editorial_brain._parse_editorial_payload(
        "Here is the plan:\n```json\n{\"beats\": [{\"scene_index\": 0}]}\n```"
    )["beats"][0]["scene_index"] == 0
    assert editorial_brain._parse_editorial_payload(
        "Notes before {\"beats\": [{\"scene_index\": 1}]} notes after"
    )["beats"][0]["scene_index"] == 1


def test_editorial_brain_uses_openrouter_model_and_preserves_partial_chunks(monkeypatch):
    seen_models = []

    def fake_chunk(_key, model, rows, _timeout):
        seen_models.append(model)
        if rows[0]["scene_index"] >= 24:
            raise RuntimeError("bad chunk")
        return [
            {
                "scene_index": row["scene_index"], "role": "evidence",
                "emphasis": "critical evidence", "treatment": "keyword",
                "animation": "scale_pop", "background": "none",
                "transition": "cut", "energy": "tense", "confidence": 0.9,
            }
            for row in rows
        ]

    monkeypatch.setattr(editorial_brain, "_plan_chunk", fake_chunk)
    texts = ["The critical evidence changed the case."] * 30
    beats = editorial_brain.plan_editorial_beats(
        texts,
        {"openrouter_api_key": "test", "llm_model": "openai/gpt-6-luna", "groq_model": "ignored/legacy"},
    )

    assert set(seen_models) == {"openai/gpt-6-luna"}
    assert all(beat["source"] == "openrouter" for beat in beats[:24])
    assert all(beat["source"] == "deterministic" for beat in beats[24:])


def test_edit_plan_varies_templates_and_only_highlights_short_phrases():
    texts = [
        "The first warning changed the investigation.",
        "Only 27 percent of the evidence survived.",
        "The secret record was finally revealed.",
        "The final discovery confirmed the truth.",
    ]
    plan = build_documentary_edit_plan(
        texts,
        [4.0] * len(texts),
        {"documentary_editing_enabled": True, "documentary_density": "dynamic"},
        ["Case"] * len(texts),
    )

    templates = {
        scene["overlay_template"] or scene["highlight_template"]
        for scene in plan["scenes"]
        if scene["overlay_template"] or scene["highlight_template"]
    }
    assert len(templates) >= 2
    assert all(len(scene["key_highlight"].split()) <= 5 for scene in plan["scenes"])
    assert any(scene["text_background"] == "none" for scene in plan["scenes"])


def test_llm_text_budget_prevents_forced_text_on_every_scene(monkeypatch):
    def fake_chunk(_key, _model, rows, _timeout):
        return [
            {
                "scene_index": row["scene_index"], "role": "reveal",
                "emphasis": "critical evidence", "treatment": "keyword",
                "animation": "scale_pop", "background": "marker",
                "transition": "quick_zoom_in", "energy": "climactic",
                "confidence": 0.9, "importance": 0.9,
            }
            for row in rows
        ]

    monkeypatch.setattr(editorial_brain, "_plan_chunk", fake_chunk)
    beats = editorial_brain.plan_editorial_beats(
        ["The critical evidence changed everything."] * 20,
        {"openrouter_api_key": "test", "documentary_density": "balanced"},
    )

    visible = [beat for beat in beats if beat["treatment"] != "none"]
    assert len(visible) == 1
    assert all(beat["emphasis"] == "" for beat in beats if beat["treatment"] == "none")


def test_editorial_selection_preserves_dates_and_varied_key_facts(monkeypatch):
    kinds = [
        ("milestone", "April 10 1912", "date"),
        ("milestone", "April 14 1912", "date"),
        ("milestone", "April 15 1912", "date"),
        ("scale", "1,500 people", "counter"),
        ("warning", "final warning", "keyword"),
        ("turning_point", "struck the iceberg", "title"),
        ("consequence", "ship split apart", "keyword"),
        ("contrast", "help arrived late", "keyword"),
    ]

    def fake_chunk(_key, _model, rows, _timeout):
        beats = []
        for row in rows:
            kind, phrase, treatment = kinds[row["scene_index"] % len(kinds)]
            beats.append({
                "scene_index": row["scene_index"], "role": "evidence",
                "emphasis": phrase, "treatment": treatment,
                "animation": "fade", "background": "none", "transition": "cut",
                "energy": "tense", "confidence": 0.92, "importance": 0.9,
                "highlight_kind": kind, "editorial_reason": "major narrated fact",
            })
        return beats

    monkeypatch.setattr(editorial_brain, "_plan_chunk", fake_chunk)
    texts = [
        f"Investigators confirmed {kinds[index % len(kinds)][1]} during the disaster."
        for index in range(21)
    ]
    beats = editorial_brain.plan_editorial_beats(
        texts,
        {"openrouter_api_key": "test", "documentary_density": "balanced"},
    )

    selected = [beat for beat in beats if beat["selected_for_text"]]
    selected_kinds = [beat["highlight_kind"] for beat in selected]
    assert len(selected) == 6
    assert selected_kinds.count("milestone") == 3
    assert len(set(selected_kinds) - {"milestone"}) >= 3


def test_editorial_brain_rejects_dangling_phrases_and_generic_single_words():
    cases = [
        ("Passengers waited for instructions.", "Passengers"),
        ("Many lives were lost during the night.", "lost"),
        ("It was designed to stay afloat even after flooding.", "designed to stay afloat even"),
        ("The lookout spotted an iceberg directly ahead.", "lookout spotted an iceberg directly"),
        ("The International Convention for the safety of life followed.", "International Convention for the safety"),
    ]
    for text, phrase in cases:
        beat = editorial_brain._normalize_beat(
            {
                "role": "evidence", "emphasis": phrase, "treatment": "keyword",
                "animation": "fade", "background": "marker", "transition": "cut",
                "energy": "normal", "confidence": 0.9, "importance": 0.9,
                "highlight_kind": "key_fact", "editorial_reason": "candidate",
            },
            text,
            0,
            1,
        )
        assert beat["emphasis"] == ""
        assert beat["treatment"] == "none"


def test_single_emphasis_word_skips_generic_subject_words():
    # A generic word ("people", "ship") reads as a meaningless card; the brain
    # must pick a concrete, content-bearing token instead.
    assert editorial_brain._single_emphasis_word("thousands of people") == "thousands"
    assert editorial_brain._single_emphasis_word("the ship went down") == "down"
    assert editorial_brain._single_emphasis_word("70") == "70"
    assert editorial_brain._single_emphasis_word("iceberg") == "iceberg"


def test_deterministic_date_keeps_full_narrated_year():
    beat = editorial_brain.deterministic_editorial_beat(
        "The Titanic departed on April 10 1912 from Southampton.",
        0,
        1,
    )

    assert beat["emphasis"] == "April 10 1912"
    assert beat["treatment"] == "date"


def test_full_date_wins_when_scene_also_contains_a_statistic():
    beat = editorial_brain._normalize_beat(
        {
            "role": "statistic", "emphasis": "1,500 people", "treatment": "counter",
            "animation": "scale_pop", "background": "none", "transition": "cut",
            "energy": "tense", "confidence": 0.9, "importance": 0.9,
            "highlight_kind": "scale", "editorial_reason": "large human toll",
        },
        "More than 1,500 people died on April 15 1912.",
        0,
        1,
    )

    assert beat["emphasis"] == "April 15 1912"
    assert beat["treatment"] == "date"
    assert beat["highlight_kind"] == "milestone"


def test_deterministic_fallback_obeys_sparse_global_budget(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("VIDZEO_OPENROUTER_API_KEY", raising=False)
    texts = ["Only 27 percent of the evidence survived."] * 20

    beats = editorial_brain.plan_editorial_beats(
        texts,
        {"openrouter_api_key": "", "documentary_density": "balanced"},
    )

    selected = [beat for beat in beats if beat["selected_for_text"]]
    assert len(selected) == 1
    assert all(beat["highlight_kind"] == "scale" for beat in selected)
    assert all(beat["treatment"] == "none" for beat in beats if not beat["selected_for_text"])


def test_balanced_density_keeps_sparse_editorial_cadence():
    kinds = ["key_fact", "warning", "consequence", "contrast", "scale", "human_stakes"]
    beats = [
        {
            "scene_index": index,
            "role": "evidence",
            "emphasis": f"Important fact {index}",
            "treatment": "keyword",
            "highlight_kind": kinds[index % len(kinds)],
            "importance": 0.72,
            "confidence": 0.8,
        }
        for index in range(12)
    ]

    selected = editorial_brain._select_sparse_beats(
        beats,
        {
            "documentary_density": "balanced",
            "_editorial_scene_starts": [index * 3.0 for index in range(12)],
            "_editorial_total_duration": 36.0,
        },
    )

    assert sum(1 for beat in selected if beat["selected_for_text"]) == 3


def test_nonconsecutive_llm_word_bag_is_rejected():
    beat = editorial_brain._normalize_beat(
        {
            "role": "reveal", "emphasis": "Titanic final warning", "treatment": "keyword",
            "animation": "fade", "background": "none", "transition": "cut", "energy": "tense",
        },
        "Titanic received a warning during its final voyage.",
        0,
        1,
    )

    assert beat["emphasis"] == ""
    assert beat["treatment"] == "none"


def test_strong_grounded_fact_supplements_an_explicit_empty_llm_scene():
    beat = editorial_brain._normalize_beat(
        {
            "role": "context", "emphasis": "", "treatment": "none",
            "animation": "none", "background": "none", "transition": "cut", "energy": "normal",
            "confidence": 0.8, "importance": 0.2, "highlight_kind": "none", "editorial_reason": "ordinary",
        },
        "Titanic was the largest passenger ship ever constructed.",
        0,
        1,
    )

    assert beat["treatment"] == "keyword"
    assert beat["highlight_kind"] == "key_fact"
    assert "largest" in beat["emphasis"].lower()
    assert beat["source"] == "openrouter+deterministic"


def test_deterministic_editor_recognizes_central_proper_place():
    beat = editorial_brain.deterministic_editorial_beat(
        "The ship departed from Southampton before dawn.",
        0,
        1,
    )

    assert beat["emphasis"] == "Southampton"
    assert beat["treatment"] == "location"
    assert beat["highlight_kind"] == "location"


def test_empty_llm_scene_can_recover_one_grounded_milestone():
    beat = editorial_brain._normalize_beat(
        {
            "role": "context", "emphasis": "", "treatment": "none",
            "animation": "none", "background": "none", "transition": "cut", "energy": "normal",
            "confidence": 0.8, "importance": 0.1, "highlight_kind": "none", "editorial_reason": "ordinary",
        },
        "On April 15 1912 the investigation began.",
        0,
        1,
    )

    assert beat["emphasis"] == "April 15 1912"
    assert beat["treatment"] == "date"
    assert beat["highlight_kind"] == "milestone"


def test_semantic_fallback_recovers_exact_named_fact_when_deterministic_is_empty():
    beat = editorial_brain._semantic_fallback_beat(
        "The studio now features dynamic weather throughout the world.",
        0,
        1,
    )

    assert beat["source"] == "semantic-fallback"
    assert beat["treatment"] == "keyword"
    assert beat["emphasis"] in {"features dynamic", "dynamic weather"}
    assert beat["emphasis"] in "The studio now features dynamic weather throughout the world."


def test_empty_openrouter_plan_gets_bounded_grounded_recovery(monkeypatch):
    monkeypatch.setattr(editorial_brain, "_plan_chunk", lambda *args, **kwargs: [])
    beats = editorial_brain.plan_editorial_beats(
        [
            "Rockstar Games returns players to Vice City.",
            "Jason and Lucia lead the story.",
            "The world includes dynamic weather and realistic traffic.",
            "Analysts expect billions in revenue.",
            "The game could remain popular for a decade.",
            "This ambition reaches beyond gaming.",
        ],
        {"openrouter_api_key": "test-key", "documentary_density": "balanced"},
    )

    selected = [beat for beat in beats if beat.get("selected_for_text") is True]
    assert selected
    assert len(selected) < len(beats)
    assert all(beat["emphasis"] in " ".join([
        "Rockstar Games returns players to Vice City.",
        "Jason and Lucia lead the story.",
        "The world includes dynamic weather and realistic traffic.",
        "Analysts expect billions in revenue.",
        "The game could remain popular for a decade.",
        "This ambition reaches beyond gaming.",
    ]) for beat in selected)


def test_empty_openrouter_plan_targets_editorial_coverage_without_returning_zero(monkeypatch):
    monkeypatch.setattr(editorial_brain, "_plan_chunk", lambda *args, **kwargs: [])
    texts = [
        "The studio announced a record launch for the new platform.",
        "The project created a breakthrough in battery technology.",
        "Analysts expect billions in revenue over the next decade.",
        "The company opened its largest facility in Berlin.",
        "A final warning changed the direction of the investigation.",
        "The first prototype survived extreme testing conditions.",
        "The team revealed the discovery after years of research.",
        "The result changed how millions of people use the product.",
        "The new system reduced energy use by 27 percent.",
        "The founders announced the next phase in January 2026.",
    ]

    beats = editorial_brain.plan_editorial_beats(
        texts,
        {"openrouter_api_key": "test-key", "documentary_density": "balanced"},
    )

    selected = [beat for beat in beats if beat.get("selected_for_text") is True]
    assert 3 <= len(selected) <= 4
    assert all(beat["emphasis"] for beat in selected)
    assert all(beat["source"] in {"semantic-fallback", "semantic-last-resort", "deterministic", "openrouter+deterministic"} for beat in selected)


def test_lookahead_only_phrase_moves_to_its_spoken_scene():
    beats = [
        {
            "scene_index": 0, "emphasis": "maiden voyage", "treatment": "keyword",
            "selected_for_text": True, "highlight_kind": "key_fact",
        },
        {"scene_index": 1, "emphasis": "", "treatment": "none", "selected_for_text": False},
    ]

    mapped, moved = editorial_brain.remap_beats_to_primary_scenes(
        beats,
        ["England", "on its maiden voyage to New York"],
    )

    assert moved == 1
    assert mapped[0]["selected_for_text"] is False
    assert mapped[1]["selected_for_text"] is True
    assert mapped[1]["emphasis"] == "maiden voyage"


def test_time_banded_selection_distributes_cues_across_the_video():
    beats = []
    candidates = {
        0: ("April 15", "milestone"),
        5: ("Southampton", "location"),
        9: ("largest passenger ship", "key_fact"),
        12: ("practically unsinkable", "key_fact"),
        16: ("immigrants seeking", "human_stakes"),
        19: ("everything changed", "turning_point"),
    }
    for index in range(20):
        phrase, kind = candidates.get(index, ("", "none"))
        beats.append({
            "scene_index": index, "role": "reveal" if kind == "turning_point" else "evidence",
            "emphasis": phrase, "treatment": "keyword" if phrase else "none",
            "highlight_kind": kind, "importance": 0.85 if phrase else 0.1, "confidence": 0.9,
        })

    selected = editorial_brain._select_sparse_beats(
        beats,
        {
            "documentary_density": "balanced",
            "_editorial_scene_starts": [index * 2.65 for index in range(20)],
            "_editorial_total_duration": 53.0,
        },
    )
    selected_indices = [index for index, beat in enumerate(selected) if beat["selected_for_text"]]

    assert selected_indices == [0, 5, 9, 12, 16, 19]
    selected_times = [index * 2.65 for index in selected_indices]
    gaps = [selected_times[0], *[right - left for left, right in zip(selected_times, selected_times[1:])], 53.0 - selected_times[-1]]
    assert max(gaps) < 14.0


def test_underfilled_openrouter_plan_gets_one_focused_openrouter_repair_before_local_recovery(monkeypatch):
    calls = []

    def beat(scene_index, phrase, kind, treatment="keyword"):
        return {
            "scene_index": scene_index,
            "role": "reveal",
            "emphasis": phrase,
            "treatment": treatment,
            "animation": "scale_pop",
            "background": "none",
            "transition": "cut",
            "energy": "tense",
            "confidence": 0.94,
            "importance": 0.9,
            "highlight_kind": kind,
            "editorial_reason": "decisive narrated fact",
        }

    def fake_chunk(_key, _model, rows, _timeout):
        calls.append(rows)
        if any(row.get("_editorial_recovery_target") for row in rows):
            assert rows[0]["_editorial_recovery_target"] == 2
            return [
                beat(1, "27 percent", "scale", "counter"),
                beat(2, "critical evidence", "turning_point"),
            ]
        return [beat(0, "final warning", "warning")]

    monkeypatch.setattr(editorial_brain, "_plan_chunk", fake_chunk)
    texts = [
        "The final warning changed the investigation.",
        "Only 27 percent survived the test.",
        "The critical evidence changed the case.",
        "The camera crossed a quiet room.",
        "A corridor appeared beyond the doorway.",
        "The narrator continued through the account.",
        "The view moved slowly across the building.",
        "The sequence ended beside the empty road.",
    ]

    beats = editorial_brain.plan_editorial_beats(
        texts,
        {"openrouter_api_key": "test", "documentary_density": "balanced"},
    )

    assert len(calls) == 2
    selected = [beat for beat in beats if beat.get("selected_for_text") is True]
    assert len(selected) == 3
    assert all(str(beat.get("source") or "").startswith("openrouter") for beat in selected)
    assert {beat["emphasis"] for beat in selected} in (
        {"final", "27 percent", "critical"},
        {"final warning", "27 percent", "critical evidence"},
    )


def test_empty_openrouter_quality_repair_is_bounded_to_one_extra_call(monkeypatch):
    calls = []

    def empty_chunk(_key, _model, rows, _timeout):
        calls.append(rows)
        return []

    monkeypatch.setattr(editorial_brain, "_plan_chunk", empty_chunk)
    beats = editorial_brain.plan_editorial_beats(
        [
            "The final warning changed the investigation.",
            "Only 27 percent of the evidence survived.",
            "The largest facility opened in Berlin.",
            "The first prototype survived extreme testing.",
            "The discovery changed millions of lives.",
            "The project reached its conclusion.",
        ],
        {"openrouter_api_key": "test", "documentary_density": "balanced"},
    )

    assert len(calls) == 2
    assert sum(1 for rows in calls if any(row.get("_editorial_recovery_target") for row in rows)) == 1
    assert any(beat.get("selected_for_text") is True for beat in beats)


def test_editorial_chunk_schema_rejects_missing_beats_and_partial_scene_sets():
    rows = [{"scene_index": 4, "text": "The water lifted the ship."}]
    with pytest.raises(ValueError, match="missing the required beats array"):
        editorial_brain._validate_editorial_chunk({"plan": []}, rows)
    with pytest.raises(ValueError, match="exactly 1 scene beats"):
        editorial_brain._validate_editorial_chunk({"beats": []}, rows)


def test_malformed_openrouter_chunk_retries_next_model_with_strict_schema(monkeypatch):
    import json
    from server.services import openrouter_llm

    calls = []

    def beat(index):
        return {
            "scene_index": index,
            "role": "evidence",
            "emphasis": "water lifted",
            "treatment": "keyword",
            "animation": "fade",
            "background": "none",
            "transition": "cut",
            "energy": "normal",
            "grade": "auto",
            "texture": "none",
            "confidence": 0.9,
            "importance": 0.8,
            "highlight_kind": "key_fact",
            "editorial_reason": "explains the lock mechanism",
            "sound_intent": "impact",
            "sound_intensity": 0.3,
            "sound_reason": "quiet hydraulic movement",
            "sound_anchor": "water",
        }

    class FakeClient:
        responses = ['{"plan": []}', json.dumps({"beats": [beat(0), beat(1)]})]

        def __init__(self, api_key, model=None, fallback_models=None, **_kwargs):
            self.api_key = api_key
            self.model = model
            self.fallback_models = list(fallback_models or [])
            self.last_model = model

        def chat(self, prompt, **kwargs):
            calls.append({"model": self.model, "fallbacks": self.fallback_models, "kwargs": kwargs})
            return self.responses.pop(0)

    monkeypatch.setattr(openrouter_llm, "OpenRouterLLM", FakeClient)
    result = editorial_brain._plan_chunk(
        "test-key",
        "openai/gpt-6-luna",
        [{"scene_index": 0, "text": "The water lifted the ship."}, {"scene_index": 1, "text": "The canal opened."}],
        40,
        ["openai/gpt-5.6-luna", "deepseek/deepseek-v4.1-flash"],
    )

    assert [item["scene_index"] for item in result] == [0, 1]
    assert len(calls) == 2
    assert calls[0]["model"] == "openai/gpt-6-luna"
    assert calls[1]["model"] == "openai/gpt-5.6-luna"
    schema = calls[0]["kwargs"]["json_schema"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["beats"]["items"]["additionalProperties"] is False
    assert calls[1]["kwargs"]["max_attempts"] == 1


def test_underfilled_editorial_repair_uses_configured_long_wait(monkeypatch):
    calls = []

    def beat(index, phrase, kind):
        return {
            "scene_index": index, "role": "evidence", "emphasis": phrase,
            "treatment": "keyword", "animation": "fade", "background": "none",
            "transition": "cut", "energy": "normal", "confidence": 0.9,
            "importance": 0.8, "highlight_kind": kind, "editorial_reason": "narrated fact",
        }

    def fake_chunk(_key, _model, rows, timeout, *_fallbacks):
        calls.append((rows, timeout))
        if any(row.get("_editorial_recovery_target") for row in rows):
            return [beat(1, "27 percent", "scale"), beat(2, "final warning", "warning")]
        return [beat(0, "first discovery", "key_fact")]

    monkeypatch.setattr(editorial_brain, "_plan_chunk", fake_chunk)
    monkeypatch.setenv("VIDZEO_EDITORIAL_BRAIN_REPAIR_TIMEOUT_SECONDS", "75")
    texts = [
        "The first discovery changed the project.",
        "Only 27 percent survived the trial.",
        "The final warning arrived too late.",
        "Researchers mapped the site in detail.",
        "The remaining evidence changed the verdict.",
        "The reconstruction opened to the public.",
        "The team published its conclusions.",
        "The story reached its conclusion.",
    ]

    editorial_brain.plan_editorial_beats(
        texts,
        {"openrouter_api_key": "test", "editorial_brain_timeout": 110, "documentary_density": "balanced"},
    )

    assert len(calls) == 2
    assert calls[1][1] == 75
