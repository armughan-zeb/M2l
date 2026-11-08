import json
import os
import sys
import pytest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from config import normalize_generation_config  # noqa: E402
from services.openrouter_llm import (  # noqa: E402
    DEFAULT_SCENE_BRAIN_MODEL,
    OpenRouterLLM,
    _json_schema_error,
    _normalise_strict_json_schema,
)


@pytest.fixture(autouse=True)
def offline_export_allowance(monkeypatch):
    # These tests exercise planning/media failures, not the separate cloud quota.
    monkeypatch.setattr('services.export_entitlements.reserve_generation_export', lambda *args, **kwargs: None)


def _response(status=200, content="{\"ok\": true}"):
    response = Mock(status_code=status)
    response.raise_for_status.return_value = None
    response.text = "provider response"
    response.json.return_value = {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
    }
    return response


@patch("services.openrouter_llm.requests.post")
def test_first_pass_recipe_is_deferred_without_losing_director_decisions(post):
    schema = {'type': 'object', 'required': ['scenes'], 'properties': {'scenes': {
        'type': 'array', 'items': {'type': 'object', 'required': ['scene_index', 'template_recipe', 'visual_intent'],
        'properties': {'scene_index': {'type': 'integer'}, 'template_recipe': {'type': ['object', 'null'],
                      'properties': {'data': {'type': 'array'}}},
                      'visual_intent': {'type': 'string', 'maxLength': 40}}}}}}
    post.return_value = _response(content=json.dumps({'scenes': [{'scene_index': 6,
        'template_recipe': {'data': {'incorrect': 'shape'}},
        'visual_intent': 'Explain the three systems with purposeful grounded actor labels and relationship animation.'}]}))
    client = OpenRouterLLM('test-key', 'qwen/qwen3.5-flash-02-23', fallback_models=[])
    result = json.loads(client.chat('First pass', json_mode=True, json_schema=schema,
                                   json_schema_name='vidnets_scene_direction_v1', max_attempts=1))
    assert result['scenes'][0]['template_recipe'] is None
    assert result['scenes'][0]['scene_index'] == 6
    assert len(result['scenes'][0]['visual_intent']) <= 40
    assert client.attempt_history[-1]['deferred_composition_scene_indices'] == [6]
    assert post.call_count == 1


@patch("services.openrouter_llm.requests.post")
def test_optional_motion_aliases_preserve_direction_without_paid_retry(post):
    schema = {'type': 'object', 'properties': {'scenes': {'type': 'array', 'items': {
        'type': 'object', 'properties': {'scene_index': {'type': 'integer'},
        'motion_cues': {'type': 'array', 'maxItems': 3, 'items': {'type': 'object',
        'properties': {'action': {'type': 'string', 'enum': ['trace', 'pan', 'wipe', 'reveal']},
                       'element': {'type': 'string', 'enum': ['media', 'text']}}}}}}}}}
    post.return_value = _response(content=json.dumps({'scenes': [{'scene_index': 2, 'motion_cues': [
        {'action': 'zoom', 'element': 'media'}, {'action': 'fade', 'element': 'text'},
        {'action': 'nonsense', 'element': 'text'}, {'action': 'trace', 'element': 'unapproved'},
    ]}]}))
    client = OpenRouterLLM('test-key', 'qwen/qwen3.5-flash-02-23', fallback_models=[])
    result = json.loads(client.chat('Direct scenes', json_mode=True, json_schema=schema,
                                   json_schema_name='vidnets_scene_direction_v1', max_attempts=1))
    assert result['scenes'][0]['motion_cues'] == [
        {'action': 'pan', 'element': 'media'}, {'action': 'reveal', 'element': 'text'}]
    assert client.attempt_history[-1]['normalized_motion_scene_indices'] == [2]
    assert post.call_count == 1


