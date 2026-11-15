"""Wider query batches (item b) + persisted scene plan (item e).

(b) Queries chunk default 8 -> 12 (inside the 3-12 clamp): 25 scenes must
be planned in 3 bridge requests (12/12/1), not 4 (8/8/8/1). The chunking
code is the justification — query_planning_budget scales with
ceil(chunks/workers), and one chunk is one bridge request whatever its size.

(e) plan_scenes persists direction + web/YT/stock queries on the job record
(package/checkpoints/scene_plan.json) and a resumed call reuses its
already-directed rows, filing zero new requests for them. Only DIRECTED
rows are reused; mechanical/fallback rows are re-planned.
"""
import json
import re
import threading
from pathlib import Path
from unittest import mock

import pytest

from services import vidzeo_brain
from services.job_context import job_scope
from services.vidzeo_brain import VidzeoBrain

REPO_ROOT = Path(__file__).resolve().parents[2]


def _valid_row(scene_number, tag):
    return {
        "scene_number": scene_number,
        "container_subject": f"Test Subject {tag}",
        "scene_subject": f"Test Subject {tag} scene",
        "visual_intent": f"Show test subject {tag} on screen",
        "visual_subject": f"Test Subject {tag}",
        "visual_subject_type": "place",
        "subject_evidence": f"Test subject {tag} is visible.",
        "visible_target": f"Test subject {tag}",
        "identity_scope": "subject",
        "youtube": {
            "primary": f"test subject {tag} footage",
            "supporting": f"test subject {tag} video",
            "fallback": f"test subject {tag} clip",
        },
        "web": {
            "primary": f"test subject {tag} photo",
            "supporting": f"test subject {tag} image",
            "fallback": f"test subject {tag} picture",
        },
        "stock": {
            "primary_query": f"test subject {tag}",
            "alt_query": f"test subject {tag} scene",
            "fallback_query": "test subject",
        },
    }


def _chat_reply_for_prompt(prompt):
    """Return valid rows for the scenes named in a brain user prompt."""
    count = len(re.findall(r"^Scene \d+:", prompt, flags=re.MULTILINE))
    first = re.search(r"^Scene 1: (.+)$", prompt, flags=re.MULTILINE)
    tag = (first.group(1).strip()[:40] if first else "chunk")
    return json.dumps({"scenes": [_valid_row(i + 1, tag) for i in range(count)]})


def _scenes(count):
    return [f"Scene text {index + 1} describes the test subject in detail."
            for index in range(count)]


def _brain(monkeypatch):
    monkeypatch.delenv("VIDZEO_BRAIN_SCENE_CHUNK_SIZE", raising=False)
    monkeypatch.delenv("VIDZEO_BRAIN_SCENE_WORKERS", raising=False)
    return VidzeoBrain("test-key", fallback_models=[])


def test_query_chunk_default_is_twelve(monkeypatch):
    brain = _brain(monkeypatch)
    calls = []
    lock = threading.Lock()

    def fake_chat(*_args, **kwargs):
        with lock:
            calls.append(kwargs["user_prompt"])
        return _chat_reply_for_prompt(kwargs["user_prompt"])

    with mock.patch.object(vidzeo_brain.OpenRouterLLM, "chat", side_effect=fake_chat):
        rows = brain.plan_scenes(
            topic="Test", script=" ".join(_scenes(25)), scene_texts=_scenes(25))
    assert rows is not None and len(rows) == 25
    # 25 scenes at chunk 12 -> chunks of 12/12/1 -> 3 bridge requests.
    # At the old default 8 this was 4 (8/8/8/1).
    assert len(calls) == 3, [len(re.findall(r"^Scene \d+:", p, flags=re.MULTILINE)) for p in calls]
    assert sorted(len(re.findall(r"^Scene \d+:", p, flags=re.MULTILINE)) for p in calls) == [1, 12, 12]


