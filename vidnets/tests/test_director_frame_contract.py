from types import SimpleNamespace
import json

from server.services.editorial_director.contract import PrimaryVisual, SceneDecision, VisualSceneType
from server.services.editorial_director.director import (
    _build_diagram_props,
    _extract_numbers_from_text,
    _is_single_financial_stat,
    _plan_deterministic_scenes,
    _grounded_director_map,
    _apply_directed_region_map,
    _adjacent_site_location_context,
    _build_map_props,
    _build_stat_card_props,
    _repair_scene_frame_contract,
    _repair_unverified_map_route,
    _scene_frame_contract_failure,
    _sync_scene_display_props,
    _director_overlay_budget,
    _enforce_director_overlay_budget,
    _validated_motion_cues,
    EditingDirector,
)
from server.services.editorial_director.script_analyzer import analyze_sentence_triggers


def test_llm_person_cues_require_an_exact_full_name_not_a_sentence_lead():
    narration = 'But Butler had read the play. Malcolm Butler takes the interception.'
    rejected = _validated_motion_cues(
        [{'element': 'person', 'action': 'reveal', 'anchor_text': 'But Butler'}],
        narration, 0, 4,
    )
    accepted = _validated_motion_cues(
        [{'element': 'person', 'action': 'reveal', 'anchor_text': 'Malcolm Butler'}],
        narration, 0, 4,
    )
    assert rejected == []
    assert len(accepted) == 1
    assert accepted[0]['anchor_text'] == 'Malcolm Butler'


def test_director_highlight_budget_is_modestly_increased_and_configurable():
    assert _director_overlay_budget(20) == 10
    assert _director_overlay_budget(20, {'director_overlay_ratio': 0.45}) == 9


def test_director_uses_grounded_highlights_across_the_film_with_cooldown():
    scene_texts = [f"Scene {index} explains the North Atlantic crossing." for index in range(20)]
    proposed = [{
        'scene_index': index,
        'overlay_policy': 'single_short_highlight',
        'display_text': 'North Atlantic',
        'source_span': scene_texts[index],
        'frame_mode': 'map',
        'editorial_priority': 0.7,
    } for index in range(20)]
    metadata = {}

    selected = _enforce_director_overlay_budget(proposed, scene_texts, metadata=metadata)

    chosen = [row['scene_index'] for row in selected if row['overlay_policy'] == 'single_short_highlight']
    assert len(chosen) == 10
    assert all(right - left > 1 for left, right in zip(chosen, chosen[1:]))
    assert metadata['highlight_source'] == 'llm_director'
    assert metadata['overlay_budget'] == 10


def _scene(scene_type, variant, props, mode, asset=None, scene_index=1):
    return SceneDecision(
        scene_id=f"scene_{scene_index + 1}",
        scene_index=scene_index,
        start=float(scene_index * 4),
        end=float((scene_index + 1) * 4),
        duration=4.0,
        narration="A grounded narration passage about this scene.",
        visual_intent="test documentary beat",
        primary_visual=PrimaryVisual(type=scene_type, variant=variant, props=props, asset=asset),
        text={"frame_mode": mode, "overlay_policy": "none"},
    )


def test_omitted_emphasis_stays_unaccented_instead_of_highlighting_the_whole_copy():
    scene = SceneDecision(
        scene_id='scene_1', scene_index=0, start=0, end=4, duration=4,
        narration='A total eclipse is not a darkening of the Sun.',
        visual_intent='Open on the eclipse image.',
        primary_visual=PrimaryVisual(type=VisualSceneType.KINETIC_TEXT.value,
            variant='bold_minimal_center', props={}),
        text={'overlay_policy':'single_short_highlight'},
        display_text='not a darkening',
        source_span='A total eclipse is not a darkening of the Sun.',
        emphasis_spans=[],
    )
    _sync_scene_display_props(scene)
    assert scene.display_text == 'not a darkening'
    assert scene.emphasis_spans == []
    assert 'emphasis_spans' not in scene.primary_visual.props