@patch('services.openrouter_llm.requests.post')
def test_optional_motion_fields_do_not_discard_factual_direction(post):
    schema = {'type': 'object', 'properties': {'scenes': {'type': 'array', 'items': {
        'type': 'object', 'properties': {'scene_index': {'type': 'integer'},
            'display_text': {'type': 'string'},
            'entrance': {'type': 'string', 'enum': ['fade', 'slide_up']},
            'transition': {'type': 'string', 'enum': ['cut', 'dissolve']},
            'camera': {'type': 'string', 'enum': ['static', 'slow_push']}}}}}}
    post.return_value = _response(content=json.dumps({'scenes': [{'scene_index': 3,
        'display_text': 'Powerwall', 'entrance': 'dramatic_reveal',
        'transition': 'crossfade', 'camera': 'zoom in'}]}))
    client = OpenRouterLLM('test-key', 'qwen/qwen3.5-flash-02-23', fallback_models=[])
    result = json.loads(client.chat('Direct', json_mode=True, json_schema=schema,
        json_schema_name='vidnets_scene_direction_v1', max_attempts=1))['scenes'][0]
    assert result['display_text'] == 'Powerwall'
    assert (result['entrance'], result['transition'], result['camera']) == ('fade', 'dissolve', 'slow_push')
    assert len(client.attempt_history[-1]['normalized_motion_fields']) == 3
    assert post.call_count == 1


@patch('services.openrouter_llm.requests.post')
def test_composition_motion_vocabulary_is_repaired_without_changing_geometry(post):
    schema = {'type': 'object', 'properties': {'objects': {'type': 'array', 'items': {
        'type': 'object', 'properties': {'actor_label': {'type': 'string'},
            'x': {'type': 'number'}, 'motion': {'type': 'string', 'enum': ['fade', 'draw', 'scale', 'wipe', 'rise']}}}}}}
    post.return_value = _response(content=json.dumps({'objects': [
        {'actor_label': 'Megapack', 'x': 24.5, 'motion': 'slide up'}]}))
    client = OpenRouterLLM('test-key', 'qwen/qwen3.5-flash-02-23', fallback_models=[])
    result = json.loads(client.chat('Design', json_mode=True, json_schema=schema,
        json_schema_name='vidnets_scene_composition_v1', max_attempts=1))
    assert result['objects'] == [{'actor_label': 'Megapack', 'x': 24.5, 'motion': 'rise'}]
    assert post.call_count == 1


