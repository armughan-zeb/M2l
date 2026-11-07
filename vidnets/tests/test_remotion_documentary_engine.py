import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

import app as server_app
from services.editorial_director.director import (
    EditingDirector,
    _DIRECTOR_AUTO_TEMPLATES,
    _build_chart_props,
    _build_map_props,
    _build_stat_card_props,
    _director_story_map,
    _director_runtime_capabilities,
    _extract_numbers_from_text,
    _recipe_is_grounded,
    _recipe_supports_visual_type,
    _validated_motion_cues,
    director_planning_budget,
)
from services.editing_profile import classify_niche, niche_visual_grammar
from services.template_library import list_template_catalog


def test_keyword_reveal_is_approved_at_the_remotion_boundary():
    """The director and Remotion validator must share the same approved catalog."""
    approved_ids = {
        template["id"]
        for template in list_template_catalog().get("templates", [])
        if template.get("status") == "approved"
    }
    assert "keyword-reveal" in approved_ids
    assert set(_DIRECTOR_AUTO_TEMPLATES).issubset(approved_ids)


def test_director_receives_live_remotion_component_variants_and_template_purposes():
    from services.editorial_director.contract import SCENE_TYPE_VARIANTS

    capabilities = _director_runtime_capabilities()
    visual_systems = capabilities["visual_systems"]
    assert set(visual_systems) == set(SCENE_TYPE_VARIANTS)
    assert visual_systems["Chart"]["remotion_component"] == "AdaptiveChart"
    assert visual_systems["StatCard"]["remotion_component"] == "AdaptiveStat"
    assert "quote_fragment_punch" in visual_systems["QuoteCard"]["variants"]
    assert "multi_stat_grid" not in visual_systems["StatCard"]["variants"]
    assert all(item["purpose"] for item in capabilities["approved_templates"])
    assert capabilities["generated_recipe"]["version"] == 2
    assert capabilities["generated_recipe"]["layer_kinds"] == [
        "text", "line", "rect", "circle", "image", "chart", "map",
        "diagram", "timeline", "comparison", "counter", "media_montage",
        "sphere", "cone", "arrow", "group", "path",
    ]


def test_remotion_contract_repair_removes_stale_hud_and_bounds_short_scene_cues():
    manifest = {
        "fps": 30,
        "director": {"status": "directed"},
        "scenes": [
            {
                "director_treatment_enabled": True,
                "duration": 0.32,
                "start": 0.0,
                "narration": "At 11:40 p.",
                "text": {"frame_mode": "media_only", "overlay_policy": "none"},
                "template_id": "cyber-hud",
                "graphic": {
                    "template_id": "cyber-hud",
                    "props": {},
                    "cue": {"template": "cyber-hud", "text": "11:40"},
                },
                "primary_visual": {"type": "SmartVideo"},
                "motion_cues": [{
                    "element": "copy",
                    "action": "reveal",
                    "anchor_text": "11:40",
                    "time_local": 0.0,
                    "duration_seconds": 0.42,
                }],
            },
            {
                "director_treatment_enabled": True,
                "duration": 0.92,
                "start": 0.32,
                "narration": "The sinking killed 1,517.",
                "text": {"frame_mode": "data", "overlay_policy": "none"},
                "template_id": "cyber-hud",
                "graphic": {"template_id": "cyber-hud", "props": {}},
                "primary_visual": {"type": "StatCard", "props": {"value": 1517}},
                "motion_cues": [{
                    "element": "data",
                    "action": "count",
                    "anchor_text": "1,517",
                    "time_local": 0.672,
                    "duration_seconds": 0.65,
                }],
            },
        ],
    }

    repaired = server_app._repair_invalid_director_templates_and_motion_cues(manifest)

    assert repaired == [0, 1]
    assert manifest["scenes"][0]["template_id"] == "full-bleed"
    assert manifest["scenes"][0]["graphic"] == {"template_id": "full-bleed", "props": {}}
    assert manifest["scenes"][0]["motion_cues"] == []
    assert manifest["scenes"][1]["template_id"] == "stat-card"
    assert manifest["scenes"][1]["motion_cues"] == []
    assert manifest["director"]["repairs"] == [{
        "type": "remotion_contract_sanitization",
        "scene_indices": [0, 1],
        "motion_cues_clipped": 0,
        "motion_cues_omitted": 2,
    }]


def test_legacy_scene_selectors_are_repaired_to_live_remotion_variants():
    from services.editorial_director.contract import SCENE_TYPE_VARIANTS
    from services.semantic_edit_engine import _supported_remotion_variant

    legacy_cases = {
        "QuoteCard": ("minimal_serif_quote", "quote_fragment_punch"),
        "PersonProfile": ("dossier_case_file", "minimal_profile_split"),
        "AnimatedMap": ("location_pin_focus", "country_spotlight"),
        "DocumentHighlight": ("blackout_redaction", "declassified_redaction"),
        "Diagram": ("technical_schematic", "network_node_connect"),
        "KineticText": ("multi_line_build", "staggered_lines"),
    }
    for scene_type, (stale_variant, preferred_variant) in legacy_cases.items():
        resolved, repaired = _supported_remotion_variant(scene_type, stale_variant, preferred_variant)
        assert repaired
        assert resolved == preferred_variant
        assert resolved in SCENE_TYPE_VARIANTS[scene_type]


def test_generated_recipe_requires_grounded_copy_and_primary_data_layers():
    narration = "The vote split was 60 percent yes and 40 percent no."
    recipe = {"layers": [{"kind": "chart", "data": [
        {"label": "yes", "value": 60.0}, {"label": "no", "value": 40.0},
    ]}]}
    assert _recipe_is_grounded(recipe, narration)
    assert _recipe_supports_visual_type(recipe, "Chart")
    unsupported = {"layers": [{"kind": "rect", "text": "A fabricated outcome"}]}
    assert not _recipe_is_grounded(unsupported, narration)
    assert not _recipe_supports_visual_type(unsupported, "Chart")
    map_recipe = {"layers": [{"kind": "map"}]}
    assert _recipe_supports_visual_type(map_recipe, "AnimatedMap", map_enabled=True)
    assert not _recipe_supports_visual_type(map_recipe, "AnimatedMap", map_enabled=False)


def test_scene_specific_director_palette_survives_the_production_merge():
    scene_dna = {"palette": {"background": "#F6F2EA", "primary": "#14202A", "accent": "#D99137"}, "chart_style": "editorial_white"}
    merged = server_app._directed_scene_style_fields(
        {"graphic_theme": "editorial_light", "visual_dna": scene_dna},
        {"graphic_theme": "cinematic_dark", "visual_dna": {"palette": {"accent": "#FFFFFF"}}},
    )
    assert merged["graphic_theme"] == "editorial_light"
    assert merged["visual_dna"] == scene_dna
    fallback = server_app._directed_scene_style_fields({}, {"graphic_theme": "accent_focus", "visualDNA": scene_dna})
    assert fallback["graphic_theme"] == "accent_focus"
    assert fallback["visual_dna"] == scene_dna


def test_chart_mapping_keeps_narrated_relationships_and_avoids_numeric_labels():
    unrelated = _build_chart_props(
        "donut_distribution", "The cost rose from 12 million people to 18 million people.",
        [], "dark", [],
    )
    assert unrelated["variant"] == "ranked_horizontal_bars"
    assert unrelated["data"][0]["value"] == "12 MILLION"
    assert unrelated["data"][0]["label"] != unrelated["data"][0]["value"]

    partition = _build_chart_props(
        "donut_distribution", "The vote split was 60% yes and 40% no.",
        [], "dark", [],
    )
    assert partition["variant"] == "donut_distribution"
    assert sum(row["value"] for row in partition["data"]) == 100

    non_trend = _build_chart_props(
        "multi_line_trend", "The two groups had 20 people and 30 people.",
        [], "dark", [],
    )
    assert non_trend["variant"] == "editorial_bar"


def test_stat_delta_layout_gets_both_values_only_for_an_explicit_narrated_range():
    delta = _build_stat_card_props("delta_growth_badge", "The total rose from 20 million to 40 million.", [], None)
    assert delta["fromVal"] == "20 MILLION"
    assert delta["toVal"] == "40 MILLION"
    single_value = _build_stat_card_props("delta_growth_badge", "The total reached 40 million.", [], None)
    assert "fromVal" not in single_value and "toVal" not in single_value


def test_failed_director_keeps_grounded_stat_graphics_without_turning_dates_into_metrics():
    texts = [
        "On April 10, 1912, the Titanic departed Southampton.",
        "At 882 feet in length and weighing over 46,000 tons, it was immense.",
        "Carrying more than 2,200 passengers and crew, only 705 survived.",
        "At 11:40 PM on April 14, the iceberg appeared.",
    ]
    plan = EditingDirector(llm_client=_FailingDirector()).plan_documentary(
        script_text=" ".join(texts), scene_texts=texts,
        scene_durations=[4.0] * len(texts), topic="Titanic", category="history",
    )
    assert [scene.primary_visual.type for scene in plan.scenes] == [
        "SmartImage", "StatCard", "StatCard", "SmartImage",
    ]
    assert all(scene.display_text == "" for scene in plan.scenes)
    assert _extract_numbers_from_text(texts[3]) == []
from services.source_evidence import capture_source_evidence
import services.source_evidence as source_evidence


