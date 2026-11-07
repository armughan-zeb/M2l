import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

import app as server_app
from services.pipeline_v2_media_adapter import _visual_requirement_issue
from services.pipeline_v2_media_adapter import _score_verdict
from services.media_resolution_coordinator import resolve_storyboard_media_v2
from services.media_resolution_coordinator import _stock_terminal_metadata_gate


def _strict_scene():
    requirement = {
        "strict_identity": True,
        "canonical_entity": "Prince Archie",
        "subject_type": "person",
        "requirement_hash": "archie-hash",
    }
    return {
        "scene_id": "archie",
        "container_id": "archie",
        "text": "Prince Archie and Princess Lilibet",
        "visual_requirement": requirement,
        "source_policy": {
            "strict_real_media": True,
            "stock_allowed": True,
            "stock_last_resort_allowed": True,
            "stock_last_resort_no_vision": False,
        },
    }


def test_strict_scene_rejects_terminal_stock_without_vision(monkeypatch):
    scene = _strict_scene()
    media_plan = {"media_paths": [None], "source_labels": [""], "metadata": {}}
    audio_plan = {
        "scene_texts": [scene["text"]],
        "scene_contracts": [scene],
    }
    stock_called = []

    def stock_branch(*args, **kwargs):
        stock_called.append((args, kwargs))
        return {"stock_paths": ["stock-emergency.jpg"]}

    monkeypatch.setattr(server_app, "_run_stock_media_branch", stock_branch)
    monkeypatch.setattr(server_app, "_media_path_exists_for_render", lambda _path: True)

    updated, remaining = server_app._fill_strict_visual_delivery_fallback(
        media_plan,
        audio_plan,
        [0],
        (1280, 720),
        "The British Royal Family",
        {
            "delivery_first_media": True,
            "strict_retrieval_accuracy": True,
            "visual_required": True,
            "allow_stock_last_resort": True,
            "stock_last_resort_no_vision": False,
            "_stock_last_resort_enabled": True,
            "_prebuilt_stock_queries": [{"primary_query": "Prince Archie"}],
        },
    )

    assert remaining == [0]
    assert not stock_called
    assert updated['media_paths'] == [None]


def test_visual_requirement_issue_rejects_named_terminal_stock_without_vision():
    scene = _strict_scene()
    row = {
        "provider": "stock",
        "status": "accepted",
        "verification_status": "deterministic_stock_last_resort",
        "visual_verification_skipped": True,
        "requirement_hash": "archie-hash",
        "candidate_evidence": {"degraded_reason": "stock_last_resort_no_vision"},
    }
    assert _visual_requirement_issue(scene, row, None, "Stock Media Final Fallback") == "visual_verification_skipped_for_strict_scene"


def test_mixed_media_stock_emergency_policy_cannot_enable_vision():
    from services.pipeline_v2_media_adapter import _source_lane_policy

    policy = _source_lane_policy({
        "image_source": "Mixed Media",
        "allow_relevant_stock": True,
        "allow_stock_last_resort": True,
        "stock_last_resort_no_vision": False,
    })

    assert policy["stock_last_resort_no_vision"] is True


def test_visual_requirement_issue_still_requires_web_and_youtube_vision():
    scene = _strict_scene()
    for provider in ("web", "youtube"):
        row = {
            "provider": provider,
            "status": "accepted",
            "verification_status": "verified",
            "visual_verification_skipped": True,
            "requirement_hash": "archie-hash",
            "candidate_evidence": {"degraded_reason": "visual_verification_skipped"},
        }
        assert _visual_requirement_issue(scene, row, None, provider) == (
            "visual_verification_skipped_for_strict_scene"
        )


def test_coordinator_rejects_explicitly_wrong_stock_even_when_vision_is_skipped():
    scene = _strict_scene()
    result = resolve_storyboard_media_v2(
        [scene],
        youtube_lane=lambda *_args: [],
        web_lane=lambda *_args: [],
        stock_lane=lambda *_args: {
            "path": "crowned-frog.jpg",
            "source": "stock",
            "container_id": "archie",
            "title": "Prince frog toy",
        },
        visual_verifier=lambda *_args: {
            "accepted": True,
            "relevance": 99,
        },
        neutral_factory=lambda *_args: None,
    )
    assert result[0].provider == "neutral"
    assert any(
        row.get("stage") == "stock_last_resort_hard_gate"
        and row.get("status") != "accepted"
        for row in result[0].attempts
    )