def test_query_chunk_env_still_wins(monkeypatch):
    brain = _brain(monkeypatch)
    monkeypatch.setenv("VIDZEO_BRAIN_SCENE_CHUNK_SIZE", "3")
    calls = []

    def fake_chat(*_args, **kwargs):
        calls.append(kwargs["user_prompt"])
        return _chat_reply_for_prompt(kwargs["user_prompt"])

    with mock.patch.object(vidzeo_brain.OpenRouterLLM, "chat", side_effect=fake_chat):
        rows = brain.plan_scenes(
            topic="Test", script=" ".join(_scenes(7)), scene_texts=_scenes(7))
    assert rows is not None and len(rows) == 7
    assert len(calls) == 3  # 7 scenes at chunk 3 -> 3/3/1


def _job_cache(tmp_path):
    return str(tmp_path / "checkpoints" / "model-cache")


def test_resumed_plan_files_zero_new_requests(tmp_path, monkeypatch):
    brain = _brain(monkeypatch)
    scenes = _scenes(5)
    script = " ".join(scenes)
    with job_scope({"job_id": "job-1", "llm_cache_dir": _job_cache(tmp_path)}):
        with mock.patch.object(vidzeo_brain.OpenRouterLLM, "chat",
                               side_effect=lambda **kw: _chat_reply_for_prompt(kw["user_prompt"])) as chat:
            rows = brain.plan_scenes(topic="Test", script=script, scene_texts=scenes)
        assert rows is not None and len(rows) == 5
        assert chat.call_count == 1
        store = tmp_path / "checkpoints" / "scene_plan.json"
        assert store.is_file()
        stored = json.loads(store.read_text(encoding="utf-8"))
        assert stored["_schema"] == vidzeo_brain._SCENE_PLAN_STORE_SCHEMA
        assert stored["scene_count"] == 5
        # Second attempt, same job record, operator answers nothing: the
        # mock raises on ANY call, so any new request fails the test.
        brain2 = VidzeoBrain("test-key", fallback_models=[])
        with mock.patch.object(vidzeo_brain.OpenRouterLLM, "chat",
                               side_effect=AssertionError("must file zero new requests")) as chat2:
            resumed = brain2.plan_scenes(topic="Test", script=script, scene_texts=scenes)
        assert chat2.call_count == 0
        assert resumed == rows


def test_resumed_plan_replans_only_fallback_scenes(tmp_path, monkeypatch):
    # First attempt directs chunk 1, then the provider stalls: chunks 2-3
    # fall back to mechanical rows. The resume must re-plan ONLY those 13
    # scenes (2 bridge requests: 12 + 1), reusing the first 12 directed rows.
    brain = _brain(monkeypatch)
    # NOTE: set AFTER _brain(), which clears the chunk/worker env vars.
    monkeypatch.setenv("VIDZEO_BRAIN_SCENE_WORKERS", "1")
    scenes = _scenes(25)
    script = " ".join(scenes)

    def flaky_chat(*_args, **kwargs):
        # Decide by CHUNK IDENTITY, not by a shared call counter: with
        # parallel workers a counter read twice (increment, then test) races
        # and the wrong chunk can win the single success. Only the chunk
        # starting at scene text 1 succeeds; every other chunk stalls.
        prompt = kwargs["user_prompt"]
        first = re.search(r"^Scene 1: (.+)$", prompt, flags=re.MULTILINE)
        if first and first.group(1).startswith("Scene text 1 "):
            return _chat_reply_for_prompt(prompt)
        raise TimeoutError("provider stalled")

    with job_scope({"job_id": "job-1", "llm_cache_dir": _job_cache(tmp_path)}):
        with mock.patch.object(vidzeo_brain.OpenRouterLLM, "chat", side_effect=flaky_chat):
            rows = brain.plan_scenes(topic="Test", script=script, scene_texts=scenes)
    assert rows is not None and len(rows) == 25
    assert sum(1 for row in rows if row.get("_query_fallback")) == 13
    assert sum(1 for row in rows if not row.get("_query_fallback")) == 12
    directed_first = [dict(row) for row in rows[:12]]
    brain2 = VidzeoBrain("test-key", fallback_models=[])
    with job_scope({"job_id": "job-1", "llm_cache_dir": _job_cache(tmp_path)}):
        with mock.patch.object(vidzeo_brain.OpenRouterLLM, "chat",
                               side_effect=lambda **kw: _chat_reply_for_prompt(kw["user_prompt"])) as chat2:
            resumed = brain2.plan_scenes(topic="Test", script=script, scene_texts=scenes)
    assert resumed is not None and len(resumed) == 25
    assert chat2.call_count == 2, "only the 13 fallback scenes may be re-planned (12+1)"
    assert resumed[:12] == directed_first, "directed rows must be reused verbatim"
    assert not any(row.get("_query_fallback") for row in resumed)


