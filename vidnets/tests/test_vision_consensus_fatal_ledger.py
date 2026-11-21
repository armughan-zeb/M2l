"""Coordinator + adapter half of the vision-consensus fix.

Job 4f61cc9e, scene 1: ONE visual_review request received TWO verdicts for
youtube:F4yOVIKCZpw.  The pool's ACCEPT (identity 75, action 70, context
78, final 76) landed first and the coordinator selected the clip; a second
agent's REJECT (final_score 20, fatal_mismatch true, entity_conflict true)
arrived 52s later and never reached the server.  There was no
consensus/strict-wins rule and every verdict container was a single dict,
so the lenient verdict won by arrival order.

These tests prove the fatal verdict now wins: a fatal rejection recorded
for (scene, content) blocks acceptance even when an ACCEPT is already on
the row / in the verdict cache, whether it arrives first or late, and the
adapter records the raw fatal verdict in the file ledger before scoring
can soften it.
"""
import hashlib
import json
import os
import sys
import time

import pytest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services import agent_bridge
from services.media_resolution_coordinator import (
    _BatchedVerifier,
    _cached_verification_result,
    _verification_result,
    resolve_storyboard_media_v2,
)
from services.verdict_conflicts import load_fatal_rejects

# Real verdicts, copied verbatim from the job records (see
# test_verdict_conflicts.py): the ACCEPT the resident pool delivered first
# (response.json) and the REJECT the hook worker's answer carried.
REAL_ACCEPT = {
    "candidate_id": "youtube:F4yOVIKCZpw",
    "identity_score": 75,
    "action_score": 70,
    "context_score": 78,
    "quality": 72,
    "final_score": 76,
    "entity_conflict": False,
    "fatal_mismatch": False,
    "domain_match": True,
    "scene_match": True,
    "reason": "The Miami-style neon nightlife, clubs and towers match the "
              "narrated neon streets of Vice City in Leonida.",
    "source_presentation_type": "gameplay",
    "has_text": True,
}

REAL_REJECT = {
    "action_score": 20,
    "candidate_id": "youtube:F4yOVIKCZpw",
    "context_score": 25,
    "domain_match": False,
    "entity_conflict": True,
    "fatal_mismatch": True,
    "final_score": 20,
    "has_text": True,
    "identity_score": 15,
    "quality": 55,
    "reason": "Wrong game for this scene: frame 1 shows GTA V-style race gameplay "
              "with a progress/lap HUD and minimap, and the remaining frames are "
              "generic nightclub and night-city shots with no Vice City, Leonida, "
              "beach or swamp content. The scene requires GTA VI Vice City / "
              "Leonida; GTA V gameplay is a competing game and wrong subject here.",
    "safe_text_regions": [],
    "scene_description": "Nighttime gameplay montage: a street race with a HUD "
                         "(progress, lap counter, timer, minimap), crowded nightclub "
                         "dancing and bar scenes, and night city shots including an "
                         "elevated highway and an aerial view of a neon-lit plaza. "
                         "No beaches, swamps, or Vice City / Leonida landmarks are "
                         "visible in any frame.",
    "scene_match": False,
    "source_presentation_type": "gameplay",
    "subject_box": {"h": 100, "w": 100, "x": 0, "y": 0},
    "visible_overlays": [
        "Frame 1: race HUD with PROGRESS 0%, 3/6, timer 00:07.03 and minimap bottom-left",
        "Frame 3: small HUD icons bottom-right",
        "Frame 4: MEGAMUNDO building sign",
    ],
}

REQUIREMENT_HASH = "dfb2a7852862d9b5e4026a5fdf5bbcef96fa4dc0b8238784f49c3dd508314e36"
REQUEST_ID = "8fc1a5f71d11747927affcc0b7e8ad59"


def _scene(index=1):
    return {
        "scene_id": f"gta-{index}",
        "scene_index": index,
        "container_id": "gta",
        "text": "The game returns to Vice City, now set in the fictional state of "
                "Leonida, a sun-soaked reimagining of Florida filled with beaches, "
                "swamps, and neon streets.",
        "domain_category": "gaming",
        "positive_subjects": ["Grand Theft Auto VI"],
        "forbidden_subjects": [],
        "source_policy": {"youtube_quota": True, "visual_verification_required": False,
                          "youtube_relaxed_visual_gate": True},
        "visual_requirement": {
            "requirement_hash": REQUIREMENT_HASH,
            "canonical_entity": "Vice City",
            "domain": "gaming",
        },
    }


