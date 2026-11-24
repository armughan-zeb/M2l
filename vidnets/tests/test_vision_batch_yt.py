"""Batched YouTube-lane vision: one vision call per scene, not per candidate.

A scene's top candidates (default 4 x 2 frames) are judged in a single
vision request whose response carries one verdict per candidate
(candidate_id + verdict + reason + score). The caller picks the best
accepted candidate from the batch. Batching changes HOW verdicts arrive,
never the acceptance rule: subject-level acceptance still applies per
candidate, a low score only ranks, and hard rejects stay hard.

Covered here:
- the batch response_format schema validates through the server's own
  ``_json_schema_error`` (the same validator
  ``scripts/resident-inference-worker/validate_answer.py`` uses), valid
  batches pass and malformed batches fail cleanly;
- ``analyze_clip_frames_batch`` sends one request for all candidates and
  returns one normalised verdict per candidate;
- ``analyze_clips_batch`` produces the same ClipAnalysis fields as the
  sequential path and falls back to it when the batch fails;
- ``_batched_visual_verdicts`` returns the same accept/reject tuples as
  sequential ``_visual_verdict_scored`` for the same scores, and the
  ``VIDZEO_YT_VISION_BATCH=0`` kill-switch restores sequential vision.
"""
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from services.job_context import job_scope
from services.visual_intelligence import clip_pipeline as clip_pipeline_module
from services.visual_intelligence.clip_pipeline import ClipIntelligencePipeline
from services.visual_intelligence.prompts import (
    build_clip_batch_prompt,
    CLIP_BATCH_RESPONSE_FORMAT,
    CLIP_BATCH_SYSTEM_PROMPT,
)
from services.visual_intelligence.vision_client import (
    QwenVisionClient,
    VisionClientError,
    _normalise_clip_response,
)
from services.openrouter_llm import _json_schema_error
import services.youtube_pipeline_v2 as ytv2

REPO_ROOT = Path(__file__).resolve().parents[2]
VALIDATE_ANSWER = REPO_ROOT / "scripts" / "resident-inference-worker" / "validate_answer.py"
BATCH_SCHEMA = CLIP_BATCH_RESPONSE_FORMAT["json_schema"]["schema"]


def _batch_row(candidate_id, verdict="accept", score=80.0, **patch):
    row = {
        "candidate_id": candidate_id,
        "verdict": verdict,
        "reason": "shows the documentary subject",
        "score": score,
        "story_relevance": score,
        "confidence": 85.0,
        "fatal_mismatch": False,
        "talking_head": False,
    }
    row.update(patch)
    return row


def _batch_payload(rows):
    return {"candidates": rows}


def _frame(tmp_path, name, color=(12, 34, 56)):
    path = tmp_path / name
    Image.new("RGB", (64, 48), color).save(path, "JPEG", quality=70)
    return str(path)


# ---------------------------------------------------------------------------
# Schema validation (server's own validator == validate_answer.py's)
# ---------------------------------------------------------------------------

def test_batch_schema_accepts_valid_response():
    assert _json_schema_error(
        _batch_payload([_batch_row("clipA"), _batch_row("clipB", "reject", 10.0,
                                                        reason="wrong subject",
                                                        fatal_mismatch=True)]),
        BATCH_SCHEMA,
    ) == ""


def test_batch_schema_rejects_missing_candidate_entry():
    assert _json_schema_error({"candidates": []}, BATCH_SCHEMA) != ""


def test_batch_schema_accepts_eight_candidate_batch():
    """REGRESSION (schema cap, 2026-11-20): an 8-candidate batch validates.

    Live request 4a7cc247698e54f5485fe34f5f8a4ce9 carried 8 verdicts
    (4 REJECT + 4 ACCEPT) and failed submission with 400 "too many items"
    because the schema capped candidates at maxItems 6 while the batch
    cap is 16 candidates/request (a scene has up to 8). The cap is now
    16, matching the batch cap, so a full scene validates in one request.
    """
    rows = [_batch_row(f"clip{i}", "reject", 10.0, reason="wrong subject",
                       fatal_mismatch=True) for i in range(4)]
    rows += [_batch_row(f"clip{i}") for i in range(4, 8)]
    assert _json_schema_error(_batch_payload(rows), BATCH_SCHEMA) == ""