def test_changed_script_does_not_reuse_the_plan(tmp_path, monkeypatch):
    brain = _brain(monkeypatch)
    scenes = _scenes(5)
    with job_scope({"job_id": "job-1", "llm_cache_dir": _job_cache(tmp_path)}):
        with mock.patch.object(vidzeo_brain.OpenRouterLLM, "chat",
                               side_effect=lambda **kw: _chat_reply_for_prompt(kw["user_prompt"])):
            rows = brain.plan_scenes(topic="Test", script=" ".join(scenes), scene_texts=scenes)
        assert rows is not None and len(rows) == 5
        brain2 = VidzeoBrain("test-key", fallback_models=[])
        with mock.patch.object(vidzeo_brain.OpenRouterLLM, "chat",
                               side_effect=TimeoutError("provider stalled")):
            # Different script -> digest mismatch -> no reuse -> planning
            # runs and fails honestly instead of serving a stale plan.
            assert brain2.plan_scenes(
                topic="Test", script=" ".join(scenes) + " Extra ending.",
                scene_texts=scenes) is None


def test_hindenburg_fixture_plan_is_reused_on_resume(tmp_path, monkeypatch):
    # Reference fixture only (READ-ONLY): the preserved Hindenburg job's
    # real scene texts (57) + script. A first attempt stores the plan; a
    # resumed attempt with a dead provider files zero new requests.
    contracts_path = (REPO_ROOT / "work" / "hindenburg5min" / "vidnets"
                      / "checkpoints" / "scene_contracts.json")
    script_path = REPO_ROOT / "work" / "hindenburg5min" / "script.txt"
    if not contracts_path.is_file() or not script_path.is_file():
        pytest.skip("Hindenburg fixture not present")
    contracts = json.loads(contracts_path.read_text(encoding="utf-8"))
    scenes = list((contracts.get("expected") or {}).get("scene_texts") or [])
    assert len(scenes) == 57
    script = script_path.read_text(encoding="utf-8")
    brain = _brain(monkeypatch)
    with job_scope({"job_id": "53c2d9f4", "llm_cache_dir": _job_cache(tmp_path)}):
        with mock.patch.object(vidzeo_brain.OpenRouterLLM, "chat",
                               side_effect=lambda **kw: _chat_reply_for_prompt(kw["user_prompt"])) as chat:
            rows = brain.plan_scenes(
                topic="The Hindenburg Disaster: Fire in the Sky",
                script=script, scene_texts=scenes)
        assert rows is not None and len(rows) == 57
        assert chat.call_count == 5  # 57 scenes at chunk 12 -> 12/12/12/12/9
        brain2 = VidzeoBrain("test-key", fallback_models=[])
        with mock.patch.object(vidzeo_brain.OpenRouterLLM, "chat",
                               side_effect=AssertionError("must file zero new requests")) as chat2:
            resumed = brain2.plan_scenes(
                topic="The Hindenburg Disaster: Fire in the Sky",
                script=script, scene_texts=scenes)
        assert chat2.call_count == 0
        assert resumed == rows
