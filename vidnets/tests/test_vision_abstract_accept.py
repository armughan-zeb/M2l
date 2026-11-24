"""Vision acceptance for abstract narration (run 9, job a7f52c58).

Run 9 finished 18/18 real with 0 graphics but only 1 YouTube clip + 1
frame (11% vs the 80% gaming target): the coordinator's vision gate
rejected nearly every YouTube clip with "the scene's subject is entirely
absent" because the narration on most scenes is ABSTRACT (platforms,
sales, numbers, specs) and no frame can literally depict an abstract
concept. These tests pin the fix: authentic same-game footage PASSES
for an abstract line, while wrong-game / wrong-domain / wrong-era
footage (Germany travel doc, DIY painting, Apollo rocket, 2002 Vice
City cutscene) still rejects, as do unsafe / watermarked / subtitled /
outro verdicts.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../server")))

from services.media_resolution_coordinator import (
    _candidate_hard_gate,
    _fatal_ledger_record,
    _is_fatal_vision_verdict,
    _verification_result,
)
from services.visual_intelligence.semantic_guard import (
    abstract_scene_accepts_verdict,
    candidate_proves_game,
    is_abstract_narration,
    scene_game_identity,
)

# ---------------------------------------------------------------------------
# Fixtures: run-9 scenes (narration verbatim from the measurement record)
# and verdicts modelled on the stored run-9 ledger/bridge verdicts.
# ---------------------------------------------------------------------------

RUN9_ABSTRACT_TEXTS = {
    2: "and the numbers are already staggering.",
    3: "It launched at midnight, November 19th, on PlayStation 5 and Xbox Series X and S. No PC, no last-gen — ",
    4: "current-gen only.",
    5: "Before you play, clear some space. ",
    6: "The download runs up to 212 gigabytes, pre-loads opened November 10th, and two patches are already out — ",
    7: "both mandatory.",
    8: "Standard is $79.99, Ultimate is $99.99. ",
    9: "Pre-orders get the Vintage Vice City Pack plus a month of GTA+. ",
    10: "And the boxed copy? ",
    11: "A download code, not a disc.",
    12: "Expect 30 frames per second on every console — ",
    13: "no 60 fps mode at launch. ",
    14: "PS5 Pro adds PSSR upscaling and ray-traced shadows.",
    15: "Day one is single-player only — ",
    16: "no GTA Online yet, no date. ",
    17: "But with 16 million digital pre-orders, over $1.5 billion, and a 99 on Metacritic, this is the biggest launch in gaming history.",
}

RUN9_CONCRETE_TEXTS = {
    0: "Grand Theft Auto 6 is finally here. ",
    1: "After more than a decade of waiting, launch day has arrived — ",
}


def gta_scene(text, canonical="Grand Theft Auto VI", subject_type="video_game",
              local_identity=False):
    requirement = {
        "canonical_entity": canonical,
        "subject_type": subject_type,
        "strict_identity": True,
        "aliases": ["GTA 6", "GTA VI", "GTA6", "Grand Theft Auto 6"],
        "subject_context": {"film_subject": "Grand Theft Auto VI"},
    }
    if local_identity:
        # The run-9 manifest marks scenes 15/17 strict-identity with a
        # scene-local identity scope, which switches the relaxed YouTube
        # gate OFF (exact_scene_identity_required) — a clean ACCEPT verdict
        # with no ``accepted`` field is then rejected by the normal path
        # and only the abstract rule can rescue it.
        requirement["subject_context"].update(
            {"scene_local": True, "identity_scope": "local"}
        )
    return {
        "text": text,
        "content_title": "Grand Theft Auto VI",
        "visual_requirement": requirement,
    }


def same_game_fatal_verdict(candidate_id, reason, identity=60.0, final=40.0):
    """A run-9 style fatal: same-game pixels, subject 'entirely absent'."""
    return {
        "candidate_id": candidate_id,
        "entity_conflict": False,
        "identity_score": identity,
        "action_score": 40.0,
        "context_score": 25.0,
        "fatal_mismatch": True,
        "domain_match": True,
        "scene_match": False,
        "final_score": final,
        "quality": 70.0,
        "watermark": False,
        "source_presentation_type": "gameplay_footage",
        "visual_checked": True,
        "reason": reason,
    }


YL5_FRAME1_SCENE4 = same_game_fatal_verdict(
    "youtube:_yl5ELcmugI:frame:1",
    "A dark, blurry nightclub frame — a woman dancing in neon light with "
    "car headlights behind; despite the frame id, the pixels are not the "
    "yacht from the earlier clip review, and nothing here shows consoles, "
    "platforms, or anything current-gen — the scene's subject is entirely absent.",
    identity=50.0,
)

AKOZY_CLIP_SCENE12 = same_game_fatal_verdict(
    "youtube:AKoZY5TgpjI",
    "Real-world drone footage of Dresden, Germany — the Frauenkirche dome "
    "and red-roofed old town — across three frames; a real European city, "
    "no game, no console, no frame rate; entirely the wrong place and domain "
    "for the scene.",
    identity=20.0,
)
AKOZY_CLIP_SCENE12.update(entity_conflict=True, domain_match=False)

ERFJP_CLIP_SCENE2 = same_game_fatal_verdict(
    "youtube:ErFJP0BSWRM",
    "All three frames show the same DIY painting scene — a man rolling "
    "green paint onto a wall — no game, no numbers, no launch; entirely "
    "the wrong domain for the scene.",
    identity=20.0,
)
ERFJP_CLIP_SCENE2.update(domain_match=False)

APOLLO_CLIP_SCENE13 = same_game_fatal_verdict(
    "youtube:DKtVpvzUF1Y",
    "Wrong subject: all three frames show the 1969 Apollo 11 Saturn V "
    "rocket launch, not Grand Theft Auto VI gameplay or any gaming content.",
    identity=0.0, final=5.0,
)
APOLLO_CLIP_SCENE13.update(
    entity_conflict=True, domain_match=False, source_presentation_type="archival_footage"
)

VICE_CITY_2002 = same_game_fatal_verdict(
    "youtube:VC2002cutscene",
    "A 2002 Vice City cutscene — Tommy Vercetti in the original 2002 game, "
    "wrong era and wrong game for a GTA VI launch scene.",
)
VICE_CITY_2002.update(domain_match=True)

# Run-9 bridge verdict for scene 17 tJbzMqJGH4k:frame:3 (verbatim
# substance): a CLEAN accept — fatal False, scene_match True, identity 90
# — whose reason denies the mismatch in words ("no wrong subject, era,
# watermark or text card"). Live it was still visual_rejected: scene 17
# is strict-identity, so the relaxed gate is off, and a raw bridge verdict
# carries no ``accepted`` field for the normal path to read.
FRAME3_SCENE17 = {
    "candidate_id": "youtube:tJbzMqJGH4k:frame:3",
    "entity_conflict": False,
    "identity_score": 90.0,
    "action_score": 55.0,
    "context_score": 80.0,
    "fatal_mismatch": False,
    "domain_match": True,
    "scene_match": True,
    "final_score": 78.0,
    "quality": "good",
    "watermark": False,
    "source_presentation_type": "in-game footage",
    "visual_checked": True,
    "reason": (
        "Authentic Grand Theft Auto VI in-game footage from the official "
        "extended look, showing Vice City nightlife. The narration is about "
        "launch records (pre-orders, revenue, Metacritic), which no single "
        "frame can literally depict, so this works as a relevant same-game "
        "establishing shot for the documentary context; no wrong subject, "
        "era, watermark or text card."
    ),
}


def yt_candidate(video_id, title, description="", path="/tmp/does-not-exist.mp4"):
    return {
        "candidate_id": f"youtube:{video_id}",
        "source": "youtube",
        "provider": "youtube",
        "title": title,
        "description": description,
        "path": path,
    }


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------

def test_run9_abstract_scenes_detected():
    for index, text in RUN9_ABSTRACT_TEXTS.items():
        assert is_abstract_narration(gta_scene(text)), f"scene {index} should be abstract: {text!r}"


def test_run9_concrete_scenes_not_abstract():
    for index, text in RUN9_CONCRETE_TEXTS.items():
        assert not is_abstract_narration(gta_scene(text)), f"scene {index} should NOT be abstract: {text!r}"


def test_concrete_gta_action_scene_not_abstract():
    scene = gta_scene("Lucia drives a pink sports car down Ocean Drive in Vice City, police in pursuit.")
    assert not is_abstract_narration(scene)


def test_concrete_person_scene_not_abstract():
    scene = {
        "text": "King Charles III attends the ceremony at the palace.",
        "content_title": "The Coronation",
        "visual_requirement": {
            "canonical_entity": "King Charles III",
            "subject_type": "person",
            "strict_identity": True,
            "aliases": [],
            "subject_context": {"film_subject": ""},
        },
    }
    assert not is_abstract_narration(scene)


def test_non_gaming_numbers_scene_not_abstract_for_game_gate():
    # Numbers narration in a documentary is abstract text, but the game
    # gate (not the detector) keeps the bypass gaming-only; the detector
    # itself is text-based, so assert the scene identity is empty here.
    scene = {
        "text": "and the numbers are already staggering.",
        "content_title": "The California Zephyr",
        "visual_requirement": {
            "canonical_entity": "California Zephyr",
            "subject_type": "train",
            "strict_identity": True,
            "aliases": [],
            "subject_context": {"film_subject": ""},
        },
    }
    assert is_abstract_narration(scene)
    assert scene_game_identity(scene) == ""


def test_scene_game_identity_prefers_film_subject():
    scene = gta_scene(RUN9_ABSTRACT_TEXTS[13], canonical="fps mode at launch", subject_type="named_entity")
    assert scene_game_identity(scene) == "Grand Theft Auto VI"


# ---------------------------------------------------------------------------
# Gate: accept / reject matrix on stored-style verdicts
# ---------------------------------------------------------------------------

def test_scene4_yl5_frame1_accepts():
    scene = gta_scene(RUN9_ABSTRACT_TEXTS[4])
    assert abstract_scene_accepts_verdict(YL5_FRAME1_SCENE4, scene)


def test_scene12_germany_travel_doc_still_rejects():
    scene = gta_scene(RUN9_ABSTRACT_TEXTS[12])
    assert not abstract_scene_accepts_verdict(AKOZY_CLIP_SCENE12, scene)


def test_scene2_diy_painting_still_rejects():
    scene = gta_scene(RUN9_ABSTRACT_TEXTS[2])
    assert not abstract_scene_accepts_verdict(ERFJP_CLIP_SCENE2, scene)


def test_scene13_apollo_rocket_still_rejects():
    scene = gta_scene(RUN9_ABSTRACT_TEXTS[13])
    assert not abstract_scene_accepts_verdict(APOLLO_CLIP_SCENE13, scene)


def test_2002_vice_city_cutscene_still_rejects():
    scene = gta_scene(RUN9_ABSTRACT_TEXTS[4])
    assert not abstract_scene_accepts_verdict(VICE_CITY_2002, scene)
    # ... and its metadata must not prove the game either.
    candidate = yt_candidate(
        "VC2002cutscene", "GTA Vice City (2002) — Tommy Vercetti cutscene, original game"
    )
    assert not candidate_proves_game(candidate, scene)


def test_negated_wrong_subject_does_not_block_accept():
    """Run-9 scene 17 tJbz frame:3: the verdict's reason DENIES the
    mismatch ("no wrong subject, era, watermark or text card") — a bare
    substring match on "wrong subject" would refuse authentic footage
    on the model's own denial. Negated markers must not veto."""
    scene = gta_scene(RUN9_ABSTRACT_TEXTS[17])
    assert abstract_scene_accepts_verdict(FRAME3_SCENE17, scene)