def test_batch_schema_cap_is_sixteen():
    """The candidate cap matches the 16-image batch cap: 16 pass, 17 fail."""
    assert BATCH_SCHEMA["properties"]["candidates"]["maxItems"] == 16
    assert _json_schema_error(
        _batch_payload([_batch_row(f"clip{i}") for i in range(16)]), BATCH_SCHEMA
    ) == ""
    assert _json_schema_error(
        _batch_payload([_batch_row(f"clip{i}") for i in range(17)]), BATCH_SCHEMA
    ) != ""


def test_batch_schema_rejects_unknown_verdict():
    assert _json_schema_error(
        _batch_payload([_batch_row("clipA", verdict="maybe")]), BATCH_SCHEMA
    ) != ""


def test_batch_schema_rejects_missing_reason():
    row = _batch_row("clipA")
    del row["reason"]
    assert _json_schema_error(_batch_payload([row]), BATCH_SCHEMA) != ""


def test_batch_schema_rejects_score_out_of_range():
    assert _json_schema_error(
        _batch_payload([_batch_row("clipA", score=140.0)]), BATCH_SCHEMA
    ) != ""


def test_batch_schema_rejects_wrong_types():
    assert _json_schema_error(
        _batch_payload([_batch_row("clipA", fatal_mismatch="yes")]), BATCH_SCHEMA
    ) != ""


def test_batch_schema_rejects_stray_fields():
    row = _batch_row("clipA")
    row["extra_field"] = "drift"
    assert _json_schema_error(_batch_payload([row]), BATCH_SCHEMA) != ""


def test_validate_answer_accepts_valid_batch_and_rejects_malformed(tmp_path):
    """validate_answer.py is the worker-side gate: a valid batch delivers,
    a malformed batch must not."""
    request = {
        "id": "batch-test",
        "kind": "vision",
        "operation": "visual_review",
        "response_format": CLIP_BATCH_RESPONSE_FORMAT,
        "messages": [{"role": "user", "content": "judge the candidates"}],
    }
    request_path = tmp_path / "req.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    good_path = tmp_path / "good.json"
    good_path.write_text(json.dumps(_batch_payload([_batch_row("clipA"), _batch_row("clipB")])), encoding="utf-8")
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps(_batch_payload([_batch_row("clipA", verdict="maybe")])), encoding="utf-8")
    env = dict(os.environ)
    env["VIDZEO_RENDER_GATE"] = "off"
    good = subprocess.run(
        [sys.executable, str(VALIDATE_ANSWER), str(good_path), str(request_path)],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT),
    )
    assert good.returncode == 0, good.stdout + good.stderr
    assert good.stdout.strip() == "valid"
    bad = subprocess.run(
        [sys.executable, str(VALIDATE_ANSWER), str(bad_path), str(request_path)],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT),
    )
    assert bad.returncode == 1, bad.stdout + bad.stderr
    assert "schema" in bad.stdout.lower() or "verdict" in bad.stdout.lower()