def test_compound_metric_animates_spoken_mantissa_without_duplicate_unit():
    props = _build_stat_card_props(
        "floating_hero_counter",
        "The operation moved 2.3 million tons of supplies.",
        ["2.3 million"],
        None,
    )

    assert props["value"] == 2.3
    assert props["target"] == 2.3
    assert props["decimals"] == 1
    assert props["suffix"] == " MILLION"
    assert props["source_value"] == 2_300_000


def test_spoken_financial_amount_becomes_a_grounded_short_stat_beat():
    narration = "The mission cost twenty five billion dollars in today's money."
    extracted = _extract_numbers_from_text(narration)
    assert len(extracted) == 1
    assert extracted[0]["raw"] == 25_000_000_000
    assert extracted[0]["display"] == "$25 BILLION"
    assert _is_single_financial_stat(narration) is True

    props = _build_stat_card_props("hero_counter_large", narration, [], None)
    assert props["value"] == 25
    assert props["prefix"] == "$"
    assert props["suffix"] == " BILLION"
    assert props["label"] == "MISSION COST"
    assert props["kicker"] == ""
    assert props["context"] == "IN TODAY'S MONEY"


def test_deterministic_director_routes_spoken_financial_claim_to_stat_card():
    opening = "Apollo 11 began its journey toward the Moon."
    narration = "The mission cost twenty five billion dollars in today's money."
    decisions = _plan_deterministic_scenes([opening, narration], [3.0, 3.5])

    assert len(decisions) == 2
    assert decisions[1].primary_visual.type == VisualSceneType.STAT_CARD.value
    assert decisions[1].primary_visual.props["value"] == 25
    assert decisions[1].primary_visual.props["label"] == "MISSION COST"


def test_rejected_map_frame_becomes_explicit_media_fallback():
    scene = _scene(
        VisualSceneType.SMART_IMAGE.value,
        "editorial_crop",
        {"variant": "editorial_crop"},
        "map",
        asset={"src": "archive/interview.mp4", "type": "video"},
    )

    _repair_scene_frame_contract(scene)

    assert scene.primary_visual.type == VisualSceneType.SMART_VIDEO.value
    assert scene.text["frame_mode"] == "media_only"
    assert scene.text["frame_contract_validation"]["status"] == "repaired"
    assert scene.template_id == "full-bleed"


def test_grounded_japan_map_remains_a_map_frame():
    scene = _scene(
        VisualSceneType.ANIMATED_MAP.value,
        "country_spotlight",
        {"variant": "country_spotlight", "country": "JPN", "country_code": "JPN"},
        "map",
    )
    scene.map_shot = {"enabled": True, "location_names": ["Japan"], "feature_ids": [], "route_id": ""}

    assert _scene_frame_contract_failure(scene) == ""
    _repair_scene_frame_contract(scene)
    assert scene.text["frame_mode"] == "map"
    assert scene.text["frame_contract_validation"]["status"] == "pass"


def test_directed_north_atlantic_cue_promotes_media_to_grounded_globe_map():
    narration = "The Titanic crossed the North Atlantic on its first voyage."
    scene = _scene(
        VisualSceneType.SMART_IMAGE.value,
        "full_bleed_cinematic",
        {"image_treatment": "cinematic_grade"},
        "media_only",
    )
    scene.narration = narration
    scene.motion_cues = [{"element": "map", "action": "pin", "anchor_text": "North Atlantic"}]

    changed = _apply_directed_region_map(scene, {"variant": "country_spotlight"}, narration)

    assert changed is True
    assert scene.primary_visual.type == VisualSceneType.ANIMATED_MAP.value
    assert scene.primary_visual.props["location"] == "NORTH ATLANTIC"
    assert scene.map_shot["location_names"] == ["North Atlantic"]
    assert scene.map_shot["render_style"] == "globe_3d"
    assert scene.template_id == "map-route"
    assert _scene_frame_contract_failure(scene) == ""