def test_negated_different_era_and_not_2002_do_not_block_accept():
    scene = gta_scene(RUN9_ABSTRACT_TEXTS[4])
    verdict = same_game_fatal_verdict(
        "youtube:tJbzMqJGH4k:frame:1",
        "GTA VI in-game footage — not a different era, not 2002 footage, "
        "the same game as the scene; the scene's subject is entirely absent.",
    )
    assert abstract_scene_accepts_verdict(verdict, scene)


def test_positive_wrong_subject_marker_still_blocks():
    """Same words WITHOUT the negation are still a refusal — the
    negation check must not swallow a genuine wrong-subject verdict."""
    scene = gta_scene(RUN9_ABSTRACT_TEXTS[4])
    verdict = same_game_fatal_verdict(
        "youtube:SomeOtherClip",
        "Wrong subject: a Dresden street scene, no game footage at all — "
        "the scene's subject is entirely absent.",
    )
    assert not abstract_scene_accepts_verdict(verdict, scene)


def test_deterministic_unbound_verdict_never_accepts():
    """Proof job a57cb5c7, platforms scene: the Rockstar-logo frame's
    verdict was deterministic (compare_relevance, visual_checked False)
    — no vision, no bound pixel review. The abstract rule excuses real
    same-game VISION verdicts only; an unbound acceptance must fall
    through to real media, never commit through this rule."""
    scene = gta_scene(
        "When it arrives, it launches first on PlayStation 5 and Xbox "
        "Series X and S, with a PC version yet to be announced."
    )
    verdict = {
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
    candidate = yt_candidate(
        "qq76pQsI1iw", "Grand Theft Auto VI Trailer 2",
        "Grand Theft Auto VI Trailer 2",
    )
    assert not abstract_scene_accepts_verdict(verdict, scene, candidate)


def test_watermark_unsafe_subtitle_outro_still_reject():
    scene = gta_scene(RUN9_ABSTRACT_TEXTS[4])
    for patch in (
        {"watermark": True},
        {"unsafe_content": True},
        {"suggestive_content": True},
        {"subtitle": True},
        {"outro_detected": True},
        {"talking_head": True},
    ):
        verdict = dict(YL5_FRAME1_SCENE4)
        verdict.update(patch)
        assert not abstract_scene_accepts_verdict(verdict, scene), patch


def test_non_abstract_scene_same_game_fatal_still_rejects():
    scene = gta_scene(RUN9_CONCRETE_TEXTS[1])
    assert not abstract_scene_accepts_verdict(YL5_FRAME1_SCENE4, scene)


def test_non_gaming_abstract_scene_still_rejects():
    scene = {
        "text": "and the numbers are already staggering.",
        "content_title": "The California Zephyr",
        "visual_requirement": {
            "canonical_entity": "California Zephyr",
            "subject_type": "train",
            "strict_identity": True,
            "aliases": [],
            "subject_context": {"film_subject": ""},
        },
    }
    verdict = same_game_fatal_verdict(
        "youtube:trainclip",
        "A steam locomotive crosses the bridge at dawn; no numbers are shown — the scene's subject is entirely absent.",
    )
    assert not abstract_scene_accepts_verdict(verdict, scene)


def test_web_verdict_not_accepted_by_yt_gate():
    scene = gta_scene(RUN9_ABSTRACT_TEXTS[4])
    web_verdict = same_game_fatal_verdict(
        "web:scn_abc",
        "GTA VI key art — Jason and Lucia by a pink sports car; no consoles, no platform — the scene's subject is entirely absent.",
    )
    web_verdict["source_presentation_type"] = "web_image"
    assert not abstract_scene_accepts_verdict(web_verdict, scene)


def test_candidate_metadata_proves_game():
    scene = gta_scene(RUN9_ABSTRACT_TEXTS[13], canonical="fps mode at launch", subject_type="named_entity")
    tJbz = yt_candidate(
        "tJbzMqJGH4k",
        "Grand Theft Auto VI: An Extended Look",
        "Captured entirely from in-game footage on PlayStation 5. Rockstar Games",
    )
    assert candidate_proves_game(tJbz, scene)
    akozy = yt_candidate(
        "AKoZY5TgpjI", "More Than You Expect", "Aerial views of Dresden, Germany."
    )
    assert not candidate_proves_game(akozy, scene)
    vc2002 = yt_candidate("VC2002cutscene", "GTA Vice City 2002 gameplay", "Tommy Vercetti")
    assert not candidate_proves_game(vc2002, scene)


# ---------------------------------------------------------------------------
# Ledger: abstract fatals must not file, wrong-game fatals still file
# ---------------------------------------------------------------------------

def test_abstract_same_game_fatal_does_not_file():
    scene = gta_scene(RUN9_ABSTRACT_TEXTS[4])
    assert not _is_fatal_vision_verdict(YL5_FRAME1_SCENE4, scene)


def test_germany_fatal_still_files():
    scene = gta_scene(RUN9_ABSTRACT_TEXTS[12])
    assert _is_fatal_vision_verdict(AKOZY_CLIP_SCENE12, scene)


def test_diy_fatal_still_files():
    scene = gta_scene(RUN9_ABSTRACT_TEXTS[2])
    assert _is_fatal_vision_verdict(ERFJP_CLIP_SCENE2, scene)


# ---------------------------------------------------------------------------
# Hard gate: canonical_entity_mismatch on abstract scenes
# ---------------------------------------------------------------------------

def test_hard_gate_abstract_scene_same_game_passes_to_vision(tmp_path):
    clip = tmp_path / "tJbz.mp4"
    clip.write_bytes(b"fake")
    scene = gta_scene(
        RUN9_ABSTRACT_TEXTS[13], canonical="fps mode at launch", subject_type="named_entity"
    )
    scene["container_id"] = "ctr_test"
    candidate = yt_candidate(
        "tJbzMqJGH4k",
        "Grand Theft Auto VI: An Extended Look",
        "Captured entirely from in-game footage on PlayStation 5. Rockstar Games",
        path=str(clip),
    )
    candidate["container_id"] = "ctr_test"
    passed, reason = _candidate_hard_gate(scene, candidate)
    assert passed, reason
    assert reason == "passed"
    assert candidate.get("semantic_context_pending") is True


def test_hard_gate_germany_doc_not_abstract_bypassed(tmp_path):
    """The Germany doc passes the hard gate live on the trivial single-token
    canonical match ("Expect" is in "More Than You Expect") — that is
    pre-existing behaviour, and the vision gate is what rejects it (see
    test_verification_result_still_rejects_scene12_germany). What must NOT
    happen is the abstract bypass claiming it: its metadata proves no game.
    """
    clip = tmp_path / "akozy.mp4"
    clip.write_bytes(b"fake")
    scene = gta_scene(
        RUN9_ABSTRACT_TEXTS[12], canonical="Expect", subject_type="concept"
    )
    scene["container_id"] = "ctr_test"
    candidate = yt_candidate(
        "AKoZY5TgpjI", "More Than You Expect", "Aerial views of Dresden, Germany.",
        path=str(clip),
    )
    candidate["container_id"] = "ctr_test"
    passed, reason = _candidate_hard_gate(scene, candidate)
    assert passed, reason
    assert candidate.get("match_reason") != "abstract_scene_game_identity_pending_visual_required"
    assert candidate.get("semantic_context_pending") is not True


def test_hard_gate_2002_vice_city_still_fails(tmp_path):
    clip = tmp_path / "vc2002.mp4"
    clip.write_bytes(b"fake")
    scene = gta_scene(
        RUN9_ABSTRACT_TEXTS[4], canonical="Grand Theft Auto VI", subject_type="video_game"
    )
    scene["container_id"] = "ctr_test"
    candidate = yt_candidate(
        "VC2002cutscene", "GTA Vice City (2002) gameplay", "Tommy Vercetti cutscene",
        path=str(clip),
    )
    candidate["container_id"] = "ctr_test"
    passed, reason = _candidate_hard_gate(scene, candidate)
    assert not passed


# ---------------------------------------------------------------------------
# Live gate end-to-end (fake verifier returns the stored verdict)
# ---------------------------------------------------------------------------

def _scene_with_container(text, **kwargs):
    scene = gta_scene(text, **kwargs)
    scene["container_id"] = "ctr_test"
    scene["scene_index"] = 4
    return scene


def test_verification_result_accepts_scene4_yl5_frame1(tmp_path):
    clip = tmp_path / "yl5.mp4"
    clip.write_bytes(b"fake")
    scene = _scene_with_container(RUN9_ABSTRACT_TEXTS[4])
    candidate = yt_candidate(
        "_yl5ELcmugI", "Grand Theft Auto VI: Out Now",
        "Grand Theft Auto VI is now available worldwide for PlayStation 5 and XBOX Series X|S. Rockstar Games",
        path=str(clip),
    )
    candidate["container_id"] = "ctr_test"

    def verifier(_scene, _candidate):
        return dict(YL5_FRAME1_SCENE4)

    accepted, reason, _score, result = _verification_result(scene, candidate, verifier, scene_index=4)
    assert accepted, reason
    assert reason == "abstract_scene_same_game_accept"
    assert result.get("abstract_scene_accepted") is True
    assert result.get("fatal_mismatch") is False


def test_verification_result_rescues_clean_accept_for_strict_identity_scene17(tmp_path):
    """Run-9 scene 17 tJbz frame:3: a CLEAN accept verdict (identity 90,
    final 78, no flags) that the live gate rejected as visual_rejected —
    scene 17 is strict-identity with a scene-local scope, so the relaxed
    gate is off and the raw verdict carries no ``accepted`` field for the
    normal path to read. Only the abstract rule rescues it."""
    clip = tmp_path / "tjbz.mp4"
    clip.write_bytes(b"fake")
    scene = _scene_with_container(RUN9_ABSTRACT_TEXTS[17], local_identity=True)
    scene["scene_index"] = 17
    candidate = yt_candidate(
        "tJbzMqJGH4k", "Grand Theft Auto VI: An Extended Look",
        "Captured entirely from in-game footage on PlayStation 5. Rockstar Games",
        path=str(clip),
    )
    candidate["container_id"] = "ctr_test"
    candidate["candidate_id"] = "youtube:tJbzMqJGH4k:frame:3"
    candidate["resolution_kind"] = "same_video_timestamp_frame"

    def verifier(_scene, _candidate):
        verdict = dict(FRAME3_SCENE17)
        verdict["visual_checked"] = True
        verdict["visual_available"] = True
        return verdict

    accepted, reason, _score, result = _verification_result(
        scene, candidate, verifier, scene_index=17
    )
    assert accepted, reason
    assert reason == "abstract_scene_same_game_accept"
    assert result.get("abstract_scene_accepted") is True


def test_verification_result_still_rejects_scene12_germany(tmp_path):
    clip = tmp_path / "akozy.mp4"
    clip.write_bytes(b"fake")
    scene = _scene_with_container(
        RUN9_ABSTRACT_TEXTS[12], canonical="Expect", subject_type="concept"
    )
    candidate = yt_candidate(
        "AKoZY5TgpjI", "More Than You Expect", "Aerial views of Dresden, Germany.",
        path=str(clip),
    )
    candidate["container_id"] = "ctr_test"

    def verifier(_scene, _candidate):
        return dict(AKOZY_CLIP_SCENE12)

    accepted, reason, _score, _result = _verification_result(scene, candidate, verifier, scene_index=12)
    assert not accepted
    assert reason in {"explicit_scene_conflict", "visual_rejected", "insufficient_visible_subject_match"}


def test_verification_result_still_rejects_scene2_diy(tmp_path):
    clip = tmp_path / "erfjp.mp4"
    clip.write_bytes(b"fake")
    scene = _scene_with_container(RUN9_ABSTRACT_TEXTS[2])
    candidate = yt_candidate(
        "ErFJP0BSWRM", "DIY painting a wall green", "Home renovation timelapse.",
        path=str(clip),
    )
    candidate["container_id"] = "ctr_test"

    def verifier(_scene, _candidate):
        return dict(ERFJP_CLIP_SCENE2)

    accepted, reason, _score, _result = _verification_result(scene, candidate, verifier, scene_index=2)
    assert not accepted
    assert reason in {"explicit_scene_conflict", "visual_rejected", "insufficient_visible_subject_match"}


def test_verification_result_prior_fatal_excused_for_abstract_scene(tmp_path):
    """A stored fatal for _yl5ELcmugI:frame:1 must not veto scene 4.

    The ledger holds a stored fatal for the frame (a coherent fatal for
    the same dark/blurry nightclub frame — the cross-contaminated "yacht
    from the earlier clip review" wording is no longer filed as a fatal
    under the coherence gate, so the stored entry here uses the same
    verdict with a coherent reason). The run-9 resume (attempt 2) vetoed
    the re-presentation as prior_fatal_mismatch_rejection. Under the fix
    the veto is excused and the live gate accepts.
    """
    clip = tmp_path / "yl5.mp4"
    clip.write_bytes(b"fake")
    scene = _scene_with_container(RUN9_ABSTRACT_TEXTS[4])
    candidate = yt_candidate(
        "_yl5ELcmugI", "Grand Theft Auto VI: Out Now",
        "Grand Theft Auto VI is now available worldwide for PlayStation 5 and XBOX Series X|S. Rockstar Games",
        path=str(clip),
    )
    candidate["container_id"] = "ctr_test"
    candidate["candidate_id"] = "youtube:_yl5ELcmugI:frame:1"
    fatal_rejections: set = set()
    fatal_reject_verdicts: dict = {}
    coherent_fatal = dict(YL5_FRAME1_SCENE4)
    coherent_fatal["reason"] = (
        "A dark, blurry nightclub frame — a woman dancing in neon light "
        "with car headlights behind; nothing here shows consoles, "
        "platforms, or anything current-gen — the scene's subject is "
        "entirely absent."
    )
    _fatal_ledger_record(
        4, candidate, coherent_fatal, scene, fatal_rejections, fatal_reject_verdicts
    )
    assert fatal_rejections, "ledger record must seed the veto key"

    def verifier(_scene, _candidate):
        return dict(YL5_FRAME1_SCENE4)

    accepted, reason, _score, _result = _verification_result(
        scene, candidate, verifier, scene_index=4,
        fatal_rejections=fatal_rejections, fatal_reject_verdicts=fatal_reject_verdicts,
    )
    assert accepted, reason
    assert reason == "abstract_scene_same_game_accept"


def test_verification_result_prior_fatal_still_vetoes_germany(tmp_path):
    """The same veto machinery must still veto the Germany doc for scene 12."""
    clip = tmp_path / "akozy.mp4"
    clip.write_bytes(b"fake")
    scene = _scene_with_container(
        RUN9_ABSTRACT_TEXTS[12], canonical="Expect", subject_type="concept"
    )
    candidate = yt_candidate(
        "AKoZY5TgpjI", "More Than You Expect", "Aerial views of Dresden, Germany.",
        path=str(clip),
    )
    candidate["container_id"] = "ctr_test"
    fatal_rejections: set = set()
    fatal_reject_verdicts: dict = {}
    _fatal_ledger_record(
        12, candidate, AKOZY_CLIP_SCENE12, scene, fatal_rejections, fatal_reject_verdicts
    )
    assert fatal_rejections

    def verifier(_scene, _candidate):
        raise AssertionError("vetoed candidate must not reach the verifier")

    accepted, reason, _score, _result = _verification_result(
        scene, candidate, verifier, scene_index=12,
        fatal_rejections=fatal_rejections, fatal_reject_verdicts=fatal_reject_verdicts,
    )
    assert not accepted
    assert reason == "prior_fatal_mismatch_rejection"
