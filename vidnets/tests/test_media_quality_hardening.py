import sys

sys.path.insert(0, "server")

from services.editorial_cue_planner import plan_editorial_cues
from services.media_resolution_coordinator import _allow_topic_web_reuse, _candidate_hard_gate, _verification_result
from services.pipeline_v2_media_adapter import _visual_requirement_issue
from services.visual_requirement import build_visual_requirement
from services.scene_understanding.analyzers import hard_talking_head_signal


def test_real_subject_can_back_a_statistic_without_showing_the_count():
    from services.pipeline_v2_media_adapter import _score_verdict
    verdict = {
        "identity_score": 95, "context_score": 60, "action_score": 0,
        "quality": 90, "domain_match": True, "scene_match": False,
        "fatal_mismatch": True, "entity_conflict": False,
        "source_presentation_type": "photograph",
        "reason": "Tesla sign is an establishing shot, but does not depict production figures or fleet scale.",
    }
    scene = {"text": "486,532 vehicles delivered in one quarter.",
             "visual_requirement": {"canonical_entity": "Tesla", "strict_identity": True}}
    result = _score_verdict(scene, verdict)
    assert result["accepted"] is True
    assert result["factual_overlay_required"] is True


def test_statistic_context_still_rejects_wrong_subject_and_found_chart():
    from services.pipeline_v2_media_adapter import _score_verdict
    scene = {"text": "486,532 vehicles delivered in one quarter."}
    base = {"identity_score": 95, "context_score": 60, "action_score": 0,
            "domain_match": True, "scene_match": False, "fatal_mismatch": True,
            "source_presentation_type": "photograph", "reason": "Production figures absent."}
    assert not _score_verdict(scene, {**base, "entity_conflict": True})["accepted"]
    assert not _score_verdict(scene, {**base, "source_presentation_type": "text_heavy_diagram"})["accepted"]


def test_founding_beat_is_not_required_to_show_a_pre_product_car():
    requirement = build_visual_requirement(title="Tesla", script="Tesla makes cars.",
                                          scene_text="Tesla was incorporated in 2003.")
    assert requirement["subject_type"] == "organization"
    assert requirement["domain"] == "business"
    assert "automobile" not in requirement["required_terms"]


def test_model_designation_survives_a_brand_wide_title():
    requirement = build_visual_requirement(title="Tesla", script="Tesla makes electric cars.",
                                          scene_text="The Model S followed in 2012.")
    assert requirement["required_attributes"]["model"] == "model s"


def test_search_keywords_do_not_turn_a_timeline_photo_into_exact_action_footage():
    from services.pipeline_v2_media_adapter import _score_verdict
    scene = {"text": "Roadster deliveries began in 2008.",
             "visual_requirement": {"canonical_entity": "Tesla", "strict_identity": True,
                                    "action_terms": ["roadster", "deliveries", "began", "2008"]}}
    result = _score_verdict(scene, {"identity_score": 95, "context_score": 90,
        "action_score": 0, "domain_match": True, "scene_match": True,
        "reason": "A photograph of the correct Tesla Roadster."})
    assert result["accepted"]
    assert not result.get("action_specific_match_required")


def _scene(scene_id, text, start=0.0, token_start=0):
    words = text.split()
    return {
        "scene_id": scene_id,
        "container_id": scene_id,
        "text": text,
        "start_seconds": start,
        "end_seconds": start + len(words) * 0.5,
        "word_start_index": token_start,
        "word_end_index": token_start + len(words),
        "alignment_confidence": 0.99,
        "timing_quality": "word_aligned",
        "char_start": 0,
        "token_start": token_start,
    }


def _words(scenes):
    rows = []
    index = 0
    for scene in scenes:
        for word in scene["text"].split():
            rows.append({"index": index, "text": word, "start": index * 0.5, "end": index * 0.5 + 0.5, "confidence": 0.99})
            index += 1
    return rows


def test_talking_head_requires_confident_presenter_signal(monkeypatch):
    monkeypatch.delenv("VIDZEO_YT_TALKING_HEAD_HARD_CONFIDENCE", raising=False)
    assert not hard_talking_head_signal(True, 0.55, "stable_centered_presenter")
    assert hard_talking_head_signal(True, 0.90, "stable_centered_presenter")
    assert not hard_talking_head_signal(True, 0.99, "face_composition_not_talking_head")