def test_coordinator_requires_vision_for_metadata_free_named_stock():
    scene = _strict_scene()

    checks = []
    def verify_named_stock(*args):
        checks.append(args)
        return {"accepted": False, "visual_checked": True, "reason": "wrong_named_person"}

    result = resolve_storyboard_media_v2(
        [scene],
        youtube_lane=lambda *_args: [],
        web_lane=lambda *_args: [],
        stock_lane=lambda *_args: {
            "path": "royal-event.jpg",
            "source": "stock",
            "container_id": "archie",
            "score": 80,
        },
        visual_verifier=verify_named_stock,
        neutral_factory=lambda *_args: None,
    )
    assert checks
    assert result[0].provider == "neutral"
    assert any(row.get('stage') == 'stock_last_resort_visual_gate' and row.get('status') != 'accepted'
               for row in result[0].attempts)


def test_terminal_stock_allows_generic_named_person_caption_without_vision():
    scene = _strict_scene()
    generic_stock = {
        "title": "Royal family event at a palace",
        "provider": "stock",
        "stock_last_resort_no_vision": True,
        "visual_verification_skipped": True,
    }
    passed, reason = _stock_terminal_metadata_gate(scene, generic_stock)
    assert passed is True
    assert reason == "passed_degraded_stock_metadata"


def test_terminal_stock_still_blocks_explicit_non_photographic_metadata():
    scene = _strict_scene()
    wrong_stock = {
        "title": "Prince frog toy royal statue",
        "provider": "stock",
        "stock_last_resort_no_vision": True,
        "visual_verification_skipped": True,
    }
    passed, reason = _stock_terminal_metadata_gate(scene, wrong_stock)
    assert passed is False
    assert reason.startswith("non_photographic_metadata:")


def test_no_vision_stock_fallback_requires_exact_historical_year_evidence():
    scene = {
        "visual_requirement": {
            "strict_identity": True,
            "canonical_entity": "Ice Bowl",
            "subject_type": "named_event",
            "required_attributes": {"year": "1967"},
        }
    }
    generic_modern_football = {
        "title": "Professional football game under stadium lights",
        "provider": "stock",
        "stock_last_resort_no_vision": True,
        "visual_verification_skipped": True,
    }
    archival_event_asset = {
        "title": "Green Bay Packers in the 1967 Ice Bowl",
        "provider": "stock",
        "stock_last_resort_no_vision": True,
        "visual_verification_skipped": True,
    }
    conflicting_modern_asset = {
        "title": "1967 Ice Bowl reenactment filmed in 2024",
        "provider": "stock",
        "stock_last_resort_no_vision": True,
        "visual_verification_skipped": True,
    }

    assert _stock_terminal_metadata_gate(scene, generic_modern_football) == (
        False, "historical_period_metadata_missing"
    )
    assert _stock_terminal_metadata_gate(scene, archival_event_asset) == (
        True, "passed"
    )
    assert _stock_terminal_metadata_gate(scene, conflicting_modern_asset) == (
        False, "historical_period_metadata_conflict"
    )


def test_historical_terminal_stock_still_requires_live_visual_verification():
    from services.pipeline_v2_media_adapter import _visual_requirement_issue

    scene = {
        "visual_requirement": {
            "strict_identity": True,
            "canonical_entity": "Ice Bowl",
            "subject_type": "named_event",
            "required_attributes": {"year": "1967"},
            "requirement_hash": "ice-bowl-1967",
        }
    }
    row = {
        "provider": "stock",
        "status": "accepted",
        "verification_status": "deterministic_stock_last_resort",
        "visual_verification_skipped": True,
        "requirement_hash": "ice-bowl-1967",
        "candidate_evidence": {"degraded_reason": "stock_last_resort_no_vision"},
    }

    assert _visual_requirement_issue(scene, row, None, "Stock Media Final Fallback") == (
        "historical_period_requires_visual_verification"
    )


def test_domain_and_scene_match_keep_a_relevant_near_match():
    result = _score_verdict(
        {
            "text": "Prince Archie and Princess Lilibet appear with their family.",
            "visual_requirement": {
                "strict_identity": True,
                "canonical_entity": "Prince Archie",
                "subject_type": "person",
            },
        },
        {
            "identity_score": 35,
            "action_score": 20,
            "context_score": 70,
            "quality": 85,
            "fatal_mismatch": True,
            "entity_conflict": False,
            "domain_match": True,
            "scene_match": True,
            "reason": "The exact birth year is not visible, but the royal family context matches the scene.",
        },
    )
    assert result["accepted"] is True
    assert result["contextual_match"] is True
    assert result["nearest_match"] is True
    assert result["fatal_mismatch"] is False