def test_director_map_mode_resolves_a_narrated_city_without_model_coordinates():
    result = _grounded_director_map(
        "Workers brought the iron pieces to the site beside the Seine in Paris.",
        "country_spotlight",
        {"enabled": False, "location_names": ["Paris"]},
    )

    assert result is not None
    props, map_shot = result
    assert props["location"] == "PARIS"
    assert map_shot["enabled"] is True
    assert map_shot["location_names"] == ["Paris"]
    assert not props.get("from") and not props.get("to")


def test_director_map_mode_does_not_invent_an_unmentioned_location():
    assert _grounded_director_map(
        "The structure rose in stages while workers checked each section.",
        "country_spotlight",
        {"enabled": True, "location_names": ["Tokyo"]},
    ) is None


def test_director_map_mode_can_use_one_established_adjacent_site():
    result = _grounded_director_map(
        "Workers brought the iron pieces to the site beside the Seine.",
        "country_spotlight",
        {"enabled": False, "location_names": ["Paris"]},
        "The tower would become the centerpiece of the 1889 Paris Exposition.",
    )

    assert result is not None
    props, map_shot = result
    assert props["location"] == "PARIS"
    assert map_shot["location_names"] == ["Paris"]
    assert map_shot["route_id"] == ""


def test_explicit_site_beat_uses_named_adjacent_city_as_a_locator_context():
    scenes = [
        "Construction began on a tower for the Paris Exposition.",
        "Workers brought the iron pieces to the site beside the Seine.",
        "Rivets joined the metal sections as the four legs rose.",
    ]

    context = _adjacent_site_location_context(1, scenes, scenes[1])

    assert "Paris" in context
    assert "Seine" in context


def test_site_beat_without_adjacent_catalog_location_stays_unmapped():
    scenes = ["Workers brought material to the site.", "Crews aligned the sections."]

    assert _adjacent_site_location_context(0, scenes, scenes[0]) == ""


def test_split_script_keeps_nearest_named_city_for_later_specific_site_beat():
    scenes = [
        "The tower would become the centerpiece of the Paris Exposition.",
        "Gustave Eiffel's company designed a lattice of iron pieces.",
        "Workers brought the pieces to the site beside the Seine.",
        "Rivets joined the metal sections.",
    ]

    context = _adjacent_site_location_context(2, scenes, scenes[2])

    assert "Paris" in context
    assert "Rivets" not in context


def test_director_pipeline_promotes_an_established_site_to_a_local_map():
    scene_texts = [
        "The tower would become the centerpiece of the Paris Exposition.",
        "Workers brought the iron pieces to the site beside the Seine.",
    ]

    class FakeDirector:
        model = "test/director"
        model_chain = [model]
        attempt_history = []
        last_model = model

        def chat(self, *, json_schema_name, **_kwargs):
            if json_schema_name == "vidnets_art_direction_v1":
                return json.dumps({
                    "style": "editorial",
                    "display_font": "Barlow Condensed",
                    "body_font": "Inter",
                    "accent_color": "#A64B36",
                    "visual_motif": "site map and construction details",
                    "motion_rhythm": "measured",
                })
            return json.dumps({"scenes": [
                {
                    "scene_index": index,
                    "type": VisualSceneType.SMART_IMAGE.value,
                    "variant": "full_bleed_cinematic",
                    "visual_intent": "Let the image breathe.",
                    "frame_mode": "media_only",
                    "overlay_policy": "none",
                    "editorial_priority": 0.2,
                    "camera": "slow_push",
                    "entrance": "fade",
                    "transition": "cut",
                    "text_focus": "",
                    "display_text": "",
                    "source_span": "",
                    "emphasis_spans": [],
                    "placement": "none",
                    "template_id": "full-bleed",
                    "template_recipe": None,
                    "map_shot": {
                        "enabled": False,
                        "location_names": ["Paris"] if index == 1 else [],
                        "feature_ids": [],
                        "route_id": "",
                        "source_ids": [],
                        "motion_profile": "auto",
                    },
                }
                for index in range(2)
            ]})

    plan = EditingDirector(llm_client=FakeDirector()).plan_documentary(
        script_text=" ".join(scene_texts),
        scene_texts=scene_texts,
        scene_durations=[4.0, 4.0],
        topic="Eiffel Tower Construction",
        category="history",
    )

    site = plan.scenes[1]
    assert site.text["frame_mode"] == "map"
    assert site.primary_visual.type == VisualSceneType.ANIMATED_MAP.value
    assert site.map_shot["location_names"] == ["Paris"]
    assert site.map_shot["route_id"] == ""


