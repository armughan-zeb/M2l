"""Bulk query batches (item b) + persisted scene plan (item e).

(b) Queries are BULK: chunk default 32 (inside the 3-32 clamp): 25 scenes
must be planned in 1 bridge request, not 3 (12/12/1) as before. One
request carries a whole short job; longer jobs split into 32-scene
chunks the worker pool answers in parallel. The chunking code is the
justification — query_planning_budget scales with ceil(chunks/workers),
and one chunk is one bridge request whatever its size.

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
    # Contract-valid directed row: domain_category set, >=3 positive
    # terms, non-empty intents, a real subject typed to match, and
    # primary YT/web queries carrying the entity + a viewpoint word
    # ("aerial") from the visual intent.
    return {
        "scene_number": scene_number,
        "container_subject": f"Test Subject {tag}",
        "scene_subject": f"Test Subject {tag} scene",
        "visual_intent": f"Aerial view of test subject {tag} on screen",
        "visual_subject": f"Test Subject {tag}",
        "visual_subject_type": "place",
        "subject_evidence": f"Test subject {tag} is visible.",
        "visible_target": f"Test subject {tag}",
        "identity_scope": "subject",
        "domain_category": "documentary",
        "scene_subject_type": "place",
        "retrieval_positive_terms": [f"Test Subject {tag}", "test subject", "aerial view"],
        "retrieval_negative_terms": [],
        "youtube_query_intents": ["aerial footage", "test subject"],
        "youtube": {
            "primary": f"test subject {tag} aerial footage",
            "supporting": f"test subject {tag} video",
            "fallback": f"test subject {tag} clip",
        },
        "web": {
            "primary": f"test subject {tag} aerial photo",
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
    """Return valid rows for the scenes named in a brain user prompt.

    One DISTINCT row per scene: the tag is the scene's own text, so the
    plan-level duplicate check (no two scenes share a primary query)
    passes. A helper returning the same row for every scene is exactly
    the bug that check exists to catch, so it must not be "fixed" by
    weakening the validator. The tag is the FULL scene text, not a
    truncation: the distinctiveness check needs a scene's distinctive
    token (e.g. Hindenburg scene 1's only one, "coming") to be present
    in its queries, and a 40-char cut can hide it past the boundary.
    """
    lines = re.findall(r"^Scene \d+: (.+)$", prompt, flags=re.MULTILINE)
    count = len(lines)
    return json.dumps({"scenes": [
        _valid_row(i + 1, (lines[i].strip() if i < len(lines) else "chunk"))
        for i in range(count)]})


def _scenes(count):
    return [f"Scene text {index + 1} describes the test subject in detail."
            for index in range(count)]


def _brain(monkeypatch):
    monkeypatch.delenv("VIDZEO_BRAIN_SCENE_CHUNK_SIZE", raising=False)
    monkeypatch.delenv("VIDZEO_BRAIN_SCENE_WORKERS", raising=False)
    return VidzeoBrain("test-key", fallback_models=[])


def test_directed_row_rejects_gta_scene_seven_row():
    """The real GTA VI scene-7 stored plan row (job ef306926, earlier
    attempt): the container subject is a format phrase and the retrieval
    contract fields are absent. It must never be reused as directed, and
    the fresh path must reject it so the chunk is re-planned mechanically.
    """
    from services.vidzeo_brain import _directed_scene_row_error, _is_directed_scene_row

    row = {
        "scene_number": 1,
        "container_subject": "video game official trailer gameplay",
        "scene_subject": "Grand Theft Auto VI gameplay on PlayStation 5 and Xbox Series X and S",
        "visual_intent": "Official gameplay footage showing Leonida action on console",
        "visual_subject": "Grand Theft Auto VI",
        "visual_subject_type": "video_game",
        "subject_evidence": "it launches first on PlayStation 5 and Xbox Series X and S",
        "visible_target": "GTA VI gameplay running on PlayStation 5 and Xbox Series X and S",
        "identity_scope": "context",
        "youtube": {
            "primary": "Grand Theft Auto VI official gameplay PlayStation 5",
            "supporting": "GTA VI gameplay Xbox Series X",
            "fallback": "GTA VI official trailer gameplay 4K",
        },
        "web": {
            "primary": "Grand Theft Auto VI gameplay screenshot",
            "supporting": "GTA VI PlayStation 5 gameplay",
            "fallback": "GTA VI Xbox Series X gameplay",
        },
        "stock": {
            "primary_query": "person playing video game console controller",
            "alt_query": "gaming console living room night",
            "fallback_query": "video game controller close up",
        },
    }
    assert _is_directed_scene_row(row) is False
    assert "domain_category" in (_directed_scene_row_error(row) or "")
    # The contract-valid fixture row is still directed.
    assert _is_directed_scene_row(_valid_row(1, "Contract")) is True
    assert _directed_scene_row_error(_valid_row(1, "Contract")) is None


def test_fresh_path_rejects_empty_contract_row(monkeypatch):
    brain = _brain(monkeypatch)
    gta_row = {
        "scene_number": 1,
        "container_subject": "video game official trailer gameplay",
        "scene_subject": "Grand Theft Auto VI gameplay on PlayStation 5",
        "visual_intent": "Official gameplay footage showing Leonida action on console",
        "visual_subject": "Grand Theft Auto VI",
        "visual_subject_type": "video_game",
        "subject_evidence": "it launches first on PlayStation 5",
        "visible_target": "GTA VI gameplay running on PlayStation 5",
        "identity_scope": "context",
        "youtube": {
            "primary": "Grand Theft Auto VI official gameplay PlayStation 5",
            "supporting": "GTA VI gameplay Xbox Series X",
            "fallback": "GTA VI official trailer gameplay 4K",
        },
        "web": {
            "primary": "Grand Theft Auto VI gameplay screenshot",
            "supporting": "GTA VI PlayStation 5 gameplay",
            "fallback": "GTA VI Xbox Series X gameplay",
        },
        "stock": {
            "primary_query": "person playing video game console controller",
            "alt_query": "gaming console living room night",
            "fallback_query": "video game controller close up",
        },
    }
    with mock.patch.object(vidzeo_brain.OpenRouterLLM, "chat",
                           return_value=json.dumps({"scenes": [gta_row]})):
        assert brain.plan_scenes(
            topic="Grand Theft Auto VI",
            script="When it arrives, it launches first on PlayStation 5.",
            scene_texts=["When it arrives, it launches first on PlayStation 5."],
        ) is None


def test_query_chunk_default_is_bulk_thirty_two(monkeypatch):
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
    # 25 scenes at chunk 32 -> a single bulk request carrying all 25.
    # At the old default 12 this was 3 (12/12/1).
    assert len(calls) == 1, [len(re.findall(r"^Scene \d+:", p, flags=re.MULTILINE)) for p in calls]
    assert sorted(len(re.findall(r"^Scene \d+:", p, flags=re.MULTILINE)) for p in calls) == [25]


def test_query_chunk_bulk_splits_long_jobs(monkeypatch):
    # 40 scenes at chunk 32 -> 2 bulk requests (32/8), planned in parallel.
    brain = _brain(monkeypatch)
    calls = []
    lock = threading.Lock()

    def fake_chat(*_args, **kwargs):
        with lock:
            calls.append(kwargs["user_prompt"])
        return _chat_reply_for_prompt(kwargs["user_prompt"])

    with mock.patch.object(vidzeo_brain.OpenRouterLLM, "chat", side_effect=fake_chat):
        rows = brain.plan_scenes(
            topic="Test", script=" ".join(_scenes(40)), scene_texts=_scenes(40))
    assert rows is not None and len(rows) == 40
    assert len(calls) == 2, [len(re.findall(r"^Scene \d+:", p, flags=re.MULTILINE)) for p in calls]
    assert sorted(len(re.findall(r"^Scene \d+:", p, flags=re.MULTILINE)) for p in calls) == [8, 32]


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
    # First attempt directs chunk 1, then the provider stalls: chunk 2
    # falls back to mechanical rows. The resume must re-plan ONLY those 8
    # scenes (1 bulk bridge request), reusing the first 32 directed rows.
    brain = _brain(monkeypatch)
    # NOTE: set AFTER _brain(), which clears the chunk/worker env vars.
    monkeypatch.setenv("VIDZEO_BRAIN_SCENE_WORKERS", "1")
    scenes = _scenes(40)
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
    assert rows is not None and len(rows) == 40
    assert sum(1 for row in rows if row.get("_query_fallback")) == 8
    assert sum(1 for row in rows if not row.get("_query_fallback")) == 32
    directed_first = [dict(row) for row in rows[:32]]
    brain2 = VidzeoBrain("test-key", fallback_models=[])
    with job_scope({"job_id": "job-1", "llm_cache_dir": _job_cache(tmp_path)}):
        with mock.patch.object(vidzeo_brain.OpenRouterLLM, "chat",
                               side_effect=lambda **kw: _chat_reply_for_prompt(kw["user_prompt"])) as chat2:
            resumed = brain2.plan_scenes(topic="Test", script=script, scene_texts=scenes)
    assert resumed is not None and len(resumed) == 40
    assert chat2.call_count == 1, "only the 8 fallback scenes may be re-planned (one bulk request)"
    assert resumed[:32] == directed_first, "directed rows must be reused verbatim"
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
        assert chat.call_count == 2  # 57 scenes at chunk 32 -> 32/25
        brain2 = VidzeoBrain("test-key", fallback_models=[])
        with mock.patch.object(vidzeo_brain.OpenRouterLLM, "chat",
                               side_effect=AssertionError("must file zero new requests")) as chat2:
            resumed = brain2.plan_scenes(
                topic="The Hindenburg Disaster: Fire in the Sky",
                script=script, scene_texts=scenes)
        assert chat2.call_count == 0
        assert resumed == rows
