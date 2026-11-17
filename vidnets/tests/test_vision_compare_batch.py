"""Vision compare batching + external-agent vision concurrency (speedup item c).

- verify_batch judges up to 4 image finalists in ONE compare_images request,
  then runs the full single-candidate verify() only for the compare winner.
  Compare rows carry relevance/quality/fatal_mismatch/watermark/
  scene_description, but NOT the identity/action/context scores the V2
  verdict schema requires, so losers get compare-only verdicts and are
  never given invented scores.
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

COMPARE = {
    "selected_index": 1,
    "confidence": 88.0,
    "reason": "candidate 1 shows the highway traffic",
    "acceptable": True,
    "candidates": [
        {"relevance": 40, "quality": 60, "fatal_mismatch": False,
         "watermark": False, "scene_description": "a quiet road"},
        {"relevance": 92, "quality": 85, "fatal_mismatch": False,
         "watermark": False, "scene_description": "highway traffic"},
        {"relevance": 70, "quality": 70, "fatal_mismatch": True,
         "watermark": False, "scene_description": "a different bridge"},
    ],
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
    calls = {"compare": 0, "analyze": 0}

    def fake_compare(image_paths, _prompt):
        calls["compare"] += 1
        assert len(image_paths) <= 4
        return dict(COMPARE)

    def fake_analyze(image_path, _prompt, _system):
        calls["analyze"] += 1
        verdict = dict(FULL_VERDICT)
        verdict["candidate_id"] = str(image_path)
        return verdict

    client._client.compare_images = fake_compare
    client._client.analyze_image = fake_analyze
    client._client.analyze_clip_frames = fake_analyze
    return verifier, context, calls


def test_batched_gate_keeps_all_rejected_hard_fatals(tmp_path):
    """All-rejected compare (job 31f5bfec): the compare judged every
    finalist and rejected them, so verify_batch used to return {} and the
    gate re-verified each loser singly — 4 extra vision requests per
    scene. The hard-rejected rows are now returned as checked
    compare_rejected verdicts; non-fatal rows are still omitted so they
    fall back to per-candidate verify(). No full verify() runs here."""
    verifier, context, calls = _build_verifier(tmp_path)
    client = context.get_client()

    def all_rejected(_paths, _prompt):
        calls["compare"] += 1
        return {
            "selected_index": -1,
            "confidence": 80.0,
            "reason": "all four candidates are GTA V frames, no GTA VI content",
            "acceptable": False,
            "candidates": [
                {"relevance": 25, "quality": 60, "fatal_mismatch": True,
                 "watermark": False, "scene_description": "GTA V jet over water"},
                {"relevance": 30, "quality": 65, "fatal_mismatch": False,
                 "watermark": True, "scene_description": "city skyline, watermarked"},
                {"relevance": 55, "quality": 70, "fatal_mismatch": False,
                 "watermark": False, "scene_description": "a night street"},
            ],
        }

    client._client.compare_images = all_rejected
    candidates = [_candidate(tmp_path, f"cand-{i}.png") for i in range(3)]
    results = verifier.verify_batch(SCENE, candidates)
    assert calls["compare"] == 1, calls
    assert calls["analyze"] == 0, "no winner to fully verify, no single re-check"
    # Only the hard-rejected rows come back, as checked verdicts.
    assert set(results) == {candidates[0]["path"], candidates[1]["path"]}
    fatal = results[candidates[0]["path"]]
    assert fatal["accepted"] is False
    assert fatal["status"] == "compare_rejected"
    assert fatal["fatal_mismatch"] is True
    assert fatal["visual_checked"] is True
    assert fatal["visual_available"] is True
    assert fatal["schema_valid"] is False  # no invented identity scores
    watermarked = results[candidates[1]["path"]]
    assert watermarked["status"] == "compare_rejected"
    assert watermarked["watermark"] is True
    assert watermarked["visual_checked"] is True


def test_batched_gate_all_rejected_without_hard_flags_falls_back(tmp_path):
    """All-rejected compare with no confident hard rejection: nothing is
    trusted, verify_batch returns {} and the caller falls back to
    per-candidate verify() exactly as before."""
    verifier, context, calls = _build_verifier(tmp_path)
    client = context.get_client()

    def all_clean_rejected(_paths, _prompt):
        calls["compare"] += 1
        return {
            "selected_index": -1,
            "confidence": 60.0,
            "reason": "none of the candidates shows the required subject",
            "acceptable": False,
            "candidates": [
                {"relevance": 40, "quality": 60, "fatal_mismatch": False,
                 "watermark": False, "scene_description": "a quiet road"},
                {"relevance": 45, "quality": 62, "fatal_mismatch": False,
                 "watermark": False, "scene_description": "another road"},
            ],
        }

    client._client.compare_images = all_clean_rejected
    candidates = [_candidate(tmp_path, f"cand-{i}.png") for i in range(2)]
    assert verifier.verify_batch(SCENE, candidates) == {}
    assert calls["compare"] == 1, calls
    assert calls["analyze"] == 0


def test_batched_gate_all_rows_hard_rejected_returns_all(tmp_path):
    """Selected index is valid but every row is hard-rejected: previously
    verify_batch returned {} (not usable) and the gate spent one single
    verify per finalist. Now all rows come back as checked rejects."""
    verifier, context, calls = _build_verifier(tmp_path)
    client = context.get_client()

    def all_fatal(_paths, _prompt):
        calls["compare"] += 1
        return {
            "selected_index": 0,
            "confidence": 85.0,
            "reason": "every candidate is the wrong game",
            "acceptable": False,
            "candidates": [
                {"relevance": 20, "quality": 60, "fatal_mismatch": True,
                 "watermark": False, "scene_description": "GTA V jet"},
                {"relevance": 22, "quality": 61, "fatal_mismatch": True,
                 "watermark": False, "scene_description": "GTA V skyline"},
            ],
        }

    client._client.compare_images = all_fatal
    candidates = [_candidate(tmp_path, f"cand-{i}.png") for i in range(2)]
    results = verifier.verify_batch(SCENE, candidates)
    assert calls["compare"] == 1, calls
    assert calls["analyze"] == 0
    assert set(results) == {c["path"] for c in candidates}
    assert all(v["status"] == "compare_rejected" and v["visual_checked"] is True
               for v in results.values())


def test_batched_gate_judges_four_finalists_in_one_request(tmp_path):
    verifier, _context, calls = _build_verifier(tmp_path)
    candidates = [_candidate(tmp_path, f"cand-{i}.png") for i in range(3)]
    results = verifier.verify_batch(SCENE, candidates)
    assert set(results) == {c["path"] for c in candidates}
    # One compare request for all three, one full verify for the winner only.
    assert calls["compare"] == 1, calls
    assert calls["analyze"] == 1, calls
    winner = results[candidates[1]["path"]]
    assert winner["accepted"] is True
    assert winner["visual_checked"] is True
    assert winner["compare_selected"] is True
    loser = results[candidates[0]["path"]]
    assert loser["accepted"] is False
    assert loser["status"] == "compare_not_selected"
    assert loser["visual_checked"] is False
    assert loser["relevance"] == 40
    assert loser["final_score_source"] == "compare_relevance"
    fatal = results[candidates[2]["path"]]
    assert fatal["status"] == "rejected"
    assert fatal["fatal_mismatch"] is True
    assert fatal["visual_checked"] is False


def test_batched_gate_returns_empty_when_compare_fails(tmp_path):
    verifier, context, calls = _build_verifier(tmp_path)
    client = context.get_client()

    def boom(_paths, _prompt):
        calls["compare"] += 1
        raise RuntimeError("inbox timeout")

    client._client.compare_images = boom
    candidates = [_candidate(tmp_path, f"cand-{i}.png") for i in range(3)]
    assert verifier.verify_batch(SCENE, candidates) == {}
    assert calls["analyze"] == 0, "a failed compare must not spend a full verify"


def test_batched_gate_skips_video_finalists(tmp_path):
    verifier, _context, calls = _build_verifier(tmp_path)
    candidates = [_candidate(tmp_path, "cand-0.png"), _candidate(tmp_path, "cand-1.png")]
    clip = dict(candidates[1])
    clip["path"] = str(tmp_path / "clip.mp4")
    (tmp_path / "clip.mp4").write_bytes(b"fake video bytes")
    clip["media_type"] = "video"
    assert verifier.verify_batch(SCENE, [candidates[0], clip]) == {}
    assert calls == {"compare": 0, "analyze": 0}


def test_batched_gate_needs_at_least_two_finalists(tmp_path):
    verifier, _context, calls = _build_verifier(tmp_path)
    assert verifier.verify_batch(SCENE, [_candidate(tmp_path, "only.png")]) == {}
    assert calls == {"compare": 0, "analyze": 0}


def test_external_agent_vision_semaphore_is_four_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("VIDZEO_EXTERNAL_AGENT_VISION_CONCURRENCY", raising=False)
    assert external_agent_vision_concurrency() == 4
    config = {
        "llm_provider": "external_agent",
        "openrouter_api_key": "external-agent:local-user",
        "image_source": "Mixed Media",
    }
    _verifier, context = build_qwen_verifier(config, [SCENE])
    assert context.max_concurrent == 4
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