def test_consecutive_llm_stat_scenes_do_not_abort_director_plan():
    scene_texts = [
        "Seattle reached the Patriots' one-yard line.",
        "With twenty-six seconds left, Seattle faced second-and-goal.",
    ]

    class FakeDirector:
        model = "test/director"
        model_chain = [model]
        attempt_history = []
        last_model = model

        def chat(self, *, json_schema_name, user_prompt="", **_kwargs):
            if json_schema_name == "vidnets_art_direction_v1":
                return json.dumps({
                    "style": "editorial", "display_font": "Archivo Black", "body_font": "DM Sans",
                    "accent_color": "#D9FF3F", "visual_motif": "field position and clock",
                    "motion_rhythm": "short count, hold, decisive reveal",
                })
            owned = user_prompt.split("global indices ", 1)[1].splitlines()[0].rstrip(".")
            first_index, last_index = (int(value) for value in owned.split("-", 1))
            indices = list(range(first_index, last_index + 1))
            return json.dumps({"scenes": [
                {
                    "scene_index": index, "type": VisualSceneType.STAT_CARD.value,
                    "variant": "hero_counter_large", "visual_intent": "Land the grounded field-position fact.",
                    "frame_mode": "data", "visual_role": "primary_explanation",
                    "overlay_policy": "none", "editorial_priority": 0.7,
                    "camera": "locked", "entrance": "count", "transition": "cut",
                    "text_focus": "", "display_text": "", "source_span": "", "emphasis_spans": [],
                    "placement": "none", "text_treatment": None, "graphic_theme": "accent_focus",
                    "template_id": "full-bleed", "template_recipe": None,
                    "map_shot": {"enabled": False, "location_names": [], "feature_ids": [],
                                  "route_id": "", "source_ids": [], "motion_profile": "auto"},
                    "media_plan": {"visual_beats": 1, "media_preference": "either", "rhythm": "single_hold"},
                }
                for index in indices
            ]})

    plan = EditingDirector(llm_client=FakeDirector()).plan_documentary(
        script_text=" ".join(scene_texts), scene_texts=scene_texts,
        scene_durations=[3.0, 3.0], topic="Super Bowl XLIX", category="sports",
    )

    assert plan.director_metadata["status"] == "directed"
    assert plan.director_metadata.get("reason") != "request_error:NameError"
    assert len(plan.scenes) == 2