@patch("services.openrouter_llm.requests.post")
def test_creative_primary_share_keeps_a_real_fallback_tail(post, monkeypatch):
    import requests
    import services.openrouter_llm as module
    now = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(module.time, "sleep", lambda duration: None)
    calls = []
    def respond(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            now[0] += kwargs["timeout"]
            raise requests.exceptions.Timeout("slow creative primary")
        return _response()
    post.side_effect = respond
    client = OpenRouterLLM("test-key", "openai/gpt-6-luna", fallback_models=["openai/gpt-5.6-luna", "deepseek/deepseek-v4.1-flash", "qwen/qwen3.5-flash-02-23"])
    assert client.chat("Return JSON", json_mode=True, max_attempts=1, total_timeout_seconds=55, primary_timeout_share=.65) == '{"ok": true}'
    assert 35 < calls[0]["timeout"] <= 35.75
    assert 0 < calls[1]["timeout"] < 20
    assert now[0] < 55


def test_scene_brain_defaults_to_nemotron_free_without_premium_fallbacks(monkeypatch):
    monkeypatch.delenv("VIDZEO_SCENE_BRAIN_MODEL", raising=False)
    monkeypatch.delenv("VIDZEO_SCENE_BRAIN_FALLBACK_MODELS", raising=False)
    monkeypatch.delenv("VIDZEO_SCENE_BRAIN_FALLBACK_CHAIN", raising=False)
    config = normalize_generation_config({})
    assert config["scene_brain_model"] == "nvidia/nemotron-3.5-super-vl:free"
    assert config["scene_brain_fallback_models"] == [
        "nvidia/nemotron-3.5-super-vl:free",
    ]
    assert config["scene_brain_fallback_chain"] == [
        "nvidia/nemotron-3.5-super-vl:free",
    ]


def test_qwen_choice_is_preserved_and_never_migrated_to_gpt(monkeypatch):
    monkeypatch.delenv("VIDZEO_SCENE_BRAIN_MODEL", raising=False)
    config = normalize_generation_config({
        "scene_brain_model": "qwen/qwen3.5-flash-02-23",
        "scene_brain_fallback_chain": [
            "qwen/qwen3.5-flash-02-23",
            "google/gemma-4-31b-it:free",
            "nex-agi/nex-n2.5-mini:free",
            "openrouter/free",
        ],
    })
    assert config["scene_brain_model"] == "qwen/qwen3.5-flash-02-23"
    assert config["scene_brain_fallback_chain"] == [
        "qwen/qwen3.5-flash-02-23",
    ]


@patch("services.openrouter_llm.requests.post")
def test_openrouter_sends_ordered_model_fallback_array(post):
    post.return_value = _response()
    client = OpenRouterLLM(
        "test-key",
        "openai/gpt-6-luna",
        fallback_models=[
            "openai/gpt-5.6-luna",
            "deepseek/deepseek-v4.1-flash",
            "qwen/qwen3.5-flash-02-23",
        ],
    )
    assert client.chat("Return JSON", json_mode=True) == '{"ok": true}'
    payload = post.call_args.kwargs["json"]
    assert payload["model"] == "openai/gpt-6-luna"
    assert payload["models"] == [
        "openai/gpt-5.6-luna",
        "deepseek/deepseek-v4.1-flash",
        "qwen/qwen3.5-flash-02-23",
    ]


@patch("services.openrouter_llm.requests.post")
def test_openrouter_makes_optional_fields_nullable_in_strict_schema(post):
    post.return_value = _response(content='{"scene":{"subject":"Osaka","note":null}}')
    schema = {
        "type": "object",
        "properties": {
            "scene": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["subject"],
            },
        },
        "required": ["scene"],
    }
    client = OpenRouterLLM("test-key", DEFAULT_SCENE_BRAIN_MODEL, fallback_models=[])

    assert client.chat("Return the scene", json_mode=True, json_schema=schema, max_attempts=1) == (
        '{"scene":{"subject":"Osaka","note":null}}'
    )
    sent = post.call_args.kwargs["json"]["response_format"]["json_schema"]["schema"]
    assert sent["additionalProperties"] is False
    assert sent["properties"]["scene"]["additionalProperties"] is False
    assert sent["properties"]["scene"]["required"] == ["subject", "note"]
    assert sent["properties"]["scene"]["properties"]["note"]["type"] == ["string", "null"]
    assert "additionalProperties" not in schema
    assert _normalise_strict_json_schema(schema) == sent


def test_scene_query_director_uses_compact_valid_schema(monkeypatch):
    from services.vidzeo_brain import SCENE_PLAN_SCHEMA, VidzeoBrain

    scene = {
        "scene_number": 1,
        "container_subject": "Panama Canal",
        "scene_subject": "Panama Canal locks",
        "visual_intent": "Cargo ship entering a canal lock",
        "visual_subject": "Panama Canal",
        "visual_subject_type": "place",
        "subject_evidence": "A cargo ship enters a Panama Canal lock.",
        "visible_target": "Cargo ship entering a canal lock",
        "identity_scope": "subject",
        "youtube": {
            "primary": "Panama Canal cargo ship entering lock raw footage",
            "supporting": "Panama Canal ship lock transit footage",
            "fallback": "Panama Canal locks moving ship footage",
        },
        "web": {
            "primary": "Panama Canal cargo ship entering lock photo",
            "supporting": "Panama Canal lock gate ship photograph",
            "fallback": "Panama Canal locks aerial photo",
        },
        "stock": {
            "primary_query": "cargo ship entering canal lock",
            "alt_query": "ship canal lock aerial",
            "fallback_query": "cargo vessel waterway",
        },
    }
    assert SCENE_PLAN_SCHEMA["additionalProperties"] is False
    assert set(SCENE_PLAN_SCHEMA["required"]) == set(SCENE_PLAN_SCHEMA["properties"])
    brain = VidzeoBrain("test-key", fallback_models=[])
    with patch.object(brain._client, "chat", return_value=json.dumps({"scenes": [scene]})) as chat:
        rows = brain.plan_scenes(
            topic="Panama Canal",
            script="A cargo ship enters a Panama Canal lock.",
            scene_texts=["A cargo ship enters a Panama Canal lock."],
        )
    assert rows == [scene]
    assert chat.call_args.kwargs["json_schema_name"] == "vidzeo_scene_queries_v3"
    assert chat.call_args.kwargs["json_schema"]["additionalProperties"] is False

    invalid_scene = {**scene, "youtube": {**scene["youtube"], "primary": ""}}
    with patch.object(brain._client, "chat", return_value=json.dumps({"scenes": [invalid_scene]})):
        repaired = brain.plan_scenes(
            topic="Panama Canal",
            script="A cargo ship slowly enters a Panama Canal lock.",
            scene_texts=["A cargo ship slowly enters a Panama Canal lock."],
        )
    assert repaired[0]['youtube']['primary'] == scene['youtube']['supporting']
    assert repaired[0]['youtube']['primary'] != scene['scene_subject']
    invalid_scene = {**scene, 'youtube': {'primary':'', 'supporting':'', 'fallback':''}}
    with patch.object(brain._client, 'chat', return_value=json.dumps({'scenes':[invalid_scene]})):
        assert brain.plan_scenes(topic='Panama Canal', script='A ship enters a lock.',
            scene_texts=['A ship enters a lock.']) is None


