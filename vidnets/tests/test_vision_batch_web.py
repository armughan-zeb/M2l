"""Batched web-lane vision: one request per scene, all scenes filed at once.

Covers the three layers of the batch:

- transport: ``QwenVisionClient.analyze_images_batch`` files ONE request
  for a scene's candidates and returns one verdict row per candidate,
  under the VISION_BATCH_VERDICT_SCHEMA response_format contract;
- gate: ``verify_batch`` (pipeline_v2_media_adapter) scores those rows
  through the full single-candidate ``verify()`` — same validation,
  same subject-level acceptance — and falls back to the sequential loop
  when the batch is unavailable;
- resolver: ``_rank_deferred_scene_shortlist`` judges a shortlist from
  one batch call, and prep-banked (cached_prep) candidates spend NO
  vision call at all.

The test-12 replay (real audit/manifest data) lives in
test_vision_batch_test12_replay.py.
"""
import base64
import importlib.util
import json
from pathlib import Path

import pytest

from services.pipeline_v2_media_adapter import build_qwen_verifier
from services.visual_intelligence.cache import VisualDecisionCache, valid_cached_decision
from services.visual_intelligence.context import VisualDecisionContext
from services.visual_intelligence.vision_client import (
    VISION_BATCH_VERDICT_SCHEMA,
    QwenVisionClient,
    VisionClientError,
    _normalise_image_response,
)
from services.web_image.resolver import (
    _is_prep_cache_hit,
    _prep_verified_model_row,
    _rank_deferred_scene_shortlist,
)

# 1x1 transparent PNG: a real decodable file so pixel_digest/_is_video work.
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

SCENE = {
    "scene_id": "scene-batch-1",
    "text": "A modern highway traffic scene.",
    "visual_requirement": {
        "canonical_entity": "Highway",
        "requirement_hash": "highway-hash",
    },
}

FULL_VERDICT = {
    "candidate_id": "",
    "entity_conflict": False,
    "identity_score": 90,
    "action_score": 80,
    "context_score": 85,
    "quality": 88,
    "final_score": 87,
    "relevance": 88,
    "confidence": 90,
    "visible_overlays": [],
    "subject_box": {},
    "safe_text_regions": [],
    "fatal_mismatch": False,
    "domain_match": True,
    "scene_match": True,
    "reason": "highway traffic matches the narration",
    "scene_description": "highway traffic",
    "source_presentation_type": "photograph",
    "has_text": False,
}

# A raw verdict that _score_verdict hard-rejects (identity/final < 35).
REJECTED_VERDICT = dict(
    FULL_VERDICT,
    identity_score=10,
    action_score=10,
    context_score=10,
    final_score=10,
    relevance=10,
    confidence=90,
    fatal_mismatch=True,
    domain_match=False,
    scene_match=False,
    reason="wrong entity: a different bridge, unrelated to the highway",
    scene_description="a different bridge",
)


