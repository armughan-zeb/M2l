"""Batched image verification + external-agent vision concurrency.

verify_batch now judges a scene's image finalists in ONE vision request
(analyze_images_batch): one verdict row per candidate, each scored by the
full single-candidate verify() — same validation, same subject-level
acceptance. The old P4 shape (one analyze_image per finalist, in ranked
order, early exit on the first accept) survives only as the fallback when
the batch call fails or the client has no batch method. Every tried
candidate gets a real, full visual_checked verdict (identity/action/
context scores), so the D4 all-rejected waste (compare rows re-verified
singly, 28/43 in job 31f5bfec) cannot recur: the losers' verdicts are
already checked and cached.

- The external-agent vision semaphore is 4 (matching the context default),
  tunable via VIDZEO_EXTERNAL_AGENT_VISION_CONCURRENCY.
"""
import base64

import pytest

from services.pipeline_v2_media_adapter import build_qwen_verifier
from services.visual_intelligence.context import (
    VisualDecisionContext,
    external_agent_vision_concurrency,
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


def _build_verifier(tmp_path, config=None):
    config = dict(config or {})
    config.setdefault("llm_provider", "external_agent")
    config.setdefault("openrouter_api_key", "external-agent:local-user")
    verifier, context = build_qwen_verifier(config, [SCENE])
    assert hasattr(verifier, "verify_batch"), "verifier must expose verify_batch"
    # Hermetic vision cache: _ContextClient._run consults the context's
    # SQLite cache, keyed by file content hash — the same 1x1 PNGs would
    # otherwise hit entries stored by an earlier test run and the stubbed
    # client would never be called.
    from services.visual_intelligence.cache import VisualDecisionCache
    context.cache = VisualDecisionCache(str(tmp_path / "vision-cache.sqlite3"))
    # Force client construction, then stub the raw vision client: no network.
    client = context.get_client()
    assert client is not None
    calls = {"compare": 0, "analyze": 0, "batch": 0}

    def fake_compare(image_paths, _prompt):
        calls["compare"] += 1
        raise AssertionError("P4: compare_images must never be called")

    def fake_analyze(image_path, _prompt, _system):
        calls["analyze"] += 1
        verdict = dict(FULL_VERDICT)
        verdict["candidate_id"] = str(image_path)
        return verdict

    def fake_batch(image_paths, _prompt, _system, candidate_ids=None):
        calls["batch"] += 1
        ids = list(candidate_ids or [])
        rows = {}
        for index, path in enumerate(image_paths):
            cid = ids[index] if index < len(ids) else str(path)
            verdict = dict(FULL_VERDICT)
            verdict["candidate_id"] = str(path)
            rows[cid] = verdict
        return rows

    client._client.compare_images = fake_compare
    client._client.analyze_image = fake_analyze
    client._client.analyze_clip_frames = fake_analyze
    client._client.analyze_images_batch = fake_batch
    return verifier, context, calls


def _batch_from_verdict_fn(client, calls, verdict_for):
    """Stub analyze_images_batch from a per-path verdict function."""

    def fake_batch(image_paths, _prompt, _system, candidate_ids=None):
        calls["batch"] += 1
        ids = list(candidate_ids or [])
        rows = {}
        for index, path in enumerate(image_paths):
            cid = ids[index] if index < len(ids) else str(path)
            verdict = dict(verdict_for(path))
            verdict["candidate_id"] = str(path)
            rows[cid] = verdict
        return rows

    client._client.analyze_images_batch = fake_batch


def test_batched_verify_first_accept_wins_with_batches(tmp_path):
    """Common case: the top-ranked finalist passes, so verify_batch spends
    exactly one batched vision request and never touches the rest."""
    verifier, _context, calls = _build_verifier(tmp_path)
    candidates = [_candidate(tmp_path, f"cand-{i}.png") for i in range(3)]
    results = verifier.verify_batch(SCENE, candidates)
    assert set(results) == {candidates[0]["path"]}
    assert calls["compare"] == 0, calls
    assert calls["analyze"] == 0, calls
    assert calls["batch"] == 1, calls  # 3 candidates = 1 batch (cap 16)
    winner = results[candidates[0]["path"]]
    assert winner["accepted"] is True
    assert winner["visual_checked"] is True
    assert winner["compare_selected"] is True


def test_batched_verify_reject_then_accept_keeps_real_loser_verdict(tmp_path):
    """First finalist rejected, second accepted: one batch request, and
    the loser keeps its real checked verdict (no re-verification later)."""
    verifier, context, calls = _build_verifier(tmp_path)
    client = context.get_client()

    def reject_first(image_path, _prompt, _system):
        calls["analyze"] += 1
        verdict = dict(REJECTED_VERDICT if "cand-0" in str(image_path) else FULL_VERDICT)
        verdict["candidate_id"] = str(image_path)
        return verdict

    client._client.analyze_image = reject_first
    _batch_from_verdict_fn(client, calls, lambda path: (
        REJECTED_VERDICT if "cand-0" in str(path) else FULL_VERDICT))
    candidates = [_candidate(tmp_path, f"cand-{i}.png") for i in range(3)]
    results = verifier.verify_batch(SCENE, candidates)
    assert set(results) == {candidates[0]["path"], candidates[1]["path"]}
    assert calls["compare"] == 0, calls
    assert calls["analyze"] == 0, calls
    assert calls["batch"] == 1, calls  # 3 candidates = 1 batch (cap 16)
    loser = results[candidates[0]["path"]]
    assert loser["accepted"] is False
    assert loser["status"] == "rejected"
    assert loser["fatal_mismatch"] is True
    assert loser["visual_checked"] is True
    winner = results[candidates[1]["path"]]
    assert winner["accepted"] is True
    assert winner["compare_selected"] is True


def test_batched_verify_all_rejected_returns_checked_verdicts(tmp_path):
    """All-rejected (job 31f5bfec): one batch request; every finalist gets
    one real checked verdict and is returned — the gate never re-verifies
    a loser, so the old D4 waste (28/43 re-verifications) cannot recur."""
    verifier, context, calls = _build_verifier(tmp_path)
    client = context.get_client()

    def all_rejected(image_path, _prompt, _system):
        calls["analyze"] += 1
        verdict = dict(REJECTED_VERDICT)
        verdict["candidate_id"] = str(image_path)
        return verdict

    client._client.analyze_image = all_rejected
    _batch_from_verdict_fn(client, calls, lambda path: REJECTED_VERDICT)
    candidates = [_candidate(tmp_path, f"cand-{i}.png") for i in range(3)]
    results = verifier.verify_batch(SCENE, candidates)
    assert set(results) == {c["path"] for c in candidates}
    assert calls["compare"] == 0, calls
    assert calls["analyze"] == 0, calls
    assert calls["batch"] == 1, calls  # 3 candidates = 1 batch (cap 16)
    assert all(v["accepted"] is False and v["visual_checked"] is True
               for v in results.values())


def test_batched_verify_falls_back_to_sequential_when_batch_raises(tmp_path):
    """A raising batch is a per-scene fallback, not an abort: verify_batch
    tries each finalist once with the old single verifies and returns what
    verify() recorded, with no compare call."""
    verifier, context, calls = _build_verifier(tmp_path)
    client = context.get_client()

    def boom(_path, _prompt, _system):
        calls["analyze"] += 1
        raise RuntimeError("inbox timeout")

    def boom_batch(*args, **kwargs):
        calls["batch"] += 1
        raise RuntimeError("inbox timeout")

    client._client.analyze_image = boom
    client._client.analyze_images_batch = boom_batch
    candidates = [_candidate(tmp_path, f"cand-{i}.png") for i in range(3)]
    results = verifier.verify_batch(SCENE, candidates)
    assert set(results) == {c["path"] for c in candidates}
    assert calls["compare"] == 0, calls
    assert calls["batch"] == 1, calls  # 3 candidates = 1 batch, raises once
    assert calls["analyze"] == 3, calls
    assert all(v["accepted"] is False and v["status"] == "schema_invalid"
               for v in results.values())


def test_batched_gate_skips_video_finalists(tmp_path):
    verifier, _context, calls = _build_verifier(tmp_path)
    candidates = [_candidate(tmp_path, "cand-0.png"), _candidate(tmp_path, "cand-1.png")]
    clip = dict(candidates[1])
    clip["path"] = str(tmp_path / "clip.mp4")
    (tmp_path / "clip.mp4").write_bytes(b"fake video bytes")
    clip["media_type"] = "video"
    assert verifier.verify_batch(SCENE, [candidates[0], clip]) == {}
    assert calls == {"compare": 0, "analyze": 0, "batch": 0}


def test_batched_gate_needs_at_least_two_finalists(tmp_path):
    verifier, _context, calls = _build_verifier(tmp_path)
    assert verifier.verify_batch(SCENE, [_candidate(tmp_path, "only.png")]) == {}
    assert calls == {"compare": 0, "analyze": 0, "batch": 0}


def test_external_agent_vision_semaphore_is_eight_by_default(tmp_path, monkeypatch):
    # Test-20 fan-out (Nov 21, 2026): the default rose 4 -> 8 so the
    # YT-branch verify pool can file 8 concurrent inbox requests and
    # the pool scales to its ceiling. The from_config fallback without
    # an explicit concurrency is still 4 (see the wall test below).
    monkeypatch.delenv("VIDZEO_EXTERNAL_AGENT_VISION_CONCURRENCY", raising=False)
    assert external_agent_vision_concurrency() == 8
    config = {
        "llm_provider": "external_agent",
        "openrouter_api_key": "external-agent:local-user",
        "image_source": "Mixed Media",
    }
    _verifier, context = build_qwen_verifier(config, [SCENE])
    assert context.max_concurrent == 8
    assert context._semaphore is not None


def test_external_agent_vision_semaphore_is_env_tunable(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDZEO_EXTERNAL_AGENT_VISION_CONCURRENCY", "3")
    assert external_agent_vision_concurrency() == 3
    config = {
        "llm_provider": "external_agent",
        "openrouter_api_key": "external-agent:local-user",
        "image_source": "Mixed Media",
    }
    _verifier, context = build_qwen_verifier(config, [SCENE])
    assert context.max_concurrent == 3
    monkeypatch.setenv("VIDZEO_EXTERNAL_AGENT_VISION_CONCURRENCY", "99")
    assert external_agent_vision_concurrency() == 8, "clamped to the inbox concurrency"
    # 6 reaches the adapter's mixed-media branch too: the old hardcoded 2
    # would have capped it regardless of the env value.
    monkeypatch.setenv("VIDZEO_EXTERNAL_AGENT_VISION_CONCURRENCY", "6")
    _verifier, context = build_qwen_verifier(config, [SCENE])
    assert context.max_concurrent == 6


def test_external_agent_vision_wait_follows_the_agent_wall(monkeypatch):
    monkeypatch.delenv("VIDZEO_AGENT_MIN_WALL_SECONDS", raising=False)
    context = VisualDecisionContext.from_config(
        {"llm_provider": "external_agent",
         "openrouter_api_key": "external-agent:local-user"}, 1)
    assert context.call_timeout_seconds == 3600.0
    assert context.max_concurrent == 4
    monkeypatch.setenv("VIDZEO_AGENT_MIN_WALL_SECONDS", "600")
    context = VisualDecisionContext.from_config(
        {"llm_provider": "external_agent",
         "openrouter_api_key": "external-agent:local-user"}, 1)
    assert context.call_timeout_seconds == 600.0
