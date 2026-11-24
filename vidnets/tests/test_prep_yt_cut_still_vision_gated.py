"""Prep-banked YouTube cuts must still pass the live visual gate.

Worker-B finding (tests 12/14/15 replay, "Return to Vice City"): a live job
restores prep cuts from disk in ~0.1 s (no re-download) and runs no contact
probe on them — but the coordinator's scene visual gate re-verifies every
restored clip with live vision, and that re-verification is load-bearing.

Prep YouTube verification is deterministic only (search -> transcript moment
match -> ranged download -> transport/overlay screens,
``verification="cache"|"deterministic"``; see
``services.advance_prep._prep_one_youtube_cut``: "No vision here"). It is
NOT the prep-time vision verdict the web lane banks, so a prep cut's
recorded subject/scene match (text overlap + transcript) is not pixel
proof. Test 15 scene 0 proves it: prep clip ``c5qhgwwLY50`` (prep scene 1,
t=0.0, 4.5 s) is byte-identical to the clip test 12 vision-ACCEPTED for the
same scene (requirement hash ``d4f3ea2518``, sha ``da04c0510c…``), and live
vision REJECTED it in test 15 — its opening frames are GTA V title cards
("SEPTEMBER 17, 2013"), wrong-subject pixels for a GTA VI scene. Trusting
the prep cut would have shipped those title cards.

These tests pin the guardrail: a prep-restored candidate — however well
mapped — must still reach the live verifier, and a live vision rejection
of one must stand. Any future "trust prep cuts" carve-out has to fail
here first.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services import advance_prep as prep
from services.media_resolution_coordinator import _verification_result
from services.youtube_media_cache import put_verified_cut
from services.youtube_pipeline_v2 import (
    _build_scene_rows,
    inject_prep_manifest_candidates,
)

TITLE = "Grand Theft Auto VI: Return to Vice City"
SCENE_TEXT = "After a thirteen-year wait Grand Theft Auto VI arrives today"
VIDEO_ID = "c5qhgwwLY50"


def _fake_cut_bytes() -> bytes:
    # put_verified_cut / injection need >= 8 KiB with an ftyp box.
    return b"\x00\x00\x00\x18ftypisom" + b"\x00" * 9000


def _prep_candidate(*, live_scene=0, overlap=0.9, prep_scene=1):
    """A prep-restored YT candidate exactly as the worker hands it over."""
    return {
        "path": "/tmp/yt_clip_scene_001.mp4",
        "provider": "youtube",
        "source": "youtube",
        "media_type": "video",
        "candidate_id": f"youtube:{VIDEO_ID}",
        "video_id": VIDEO_ID,
        "title": "",
        "description": "",
        "channel": "",
        "score": 80.0,
        "prep_cache_restored": True,
        "prep_manifest_injected": True,
        "prep_live_scene": live_scene,
        "prep_scene": prep_scene,
        "prep_scene_overlap": overlap,
        "prep_query": "Grand Theft Auto VI thirteen-year arrives gameplay no commentary",
        "prep_cache_path": "/tmp/verified_cut_cache/02556d3111899f59da7f662691cb3f66ca0672c9d96792ad24c1c06aa8629e5b.mp4",
        "prep_verification": "cache",
        "prep_best_timestamp": 0.0,
        "timestamp_evidence": "prep_manifest_verified_moment",
    }


def _gaming_scene(*, strict=False):
    scene = {
        "scene_index": 0,
        "text": SCENE_TEXT,
        "domain_category": "gaming",
    }
    if strict:
        scene["visual_requirement"] = {
            "strict_identity": True,
            "canonical_entity": "Lucia Caminos",
            "subject_type": "person",
            "requirement_hash": "lucia-scene",
        }
        scene["source_policy"] = {"strict_real_media": True}
    return scene


def _vision_pass(_scene, _candidate):
    return {
        "accepted": True,
        "status": "accepted",
        "identity_score": 85.0,
        "action_score": 80.0,
        "context_score": 85.0,
        "final_score": 88.0,
        "visual_checked": True,
        "visual_available": True,
        "semantic_verified": True,
        "fatal_mismatch": False,
        "entity_conflict": False,
    }


def _vision_reject_title_cards(_scene, _candidate):
    """The real test-15 scene-0 verdict, fields as recorded in the ledger."""
    return {
        "accepted": False,
        "status": "rejected",
        "identity_score": 30.0,
        "action_score": 20.0,
        "context_score": 40.0,
        "final_score": 30.0,
        "reason": "insufficient visible subject match: title cards show "
        "SEPTEMBER 17, 2013 GRAND THEFT AUTO V, not GTA VI gameplay",
        "scene_description": "Title cards: 'SEPTEMBER 17, 2013 / GRAND THEFT AUTO V'",
        "visual_checked": True,
        "visual_available": True,
        "fatal_mismatch": False,
        "entity_conflict": True,
    }


def test_prep_restored_cut_still_reaches_live_vision():
    """A perfectly mapped prep restore is verified live, not auto-accepted."""
    calls = []

    def verify(scene, candidate):
        calls.append((scene, candidate))
        return _vision_pass(scene, candidate)

    accepted, reason, _score, _verdict = _verification_result(
        _gaming_scene(), _prep_candidate(), verify, scene_index=0
    )
    assert calls, "prep-restored cut must reach the live verifier"
    assert len(calls) == 1
    assert accepted is True


def test_prep_restored_cut_vision_reject_stands():
    """Test-15 scene-0 replay: vision rejects the prep clip; it stays out.

    Same video, same scene, same bytes test 12 accepted — a prep-trust
    shortcut would have shipped GTA V title cards in a GTA VI video.
    """
    calls = []

    def verify(scene, candidate):
        calls.append(True)
        return _vision_reject_title_cards(scene, candidate)

    accepted, reason, _score, verdict = _verification_result(
        _gaming_scene(), _prep_candidate(), verify, scene_index=0
    )
    assert calls, "prep-restored cut must reach the live verifier"
    assert accepted is False
    assert reason == "insufficient_visible_subject_match"
    assert verdict.get("entity_conflict") is True


def test_prep_cut_mapped_to_another_scene_still_reaches_vision():
    """A prep cut stamped for a different live scene gets no trust."""
    calls = []

    def verify(scene, candidate):
        calls.append(True)
        return _vision_pass(scene, candidate)

    accepted, _reason, _score, _verdict = _verification_result(
        _gaming_scene(), _prep_candidate(live_scene=3), verify, scene_index=0
    )
    assert calls, "wrong-scene prep cut must reach the live verifier"
    assert accepted is True


def test_prep_cut_below_overlap_floor_still_reaches_vision():
    """A weak subject match (overlap under the 0.5 injection floor) is verified live."""
    calls = []

    def verify(scene, candidate):
        calls.append(True)
        return _vision_pass(scene, candidate)

    accepted, _reason, _score, _verdict = _verification_result(
        _gaming_scene(), _prep_candidate(overlap=0.3), verify, scene_index=0
    )
    assert calls, "low-overlap prep cut must reach the live verifier"
    assert accepted is True


def test_prep_cut_strict_scene_still_reaches_vision():
    """Strict-identity scenes fail closed: prep trust never bypasses vision."""
    calls = []

    def verify(scene, candidate):
        calls.append(True)
        return _vision_pass(scene, candidate)

    accepted, _reason, _score, _verdict = _verification_result(
        _gaming_scene(strict=True), _prep_candidate(), verify, scene_index=0
    )
    assert calls, "strict-scene prep cut must reach the live verifier"
    assert accepted is True


def test_injected_prep_candidate_carries_no_vision_verdict(tmp_path, monkeypatch):
    """Injection stamps provenance, never a vision verdict.

    The candidate enters the pool with its prep moment and cache path and
    no ``visual_decision``/``qwen_result`` — there is nothing for the
    branch-verdict reuse path to trust, so the live gate must run.
    """
    monkeypatch.setenv("VIDZEO_YT_CUT_CACHE_DIR", str(tmp_path / "cut-cache"))
    monkeypatch.setenv("VIDZEO_YT_PREP_MANIFEST_INJECTION", "1")
    cut_source = tmp_path / "source.mp4"
    cut_source.write_bytes(_fake_cut_bytes())
    stored = put_verified_cut(VIDEO_ID, 0.0, 4.5, str(cut_source))
    assert stored and os.path.isfile(stored)
    manifest = {
        "schema": "vidzeo_prep_manifest_v1",
        "title": TITLE,
        "title_key": "0" * 64,
        "duration_minutes": 1.0,
        "status": "ready",
        "scenes": [{"index": 1, "text": SCENE_TEXT}],
        "youtube": {
            "attempted": 1,
            "verified": 1,
            "entries": [
                {
                    "scene": 1,
                    "video_id": VIDEO_ID,
                    "start": 0.0,
                    "duration": 4.5,
                    "query": "Grand Theft Auto VI thirteen-year arrives gameplay no commentary",
                    "path": stored,
                    "scaled_path": None,
                    "verification": "cache",
                }
            ],
        },
        "web": {"attempted": 0, "verified": 0, "entries": []},
    }
    monkeypatch.setattr(prep, "load_manifest_for_title", lambda _title: manifest)
    rows = _build_scene_rows(TITLE, [SCENE_TEXT])
    pools = {}
    audit = inject_prep_manifest_candidates(pools, rows, TITLE)
    assert audit["injected"] == 1
    assert audit["scenes"] == [0]
    (candidate,) = [c for values in pools.values() for c in values]
    assert candidate["prep_manifest_injected"] is True
    assert candidate["prep_live_scene"] == 0
    assert candidate["prep_cache_path"] == stored
    assert candidate["prep_verification"] == "cache"
    assert "visual_decision" not in candidate
    assert "qwen_result" not in candidate
    assert candidate.get("semantic_verified") is not True