def test_director_pipeline_routes_a_narrated_ocean_map_cue_to_the_globe():
    scene_texts = [
        "The ship began its voyage from Southampton.",
        "Then Titanic crossed the North Atlantic.",
    ]

    class FakeDirector:
        model = "test/director"
        model_chain = [model]
        attempt_history = []
        last_model = model

        def chat(self, *, json_schema_name, **_kwargs):
            if json_schema_name == "vidnets_art_direction_v1":
                return json.dumps({
                    "style": "editorial",
                    "display_font": "Archivo Black",
                    "body_font": "DM Sans",
                    "accent_color": "#D99A37",
                    "visual_motif": "a clean voyage map and archival evidence",
                    "motion_rhythm": "fast reveal, calm hold",
                })
            return json.dumps({"scenes": [
                {
                    "scene_index": index,
                    "type": VisualSceneType.SMART_IMAGE.value,
                    "variant": "full_bleed_cinematic",
                    "visual_intent": "Ground the narrated beat in its real setting.",
                    "frame_mode": "media_only",
                    "overlay_policy": "none",
                    "editorial_priority": 0.4,
                    "camera": "slow_push",
                    "entrance": "fade",
                    "transition": "cut",
                    "text_focus": "",
                    "display_text": "",
                    "source_span": "",
                    "emphasis_spans": [],
                    "placement": "none",
                    "template_id": "full-bleed",
                    "template_recipe": None,
                    "map_shot": {"enabled": False, "location_names": [], "feature_ids": [], "route_id": "", "source_ids": [], "motion_profile": "auto"},
                    "motion_cues": ([{"element": "map", "action": "pin", "anchor_text": "North Atlantic"}] if index == 1 else []),
                }
                for index in range(2)
            ]})

    plan = EditingDirector(llm_client=FakeDirector()).plan_documentary(
        script_text=" ".join(scene_texts),
        scene_texts=scene_texts,
        scene_durations=[4.0, 4.0],
        topic="Titanic voyage",
        category="history",
    )

    scene = plan.scenes[1]
    manifest_scene = plan.to_remotion_manifest()["scenes"][1]
    assert scene.primary_visual.type == VisualSceneType.ANIMATED_MAP.value
    assert scene.map_shot["location_names"] == ["North Atlantic"]
    assert scene.map_shot["render_style"] == "globe_3d"
    assert manifest_scene["graphic"]["template_id"] == "map-route"
    assert manifest_scene["primary_visual"]["type"] == "AnimatedMap"


def test_director_pipeline_keeps_a_grounded_process_diagram():
    scene_texts = [
        "A new assembly sequence began.",
        "Rivets joined the metal sections as the four legs rose toward the first platform.",
    ]

    class FakeDirector:
        model = "test/director"
        model_chain = [model]
        attempt_history = []
        last_model = model

        def chat(self, *, json_schema_name, **_kwargs):
            if json_schema_name == "vidnets_art_direction_v1":
                return json.dumps({
                    "style": "editorial", "display_font": "Barlow Condensed", "body_font": "Inter",
                    "accent_color": "#A64B36", "visual_motif": "assembly diagram", "motion_rhythm": "measured",
                })
            return json.dumps({"scenes": [
                {
                    "scene_index": index, "type": VisualSceneType.SMART_IMAGE.value,
                    "variant": "full_bleed_cinematic", "visual_intent": "Show the narrated beat.",
                    "frame_mode": "diagram" if index == 1 else "media_only", "overlay_policy": "none",
                    "editorial_priority": 0.2, "camera": "slow_push", "entrance": "fade", "transition": "cut",
                    "text_focus": "", "display_text": "", "source_span": "", "emphasis_spans": [],
                    "placement": "none", "template_id": "full-bleed", "template_recipe": {
                        "version": 2, "id": "assembly-explanation", "name": "Assembly explanation",
                        "layers": [
                            {"kind":"diagram", "x":0,"y":0,"width":100,"height":100,
                             "data":[{"label":"Rivets"},{"label":"four legs"}]},
                            {"kind":"rect", "x":20,"y":35,"width":60,"height":25,"motion":"wipe"},
                        ],
                    } if index == 1 else None,
                    "map_shot": {"enabled": False, "location_names": [], "feature_ids": [], "route_id": "", "source_ids": [], "motion_profile": "auto"},
                    "diagram_nodes": [
                        {"label": "Rivets", "source_span": "Rivets joined the metal sections"},
                        {"label": "four legs", "source_span": "the four legs rose toward the first platform"},
                    ] if index == 1 else [],
                }
                for index in range(2)
            ]})

    plan = EditingDirector(llm_client=FakeDirector()).plan_documentary(
        script_text=" ".join(scene_texts), scene_texts=scene_texts, scene_durations=[4.0, 4.0],
        topic="Tower assembly", category="history",
    )

    assembly = plan.scenes[1]
    assert assembly.text["frame_mode"] == "diagram", assembly.text
    assert assembly.primary_visual.type == VisualSceneType.DIAGRAM.value
    assert len(assembly.primary_visual.props["steps"]) == 2
    assert assembly.primary_visual.props["steps"] == [
        {"title": "Rivets", "desc": ""}, {"title": "four legs", "desc": ""},
    ]
    assert assembly.primary_visual.props["node_copy_contract"]["source"] == "llm"