def test_strict_web_presenter_package_is_not_a_factual_still():
    requirement = build_visual_requirement(
        title="The British Royal Family",
        script="A documentary about Prince Harry and King Charles III.",
        scene_text="The British Royal Family Prince Harry interview.",
    )
    scene = {
        "scene_id": "royal-1",
        "container_id": "royal",
        "text": "The British Royal Family Prince Harry interview.",
        "visual_requirement": requirement,
        "source_policy": {"visual_verification_required": True},
    }
    candidate = {
        "path": "et.jpg",
        "source": "web",
        "provider": "web",
        "container_id": "royal",
        "title": "The British Royal Family Prince Harry interview",
        "source_url": "https://etonline.com/prince-harry",
    }
    assert _candidate_hard_gate(scene, candidate) == (False, "web_presenter_package")


def test_topic_web_reuse_is_blocked_between_listicle_containers():
    rows = [{"container_id": "ronaldo"}, {"container_id": "messi"}]
    assert not _allow_topic_web_reuse(rows, {"container_id": "messi"})
    assert not _allow_topic_web_reuse(rows, {"container_id": "messi", "is_intro": True})
    assert _allow_topic_web_reuse(rows, {"container_id": "general", "is_intro": True})
    assert _allow_topic_web_reuse([{"container_id": "ronaldo"}], {"container_id": "ronaldo"})


def test_grounded_stats_and_achievements_use_whisper_word_cues():
    first = _scene("ronaldo", "He has over 900 career goals.")
    second = _scene("messi", "He lifted the FIFA World Cup.", token_start=len(first["text"].split()))
    cues = plan_editorial_cues([first, second], _words([first, second]), media=[{}, {}], variation_seed="quality")
    assert [cue.cue_type for cue in cues] == ["statistic", "highlight"]
    assert cues[0].text == "over 900 career goals"
    assert tuple(cues[0].props["_highlight_tokens"]) == ("900", "goals")
    assert cues[0].props["_word_highlight_mode"] == "whisper"
    assert cues[1].text == "FIFA World Cup"
    assert cues[1].timing_quality == "word_aligned"


def test_top_count_and_phonetic_name_cards_use_exact_whisper_windows():
    top = _scene("top", "Here are the top five best footballers.")
    top_cues = plan_editorial_cues([top], _words([top]), media=[{}], variation_seed="quality")
    assert [cue.text for cue in top_cues] == ["top five best footballers"]

    name = _scene("haaland", "At number five is Erling Haaland.")
    transcript = [{
        "index": i,
        "text": word.replace("Haaland", "Holland"),
        "start": i * 0.5,
        "end": i * 0.5 + 0.5,
        "confidence": 0.99,
    } for i, word in enumerate(name["text"].split())]
    name_cues = plan_editorial_cues(
        [name], transcript,
        containers=[{"container_id": "haaland", "kind": "list_item", "canonical_subject": "Erling Haaland"}],
        media=[{}], variation_seed="quality",
    )
    assert [cue.text for cue in name_cues] == ["Erling Haaland"]
    assert name_cues[0].timing_quality == "word_aligned"


def test_deterministic_web_stamp_cannot_bypass_live_visual_verifier():
    scene = {
        "text": "Cristiano Ronaldo scores",
        "positive_subjects": ["Cristiano Ronaldo"],
        "source_policy": {"visual_verification_required": True},
    }
    candidate = {
        "path": "ronaldo.jpg",
        "source": "web",
        "qwen_result": {
            "accepted": True,
            "source": "web_image_resolver",
            "verification_status": "deterministic_web_image",
        },
    }
    calls = []

    def verifier(_scene, _candidate):
        calls.append(True)
        return {"accepted": True, "status": "accepted", "final_score": 91}

    accepted, reason, score, _ = _verification_result(scene, candidate, verifier)
    assert accepted is True
    assert reason == "visual_pass"
    assert score == 91
    assert calls == [True]


def test_live_visual_acceptance_overrides_stale_metadata_identity_failure():
    """A strong pixel verdict must rescue a stale resolver marker."""
    scene = {
        "text": "Electric cars at highway chargers",
        "positive_subjects": ["Electric Cars"],
        "source_policy": {"visual_verification_required": True},
    }
    candidate = {
        "path": "ev-chargers.webp",
        "source": "web",
        # The provider's cheap metadata pass marked this row uncertain, but
        # the candidate still carries identity evidence and is sent to vision.
        "identity_failed": True,
        "candidate_evidence": {"identity_proof": "Electric Cars"},
    }
    accepted, reason, score, _verdict = _verification_result(
        scene,
        candidate,
        lambda _scene, _candidate: {
            "accepted": True,
            "status": "accepted",
            "relevance": 95,
            "identity_score": 90,
            "context_score": 90,
            "confidence": 95,
        },
    )
    assert accepted is True
    assert reason == "visual_pass"
    assert score == 95