def test_deferred_visual_gate_uses_domain_and_scene_context(tmp_path):
    from PIL import Image
    from services.web_image.resolver import _rank_deferred_scene_shortlist

    image_path = tmp_path / "royal-context.jpg"
    Image.new("RGB", (800, 450), color=(30, 50, 90)).save(image_path)
    prompts = []

    class Client:
        def compare_images(self, _paths, prompt):
            prompts.append(prompt)
            return {
                "acceptable": True,
                "candidates": [{
                    "relevance": 35,
                    "quality": 82,
                    "confidence": 88,
                    "fatal_mismatch": True,
                    "domain_match": True,
                    "scene_match": True,
                    "reason": "The exact person is not visible, but this royal residence supports the scene.",
                }],
            }

    class Context:
        model = "test-model"
        visual_required = True

        def get_client(self):
            return Client()

    winner = _rank_deferred_scene_shortlist(
        scene_index=0,
        scene_text="The royal family gathers at Windsor Castle.",
        subject="British Royal Family",
        global_topic="British Royal Family",
        query="British Royal Family Windsor Castle photograph",
        candidates=[{
            "path": str(image_path),
            "domain": "yandex",
            "title": "Windsor Castle royal residence",
        }],
        visual_context=Context(),
    )
    assert winner and winner["path"] == str(image_path)
    assert winner["visual_decision"]["domain_match"] is True
    assert "Scene narration" in prompts[0]
    assert "Candidate 0 metadata" in prompts[0]


def _run_deferred_gate(tmp_path, model_row, *, visual_required=True):
    from PIL import Image
    from services.web_image.resolver import _rank_deferred_scene_shortlist

    image_path = tmp_path / "candidate.jpg"
    Image.new("RGB", (640, 360), color=(40, 70, 100)).save(image_path)

    class Client:
        def compare_images(self, _paths, _prompt):
            return {"acceptable": True, "candidates": [model_row]}

    required = visual_required

    class Context:
        model = "test-model"
        visual_required = required

        def get_client(self):
            return Client()

    return _rank_deferred_scene_shortlist(
        scene_index=0,
        scene_text="Prince Harry visits Windsor Castle with the royal family.",
        subject="Prince Harry",
        global_topic="British Royal Family",
        query="Prince Harry portrait Windsor Castle photograph",
        candidates=[{
            "path": str(image_path),
            "domain": "yandex",
            "title": "Royal family event photograph",
        }],
        visual_context=Context(),
    )


def test_wrong_entity_or_domain_is_still_hard_rejected(tmp_path):
    result = _run_deferred_gate(
        tmp_path,
        {
            "relevance": 95,
            "quality": 90,
            "confidence": 98,
            "fatal_mismatch": True,
            "entity_conflict": True,
            "domain_match": False,
            "scene_match": False,
            "reason": "wrong entity: this is a wildlife frog, unrelated to Prince Harry",
        },
    )
    assert result["path"] == ""
    assert result["visual_rejected"] is True
    assert result["visual_reason"] == "all_candidates_clearly_irrelevant"


def test_unsafe_media_is_hard_rejected_even_when_context_matches(tmp_path):
    result = _run_deferred_gate(
        tmp_path,
        {
            "relevance": 90,
            "quality": 88,
            "confidence": 96,
            "domain_match": True,
            "scene_match": True,
            "unsafe_content": True,
            "reason": "royal-family context is present but the image is unsafe",
        },
    )
    assert result["path"] == ""
    assert result["visual_rejected"] is True
    assert result["visual_reason"] == "all_candidates_clearly_irrelevant"


def test_unexplained_fatal_flag_cannot_be_overridden_by_match_booleans(tmp_path):
    result = _run_deferred_gate(
        tmp_path,
        {
            "relevance": 95,
            "quality": 90,
            "confidence": 98,
            "fatal_mismatch": True,
            "domain_match": True,
            "scene_match": True,
            "reason": "",
        },
    )
    assert result["path"] == ""
    verdict = _score_verdict(
        {"visual_requirement": {"strict_identity": True}},
        {
            "identity_score": 95,
            "context_score": 90,
            "action_score": 80,
            "fatal_mismatch": True,
            "domain_match": True,
            "scene_match": True,
            "reason": "",
        },
    )
    assert verdict["accepted"] is False


def test_strict_visual_gate_fails_closed_when_vision_is_unavailable(tmp_path):
    from PIL import Image
    from services.web_image.resolver import _rank_deferred_scene_shortlist

    image_path = tmp_path / "unverified.jpg"
    Image.new("RGB", (640, 360), color=(100, 70, 40)).save(image_path)

    class Context:
        visual_required = True

        def get_client(self):
            return None

    result = _rank_deferred_scene_shortlist(
        scene_index=0,
        scene_text="Prince Harry addresses the public.",
        subject="Prince Harry",
        global_topic="British Royal Family",
        query="Prince Harry portrait",
        candidates=[{"path": str(image_path), "domain": "yandex", "title": "Prince Harry"}],
        visual_context=Context(),
    )
    assert result["path"] == ""
    assert result["visual_outcome"] == "unavailable"
    assert result["visual_reason"] == "client_unavailable"