def test_map_builder_does_not_connect_two_unrelated_place_mentions():
    triggers = SimpleNamespace(country_corridor=None, countries=[], locations=["Berlin", "Paris"])
    locator = _build_map_props(
        "route_flight_arc", triggers, "The dispute affected Berlin and Paris."
    )
    route = _build_map_props(
        "route_flight_arc", triggers, "A train traveled from Berlin to Paris."
    )

    assert locator["variant"] == "country_spotlight"
    assert "from" not in locator and "to" not in locator
    assert route["variant"] == "city_to_city_transit"
    assert route["from"] == "BERLIN" and route["to"] == "PARIS"


def test_historical_soviet_scene_does_not_turn_into_a_modern_russia_map():
    narration = "In June 1948, Soviet authorities blocked access to West Berlin."
    triggers = analyze_sentence_triggers(narration)
    props = _build_map_props("route_flight_arc", triggers, narration)

    assert "RUS" not in triggers.countries
    assert props["variant"] == "country_spotlight"
    assert props["location"] == "BERLIN"
    assert "from" not in props and "to" not in props


def test_city_route_uses_exact_places_instead_of_country_centers():
    narration = "A train traveled from Berlin to Paris."
    triggers = analyze_sentence_triggers(narration)
    props = _build_map_props("country_spotlight", triggers, narration)

    assert props["variant"] == "city_to_city_transit"
    assert props["from"] == "BERLIN" and props["to"] == "PARIS"
    assert "from_country" not in props and "to_country" not in props


def test_map_uses_aircraft_arc_only_when_the_script_says_flight():
    narration = "The aircraft flew from Berlin to Paris."
    triggers = analyze_sentence_triggers(narration)
    props = _build_map_props("city_to_city_transit", triggers, narration)

    assert props["variant"] == "route_flight_arc"
    assert props["from"] == "BERLIN" and props["to"] == "PARIS"


def test_border_between_countries_does_not_create_a_route_corridor():
    triggers = analyze_sentence_triggers(
        "The border between Germany and France became a flashpoint."
    )

    assert triggers.country_corridor is None


def test_explicit_rail_corridor_between_countries_stays_mappable():
    triggers = analyze_sentence_triggers(
        "A rail corridor ran between Germany and France."
    )

    assert triggers.country_corridor == {"from": "DEU", "to": "FRA"}


def test_unverified_llm_flight_arc_is_repaired_to_a_grounded_locator():
    scene = _scene(
        VisualSceneType.ANIMATED_MAP.value,
        "route_flight_arc",
        {
            "variant": "route_flight_arc",
            "location": "BERLIN",
            "from": "BERLIN",
            "to": "PARIS",
            "map_shot": {"motion_profile": "route_trace", "location_names": ["Berlin"]},
        },
        "map",
    )
    scene.narration = "The dispute affected Berlin and Paris, but described no journey."
    scene.map_shot = {"motion_profile": "route_trace", "location_names": ["Berlin"]}

    assert _repair_unverified_map_route(scene) is True
    assert scene.primary_visual.variant == "country_spotlight"
    assert scene.primary_visual.props["location"] == "BERLIN"
    assert "from" not in scene.primary_visual.props and "to" not in scene.primary_visual.props
    assert scene.map_shot["motion_profile"] == "auto"
    assert scene.text["map_route_validation"]["status"] == "repaired"
    assert _scene_frame_contract_failure(scene) == ""