def test_live_visual_acceptance_can_resolve_query_anchored_metadata_mismatch():
    scene = {
        "text": "Prince Harry and Meghan prepare to return to Britain.",
        "positive_subjects": ["Prince Harry"],
        "source_policy": {"visual_verification_required": True},
    }
    candidate = {
        "path": "royal-event.jpg",
        "source": "web",
        "semantic_context_pending": True,
        "candidate_evidence": {
            "metadata_source": "query_anchored_visual_pending",
            "verification_required": True,
            "canonical_entity_mismatch": True,
            "missing_terms": ["harry"],
        },
    }
    accepted, reason, score, _verdict = _verification_result(
        scene,
        candidate,
        lambda _scene, _candidate: {
            "accepted": True,
            "status": "accepted",
            "relevance": 91,
            "identity_score": 90,
            "context_score": 88,
            "confidence": 92,
        },
    )

    assert accepted is True
    assert reason == "visual_pass"
    assert score == 91


def test_explicit_visual_wrong_entity_still_vetoes_pending_candidate():
    scene = {
        "text": "Prince Harry and Meghan prepare to return to Britain.",
        "positive_subjects": ["Prince Harry"],
        "source_policy": {"visual_verification_required": True},
    }
    candidate = {
        "path": "frog-toy.jpg",
        "source": "web",
        "semantic_context_pending": True,
        "candidate_evidence": {
            "metadata_source": "query_anchored_visual_pending",
            "verification_required": True,
        },
    }
    accepted, reason, _score, _verdict = _verification_result(
        scene,
        candidate,
        lambda _scene, _candidate: {
            "accepted": False,
            "status": "rejected",
            "fatal_mismatch": True,
            "entity_conflict": True,
            "relevance": 2,
            "identity_score": 0,
            "reason": "wrong species and irrelevant toy image",
        },
    )

    assert accepted is False
    # Explicit pixel contradictions now have a distinct auditable rejection code.
    assert reason == "explicit_scene_conflict"


def test_low_relevance_score_alone_is_a_soft_web_warning_for_pending_candidate():
    scene = {
        "text": "Prince Harry and Meghan prepare to return to Britain.",
        "positive_subjects": ["Prince Harry"],
        "source_policy": {"visual_verification_required": True},
    }
    candidate = {
        "path": "royal-event.jpg",
        "source": "web",
        "semantic_context_pending": True,
        "candidate_evidence": {
            "metadata_source": "query_anchored_visual_pending",
            "verification_required": True,
            "canonical_entity_mismatch": True,
        },
    }
    accepted, reason, _score, verdict = _verification_result(
        scene,
        candidate,
        lambda _scene, _candidate: {
            "accepted": False,
            "status": "rejected",
            "reason": "low_relevance_but_same_person_context",
            "relevance": 24,
            "identity_score": 20,
            "context_score": 52,
            "confidence": 90,
        },
    )

    assert accepted is True
    assert reason == "soft_visual_pass"
    assert verdict["verification_status"] == "soft_visual_pass"


def test_web_watermark_cannot_be_promoted_by_identity_soft_pass():
    scene = {"text": "The Eiffel Tower rose beside the Seine."}
    candidate = {"path": "tower.jpg", "source": "web"}
    accepted, reason, _score, verdict = _verification_result(
        scene,
        candidate,
        lambda _scene, _candidate: {
            "accepted": False,
            "identity_score": 95,
            "context_score": 85,
            "watermark": True,
            "rejection_reason": "watermark_detected",
            "visual_checked": True,
            "visual_available": True,
        },
    )
    assert accepted is False
    assert reason == "watermark_detected"
    assert verdict["verification_status"] == "rejected"
    assert verdict["accepted"] is False