def test_validate_answer_accepts_eight_candidate_batch(tmp_path):
    """REGRESSION (schema cap, 2026-11-20): the worker-side gate that
    returned the live 400 "too many items" for 8 verdicts now accepts
    the same 4 REJECT + 4 ACCEPT batch through validate_answer.py."""
    request = {
        "id": "batch-test-8",
        "kind": "vision",
        "operation": "visual_review",
        "response_format": CLIP_BATCH_RESPONSE_FORMAT,
        "messages": [{"role": "user", "content": "judge the candidates"}],
    }
    request_path = tmp_path / "req8.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    rows = [_batch_row(f"clip{i}", "reject", 10.0, reason="wrong subject",
                       fatal_mismatch=True) for i in range(4)]
    rows += [_batch_row(f"clip{i}") for i in range(4, 8)]
    answer_path = tmp_path / "good8.json"
    answer_path.write_text(json.dumps(_batch_payload(rows)), encoding="utf-8")
    env = dict(os.environ)
    env["VIDZEO_RENDER_GATE"] = "off"
    good = subprocess.run(
        [sys.executable, str(VALIDATE_ANSWER), str(answer_path), str(request_path)],
        capture_output=True, text=True, env=env, cwd=str(REPO_ROOT),
    )
    assert good.returncode == 0, good.stdout + good.stderr
    assert good.stdout.strip() == "valid"


# ---------------------------------------------------------------------------
# Client: one request for all of a scene's candidates
# ---------------------------------------------------------------------------

def test_batch_sends_one_safe_request_for_two_candidates(tmp_path, monkeypatch):
    """VISION LANES: 2 candidates x 2 frames each = 4 images in ONE request.

    Two frames (first + last sample of the probe window) bracket the
    transcript-matched moment, so the verdict sees the subject persist
    across the cut. 4 candidates x 2 frames = 8 images is the operating
    batch size; 16 images stays the hard transport ceiling.
    """
    frames_a = [_frame(tmp_path, "a1.jpg"), _frame(tmp_path, "a2.jpg", (200, 30, 30))]
    frames_b = [_frame(tmp_path, "b1.jpg", (30, 200, 30)), _frame(tmp_path, "b2.jpg", (30, 30, 200))]
    payload = _batch_payload([_batch_row("clipA", score=82.0), _batch_row("clipB", "reject", 12.0,
                                          reason="wrong subject", fatal_mismatch=True)])
    calls = []

    def fake_post_json(self, content, **kwargs):
        calls.append((content, kwargs))
        return json.dumps(payload)

    monkeypatch.setattr(QwenVisionClient, "_post_json", fake_post_json)
    with job_scope({"llm_cache_dir": str(tmp_path / "cache")}):
        client = QwenVisionClient("account-a", model="qwen/test")
        out = client.analyze_clip_frames_batch(
            [{"candidate_id": "clipA", "frame_paths": frames_a},
             {"candidate_id": "clipB", "frame_paths": frames_b}],
            user_prompt=build_clip_batch_prompt("t", "n", "s", ["clipA", "clipB"]),
            system_prompt=CLIP_BATCH_SYSTEM_PROMPT,
        )
    assert len(calls) == 1, "2 candidates fit in one safe batch"
    content, kwargs = calls[0]
    # The filed request carries the batch json_schema contract.
    assert kwargs.get("response_format") == CLIP_BATCH_RESPONSE_FORMAT
    text_parts = [c.get("text", "") for c in content if c.get("type") == "text"]
    assert any("Candidate clipA" in t for t in text_parts)
    assert any("Candidate clipB" in t for t in text_parts)
    image_parts = [c for c in content if c.get("type") == "image_url"]
    assert len(image_parts) == 4, "VISION LANES: 2 frames per candidate, 4 images total"
    assert set(out) == {"clipA", "clipB"}
    assert out["clipA"]["batch_verdict"] == "accept"
    assert out["clipA"]["batch_score"] == 82.0
    assert out["clipA"]["story_relevance"] == 82.0
    assert out["clipB"]["batch_verdict"] == "reject"
    assert out["clipB"]["fatal_mismatch"] is True


