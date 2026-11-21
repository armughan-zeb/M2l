"""Tests for scripts/resident-inference-worker/validate_answer.py.

The validator is the worker-side gate that must catch, before delivery,
exactly what the server would reject terminally in agent_bridge.py
(``_json_schema_error`` from openrouter_llm.py is the shared checker).

Regression: 2026-11-15, Apollo job 06f75ff4, direction request 3068b384
(scenes 24-28) was delivered with transition 'split_wipe' — an ENTRANCE
value, not in the transition enum — and the server rejected it, so those
5 scenes fell back to mechanical direction.
"""

import copy
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(__file__), "..", "..", "scripts", "resident-inference-worker"
    ),
)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import validate_answer  # noqa: E402
from server.services.openrouter_llm import (  # noqa: E402
    _normalise_strict_json_schema,
)
from server.services.editorial_director.director import (  # noqa: E402
    _director_design_contract_snapshot,
)
from server.services.vidzeo_brain import (  # noqa: E402
    SCENE_PLAN_SCHEMA,
    YT_WINNER_SCHEMA,
)

# Minimal schema reproducing the real direction failure: the transition
# enum (real values) and the entrance enum (real values) are DIFFERENT
# lists, and 'split_wipe' belongs to entrance only.
DIRECTION_SCHEMA = {
    "type": "object",
    "required": ["scenes"],
    "properties": {
        "scenes": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "required": ["scene_index", "entrance", "transition"],
                "properties": {
                    "scene_index": {"type": "integer"},
                    "entrance": {
                        "type": "string",
                        "enum": [
                            "blur_in", "cut", "fade", "mask_reveal",
                            "redaction_wipe", "scale_pop", "slide_left",
                            "slide_right", "slide_up", "split_wipe",
                            "spring_drop",
                        ],
                    },
                    "transition": {
                        "type": "string",
                        "enum": [
                            "cross_dissolve", "dip_black", "dip_white",
                            "directional_slide_left", "directional_slide_right",
                            "film_burn", "hard_cut", "light_leak", "paper_tear",
                            "wipe_left", "wipe_right", "zoom_cut",
                        ],
                    },
                },
            },
        }
    },
}


def _request_file(tmp_path, schema=None):
    request = {"id": "req1", "operation": "vidnets_scene_direction_v1"}
    if schema is not None:
        request["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "direction", "strict": True, "schema": schema},
        }
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    return str(path)


def _answer_file(tmp_path, content):
    path = tmp_path / "answer.json.tmp-worker-1-123"
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8")
    else:
        path.write_text(json.dumps(content), encoding="utf-8")
    return str(path)


def _direction(transition):
    return {
        "scenes": [
            {"scene_index": 24, "entrance": "split_wipe", "transition": transition}
        ]
    }


def test_rejects_entrance_value_in_transition(tmp_path):
    request = _request_file(tmp_path, DIRECTION_SCHEMA)
    answer = _answer_file(tmp_path, _direction("split_wipe"))
    ok, issue = validate_answer.validate(answer, request)
    assert ok is False
    assert "$.scenes[0].transition" in issue
    assert "outside the allowed choices" in issue


def test_accepts_fixed_transition(tmp_path):
    request = _request_file(tmp_path, DIRECTION_SCHEMA)
    answer = _answer_file(tmp_path, _direction("wipe_left"))
    ok, issue = validate_answer.validate(answer, request)
    assert ok is True
    assert issue == ""


def test_plain_text_without_schema_is_valid(tmp_path):
    request = _request_file(tmp_path)
    answer = _answer_file(tmp_path, "a plain text answer")
    ok, _ = validate_answer.validate(answer, request)
    assert ok is True


def test_malformed_json_with_schema_is_invalid(tmp_path):
    request = _request_file(tmp_path, DIRECTION_SCHEMA)
    answer = _answer_file(tmp_path, "{not json")
    ok, issue = validate_answer.validate(answer, request)
    assert ok is False
    assert "valid JSON" in issue


def test_missing_required_field_is_invalid(tmp_path):
    request = _request_file(tmp_path, DIRECTION_SCHEMA)
    answer = _answer_file(tmp_path, {"scenes": [{"scene_index": 24}]})
    ok, issue = validate_answer.validate(answer, request)
    assert ok is False
    assert "missing required fields" in issue