def test_caption_progress_cannot_mask_openrouter_planning():
    from app import _make_progress_reporter

    events = []
    report = _make_progress_reporter(lambda fraction, message: events.append((fraction, message)))
    report(0.20, "Generating captions from Whisper transcript...")
    report(0.24, "OpenRouter query planning: directing 27 scenes")

    assert events == [
        (0.20, "Generating captions"),
        (0.24, "Planning visual searches"),
    ]


@patch("services.openrouter_llm.requests.post")
def test_openrouter_retries_without_response_format_on_provider_400(post):
    rejected = Mock(status_code=400)
    rejected.text = "response_format unsupported"
    accepted = _response()
    post.side_effect = [rejected, accepted]
    client = OpenRouterLLM("test-key", DEFAULT_SCENE_BRAIN_MODEL, fallback_models=[])
    assert client.chat("Return JSON", json_mode=True, max_attempts=2) == '{"ok": true}'
    assert post.call_count == 2
    assert "response_format" not in post.call_args_list[1].kwargs["json"]


def test_openrouter_json_extractor_handles_multiple_objects_with_prose():
    client = OpenRouterLLM("test-key", DEFAULT_SCENE_BRAIN_MODEL)
    raw = "Answer:\n{\"beats\": [{\"scene_index\": 0}]}\nExample: {\"ignored\": true}"
    extracted = client._extract_json_text(raw)
    assert extracted == '{"beats": [{"scene_index": 0}]}'


@patch("services.openrouter_llm.requests.post")
def test_openrouter_strict_schema_payload_fails_over_and_validates_locally(post):
    post.side_effect = [
        _response(content='{"ok": "yes"}'),
        _response(content='{"ok": true}'),
    ]
    chain = [
        "openai/gpt-6-luna",
        "openai/gpt-5.6-luna",
        "deepseek/deepseek-v4.1-flash",
        "qwen/qwen3.5-flash-02-23",
    ]
    client = OpenRouterLLM("test-key", chain[0], fallback_models=chain[1:])
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["ok"],
        "properties": {"ok": {"type": "boolean"}},
    }

    assert client.chat(
        "Return a valid response",
        json_mode=True,
        json_schema=schema,
        json_schema_name="director_smoke_v1",
        max_attempts=1,
        total_timeout_seconds=8,
    ) == '{"ok": true}'
    first_payload = post.call_args_list[0].kwargs["json"]
    second_payload = post.call_args_list[1].kwargs["json"]
    assert first_payload["model"] == chain[0]
    assert first_payload["models"] == chain[1:]
    assert first_payload["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "director_smoke_v1", "strict": True, "schema": schema},
    }
    assert first_payload["provider"]["require_parameters"] is True
    assert second_payload["model"] == chain[1]
    assert second_payload["models"] == chain[2:]