def test_batch_never_sends_more_than_sixteen_images(tmp_path, monkeypatch):
    """REGRESSION (max-parallel, 2026-11-20): a batch carries at most 16 images.

    The cap was raised from 2 to 16 after the live test proved 16 images in
    one request judges fine. Vision lanes (2026-11-22) set the OPERATING
    size to 4 candidates x 2 frames (8 images) per request, while 16 stays
    the hard transport ceiling. 17 candidates become 5 requests
    (4 + 4 + 4 + 4 + 1), never 1 request of 17, and never more than 16
    images in one request.
    """
    frames = [[_frame(tmp_path, f"c{i}f1.jpg")] for i in range(17)]
    payload = _batch_payload([_batch_row(f"clip{i}", score=80.0) for i in range(17)])
    calls = []

    def fake_post_json(self, content, **kwargs):
        images = [c for c in content if c.get("type") == "image_url"]
        assert len(images) <= 16, "max batch: at most 16 images per request"
        calls.append((content, kwargs))
        return json.dumps(payload)

    monkeypatch.setattr(QwenVisionClient, "_post_json", fake_post_json)
    with job_scope({"llm_cache_dir": str(tmp_path / "cache")}):
        client = QwenVisionClient("account-a", model="qwen/test")
        out = client.analyze_clip_frames_batch(
            [{"candidate_id": f"clip{i}", "frame_paths": frames[i]} for i in range(17)],
            user_prompt=build_clip_batch_prompt("t", "n", "s", [f"clip{i}" for i in range(17)]),
            system_prompt=CLIP_BATCH_SYSTEM_PROMPT,
        )
    assert len(calls) == 5, "17 candidates split into 5 batches (4 + 4 + 4 + 4 + 1)"
    for content, _kwargs in calls:
        image_parts = [c for c in content if c.get("type") == "image_url"]
        assert len(image_parts) <= 16, "at most 16 images per request"
    assert set(out) == {f"clip{i}" for i in range(17)}
    assert all(out[f"clip{i}"]["batch_verdict"] == "accept" for i in range(17))


def test_batch_works_in_fast_mode_with_full_budget(tmp_path, monkeypatch):
    """REGRESSION (test 13): the YT batch must work when fast_mode is True.

    The adapter sets fast_mode=True for every mixed-media job. The YT
    batch has no explicit fast_mode guard, but _post_json caps max_tokens
    to 900 in fast mode — a 4-candidate batch (budget 2400) is truncated
    mid-list and unparseable, so the batch fails silently and falls back
    to sequential singles. full_budget=True keeps the batch's full token
    budget in fast mode (one attempt, no retry — the fast contract).
    """
    frames_a = [_frame(tmp_path, "a1.jpg"), _frame(tmp_path, "a2.jpg", (200, 30, 30))]
    frames_b = [_frame(tmp_path, "b1.jpg", (30, 200, 30)), _frame(tmp_path, "b2.jpg", (30, 30, 200))]
    payload = _batch_payload([_batch_row("clipA", score=82.0), _batch_row("clipB", "reject", 12.0,
                                          reason="wrong subject", fatal_mismatch=True)])
    calls = []

    def fake_post_json(self, content, **kwargs):
        calls.append((content, kwargs))
        return json.dumps(payload)

    monkeypatch.setattr(QwenVisionClient, "_post_json", fake_post_json)
    with job_scope({"llm_cache_dir": str(tmp_path / "cache")}):
        client = QwenVisionClient("account-a", model="qwen/test", fast_mode=True)
        assert client.fast_mode is True
        out = client.analyze_clip_frames_batch(
            [{"candidate_id": "clipA", "frame_paths": frames_a},
             {"candidate_id": "clipB", "frame_paths": frames_b}],
            user_prompt=build_clip_batch_prompt("t", "n", "s", ["clipA", "clipB"]),
            system_prompt=CLIP_BATCH_SYSTEM_PROMPT,
        )
    assert len(calls) == 1, "one vision request for the whole scene, even in fast mode"
    assert calls[0][1].get("full_budget") is True, (
        "the YT batch must carry its full token budget in fast mode"
    )
    assert set(out) == {"clipA", "clipB"}
    assert out["clipA"]["batch_verdict"] == "accept"
    assert out["clipB"]["batch_verdict"] == "reject"