def _candidate(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(_PNG_1X1)
    return {
        "path": str(path),
        "candidate_id": str(path),
        "source": "stock",
        "provider": "pexels",
        "metadata_score": 75.0,
        "score": 75.0,
    }


def _row_for(path, accept=True):
    row = dict(FULL_VERDICT if accept else REJECTED_VERDICT)
    row["candidate_id"] = str(path)
    return _normalise_image_response(row)


# ---------------------------------------------------------------------------
# 1. The batch response_format schema, enforced by validate_answer.py
# ---------------------------------------------------------------------------

def _load_validate_answer():
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "resident-inference-worker" / "validate_answer.py"
    spec = importlib.util.spec_from_file_location("validate_answer", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _batch_request_file(tmp_path):
    request = {
        "id": "req-batch-1",
        "owner_id": "owner",
        "job_id": "job",
        "kind": "vision",
        "operation": "visual_review",
        "state": "pending",
        "response_format": {
            "type": "json_schema",
            "json_schema": VISION_BATCH_VERDICT_SCHEMA,
        },
    }
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    return str(path)


def _write_answer(tmp_path, value):
    path = tmp_path / "answer.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return str(path)


def _valid_batch_answer(paths):
    return {
        "verdicts": [
            {
                "candidate_id": str(path),
                "entity_conflict": False,
                "identity_score": 90,
                "action_score": 80,
                "context_score": 85,
                "quality": 88,
                "final_score": 87,
                "relevance": 88,
                "confidence": 90,
                "visible_overlays": [],
                "subject_box": {},
                "safe_text_regions": [],
                "fatal_mismatch": False,
                "domain_match": True,
                "scene_match": True,
                "reason": "highway traffic matches the narration",
                "scene_description": "highway traffic",
                "source_presentation_type": "photograph",
                "has_text": False,
            }
            for path in paths
        ]
    }


def test_batch_schema_valid_answer_passes_validate_answer(tmp_path):
    """A well-formed batched response validates through validate_answer.py."""
    validate_answer = _load_validate_answer()
    request_path = _batch_request_file(tmp_path)
    answer_path = _write_answer(tmp_path, _valid_batch_answer(["cand-0", "cand-1"]))
    ok, issue = validate_answer.validate(answer_path, request_path)
    assert ok, issue


def test_batch_schema_malformed_answers_fail_validate_answer(tmp_path):
    """Malformed batches are rejected cleanly by the same gate."""
    validate_answer = _load_validate_answer()
    request_path = _batch_request_file(tmp_path)
    cases = {
        "missing_verdicts": {"other": []},
        "verdicts_not_array": {"verdicts": {"candidate_id": "cand-0"}},
        "empty_verdicts": {"verdicts": []},
        "missing_candidate_id": {"verdicts": [dict(_valid_batch_answer(["cand-0"])["verdicts"][0], candidate_id=None)]},
        "score_out_of_range": {"verdicts": [dict(_valid_batch_answer(["cand-0"])["verdicts"][0], identity_score=150)]},
        "boolean_as_string": {"verdicts": [dict(_valid_batch_answer(["cand-0"])["verdicts"][0], entity_conflict="false")]},
        "missing_reason": {"verdicts": [{k: v for k, v in _valid_batch_answer(["cand-0"])["verdicts"][0].items() if k != "reason"}]},
    }
    # candidate_id None is not a string; build that case explicitly.
    cases["missing_candidate_id"] = {"verdicts": [{k: v for k, v in _valid_batch_answer(["cand-0"])["verdicts"][0].items() if k != "candidate_id"}]}
    for name, answer in cases.items():
        answer_path = _write_answer(tmp_path, answer)
        ok, issue = validate_answer.validate(answer_path, request_path)
        assert not ok, f"{name} unexpectedly passed: {issue}"
        assert issue, f"{name} failed without an issue message"


def test_batch_schema_direct_validator(tmp_path):
    """The server's own _json_schema_error enforces the batch contract."""
    from server.services.openrouter_llm import _json_schema_error

    schema = VISION_BATCH_VERDICT_SCHEMA["schema"]
    assert _json_schema_error(_valid_batch_answer(["a", "b"]), schema) == ""
    assert _json_schema_error({"verdicts": []}, schema) != ""
    assert _json_schema_error(
        {"verdicts": [dict(_valid_batch_answer(["a"])["verdicts"][0], quality="high")]},
        schema,
    ) != ""


def test_batch_schema_accepts_eight_and_sixteen_verdicts(tmp_path):
    """REGRESSION (schema cap, 2026-11-20): the web batch schema capped
    verdicts at maxItems 4 while analyze_images_batch sends up to 16
    images (SAFE_VISION_BATCH_IMAGES) in one request, so every 5+ image
    batch failed submission with 400 "too many items" — the same failure
    the YT lane hit live with 8 verdicts, and the transport test proved
    live the web lane breaks at 6 (largest working size 4). The cap is
    now 16, matching the batch cap: 6, 8 and 16 verdicts validate, 17
    still fail, through both the worker-side gate and the server's own
    validator."""
    from server.services.openrouter_llm import _json_schema_error

    schema = VISION_BATCH_VERDICT_SCHEMA["schema"]
    assert schema["properties"]["verdicts"]["maxItems"] == 16
    validate_answer = _load_validate_answer()
    request_path = _batch_request_file(tmp_path)
    for count in (6, 8, 16):
        answer = _valid_batch_answer([f"cand-{i}" for i in range(count)])
        assert _json_schema_error(answer, schema) == ""
        answer_path = _write_answer(tmp_path, answer)
        ok, issue = validate_answer.validate(answer_path, request_path)
        assert ok, issue
    too_many = _valid_batch_answer([f"cand-{i}" for i in range(17)])
    assert _json_schema_error(too_many, schema) != ""
    answer_path = _write_answer(tmp_path, too_many)
    ok, issue = validate_answer.validate(answer_path, request_path)
    assert not ok
    assert issue


# ---------------------------------------------------------------------------
# 2. Transport: one request for the whole shortlist
# ---------------------------------------------------------------------------

def _raw_client():
    return QwenVisionClient(api_key="test-key", model="qwen/qwen3.5-flash-02-23")


def test_analyze_images_batch_never_sends_more_than_sixteen_images(tmp_path):
    """REGRESSION (max-parallel, 2026-11-20): a batch carries at most 16 images.

    The cap was raised from 2 to 16 after the live test proved 16 images in
    one request judges fine; the token budget (6000) is the binding limit,
    not the image count. 17 candidates become 2 requests (16 + 1), never
    1 request of 17.
    """
    client = _raw_client()
    candidates = [_candidate(tmp_path, f"cand-{i}.png") for i in range(17)]
    paths = [c["path"] for c in candidates]
    posted = []

    def fake_post_json(content, *, system="", budget=1400, retry_budget=2400,
                       json_schema=None, full_budget=False, **kwargs):
        images = [part for part in content if part.get("type") == "image_url"]
        assert len(images) <= 16, "max batch: at most 16 images per request"
        posted.append({"images": len(images)})
        start = sum(p["images"] for p in posted[:-1])
        rows = [dict(_valid_batch_answer([paths[start + j]])["verdicts"][0])
                for j in range(len(images))]
        return json.dumps({"verdicts": rows})

    client._post_json = fake_post_json
    result = QwenVisionClient.analyze_images_batch.__wrapped__(
        client, paths, "judge these", "system prompt",
        [c["candidate_id"] for c in candidates],
    )
    assert len(posted) == 2, "17 candidates split into 2 batches (16 + 1)"
    assert [p["images"] for p in posted] == [16, 1]
    assert set(result) == set(paths)
    assert all(result[p]["identity_score"] == 90 for p in paths)


def test_analyze_images_batch_works_in_fast_mode(tmp_path):
    """REGRESSION (test 13): batching must work when fast_mode is True.

    The adapter sets fast_mode=True on the vision client for EVERY
    mixed-media job (pipeline_v2_media_adapter lines 2226/2311/2352), and
    analyze_images_batch used to raise "Batched vision is disabled in fast
    mode" there — so the batch failed 6/6 times live in test 13 and every
    scene fell back to sequential singles. A batch is one request (fewer
    than the sequential loop it replaces), so fast_mode must not ban it.
    """
    client = QwenVisionClient(
        api_key="test-key", model="qwen/qwen3.5-flash-02-23", fast_mode=True,
    )
    assert client.fast_mode is True
    candidates = [_candidate(tmp_path, f"cand-{i}.png") for i in range(3)]
    paths = [c["path"] for c in candidates]
    posted = {}

    def fake_post_json(content, *, system="", budget=1400, retry_budget=2400,
                       json_schema=None, full_budget=False, **kwargs):
        posted["full_budget"] = full_budget
        posted["budget"] = budget
        return json.dumps({"verdicts": [
            dict(_valid_batch_answer([paths[0]])["verdicts"][0]),
            dict(_valid_batch_answer([paths[1]])["verdicts"][0]),
            dict(_valid_batch_answer([paths[2]])["verdicts"][0], identity_score=12, final_score=12,
                 fatal_mismatch=True, domain_match=False, scene_match=False,
                 reason="wrong entity: a different bridge"),
        ]})

    client._post_json = fake_post_json
    result = QwenVisionClient.analyze_images_batch.__wrapped__(
        client, paths, "judge these", "system prompt",
        [c["candidate_id"] for c in candidates],
    )
    assert set(result) == set(paths), "fast_mode must not block the batch"
    assert result[paths[0]]["identity_score"] == 90
    assert result[paths[2]]["fatal_mismatch"] is True
    assert posted["full_budget"] is True, (
        "a batch must carry its full token budget in fast mode — the 900 "
        "cap truncates a multi-verdict response mid-list"
    )
    assert posted["budget"] > 900, "batch budget is sized for N verdicts"


def test_post_json_full_budget_lifts_fast_mode_cap(tmp_path):
    """In fast mode, full_budget=True passes the full budget to _post.

    _post_json caps max_tokens to 900 in fast_mode (a single verdict fits);
    a batch verdict carries one verdict per candidate and needs its full
    computed budget (2400-4000). Without full_budget the batch response is
    truncated mid-list and unparseable — the YT lane's silent failure mode
    in test 13. Fast mode still means exactly one attempt (no retry).
    """
    client = QwenVisionClient(
        api_key="test-key", model="qwen/qwen3.5-flash-02-23", fast_mode=True,
    )
    seen = {}

    def fake_post(content, *, system="", json_mode=False, max_tokens=900,
                  json_schema=None, response_format=None):
        seen["max_tokens"] = max_tokens
        return '{"verdicts": []}'

    client._post = fake_post
    client._post_json([{"type": "text", "text": "x"}],
                      budget=3000, full_budget=True)
    assert seen["max_tokens"] == 3000, "full_budget must lift the 900 cap"
    client._post_json([{"type": "text", "text": "x"}],
                      budget=3000)
    assert seen["max_tokens"] == 900, "without full_budget the fast cap stays"


def test_analyze_images_batch_splits_into_batches(tmp_path):
    """MAX BATCH: 17 candidates -> 2 _post_json calls, each at most 16 images.

    A scene's candidates are split into chunks of SAFE_VISION_BATCH_IMAGES
    (16); each chunk is one vision request; verdicts are merged, one row
    per candidate.
    """
    client = _raw_client()
    candidates = [_candidate(tmp_path, f"cand-{i}.png") for i in range(17)]
    paths = [c["path"] for c in candidates]
    posted = []

    def fake_post_json(content, *, system="", budget=1400, retry_budget=2400,
                       json_schema=None, full_budget=False, **kwargs):
        images = [part for part in content if part.get("type") == "image_url"]
        assert len(images) <= 16, "max batch: at most 16 images per request"
        posted.append({"images": len(images), "json_schema": json_schema, "budget": budget,
                       "full_budget": full_budget})
        # The raw client chunks sequentially: call 1 carries paths[0:16],
        # call 2 carries paths[16:17]. Return one verdict per image in order.
        start = sum(p["images"] for p in posted[:-1])
        rows = []
        for j in range(len(images)):
            p = paths[start + j]
            row = dict(_valid_batch_answer([p])["verdicts"][0])
            if p == paths[16]:
                row = dict(row, identity_score=12, final_score=12,
                           fatal_mismatch=True, domain_match=False, scene_match=False,
                           reason="wrong entity: a different bridge")
            rows.append(row)
        return json.dumps({"verdicts": rows})

    # The method reads self._post_json, so stub it on the instance.
    client._post_json = fake_post_json
    result = QwenVisionClient.analyze_images_batch.__wrapped__(
        client, paths, "judge these", "system prompt",
        [c["candidate_id"] for c in candidates],
    )
    assert len(posted) == 2, "17 candidates split into 2 batches (16 + 1)"
    assert [p["images"] for p in posted] == [16, 1]
    assert all(p["json_schema"] == VISION_BATCH_VERDICT_SCHEMA for p in posted)
    assert all(p["full_budget"] is True for p in posted)
    assert set(result) == set(paths)
    assert result[paths[0]]["identity_score"] == 90
    assert result[paths[16]]["fatal_mismatch"] is True
    assert result[paths[16]]["candidate_id"] == paths[16]


def test_analyze_images_batch_positional_fallback(tmp_path):
    """Rows without a usable candidate_id map to candidates in order."""
    client = _raw_client()
    candidates = [_candidate(tmp_path, f"cand-{i}.png") for i in range(2)]
    paths = [c["path"] for c in candidates]

    def fake_post_json(content, *, system="", budget=1400, retry_budget=2400, json_schema=None, full_budget=False, **kwargs):
        return json.dumps({"verdicts": [
            dict(_valid_batch_answer(["x"])["verdicts"][0], candidate_id=""),
            dict(_valid_batch_answer(["y"])["verdicts"][0], candidate_id=""),
        ]})

    client._post_json = fake_post_json
    result = QwenVisionClient.analyze_images_batch.__wrapped__(
        client, paths, "judge these", "system prompt",
        [c["candidate_id"] for c in candidates],
    )
    assert set(result) == set(paths)
    assert result[paths[0]]["candidate_id"] == paths[0]
    assert result[paths[1]]["candidate_id"] == paths[1]


def test_analyze_images_batch_raises_on_unusable_payload(tmp_path):
    """Non-JSON or verdict-less payloads raise so callers fall back."""
    client = _raw_client()
    candidates = [_candidate(tmp_path, f"cand-{i}.png") for i in range(2)]
    paths = [c["path"] for c in candidates]
    for raw in ("not json at all", json.dumps({"verdicts": []}), json.dumps({"other": 1})):
        def _fake_post_json(*args, _raw=raw, **kwargs):
            return _raw

        client._post_json = _fake_post_json
        with pytest.raises(VisionClientError):
            QwenVisionClient.analyze_images_batch.__wrapped__(
                client, paths, "judge these", "system prompt",
                [c["candidate_id"] for c in candidates],
            )


def test_batch_cache_decision_requires_every_row(tmp_path):
    """A cached batch is reusable only when every row is a full verdict."""
    good = {f"cand-{i}": _row_for(f"cand-{i}", accept=True) for i in range(2)}
    assert valid_cached_decision("image_batch", good) is True
    assert valid_cached_decision("analyze_images_batch", good) is True
    bad = dict(good)
    bad["cand-1"] = dict(bad["cand-1"])
    bad["cand-1"]["identity_score"] = 150
    assert valid_cached_decision("image_batch", bad) is False
    assert valid_cached_decision("image_batch", {}) is False


# ---------------------------------------------------------------------------
# 3. Context: one reservation + one call-count slot per batch
# ---------------------------------------------------------------------------

def _build_verifier(tmp_path, config=None):
    config = dict(config or {})
    config.setdefault("llm_provider", "external_agent")
    config.setdefault("openrouter_api_key", "external-agent:local-user")
    verifier, context = build_qwen_verifier(config, [SCENE])
    assert hasattr(verifier, "verify_batch"), "verifier must expose verify_batch"
    context.cache = VisualDecisionCache(str(tmp_path / "vision-cache.sqlite3"))
    client = context.get_client()
    assert client is not None
    return verifier, context, client


def _stub_raw_client(client, accept_map, calls):
    """Stub the raw Qwen client: batch + single both answer from accept_map.

    accept_map: {candidate_id: bool}. Rows are real normalised verdicts.
    """

    def fake_batch(image_paths, user_prompt, system_prompt, candidate_ids=None):
        calls["batch"] += 1
        ids = list(candidate_ids or [])
        rows = {}
        for index, path in enumerate(image_paths):
            cid = ids[index] if index < len(ids) else str(path)
            rows[cid] = _row_for(cid, accept=accept_map.get(cid, True))
        return rows

    def fake_analyze(image_path, user_prompt, system_prompt):
        calls["single"] += 1
        return _row_for(str(image_path), accept=accept_map.get(str(image_path), True))

    client._client.analyze_images_batch = fake_batch
    client._client.analyze_image = fake_analyze
    client._client.analyze_clip_frames = fake_analyze
    client._client.compare_images = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("compare_images must never be called"))
    return calls


def test_context_batch_costs_one_call_and_caches(tmp_path):
    """MAX BATCH: 3 candidates = 1 context call; identical batch is a cache hit."""
    _verifier, context, client = _build_verifier(tmp_path)
    candidates = [_candidate(tmp_path, f"cand-{i}.png") for i in range(3)]
    paths = [c["path"] for c in candidates]
    calls = {"batch": 0, "single": 0}
    _stub_raw_client(client, {p: True for p in paths}, calls)
    first = client.analyze_images_batch(paths, "judge these", "system")
    assert calls["batch"] == 1, "3 candidates fit in one batch (cap 16)"
    assert context.calls == 1, context.summary()
    second = client.analyze_images_batch(paths, "judge these", "system")
    assert calls["batch"] == 1, "identical batch must hit the cache, not re-file"
    assert first == second


# ---------------------------------------------------------------------------
# 4. Adapter verify_batch: same winners, one request
# ---------------------------------------------------------------------------

def test_verify_batch_first_accept_wins_with_batches(tmp_path):
    """Common case: winner first — one batch, zero single calls."""
    verifier, _context, client = _build_verifier(tmp_path)
    candidates = [_candidate(tmp_path, f"cand-{i}.png") for i in range(3)]
    paths = [c["path"] for c in candidates]
    calls = {"batch": 0, "single": 0}
    _stub_raw_client(client, {p: True for p in paths}, calls)
    results = verifier.verify_batch(SCENE, candidates)
    assert set(results) == {paths[0]}
    assert calls == {"batch": 1, "single": 0}, calls  # 3 candidates = 1 batch
    winner = results[paths[0]]
    assert winner["accepted"] is True
    assert winner["visual_checked"] is True
    assert winner["compare_selected"] is True


def test_verify_batch_reject_then_accept_keeps_real_loser_verdict(tmp_path):
    """First rejected, second accepted: one batch, both verdicts real."""
    verifier, _context, client = _build_verifier(tmp_path)
    candidates = [_candidate(tmp_path, f"cand-{i}.png") for i in range(3)]
    paths = [c["path"] for c in candidates]
    calls = {"batch": 0, "single": 0}
    _stub_raw_client(client, {paths[0]: False, paths[1]: True, paths[2]: True}, calls)
    results = verifier.verify_batch(SCENE, candidates)
    assert set(results) == {paths[0], paths[1]}
    assert calls == {"batch": 1, "single": 0}, calls
    loser = results[paths[0]]
    assert loser["accepted"] is False
    assert loser["status"] == "rejected"
    assert loser["fatal_mismatch"] is True
    assert loser["visual_checked"] is True
    assert results[paths[1]]["accepted"] is True


def test_verify_batch_all_rejected_returns_checked_verdicts(tmp_path):
    """All-rejected: one batch, every finalist keeps a checked verdict."""
    verifier, _context, client = _build_verifier(tmp_path)
    candidates = [_candidate(tmp_path, f"cand-{i}.png") for i in range(3)]
    paths = [c["path"] for c in candidates]
    calls = {"batch": 0, "single": 0}
    _stub_raw_client(client, {p: False for p in paths}, calls)
    results = verifier.verify_batch(SCENE, candidates)
    assert set(results) == set(paths)
    assert calls == {"batch": 1, "single": 0}, calls
    assert all(v["accepted"] is False and v["visual_checked"] is True
               for v in results.values())


def test_verify_batch_failure_falls_back_to_sequential(tmp_path):
    """A raising batch degrades to the old loop: same winner, N singles."""
    verifier, _context, client = _build_verifier(tmp_path)
    candidates = [_candidate(tmp_path, f"cand-{i}.png") for i in range(2)]
    paths = [c["path"] for c in candidates]
    calls = {"batch": 0, "single": 0}
    _stub_raw_client(client, {paths[0]: False, paths[1]: True}, calls)

    def boom(*args, **kwargs):
        calls["batch"] += 1
        raise RuntimeError("inbox timeout")

    client._client.analyze_images_batch = boom
    results = verifier.verify_batch(SCENE, candidates)
    assert set(results) == {paths[0], paths[1]}
    assert calls["batch"] == 1, calls  # 2 candidates = 1 safe batch, raises once
    assert calls["single"] == 2, calls  # reject then accept, early exit
    assert results[paths[1]]["accepted"] is True


def test_verify_batch_agrees_with_sequential_on_same_verdicts(tmp_path):
    """Same stub verdicts, batch vs sequential: identical winners/verdicts."""
    candidates = [_candidate(tmp_path, f"cand-{i}.png") for i in range(3)]
    paths = [c["path"] for c in candidates]
    accept_map = {paths[0]: False, paths[1]: False, paths[2]: True}

    verifier_b, _ctx_b, client_b = _build_verifier(tmp_path / "batch")
    calls_b = {"batch": 0, "single": 0}
    _stub_raw_client(client_b, accept_map, calls_b)
    batched = verifier_b.verify_batch(SCENE, candidates)

    verifier_s, _ctx_s, client_s = _build_verifier(tmp_path / "seq")
    calls_s = {"batch": 0, "single": 0}
    _stub_raw_client(client_s, accept_map, calls_s)
    client_s.analyze_images_batch = None  # no batch method: sequential path
    sequential = verifier_s.verify_batch(SCENE, candidates)

    assert calls_b == {"batch": 1, "single": 0}, calls_b  # 3 candidates = 1 batch
    assert calls_s == {"batch": 0, "single": 3}, calls_s
    assert set(batched) == set(sequential) == set(paths)
    for path in paths:
        assert batched[path]["accepted"] == sequential[path]["accepted"] == (path == paths[2])
        assert batched[path]["status"] == sequential[path]["status"]
        assert batched[path]["fatal_mismatch"] == sequential[path]["fatal_mismatch"]


# ---------------------------------------------------------------------------
# 5. Resolver: batched shortlist + prep hits spend zero vision calls
# ---------------------------------------------------------------------------

class _FakeVisionClient:
    """Counts single + batch calls; answers from a per-path verdict map."""

    def __init__(self, verdicts, calls):
        self._verdicts = verdicts
        self.calls = calls
        self.model = "test/vision"

    def _row(self, path, accept):
        row = dict(FULL_VERDICT if accept else REJECTED_VERDICT)
        row["candidate_id"] = str(path)
        row["relevance"] = 88.0 if accept else 10.0
        row["confidence"] = 90.0
        return row

    def analyze_image(self, path, prompt, system_prompt):
        self.calls["single"] += 1
        return self._row(path, self._verdicts.get(str(path), True))

    def analyze_images_batch(self, paths, prompt, system_prompt, candidate_ids=None):
        self.calls["batch"] += 1
        ids = list(candidate_ids or [])
        rows = {}
        for index, path in enumerate(paths):
            cid = ids[index] if index < len(ids) else str(path)
            rows[cid] = self._row(path, self._verdicts.get(str(path), True))
        return rows


class _FakeVisualContext:
    def __init__(self, client):
        self._client = client
        self.visual_required = False
        self.model = "test/vision"

    def get_client(self):
        return self._client


def _photo(tmp_path, name, seed=7):
    """A 320x240 noise JPEG: passes the resolver's image_is_safe gate
    (the 1x1 PNG the adapter tests use is rejected as unreadable)."""
    import random

    from PIL import Image

    random.seed(seed)
    img = Image.new("RGB", (320, 240))
    px = img.load()
    for y in range(240):
        for x in range(320):
            px[x, y] = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
    path = tmp_path / name
    img.save(path, "JPEG", quality=85)
    return path


def _resolver_candidates(tmp_path, names, accept_map):
    candidates = []
    for index, name in enumerate(names):
        path = _photo(tmp_path, name, seed=7 + index)
        candidates.append({
            "path": str(path),
            "candidate_id": str(path),
            "source": "web",
            "provider": "yandex",
            "title": "Grand Theft Auto VI official screenshot",
            "final_score": 80.0,
        })
    return candidates


def _rank(tmp_path, candidates, accept_map, with_batch=True):
    calls = {"single": 0, "batch": 0}
    client = _FakeVisionClient(accept_map, calls)
    if not with_batch:
        client.analyze_images_batch = None
    context = _FakeVisualContext(client)
    result = _rank_deferred_scene_shortlist(
        scene_index=0,
        scene_text="Grand Theft Auto VI returns to Vice City.",
        subject="Grand Theft Auto VI",
        global_topic="Grand Theft Auto VI",
        query="Grand Theft Auto VI Vice City screenshot",
        candidates=candidates,
        visual_context=context,
    )
    return result, calls


def test_resolver_batch_one_request_same_winner(tmp_path):
    """Reject, reject, accept: batch spends 1 call, sequential 3, same winner."""
    candidates = _resolver_candidates(tmp_path, ["a.jpg", "b.jpg", "c.jpg"], None)
    paths = [c["path"] for c in candidates]
    accept_map = {paths[0]: False, paths[1]: False, paths[2]: True}
    batched, calls_b = _rank(tmp_path, candidates, accept_map, with_batch=True)
    sequential, calls_s = _rank(tmp_path, candidates, accept_map, with_batch=False)
    assert calls_b == {"single": 0, "batch": 1}, calls_b
    assert calls_s == {"single": 3, "batch": 0}, calls_s
    assert batched is not None and sequential is not None
    assert batched["path"] == sequential["path"] == paths[2]


def test_resolver_batch_failure_falls_back_to_sequential(tmp_path):
    """A raising batch degrades to single verifies with the same winner."""
    candidates = _resolver_candidates(tmp_path, ["a.jpg", "b.jpg", "c.jpg"], None)
    paths = [c["path"] for c in candidates]
    accept_map = {paths[0]: False, paths[1]: True, paths[2]: True}
    calls = {"single": 0, "batch": 0}
    client = _FakeVisionClient(accept_map, calls)

    def boom(*args, **kwargs):
        calls["batch"] += 1
        raise RuntimeError("inbox timeout")

    client.analyze_images_batch = boom
    result = _rank_deferred_scene_shortlist(
        scene_index=0,
        scene_text="Grand Theft Auto VI returns to Vice City.",
        subject="Grand Theft Auto VI",
        global_topic="Grand Theft Auto VI",
        query="Grand Theft Auto VI Vice City screenshot",
        candidates=candidates,
        visual_context=_FakeVisualContext(client),
    )
    assert calls == {"single": 2, "batch": 1}, calls
    assert result is not None and result["path"] == paths[1]


def test_resolver_prep_hits_spend_zero_vision_calls(tmp_path):
    """An all-cached_prep shortlist is judged with no vision call at all."""
    candidates = _resolver_candidates(tmp_path, ["a.jpg", "b.jpg"], None)
    for candidate in candidates:
        candidate["prep_cache_hit"] = True
        candidate["reused_from_prep"] = True
        candidate["prep_key"] = "prep-key-" + Path(candidate["path"]).stem
    accept_map = {c["path"]: True for c in candidates}
    result, calls = _rank(tmp_path, candidates, accept_map, with_batch=True)
    assert calls == {"single": 0, "batch": 0}, calls
    assert result is not None
    assert result["path"] == candidates[0]["path"]
    assert result["visual_outcome"] == "pass"
    assert result["visual_checked"] is True
    decision = result.get("visual_decision") or {}
    assert decision.get("accepted") is True
    assert decision.get("visual_available") is True


def test_resolver_mixed_shortlist_batches_live_only(tmp_path):
    """Prep winner first + live alternates: 1 batch for the live rows only."""
    candidates = _resolver_candidates(tmp_path, ["a.jpg", "b.jpg", "c.jpg"], None)
    paths = [c["path"] for c in candidates]
    candidates[0]["prep_cache_hit"] = True
    candidates[0]["reused_from_prep"] = True
    accept_map = {paths[1]: False, paths[2]: True}
    result, calls = _rank(tmp_path, candidates, accept_map, with_batch=True)
    assert calls == {"single": 0, "batch": 1}, calls
    assert result is not None and result["path"] == paths[0]


def test_prep_hit_helpers(tmp_path):
    """_is_prep_cache_hit / synthetic prep row shape."""
    assert _is_prep_cache_hit({"prep_cache_hit": True}) is True
    assert _is_prep_cache_hit({"reused_from_prep": True}) is True
    assert _is_prep_cache_hit({"prep_cache_hit": False}) is False
    assert _is_prep_cache_hit({}) is False
    row = _prep_verified_model_row({"candidate_id": "cid-1", "path": "/tmp/x.jpg"})
    assert row["candidate_id"] == "cid-1"
    assert row["relevance"] >= 55 and row["confidence"] >= 50
    assert row["fatal_mismatch"] is False
    assert row["domain_match"] is True and row["scene_match"] is True