def test_diagram_uses_complete_connected_clauses():
    narration = "Planes carried medicine and fuel through Tempelhof and Gatow, while each landing was tightly controlled."
    props = _build_diagram_props("cause_and_effect_flow", narration, analyze_sentence_triggers(narration))
    scene = _scene(VisualSceneType.DIAGRAM.value, "cause_and_effect_flow", props, "diagram")

    assert not props.get("no_data")
    assert [step["desc"] for step in props["steps"]] == [
        "Planes carried medicine and fuel through Tempelhof and Gatow",
        "each landing was tightly controlled",
    ]
    assert props["node_copy_contract"]["source"] == "system_clause_extractor"
    assert all(node["source_span"] in narration for node in props["node_copy_contract"]["nodes"])
    assert _scene_frame_contract_failure(scene) == ""


def test_diagram_builder_preserves_narrated_simultaneous_assembly_stages():
    narration = "Rivets joined the metal sections as the four legs rose toward the first platform."
    props = _build_diagram_props("cause_and_effect_flow", narration, analyze_sentence_triggers(narration))

    assert not props.get("no_data")
    assert [step["desc"] for step in props["steps"]] == [
        "Rivets joined the metal sections",
        "the four legs rose toward the first platform",
    ]
    assert props["node_copy_contract"]["approved"] is True


def test_director_prompt_separates_sparse_copy_from_graphic_variety():
    from server.services.editorial_director.director import _LLM_DIRECTOR_PROMPT

    prompt = _LLM_DIRECTOR_PROMPT.lower()

    assert "sparse visible copy is not sparse editing" in prompt
    assert "that copy rule does not require the visual itself to be media_only" in prompt
    assert "script's niche" in prompt
    assert "one dominant visual" in prompt
    assert "transparent cutout asset" in prompt


def test_export_preflight_rejects_a_map_frame_that_would_render_as_media(monkeypatch):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
    import app as server_app

    monkeypatch.setattr(server_app, "_remotion_runtime_asset_errors", lambda: [])
    result = server_app._validate_remotion_edit_plan(
        {
            "edit_plan_schema_version": 4,
            "director": {"overlay_budget": 0},
            "scenes": [
                {
                    "duration": 3.0,
                    "narration": "The city is surrounded.",
                    "text": {"frame_mode": "map", "overlay_policy": "none"},
                    "primary_visual": {
                        "type": "SmartImage",
                        "variant": "full_bleed_cinematic",
                        "asset": {"src": "https://assets.example.test/archive.jpg"},
                    },
                    "graphic": {"template_id": "map-route", "props": {}},
                }
            ],
        },
        1,
    )

    assert result["status"] == "fail"
    assert any(error["code"] == "frame_visual_mismatch" for error in result["errors"])


def test_export_preflight_allows_nearest_named_city_for_explicit_site_map(monkeypatch):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
    import app as server_app

    monkeypatch.setattr(server_app, "_remotion_runtime_asset_errors", lambda: [])
    plan = {
        "edit_plan_schema_version": 4,
        "director": {"overlay_budget": 0},
        "scenes": [
            {
                "duration": 3.0,
                "narration": "The tower became the centerpiece of the Paris Exposition.",
                "text": {"frame_mode": "media_only", "overlay_policy": "none"},
                "primary_visual": {"type": "SmartImage", "variant": "full_bleed_cinematic", "asset": {"src": "https://assets.example.test/archive.jpg"}},
                "graphic": {"template_id": "full-bleed", "props": {}},
            },
            {
                "duration": 3.0,
                "narration": "Workers brought the pieces to the site beside the Seine.",
                "text": {"frame_mode": "map", "overlay_policy": "none"},
                "map_shot": {"enabled": True, "location_names": ["Paris"], "feature_ids": [], "route_id": "", "source_ids": [], "motion_profile": "auto"},
                "primary_visual": {"type": "AnimatedMap", "variant": "country_spotlight", "props": {"location": "PARIS", "map_shot": {"enabled": True, "location_names": ["Paris"], "feature_ids": [], "route_id": "", "source_ids": [], "motion_profile": "auto"}}},
                "graphic": {"template_id": "map-card", "props": {}},
            },
        ],
    }

    result = server_app._validate_remotion_edit_plan(plan, 1)

    assert not any(error["code"] == "unsupported_map_location" for error in result["errors"])