def test_batch_ignores_unknown_ids_and_marks_missing_unavailable(tmp_path, monkeypatch):
    payload = _batch_payload([
        _batch_row("clipA", score=70.0),
        _batch_row("clipGhost", score=99.0),  # never requested: ignored
    ])
    monkeypatch.setattr(
        QwenVisionClient, "_post_json",
        lambda self, content, **kwargs: json.dumps(payload),
    )
    with job_scope({"llm_cache_dir": str(tmp_path / "cache")}):
        client = QwenVisionClient("account-a", model="qwen/test")
        out = client.analyze_clip_frames_batch(
            [{"candidate_id": "clipA", "frame_paths": [_frame(tmp_path, "a1.jpg")]},
             {"candidate_id": "clipB", "frame_paths": [_frame(tmp_path, "b1.jpg")]}],
            user_prompt="judge",
            system_prompt="return JSON",
        )
    assert set(out) == {"clipA"}, "ghost ignored, unjudged clipB absent (caller fail-opens it)"


def test_batch_non_json_raises_for_sequential_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(
        QwenVisionClient, "_post_json",
        lambda self, content, **kwargs: "not json at all",
    )
    with job_scope({"llm_cache_dir": str(tmp_path / "cache")}):
        client = QwenVisionClient("account-a", model="qwen/test")
        with pytest.raises(VisionClientError):
            client.analyze_clip_frames_batch(
                [{"candidate_id": "clipA", "frame_paths": [_frame(tmp_path, "a1.jpg")]}],
                user_prompt="judge",
                system_prompt="return JSON",
            )


def test_batch_prompt_names_candidates_and_contract():
    prompt = build_clip_batch_prompt("GTA VI", "Vice City returns", "GTA VI", ["clipA", "clipB"])
    assert "clipA" in prompt and "clipB" in prompt
    assert "candidate_id" in prompt and "accept|reject" in prompt


# ---------------------------------------------------------------------------
# Clip pipeline: batched analyses equal sequential analyses
# ---------------------------------------------------------------------------

def _stubbed_pipeline(tmp_path, monkeypatch, batch_rows=None, batch_error=None):
    """Pipeline whose frame extraction is stubbed (real PNGs, no ffmpeg)
    and whose vision client answers from canned batch rows."""
    frame_a = _frame(tmp_path, "fa.jpg")
    frame_b = _frame(tmp_path, "fb.jpg", (200, 30, 30))

    def fake_extract(path, max_frames=8):
        return ([frame_a, frame_b], 10.0)

    def fake_cleanup(frames):
        return None

    monkeypatch.setattr(clip_pipeline_module, "extract_clip_frames", fake_extract)
    monkeypatch.setattr(clip_pipeline_module, "cleanup_frames", fake_cleanup)
    monkeypatch.setattr(
        clip_pipeline_module, "apply_clip_prefilter", lambda analysis, **kwargs: None
    )

    class StubClient:
        def __init__(self):
            self.batch_calls = 0
            self.single_calls = 0

        def analyze_clip_frames_batch(self, candidates, user_prompt, system_prompt):
            self.batch_calls += 1
            if batch_error is not None:
                raise batch_error
            rows = batch_rows if batch_rows is not None else [
                _batch_row(c["candidate_id"], score=80.0) for c in candidates
            ]
            return {
                row["candidate_id"]: _normalise_clip_response({
                    **row, "final_score": row["score"],
                    "story_relevance": row["story_relevance"],
                    "reason": row["reason"],
                }) | {"batch_verdict": row["verdict"], "batch_score": row["score"]}
                for row in rows
            }

        def analyze_clip_frames(self, frame_paths, user_prompt, system_prompt):
            self.single_calls += 1
            return _normalise_clip_response({
                **_batch_row("single", score=80.0), "final_score": 80.0,
                "reason": "single fallback verdict",
            })

    client = StubClient()
    pipeline = ClipIntelligencePipeline(client, max_frames=2)
    return pipeline, client