@patch("services.openrouter_llm.requests.post")
def test_openrouter_timeout_budget_leaves_time_for_each_director_fallback(post):
    import requests

    chain = [
        "openai/gpt-6-luna",
        "openai/gpt-5.6-luna",
        "deepseek/deepseek-v4.1-flash",
        "qwen/qwen3.5-flash-02-23",
    ]
    post.side_effect = [requests.Timeout("slow model") for _ in chain[:-1]] + [_response()]
    client = OpenRouterLLM("test-key", chain[0], fallback_models=chain[1:])

    assert client.chat(
        "Return a valid response",
        json_mode=True,
        json_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
        max_attempts=1,
        total_timeout_seconds=8,
    ) == '{"ok": true}'

    assert post.call_count == 4
    assert [call.kwargs["json"]["model"] for call in post.call_args_list] == chain
    assert post.call_args_list[0].kwargs["timeout"] <= 2.01
    assert all(call.kwargs["timeout"] <= 8 for call in post.call_args_list)


def test_openrouter_retry_backoff_stays_inside_the_total_timeout(monkeypatch):
    import requests
    from services import openrouter_llm

    clock = {"now": 0.0}
    slept = []

    def advance_clock(seconds):
        slept.append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(openrouter_llm.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(openrouter_llm.time, "sleep", advance_clock)
    with patch("services.openrouter_llm.requests.post", side_effect=requests.Timeout("slow")) as post:
        client = OpenRouterLLM("test-key", DEFAULT_SCENE_BRAIN_MODEL, fallback_models=[])
        try:
            client.chat("Return JSON", json_mode=True, max_attempts=2, total_timeout_seconds=1)
        except ValueError as exc:
            assert "total timeout exceeded (1.0s)" in str(exc)
        else:
            raise AssertionError("The retry loop should stop when its total budget expires")

    assert post.call_count == 1
    assert slept == [1.0]
    assert clock["now"] == 1.0


def test_openrouter_default_budget_expiry_is_reported_without_type_error(monkeypatch):
    import requests
    from services import openrouter_llm

    clock = {"now": 0.0}

    def timed_out_request(*args, **kwargs):
        clock["now"] = 601.0
        raise requests.Timeout("slow provider")

    monkeypatch.setattr(openrouter_llm.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(openrouter_llm.time, "sleep", lambda _seconds: None)
    monkeypatch.setenv("VIDZEO_OPENROUTER_TIMEOUT_SECONDS", "240")
    monkeypatch.setenv("VIDZEO_OPENROUTER_TOTAL_TIMEOUT_SECONDS", "600")
    with patch("services.openrouter_llm.requests.post", side_effect=timed_out_request):
        client = OpenRouterLLM("test-key", DEFAULT_SCENE_BRAIN_MODEL, fallback_models=[])
        try:
            client.chat("Return JSON", json_mode=True, max_attempts=2)
        except ValueError as exc:
            assert "total timeout exceeded (600.0s)" in str(exc)
        else:
            raise AssertionError("The default request budget should produce a retryable ValueError")


@patch("services.openrouter_llm.requests.post")
def test_openrouter_strict_schema_respects_director_token_cap(post):
    post.return_value = _response()
    client = OpenRouterLLM("test-key", "openai/gpt-6-luna", fallback_models=[])
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}

    client.chat(
        "Return a compact object",
        json_mode=True,
        json_schema=schema,
        max_tokens=700,
        max_attempts=1,
    )

    assert post.call_args.kwargs["json"]["max_tokens"] == 700


def test_documentary_director_uses_its_ordered_fallback_chain(monkeypatch):
    from services.editorial_director.director import (
        DIRECTOR_DEFAULT_MODEL,
        _director_fallback_models,
    )

    monkeypatch.delenv("VIDZEO_EDITORIAL_DIRECTOR_FALLBACK_MODELS", raising=False)
    expected = [
        "qwen/qwen3.5-flash-02-23",
    ]
    assert [DIRECTOR_DEFAULT_MODEL, *_director_fallback_models()] == expected

    monkeypatch.setenv("VIDZEO_EDITORIAL_DIRECTOR_FALLBACK_MODELS", "custom/first,custom/second")
    assert _director_fallback_models() == ("custom/first", "custom/second")


def test_legacy_query_model_setting_remains_separate_from_scene_director(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    from app import _resolve_query_llm_settings

    provider, key, model = _resolve_query_llm_settings({
        "scene_brain_model": "nvidia/nemotron-3.5-super-vl:free",
        "query_llm_model": "qwen/qwen3.5-flash-02-23",
        "llm_model": "qwen/qwen3.5-flash-02-23",
    })

    assert (provider, key, model) == (
        "openrouter",
        "test-openrouter-key",
        "qwen/qwen3.5-flash-02-23",
    )


def test_shared_scene_plan_uses_director_and_never_calls_container_fallback(monkeypatch):
    import app as app_module
    from services import vidzeo_brain

    route_chain = [
        "openai/gpt-6-luna",
        "openai/gpt-5.6-luna",
        "deepseek/deepseek-v4.1-flash",
        "qwen/qwen3.5-flash-02-23",
    ]

    class FakeClient:
        model_chain = tuple(route_chain)
        last_model = route_chain[0]

    class FakeBrain:
        def __init__(self, api_key, model, fallback_models):
            assert model == route_chain[0]
            assert fallback_models == route_chain
            self._client = FakeClient()
            self._last_scene_model_routes = [{
                "status": "directed",
                "selected_model": route_chain[0],
                "configured_chain": route_chain,
                "attempted_models": [route_chain[0]],
                "attempts": [{"model": route_chain[0], "resolved_model": route_chain[0]}],
            }]

        def plan_containers(self, *_args, **_kwargs):
            raise AssertionError("the old mechanical-prone container stage must not run")

        def plan_scenes(self, **kwargs):
            assert kwargs["scene_texts"] == ["Beat one.", "Beat two."]
            return [{"scene_number": 1}, {"scene_number": 2}]

    monkeypatch.setattr(app_module, "_load_shared_plan_cache", lambda *_args: None)
    monkeypatch.setattr(app_module, "_store_shared_plan_cache", lambda *_args: None)
    monkeypatch.setattr(app_module, "_resolve_query_llm_settings", lambda _config: ("openrouter", "test-key", "qwen/legacy"))
    monkeypatch.setattr(app_module, "_scene_brain_section_subjects", lambda *_args: (["", ""], [{}, {}], False))
    monkeypatch.setattr(app_module, "_adapt_scene_brain_plans", lambda *_args: {
        source: [{"_llm": True, "search_query": f"directed {source} {i}"} for i in range(2)]
        for source in ("web", "yt", "stock")
    })
    monkeypatch.setattr(vidzeo_brain, "VidzeoBrain", FakeBrain)

    result = app_module._generate_shared_scene_brain_bundle(
        "Beat one. Beat two.",
        "A two beat test",
        ["Beat one.", "Beat two."],
        {"scene_brain_model": route_chain[0], "scene_brain_fallback_chain": route_chain},
    )

    assert result["_planner"]["status"] == "directed"
    assert result["_planner"]["configured_chain"] == route_chain
    assert result["_planner"]["directed_scenes"] == 2


def test_incomplete_openrouter_scene_plan_fails_instead_of_mechanical_rows(monkeypatch):
    import app as app_module
    from services import vidzeo_brain
    from app import OpenRouterScenePlanningError

    class FakeClient:
        model_chain = ("openai/gpt-6-luna",)
        last_model = "openai/gpt-6-luna"

    class FakeBrain:
        def __init__(self, **_kwargs):
            self._client = FakeClient()
            self._last_scene_model_routes = []

        def plan_scenes(self, **_kwargs):
            return [{"scene_number": 1}]

    monkeypatch.setattr(app_module, "_load_shared_plan_cache", lambda *_args: None)
    monkeypatch.setattr(app_module, "_resolve_query_llm_settings", lambda _config: ("openrouter", "test-key", "openai/gpt-6-luna"))
    monkeypatch.setattr(app_module, "_scene_brain_section_subjects", lambda *_args: (["", ""], [{}, {}], False))
    monkeypatch.setattr(vidzeo_brain, "VidzeoBrain", FakeBrain)

    try:
        app_module._generate_shared_scene_brain_bundle(
            "Beat one. Beat two.",
            "A two beat test",
            ["Beat one.", "Beat two."],
            {},
        )
    except OpenRouterScenePlanningError as exc:
        assert "complete scene plan" in str(exc)
    else:
        raise AssertionError("An incomplete LLM response must not be replaced with mechanical rows")


def test_local_strict_schema_validator_checks_patterns():
    assert _json_schema_error("#abc", {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"})
    assert not _json_schema_error("#aabbcc", {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"})


def test_check_openrouter_key_status_missing():
    from services.openrouter_llm import check_openrouter_key_status
    res = check_openrouter_key_status("")
    assert res["ok"] is False
    assert res["status"] == "missing_key"


@patch("services.openrouter_llm.requests.get")
def test_check_openrouter_key_status_valid(mock_get):
    key_resp = Mock(status_code=200)
    key_resp.json.return_value = {
        "data": {
            "label": "my-key",
            "limit": 10.0,
            "usage": 2.5,
            "limit_remaining": 7.5,
        }
    }
    mock_get.return_value = key_resp
    from services.openrouter_llm import check_openrouter_key_status
    res = check_openrouter_key_status("sk-or-v1-testkey1234567890")
    assert res["ok"] is True
    assert res["status"] == "ready"
    assert res["limit_remaining"] == 7.5
    assert res["label"] == "my-key"


@patch("services.openrouter_llm.requests.get")
def test_check_openrouter_key_status_invalid_401(mock_get):
    key_resp = Mock(status_code=401)
    mock_get.return_value = key_resp
    from services.openrouter_llm import check_openrouter_key_status
    res = check_openrouter_key_status("sk-or-v1-badkey")
    assert res["ok"] is False
    assert res["status"] == "invalid_key"


def test_api_openrouter_status_endpoint():
    from app import app
    client = app.test_client()
    res = client.post("/api/openrouter/status", json={"api_key": ""})
    assert res.status_code == 200
    data = res.get_json()
    assert "ok" in data
    assert "status" in data


def test_api_generate_reports_retryable_openrouter_failure_with_checkpoint(tmp_path, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, '_get_authenticated_state', lambda: ('test-token', {'access_state': 'active'}))
    monkeypatch.setattr(app_module, '_enforce_generation_plan_limits', lambda config, _user=None: config)
    monkeypatch.setattr(app_module, '_validate_media_request_config', lambda _config: None)

    def fail_scene_plan(_config, **_kwargs):
        raise app_module.OpenRouterScenePlanningError('Scene planning provider unavailable')

    monkeypatch.setattr(app_module, '_run_video_pipeline', fail_scene_plan)
    response = app_module.app.test_client().post('/api/generate', json={
        'title': 'Retryable planner example',
        'script': 'A narration script long enough to reach OpenRouter scene planning.',
        'output_dir': str(tmp_path),
    })
    assert response.status_code == 503
    payload = response.get_json()
    assert payload['retryable'] is True
    assert payload['status'] == 'waiting_for_openrouter'
    assert payload['stage'] == 'openrouter_scene_plan'
    assert (Path(payload['checkpoint_dir']).parent / 'package.json').is_file()


def test_api_generate_reports_correctable_remotion_plan_failure(tmp_path, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, '_get_authenticated_state', lambda: ('test-token', {'access_state': 'active'}))
    monkeypatch.setattr(app_module, '_enforce_generation_plan_limits', lambda config, _user=None: config)
    monkeypatch.setattr(app_module, '_validate_media_request_config', lambda _config: None)
    monkeypatch.setattr(app_module, '_resume_checkpointed_remotion_render', lambda *_args: None)

    def fail_plan(_config, **_kwargs):
        raise app_module.RemotionEditPlanError('Unsupported map route')

    monkeypatch.setattr(app_module, '_run_video_pipeline', fail_plan)
    response = app_module.app.test_client().post('/api/generate', json={
        'title': 'Correctable map route',
        'script': 'Narration script long enough for a saved Remotion plan.',
        'output_dir': str(tmp_path),
    })
    assert response.status_code == 409, response.get_json()
    payload = response.get_json()
    assert payload['retryable'] is True
    assert payload['stage'] == 'remotion_edit_plan'
    assert Path(payload['checkpoint_dir']).is_dir()


def test_api_generate_keeps_media_failure_package_retryable(tmp_path, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, '_get_authenticated_state', lambda: ('test-token', {'access_state': 'active'}))
    monkeypatch.setattr(app_module, '_enforce_generation_plan_limits', lambda config, _user=None: config)
    monkeypatch.setattr(app_module, '_validate_media_request_config', lambda _config: None)
    monkeypatch.setattr(app_module, '_resume_checkpointed_remotion_render', lambda *_args: None)

    def fail_media(_config, **_kwargs):
        raise app_module.MediaContractError('No verified media for scene 1', {'scene_index': 0})

    monkeypatch.setattr(app_module, '_run_video_pipeline', fail_media)
    response = app_module.app.test_client().post('/api/generate', json={
        'title': 'Retryable source search',
        'script': 'A narration script long enough to save a job package.',
        'output_dir': str(tmp_path),
    })
    assert response.status_code == 422, response.get_json()
    payload = response.get_json()
    assert payload['status'] == 'waiting_for_media'
    assert payload['stage'] == 'media_validation'
    assert payload['retryable'] is True
    assert Path(payload['checkpoint_dir']).is_dir()


def test_unverified_director_route_uses_verified_scene_media(tmp_path):
    import app as app_module

    image_path = tmp_path / 'scene.jpg'
    image_path.write_bytes(b'local-scene-image')
    manifest = {
        'director': {'status': 'directed'},
        'scenes': [{
            'narration': 'Ships traveled around South America.',
            'media': {'src': str(image_path), 'type': 'image'},
            'primary_visual': {
                'type': 'AnimatedMap', 'variant': 'route_flight_arc',
                'props': {'from': 'New York', 'to': 'San Francisco'},
            },
            'graphic': {'template_id': 'map-route', 'props': {}},
            'map_shot': {'enabled': True, 'route_id': '', 'location_names': ['New York', 'San Francisco']},
            'text': {'frame_mode': 'map'},
        }],
    }
    assert app_module._repair_unsupported_route_map_scenes(manifest) == [0]
    scene = manifest['scenes'][0]
    assert scene['primary_visual']['type'] == 'SmartImage'
    assert scene['text']['frame_mode'] == 'media_only'
    assert scene['map_shot'] == {}
    assert scene['narration'] == 'Ships traveled around South America.'
    assert manifest['director']['repairs'][0]['type'] == 'unverified_route_to_media'


def test_checkpoint_redacted_openrouter_key_is_rehydrated_from_environment(monkeypatch):
    import app as app_module

    monkeypatch.setenv('OPENROUTER_API_KEY', 'sk-or-v1-local-test-key')
    restored = app_module._apply_env_api_key_defaults({
        'openrouter_api_key': '[REDACTED]',
        'pexels_api_key': '[REDACTED]',
    })
    assert restored['openrouter_api_key'] == 'sk-or-v1-local-test-key'
    assert restored['pexels_api_key'] != '[REDACTED]'


def test_explicit_api_media_source_survives_automatic_default():
    import app as app_module

    for source in ('Web Images', 'Stock Media', 'YouTube Clips'):
        config = normalize_generation_config({'image_source': source})
        assert app_module._coerce_media_source_from_weights(config)['image_source'] == source
    assert app_module._coerce_media_source_from_weights(
        normalize_generation_config({'media_mode': 'automatic'})
    )['image_source'] == 'Mixed Media'