def test_strict_named_person_does_not_promote_unverified_web_soft_pass():
    """A metadata/title match cannot override a missing pixel-level identity check."""
    scene = {
        "text": "Princess Eugenie attends a royal event.",
        "visual_requirement": {
            "strict_identity": True,
            "canonical_entity": "Princess Eugenie",
            "subject_type": "person",
        },
    }
    candidate = {
        "path": "candidate.jpg",
        "source": "web",
        "candidate_evidence": {
            "identity_proof": "Princess Eugenie",
            "metadata_source": "asset",
        },
    }
    accepted, reason, _score, verdict = _verification_result(
        scene,
        candidate,
        lambda _scene, _candidate: {
            "accepted": False,
            "status": "rejected",
            "rejection_reason": "watermark_detected",
            "identity_score": 95,
            "context_score": 90,
        },
    )

    assert accepted is False
    assert reason == "watermark_detected"


def test_final_media_gate_rejects_accepted_web_watermark():
    scene = {"visual_required": True}
    row = {
        "provider": "web",
        "verification_status": "soft_visual_pass",
        "visual_decision": {
            "accepted": True,
            "visual_checked": True,
            "watermark": True,
            "rejection_reason": "watermark_detected",
        },
    }
    assert _visual_requirement_issue(scene, row, None, "Web Images") == "visual_verdict_rejected"


def test_strict_named_person_rejects_model_soft_pass_without_semantic_flag():
    """An already-labelled soft pass still needs the semantic_verified bit."""
    scene = {
        "text": "Princess Eugenie attends a royal event.",
        "visual_requirement": {
            "strict_identity": True,
            "canonical_entity": "Princess Eugenie",
            "subject_type": "person",
        },
    }
    candidate = {"path": "candidate.jpg", "source": "web"}
    accepted, reason, _score, verdict = _verification_result(
        scene,
        candidate,
        lambda _scene, _candidate: {
            "accepted": True,
            "status": "soft_visual_pass",
            "verification_status": "soft_visual_pass",
            "semantic_verified": False,
            "identity_score": 95,
            "context_score": 90,
        },
    )

    assert accepted is False
    assert reason == "strict_identity_not_semantically_verified"
    assert verdict["verification_status"] == "rejected"


def test_strict_web_metadata_winner_fails_closed_when_vision_outage():
    scene = {
        "text": "Cristiano Ronaldo scores",
        "positive_subjects": ["Cristiano Ronaldo"],
        "source_policy": {"visual_verification_required": True},
    }
    candidate = {
        "path": "ronaldo.jpg",
        "source": "web",
        "visual_unverified": True,
        "qwen_result": {
            "accepted": False,
            "source": "web_image_resolver",
            "verification_status": "deterministic_web_image",
        },
    }
    accepted, reason, _score, verdict = _verification_result(
        scene,
        candidate,
        lambda _scene, _candidate: {
            "accepted": False,
            "status": "vision_unavailable",
            "reason": "client_unavailable",
        },
    )
    assert accepted is False
    assert reason == "vision_unavailable"
    assert verdict["status"] == "vision_unavailable"
    assert verdict["reason"] == "strict_identity_requires_live_semantic_verification"


def test_strict_named_entity_never_accepts_metadata_only_without_verifier():
    """A search label is not proof of what the downloaded pixels depict."""
    scene = {
        "text": "The British Royal Family under public scrutiny",
        "visual_requirement": {
            "strict_identity": True,
            "canonical_entity": "British Royal Family",
            "subject_type": "person",
        },
    }
    candidate = {
        "path": "wrong-stock.jpg",
        "source": "web",
        "title": "British Royal Family meme collage",
        "candidate_evidence": {
            "identity_proof": "British Royal Family",
            "metadata_source": "asset",
        },
    }
    accepted, reason, _score, verdict = _verification_result(scene, candidate, None)
    assert accepted is False
    assert reason == "visual_verifier_unavailable"
    assert verdict["status"] == "vision_unavailable"


def test_strict_real_media_policy_never_accepts_metadata_only_without_verifier():
    scene = {
        "text": "The British Royal Family at a state ceremony",
        "source_policy": {"strict_real_media": True},
    }
    candidate = {
        "path": "wrong-stock.jpg",
        "source": "web",
        "title": "British Royal Family stock photo",
        "candidate_evidence": {"identity_proof": "British Royal Family"},
    }
    accepted, reason, _score, verdict = _verification_result(scene, candidate, None)
    assert accepted is False
    assert reason == "visual_verifier_unavailable"
    assert verdict["status"] == "vision_unavailable"