def test_pipeline_batch_matches_sequential_fields(tmp_path, monkeypatch):
    clip_a = tmp_path / "clipA.mp4"
    clip_b = tmp_path / "clipB.mp4"
    clip_a.write_bytes(b"x" * 100)
    clip_b.write_bytes(b"x" * 100)
    rows = [
        _batch_row("clipA", score=82.0),
        _batch_row("clipB", "reject", 12.0, reason="wrong subject", fatal_mismatch=True),
    ]
    pipeline, client = _stubbed_pipeline(tmp_path, monkeypatch, batch_rows=rows)
    out = pipeline.analyze_clips_batch(
        0,
        [{"candidate_id": "clipA", "path": str(clip_a)},
         {"candidate_id": "clipB", "path": str(clip_b)}],
        "GTA VI", "Vice City returns", "GTA VI",
    )
    assert client.batch_calls == 1
    assert client.single_calls == 0
    assert out["clipA"].vision_available is True
    assert out["clipA"].story_relevance == 82.0
    assert out["clipA"].batch_verdict == "accept"
    assert pipeline.verdict_for(out["clipA"]) == "keep"
    assert out["clipB"].fatal_mismatch is True
    assert out["clipB"].batch_verdict == "reject"
    assert pipeline.verdict_for(out["clipB"]) == "replace"


def test_pipeline_batch_failure_falls_back_to_sequential(tmp_path, monkeypatch):
    clip_a = tmp_path / "clipA.mp4"
    clip_a.write_bytes(b"x" * 100)
    pipeline, client = _stubbed_pipeline(
        tmp_path, monkeypatch, batch_error=VisionClientError("inbox timeout")
    )
    out = pipeline.analyze_clips_batch(
        0, [{"candidate_id": "clipA", "path": str(clip_a)}],
        "GTA VI", "Vice City returns", "GTA VI",
    )
    assert client.batch_calls == 1
    assert client.single_calls == 1, "batch failure judges the candidate singly"
    assert out["clipA"].vision_available is True


# ---------------------------------------------------------------------------
# YT lane: batched verdicts agree with sequential verdicts
# ---------------------------------------------------------------------------

def _stub_visual_context(monkeypatch, batch_rows, frame):
    """A visual context whose client answers batch + single calls from the
    same canned rows, so parity can be checked directly. Single calls
    consume the rows in order (the sequential path judges one candidate
    per call); batch calls answer by candidate id."""
    import services.visual_intelligence.clip_pipeline as cp

    monkeypatch.setattr(cp, "extract_clip_frames", lambda path, max_frames=8: ([frame], 10.0))
    monkeypatch.setattr(cp, "cleanup_frames", lambda frames: None)
    monkeypatch.setattr(cp, "apply_clip_prefilter", lambda analysis, **kwargs: None)

    def _normalised(row):
        return _normalise_clip_response({
            **row, "final_score": row["score"],
            "story_relevance": row["story_relevance"],
            "reason": row["reason"],
        }) | {"batch_verdict": row["verdict"], "batch_score": row["score"]}

    class StubClient:
        def __init__(self):
            self.batch_calls = 0
            self.single_calls = 0
            self._single_rows = list(batch_rows)

        def analyze_clip_frames_batch(self, candidates, user_prompt, system_prompt):
            self.batch_calls += 1
            by_id = {r["candidate_id"]: r for r in batch_rows}
            return {c["candidate_id"]: _normalised(by_id[c["candidate_id"]]) for c in candidates}

        def analyze_clip_frames(self, frame_paths, user_prompt, system_prompt):
            self.single_calls += 1
            row = self._single_rows.pop(0)
            return _normalise_clip_response({
                **row, "final_score": row["score"],
                "story_relevance": row["story_relevance"],
                "reason": row["reason"],
            })

    client = StubClient()

    class Ctx:
        enabled = True
        visual_required = False

        def get_client(self):
            return client

    return Ctx(), client