def test_visual_audit_counts_full_frame_graphics_and_ignores_stale_media_grammar():
    from server.services.editorial_director.visual_critic import audit_manifest

    result = audit_manifest(
        {
            "scenes": [
                {
                    "director_treatment_enabled": True,
                    "narration": "The story opens on archival footage.",
                    "text": {"frame_mode": "media_only"},
                    "media_composition": {"overlay_grammar": "chronology_strip"},
                    "primary_visual": {"type": "SmartImage", "variant": "archive"},
                },
                {
                    "director_treatment_enabled": True,
                    "narration": "Japan sits between the Pacific and Sea of Japan.",
                    "text": {"frame_mode": "map"},
                    "primary_visual": {
                        "type": "AnimatedMap",
                        "variant": "country_spotlight",
                        "props": {"country": "Japan"},
                    },
                },
                {
                    "director_treatment_enabled": True,
                    "narration": "The operation moved 2.3 million tons.",
                    "text": {"frame_mode": "data"},
                    "primary_visual": {
                        "type": "StatCard",
                        "variant": "hero_counter_large",
                        "props": {"value": 2.3, "suffix": " million tons"},
                    },
                },
            ]
        }
    )

    assert result["grammar_sequence"][0] == "cinematic_type"
    assert result["graphic_events"] == 2
    assert result["graphic_ratio"] == 0.667
    assert not any(item["code"] == "chronology_without_date" for item in result["findings"])


def test_export_preflight_fails_when_deterministic_audit_fails(monkeypatch):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
    import app as server_app
    import services.editorial_director as editorial_director

    monkeypatch.setattr(
        editorial_director,
        "audit_manifest",
        lambda _manifest: {
            "status": "fail",
            "findings": [
                {"severity": "error", "code": "fixture_quality_failure", "scene_index": 0}
            ],
        },
    )
    monkeypatch.setattr(server_app, "_remotion_runtime_asset_errors", lambda: [])
    result = server_app._validate_remotion_edit_plan(
        {
            "edit_plan_schema_version": 4,
            "director": {"overlay_budget": 0},
            "scenes": [
                {
                    "duration": 3.0,
                    "narration": "A grounded archival scene.",
                    "text": {"frame_mode": "media_only", "overlay_policy": "none"},
                    "primary_visual": {
                        "type": "SmartImage",
                        "variant": "full_bleed_cinematic",
                        "asset": {"src": "https://assets.example.test/archive.jpg"},
                    },
                }
            ],
        },
        1,
    )

    assert result["status"] == "fail"
    assert any(error["code"] == "fixture_quality_failure" for error in result["errors"])


def test_export_preflight_rejects_adjacent_repeated_graphic_treatments(monkeypatch):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
    import app as server_app

    monkeypatch.setattr(server_app, "_remotion_runtime_asset_errors", lambda: [])
    scene = {
        "duration": 3.0,
        "narration": "A grounded statistic from the documentary.",
        "text": {"frame_mode": "data", "overlay_policy": "none"},
        "primary_visual": {
            "type": "StatCard",
            "variant": "hero_counter_large",
            "props": {"value": 12, "suffix": " ships"},
        },
    }
    result = server_app._validate_remotion_edit_plan(
        {
            "edit_plan_schema_version": 4,
            "director": {"overlay_budget": 0},
            "scenes": [dict(scene), dict(scene)],
        },
        2,
    )

    assert result["status"] == "fail"
    assert any(error["code"] == "repeated_adjacent_treatment" for error in result["errors"])
