"""Verification regressions: vision-backed acceptances must commit, and
adjacent rescue is a last resort, not a rejection.

Root causes (combined-tree regressions, all in
``services/media_resolution_coordinator.py``):

* The unbound strict-identity fall-through discarded EVERY YouTube
  acceptance whose verdict lacked hash binding — including verdicts vision
  actually passed. That broke the 63-scene listicle (vision-scored
  verdicts, no ``visual_checked`` key) and the royal quota test
  (``visual_checked`` + ``semantic_verified``, no hashes). Only a verdict
  with no vision at all (proof job a57cb5c7 scene 6, ff8f checkpoint: an
  accepted ``deterministic_degraded`` Rockstar-logo frame verdict) may
  fall through.
* The pre-stock rescue hard-rejected adjacent donors, so a scene whose
  only verified media was its neighbour's still went neutral instead of
  reusing it — the stall policy's tier 1 (reuse the closest
  already-downloaded media). Adjacent donors are now deferred and used
  only when no non-adjacent verified donor exists.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from services.media_resolution_coordinator import resolve_storyboard_media_v2  # noqa: E402


def _strict_scene(index, text="Prince Harry royal family scene"):
    return {
        "scene_id": f"royal-{index}",
        "scene_index": index,
        "container_id": "royal-family",
        "text": f"{text} {index}",
        "positive_subjects": ["Prince Harry"],
        "visual_requirement": {
            "strict_identity": True,
            "domain": "person",
            "subject_type": "person",
            "canonical_entity": "Prince Harry",
            "requirement_hash": f"req-hash-{index}",
        },
        "source_policy": {
            "mixed_media": True,
            "youtube_quota": True,
            "visual_verification_required": True,
            "strict_real_media": True,
        },
    }


def _yt_clip():
    return {
        "path": "shared-harry-clip.mp4",
        "source": "youtube",
        "provider": "youtube",
        "media_type": "video",
        "asset_fingerprint": "same-physical-clip",
        "source_url": "https://youtube.com/watch?v=harry",
        "container_id": "royal-family",
        "candidate_id": "yt-harry-clip",
        "status": "accepted",
        "verification_status": "verified",
        "visual_decision": {
            "accepted": True,
            "semantic_verified": True,
            "visual_available": True,
            "visual_checked": True,
        },
    }


def _verifier(return_value):
    def verify(_scene, _candidate):
        return dict(return_value)

    return verify


def test_vision_checked_strict_identity_youtube_acceptance_commits():
    """A vision-checked YT clip commits even though its verdict was never
    hash-stamped for this scene (no verified_requirement_hash /
    verified_asset_sha256). Hash binding is the ideal; a vision pass is
    the requirement."""
    results = resolve_storyboard_media_v2(
        [_strict_scene(0)],
        youtube_lane=lambda _scene, _cancel: _yt_clip(),
        web_lane=lambda _scene, _cancel: [],
        visual_verifier=_verifier({
            "accepted": True,
            "status": "accepted",
            "semantic_verified": True,
            "visual_available": True,
            "visual_checked": True,
            "identity_score": 90,
            "final_score": 90,
        }),
        neutral_factory=lambda _scene: None,
        deadline_seconds=10,
        max_workers=1,
    )
    assert results[0].provider == "youtube"
    assert results[0].status == "accepted"
    assert not any(
        attempt.get("stage") == "bound_pixel_review"
        for attempt in (results[0].attempts or [])
    )


def test_vision_scored_strict_identity_youtube_acceptance_commits():
    """Same boundary for the listicle verifier shape: full vision scores
    (identity/action/context) with no ``visual_checked`` key at all. The
    scores ARE the vision evidence."""
    results = resolve_storyboard_media_v2(
        [_strict_scene(0)],
        youtube_lane=lambda _scene, _cancel: {
            "path": "correct-youtube-1",
            "source": "youtube",
            "provider": "youtube",
            "media_type": "video",
            "container_id": "royal-family",
            "candidate_id": "correct:youtube:1",
            "status": "accepted",
        },
        web_lane=lambda _scene, _cancel: [],
        visual_verifier=_verifier({
            "accepted": True,
            "candidate_id": "correct:youtube:1",
            "identity_score": 95,
            "action_score": 90,
            "context_score": 95,
            "quality": 90,
            "final_score": 93,
            "entity_conflict": False,
        }),
        neutral_factory=lambda _scene: None,
        deadline_seconds=10,
        max_workers=1,
    )
    assert results[0].provider == "youtube"
    assert results[0].status == "accepted"


def test_verdict_vision_evidence_boundary():
    """The fall-through boundary, pinned at the helper level: only a
    verdict with no vision at all falls through. The end-to-end case
    (the deterministic verdict falling through to real web media) is
    covered by E's ``test_platforms_scene_unbound_youtube_acceptance_
    falls_through_to_real_media``; this pins the exact shapes."""
    from services.media_resolution_coordinator import _verdict_has_vision_evidence

    # Proof job a57cb5c7 scene 6, ff8f checkpoint: the accepted
    # deterministic Rockstar-logo frame verdict — no vision, no hashes.
    deterministic = {
        "accepted": True,
        "status": "deterministic_degraded",
        "final_score": 0.4,
        "final_score_source": "compare_relevance",
        "relevance": 0.4,
        "quality": 0.7,
        "fatal_mismatch": False,
        "entity_conflict": False,
        "synthetic_content": True,
        "visual_checked": False,
        "visual_available": False,
        "reason": "youtube_frame_recovery_soft_visual_warning",
    }
    assert _verdict_has_vision_evidence(deterministic) is False
    assert _verdict_has_vision_evidence({}) is False
    assert _verdict_has_vision_evidence(None) is False
    # Vision-checked / semantically verified verdicts carry evidence.
    assert _verdict_has_vision_evidence({
        "accepted": True, "visual_checked": True, "semantic_verified": True,
    }) is True
    # So do the vision scorer's identity/action/context scores, even with
    # no ``visual_checked`` key (the 63-scene listicle verifier shape).
    assert _verdict_has_vision_evidence({
        "accepted": True, "identity_score": 95, "action_score": 90,
        "context_score": 95, "final_score": 93,
    }) is True


def _web_image(tmp_path, name, seed):
    path = tmp_path / name
    path.write_bytes(b"\xFF\xD8\xFF\xE0" + bytes([seed]) * 2000)
    return {
        "candidate_id": f"web-{name}",
        "path": str(path),
        "media_type": "image",
        "source": "web",
        "provider": "web",
        "final_score": 90.0,
        "visual_decision": {"accepted": True, "status": "accepted", "score": 90.0},
    }


class _BatchWebLane:
    """A web lane that serves per-scene rows (own + cross-scene rescue)."""

    def __init__(self, rows):
        self.rows = rows

    def __call__(self, scene, _event):
        return list(self.rows.get(int(scene.get("scene_index", 0)), []))

    def get_row(self, index, timeout=0.0):
        return list(self.rows.get(int(index), []))


def _plain_scenes(count):
    return [
        {
            "scene_index": i,
            "scene_id": f"scene-{i}",
            "container_id": "c-0",
            "text": f"Documentary beat {i} about the expedition",
            "duration": 4.0,
        }
        for i in range(count)
    ]


def test_rescue_prefers_nonadjacent_donor_over_adjacent(tmp_path):
    """When both an adjacent and a non-adjacent verified donor exist, the
    rescue takes the non-adjacent one (distinctness wins while a choice
    exists)."""
    image_a = _web_image(tmp_path, "donor-far.jpg", 11)
    image_b = _web_image(tmp_path, "donor-near.jpg", 22)
    lane = _BatchWebLane({0: [image_a], 1: [image_b], 2: []})
    results = resolve_storyboard_media_v2(
        _plain_scenes(3),
        youtube_lane=lambda _s, _e: [],
        web_lane=lane,
        stock_lane=lambda _s, _e: [],
        max_workers=1,
        deadline_seconds=10.0,
    )
    assert results[0].provider == "web"
    assert results[0].provider == "web"
    assert results[2].provider == "web"
    assert results[2].selected_path == image_a["path"]


def test_rescue_uses_adjacent_donor_as_last_resort(tmp_path):
    """When the ONLY verified media in the job is the adjacent scene's
    still, the rescue uses it rather than returning a neutral card."""
    image = _web_image(tmp_path, "only-donor.jpg", 33)
    lane = _BatchWebLane({0: [], 1: [image]})
    results = resolve_storyboard_media_v2(
        _plain_scenes(2),
        youtube_lane=lambda _s, _e: [],
        web_lane=lane,
        stock_lane=lambda _s, _e: [],
        max_workers=1,
        deadline_seconds=10.0,
    )
    assert results[0].provider == "web"
    assert results[0].provider == "web"
    assert results[0].selected_path == image["path"]
    assert any(
        attempt.get("stage") == "pre_stock_web_rescue"
        and attempt.get("status") == "adjacent_reuse_last_resort"
        for attempt in (results[0].attempts or [])
    )

def test_rescue_last_resort_never_reuses_another_named_persons_media(tmp_path):
    """The last-resort adjacent promotion must NOT override the identity
    boundary the main loop's nearest-neighbour path enforces: a verified
    photo of Malcolm Butler is not verified media for Jermaine Kearse.
    Regression for the first version of the adjacent-promotion fix, which
    re-admitted the Butler photo for Kearse through the rescue and broke
    ``test_nearest_fallback_never_reuses_another_named_persons_media``."""
    import tempfile

    image_path = tmp_path / "malcolm-butler.jpg"
    image_path.write_bytes(b"x" * 2048)
    scenes = [
        {
            "scene_index": 0,
            "scene_id": "butler",
            "container_id": "super-bowl-xlix",
            "text": "Malcolm Butler intercepts the pass.",
            "positive_subjects": ["Malcolm Butler"],
            "visual_requirement": {
                "canonical_entity": "Malcolm Butler",
                "subject_type": "person",
                "strict_identity": True,
                "requirement_hash": "butler-scene",
                "required_terms": ["Malcolm Butler"],
            },
            "source_policy": {"visual_verification_required": True},
        },
        {
            "scene_index": 1,
            "scene_id": "kearse",
            "container_id": "super-bowl-xlix",
            "text": "Jermaine Kearse makes the juggling catch.",
            "positive_subjects": ["Jermaine Kearse"],
            "visual_requirement": {
                "canonical_entity": "Jermaine Kearse",
                "subject_type": "person",
                "strict_identity": True,
                "requirement_hash": "kearse-scene",
                "required_terms": ["Jermaine Kearse"],
            },
            "source_policy": {"visual_verification_required": True},
        },
    ]

    def web_lane(scene, _cancel_event):
        if scene["scene_index"] != 0:
            return []
        return {
            "candidate_id": "butler-photo",
            "path": str(image_path),
            "provider": "web",
            "source": "web",
            "media_type": "image",
            "container_id": "super-bowl-xlix",
            "title": "Malcolm Butler interception",
            "asset_fingerprint": "butler-photo-sha",
        }

    results = resolve_storyboard_media_v2(
        scenes,
        youtube_lane=lambda _scene, _cancel_event: [],
        web_lane=web_lane,
        visual_verifier=lambda _scene, _candidate: {
            "accepted": True,
            "status": "accepted",
            "semantic_verified": True,
            "visual_available": True,
            "visual_checked": True,
            "identity_score": 95,
            "final_score": 95,
        },
        neutral_factory=lambda _scene: None,
        deadline_seconds=3,
        max_workers=2,
    )
    assert results[0].provider == "web"
    assert results[1].provider != "nearest_fallback"
    assert results[1].selected_path != str(image_path)
    assert any(
        row.get("status") == "rejected_semantic_identity_mismatch"
        for row in results[1].attempts
    )


def test_rescue_donor_identity_compatible_boundary():
    """The promotion's identity gate, pinned at the helper level."""
    from services.media_resolution_coordinator import (
        _rescue_donor_identity_compatible,
    )
    from services.media_resolution_coordinator import SceneResolution

    person_scene = {
        "visual_requirement": {
            "canonical_entity": "Jermaine Kearse",
            "subject_type": "person",
            "strict_identity": True,
        }
    }
    topic_scene = {
        "visual_requirement": {
            "canonical_entity": "Grand Theft Auto VI",
            "subject_type": "topic",
            "strict_identity": True,
        }
    }
    butler_resolution = SceneResolution(
        scene_index=0,
        selected_path="/tmp/butler.jpg",
        provider="web",
        final_scene_context={
            "visual_requirement": {
                "canonical_entity": "Malcolm Butler",
                "subject_type": "person",
            }
        },
        visual_decision={"accepted": True},
    )
    kearse_resolution = SceneResolution(
        scene_index=0,
        selected_path="/tmp/kearse.jpg",
        provider="web",
        final_scene_context={
            "visual_requirement": {
                "canonical_entity": "Jermaine Kearse",
                "subject_type": "person",
            }
        },
        visual_decision={"accepted": True},
    )
    donor = {"path": "/tmp/butler.jpg"}

    # A different named person may never serve a person scene.
    assert _rescue_donor_identity_compatible(
        person_scene, donor, {0: butler_resolution}
    ) is False
    # The same person may.
    assert _rescue_donor_identity_compatible(
        person_scene, {"path": "/tmp/kearse.jpg"}, {0: kearse_resolution}
    ) is True
    # A donor no scene vouches for cannot prove it shows this person.
    assert _rescue_donor_identity_compatible(person_scene, donor, {}) is False
    # Topic beats are not gated: the general tier's judgement stands.
    assert _rescue_donor_identity_compatible(
        topic_scene, donor, {0: butler_resolution}
    ) is True