def test_batched_verdicts_agree_with_sequential(tmp_path, monkeypatch):
    frame = _frame(tmp_path, "f.jpg")
    rows = [
        _batch_row("clipA", score=82.0),
        _batch_row("clipB", score=45.0),
        _batch_row("clipC", "reject", 12.0, reason="wrong subject", fatal_mismatch=True),
    ]
    ctx, client = _stub_visual_context(monkeypatch, rows, frame)
    candidates = [
        {"candidate_id": "clipA", "path": str(tmp_path / "clipA.mp4")},
        {"candidate_id": "clipB", "path": str(tmp_path / "clipB.mp4")},
        {"candidate_id": "clipC", "path": str(tmp_path / "clipC.mp4")},
    ]
    for cand in candidates:
        Path(cand["path"]).write_bytes(b"x" * 100)
    batched = ytv2._batched_visual_verdicts(
        candidates, 0, "GTA VI", "Vice City returns", "GTA VI", ctx,
    )
    assert client.batch_calls == 1
    assert client.single_calls == 0
    # Same rows through the sequential path, one candidate at a time.
    ctx2, client2 = _stub_visual_context(monkeypatch, rows, frame)
    sequential = {
        cand["candidate_id"]: ytv2._visual_verdict_scored(
            cand["path"], 0, "GTA VI", "Vice City returns", "GTA VI", ctx2,
        )
        for cand in candidates
    }
    assert client2.single_calls == 3
    assert batched == sequential, "batching must not change any verdict"
    assert batched["clipA"][0] is True and batched["clipA"][3] == "youtube_relaxed_relevance_keep"
    assert batched["clipB"][0] is True, "low score ranks, never rejects"
    assert batched["clipC"][0] is False
    assert batched["clipC"][3] in {"fatal_mismatch", "batch_reject"}


def test_batched_explicit_reject_is_a_hard_veto(tmp_path, monkeypatch):
    frame = _frame(tmp_path, "f.jpg")
    rows = [_batch_row("clipA", "reject", 88.0, reason="wrong entity on screen")]
    ctx, client = _stub_visual_context(monkeypatch, rows, frame)
    cand = {"candidate_id": "clipA", "path": str(tmp_path / "clipA.mp4")}
    Path(cand["path"]).write_bytes(b"x" * 100)
    out = ytv2._batched_visual_verdicts([cand], 0, "GTA VI", "n", "GTA VI", ctx)
    assert out["clipA"][0] is False, "explicit reject vetoes even a high score"
    assert out["clipA"][3] == "batch_reject"
    assert ytv2._classify_visual_result(*out["clipA"]) == "hard_fail"


def test_batched_missing_candidate_fails_open(tmp_path, monkeypatch):
    frame = _frame(tmp_path, "f.jpg")
    rows = [_batch_row("clipA", score=80.0)]
    ctx, client = _stub_visual_context(monkeypatch, rows, frame)

    def fake_batch(candidates, user_prompt, system_prompt):
        client.batch_calls += 1
        return {}  # batch judged nobody

    client.analyze_clip_frames_batch = fake_batch
    cand = {"candidate_id": "clipA", "path": str(tmp_path / "clipA.mp4")}
    Path(cand["path"]).write_bytes(b"x" * 100)
    out = ytv2._batched_visual_verdicts([cand], 0, "GTA VI", "n", "GTA VI", ctx)
    assert out["clipA"] == (True, 100.0, False, "vision_unavailable_local_checks_passed")


def test_batch_kill_switch_restores_sequential_vision(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDZEO_YT_VISION_BATCH", "0")
    calls = []

    def fake_scored(path, scene_index, topic, narration, subject, vc, **kwargs):
        calls.append(path)
        return (True, 90.0, True, "keep")

    monkeypatch.setattr(ytv2, "_visual_verdict_scored", fake_scored)
    out = ytv2._batched_visual_verdicts(
        [{"candidate_id": "a", "path": "a.mp4"}, {"candidate_id": "b", "path": "b.mp4"}],
        0, "t", "n", "s", object(),
    )
    assert len(calls) == 2, "kill-switch files one vision call per candidate"
    assert set(out) == {"a", "b"}
    assert all(v[0] is True for v in out.values())