def test_cli_exit_codes(tmp_path):
    request = _request_file(tmp_path, DIRECTION_SCHEMA)
    helper = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "scripts",
        "resident-inference-worker",
        "validate_answer.py",
    )
    bad = _answer_file(tmp_path, _direction("split_wipe"))
    good = tmp_path / "good.json"
    good.write_text(json.dumps(_direction("wipe_left")), encoding="utf-8")
    assert (
        subprocess.run(
            [sys.executable, helper, bad, request],
            capture_output=True,
            text=True,
        ).returncode
        == 1
    )
    assert (
        subprocess.run(
            [sys.executable, helper, str(good), request],
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )


# --- Real-schema regression tests (Area 9, 2026-11-16) ---------------------
# The mini-schema above replays the 2026-11-15 split_wipe failure in
# isolation. These tests replay the same failure modes against the REAL
# schemas the server files (normalised strict form, exactly as
# bounded_http files them into the bridge), so a hole in any live enum or
# bound fails here first. The filed form is the normalised one: every
# property is required and optional enums admit null.
#
# P2 (2026-11-18, "fold art direction into the first direction batch"):
# the filed director schema now carries a top-level ``art_direction``
# property. The normaliser makes it required-but-nullable, exactly like
# every other optional field, and a real strict-mode answer always
# carries the key (batch 0 returns a full art object; later batches may
# return null). The fixtures below therefore carry a valid art object,
# so a rejection here is always about the field under test, never about
# the missing art key.

_REAL_ART_DIRECTION = {
    "style": "editorial",
    "display_font": "Archivo Black",
    "body_font": "Inter",
    "accent_color": "#F2C14E",
    "visual_motif": "maps and evidence traces",
    "motion_rhythm": "trace, reveal, settle",
    "story_arc": "A question leads through evidence to the payoff.",
    "retention_plan": "Hook with the question, breathe at the consequence.",
}


def _real_director_answer(scenes):
    """A real filed director answer: scenes plus the P2 art_direction key."""
    return {"scenes": scenes, "art_direction": dict(_REAL_ART_DIRECTION)}


def _filed_schema(schema):
    """Return the schema exactly as it is filed in a bridge request."""
    return _normalise_strict_json_schema(copy.deepcopy(schema))


def _request_file_with_schema(tmp_path, schema):
    request = {
        "id": "req-real",
        "operation": "vidnets_scene_direction_v1",
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "filed", "strict": True, "schema": _filed_schema(schema)},
        },
    }
    path = tmp_path / "request-real.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    return str(path)


def _real_director_scene(**overrides):
    scene = {
        "scene_index": 24, "type": "ChapterTitle", "variant": "v",
        "visual_intent": "x", "frame_mode": "media_only",
        "visual_role": "media_overlay", "overlay_policy": "none",
        "editorial_priority": 0.5, "camera": "static", "entrance": "fade",
        "transition": "hard_cut", "text_focus": "t", "display_text": "d",
        "source_span": "s", "emphasis_spans": [{"start": 0, "end": 1}],
        "placement": "center",
        "text_treatment": {"panel_style": "hero_type", "text_color": "#FFFFFF",
                           "accent_color": "#000000", "panel_background": "#111111"},
        "graphic_theme": "cinematic_dark",
        "template_id": "generated-recipe", "template_recipe": None,
        "map_shot": {"enabled": False, "location_names": [], "feature_ids": [],
                     "route_id": "", "source_ids": [], "motion_profile": "auto",
                     "render_style": "globe_3d"},
        "media_plan": {"visual_beats": 1, "media_preference": "clip",
                       "rhythm": "single_hold"},
        "diagram_nodes": [], "diagram_links": [], "motion_cues": [],
        # P1 (2026-11-18): editorial beats folded into the director turn.
        # Optional in the raw schema, so the filed strict form requires
        # them as nullable keys — a real strict-mode answer carries them
        # as null when the model nominates no beat for the scene.
        "editorial_role": None, "editorial_treatment": None,
        "editorial_emphasis": None, "editorial_highlight_kind": None,
        "editorial_importance": None,
    }
    scene.update(overrides)
    return scene


def _real_scene_plan(**overrides):
    plan = {
        "scene_number": 1, "container_subject": "c", "scene_subject": "s",
        "visual_intent": "v", "visual_subject": "vs",
        "visual_subject_type": "person", "subject_evidence": "e",
        "visible_target": "t", "identity_scope": "subject",
        "domain_category": "documentary", "scene_subject_type": "person",
        "retrieval_positive_terms": ["c", "s", "v"],
        "retrieval_negative_terms": [],
        "youtube_query_intents": ["footage"],
        "youtube": {"primary": "q", "supporting": "q", "fallback": "q"},
        "web": {"primary": "q", "supporting": "q", "fallback": "q"},
        "stock": {"primary_query": "q", "alt_query": "q", "fallback_query": "q"},
    }
    plan.update(overrides)
    return plan


def _plan_schema():
    return {
        "type": "object", "additionalProperties": False,
        "properties": {"scenes": {"type": "array", "minItems": 1, "items": SCENE_PLAN_SCHEMA}},
        "required": ["scenes"],
    }