def test_strict_accuracy_never_fail_opens_clean_youtube_when_vision_times_out():
    scene = {
        "text": "King Charles III at a state ceremony",
        "visual_required": True,
        "source_policy": {
            "strict_real_media": True,
            "visual_verification_required": True,
            "youtube_relaxed_visual_gate": True,
            "youtube_fail_open_on_visual_unavailable": True,
        },
    }
    candidate = {
        "path": "possibly-wrong-royal-clip.mp4",
        "source": "youtube",
        "overlay_clean": True,
        "qwen_result": {
            "accepted": True,
            "verification_status": "deterministic_pool_cut",
            "visual_available": False,
        },
    }
    accepted, reason, _score, verdict = _verification_result(
        scene,
        candidate,
        lambda _scene, _candidate: {
            "accepted": False,
            "status": "vision_unavailable",
            "reason": "visual_deadline_exceeded",
            "visual_available": False,
        },
    )
    assert accepted is False
    assert reason == "vision_unavailable"
    assert verdict["status"] == "vision_unavailable"


def test_unchecked_youtube_pool_verdict_cannot_bypass_live_visual_gate():
    """A pool cut with visual_available=true still needs a live pixel check."""
    scene = {
        "text": "Electric cars charging in a city",
        "positive_subjects": ["electric car", "charging station"],
        "source_policy": {
            "strict_real_media": True,
            "visual_verification_required": True,
        },
    }
    candidate = {
        "path": "irrelevant-pool-cut.mp4",
        "source": "youtube",
        "qwen_result": {
            "accepted": True,
            "source": "youtube_pool_moment",
            "verification_status": "visual_pool_cut",
            "visual_available": True,
            "visual_checked": False,
            "semantic_verified": True,
            "visual_reason": "fatal_mismatch",
        },
    }
    calls = []

    def verifier(_scene, _candidate):
        calls.append(True)
        return {
            "accepted": False,
            "status": "visual_rejected",
            "reason": "wrong subject: gardening footage",
            "entity_conflict": True,
            "fatal_mismatch": True,
            "relevance": 1,
            "confidence": 99,
        }

    accepted, reason, _score, _verdict = _verification_result(scene, candidate, verifier)
    assert calls == [True]
    assert accepted is False
    assert reason == "visual_rejected"


def test_strict_scene_allows_high_context_establishing_shot():
    from services.pipeline_v2_media_adapter import _score_verdict

    result = _score_verdict(
        {
            "visual_requirement": {"strict_identity": True},
        },
        {
            "identity_score": 85,
            "context_score": 90,
            "action_score": 20,
            "quality": 95,
            "fatal_mismatch": True,
            "entity_conflict": False,
            "reason": (
                "The required subject (King Charles III) is absent from the image; "
                "the asset only provides an aerial establishing shot of Buckingham Palace."
            ),
        },
    )
    assert result["accepted"] is True
    assert result["contextual_match"] is True
    assert result["fatal_mismatch"] is False


def test_strict_scene_keeps_real_context_shot_when_named_subject_is_not_visible():
    """A relevant location shot must beat a blank card even with low identity."""
    from services.pipeline_v2_media_adapter import _score_verdict

    result = _score_verdict(
        {"visual_requirement": {"strict_identity": True}},
        {
            "identity_score": 45,
            "context_score": 90,
            "action_score": 10,
            "quality": 90,
            "fatal_mismatch": True,
            "entity_conflict": False,
            "reason": (
                "The image depicts Buckingham Palace but fails to show the "
                "required entity King Charles III; it is a relevant establishing shot."
            ),
        },
    )
    assert result["accepted"] is True
    assert result["contextual_match"] is True
    assert result["fatal_mismatch"] is False


def test_relaxed_youtube_keeps_clean_clip_when_model_is_uncertain():
    scene = {
        "text": "Train crew prepares a locomotive",
        "positive_subjects": ["train crew"],
        "source_policy": {
            "visual_verification_required": True,
            "youtube_relaxed_visual_gate": True,
        },
    }
    candidate = {
        "path": "train.mp4",
        "source": "youtube",
        "overlay_clean": True,
        "visual_unverified": True,
    }
    accepted, reason, _score, verdict = _verification_result(
        scene,
        candidate,
        lambda _scene, _candidate: {
            "accepted": False,
            "status": "visual_rejected",
            "reason": "model_uncertain",
            "relevance": 42,
            "confidence": 48,
        },
    )
    assert accepted is True
    assert reason == "deterministic_degraded"
    assert verdict["reason"] == "youtube_relaxed_soft_visual_rejection"


