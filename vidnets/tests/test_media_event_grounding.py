import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from services.visual_intelligence.vision_client import _normalise_image_response, _normalise_clip_response
from services.media_resolution_coordinator import _verification_result
from services.pipeline_v2_media_adapter import _score_verdict
from services.media_shot_plan import build_media_shot_plan


@pytest.mark.parametrize("normalizer", [_normalise_image_response, _normalise_clip_response])
def test_scene_event_decisions_survive_vision_normalization(normalizer):
    result = normalizer({"domain_match": True, "scene_match": False, "event_mismatch": True})
    assert result["domain_match"] is True
    assert result["scene_match"] is False
    assert result["event_mismatch"] is True
    assert "scene_match" not in normalizer({})


@pytest.mark.parametrize("reason", [
    "Same mission, but a different event: ocean recovery instead of lunar landing.",
    "Same team, but a different matchup.",
    "A different event: a hearing instead of the narrated arrest.",
    "This is video-game simulation footage.",
])
@pytest.mark.parametrize("source", ["web", "youtube"])
def test_advisory_and_relaxed_gates_do_not_promote_wrong_events(reason, source):
    scene = {"text": "Exact narrated event", "source_policy": {"youtube_relaxed_visual_gate": True}}
    verdict = {"accepted": True, "identity_score": 99, "context_score": 90,
               "action_score": 90, "final_score": 95, "reason": reason}
    accepted, rejection, _, _ = _verification_result(scene, {"source": source}, lambda *_: verdict)
    assert not accepted
    assert rejection == "explicit_scene_conflict"
    assert _score_verdict(scene, verdict)["accepted"] is False


def test_explicit_youtube_event_mismatch_is_never_relaxed_by_high_identity_scores():
    scene = {
        "text": "Super Bowl XLIX: Seattle Seahawks vs. New England Patriots",
        "source_policy": {"youtube_relaxed_visual_gate": True},
    }
    candidate = {"source": "youtube", "path": "wrong-game.mp4"}
    verdict = {
        "accepted": True,
        "domain_match": True,
        "scene_match": False,
        "event_mismatch": True,
        "identity_score": 99,
        "context_score": 92,
        "action_score": 90,
        "final_score": 98,
        "reason": "Same sport and team colors, but footage is from a different game.",
    }

    accepted, rejection, _, decision = _verification_result(scene, candidate, lambda *_: verdict)

    assert not accepted
    assert rejection == "explicit_scene_conflict"
    assert decision["accepted"] is False
    assert decision["rejection_reason"] == "explicit_scene_conflict"


def test_contextual_same_event_still_is_allowed():
    verdict = {"accepted": True, "domain_match": True, "scene_match": True,
               "identity_score": 95, "context_score": 95, "final_score": 90,
               "reason": "An establishing view of the same narrated event."}
    assert _score_verdict({}, verdict)["accepted"]


@pytest.mark.parametrize("reason", [
    "The scene supports the context without depicting a competing event.",
    "There is no evidence of the wrong entity.",
    "This is not a different matchup.",
])
def test_negated_conflict_explanations_do_not_reject_correct_media(reason):
    from services.visual_intelligence.semantic_guard import explicit_scene_conflict
    verdict = {"domain_match": True, "scene_match": True, "entity_conflict": False,
               "fatal_mismatch": False, "identity_score": 95, "context_score": 85,
               "action_score": 70, "reason": reason}
    assert not explicit_scene_conflict(verdict)
    assert _score_verdict({}, verdict)["accepted"]
    assert explicit_scene_conflict({**verdict, "entity_conflict": True})


def test_negation_in_previous_clause_does_not_hide_real_conflict():
    from services.visual_intelligence.semantic_guard import explicit_scene_conflict
    assert explicit_scene_conflict({"reason": "No unsafe content, but wrong entity."})


def test_stale_verified_alternate_cannot_override_pixel_rejection(tmp_path):
    paths = [tmp_path / "primary.jpg", tmp_path / "wrong-event.jpg"]
    for path in paths:
        path.write_bytes(b"fixture")
    plans, _ = build_media_shot_plan([{
        "scene_index": 0, "path": str(paths[0]), "type": "image", "source": "web",
        "candidate_id": "primary", "verification_status": "verified",
        "resolution": {"verification_status": "verified", "alternates": [{
            "path": str(paths[1]), "candidate_id": "wrong", "verified": True,
            "visual_decision": {"event_mismatch": True, "scene_match": False},
        }]},
    }], [8.0], config={"intro_media_duration_seconds": 0})
    assert {shot["path"] for shot in plans[0]} == {str(paths[0])}
