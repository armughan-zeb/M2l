"""Evidence-led directing and live catalog selection without provider calls."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from services.editorial_director import director
from services.editorial_director.contract import SCENE_TYPE_VARIANTS


def test_argument_board_keeps_multiple_script_earned_choices():
    board = director._director_visual_argument_board([
        "The archive opened in 1985 in London.",
        'President John Smith said "We will return tomorrow" in the report.',
        "The engine failed, the alarms sounded, which led to the crew turning back.",
        "The empty corridor falls quiet.",
    ])
    assert any("chronology" in item for item in board[0]["arguments"])
    assert any("geography" in item for item in board[0]["arguments"])
    assert "1985" in board[0]["anchors"]
    assert any("testimony" in item for item in board[1]["arguments"])
    assert any("evidence" in item for item in board[1]["arguments"])
    assert any("mechanism" in item for item in board[2]["arguments"])
    assert board[3]["arguments"] == ["observation: verified media with purposeful framing"]


def test_argument_board_does_not_turn_unrelated_numbers_into_a_graph():
    row = director._director_visual_argument_board(["At 8:45, 37 people entered the room in 1971."])[0]
    assert not any("magnitude/change" in item for item in row["arguments"])


def test_short_films_also_receive_grounded_explanation_repairs():
    from services.editorial_director.contract import SceneDecision, PrimaryVisual
    texts = ["The room is quiet.", "The archive opened in 1985.", "A source photo shows the event."]
    scenes = [SceneDecision(scene_id=str(i), scene_index=i, start=i * 5, end=(i + 1) * 5,
                            duration=5, narration=text, visual_intent="Observe the verified media",
                            primary_visual=PrimaryVisual(type="SmartVideo", variant="full_bleed"))
              for i, text in enumerate(texts)]
    repairs = director._repair_director_visual_opportunities(scenes)
    assert len(repairs) == 1
    assert scenes[1].primary_visual.type == "Timeline"
    assert scenes[1].primary_visual.props["events"][0]["year"] == "1985"


def test_live_job_snapshot_preserves_every_variant_and_new_approved_template(monkeypatch):
    catalog = director.list_template_catalog()
    catalog["templates"].append({"id": "new-approved-recipe", "status": "approved",
                                 "component": "Diagram", "purpose": "Actor-specific mechanism", "roles": []})
    catalog["templates"].append({"id": "unsafe-draft", "status": "draft", "purpose": "Not approved"})
    monkeypatch.setattr(director, "list_template_catalog", lambda: catalog)
    before = json.dumps(director._DIRECTOR_RESPONSE_SCHEMA, sort_keys=True)
    snapshot = director._director_design_contract_snapshot()
    enums = snapshot["schema"]["properties"]["scenes"]["items"]["properties"]["template_id"]["enum"]
    assert "new-approved-recipe" in enums
    assert "unsafe-draft" not in enums
    assert "new-approved-recipe" in snapshot["templates_by_id"]
    for kind, variants in SCENE_TYPE_VARIANTS.items():
        assert tuple(snapshot["capabilities"]["visual_systems"][kind]["variants"]) == variants
    enums.append("only-this-job")
    assert json.dumps(director._DIRECTOR_RESPONSE_SCHEMA, sort_keys=True) == before
    assert "only-this-job" not in director._director_design_contract_snapshot()["schema"]["properties"]["scenes"]["items"]["properties"]["template_id"]["enum"]


def test_compact_art_direction_removes_only_duplicated_grammar():
    art = {"niche": "crime", "visual_grammar": {"evidence": "timeline"},
           "visual_motif": "grounded scene details", "custom_instructions": "Use authentic source footage",
           "display_font": "Archivo Black", "retention_plan": "question then evidence"}
    compact = director._director_prompt_art_direction(art)
    assert compact == {key: value for key, value in art.items() if key != "visual_grammar"}
    assert art["visual_grammar"] == {"evidence": "timeline"}


def test_compact_argument_window_keeps_global_family_indices_and_local_evidence():
    board = [
        {"scene": 0, "arguments": ["chronology"], "anchors": ["1912"]},
        {"scene": 1, "arguments": ["geography", "chronology"], "anchors": ["London", "1985"]},
        {"scene": 2, "arguments": ["mechanism"], "anchors": ["engine"]},
    ]
    compact = director._director_prompt_argument_window(board, 1, 2)
    assert compact["film_arguments"] == {"chronology": [0, 1], "geography": [1], "mechanism": [2]}
    assert compact["window"] == [board[1]]
    assert board[0]["anchors"] == ["1912"]


def test_compact_system_preserves_editing_and_grounding_contract():
    text = director._LLM_DIRECTOR_PROMPT
    assert len(text.encode("utf-8")) < 12000
    for required in ("30% meaningful graphics", "2-3 seconds", "3-5 seconds", "one dominant visual owner",
                     "transparent cutout asset", "do not invent facts", "subject_box/safe_text_regions",
                     "source_span", "maximum 5 words/42 characters", "emphasis span", "caption",
                     "impact_slab", "globe_3d", "satellite_local", "vector_editorial", "stable readable hold",
                     "generated-recipe", "image_index", "canvas percentages", "Maximum 16 useful layers",
                     "HTML, CSS or remote assets", "one to three motion_cues", "non-overlapping timestamp"):
        assert required.casefold() in text.casefold()
    for component in SCENE_TYPE_VARIANTS:
        assert component in text


class _ChoiceClient:
    model = "test/local"
    last_model = model
    attempt_history = []

    def __init__(self, copy):
        self.copy = copy
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("json_schema_name") == "vidnets_art_direction_v1":
            return json.dumps({"style": "editorial", "display_font": "Archivo Black", "body_font": "DM Sans",
                               "accent_color": "#D32F2F", "visual_motif": "source-led images",
                               "motion_rhythm": "reveal and hold", "story_arc": "question then proof",
                               "retention_plan": "explain the narrated evidence"})
        return json.dumps({"scenes": [
            {"scene_index": 0, "type": "SmartImage", "variant": "full_bleed_cinematic",
             "frame_mode": "media_only", "overlay_policy": "none", "template_id": "full-bleed"},
            {"scene_index": 1, "type": "KineticText", "variant": "impact_slab", "frame_mode": "kinetic",
             "visual_role": "media_overlay", "overlay_policy": "single_short_highlight",
             "editorial_priority": .8, "display_text": self.copy, "source_span": self.copy,
             "emphasis_spans": [self.copy], "placement": "center", "template_id": "keyword-reveal"},
        ]})


@pytest.mark.parametrize("narration,copy", [
    ("The ransom cost $200,000.", "$200,000"),
    ('The witness said "I saw the aircraft" before leaving.', "I saw the aircraft"),
])
def test_explicit_director_highlight_is_not_forced_into_counter_or_quote(monkeypatch, narration, copy):
    monkeypatch.delenv("VIDZEO_EDITORIAL_DIRECTOR_SCENE_BATCH_SIZE", raising=False)
    client = _ChoiceClient(copy)
    metadata = {}
    scenes = director._plan_llm_scenes(
        ["The empty room establishes the story.", narration], [4, 5],
        media_items=[{"media_type": "image", "src": "first.jpg"}, {"media_type": "image", "src": "second.jpg"}],
        llm_client=client, director_config={"llm_provider": "openrouter"}, metadata_out=metadata,
    )
    assert scenes is not None
    assert scenes[1].primary_visual.type == "KineticText"
    assert scenes[1].primary_visual.variant == "impact_slab"
    assert scenes[1].display_text == copy
    assert metadata["scene_batch_size"] == 8
    scene_calls = [call for call in client.calls if call.get("json_schema_name") == "vidnets_scene_direction_v1"]
    assert len(scene_calls) == 1
    assert "Choose the explanatory question and evidence BEFORE the template" in scene_calls[0]["user_prompt"]
    # All live component variants and approved custom recipes stay available;
    # savings come from duplicate policy, not narrowing the creative surface.
    prompt = scene_calls[0]["user_prompt"]
    for kind, variants in SCENE_TYPE_VARIANTS.items():
        assert kind in prompt
        assert all(variant in prompt for variant in variants)
    assert prompt.count('"editorial_logic":') == 1