def test_real_director_schema_rejects_split_wipe_transition(tmp_path):
    # The exact 2026-11-15 Apollo failure, replayed against the real filed
    # director schema instead of the mini-schema above.
    schema = _director_design_contract_snapshot()["schema"]
    request = _request_file_with_schema(tmp_path, schema)
    answer = _answer_file(tmp_path, _real_director_answer([_real_director_scene(transition="split_wipe")]))
    ok, issue = validate_answer.validate(answer, request)
    assert ok is False
    assert "$.scenes[0].transition" in issue
    assert "outside the allowed choices" in issue


def test_real_director_schema_accepts_valid_scene(tmp_path):
    schema = _director_design_contract_snapshot()["schema"]
    request = _request_file_with_schema(tmp_path, schema)
    answer = _answer_file(tmp_path, _real_director_answer([_real_director_scene()]))
    ok, issue = validate_answer.validate(answer, request)
    assert ok is True, issue
    assert issue == ""


def test_real_director_schema_rejects_empty_scenes(tmp_path):
    schema = _director_design_contract_snapshot()["schema"]
    request = _request_file_with_schema(tmp_path, schema)
    answer = _answer_file(tmp_path, _real_director_answer([]))
    ok, issue = validate_answer.validate(answer, request)
    assert ok is False
    assert "$.scenes" in issue


@pytest.mark.parametrize("field,bad", [
    ("entrance", "wipe_left"),   # transition value in an entrance field
    ("camera", "fade"),          # entrance value in a camera field
    ("frame_mode", "bogus"),
    ("visual_role", "bogus"),
    ("placement", "bogus"),
    ("graphic_theme", "bogus"),
])
def test_real_director_schema_rejects_cross_enum_values(tmp_path, field, bad):
    schema = _director_design_contract_snapshot()["schema"]
    request = _request_file_with_schema(tmp_path, schema)
    answer = _answer_file(tmp_path, _real_director_answer([_real_director_scene(**{field: bad})]))
    ok, issue = validate_answer.validate(answer, request)
    assert ok is False
    assert f"$.scenes[0].{field}" in issue


def test_real_yt_winner_schema_bounds(tmp_path):
    request = _request_file_with_schema(tmp_path, YT_WINNER_SCHEMA)
    base = {"winner_index": 0, "confidence": 0.9, "reasoning": "r", "scores": []}
    ok, issue = validate_answer.validate(_answer_file(tmp_path, base), request)
    assert ok is True, issue
    # Out-of-range values used to slip through unvalidated; the consumer
    # silently clamped a bad index to candidate 0. (A too-large index
    # still cannot be bounded in the schema — the candidate count varies
    # per request — so the consumer clamp remains the guard for that case.)
    for bad in (
        {"winner_index": -1},
        {"confidence": 150}, {"confidence": -5},
        {"scores": [{"index": 0, "relevance": 99, "footage_signal": 1,
                     "visual_match": 1, "total": 1}]},
        {"scores": [{"index": 0, "relevance": 1, "footage_signal": 1,
                     "visual_match": 1, "total": -1}]},
    ):
        answer = _answer_file(tmp_path, {**base, **bad})
        ok, issue = validate_answer.validate(answer, request)
        assert ok is False, bad
        assert "$.winner_index" in issue or "$.confidence" in issue or "$.scores[0]" in issue


def test_real_scene_plan_schema_rejects_invented_subject_type(tmp_path):
    # visual_subject_type had no enum even though the system prompt names
    # 15 allowed values; an invented type flowed into visual_requirement.
    request = _request_file_with_schema(tmp_path, _plan_schema())
    answer = _answer_file(tmp_path, {"scenes": [_real_scene_plan(visual_subject_type="spaceship")]})
    ok, issue = validate_answer.validate(answer, request)
    assert ok is False
    assert "$.scenes[0].visual_subject_type" in issue
    good = _answer_file(tmp_path, {"scenes": [_real_scene_plan(visual_subject_type="rail_crew")]})
    ok, issue = validate_answer.validate(good, request)
    assert ok is True, issue


def test_real_scene_plan_schema_rejects_empty_subjects_and_scenes(tmp_path):
    request = _request_file_with_schema(tmp_path, _plan_schema())
    for bad in (
        {"scenes": []},
        {"scenes": [_real_scene_plan(container_subject="")]},
        {"scenes": [_real_scene_plan(scene_number=0)]},
        {"scenes": [_real_scene_plan(identity_scope="bogus")]},
    ):
        answer = _answer_file(tmp_path, bad)
        ok, issue = validate_answer.validate(answer, request)
        assert ok is False, bad