class _FakeDirector:
    model = "openrouter/test-director"
    last_model = "openrouter/test-director"

    def chat(self, **_kwargs):
        return json.dumps({
            "scenes": [
                {
                    "scene_index": 0,
                    "type": "KineticText",
                    "variant": "staggered_lines",
                    "visual_intent": "Open on the narrated phrase",
                    "camera": "slow_push",
                    "entrance": "fade",
                    "transition": "hard_cut",
                    "text_focus": "A journey begins",
                },
                {
                    "scene_index": 1,
                    "type": "SmartImage",
                    "variant": "full_bleed_cinematic",
                    "visual_intent": "Give the second beat an atmospheric image",
                    "camera": "pan_left",
                    "entrance": "mask_reveal",
                    "transition": "cross_dissolve",
                    "text_focus": "",
                },
            ]
        })


class _FailingDirector:
    model = "openrouter/test-director"

    def chat(self, **_kwargs):
        raise RuntimeError("provider unavailable")


class _MapDirectionDirector:
    model = "openrouter/test-map-director"
    last_model = model

    def chat(self, **kwargs):
        if kwargs.get("json_schema_name") == "vidnets_art_direction_v1":
            return json.dumps({
                "style": "archive",
                "display_font": "Archivo Black",
                "body_font": "Inter",
                "accent_color": "#F2C14E",
                "visual_motif": "grounded Berlin locator",
                "motion_rhythm": "wide context into city reveal",
            })

        from services.editorial_director.director import _DIRECTOR_AUTO_TEMPLATES

        return json.dumps({
            "scenes": [{
                "scene_index": 0,
                "type": "AnimatedMap",
                "variant": "route_flight_arc",
                "visual_intent": "Locate the blockade at West Berlin",
                "frame_mode": "map",
                "overlay_policy": "none",
                "editorial_priority": 0.9,
                "camera": "slow_push",
                "entrance": "fade",
                "transition": "hard_cut",
                "text_focus": "",
                "display_text": "",
                "source_span": "",
                "emphasis_spans": [],
                "placement": "none",
                "template_id": _DIRECTOR_AUTO_TEMPLATES[0],
                "template_recipe": None,
                "map_shot": {
                    "enabled": True,
                    "location_names": ["Berlin"],
                    "feature_ids": [],
                    "route_id": "",
                    "source_ids": [],
                    "motion_profile": "route_trace",
                },
            }]
        })


class _BudgetedCueDirector(_MapDirectionDirector):
    def __init__(self):
        self.budgets = {}

    def chat(self, **kwargs):
        stage = kwargs.get("json_schema_name")
        self.budgets[stage] = kwargs.get("total_timeout_seconds")
        response = super().chat(**kwargs)
        if stage == "vidnets_scene_direction_v1":
            payload = json.loads(response)
            payload["scenes"][0]["motion_cues"] = [
                {"element": "map", "action": "pin", "anchor_text": "West Berlin"},
            ]
            return json.dumps(payload)
        return response


def test_director_cues_survive_manifest_with_bounded_stage_calls():
    client = _BudgetedCueDirector()
    narration = "In June 1948, Soviet authorities blocked road and rail access to West Berlin."
    plan = EditingDirector(llm_client=client).plan_documentary(
        script_text=narration,
        scene_texts=[narration],
        scene_durations=[4.0],
        topic="Berlin blockade",
        word_timings=[{"word": "West", "start": 2.1}, {"word": "Berlin", "start": 2.4}],
    )
    assert plan.director_metadata["planning_budget_seconds"] == 130.0
    assert client.budgets["vidnets_art_direction_v1"] <= 20.0
    assert client.budgets["vidnets_scene_direction_v1"] <= 55.0
    cues = plan.to_remotion_manifest()["scenes"][0]["motion_cues"]
    assert cues[0]["element"] == "map"
    assert cues[0]["time_local"] == 2.1


def test_adaptive_director_budget_scales_with_parallel_scene_waves(monkeypatch):
    monkeypatch.setenv("VIDZEO_EDITORIAL_DIRECTOR_ADAPTIVE_BUDGET", "1")
    monkeypatch.setenv("VIDZEO_EDITORIAL_DIRECTOR_TOTAL_TIMEOUT_SECONDS", "90")
    monkeypatch.setenv("VIDZEO_EDITORIAL_DIRECTOR_MAX_TOTAL_TIMEOUT_SECONDS", "3600")
    monkeypatch.setenv("VIDZEO_EDITORIAL_DIRECTOR_SCENE_BATCH_SIZE", "3")
    monkeypatch.setenv("VIDZEO_EDITORIAL_DIRECTOR_SCENE_WORKERS", "4")
    assert director_planning_budget(12)["total_seconds"] == 130
    assert director_planning_budget(13)["total_seconds"] == 225
    assert director_planning_budget(60)["total_seconds"] == 510
    assert director_planning_budget(900)["total_seconds"] == 3600
    monkeypatch.setenv("VIDZEO_EDITORIAL_DIRECTOR_ADAPTIVE_BUDGET", "0")
    assert director_planning_budget(60)["total_seconds"] == 90


def test_director_defaults_to_eight_scene_parallel_chunks(monkeypatch):
    monkeypatch.delenv("VIDZEO_EDITORIAL_DIRECTOR_SCENE_BATCH_SIZE", raising=False)
    monkeypatch.setenv("VIDZEO_EDITORIAL_DIRECTOR_ADAPTIVE_BUDGET", "1")
    assert director_planning_budget(24)["waves"] == 1
    assert director_planning_budget(33)["waves"] == 2


def test_apollo_documentary_uses_history_visual_grammar():
    niche = classify_niche(
        "Apollo 11",
        "",
        "The astronauts launched on Saturn V. The lunar module carried Armstrong to the Moon.",
    )
    assert niche == "history"
    assert "dated archival images" in niche_visual_grammar(niche)["use_when_grounded"]


def test_long_film_art_context_is_bounded_but_reaches_the_payoff():
    scenes = [f"Documentary beat {index} changes the story." for index in range(300)]
    context = _director_story_map(scenes, max_chars=5000, max_anchors=12)
    assert len(context) <= 5000
    assert "scene 0:" in context
    assert "scene 299:" in context
    assert context.count("scene ") == 12


def test_early_director_plan_retimes_cues_and_attaches_final_media():
    narration = "In June 1948, Soviet authorities blocked road and rail access to West Berlin."
    plan = EditingDirector(llm_client=_BudgetedCueDirector()).plan_documentary(
        script_text=narration, scene_texts=[narration], scene_durations=[4.0], topic="Berlin blockade",
    )
    updated = server_app._reconcile_parallel_director_plan(
        plan, [narration], [6.0], [{"src": "C:/verified/berlin.jpg", "type": "image"}],
        [{"word": "West", "start": 3.2}, {"word": "Berlin", "start": 3.5}],
    )
    assert updated is plan
    assert updated.scenes[0].duration == 6.0
    assert updated.scenes[0].motion_cues[0]["time_local"] == 3.2
    assert updated.scenes[0].primary_visual.asset["src"] == "C:/verified/berlin.jpg"
    assert server_app._reconcile_parallel_director_plan(plan, ["A different narration."], [6.0], [], []) is None


class _RouteChangingDirector(_MapDirectionDirector):
    model = "openai/gpt-6-luna"
    last_model = model
    model_chain = (
        "openai/gpt-6-luna",
        "openai/gpt-5.6-luna",
        "deepseek/deepseek-v4.1-flash",
        "qwen/qwen3.5-flash-02-23",
    )
    attempt_history = []

    def chat(self, **kwargs):
        if kwargs.get("json_schema_name") == "vidnets_copy_repair_v1":
            self.last_model = "qwen/qwen3.5-flash-02-23"
            self.attempt_history = [{
                "attempt": 1,
                "model": self.model,
                "resolved_model": self.last_model,
                "status_code": 200,
            }]
            return json.dumps({
                "items": [{
                    "scene_index": 0,
                    "display_text": "West Berlin",
                    "source_span": "West Berlin",
                    "emphasis_spans": [],
                }],
            })
        response = super().chat(**kwargs)
        if kwargs.get("json_schema_name") == "vidnets_art_direction_v1":
            self.last_model = self.model
            self.attempt_history = [{
                "attempt": 1,
                "model": self.model,
                "resolved_model": self.model,
                "status_code": 200,
            }]
        else:
            self.last_model = "deepseek/deepseek-v4.1-flash"
            payload = json.loads(response)
            payload["scenes"][0]["display_text"] = "A secret map"
            payload["scenes"][0]["source_span"] = "A secret map"
            payload["scenes"][0]["overlay_policy"] = "single_short_highlight"
            response = json.dumps(payload)
            self.attempt_history = [{
                "attempt": 1,
                "model": self.model,
                "resolved_model": self.last_model,
                "status_code": 200,
            }]
        return response