def test_relaxed_youtube_does_not_quota_retain_person_clip_with_no_visual_evidence():
    scene = {
        "text": "Prince Harry grew up in the British royal family",
        "positive_subjects": ["Prince Harry"],
        "visual_requirement": {
            "strict_identity": True,
            "domain": "person",
            "subject_type": "person",
            "canonical_entity": "Prince Harry",
        },
        "source_policy": {
            "strict_real_media": True,
            "visual_verification_required": True,
            "youtube_quota": True,
            "youtube_relaxed_visual_gate": True,
        },
    }
    candidate = {
        "path": "unrelated-home-tour.mp4",
        "source": "youtube",
        "overlay_clean": True,
    }
    accepted, _reason, _score, verdict = _verification_result(
        scene,
        candidate,
        lambda _scene, _candidate: {
            "accepted": False,
            "status": "visual_rejected",
            "final_score": 21.25,
            "identity_score": 0,
            "context_score": 85,
            "subjects": [],
            "scene_description": "",
            "visual_available": True,
            "reason": "insufficient_semantic_score",
        },
    )

    assert accepted is False
    assert verdict["rejection_reason"] == "required_person_identity_not_visually_supported"


def test_relaxed_youtube_keeps_named_person_near_match_with_visual_context():
    scene = {
        "text": "Prince Harry at a public event",
        "positive_subjects": ["Prince Harry"],
        "visual_requirement": {
            "strict_identity": True,
            "domain": "person",
            "subject_type": "person",
            "canonical_entity": "Prince Harry",
        },
        "source_policy": {
            "strict_real_media": True,
            "visual_verification_required": True,
            "youtube_quota": True,
            "youtube_relaxed_visual_gate": True,
        },
    }
    candidate = {
        "path": "distant-public-event.mp4",
        "source": "youtube",
        "overlay_clean": True,
    }
    accepted, reason, _score, verdict = _verification_result(
        scene,
        candidate,
        lambda _scene, _candidate: {
            "accepted": False,
            "status": "visual_rejected",
            "final_score": 54,
            "identity_score": 38,
            "context_score": 68,
            "subjects": ["Prince Harry"],
            "scene_description": "A distant view of Harry speaking at a public event.",
            "visual_available": True,
            "reason": "The subject is distant but the scene is consistent with the narration.",
        },
    )

    assert accepted is True
    assert reason == "deterministic_degraded"
    assert verdict["reason"] == "youtube_relaxed_soft_visual_rejection"


def test_relaxed_youtube_still_rejects_explicit_confusable_entity():
    scene = {
        "text": "Jaguar E-Type history",
        "positive_subjects": ["Jaguar E-Type"],
        "source_policy": {
            "visual_verification_required": True,
            "youtube_relaxed_visual_gate": True,
        },
    }
    candidate = {
        "path": "animal.mp4",
        "source": "youtube",
        "overlay_clean": True,
    }
    accepted, reason, _score, _verdict = _verification_result(
        scene,
        candidate,
        lambda _scene, _candidate: {
            "accepted": False,
            "status": "visual_rejected",
            "reason": "wrong entity: animal wildlife",
            "entity_conflict": True,
            "fatal_mismatch": True,
            "relevance": 2,
            "confidence": 98,
        },
    )
    assert accepted is False
    assert reason == "visual_rejected"


def test_footage_only_scene_rejects_web_stills_and_extracted_frames():
    scene = {
        "text": "Royal family ceremony",
        "positive_subjects": ["British royal family"],
        "source_policy": {"footage_only": True, "youtube_relaxed_visual_gate": True},
    }
    accepted, reason, _score, _verdict = _verification_result(
        scene,
        {"path": "royals.jpg", "source": "web"},
        lambda *_: {"accepted": True, "status": "accepted", "final_score": 99},
    )
    assert accepted is False
    assert reason == "footage_required_web_still_rejected"

    accepted, reason, _score, _verdict = _verification_result(
        scene,
        {
            "path": "frame.jpg",
            "source": "youtube",
            "resolution_kind": "youtube_extracted_frame",
        },
        lambda *_: {"accepted": True, "status": "accepted", "final_score": 99},
    )
    assert accepted is False
    assert reason == "footage_required_extracted_frame_rejected"