def _clip_file(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 2040)
    return str(path)


def _clip_digest(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _bound_accept(path):
    """The real ACCEPT, bound to this scene and these bytes (branch reuse)."""
    return dict(
        REAL_ACCEPT,
        accepted=True,
        status="accepted",
        visual_checked=True,
        visual_available=True,
        semantic_verified=True,
        verification_status="verified",
        verified_requirement_hash=REQUIREMENT_HASH,
        verified_asset_sha256=_clip_digest(path),
    )


def _fatal_reject():
    return dict(REAL_REJECT, visual_checked=True, visual_available=True)


def _must_not_call(*_args, **_kwargs):
    raise AssertionError("verifier must not be called once the ledger vetoes the candidate")


# --- (a) coordinator: fatal REJECT blocks an ACCEPT already on the row ---

def test_prior_fatal_reject_blocks_branch_reuse_accept(tmp_path):
    clip = _clip_file(tmp_path, "clip.mp4")
    scene = _scene(1)
    candidate = {
        "candidate_id": "youtube:F4yOVIKCZpw",
        "path": clip,
        "source": "youtube",
        "provider": "youtube",
        "media_type": "video",
        "title": "Grand Theft Auto VI Trailer 2",
        "description": "Official GTA VI trailer footage",
        "visual_decision": _bound_accept(clip),
    }
    # Control: without the ledger the bound branch verdict is reused.
    accepted, reason, _score, _verdict = _verification_result(
        scene, dict(candidate), _must_not_call)
    assert accepted is True
    assert reason == "visual_pass"

    # With the fatal reject recorded, the same row is vetoed before the
    # branch-reuse shortcut can fire — the verifier is still not called.
    fatal_rejections = {(1, "F4yOVIKCZpw")}
    fatal_verdicts = {(1, "F4yOVIKCZpw"): _fatal_reject()}
    accepted, reason, score, verdict = _verification_result(
        scene, dict(candidate), _must_not_call,
        fatal_rejections=fatal_rejections, fatal_reject_verdicts=fatal_verdicts)
    assert accepted is False
    assert reason == "prior_fatal_mismatch_rejection"
    assert score == 0.0
    assert verdict["accepted"] is False
    assert verdict["fatal_mismatch"] is True
    assert verdict["final_score"] == 20


# --- (b) coordinator: ACCEPT first, fatal REJECT later, re-verify blocked ---

def test_accept_first_then_fatal_reject_blocks_reverification(tmp_path):
    clip_a = _clip_file(tmp_path, "a.mp4")
    clip_b = _clip_file(tmp_path, "b.mp4")
    scene = _scene(1)
    fatal_rejections, fatal_verdicts = set(), {}
    cache, unavailable = {}, {}
    calls = []

    def verifier(_scene, candidate):
        calls.append(str(candidate.get("path") or ""))
        if str(candidate.get("path") or "").endswith("b.mp4"):
            return _fatal_reject()
        return dict(REAL_ACCEPT, visual_checked=True, visual_available=True,
                    semantic_verified=True)

    def candidate(path):
        return {
            "candidate_id": "youtube:F4yOVIKCZpw",
            "path": path,
            "source": "youtube",
            "provider": "youtube",
            "media_type": "video",
        }

    # ACCEPT first (copy A): accepted and cached under A's path key.
    accepted, reason, _score, _verdict = _cached_verification_result(
        1, scene, candidate(clip_a), verifier, cache, unavailable,
        fatal_rejections=fatal_rejections, fatal_reject_verdicts=fatal_verdicts)
    assert accepted is True

    # Fatal REJECT later (copy B, different local path, same content):
    # rejected, and the live update records it in the ledger.
    accepted, reason, _score, verdict = _cached_verification_result(
        1, scene, candidate(clip_b), verifier, cache, unavailable,
        fatal_rejections=fatal_rejections, fatal_reject_verdicts=fatal_verdicts)
    assert accepted is False
    assert (1, "F4yOVIKCZpw") in fatal_rejections

    # Re-verification of copy A: the cached ACCEPT must not win.
    accepted, reason, score, verdict = _cached_verification_result(
        1, scene, candidate(clip_a), verifier, cache, unavailable,
        fatal_rejections=fatal_rejections, fatal_reject_verdicts=fatal_verdicts)
    assert accepted is False
    assert reason == "prior_fatal_mismatch_rejection"
    assert score == 0.0
    assert verdict["fatal_mismatch"] is True
    assert verdict["final_score"] == 20
    assert calls.count(clip_a) == 1  # no second live read for copy A


# --- (c) adapter: raw fatal verdict is recorded before scoring softens it ---

def _build_verifier(monkeypatch, fake_client, scene):
    from services.pipeline_v2_media_adapter import build_qwen_verifier
    from services.visual_intelligence.context import VisualDecisionContext

    context = VisualDecisionContext.from_config(
        {
            "visual_understanding": True,
            "openrouter_api_key": "test-key",
            "pipeline_v2_mode": "on",
            "title": "Railway documentary",
        },
        scene_count=1,
    )
    context._client = object()
    context.get_client = lambda: fake_client
    with mock.patch(
        "services.pipeline_v2_media_adapter.VisualDecisionContext.from_config",
        return_value=context,
    ):
        verifier, _ = build_qwen_verifier(
            {"visual_understanding": True, "openrouter_api_key": "test-key"},
            [scene],
        )
    return verifier


def test_adapter_records_raw_fatal_before_score_softening(tmp_path, monkeypatch):
    """The raw verdict is an action-only fatality that _score_verdict
    downgrades to an accepted near-match — the ledger must still hold
    the model's original fatal call."""
    monkeypatch.setenv("VIDNETS_AGENT_BRIDGE_DIR", str(tmp_path))

    class FakeClient:
        model = "test-qwen"

        def analyze_image(self, *_args, **_kwargs):
            return {
                "candidate_id": "youtube:F4yOVIKCZpw",
                "entity_conflict": False,
                "identity_score": 95,
                "action_score": 30,
                "context_score": 85,
                "quality": 85,
                "visible_overlays": [],
                "subject_box": {},
                "safe_text_regions": [],
                "fatal_mismatch": True,
                "reason": (
                    "The correct railway crew member and locomotive are visible, but the "
                    "required action of helping a passenger is not actively shown; action mismatch."
                ),
            }

    scene = {
        "scene_index": 1,
        "text": "The crew helps a passenger board the locomotive.",
        "visual_requirement": {"strict_identity": True, "requirement_hash": "rail-1"},
    }
    asset = tmp_path / "frame.jpg"
    asset.write_bytes(b"fixture-bytes")
    candidate = {
        "candidate_id": "youtube:F4yOVIKCZpw",
        "path": str(asset),
        "source": "youtube",
        "media_type": "image",
    }
    verifier = _build_verifier(monkeypatch, FakeClient(), scene)
    result = verifier(scene, candidate)

    # Scoring softened the verdict to an accepted near-match...
    assert result["accepted"] is True
    assert result["fatal_mismatch"] is False
    # ...but the raw fatal verdict was recorded first.
    records = load_fatal_rejects(tmp_path)
    assert len(records) == 1
    record = records[0]
    assert record["video_id"] == "F4yOVIKCZpw"
    assert record["scene_index"] == 1
    assert record["verdict"]["fatal_mismatch"] is True
    assert record["verdict"]["identity_score"] == 95


# --- (d) adapter: a cached fatal reject is never overwritten by an accept ---

def test_adapter_cached_fatal_reject_survives_later_accept(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDNETS_AGENT_BRIDGE_DIR", str(tmp_path))

    class FakeClient:
        model = "test-qwen"

        def __init__(self):
            self.calls = 0

        def analyze_image(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                return dict(
                    REAL_REJECT,
                    visible_overlays=[],
                    subject_box={},
                    safe_text_regions=[],
                )
            return {
                "candidate_id": "youtube:F4yOVIKCZpw",
                "entity_conflict": False,
                "identity_score": 95,
                "action_score": 90,
                "context_score": 90,
                "quality": 85,
                "visible_overlays": [],
                "subject_box": {},
                "safe_text_regions": [],
                "fatal_mismatch": False,
                "reason": "The frame shows the required GTA VI Vice City gameplay.",
            }

    scene = _scene(1)
    scene["visual_requirement"] = dict(scene["visual_requirement"], strict_identity=False)
    fake_client = FakeClient()
    verifier = _build_verifier(monkeypatch, fake_client, scene)

    clip_a = _clip_file(tmp_path, "a.jpg")
    clip_b = _clip_file(tmp_path, "b.jpg")  # identical bytes, different path
    assert open(clip_a, "rb").read() == open(clip_b, "rb").read()

    def candidate(path):
        return {
            "candidate_id": "youtube:F4yOVIKCZpw",
            "path": path,
            "source": "youtube",
            "provider": "youtube",
            "media_type": "image",
        }

    first = verifier(scene, candidate(clip_a))
    assert first["accepted"] is False
    assert first["fatal_mismatch"] is True

    # Same content under a different local path, lenient vision this time:
    # the cached fatal reject must win — no second live read, no overwrite.
    second = verifier(scene, candidate(clip_b))
    assert second["accepted"] is False
    assert second["fatal_mismatch"] is True
    assert fake_client.calls == 1


# --- (f) batched compare_rejected verdicts are cached + ledgered ---

def _compare_rejected_verdict():
    """What verify_batch returns for a hard-rejected row when the compare
    selects no winner (job 31f5bfec: all-rejected compare, 4 recovered
    frames re-verified singly per scene)."""
    return {
        "accepted": False,
        "status": "compare_rejected",
        "final_score": 25,
        "relevance": 25,
        "quality": 60,
        "fatal_mismatch": True,
        "watermark": False,
        "unsafe_content": False,
        "scene_description": "GTA V jet over water, no GTA VI content",
        "reason": "all four candidates are GTA V frames, no GTA VI content",
        "verification_attempt": "compare_batch",
        "compare_selected": False,
        "visual_checked": True,
        "visual_available": True,
        "schema_valid": False,
    }


def test_batched_compare_reject_is_cached_and_ledgered(tmp_path):
    """A compare_rejected verdict consumed once via _BatchedVerifier must
    be cached for (scene, candidate) AND recorded in the scene-scoped
    fatal ledger — so a later recovery pass re-presenting the same frame
    for the same scene spends zero live vision calls, and the same content
    under a different local path is vetoed without a live read either."""
    clip_a = _clip_file(tmp_path, "a.png")
    clip_b = _clip_file(tmp_path, "b.png")
    scene = _scene(1)
    fatal_rejections, fatal_verdicts = set(), {}
    cache, unavailable = {}, {}
    live_calls = []

    def live_verifier(_scene, candidate):
        live_calls.append(str(candidate.get("path") or ""))
        raise AssertionError("live verifier must not be called for a cached/ledgered reject")

    def candidate(path):
        return {
            "candidate_id": "youtube:recovered-frame-1",
            "path": path,
            "source": "youtube",
            "provider": "youtube",
            "media_type": "image",
        }

    # First presentation: the batched compare verdict, no live call.
    accepted, reason, score, verdict = _cached_verification_result(
        1, scene, candidate(clip_a), _BatchedVerifier(_compare_rejected_verdict()),
        cache, unavailable,
        fatal_rejections=fatal_rejections, fatal_reject_verdicts=fatal_verdicts)
    assert accepted is False
    assert verdict["fatal_mismatch"] is True
    assert verdict["visual_checked"] is True
    assert live_calls == []
    assert (1, "recovered-frame-1") in fatal_rejections

    # Same scene + candidate again (recovery re-presents it): cache hit,
    # the live verifier is never consulted.
    accepted, reason, score, verdict = _cached_verification_result(
        1, scene, candidate(clip_a), live_verifier, cache, unavailable,
        fatal_rejections=fatal_rejections, fatal_reject_verdicts=fatal_verdicts)
    assert accepted is False
    assert verdict["fatal_mismatch"] is True
    assert live_calls == []

    # Same content under a different local path: the fatal ledger vetoes
    # it before any live read, even though the verdict cache key differs.
    accepted, reason, score, verdict = _cached_verification_result(
        1, scene, candidate(clip_b), live_verifier, cache, unavailable,
        fatal_rejections=fatal_rejections, fatal_reject_verdicts=fatal_verdicts)
    assert accepted is False
    assert reason == "prior_fatal_mismatch_rejection"
    assert verdict["fatal_mismatch"] is True
    assert live_calls == []


# --- (e) REGRESSION from the real records, end to end ---

def _seed_real_ledger(tmp_path):
    """Replay the real late answer: ACCEPT stored first, REJECT refused
    late — the bridge half preserves the REJECT in the file ledger."""
    requirement = json.dumps({
        "schema_version": "visual_requirement.v2",
        "canonical_entity": "Vice City",
        "scene_subject_game": "gta_6",
        "requirement_hash": REQUIREMENT_HASH,
    })
    text = (
        "Inspect the actual pixels first. Return one JSON object.\n"
        "Candidate ID: youtube:F4yOVIKCZpw\n"
        "Exact narration: The game returns to Vice City, now set in the fictional "
        "state of Leonida, a sun-soaked reimagining of Florida filled with beaches, "
        "swamps, and neon streets.\n\n"
        f"Visual requirement: {requirement}\n"
    )
    directory = tmp_path / REQUEST_ID
    directory.mkdir(parents=True, exist_ok=True)
    now = time.time()
    row = {
        "id": REQUEST_ID,
        "owner_id": "owner",
        "state": "pending",
        "job_id": "4f61cc9e",
        "operation": "visual_review",
        "kind": "vision",
        "messages": [
            {"role": "system", "content": "You are the final factual visual verifier."},
            {"role": "user", "content": [{"type": "text", "text": text}]},
        ],
        "response_format": {"type": "json_object"},
        "created_at": now,
        "expires_at": now + 120,
    }
    (directory / "request.json").write_text(json.dumps(row), encoding="utf-8")
    agent_bridge.complete_request(REQUEST_ID, "owner", dict(REAL_ACCEPT))
    with pytest.raises(ValueError, match="expired or was already answered"):
        agent_bridge.complete_request(REQUEST_ID, "owner", dict(REAL_REJECT))
    records = load_fatal_rejects(tmp_path)
    assert len(records) == 1
    assert records[0]["video_id"] == "F4yOVIKCZpw"
    assert records[0]["scene_index"] is None  # real rows carry no scene field
    assert records[0]["requirement_hash"] == REQUIREMENT_HASH


def _run_gta_resolution(tmp_path, clip, job_id="4f61cc9e"):
    scenes = [_scene(0), _scene(1)]
    calls = []

    def yt_lane(scene, _cancel):
        if scene["scene_index"] == 1:
            return [{
                "candidate_id": "youtube:F4yOVIKCZpw",
                "path": clip,
                "source": "youtube",
                "provider": "youtube",
                "media_type": "video",
                "title": "Grand Theft Auto VI Trailer 2",
                "description": "Official GTA VI trailer footage",
                "score": 95,
                "final_score": 95,
                "visual_decision": _bound_accept(clip),
            }]
        return []

    def verifier(scene, candidate):
        calls.append((scene["scene_index"], str(candidate.get("path") or "")))
        return dict(REAL_ACCEPT, visual_checked=True, visual_available=True,
                    semantic_verified=True)

    result = resolve_storyboard_media_v2(
        scenes,
        youtube_lane=yt_lane,
        web_lane=lambda _s, _c: [],
        visual_verifier=verifier,
        neutral_factory=lambda _s: None,
        deadline_seconds=10,
        scene_budget_seconds=10,
        vision_reserve_seconds=0.5,
        job_id=job_id,
    )
    return result, calls


def test_regression_real_f4y_verdicts_block_scene_1(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDNETS_AGENT_BRIDGE_DIR", str(tmp_path))
    clip = _clip_file(tmp_path, "f4y.mp4")
    _seed_real_ledger(tmp_path)

    result, calls = _run_gta_resolution(tmp_path, clip, job_id="4f61cc9e")

    # The candidate consumed first was the ACCEPT — but the late REJECT is
    # in the file ledger, so scene 1 must not select this clip.
    assert result[1].selected_path != clip
    assert not any(call[0] == 1 for call in calls)


def test_regression_real_f4y_verdicts_do_not_block_other_job(tmp_path, monkeypatch):
    """Job scoping (run 8): the same late REJECT filed by job 4f61cc9e
    must not veto the clip in a different job — the bound ACCEPT is
    selected again (the verifier is not re-called: the bound verdict is
    reused once the veto is gone, exactly as in the no-ledger control)."""
    monkeypatch.setenv("VIDNETS_AGENT_BRIDGE_DIR", str(tmp_path))
    clip = _clip_file(tmp_path, "f4y.mp4")
    _seed_real_ledger(tmp_path)

    result, _calls = _run_gta_resolution(tmp_path, clip, job_id="62d7671a")

    assert result[1].selected_path == clip


def test_regression_real_f4y_accept_selects_without_ledger(tmp_path, monkeypatch):
    """Control: with no fatal record seeded, the same ACCEPT is selected."""
    monkeypatch.setenv("VIDNETS_AGENT_BRIDGE_DIR", str(tmp_path))
    clip = _clip_file(tmp_path, "f4y.mp4")

    result, _calls = _run_gta_resolution(tmp_path, clip)

    assert result[1].selected_path == clip
    assert (result[1].visual_decision or {}).get("accepted") is True