class _ChunkedPartialDirector:
    model = "openrouter/test-chunked-director"
    last_model = model
    model_chain = (model,)
    attempt_history = []

    def __init__(self):
        self.scene_prompts = []
        self.call_options = []

    def chat(self, **kwargs):
        self.call_options.append(dict(kwargs))
        if kwargs.get("json_schema_name") == "vidnets_art_direction_v1":
            return json.dumps({
                "style": "editorial",
                "display_font": "Archivo Black",
                "body_font": "Inter",
                "accent_color": "#F2C14E",
                "visual_motif": "maps, evidence traces, and changing scale",
                "motion_rhythm": "trace, split, reveal, and settle on the narration",
                "story_arc": "The opening question leads through evidence to the central turn and its payoff.",
                "retention_plan": "Hook with the question, accelerate through proof, breathe at the consequence.",
            })

        from services.editorial_director.director import _DIRECTOR_AUTO_TEMPLATES

        prompt = str(kwargs.get("user_prompt") or "")
        self.scene_prompts.append(prompt)
        import re

        match = re.search(r"preserving their global indices (\d+)-(\d+)", prompt)
        start, end = int(match.group(1)), int(match.group(2))
        if start == 2:
            raise TimeoutError("simulated slow scene batch")
        scenes = []
        for index in range(start, end + 1):
            scenes.append({
                "scene_index": index,
                "type": "SmartImage",
                "variant": "full_bleed_cinematic",
                "visual_intent": "Carry the narrated image with a deliberate reframe.",
                "frame_mode": "media_only",
                "overlay_policy": "none",
                "editorial_priority": 0.2,
                "camera": "slow_push",
                "entrance": "fade",
                "transition": "hard_cut",
                "text_focus": "",
                "display_text": "",
                "source_span": "",
                "emphasis_spans": [],
                "placement": "none",
                "template_id": _DIRECTOR_AUTO_TEMPLATES[0],
                "template_recipe": None,
                "map_shot": {
                    "enabled": False,
                    "location_names": [],
                    "feature_ids": [],
                    "route_id": "",
                    "source_ids": [],
                    "motion_profile": "auto",
                },
            })
        return json.dumps({"scenes": scenes})


def _plan(client):
    texts = ["A journey begins with one decision.", "The next scene follows its consequences."]
    return EditingDirector(llm_client=client).plan_documentary(
        script_text=" ".join(texts),
        scene_texts=texts,
        scene_durations=[3.0, 4.0],
        topic="A short documentary",
        category="history",
        variation_seed=73,
    )


def test_director_model_route_is_recorded_and_scene_lock_survives_manifest():
    plan = _plan(_FakeDirector())
    plan.scenes[0].locked = True

    manifest = plan.to_remotion_manifest()

    assert manifest["edit_plan_schema_version"] == 4
    assert manifest["director"]["status"] == "directed"
    assert manifest["director"]["model"] == "openrouter/test-director"
    assert manifest["scenes"][0]["locked"] is True
    assert manifest["scenes"][0]["design"]["locked"] is True


def test_director_records_actual_parallel_scene_batch_fallback_route():
    narration = "In June 1948, Soviet authorities blocked road and rail access to West Berlin."
    plan = EditingDirector(llm_client=_RouteChangingDirector()).plan_documentary(
        script_text=narration,
        scene_texts=[narration],
        scene_durations=[6.0],
        topic="Berlin Airlift",
        category="history",
        variation_seed=20260926,
    )

    routes = plan.director_metadata["model_routes"]
    assert routes[0]["stage"] == "art_direction"
    assert routes[0]["selected_model"] == "openai/gpt-6-luna"
    assert routes[0]["fallback_used"] is False
    assert routes[1]["stage"] == "scene_direction"
    assert routes[1]["scene_indices"] == [0]
    assert routes[1]["selected_model"] == "deepseek/deepseek-v4.1-flash"
    assert routes[1]["fallback_used"] is True
    assert routes[2]["stage"] == "copy_repair"
    assert routes[2]["scene_indices"] == [0]
    assert routes[2]["selected_model"] == "qwen/qwen3.5-flash-02-23"
    assert routes[2]["fallback_used"] is True
    assert plan.director_metadata["fallback_used"] is True
    assert plan.director_metadata["models_used"] == [
        "openai/gpt-6-luna",
        "deepseek/deepseek-v4.1-flash",
        "qwen/qwen3.5-flash-02-23",
    ]
    assert plan.scenes[0].display_text == "West Berlin"
    # Copy repair did not return an exact emphasis span, so it must not be
    # inferred from the whole two-word copy at the rendering handoff.
    assert plan.scenes[0].emphasis_spans == []
    manifest = plan.to_remotion_manifest()
    assert manifest["director"]["model_routes"] == routes
    assert manifest["scenes"][0]["visual_role"] == "map_canvas"
    assert manifest["scenes"][0]["visual_role_contract_version"] == 2


def test_scene_batches_keep_full_story_context_and_isolate_a_timed_out_chunk(monkeypatch):
    monkeypatch.setenv("VIDZEO_EDITORIAL_DIRECTOR_SCENE_BATCH_SIZE", "2")
    monkeypatch.setenv("VIDZEO_EDITORIAL_DIRECTOR_SCENE_WORKERS", "2")
    client = _ChunkedPartialDirector()
    texts = [
        "The opening asks why this place changed.",
        "The first clue appears in the original record.",
        "A new mechanism shifts the balance of power.",
        "The turning point changes what comes next.",
        "The final evidence reveals the human cost.",
        "The story closes on the consequence of that choice.",
    ]
    plan = EditingDirector(llm_client=client).plan_documentary(
        script_text=" ".join(texts),
        scene_texts=texts,
        scene_durations=[4.0] * len(texts),
        topic="A story with a turning point",
        category="history",
        variation_seed=20260928,
    )

    assert plan.director_metadata["status"] == "directed"
    assert plan.director_metadata["local_fallback_scene_indices"] == [2, 3]
    assert plan.director_metadata["creative_coverage"] == "partial"
    assert plan.director_metadata["fallback_scene_count"] == 2
    assert plan.director_metadata["opening_fully_directed"] is False
    assert plan.director_metadata["scene_batch_size"] == 2
    assert len(plan.scenes) == len(texts)
    scene_prompts = [call["user_prompt"] for call in client.call_options
                     if call.get("json_schema_name") == "vidnets_scene_direction_v1"]
    assert len(scene_prompts) == 3
    assert all("The opening question leads through evidence" in prompt for prompt in scene_prompts)
    assert all("Full-film story map" in prompt for prompt in scene_prompts)
    assert all("Immediate setup just before this window" in prompt for prompt in scene_prompts)
    assert all("Immediate narrative handoff after this window" in prompt for prompt in scene_prompts)
    assert all("STRICT REMOTION DESIGN CONTRACT" in prompt for prompt in scene_prompts)
    assert all('"AdaptiveChart"' in prompt and '"quote_fragment_punch"' in prompt for prompt in scene_prompts)
    assert all("Creative variation seed: 20260928" in prompt for prompt in scene_prompts)
    assert all("Film-wide visual argument opportunities" in prompt for prompt in scene_prompts)
    limits = {"vidnets_art_direction_v1": 20, "vidnets_scene_direction_v1": 55, "vidnets_copy_repair_v1": 10}
    assert all(call.get("total_timeout_seconds", 999) <= limits[call["json_schema_name"]]
               for call in client.call_options if call.get("json_schema_name") in limits)


def test_scene_pool_returns_at_deadline_when_provider_ignores_timeout(monkeypatch):
    class StalledSceneDirector:
        model = "openrouter/stalled-director"
        last_model = model

        def chat(self, **kwargs):
            if kwargs.get("json_schema_name") == "vidnets_art_direction_v1":
                return json.dumps({"style": "editorial", "visual_motif": "narrated evidence"})
            time.sleep(11.5)
            return json.dumps({"scenes": []})

    monkeypatch.setenv("VIDZEO_EDITORIAL_DIRECTOR_TOTAL_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("VIDZEO_EDITORIAL_DIRECTOR_SCENE_TIMEOUT_SECONDS", "10")
    monkeypatch.setenv("VIDZEO_EDITORIAL_DIRECTOR_ADAPTIVE_BUDGET", "0")
    started = time.monotonic()
    plan = EditingDirector(llm_client=StalledSceneDirector()).plan_documentary(
        script_text="A source shows what changed.",
        scene_texts=["A source shows what changed."],
        scene_durations=[4.0],
        topic="A grounded historical change", category="history",
    )
    assert time.monotonic() - started < 11.4
    assert plan.director_metadata["status"] == "deterministic_fallback"
    assert plan.scenes[0].display_text == ""


def test_director_failure_reports_deterministic_fallback():
    plan = _plan(_FailingDirector())

    assert plan.director_metadata["status"] == "deterministic_fallback"
    assert plan.director_metadata["reason"] == "request_error:RuntimeError"
    assert len(plan.scenes) == 2


def test_unsupported_chart_repair_keeps_frame_mode_asset_and_director_audit(monkeypatch):
    monkeypatch.setattr(server_app, "_remotion_runtime_asset_errors", lambda: [])
    manifest = {
        "edit_plan_schema_version": 4,
        "director": {"status": "directed", "model": "openrouter/test-director"},
        "scenes": [
            {
                "duration": 4.0,
                "narration": "The route changed after the blockade.",
                "text": {"frame_mode": "data", "overlay_policy": "none"},
                "primary_visual": {
                    "type": "Chart",
                    "variant": "editorial_bars",
                    "props": {"no_data": True},
                    "asset": {"src": "https://assets.example.test/airlift.mp4", "type": "video"},
                },
                "director_decision": {"scene_index": 0},
            },
            {
                "duration": 4.0,
                "narration": "Two grounded values are compared.",
                "text": {"frame_mode": "data", "overlay_policy": "none"},
                "primary_visual": {
                    "type": "Chart",
                    "variant": "editorial_bars",
                    "props": {"data": [{"label": "A", "value": 1}, {"label": "B", "value": 2}]},
                    "asset": {"src": "https://assets.example.test/comparison.jpg", "type": "image"},
                },
            },
        ],
    }

    repaired = server_app._repair_unsupported_chart_scenes(manifest)

    assert repaired == [0]
    scene = manifest["scenes"][0]
    assert scene["primary_visual"]["type"] == "SmartVideo"
    assert scene["primary_visual"]["asset"]["src"].endswith(".mp4")
    assert scene["text"]["frame_mode"] == "media_only"
    assert scene["text"]["frame_contract_validation"] == {
        "status": "repaired",
        "requested_mode": "data",
        "reason": "chart_has_no_grounded_comparable_values",
        "resolved_mode": "media_only",
    }
    assert scene["director_decision"]["chart_repair"] == "unsupported_values_to_media"
    assert manifest["director"]["model"] == "openrouter/test-director"
    assert manifest["director"]["repairs"] == [
        {"type": "unsupported_chart_to_media", "scene_indices": [0]}
    ]
    assert manifest["scenes"][1]["primary_visual"]["type"] == "Chart"

    validation = server_app._validate_remotion_edit_plan(manifest, 2)
    assert validation["status"] == "pass"
    assert not any(error["code"] == "frame_visual_mismatch" for error in validation["errors"])


def test_repeated_adjacent_treatment_rerolls_layout_instead_of_downgrading_to_media(monkeypatch):
    monkeypatch.setattr(server_app, "_remotion_runtime_asset_errors", lambda: [])
    manifest = {
        "edit_plan_schema_version": 4,
        "director": {"status": "directed", "model": "openrouter/test-director", "overlay_budget": 0},
        "scenes": [
            {
                "duration": 4.0,
                "narration": "First kinetic statement.",
                "text": {"frame_mode": "kinetic", "overlay_policy": "none"},
                "template_id": "kinetic-statement",
                "graphic": {"template_id": "kinetic-statement", "props": {}},
                "primary_visual": {
                    "type": "KineticText",
                    "variant": "staggered_lines",
                    "props": {"variant": "staggered_lines"},
                    "asset": {"src": "https://assets.example.test/scene0.jpg", "type": "image"},
                },
                "pacing": {"continuation": False},
            },
            {
                "duration": 4.0,
                "narration": "Second adjacent kinetic statement with identical treatment.",
                "text": {"frame_mode": "kinetic", "overlay_policy": "none"},
                "template_id": "kinetic-statement",
                "graphic": {"template_id": "kinetic-statement", "props": {}},
                "primary_visual": {
                    "type": "KineticText",
                    "variant": "staggered_lines",
                    "props": {"variant": "staggered_lines"},
                    "asset": {"src": "https://assets.example.test/scene1.jpg", "type": "image"},
                },
                "pacing": {"continuation": False},
            },
        ],
    }

    raw_validation = server_app._validate_remotion_edit_plan(manifest, 2)
    assert raw_validation["status"] == "fail"
    assert any(error["code"] == "repeated_adjacent_treatment" for error in raw_validation["errors"])

    repaired = server_app._repair_repeated_adjacent_treatments(manifest)
    assert repaired == [1]

    s0 = manifest["scenes"][0]
    s1 = manifest["scenes"][1]
    assert s0["primary_visual"]["variant"] != s1["primary_visual"]["variant"]
    assert s0.get("template_id") == s1.get("template_id") == "kinetic-statement"
    assert s1["text"]["frame_mode"] == "kinetic"
    assert s1["primary_visual"]["type"] == "KineticText"

    post_validation = server_app._validate_remotion_edit_plan(manifest, 2)
    assert post_validation["status"] == "pass"
    assert not any(error["code"] == "repeated_adjacent_treatment" for error in post_validation["errors"])
    assert manifest["director"]["repairs"] == [
        {"type": "adjacent_treatment_quality_repair", "scene_indices": [1]}
    ]


def test_export_preflight_unifies_visual_dna_typography_aliases(monkeypatch):
    monkeypatch.setattr(server_app, "_remotion_runtime_asset_errors", lambda: [])
    manifest = {
        "edit_plan_schema_version": 4,
        "director": {"status": "directed", "overlay_budget": 0},
        "visual_dna": {"typography": {
            "display": "Archivo Black",
            "body": "Plus Jakarta Sans",
            "numbers": "JetBrains Mono",
        }},
        "visual_system": {"visual_dna": {"typography": {
            "display": "Archivo Black",
            "body": "Inter",
            "numbers": "Inter",
        }}},
        "scenes": [{
            "duration": 4,
            "narration": "A grounded media beat.",
            "text": {"frame_mode": "media_only", "overlay_policy": "none"},
            "primary_visual": {"type": "SmartImage", "variant": "full_bleed_cinematic", "props": {},
                               "asset": {"src": "https://assets.example.test/one.jpg", "type": "image"}},
        }],
    }

    result = server_app._validate_remotion_edit_plan(manifest, 1)

    assert result["status"] == "pass"
    assert manifest["visual_dna"]["typography"] == {
        "display": "Archivo Black",
        "body": "Plus Jakarta Sans",
        "numbers": "Plus Jakarta Sans",
    }
    assert manifest["visual_system"]["visual_dna"]["typography"] == manifest["visual_dna"]["typography"]


def test_same_family_template_alias_does_not_reject_distinct_chart_variants(monkeypatch):
    monkeypatch.setattr(server_app, "_remotion_runtime_asset_errors", lambda: [])
    manifest = {
        "edit_plan_schema_version": 4,
        "director": {"overlay_budget": 0},
        "scenes": [
            {"duration": 4, "narration": "The total reached 2 million in 2000 and 4 million in 2010.",
             "text": {"frame_mode": "data", "overlay_policy": "none"}, "template_id": "bar-chart",
             "graphic": {"template_id": "bar-chart", "props": {}},
             "primary_visual": {"type": "Chart", "variant": "editorial_bar", "props": {"data": [{"label": "2000", "value": "2 million"}, {"label": "2010", "value": "4 million"}]} }},
            {"duration": 4, "narration": "The same years rank at 4 million and 2 million.",
             "text": {"frame_mode": "data", "overlay_policy": "none"}, "template_id": "bar-chart",
             "graphic": {"template_id": "bar-chart", "props": {}},
             "primary_visual": {"type": "Chart", "variant": "ranked_horizontal_bars", "props": {"data": [{"label": "2010", "value": "4 million"}, {"label": "2000", "value": "2 million"}]} }},
        ],
    }
    assert server_app._repair_repeated_adjacent_treatments(manifest) == []
    assert manifest["scenes"][0]["template_id"] == manifest["scenes"][1]["template_id"] == "bar-chart"
    assert server_app._directed_treatment_signature(manifest["scenes"][0]) != server_app._directed_treatment_signature(manifest["scenes"][1])
    result = server_app._validate_remotion_edit_plan(manifest, 2)
    assert not any(error["code"] == "repeated_adjacent_treatment" for error in result["errors"])



def test_local_fallback_keeps_grounded_maps_without_unapproved_copy():
    texts = [
        "Maps should follow the story. Japan's full outline establishes the setting before the camera moves toward Osaka. This city callout marks Osaka without drawing a route or highlighting the prefecture.",
        "Now the story shifts to India. Madhya Pradesh appears as a real state polygon, with its border revealed over local terrain.",
    ]
    plan = EditingDirector(llm_client=_FailingDirector()).plan_documentary(
        script_text=" ".join(texts),
        scene_texts=texts,
        scene_durations=[20.0, 20.0],
        topic="A map validation short",
        category="documentary",
        variation_seed=19,
    )

    first, second = plan.scenes
    assert first.primary_visual.type == "AnimatedMap"
    assert first.map_shot["location_names"] == ["Japan", "Osaka"]
    assert first.map_shot["route_id"] == ""
    assert first.display_text == ""
    assert second.primary_visual.type == "AnimatedMap"
    assert second.map_shot["feature_ids"] == ["IND-3261"]
    assert second.display_text == ""
    assert all(scene.text.get("overlay_policy") == "none" for scene in plan.scenes)


def test_motion_cues_align_to_words_and_reject_unsupported_or_invented_actions():
    narration = "The route reached West Berlin, then the figures rose."
    cues = _validated_motion_cues(
        [
            {"element": "map", "action": "trace", "anchor_text": "West Berlin"},
            {"element": "data", "action": "count", "anchor_text": "figures rose"},
            {"element": "map", "action": "count", "anchor_text": "route"},
            {"element": "image", "action": "pan", "anchor_text": "invented place"},
        ],
        narration, 10.0, 5.0,
        [
            {"word": "West", "start": 11.1},
            {"word": "Berlin,", "start": 11.4},
            {"word": "figures", "start": 12.7},
            {"word": "rose.", "start": 13.0},
        ],
    )
    assert [(cue["element"], cue["action"], cue["time_local"]) for cue in cues] == [
        ("map", "trace", 1.1), ("data", "count", 2.7),
    ]


def test_llm_map_choice_cannot_turn_a_city_locator_into_a_fake_route():
    from services.editorial_director.director import _scene_frame_contract_failure

    narration = "In June 1948, Soviet authorities blocked road and rail access to West Berlin."
    plan = EditingDirector(llm_client=_MapDirectionDirector()).plan_documentary(
        script_text=narration,
        scene_texts=[narration],
        scene_durations=[6.0],
        topic="Berlin Airlift",
        category="history",
        variation_seed=20260926,
    )
    scene = plan.scenes[0]

    assert plan.director_metadata["status"] == "directed"
    assert scene.text["frame_mode"] == "map"
    assert scene.primary_visual.type == "AnimatedMap"
    assert scene.primary_visual.variant == "country_spotlight"
    assert scene.primary_visual.props["location"] == "BERLIN"
    assert not scene.primary_visual.props.get("from") and not scene.primary_visual.props.get("to")
    assert scene.map_shot["motion_profile"] != "route_trace"
    assert _scene_frame_contract_failure(scene) == ""


def test_director_prompt_calls_for_script_grounded_scene_specific_modern_design():
    from services.editorial_director.director import _LLM_DIRECTOR_PROMPT

    prompt = _LLM_DIRECTOR_PROMPT.lower()
    assert "each scene its own visual intent" in prompt
    assert "contemporary sans-serif typography" in prompt
    assert "do not invent facts" in prompt
    assert "fake telemetry" in prompt
    assert "before choosing media_only" in prompt
    assert "a single place earns a locator or boundary reveal; it does not earn a route" in prompt


def test_director_map_refuses_locations_missing_from_bundled_catalog():
    triggers = SimpleNamespace(country_corridor=None, countries=[], locations=["Atlantis", "El Dorado"])

    props = _build_map_props("route_flight_arc", triggers, "A route crosses from Atlantis to El Dorado.")

    assert props["no_data"] is True


def test_edit_plan_preflight_rejects_missing_remotion_font(monkeypatch):
    monkeypatch.setattr(
        server_app,
        "_remotion_runtime_asset_errors",
        lambda: ["public/font-library/Inter-Regular.ttf"],
    )

    result = server_app._validate_remotion_edit_plan({
        "edit_plan_schema_version": 3,
        "scenes": [{
            "duration": 2.0,
            "narration": "A title scene.",
            "primary_visual": {
                "type": "ChapterTitle",
                "variant": "cinematic_minimal_act",
                "props": {"title": "A TEST"},
            },
        }],
    }, 1)

    assert result["status"] == "fail"
    assert any(error["code"] == "missing_runtime_asset" for error in result["errors"])


def test_remotion_runtime_bundle_fonts_maps_and_icons_are_present():
    assert server_app._remotion_runtime_asset_errors() == []


def test_person_profile_uses_only_supplied_alpha_cutouts_for_isolated_portrait_motion():
    profile = (ROOT / "src" / "remotion" / "components" / "PersonProfile.jsx").read_text(encoding="utf-8")
    assert "props?.cutout_src" in profile
    assert "asset?.cutout_src" in profile
    assert "Never fake background removal" in profile
    assert "objectFit: 'contain'" in profile
    assert "clipPath: `inset(0 0 ${interpolate(enter" in profile


def test_render_smoke_uses_registered_director_visual_variants():
    from services.editorial_director.contract import SCENE_TYPE_VARIANTS, VisualSceneType

    smoke = (ROOT / "scripts" / "render-adaptive-documentary-smoke.js").read_text(encoding="utf-8")
    assert "variant: 'cause_and_effect_flow'" in smoke
    assert "variant: 'yellow_marker_highlight'" in smoke
    assert "templateId: 'generated-recipe'" in smoke
    assert "variant: 'percentage_ring'" in smoke
    assert "variant: 'stat_with_context'" in smoke
    assert "cause_and_effect_flow" in SCENE_TYPE_VARIANTS[VisualSceneType.DIAGRAM.value]
    assert "yellow_marker_highlight" in SCENE_TYPE_VARIANTS[VisualSceneType.DOCUMENT_HIGHLIGHT.value]


def test_remotion_worker_selection_scales_with_available_memory():
    from services.render_resource_manager import select_remotion_workers

    assert select_remotion_workers(4096, 8192, cpu_count=8)["recommended_workers"] == 7
    assert select_remotion_workers(1800, 8192, cpu_count=8)["recommended_workers"] == 2
    assert select_remotion_workers(1500, 8192, cpu_count=8)["recommended_workers"] == 1
    assert select_remotion_workers(1474, 8192, cpu_count=8)["recommended_workers"] == 1
    assert select_remotion_workers(2200, 8192, cpu_count=8, video_heavy=True)["recommended_workers"] == 3
    assert select_remotion_workers(4096, 8192, cpu_count=2)["recommended_workers"] == 2


def test_v4_map_preflight_accepts_madhya_pradesh_and_rejects_osaka_prefecture_for_city_mention(monkeypatch):
    monkeypatch.setattr(server_app, "_remotion_runtime_asset_errors", lambda: [])

    def manifest(narration, locations, feature_ids, map_props):
        return {
            "edit_plan_schema_version": 4,
            "fps": 30,
            "scenes": [{
                "scene_index": 0,
                "duration": 60,
                "narration": narration,
                "primary_visual": {
                    "type": "AnimatedMap",
                    "variant": "country_spotlight",
                    "props": map_props,
                },
                "graphic": {"template_id": "map-route", "props": {}},
                "map_shot": {
                    "enabled": True,
                    "location_names": locations,
                    "feature_ids": feature_ids,
                    "route_id": "",
                    "source_ids": [],
                },
            }],
        }

    madhya = server_app._validate_remotion_edit_plan(
        manifest(
            "The story moves through India and Madhya Pradesh.",
            ["India", "Madhya Pradesh"],
            ["IND-3261"],
            {"country": "India", "region": "Madhya Pradesh"},
        ),
        1,
    )
    assert madhya["status"] == "pass", madhya["errors"]

    osaka = server_app._validate_remotion_edit_plan(
        manifest(
            "The journey reaches Japan and the city of Osaka.",
            ["Japan", "Osaka"],
            ["JPN-1852"],
            {"country": "Japan", "location": "Osaka"},
        ),
        1,
    )
    assert any(error["code"] == "unsupported_map_feature" for error in osaka["errors"])

    mismatch = server_app._validate_remotion_edit_plan(
        manifest(
            "Ships crossed Panama before traveling around South America.",
            ["Panama"],
            [],
            {"country": "USA", "country_code": "USA", "location": "UNITED STATES"},
        ),
        1,
    )
    assert mismatch["status"] == "fail"
    assert any(error["code"] == "map_subject_mismatch" for error in mismatch["errors"])


def test_grounded_panama_location_overrides_an_unrelated_map_country():
    from services.editorial_director.director import apply_grounded_map_country_props

    props = apply_grounded_map_country_props(
        {"country": "USA", "country_code": "USA", "location": "UNITED STATES", "title": "UNITED STATES"},
        {"enabled": True, "location_names": ["Panama"], "feature_ids": []},
    )

    assert props["country"] == "PAN"
    assert props["country_code"] == "PAN"
    assert props["location"] == "PANAMA"
    assert props["title"] == "PANAMA"


def test_flask_serves_brand_icons_and_remotion_editor_bundle():
    client = server_app.app.test_client()
    for url in ("/assets/vidnets-icon-glow.png", "/favicon.ico", "/remotion-player.js"):
        response = client.get(url)
        assert response.status_code == 200, url
        assert response.data, url


def test_remotion_primary_visual_survives_legacy_overlay_suppression_metadata():
    root = Path(__file__).resolve().parents[1]
    registry = (root / "src" / "remotion" / "engine" / "SceneRegistry.js").read_text(encoding="utf-8")
    overlay = (root / "src" / "remotion" / "components" / "MediaOverlayCanvas.jsx").read_text(encoding="utf-8")

    assert "return <EditorialSceneCanvas scene={scene} visualDNA={visualDNA} props={sanitizedProps} />;" in registry
    assert "if (sanitizedProps.no_data) return null;" not in registry
    assert "if (!contract.family && scene.media_composition?.overlay_grammar === 'none') return null;" in overlay


def test_remotion_routes_map_scenes_before_generic_contract_overlays():
    root = Path(__file__).resolve().parents[1]
    registry = (root / "src" / "remotion" / "engine" / "SceneRegistry.js").read_text(encoding="utf-8")
    canvas = (root / "src" / "remotion" / "components" / "EditorialSceneCanvas.jsx").read_text(encoding="utf-8")

    assert registry.index("if (Component === AnimatedMap)") < registry.index("if (hasEditorialContract && !directed)")
    assert "editorialContract.enabled !== false" in canvas
    assert "editorialContract.family !== 'media_hold'" in canvas


def test_verified_document_capture_routes_to_screenshot_highlight_renderer():
    root = Path(__file__).resolve().parents[1]
    editorial_canvas = (root / "src" / "remotion" / "components" / "EditorialSceneCanvas.jsx").read_text(encoding="utf-8")

    assert "import { DocumentHighlight } from './DocumentHighlight';" in editorial_canvas
    capture_route = "return <DocumentHighlight scene={scene} visualDNA={visualDNA} props={{ ...props, source_capture: sourceCapture || props.source_capture }} />;"
    assert capture_route in editorial_canvas
    assert editorial_canvas.index(capture_route) < editorial_canvas.index("const editorialContract = props.editorial_contract || scene.editorial_contract;")
    document_highlight = (root / "src" / "remotion" / "components" / "DocumentHighlight.jsx").read_text(encoding="utf-8")
    assert "const imageSrc = capture.image_path || capture.image_url || '';" in document_highlight


def test_render_plan_rejects_unverified_map_endpoints_and_accepts_catalogued_route():
    def manifest_for(props):
        return {
            "edit_plan_schema_version": 3,
            "scenes": [{
                "duration": 2.0,
                "narration": "The route runs from Los Angeles to New York.",
                "primary_visual": {
                    "type": "AnimatedMap",
                    "variant": "route_flight_arc",
                    "props": props,
                },
            }],
        }

    invalid = server_app._validate_remotion_edit_plan(
        manifest_for({"from": "Atlantis", "to": "El Dorado"}), 1,
    )
    valid = server_app._validate_remotion_edit_plan(
        manifest_for({"from": "Los Angeles", "to": "New York"}), 1,
    )

    assert {error["code"] for error in invalid["errors"]} == {"unsupported_map_location"}
    assert valid["status"] == "pass"


def test_source_capture_requires_public_urls_and_uses_cards_when_capture_is_unavailable(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="non-public"):
        source_evidence._validated_public_url("http://127.0.0.1/secret")

    monkeypatch.setattr(source_evidence, "_validated_public_url", lambda value: value)
    monkeypatch.setattr(
        source_evidence,
        "_fetch_public_html",
        lambda _url: ("<title>Source record</title><p>The verified passage is present.</p>", "https://example.org/report"),
    )
    monkeypatch.setattr(source_evidence.shutil, "which", lambda _name: None)

    verified = capture_source_evidence(
        "https://example.org/report", "The verified passage is present.", tmp_path,
    )
    unavailable = capture_source_evidence(
        "https://example.org/report", "A passage that is not on the page.", tmp_path,
    )

    assert verified["status"] == "verified_quote_card"
    assert verified["exact_excerpt"] == "The verified passage is present."
    assert not verified["image_path"]
    assert unavailable["status"] == "attributed_card"
    assert unavailable["exact_excerpt"] == ""


def test_verified_source_highlight_requires_existing_capture_file(tmp_path):
    result = server_app._validate_remotion_edit_plan({
        "edit_plan_schema_version": 3,
        "scenes": [{
            "duration": 2.0,
            "primary_visual": {
                "type": "DocumentHighlight",
                "variant": "yellow_marker_highlight",
                "props": {
                    "source_capture": {
                        "status": "verified_capture",
                        "verification_status": "exact_passage_verified",
                        "exact_excerpt": "A verified passage.",
                        "image_path": str(tmp_path / "missing.png"),
                    },
                },
            },
        }],
    }, 1)

    assert result["status"] == "fail"
    assert "source_capture_asset_missing" in {error["code"] for error in result["errors"]}


def test_editor_manifest_export_is_remotion_and_reports_quality_checks(tmp_path, monkeypatch):
    monkeypatch.setenv('VIDZEO_AUTH_MODE', 'local')
    monkeypatch.setenv('VIDZEO_PACKAGED', '0')
    image = tmp_path / "editor-frame.png"
    image.write_bytes(b"test media placeholder")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    monkeypatch.setattr(server_app, "OUTPUT_DIR", output_dir)
    calls = {}

    def fake_render(manifest, output_path, config):
        calls["backend"] = config.get("render_backend")
        calls["required"] = config.get("semantic_render_required")
        calls["scene_count"] = len(manifest.get("scenes") or [])
        Path(output_path).write_bytes(b"rendered")
        return {"renderer": "remotion-react"}

    monkeypatch.setattr(
        "services.native_render_policy.render_semantic_documentary",
        fake_render,
    )
    monkeypatch.setattr(
        server_app,
        "_validate_remotion_master",
        lambda *_args, **_kwargs: {"status": "pass", "backend": "remotion", "frame_review": {"status": "pass"}},
    )

    manifest = {
        "version": 2,
        "edit_plan_schema_version": 3,
        "fps": 24,
        "width": 320,
        "height": 180,
        "duration": 1.5,
        "scenes": [{
            "id": "editor-scene-1",
            "scene_index": 0,
            "start": 0,
            "duration": 1.5,
            "narration": "A grounded scene.",
            "primary_visual": {
                "type": "SmartImage",
                "variant": "full_bleed_cinematic",
                "props": {},
                "asset": {"src": str(image), "path": str(image)},
            },
            "media": {"type": "image", "src": str(image), "path": str(image)},
        }],
    }
    response = server_app.app.test_client().post(
            "/api/editor/render", headers={'Authorization': 'Bearer local-dev-token'}, json={"name": "editor-remotion-contract", "manifest": manifest},
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    assert calls == {"backend": "remotion", "required": True, "scene_count": 1}
    assert payload["render_metadata"]["selected_backend"] == "remotion"
    assert payload["render_metadata"]["status"] == "validated"
    saved = json.loads(Path(payload["edit_plan_path"]).read_text(encoding="utf-8"))
    assert saved["render_metadata"]["quality_checks"]["status"] == "pass"


def test_editor_rejects_missing_smart_media_and_keeps_plan_for_retry(tmp_path, monkeypatch):
    monkeypatch.setenv('VIDZEO_AUTH_MODE', 'local')
    monkeypatch.setenv('VIDZEO_PACKAGED', '0')
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    monkeypatch.setattr(server_app, "OUTPUT_DIR", output_dir)
    manifest = {
        "edit_plan_schema_version": 3,
        "fps": 24,
        "width": 320,
        "height": 180,
        "duration": 1.5,
        "scenes": [{
            "id": "missing-scene",
            "scene_index": 0,
            "start": 0,
            "duration": 1.5,
            "narration": "A scene with missing source media.",
            "primary_visual": {
                "type": "SmartImage",
                "variant": "full_bleed_cinematic",
                "props": {},
            },
        }],
    }
    response = server_app.app.test_client().post(
            "/api/editor/render", headers={'Authorization': 'Bearer local-dev-token'}, json={"name": "missing-editor-media", "manifest": manifest},
    )

    assert response.status_code == 500
    payload = response.get_json()
    assert "missing_scene_asset" in payload["error"]
    saved_path = Path(payload["edit_plan_path"])
    assert saved_path.is_file()
    saved = json.loads(saved_path.read_text(encoding="utf-8"))
    assert saved["render_metadata"]["status"] == "failed"


def test_legacy_video_clip_manifest_upgrades_to_remotion_scene(tmp_path):
    image = tmp_path / "legacy.png"
    image.write_bytes(b"test media placeholder")

    upgraded = server_app._upgrade_legacy_video_clip_manifest({
        "fps": 24,
        "duration": 3.0,
        "video_clips": [{
            "path": str(image),
            "url": str(image),
            "media_type": "image",
            "start": 0.5,
            "end": 2.5,
            "track_offset": 1.0,
        }],
    }, width=640, height=360)

    assert len(upgraded["scenes"]) == 1
    scene = upgraded["scenes"][0]
    assert scene["primary_visual"]["type"] == "SmartImage"
    assert scene["start"] == 1.0
    assert scene["duration"] == 2.0
    assert scene["media"]["path"] == str(image)


def test_remotion_frame_timeline_does_not_accumulate_fractional_scene_rounding():
    from services.native_render_policy import _frame_align_remotion_manifest

    scenes = [
        {"start": round(index * 0.05, 6), "duration": 0.05, "narration": f"Beat {index}."}
        for index in range(10)
    ]
    aligned = _frame_align_remotion_manifest({
        "edit_plan_schema_version": 4,
        "fps": 30,
        "duration": 0.5,
        "scenes": scenes,
    })

    assert aligned["duration_in_frames"] == 15
    assert sum(scene["duration_in_frames"] for scene in aligned["scenes"]) == 15
    assert [scene["from"] for scene in aligned["scenes"]] == [
        sum(previous["duration_in_frames"] for previous in aligned["scenes"][:index])
        for index in range(10)
    ]
    assert aligned["scenes"][-1]["from"] + aligned["scenes"][-1]["duration_in_frames"] == 15


@pytest.mark.parametrize('interrupted', [False, True, 'resuming_render'])
@pytest.mark.parametrize('failure_kind', ['low_memory', 'render_failed'])
@pytest.mark.parametrize('validation_ok', [False, True])
def test_queue_retry_renders_saved_plan_without_rebuilding_generation(tmp_path, monkeypatch, interrupted, failure_kind, validation_ok):
    package = tmp_path / "paused-video"
    checkpoints = package / "checkpoints"
    editorial = package / "editorial"
    media_dir = tmp_path / "media"
    for directory in (checkpoints, editorial, package / "render", media_dir):
        directory.mkdir(parents=True, exist_ok=True)
    image = media_dir / "scene.jpg"
    audio = media_dir / "voiceover.mp3"
    image.write_bytes(b"image-bytes" * 32)
    audio.write_bytes(b"audio-bytes" * 32)
    plan_path = editorial / "remotion_edit_plan.json"
    plan_path.write_text(json.dumps({
        "edit_plan_schema_version": 4,
        "fps": 30,
        "width": 1920,
        "height": 1080,
        "duration": 1.0,
        "config": {},
        "director": {"status": "directed", "model": "openrouter/test"},
        "audio": {"voiceover": {"src": str(audio)}},
        "scenes": [{
            "scene_index": 0,
            "start": 0.0,
            "duration": 1.0,
            "narration": "One beat only.",
            "media": {"type": "image", "src": str(image)},
            "primary_visual": {
                "type": "SmartImage",
                "asset": {"type": "image", "src": str(image)},
            },
        }],
    }), encoding="utf-8")
    (checkpoints / "state.json").write_text(json.dumps({
        "stage": (interrupted if isinstance(interrupted, str) else "ready_for_render") if interrupted else "waiting_for_memory",
        "status": "saved" if interrupted else "retryable",
    }), encoding="utf-8")
    (checkpoints / "render_failure.json").write_text(json.dumps({
        "kind": failure_kind, "retryable": True,
    }), encoding="utf-8")
    if interrupted:
        (checkpoints / 'render_failure.json').unlink()
    (checkpoints / "pre_render.json").write_text(json.dumps({
        "backend": "remotion", "edit_plan_path": str(plan_path),
        "media_paths": [str(image)], "audio_path": str(audio), "duration": 1.0,
    }), encoding="utf-8")
    (checkpoints / "audio_plan.json").write_text(json.dumps({
        "audio_path": str(audio), "duration": 1.0,
        "scene_texts": ["One beat only."], "scene_durations": [1.0],
    }), encoding="utf-8")

    render_calls = []

    def fake_render(*, manifest, output_path, config, progress_callback, cancellation):
        assert callable(config.get('_creative_media_repair'))
        render_calls.append((len(manifest["scenes"]), config["render_backend"]))
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"rendered-remotion-video")
        reviewed = {**manifest, 'creative_review': {'status': 'pass_with_advisories'},
                    'preview_review': {'status': 'pass'},
                    'scenes': [{**scene, 'reviewed_revision': True} for scene in manifest['scenes']]}
        reviewed_path = output.parent / 'reviewed.manifest.json'
        reviewed_path.write_text(json.dumps(reviewed), encoding='utf-8')
        return {
            "output_path": str(output),
            "renderer": "remotion-react",
            "render_resources": {"recommended_workers": 2},
            "rendered_manifest_path": str(reviewed_path),
            "creative_review": reviewed['creative_review'],
            "preview_review": reviewed['preview_review'],
        }

    original_plan_bytes = plan_path.read_bytes()
    monkeypatch.setattr(server_app, "_validate_remotion_edit_plan", lambda _m, _n: {
        "status": "pass" if validation_ok else "fail", "asset_status": [], "errors": [] if validation_ok else ['fixture validation failure'],
    })
    monkeypatch.setattr(server_app, "_validate_remotion_master", lambda *args, **kwargs: {
        "status": "pass", "backend": "remotion",
    })
    monkeypatch.setattr(server_app, "_memory_status_snapshot", lambda: {"available_mb": 2048})
    def no_new_direction(*args, **kwargs):
        raise AssertionError('A saved render retry must not call the media director again')
    monkeypatch.setattr('services.editorial_director.media_repair.repair_failed_shots', no_new_direction)
    monkeypatch.setattr(
        "services.native_render_policy.render_semantic_documentary",
        fake_render,
    )
    progress = []

    if not validation_ok:
        with pytest.raises(server_app.RemotionEditPlanError, match='fixture validation failure'):
            server_app._resume_checkpointed_remotion_render(
                {'_output_package_dir': str(package), 'output_name': 'saved-edit'},
                lambda *_args: None)
        assert plan_path.read_bytes() == original_plan_bytes
        assert render_calls == []
        return

    result = server_app._resume_checkpointed_remotion_render(
        {
            "_output_package_dir": str(package),
            "output_package_dir": str(package),
            "output_name": "saved-edit",
            "content_title": "Saved Edit",
            "type": "long",
        },
        lambda value, message: progress.append((value, message)),
    )

    assert result["render_metadata"]["resumed_from_checkpoint"] is True
    assert result["render_metadata"]["one_beat_per_scene"] is True
    assert result['render_metadata']['creative_review']['status'] == 'pass_with_advisories'
    assert result['ready_to_publish'] is True
    assert result['quality_status'] == 'validated'
    assert result['review_status'] == 'pass_with_advisories'
    assert result['llm_review_certified'] is False
    assert result['component_manifest']['scenes'][0]['reviewed_revision'] is True
    assert result['component_manifest']['preview_review']['status'] == 'pass'
    assert Path(result["video_path"]).is_file()
    assert render_calls == [(1, "remotion")]
    assert progress[-1][0] == 100
    assert json.loads((checkpoints / "state.json").read_text(encoding="utf-8"))["stage"] == "complete"


def test_queue_retry_rejects_saved_scene_that_combines_two_narration_beats(tmp_path):
    package = tmp_path / "two-beat-video"
    checkpoints = package / "checkpoints"
    editorial = package / "editorial"
    checkpoints.mkdir(parents=True)
    editorial.mkdir(parents=True)
    (checkpoints / "state.json").write_text(json.dumps({
        "stage": "waiting_for_memory", "status": "retryable",
    }), encoding="utf-8")
    (checkpoints / "render_failure.json").write_text(json.dumps({
        "kind": "low_memory", "retryable": True,
    }), encoding="utf-8")
    (checkpoints / "pre_render.json").write_text(json.dumps({
        "backend": "remotion",
    }), encoding="utf-8")
    (checkpoints / "audio_plan.json").write_text(json.dumps({
        "duration": 2.0,
        "scene_texts": ["First beat.", "Second beat."],
        "scene_durations": [1.0, 1.0],
    }), encoding="utf-8")
    (editorial / "remotion_edit_plan.json").write_text(json.dumps({
        "fps": 30,
        "duration": 2.0,
        "scenes": [{
            "start": 0.0, "duration": 2.0,
            "narration": "First beat. Second beat.",
        }],
    }), encoding="utf-8")

    result = server_app._resume_checkpointed_remotion_render(
        {"_output_package_dir": str(package)}, lambda *_args: None,
    )

    assert result is None


def test_queue_retry_repairs_overused_saved_pixels_before_rendering(tmp_path):
    package = tmp_path / 'repeated-source'
    checkpoints = package / 'checkpoints'; checkpoints.mkdir(parents=True)
    paths = []
    for index in range(5):
        image = package / f'scene-{index}.jpg'
        image.write_bytes(b'repeated source pixels' if index % 2 == 0 else f'other {index}'.encode())
        paths.append(str(image))
    (checkpoints / 'state.json').write_text(json.dumps({'stage': 'ready_for_render', 'status': 'saved'}))
    (checkpoints / 'pre_render.json').write_text(json.dumps({'backend': 'remotion'}))
    (checkpoints / 'media_plan.json').write_text(json.dumps({'media_paths': paths,
        'metadata': {'media_resolution_v2': [{'provider': 'web'} for _ in paths]}}))
    assert server_app._resume_checkpointed_remotion_render(
        {'_output_package_dir': str(package)}, lambda *_: None) is None


def test_directed_media_layouts_receive_adjacent_variation_without_affecting_continuations():
    import copy
    base = {"duration": 4, "director_treatment_enabled": True,
            "text": {"frame_mode": "media_only", "overlay_policy": "none"},
            "primary_visual": {"type": "SmartImage", "variant": "full_bleed_cinematic", "props": {}},
            "graphic": {"template_id": "full-bleed", "props": {}}}
    manifest = {"scenes": [copy.deepcopy(base), copy.deepcopy(base)]}
    assert server_app._repair_repeated_adjacent_treatments(manifest) == [1]
    changed = manifest["scenes"][1]
    assert changed["primary_visual"]["type"] == "SmartImage"
    assert changed["primary_visual"]["variant"] != "full_bleed_cinematic"
    assert changed["graphic"]["props"]["variant"] == changed["primary_visual"]["variant"]
    continuous = {"scenes": [copy.deepcopy(base), copy.deepcopy(base)]}
    continuous["scenes"][1]["pacing"] = {"continuation": True}
    assert server_app._repair_repeated_adjacent_treatments(continuous) == []


def test_perceptual_repeat_recovery_promotes_only_hash_verified_accepted_local_alternate(tmp_path):
    import hashlib

    chosen_path = tmp_path / "repeated.jpg"
    alternate_path = tmp_path / "verified-trophy.jpg"
    chosen_bytes = b"repeated source frame"
    alternate_bytes = b"vision approved distinct trophy frame"
    chosen_path.write_bytes(chosen_bytes)
    alternate_path.write_bytes(alternate_bytes)
    alternate_hash = hashlib.sha256(alternate_bytes).hexdigest()
    manifest = {
        "scenes": [{
            "duration": 8.0,
            "media": {"src": str(chosen_path), "source": "web", "shots": [
                {"path": str(chosen_path), "candidate_id": "repeat-selected", "source": "web",
                 "verification": "verified", "asset_fingerprint": "repeat-fingerprint",
                 "shot_start": 0.0, "shot_end": 4.0},
                {"path": str(alternate_path), "asset_path": str(alternate_path),
                 "media_type": "image", "candidate_id": "local-reviewed-trophy", "source": "web",
                 "source_url": "https://example.test/trophy", "verification": "accepted",
                 "verification_scope": "actual_asset", "verified_asset_sha256": alternate_hash,
                 "asset_fingerprint": alternate_hash, "shot_start": 4.0, "shot_end": 8.0,
                 "visual_decision": {"accepted": True, "visual_checked": True,
                     "semantic_verified": True, "scene_match": True, "fatal_mismatch": False,
                     "entity_conflict": False, "unsafe_content": False, "final_score": 92.0,
                     "verified_asset_sha256": alternate_hash}},
            ]},
            "primary_visual": {"type": "SmartImage", "asset": {"src": str(chosen_path)}},
            "media_plan": {"visual_beats": 2},
        }],
        "media_shot_plan": {
            "shot_count": 2,
            "repeated_source_shot_count": 1,
            "scene_media_choices": [{"scene_index": 0, "selected_shots": 2}],
            "perceptual_repetition_audit": {"status": "needs_revision", "unresolved_repeats": [
                {"scene_index": 0, "shot_index": 0, "candidate_id": "repeat-selected",
                 "matches_scene_index": 3, "matches_candidate_id": "repeat-selected-elsewhere",
                 "reason": "no_distinct_verified_local_alternate"}
            ]},
        },
    }

    assert server_app._repair_verified_local_perceptual_repeats(manifest) == [0]
    scene = manifest["scenes"][0]
    assert len(scene["media"]["shots"]) == 1
    assert scene["media"]["shots"][0]["candidate_id"] == "local-reviewed-trophy"
    assert scene["media"]["shots"][0]["shot_start"] == 0.0
    assert scene["media"]["shots"][0]["shot_end"] == 8.0
    assert scene["media"]["src"] == str(alternate_path)
    assert scene["primary_visual"]["asset"]["src"] == str(alternate_path)
    assert manifest["media_shot_plan"]["perceptual_repetition_audit"]["status"] == "pass"
    assert manifest["media_shot_plan"]["perceptual_repetition_audit"]["resolved_repeats"][0]["resolution"] == "promoted_existing_verified_distinct_local_alternate"


def test_perceptual_repeat_recovery_does_not_use_unreviewed_or_modified_local_alternate(tmp_path):
    repeated = tmp_path / "repeat.jpg"
    alternate = tmp_path / "unverified.jpg"
    repeated.write_bytes(b"repeat")
    alternate.write_bytes(b"current pixels do not match review hash")
    manifest = {"scenes": [{"duration": 4, "media": {"shots": [
        {"path": str(repeated), "candidate_id": "repeat", "verification": "verified"},
        {"path": str(alternate), "candidate_id": "alternate", "verification": "accepted",
         "verified_asset_sha256": "0" * 64,
         "visual_decision": {"accepted": True, "visual_checked": True, "semantic_verified": True,
             "scene_match": True, "final_score": 99}},
    ]}}], "media_shot_plan": {"perceptual_repetition_audit": {"unresolved_repeats": [
        {"scene_index": 0, "shot_index": 0, "candidate_id": "repeat"}
    ]}}}
    assert server_app._repair_verified_local_perceptual_repeats(manifest) == []
    assert len(manifest["scenes"][0]["media"]["shots"]) == 2
    assert manifest["media_shot_plan"]["perceptual_repetition_audit"]["unresolved_repeats"]


def test_llm_diagram_labels_require_exact_narration_evidence():
    from services.editorial_director.director import validated_diagram_nodes
    narration = "Neil Armstrong and Buzz Aldrin descended in Eagle while Michael Collins orbited above."
    nodes = validated_diagram_nodes([
        {"label": "Eagle", "source_span": "Buzz Aldrin descended in Eagle"},
        {"label": "Michael Collins", "source_span": "Michael Collins orbited above"},
        {"label": "Mars", "source_span": "Michael Collins orbited above"},
        {"label": "Neil Armstrong", "source_span": "Neil Armstrong invented a new rocket"},
        {"label": "Eagle", "source_span": "Buzz Aldrin descended in Eagle"},
    ], narration)
    assert [node["label"] for node in nodes] == ["Eagle", "Michael Collins"]


def test_media_shot_clock_covers_every_frame_without_gaps_or_duplicate_boundaries():
    import subprocess
    import shutil
    node = shutil.which("node")
    assert node, "Node is a required Remotion runtime"
    source = Path(__file__).resolve().parents[1] / "src/remotion/engine/MediaShotTiming.js"
    script = """
const fs = require('fs');
const assert = require('assert');
(async () => {
 const code = fs.readFileSync(process.argv[1], 'utf8');
 const {normalizeMediaShotTiming} = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
 const shots = normalizeMediaShotTiming([
  {src:'a.jpg', shot_start:.02, shot_end:1},
  {src:'b.jpg', shot_start:1.02, shot_end:2},
  {src:'discarded.jpg', shot_start:2.03},
  {src:'c.jpg', shot_start:2.031, shot_end:2.9},
  {shot_start:2.5}
 ], 100, 30);
 assert.deepEqual(shots.map(s => s.shot.src), ['a.jpg','b.jpg','c.jpg']);
 for(let frame=0; frame<100; frame++) {
  assert.equal(shots.filter(s => frame>=s.from && frame<s.from+s.duration).length,1);
 }
 assert.deepEqual(normalizeMediaShotTiming([],100,30),[]);
})().catch(e => { console.error(e); process.exit(1); });
"""
    result = subprocess.run([node, "-e", script, str(source)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_media_cut_entrance_is_visible_at_the_first_frame():
    import subprocess
    import shutil
    source = Path(__file__).resolve().parents[1] / "src/remotion/engine/MotionEngine.js"
    script = """
const fs = require('fs');
const assert = require('assert');
const source = fs.readFileSync(process.argv[1], 'utf8').replace(/^import .*;$/gm,'').replace(/export function/g,'function');
const {computeEntranceStyle,calculateCameraTransform} = new Function(source+';return {computeEntranceStyle,calculateCameraTransform};')();
for(const entrance of ['none','cut','hard_cut']) assert.deepEqual(computeEntranceStyle(entrance,0,0),{opacity:1});
assert.equal(typeof calculateCameraTransform('none',0),'string');
assert.equal(computeEntranceStyle('fade',0,0).opacity,0);
"""
    result = subprocess.run([shutil.which("node"), "-e", script, str(source)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_photo_entrances_stay_visible_and_director_crop_beats_retrieval_hint():
    import subprocess
    import shutil
    motion = Path(__file__).resolve().parents[1] / "src/remotion/engine/MotionEngine.js"
    focal = Path(__file__).resolve().parents[1] / "src/remotion/engine/FocalFrame.js"
    script = """
const fs=require('fs'),assert=require('assert');
const motion=fs.readFileSync(process.argv[1],'utf8').replace(/^import .*;$/gm,'').replace(/export function/g,'function');
const {computeMediaEntranceStyle}=new Function(motion+';return {computeMediaEntranceStyle};')();
for(const name of ['fade','mask_reveal','split_wipe','none']) assert.equal(computeMediaEntranceStyle(name,0).opacity,1);
assert.match(computeMediaEntranceStyle('slide_left',0).transform,/translateX/);
const focal=fs.readFileSync(process.argv[2],'utf8');
(async()=>{
 const {resolveMediaFit}=await import('data:text/javascript;base64,'+Buffer.from(focal).toString('base64'));
 assert.equal(resolveMediaFit({fit:'contain',safe_framing:true,width:4096,height:3427},{fit:'force_cover'}),'cover');
 assert.equal(resolveMediaFit({fit:'contain',safe_framing:true,width:1000,height:800},{}),'contain');
})().catch(e=>{console.error(e);process.exit(1)});
"""
    result = subprocess.run([shutil.which("node"), "-e", script, str(motion), str(focal)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_slider_comparison_can_keep_its_sourced_subject_inside_the_reveal():
    source = (Path(__file__).resolve().parents[1] / "src/remotion/components/AdaptiveComparison.jsx").read_text(encoding="utf-8")
    assert "props.split_position" in source
    assert "props.right_focus_x" not in source
    assert "props.right_focal_target" not in source
    assert "left: `${split}%`" in source
    assert "props.preserve_label_case === true" in source
    assert "objectPosition: props.right_position" in source
    assert "left: `${split}%`" in source
    assert "const photoBottom = '15%'" in source
    assert "height: photoBottom" in source
    assert "bottom: photoBottom" in source
    assert "const labelInk = props.label_text_color || props.labelTextColor || '#F5F2EA'" in source
    assert "const dividerWidth = Math.max(4, Math.min(10" in source


def test_approved_editorial_type_honors_middle_vertical_positions():
    source = (Path(__file__).resolve().parents[1] / "src/remotion/components/EditorialType.jsx").read_text(encoding="utf-8")
    assert "const middle = String(position).includes('middle')" in source
    assert "top: '50%', transform: 'translateY(-50%)'" in source


def test_director_copy_reaches_stat_and_specialist_text_without_duplicate_layers():
    root = Path(__file__).resolve().parents[1]
    registry = (root / "src/remotion/engine/SceneRegistry.js").read_text(encoding="utf-8")
    composition = (root / "src/remotion/DocumentaryComposition.jsx").read_text(encoding="utf-8")
    stat = (root / "src/remotion/components/AdaptiveStat.jsx").read_text(encoding="utf-8")
    kinetic = (root / "src/remotion/components/KineticText.jsx").read_text(encoding="utf-8")
    assert "Component === StatCard ? { display_text: approvedCopyText" in registry
    assert "emphasis_spans: scene.editorial_contract.emphasis_spans || []" in registry
    assert "rangeOrScore" in stat and "position === 'center'" in stat
    assert "panel_style" in stat and "const noPanel" in stat
    assert "variant === 'split_counter_accent'" in kinetic
    assert "variant === 'word_by_word_punch'" in kinetic
    owner_list = composition.split('const approvedCopyPrimaryText =', 1)[1].split('].includes(primaryType)', 1)[0]
    assert all(name in owner_list for name in ("'KineticText'", "'ChapterTitle'", "'Headline'", "'StatCard'"))
    assert "'Chart'" not in owner_list and "'Timeline'" not in owner_list
    assert "const preserveSemanticCopy = hasGeneratedRecipe && ['PersonProfile', 'QuoteCard'].includes(primaryType);" in composition


def test_preview_watchdog_timeout_fails_open_only_for_non_strict_exports():
    source = (Path(__file__).resolve().parents[1] / "server/services/native_render_policy.py").read_text(encoding="utf-8")
    assert "preview_failure_reason = 'Remotion preview exceeded its bounded review budget'" in source
    assert "if _enabled(cfg.get('strict_creative_review', False))" in source
    assert "'llm_review_certified': False" in source
    assert "'fallback': 'direct_render'" in source


def test_signed_chart_domain_preserves_zero_and_negative_geometry():
    import subprocess
    import shutil
    source = Path(__file__).resolve().parents[1] / "src/remotion/engine/ChartGeometry.js"
    script = """
const fs=require('fs'); const assert=require('assert');
(async()=>{
 const code=fs.readFileSync(process.argv[1],'utf8');
 const {signedChartDomain}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
 const mixed=signedChartDomain([-3,0,9]);
 assert.equal(mixed.zero,.25); assert.equal(mixed.position(-3),0); assert.equal(mixed.position(9),1);
 assert.equal(mixed.position(0),mixed.zero);
 const negative=signedChartDomain([-9,-3]); assert.equal(negative.zero,1); assert.equal(negative.position(-9),0);
 const empty=signedChartDomain([0,0]); assert.equal(empty.position(0),0);
})().catch(e=>{console.error(e);process.exit(1);});
"""
    result=subprocess.run([shutil.which("node"),"-e",script,str(source)],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr

